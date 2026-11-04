"""Bounded Vault staging-audio expiry for telehealth capture parts.

Scheduled by OpenScribe's existing ingestion-source expiry worker. The queued
Vault cleanup intent is committed before clearing a live source reference, and
part expiry is explicit rather than silently reporting a complete consultation.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    User,
    utcnow,
)


def expire_telehealth_staged_audio(
    db: Session,
    *,
    batch_size: int = 100,
    now: datetime | None = None,
) -> int:
    """Expire staged/failed untranscribed parts under owner/root/session locks.

    This does NOT expire audio already handed over to an ingestion job. Once a
    job owns the Vault ref, existing ingestion-job retry/expiry rules apply.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    comparison_now = now or utcnow()
    candidates = list(db.scalars(
        select(TelehealthCapturePart.id).where(
            TelehealthCapturePart.source_audio_expires_at.is_not(None),
            TelehealthCapturePart.source_audio_expires_at <= comparison_now,
            TelehealthCapturePart.source_audio_vault_ref.is_not(None),
            TelehealthCapturePart.status.in_(("staged", "failed")),
        ).order_by(
            TelehealthCapturePart.source_audio_expires_at.asc(),
            TelehealthCapturePart.id.asc(),
        ).limit(batch_size)
    ))
    # Release the snapshot transaction before acquiring the owner/root locks.
    db.rollback()
    expired = 0
    for part_id in candidates:
        part_identity = db.get(TelehealthCapturePart, part_id)
        if part_identity is None:
            db.rollback()
            continue
        owner_id, transcript_id, session_id = (
            part_identity.owner_user_id, part_identity.transcript_id, part_identity.session_id,
        )
        # Match established lock order: owner -> transcript -> capture -> part.
        owner = db.scalar(select(User).where(User.id == owner_id).with_for_update())
        if owner is None:
            db.rollback()
            continue
        transcript = db.scalar(select(Transcript).where(
            Transcript.id == transcript_id,
            Transcript.owner_user_id == owner_id,
        ).with_for_update())
        if transcript is None:
            db.rollback()
            continue
        session = db.scalar(select(TelehealthCaptureSession).where(
            TelehealthCaptureSession.id == session_id,
            TelehealthCaptureSession.owner_user_id == owner_id,
            TelehealthCaptureSession.transcript_id == transcript_id,
        ).with_for_update())
        part = db.scalar(select(TelehealthCapturePart).where(
            TelehealthCapturePart.id == part_id,
            TelehealthCapturePart.owner_user_id == owner_id,
            TelehealthCapturePart.transcript_id == transcript_id,
            TelehealthCapturePart.session_id == session_id,
        ).with_for_update())
        if (
            session is None
            or part is None
            or part.status not in ("staged", "failed")
            or part.source_audio_expires_at is None
            or part.source_audio_expires_at > comparison_now
            or not part.source_audio_vault_ref
        ):
            db.rollback()
            continue

        # If processing has claimed a part, do not race against a provider
        # attempt or expire its shared Vault bytes. The handoff must atomically
        # clear part.source_audio_vault_ref once the job owns that reference.
        if part.ingestion_job_id is not None:
            db.rollback()
            continue

        from app.services.transcripts import (
            process_transcript_audio_cleanup_jobs,
            queue_transcript_audio_cleanup,
        )

        cleanup_ids = queue_transcript_audio_cleanup(
            db, secret_refs=[part.source_audio_vault_ref],
        )
        part.source_audio_vault_ref = None
        part.source_audio_expired_at = comparison_now
        part.status = "expired"
        part.updated_at = comparison_now
        if session.status not in ("completed", "failed"):
            session.status = "failed"
            session.failure_channel = part.channel
            session.failure_code = "source_audio_expired"
            session.recording_stopped_at = session.recording_stopped_at or comparison_now
            session.updated_at = comparison_now
            db.add(session)
        db.add(part)
        db.commit()
        # The durable cleanup intent survives transient Vault failures.
        process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_ids)
        expired += 1
    return expired
