"""At-most-once, fail-open runtime for bundled split verification."""
from __future__ import annotations

from app.services.provider_transport_telemetry import provider_attempt_execution
from app.services.llm_execution import ExecutionBudget, require_supported_execution_context

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (AttemptOutcome, AttemptStatus, ConsultationSplitBatch,
    ConsultationSplitBatchStatus, ConsultationSplitBatchTopic, ConsultationSplitExecution,
    ConsultationSplitExecutionKind, ConsultationSplitExecutionStatus,
    ConsultationSplitTopicDisposition, ConsultationSplitTopicOutcome,
    ConsultationSplitTopicOutcomeStatus, ConsultationSplitVerificationStatus,
    ProviderAttempt, TaskDispatchOutbox, TaskDispatchSourceKind, TaskDispatchState,
    TeamLlmConfig, Transcript, User, utcnow)
from app.services.consultation_split_partial import _materialize_available_split_notes_locked
from app.services.consultation_split_materialization import require_batch_materialization_version
from app.services.consultation_split_verification import apply_split_verification_response
from app.services.consultation_split_verification import prepare_split_verification_request
from app.services.consultation_split_pre_submit import _credential_config_identity
from app.services.consultation_splits import (read_split_batch_json, read_split_execution_json,
    read_split_topic_outcome_output)
from app.services.content_crypto import encrypt_json_for_existing_owner
from app.services.llm_adapters import runtime as llm_runtime
from app.services.llm_adapters.runtime import generation_request_snapshot, validate_provider_snapshot_for_config
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.llm_credentials import resolve_generation_credential
from app.services.llm_backup import backup_is_distinct, read_backup_provider, reserve_backup_attempt
from app.services.llm_backup_policy import can_failover, prepare_backup_request, tag_provider_failure, tag_output_validation_failure
from app.services.quotas import estimate_token_reservation
from app.services.llm_backup import effective_provider_attempt
from app.services.quotas import (cancel_provider_attempt, mark_provider_attempt_submitted,
    settle_provider_attempt_tokens, settle_provider_attempt_unknown_tokens)
from app.services.transcripts import transcript_is_expired
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.task_outbox import cancel_pending_task_dispatch

_DEADLINE_SECONDS = 30


def _credential_ok(config: TeamLlmConfig | None, credential: object | None) -> bool:
    if config is None:
        return False
    mode = config.auth_mode.value
    return ((mode in {"none", "google_adc"} and credential is None)
            or (mode == "bearer" and isinstance(credential, str) and bool(credential.strip()))
            or (mode == "google_service_account" and credential is not None))


def _lock(db: Session, execution_id: UUID, *, require_active_dispatch: bool = True):
    identity = db.get(ConsultationSplitExecution, execution_id)
    if identity is None or identity.kind is not ConsultationSplitExecutionKind.verification or identity.batch_id is None:
        return None
    scope = lock_consultation_split_source_scope(
        db, owner_user_id=identity.owner_user_id, transcript_id=identity.transcript_id,
    )
    if scope is None:
        return None
    owner, transcript = scope.owner, scope.transcript
    execution = db.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.id == execution_id).execution_options(populate_existing=True).with_for_update())
    if execution is None or execution.kind is not ConsultationSplitExecutionKind.verification or execution.batch_id is None:
        return None
    batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.id == execution.batch_id).execution_options(populate_existing=True).with_for_update())
    primary_attempt = db.scalar(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == execution.id,
        ProviderAttempt.backup_of_attempt_id.is_(None),
    ).execution_options(populate_existing=True).with_for_update())
    attempt = effective_provider_attempt(db, primary=primary_attempt, lock=True) if primary_attempt is not None else None
    dispatch = db.scalar(select(TaskDispatchOutbox).where(
        TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
        TaskDispatchOutbox.source_id == execution.id,
    ).execution_options(populate_existing=True).with_for_update())
    topics = db.scalars(select(ConsultationSplitBatchTopic).where(ConsultationSplitBatchTopic.batch_id == execution.batch_id).execution_options(populate_existing=True).with_for_update()).all() if batch else []
    outcomes = db.scalars(select(ConsultationSplitTopicOutcome).where(ConsultationSplitTopicOutcome.batch_topic_id.in_([x.id for x in topics])).execution_options(populate_existing=True).with_for_update()).all() if topics else []
    if not all((batch, owner, transcript, attempt, primary_attempt)) or len(outcomes) != len(topics):
        return None
    if require_active_dispatch:
        if dispatch is None or dispatch.dispatch_kind.value != "consultation_split_verification":
            return None
        if dispatch.state not in {TaskDispatchState.pending, TaskDispatchState.published}:
            return None
    return execution, batch, owner, transcript, attempt, dispatch, topics, {x.batch_topic_id: x for x in outcomes}


def _valid_verification_stage(work) -> bool:
    """Only a queue-created verification stage may materialize survivors."""
    execution, batch, _owner, _transcript, _attempt, _dispatch, topics, outcomes = work
    if (execution.kind is not ConsultationSplitExecutionKind.verification
            or batch.status is not ConsultationSplitBatchStatus.verifying
            or batch.verification_status is not ConsultationSplitVerificationStatus.verifying):
        return False
    separate = [topic for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note]
    return bool(separate) and any(
        outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated
        for topic in separate
    )


def _finish_materialization_binding_invalid(db: Session, work) -> None:
    """Durably terminalize a verifier whose confirmed document lineage is gone."""
    execution, batch, _owner, _transcript, _attempt, _dispatch, _topics, _outcomes = work
    now = utcnow()
    code = "consultation_split_materialization_binding_invalid"
    execution.status = ConsultationSplitExecutionStatus.failed
    execution.error_code = code
    execution.completed_at = now
    execution.recoverable_response_encrypted = None
    if _valid_verification_stage(work):
        batch.status = ConsultationSplitBatchStatus.failed
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = code
        batch.verification_completed_at = now
        batch.verification_correction_count = None
        batch.completed_at = now
    db.commit()


def _try_pre_submit_backup_verification(db: Session, *, work, primary_request: dict[str, object],
                                       schema: dict[str, object], error: AppError) -> bool:
    """Switch a reserved checker to a provider-scoped credential backup."""
    if not can_failover(tag_provider_failure(error)):
        return False
    execution_id = work[0].id
    db.rollback()
    with Session(bind=db.get_bind(), future=True) as lookup:
        execution = lookup.get(ConsultationSplitExecution, execution_id)
        batch = lookup.get(ConsultationSplitBatch, execution.batch_id) if execution and execution.batch_id else None
        owner = lookup.get(User, execution.owner_user_id) if execution else None
        if execution is None or batch is None or owner is None:
            return False
        try:
            backup = read_backup_provider(lookup, source=batch)
            stored_request = read_split_execution_json(lookup, owner, execution=execution, field="request_payload_encrypted")
        except AppError:
            return False
        if backup is None or stored_request != primary_request or not backup_is_distinct(
            primary_config_id=execution.llm_config_id, primary_model=execution.provider_model, backup_provider=backup):
            return False
        config = lookup.get(TeamLlmConfig, UUID(backup.llm_config_id))
        if config is None or config.team_id != execution.team_id:
            return False
        cap = llm_runtime.request_output_token_cap(primary_request)
        if not isinstance(cap, int) or cap <= 0:
            return False
        try:
            validate_provider_snapshot_for_config(backup.to_dict(), config=config, expected_model=backup.model)
            request = prepare_backup_request(backup, primary_request, owner.id,
                output_token_cap=cap, response_json_schema=schema)
            credential = resolve_generation_credential(config)
        except (AppError, TypeError, ValueError):
            return False
        lookup.expunge(config)
    locked = _lock(db, execution_id)
    if (locked is None or locked[0].status is not ConsultationSplitExecutionStatus.queued
        or locked[4].id != work[4].id or locked[4].status is not AttemptStatus.reserved
        or not _valid_verification_stage(locked) or transcript_is_expired(locked[3])):
        db.rollback()
        return False
    execution, batch, owner, transcript, primary, dispatch, topics, outcomes = locked
    if read_split_execution_json(db, owner, execution=execution, field="request_payload_encrypted") != primary_request:
        db.rollback()
        return False
    try:
        require_supported_execution_context()
    except AppError as exc:
        _finish(db, locked, reason=exc.code, verified=False, submitted=False)
        return True
    if isinstance(request.get("messages"), list):
        texts = tuple(item["content"] for item in request["messages"]
            if isinstance(item, dict) and isinstance(item.get("content"), str))
    else:
        parsed = llm_runtime.gemini_request_from_snapshot(request)
        texts = (parsed.system_message, parsed.user_message)
    now = utcnow()
    cancel_provider_attempt(db, attempt_id=primary.id, now=now)
    try:
        with db.begin_nested():
            child = reserve_backup_attempt(db, primary=primary, provider=backup,
                reserved_units=estimate_token_reservation(texts, max_completion_tokens=llm_runtime.request_output_token_cap(request) or cap), now=now)
    except AppError as exc:
        _finish(db, locked, reason=exc.code, verified=False)
        return True
    execution.status = ConsultationSplitExecutionStatus.processing
    execution.started_at = now
    mark_provider_attempt_submitted(db, attempt_id=child.id, now=now,
        deadline_at=now + timedelta(seconds=_DEADLINE_SECONDS))
    db.commit()
    try:
        with provider_attempt_execution(db, attempt_id=child.id, maximum_seconds=30):
            text, usage = llm_runtime.invoke_llm(snapshot=backup, credential=credential, request_body=request)
    except AppError:
        current = _lock(db, execution_id)
        if current and current[4].id == child.id:
            _finish(db, current, reason="verification_provider_failed", verified=False,
                submitted=True, expected_attempt_id=child.id)
        else:
            db.rollback()
        return True
    current = _lock(db, execution_id)
    if (current is None or current[4].id != child.id or current[4].status is not AttemptStatus.submitted
        or current[4].deadline_at is None or current[4].deadline_at <= utcnow()
        or transcript_is_expired(current[3]) or not _valid_verification_stage(current)):
        db.rollback()
        return True
    if not isinstance(text, str):
        _finish(db, current, reason="verification_invalid_output", verified=False,
            submitted=True, usage=usage, expected_attempt_id=child.id)
        return True
    active_execution = current[0]
    active_execution.recoverable_response_encrypted = encrypt_json_for_existing_owner(
        db, owner_user_id=current[2].id, table="consultation_split_executions",
        field="recoverable_response_encrypted", record_id=active_execution.id,
        plaintext={"text": text, "usage": usage if isinstance(usage, dict) else {}})
    db.commit()
    process_consultation_split_verification_execution(db, execution_id=execution_id)
    return True


def _finish(db: Session, work, *, reason: str | None, verified: bool, corrected: dict | None = None,
            correction_count: int | None = None, submitted: bool = False, usage: dict | None = None,
            expected_attempt_id: UUID | None = None) -> None:
    execution, batch, owner, transcript, attempt, dispatch, topics, outcomes = work
    now = utcnow()
    if expected_attempt_id is not None and (
        attempt.id != expected_attempt_id
        or attempt.status is not AttemptStatus.submitted
        or attempt.deadline_at is None
        or attempt.deadline_at <= now
    ):
        db.rollback()
        return
    if transcript_is_expired(transcript):
        if attempt.status is AttemptStatus.submitted:
            settle_provider_attempt_unknown_tokens(db, attempt_id=attempt.id, now=now)
        elif attempt.status is AttemptStatus.reserved:
            cancel_provider_attempt(db, attempt_id=attempt.id, now=now)
            if dispatch.state is TaskDispatchState.pending:
                cancel_pending_task_dispatch(db, task_id=dispatch.task_id)
        execution.status = ConsultationSplitExecutionStatus.failed
        execution.error_code = "consultation_split_source_expired"
        execution.completed_at = now
        execution.recoverable_response_encrypted = None
        batch.status = ConsultationSplitBatchStatus.failed
        batch.error_code = execution.error_code
        batch.completed_at = now
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = execution.error_code
        batch.verification_completed_at = now
        db.commit()
        return
    valid_stage = _valid_verification_stage(work)
    if valid_stage:
        try:
            require_batch_materialization_version(
                db, batch=batch, transcript_id=transcript.id,
            )
        except AppError:
            if submitted:
                if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
                    settle_provider_attempt_tokens(db, attempt_id=attempt.id, reported_total_tokens=usage["total_tokens"],
                        reported_input_tokens=usage.get("input_tokens"), reported_output_tokens=usage.get("output_tokens"),
                        outcome=AttemptOutcome.failed)
                else:
                    settle_provider_attempt_unknown_tokens(db, attempt_id=attempt.id)
            else:
                cancel_provider_attempt(db, attempt_id=attempt.id, now=now)
                if dispatch.state is TaskDispatchState.pending:
                    cancel_pending_task_dispatch(db, task_id=dispatch.task_id)
            _finish_materialization_binding_invalid(db, work)
            return
    if submitted:
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            settle_provider_attempt_tokens(db, attempt_id=attempt.id, reported_total_tokens=usage["total_tokens"],
                reported_input_tokens=usage.get("input_tokens"), reported_output_tokens=usage.get("output_tokens"),
                outcome=AttemptOutcome.succeeded if verified else AttemptOutcome.failed)
        else:
            settle_provider_attempt_unknown_tokens(db, attempt_id=attempt.id)
    else:
        cancel_provider_attempt(db, attempt_id=attempt.id, now=now)
        if dispatch.state is TaskDispatchState.pending:
            cancel_pending_task_dispatch(db, task_id=dispatch.task_id)
    execution.status = ConsultationSplitExecutionStatus.completed if verified else ConsultationSplitExecutionStatus.failed
    execution.error_code = None if verified else reason
    execution.completed_at = now
    execution.recoverable_response_encrypted = None
    if not valid_stage:
        # A malformed/manual pre-generation verification row must never promote
        # a batch or materialize outcomes.
        db.commit()
        return
    batch.verification_status = ConsultationSplitVerificationStatus.verified if verified else ConsultationSplitVerificationStatus.unchecked
    batch.verification_reason = None if verified else reason
    batch.verification_completed_at = now
    batch.verification_correction_count = correction_count if verified else None
    if corrected:
        for topic in topics:
            value = corrected.get(topic.topic_uuid)
            if value is not None:
                outcome = outcomes[topic.id]
                outcome.verified_output_encrypted = encrypt_json_for_existing_owner(db, owner_user_id=owner.id,
                    table="consultation_split_topic_outcomes", field="verified_output_encrypted",
                    record_id=outcome.id, plaintext=value)
    # The verifier has already passed the gate at durable submission.  Its
    # finalization must not become a new clinician action if a later rollout or
    # preference change closes that gate.  Keep terminalization and document
    # creation in this same transaction.
    batch.status = ConsultationSplitBatchStatus.partially_ready
    # A complete survivor set is a normal ready batch, not a clinician partial decision.
    _materialize_available_split_notes_locked(
        db, actor=owner, transcript_id=transcript.id, batch=batch,
        topics=topics, outcomes=outcomes,
    )
    complete = all(topic.disposition is not ConsultationSplitTopicDisposition.separate_note or outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.ready for topic in topics)
    if complete:
        batch.status = ConsultationSplitBatchStatus.ready
        batch.completed_at = now
    db.commit()


def fail_open_verification_execution(
    db: Session, *, execution_id: UUID, reason: str, terminalized_at: datetime | None = None,
) -> bool:
    """Terminalize already-settled/cancelled verification without a provider call.

    Quota lifecycle invokes this after it has settled the attempt.  It only
    materializes immutable accepted outcomes, so it cannot consume another
    reservation or revive/retry a submitted provider request.
    """
    work = _lock(db, execution_id, require_active_dispatch=False)
    if work is None:
        db.rollback()
        return False
    execution, batch, owner, transcript, attempt, _dispatch, topics, outcomes = work
    if execution.status not in {ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing}:
        db.rollback()
        return False
    # Quota lifecycle normally settles the attempt before entering this
    # fail-open path.  Re-check and settle a still-submitted attempt when the
    # lifecycle timestamp is supplied so a stale identity-map view cannot
    # leave provider quota reserved after the execution is terminalized.
    if terminalized_at is not None and attempt.status is AttemptStatus.submitted:
        settle_provider_attempt_unknown_tokens(db, attempt_id=attempt.id, now=terminalized_at)
    now = utcnow()
    valid_stage = _valid_verification_stage(work)
    execution.status = ConsultationSplitExecutionStatus.failed
    execution.error_code = reason
    execution.completed_at = now
    execution.recoverable_response_encrypted = None
    if not valid_stage:
        db.commit()
        return True
    try:
        require_batch_materialization_version(
            db, batch=batch, transcript_id=transcript.id,
        )
    except AppError:
        _finish_materialization_binding_invalid(db, work)
        return True
    batch.verification_status = ConsultationSplitVerificationStatus.unchecked
    batch.verification_reason = reason
    batch.verification_completed_at = now
    batch.verification_correction_count = None
    batch.status = ConsultationSplitBatchStatus.partially_ready
    _materialize_available_split_notes_locked(
        db, actor=owner, transcript_id=transcript.id, batch=batch,
        topics=topics, outcomes=outcomes,
    )
    complete = all(
        topic.disposition is not ConsultationSplitTopicDisposition.separate_note
        or outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.ready
        for topic in topics
    )
    if complete:
        batch.status = ConsultationSplitBatchStatus.ready
        batch.completed_at = now
    db.commit()
    return True


def _prepare(db: Session, execution_id: UUID):
    """Resolve the checker credential before taking final row locks."""
    if db.in_transaction():
        raise AppError(500, "consultation_split_runtime_transaction_active", "Split verification requires a clean database session")
    with Session(bind=db.get_bind(), future=True) as lookup:
        identity = lookup.get(ConsultationSplitExecution, execution_id)
        config = lookup.get(TeamLlmConfig, identity.llm_config_id) if identity and identity.llm_config_id else None
        if config is not None:
            preliminary_identity = _credential_config_identity(config)
            lookup.expunge(config)
        else:
            preliminary_identity = None
    credential_error = None
    try:
        credential = resolve_generation_credential(config) if config is not None else None
    except AppError as exc:
        credential = None
        credential_error = exc
        if (config is not None and exc.code == "business_rule_violation" and not config.vault_secret_ref
            and config.auth_mode.value not in {"none", "google_adc"}):
            credential_error = AppError(401, "llm_invalid_credential", "The LLM provider credential is not configured")
    except Exception:
        credential = None
    work = _lock(db, execution_id)
    if work is None:
        db.rollback()
        return None
    execution, batch, owner, transcript, attempt, _dispatch, topics, outcomes = work
    if not _valid_verification_stage(work):
        return work, None, None, "verification_provider_config_invalid"
    try:
        require_batch_materialization_version(
            db, batch=batch, transcript_id=transcript.id,
        )
    except AppError:
        return work, None, None, "consultation_split_materialization_binding_invalid"
    if transcript_is_expired(transcript) or attempt.reservation_valid_until <= utcnow():
        return work, None, None, "verification_source_expired"
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == execution.llm_config_id).with_for_update()) if execution.llm_config_id else None
    if (
        config is None
        or config.team_id != transcript.team_id
        or preliminary_identity is None
        or _credential_config_identity(config) != preliminary_identity
    ):
        return work, None, None, "verification_provider_config_invalid"
    try:
        snapshot = read_split_execution_json(db, owner, execution=execution, field="provider_snapshot_encrypted")
        request = read_split_execution_json(db, owner, execution=execution, field="request_payload_encrypted")
        provider = LlmProviderSnapshot(**validate_provider_snapshot_for_config(snapshot, config=config, expected_model=execution.provider_model))
        survivors = {topic.topic_uuid: read_split_topic_outcome_output(db, owner, outcome=outcomes[topic.id])
                     for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note
                     and outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated}
        prepared = prepare_split_verification_request(
            source_snapshot=read_split_batch_json(db, owner, batch=batch, field="source_snapshot_encrypted") or {},
            clinical_snapshot=read_split_batch_json(db, owner, batch=batch, field="clinical_snapshot_encrypted") or {},
            confirmed_plan=read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted") or {},
            survivor_outputs=survivors,
        )
        messages = prepared.request_body["messages"]
        canonical_request = generation_request_snapshot(adapter_kind=config.adapter_kind, model=provider.model,
            user_id=owner.id, system_message=messages[0]["content"], user_message=messages[1]["content"],
            output_token_cap=prepared.output_token_cap, response_json_schema=prepared.response_json_schema)
        if request != canonical_request:
            raise ValueError("binding")
    except Exception:
        return work, None, None, "verification_provider_binding_invalid"
    if not _credential_ok(config, credential):
        credential_error = credential_error or AppError(401, "llm_invalid_credential", "The LLM provider credential is invalid")
        result = _try_pre_submit_backup_verification(
            db, work=work, primary_request=canonical_request,
            schema=prepared.response_json_schema, error=credential_error,
        )
        if result:
            return True
        work = _lock(db, execution.id)
        return (work, None, None, "verification_credential_unavailable") if work else None
    return work, provider, (canonical_request, credential), None


def _try_backup_verification(db: Session, *, execution_id: UUID, error: AppError,
                            usage: object | None = None) -> bool:
    """Run the one backup checker from the persisted redacted request."""
    if not can_failover(error):
        return False
    if db.in_transaction():
        db.rollback()
    with Session(bind=db.get_bind(), future=True) as lookup:
        execution = lookup.get(ConsultationSplitExecution, execution_id)
        batch = lookup.get(ConsultationSplitBatch, execution.batch_id) if execution and execution.batch_id else None
        owner = lookup.get(User, execution.owner_user_id) if execution else None
        if execution is None or batch is None or owner is None:
            return False
        try:
            backup = read_backup_provider(lookup, source=batch)
            primary_request = read_split_execution_json(lookup, owner, execution=execution, field="request_payload_encrypted")
        except AppError:
            return False
        if backup is None or not isinstance(primary_request, dict):
            return False
        if not backup_is_distinct(primary_config_id=execution.llm_config_id,
                                  primary_model=execution.provider_model, backup_provider=backup):
            return False
        cap = llm_runtime.request_output_token_cap(primary_request)
        if not isinstance(cap, int) or cap <= 0:
            return False
        topics = lookup.scalars(select(ConsultationSplitBatchTopic).where(
            ConsultationSplitBatchTopic.batch_id == batch.id).order_by(ConsultationSplitBatchTopic.topic_order)).all()
        outcomes = {item.batch_topic_id: item for item in lookup.scalars(select(ConsultationSplitTopicOutcome).where(
            ConsultationSplitTopicOutcome.batch_topic_id.in_([topic.id for topic in topics]))).all()}
        plan = read_split_batch_json(lookup, owner, batch=batch, field="confirmed_plan_encrypted") or {}
        survivors = {topic.topic_uuid: read_split_topic_outcome_output(lookup, owner, outcome=outcomes[topic.id])
            for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note
            and outcomes.get(topic.id) is not None
            and outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated}
        try:
            prepared = prepare_split_verification_request(
                source_snapshot=read_split_batch_json(lookup, owner, batch=batch, field="source_snapshot_encrypted") or {},
                clinical_snapshot=read_split_batch_json(lookup, owner, batch=batch, field="clinical_snapshot_encrypted") or {},
                confirmed_plan=plan, survivor_outputs=survivors)
        except AppError:
            return False
        schema = prepared.response_json_schema
        config = lookup.get(TeamLlmConfig, UUID(backup.llm_config_id))
        if config is None or config.team_id != execution.team_id:
            return False
        try:
            validate_provider_snapshot_for_config(backup.to_dict(), config=config, expected_model=backup.model)
            request = prepare_backup_request(backup, primary_request, owner.id,
                output_token_cap=cap, response_json_schema=schema)
            credential = resolve_generation_credential(config)
        except (AppError, TypeError, ValueError):
            return False
        lookup.expunge(config)
    work = _lock(db, execution_id)
    if work is None:
        db.rollback()
        return False
    execution, batch, owner, transcript, attempt, _dispatch, topics, outcomes = work
    primary = db.scalar(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == execution.id,
        ProviderAttempt.backup_of_attempt_id.is_(None),
    ).with_for_update())
    if (primary is None or attempt.id != primary.id or attempt.status is not AttemptStatus.submitted
        or not _valid_verification_stage(work) or transcript_is_expired(transcript)):
        db.rollback()
        return False
    try:
        if read_split_execution_json(db, owner, execution=execution, field="request_payload_encrypted") != primary_request:
            db.rollback()
            return False
    except AppError:
        db.rollback()
        return False
    try:
        require_supported_execution_context()
    except AppError:
        db.rollback()
        return False
    raw_usage = usage if isinstance(usage, dict) else {}
    total = raw_usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        settle_provider_attempt_tokens(db, attempt_id=primary.id, reported_total_tokens=total,
            reported_input_tokens=raw_usage.get("input_tokens"), reported_output_tokens=raw_usage.get("output_tokens"),
            outcome=AttemptOutcome.failed)
    else:
        settle_provider_attempt_unknown_tokens(db, attempt_id=primary.id)
    if isinstance(request.get("messages"), list):
        strings = tuple(item["content"] for item in request["messages"]
                        if isinstance(item, dict) and isinstance(item.get("content"), str))
    else:
        parsed_request = llm_runtime.gemini_request_from_snapshot(request)
        strings = (parsed_request.system_message, parsed_request.user_message)
    try:
        with db.begin_nested():
            child = reserve_backup_attempt(db, primary=primary, provider=backup,
                reserved_units=estimate_token_reservation(strings, max_completion_tokens=cap), now=utcnow())
    except AppError as exc:
        # Primary settlement is durable; checker quota exhaustion keeps the
        # existing fail-open result and never changes generation eligibility.
        execution.status = ConsultationSplitExecutionStatus.failed
        execution.error_code = exc.code
        execution.completed_at = utcnow()
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = "verification_quota_unavailable"
        batch.verification_completed_at = execution.completed_at
        batch.status = ConsultationSplitBatchStatus.partially_ready
        if transcript_is_expired(transcript):
            batch.status = ConsultationSplitBatchStatus.failed
            batch.error_code = "consultation_split_source_expired"
            batch.completed_at = execution.completed_at
            db.commit()
            return True
        _materialize_available_split_notes_locked(db, actor=owner, transcript_id=transcript.id,
            batch=batch, topics=topics, outcomes=outcomes)
        complete = all(topic.disposition is not ConsultationSplitTopicDisposition.separate_note
            or outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.ready for topic in topics)
        if complete:
            batch.status = ConsultationSplitBatchStatus.ready
            batch.completed_at = execution.completed_at
        db.commit()
        return True
    now = utcnow()
    # Discard the primary response as the backup becomes active. Duplicate
    # delivery must not replay primary output against the submitted child.
    execution.recoverable_response_encrypted = None
    mark_provider_attempt_submitted(db, attempt_id=child.id, now=now, deadline_at=now + timedelta(seconds=_DEADLINE_SECONDS))
    db.commit()
    try:
        with provider_attempt_execution(db, attempt_id=child.id, maximum_seconds=30):
            text, backup_usage = llm_runtime.invoke_llm(snapshot=backup, credential=credential, request_body=request)
    except AppError as exc:
        tagged = tag_provider_failure(exc)
        work = _lock(db, execution_id)
        if work and work[4].id == child.id:
            _finish(db, work, reason="verification_provider_failed", verified=False,
                submitted=True, expected_attempt_id=child.id)
        else:
            db.rollback()
        return True
    if not isinstance(text, str):
        invalid = tag_output_validation_failure(AppError(502, "consultation_split_verification_invalid", "Verification output was invalid"))
        work = _lock(db, execution_id)
        if work and work[4].id == child.id:
            _finish(db, work, reason="verification_invalid_output", verified=False,
                submitted=True, usage=backup_usage, expected_attempt_id=child.id)
        else:
            db.rollback()
        return True
    work = _lock(db, execution_id)
    if work is None:
        db.rollback()
        return True
    execution, _batch, owner, transcript, active_attempt, _dispatch, _topics, _outcomes = work
    if (execution.status is not ConsultationSplitExecutionStatus.processing
        or active_attempt.id != child.id or active_attempt.status is not AttemptStatus.submitted
        or active_attempt.deadline_at is None or active_attempt.deadline_at <= utcnow()
        or transcript_is_expired(transcript) or not _valid_verification_stage(work)):
        db.rollback()
        return True
    execution.recoverable_response_encrypted = encrypt_json_for_existing_owner(db, owner_user_id=owner.id,
        table="consultation_split_executions", field="recoverable_response_encrypted", record_id=execution.id,
        plaintext={"text": text, "usage": backup_usage if isinstance(backup_usage, dict) else {}})
    db.commit()
    process_consultation_split_verification_execution(db, execution_id=execution_id)
    return True


def process_consultation_split_verification_execution(db: Session, *, execution_id: UUID) -> None:
    """Do one submitted checker call. Every problem is fail-open and terminal."""
    if db.in_transaction():
        raise AppError(500, "consultation_split_runtime_transaction_active", "Split verification requires a clean database session")
    with Session(bind=db.get_bind(), future=True) as read_db:
        state = read_db.get(ConsultationSplitExecution, execution_id)
        if state is not None:
            read_db.expunge(state)
    if state is None or state.kind is not ConsultationSplitExecutionKind.verification:
        return
    if state.status is ConsultationSplitExecutionStatus.processing:
        work = _lock(db, execution_id)
        if work is None:
            db.rollback(); return
        execution, batch, owner, transcript, attempt, _dispatch, topics, outcomes = work
        expected_attempt_id = attempt.id
        if execution.status is not ConsultationSplitExecutionStatus.processing:
            db.rollback()
            return
        # A persisted response is replayed locally; a no-response submitted call
        # is never repeated.
        response = read_split_execution_json(db, owner, execution=execution, field="recoverable_response_encrypted")
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            # The original submitted call may still be live.  Only lifecycle
            # deadline processing settles this uncertain state.
            db.rollback()
            return
        try:
            plan = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted") or {}
            survivors = {topic.topic_uuid: read_split_topic_outcome_output(db, owner, outcome=outcomes[topic.id])
                         for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note}
        except (AppError, UnicodeDecodeError):
            db.rollback()
            work = _lock(db, execution_id)
            if work and work[4].id == expected_attempt_id:
                _finish(db, work, reason="verification_source_binding_invalid", verified=False,
                    submitted=True, expected_attempt_id=expected_attempt_id)
            else:
                db.rollback()
            return
        try:
            corrected, count = apply_split_verification_response(payload_text=response["text"], confirmed_plan=plan, survivor_outputs=survivors)
        except (AppError, TypeError, ValueError, KeyError):
            db.rollback()
            invalid = tag_output_validation_failure(AppError(502, "consultation_split_verification_invalid", "Verification output was invalid"))
            if _try_backup_verification(db, execution_id=execution_id, error=invalid, usage=response.get("usage")):
                return
            work = _lock(db, execution_id)
            if work and work[4].id == expected_attempt_id:
                _finish(db, work, reason="verification_invalid_output", verified=False,
                    submitted=True, expected_attempt_id=expected_attempt_id)
            else:
                db.rollback()
            return
        _finish(db, work, reason=None, verified=True, corrected=corrected, correction_count=count,
            submitted=True, usage=response.get("usage"), expected_attempt_id=expected_attempt_id)
        return
    if state.status is not ConsultationSplitExecutionStatus.queued:
        return
    prepared = _prepare(db, execution_id)
    if prepared is True:
        return
    if prepared is None:
        return
    work, provider, request_and_credential, preparation_error = prepared
    if provider is None or request_and_credential is None:
        _finish(db, work, reason=preparation_error or "verification_credential_unavailable", verified=False)
        return
    execution, _batch, _owner, _transcript, attempt, _dispatch, _topics, _outcomes = work
    request, credential = request_and_credential
    submitted_attempt_id = attempt.id
    try:
        require_supported_execution_context()
    except AppError as exc:
        _finish(db, work, reason=exc.code, verified=False, submitted=False)
        return
    mark_provider_attempt_submitted(db, attempt_id=attempt.id, now=utcnow(), deadline_at=utcnow() + timedelta(seconds=_DEADLINE_SECONDS))
    execution.status = ConsultationSplitExecutionStatus.processing; execution.started_at = utcnow(); db.commit()
    try:
        with provider_attempt_execution(db, attempt_id=attempt.id, maximum_seconds=30):
            text, usage = llm_runtime.invoke_llm(snapshot=provider, credential=credential, request_body=request)
    except AppError as exc:
        tagged = tag_provider_failure(exc)
        if _try_backup_verification(db, execution_id=execution_id, error=tagged):
            return
        work = _lock(db, execution_id)
        if work and work[4].id == submitted_attempt_id:
            _finish(db, work, reason="verification_provider_failed", verified=False,
                submitted=True, expected_attempt_id=submitted_attempt_id)
        else:
            db.rollback()
        return
    except Exception:
        work = _lock(db, execution_id)
        if work and work[4].id == submitted_attempt_id:
            _finish(db, work, reason="verification_provider_failed", verified=False,
                submitted=True, expected_attempt_id=submitted_attempt_id)
        else:
            db.rollback()
        return
    work = _lock(db, execution_id)
    if work is None: db.rollback(); return
    execution, _batch, owner, _transcript, _attempt, _dispatch, _topics, _outcomes = work
    if (
        execution.status is not ConsultationSplitExecutionStatus.processing
        or _attempt.id != submitted_attempt_id
        or _attempt.status is not AttemptStatus.submitted
        or _attempt.deadline_at is None
        or _attempt.deadline_at <= utcnow()
        or transcript_is_expired(_transcript)
        or not _valid_verification_stage(work)
    ):
        # A lifecycle worker won the race. Its terminal decision is
        # authoritative; discard the late provider response without writes.
        db.rollback()
        return
    if not isinstance(text, str):
        invalid = tag_output_validation_failure(AppError(502, "consultation_split_verification_invalid", "Verification output was invalid"))
        if _try_backup_verification(db, execution_id=execution_id, error=invalid, usage=usage):
            return
        work = _lock(db, execution_id)
        if work and work[4].id == submitted_attempt_id:
            _finish(db, work, reason="verification_invalid_output", verified=False,
                submitted=True, usage=usage, expected_attempt_id=submitted_attempt_id)
        else:
            db.rollback()
        return
    execution.recoverable_response_encrypted = encrypt_json_for_existing_owner(db, owner_user_id=owner.id,
        table="consultation_split_executions", field="recoverable_response_encrypted", record_id=execution.id,
        plaintext={"text": text, "usage": usage if isinstance(usage, dict) else {}})
    db.commit()
    process_consultation_split_verification_execution(db, execution_id=execution_id)
