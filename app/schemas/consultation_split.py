"""Safe owner-facing consultation split analysis responses."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .templates import GeneratedDocumentDetail


ConsultationSplitPublicStatus = Literal[
    "queued",
    "processing",
    "ready",
    "not_required",
    "failed",
    "stale",
    "incomplete",
]


class ConsultationSplitTopicDetail(BaseModel):
    """The only proposal fields safe for the owner API."""

    model_config = ConfigDict(extra="forbid")

    topic_uuid: UUID
    title: str = Field(min_length=1, max_length=255)
    is_primary: bool
    disposition: Literal["separate_note", "include_in_primary", "exclude_from_notes"]
    template_id: UUID | None = None


class ConsultationSplitAnalysisDetail(BaseModel):
    """Content-safe analysis lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID | None = None
    status: ConsultationSplitPublicStatus
    error_code: str | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    topics: list[ConsultationSplitTopicDetail] = Field(default_factory=list, max_length=6)


class ConsultationSplitIntentStartRequest(BaseModel):
    """One owner Generate intent, keyed for safe replay."""

    model_config = ConfigDict(extra="forbid")

    client_idempotency_key: UUID
    selected_template_id: UUID


class ConsultationSplitIntentStartResponse(BaseModel):
    """Safe result of starting or replaying one split Generate intent."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID | None = None
    idempotency_replayed: bool
    analysis: ConsultationSplitAnalysisDetail


class ConsultationSplitIntentContinueAsOneNoteResponse(BaseModel):
    """Safe replay-aware result of consuming one saved split intent."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    idempotency_replayed: bool
    document: GeneratedDocumentDetail | None = None
    consumed_document_deleted: bool


class ConsultationSplitBatchRegenerateRequest(BaseModel):
    """Durable replay key for a fresh generation from a confirmed batch."""

    model_config = ConfigDict(extra="forbid")

    client_idempotency_key: UUID


class ConsultationSplitBatchRegenerateResponse(BaseModel):
    """Content-safe result of queuing a regenerated split batch."""

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    execution_id: UUID
    idempotency_replayed: bool


ConsultationSplitDraftPublicStatus = Literal["active", "stale", "confirmed", "bypassed"]
ConsultationSplitDraftDisposition = Literal[
    "separate_note",
    "include_in_primary",
    "exclude_from_notes",
]


class ConsultationSplitDraftTopicDetail(BaseModel):
    """Owner-only editable draft topic; titles are decrypted only for this response."""

    model_config = ConfigDict(extra="forbid")

    topic_uuid: UUID
    title: str = Field(min_length=1, max_length=255)
    order: int = Field(ge=0, le=5)
    is_primary: bool
    disposition: ConsultationSplitDraftDisposition
    template_id: UUID | None = None
    template_version_id: UUID | None = None


class ConsultationSplitDraftDetail(BaseModel):
    """The safe review-draft projection; no source or provider metadata."""

    model_config = ConfigDict(extra="forbid")

    draft_id: UUID
    analysis_id: UUID
    status: ConsultationSplitDraftPublicStatus
    created_at: datetime
    updated_at: datetime
    topics: list[ConsultationSplitDraftTopicDetail] = Field(default_factory=list, max_length=6)


class ConsultationSplitDraftTopicReplace(BaseModel):
    """One complete replacement topic; omitted UUID creates a clinician topic."""

    model_config = ConfigDict(extra="forbid")

    topic_uuid: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    is_primary: bool
    disposition: ConsultationSplitDraftDisposition
    template_id: UUID | None = None


class ConsultationSplitDraftReplace(BaseModel):
    """Optimistic, full-list review-draft replacement request."""

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime
    topics: list[ConsultationSplitDraftTopicReplace] = Field(default_factory=list, max_length=6)


class ConsultationSplitDraftConfirmRequest(BaseModel):
    """Idempotently bind one browser-owned intent to the current draft."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    expected_updated_at: datetime


class ConsultationSplitDraftConfirmResponse(BaseModel):
    """Content-safe acknowledgement of immutable split-batch persistence."""

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    status: Literal["generation_queued"]
    separate_note_count: int = Field(ge=2, le=6)
    topic_count: int = Field(ge=2, le=6)
    created_at: datetime
    updated_at: datetime
    idempotency_replayed: bool


class ConsultationSplitKeepAvailableResponse(BaseModel):
    """Content-safe acknowledgement of a clinician's partial acceptance."""

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    status: Literal["completed_partial"]
    document_ids: list[UUID] = Field(default_factory=list, max_length=6)


class ConsultationSplitRetryMissingResponse(BaseModel):
    """Safe acknowledgement of one targeted recovery execution."""

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    execution_id: UUID
    idempotency_replayed: bool


class ConsultationSplitBatchDetail(BaseModel):
    """Owner-safe partial-batch state; never includes topic titles or output."""
    model_config = ConfigDict(extra="forbid")
    batch_id: UUID
    status: Literal["generation_queued", "generating", "verifying", "ready", "partially_ready", "completed_partial", "failed"]
    failed_topic_count: int = Field(ge=0, le=6)
    validated_topic_count: int = Field(ge=0, le=6)
    primary_failed: bool
    can_retry_missing: bool
    can_keep_available: bool
    active_execution_id: UUID | None = None
    # This is metadata only.  It lets the workspace select a useful note after
    # a completed partial decision without exposing any topic or provider data.
    preferred_document_id: UUID | None = None
    verification_status: Literal["pending", "verifying", "verified", "unchecked"] = "pending"
    verification_correction_count: int | None = Field(default=None, ge=0)
