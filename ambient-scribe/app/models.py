import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class TeamRole(str, enum.Enum):
    leader = "leader"
    user = "user"


class UserStatus(str, enum.Enum):
    active = "active"
    locked = "locked"
    disabled = "disabled"


class TranscriptStatus(str, enum.Enum):
    recording = "recording"
    transcribing = "transcribing"
    ready = "ready"
    failed = "failed"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    default_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="team")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="team")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    team_role: Mapped[TeamRole | None] = mapped_column(Enum(TeamRole), nullable=True)
    is_system_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.active, nullable=False)
    mfa_required: Mapped[bool] = mapped_column(default=True, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    team: Mapped[Team | None] = relationship(back_populates="users")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="owner")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_draft_text_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TranscriptStatus] = mapped_column(
        Enum(TranscriptStatus), default=TranscriptStatus.recording, nullable=False
    )
    retention_days_applied: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    owner: Mapped[User] = relationship(back_populates="transcripts")
    team: Mapped[Team] = relationship(back_populates="transcripts")
    versions: Mapped[list["TranscriptVersion"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )


class TranscriptVersion(Base):
    __tablename__ = "transcript_versions"
    __table_args__ = (UniqueConstraint("transcript_id", "version_no", name="uq_transcript_version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transcripts.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    transcript: Mapped[Transcript] = relationship(back_populates="versions")


def transcript_expiry(days: int) -> datetime:
    return utcnow() + timedelta(days=days)
