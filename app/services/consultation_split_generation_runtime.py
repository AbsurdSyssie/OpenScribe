"""At-most-once runtime for the confirmed split-generation execution.

All provider input comes from encrypted confirmation snapshots.  This is kept
separate from analysis runtime because generation persists ordinary documents.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (AttemptOutcome, AttemptStatus, ConsultationSplitAnalysis, ConsultationSplitBatch, ConsultationSplitBatchTopic,
    ConsultationSplitBatchStatus, ConsultationSplitExecution, ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus, ConsultationSplitTopicDisposition, ConsultationSplitTopicOutcome,
    ConsultationSplitTopicOutcomeStatus, GeneratedDocument, GeneratedDocumentGeneratorType,
    GeneratedDocumentSection, GeneratedDocumentStatus, ProviderAttempt, ProviderFeatureType,
    ProviderUsageEvent, ProviderUsageEventType, TaskDispatchOutbox, TaskDispatchSourceKind,
    TaskDispatchState, TeamLlmConfig, TemplateMode, Transcript, TranscriptVersion, User, utcnow)
from app.services.consultation_split_generation import (SplitGenerationTopic, parse_split_generation_partial,
    prepare_split_generation_request, prepare_split_generation_recovery_request)
from app.services.consultation_splits import read_split_topic_outcome_output
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import read_split_batch_json, read_split_batch_phi_index, read_split_execution_json
from app.services.content_crypto import encrypt_json_for_existing_owner, encrypt_text_for_owner
from app.services.llm_adapters import runtime as llm_runtime
from app.services.llm_adapters.runtime import generation_request_snapshot, validate_provider_snapshot_for_config
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.llm_credentials import resolve_generation_credential
from app.services.consultation_split_pre_submit import _credential_config_identity
from app.services.provider_errors import safe_provider_error_code
from app.services.quotas import cancel_provider_attempt, mark_provider_attempt_submitted, settle_provider_attempt_tokens, settle_provider_attempt_unknown_tokens
from app.services.task_outbox import cancel_pending_task_dispatch
from app.services.templates import _transcript_title_can_be_auto_filled
from app.services.transcripts import transcript_is_expired
from app.services.redaction import reidentify_text

SPLIT_GENERATION_PROVIDER_DEADLINE_SECONDS = 600
RECOVERABLE_GENERATION_RESPONSE_MAX_CHARS = 2_097_152


GENERIC_SPLIT_DOCUMENT_TITLE = "Consultation split note"
GenerationOutcome = Literal["ready", "failed", "in_flight", "noop"]

@dataclass(frozen=True, slots=True)
class SplitGenerationRuntimeResult:
    outcome: GenerationOutcome
    execution_id: UUID
    error_code: str | None = None

@dataclass(slots=True)
class _Work:
    owner: User; transcript: Transcript; batch: ConsultationSplitBatch; analysis: ConsultationSplitAnalysis
    execution: ConsultationSplitExecution; attempt: ProviderAttempt; dispatch: TaskDispatchOutbox
    topics: list[object]; outcomes: dict[UUID, ConsultationSplitTopicOutcome]
    materialization_transcript_version: TranscriptVersion | None

def _usage(value: object) -> dict[str, int | None]:
    raw = value if isinstance(value, dict) else {}
    def number(key: str) -> int | None:
        item = raw.get(key)
        return item if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 10_000_000 else None
    result = {key: number(key) for key in ("input_tokens", "output_tokens", "total_tokens", "duration_ms", "provider_duration_ms")}
    if (
        result["total_tokens"] is not None
        and result["input_tokens"] is not None
        and result["output_tokens"] is not None
        and result["total_tokens"] < result["input_tokens"] + result["output_tokens"]
    ):
        result["total_tokens"] = None
    return result

def _credential_ok(config: TeamLlmConfig, credential: object | None) -> bool:
    mode = config.auth_mode.value
    return ((mode in {"none", "google_adc"} and credential is None)
            or (mode == "bearer" and isinstance(credential, str) and bool(credential.strip()))
            or (mode == "google_service_account" and credential is not None))

def _lock_work(db: Session, execution_id: UUID, *, status: ConsultationSplitExecutionStatus) -> _Work | None:
    identity = db.get(ConsultationSplitExecution, execution_id)
    if identity is None:
        return None
    scope = lock_consultation_split_source_scope(db, owner_user_id=identity.owner_user_id, transcript_id=identity.transcript_id)
    if scope is None:
        return None
    owner, transcript = scope.owner, scope.transcript
    execution = db.scalar(
        select(ConsultationSplitExecution)
        .where(ConsultationSplitExecution.id == execution_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if execution is None or execution.kind is not ConsultationSplitExecutionKind.generation or execution.status is not status or execution.batch_id is None:
        return None
    batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.id == execution.batch_id).execution_options(populate_existing=True).with_for_update())
    analysis = db.scalar(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.id == batch.analysis_id).execution_options(populate_existing=True).with_for_update()) if batch else None
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution_id).execution_options(populate_existing=True).with_for_update())
    dispatch = db.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution, TaskDispatchOutbox.source_id == execution_id).execution_options(populate_existing=True).with_for_update())
    if not all((batch, analysis, attempt, dispatch)) or attempt.attempt_kind.value != "consultation_split_generation":
        return None
    topics = db.scalars(
        select(ConsultationSplitBatchTopic)
        .where(ConsultationSplitBatchTopic.batch_id == batch.id)
        .order_by(ConsultationSplitBatchTopic.topic_order)
        .with_for_update()
    ).all()
    outcomes = db.scalars(select(ConsultationSplitTopicOutcome).where(ConsultationSplitTopicOutcome.batch_topic_id.in_([topic.id for topic in topics])).with_for_update()).all()
    if (owner.is_system_admin or owner.team_id != transcript.team_id or batch.owner_user_id != owner.id or batch.team_id != owner.team_id
        or analysis.owner_user_id != owner.id or execution.owner_user_id != owner.id or execution.team_id != owner.team_id
        or batch.transcript_id != transcript.id or analysis.transcript_id != transcript.id
        or execution.retention_expires_at != transcript.retention_expires_at or batch.retention_expires_at != transcript.retention_expires_at
        or analysis.retention_expires_at != transcript.retention_expires_at or len(outcomes) != len(topics)
        or dispatch.dispatch_kind.value != "consultation_split_generation" or dispatch.state not in {TaskDispatchState.pending, TaskDispatchState.published}):
        return None
    materialization_version = (
        db.scalar(
            select(TranscriptVersion)
            .where(TranscriptVersion.id == batch.materialization_transcript_version_id)
            .with_for_update()
        )
        if batch.materialization_transcript_version_id is not None
        else None
    )
    return _Work(
        owner, transcript, batch, analysis, execution, attempt, dispatch, topics,
        {outcome.batch_topic_id: outcome for outcome in outcomes}, materialization_version,
    )


def _has_valid_materialization_binding(work: _Work) -> bool:
    """Require the confirmation-bound version; workers must never create one."""
    version = work.materialization_transcript_version
    return (
        version is not None
        and work.batch.materialization_transcript_version_id == version.id
        and version.transcript_id == work.transcript.id
        and work.batch.retention_expires_at == work.transcript.retention_expires_at
        and work.execution.retention_expires_at == work.transcript.retention_expires_at
    )

def _terminal(db: Session, work: _Work, code: str, *, submitted: bool, usage: dict[str, int | None] | None = None,
              retain_partial_for_recovery: bool = False) -> SplitGenerationRuntimeResult:
    now = utcnow()
    if submitted:
        if usage and usage["total_tokens"] is not None:
            settle_provider_attempt_tokens(db, attempt_id=work.attempt.id, reported_total_tokens=usage["total_tokens"], reported_input_tokens=usage["input_tokens"], reported_output_tokens=usage["output_tokens"], outcome=AttemptOutcome.failed)
        else: settle_provider_attempt_unknown_tokens(db, attempt_id=work.attempt.id)
        event_type = ProviderUsageEventType.failed
    else:
        cancel_provider_attempt(db, attempt_id=work.attempt.id, now=now)
        if work.dispatch.state is TaskDispatchState.pending: cancel_pending_task_dispatch(db, task_id=work.dispatch.task_id)
        event_type = None
    work.execution.status = ConsultationSplitExecutionStatus.failed
    work.execution.error_code = code; work.execution.completed_at = now
    recovery = retain_partial_for_recovery or (work.execution.attempt_no > 1 and work.batch.status in {
        ConsultationSplitBatchStatus.partially_ready, ConsultationSplitBatchStatus.generating,
    })
    work.batch.status = ConsultationSplitBatchStatus.partially_ready if recovery else ConsultationSplitBatchStatus.failed
    work.batch.error_code = code; work.batch.completed_at = now
    for outcome in work.outcomes.values():
        if recovery and outcome.status is ConsultationSplitTopicOutcomeStatus.validated:
            continue
        outcome.status = ConsultationSplitTopicOutcomeStatus.failed; outcome.error_code = code
    if event_type:
        db.add(ProviderUsageEvent(team_id=work.transcript.team_id, owner_user_id=work.owner.id, transcript_id=work.transcript.id,
            consultation_split_execution_id=work.execution.id, llm_config_id=work.execution.llm_config_id,
            feature_type=ProviderFeatureType.consultation_split_generation, event_type=event_type, status="failed",
            provider_adapter=work.execution.provider_adapter, model_name=work.execution.provider_model, error_code=code,
            prompt_tokens=usage["input_tokens"] if usage else None, completion_tokens=usage["output_tokens"] if usage else None,
            total_tokens=usage["total_tokens"] if usage else None))
    db.commit()
    # A recovery has now failed terminally, so its survivor set cannot change.
    # Queue the optional checker only at this stable boundary; never let a
    # checker race an active recovery and snapshot an obsolete subset.
    if recovery and work.execution.attempt_no > 1:
        _queue_verification_after_terminal_recovery(db, work)
    return SplitGenerationRuntimeResult("failed", work.execution.id, code)


def _queue_verification_after_terminal_recovery(db: Session, work: _Work) -> None:
    """Queue optional verification after a recovery has reached a stable end."""
    scope = lock_consultation_split_source_scope(
        db, owner_user_id=work.owner.id, transcript_id=work.transcript.id,
    )
    if scope is None:
        db.rollback()
        return
    batch = db.scalar(select(ConsultationSplitBatch).where(
        ConsultationSplitBatch.id == work.batch.id,
        ConsultationSplitBatch.status == ConsultationSplitBatchStatus.partially_ready,
    ).with_for_update())
    if batch is None:
        db.rollback()
        return
    from app.services.consultation_split_verification_queue import queue_split_verification_or_mark_unchecked
    queued = queue_split_verification_or_mark_unchecked(
        db, batch=batch, target_status=ConsultationSplitBatchStatus.partially_ready,
    )
    if not queued.checked:
        batch.status = ConsultationSplitBatchStatus.partially_ready
    db.commit()
    if queued.dispatch_task_id is not None:
        from app.services.task_outbox import try_publish_task_dispatch_safely
        try_publish_task_dispatch_safely(queued.dispatch_task_id)

def _prepared(db: Session, execution_id: UUID) -> tuple[_Work, LlmProviderSnapshot, dict[str, object], object] | SplitGenerationRuntimeResult | None:
    # Read the config in a separate session so vault resolution cannot occur in
    # the final database transaction.
    if db.in_transaction():
        raise AppError(500, "consultation_split_pre_submit_transaction_active", "Split generation preparation requires a clean database session")
    with Session(bind=db.get_bind(), future=True) as lookup:
        execution = lookup.get(ConsultationSplitExecution, execution_id)
        config = lookup.get(TeamLlmConfig, execution.llm_config_id) if execution and execution.llm_config_id else None
        preliminary_identity = _credential_config_identity(config) if config is not None else None
        if config: lookup.expunge(config)
    credential = None
    try: credential = resolve_generation_credential(config) if config else None
    except (AppError, TypeError, ValueError): pass
    work = _lock_work(db, execution_id, status=ConsultationSplitExecutionStatus.queued)
    if work is None: db.rollback(); return None
    now = utcnow()
    if transcript_is_expired(work.transcript, now=now) or work.attempt.reservation_valid_until <= now:
        return _terminal(db, work, "consultation_split_source_expired" if transcript_is_expired(work.transcript, now=now) else "consultation_split_reservation_expired", submitted=False)
    if not _has_valid_materialization_binding(work):
        return _terminal(db, work, "consultation_split_materialization_binding_invalid", submitted=False)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == work.execution.llm_config_id).with_for_update()) if work.execution.llm_config_id else None
    if config is None or config.team_id != work.transcript.team_id:
        return _terminal(db, work, "consultation_split_provider_config_invalid", submitted=False)
    if preliminary_identity is None or _credential_config_identity(config) != preliminary_identity:
        return _terminal(db, work, "consultation_split_provider_config_invalid", submitted=False)
    try:
        snapshot_data = read_split_execution_json(db, work.owner, execution=work.execution, field="provider_snapshot_encrypted")
        snapshot_data_batch = read_split_batch_json(db, work.owner, batch=work.batch, field="provider_snapshot_encrypted")
        validated = validate_provider_snapshot_for_config(snapshot_data, config=config, expected_model=work.execution.provider_model)
        if validated != snapshot_data_batch: raise AppError(422, "invalid", "invalid")
        provider = LlmProviderSnapshot(**validated)
        source = read_split_batch_json(db, work.owner, batch=work.batch, field="source_snapshot_encrypted") or {}
        clinical = read_split_batch_json(db, work.owner, batch=work.batch, field="clinical_snapshot_encrypted") or {}
        plan = read_split_batch_json(db, work.owner, batch=work.batch, field="confirmed_plan_encrypted") or {}
        options = read_split_batch_json(db, work.owner, batch=work.batch, field="note_options_snapshot_encrypted") or {}
        if work.execution.attempt_no == 1:
            prepared = prepare_split_generation_request(source_snapshot=source, clinical_snapshot=clinical, confirmed_plan=plan, note_options_snapshot=options)
        else:
            failed = [topic.topic_uuid for topic in work.topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note and work.outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.failed]
            siblings = {topic.topic_uuid: read_split_topic_outcome_output(db, work.owner, outcome=work.outcomes[topic.id])
                        for topic in work.topics if work.outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated}
            prepared = prepare_split_generation_recovery_request(source_snapshot=source, clinical_snapshot=clinical, confirmed_plan=plan, note_options_snapshot=options, failed_topic_uuids=failed, accepted_sibling_outputs=siblings)
        messages = prepared.request_body["messages"]
        request = generation_request_snapshot(adapter_kind=config.adapter_kind, model=provider.model, user_id=work.owner.id, system_message=messages[0]["content"], user_message=messages[1]["content"], output_token_cap=prepared.output_token_cap, response_json_schema=prepared.response_json_schema)
        if request != read_split_execution_json(db, work.owner, execution=work.execution, field="request_payload_encrypted"): raise AppError(422, "invalid", "invalid")
    except (AppError, UnicodeDecodeError, KeyError, TypeError):
        return _terminal(db, work, "consultation_split_provider_binding_invalid", submitted=False)
    if not _credential_ok(config, credential): return _terminal(db, work, "consultation_split_credential_unavailable", submitted=False)
    return work, provider, request, credential

def _submit(db: Session, work: _Work) -> bool:
    now = utcnow()
    allowed_batch_statuses = {ConsultationSplitBatchStatus.generation_queued, ConsultationSplitBatchStatus.partially_ready}
    if work.batch.status not in allowed_batch_statuses or work.attempt.status is not AttemptStatus.reserved: db.rollback(); return False
    mark_provider_attempt_submitted(db, attempt_id=work.attempt.id, now=now, deadline_at=now + timedelta(seconds=SPLIT_GENERATION_PROVIDER_DEADLINE_SECONDS))
    work.execution.status = ConsultationSplitExecutionStatus.processing; work.execution.started_at = now; work.batch.status = ConsultationSplitBatchStatus.generating
    db.commit(); return True

def _topics_from_plan(plan: dict[str, object]) -> list[SplitGenerationTopic]:
    result=[]
    for raw in plan.get("topics", []):
        if raw.get("disposition") != "separate_note": continue
        template=raw["template"]; sections=template["structured_sections"]
        keys=tuple(item["section_key"] for item in sections["sections"]) if template["mode"] == "structured" else ()
        result.append(SplitGenerationTopic(UUID(raw["topic_uuid"]), template["mode"], keys))
    return result

def _finalize(db: Session, execution_id: UUID) -> SplitGenerationRuntimeResult:
    work = _lock_work(db, execution_id, status=ConsultationSplitExecutionStatus.processing)
    if work is None: db.rollback(); return SplitGenerationRuntimeResult("noop", execution_id)
    if (
        work.attempt.status is not AttemptStatus.submitted
        or work.attempt.deadline_at is None
        or work.attempt.deadline_at <= utcnow()
        or transcript_is_expired(work.transcript)
    ):
        db.rollback()
        return SplitGenerationRuntimeResult("in_flight", execution_id)
    if not _has_valid_materialization_binding(work):
        return _terminal(db, work, "consultation_split_materialization_binding_invalid", submitted=True)
    parsed_response = False
    try:
        response=read_split_execution_json(db, work.owner, execution=work.execution, field="recoverable_response_encrypted")
        text=response["text"]; usage=_usage(response.get("usage")); plan=read_split_batch_json(db, work.owner, batch=work.batch, field="confirmed_plan_encrypted")
        by_uuid={topic.topic_uuid: topic for topic in work.topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note}
        target_uuids = {topic.topic_uuid for topic in work.topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note and work.outcomes[topic.id].status in {ConsultationSplitTopicOutcomeStatus.pending, ConsultationSplitTopicOutcomeStatus.failed}}
        # A recovery request contains only failed siblings. Parse it against the
        # same target set, not the full confirmed plan: retained validated
        # siblings are immutable context and are intentionally absent from the
        # provider response.
        parsed=parse_split_generation_partial(
            text,
            topics=[topic for topic in _topics_from_plan(plan or {}) if topic.topic_uuid in target_uuids],
        )
        parsed_response = True
        if target_uuids != {note.topic_uuid for note in parsed.notes} | set(parsed.failed_topic_uuids):
            raise AppError(502, "invalid", "invalid")

        # Split notes keep generic persisted document titles.  The one
        # provider-generated title belongs to the transcript and follows the
        # same auto-fill rule as ordinary note generation.  Apply it before
        # either partial recovery or final materialization is committed.
        phi_index = read_split_batch_phi_index(db, work.owner, batch=work.batch)
        if _transcript_title_can_be_auto_filled(work.transcript.title):
            work.transcript.title = reidentify_text(parsed.title, phi_index=phi_index)[:255]
            db.add(work.transcript)

        # A trustworthy envelope lets us retain independently valid work before
        # retrying missing siblings.  The accepted execution and ciphertext are
        # write-once: a retry cannot replace either value.
        for note in parsed.notes:
            topic=by_uuid[note.topic_uuid]; outcome=work.outcomes[topic.id]
            if outcome.status not in {ConsultationSplitTopicOutcomeStatus.pending, ConsultationSplitTopicOutcomeStatus.failed}: raise AppError(502, "invalid", "invalid")
            template=next(item["template"] for item in plan["topics"] if UUID(item["topic_uuid"]) == note.topic_uuid)
            rendered = note.content if isinstance(note.content, str) else json.dumps(note.content, separators=(",", ":"), sort_keys=True)
            outcome.output_encrypted=encrypt_json_for_existing_owner(
                db, owner_user_id=work.owner.id, table="consultation_split_topic_outcomes",
                field="output_encrypted", record_id=outcome.id,
                plaintext={"content": note.content, "mode": note.mode},
            )
            outcome.accepted_execution_id = work.execution.id
            outcome.status=ConsultationSplitTopicOutcomeStatus.validated

        for topic_uuid in parsed.failed_topic_uuids:
            outcome = work.outcomes[by_uuid[topic_uuid].id]
            if outcome.status not in {ConsultationSplitTopicOutcomeStatus.pending, ConsultationSplitTopicOutcomeStatus.failed}:
                raise AppError(502, "invalid", "invalid")
            outcome.status = ConsultationSplitTopicOutcomeStatus.failed
            outcome.error_code = "consultation_split_generation_invalid_output"

        if parsed.failed_topic_uuids:
            # This execution did return a durable provider response, so it is
            # safely settled once.  Recovery is a separate, explicit execution
            # and never reuses this attempt or overwrites its siblings.
            settle_provider_attempt_tokens(db, attempt_id=work.attempt.id, reported_total_tokens=usage["total_tokens"] or 0, reported_input_tokens=usage["input_tokens"], reported_output_tokens=usage["output_tokens"], outcome=AttemptOutcome.succeeded) if usage["total_tokens"] is not None else settle_provider_attempt_unknown_tokens(db, attempt_id=work.attempt.id)
            now = utcnow()
            work.execution.status = ConsultationSplitExecutionStatus.completed
            work.execution.completed_at = now
            work.execution.recoverable_response_encrypted = None
            # The initial durable partial result is available for the sole
            # recovery decision. The queued recovery will move it back to
            # generating only when it has claimed its own submission.
            work.batch.status = ConsultationSplitBatchStatus.partially_ready
            work.batch.error_code = "consultation_split_generation_invalid_output"
            db.add(ProviderUsageEvent(team_id=work.transcript.team_id, owner_user_id=work.owner.id, transcript_id=work.transcript.id, consultation_split_execution_id=work.execution.id, llm_config_id=work.execution.llm_config_id, feature_type=ProviderFeatureType.consultation_split_generation, event_type=ProviderUsageEventType.completed, status="partially_ready", provider_adapter=work.execution.provider_adapter, model_name=work.execution.provider_model, prompt_tokens=usage["input_tokens"], completion_tokens=usage["output_tokens"], total_tokens=usage["total_tokens"]))
            db.commit()
            # A parsed, encrypted response is the only automatic-retry proof.
            # Do this before any optional verification. Queue construction
            # independently proves that attempt two has never existed.
            if work.execution.attempt_no == 1:
                from app.services.consultation_split_recovery import queue_automatic_split_recovery
                automatic = queue_automatic_split_recovery(
                    db, work.owner, transcript_id=work.transcript.id,
                    batch_id=work.batch.id, execution_id=work.execution.id,
                )
                if automatic is not None:
                    return SplitGenerationRuntimeResult("ready", execution_id)
            # Either this recovery is terminal, or the automatic decision could
            # not safely create attempt two. The survivor set is now stable.
            _queue_verification_after_terminal_recovery(db, work)
            return SplitGenerationRuntimeResult("ready", execution_id)

        # All separate-note topics validated in this one transaction.  Recovery
        # retains earlier accepted ciphertext, so materialize the union rather
        # than silently omitting surviving siblings.
        if work.execution.attempt_no > 1:
            # The recovery's response has fixed the complete survivor set. Mark
            # its provider attempt and execution terminal before constructing a
            # checker snapshot, so the checker cannot coexist with an active
            # recovery for this stage.
            if usage["total_tokens"] is not None:
                settle_provider_attempt_tokens(db, attempt_id=work.attempt.id,
                    reported_total_tokens=usage["total_tokens"] or 0,
                    reported_input_tokens=usage["input_tokens"],
                    reported_output_tokens=usage["output_tokens"], outcome=AttemptOutcome.succeeded)
            else:
                settle_provider_attempt_unknown_tokens(db, attempt_id=work.attempt.id)
            now = utcnow()
            work.execution.status = ConsultationSplitExecutionStatus.completed
            work.execution.completed_at = now
            work.execution.recoverable_response_encrypted = None
            work.batch.status = ConsultationSplitBatchStatus.partially_ready
            # The checker queue performs an independent active-recovery query.
            # Flush this terminal transition first so it cannot see this same
            # execution as an active sibling on any supported session setting.
            db.flush()
        from app.services.consultation_split_verification_queue import queue_split_verification_or_mark_unchecked
        queued_verification = queue_split_verification_or_mark_unchecked(
            db, batch=work.batch, target_status=ConsultationSplitBatchStatus.ready)
        if queued_verification.checked:
            settle_provider_attempt_tokens(db, attempt_id=work.attempt.id, reported_total_tokens=usage["total_tokens"] or 0, reported_input_tokens=usage["input_tokens"], reported_output_tokens=usage["output_tokens"], outcome=AttemptOutcome.succeeded) if usage["total_tokens"] is not None else settle_provider_attempt_unknown_tokens(db, attempt_id=work.attempt.id)
            now = utcnow(); work.execution.status = ConsultationSplitExecutionStatus.completed; work.execution.completed_at = now; work.execution.recoverable_response_encrypted = None
            db.commit()
            if queued_verification.dispatch_task_id is not None:
                from app.services.task_outbox import try_publish_task_dispatch_safely
                try_publish_task_dispatch_safely(queued_verification.dispatch_task_id)
            return SplitGenerationRuntimeResult("ready", execution_id)
        complete_notes = list(parsed.notes)
        present = {note.topic_uuid for note in complete_notes}
        for topic in work.topics:
            if topic.disposition is not ConsultationSplitTopicDisposition.separate_note or topic.topic_uuid in present:
                continue
            outcome = work.outcomes[topic.id]
            if outcome.status is not ConsultationSplitTopicOutcomeStatus.validated:
                raise AppError(502, "invalid", "invalid")
            retained = read_split_topic_outcome_output(db, work.owner, outcome=outcome)
            if not isinstance(retained, dict):
                raise AppError(502, "invalid", "invalid")
            mode, content = retained.get("mode"), retained.get("content")
            if mode not in {"freeform", "structured"}:
                raise AppError(502, "invalid", "invalid")
            from app.services.consultation_split_generation import SplitGeneratedNote
            complete_notes.append(SplitGeneratedNote(topic_uuid=topic.topic_uuid, mode=mode, content=content))
        # Only now may documents be materialized. Provider output remains
        # redacted in the immutable outcome; the owner-facing document gets
        # its frozen placeholders restored at this final boundary.
        for note in complete_notes:
            topic=by_uuid[note.topic_uuid]; outcome=work.outcomes[topic.id]
            template=next(item["template"] for item in plan["topics"] if UUID(item["topic_uuid"]) == note.topic_uuid)
            content = (
                reidentify_text(note.content, phi_index=phi_index)
                if isinstance(note.content, str)
                else {key: reidentify_text(value, phi_index=phi_index) for key, value in note.content.items()}
            )
            rendered = content if isinstance(content, str) else json.dumps(content, separators=(",", ":"), sort_keys=True)
            doc=GeneratedDocument(id=uuid4(), owner_user_id=work.owner.id, team_id=work.transcript.team_id, transcript_id=work.transcript.id, transcript_version_id=work.materialization_transcript_version.id, redaction_run_id=work.analysis.redaction_run_id, consultation_split_batch_topic_id=topic.id, consultation_split_topic_uuid=topic.topic_uuid, generator_type=GeneratedDocumentGeneratorType.template, template_version_id=None, llm_config_id=work.execution.llm_config_id, source_template_name=GENERIC_SPLIT_DOCUMENT_TITLE, prompt_snapshot_text=None, status=GeneratedDocumentStatus.ready, title=GENERIC_SPLIT_DOCUMENT_TITLE, document_mode=TemplateMode(note.mode), original_output_text_encrypted="", edited_output_text_encrypted="", retention_expires_at=work.transcript.retention_expires_at, model_used=work.execution.provider_model, llm_adapter_kind=work.execution.provider_adapter, llm_base_url=work.execution.provider_base_url)
            doc.regeneration_lineage_id = doc.id
            doc.original_output_text_encrypted=encrypt_text_for_owner(db, owner_user_id=work.owner.id, table="generated_documents", field="original_output_text_encrypted", record_id=doc.id, plaintext=rendered) or ""
            doc.edited_output_text_encrypted=encrypt_text_for_owner(db, owner_user_id=work.owner.id, table="generated_documents", field="edited_output_text_encrypted", record_id=doc.id, plaintext=rendered) or ""
            db.add(doc)
            if isinstance(content, dict):
                for index, definition in enumerate(template["structured_sections"]["sections"]):
                    key=definition["section_key"]; section=GeneratedDocumentSection(id=uuid4(), generated_document_id=doc.id, section_key=key, section_label=definition["section_label"], section_order=definition["section_order"], original_text_encrypted="", edited_text_encrypted="")
                    section.original_text_encrypted=encrypt_text_for_owner(db, owner_user_id=work.owner.id, table="generated_document_sections", field="original_text_encrypted", record_id=section.id, plaintext=content[key]) or ""; section.edited_text_encrypted=encrypt_text_for_owner(db, owner_user_id=work.owner.id, table="generated_document_sections", field="edited_text_encrypted", record_id=section.id, plaintext=content[key]) or ""; db.add(section)
            # Preserve the accepted ciphertext byte-for-byte.  Materialization
            # adds a document; it never rewrites provider output.
            outcome.status = ConsultationSplitTopicOutcomeStatus.ready
        # The schema has only pending/ready/failed.  Non-document dispositions
        # therefore reach the existing terminal ready state with encrypted,
        # metadata-only disposition evidence; they never remain pending.
        for topic in work.topics:
            if topic.disposition is ConsultationSplitTopicDisposition.separate_note:
                continue
            outcome = work.outcomes[topic.id]
            if outcome.status is ConsultationSplitTopicOutcomeStatus.ready:
                continue
            if outcome.status is not ConsultationSplitTopicOutcomeStatus.pending:
                raise AppError(502, "invalid", "invalid")
            outcome.output_encrypted = encrypt_json_for_existing_owner(
                db, owner_user_id=work.owner.id, table="consultation_split_topic_outcomes",
                field="output_encrypted", record_id=outcome.id,
                plaintext={"disposition": topic.disposition.value},
            )
            outcome.accepted_execution_id = work.execution.id
            outcome.status = ConsultationSplitTopicOutcomeStatus.ready
        if work.attempt.status is AttemptStatus.submitted:
            settle_provider_attempt_tokens(db, attempt_id=work.attempt.id, reported_total_tokens=usage["total_tokens"] or 0, reported_input_tokens=usage["input_tokens"], reported_output_tokens=usage["output_tokens"], outcome=AttemptOutcome.succeeded) if usage["total_tokens"] is not None else settle_provider_attempt_unknown_tokens(db, attempt_id=work.attempt.id)
        now=utcnow(); work.execution.status=ConsultationSplitExecutionStatus.completed; work.execution.completed_at=now; work.execution.recoverable_response_encrypted=None; work.batch.status=ConsultationSplitBatchStatus.ready; work.batch.completed_at=now
        db.add(ProviderUsageEvent(team_id=work.transcript.team_id, owner_user_id=work.owner.id, transcript_id=work.transcript.id, consultation_split_execution_id=work.execution.id, llm_config_id=work.execution.llm_config_id, feature_type=ProviderFeatureType.consultation_split_generation, event_type=ProviderUsageEventType.completed, status="ready", provider_adapter=work.execution.provider_adapter, model_name=work.execution.provider_model, prompt_tokens=usage["input_tokens"], completion_tokens=usage["output_tokens"], total_tokens=usage["total_tokens"]))
        db.commit(); return SplitGenerationRuntimeResult("ready", execution_id)
    except Exception:
        db.rollback(); work=_lock_work(db, execution_id, status=ConsultationSplitExecutionStatus.processing)
        if work is None:
            return SplitGenerationRuntimeResult("in_flight", execution_id)
        # A response was committed before finalization.  Unlike transport
        # failures, this is proof that retrying will not duplicate an uncertain
        # submitted request.  Consume the one automatic opportunity here too.
        recover = (
            not parsed_response
            and work.execution.attempt_no == 1
            and bool(work.execution.recoverable_response_encrypted)
        )
        result = _terminal(
            db, work, "consultation_split_generation_invalid_output", submitted=True,
            retain_partial_for_recovery=recover,
        )
        if recover:
            from app.services.consultation_split_recovery import queue_automatic_split_recovery
            queue_automatic_split_recovery(
                db, work.owner, transcript_id=work.transcript.id,
                batch_id=work.batch.id, execution_id=work.execution.id,
            )
        return result

def process_consultation_split_generation_execution(db: Session, *, execution_id: UUID) -> SplitGenerationRuntimeResult:
    if db.in_transaction(): raise AppError(500, "consultation_split_runtime_transaction_active", "Split runtime requires a clean database session")
    with Session(bind=db.get_bind(), future=True) as read_db:
        state = read_db.get(ConsultationSplitExecution, execution_id)
        if state is not None:
            read_db.expunge(state)
    if state is None or state.kind is not ConsultationSplitExecutionKind.generation: return SplitGenerationRuntimeResult("noop", execution_id)
    if state.status is ConsultationSplitExecutionStatus.processing:
        return _finalize(db, execution_id) if state.recoverable_response_encrypted else SplitGenerationRuntimeResult("in_flight", execution_id)
    if state.status is not ConsultationSplitExecutionStatus.queued: return SplitGenerationRuntimeResult("ready" if state.status is ConsultationSplitExecutionStatus.completed else "failed", execution_id, state.error_code)
    prepared=_prepared(db, execution_id)
    if not isinstance(prepared, tuple): return prepared or SplitGenerationRuntimeResult("noop", execution_id)
    work, provider, request, credential=prepared
    if not _submit(db, work): return SplitGenerationRuntimeResult("noop", execution_id)
    try: text, raw_usage=llm_runtime.invoke_llm(snapshot=provider, credential=credential, request_body=request)
    except AppError as exc:
        work=_lock_work(db, execution_id, status=ConsultationSplitExecutionStatus.processing)
        return _terminal(db, work, "consultation_split_provider_failed", submitted=True) if work else SplitGenerationRuntimeResult("in_flight", execution_id)
    except Exception:
        work=_lock_work(db, execution_id, status=ConsultationSplitExecutionStatus.processing)
        return _terminal(db, work, "consultation_split_provider_failed", submitted=True) if work else SplitGenerationRuntimeResult("in_flight", execution_id)
    work=_lock_work(db, execution_id, status=ConsultationSplitExecutionStatus.processing)
    if work is None: db.rollback(); return SplitGenerationRuntimeResult("in_flight", execution_id)
    if (
        work.attempt.status is not AttemptStatus.submitted
        or work.attempt.deadline_at is None
        or work.attempt.deadline_at <= utcnow()
        or transcript_is_expired(work.transcript)
    ):
        db.rollback()
        return SplitGenerationRuntimeResult("in_flight", execution_id)
    # A returned (including empty) bounded string is a durable provider
    # outcome.  Persist it before parsing so finalization can safely consume
    # the single automatic recovery chance.  Exceptions/no response remain
    # uncertain submitted outcomes and never reach that path.
    if not isinstance(text, str) or len(text) > RECOVERABLE_GENERATION_RESPONSE_MAX_CHARS: return _terminal(db, work, "consultation_split_generation_invalid_output", submitted=True, usage=_usage(raw_usage))
    work.execution.recoverable_response_encrypted=encrypt_json_for_existing_owner(db, owner_user_id=work.owner.id, table="consultation_split_executions", field="recoverable_response_encrypted", record_id=work.execution.id, plaintext={"text": text, "usage": _usage(raw_usage)})
    db.commit(); return _finalize(db, execution_id)
