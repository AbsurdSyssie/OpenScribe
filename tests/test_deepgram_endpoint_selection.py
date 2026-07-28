from pathlib import Path

import pytest

from app.models import SttAdapterKind, SttProviderPreset
from app.services.stt_presets import (
    DEEPGRAM_EU_BASE_URL,
    DEEPGRAM_GLOBAL_BASE_URL,
    apply_stt_provider_defaults,
    get_stt_provider_preset,
    infer_stt_provider_preset,
    is_deepgram_stt_base_url,
)


@pytest.mark.parametrize("base_url", [DEEPGRAM_EU_BASE_URL, DEEPGRAM_GLOBAL_BASE_URL])
def test_deepgram_hosts_are_recognised(base_url: str) -> None:
    assert is_deepgram_stt_base_url(base_url)
    assert (
        infer_stt_provider_preset(SttAdapterKind.generic_rest, base_url)
        == SttProviderPreset.deepgram.value
    )


def test_deepgram_preset_defaults_to_eu_endpoint() -> None:
    preset = get_stt_provider_preset(SttProviderPreset.deepgram)

    assert preset.default_base_url == DEEPGRAM_EU_BASE_URL
    assert preset.default_extra_form_fields == {
        "smart_format": "true",
        "mip_opt_out": "true",
    }

    preset_key, adapter_kind, base_url, resolved_preset = apply_stt_provider_defaults(
        provider_preset=SttProviderPreset.deepgram,
        base_url=None,
    )

    assert preset_key == SttProviderPreset.deepgram.value
    assert adapter_kind is SttAdapterKind.generic_rest
    assert base_url == DEEPGRAM_EU_BASE_URL
    assert resolved_preset is preset


def test_deepgram_global_endpoint_remains_selectable() -> None:
    preset_key, adapter_kind, base_url, _ = apply_stt_provider_defaults(
        provider_preset=SttProviderPreset.deepgram,
        base_url=DEEPGRAM_GLOBAL_BASE_URL,
    )

    assert preset_key == SttProviderPreset.deepgram.value
    assert adapter_kind is SttAdapterKind.generic_rest
    assert base_url == DEEPGRAM_GLOBAL_BASE_URL


def test_admin_wizard_requires_global_endpoint_acknowledgement() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "app/static/js/deepgram-endpoint-selection.js").read_text()
    csrf_include = (repo_root / "app/templates/_csrf_script.html").read_text()

    assert 'new Option("EU endpoint (recommended)", "eu", true, true)' in script
    assert 'new Option("Global endpoint", "global")' in script
    assert "Are you sure the Global endpoint is compliant?" in script
    assert "stt-deepgram-global-acknowledgement" in script
    assert "event.stopImmediatePropagation()" in script
    assert "/static/js/deepgram-endpoint-selection.js" in csrf_include
