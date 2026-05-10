from dataclasses import dataclass
from urllib.parse import urlparse

from app.llm_provider_defaults import (
    DEFAULT_BEDROCK_CHAT_REGION,
    OLLAMA_CHAT_BASE_URL,
    OPENAI_CHAT_BASE_URL,
    bedrock_chat_base_url,
    bedrock_region_from_base_url,
)
from app.models import LlmAdapterKind, LlmProviderPreset


BEDROCK_HTTP_GATEWAY_REGIONS = [
    "eu-west-2",
    "eu-west-1",
    "eu-central-1",
    "us-east-1",
    "us-west-2",
    "ap-southeast-1",
    "ap-southeast-2",
]


@dataclass(frozen=True)
class LlmProviderPresetDefinition:
    key: str
    display_name: str
    adapter_kind: LlmAdapterKind
    default_base_url: str | None
    requires_bearer_token: bool
    supports_model_discovery: bool
    allow_manual_model: bool
    advanced: bool = False
    default_bedrock_region: str | None = None
    help_text: str = ""


LLM_PROVIDER_PRESETS: dict[str, LlmProviderPresetDefinition] = {
    LlmProviderPreset.openai.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.openai.value,
        display_name="OpenAI",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url=OPENAI_CHAT_BASE_URL,
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.openrouter.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.openrouter.value,
        display_name="OpenRouter",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url="https://openrouter.ai/api/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.xai.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.xai.value,
        display_name="xAI",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url="https://api.x.ai/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.groq.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.groq.value,
        display_name="Groq",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url="https://api.groq.com/openai/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.mistral.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.mistral.value,
        display_name="Mistral",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url="https://api.mistral.ai/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.deepseek.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.deepseek.value,
        display_name="DeepSeek",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url="https://api.deepseek.com",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.together.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.together.value,
        display_name="Together AI",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url="https://api.together.xyz/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.ollama.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.ollama.value,
        display_name="Ollama",
        adapter_kind=LlmAdapterKind.ollama_chat,
        default_base_url=OLLAMA_CHAT_BASE_URL,
        requires_bearer_token=False,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    LlmProviderPreset.bedrock_http_gateway.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.bedrock_http_gateway.value,
        display_name="Bedrock HTTP gateway",
        adapter_kind=LlmAdapterKind.bedrock_chat,
        default_base_url=None,
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
        default_bedrock_region=DEFAULT_BEDROCK_CHAT_REGION,
    ),
    LlmProviderPreset.custom_openai_compatible.value: LlmProviderPresetDefinition(
        key=LlmProviderPreset.custom_openai_compatible.value,
        display_name="Custom OpenAI-compatible · advanced",
        adapter_kind=LlmAdapterKind.openai_chat,
        default_base_url=None,
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
        advanced=True,
    ),
}

BRANDED_OPENAI_COMPATIBLE_PRESETS = {
    LlmProviderPreset.openai.value,
    LlmProviderPreset.openrouter.value,
    LlmProviderPreset.xai.value,
    LlmProviderPreset.groq.value,
    LlmProviderPreset.mistral.value,
    LlmProviderPreset.deepseek.value,
    LlmProviderPreset.together.value,
}


def get_llm_provider_preset(key: str | LlmProviderPreset | None) -> LlmProviderPresetDefinition:
    preset_key = (key.value if isinstance(key, LlmProviderPreset) else key) or LlmProviderPreset.openai.value
    try:
        return LLM_PROVIDER_PRESETS[preset_key]
    except KeyError as exc:
        raise ValueError("Unsupported LLM provider preset") from exc


def default_llm_config_label(*, provider_display_name: str, team_name: str) -> str:
    return f"{provider_display_name} · {team_name}"


def infer_llm_provider_preset(adapter_kind: str | LlmAdapterKind, base_url: str | None) -> str:
    adapter_value = adapter_kind.value if isinstance(adapter_kind, LlmAdapterKind) else str(adapter_kind or "")
    host = (urlparse(base_url or "").hostname or "").lower()
    if adapter_value == LlmAdapterKind.ollama_chat.value:
        return LlmProviderPreset.ollama.value
    if adapter_value == LlmAdapterKind.bedrock_chat.value:
        return LlmProviderPreset.bedrock_http_gateway.value
    if adapter_value == LlmAdapterKind.openai_chat.value:
        if host == "api.openai.com":
            return LlmProviderPreset.openai.value
        if host.endswith("openrouter.ai"):
            return LlmProviderPreset.openrouter.value
        if host == "api.x.ai":
            return LlmProviderPreset.xai.value
        if host == "api.groq.com":
            return LlmProviderPreset.groq.value
        if host == "api.deepseek.com":
            return LlmProviderPreset.deepseek.value
        if host == "api.mistral.ai":
            return LlmProviderPreset.mistral.value
        if host in {"api.together.xyz", "api.together.ai"}:
            return LlmProviderPreset.together.value
    return LlmProviderPreset.custom_openai_compatible.value


def _preset_from_legacy_adapter(adapter_kind: str | LlmAdapterKind | None) -> str:
    adapter_value = adapter_kind.value if isinstance(adapter_kind, LlmAdapterKind) else str(adapter_kind or "")
    if adapter_value == LlmAdapterKind.bedrock_chat.value:
        return LlmProviderPreset.bedrock_http_gateway.value
    if adapter_value == LlmAdapterKind.ollama_chat.value:
        return LlmProviderPreset.ollama.value
    return LlmProviderPreset.openai.value


def apply_provider_defaults(
    *,
    provider_preset: str | LlmProviderPreset | None,
    base_url: str | None,
    bedrock_region: str | None,
    adapter_kind: str | LlmAdapterKind | None = None,
) -> tuple[str, LlmAdapterKind, str, str | None]:
    preset = get_llm_provider_preset(provider_preset or _preset_from_legacy_adapter(adapter_kind))
    resolved_base_url = (base_url or "").strip()
    resolved_region = bedrock_region
    if preset.key == LlmProviderPreset.bedrock_http_gateway.value:
        if resolved_base_url and not bedrock_region:
            resolved_region = bedrock_region_from_base_url(resolved_base_url)
        resolved_region = (resolved_region or preset.default_bedrock_region or DEFAULT_BEDROCK_CHAT_REGION).strip()
        if not resolved_base_url:
            resolved_base_url = bedrock_chat_base_url(resolved_region)
    elif preset.default_base_url and not resolved_base_url:
        resolved_base_url = preset.default_base_url
    return preset.key, preset.adapter_kind, resolved_base_url, resolved_region


def reclassify_preset_for_base_url(provider_preset: str, base_url: str) -> str:
    preset = get_llm_provider_preset(provider_preset)
    if provider_preset in BRANDED_OPENAI_COMPATIBLE_PRESETS and preset.default_base_url:
        if base_url.rstrip("/") != preset.default_base_url.rstrip("/"):
            return LlmProviderPreset.custom_openai_compatible.value
    if provider_preset == LlmProviderPreset.bedrock_http_gateway.value and bedrock_region_from_base_url(base_url) is None:
        return LlmProviderPreset.custom_openai_compatible.value
    return provider_preset


def filter_discovered_models(provider_preset: str, model_ids: list[str]) -> list[str]:
    blocked_tokens = ("embedding", "transcribe", "whisper", "tts", "moderation", "image")
    filtered = []
    for model_id in model_ids:
        lower = model_id.lower()
        if any(token in lower for token in blocked_tokens):
            continue
        filtered.append(model_id)
    if provider_preset == LlmProviderPreset.openai.value:
        return sorted({model_id for model_id in filtered if model_id.lower().startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))})
    return sorted(set(filtered))
