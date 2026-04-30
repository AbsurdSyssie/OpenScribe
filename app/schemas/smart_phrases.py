import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


_TRIGGER_RE = re.compile(r"^[A-Z0-9_]{1,64}$")


def normalize_smart_phrase_trigger(value: str) -> str:
    trigger = str(value or "").strip()
    if trigger.startswith("/"):
        raise ValueError("Store smart phrase triggers without the leading slash")
    trigger = trigger.upper()
    if not _TRIGGER_RE.fullmatch(trigger):
        raise ValueError("Trigger must use only A-Z, 0-9, and underscore")
    return trigger


def normalize_expansion_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Expansion text is required")
    if len(text) > 2000:
        raise ValueError("Expansion text must be 2,000 characters or fewer")
    return text


class SmartPhraseCreate(BaseModel):
    trigger: str = Field(min_length=1, max_length=64)
    expansion_text: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("trigger")
    @classmethod
    def validate_trigger(cls, value: str) -> str:
        return normalize_smart_phrase_trigger(value)

    @field_validator("expansion_text")
    @classmethod
    def validate_expansion_text(cls, value: str) -> str:
        return normalize_expansion_text(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class SmartPhraseUpdate(BaseModel):
    trigger: str | None = Field(default=None, min_length=1, max_length=64)
    expansion_text: str | None = Field(default=None, min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("trigger")
    @classmethod
    def validate_trigger(cls, value: str | None) -> str | None:
        return normalize_smart_phrase_trigger(value) if value is not None else None

    @field_validator("expansion_text")
    @classmethod
    def validate_expansion_text(cls, value: str | None) -> str | None:
        return normalize_expansion_text(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class SmartPhraseDetail(BaseModel):
    id: UUID
    owner_user_id: UUID
    trigger: str
    expansion_text: str
    description: str | None = None
    last_used_at: datetime | None = None
    times_used: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
