## Verdict

**No — I would not consider the implementation proper/merge-ready yet.** It has several good pieces, but there are blocking correctness problems, especially in `app/services/stt.py`.

The biggest issue: **the current STT service appears to be syntactically invalid**. The `transcribe_with_stt_snapshot` function signature puts defaulted parameters before required parameters:

```python
model_field_name: str | None = None,
language_field_name: str | None = None,
audio_bytes: bytes,
filename: str,
content_type: str,
```

That is a Python syntax error: non-default parameters cannot follow default parameters. This would prevent the module from importing. The same file also has call-site/signature mismatches around `_transcribe_via_openai_cloud` and `_transcribe_via_http`. 

## What looks good

### 1. Dependencies were added

The intended inspection libraries were added to `requirements.txt`:

```text
openapi-spec-validator
prance
jsonschema
jsonpath-ng
```



### 2. STT schema/model fields were added

`SttConfigUpsert`, `SttConfigDetail`, and `SttInspectResult` now include the expected dynamic STT fields:

```text
model_field_name
language_field_name
segments_path
segment_text_field
segment_start_field
segment_end_field
segment_speaker_field
```



`TeamSttConfig` also has the corresponding model columns. 

### 3. Migration exists

There is an Alembic migration adding the new STT config fields and ingestion-job snapshot fields, with sensible backfills for existing `model` and `language` behavior. 

### 4. LLM inspection state is improved

`LlmConfigInspectResult` now includes machine-readable fields:

```text
discovery_status
default_model_source
requires_bearer_token
supports_model_discovery
warnings
```



The LLM service correctly distinguishes OpenAI fallback, Bedrock manual-required, and Ollama manual-required/fetched states. 

### 5. Admin flow was partially updated

The admin route now accepts the dynamic STT fields and has an STT inspection route that returns an inspection review state. 

---

## Blocking issues

### 1. `app/services/stt.py` likely will not import

This is the main blocker.

`transcribe_with_stt_snapshot` has defaulted parameters before required parameters. That is a syntax error and would break application startup/tests. 

Fix by moving new optional params after required params, or making all following params keyword-only/defaulted. Preferred:

```python
def transcribe_with_stt_snapshot(
    db: Session,
    *,
    team_id: UUID,
    stt_config_id: UUID | None,
    adapter_kind: str | None,
    base_url: str | None,
    transcribe_path: str | None,
    file_field_name: str | None,
    response_text_path: str | None,
    extra_form_fields_json: dict[str, str] | None,
    model_name: str | None,
    language: str | None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    model_field_name: str | None = None,
    language_field_name: str | None = None,
) -> str:
```

### 2. `run_saved_stt_config_test` has bad function calls

In the visible STT service code, the OpenAI branch calls `_transcribe_via_openai_cloud` with `model_field_name` and `language_field_name`, but that function does not accept those parameters. The non-OpenAI branch calls `_transcribe_via_http` without the newly required `model_field_name` and `language_field_name`. 

This will fail even after the syntax error is fixed.

Expected shape:

```python
if config.adapter_kind is SttAdapterKind.openai_cloud:
    transcript_text = _transcribe_via_openai_cloud(
        base_url=config.base_url,
        extra_form_fields_json=config.extra_form_fields_json,
        bearer_token=bearer_token,
        model_name=config.model_name,
        language=config.language,
        audio_bytes=audio_bytes,
        filename=sample_path.name,
    )
else:
    transcript_text = _transcribe_via_http(
        base_url=config.base_url,
        transcribe_path=config.transcribe_path,
        file_field_name=config.file_field_name,
        response_text_path=config.response_text_path,
        extra_form_fields_json=config.extra_form_fields_json,
        bearer_token=bearer_token,
        model_name=config.model_name,
        model_field_name=config.model_field_name or "model",
        language=config.language,
        language_field_name=config.language_field_name or "language",
        audio_bytes=audio_bytes,
        filename=sample_path.name,
        content_type="audio/wav",
    )
```

### 3. The new libraries are mostly not used

`provider_inspection.py` imports only `httpx` and manually implements:

* local `$ref` resolution
* schema traversal
* dot-path extraction
* a partial JSONPath parser



That means the implementation added `openapi-spec-validator`, `prance`, `jsonschema`, and `jsonpath-ng`, but still hand-rolls most of the behavior we wanted libraries for. This defeats a core part of the plan.

Minimum fix:

* use `openapi_spec_validator.validate_spec`
* use `prance.ResolvingParser` or a real dereferencing flow
* use `jsonpath_ng.parse` instead of `_extract_jsonpath`

### 4. The STT segment fields are saved but not meaningfully used

The model/schema now support:

```text
segments_path
segment_text_field
segment_start_field
segment_end_field
segment_speaker_field
```

But runtime formatting still only checks hard-coded top-level `segments` and assumes fields named `text`, `start`, `end`, and `speaker`. 

So the dynamic segment contract is currently cosmetic unless the provider happens to match the old hard-coded shape.

Either wire these fields into `_format_timestamped_transcript_payload`, or remove them from this slice until implemented.

### 5. Admin still has a `preserved_bearer_token` path

The admin STT save route accepts both `bearer_token` and `preserved_bearer_token`, then uses:

```python
resolved_bearer_token = bearer_token or preserved_bearer_token or None
```



The current inspection route appears to set `preserved_bearer_token` to an empty string, which is good, and presentation defaults also set it empty. 

But the presence of this pathway is contrary to the stated rule: **do not carry tokens through hidden/preserved fields**. I would remove `preserved_bearer_token` entirely for STT/LLM unless there is a very specific server-side-only flow. The de-identification test already asserts this principle.

### 6. Tests do not appear to cover the new behavior adequately

Search results show references in docs and production code, but not clear service-level tests for:

* STT dynamic field-name runtime behavior
* STT OpenAPI inference
* LLM discovery status branches
* JSONPath extraction via `jsonpath-ng`
* syntax/import coverage for the changed STT service

The absence of direct test hits for `discovery_status` outside docs/service/schema/template is a red flag. 

Given the syntax/signature issues, a basic import or pytest run should have caught this.

---

## Non-blocking but important design issues

### Provider inspection helper is too permissive / underpowered

`fetch_openapi_document` accepts any JSON object with a `paths` dict. It does not validate the spec despite adding `openapi-spec-validator`. 

That means malformed specs can pass and then fail later in confusing ways.

### JSONPath support is incomplete

The hand-rolled `_extract_jsonpath` only supports a narrow subset like:

```text
$.a.b[0].c
```

It does not support real JSONPath features:

```text
$['choices'][0]['message']['content']
$.entities[*]
$..text
filters
```

Since `jsonpath-ng` was added, use it.

### OpenAPI dereferencing still only supports local refs

`_resolve_openapi_pointer` rejects non-local refs. That may be acceptable for security, but it should be explicit. If the project wants to support arbitrary provider OpenAPI docs, external refs will appear in the wild. 

---

## What I would ask the agents to fix next

### Immediate fix list

1. Fix the `transcribe_with_stt_snapshot` function signature.
2. Fix all `_transcribe_via_http` and `_transcribe_via_openai_cloud` call sites.
3. Remove `preserved_bearer_token` from STT/LLM admin save flows.
4. Use the actual libraries that were added:

   * `openapi-spec-validator`
   * `prance`
   * `jsonpath-ng`
5. Wire `segments_path` and `segment_*` fields into runtime response parsing, or remove them for now.
6. Add focused tests before further UI work.

### Minimum tests to add before considering this done

```text
1. Import app.services.stt succeeds.
2. Generic STT config sends model_id/lang rather than model/language.
3. run_saved_stt_config_test works for OpenAI and generic REST branches.
4. transcribe_with_stt_snapshot works with old snapshots and new snapshots.
5. LLM inspect returns fetched/fallback/manual_required states.
6. STT inspect does not render or preserve bearer tokens.
7. Admin UI does not include bearer token in hidden fields after inspect.
8. Migration applies and downgrades.
```
