"""Owner-only regeneration of an immutable confirmed split batch."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    User,
)
from app.services.consultation_split_confirmation import _require_owner
from app.services.consultation_split_generation_queue import queue_confirmed_split_generation
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import (
    create_split_batch,
    create_split_batch_topic,
    create_split_topic_outcome,
    read_split_batch_json,
    read_split_batch_topic_template_snapshot,
    read_split_topic_title,
)
from app.services.content_crypto import encrypt_json_for_owner
from app.services.task_outbox import try_publish_task_dispatch_safely
from app.services.transcripts import transcript_is_expired


_TABLE_INTENT = "consultation_split_intents"
_TERMINAL_BATCH_STATUSES = {
    ConsultationSplitBatchStatus.ready,
    ConsultationSplitBatchStatus.completed_partial,
    ConsultationSplitBatchStatus.failed,
}


@dataclass(frozen=True, slots=True)
class RegeneratedConsultationSplitBatch:
    batch_id: UUID
    execution_id: UUID
    replayed: bool


def _unavailable() -> AppError:
    return AppError(404, "consultation_split_batch_unavailable", "Split batch is unavailable")


def _response(batch: ConsultationSplitBatch, *, replayed: bool) -> RegeneratedConsultationSplitBatch:
    execution = next((row for row in batch.executions if row.kind.value == "generation"), None)
    if execution is None:
        raise AppError(500, "consultation_split_batch_invalid", "Consultation split content is unavailable")
    return RegeneratedConsultationSplitBatch(batch.id, execution.id, replayed)


def regenerate_confirmed_split_batch(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    batch_id: UUID,
    client_idempotency_key: UUID,
) -> RegeneratedConsultationSplitBatch:
    """Queue a fresh batch from an earlier batch's frozen clinical inputs.

    This deliberately does not reopen analysis or review a draft. Provider
    selection remains live at queue time; all clinician/source/template inputs
    are copied from the earlier immutable batch.
    """
    _require_owner(db, actor)
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or scope.transcript.team_id != actor.team_id or transcript_is_expired(scope.transcript):
        raise _unavailable()

    existing_intent = db.scalar(
        select(ConsultationSplitIntent)
        .where(
            ConsultationSplitIntent.owner_user_id == actor.id,
            ConsultationSplitIntent.client_idempotency_key == client_idempotency_key,
        )
        .with_for_update()
    )
    if existing_intent is not None:
        batch = db.scalar(
            select(ConsultationSplitBatch)
            .where(
                ConsultationSplitBatch.intent_id == existing_intent.id,
                ConsultationSplitBatch.transcript_id == transcript_id,
                ConsultationSplitBatch.owner_user_id == actor.id,
                ConsultationSplitBatch.team_id == actor.team_id,
            )
            .with_for_update()
        )
        if batch is None:
            raise AppError(409, "consultation_split_idempotency_conflict", "This regeneration key is unavailable")
        return _response(batch, replayed=True)

    source = db.scalar(
        select(ConsultationSplitBatch)
        .where(
            ConsultationSplitBatch.id == batch_id,
            ConsultationSplitBatch.transcript_id == transcript_id,
            ConsultationSplitBatch.owner_user_id == actor.id,
            ConsultationSplitBatch.team_id == actor.team_id,
        )
        .with_for_update()
    )
    if source is None or source.status not in _TERMINAL_BATCH_STATUSES:
        raise _unavailable()

    # A distinct intent gives the existing unique owner/key boundary durable
    # replay semantics without altering the earlier batch or its documents.
    intent = ConsultationSplitIntent(
        id=uuid4(), owner_user_id=actor.id, team_id=actor.team_id,
        transcript_id=transcript_id, analysis_id=source.analysis_id,
        client_idempotency_key=client_idempotency_key,
        status=ConsultationSplitIntentStatus.confirmed,
        retention_expires_at=scope.transcript.retention_expires_at,
    )
    intent.generation_snapshot_encrypted = encrypt_json_for_owner(
        db, owner_user_id=actor.id, table=_TABLE_INTENT,
        field="generation_snapshot_encrypted", record_id=intent.id,
        plaintext={"regeneration_source_batch_id": str(source.id)},
    ) or ""
    db.add(intent)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # The unique key winner is authoritative after a concurrent submit.
        return regenerate_confirmed_split_batch(
            db, actor, transcript_id=transcript_id, batch_id=batch_id,
            client_idempotency_key=client_idempotency_key,
        )

    snapshots = {
        field: read_split_batch_json(db, actor, batch=source, field=field)
        for field in (
            "confirmed_plan_encrypted", "clinical_snapshot_encrypted", "source_snapshot_encrypted",
            "template_snapshot_encrypted", "pii_snapshot_encrypted", "note_options_snapshot_encrypted",
        )
    }
    if any(value is None for value in snapshots.values()):
        raise AppError(500, "consultation_split_batch_invalid", "Consultation split content is unavailable")
    plan = deepcopy(snapshots["confirmed_plan_encrypted"])
    plan["intent_id"] = str(intent.id)
    batch = create_split_batch(
        db, actor, analysis=source.analysis, intent_id=intent.id, confirmed_plan=plan,
        clinical_snapshot=snapshots["clinical_snapshot_encrypted"], source_snapshot=snapshots["source_snapshot_encrypted"],
        template_snapshot=snapshots["template_snapshot_encrypted"], pii_snapshot=snapshots["pii_snapshot_encrypted"],
        provider_snapshot={}, note_options_snapshot=snapshots["note_options_snapshot_encrypted"],
        materialization_transcript_version_id=source.materialization_transcript_version_id,
    )
    for topic in source.topics:
        clone = create_split_batch_topic(
            db, actor, batch=batch, title=read_split_topic_title(db, actor, topic=topic),
            topic_order=topic.topic_order, is_primary=topic.is_primary, disposition=topic.disposition,
            template_snapshot=read_split_batch_topic_template_snapshot(db, actor, topic=topic), topic_uuid=topic.topic_uuid,
        )
        create_split_topic_outcome(db, actor, batch_topic=clone)
    queued = queue_confirmed_split_generation(db, batch=batch)
    db.commit()
    if queued.dispatch_task_id is not None:
        try_publish_task_dispatch_safely(queued.dispatch_task_id)
    return RegeneratedConsultationSplitBatch(batch.id, queued.execution.id, False)
