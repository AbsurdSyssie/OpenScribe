"""Targeted, clinician-directed recovery for validated split-note survivors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitBatch, ConsultationSplitBatchStatus, ConsultationSplitBatchTopic,
    ConsultationSplitExecution, ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus, ConsultationSplitTopicDisposition,
    ConsultationSplitTopicOutcome, ConsultationSplitTopicOutcomeStatus, TeamLlmConfig, TeamRole, User, utcnow,
)
from app.services.consultation_split_generation import prepare_split_generation_recovery_request
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import (
    queue_split_execution, read_split_batch_json, read_split_execution_json, read_split_topic_outcome_output,
)
from app.services.llm import resolve_user_llm
from app.services.llm_adapters.runtime import build_provider_snapshot, generation_request_snapshot
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled
from app.services.task_outbox import try_publish_task_dispatch_safely
from app.services.transcripts import transcript_is_expired

SPLIT_RECOVERY_RESERVATION_SECONDS = 1_500


@dataclass(frozen=True, slots=True)
class QueuedSplitRecovery:
    execution_id: UUID
    replayed: bool


def retry_missing_split_notes(
    db: Session, actor: User, *, transcript_id: UUID, batch_id: UUID,
) -> QueuedSplitRecovery:
    """Queue one clinician-directed recovery winner using immutable batch data.

    This is deliberately clinician-directed.  It takes a current eligible
    provider selection, creates a fresh quota reservation/outbox row, and does
    not modify accepted sibling ciphertext or create documents.
    """
    return _queue_recovery(db, actor, transcript_id=transcript_id, batch_id=batch_id, automatic_from_execution=None)


def queue_automatic_split_recovery(
    db: Session, actor: User, *, transcript_id: UUID, batch_id: UUID, execution_id: UUID,
) -> QueuedSplitRecovery | None:
    """Queue the sole automatic retry after a durable, parsed response.

    The initial submitted attempt has already settled and its response was
    persisted before parsing.  Submitted/no-response and timeout states cannot
    satisfy this proof and therefore never enter this function.
    """
    return _queue_recovery(db, actor, transcript_id=transcript_id, batch_id=batch_id,
                           automatic_from_execution=execution_id)


def _queue_recovery(
    db: Session, actor: User, *, transcript_id: UUID, batch_id: UUID,
    automatic_from_execution: UUID | None,
) -> QueuedSplitRecovery | None:
    # Leaders may recover their own roots; the owner-id lock below still denies
    # access to every other user's transcript-derived content.
    if actor.is_system_admin or actor.team_id is None or actor.team_role not in {TeamRole.user, TeamRole.leader}:
        raise AppError(403, "forbidden", "Consultation split content is restricted to the owning user")
    # Manual recovery is a new owner action, so it must honour the effective
    # deployment and owner gate before any transcript-root lookup. Automatic
    # recovery finishes already durable work and must remain able to do so if a
    # rollout gate changes after the initial submission.
    if automatic_from_execution is None and (
        not consultation_splitting_feature_enabled()
        or not consultation_splitting_enabled(db, actor)
    ):
        raise AppError(403, "consultation_split_disabled", "Consultation splitting is not enabled")
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or scope.owner.is_system_admin or transcript_is_expired(scope.transcript):
        raise AppError(404, "consultation_split_batch_unavailable", "Split batch is unavailable")
    batch = db.scalar(select(ConsultationSplitBatch).where(
        ConsultationSplitBatch.id == batch_id,
        ConsultationSplitBatch.owner_user_id == actor.id,
        ConsultationSplitBatch.transcript_id == transcript_id,
    ).with_for_update())
    if batch is None or batch.status is not ConsultationSplitBatchStatus.partially_ready:
        raise AppError(409, "consultation_split_batch_unavailable", "Split batch is unavailable")
    topics = db.scalars(select(ConsultationSplitBatchTopic).where(
        ConsultationSplitBatchTopic.batch_id == batch.id,
    ).order_by(ConsultationSplitBatchTopic.topic_order).with_for_update()).all()
    outcomes = {item.batch_topic_id: item for item in db.scalars(select(ConsultationSplitTopicOutcome).where(
        ConsultationSplitTopicOutcome.batch_topic_id.in_([topic.id for topic in topics]),
    ).with_for_update()).all()}
    failed = [topic for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note
              and outcomes.get(topic.id) is not None and outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.failed]
    if not failed or len(outcomes) != len(topics):
        raise AppError(409, "consultation_split_batch_unavailable", "Split batch is unavailable")
    active = db.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.generation,
        ConsultationSplitExecution.attempt_no > 1,
        ConsultationSplitExecution.status.in_([
            ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing,
        ]),
    ).with_for_update())
    if active is not None:
        db.rollback()
        return QueuedSplitRecovery(active.id, True)
    if automatic_from_execution is not None:
        initial = db.scalar(select(ConsultationSplitExecution).where(
            ConsultationSplitExecution.id == automatic_from_execution,
            ConsultationSplitExecution.batch_id == batch.id,
            ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.generation,
            ConsultationSplitExecution.attempt_no == 1,
            ConsultationSplitExecution.status.in_([
                ConsultationSplitExecutionStatus.completed,
                ConsultationSplitExecutionStatus.failed,
            ]),
        ).with_for_update())
        # Existing attempt two (even terminal) proves the automatic chance was
        # consumed.  Never infer safety from a submitted or absent response.
        if initial is None or db.scalar(select(ConsultationSplitExecution.id).where(
            ConsultationSplitExecution.batch_id == batch.id,
            ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.generation,
            ConsultationSplitExecution.attempt_no == 2,
        )) is not None:
            db.rollback()
            return None
    siblings: dict[UUID, dict[str, object]] = {}
    for topic in topics:
        outcome = outcomes[topic.id]
        if outcome.status is ConsultationSplitTopicOutcomeStatus.validated:
            siblings[topic.topic_uuid] = read_split_topic_outcome_output(db, actor, outcome=outcome)
    if automatic_from_execution is None:
        _selection, config, model, _preference = resolve_user_llm(db, actor)
        if not isinstance(model, str) or not model.strip():
            raise AppError(422, "consultation_split_llm_model_unavailable", "No LLM model is available for consultation splitting")
        snapshot = build_provider_snapshot(config=config, model=model.strip()).to_dict()
    else:
        config = db.get(TeamLlmConfig, initial.llm_config_id) if initial.llm_config_id else None
        if config is None or config.team_id != actor.team_id:
            db.rollback()
            return None
        model = initial.provider_model
        if not isinstance(model, str) or not model:
            db.rollback()
            return None
        try:
            snapshot = read_split_execution_json(db, actor, execution=initial, field="provider_snapshot_encrypted")
        except (AppError, UnicodeDecodeError):
            db.rollback()
            return None
    prepared = prepare_split_generation_recovery_request(
        source_snapshot=read_split_batch_json(db, actor, batch=batch, field="source_snapshot_encrypted") or {},
        clinical_snapshot=read_split_batch_json(db, actor, batch=batch, field="clinical_snapshot_encrypted") or {},
        confirmed_plan=read_split_batch_json(db, actor, batch=batch, field="confirmed_plan_encrypted") or {},
        note_options_snapshot=read_split_batch_json(db, actor, batch=batch, field="note_options_snapshot_encrypted") or {},
        failed_topic_uuids=[topic.topic_uuid for topic in failed], accepted_sibling_outputs=siblings,
    )
    messages = prepared.request_body["messages"]
    request = generation_request_snapshot(
        adapter_kind=config.adapter_kind, model=model.strip(), user_id=actor.id,
        system_message=messages[0]["content"], user_message=messages[1]["content"],
        output_token_cap=prepared.output_token_cap, response_json_schema=prepared.response_json_schema,
    )
    execution, _attempt, dispatch = queue_split_execution(
        db, actor, kind=ConsultationSplitExecutionKind.generation, batch=batch,
        reserved_units=prepared.reservation_units,
        reservation_valid_until=utcnow() + timedelta(seconds=SPLIT_RECOVERY_RESERVATION_SECONDS),
        llm_config_id=config.id, provider_snapshot=snapshot, request_payload=request,
        expected_model=model.strip(),
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    try_publish_task_dispatch_safely(dispatch.task_id)
    return QueuedSplitRecovery(execution.id, False)
