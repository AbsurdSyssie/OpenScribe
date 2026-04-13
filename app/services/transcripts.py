import logging
import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    User,
    transcript_expiry,
    utcnow,
)
from app.schemas.transcripts import TranscriptCreate, TranscriptStart
from app.services.audio import (
    enforce_whole_file_duration_limit,
    inspect_audio_duration_seconds,
    normalize_audio_to_wav_16k_mono,
    probe_audio_duration_seconds,
)
from app.services.content_crypto import decrypt_json_for_owner, decrypt_text_for_owner, encrypt_json_for_owner, encrypt_text_for_owner
from app.services.stt import ensure_stt_config_credential_ready, resolve_selected_team_stt, transcribe_with_stt_snapshot
from app.services.vault import (
    delete_transcript_ingestion_source_audio,
    read_transcript_ingestion_source_audio,
    write_transcript_ingestion_source_audio,
)

LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS = float(os.getenv("LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", "3600"))
WHOLE_FILE_HOURLY_UPLOAD_BYTES = int(os.getenv("WHOLE_FILE_HOURLY_UPLOAD_BYTES", str(250 * 1024 * 1024)))
WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS = float(os.getenv("WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS", "7200"))
retry_audio_logger = logging.getLogger("openscribe.retry_audio")


def _retry_source_available(job: TranscriptIngestionJob) -> bool:
    return bool(job.source_audio_blob or job.source_audio_vault_ref)


def _read_retry_source_audio(job: TranscriptIngestionJob) -> bytes:
    if job.source_audio_blob:
        return job.source_audio_blob
    if job.source_audio_vault_ref:
        try:
            return read_transcript_ingestion_source_audio(secret_ref=job.source_audio_vault_ref)
        except AppError as exc:
            if exc.code == "vault_read_failed" and exc.message == "Stored retry audio is missing":
                raise AppError(
                    409,
                    "ingestion_retry_unavailable",
                    "The failed upload is no longer available to retry. Upload the audio file again.",
                ) from exc
            raise
    raise AppError(409, "ingestion_retry_unavailable", "The failed upload is no longer available to retry. Upload the audio file again.")


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
    if clear_storage and delete_backing_secret and source_audio_vault_ref:
        delete_transcript_ingestion_source_audio(secret_ref=source_audio_vault_ref)
    if clear_storage:
        job.source_audio_blob = None
        job.source_audio_vault_ref = None
    if clear_accounting:
        job.source_audio_size_bytes = None
        job.source_audio_duration_seconds = None
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def delete_retry_sources_for_transcripts(db: Session, *, transcript_ids: list[UUID]) -> None:
    if not transcript_ids:
        return
    vault_refs = list(
        db.scalars(
            select(TranscriptIngestionJob.source_audio_vault_ref).where(
                TranscriptIngestionJob.transcript_id.in_(transcript_ids),
                TranscriptIngestionJob.source_audio_vault_ref.is_not(None),
            )
        )
    )
    for secret_ref in {secret_ref for secret_ref in vault_refs if secret_ref}:
        try:
            delete_transcript_ingestion_source_audio(secret_ref=secret_ref)
        except AppError as exc:
            retry_audio_logger.warning(
                "retry_audio_delete_failed",
                extra={"secret_ref": secret_ref, "error_code": exc.code, "error_message": exc.message},
            )


def _create_transcript_row(
    db: Session,
    *,
    owner: User,
    title: str | None,
    current_draft_text_encrypted: str | None,
    structured_context_json: dict | None,
    ingestion_mode: TranscriptIngestionMode,
    retention_days_applied: int | None,
) -> Transcript:
    if owner.is_system_admin or owner.team_id is None:
        raise AppError(403, "forbidden", "System-admin accounts cannot own transcript content")
    if owner.team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(owner.team_id)})

    retention_days = retention_days_applied or owner.team.default_retention_days
    transcript_id = uuid4()
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
                plaintext=structured_context_json,
            )
            if structured_context_json is not None
            else None
        ),
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
        retention_days_applied=payload.retention_days_applied,
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
        retention_days_applied=payload.retention_days_applied,
    )


def _append_chunk_text(existing_text: str | None, chunk_text: str) -> str:
    normalized_chunk = chunk_text.strip()
    if not existing_text:
        return normalized_chunk
    if not normalized_chunk:
        return existing_text
    return f"{existing_text.rstrip()}\n{normalized_chunk}"


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
        .where(Transcript.owner_user_id == owner.id)
        .order_by(Transcript.created_at.desc())
        .limit(1)
    )


def _transcript_has_meaningful_content(db: Session, transcript: Transcript) -> bool:
    current_draft = transcript_draft_text(db, transcript=transcript)
    if current_draft and current_draft.strip():
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
    if latest.status is TranscriptStatus.transcribing:
        return False, "Wait for the current session transcription to finish before creating a new one"
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
    return transcript


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
        set_transcript_structured_context(db, transcript=transcript, plaintext=structured_context_json)
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
    current_max = db.scalar(select(func.max(TranscriptVersion.version_no)).where(TranscriptVersion.transcript_id == transcript.id))
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=(current_max or 0) + 1,
        text_encrypted=encrypt_text_for_owner(
            db,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext=plaintext,
        ),
    )
    set_transcript_draft_text(db, transcript=transcript, plaintext=plaintext)
    transcript.status = TranscriptStatus.ready
    db.add(version)
    db.add(transcript)
    db.commit()
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
    delete_retry_sources_for_transcripts(db, transcript_ids=[transcript.id for transcript in transcripts])
    deleted_count = len(transcripts)
    for transcript in transcripts:
        db.delete(transcript)
    db.commit()
    return deleted_count


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
    job = TranscriptIngestionJob(
        id=uuid4(),
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=chunk_sequence_no,
        source_filename=filename,
        source_audio_size_bytes=len(source_audio_bytes),
        declared_duration_seconds=measured_duration_seconds,
        stt_config_id=config.id,
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=resolved_model_name,
        stt_language=resolved_language,
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
        stt_extra_form_fields_json=dict(config.extra_form_fields_json or {}),
        status=TranscriptIngestionJobStatus.queued,
    )
    transcript.status = TranscriptStatus.transcribing
    db.add(job)
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    db.refresh(job)
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
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=resolved_model_name,
        stt_language=resolved_language,
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
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
            try:
                delete_transcript_ingestion_source_audio(secret_ref=persisted_source_audio_vault_ref)
            except AppError:
                pass
        raise
    db.refresh(transcript)
    db.refresh(job)
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
        source_audio_vault_ref=latest_job.source_audio_vault_ref,
        exclude_job_ids=(latest_job.id,),
    )
    latest_job.source_audio_size_bytes = None
    latest_job.source_audio_duration_seconds = None
    db.add(latest_job)
    db.commit()
    db.refresh(latest_job)
    db.refresh(retry_job)
    return transcript, retry_job, source_audio_blob, latest_job


def _mark_job_processing(db: Session, job: TranscriptIngestionJob) -> None:
    job.status = TranscriptIngestionJobStatus.processing
    job.started_at = utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)


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

    transcript.status = _resolved_transcript_status(db, transcript=transcript)
    db.add(transcript)
    db.commit()


def reconcile_transcript_status(
    db: Session,
    *,
    transcript: Transcript,
) -> Transcript:
    if transcript.ingestion_mode is TranscriptIngestionMode.live_chunked:
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


def process_transcript_ingestion_job(
    db: Session,
    *,
    job_id: UUID,
    audio_bytes: bytes,
) -> TranscriptIngestionJob:
    job = db.get(TranscriptIngestionJob, job_id)
    if job is None:
        raise AppError(404, "not_found", "Transcript ingestion job not found", {"resource": "transcript_ingestion_job", "job_id": str(job_id)})
    transcript = db.get(Transcript, job.transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(job.transcript_id)})
    if job.status in {TranscriptIngestionJobStatus.completed, TranscriptIngestionJobStatus.applied}:
        return job

    _mark_job_processing(db, job)

    try:
        normalized_audio = normalize_audio_to_wav_16k_mono(audio_bytes=audio_bytes, source_filename=job.source_filename)
        if job.job_kind is TranscriptIngestionJobKind.audio_file:
            enforce_whole_file_duration_limit(audio_bytes=normalized_audio.data)
        transcript_text = transcribe_with_stt_snapshot(
            db,
            team_id=transcript.team_id,
            stt_config_id=job.stt_config_id,
            adapter_kind=job.stt_adapter_kind,
            base_url=job.stt_base_url,
            transcribe_path=job.stt_transcribe_path,
            file_field_name=job.stt_file_field_name,
            response_text_path=job.stt_response_text_path,
            extra_form_fields_json=job.stt_extra_form_fields_json,
            model_name=job.stt_model_name,
            language=job.stt_language,
            audio_bytes=normalized_audio.data,
            filename=normalized_audio.filename,
            content_type=normalized_audio.content_type,
        )
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
            transcript.status = TranscriptStatus.ready
            db.add(job)
            db.add(transcript)
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
                    db.refresh(transcript)
                except AppError:
                    pass
        else:
            job.status = TranscriptIngestionJobStatus.completed
            db.add(job)
            db.commit()
            db.refresh(transcript)
            _apply_completed_live_chunks(db, transcript)
    except AppError as exc:
        _mark_job_failed(db, transcript, job, code=exc.code, message=exc.message)
        db.refresh(job)
        return job
    except Exception as exc:  # pragma: no cover
        _mark_job_failed(db, transcript, job, code="ingestion_failed", message="Transcript ingestion job failed")
        raise AppError(502, "ingestion_failed", "Transcript ingestion job failed") from exc

    db.refresh(job)
    return job


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
