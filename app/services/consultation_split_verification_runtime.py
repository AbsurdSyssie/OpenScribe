"""At-most-once, fail-open runtime for bundled split verification."""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (AttemptOutcome, AttemptStatus, ConsultationSplitBatch,
    ConsultationSplitBatchStatus, ConsultationSplitBatchTopic, ConsultationSplitExecution,
    ConsultationSplitExecutionKind, ConsultationSplitExecutionStatus,
    ConsultationSplitTopicDisposition, ConsultationSplitTopicOutcome,
    ConsultationSplitTopicOutcomeStatus, ConsultationSplitVerificationStatus,
    ProviderAttempt, TeamLlmConfig, Transcript, User, utcnow)
from app.services.consultation_split_partial import _materialize_available_split_notes_locked
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
from app.services.quotas import (cancel_provider_attempt, mark_provider_attempt_submitted,
    settle_provider_attempt_tokens, settle_provider_attempt_unknown_tokens)
from app.services.transcripts import transcript_is_expired

_DEADLINE_SECONDS = 600


def _credential_ok(config: TeamLlmConfig | None, credential: object | None) -> bool:
    if config is None:
        return False
    mode = config.auth_mode.value
    return ((mode in {"none", "google_adc"} and credential is None)
            or (mode == "bearer" and isinstance(credential, str) and bool(credential.strip()))
            or (mode == "google_service_account" and credential is not None))


def _lock(db: Session, execution_id: UUID):
    execution = db.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.id == execution_id).with_for_update())
    if execution is None or execution.kind is not ConsultationSplitExecutionKind.verification or execution.batch_id is None:
        return None
    batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.id == execution.batch_id).with_for_update())
    owner = db.scalar(select(User).where(User.id == execution.owner_user_id).with_for_update())
    transcript = db.scalar(select(Transcript).where(Transcript.id == execution.transcript_id).with_for_update())
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution.id).with_for_update())
    topics = db.scalars(select(ConsultationSplitBatchTopic).where(ConsultationSplitBatchTopic.batch_id == execution.batch_id).with_for_update()).all() if batch else []
    outcomes = db.scalars(select(ConsultationSplitTopicOutcome).where(ConsultationSplitTopicOutcome.batch_topic_id.in_([x.id for x in topics])).with_for_update()).all() if topics else []
    if not all((batch, owner, transcript, attempt)) or len(outcomes) != len(topics):
        return None
    return execution, batch, owner, transcript, attempt, topics, {x.batch_topic_id: x for x in outcomes}


def _valid_verification_stage(work) -> bool:
    """Only a queue-created verification stage may materialize survivors."""
    execution, batch, _owner, _transcript, _attempt, topics, outcomes = work
    if (execution.kind is not ConsultationSplitExecutionKind.verification
            or batch.status is not ConsultationSplitBatchStatus.verifying
            or batch.verification_status is not ConsultationSplitVerificationStatus.verifying):
        return False
    separate = [topic for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note]
    return bool(separate) and any(
        outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated
        for topic in separate
    )


def _finish(db: Session, work, *, reason: str | None, verified: bool, corrected: dict | None = None,
            correction_count: int | None = None, submitted: bool = False, usage: dict | None = None) -> None:
    execution, batch, owner, transcript, attempt, topics, outcomes = work
    now = utcnow()
    valid_stage = _valid_verification_stage(work)
    if submitted:
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            settle_provider_attempt_tokens(db, attempt_id=attempt.id, reported_total_tokens=usage["total_tokens"],
                reported_input_tokens=usage.get("input_tokens"), reported_output_tokens=usage.get("output_tokens"),
                outcome=AttemptOutcome.succeeded if verified else AttemptOutcome.failed)
        else:
            settle_provider_attempt_unknown_tokens(db, attempt_id=attempt.id)
    else:
        cancel_provider_attempt(db, attempt_id=attempt.id, now=now)
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


def fail_open_verification_execution(db: Session, *, execution_id: UUID, reason: str) -> bool:
    """Terminalize already-settled/cancelled verification without a provider call.

    Quota lifecycle invokes this after it has settled the attempt.  It only
    materializes immutable accepted outcomes, so it cannot consume another
    reservation or revive/retry a submitted provider request.
    """
    work = _lock(db, execution_id)
    if work is None:
        db.rollback()
        return False
    execution, batch, owner, transcript, _attempt, topics, outcomes = work
    if execution.status not in {ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing}:
        db.rollback()
        return False
    now = utcnow()
    valid_stage = _valid_verification_stage(work)
    execution.status = ConsultationSplitExecutionStatus.failed
    execution.error_code = reason
    execution.completed_at = now
    execution.recoverable_response_encrypted = None
    if not valid_stage:
        db.commit()
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
    try:
        credential = resolve_generation_credential(config) if config is not None else None
    except Exception:
        credential = None
    work = _lock(db, execution_id)
    if work is None:
        db.rollback()
        return None
    execution, batch, owner, transcript, attempt, topics, outcomes = work
    if not _valid_verification_stage(work):
        return work, None, None, "verification_provider_config_invalid"
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
        if request != canonical_request or not _credential_ok(config, credential):
            raise ValueError("binding")
    except Exception:
        return work, None, None, "verification_provider_binding_invalid"
    return work, provider, (canonical_request, credential), None


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
        execution, batch, owner, transcript, attempt, topics, outcomes = work
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
            corrected, count = apply_split_verification_response(payload_text=response["text"], confirmed_plan=plan, survivor_outputs=survivors)
            _finish(db, work, reason=None, verified=True, corrected=corrected, correction_count=count, submitted=True, usage=response.get("usage"))
        except Exception:
            db.rollback(); work = _lock(db, execution_id)
            if work: _finish(db, work, reason="verification_invalid_output", verified=False, submitted=True)
        return
    if state.status is not ConsultationSplitExecutionStatus.queued:
        return
    prepared = _prepare(db, execution_id)
    if prepared is None:
        return
    work, provider, request_and_credential, preparation_error = prepared
    if provider is None or request_and_credential is None:
        _finish(db, work, reason=preparation_error or "verification_credential_unavailable", verified=False)
        return
    execution, _batch, _owner, _transcript, attempt, _topics, _outcomes = work
    request, credential = request_and_credential
    mark_provider_attempt_submitted(db, attempt_id=attempt.id, now=utcnow(), deadline_at=utcnow() + timedelta(seconds=_DEADLINE_SECONDS))
    execution.status = ConsultationSplitExecutionStatus.processing; execution.started_at = utcnow(); db.commit()
    try:
        text, usage = llm_runtime.invoke_llm(snapshot=provider, credential=credential, request_body=request)
    except Exception:
        work = _lock(db, execution_id)
        if work: _finish(db, work, reason="verification_provider_failed", verified=False, submitted=True)
        return
    work = _lock(db, execution_id)
    if work is None: db.rollback(); return
    execution, _batch, owner, _transcript, _attempt, _topics, _outcomes = work
    if (
        execution.status is not ConsultationSplitExecutionStatus.processing
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
        _finish(db, work, reason="verification_invalid_output", verified=False, submitted=True)
        return
    execution.recoverable_response_encrypted = encrypt_json_for_existing_owner(db, owner_user_id=owner.id,
        table="consultation_split_executions", field="recoverable_response_encrypted", record_id=execution.id,
        plaintext={"text": text, "usage": usage if isinstance(usage, dict) else {}})
    db.commit()
    process_consultation_split_verification_execution(db, execution_id=execution_id)
