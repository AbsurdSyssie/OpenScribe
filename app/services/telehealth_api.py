"""Read-only, owner-filtered Telehealth capture projection for the workspace."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import TelehealthCapturePart, TelehealthCaptureSession, User
from app.schemas.telehealth import (
    TelehealthCaptureDetail, TelehealthCapturePartDetail,
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
    return TelehealthCaptureDetail(
        capture_session_id=capture.id,
        transcript_id=transcript.id,
        status=capture.status,
        timeline_sample_rate=capture.timeline_sample_rate,
        failure_channel=capture.failure_channel,
        failure_code=capture.failure_code,
        incomplete_acknowledged_at=capture.incomplete_acknowledged_at,
        parts=[TelehealthCapturePartDetail.model_validate(p) for p in parts],
        recording_started_at=capture.recording_started_at,
        recording_stopped_at=capture.recording_stopped_at,
    )
