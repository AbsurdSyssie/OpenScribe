"""Read-only, owner-filtered Telehealth capture projection for the workspace."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    TelehealthCapturePart, TelehealthCaptureSession, TranscriptIngestionJob,
    TranscriptIngestionJobStatus, User, utcnow,
)
from app.schemas.telehealth import (
    TelehealthCaptureDetail, TelehealthCapturePartDetail,
    TelehealthPartialCapturesPage,
)
from app.services.transcripts import get_active_owner_transcript


def telehealth_capture_detail(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
) -> TelehealthCaptureDetail:
    """Never return Vault secret refs, provider settings, raw audio or source text."""
    transcript = get_active_owner_transcript(db, owner, transcript_id=transcript_id)
    capture = db.scalar(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.id == capture_session_id,
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
        TelehealthCaptureSession.team_id == transcript.team_id,
    ))
    if capture is None:
        raise AppError(404, "not_found", "Telehealth capture was not found.")
    parts = list(db.scalars(select(TelehealthCapturePart).where(
        TelehealthCapturePart.session_id == capture.id,
        TelehealthCapturePart.transcript_id == transcript.id,
        TelehealthCapturePart.owner_user_id == owner.id,
        TelehealthCapturePart.team_id == transcript.team_id,
    ).order_by(TelehealthCapturePart.channel.asc(), TelehealthCapturePart.sequence_no.asc())))
    jobs = {
        job.id: job for job in db.scalars(select(TranscriptIngestionJob).where(
            TranscriptIngestionJob.id.in_([part.ingestion_job_id for part in parts if part.ingestion_job_id]),
            TranscriptIngestionJob.transcript_id == transcript.id,
            TranscriptIngestionJob.owner_user_id == owner.id,
        ))
    }
    now = utcnow()
    submitted = any(part.ingestion_job_id is not None for part in parts)
    return TelehealthCaptureDetail(
        capture_session_id=capture.id,
        transcript_id=transcript.id,
        status=capture.status,
        timeline_sample_rate=capture.timeline_sample_rate,
        requested_audio_scope=capture.requested_audio_scope,
        failure_channel=capture.failure_channel,
        failure_code=capture.failure_code,
        incomplete_acknowledged_at=capture.incomplete_acknowledged_at,
        parts=[TelehealthCapturePartDetail(
            id=part.id,
            channel=part.channel,
            sequence_no=part.sequence_no,
            status=part.status,
            timeline_start_frame=part.timeline_start_frame,
            timeline_end_frame=part.timeline_end_frame,
            source_audio_duration_seconds=part.source_audio_duration_seconds,
            ingestion_job_id=part.ingestion_job_id,
            retry_available=bool(
                (job := jobs.get(part.ingestion_job_id)) is not None
                and job.status is TranscriptIngestionJobStatus.failed
                and job.source_audio_vault_ref
                and job.source_audio_expires_at is not None
                and job.source_audio_expires_at > now
            ),
            included_in_transcript=bool(
                (job := jobs.get(part.ingestion_job_id)) is not None
                and job.status is TranscriptIngestionJobStatus.applied
                and job.applied_at is not None
            ),
        ) for part in parts],
        can_use_available_transcript=capture.status == "failed" and submitted,
        recording_started_at=capture.recording_started_at,
        recording_stopped_at=capture.recording_stopped_at,
    )



def unresolved_telehealth_capture_detail(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
) -> TelehealthCaptureDetail | None:
    """Recover the identity/status of a capture after browser reload.

    This returns only bounded metadata and accepted part identifiers. It
    cannot recover unuploaded PCM from a crashed tab and must never imply
    that an interrupted consultation is complete.
    """
    transcript = get_active_owner_transcript(db, owner, transcript_id=transcript_id)
    capture_id = db.scalar(select(TelehealthCaptureSession.id).where(
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
        TelehealthCaptureSession.team_id == transcript.team_id,
        TelehealthCaptureSession.status.in_((
            "active", "stopping", "ready", "interrupted", "processing", "partial", "failed",
        )),
    ).order_by(
        TelehealthCaptureSession.created_at.desc(),
        TelehealthCaptureSession.id.desc(),
    ).limit(1))
    if capture_id is None:
        return None
    return telehealth_capture_detail(
        db, owner, transcript_id=transcript.id,
        capture_session_id=capture_id,
    )


def partial_telehealth_capture_details(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    offset: int = 0,
    limit: int = 20,
) -> TelehealthPartialCapturesPage:
    """Return a stable paginated owner-only projection of accepted partial captures."""
    if offset < 0 or not 1 <= limit <= 100:
        raise AppError(422, "validation_error", "Invalid partial-capture page.")
    transcript = get_active_owner_transcript(db, owner, transcript_id=transcript_id)
    rows = list(db.scalars(select(TelehealthCaptureSession.id).where(
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
        TelehealthCaptureSession.team_id == transcript.team_id,
        TelehealthCaptureSession.status == "partial",
    ).order_by(
        TelehealthCaptureSession.created_at.asc(),
        TelehealthCaptureSession.id.asc(),
    ).offset(offset).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return TelehealthPartialCapturesPage(
        items=[telehealth_capture_detail(
            db, owner, transcript_id=transcript.id, capture_session_id=capture_id,
        ) for capture_id in rows],
        next_offset=offset + limit if has_more else None,
    )
