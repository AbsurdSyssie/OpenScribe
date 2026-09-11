"""Clinician acceptance of validated split-note survivors.

This is deliberately database-only.  It never contacts a provider, so a Keep
request cannot turn an uncertain provider outcome into another submission.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (ConsultationSplitBatch, ConsultationSplitBatchStatus,
    ConsultationSplitExecution, ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ConsultationSplitBatchTopic, ConsultationSplitTopicDisposition,
    ConsultationSplitTopicOutcome, ConsultationSplitTopicOutcomeStatus,
    GeneratedDocument, GeneratedDocumentGeneratorType, GeneratedDocumentSection,
    GeneratedDocumentStatus, TeamRole, TemplateMode, User)
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import (read_split_batch_topic_template_snapshot,
    read_split_batch_phi_index, read_split_topic_outcome_output, read_split_topic_outcome_verified_output)
from app.services.content_crypto import encrypt_text_for_owner
from app.services.consultation_split_generation_runtime import GENERIC_SPLIT_DOCUMENT_TITLE
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled
from app.services.redaction import reidentify_text
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
    documents: list[GeneratedDocument] = []
    phi_index = read_split_batch_phi_index(db, actor, batch=batch)
    for topic, outcome, accepted, template in material:
        raw_content = accepted["content"]
        content = (
            reidentify_text(raw_content, phi_index=phi_index)
            if isinstance(raw_content, str)
            else {key: reidentify_text(value, phi_index=phi_index) for key, value in raw_content.items()}
        )
        mode = TemplateMode(accepted["mode"])
        rendered = content if isinstance(content, str) else json.dumps(content, separators=(",", ":"), sort_keys=True)
        document = GeneratedDocument(id=uuid4(), owner_user_id=actor.id, team_id=batch.team_id,
            transcript_id=batch.transcript_id, transcript_version_id=batch.analysis.transcript_version_id,
            redaction_run_id=batch.analysis.redaction_run_id, consultation_split_batch_topic_id=topic.id,
            consultation_split_topic_uuid=topic.topic_uuid, generator_type=GeneratedDocumentGeneratorType.template,
            template_version_id=None, llm_config_id=None, source_template_name=GENERIC_SPLIT_DOCUMENT_TITLE,
            prompt_snapshot_text=None, status=GeneratedDocumentStatus.ready, title=GENERIC_SPLIT_DOCUMENT_TITLE,
            document_mode=mode, original_output_text_encrypted="", edited_output_text_encrypted="",
            retention_expires_at=batch.retention_expires_at)
        document.regeneration_lineage_id = document.id
        document.original_output_text_encrypted = encrypt_text_for_owner(db, owner_user_id=actor.id, table="generated_documents", field="original_output_text_encrypted", record_id=document.id, plaintext=rendered) or ""
        document.edited_output_text_encrypted = encrypt_text_for_owner(db, owner_user_id=actor.id, table="generated_documents", field="edited_output_text_encrypted", record_id=document.id, plaintext=rendered) or ""
        db.add(document); documents.append(document)
        if isinstance(content, dict):
            for definition in template.get("structured_sections", {}).get("sections", []):
                key = definition.get("section_key")
                section = GeneratedDocumentSection(id=uuid4(), generated_document_id=document.id, section_key=key,
                    section_label=definition["section_label"], section_order=definition["section_order"],
                    original_text_encrypted="", edited_text_encrypted="")
                section.original_text_encrypted = encrypt_text_for_owner(db, owner_user_id=actor.id, table="generated_document_sections", field="original_text_encrypted", record_id=section.id, plaintext=content[key]) or ""
                section.edited_text_encrypted = encrypt_text_for_owner(db, owner_user_id=actor.id, table="generated_document_sections", field="edited_text_encrypted", record_id=section.id, plaintext=content[key]) or ""
                db.add(section)
        outcome.status = ConsultationSplitTopicOutcomeStatus.ready
    batch.status = ConsultationSplitBatchStatus.completed_partial
    # The acknowledgement's first id is the primary when it survived, otherwise
    # the first surviving topic.  This is metadata-only and gives the browser a
    # deterministic document to select without reading protected output.
    return sorted(documents, key=lambda document: next(
        (not topic.is_primary, topic.topic_order) for topic in topics if topic.id == document.consultation_split_batch_topic_id
    ))
