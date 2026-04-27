from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import TranscriptIngestionJobKind, TranscriptIngestionJobStatus, TranscriptIngestionMode, TranscriptStatus


class TranscriptCreate(BaseModel):
    owner_user_id: UUID
    team_id: UUID
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    structured_context_json: dict | None = None
    ingestion_mode: TranscriptIngestionMode = TranscriptIngestionMode.whole_file
    retention_days_applied: int | None = Field(default=None, ge=1)


class TranscriptStart(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    structured_context_json: dict | None = None
    ingestion_mode: TranscriptIngestionMode = TranscriptIngestionMode.whole_file
    retention_days_applied: int | None = Field(default=None, ge=1)


class TranscriptCommit(BaseModel):
    text_encrypted: str = Field(min_length=1)


class TranscriptUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    ingestion_mode: TranscriptIngestionMode | None = None
    structured_context_json: dict | None = None


class TranscriptListItem(BaseModel):
    id: UUID
    owner_user_id: UUID
    team_id: UUID
    title: str | None
    ingestion_mode: TranscriptIngestionMode
    status: TranscriptStatus
    has_transcript_content: bool = False
    retention_days_applied: int
    retention_expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class TranscriptDetail(TranscriptListItem):
    current_draft_text_encrypted: str | None = None
    structured_context_json: dict | None = None
    next_live_chunk_sequence_no_upload: int | None = None
    latest_ingestion_job_status: TranscriptIngestionJobStatus | None = None
    latest_ingestion_error_code: str | None = None
    latest_ingestion_error_message: str | None = None
    latest_ingestion_retry_available: bool = False


class TranscriptPiiEntityDetail(BaseModel):
    id: UUID | None = None
    entity_type: str
    value: str
    placeholder: str
    occurrence_count: int
    source: str = "detected"


class TranscriptManualPiiEntityCreate(BaseModel):
    entity_type: str = Field(default="PII", min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=4096)
    occurrence_count: int = Field(default=1, ge=1, le=999)


class TranscriptIngestionJobDetail(BaseModel):
    id: UUID
    transcript_id: UUID
    job_kind: TranscriptIngestionJobKind
    chunk_sequence_no: int | None
    source_filename: str
    status: TranscriptIngestionJobStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TranscriptIngestionAccepted(BaseModel):
    transcript: TranscriptDetail
    job: TranscriptIngestionJobDetail
