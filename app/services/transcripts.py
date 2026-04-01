from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
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
from app.services.audio import enforce_whole_file_duration_limit, normalize_audio_to_wav_16k_mono
from app.services.stt import ensure_stt_config_credential_ready, resolve_selected_team_stt, transcribe_with_stt_snapshot


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
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title=title,
        current_draft_text_encrypted=current_draft_text_encrypted,
        structured_context_json=structured_context_json,
        ingestion_mode=ingestion_mode,
        status=TranscriptStatus.recording,
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


def latest_ingestion_job_for_transcript(db: Session, *, transcript_id: UUID) -> TranscriptIngestionJob | None:
    return db.scalar(
        select(TranscriptIngestionJob)
        .where(TranscriptIngestionJob.transcript_id == transcript_id)
        .order_by(TranscriptIngestionJob.created_at.desc(), TranscriptIngestionJob.id.desc())
        .limit(1)
    )


def _latest_owner_transcript(db: Session, owner: User) -> Transcript | None:
    return db.scalar(
        select(Transcript)
        .where(Transcript.owner_user_id == owner.id)
        .order_by(Transcript.created_at.desc())
        .limit(1)
    )


def _transcript_has_meaningful_content(db: Session, transcript: Transcript) -> bool:
    if transcript.current_draft_text_encrypted and transcript.current_draft_text_encrypted.strip():
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
    if transcript.status is TranscriptStatus.transcribing:
        return transcript, False, "Wait for the current session transcription to finish before switching input mode"
    if _transcript_has_meaningful_content(db, transcript):
        return transcript, False, "Switch input mode before recording, uploading, or adding transcript content"
    return transcript, True, None


def _get_owner_transcript_for_ingestion(db: Session, owner: User, *, transcript_id: UUID) -> Transcript:
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != owner.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    return transcript


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
        transcript.structured_context_json = structured_context_json
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
    if declared_duration_seconds is not None and declared_duration_seconds > 30:
        raise AppError(
            422,
            "business_rule_violation",
            "Declared chunk duration exceeds the current maximum",
            {"field": "declared_duration_seconds", "max_seconds": 30},
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
    job = TranscriptIngestionJob(
        id=uuid4(),
        transcript_id=transcript.id,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=chunk_sequence_no,
        source_filename=filename,
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

    job = TranscriptIngestionJob(
        id=uuid4(),
        transcript_id=transcript.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        chunk_sequence_no=None,
        source_filename=filename,
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
            break
        transcript.current_draft_text_encrypted = _append_chunk_text(
            transcript.current_draft_text_encrypted,
            job.result_text_encrypted or "",
        )
        job.status = TranscriptIngestionJobStatus.applied
        job.applied_at = utcnow()
        expected_sequence += 1
        db.add(job)
        db.add(transcript)

    transcript.next_live_chunk_sequence_no_applied = expected_sequence
    transcript.status = TranscriptStatus.transcribing
    db.add(transcript)
    db.commit()


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
        job.result_text_encrypted = transcript_text
        job.completed_at = now

        if job.job_kind is TranscriptIngestionJobKind.audio_file:
            job.status = TranscriptIngestionJobStatus.applied
            job.applied_at = now
            transcript.current_draft_text_encrypted = _append_chunk_text(
                transcript.current_draft_text_encrypted,
                transcript_text,
            )
            transcript.status = TranscriptStatus.ready
            db.add(job)
            db.add(transcript)
            db.commit()
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
