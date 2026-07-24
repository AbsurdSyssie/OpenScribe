from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.smart_phrases import SmartPhraseCreate


class SmartPhraseBundleEntry(BaseModel):
    """A portable smart phrase, deliberately excluding operational metadata."""

    trigger: str = Field(min_length=1, max_length=64)
    expansion_text: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(max_length=255)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def normalize_like_personal_smart_phrase(self) -> "SmartPhraseBundleEntry":
        normalized = SmartPhraseCreate(
            trigger=self.trigger,
            expansion_text=self.expansion_text,
            description=self.description,
        )
        self.trigger = normalized.trigger
        self.expansion_text = normalized.expansion_text
        self.description = normalized.description
        return self


class SmartPhraseBundle(BaseModel):
    format: str
    format_version: int
    smart_phrases: list[SmartPhraseBundleEntry] = Field(min_length=1, max_length=100)

    model_config = {"extra": "forbid"}


class SmartPhraseBundleExportRequest(BaseModel):
    smart_phrase_ids: list[UUID] = Field(min_length=1, max_length=100)
