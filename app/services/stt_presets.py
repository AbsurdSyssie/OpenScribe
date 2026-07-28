from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from app.models import SttAdapterKind, SttProviderPreset


DEEPGRAM_EU_BASE_URL = "https://api.eu.deepgram.com"
DEEPGRAM_GLOBAL_BASE_URL = "https://api.deepgram.com"
DEEPGRAM_HOSTS = frozenset(
    {
        urlparse(DEEPGRAM_EU_BASE_URL).hostname,
        urlparse(DEEPGRAM_GLOBAL_BASE_URL).hostname,
    }
)


@dataclass(frozen=True)
class SttProviderPresetDefinition:
    key: str
    display_name: str
    adapter_kind: SttAdapterKind
    default_base_url: str | None
    transcribe_path: str
    auth_header_style: Literal["bearer", "token", "xi-api-key", "none"]
    requires_api_key: bool
    supports_model_discovery: bool
    supports_language_selection: bool
    supports_diarization: bool
    advanced: bool = False
    default_model_name: str | None = None
    default_model_field_name: str | None = "model"
    default_file_field_name: str = "file"
    default_language_field_name: str | None = "language"
    default_response_text_path: str = "text"
    default_extra_form_fields: dict[str, str] | None = None
    help_text: str = ""


STT_PROVIDER_PRESETS: dict[str, SttProviderPresetDefinition] = {
    SttProviderPreset.openai.value: SttProviderPresetDefinition(
        key=SttProviderPreset.openai.value,
        display_name="OpenAI",
        adapter_kind=SttAdapterKind.openai_cloud,
        default_base_url="https://api.openai.com/v1",
        transcribe_path="/v1/audio/transcriptions",
        auth_header_style="bearer",
        requires_api_key=True,
        supports_model_discovery=True,
        supports_language_selection=True,
        supports_diarization=True,
        default_response_text_path="text",
    ),
    SttProviderPreset.deepgram.value: SttProviderPresetDefinition(
        key=SttProviderPreset.deepgram.value,
        display_name="Deepgram",
        adapter_kind=SttAdapterKind.generic_rest,
        default_base_url=DEEPGRAM_EU_BASE_URL,
        transcribe_path="/v1/listen",
        auth_header_style="token",
        requires_api_key=True,
        supports_model_discovery=True,
        supports_language_selection=True,
        supports_diarization=True,
        default_model_name=None,
        default_response_text_path="results.channels.0.alternatives.0.transcript",
        default_extra_form_fields={"smart_format": "true", "mip_opt_out": "true"},
        help_text="EU routing is the default. The global endpoint requires a separate local compliance assessment.",
    ),
    SttProviderPreset.elevenlabs.value: SttProviderPresetDefinition(
        key=SttProviderPreset.elevenlabs.value,
        display_name="ElevenLabs",
        adapter_kind=SttAdapterKind.elevenlabs_speech_to_text,
        default_base_url="https://api.elevenlabs.io",
        transcribe_path="/v1/speech-to-text",
        auth_header_style="xi-api-key",
        requires_api_key=True,
        supports_model_discovery=True,
        supports_language_selection=True,
        supports_diarization=True,
        default_model_name=None,
        default_model_field_name="model_id",
        default_file_field_name="file",
        default_language_field_name="language_code",
        default_response_text_path="text",
    ),
    SttProviderPreset.custom_openai_compatible.value: SttProviderPresetDefinition(
        key=SttProviderPreset.custom_openai_compatible.value,
        display_name="Custom OpenAI-compatible · advanced",
        adapter_kind=SttAdapterKind.openai_compatible_rest,
        default_base_url=None,
        transcribe_path="/v1/audio/transcriptions",
        auth_header_style="bearer",
        requires_api_key=True,
        supports_model_discovery=True,
        supports_language_selection=True,
        supports_diarization=False,
        advanced=True,
        default_response_text_path="text",
    ),
    SttProviderPreset.custom_rest_openapi.value: SttProviderPresetDefinition(
        key=SttProviderPreset.custom_rest_openapi.value,
        display_name="Custom REST/OpenAPI · advanced",
        adapter_kind=SttAdapterKind.generic_rest,
        default_base_url=None,
        transcribe_path="/v1/audio/transcriptions",
        auth_header_style="bearer",
        requires_api_key=False,
        supports_model_discovery=False,
        supports_language_selection=True,
        supports_diarization=False,
        advanced=True,
        default_response_text_path="text",
    ),
}


def get_stt_provider_preset(key: str | SttProviderPreset | None) -> SttProviderPresetDefinition:
    preset_key = (key.value if isinstance(key, SttProviderPreset) else key) or SttProviderPreset.custom_rest_openapi.value
    try:
        return STT_PROVIDER_PRESETS[preset_key]
    except KeyError as exc:
        raise ValueError("Unsupported STT provider preset") from exc


def default_stt_config_label(*, provider_display_name: str, team_name: str) -> str:
    return f"{provider_display_name} · {team_name}"


def is_deepgram_stt_base_url(base_url: str | None) -> bool:
    return (urlparse(base_url or "").hostname or "").lower() in DEEPGRAM_HOSTS


def infer_stt_provider_preset(
    adapter_kind: str | SttAdapterKind,
    base_url: str | None,
    *,
    prefer_known_hosts: bool = True,
) -> str:
    adapter_value = adapter_kind.value if isinstance(adapter_kind, SttAdapterKind) else str(adapter_kind or "")
    host = (urlparse(base_url or "").hostname or "").lower()
    if adapter_value == SttAdapterKind.openai_cloud.value:
        return SttProviderPreset.openai.value
    if adapter_value == SttAdapterKind.elevenlabs_speech_to_text.value:
        return SttProviderPreset.elevenlabs.value
    if prefer_known_hosts and host in DEEPGRAM_HOSTS:
        return SttProviderPreset.deepgram.value
    if host == "api.elevenlabs.io":
        return SttProviderPreset.elevenlabs.value
    if adapter_value == SttAdapterKind.openai_compatible_rest.value:
        return SttProviderPreset.custom_openai_compatible.value
    return SttProviderPreset.custom_rest_openapi.value


def resolve_stt_provider_preset(
    provider_preset: str | SttProviderPreset | None,
    adapter_kind: str | SttAdapterKind,
    base_url: str | None,
) -> str:
    preset_value = provider_preset.value if isinstance(provider_preset, SttProviderPreset) else provider_preset
    inferred = infer_stt_provider_preset(adapter_kind, base_url, prefer_known_hosts=False)
    return preset_value or inferred


def apply_stt_provider_defaults(
    *,
    provider_preset: str | SttProviderPreset | None,
    base_url: str | None,
    adapter_kind: str | SttAdapterKind | None = None,
) -> tuple[str, SttAdapterKind, str, SttProviderPresetDefinition]:
    preset = get_stt_provider_preset(provider_preset or infer_stt_provider_preset(adapter_kind or SttAdapterKind.generic_rest, base_url))
    resolved_base_url = (base_url or "").strip()
    if preset.default_base_url and not resolved_base_url:
        resolved_base_url = preset.default_base_url
    return preset.key, preset.adapter_kind, resolved_base_url, preset
