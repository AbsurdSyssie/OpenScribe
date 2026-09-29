"""Shared owner-side materialization for confirmed consultation-split output."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitBatch,
    ConsultationSplitBatchTopic,
    ConsultationSplitTopicOutcome,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentSection,
    GeneratedDocumentStatus,
    TemplateMode,
    TranscriptVersion,
    User,
)
from app.services.content_crypto import encrypt_text_for_owner
from app.services.redaction import reidentify_text


GENERIC_SPLIT_DOCUMENT_TITLE = "Consultation split note"


@dataclass(frozen=True, slots=True)
class SplitDocumentProviderMetadata:
    """Provider fields present only on normal generation materialization."""

    llm_config_id: UUID | None
    model: str | None
    adapter_kind: str | None
    base_url: str | None


def require_batch_materialization_version(
    db: Session,
    *,
    batch: ConsultationSplitBatch,
    transcript_id: UUID,
    version: TranscriptVersion | None = None,
) -> TranscriptVersion:
    """Return the locked confirmation-bound lineage version or fail closed."""
    if version is None and batch.materialization_transcript_version_id is not None:
        version = db.scalar(
            select(TranscriptVersion)
            .where(TranscriptVersion.id == batch.materialization_transcript_version_id)
            .with_for_update()
        )
    if (
        version is None
        or batch.materialization_transcript_version_id != version.id
        or version.transcript_id != transcript_id
    ):
        raise AppError(500, "consultation_split_materialization_binding_invalid", "Split batch is unavailable")
    return version


def materialize_split_document(
    db: Session,
    *,
    owner: User,
    batch: ConsultationSplitBatch,
    topic: ConsultationSplitBatchTopic,
    accepted_output: dict[str, Any],
    template_snapshot: dict[str, Any],
    phi_index: list[dict[str, Any]],
    materialization_version: TranscriptVersion,
    provider_metadata: SplitDocumentProviderMetadata | None = None,
) -> GeneratedDocument:
    """Create one encrypted clinician draft from already-validated redacted output.

    Callers validate the complete set before calling this function, keeping the
    all-before-any partial-materialization guarantee at their phase boundary.
    """
    raw_content = accepted_output["content"]
    content = (
        reidentify_text(raw_content, phi_index=phi_index)
        if isinstance(raw_content, str)
        else {key: reidentify_text(value, phi_index=phi_index) for key, value in raw_content.items()}
    )
    rendered = content if isinstance(content, str) else json.dumps(content, separators=(",", ":"), sort_keys=True)
    metadata = provider_metadata or SplitDocumentProviderMetadata(None, None, None, None)
    document = GeneratedDocument(
        id=uuid4(), owner_user_id=owner.id, team_id=batch.team_id,
        transcript_id=batch.transcript_id, transcript_version_id=materialization_version.id,
        redaction_run_id=batch.analysis.redaction_run_id,
        consultation_split_batch_topic_id=topic.id, consultation_split_topic_uuid=topic.topic_uuid,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=None, llm_config_id=metadata.llm_config_id,
        source_template_name=GENERIC_SPLIT_DOCUMENT_TITLE, prompt_snapshot_text=None,
        status=GeneratedDocumentStatus.ready, title=GENERIC_SPLIT_DOCUMENT_TITLE,
        document_mode=TemplateMode(accepted_output["mode"]),
        original_output_text_encrypted="", edited_output_text_encrypted="",
        retention_expires_at=batch.retention_expires_at, model_used=metadata.model,
        llm_adapter_kind=metadata.adapter_kind, llm_base_url=metadata.base_url,
    )
    document.regeneration_lineage_id = document.id
    document.original_output_text_encrypted = encrypt_text_for_owner(
        db, owner_user_id=owner.id, table="generated_documents",
        field="original_output_text_encrypted", record_id=document.id, plaintext=rendered,
    ) or ""
    document.edited_output_text_encrypted = encrypt_text_for_owner(
        db, owner_user_id=owner.id, table="generated_documents",
        field="edited_output_text_encrypted", record_id=document.id, plaintext=rendered,
    ) or ""
    db.add(document)
    if isinstance(content, dict):
        for definition in template_snapshot["structured_sections"]["sections"]:
            key = definition["section_key"]
            section = GeneratedDocumentSection(
                id=uuid4(), generated_document_id=document.id, section_key=key,
                section_label=definition["section_label"], section_order=definition["section_order"],
                original_text_encrypted="", edited_text_encrypted="",
            )
            section.original_text_encrypted = encrypt_text_for_owner(
                db, owner_user_id=owner.id, table="generated_document_sections",
                field="original_text_encrypted", record_id=section.id, plaintext=content[key],
            ) or ""
            section.edited_text_encrypted = encrypt_text_for_owner(
                db, owner_user_id=owner.id, table="generated_document_sections",
                field="edited_text_encrypted", record_id=section.id, plaintext=content[key],
            ) or ""
            db.add(section)
    return document
