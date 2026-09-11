"""Durable optional checker queue for validated split-note survivors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (ConsultationSplitBatch, ConsultationSplitBatchStatus, ConsultationSplitBatchTopic,
    ConsultationSplitExecution, ConsultationSplitExecutionKind,
    ConsultationSplitTopicDisposition, ConsultationSplitTopicOutcome,
    ConsultationSplitTopicOutcomeStatus, ConsultationSplitVerificationStatus,
    User, utcnow)
from app.services.consultation_split_verification import prepare_split_verification_request
from app.services.consultation_splits import (queue_split_execution,
    read_split_batch_json, read_split_topic_outcome_output)
from app.services.llm import active_team_hallucination_check_selection
from app.services.llm_adapters.runtime import build_provider_snapshot, generation_request_snapshot
from app.errors import AppError

SPLIT_VERIFICATION_RESERVATION_SECONDS = 1_500


@dataclass(frozen=True, slots=True)
class QueuedSplitVerification:
    execution_id: UUID | None
    dispatch_task_id: UUID | None
    checked: bool


def queue_split_verification_or_mark_unchecked(db: Session, *, batch: ConsultationSplitBatch,
                                                target_status: ConsultationSplitBatchStatus) -> QueuedSplitVerification:
    """Queue exactly one checker call, or record an intentional fail-open skip.

    The caller owns the transaction.  No credential is resolved here.
    """
    # A verifier snapshots survivors.  Do not create that snapshot while a
    # recovery can still change it; generation finalization queues verification
    # only after the recovery reaches a terminal outcome.
    active_recovery = db.scalar(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.generation,
        ConsultationSplitExecution.attempt_no > 1,
        ConsultationSplitExecution.status.in_(["queued", "processing"]),
    ).with_for_update())
    if active_recovery is not None:
        return QueuedSplitVerification(None, None, False)
    owner = db.scalar(select(User).where(User.id == batch.owner_user_id).with_for_update())
    selection = active_team_hallucination_check_selection(db, team_id=batch.team_id)
    if selection is None or selection.config is None:
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = "verification_not_selected"
        batch.verification_completed_at = utcnow()
        return QueuedSplitVerification(None, None, False)
    topics = db.scalars(select(ConsultationSplitBatchTopic).where(
        ConsultationSplitBatchTopic.batch_id == batch.id).order_by(ConsultationSplitBatchTopic.topic_order).with_for_update()).all()
    outcomes = {item.batch_topic_id: item for item in db.scalars(select(ConsultationSplitTopicOutcome).where(
        ConsultationSplitTopicOutcome.batch_topic_id.in_([topic.id for topic in topics])).with_for_update()).all()}
    if owner is None or len(outcomes) != len(topics):
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = "verification_unavailable"
        batch.verification_completed_at = utcnow()
        return QueuedSplitVerification(None, None, False)
    separate = [topic for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note]
    # The established patch protocol is valid only for a complete structured set.
    survivors = {topic.topic_uuid: read_split_topic_outcome_output(db, owner, outcome=outcomes[topic.id])
                 for topic in separate if outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated}
    plan = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted") or {}
    try:
        prepared = prepare_split_verification_request(
            source_snapshot=read_split_batch_json(db, owner, batch=batch, field="source_snapshot_encrypted") or {},
            clinical_snapshot=read_split_batch_json(db, owner, batch=batch, field="clinical_snapshot_encrypted") or {},
            confirmed_plan=plan, survivor_outputs=survivors,
        )
        model = (selection.model_name_override or selection.config.model_name or "").strip()
        if not model:
            raise ValueError("no model")
        provider = build_provider_snapshot(config=selection.config, model=model)
        messages = prepared.request_body["messages"]
        request = generation_request_snapshot(adapter_kind=selection.config.adapter_kind, model=model,
            user_id=owner.id, system_message=messages[0]["content"], user_message=messages[1]["content"],
            output_token_cap=prepared.output_token_cap, response_json_schema=prepared.response_json_schema)
    except Exception:
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = "verification_not_eligible"
        batch.verification_completed_at = utcnow()
        return QueuedSplitVerification(None, None, False)
    existing = db.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.verification).with_for_update())
    if existing is not None:
        return QueuedSplitVerification(existing.id, None, True)
    try:
        execution, _attempt, dispatch = queue_split_execution(db, owner,
            kind=ConsultationSplitExecutionKind.verification, batch=batch,
            reserved_units=prepared.reservation_units,
            reservation_valid_until=utcnow() + timedelta(seconds=SPLIT_VERIFICATION_RESERVATION_SECONDS),
            llm_config_id=selection.config.id, provider_snapshot=provider.to_dict(), request_payload=request,
            expected_model=model)
    except AppError as exc:
        # The checker is optional. Its independent reservation must not unwind
        # the accepted generation response, its own settled usage, or the
        # clinician's partial-recovery path.
        if exc.code not in {"quota_exceeded", "quota_disabled", "provider_quota_exceeded", "quota_unavailable"}:
            raise
        batch.verification_status = ConsultationSplitVerificationStatus.unchecked
        batch.verification_reason = "verification_quota_unavailable"
        batch.verification_completed_at = utcnow()
        return QueuedSplitVerification(None, None, False)
    batch.status = ConsultationSplitBatchStatus.verifying
    batch.verification_status = ConsultationSplitVerificationStatus.verifying
    batch.verification_reason = None
    return QueuedSplitVerification(execution.id, dispatch.task_id, True)
