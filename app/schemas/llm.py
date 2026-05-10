from datetime import datetime
import ipaddress
from typing import Literal
from uuid import UUID
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import LlmAdapterKind, LlmAuthMode, LlmProviderPreset


OPENAI_CHAT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_BEDROCK_CHAT_REGION = "eu-west-2"
OLLAMA_CHAT_BASE_URL = "http://localhost:11434"


def _normalize_bedrock_region(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Bedrock region is required")
    if not normalized.replace("-", "").isalnum():
        raise ValueError("Bedrock region must contain only letters, numbers, and hyphens")
    return normalized


def bedrock_chat_base_url(region: str) -> str:
    return f"https://bedrock-mantle.{_normalize_bedrock_region(region)}.api.aws/v1"


def bedrock_region_from_base_url(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    prefix = "bedrock-mantle."
    suffix = ".api.aws"
    if host.startswith(prefix) and host.endswith(suffix):
        candidate = host[len(prefix) : -len(suffix)]
        if candidate:
            return candidate
    return None


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
    model_name: str | None = Field(default=None, max_length=255)
    inspection_metadata_json: dict[str, object] | None = None
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def apply_provider_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        preset = normalized.get("provider_preset")
        if not preset:
            adapter_kind = normalized.get("adapter_kind", LlmAdapterKind.openai_chat)
            if isinstance(adapter_kind, str):
                adapter_kind = LlmAdapterKind(adapter_kind)
            if adapter_kind is LlmAdapterKind.bedrock_chat:
                preset = LlmProviderPreset.bedrock_http_gateway.value
            elif adapter_kind is LlmAdapterKind.ollama_chat:
                preset = LlmProviderPreset.ollama.value
            else:
                preset = LlmProviderPreset.openai.value
        if isinstance(preset, LlmProviderPreset):
            preset = preset.value
        normalized["provider_preset"] = preset
        if preset == LlmProviderPreset.bedrock_http_gateway.value:
            normalized["adapter_kind"] = LlmAdapterKind.bedrock_chat
            region = normalized.get("bedrock_region")
            if isinstance(region, str) and region.strip():
                normalized["bedrock_region"] = _normalize_bedrock_region(region)
            elif isinstance(normalized.get("base_url"), str) and normalized.get("base_url", "").strip():
                normalized["bedrock_region"] = bedrock_region_from_base_url(str(normalized["base_url"]).strip())
            else:
                normalized["bedrock_region"] = DEFAULT_BEDROCK_CHAT_REGION
            normalized["base_url"] = (
                normalized.get("base_url")
                or bedrock_chat_base_url(str(normalized["bedrock_region"] or DEFAULT_BEDROCK_CHAT_REGION))
            ).strip()
        elif preset == LlmProviderPreset.ollama.value:
            normalized["adapter_kind"] = LlmAdapterKind.ollama_chat
            normalized["base_url"] = (normalized.get("base_url") or OLLAMA_CHAT_BASE_URL).strip()
        else:
            normalized["adapter_kind"] = LlmAdapterKind.openai_chat
            default_base_urls = {
                LlmProviderPreset.openai.value: OPENAI_CHAT_BASE_URL,
                LlmProviderPreset.openrouter.value: "https://openrouter.ai/api/v1",
                LlmProviderPreset.xai.value: "https://api.x.ai/v1",
                LlmProviderPreset.groq.value: "https://api.groq.com/openai/v1",
                LlmProviderPreset.mistral.value: "https://api.mistral.ai/v1",
                LlmProviderPreset.deepseek.value: "https://api.deepseek.com",
                LlmProviderPreset.together.value: "https://api.together.xyz/v1",
            }
            normalized["base_url"] = (normalized.get("base_url") or default_base_urls.get(str(preset), "")).strip()
        return normalized

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
        return _normalize_bedrock_region(value)

    @model_validator(mode="after")
    def validate_model_name(self):
        if self.adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat, LlmAdapterKind.ollama_chat} and not self.model_name:
            raise ValueError("Model name is required for LLM adapters")
        return self


class LlmConfigDetail(BaseModel):
    id: UUID
    team_id: UUID
    label: str
    provider_preset: str
    adapter_kind: LlmAdapterKind
    base_url: str
    auth_mode: LlmAuthMode
    model_name: str | None
    available_models_json: list[str]
    inspection_metadata_json: dict[str, object]
    is_active: bool
    has_secret: bool
    created_by_user_id: UUID
    updated_by_user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


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
    selected_config_adapter_kind: LlmAdapterKind
    selected_config_base_url: str
    provider_available_models_json: list[str]
    allowed_models_json: list[str]
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

    @model_validator(mode="before")
    @classmethod
    def apply_provider_defaults(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        preset = normalized.get("provider_preset")
        if not preset:
            adapter_kind = normalized.get("adapter_kind", LlmAdapterKind.openai_chat)
            if isinstance(adapter_kind, str):
                adapter_kind = LlmAdapterKind(adapter_kind)
            if adapter_kind is LlmAdapterKind.bedrock_chat:
                preset = LlmProviderPreset.bedrock_http_gateway.value
            elif adapter_kind is LlmAdapterKind.ollama_chat:
                preset = LlmProviderPreset.ollama.value
            else:
                preset = LlmProviderPreset.openai.value
        if isinstance(preset, LlmProviderPreset):
            preset = preset.value
        normalized["provider_preset"] = preset
        if preset == LlmProviderPreset.bedrock_http_gateway.value:
            normalized["adapter_kind"] = LlmAdapterKind.bedrock_chat
            region = normalized.get("bedrock_region")
            if isinstance(region, str) and region.strip():
                normalized["bedrock_region"] = _normalize_bedrock_region(region)
            elif isinstance(normalized.get("base_url"), str) and normalized.get("base_url", "").strip():
                normalized["bedrock_region"] = bedrock_region_from_base_url(str(normalized["base_url"]).strip())
            else:
                normalized["bedrock_region"] = DEFAULT_BEDROCK_CHAT_REGION
            normalized["base_url"] = (
                normalized.get("base_url")
                or bedrock_chat_base_url(str(normalized["bedrock_region"] or DEFAULT_BEDROCK_CHAT_REGION))
            ).strip()
        elif preset == LlmProviderPreset.ollama.value:
            normalized["adapter_kind"] = LlmAdapterKind.ollama_chat
            normalized["base_url"] = (normalized.get("base_url") or OLLAMA_CHAT_BASE_URL).strip()
        else:
            normalized["adapter_kind"] = LlmAdapterKind.openai_chat
            default_base_urls = {
                LlmProviderPreset.openai.value: OPENAI_CHAT_BASE_URL,
                LlmProviderPreset.openrouter.value: "https://openrouter.ai/api/v1",
                LlmProviderPreset.xai.value: "https://api.x.ai/v1",
                LlmProviderPreset.groq.value: "https://api.groq.com/openai/v1",
                LlmProviderPreset.mistral.value: "https://api.mistral.ai/v1",
                LlmProviderPreset.deepseek.value: "https://api.deepseek.com",
                LlmProviderPreset.together.value: "https://api.together.xyz/v1",
            }
            normalized["base_url"] = (normalized.get("base_url") or default_base_urls.get(str(preset), "")).strip()
        return normalized

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
        return _normalize_bedrock_region(value)


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

    model_config = {"protected_namespaces": ()}
