from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import TranscriptIngestionJobKind, TranscriptIngestionJobStatus, TranscriptIngestionMode, TranscriptStatus, TranscriptWorkingNoteMode


EMIS_WORKING_NOTE_SECTION_KEYS = (
    "problem",
    "history",
    "family_history",
    "social_history",
    "examination",
    "comment",
    "tasks",
    "investigations",
)


class TranscriptCreate(BaseModel):
    owner_user_id: UUID
    team_id: UUID
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    structured_context_json: dict | None = None
    ingestion_mode: TranscriptIngestionMode = TranscriptIngestionMode.whole_file


class TranscriptStart(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    current_draft_text_encrypted: str | None = None
    structured_context_json: dict | None = None
    ingestion_mode: TranscriptIngestionMode = TranscriptIngestionMode.whole_file


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
    working_note_mode: TranscriptWorkingNoteMode | None = None
    has_working_note: bool = False
    retention_days_applied: int
    retention_expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class TranscriptDetail(TranscriptListItem):
    current_draft_text: str | None = None
    structured_context_json: dict | None = None
    next_live_chunk_sequence_no_upload: int | None = None
    latest_ingestion_job_status: TranscriptIngestionJobStatus | None = None
    latest_ingestion_error_code: str | None = None
    latest_ingestion_error_message: str | None = None
    latest_ingestion_retry_available: bool = False


class StructuredWorkingNotePayload(BaseModel):
    profile: str = "emis"
    sections: dict[str, list[str] | str] = Field(default_factory=dict)

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        if value != "emis":
            raise ValueError("Structured working note profile must be emis")
        return value


class WorkingNoteUpdate(BaseModel):
    mode: TranscriptWorkingNoteMode
    freeform_text: str | None = Field(default=None, max_length=20000)
    structured_note: StructuredWorkingNotePayload | None = None

    @model_validator(mode="after")
    def validate_matching_content(self) -> "WorkingNoteUpdate":
        if self.mode is TranscriptWorkingNoteMode.freeform:
            if self.structured_note is not None:
                raise ValueError("Freeform working note cannot include structured content")
            if self.freeform_text is None or not self.freeform_text.strip():
                raise ValueError("Freeform working note text is required")
        if self.mode is TranscriptWorkingNoteMode.structured:
            if self.freeform_text is not None:
                raise ValueError("Structured working note cannot include freeform text")
            if self.structured_note is None:
                raise ValueError("Structured working note content is required")
        return self


class WorkingNoteDetail(BaseModel):
    transcript_id: UUID
    mode: TranscriptWorkingNoteMode | None = None
    freeform_text: str = ""
    structured_note: dict | None = None
    updated_at: datetime | None = None


class TranscriptPiiEntitySummary(BaseModel):
    id: UUID | None = None
    entity_type: str
    placeholder: str
    occurrence_count: int
    source: str = "detected"
    has_value: bool = True


class TranscriptPiiEntityDetail(TranscriptPiiEntitySummary):
    value: str


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
