from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PostConsultationDictationUpdate(BaseModel):
    combined_text: str = Field(default="")


class PostConsultationDictationPreview(BaseModel):
    text: str


class PromptContextPreview(BaseModel):
    text: str


class PostConsultationDictationDetail(BaseModel):
    id: UUID
    transcript_id: UUID
    owner_user_id: UUID
    team_id: UUID
    combined_edited_text_encrypted: str | None = None
    effective_text: str
    is_combined_text_user_edited: bool
    segment_count: int
    latest_appended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
