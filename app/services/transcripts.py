import logging
import os
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    Transcript,
    TranscriptAudioCleanupJob,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptManualPiiEntity,
    TranscriptStatus,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
    transcript_expiry,
    utcnow,
)
from app.schemas.transcripts import EMIS_WORKING_NOTE_SECTION_KEYS, TranscriptCreate, TranscriptStart, WorkingNoteUpdate
from app.services.audio import (
    enforce_whole_file_duration_limit,
    inspect_audio_duration_seconds,
    normalize_audio_to_wav_16k_mono,
    probe_audio_duration_seconds,
)
from app.services.content_crypto import decrypt_json_for_owner, decrypt_text_for_owner, encrypt_json_for_owner, encrypt_text_for_owner, keyed_digest_for_owner
from app.services.redaction import ensure_redaction_run_for_transcript_version
from app.services.security_audit import record_security_event
from app.services.stt import ensure_stt_config_credential_ready, resolve_selected_team_stt, transcribe_with_stt_snapshot
from app.services.vault import (
    delete_transcript_ingestion_source_audio,
    read_transcript_ingestion_source_audio,
    write_transcript_ingestion_source_audio,
)

LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS = float(os.getenv("LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", "3600"))
LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS = float(os.getenv("LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS", "600"))
WHOLE_FILE_HOURLY_UPLOAD_BYTES = int(os.getenv("WHOLE_FILE_HOURLY_UPLOAD_BYTES", str(200 * 1024 * 1024)))
WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS = float(os.getenv("WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS", str(4 * 60 * 60)))
retry_audio_logger = logging.getLogger("openscribe.retry_audio")
transcript_redaction_logger = logging.getLogger("openscribe.transcript_redaction")
transcript_retention_logger = logging.getLogger("openscribe.transcript_retention")


def active_transcript_condition(*, now: datetime | None = None):
    return Transcript.retention_expires_at > (now or utcnow())


def transcript_is_expired(transcript: Transcript, *, now: datetime | None = None) -> bool:
    expires_at = transcript.retention_expires_at
    comparison_now = now or utcnow()
    if expires_at.tzinfo is None and comparison_now.tzinfo is not None:
        comparison_now = comparison_now.replace(tzinfo=None)
    return expires_at <= comparison_now


def _retry_source_available(job: TranscriptIngestionJob) -> bool:
    return bool(job.source_audio_blob or job.source_audio_vault_ref)


def _read_retry_source_audio(job: TranscriptIngestionJob) -> bytes:
    if job.source_audio_blob:
        return job.source_audio_blob
    if job.source_audio_vault_ref:
        try:
            return read_transcript_ingestion_source_audio(secret_ref=job.source_audio_vault_ref)
        except AppError as exc:
            if exc.code == "vault_read_failed":
                raise AppError(
                    409,
                    "ingestion_retry_unavailable",
                    "The failed upload is no longer available to retry. Upload the audio file again.",
                ) from exc
            raise
    raise AppError(409, "ingestion_retry_unavailable", "The failed upload is no longer available to retry. Upload the audio file again.")


def _read_queued_source_audio(db: Session, job: TranscriptIngestionJob, *, legacy_audio_bytes: bytes | None = None) -> bytes:
    if not job.source_audio_vault_ref:
        if legacy_audio_bytes is not None:
            job.source_audio_vault_ref = write_transcript_ingestion_source_audio(job_id=job.id, audio_bytes=legacy_audio_bytes)
            db.add(job)
            db.commit()
            db.refresh(job)
            return legacy_audio_bytes
        raise AppError(
            409,
            "ingestion_source_unavailable",
            "Queued audio is no longer available. Upload the audio file again.",
            {"job_id": str(job.id), "transcript_id": str(job.transcript_id)},
        )
    try:
        return read_transcript_ingestion_source_audio(secret_ref=job.source_audio_vault_ref)
    except AppError as exc:
        if exc.code == "vault_read_failed":
            raise AppError(
                409,
                "ingestion_source_unavailable",
                "Queued audio is no longer available. Upload the audio file again.",
                {"job_id": str(job.id), "transcript_id": str(job.transcript_id)},
            ) from exc
        raise


def clear_ingestion_retry_source(
    db: Session,
    *,
    job_id: UUID,
    clear_storage: bool,
    clear_accounting: bool,
    delete_backing_secret: bool = False,
) -> TranscriptIngestionJob:
    job = db.get(TranscriptIngestionJob, job_id)
    if job is None:
        raise AppError(404, "not_found", "Transcript ingestion job not found", {"resource": "transcript_ingestion_job", "job_id": str(job_id)})
    source_audio_vault_ref = job.source_audio_vault_ref
    if clear_storage:
        job.source_audio_blob = None
        job.source_audio_vault_ref = None
    if clear_accounting:
        job.source_audio_size_bytes = None
        job.source_audio_duration_seconds = None
    db.add(job)
    cleanup_job_ids = (
        queue_transcript_audio_cleanup(db, secret_refs=[source_audio_vault_ref])
        if clear_storage and delete_backing_secret and source_audio_vault_ref
        else []
    )
    db.commit()
    db.refresh(job)
    if cleanup_job_ids:
        process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_job_ids)
    return job


TRANSCRIPT_AUDIO_CLEANUP_RETRY_BASE_SECONDS = 10
TRANSCRIPT_AUDIO_CLEANUP_RETRY_MAX_SECONDS = 60 * 60
TRANSCRIPT_AUDIO_CLEANUP_COMPENSATION_ENQUEUE_ATTEMPTS = 2


def _transcript_audio_cleanup_insert(db: Session):
    """Return an INSERT supporting unique-ref conflict suppression for this DB."""
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        return postgresql_insert(TranscriptAudioCleanupJob)
    if dialect_name == "sqlite":
        return sqlite_insert(TranscriptAudioCleanupJob)
    raise AppError(500, "transcript_audio_cleanup_enqueue_unsupported", "Transcript audio cleanup enqueue is not supported by this database")


def queue_transcript_audio_cleanup(
    db: Session,
    *,
    secret_refs: list[str] | tuple[str, ...] | set[str],
) -> list[UUID]:
    """Queue audio refs within caller transaction before their final DB reference disappears."""
    refs = sorted({ref for ref in secret_refs if ref})
    if not refs:
        return []
    insert = _transcript_audio_cleanup_insert(db)
    for secret_ref in refs:
        db.execute(
            insert.values(
                id=uuid4(),
                secret_ref=secret_ref,
                next_attempt_at=utcnow(),
            ).on_conflict_do_nothing(index_elements=["secret_ref"])
        )
    jobs_by_ref = {
        job.secret_ref: job
        for job in db.scalars(select(TranscriptAudioCleanupJob).where(TranscriptAudioCleanupJob.secret_ref.in_(refs)))
    }
    if len(jobs_by_ref) != len(refs):
        raise AppError(500, "transcript_audio_cleanup_enqueue_failed", "Transcript audio cleanup could not be durably queued")
    return [jobs_by_ref[secret_ref].id for secret_ref in refs]


def queue_orphan_transcript_audio_after_rollback(db: Session, *, secret_ref: str) -> None:
    """Compensate a Vault audio write after its owning ingestion transaction rolls back."""
    if not secret_ref:
        return
    enqueue_error_code = "database_error"
    for attempt in range(1, TRANSCRIPT_AUDIO_CLEANUP_COMPENSATION_ENQUEUE_ATTEMPTS + 1):
        try:
            queue_transcript_audio_cleanup(db, secret_refs=[secret_ref])
            db.commit()
            return
        except Exception as exc:
            enqueue_error_code = exc.code if isinstance(exc, AppError) else "database_error"
            try:
                db.rollback()
            except Exception:
                enqueue_error_code = "database_rollback_error"
            retry_audio_logger.warning(
                "transcript_audio_orphan_enqueue_retry_failed",
                extra={"attempt": attempt, "error_code": enqueue_error_code},
            )

    # Ref was written by a transaction already rolled back. Vault helper checks
    # the exact transcript-ingestion ref shape before deletion.
    try:
        delete_transcript_ingestion_source_audio(secret_ref=secret_ref)
    except Exception as exc:
        delete_error_code = exc.code if isinstance(exc, AppError) else "vault_delete_error"
        retry_audio_logger.error(
            "transcript_audio_orphan_compensation_failed",
            extra={"enqueue_error_code": enqueue_error_code, "delete_error_code": delete_error_code},
        )
        raise AppError(
            502,
            "transcript_audio_cleanup_compensation_failed",
            "Transcript audio cleanup could not be durably queued or deleted",
        ) from exc


def queue_retry_source_cleanup_for_transcripts(db: Session, *, transcript_ids: list[UUID]) -> list[UUID]:
    if not transcript_ids:
        return []
    vault_refs = {
        secret_ref
        for secret_ref in db.scalars(
            select(TranscriptIngestionJob.source_audio_vault_ref).where(
                TranscriptIngestionJob.transcript_id.in_(transcript_ids),
                TranscriptIngestionJob.source_audio_vault_ref.is_not(None),
            )
        )
        if secret_ref
    }
    if not vault_refs:
        return []
    return queue_transcript_audio_cleanup(db, secret_refs=vault_refs)


def _is_live_transcript_ingestion_source_audio_ref(db: Session, *, secret_ref: str) -> bool:
    """Return whether an ingestion job still owns this retry-audio ref."""
    return db.scalar(
        select(TranscriptIngestionJob.id)
        .where(TranscriptIngestionJob.source_audio_vault_ref == secret_ref)
        .limit(1)
    ) is not None


def process_transcript_audio_cleanup_jobs(
    db: Session,
    *,
    job_ids: list[UUID] | None = None,
    batch_size: int = 100,
    now: datetime | None = None,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    comparison_now = now or utcnow()
    stmt = (
        select(TranscriptAudioCleanupJob)
        .where(TranscriptAudioCleanupJob.next_attempt_at <= comparison_now)
        .order_by(TranscriptAudioCleanupJob.next_attempt_at.asc(), TranscriptAudioCleanupJob.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    if job_ids is not None:
        if not job_ids:
            return 0
        stmt = stmt.where(TranscriptAudioCleanupJob.id.in_(job_ids))
    cleanup_jobs = list(db.scalars(stmt))
    deleted_count = 0
    for cleanup_job in cleanup_jobs:
        if _is_live_transcript_ingestion_source_audio_ref(db, secret_ref=cleanup_job.secret_ref):
            # A rollback/commit outcome can be ambiguous. The ref is still
            # retry source data, so discard stale cleanup intent rather than
            # risking deletion of owner content.
            db.delete(cleanup_job)
            continue
        try:
            delete_transcript_ingestion_source_audio(secret_ref=cleanup_job.secret_ref)
        except AppError as exc:
            retry_delay_seconds = min(
                TRANSCRIPT_AUDIO_CLEANUP_RETRY_BASE_SECONDS * (2 ** min(cleanup_job.attempt_count, 8)),
                TRANSCRIPT_AUDIO_CLEANUP_RETRY_MAX_SECONDS,
            )
            cleanup_job.attempt_count += 1
            cleanup_job.last_error_code = exc.code
            cleanup_job.next_attempt_at = comparison_now + timedelta(seconds=retry_delay_seconds)
            db.add(cleanup_job)
            retry_audio_logger.warning(
                "retry_audio_delete_failed",
                extra={
                    "cleanup_job_id": str(cleanup_job.id),
                    "attempt_count": cleanup_job.attempt_count,
                    "error_code": exc.code,
                },
            )
        else:
            db.delete(cleanup_job)
            deleted_count += 1
    db.commit()
    return deleted_count


def _create_transcript_row(
    db: Session,
    *,
    owner: User,
    title: str | None,
    current_draft_text_encrypted: str | None,
    structured_context_json: dict | None,
    ingestion_mode: TranscriptIngestionMode,
) -> Transcript:
    if owner.is_system_admin or owner.team_id is None:
        raise AppError(403, "forbidden", "System-admin accounts cannot own transcript content")
    if owner.team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(owner.team_id)})

    retention_days = owner.team.default_retention_days
    transcript_id = uuid4()
    normalized_structured_context = normalize_structured_working_note(structured_context_json)
    if structured_context_json is not None and normalized_structured_context is None:
        raise AppError(
            422,
            "validation_error",
            "Structured working note must use EMIS profile with at least one non-empty section",
        )
    transcript = Transcript(
        id=transcript_id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title=title,
        current_draft_text_encrypted=(
            encrypt_text_for_owner(
                db,
                owner_user_id=owner.id,
                table="transcripts",
                field="current_draft_text_encrypted",
                record_id=transcript_id,
                plaintext=current_draft_text_encrypted,
            )
            if current_draft_text_encrypted
            else None
        ),
        structured_context_json=(
            encrypt_json_for_owner(
                db,
                owner_user_id=owner.id,
                table="transcripts",
                field="structured_context_json",
                record_id=transcript_id,
                plaintext=normalized_structured_context,
            )
            if normalized_structured_context is not None
            else None
        ),
        working_note_mode=TranscriptWorkingNoteMode.structured if normalized_structured_context is not None else None,
        working_note_updated_at=utcnow() if normalized_structured_context is not None else None,
        ingestion_mode=ingestion_mode,
        status=TranscriptStatus.ready,
        next_live_chunk_sequence_no_applied=1,
        retention_days_applied=retention_days,
        retention_expires_at=transcript_expiry(retention_days),
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def start_transcript(db: Session, owner: User, payload: TranscriptStart) -> Transcript:
    allowed, message = can_create_new_session(db, owner)
    if not allowed:
        raise AppError(409, "business_rule_violation", message or "Cannot create a new transcript session")
    return _create_transcript_row(
        db,
        owner=owner,
        title=payload.title,
        current_draft_text_encrypted=payload.current_draft_text_encrypted,
        structured_context_json=payload.structured_context_json,
        ingestion_mode=payload.ingestion_mode,
    )


def create_transcript_from_payload(db: Session, actor: User, payload: TranscriptCreate) -> Transcript:
    owner = db.get(User, payload.owner_user_id)
    if not owner:
        raise AppError(404, "not_found", "Owner user not found", {"resource": "user", "user_id": str(payload.owner_user_id)})
    if actor.id != payload.owner_user_id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    if owner.team_id != payload.team_id:
        raise AppError(
            422,
            "business_rule_violation",
            "Owner user does not belong to the provided team",
            {"owner_user_id": str(payload.owner_user_id), "team_id": str(payload.team_id)},
        )
    allowed, message = can_create_new_session(db, owner)
    if not allowed:
        raise AppError(409, "business_rule_violation", message or "Cannot create a new transcript session")
    return _create_transcript_row(
        db,
        owner=owner,
        title=payload.title,
        current_draft_text_encrypted=payload.current_draft_text_encrypted,
        structured_context_json=payload.structured_context_json,
        ingestion_mode=payload.ingestion_mode,
    )


def _append_chunk_text(existing_text: str | None, chunk_text: str) -> str:
    normalized_chunk = chunk_text.strip()
    if not existing_text:
        return normalized_chunk
    if not normalized_chunk:
        return existing_text
    return f"{existing_text.rstrip()}\n{normalized_chunk}"


def _create_transcript_version_from_text(
    db: Session,
    *,
    transcript: Transcript,
    owner_user_id: UUID,
    plaintext: str,
) -> TranscriptVersion:
    current_max = db.scalar(select(func.max(TranscriptVersion.version_no)).where(TranscriptVersion.transcript_id == transcript.id))
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=(current_max or 0) + 1,
        text_encrypted=encrypt_text_for_owner(
            db,
            owner_user_id=owner_user_id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext=plaintext,
        ),
    )
    db.add(version)
    return version


def _latest_matching_transcript_version(
    db: Session,
    *,
    transcript: Transcript,
    plaintext: str,
) -> TranscriptVersion | None:
    versions = db.scalars(
        select(TranscriptVersion)
        .where(TranscriptVersion.transcript_id == transcript.id)
        .order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.created_at.desc(), TranscriptVersion.id.desc())
    )
    for version in versions:
        existing_text = (
            decrypt_text_for_owner(
                db,
                owner_user_id=transcript.owner_user_id,
                table="transcript_versions",
                field="text_encrypted",
                record_id=version.id,
                stored_value=version.text_encrypted,
            )
            or ""
        ).strip()
        if existing_text == plaintext.strip():
            return version
    return None


def _create_or_reuse_transcript_version_from_text(
    db: Session,
    *,
    transcript: Transcript,
    owner_user_id: UUID,
    plaintext: str,
) -> TranscriptVersion:
    existing = _latest_matching_transcript_version(db, transcript=transcript, plaintext=plaintext)
    if existing is not None:
        return existing
    return _create_transcript_version_from_text(
        db,
        transcript=transcript,
        owner_user_id=owner_user_id,
        plaintext=plaintext,
    )


def _attempt_preview_redaction(db: Session, *, transcript_version: TranscriptVersion) -> None:
    try:
        ensure_redaction_run_for_transcript_version(db, transcript_version=transcript_version)
    except AppError as exc:
        transcript_redaction_logger.warning(
            "preview_redaction_failed",
            extra={
                "transcript_id": str(transcript_version.transcript_id),
                "transcript_version_id": str(transcript_version.id),
                "error_code": exc.code,
            },
        )


def _preview_redact_current_draft_if_ready(db: Session, *, transcript: Transcript) -> None:
    if transcript.status is not TranscriptStatus.ready:
        return
    current_draft = (transcript_draft_text(db, transcript=transcript) or "").strip()
    if not current_draft:
        return
    version = _create_or_reuse_transcript_version_from_text(
        db,
        transcript=transcript,
        owner_user_id=transcript.owner_user_id,
        plaintext=current_draft,
    )
    db.add(transcript)
    db.commit()
    _attempt_preview_redaction(db, transcript_version=version)


def transcript_draft_text(db: Session, *, transcript: Transcript) -> str | None:
    return decrypt_text_for_owner(
        db,
        owner_user_id=transcript.owner_user_id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        stored_value=transcript.current_draft_text_encrypted,
    )


def transcript_structured_context(db: Session, *, transcript: Transcript) -> dict | None:
    value = decrypt_json_for_owner(
        db,
        owner_user_id=transcript.owner_user_id,
        table="transcripts",
        field="structured_context_json",
        record_id=transcript.id,
        stored_value=transcript.structured_context_json,
    )
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AppError(500, "content_crypto_invalid", "Transcript structured context is invalid")
    return value


def freeform_working_note_text(db: Session, *, transcript: Transcript) -> str:
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcripts",
            field="freeform_working_note_encrypted",
            record_id=transcript.id,
            stored_value=transcript.freeform_working_note_encrypted,
        )
        or ""
    )


def set_freeform_working_note_text(db: Session, *, transcript: Transcript, plaintext: str | None) -> None:
    transcript.freeform_working_note_encrypted = (
        encrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcripts",
            field="freeform_working_note_encrypted",
            record_id=transcript.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None
    )


def _normalize_working_note_line(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def normalize_structured_working_note(raw_value: dict | None) -> dict | None:
    if not isinstance(raw_value, dict):
        return None
    profile = raw_value.get("profile")
    sections = raw_value.get("sections")
    if profile != "emis" or not isinstance(sections, dict):
        return None
    unsupported_section_keys = sorted(set(sections) - set(EMIS_WORKING_NOTE_SECTION_KEYS))
    if unsupported_section_keys:
        raise AppError(
            422,
            "validation_error",
            "Structured working note contains unsupported section keys",
            {"section_keys": unsupported_section_keys},
        )
    normalized_sections: dict[str, list[str]] = {}
    total_chars = 0
    for section_key in EMIS_WORKING_NOTE_SECTION_KEYS:
        raw_section_value = sections.get(section_key)
        if isinstance(raw_section_value, list):
            lines = [_normalize_working_note_line(item) for item in raw_section_value]
        elif isinstance(raw_section_value, str):
            lines = [_normalize_working_note_line(raw_section_value)]
        else:
            lines = []
        clean_lines = [line for line in lines if line]
        section_chars = sum(len(line) for line in clean_lines)
        if section_chars > 4000:
            raise AppError(422, "validation_error", "Structured working note section is too long", {"section_key": section_key})
        total_chars += section_chars
        if clean_lines:
            normalized_sections[section_key] = clean_lines
    if total_chars > 20000:
        raise AppError(422, "validation_error", "Structured working note is too long")
    if not normalized_sections:
        return None
    return {"profile": "emis", "sections": normalized_sections}


def transcript_has_working_note(db: Session, *, transcript: Transcript) -> bool:
    return transcript_working_note_mode(db, transcript=transcript) is not None


def transcript_working_note_mode(db: Session, *, transcript: Transcript) -> TranscriptWorkingNoteMode | None:
    if transcript.working_note_mode is TranscriptWorkingNoteMode.freeform:
        return TranscriptWorkingNoteMode.freeform if freeform_working_note_text(db, transcript=transcript).strip() else None
    if transcript.working_note_mode is TranscriptWorkingNoteMode.structured:
        return (
            TranscriptWorkingNoteMode.structured
            if normalize_structured_working_note(transcript_structured_context(db, transcript=transcript)) is not None
            else None
        )
    return (
        TranscriptWorkingNoteMode.structured
        if normalize_structured_working_note(transcript_structured_context(db, transcript=transcript)) is not None
        else None
    )


def working_note_detail(db: Session, actor: User, *, transcript_id: UUID) -> dict:
    transcript = _get_owner_transcript_for_ingestion(db, actor, transcript_id=transcript_id)
    structured_note = normalize_structured_working_note(transcript_structured_context(db, transcript=transcript))
    mode = transcript_working_note_mode(db, transcript=transcript)
    return {
        "transcript_id": transcript.id,
        "mode": mode,
        "freeform_text": freeform_working_note_text(db, transcript=transcript) if mode is TranscriptWorkingNoteMode.freeform else "",
        "structured_note": structured_note if mode is TranscriptWorkingNoteMode.structured else None,
        "updated_at": transcript.working_note_updated_at if mode is not None else None,
    }


def _assert_working_note_update_current(transcript: Transcript, expected_updated_at: datetime | None) -> None:
    if transcript.working_note_updated_at is None:
        if expected_updated_at is not None:
            raise AppError(409, "conflict", "Working note changed elsewhere. Reload before saving again.")
        return
    if expected_updated_at is None:
        raise AppError(409, "conflict", "Working note changed elsewhere. Reload before saving again.")
    normalized_expected = expected_updated_at
    if normalized_expected.tzinfo is None:
        normalized_expected = normalized_expected.replace(tzinfo=timezone.utc)
    if transcript.working_note_updated_at != normalized_expected:
        raise AppError(409, "conflict", "Working note changed elsewhere. Reload before saving again.")


def save_working_note(db: Session, actor: User, *, transcript_id: UUID, payload: WorkingNoteUpdate) -> Transcript:
    transcript = _get_owner_transcript_for_ingestion(db, actor, transcript_id=transcript_id)
    _assert_working_note_update_current(transcript, payload.expected_updated_at)
    if transcript.working_note_mode is not None and transcript.working_note_mode is not payload.mode and transcript_has_working_note(db, transcript=transcript):
        raise AppError(
            409,
            "business_rule_violation",
            "Clear the working note before switching mode.",
            {"code": "working_note_mode_locked", "working_note_mode": transcript.working_note_mode.value},
        )
    if payload.mode is TranscriptWorkingNoteMode.freeform:
        clean_text = (payload.freeform_text or "").strip()
        if not clean_text:
            raise AppError(422, "validation_error", "Clear the working note instead of saving empty text")
        transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
        set_freeform_working_note_text(db, transcript=transcript, plaintext=clean_text)
        set_transcript_structured_context(db, transcript=transcript, plaintext=None)
    else:
        structured_note = normalize_structured_working_note(payload.structured_note.model_dump() if payload.structured_note else None)
        if structured_note is None:
            raise AppError(422, "validation_error", "Clear the working note instead of saving empty sections")
        transcript.working_note_mode = TranscriptWorkingNoteMode.structured
        set_freeform_working_note_text(db, transcript=transcript, plaintext=None)
        set_transcript_structured_context(db, transcript=transcript, plaintext=structured_note)
    transcript.working_note_updated_at = utcnow()
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def clear_working_note(db: Session, actor: User, *, transcript_id: UUID, expected_updated_at: datetime | None = None) -> None:
    transcript = _get_owner_transcript_for_ingestion(db, actor, transcript_id=transcript_id)
    _assert_working_note_update_current(transcript, expected_updated_at)
    transcript.working_note_mode = None
    set_freeform_working_note_text(db, transcript=transcript, plaintext=None)
    set_transcript_structured_context(db, transcript=transcript, plaintext=None)
    transcript.working_note_updated_at = None
    db.add(transcript)
    db.commit()


def set_transcript_draft_text(db: Session, *, transcript: Transcript, plaintext: str | None) -> None:
    transcript.current_draft_text_encrypted = (
        encrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None
    )


def set_transcript_structured_context(db: Session, *, transcript: Transcript, plaintext: dict | None) -> None:
    transcript.structured_context_json = (
        encrypt_json_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcripts",
            field="structured_context_json",
            record_id=transcript.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None
    )


def transcript_version_text(db: Session, *, transcript_version: TranscriptVersion) -> str:
    return (
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


def job_result_text(db: Session, *, transcript: Transcript, job: TranscriptIngestionJob) -> str | None:
    return decrypt_text_for_owner(
        db,
        owner_user_id=transcript.owner_user_id,
        table="transcript_ingestion_jobs",
        field="result_text_encrypted",
        record_id=job.id,
        stored_value=job.result_text_encrypted,
    )


def latest_ingestion_job_for_transcript(db: Session, *, transcript_id: UUID) -> TranscriptIngestionJob | None:
    return db.scalar(
        select(TranscriptIngestionJob)
        .where(TranscriptIngestionJob.transcript_id == transcript_id)
        .order_by(TranscriptIngestionJob.created_at.desc(), TranscriptIngestionJob.id.desc())
        .limit(1)
    )


def latest_successful_ingestion_completed_at(db: Session, *, transcript_id: UUID) -> datetime | None:
    return db.scalar(
        select(TranscriptIngestionJob.completed_at)
        .where(
            TranscriptIngestionJob.transcript_id == transcript_id,
            TranscriptIngestionJob.status.in_(
                [TranscriptIngestionJobStatus.applied, TranscriptIngestionJobStatus.completed]
            ),
            TranscriptIngestionJob.completed_at.is_not(None),
        )
        .order_by(TranscriptIngestionJob.completed_at.desc(), TranscriptIngestionJob.id.desc())
        .limit(1)
    )


def _has_pending_ingestion_jobs(db: Session, *, transcript_id: UUID) -> bool:
    return db.scalar(
        select(TranscriptIngestionJob.id)
        .where(
            TranscriptIngestionJob.transcript_id == transcript_id,
            TranscriptIngestionJob.status.in_(
                [
                    TranscriptIngestionJobStatus.queued,
                    TranscriptIngestionJobStatus.processing,
                    TranscriptIngestionJobStatus.completed,
                ]
            ),
        )
        .limit(1)
    ) is not None


def _has_blocking_live_chunk_failure(db: Session, *, transcript: Transcript) -> bool:
    if transcript.ingestion_mode is not TranscriptIngestionMode.live_chunked:
        return False
    return db.scalar(
        select(TranscriptIngestionJob.id)
        .where(
            TranscriptIngestionJob.transcript_id == transcript.id,
            TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
            TranscriptIngestionJob.chunk_sequence_no == transcript.next_live_chunk_sequence_no_applied,
            TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed,
        )
        .limit(1)
    ) is not None


def _resolved_transcript_status(db: Session, *, transcript: Transcript) -> TranscriptStatus:
    if transcript.status is TranscriptStatus.recording:
        return TranscriptStatus.recording
    if _has_pending_ingestion_jobs(db, transcript_id=transcript.id):
        return TranscriptStatus.transcribing
    if _has_blocking_live_chunk_failure(db, transcript=transcript):
        return TranscriptStatus.failed
    latest_job = latest_ingestion_job_for_transcript(db, transcript_id=transcript.id)
    if latest_job is not None and latest_job.status is TranscriptIngestionJobStatus.failed:
        return TranscriptStatus.failed
    return TranscriptStatus.ready


def ingestion_usage_totals_for_owner(
    db: Session,
    *,
    owner_user_id: UUID,
    since: datetime,
    job_kinds: tuple[TranscriptIngestionJobKind, ...],
    duration_column,
    exclude_job_ids: tuple[UUID, ...] = (),
) -> tuple[int, float]:
    statement = (
        select(
            func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0),
            func.coalesce(func.sum(duration_column), 0.0),
        )
        .select_from(TranscriptIngestionJob)
        .join(Transcript, Transcript.id == TranscriptIngestionJob.transcript_id)
        .where(
            Transcript.owner_user_id == owner_user_id,
            TranscriptIngestionJob.job_kind.in_(job_kinds),
            TranscriptIngestionJob.created_at >= since,
        )
    )
    if exclude_job_ids:
        statement = statement.where(TranscriptIngestionJob.id.not_in(exclude_job_ids))
    total_bytes, total_duration = db.execute(statement).one()
    return int(total_bytes or 0), float(total_duration or 0.0)


def next_live_chunk_sequence_no_for_transcript(db: Session, *, transcript_id: UUID) -> int:
    max_sequence = db.scalar(
        select(TranscriptIngestionJob.chunk_sequence_no)
        .where(
            TranscriptIngestionJob.transcript_id == transcript_id,
            TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
            TranscriptIngestionJob.chunk_sequence_no.is_not(None),
        )
        .order_by(TranscriptIngestionJob.chunk_sequence_no.desc())
        .limit(1)
    )
    return (max_sequence or 0) + 1


def _latest_owner_transcript(db: Session, owner: User) -> Transcript | None:
    return db.scalar(
        select(Transcript)
        .where(Transcript.owner_user_id == owner.id, active_transcript_condition())
        .order_by(Transcript.created_at.desc())
        .limit(1)
    )


def _transcript_has_meaningful_content(db: Session, transcript: Transcript) -> bool:
    current_draft = transcript_draft_text(db, transcript=transcript)
    if current_draft and current_draft.strip():
        return True
    if transcript_has_working_note(db, transcript=transcript):
        return True
    if db.scalar(select(TranscriptVersion.id).where(TranscriptVersion.transcript_id == transcript.id).limit(1)) is not None:
        return True
    if db.scalar(select(TranscriptIngestionJob.id).where(TranscriptIngestionJob.transcript_id == transcript.id).limit(1)) is not None:
        return True
    return False


def can_create_new_session(db: Session, owner: User) -> tuple[bool, str | None]:
    latest = _latest_owner_transcript(db, owner)
    if latest is None:
        return True, None
    latest = reconcile_transcript_status(db, transcript=latest)
    if _transcript_has_meaningful_content(db, latest):
        return True, None
    return False, "Finish or delete the current empty session before creating a new one"


def can_switch_transcript_ingestion_mode(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    target_mode: TranscriptIngestionMode,
) -> tuple[Transcript, bool, str | None]:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    if transcript.ingestion_mode is target_mode:
        return transcript, True, None
    if transcript.status is TranscriptStatus.recording:
        return transcript, False, "Stop the active recording before switching input mode"
    if _has_pending_ingestion_jobs(db, transcript_id=transcript.id):
        return transcript, False, "Wait for the current session transcription to finish before switching input mode"
    return transcript, True, None


def _get_owner_transcript_for_ingestion(db: Session, owner: User, *, transcript_id: UUID) -> Transcript:
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != owner.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    if transcript_is_expired(transcript):
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    return transcript


def get_active_owner_transcript(db: Session, owner: User, *, transcript_id: UUID) -> Transcript:
    return _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)


def _normalize_manual_pii_type(value: str | None) -> str:
    normalized = " ".join((value or "PII").strip().split())
    return normalized[:255] or "PII"


def _normalize_manual_pii_value(value: str) -> str:
    return " ".join(value.strip().split())


def _manual_pii_value_hash(db: Session, *, owner_user_id: UUID, value: str) -> str:
    normalized = _normalize_manual_pii_value(value).lower()
    return keyed_digest_for_owner(
        db,
        owner_user_id=owner_user_id,
        purpose="transcript_manual_pii_entities.normalized_value_hash",
        value=normalized,
    )


def _legacy_manual_pii_value_hash(value: str) -> str:
    normalized = _normalize_manual_pii_value(value).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def manual_pii_entity_value(db: Session, *, entity: TranscriptManualPiiEntity) -> str:
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=entity.owner_user_id,
            table="transcript_manual_pii_entities",
            field="original_value_encrypted",
            record_id=entity.id,
            stored_value=entity.original_value_encrypted,
        )
        or ""
    )


def create_manual_pii_entity(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    entity_type: str,
    value: str,
    occurrence_count: int = 1,
) -> TranscriptManualPiiEntity:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    normalized_type = _normalize_manual_pii_type(entity_type)
    normalized_value = _normalize_manual_pii_value(value)
    if not normalized_value:
        raise AppError(422, "business_rule_violation", "PII value is required", {"field": "value"})
    normalized_hash = _manual_pii_value_hash(db, owner_user_id=owner.id, value=normalized_value)
    legacy_normalized_hash = _legacy_manual_pii_value_hash(normalized_value)
    existing = db.scalar(
        select(TranscriptManualPiiEntity)
        .where(
            TranscriptManualPiiEntity.transcript_id == transcript.id,
            TranscriptManualPiiEntity.entity_type == normalized_type,
            TranscriptManualPiiEntity.normalized_value_hash.in_([normalized_hash, legacy_normalized_hash]),
        )
        .limit(1)
    )
    if existing is not None:
        existing.normalized_value_hash = normalized_hash
        existing.occurrence_count = max(existing.occurrence_count, occurrence_count)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    entity_id = uuid4()
    entity = TranscriptManualPiiEntity(
        id=entity_id,
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        entity_type=normalized_type,
        original_value_encrypted=encrypt_text_for_owner(
            db,
            owner_user_id=owner.id,
            table="transcript_manual_pii_entities",
            field="original_value_encrypted",
            record_id=entity_id,
            plaintext=normalized_value,
        ),
        normalized_value_hash=normalized_hash,
        occurrence_count=occurrence_count,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


def delete_manual_pii_entity(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    entity_id: UUID,
) -> None:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    entity = db.get(TranscriptManualPiiEntity, entity_id)
    if entity is None or entity.transcript_id != transcript.id:
        raise AppError(404, "not_found", "Manual PII entity not found", {"resource": "transcript_manual_pii_entity", "entity_id": str(entity_id)})
    if entity.owner_user_id != owner.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    db.delete(entity)
    db.commit()


def _enforce_live_chunk_hourly_duration_budget(
    db: Session,
    *,
    owner: User,
    duration_seconds: float,
) -> None:
    if LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS <= 0:
        return
    _, used_duration_seconds = ingestion_usage_totals_for_owner(
        db,
        owner_user_id=owner.id,
        since=utcnow() - timedelta(hours=1),
        job_kinds=(TranscriptIngestionJobKind.live_chunk,),
        duration_column=TranscriptIngestionJob.declared_duration_seconds,
    )
    projected_duration_seconds = used_duration_seconds + duration_seconds
    if projected_duration_seconds <= LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS:
        return
    raise AppError(
        429,
        "rate_limited",
        "Live transcription hourly audio limit exceeded",
        {
            "window": "1 hour",
            "max_seconds": LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS,
            "used_seconds": round(used_duration_seconds, 2),
            "requested_seconds": round(duration_seconds, 3),
        },
    )


def _enforce_whole_file_hourly_usage_budget(
    db: Session,
    *,
    owner: User,
    source_audio_size_bytes: int,
    source_audio_duration_seconds: float,
    exclude_job_ids: tuple[UUID, ...] = (),
) -> None:
    if WHOLE_FILE_HOURLY_UPLOAD_BYTES <= 0 and WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS <= 0:
        return
    used_bytes, used_duration_seconds = ingestion_usage_totals_for_owner(
        db,
        owner_user_id=owner.id,
        since=utcnow() - timedelta(hours=1),
        job_kinds=(TranscriptIngestionJobKind.audio_file,),
        duration_column=TranscriptIngestionJob.source_audio_duration_seconds,
        exclude_job_ids=exclude_job_ids,
    )
    projected_bytes = used_bytes + source_audio_size_bytes
    projected_duration_seconds = used_duration_seconds + source_audio_duration_seconds
    if WHOLE_FILE_HOURLY_UPLOAD_BYTES > 0 and projected_bytes > WHOLE_FILE_HOURLY_UPLOAD_BYTES:
        raise AppError(
            429,
            "rate_limited",
            "Whole-file hourly upload size limit exceeded",
            {
                "window": "1 hour",
                "max_bytes": WHOLE_FILE_HOURLY_UPLOAD_BYTES,
                "used_bytes": used_bytes,
                "requested_bytes": source_audio_size_bytes,
            },
        )
    if WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS > 0 and projected_duration_seconds > WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS:
        raise AppError(
            429,
            "rate_limited",
            "Whole-file hourly audio limit exceeded",
            {
                "window": "1 hour",
                "max_seconds": WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS,
                "used_seconds": round(used_duration_seconds, 2),
                "requested_seconds": round(source_audio_duration_seconds, 2),
            },
        )


def update_transcript_title(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    title: str | None,
) -> Transcript:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    normalized_title = (title or "").strip() or None
    transcript.title = normalized_title
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def update_transcript(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    title: str | None,
    ingestion_mode: TranscriptIngestionMode | None,
    structured_context_json: dict | None,
    expected_updated_at: datetime | None = None,
) -> Transcript:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    if title is not None:
        transcript.title = (title or "").strip() or None
    if ingestion_mode is not None and ingestion_mode is not transcript.ingestion_mode:
        _, allowed, message = can_switch_transcript_ingestion_mode(
            db,
            owner,
            transcript_id=transcript_id,
            target_mode=ingestion_mode,
        )
        if not allowed:
            raise AppError(409, "business_rule_violation", message or "Cannot switch transcript input mode")
        transcript.ingestion_mode = ingestion_mode
    if structured_context_json is not None:
        normalized_structured_context = normalize_structured_working_note(structured_context_json)
        if normalized_structured_context is None:
            raise AppError(
                422,
                "validation_error",
                "Structured working note must use EMIS profile with at least one non-empty section",
            )
        _assert_working_note_update_current(transcript, expected_updated_at)
        if transcript.working_note_mode is TranscriptWorkingNoteMode.freeform and transcript_has_working_note(db, transcript=transcript):
            raise AppError(409, "business_rule_violation", "Clear the working note before switching mode.", {"code": "working_note_mode_locked", "working_note_mode": transcript.working_note_mode.value})
        transcript.working_note_mode = TranscriptWorkingNoteMode.structured
        transcript.working_note_updated_at = utcnow()
        set_freeform_working_note_text(db, transcript=transcript, plaintext=None)
        set_transcript_structured_context(db, transcript=transcript, plaintext=normalized_structured_context)
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def commit_transcript_text(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    plaintext: str,
) -> Transcript:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    version = _create_transcript_version_from_text(
        db,
        transcript=transcript,
        owner_user_id=owner.id,
        plaintext=plaintext,
    )
    set_transcript_draft_text(db, transcript=transcript, plaintext=plaintext)
    transcript.status = TranscriptStatus.ready
    db.add(transcript)
    db.commit()
    _attempt_preview_redaction(db, transcript_version=version)
    db.refresh(transcript)
    return transcript


def delete_transcripts(
    db: Session,
    owner: User,
    *,
    transcript_ids: list[UUID],
) -> int:
    unique_ids: list[UUID] = []
    for transcript_id in transcript_ids:
        if transcript_id not in unique_ids:
            unique_ids.append(transcript_id)

    if not unique_ids:
        raise AppError(422, "business_rule_violation", "Select at least one transcript to delete")

    transcripts = [
        _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
        for transcript_id in unique_ids
    ]
    cleanup_job_ids = queue_retry_source_cleanup_for_transcripts(db, transcript_ids=[transcript.id for transcript in transcripts])
    deleted_count = len(transcripts)
    deleted_ids = [str(transcript.id) for transcript in transcripts]
    for transcript in transcripts:
        db.delete(transcript)
    db.commit()
    process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_job_ids)
    record_security_event(
        db,
        action="transcript_root_deleted",
        actor=owner,
        team_id=owner.team_id,
        details={"category": "transcript", "outcome": "success", "object_type": "transcript", "object_ids": deleted_ids, "deleted_count": deleted_count},
    )
    return deleted_count


def delete_expired_transcripts(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = 100,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    comparison_now = now or utcnow()
    transcripts = list(
        db.scalars(
            select(Transcript)
            .where(Transcript.retention_expires_at <= comparison_now)
            .order_by(Transcript.retention_expires_at.asc(), Transcript.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    if not transcripts:
        return 0

    transcript_ids = [transcript.id for transcript in transcripts]
    cleanup_job_ids = queue_retry_source_cleanup_for_transcripts(db, transcript_ids=transcript_ids)
    for transcript in transcripts:
        db.delete(transcript)
    db.commit()
    process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_job_ids)
    transcript_retention_logger.info(
        "expired_transcript_roots_deleted",
        extra={"deleted_count": len(transcripts)},
    )
    return len(transcripts)


def queue_audio_chunk_ingestion(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    filename: str,
    source_audio_bytes: bytes,
    chunk_sequence_no: int,
    declared_duration_seconds: float | None,
) -> tuple[Transcript, TranscriptIngestionJob]:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    if transcript.ingestion_mode is not TranscriptIngestionMode.live_chunked:
        raise AppError(
            409,
            "business_rule_violation",
            "Transcript ingestion mode does not accept live audio chunks",
            {"ingestion_mode": transcript.ingestion_mode.value},
        )
    existing = db.scalar(
        select(TranscriptIngestionJob).where(
            TranscriptIngestionJob.transcript_id == transcript.id,
            TranscriptIngestionJob.chunk_sequence_no == chunk_sequence_no,
        )
    )
    if existing is not None:
        raise AppError(
            409,
            "conflict",
            "Chunk sequence number has already been submitted",
            {"transcript_id": str(transcript.id), "chunk_sequence_no": chunk_sequence_no},
        )
    _, config, resolved_model_name, resolved_language = resolve_selected_team_stt(db, team_id=transcript.team_id)
    ensure_stt_config_credential_ready(team_id=transcript.team_id, config=config)
    try:
        measured_duration_seconds = inspect_audio_duration_seconds(
            audio_bytes=source_audio_bytes,
            source_filename=filename,
        )
    except AppError as exc:
        if exc.code not in {"audio_duration_probe_failed", "audio_normalization_failed"}:
            raise
        raise AppError(422, "business_rule_violation", "Audio duration could not be inspected", {"field": "audio"}) from exc

    if measured_duration_seconds > 30:
        raise AppError(
            422,
            "business_rule_violation",
            "Audio duration exceeds the current maximum",
            {"field": "audio", "max_seconds": 30, "duration_seconds": round(measured_duration_seconds, 3)},
        )

    _enforce_live_chunk_hourly_duration_budget(
        db,
        owner=owner,
        duration_seconds=measured_duration_seconds,
    )
    job_id = uuid4()
    source_audio_vault_ref = None
    try:
        source_audio_vault_ref = write_transcript_ingestion_source_audio(job_id=job_id, audio_bytes=source_audio_bytes)
    except Exception:
        raise

    job = TranscriptIngestionJob(
        id=job_id,
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=chunk_sequence_no,
        source_filename=filename,
        source_audio_blob=None,
        source_audio_vault_ref=source_audio_vault_ref,
        source_audio_size_bytes=len(source_audio_bytes),
        declared_duration_seconds=measured_duration_seconds,
        stt_config_id=config.id,
        stt_provider_preset=config.provider_preset,
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=resolved_model_name,
        stt_model_field_name=config.model_field_name or "model",
        stt_language=resolved_language,
        stt_language_field_name=config.language_field_name or "language",
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
        stt_segments_path=config.segments_path,
        stt_segment_text_field=config.segment_text_field,
        stt_segment_start_field=config.segment_start_field,
        stt_segment_end_field=config.segment_end_field,
        stt_segment_speaker_field=config.segment_speaker_field,
        stt_extra_form_fields_json=dict(config.extra_form_fields_json or {}),
        status=TranscriptIngestionJobStatus.queued,
    )
    transcript.status = TranscriptStatus.transcribing
    db.add(job)
    db.add(transcript)
    try:
        db.commit()
    except Exception:
        if source_audio_vault_ref is not None:
            db.rollback()
            queue_orphan_transcript_audio_after_rollback(db, secret_ref=source_audio_vault_ref)
        raise
    db.refresh(transcript)
    db.refresh(job)
    record_security_event(
        db,
        action="audio_ingestion_queued",
        actor=owner,
        target=owner,
        team_id=transcript.team_id,
        details={
            "category": "upload",
            "outcome": "success",
            "object_type": "transcript_ingestion_job",
            "object_id": str(job.id),
            "transcript_id": str(transcript.id),
            "job_kind": job.job_kind.value,
            "source_audio_size_bytes": job.source_audio_size_bytes,
            "duration_seconds": round(measured_duration_seconds, 3),
        },
    )
    return transcript, job


def queue_audio_file_ingestion(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    filename: str,
    source_audio_blob: bytes,
    source_audio_duration_seconds: float | None = None,
    source_audio_vault_ref: str | None = None,
    exclude_job_ids: tuple[UUID, ...] = (),
) -> tuple[Transcript, TranscriptIngestionJob]:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    if transcript.ingestion_mode is not TranscriptIngestionMode.whole_file:
        raise AppError(
            409,
            "business_rule_violation",
            "Transcript ingestion mode does not accept file ingestion",
            {"ingestion_mode": transcript.ingestion_mode.value},
        )
    existing_in_progress = db.scalar(
        select(TranscriptIngestionJob).where(
            TranscriptIngestionJob.transcript_id == transcript.id,
            TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file,
            TranscriptIngestionJob.status.in_(
                [TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing]
            ),
        )
    )
    if existing_in_progress is not None:
        raise AppError(
            409,
            "conflict",
            "A file transcription job is already in progress for this session",
            {"transcript_id": str(transcript.id)},
        )
    _, config, resolved_model_name, resolved_language = resolve_selected_team_stt(db, team_id=transcript.team_id)
    ensure_stt_config_credential_ready(team_id=transcript.team_id, config=config)
    resolved_source_audio_duration_seconds = source_audio_duration_seconds
    if resolved_source_audio_duration_seconds is None:
        try:
            resolved_source_audio_duration_seconds = probe_audio_duration_seconds(
                audio_bytes=source_audio_blob,
                source_filename=filename,
            )
        except AppError as exc:
            if exc.code != "audio_duration_probe_failed":
                raise
            resolved_source_audio_duration_seconds = None
    if resolved_source_audio_duration_seconds is not None:
        _enforce_whole_file_hourly_usage_budget(
            db,
            owner=owner,
            source_audio_size_bytes=len(source_audio_blob),
            source_audio_duration_seconds=resolved_source_audio_duration_seconds,
            exclude_job_ids=exclude_job_ids,
        )
    elif WHOLE_FILE_HOURLY_UPLOAD_BYTES > 0:
        used_bytes, _ = ingestion_usage_totals_for_owner(
            db,
            owner_user_id=owner.id,
            since=utcnow() - timedelta(hours=1),
            job_kinds=(TranscriptIngestionJobKind.audio_file,),
            duration_column=TranscriptIngestionJob.source_audio_duration_seconds,
            exclude_job_ids=exclude_job_ids,
        )
        projected_bytes = used_bytes + len(source_audio_blob)
        if projected_bytes > WHOLE_FILE_HOURLY_UPLOAD_BYTES:
            raise AppError(
                429,
                "rate_limited",
                "Whole-file hourly upload size limit exceeded",
                {
                    "window": "1 hour",
                    "max_bytes": WHOLE_FILE_HOURLY_UPLOAD_BYTES,
                    "used_bytes": used_bytes,
                    "requested_bytes": len(source_audio_blob),
                },
            )

    job_id = uuid4()
    persisted_source_audio_vault_ref = source_audio_vault_ref
    if persisted_source_audio_vault_ref is None:
        persisted_source_audio_vault_ref = write_transcript_ingestion_source_audio(job_id=job_id, audio_bytes=source_audio_blob)

    job = TranscriptIngestionJob(
        id=job_id,
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        chunk_sequence_no=None,
        source_filename=filename,
        source_audio_blob=None,
        source_audio_vault_ref=persisted_source_audio_vault_ref,
        source_audio_size_bytes=len(source_audio_blob),
        source_audio_duration_seconds=resolved_source_audio_duration_seconds,
        stt_config_id=config.id,
        stt_provider_preset=config.provider_preset,
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=resolved_model_name,
        stt_model_field_name=config.model_field_name or "model",
        stt_language=resolved_language,
        stt_language_field_name=config.language_field_name or "language",
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
        stt_segments_path=config.segments_path,
        stt_segment_text_field=config.segment_text_field,
        stt_segment_start_field=config.segment_start_field,
        stt_segment_end_field=config.segment_end_field,
        stt_segment_speaker_field=config.segment_speaker_field,
        stt_extra_form_fields_json=dict(config.extra_form_fields_json or {}),
        status=TranscriptIngestionJobStatus.queued,
    )
    transcript.status = TranscriptStatus.transcribing
    db.add(job)
    db.add(transcript)
    try:
        db.commit()
    except Exception:
        if source_audio_vault_ref is None and persisted_source_audio_vault_ref is not None:
            db.rollback()
            queue_orphan_transcript_audio_after_rollback(db, secret_ref=persisted_source_audio_vault_ref)
        raise
    db.refresh(transcript)
    db.refresh(job)
    record_security_event(
        db,
        action="audio_ingestion_queued",
        actor=owner,
        target=owner,
        team_id=transcript.team_id,
        details={
            "category": "upload",
            "outcome": "success",
            "object_type": "transcript_ingestion_job",
            "object_id": str(job.id),
            "transcript_id": str(transcript.id),
            "job_kind": job.job_kind.value,
            "source_audio_size_bytes": job.source_audio_size_bytes,
            "duration_seconds": round(resolved_source_audio_duration_seconds, 3) if resolved_source_audio_duration_seconds is not None else None,
        },
    )
    return transcript, job


def retry_audio_file_ingestion(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
) -> tuple[Transcript, TranscriptIngestionJob, bytes, TranscriptIngestionJob]:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    latest_job = latest_ingestion_job_for_transcript(db, transcript_id=transcript.id)
    if latest_job is None or latest_job.job_kind is not TranscriptIngestionJobKind.audio_file:
        raise AppError(409, "ingestion_retry_unavailable", "There is no failed uploaded audio available to retry for this session")
    if latest_job.status is not TranscriptIngestionJobStatus.failed:
        raise AppError(409, "ingestion_retry_unavailable", "The latest uploaded audio is not in a retryable failed state")
    if not _retry_source_available(latest_job):
        raise AppError(409, "ingestion_retry_unavailable", "The failed upload is no longer available to retry. Upload the audio file again.")

    source_audio_blob = _read_retry_source_audio(latest_job)
    source_audio_duration_seconds = latest_job.source_audio_duration_seconds
    transcript, retry_job = queue_audio_file_ingestion(
        db,
        owner,
        transcript_id=transcript.id,
        filename=latest_job.source_filename,
        source_audio_blob=source_audio_blob,
        source_audio_duration_seconds=source_audio_duration_seconds,
        exclude_job_ids=(latest_job.id,),
    )
    latest_job.source_audio_size_bytes = None
    latest_job.source_audio_duration_seconds = None
    db.add(latest_job)
    db.commit()
    db.refresh(latest_job)
    db.refresh(retry_job)
    return transcript, retry_job, source_audio_blob, latest_job


_TERMINAL_INGESTION_JOB_STATUSES = frozenset(
    {
        TranscriptIngestionJobStatus.completed,
        TranscriptIngestionJobStatus.applied,
        TranscriptIngestionJobStatus.failed,
    }
)


def _release_ingestion_transaction(db: Session) -> None:
    """Release worker-row locks before an idempotent ingestion return."""
    if db.in_transaction():
        db.rollback()


def _return_released_ingestion_job(db: Session, job: TranscriptIngestionJob) -> TranscriptIngestionJob:
    """Return stable job metadata without retaining a transaction or row lock."""
    db.refresh(job)
    db.expunge(job)
    _release_ingestion_transaction(db)
    return job


def _lock_ingestion_job_and_transcript(
    db: Session,
    *,
    job_id: UUID,
) -> tuple[TranscriptIngestionJob, Transcript] | None:
    """Lock root before child job, matching transcript-root deletion lock order."""
    job = db.scalar(
        select(TranscriptIngestionJob)
        .where(TranscriptIngestionJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    if job is None:
        _release_ingestion_transaction(db)
        return None

    transcript = db.scalar(
        select(Transcript)
        .where(Transcript.id == job.transcript_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if transcript is None:
        _release_ingestion_transaction(db)
        return None

    job = db.scalar(
        select(TranscriptIngestionJob)
        .where(TranscriptIngestionJob.id == job_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if job is None:
        _release_ingestion_transaction(db)
        return None
    return job, transcript


def _delete_expired_ingestion_transcript(db: Session, *, transcript: Transcript) -> None:
    """Durably queue retry-audio cleanup before root cascade, then release its lock."""
    cleanup_job_ids = queue_retry_source_cleanup_for_transcripts(db, transcript_ids=[transcript.id])
    db.delete(transcript)
    db.commit()
    process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_job_ids)


def _claim_queued_ingestion_job(
    db: Session,
    *,
    job_id: UUID,
) -> tuple[TranscriptIngestionJob, Transcript] | TranscriptIngestionJob | None:
    """Claim queued work once; terminal, in-flight, missing, and expired work is idempotent."""
    locked = _lock_ingestion_job_and_transcript(db, job_id=job_id)
    if locked is None:
        return None
    job, transcript = locked

    if transcript_is_expired(transcript):
        _delete_expired_ingestion_transcript(db, transcript=transcript)
        return None
    if job.status in _TERMINAL_INGESTION_JOB_STATUSES or job.status is TranscriptIngestionJobStatus.processing:
        return _return_released_ingestion_job(db, job)

    claimed_job_id = job.id
    # SQLite ignores SELECT ... FOR UPDATE. End its read transaction before
    # conditional UPDATE so competing workers observe the committed claim
    # instead of colliding on an older read snapshot.
    if db.get_bind().dialect.name == "sqlite":
        db.commit()

    claim = db.execute(
        update(TranscriptIngestionJob)
        .where(
            TranscriptIngestionJob.id == claimed_job_id,
            TranscriptIngestionJob.status == TranscriptIngestionJobStatus.queued,
        )
        .values(status=TranscriptIngestionJobStatus.processing, started_at=utcnow())
    )
    if claim.rowcount != 1:
        _release_ingestion_transaction(db)
        return None
    db.commit()
    locked = _lock_ingestion_job_and_transcript(db, job_id=claimed_job_id)
    if locked is None:
        return None
    job, transcript = locked
    if transcript_is_expired(transcript):
        _delete_expired_ingestion_transcript(db, transcript=transcript)
        return None
    if job.status is not TranscriptIngestionJobStatus.processing:
        return _return_released_ingestion_job(db, job)
    db.expunge(job)
    db.expunge(transcript)
    _release_ingestion_transaction(db)
    return job, transcript


def _mark_job_failed(db: Session, transcript: Transcript, job: TranscriptIngestionJob, *, code: str, message: str) -> None:
    job.status = TranscriptIngestionJobStatus.failed
    job.error_code = code
    job.error_message = message[:255]
    job.completed_at = utcnow()
    transcript.status = TranscriptStatus.failed
    db.add(job)
    db.add(transcript)
    db.commit()


def _apply_completed_live_chunks(db: Session, transcript: Transcript) -> None:
    expected_sequence = transcript.next_live_chunk_sequence_no_applied
    advanced_sequence = False
    while True:
        job = db.scalar(
            select(TranscriptIngestionJob).where(
                TranscriptIngestionJob.transcript_id == transcript.id,
                TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
                TranscriptIngestionJob.chunk_sequence_no == expected_sequence,
                TranscriptIngestionJob.status == TranscriptIngestionJobStatus.completed,
            )
        )
        if job is None:
            failed_job = db.scalar(
                select(TranscriptIngestionJob).where(
                    TranscriptIngestionJob.transcript_id == transcript.id,
                    TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
                    TranscriptIngestionJob.chunk_sequence_no == expected_sequence,
                    TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed,
                )
            )
            if failed_job is None:
                break

            later_completed_job = db.scalar(
                select(TranscriptIngestionJob.id).where(
                    TranscriptIngestionJob.transcript_id == transcript.id,
                    TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
                    TranscriptIngestionJob.chunk_sequence_no > expected_sequence,
                    TranscriptIngestionJob.status.in_(
                        [TranscriptIngestionJobStatus.completed, TranscriptIngestionJobStatus.applied]
                    ),
                ).limit(1)
            )
            if later_completed_job is None:
                break

            expected_sequence += 1
            advanced_sequence = True
            continue
        updated_draft_text = _append_chunk_text(
            transcript_draft_text(db, transcript=transcript),
            job_result_text(db, transcript=transcript, job=job) or "",
        )
        set_transcript_draft_text(db, transcript=transcript, plaintext=updated_draft_text)
        job.status = TranscriptIngestionJobStatus.applied
        job.applied_at = utcnow()
        expected_sequence += 1
        advanced_sequence = True
        db.add(job)
        db.add(transcript)

    transcript.next_live_chunk_sequence_no_applied = expected_sequence
    if not advanced_sequence:
        return

    db.flush()
    transcript.status = _resolved_transcript_status(db, transcript=transcript)
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    _preview_redact_current_draft_if_ready(db, transcript=transcript)


def _mark_stale_live_chunk_jobs_failed(db: Session, *, transcript: Transcript) -> bool:
    if transcript.ingestion_mode is not TranscriptIngestionMode.live_chunked:
        return False
    if LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS <= 0:
        return False

    cutoff = utcnow() - timedelta(seconds=LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS)
    stale_jobs = list(
        db.scalars(
            select(TranscriptIngestionJob)
            .where(
                TranscriptIngestionJob.transcript_id == transcript.id,
                TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
                TranscriptIngestionJob.status.in_(
                    [TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing]
                ),
                (
                    (TranscriptIngestionJob.started_at.is_not(None) & (TranscriptIngestionJob.started_at < cutoff))
                    | (TranscriptIngestionJob.started_at.is_(None) & (TranscriptIngestionJob.created_at < cutoff))
                ),
            )
            .order_by(TranscriptIngestionJob.chunk_sequence_no, TranscriptIngestionJob.created_at)
        )
    )
    if not stale_jobs:
        return False

    now = utcnow()
    for job in stale_jobs:
        job.status = TranscriptIngestionJobStatus.failed
        job.error_code = "ingestion_processing_stale"
        job.error_message = "Live audio chunk processing timed out before completion"
        job.completed_at = now
        db.add(job)
    db.commit()
    return True


def reconcile_transcript_status(
    db: Session,
    *,
    transcript: Transcript,
) -> Transcript:
    if transcript.ingestion_mode is TranscriptIngestionMode.live_chunked:
        _mark_stale_live_chunk_jobs_failed(db, transcript=transcript)
        _apply_completed_live_chunks(db, transcript)
        db.refresh(transcript)
    resolved_status = _resolved_transcript_status(db, transcript=transcript)
    if transcript.status is resolved_status:
        return transcript
    transcript.status = resolved_status
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def reconcile_live_chunk_progress(
    db: Session,
    *,
    transcript: Transcript,
) -> Transcript:
    return reconcile_transcript_status(db, transcript=transcript)


def finalize_live_capture(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
) -> Transcript:
    transcript = _get_owner_transcript_for_ingestion(db, owner, transcript_id=transcript_id)
    if transcript.ingestion_mode is not TranscriptIngestionMode.live_chunked:
        raise AppError(
            409,
            "business_rule_violation",
            "Only live capture transcripts can be finalized",
            {"field": "ingestion_mode"},
        )

    if transcript.status is TranscriptStatus.recording:
        transcript.status = TranscriptStatus.transcribing
        db.add(transcript)
        db.commit()
        db.refresh(transcript)

    transcript = reconcile_transcript_status(db, transcript=transcript)
    if transcript.status is TranscriptStatus.ready:
        _preview_redact_current_draft_if_ready(db, transcript=transcript)
        db.refresh(transcript)
    return transcript


def process_transcript_ingestion_job(
    db: Session,
    *,
    job_id: UUID,
    legacy_audio_bytes: bytes | None = None,
) -> TranscriptIngestionJob | None:
    claimed = _claim_queued_ingestion_job(db, job_id=job_id)
    if claimed is None:
        return None
    if isinstance(claimed, TranscriptIngestionJob):
        return claimed
    job, transcript = claimed

    try:
        audio_bytes = _read_queued_source_audio(db, job, legacy_audio_bytes=legacy_audio_bytes)
        normalized_audio = normalize_audio_to_wav_16k_mono(audio_bytes=audio_bytes, source_filename=job.source_filename)
        if job.job_kind is TranscriptIngestionJobKind.audio_file:
            enforce_whole_file_duration_limit(audio_bytes=normalized_audio.data)
        transcript_text = transcribe_with_stt_snapshot(
            db,
            team_id=transcript.team_id,
            stt_config_id=job.stt_config_id,
            provider_preset=job.stt_provider_preset,
            adapter_kind=job.stt_adapter_kind,
            base_url=job.stt_base_url,
            transcribe_path=job.stt_transcribe_path,
            file_field_name=job.stt_file_field_name,
            response_text_path=job.stt_response_text_path,
            extra_form_fields_json=job.stt_extra_form_fields_json,
            model_name=job.stt_model_name,
            model_field_name=job.stt_model_field_name,
            language=job.stt_language,
            language_field_name=job.stt_language_field_name,
            segments_path=job.stt_segments_path,
            segment_text_field=job.stt_segment_text_field,
            segment_start_field=job.stt_segment_start_field,
            segment_end_field=job.stt_segment_end_field,
            segment_speaker_field=job.stt_segment_speaker_field,
            audio_bytes=normalized_audio.data,
            filename=normalized_audio.filename,
            content_type=normalized_audio.content_type,
        )
        _release_ingestion_transaction(db)
        locked = _lock_ingestion_job_and_transcript(db, job_id=job_id)
        if locked is None:
            return None
        job, transcript = locked
        if transcript_is_expired(transcript):
            _delete_expired_ingestion_transcript(db, transcript=transcript)
            return None
        if job.status in _TERMINAL_INGESTION_JOB_STATUSES:
            return _return_released_ingestion_job(db, job)
        now = utcnow()
        job.result_text_encrypted = encrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcript_ingestion_jobs",
            field="result_text_encrypted",
            record_id=job.id,
            plaintext=transcript_text,
        )
        job.completed_at = now

        if job.job_kind is TranscriptIngestionJobKind.audio_file:
            job.status = TranscriptIngestionJobStatus.applied
            job.applied_at = now
            job.source_audio_blob = None
            updated_draft_text = _append_chunk_text(
                transcript_draft_text(db, transcript=transcript),
                transcript_text,
            )
            set_transcript_draft_text(db, transcript=transcript, plaintext=updated_draft_text)
            transcript_version = _create_transcript_version_from_text(
                db,
                transcript=transcript,
                owner_user_id=transcript.owner_user_id,
                plaintext=updated_draft_text,
            )
            transcript.status = TranscriptStatus.ready
            db.add(job)
            db.add(transcript)
            db.commit()
            _attempt_preview_redaction(db, transcript_version=transcript_version)
            if job.source_audio_vault_ref:
                try:
                    clear_ingestion_retry_source(
                        db,
                        job_id=job.id,
                        clear_storage=True,
                        clear_accounting=False,
                        delete_backing_secret=True,
                    )
                    db.refresh(transcript)
                except AppError:
                    pass
        else:
            job.status = TranscriptIngestionJobStatus.completed
            db.add(job)
            db.commit()
            if job.source_audio_vault_ref:
                try:
                    clear_ingestion_retry_source(
                        db,
                        job_id=job.id,
                        clear_storage=True,
                        clear_accounting=False,
                        delete_backing_secret=True,
                    )
                except AppError:
                    pass
            db.refresh(transcript)
            _apply_completed_live_chunks(db, transcript)
    except AppError as exc:
        _release_ingestion_transaction(db)
        locked = _lock_ingestion_job_and_transcript(db, job_id=job_id)
        if locked is None:
            return None
        job, transcript = locked
        if transcript_is_expired(transcript):
            _delete_expired_ingestion_transcript(db, transcript=transcript)
            return None
        if job.status in _TERMINAL_INGESTION_JOB_STATUSES:
            return _return_released_ingestion_job(db, job)
        _mark_job_failed(db, transcript, job, code=exc.code, message=exc.message)
        return _return_released_ingestion_job(db, job)
    except Exception as exc:  # pragma: no cover
        _release_ingestion_transaction(db)
        locked = _lock_ingestion_job_and_transcript(db, job_id=job_id)
        if locked is None:
            return None
        job, transcript = locked
        if transcript_is_expired(transcript):
            _delete_expired_ingestion_transcript(db, transcript=transcript)
            return None
        if job.status in _TERMINAL_INGESTION_JOB_STATUSES:
            return _return_released_ingestion_job(db, job)
        _mark_job_failed(db, transcript, job, code="ingestion_failed", message="Transcript ingestion job failed")
        _release_ingestion_transaction(db)
        raise AppError(502, "ingestion_failed", "Transcript ingestion job failed") from exc

    return _return_released_ingestion_job(db, job)


def attach_task_id_to_ingestion_job(db: Session, *, job_id: UUID, task_id: str | None) -> TranscriptIngestionJob:
    job = db.get(TranscriptIngestionJob, job_id)
    if job is None:
        raise AppError(404, "not_found", "Transcript ingestion job not found", {"resource": "transcript_ingestion_job", "job_id": str(job_id)})
    job.celery_task_id = task_id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_ingestion_job_enqueue_failed(db: Session, *, job_id: UUID, message: str) -> TranscriptIngestionJob:
    job = db.get(TranscriptIngestionJob, job_id)
    if job is None:
        raise AppError(404, "not_found", "Transcript ingestion job not found", {"resource": "transcript_ingestion_job", "job_id": str(job_id)})
    transcript = db.get(Transcript, job.transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(job.transcript_id)})
    _mark_job_failed(db, transcript, job, code="ingestion_enqueue_failed", message=message)
    db.refresh(job)
    return job
