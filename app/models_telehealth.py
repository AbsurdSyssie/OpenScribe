"""Owner-scoped, content-free telehealth capture metadata.

These models deliberately never contain audio, plaintext transcript text, or
clinical identities. Raw source parts live only in existing Vault-backed audio
storage, with owner/deletion/expiry cleanup through transcript root services.

Imported by app.models to register these tables in Base.metadata.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelehealthCaptureSession(Base):
    """One dual-source recording run; a consultation may have multiple runs."""

    __tablename__ = "telehealth_capture_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'stopping', 'interrupted', 'ready', 'processing', 'partial', 'completed', 'failed', 'discarded')",
            name="ck_telehealth_capture_sessions_status",
        ),
        CheckConstraint(
            "timeline_sample_rate >= 8000 AND timeline_sample_rate <= 192000",
            name="ck_telehealth_capture_sessions_sample_rate",
        ),
        CheckConstraint(
            "failure_channel IS NULL OR failure_channel IN ('clinician', 'patient')",
            name="ck_telehealth_capture_sessions_failure_channel",
        ),
        CheckConstraint(
            "failure_code IS NULL OR char_length(failure_code) <= 128",
            name="ck_telehealth_capture_sessions_failure_code_length",
        ),
        CheckConstraint(
            "requested_audio_scope IN ('unknown', 'call', 'system')",
            name="ck_telehealth_capture_sessions_audio_scope",
        ),
        Index(
            "uq_telehealth_capture_sessions_open_transcript",
            "transcript_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'stopping')"),
            sqlite_where=text("status IN ('active', 'stopping')"),
        ),
        Index(
            "ix_telehealth_capture_sessions_owner_state",
            "owner_user_id", "status", "updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False,
    )
    stt_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    timeline_sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    # User's requested browser capture scope, not a verified process/audio source.
    requested_audio_scope: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown", nullable=False,
    )
    failure_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    incomplete_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recording_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    recording_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )
    parts: Mapped[list["TelehealthCapturePart"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
    )


class TelehealthCapturePart(Base):
    """One source part, not yet assumed to be transcribed or applied."""

    __tablename__ = "telehealth_capture_parts"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "channel", "sequence_no",
            name="uq_telehealth_capture_parts_channel_sequence",
        ),
        UniqueConstraint(
            "session_id", "idempotency_key",
            name="uq_telehealth_capture_parts_idempotency",
        ),
        UniqueConstraint(
            "ingestion_job_id",
            name="uq_telehealth_capture_parts_ingestion_job",
        ),
        CheckConstraint(
            "channel IN ('clinician', 'patient')",
            name="ck_telehealth_capture_parts_channel",
        ),
        CheckConstraint(
            "status IN ('staged', 'processing', 'transcribed', 'failed', 'expired')",
            name="ck_telehealth_capture_parts_status",
        ),
        CheckConstraint(
            "sequence_no >= 0 AND sequence_no <= 100000",
            name="ck_telehealth_capture_parts_sequence",
        ),
        CheckConstraint(
            "timeline_start_frame >= 0 AND timeline_end_frame > timeline_start_frame",
            name="ck_telehealth_capture_parts_timeline_range",
        ),
        CheckConstraint(
            "file_samples > 0 AND source_audio_size_bytes > 0",
            name="ck_telehealth_capture_parts_size_positive",
        ),
        CheckConstraint(
            "source_audio_duration_seconds > 0",
            name="ck_telehealth_capture_parts_audio_duration_positive",
        ),
        CheckConstraint(
            "(source_audio_vault_ref IS NULL) OR source_audio_expires_at IS NOT NULL",
            name="ck_telehealth_capture_parts_source_expiry",
        ),
        Index(
            "ix_telehealth_capture_parts_transcript_owner",
            "transcript_id", "owner_user_id",
        ),
        Index(
            "ix_telehealth_capture_parts_source_expiry",
            "source_audio_expires_at",
            postgresql_where=text("source_audio_vault_ref IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("telehealth_capture_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="staged", nullable=False)
    timeline_start_frame: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timeline_end_frame: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timeline_sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    file_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    # Metadata only; an array of original/file sample coordinates (never audio).
    timing_spans_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    source_audio_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Owner-keyed digest of source bytes, for exact idempotency replay checks.
    source_audio_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_audio_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    source_audio_vault_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_audio_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_audio_expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transcript_ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    session: Mapped[TelehealthCaptureSession] = relationship(back_populates="parts")
