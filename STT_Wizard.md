# Implementation Plan: Fix Deepgram STT Discovery + Transcription

Apply this on top of commit:

```text
b075a6573c637eefc9aba3b18d2ec2a92e226d8a
```

## Problem

The STT wizard architecture is mostly correct, but Deepgram currently does not work because it is treated too much like the generic multipart/OpenAI-compatible STT path.

Current Deepgram preset:

```python
adapter_kind=SttAdapterKind.generic_rest
default_base_url="https://api.deepgram.com"
transcribe_path="/v1/listen"
auth_header_style="token"
supports_model_discovery=False
default_model_name="nova-3"
default_response_text_path="results.channels.0.alternatives.0.transcript"
default_extra_form_fields={"smart_format": "true"}
```



The endpoint/auth/response path are directionally right. The request/discovery behavior is incomplete.

## Desired Deepgram behavior

### Model discovery

Deepgram supports model discovery via:

```http
GET https://api.deepgram.com/v1/models
Authorization: Token <DEEPGRAM_API_KEY>
```

Use the `stt` list from the response and filter to models usable for prerecorded/direct-upload transcription.

### Transcription

Deepgram prerecorded transcription should use:

```http
POST https://api.deepgram.com/v1/listen?model=<model>&smart_format=true&language=<language>
Authorization: Token <DEEPGRAM_API_KEY>
Content-Type: <audio MIME type>

<raw audio bytes>
```

Do **not** send it as OpenAI-style multipart form data.

---

# Phase 1 — Update Deepgram preset semantics

## Change `app/services/stt_presets.py`

Current Deepgram preset has:

```python
supports_model_discovery=False
default_model_name="nova-3"
default_extra_form_fields={"smart_format": "true"}
```

Change to:

```python
supports_model_discovery=True
default_model_name=None
default_extra_form_fields={"smart_format": "true"}
```

Recommended Deepgram preset:

```python
SttProviderPreset.deepgram.value: SttProviderPresetDefinition(
    key=SttProviderPreset.deepgram.value,
    display_name="Deepgram",
    adapter_kind=SttAdapterKind.generic_rest,
    default_base_url="https://api.deepgram.com",
    transcribe_path="/v1/listen",
    auth_header_style="token",
    requires_api_key=True,
    supports_model_discovery=True,
    supports_language_selection=True,
    supports_diarization=True,
    default_model_name=None,
    default_model_field_name="model",
    default_language_field_name="language",
    default_response_text_path="results.channels.0.alternatives.0.transcript",
    default_extra_form_fields={"smart_format": "true"},
)
```

## Rationale

Deepgram should not save `["nova-3"]` as the only available model. It should discover available STT models from `/v1/models`, then let the admin choose one.

`nova-3` can still be preferred in the UI if present in discovery results, but it should not be the only saved model.

---

# Phase 2 — Add Deepgram model discovery

## Add helper in `app/services/stt.py`

Add:

```python
def _list_deepgram_stt_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Token {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {401, 403}:
            raise AppError(
                401,
                "stt_credential_invalid",
                "The API key was rejected by Deepgram.",
                {"provider_status": status_code},
            ) from exc
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not load available Deepgram STT models",
            {"provider_status": status_code},
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not reach Deepgram model discovery",
            {"provider_error_code": _safe_http_error_details(exc).get("provider_error_code")},
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "Deepgram model discovery returned invalid JSON",
        ) from exc

    stt_models = payload.get("stt")
    if not isinstance(stt_models, list):
        return []

    discovered: list[str] = []
    for item in stt_models:
        if not isinstance(item, dict):
            continue

        # OpenScribe currently uses prerecorded/direct-upload STT,
        # so prefer Deepgram models that support batch/prerecorded use.
        if item.get("batch") is False:
            continue

        model_id = item.get("canonical_name") or item.get("name")
        if isinstance(model_id, str) and model_id.strip():
            discovered.append(model_id.strip())

    return sorted(set(discovered))
```

## Add model option helper if useful

```python
def _deepgram_model_options(models: list[str], *, source: str = "provider") -> list[SttModelOption]:
    return [
        SttModelOption(id=model, source=source, label=f"{model} ({source})")
        for model in models
    ]
```

---

# Phase 3 — Wire Deepgram discovery into inspection

Find the STT inspection path, likely inside `inspect_stt_contract()` / `inspect_stt_contract_service()`.

Current OpenAI path already uses `_list_openai_transcription_models()` and fallback transcription model logic. Deepgram needs its own branch.

## Desired behavior

When inspecting a Deepgram preset:

```text
1. Require bearer token.
2. Call GET /v1/models with Authorization: Token <key>.
3. Extract STT models where batch != false.
4. Return available_models and available_model_options.
5. Do not fall back to ["nova-3"] as the only model.
6. If auth fails, raise invalid credential and do not create draft.
7. If non-auth discovery fails, allow manual model entry only if that is consistent with existing STT wizard behavior.
```

## Pseudocode

```python
def _inspect_deepgram_preset(
    *,
    team_id: UUID,
    base_url: str,
    bearer_token: str | None,
    preset: SttProviderPresetDefinition,
) -> SttInspectResult:
    if not bearer_token:
        raise AppError(
            422,
            "business_rule_violation",
            "Deepgram requires an API key",
            {"field": "bearer_token"},
        )

    models = _list_deepgram_stt_models(api_key=bearer_token, base_url=base_url)

    return SttInspectResult(
        team_id=team_id,
        provider_preset=SttProviderPreset.deepgram.value,
        provider_display_name="Deepgram",
        adapter_kind=preset.adapter_kind,
        base_url=base_url,
        openapi_path=None,
        transcribe_path=preset.transcribe_path,
        model_name=_preferred_deepgram_model(models),
        model_field_name="model" if models else None,
        available_models=models,
        available_model_options=_stt_model_options(models, source="provider"),
        file_field_name=preset.default_file_field_name,
        language=None,
        language_field_name=preset.default_language_field_name,
        response_text_path=preset.default_response_text_path,
        segments_path=None,
        segment_text_field=None,
        segment_start_field=None,
        segment_end_field=None,
        segment_speaker_field=None,
        extra_form_fields_json=dict(preset.default_extra_form_fields or {}),
        candidate_paths=[preset.transcribe_path],
        field_tips=[],
        notes=[
            "Discovered Deepgram STT models from /v1/models.",
        ],
    )
```

Preferred model helper:

```python
def _preferred_deepgram_model(models: list[str]) -> str | None:
    preferred = [
        "nova-3",
        "nova-2",
    ]
    for model in preferred:
        if model in models:
            return model
    return models[0] if models else None
```

If the product decision is “no default selection unless admin chooses,” set `model_name=None` even when models exist. But the current STT wizard seems to preselect defaults, so choosing `nova-3` if available is reasonable.

---

# Phase 4 — Fix Deepgram transcription transport

## Current issue

`_transcribe_via_http()` constructs `form_fields` and likely sends `data=form_fields` and `files={...}`. That works for OpenAI-compatible multipart APIs, but not Deepgram.

Deepgram should use:

```python
httpx.post(
    url,
    headers={
        "Authorization": f"Token {bearer_token}",
        "Content-Type": content_type,
    },
    params={
        "model": model_name,
        "smart_format": "true",
        "language": language,
    },
    content=audio_bytes,
)
```

## Add branch in `_transcribe_via_http()`

At the top of `_transcribe_via_http()`, after constructing `url`, before generic `form_fields` multipart handling:

```python
if provider_preset == SttProviderPreset.deepgram.value:
    return _transcribe_via_deepgram(
        url=url,
        bearer_token=bearer_token,
        audio_bytes=audio_bytes,
        content_type=content_type,
        model_name=model_name,
        language=language,
        extra_query_params=extra_form_fields_json or {},
        response_text_path=response_text_path,
        segments_path=segments_path,
        segment_text_field=segment_text_field,
        segment_start_field=segment_start_field,
        segment_end_field=segment_end_field,
        segment_speaker_field=segment_speaker_field,
    )
```

## Add helper

```python
def _transcribe_via_deepgram(
    *,
    url: str,
    bearer_token: str | None,
    audio_bytes: bytes,
    content_type: str,
    model_name: str | None,
    language: str | None,
    extra_query_params: dict[str, str],
    response_text_path: str,
    segments_path: str | None = None,
    segment_text_field: str | None = None,
    segment_start_field: str | None = None,
    segment_end_field: str | None = None,
    segment_speaker_field: str | None = None,
) -> str:
    if not bearer_token:
        raise AppError(
            409,
            "stt_config_secret_missing",
            "Deepgram STT requires a saved API key.",
        )

    params = dict(extra_query_params or {})

    if model_name:
        params["model"] = model_name

    if language:
        params["language"] = language

    headers = {
        "Authorization": f"Token {bearer_token}",
        "Content-Type": content_type or "application/octet-stream",
    }

    try:
        response = httpx.post(
            url,
            headers=headers,
            params=params,
            content=audio_bytes,
            timeout=60.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "stt_deepgram_request_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "provider_preset": SttProviderPreset.deepgram.value,
                    "query_keys": sorted(params.keys()),
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
            "Deepgram response was not valid JSON",
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
```

## Important call-site check

Ensure every path that calls `_transcribe_via_http()` passes:

```python
provider_preset=config.provider_preset
```

Specifically check:

```text
transcribe_with_team_stt
transcribe_with_stt_snapshot
run_saved_stt_config_test
```

The Deepgram branch will not run if `provider_preset` is omitted.

---

# Phase 5 — Handle query params vs form fields cleanly

For this fix, it is acceptable to reuse:

```python
extra_form_fields_json
```

as Deepgram query params inside `_transcribe_via_deepgram()`.

But add a comment:

```python
# For Deepgram, stored extra_form_fields_json represents query parameters
# for /v1/listen, not multipart form fields.
```

A later cleanup can rename this to something more general:

```python
extra_request_options_json
```

or extend the preset definition with:

```python
request_options_location: Literal["form", "query"] = "form"
```

Do **not** do that schema rename in this fix unless you want a larger migration.

---

# Phase 6 — Finalization rules for Deepgram models

Deepgram now has real model discovery. Update finalization behavior so that:

```text
if available_models_json is non-empty:
    selected model must be in available_models_json
if available_models_json is empty due to non-auth discovery failure:
    manual model is allowed
```

This likely already exists from the wizard work. Verify that the Deepgram inspection result saves the discovered model list, not just `["nova-3"]`.

## Expected draft result

After successful Deepgram draft creation:

```json
{
  "config": {
    "provider_preset": "deepgram",
    "setup_status": "pending_model_selection",
    "model_name": null,
    "available_models_json": ["nova-3", "..."]
  },
  "available_models": ["nova-3", "..."],
  "credential_status": "verified"
}
```

If the implementation preselects a model:

```json
{
  "config": {
    "model_name": null
  },
  "available_models": ["nova-3", "..."]
}
```

Keep `config.model_name = null` until finalize if following the LLM-style wizard strictly. The inspect result may include a suggested `model_name`.

---

# Phase 7 — Tests

## 1. Deepgram model discovery test

Add to `tests/test_api.py` or a dedicated STT service test.

```python
def test_deepgram_model_discovery_uses_models_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "stt": [
                    {
                        "name": "Nova 3",
                        "canonical_name": "nova-3",
                        "batch": True,
                        "streaming": True,
                    },
                    {
                        "name": "Streaming Only",
                        "canonical_name": "stream-only",
                        "batch": False,
                        "streaming": True,
                    },
                ],
                "tts": [
                    {"canonical_name": "aura-2"}
                ],
            }

    def fake_get(url, *, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)

    models = _list_deepgram_stt_models(
        api_key="dg-secret",
        base_url="https://api.deepgram.com",
    )

    assert models == ["nova-3"]
    assert captured["url"] == "https://api.deepgram.com/v1/models"
    assert captured["headers"]["Authorization"] == "Token dg-secret"
```

## 2. Deepgram invalid credential test

```python
def test_deepgram_model_discovery_rejects_invalid_key(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            request = httpx.Request("GET", "https://api.deepgram.com/v1/models")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(AppError) as exc_info:
        _list_deepgram_stt_models(
            api_key="bad-key",
            base_url="https://api.deepgram.com",
        )

    assert exc_info.value.code == "stt_credential_invalid"
    assert exc_info.value.status_code == 401
```

## 3. Deepgram draft creates discovered models

```python
def test_system_admin_deepgram_draft_discovers_models(client, monkeypatch, make_team, make_user):
    # Mock Deepgram /v1/models response.
    # POST /api/v1/stt-configs/drafts
    # Assert available_models includes nova-3 and not TTS models.
```

Expected assertions:

```python
assert body["provider_display_name"] == "Deepgram"
assert body["available_models"] == ["nova-3"]
assert body["config"]["available_models_json"] == ["nova-3"]
assert body["config"]["setup_status"] == "pending_model_selection"
assert "dg-secret" not in response.text
```

## 4. Deepgram transcription request shape test

This is the most important regression test.

```python
def test_deepgram_transcription_uses_query_params_and_raw_audio(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": {
                    "channels": [
                        {
                            "alternatives": [
                                {"transcript": "hello from deepgram"}
                            ]
                        }
                    ]
                }
            }

    def fake_post(url, *, headers=None, params=None, content=None, data=None, files=None, timeout=None):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "content": content,
                "data": data,
                "files": files,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    result = _transcribe_via_http(
        base_url="https://api.deepgram.com",
        transcribe_path="/v1/listen",
        file_field_name="file",
        response_text_path="results.channels.0.alternatives.0.transcript",
        extra_form_fields_json={"smart_format": "true"},
        bearer_token="dg-secret",
        model_name="nova-3",
        model_field_name="model",
        language="en",
        language_field_name="language",
        audio_bytes=b"wav-bytes",
        filename="audio.wav",
        content_type="audio/wav",
        provider_preset=SttProviderPreset.deepgram.value,
    )

    assert result == "hello from deepgram"
    assert captured["url"] == "https://api.deepgram.com/v1/listen"
    assert captured["headers"]["Authorization"] == "Token dg-secret"
    assert captured["headers"]["Content-Type"] == "audio/wav"
    assert captured["params"] == {
        "smart_format": "true",
        "model": "nova-3",
        "language": "en",
    }
    assert captured["content"] == b"wav-bytes"
    assert captured["data"] is None
    assert captured["files"] is None
```

## 5. Call-site propagation test

Add a test around `transcribe_with_team_stt()` or `transcribe_with_stt_snapshot()` that creates a Deepgram config and proves the Deepgram branch is used.

Minimum assertion:

```python
assert captured["headers"]["Authorization"] == "Token dg-secret"
assert captured["files"] is None
```

This catches a bug where `_transcribe_via_http()` supports Deepgram but callers forget to pass `provider_preset`.

---

# Phase 8 — UI behavior

Update the STT wizard behavior for Deepgram:

## Draft step

Show:

```text
Provider: Deepgram
API key
[Check API key and find transcription options]
```

No model field on the first step.

## After discovery

Show discovered model dropdown:

```text
Provider: Deepgram
Credential: saved
Provider name
Default model: [nova-3 / ...]
Default language: [optional]
Available for team selection: [x]
[Save provider]
```

If discovery returns no models for a non-auth reason:

```text
Could not find Deepgram models.
[Try again]
[Enter model manually]
```

If auth fails:

```text
The API key was rejected by Deepgram.
```

Do not create a draft for invalid credentials.

---

# Phase 9 — Acceptance criteria

## Deepgram discovery

* `POST /api/v1/stt-configs/drafts` with `provider_preset=deepgram` calls `GET /v1/models`.
* Uses `Authorization: Token <key>`.
* Extracts only `stt` models.
* Excludes TTS models.
* Excludes models with `batch=False`.
* Saves discovered models to `available_models_json`.
* Returns `available_model_options`.

## Deepgram transcription

* Uses `POST /v1/listen`.
* Uses `Authorization: Token <key>`.
* Sends `model`, `smart_format`, and `language` as query params.
* Sends raw audio bytes as request body.
* Sets `Content-Type` to the normalized audio content type.
* Does not send multipart `files`.
* Extracts transcript from:

```text
results.channels.0.alternatives.0.transcript
```

## Error behavior

* Invalid Deepgram key during discovery returns `stt_credential_invalid`.
* Invalid Deepgram key does not create a draft.
* Deepgram non-auth model discovery failure can allow manual model entry if the rest of the wizard supports that.
* Runtime transcription errors are logged without leaking API key or audio content.

---

# Phase 10 — Suggested agent task list

## Task 1 — Preset update

* Change Deepgram `supports_model_discovery` to `True`.
* Remove `default_model_name="nova-3"` or stop treating it as the only available model.
* Keep `smart_format=true`.

## Task 2 — Discovery helper

* Add `_list_deepgram_stt_models()`.
* Parse `payload["stt"]`.
* Use `canonical_name` or `name`.
* Filter `batch is not False`.
* Map 401/403 to `stt_credential_invalid`.

## Task 3 — Inspection branch

* Add Deepgram branch to STT inspection.
* Return discovered models/options.
* Save discovered list in draft config.
* Make invalid credential stop draft creation.

## Task 4 — Transport branch

* Add `_transcribe_via_deepgram()`.
* Branch in `_transcribe_via_http()` by `provider_preset`.
* Send raw audio + query params.
* Extract transcript with existing response path helper.

## Task 5 — Call-site propagation

* Ensure all `_transcribe_via_http()` callers pass `provider_preset`.
* Check runtime transcription, saved provider test, and snapshot transcription paths.

## Task 6 — Tests

* Add discovery tests.
* Add invalid credential tests.
* Add draft discovery API test.
* Add raw-audio transcription request-shape test.
* Add call-site propagation test.

## Task 7 — Admin UI verification

* Confirm Deepgram shows key-only first step.
* Confirm model dropdown appears after discovery.
* Confirm invalid key does not show model step.
* Confirm manual model path is available only for non-auth discovery failure or no returned models.

---

# Final instruction for the dev agent

Fix Deepgram as a provider-specific STT transport, not as generic multipart REST. Deepgram model discovery should call `GET /v1/models` with `Authorization: Token <key>` and use returned `stt` models that support prerecorded/batch transcription. Deepgram transcription should call `POST /v1/listen` with `Authorization: Token <key>`, query params for `model`, `smart_format`, and language, and raw audio bytes as the request body with the correct content type. Add tests proving both discovery and transcription request shape.
