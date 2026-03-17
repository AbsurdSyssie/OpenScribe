from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Transcript, TranscriptIngestionMode, TranscriptStatus, User, transcript_expiry
from app.schemas.transcripts import TranscriptCreate, TranscriptStart
from app.services.audio import normalize_audio_to_wav_16k_mono
from app.services.stt import transcribe_with_team_stt


def _create_transcript_row(
    db: Session,
    *,
    owner: User,
    title: str | None,
    current_draft_text_encrypted: str | None,
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
        ingestion_mode=ingestion_mode,
        status=TranscriptStatus.recording,
        retention_days_applied=retention_days,
        retention_expires_at=transcript_expiry(retention_days),
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def start_transcript(db: Session, owner: User, payload: TranscriptStart) -> Transcript:
    return _create_transcript_row(
        db,
        owner=owner,
        title=payload.title,
        current_draft_text_encrypted=payload.current_draft_text_encrypted,
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
    return _create_transcript_row(
        db,
        owner=owner,
        title=payload.title,
        current_draft_text_encrypted=payload.current_draft_text_encrypted,
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


def ingest_audio_chunk(
    db: Session,
    owner: User,
    *,
    transcript_id,
    audio_bytes: bytes,
    filename: str,
    declared_duration_seconds: float | None,
) -> Transcript:
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != owner.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
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

    normalized_audio = normalize_audio_to_wav_16k_mono(audio_bytes=audio_bytes, source_filename=filename)
    chunk_text = transcribe_with_team_stt(
        db,
        team_id=transcript.team_id,
        audio_bytes=normalized_audio.data,
        filename=normalized_audio.filename,
        content_type=normalized_audio.content_type,
    )
    transcript.current_draft_text_encrypted = _append_chunk_text(transcript.current_draft_text_encrypted, chunk_text)
    transcript.status = TranscriptStatus.transcribing
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def ingest_audio_file(
    db: Session,
    owner: User,
    *,
    transcript_id,
    audio_bytes: bytes,
    filename: str,
) -> Transcript:
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != owner.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    if transcript.ingestion_mode not in {TranscriptIngestionMode.file_upload, TranscriptIngestionMode.microphone_batch}:
        raise AppError(
            409,
            "business_rule_violation",
            "Transcript ingestion mode does not accept file ingestion",
            {"ingestion_mode": transcript.ingestion_mode.value},
        )

    normalized_audio = normalize_audio_to_wav_16k_mono(audio_bytes=audio_bytes, source_filename=filename)
    transcript_text = transcribe_with_team_stt(
        db,
        team_id=transcript.team_id,
        audio_bytes=normalized_audio.data,
        filename=normalized_audio.filename,
        content_type=normalized_audio.content_type,
    )
    transcript.current_draft_text_encrypted = transcript_text
    transcript.status = TranscriptStatus.ready
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript
