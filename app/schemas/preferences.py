from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models import TranscriptIngestionMode


MAX_FAVORITES = 8


class LlmDetailLevel(str, Enum):
    concise = "concise"
    balanced = "balanced"
    detailed = "detailed"


class NoteGenerationLength(str, Enum):
    short = "short"
    normal = "normal"
    long = "long"


def _dedupe_uuids(values: list[UUID]) -> list[UUID]:
    seen: set[UUID] = set()
    deduped: list[UUID] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


class UserAppPreferencesUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    favorite_quick_action_ids: list[UUID] = Field(default_factory=list)
    favorite_template_ids: list[UUID] = Field(default_factory=list)
    default_quick_action_id: UUID | None = None
    default_template_id: UUID | None = None
    template_suggestions_enabled: bool = True
    llm_detail_level: LlmDetailLevel | None = None
    note_generation_length: NoteGenerationLength | None = None
    preferred_recording_mode: TranscriptIngestionMode | None = None
    preferred_transcribe_tab: Literal["output", "followups"] | None = None

    @field_validator("favorite_quick_action_ids", "favorite_template_ids")
    @classmethod
    def validate_favorite_ids(cls, value: list[UUID]) -> list[UUID]:
        deduped = _dedupe_uuids(value)
        if len(deduped) > MAX_FAVORITES:
            raise ValueError(f"Select at most {MAX_FAVORITES} favourites")
        return deduped


class UserAppPreferencesDetail(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: UUID
    user_id: UUID
    favorite_quick_action_ids: list[UUID]
    favorite_template_ids: list[UUID]
    default_quick_action_id: UUID | None
    default_template_id: UUID | None
    template_suggestions_enabled: bool
    llm_detail_level: LlmDetailLevel | None
    note_generation_length: NoteGenerationLength | None
    preferred_recording_mode: TranscriptIngestionMode | None
    preferred_transcribe_tab: Literal["output", "followups"] | None
    created_at: datetime
    updated_at: datetime
