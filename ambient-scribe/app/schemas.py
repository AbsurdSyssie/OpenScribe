from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from .models import TeamRole, TranscriptStatus


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    default_retention_days: int = Field(default=30, ge=1)


class TeamOut(BaseModel):
    id: UUID
    name: str
    default_retention_days: int

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    password_hash: str = Field(min_length=1)
    team_id: UUID
    team_role: TeamRole = TeamRole.user


class UserOut(BaseModel):
    id: UUID
    email: str
    team_id: UUID | None
    team_role: TeamRole | None
    is_system_admin: bool

    model_config = {"from_attributes": True}


class TranscriptCreate(BaseModel):
    owner_user_id: UUID
    team_id: UUID
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    retention_days_applied: int | None = Field(default=None, ge=1)


class TranscriptCommit(BaseModel):
    text_encrypted: str = Field(min_length=1)


class TranscriptOut(BaseModel):
    id: UUID
    owner_user_id: UUID
    team_id: UUID
    title: str | None
    status: TranscriptStatus
    retention_expires_at: datetime

    model_config = {"from_attributes": True}
