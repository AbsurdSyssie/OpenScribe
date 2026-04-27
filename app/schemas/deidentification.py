from datetime import datetime
import ipaddress
from typing import Any
from uuid import UUID
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.models import DeidentificationAdapterKind, DeidentificationAuthMode


SECRET_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-api-token",
    "x-auth-token",
    "x-access-token",
    "api-key",
    "apikey",
}

SECRET_BODY_FIELD_NAMES = SECRET_HEADER_NAMES | {
    "access_token",
    "api_key",
    "api_token",
    "auth_token",
    "bearer_token",
    "client_secret",
    "client_token",
    "password",
    "secret",
    "token",
}


def _validate_deidentification_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("De-identification base URL must use http or https")
    if not parsed.netloc:
        raise ValueError("De-identification base URL must include a host")
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
        raise ValueError("Remote de-identification endpoints must use https")
    return value.rstrip("/")


class DeidentificationProviderUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    provider_id: UUID | None = None
    label: str = Field(min_length=1, max_length=255)
    adapter_kind: DeidentificationAdapterKind = DeidentificationAdapterKind.generic_rest
    base_url: str = Field(default="", max_length=2048)
    detect_path: str = Field(default="", max_length=255)
    auth_mode: DeidentificationAuthMode = DeidentificationAuthMode.none
    bearer_token: str | None = Field(default=None, min_length=1)
    request_text_field: str = Field(default="text", max_length=255)
    request_language_field: str | None = Field(default=None, max_length=255)
    extra_headers_json: dict[str, str] = Field(default_factory=dict)
    extra_body_json: dict[str, Any] = Field(default_factory=dict)
    response_entities_path: str = Field(default="entities", max_length=255)
    response_start_field: str = Field(default="start", max_length=255)
    response_end_field: str = Field(default="end", max_length=255)
    response_type_field: str = Field(default="entity_type", max_length=255)
    response_score_field: str | None = Field(default=None, max_length=255)
    response_model_version_path: str | None = Field(default=None, max_length=255)
    entity_type_map_json: dict[str, str] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def apply_adapter_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        adapter_kind = normalized.get("adapter_kind", DeidentificationAdapterKind.generic_rest)
        if isinstance(adapter_kind, str):
            adapter_kind = DeidentificationAdapterKind(adapter_kind)
        if adapter_kind is DeidentificationAdapterKind.native_presidio:
            normalized["base_url"] = ""
            normalized["detect_path"] = ""
            normalized["auth_mode"] = DeidentificationAuthMode.none
            normalized["request_text_field"] = "text"
            normalized["request_language_field"] = None
            normalized["extra_headers_json"] = {}
            normalized["extra_body_json"] = {}
            normalized["response_entities_path"] = "entities"
            normalized["response_start_field"] = "start"
            normalized["response_end_field"] = "end"
            normalized["response_type_field"] = "entity_type"
            normalized["response_score_field"] = None
            normalized["response_model_version_path"] = None
            normalized["entity_type_map_json"] = {}
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            return value
        return _validate_deidentification_base_url(value)

    @field_validator(
        "detect_path",
        "request_text_field",
        "response_entities_path",
        "response_start_field",
        "response_end_field",
        "response_type_field",
    )
    @classmethod
    def validate_required_string(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Field must not be blank")
        return trimmed

    @field_validator("request_language_field", "response_score_field", "response_model_version_path")
    @classmethod
    def validate_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("extra_headers_json", "extra_body_json", "entity_type_map_json")
    @classmethod
    def validate_string_map(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("Map keys must not be blank")
            cleaned[normalized_key] = item if info.field_name == "extra_body_json" else str(item)
        return cleaned

    @field_validator("extra_headers_json")
    @classmethod
    def validate_extra_headers_do_not_store_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if key.strip().lower() in SECRET_HEADER_NAMES:
                raise ValueError("Secret-bearing de-identification headers must use bearer_token/Vault storage")
        return value

    @field_validator("extra_body_json")
    @classmethod
    def validate_extra_body_does_not_store_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if key.strip().lower() in SECRET_BODY_FIELD_NAMES:
                raise ValueError("Secret-bearing de-identification body fields must use bearer_token/Vault storage")
        return value

    @model_validator(mode="after")
    def validate_adapter_shape(self):
        if self.adapter_kind is DeidentificationAdapterKind.generic_rest:
            if not self.base_url:
                raise ValueError("De-identification base URL is required for generic REST adapter")
            if not self.detect_path:
                raise ValueError("Detect path is required for generic REST adapter")
            if not self.detect_path.startswith("/"):
                raise ValueError("Detect path must start with /")
            if self.auth_mode is DeidentificationAuthMode.bearer and self.provider_id is None and not self.bearer_token:
                raise ValueError("Bearer token is required when creating bearer-auth de-identification provider")
        return self


class DeidentificationProviderInspectRequest(DeidentificationProviderUpsert):
    openapi_path: str | None = Field(default=None, max_length=255)
    sample_text: str = Field(default="Jane Smith attended on 22 April 2026.", min_length=1, max_length=500)

    @field_validator("sample_text")
    @classmethod
    def validate_sample_text(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Sample text must not be blank")
        return trimmed


class DeidentificationInspectEntity(BaseModel):
    start: int
    end: int
    entity_type: str
    score: float
    value: str


class DeidentificationInspectFieldTip(BaseModel):
    name: str
    role: str
    default_value: str | None = None
    description: str | None = None
    required: bool = False


class DeidentificationInspectResult(BaseModel):
    provider_label: str
    adapter_kind: DeidentificationAdapterKind
    openapi_path: str | None = None
    detect_path: str
    request_text_field: str
    request_language_field: str | None = None
    extra_body_json: dict[str, Any] = Field(default_factory=dict)
    response_entities_path: str
    response_start_field: str
    response_end_field: str
    response_type_field: str
    response_score_field: str | None = None
    response_model_version_path: str | None = None
    api_provider: str
    api_model_or_version: str | None = None
    sample_text: str
    entities: list[DeidentificationInspectEntity]
    candidate_paths: list[str] = Field(default_factory=list)
    operation_summary: str | None = None
    field_tips: list[DeidentificationInspectFieldTip] = Field(default_factory=list)
    raw_response_json: Any | None = None
    notes: list[str] = Field(default_factory=list)


class DeidentificationProviderDetail(BaseModel):
    model_config = {"from_attributes": True, "protected_namespaces": ()}

    id: UUID
    label: str
    adapter_kind: DeidentificationAdapterKind
    base_url: str
    detect_path: str
    auth_mode: DeidentificationAuthMode
    request_text_field: str
    request_language_field: str | None
    extra_headers_json: dict[str, str]
    extra_body_json: dict[str, Any]
    response_entities_path: str
    response_start_field: str
    response_end_field: str
    response_type_field: str
    response_score_field: str | None
    response_model_version_path: str | None
    entity_type_map_json: dict[str, str]
    is_active: bool
    is_builtin: bool
    has_secret: bool
    created_by_user_id: UUID | None
    updated_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class DeidentificationProviderAssignmentUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    provider_id: UUID


class DeidentificationProviderAssignmentDetail(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: UUID
    team_id: UUID
    provider_id: UUID
    provider_label: str
    provider_adapter_kind: DeidentificationAdapterKind
    assigned_by_user_id: UUID
    created_at: datetime


class DeidentificationSelectionUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID | None = None
    provider_id: UUID


class DeidentificationSelectionDetail(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: UUID
    team_id: UUID
    provider_id: UUID
    selected_by_user_id: UUID
    selected_provider_label: str
    selected_provider_adapter_kind: DeidentificationAdapterKind
    selected_provider_is_builtin: bool
    created_at: datetime
    updated_at: datetime
