"""Shared query condition for work already accepted as a partial capture."""

from sqlalchemy import exists, not_, select

from app.models import TelehealthCapturePart, TelehealthCaptureSession, TranscriptIngestionJob


def job_is_not_accepted_partial(job_model=TranscriptIngestionJob):
    """Keep accepted missing jobs out of generic transcript-wide blockers."""
    return not_(exists(select(TelehealthCapturePart.id).join(
        TelehealthCaptureSession,
        TelehealthCaptureSession.id == TelehealthCapturePart.session_id,
    ).where(
        TelehealthCapturePart.ingestion_job_id == job_model.id,
        TelehealthCaptureSession.status == "partial",
    )))
