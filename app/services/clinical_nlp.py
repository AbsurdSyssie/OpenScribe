from __future__ import annotations

import ipaddress
import uuid
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import ClinicalEntity, ClinicalEntityRun, DeidentificationAdapterKind, DeidentificationProvider, RedactionRun, RedactionRunStatus, TranscriptVersion, utcnow
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner, keyed_digest_for_owner
from app.services.deidentification import active_team_clinical_nlp_provider
from app.services.redaction import (
    _detect_with_generic_rest,
    redaction_run_text,
)


CLINICAL_ENTITY_TYPES = {"DISEASE", "DIAGNOSIS", "CONDITION", "PROBLEM", "SYMPTOM", "SIGN"}


def _provider_base_url_is_local(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified


def provider_can_receive_unredacted_clinical_text(provider: DeidentificationProvider) -> bool:
    return bool(
        provider.clinical_detection_allow_unredacted
        and (
            provider.adapter_kind is DeidentificationAdapterKind.native_presidio
            or _provider_base_url_is_local(provider.base_url)
        )
    )


def clinical_entity_value(db: Session, *, entity: ClinicalEntity) -> str:
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=entity.run.owner_user_id,
            table="clinical_entities",
            field="value_encrypted",
            record_id=entity.id,
            stored_value=entity.value_encrypted,
        )
        or ""
    )


def _clinical_entity_value_hash(db: Session, *, owner_user_id: uuid.UUID, value: str) -> str:
    normalized = " ".join(value.strip().lower().split())
    return keyed_digest_for_owner(
        db,
        owner_user_id=owner_user_id,
        purpose="clinical_entities.normalized_value_hash",
        value=normalized,
    )


def ensure_clinical_entity_run_for_transcript_version(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun | None = None,
) -> ClinicalEntityRun | None:
    provider = active_team_clinical_nlp_provider(db, team_id=transcript_version.transcript.team_id)
    if provider is None:
        return None
    existing = db.scalar(
        select(ClinicalEntityRun)
        .where(
            ClinicalEntityRun.transcript_version_id == transcript_version.id,
            ClinicalEntityRun.provider_id == provider.id,
            ClinicalEntityRun.status == RedactionRunStatus.succeeded,
        )
        .order_by(ClinicalEntityRun.created_at.desc(), ClinicalEntityRun.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing

    run = ClinicalEntityRun(
        transcript_id=transcript_version.transcript_id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=redaction_run.id if redaction_run is not None else None,
        owner_user_id=transcript_version.transcript.owner_user_id,
        team_id=transcript_version.transcript.team_id,
        provider_id=provider.id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=True,
        api_provider=provider.label,
    )
    db.add(run)
    db.flush()
    try:
        original_text = (
            decrypt_text_for_owner(
                db,
                owner_user_id=transcript_version.transcript.owner_user_id,
                table="transcript_versions",
                field="text_encrypted",
                record_id=transcript_version.id,
                stored_value=transcript_version.text_encrypted,
            )
            or ""
        )
        source_text = original_text
        if provider_can_receive_unredacted_clinical_text(provider):
            run.source_text_redacted = False
        else:
            if redaction_run is None:
                raise AppError(409, "clinical_detection_requires_redaction", "Clinical entity detection requires a redaction run")
            source_text = redaction_run_text(db, run=redaction_run) or ""
            run.source_text_redacted = True
        if not source_text.strip():
            run.entity_count = 0
            db.add(run)
            return run
        if provider.adapter_kind is DeidentificationAdapterKind.native_presidio:
            run.entity_count = 0
            db.add(run)
            return run
        detection = _detect_with_generic_rest(
            db,
            provider=provider,
            text=source_text,
            language="en",
            score_threshold=0.0,
            entities=None,
        )
        run.api_provider = detection.api_provider
        run.api_model_or_version = detection.api_model_or_version
        clinical_spans = [
            span
            for span in detection.spans
            if span.entity_type.strip().upper() in CLINICAL_ENTITY_TYPES
        ]
        run.entity_count = len(clinical_spans)
        for index, span in enumerate(clinical_spans, start=1):
            value = source_text[span.start:span.end]
            entity_id = uuid.uuid4()
            db.add(
                ClinicalEntity(
                    id=entity_id,
                    clinical_entity_run_id=run.id,
                    entity_order=index,
                    entity_type=span.entity_type,
                    value_encrypted=encrypt_text_for_owner(
                        db,
                        owner_user_id=transcript_version.transcript.owner_user_id,
                        table="clinical_entities",
                        field="value_encrypted",
                        record_id=entity_id,
                        plaintext=value,
                    ),
                    normalized_value_hash=_clinical_entity_value_hash(
                        db,
                        owner_user_id=transcript_version.transcript.owner_user_id,
                        value=value,
                    ),
                    occurrence_count=1,
                    score=span.score,
                )
            )
        db.add(run)
        return run
    except AppError as exc:
        run.status = RedactionRunStatus.failed
        run.error_code = exc.code
        run.failed_at = utcnow()
        db.add(run)
        return run
    except Exception as exc:  # pragma: no cover
        run.status = RedactionRunStatus.failed
        run.error_code = "clinical_detection_failed"
        run.failed_at = utcnow()
        db.add(run)
        raise AppError(502, "clinical_detection_failed", "Clinical entity detection failed") from exc
