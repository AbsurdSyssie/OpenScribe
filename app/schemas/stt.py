from datetime import datetime
import ipaddress
from uuid import UUID
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import SttAdapterKind, SttAuthMode, SttSelectionPurpose


def _validate_stt_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("STT base URL must use http or https")
    if not parsed.netloc:
        raise ValueError("STT base URL must include a host")
    host = (parsed.hostname or "").lower()
    is_localish = host == "localhost"
    if not is_localish:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            is_localish = ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified
    if parsed.scheme != "https" and not is_localish:
        raise ValueError("Remote STT endpoints must use https")
    return value.rstrip("/")


class SttConfigUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}
    config_id: UUID | None = None
    team_id: UUID | None = None
    label: str = Field(min_length=1, max_length=255)
    adapter_kind: SttAdapterKind = SttAdapterKind.generic_rest
    base_url: str = Field(default="", max_length=2048)
    transcribe_path: str = Field(default="", max_length=255)
    auth_mode: SttAuthMode = SttAuthMode.bearer
    bearer_token: str | None = Field(default=None, min_length=1)
    model_name: str | None = Field(default=None, max_length=255)
    model_field_name: str | None = Field(default="model", max_length=255)
    file_field_name: str = Field(default="", max_length=255)
    language: str | None = Field(default=None, max_length=32)
    language_field_name: str | None = Field(default="language", max_length=255)
    response_text_path: str = Field(default="", max_length=255)
    segments_path: str | None = Field(default=None, max_length=255)
    segment_text_field: str | None = Field(default=None, max_length=255)
    segment_start_field: str | None = Field(default=None, max_length=255)
    segment_end_field: str | None = Field(default=None, max_length=255)
    segment_speaker_field: str | None = Field(default=None, max_length=255)
    extra_form_fields_json: dict[str, str] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def apply_adapter_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        adapter_kind = data.get("adapter_kind", SttAdapterKind.generic_rest)
        if isinstance(adapter_kind, str):
            adapter_kind = SttAdapterKind(adapter_kind)

        normalized = dict(data)
        if adapter_kind is SttAdapterKind.openai_cloud:
            normalized["base_url"] = (normalized.get("base_url") or "https://api.openai.com/v1").strip()
            normalized["transcribe_path"] = "/v1/audio/transcriptions"
            normalized["file_field_name"] = "file"
            normalized["model_field_name"] = "model"
            normalized["language_field_name"] = "language"
            normalized["response_text_path"] = "text"
        elif adapter_kind is SttAdapterKind.openai_compatible_rest:
            normalized["transcribe_path"] = "/v1/audio/transcriptions"
            normalized["file_field_name"] = "file"
            normalized.setdefault("model_field_name", "model")
            normalized.setdefault("language_field_name", "language")
            normalized["response_text_path"] = "text"
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            raise ValueError("STT base URL is required")
        return _validate_stt_base_url(value)

    @field_validator("transcribe_path")
    @classmethod
    def validate_transcribe_path(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Transcribe path is required")
        if not trimmed.startswith("/"):
            raise ValueError("Transcribe path must start with /")
        return trimmed

    @field_validator("file_field_name")
    @classmethod
    def validate_file_field_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("File field name is required")
        return trimmed

    @field_validator("response_text_path")
    @classmethod
    def validate_response_text_path(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Response text path is required")
        return trimmed

    @field_validator(
        "model_field_name",
        "language_field_name",
        "segments_path",
        "segment_text_field",
        "segment_start_field",
        "segment_end_field",
        "segment_speaker_field",
    )
    @classmethod
    def blank_optional_fields_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("extra_form_fields_json")
    @classmethod
    def validate_extra_fields(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("Extra form field keys must not be blank")
            cleaned[normalized_key] = item
        return cleaned

    @model_validator(mode="after")
    def validate_known_adapter_requirements(self):
        if self.adapter_kind in {SttAdapterKind.openai_cloud, SttAdapterKind.openai_compatible_rest} and not self.model_name:
            raise ValueError("Model name is required for OpenAI STT adapters")
        if self.model_name and not self.model_field_name:
            self.model_field_name = "model"
        if self.language and not self.language_field_name:
            self.language_field_name = "language"
        return self


class SttConfigDetail(BaseModel):
    id: UUID
    team_id: UUID
    label: str
    adapter_kind: SttAdapterKind
    base_url: str
    transcribe_path: str
    auth_mode: SttAuthMode
    model_name: str | None
    model_field_name: str | None
    available_models_json: list[str]
    file_field_name: str
    language: str | None
    language_field_name: str | None
    response_text_path: str
    segments_path: str | None
    segment_text_field: str | None
    segment_start_field: str | None
    segment_end_field: str | None
    segment_speaker_field: str | None
    extra_form_fields_json: dict[str, str]
    is_active: bool
    has_secret: bool
    created_by_user_id: UUID
    updated_by_user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class SttSelectionUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}
    team_id: UUID | None = None
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation
    stt_config_id: UUID
    model_name_override: str | None = Field(default=None, max_length=255)
    language_override: str | None = Field(default=None, max_length=32)


class SttSelectionDetail(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: UUID
    team_id: UUID
    purpose: SttSelectionPurpose
    stt_config_id: UUID
    selected_by_user_id: UUID
    selected_config_label: str
    selected_config_adapter_kind: SttAdapterKind
    selected_config_base_url: str
    selected_config_transcribe_path: str
    model_name_override: str | None
    language_override: str | None
    resolved_model_name: str | None
    resolved_language: str | None
    available_models_json: list[str]
    created_at: datetime
    updated_at: datetime


class SttInspectRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID | None = None
    adapter_kind: SttAdapterKind = SttAdapterKind.generic_rest
    base_url: str = Field(default="", max_length=2048)
    openapi_path: str | None = Field(default=None, max_length=255)
    bearer_token: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def apply_adapter_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        adapter_kind = data.get("adapter_kind", SttAdapterKind.generic_rest)
        if isinstance(adapter_kind, str):
            adapter_kind = SttAdapterKind(adapter_kind)

        normalized = dict(data)
        if adapter_kind is SttAdapterKind.openai_cloud:
            normalized["base_url"] = (normalized.get("base_url") or "https://api.openai.com/v1").strip()
            normalized["openapi_path"] = None
        elif adapter_kind is SttAdapterKind.openai_compatible_rest and normalized.get("openapi_path") is None:
            normalized["openapi_path"] = None
        elif adapter_kind is SttAdapterKind.generic_rest and not normalized.get("openapi_path"):
            normalized["openapi_path"] = "/openapi.json"
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            raise ValueError("STT base URL is required")
        return _validate_stt_base_url(value)

    @field_validator("openapi_path")
    @classmethod
    def validate_openapi_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if not trimmed.startswith("/"):
            raise ValueError("OpenAPI path must start with /")
        return trimmed


class SttInspectFieldTip(BaseModel):
    name: str
    role: str
    default_value: str | None
    description: str | None
    required: bool

    model_config = {"protected_namespaces": ()}


class SttModelOption(BaseModel):
    id: str
    source: str
    label: str

    model_config = {"protected_namespaces": ()}


class SttInspectResult(BaseModel):
    base_url: str
    openapi_path: str | None
    adapter_kind: SttAdapterKind
    transcribe_path: str
    model_name: str | None
    model_field_name: str | None = Field(default="model", max_length=255)
    file_field_name: str
    language: str | None
    language_field_name: str | None = Field(default="language", max_length=255)
    response_text_path: str
    segments_path: str | None = Field(default=None, max_length=255)
    segment_text_field: str | None = Field(default=None, max_length=255)
    segment_start_field: str | None = Field(default=None, max_length=255)
    segment_end_field: str | None = Field(default=None, max_length=255)
    segment_speaker_field: str | None = Field(default=None, max_length=255)
    extra_form_fields_json: dict[str, str]
    candidate_paths: list[str]
    operation_summary: str | None
    available_models: list[str] = Field(default_factory=list)
    available_model_options: list[SttModelOption] = Field(default_factory=list)
    field_tips: list[SttInspectFieldTip]
    notes: list[str]

    model_config = {"protected_namespaces": ()}
