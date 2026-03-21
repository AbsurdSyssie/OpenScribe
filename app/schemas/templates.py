from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import GeneratedDocumentGeneratorType, GeneratedDocumentStatus, TemplateMode, TemplateScope


class PromptTemplateUpsert(BaseModel):
    template_id: UUID | None = None
    scope: TemplateScope
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    prompt_text: str = Field(min_length=1)
    is_active: bool = True


class PromptTemplateVersionDetail(BaseModel):
    id: UUID
    version_no: int
    mode: TemplateMode
    prompt_text: str
    created_by_user_id: UUID
    created_at: datetime


class PromptTemplateDetail(BaseModel):
    id: UUID
    scope: TemplateScope
    owner_user_id: UUID | None
    team_id: UUID | None
    name: str
    description: str | None
    is_active: bool
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    latest_version: PromptTemplateVersionDetail


class QuickActionUpsert(BaseModel):
    quick_action_id: UUID | None = None
    scope: TemplateScope
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    prompt_text: str = Field(min_length=1)
    is_active: bool = True


class QuickActionVersionDetail(BaseModel):
    id: UUID
    version_no: int
    mode: TemplateMode
    prompt_text: str
    created_by_user_id: UUID
    created_at: datetime


class QuickActionDetail(BaseModel):
    id: UUID
    scope: TemplateScope
    owner_user_id: UUID | None
    team_id: UUID | None
    name: str
    description: str | None
    is_active: bool
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    latest_version: QuickActionVersionDetail


class GeneratedDocumentDetail(BaseModel):
    id: UUID
    owner_user_id: UUID
    team_id: UUID
    transcript_id: UUID
    transcript_version_id: UUID
    generator_type: GeneratedDocumentGeneratorType
    template_version_id: UUID | None
    quick_action_version_id: UUID | None = None
    source_template_name: str
    source_quick_action_name: str | None = None
    follow_up_prompt_text: str | None = None
    status: GeneratedDocumentStatus
    title: str
    document_mode: TemplateMode
    original_output_text_encrypted: str
    edited_output_text_encrypted: str
    is_edited: bool
    retention_expires_at: datetime
    model_used: str | None
    input_token_count: int | None = None
    output_token_count: int | None = None
    total_token_count: int | None = None
    estimated_cost_usd: float | None = None
    duration_ms: int | None = None
    provider_duration_ms: int | None = None
    error_code: str | None = None
    provider_error_code: str | None = None
    provider_http_status: int | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class GenerateTemplateOutputRequest(BaseModel):
    template_id: UUID


class GenerateFollowupRequest(BaseModel):
    prompt_text: str = Field(min_length=1, max_length=4000)


class GenerateQuickActionRequest(BaseModel):
    quick_action_id: UUID
