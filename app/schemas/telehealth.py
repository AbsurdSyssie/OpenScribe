"""Owner-scoped Telehealth API contract; excludes audio refs and clinical content."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


TelehealthChannel = Literal["clinician", "patient"]
CaptureStatus = Literal[
    "active", "stopping", "interrupted", "ready", "processing", "completed", "failed"
]
PartStatus = Literal["staged", "processing", "transcribed", "failed", "expired"]


class TelehealthCaptureStart(BaseModel):
    model_config = {"extra": "forbid"}
    timeline_sample_rate: int = Field(strict=True, ge=8000, le=192000)


class TelehealthCaptureStop(BaseModel):
    model_config = {"extra": "forbid"}
    interrupted_channel: TelehealthChannel | None = None
    failure_code: Literal[
        "source_ended", "permission_revoked", "audio_clock_interrupted",
        "audio_backpressure", "audio_processor_failed", "upload_failed",
        "browser_capture_failed",
    ] | None = None

    @model_validator(mode="after")
    def validate_interruption_reason(self):
        if self.interrupted_channel is not None and self.failure_code is None:
            raise ValueError("A source interruption requires a fixed failure code.")
        return self


class TelehealthCaptureSeal(BaseModel):
    model_config = {"extra": "forbid"}
    expected_clinician_parts: int = Field(strict=True, ge=0, le=100000)
    expected_patient_parts: int = Field(strict=True, ge=0, le=100000)


class TelehealthAudioTimingSpan(BaseModel):
    model_config = {"extra": "forbid"}
    file_start_sample: int = Field(strict=True, ge=0)
    file_end_sample: int = Field(strict=True, ge=0)
    timeline_start_frame: int = Field(strict=True, ge=0)
    timeline_end_frame: int = Field(strict=True, ge=0)


class TelehealthCapturePartDetail(BaseModel):
    id: UUID
    channel: TelehealthChannel
    sequence_no: int
    status: PartStatus
    timeline_start_frame: int
    timeline_end_frame: int
    source_audio_duration_seconds: float
    ingestion_job_id: UUID | None

    model_config = {"from_attributes": True}


class TelehealthCaptureDetail(BaseModel):
    capture_session_id: UUID
    transcript_id: UUID
    status: CaptureStatus
    timeline_sample_rate: int
    failure_channel: TelehealthChannel | None
    failure_code: str | None
    incomplete_acknowledged_at: datetime | None
    parts: list[TelehealthCapturePartDetail]
    recording_started_at: datetime
    recording_stopped_at: datetime | None


class TelehealthAudioPartAccepted(BaseModel):
    capture_session_id: UUID
    part: TelehealthCapturePartDetail


class TelehealthTranscriptionQueued(BaseModel):
    capture_session_id: UUID
    status: CaptureStatus
    ingestion_job_ids: list[UUID]
