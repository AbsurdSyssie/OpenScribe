from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import TranscriptIngestionMode, TranscriptStatus


class TranscriptCreate(BaseModel):
    owner_user_id: UUID
    team_id: UUID
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    ingestion_mode: TranscriptIngestionMode = TranscriptIngestionMode.live_chunked
    retention_days_applied: int | None = Field(default=None, ge=1)


class TranscriptStart(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    ingestion_mode: TranscriptIngestionMode = TranscriptIngestionMode.live_chunked
    retention_days_applied: int | None = Field(default=None, ge=1)


class TranscriptCommit(BaseModel):
    text_encrypted: str = Field(min_length=1)


class TranscriptListItem(BaseModel):
    id: UUID
    owner_user_id: UUID
    team_id: UUID
    title: str | None
    ingestion_mode: TranscriptIngestionMode
    status: TranscriptStatus
    retention_days_applied: int
    retention_expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class TranscriptDetail(TranscriptListItem):
    current_draft_text_encrypted: str | None = None
