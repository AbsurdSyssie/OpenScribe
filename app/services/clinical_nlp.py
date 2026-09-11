from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import ClinicalEntity, ClinicalEntityRun, DeidentificationAdapterKind, DeidentificationProvider, RedactionRun, RedactionRunStatus, TranscriptVersion, utcnow
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner, keyed_digest_for_owner
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.deidentification import active_team_clinical_nlp_provider
from app.services.redaction import (
    DeidentificationDetectionResult,
    Span,
    _detect_with_generic_rest,
    _resolve_overlaps,
    redaction_run_text,
)


CLINICAL_ENTITY_TYPES = {"DISEASE", "DIAGNOSIS", "CONDITION", "PROBLEM", "SYMPTOM", "SIGN"}
CLINICAL_NLP_MAX_CHUNK_CHARS = 12_000
CLINICAL_NLP_MIN_SPLIT_CHARS = 1_000
SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+")
logger = logging.getLogger("openscribe.clinical_nlp")


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


def successful_redacted_clinical_hints(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun,
    limit: int = 64,
) -> list[dict[str, str]]:
    """Read optional hints only from a successful run over this redacted source.

    This is deliberately a reader, never an enrichment trigger.  A caller may
    contain any error because NLP is optional; no unredacted run can cross this
    boundary.
    """
    run = _successful_redacted_clinical_run(
        db,
        transcript_version=transcript_version,
        redaction_run=redaction_run,
    )
    if run is None or limit < 1:
        return []
    entities = _successful_redacted_clinical_entities(db, run=run, limit=limit)
    hints: list[dict[str, str]] = []
    for entity in entities:
        value = clinical_entity_value(db, entity=entity).strip()
        if entity.entity_type.strip() and value:
            hints.append({"entity_type": entity.entity_type, "text": value})
    return hints


def successful_redacted_clinical_hint_identity(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun,
    limit: int = 64,
) -> dict[str, object] | None:
    """Return non-content identity for the exact optional hint set."""
    run = _successful_redacted_clinical_run(
        db,
        transcript_version=transcript_version,
        redaction_run=redaction_run,
    )
    if run is None:
        return None
    entities = _successful_redacted_clinical_entities(db, run=run, limit=limit)
    return {
        "run_id": str(run.id),
        "entities": [
            {
                "id": str(entity.id),
                "entity_order": entity.entity_order,
                "entity_type": entity.entity_type,
                "normalized_value_hash": entity.normalized_value_hash,
                "created_at": entity.created_at.isoformat(),
            }
            for entity in entities
        ],
    }


def _successful_redacted_clinical_run(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun,
) -> ClinicalEntityRun | None:
    return db.scalar(
        select(ClinicalEntityRun)
        .where(
            ClinicalEntityRun.transcript_id == transcript_version.transcript_id,
            ClinicalEntityRun.transcript_version_id == transcript_version.id,
            ClinicalEntityRun.redaction_run_id == redaction_run.id,
            ClinicalEntityRun.owner_user_id == transcript_version.transcript.owner_user_id,
            ClinicalEntityRun.team_id == transcript_version.transcript.team_id,
            ClinicalEntityRun.status == RedactionRunStatus.succeeded,
            ClinicalEntityRun.source_text_redacted.is_(True),
        )
        .order_by(ClinicalEntityRun.created_at.desc(), ClinicalEntityRun.id.desc())
        .limit(1)
    )


def _successful_redacted_clinical_entities(
    db: Session,
    *,
    run: ClinicalEntityRun,
    limit: int,
) -> list[ClinicalEntity]:
    return list(
        db.scalars(
            select(ClinicalEntity)
            .where(ClinicalEntity.clinical_entity_run_id == run.id)
            .order_by(ClinicalEntity.entity_order.asc(), ClinicalEntity.id.asc())
            .limit(limit)
        )
    )


def _clinical_entity_value_hash(db: Session, *, owner_user_id: uuid.UUID, value: str) -> str:
    normalized = " ".join(value.strip().lower().split())
    return keyed_digest_for_owner(
        db,
        owner_user_id=owner_user_id,
        purpose="clinical_entities.normalized_value_hash",
        value=normalized,
    )


def _clinical_text_chunks(text: str, *, max_chars: int = CLINICAL_NLP_MAX_CHUNK_CHARS) -> list[tuple[int, str]]:
    chunks: list[tuple[int, str]] = []
    cursor = 0
    text_length = len(text)
    min_split_chars = min(CLINICAL_NLP_MIN_SPLIT_CHARS, max(1, max_chars // 2))
    while cursor < text_length:
        raw_end = min(text_length, cursor + max_chars)
        end = raw_end
        if raw_end < text_length:
            sentence_cut = None
            for match in SENTENCE_BOUNDARY_PATTERN.finditer(text, cursor, raw_end):
                if match.end() - cursor >= min_split_chars:
                    sentence_cut = match.end()
            if sentence_cut is not None:
                end = sentence_cut
            else:
                whitespace_cut = text.rfind(" ", cursor + min_split_chars, raw_end)
                if whitespace_cut > cursor:
                    end = whitespace_cut + 1
        chunk = text[cursor:end]
        stripped = chunk.strip()
        if stripped:
            offset = cursor + chunk.index(stripped)
            chunks.append((offset, stripped))
        cursor = end
    return chunks


def _clinical_rest_body_overrides(provider: DeidentificationProvider) -> dict[str, object]:
    configured_body = dict(provider.extra_body_json or {})
    if provider.detect_path.rstrip("/") == "/analyze" and "sentence_detection" not in configured_body:
        return {"sentence_detection": False}
    return {}


def _detect_clinical_entities_with_generic_rest(
    db: Session,
    *,
    provider: DeidentificationProvider,
    text: str,
) -> DeidentificationDetectionResult:
    chunks = _clinical_text_chunks(text)
    if not chunks:
        return DeidentificationDetectionResult(spans=[], api_provider=provider.label)
    all_spans: list[Span] = []
    api_provider = provider.label
    api_model_or_version = None
    body_overrides = _clinical_rest_body_overrides(provider)
    for offset, chunk_text in chunks:
        detection = _detect_with_generic_rest(
            db,
            provider=provider,
            text=chunk_text,
            language="en",
            score_threshold=0.0,
            entities=None,
            extra_body_overrides=body_overrides,
            failure_code="clinical_detection_failed",
            failure_message="Clinical entity detection failed",
        )
        api_provider = detection.api_provider
        api_model_or_version = detection.api_model_or_version or api_model_or_version
        for span in detection.spans:
            all_spans.append(
                Span(
                    start=offset + span.start,
                    end=offset + span.end,
                    entity_type=span.entity_type,
                    score=span.score,
                )
            )
    return DeidentificationDetectionResult(
        spans=_resolve_overlaps(all_spans),
        api_provider=api_provider,
        api_model_or_version=api_model_or_version,
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
    if existing is not None and existing.created_at >= provider.updated_at:
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
        detection = _detect_clinical_entities_with_generic_rest(
            db,
            provider=provider,
            text=source_text,
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


def _optional_clinical_failure_run(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun,
    provider: DeidentificationProvider | None,
    error_code: str,
) -> ClinicalEntityRun | None:
    try:
        with db.begin_nested():
            failed_run = ClinicalEntityRun(
                transcript_id=transcript_version.transcript_id,
                transcript_version_id=transcript_version.id,
                redaction_run_id=redaction_run.id,
                owner_user_id=transcript_version.transcript.owner_user_id,
                team_id=transcript_version.transcript.team_id,
                provider_id=provider.id if provider is not None else None,
                status=RedactionRunStatus.failed,
                source_text_redacted=not provider_can_receive_unredacted_clinical_text(provider) if provider is not None else True,
                api_provider=provider.label if provider is not None else None,
                error_code=error_code,
                failed_at=utcnow(),
            )
            db.add(failed_run)
            db.flush()
        return failed_run
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(
            "Optional clinical NLP failure record unavailable transcript_version_id=%s redaction_run_id=%s provider_id=%s error_code=%s",
            transcript_version.id,
            redaction_run.id,
            provider.id if provider is not None else None,
            error_code,
        )
        return None


def ensure_optional_clinical_entity_run_for_transcript_version(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun,
) -> ClinicalEntityRun | None:
    """Run optional clinical enrichment in a bounded, caller-safe Session.

    The public ``db`` Session may own request or source-lock work.  Never
    commit or roll it back here: the optional provider path owns a fresh
    Session and commits only its own clinical-run rows.
    """
    transcript_version_id = transcript_version.id
    redaction_run_id = redaction_run.id
    # ``get_bind`` is an Engine in production.  Tests can supply a Connection;
    # create_savepoint preserves that fixture's outer transaction while still
    # keeping this helper's commit/rollback out of the caller Session.
    isolated = Session(
        bind=db.get_bind(),
        autoflush=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    provider: DeidentificationProvider | None = None
    try:
        isolated_version = isolated.get(TranscriptVersion, transcript_version_id)
        isolated_redaction_run = isolated.get(RedactionRun, redaction_run_id)
        if (
            isolated_version is None
            or isolated_redaction_run is None
            or isolated_redaction_run.transcript_version_id != isolated_version.id
            or isolated_redaction_run.status is not RedactionRunStatus.succeeded
        ):
            isolated.rollback()
            return None
        provider = active_team_clinical_nlp_provider(
            isolated,
            team_id=isolated_version.transcript.team_id,
        )
        if provider is None:
            isolated.rollback()
            return None
        run = ensure_clinical_entity_run_for_transcript_version(
            isolated,
            transcript_version=isolated_version,
            redaction_run=isolated_redaction_run,
        )
        # Provider work finished above.  Take the source lock only for the
        # short durable-write proof, never across an external call.
        if run is not None:
            scope = lock_consultation_split_source_scope(
                isolated,
                owner_user_id=isolated_version.transcript.owner_user_id,
                transcript_id=isolated_version.transcript_id,
            )
            if scope is None:
                isolated.rollback()
                return None
        run_id = run.id if run is not None else None
        isolated.commit()
        return db.get(ClinicalEntityRun, run_id) if run_id is not None else None
    except Exception as exc:  # Optional enrichment must never invalidate redaction.
        error_code = exc.code if isinstance(exc, AppError) else "clinical_detection_failed"
        isolated.rollback()
        logger.warning(
            "Optional clinical NLP enrichment failed transcript_version_id=%s redaction_run_id=%s provider_id=%s error_code=%s",
            transcript_version_id,
            redaction_run_id,
            provider.id if provider is not None else None,
            error_code,
        )
        try:
            isolated_version = isolated.get(TranscriptVersion, transcript_version_id)
            isolated_redaction_run = isolated.get(RedactionRun, redaction_run_id)
            if isolated_version is None or isolated_redaction_run is None:
                return None
            failed_run = _optional_clinical_failure_run(
                isolated,
                transcript_version=isolated_version,
                redaction_run=isolated_redaction_run,
                provider=provider,
                error_code=error_code,
            )
            failed_run_id = failed_run.id if failed_run is not None else None
            isolated.commit()
            return db.get(ClinicalEntityRun, failed_run_id) if failed_run_id is not None else None
        except Exception:
            isolated.rollback()
            logger.warning(
                "Optional clinical NLP failure record unavailable transcript_version_id=%s redaction_run_id=%s provider_id=%s error_code=%s",
                transcript_version_id,
                redaction_run_id,
                provider.id if provider is not None else None,
                error_code,
            )
            return None
    finally:
        isolated.close()
