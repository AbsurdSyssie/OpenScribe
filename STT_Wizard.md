You probably **should** introduce ElevenLabs as its own STT adapter type.

Earlier I was treating it as a branded preset over `generic_rest`, but the docs and the failing runtime test show that ElevenLabs is not just “generic REST with different defaults.” It has enough provider-specific behavior that keeping it under `generic_rest` is making the code ambiguous.

## Why an adapter is justified

Current STT adapter kinds are:

```python
class SttAdapterKind(str, enum.Enum):
    generic_rest = "generic_rest"
    openai_cloud = "openai_cloud"
    openai_compatible_rest = "openai_compatible_rest"
```

`generic_rest` is meant for configurable endpoints: field names, response paths, auth style, OpenAPI inspection, and arbitrary provider behavior. ElevenLabs is now a **known contract**:

```text
POST /v1/speech-to-text
Header: xi-api-key
Multipart:
  model_id
  file
  language_code optional
Response:
  text
  words[]
  speaker_id
Model enum:
  scribe_v2
  scribe_v1
```

That is not meaningfully generic. It needs hard-coded behavior from the ElevenLabs endpoint spec.

## Recommended change

Add:

```python
class SttAdapterKind(str, enum.Enum):
    generic_rest = "generic_rest"
    openai_cloud = "openai_cloud"
    openai_compatible_rest = "openai_compatible_rest"
    elevenlabs_speech_to_text = "elevenlabs_speech_to_text"
```

Then map the preset:

```python
SttProviderPreset.elevenlabs -> SttAdapterKind.elevenlabs_speech_to_text
```

This makes the architecture clearer:

| Provider preset          | Adapter                                                                |
| ------------------------ | ---------------------------------------------------------------------- |
| OpenAI                   | `openai_cloud`                                                         |
| Deepgram                 | probably still `generic_rest` for now, or later `deepgram_prerecorded` |
| ElevenLabs               | `elevenlabs_speech_to_text`                                            |
| Custom OpenAI-compatible | `openai_compatible_rest`                                               |
| Custom REST/OpenAPI      | `generic_rest`                                                         |

## What the ElevenLabs adapter should own

### 1. Inspection

Inspection should validate the key but not rely on `/v1/models` for STT models.

```python
ELEVENLABS_STT_MODELS = ("scribe_v2", "scribe_v1")
```

Inspection should return:

```python
available_models = ["scribe_v2", "scribe_v1"]
model_name = "scribe_v2"
model_field_name = "model_id"
file_field_name = "file"
language_field_name = "language_code"
response_text_path = "text"
segments_path = "words"
segment_text_field = "text"
segment_start_field = "start"
segment_end_field = "end"
segment_speaker_field = "speaker_id"
```

`GET /v1/models` can be used only as a credential probe/catalog diagnostic.

### 2. Runtime transcription

The adapter should do its own request construction:

```python
def _transcribe_via_elevenlabs_speech_to_text(...):
    response = httpx.post(
        f"{base_url.rstrip()}/v1/speech-to-text",
        headers={"xi-api-key": api_key},
        data={
            "model_id": model_name or "scribe_v2",
            **({"language_code": language} if language else {}),
        },
        files={
            "file": (filename, audio_bytes, content_type),
        },
        timeout=60.0,
    )
    response.raise_for_status()
```

No generic bearer auth. No generic `model_field_name`. No blank optional fields.

### 3. Response formatting

The adapter should know ElevenLabs returns:

```text
text
words
speaker_id
```

so timestamp/speaker extraction should use those fields.

### 4. Validation

Reject non-sync models:

```python
if model_name not in {"scribe_v2", "scribe_v1"}:
    raise AppError(422, "business_rule_violation", ...)
```

Call this on finalize, upsert, selection override, and runtime.

## Migration impact

Because `SttAdapterKind` is stored as a SQLAlchemy enum, adding a new value may require a migration depending on how PostgreSQL enum/string storage is configured.

Migration/backfill:

```sql
UPDATE team_stt_configs
SET adapter_kind = 'elevenlabs_speech_to_text'
WHERE provider_preset = 'elevenlabs';
```

Also repair any existing bad ElevenLabs configs:

```sql
UPDATE team_stt_configs
SET
  model_name = CASE
    WHEN model_name IN ('scribe_v1', 'scribe_v2') THEN model_name
    ELSE 'scribe_v2'
  END,
  available_models_json = '["scribe_v2", "scribe_v1"]'::json,
  model_field_name = 'model_id',
  file_field_name = 'file',
  language_field_name = 'language_code',
  response_text_path = 'text',
  segments_path = 'words',
  segment_text_field = 'text',
  segment_start_field = 'start',
  segment_end_field = 'end',
  segment_speaker_field = 'speaker_id'
WHERE provider_preset = 'elevenlabs';
```

## Why this is better than more generic conditionals

Without a new adapter, you end up with generic code containing exceptions like:

```python
if provider_preset == "elevenlabs":
    headers = {"xi-api-key": key}
if provider_preset == "elevenlabs":
    models = ["scribe_v2", "scribe_v1"]
if provider_preset == "elevenlabs":
    response path = words/speaker_id
```

That means the provider is already an adapter in practice, just not named as one.

Making it explicit gives you:

* clearer runtime dispatch,
* cleaner tests,
* less risk of generic REST changes breaking ElevenLabs,
* less confusion between provider preset and protocol behavior,
* easier future support for ElevenLabs-specific options like `diarize`, `timestamps_granularity`, `no_verbatim`, `entity_detection`, and regional endpoints.

## Agent instruction

Implement ElevenLabs as a dedicated STT adapter:

```text
SttAdapterKind.elevenlabs_speech_to_text
```

Map `SttProviderPreset.elevenlabs` to that adapter. The adapter must use `/v1/speech-to-text` as the source of truth, hard-code selectable models to `scribe_v2` and `scribe_v1`, send `xi-api-key`, multipart `file`, `model_id`, optional `language_code`, and parse `text` plus `words` with `speaker_id`. Use `/v1/models` only as an optional credential/catalog probe, not model discovery. Add a migration to backfill existing ElevenLabs configs to the new adapter and repair any saved invalid model values.
