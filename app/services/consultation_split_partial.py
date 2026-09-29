"""Clinician acceptance of validated split-note survivors.

This is deliberately database-only.  It never contacts a provider, so a Keep
request cannot turn an uncertain provider outcome into another submission.
"""
from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (ConsultationSplitBatch, ConsultationSplitBatchStatus,
    ConsultationSplitExecution, ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ConsultationSplitBatchTopic, ConsultationSplitTopicDisposition,
    ConsultationSplitTopicOutcome, ConsultationSplitTopicOutcomeStatus,
    GeneratedDocument, TeamRole, TemplateMode, User)
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import (read_split_batch_topic_template_snapshot,
    read_split_batch_phi_index, read_split_topic_outcome_output, read_split_topic_outcome_verified_output)
from app.services.consultation_split_materialization import (
    materialize_split_document,
    require_batch_materialization_version,
)
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled
from app.services.transcripts import transcript_is_expired


def keep_available_split_notes(db: Session, actor: User, *, transcript_id: UUID, batch_id: UUID) -> list[GeneratedDocument]:
    """Materialize only write-once validated outcomes, idempotently.

    This public clinician action deliberately checks the effective gate before
    looking up content.  Durable verifier completion uses the private locked
    helper below instead; it has already passed the gate before submission.
    """
    # Leaders may act on roots they own; leadership never expands the owner-id
    # lookup below to another user's content.
    if actor.is_system_admin or actor.team_id is None or actor.team_role not in {TeamRole.user, TeamRole.leader}:
        raise AppError(403, "forbidden", "Consultation split content is restricted to the owning user")
    # Keep is a new clinician-directed content action. Reject an inactive
    # effective gate before discovering whether the supplied transcript exists.
    if not consultation_splitting_feature_enabled() or not consultation_splitting_enabled(db, actor):
        raise AppError(403, "consultation_split_disabled", "Consultation splitting is not enabled")
    # The source-scope lock remains owner-specific, including for leaders.
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or scope.owner.id != actor.id or transcript_is_expired(scope.transcript):
        raise AppError(404, "consultation_split_batch_unavailable", "Split batch is unavailable")
    batch = db.scalar(select(ConsultationSplitBatch).where(
        ConsultationSplitBatch.id == batch_id, ConsultationSplitBatch.owner_user_id == actor.id,
        ConsultationSplitBatch.transcript_id == transcript_id).with_for_update())
    if batch is None:
        raise AppError(404, "consultation_split_batch_unavailable", "Split batch is unavailable")
    topics = db.scalars(select(ConsultationSplitBatchTopic).where(
        ConsultationSplitBatchTopic.batch_id == batch.id).order_by(
        ConsultationSplitBatchTopic.topic_order).with_for_update()).all()
    outcomes = {row.batch_topic_id: row for row in db.scalars(select(ConsultationSplitTopicOutcome).where(
        ConsultationSplitTopicOutcome.batch_topic_id.in_([topic.id for topic in topics])).with_for_update()).all()}
    try:
        documents = _materialize_available_split_notes_locked(
            db, actor=actor, transcript_id=transcript_id, batch=batch,
            topics=topics, outcomes=outcomes,
        )
        db.commit()
        # Callers may close their session after Keep returns.  Refresh the
        # acknowledgement rows so their metadata remains readable detached.
        for document in documents:
            db.refresh(document)
    except Exception:
        db.rollback()
        raise
    return documents


def _materialize_available_split_notes_locked(
    db: Session,
    *,
    actor: User,
    transcript_id: UUID,
    batch: ConsultationSplitBatch,
    topics: list[ConsultationSplitBatchTopic],
    outcomes: dict[UUID, ConsultationSplitTopicOutcome],
) -> list[GeneratedDocument]:
    """Materialize validated outcomes while the owner, batch, and children are locked.

    Callers must have established owner/content authority and hold the source
    scope, batch, topics, and outcomes in the established lock order.  It does
    not inspect feature gates or commit: that keeps the public Keep action
    gated while letting a verifier atomically finish work submitted under an
    earlier valid gate.
    """
    if batch.owner_user_id != actor.id or batch.transcript_id != transcript_id:
        raise AppError(500, "consultation_split_outcome_invalid", "Split batch is unavailable")
    if batch.status is ConsultationSplitBatchStatus.completed_partial:
        return db.scalars(select(GeneratedDocument).join(
            ConsultationSplitBatchTopic,
            GeneratedDocument.consultation_split_batch_topic_id == ConsultationSplitBatchTopic.id,
        ).where(ConsultationSplitBatchTopic.batch_id == batch.id).order_by(
            ConsultationSplitBatchTopic.is_primary.desc(), ConsultationSplitBatchTopic.topic_order)).all()
    if batch.status is not ConsultationSplitBatchStatus.partially_ready:
        raise AppError(409, "consultation_split_batch_unavailable", "Split batch is unavailable")
    # The workspace action flag is only a projection.  Enforce the same rule
    # here so a crafted request cannot accept a partial set while an automatic
    # or clinician-requested recovery still owns the batch.
    active_recovery = db.scalar(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.generation,
        ConsultationSplitExecution.attempt_no > 1,
        ConsultationSplitExecution.status.in_([
            ConsultationSplitExecutionStatus.queued,
            ConsultationSplitExecutionStatus.processing,
        ]),
    ).with_for_update())
    if active_recovery is not None:
        raise AppError(409, "consultation_split_batch_unavailable", "Split batch is unavailable")
    # Validate all retained output before adding any child.  A malformed
    # ciphertext is a fail-closed integrity error, not a reason to publish a
    # prefix of the clinician's accepted set.
    material: list[tuple[ConsultationSplitBatchTopic, ConsultationSplitTopicOutcome, dict, dict]] = []
    for topic in topics:
        outcome = outcomes.get(topic.id)
        if outcome is None:
            raise AppError(500, "consultation_split_outcome_invalid", "Split batch is unavailable")
        if topic.disposition is not ConsultationSplitTopicDisposition.separate_note:
            if outcome.status is ConsultationSplitTopicOutcomeStatus.pending:
                outcome.status = ConsultationSplitTopicOutcomeStatus.failed
                outcome.error_code = "consultation_split_not_requested"
            continue
        if outcome.status is not ConsultationSplitTopicOutcomeStatus.validated:
            continue
        existing = db.scalar(select(GeneratedDocument).where(
            GeneratedDocument.consultation_split_batch_topic_id == topic.id).with_for_update())
        if existing is not None:
            continue
        # Verification never overwrites accepted provider output.  Draft
        # materialization uses its separate, validated candidate when present.
        accepted = read_split_topic_outcome_verified_output(db, actor, outcome=outcome)
        if accepted is None:
            accepted = read_split_topic_outcome_output(db, actor, outcome=outcome)
        template = read_split_batch_topic_template_snapshot(db, actor, topic=topic)
        if not isinstance(accepted, dict) or accepted.get("mode") not in {"freeform", "structured"}:
            raise AppError(500, "consultation_split_outcome_invalid", "Split batch is unavailable")
        content = accepted.get("content")
        mode = TemplateMode(accepted["mode"])
        rendered = content if isinstance(content, str) else json.dumps(content, separators=(",", ":"), sort_keys=True)
        if not isinstance(rendered, str):
            raise AppError(500, "consultation_split_outcome_invalid", "Split batch is unavailable")
        if isinstance(content, dict):
            for definition in template.get("structured_sections", {}).get("sections", []):
                key = definition.get("section_key")
                if not isinstance(key, str) or not isinstance(content.get(key), str):
                    raise AppError(500, "consultation_split_outcome_invalid", "Split batch is unavailable")
        material.append((topic, outcome, accepted, template))
    # Every materialization path uses confirmation's immutable version rather
    # than the nullable analysis source version. Validate it before creating
    # any document so a broken binding cannot publish a prefix.
    materialization_version = require_batch_materialization_version(
        db, batch=batch, transcript_id=transcript_id,
    )
    documents: list[GeneratedDocument] = []
    phi_index = read_split_batch_phi_index(db, actor, batch=batch)
    for topic, outcome, accepted, template in material:
        document = materialize_split_document(
            db, owner=actor, batch=batch, topic=topic,
            accepted_output=accepted, template_snapshot=template, phi_index=phi_index,
            materialization_version=materialization_version,
        )
        documents.append(document)
        outcome.status = ConsultationSplitTopicOutcomeStatus.ready
    batch.status = ConsultationSplitBatchStatus.completed_partial
    # The acknowledgement's first id is the primary when it survived, otherwise
    # the first surviving topic.  This is metadata-only and gives the browser a
    # deterministic document to select without reading protected output.
    return sorted(documents, key=lambda document: next(
        (not topic.is_primary, topic.topic_order) for topic in topics if topic.id == document.consultation_split_batch_topic_id
    ))
