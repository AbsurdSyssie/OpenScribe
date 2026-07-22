from datetime import datetime
import ipaddress
import json
import re
from typing import Literal
from uuid import UUID
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.llm_provider_defaults import normalize_bedrock_region
from app.models import LlmAdapterKind, LlmAuthMode, LlmConfigSetupStatus, LlmProviderPreset


GoogleAuthMethod = Literal["application_default", "service_account_json"]
GeminiCapacityMode = Literal["auto", "shared", "dedicated"]


class GeminiEnterpriseProviderConfig(BaseModel):
    project_id: str = Field(min_length=6, max_length=30)
    location: str = Field(min_length=1, max_length=63)
    api_version: Literal["v1"] = "v1"
    capacity_mode: GeminiCapacityMode = "auto"

    model_config = {"extra": "forbid"}

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", normalized):
            raise ValueError("Google Cloud project ID is invalid")
        return normalized

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", normalized):
            raise ValueError("Google Cloud location is invalid")
        return normalized


def gemini_enterprise_base_url(location: str) -> str:
    return (
        "https://aiplatform.googleapis.com"
        if location == "global"
        else f"https://{location}-aiplatform.googleapis.com"
    )


def _validate_google_service_account_json(value: dict[str, object]) -> None:
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise ValueError("Google service-account credential exceeds 64 KiB")
    if value.get("type") != "service_account":
        raise ValueError("Google credential must be a service-account JSON object")
    required = ("client_email", "private_key", "private_key_id", "token_uri")
    if any(not isinstance(value.get(key), str) or not str(value[key]).strip() for key in required):
        raise ValueError("Google service-account credential is missing required fields")


def _normalize_gemini_input(data: dict[str, object]) -> dict[str, object]:
    normalized = dict(data)
    credential_json = normalized.get("google_service_account_json")
    preset = normalized.get("provider_preset")
    preset_value = preset.value if isinstance(preset, LlmProviderPreset) else preset
    if preset_value != LlmProviderPreset.gemini_enterprise.value:
        if credential_json is not None:
            data["google_service_account_json"] = "<redacted>"
            normalized["google_service_account_json"] = "<redacted>"
            raise ValueError("Gemini Enterprise fields require the Gemini Enterprise provider preset")
        return normalized
    auth_method = normalized.get("google_auth_method")
    if credential_json is not None:
        if auth_method != "service_account_json":
            data["google_service_account_json"] = "<redacted>"
            normalized["google_service_account_json"] = "<redacted>"
            raise ValueError("Only service-account authentication accepts credential JSON")
        if not isinstance(credential_json, dict):
            data["google_service_account_json"] = "<redacted>"
            normalized["google_service_account_json"] = "<redacted>"
            raise ValueError("Google credential must be a service-account JSON object")
        try:
            _validate_google_service_account_json(credential_json)
        except (TypeError, ValueError):
            data["google_service_account_json"] = "<redacted>"
            normalized["google_service_account_json"] = "<redacted>"
            raise
    raw_provider_config = normalized.get("provider_config_json") or {}
    if isinstance(raw_provider_config, dict):
        unexpected_keys = set(raw_provider_config) - {"project_id", "location", "api_version", "capacity_mode"}
        if unexpected_keys:
            raise ValueError("Gemini Enterprise provider configuration contains unsupported fields")
        if raw_provider_config.get("api_version", "v1") != "v1":
            raise ValueError("Gemini Enterprise API version must be v1")
        normalized.setdefault("google_project_id", raw_provider_config.get("project_id"))
        normalized.setdefault("google_location", raw_provider_config.get("location"))
        normalized.setdefault("capacity_mode", raw_provider_config.get("capacity_mode", "auto"))
    location = normalized.get("google_location")
    if isinstance(location, str) and location.strip():
        normalized["base_url"] = gemini_enterprise_base_url(location.strip().lower())
    auth_mode = normalized.get("auth_mode")
    auth_value = auth_mode.value if isinstance(auth_mode, LlmAuthMode) else auth_mode
    if normalized.get("google_auth_method") is None:
        if auth_value == LlmAuthMode.google_adc.value:
            normalized["google_auth_method"] = "application_default"
        elif auth_value == LlmAuthMode.google_service_account.value:
            normalized["google_auth_method"] = "service_account_json"
    return normalized


def _validate_gemini_input(model: BaseModel, *, credential_required: bool) -> BaseModel:
    is_gemini = model.provider_preset == LlmProviderPreset.gemini_enterprise
    provider_config = model.provider_config_json or {}
    google_values_supplied = any(
        (
            model.google_project_id,
            model.google_location,
            model.google_auth_method,
            model.google_service_account_json,
            provider_config,
            model.capacity_mode != "auto",
        )
    )
    if not is_gemini:
        if google_values_supplied:
            raise ValueError("Gemini Enterprise fields require the Gemini Enterprise provider preset")
        return model

    if getattr(model, "bearer_token", None):
        raise ValueError("Gemini Enterprise does not accept bearer tokens")
    if model.google_auth_method is None:
        raise ValueError("Gemini Enterprise authentication method is required")
    config = GeminiEnterpriseProviderConfig.model_validate(
        {
            "project_id": model.google_project_id,
            "location": model.google_location,
            "api_version": "v1",
            "capacity_mode": model.capacity_mode,
        }
    )
    if model.google_auth_method == "application_default":
        if model.google_service_account_json is not None:
            raise ValueError("Application Default Credentials cannot include credential JSON")
    else:
        if model.google_service_account_json is None:
            if credential_required:
                raise ValueError("Service-account authentication requires credential JSON")
        else:
            _validate_google_service_account_json(model.google_service_account_json)
    model.google_project_id = config.project_id
    model.google_location = config.location
    model.capacity_mode = config.capacity_mode
    model.provider_config_json = config.model_dump()
    model.base_url = gemini_enterprise_base_url(config.location)
    if hasattr(model, "auth_mode"):
        model.auth_mode = (
            LlmAuthMode.google_adc
            if model.google_auth_method == "application_default"
            else LlmAuthMode.google_service_account
        )
    return model


def _validate_llm_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("LLM base URL must use http or https")
    if not parsed.netloc:
        raise ValueError("LLM base URL must include a host")
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
        raise ValueError("Remote LLM endpoints must use https")
    return value.rstrip("/")


class LlmModelOption(BaseModel):
    id: str
    source: str
    label: str

    model_config = {"protected_namespaces": ()}


class LlmConfigUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    config_id: UUID | None = None
    team_id: UUID | None = None
    label: str = Field(min_length=1, max_length=255)
    provider_preset: LlmProviderPreset = LlmProviderPreset.openai
    adapter_kind: LlmAdapterKind = LlmAdapterKind.openai_chat
    base_url: str = Field(default="", max_length=2048)
    auth_mode: LlmAuthMode = LlmAuthMode.bearer
    bearer_token: str | None = Field(default=None, min_length=1)
    credential_action: Literal["keep", "replace", "remove"] = "keep"
    bedrock_region: str | None = Field(default=None, max_length=64)
    provider_config_json: dict[str, object] = Field(default_factory=dict)
    google_project_id: str | None = None
    google_location: str | None = None
    google_auth_method: GoogleAuthMethod | None = None
    google_service_account_json: dict[str, object] | None = Field(default=None, repr=False)
    capacity_mode: GeminiCapacityMode = "auto"
    model_name: str | None = Field(default=None, max_length=255)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def apply_provider_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = _normalize_gemini_input(data)
        from app.services.llm_presets import apply_provider_defaults

        preset, adapter_kind, base_url, bedrock_region = apply_provider_defaults(
            provider_preset=normalized.get("provider_preset"),
            base_url=normalized.get("base_url"),
            bedrock_region=normalized.get("bedrock_region"),
            adapter_kind=normalized.get("adapter_kind"),
        )
        normalized["provider_preset"] = preset
        normalized["adapter_kind"] = adapter_kind
        normalized["base_url"] = base_url
        normalized["bedrock_region"] = bedrock_region
        return normalized

    @model_validator(mode="after")
    def validate_gemini_fields(self) -> "LlmConfigUpsert":
        credential_required = self.config_id is None or self.credential_action == "replace"
        return _validate_gemini_input(self, credential_required=credential_required)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            raise ValueError("LLM base URL is required")
        return _validate_llm_base_url(value)

    @field_validator("bedrock_region")
    @classmethod
    def validate_bedrock_region(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_bedrock_region(value)


class LlmConfigDetail(BaseModel):
    id: UUID
    team_id: UUID
    revision_of_config_id: UUID | None = None
    label: str
    provider_preset: str
    adapter_kind: LlmAdapterKind
    base_url: str
    auth_mode: LlmAuthMode
    model_name: str | None
    available_models_json: list[str]
    inspection_metadata_json: dict[str, object]
    setup_status: LlmConfigSetupStatus
    provider_display_name: str
    setup_status_label: str | None = None
    is_active: bool
    has_secret: bool
    google_project_id: str | None = None
    google_location: str | None = None
    google_auth_method: str | None = None
    capacity_mode: str | None = None
    created_by_user_id: UUID
    updated_by_user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class LlmConfigDraftCreate(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    revision_of_config_id: UUID | None = None
    provider_preset: LlmProviderPreset = LlmProviderPreset.openai
    label: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str = Field(default="", max_length=2048)
    bearer_token: str | None = Field(default=None, min_length=1)
    bedrock_region: str | None = Field(default=None, max_length=64)
    provider_config_json: dict[str, object] = Field(default_factory=dict)
    google_project_id: str | None = None
    google_location: str | None = None
    google_auth_method: GoogleAuthMethod | None = None
    google_service_account_json: dict[str, object] | None = Field(default=None, repr=False)
    capacity_mode: GeminiCapacityMode = "auto"

    @model_validator(mode="before")
    @classmethod
    def apply_provider_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = _normalize_gemini_input(data)
        from app.services.llm_presets import apply_provider_defaults

        preset, _adapter_kind, base_url, bedrock_region = apply_provider_defaults(
            provider_preset=normalized.get("provider_preset"),
            base_url=normalized.get("base_url"),
            bedrock_region=normalized.get("bedrock_region"),
        )
        normalized["provider_preset"] = preset
        normalized["base_url"] = base_url
        normalized["bedrock_region"] = bedrock_region
        return normalized

    @model_validator(mode="after")
    def validate_gemini_fields(self) -> "LlmConfigDraftCreate":
        return _validate_gemini_input(self, credential_required=self.revision_of_config_id is None)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            raise ValueError("LLM base URL is required")
        return _validate_llm_base_url(value)

    @field_validator("bedrock_region")
    @classmethod
    def validate_bedrock_region(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_bedrock_region(value)


class LlmConfigDraftCreateResult(BaseModel):
    model_config = {"protected_namespaces": ()}

    config: LlmConfigDetail
    provider_display_name: str
    available_models: list[str] = Field(default_factory=list)
    available_model_options: list[LlmModelOption] = Field(default_factory=list)
    discovery_status: Literal["fetched", "manual_required", "failed"]
    default_model_source: Literal["provider", "manual", "none"]
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LlmConfigFinalize(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    config_id: UUID
    label: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class LlmConfigFinalizeBody(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    label: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class LlmConfigDraftReplaceCredential(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    config_id: UUID
    bearer_token: str | None = Field(default=None, min_length=1, repr=False)
    google_auth_method: GoogleAuthMethod | None = None
    google_service_account_json: dict[str, object] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_credential(self) -> "LlmConfigDraftReplaceCredential":
        if self.bearer_token is not None:
            if self.google_auth_method is not None or self.google_service_account_json is not None:
                raise ValueError("Bearer and Google credentials cannot be submitted together")
            return self
        if self.google_auth_method is None:
            raise ValueError("Replacement credential is required")
        if self.google_auth_method == "application_default":
            if self.google_service_account_json is not None:
                raise ValueError("Application Default Credentials cannot include credential JSON")
            return self
        if self.google_service_account_json is None:
            raise ValueError("Service-account authentication requires credential JSON")
        _validate_google_service_account_json(self.google_service_account_json)
        return self


class LlmSelectionUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID | None = None
    llm_config_id: UUID
    allowed_models_json: list[str] = Field(default_factory=list)
    model_name_override: str | None = Field(default=None, max_length=255)


class LlmSelectionDetail(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: UUID
    team_id: UUID
    llm_config_id: UUID
    selected_by_user_id: UUID
    selected_config_label: str
    selected_config_provider_preset: str
    selected_config_provider_display_name: str
    selected_config_adapter_kind: LlmAdapterKind
    selected_config_base_url: str
    provider_available_models_json: list[str]
    allowed_models_json: list[str]
    model_name_override: str | None
    resolved_model_name: str | None
    created_at: datetime
    updated_at: datetime


class HallucinationCheckSelectionUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    llm_config_id: UUID
    model_name_override: str | None = Field(default=None, max_length=255)


class HallucinationCheckSelectionDetail(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: UUID
    team_id: UUID
    llm_config_id: UUID
    selected_by_user_id: UUID
    selected_config_label: str
    selected_config_provider_preset: str
    selected_config_provider_display_name: str
    selected_config_adapter_kind: LlmAdapterKind
    selected_config_base_url: str
    provider_available_models_json: list[str]
    model_name_override: str | None
    resolved_model_name: str | None
    created_at: datetime
    updated_at: datetime


class UserLlmPreferenceUpsert(BaseModel):
    model_config = {"protected_namespaces": ()}

    preferred_model_name: str | None = Field(default=None, max_length=255)


class UserLlmPreferenceDetail(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: UUID
    user_id: UUID
    preferred_model_name: str | None
    resolved_model_name: str | None
    allowed_models_json: list[str]
    created_at: datetime
    updated_at: datetime


class LlmInspectRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID | None = None
    provider_preset: LlmProviderPreset = LlmProviderPreset.openai
    adapter_kind: LlmAdapterKind = LlmAdapterKind.openai_chat
    base_url: str = Field(default="", max_length=2048)
    bearer_token: str | None = Field(default=None, min_length=1)
    bedrock_region: str | None = Field(default=None, max_length=64)
    provider_config_json: dict[str, object] = Field(default_factory=dict)
    google_project_id: str | None = None
    google_location: str | None = None
    google_auth_method: GoogleAuthMethod | None = None
    google_service_account_json: dict[str, object] | None = Field(default=None, repr=False)
    capacity_mode: GeminiCapacityMode = "auto"

    @model_validator(mode="before")
    @classmethod
    def apply_provider_defaults(cls, data: object, info: ValidationInfo) -> object:
        if not isinstance(data, dict):
            return data
        normalized = _normalize_gemini_input(data)
        from app.services.llm_presets import apply_provider_defaults

        preset, adapter_kind, base_url, bedrock_region = apply_provider_defaults(
            provider_preset=normalized.get("provider_preset"),
            base_url=normalized.get("base_url"),
            bedrock_region=normalized.get("bedrock_region"),
            adapter_kind=normalized.get("adapter_kind"),
            allow_disabled_provider=bool((info.context or {}).get("allow_disabled_provider")),
        )
        normalized["provider_preset"] = preset
        normalized["adapter_kind"] = adapter_kind
        normalized["base_url"] = base_url
        normalized["bedrock_region"] = bedrock_region
        return normalized

    @model_validator(mode="after")
    def validate_gemini_fields(self) -> "LlmInspectRequest":
        return _validate_gemini_input(self, credential_required=True)

    @classmethod
    def from_persisted_config(
        cls,
        config,
        *,
        bearer_token: str | None = None,
        google_service_account_json: dict[str, object] | None = None,
        google_auth_method: GoogleAuthMethod | None = None,
    ) -> "LlmInspectRequest":
        from app.services.llm_presets import infer_llm_provider_preset

        provider_config = dict(config.provider_config_json or {})
        return cls.model_validate(
            {
                "team_id": config.team_id,
                "provider_preset": config.provider_preset
                or infer_llm_provider_preset(config.adapter_kind, config.base_url),
                "adapter_kind": config.adapter_kind,
                "base_url": config.base_url,
                "bearer_token": bearer_token,
                "provider_config_json": provider_config,
                "google_project_id": provider_config.get("project_id"),
                "google_location": provider_config.get("location"),
                "google_auth_method": google_auth_method
                or (
                    "application_default"
                    if config.auth_mode is LlmAuthMode.google_adc
                    else "service_account_json" if config.auth_mode is LlmAuthMode.google_service_account else None
                ),
                "google_service_account_json": google_service_account_json,
                "capacity_mode": provider_config.get("capacity_mode", "auto"),
            },
            context={"allow_disabled_provider": True},
        )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            raise ValueError("LLM base URL is required")
        return _validate_llm_base_url(value)

    @field_validator("bedrock_region")
    @classmethod
    def validate_inspect_bedrock_region(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_bedrock_region(value)


class LlmConfigInspectResult(BaseModel):
    provider_preset: str
    provider_display_name: str
    base_url: str
    adapter_kind: LlmAdapterKind
    model_name: str | None
    available_models: list[str] = Field(default_factory=list)
    available_model_options: list[LlmModelOption] = Field(default_factory=list)
    discovery_status: Literal["fetched", "manual_required", "failed"]
    default_model_source: Literal["provider", "manual", "none"]
    requires_bearer_token: bool
    supports_model_discovery: bool
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    google_project_id: str | None = None
    google_location: str | None = None
    google_auth_method: str | None = None
    capacity_mode: str | None = None

    model_config = {"protected_namespaces": ()}
