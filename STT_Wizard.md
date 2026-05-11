Current state

The STT wizard branch has the right broad architecture:

STT provider presets exist.
STT setup status exists.
Draft/finalize/replace-credential schemas and routes exist.
Deepgram discovery is implemented.
Admin templates have been updated to show discovered model dropdowns.
STT selection excludes pending configs.
STT labels are unique per team.

The branch currently defines ElevenLabs as:

SttProviderPreset.elevenlabs.value: SttProviderPresetDefinition(
    key=SttProviderPreset.elevenlabs.value,
    display_name="ElevenLabs",
    adapter_kind=SttAdapterKind.generic_rest,
    default_base_url="https://api.elevenlabs.io",
    transcribe_path="/v1/speech-to-text",
    auth_header_style="xi-api-key",
    requires_api_key=True,
    supports_model_discovery=False,
    supports_language_selection=True,
    supports_diarization=True,
    default_model_name="scribe_v2",
    default_model_field_name="model_id",
    default_response_text_path="text",
)

So the admin UI can appear to “discover” scribe_v2, but it is currently a preset default, not live discovery.

The current goal is to fix four related areas:

Add real ElevenLabs model discovery.
Fix ElevenLabs runtime transcription auth/request shape.
Normalize default/auto language values so "None" is never sent.
Ensure admin “test provider” uses the exact same saved config/runtime path as production transcription.
1. Product/technical decisions
ElevenLabs model discovery

Add real ElevenLabs model discovery via:

GET https://api.elevenlabs.io/v1/models
xi-api-key: <api key>

Filter the returned models to synchronous STT models only:

ELEVENLABS_SYNC_STT_MODEL_IDS = {
    "scribe_v2",
    "scribe_v1",
}

Do not include:

"scribe_v2_realtime"

in this provider dropdown yet. That belongs to ElevenLabs realtime/WebSocket STT, while OpenScribe’s current STT provider path is synchronous direct file upload.

ElevenLabs runtime transcription

Runtime transcription must call:

POST https://api.elevenlabs.io/v1/speech-to-text
xi-api-key: <api key>
Content-Type: multipart/form-data

with multipart fields:

file=<audio file>
model_id=scribe_v2 or selected model

Optional language must be sent as:

language_code=<language>

Do not send:

Authorization: Bearer <key>

for ElevenLabs.

A 401 during transcription while the account has credits strongly suggests the current runtime path is using the wrong auth header or wrong request shape. If this were a billing/credits issue, a 402-style payment error would be more plausible than 401.

Language default

Provider default / auto-detect language must be represented as Python None.

Never save or send literal values like:

None
none
null
undefined
auto
default
provider_default

The UI may display:

Provider default / auto-detect

but the submitted HTML value must be:

<option value="">Provider default / auto-detect</option>

not:

<option value="None">None</option>
2. Update ElevenLabs preset

In app/services/stt_presets.py, change the ElevenLabs preset to:

SttProviderPreset.elevenlabs.value: SttProviderPresetDefinition(
    key=SttProviderPreset.elevenlabs.value,
    display_name="ElevenLabs",
    adapter_kind=SttAdapterKind.generic_rest,
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
)

Important changes:

supports_model_discovery: False -> True
default_model_name: "scribe_v2" -> None
default_language_field_name: "language" -> "language_code"

scribe_v2 should become the preferred discovered model, not the only preset-saved option.

3. Add shared STT normalization helpers

Add near the top of app/services/stt.py, or in a small shared STT utility module if preferred.

_STT_OPTIONAL_SENTINELS = {
    "none",
    "null",
    "undefined",
    "auto",
    "default",
    "provider_default",
}


def normalize_optional_stt_text(value: str | None) -> str | None:
    if value is None:
        return None

    trimmed = str(value).strip()
    if not trimmed:
        return None

    if trimmed.lower() in _STT_OPTIONAL_SENTINELS:
        return None

    return trimmed


def normalize_stt_language(value: str | None) -> str | None:
    return normalize_optional_stt_text(value)

Use this at every input and runtime boundary.

4. Apply normalization in schemas

In app/schemas/stt.py, apply normalization to:

SttConfigUpsert.language
SttConfigFinalize.language
SttSelectionUpsert.language_override

Example:

from app.services.stt_normalization import normalize_stt_language

or, if avoiding service import cycles, put the helper in app/schemas/stt.py or a neutral module such as:

app/stt_normalization.py

Recommended neutral module:

app/stt_normalization.py

Then:

@field_validator("language")
@classmethod
def normalize_language(cls, value: str | None) -> str | None:
    return normalize_stt_language(value)

For SttSelectionUpsert:

@field_validator("language_override")
@classmethod
def normalize_language_override(cls, value: str | None) -> str | None:
    return normalize_stt_language(value)

Also include language in any existing blank-optional-field handling if keeping local validators.

5. Apply normalization in browser routes

Where admin routes currently do:

language=language or None

change to:

language=normalize_stt_language(language)

Where selection routes do:

language_override=language_override or None

change to:

language_override=normalize_stt_language(language_override)

Do this for:

admin_upsert_stt_config
admin_finalize_stt_config_draft
team/user STT selection routes if they accept language override
any admin test route if it accepts language-like form fields

Even if Pydantic also normalizes, normalize at form boundary for clearer intent.

6. Add ElevenLabs model discovery helper

In app/services/stt.py:

ELEVENLABS_SYNC_STT_MODEL_IDS = {
    "scribe_v2",
    "scribe_v1",
}

ELEVENLABS_PREFERRED_STT_MODELS = (
    "scribe_v2",
    "scribe_v1",
)


def _elevenlabs_model_sort_key(model_id: str) -> tuple[int, str]:
    try:
        return (ELEVENLABS_PREFERRED_STT_MODELS.index(model_id), model_id)
    except ValueError:
        return (999, model_id)


def _preferred_elevenlabs_model(models: list[str]) -> str | None:
    for model in ELEVENLABS_PREFERRED_STT_MODELS:
        if model in models:
            return model
    return models[0] if models else None


def _list_elevenlabs_stt_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/v1/models",
            headers={"xi-api-key": api_key},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {401, 403}:
            raise AppError(
                401,
                "stt_credential_invalid",
                "The API key was rejected by ElevenLabs.",
                {"provider_status": status_code},
            ) from exc
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not load available ElevenLabs STT models",
            {"provider_status": status_code},
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not reach ElevenLabs model discovery",
            {
                "provider_error_code": _safe_http_error_details(exc).get(
                    "provider_error_code"
                )
            },
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "ElevenLabs model discovery returned invalid JSON",
        ) from exc

    if not isinstance(payload, list):
        return []

    discovered: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        model_id = item.get("model_id")
        if not isinstance(model_id, str):
            continue

        model_id = model_id.strip()
        if model_id in ELEVENLABS_SYNC_STT_MODEL_IDS:
            discovered.append(model_id)

    return sorted(set(discovered), key=_elevenlabs_model_sort_key)
7. Wire ElevenLabs discovery into STT inspection

Find the provider-preset inspection branch, alongside OpenAI and Deepgram handling.

Add:

if preset.key == SttProviderPreset.elevenlabs.value:
    if not bearer_token:
        raise AppError(
            422,
            "business_rule_violation",
            "ElevenLabs requires an API key",
            {"field": "bearer_token"},
        )

    models = _list_elevenlabs_stt_models(
        api_key=bearer_token,
        base_url=base_url,
    )

    return SttInspectResult(
        base_url=base_url,
        openapi_path=None,
        adapter_kind=preset.adapter_kind,
        transcribe_path=preset.transcribe_path,
        model_name=_preferred_elevenlabs_model(models),
        model_field_name="model_id" if models else None,
        file_field_name=preset.default_file_field_name,
        language=None,
        language_field_name="language_code",
        response_text_path=preset.default_response_text_path,
        segments_path="words",
        segment_text_field="text",
        segment_start_field="start",
        segment_end_field="end",
        segment_speaker_field="speaker_id",
        extra_form_fields_json=dict(preset.default_extra_form_fields or {}),
        candidate_paths=[preset.transcribe_path, "/v1/models"],
        operation_summary="ElevenLabs Speech to Text",
        available_models=models,
        available_model_options=_stt_model_options(models, source="provider"),
        field_tips=[],
        notes=[
            "Discovered ElevenLabs synchronous Speech-to-Text models from /v1/models.",
        ],
    )
Draft persistence rule

For the wizard, draft configs should remain:

setup_status = pending_model_selection
model_name = None
available_models_json = discovered_models

The inspection result may include model_name=_preferred_elevenlabs_model(models) to preselect the dropdown, but final model choice should still happen at finalize/save.

If current draft code copies inspection.model_name into config.model_name, adjust draft creation so pending drafts keep model_name=None.

8. Add ElevenLabs runtime transcription branch
Problem

ElevenLabs is currently at risk of being handled by the generic multipart path. The generic path likely uses:

Authorization: Bearer <key>

That is wrong for ElevenLabs and causes 401.

Add provider-specific helper
def _transcribe_via_elevenlabs(
    *,
    url: str,
    api_key: str | None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    model_name: str | None,
    language: str | None,
    response_text_path: str,
    segments_path: str | None = None,
    segment_text_field: str | None = None,
    segment_start_field: str | None = None,
    segment_end_field: str | None = None,
    segment_speaker_field: str | None = None,
) -> str:
    if not api_key:
        raise AppError(
            409,
            "stt_config_secret_missing",
            "ElevenLabs STT requires a saved API key.",
        )

    language = normalize_stt_language(language)

    data: dict[str, str] = {
        "model_id": model_name or "scribe_v2",
    }

    if language:
        data["language_code"] = language

    try:
        response = httpx.post(
            url,
            headers={"xi-api-key": api_key},
            data=data,
            files={
                "file": (
                    filename,
                    audio_bytes,
                    content_type or "application/octet-stream",
                )
            },
            timeout=60.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "stt_elevenlabs_request_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "provider_preset": SttProviderPreset.elevenlabs.value,
                    "form_field_keys": sorted(data.keys()),
                    "audio_byte_count": len(audio_bytes),
                    "content_type": content_type,
                }
            },
        )
        raise _translate_http_stt_error(exc) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502,
            "stt_response_invalid",
            "ElevenLabs response was not valid JSON",
        ) from exc

    return _format_timestamped_transcript_payload_with_segments(
        payload,
        response_text_path=response_text_path,
        segments_path=segments_path,
        segment_text_field=segment_text_field,
        segment_start_field=segment_start_field,
        segment_end_field=segment_end_field,
        segment_speaker_field=segment_speaker_field,
    )
Branch in _transcribe_via_http

Before generic multipart handling:

if provider_preset == SttProviderPreset.elevenlabs.value:
    return _transcribe_via_elevenlabs(
        url=url,
        api_key=bearer_token,
        audio_bytes=audio_bytes,
        filename=filename,
        content_type=content_type,
        model_name=model_name,
        language=language,
        response_text_path=response_text_path,
        segments_path=segments_path,
        segment_text_field=segment_text_field,
        segment_start_field=segment_start_field,
        segment_end_field=segment_end_field,
        segment_speaker_field=segment_speaker_field,
    )
Required call-site check

Every caller of _transcribe_via_http() must pass:

provider_preset=config.provider_preset

Check these paths:

transcribe_with_team_stt
transcribe_with_stt_snapshot
run_saved_stt_config_test
any direct saved-config test helper

If provider_preset is omitted, the ElevenLabs branch will not run and the 401 will persist.

9. Normalize language in all runtime transports

Do this defensively even after schema normalization.

Generic multipart
language = normalize_stt_language(language)

if language and language_field_name:
    form_fields[language_field_name] = language
Deepgram
language = normalize_stt_language(language)

if language:
    params["language"] = language
ElevenLabs
language = normalize_stt_language(language)

if language:
    data["language_code"] = language
OpenAI SDK or OpenAI-compatible path
language = normalize_stt_language(language)

if language:
    kwargs["language"] = language

Never send:

language=None
language=none
language=auto
language=default
language_code=None
10. Admin UI changes
Language dropdown/input

Where language is rendered, use:

<option value="">Provider default / auto-detect</option>

If rendering a text input instead of dropdown:

<input
  type="text"
  name="language"
  value="{{ stt_form.language or '' }}"
  placeholder="Provider default / auto-detect"
>

Do not render:

value="{{ stt_form.language }}"

if stt_form.language can be None in the template engine and produce "None".

Safer:

value="{{ stt_form.language or '' }}"
ElevenLabs model dropdown

After discovery, ElevenLabs should show a dropdown from available_model_options.

Expected options:

scribe_v2
scribe_v1

Do not show scribe_v2_realtime.

First-step ElevenLabs UI

First setup step should show only:

Team
Provider: ElevenLabs
API key
[Check API key and find transcription options]

No model field on the first step.

Second-step ElevenLabs UI

After discovery/draft creation:

Provider: ElevenLabs
Credential: saved
Provider name
Default model: [scribe_v2 / scribe_v1]
Default language: [Provider default / auto-detect]
Available for team selection: [x]
[Save provider]
[Replace API key]
[Delete incomplete setup]
11. Admin test/runtime parity

The admin “Test STT provider” button must use the same runtime code path as production transcription.

Do not build a parallel request from admin form defaults.

The saved-provider test must use:

config.provider_preset
config.adapter_kind
config.base_url
config.transcribe_path
config.model_name
config.model_field_name
config.language
config.language_field_name
config.file_field_name
config.response_text_path
config.segments_path
config.segment_* fields
config.extra_form_fields_json
saved Vault credential

Then call the same _transcribe_via_http() path used by actual transcription.

Recommended helper

Add a small runtime config resolver if not already present:

@dataclass(frozen=True)
class ResolvedSttRuntimeConfig:
    provider_preset: str
    adapter_kind: SttAdapterKind
    base_url: str
    transcribe_path: str
    model_name: str | None
    model_field_name: str | None
    language: str | None
    language_field_name: str | None
    file_field_name: str
    response_text_path: str
    segments_path: str | None
    segment_text_field: str | None
    segment_start_field: str | None
    segment_end_field: str | None
    segment_speaker_field: str | None
    extra_form_fields_json: dict[str, str]


def resolve_stt_runtime_config(
    config: TeamSttConfig,
    *,
    model_override: str | None = None,
    language_override: str | None = None,
) -> ResolvedSttRuntimeConfig:
    provider_preset = config.provider_preset or infer_stt_provider_preset(
        config.adapter_kind,
        config.base_url,
    )

    return ResolvedSttRuntimeConfig(
        provider_preset=provider_preset,
        adapter_kind=config.adapter_kind,
        base_url=config.base_url,
        transcribe_path=config.transcribe_path,
        model_name=normalize_optional_stt_text(model_override)
        or normalize_optional_stt_text(config.model_name),
        model_field_name=normalize_optional_stt_text(config.model_field_name),
        language=normalize_stt_language(language_override)
        or normalize_stt_language(config.language),
        language_field_name=normalize_optional_stt_text(config.language_field_name),
        file_field_name=config.file_field_name,
        response_text_path=config.response_text_path,
        segments_path=normalize_optional_stt_text(config.segments_path),
        segment_text_field=normalize_optional_stt_text(config.segment_text_field),
        segment_start_field=normalize_optional_stt_text(config.segment_start_field),
        segment_end_field=normalize_optional_stt_text(config.segment_end_field),
        segment_speaker_field=normalize_optional_stt_text(config.segment_speaker_field),
        extra_form_fields_json=dict(config.extra_form_fields_json or {}),
    )

Use this from:

transcribe_with_team_stt
transcribe_with_stt_snapshot
run_saved_stt_config_test
12. Tests to add
A. ElevenLabs discovery filters sync STT models
def test_elevenlabs_model_discovery_filters_sync_stt_models(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [
                {"model_id": "scribe_v2", "name": "Scribe v2"},
                {"model_id": "scribe_v1", "name": "Scribe v1"},
                {"model_id": "scribe_v2_realtime", "name": "Scribe v2 Realtime"},
                {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2"},
            ]

    def fake_get(url, *, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)

    models = _list_elevenlabs_stt_models(
        api_key="el-secret",
        base_url="https://api.elevenlabs.io",
    )

    assert models == ["scribe_v2", "scribe_v1"]
    assert captured["url"] == "https://api.elevenlabs.io/v1/models"
    assert captured["headers"]["xi-api-key"] == "el-secret"
    assert "Authorization" not in captured["headers"]
B. ElevenLabs discovery rejects invalid key
def test_elevenlabs_model_discovery_rejects_invalid_key(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            request = httpx.Request("GET", "https://api.elevenlabs.io/v1/models")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=request,
                response=response,
            )

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(AppError) as exc_info:
        _list_elevenlabs_stt_models(
            api_key="bad-key",
            base_url="https://api.elevenlabs.io",
        )

    assert exc_info.value.code == "stt_credential_invalid"
    assert exc_info.value.status_code == 401
C. ElevenLabs draft creation saves discovered models
def test_system_admin_elevenlabs_draft_discovers_models(
    client,
    monkeypatch,
    make_team,
    make_admin,
):
    # Mock GET /v1/models.
    # POST /api/v1/stt-configs/drafts with provider_preset=elevenlabs.
    # Assert:
    # - available_models == ["scribe_v2", "scribe_v1"]
    # - config.available_models_json == ["scribe_v2", "scribe_v1"]
    # - config.setup_status == "pending_model_selection"
    # - config.model_name is None
    # - response text does not include the API key
D. ElevenLabs transcription uses xi-api-key, not Bearer
def test_elevenlabs_transcription_uses_xi_api_key_not_bearer(monkeypatch):
    captured = {}

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "data": data,
                "files": files,
                "timeout": timeout,
            }
        )

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"text": "hello from elevenlabs"}

        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    result = _transcribe_via_elevenlabs(
        url="https://api.elevenlabs.io/v1/speech-to-text",
        api_key="el-secret",
        audio_bytes=b"audio",
        filename="audio.wav",
        content_type="audio/wav",
        model_name="scribe_v2",
        language=None,
        response_text_path="text",
    )

    assert result == "hello from elevenlabs"
    assert captured["headers"]["xi-api-key"] == "el-secret"
    assert "Authorization" not in captured["headers"]
    assert captured["data"]["model_id"] == "scribe_v2"
    assert "language_code" not in captured["data"]
    assert captured["files"]["file"][0] == "audio.wav"
E. ElevenLabs default language is not sent
@pytest.mark.parametrize(
    "language",
    ["", "None", "none", "null", "undefined", "auto", "default", "provider_default"],
)
def test_elevenlabs_transcription_does_not_send_default_language(
    monkeypatch,
    language,
):
    captured = {}

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured["data"] = data or {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"text": "ok"}

        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    _transcribe_via_elevenlabs(
        url="https://api.elevenlabs.io/v1/speech-to-text",
        api_key="el-secret",
        audio_bytes=b"audio",
        filename="audio.wav",
        content_type="audio/wav",
        model_name="scribe_v2",
        language=language,
        response_text_path="text",
    )

    assert "language_code" not in captured["data"]
F. Generic multipart does not send "None" language
@pytest.mark.parametrize("language", ["None", "none", "auto", "default", ""])
def test_generic_stt_transport_does_not_send_default_language(monkeypatch, language):
    captured = {}

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured["data"] = data or {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"text": "ok"}

        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    _transcribe_via_http(
        provider_preset=SttProviderPreset.custom_openai_compatible.value,
        base_url="https://example.com",
        transcribe_path="/v1/audio/transcriptions",
        file_field_name="file",
        response_text_path="text",
        extra_form_fields_json={},
        bearer_token="secret",
        model_name="whisper-1",
        model_field_name="model",
        language=language,
        language_field_name="language",
        audio_bytes=b"audio",
        filename="audio.wav",
        content_type="audio/wav",
    )

    assert "language" not in captured["data"]
G. Admin saved-provider test uses same runtime path

Create an ElevenLabs config:

provider_preset = "elevenlabs"
base_url = "https://api.elevenlabs.io"
transcribe_path = "/v1/speech-to-text"
model_name = "scribe_v2"
language = None

Then run the admin saved-provider test service.

Assert captured runtime request has:

xi-api-key header present
Authorization header absent
model_id=scribe_v2
language_code absent

Then repeat with:

language = "en"

and assert:

language_code=en
13. Acceptance criteria
ElevenLabs discovery
POST /api/v1/stt-configs/drafts with provider_preset=elevenlabs calls GET /v1/models.
Uses xi-api-key, not Authorization.
Filters to synchronous STT models:
scribe_v2
scribe_v1
Excludes:
scribe_v2_realtime
TTS/voice/LLM models
Saves discovered models to available_models_json.
Renders discovered models in the admin dropdown.
Does not leak the API key in API/browser responses.
ElevenLabs transcription
Runtime transcription uses POST /v1/speech-to-text.
Uses xi-api-key.
Does not send Authorization: Bearer.
Sends multipart file.
Sends model_id.
Sends language_code only when an actual language is configured.
Extracts transcript from text.
401 errors are treated as credential/auth errors, not billing errors.
Language handling
Provider default / auto-detect is stored as Python None.
UI submits blank value for provider default.
Literal "None" is never saved as language.
Literal "None" is never sent to any provider.
Admin saved-provider test and production transcription use the same runtime config/request path.
Admin UI
ElevenLabs first step shows only provider + API key.
ElevenLabs second step shows discovered model dropdown.
API key field is hidden after draft creation.
Language default option is labelled “Provider default / auto-detect.”
Model dropdown excludes realtime-only models.
14. Suggested implementation order
Step 1 — normalization
Add normalize_optional_stt_text.
Add normalize_stt_language.
Wire into schemas.
Wire into browser routes.
Wire into runtime transports.
Add language normalization tests.
Step 2 — ElevenLabs preset
Change supports_model_discovery=True.
Remove default_model_name="scribe_v2".
Set default_language_field_name="language_code".
Step 3 — ElevenLabs discovery
Add _list_elevenlabs_stt_models.
Add _preferred_elevenlabs_model.
Add ElevenLabs inspection branch.
Add discovery tests.
Step 4 — ElevenLabs runtime
Add _transcribe_via_elevenlabs.
Branch in _transcribe_via_http.
Ensure all call sites pass provider_preset.
Add request-shape tests.
Step 5 — admin test parity
Ensure run_saved_stt_config_test uses the same runtime path as production.
Add test proving ElevenLabs admin test uses xi-api-key, model_id, and no default language.
Step 6 — UI polish
Confirm model dropdown appears for ElevenLabs drafts.
Confirm language default is blank-value provider default.
Confirm no API key is re-rendered after draft creation.
Final agent instruction

Implement ElevenLabs as a first-class STT provider, not as generic bearer-auth REST. Add live ElevenLabs model discovery from GET /v1/models using xi-api-key, filter to synchronous STT models scribe_v2 and scribe_v1, and save those models into the draft config for admin selection. Fix runtime transcription so ElevenLabs calls POST /v1/speech-to-text with xi-api-key, multipart file, and model_id; never use Authorization: Bearer for ElevenLabs. Add STT language normalization so blank/provider-default/None/auto values become Python None and are not sent to providers. Ensure the admin test path uses the same saved config and runtime request builder as production transcription.