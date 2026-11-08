"""Owner-scoped Telehealth API contract; excludes audio refs and clinical content."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


TelehealthChannel = Literal["clinician", "patient"]
CaptureStatus = Literal[
    "active", "stopping", "interrupted", "ready", "processing", "partial", "completed", "failed", "discarded"
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
    retry_available: bool = False
    included_in_transcript: bool = False

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
    can_use_available_transcript: bool = False


class TelehealthPartialCapturesPage(BaseModel):
    items: list[TelehealthCaptureDetail]
    next_offset: int | None


class TelehealthAudioPartAccepted(BaseModel):
    capture_session_id: UUID
    part: TelehealthCapturePartDetail


class TelehealthTranscriptionQueued(BaseModel):
    capture_session_id: UUID
    status: CaptureStatus
    ingestion_job_ids: list[UUID]


# Client diagnostics are metadata-only; arbitrary browser exception strings,
# URLs, clinical text and identifiers are rejected at model validation.
TelehealthDiagnosticEventName = Literal[
    "picker_requested", "picker_selected", "picker_rejected", "window_acknowledged",
    "microphone_requested", "microphone_ready", "capture_started", "capture_stopping",
    "capture_stopped", "capture_failed", "capture_state", "capture_interrupted",
    "track_ended", "track_muted", "source_changed", "worklet_ready",
    "vad_loading", "vad_ready", "vad_unavailable", "vad_speech_confirmed",
    "journal_check", "journal_failed", "window_prepared", "upload_started",
    "upload_accepted", "upload_failed", "seal_started", "seal_completed",
    "seal_failed", "transcribe_started", "transcribe_accepted", "transcribe_failed",
]


class TelehealthBrowserDiagnosticEvent(BaseModel):
    model_config = {"extra": "forbid"}
    event: TelehealthDiagnosticEventName
    channel: Literal["patient", "clinician", "none"] | None = None
    surface: Literal["browser", "window", "monitor", "unknown"] | None = None
    state: Literal[
        "idle", "starting", "recording", "running", "stopping", "stopped",
        "failed", "recovery_required", "incomplete", "sealed",
        "transcribing", "discarded",
    ] | None = None
    code: Literal[
        "none", "user_cancelled", "permission_denied", "window_declined",
        "unsupported_surface", "missing_audio", "missing_video", "source_ended",
        "source_muted", "audio_clock_interrupted", "audio_backpressure",
        "audio_processor_failed", "audio_flush_failed", "browser_capture_failed",
        "vad_unavailable", "vad_initialization_failed", "journal_unavailable",
        "upload_failed", "seal_failed", "transcribe_failed",
    ] | None = None
    part: int | None = Field(default=None, strict=True, ge=0, le=1000000)


class TelehealthBrowserDiagnosticsBatch(BaseModel):
    model_config = {"extra": "forbid"}
    diagnostic_id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    events: list[TelehealthBrowserDiagnosticEvent] = Field(min_length=1, max_length=20)


class TelehealthBrowserDiagnosticsAccepted(BaseModel):
    accepted: int
