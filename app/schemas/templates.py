from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt, field_validator

from app.models import GeneratedDocumentGeneratorType, GeneratedDocumentStatus, HallucinationCheckStatus, TemplateMode, TemplateScope


EMIS_SECTION_KEYS = (
    "problem",
    "history",
    "family_history",
    "social_history",
    "examination",
    "comment",
    "tasks",
    "investigations",
)

EMIS_SECTION_LABELS = {
    "problem": "Problem",
    "history": "History",
    "family_history": "Family history",
    "social_history": "Social history",
    "examination": "Examination",
    "comment": "Comment",
    "tasks": "Tasks",
    "investigations": "Investigations",
}


class TemplateSuggestionCandidate(BaseModel):
    template_id: UUID
    template_name: str
    confidence: Literal["medium", "high"]


class TemplateSuggestionResponse(BaseModel):
    status: Literal["not_eligible", "queued", "processing", "completed", "failed"]
    suggestion: TemplateSuggestionCandidate | None = None


class StructuredTemplateSectionConfig(BaseModel):
    section_key: str
    instruction: str
    section_order: StrictInt


class StructuredTemplateConfig(BaseModel):
    profile: str = "emis"
    sections: list[StructuredTemplateSectionConfig]


class TemplateBundleVersion(BaseModel):
    mode: TemplateMode
    prompt_text: str = Field(min_length=1)
    config_json: StructuredTemplateConfig | None


class TemplateBundleEntry(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None
    latest_version: TemplateBundleVersion


class TemplateBundle(BaseModel):
    format: str
    format_version: int
    templates: list[TemplateBundleEntry] = Field(min_length=1, max_length=100)


class TemplateBundleExportRequest(BaseModel):
    template_ids: list[UUID] = Field(min_length=1, max_length=100)


class PromptTemplateUpsert(BaseModel):
    template_id: UUID | None = None
    scope: TemplateScope
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    prompt_text: str = Field(min_length=1)
    mode: TemplateMode = TemplateMode.freeform
    config_json: StructuredTemplateConfig | None = None
    is_active: bool = True


class DefaultPromptTemplateUpsert(BaseModel):
    template_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    prompt_text: str = Field(min_length=1)
    mode: TemplateMode = TemplateMode.freeform
    config_json: StructuredTemplateConfig | None = None
    is_active: bool = True


class PromptTemplateVersionDetail(BaseModel):
    id: UUID
    version_no: int
    mode: TemplateMode
    prompt_text: str
    config_json: StructuredTemplateConfig | None = None
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


class DefaultQuickActionUpsert(BaseModel):
    quick_action_id: UUID | None = None
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
    redaction_run_id: UUID | None = None
    generator_type: GeneratedDocumentGeneratorType
    template_version_id: UUID | None
    quick_action_version_id: UUID | None = None
    source_template_name: str
    source_quick_action_name: str | None = None
    follow_up_prompt_text: str | None = None
    structured_section_definitions_json: dict | None = None
    status: GeneratedDocumentStatus
    title: str
    document_mode: TemplateMode
    original_output_text: str = ""
    edited_output_text: str = ""
    llm_request_payload_json: dict | None = None
    is_edited: bool
    retention_expires_at: datetime
    model_used: str | None
    input_token_count: int | None = None
    output_token_count: int | None = None
    total_token_count: int | None = None
    estimated_cost_usd: float | None = None
    duration_ms: int | None = None
    provider_duration_ms: int | None = None
    hallucination_check_status: HallucinationCheckStatus = HallucinationCheckStatus.not_applicable
    hallucination_check_bucket: str = "not_applicable"
    hallucination_check_applied_edit_count: int | None = None
    hallucination_check_debug_json: dict | None = None
    error_code: str | None = None
    provider_error_code: str | None = None
    provider_http_status: int | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    sections: list["GeneratedDocumentSectionDetail"] = []
    pii_entities: list["GeneratedDocumentPiiEntityDetail"] = []

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class GeneratedDocumentSectionDetail(BaseModel):
    id: UUID
    section_key: str
    section_label: str
    section_order: int
    original_text_encrypted: str
    edited_text_encrypted: str
    is_edited: bool

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class GeneratedDocumentPiiEntityDetail(BaseModel):
    entity_type: str
    placeholder: str
    occurrence_count: int
    has_value: bool = True


class GeneratedDocumentSectionUpdate(BaseModel):
    section_key: str = Field(min_length=1, max_length=64)
    section_label: str = Field(min_length=1, max_length=255)
    section_order: int
    text: str = ""


class GeneratedDocumentUpdateRequest(BaseModel):
    expected_updated_at: datetime
    title: str | None = Field(default=None, max_length=255)
    edited_output_text: str = ""
    sections: list[GeneratedDocumentSectionUpdate] = Field(default_factory=list)


class RedactionDebugEntityDetail(BaseModel):
    entity_order: int
    entity_type: str
    placeholder: str
    occurrence_count: int


class GeneratedDocumentRedactionDebugDetail(BaseModel):
    generated_document_id: UUID
    redaction_run_id: UUID
    transcript_version_id: UUID
    status: str
    api_provider: str
    api_model_or_version: str | None = None
    entity_count: int
    mapping_hash: str | None = None
    redacted_text: str
    failed_provider_output_redacted_text: str | None = None
    entities: list[RedactionDebugEntityDetail]


class GenerateTemplateOutputRequest(BaseModel):
    model_config = {"extra": "forbid"}

    template_id: UUID


class GenerateFollowupRequest(BaseModel):
    prompt_text: str = Field(min_length=1, max_length=4000)


class GenerateQuickActionRequest(BaseModel):
    quick_action_id: UUID
    context_text: str | None = Field(default=None, max_length=4000)

    @field_validator("context_text")
    @classmethod
    def validate_context_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class RegenerateGeneratedDocumentRequest(BaseModel):
    model_config = {"extra": "forbid"}

    steering_text: str | None = Field(default=None, max_length=4000)

    @field_validator("steering_text")
    @classmethod
    def validate_steering_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


GeneratedDocumentDetail.model_rebuild()
