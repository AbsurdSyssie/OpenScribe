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
            "active", "stopping", "ready", "interrupted", "processing", "failed",
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
