# Implementation plan: LLM + STT inspection and admin UI upgrades

## 0. Guiding intent

Upgrade provider inspection so admins can safely connect STT and LLM providers, inspect capabilities, review inferred values, and save normalized provider contracts without leaking secrets or patient content.

The repo already has the right broad shape:

* STT inspect/save/select routes exist.
* LLM inspect/save/select routes exist.
* Provider credentials are stored through Vault references.
* The de-identification inspection flow is the best existing model to copy.
* `AGENTS.md` explicitly requires privacy-sensitive handling, Vault-backed provider secrets, no transcript/note content in logs, checklist/checkpoint workflow, tests, and docs for every meaningful change. 

The goal is **not** to make runtime code re-discover provider shapes. The goal is:

```text id="i0l2jq"
Inspect → propose normalized contract → admin reviews → save contract → runtime uses saved contract only
```

---

# 1. Current baseline

## STT

Current STT schemas already expose:

```text id="uwhwm9"
adapter_kind
base_url
openapi_path
bearer_token
transcribe_path
model_name
file_field_name
language
response_text_path
extra_form_fields_json
candidate_paths
available_models
field_tips
notes
```

This is visible in `SttInspectRequest`, `SttInspectResult`, and `SttConfigUpsert`. 

**Main gap:** STT runtime can save a model value and language value, but not the provider’s field names for those values. So arbitrary STT APIs that expect `model_id`, `engine`, `lang`, or `locale` cannot be represented cleanly.

## LLM

Current LLM inspection is model discovery for known adapters. `LlmInspectRequest` supports:

```text id="8f6xjd"
adapter_kind
base_url
bearer_token
bedrock_region
```

`LlmConfigInspectResult` returns:

```text id="i14quk"
model_name
available_models
available_model_options
notes
```

This is appropriate for OpenAI-compatible, Bedrock Mantle, and Ollama-style adapters. 

**Main gap:** the result does not clearly distinguish fetched provider models from fallback/default/manual states. The UI has to infer too much from prose notes.

## Dependencies

Current dependencies include FastAPI, Pydantic, SQLAlchemy, Alembic, `httpx`, OpenAI SDK, PyYAML, Presidio, pytest, etc. 

Add focused OpenAPI/JSON-path dependencies rather than building all OpenAPI handling manually.

---

# 2. Dependency plan

Add to `requirements.txt`:

```text id="lmthbc"
openapi-spec-validator==0.7.2
prance==23.6.21.0
jsonschema==4.23.0
jsonpath-ng==1.7.0
```

Optional test dependency:

```text id="dkd80x"
schemathesis==3.x
```

Optional future LLM runtime dependency:

```text id="zk0bco"
litellm
```

## Recommendation

For this implementation, add only:

```text id="pkepgc"
openapi-spec-validator
prance
jsonschema
jsonpath-ng
```

Do **not** introduce LiteLLM in the first pass. Current LLM code is already scoped around known adapters. LiteLLM would be a bigger runtime provider abstraction change, not just inspection/UI work.

---

# 3. Architecture decisions

## 3.1 Feature boundary

Implement this as two upgrades:

```text id="x6k76b"
A. STT generic OpenAPI inspection and normalized runtime contract
B. LLM known-adapter discovery improvements and clearer admin flow
```

Do **not** implement generic arbitrary LLM REST APIs in this pass.

## 3.2 Saved contract rule

Runtime STT/LLM code must not infer provider shape. It should only read saved provider config.

Inspection can infer. Save persists. Runtime executes.

## 3.3 Secret rule

Inspection and admin UI must never render bearer tokens back into HTML or JSON responses. If the admin wants to save a bearer-auth provider after inspection, the UI should require entering the token again unless the save occurs as one server-side action that never re-renders the token.

The existing de-identification inspect test already checks this pattern for bearer tokens. 

## 3.4 Synthetic-only rule

Inspection must never send transcript text, note text, generated document text, prompts containing patient content, or user clinical content.

Allowed:

```text id="mqc3zm"
synthetic STT sample audio
synthetic redaction sample text
empty/synthetic LLM test prompt if later added
```

---

# 4. New shared inspection utility module

Create:

```text id="qv551t"
app/services/provider_inspection.py
```

Purpose: shared low-level OpenAPI and JSON-path helpers.

Keep business-specific inference in STT/LLM service modules.

## 4.1 Functions

```python id="gf98q2"
def fetch_openapi_document(
    *,
    base_url: str,
    candidate_paths: list[str],
    bearer_token: str | None,
    timeout_seconds: float = 10.0,
) -> tuple[dict[str, Any], str]:
    ...
```

Responsibilities:

* construct URLs safely
* apply bearer auth if provided
* use `httpx`
* enforce timeout
* parse JSON
* validate OpenAPI shape
* return `(document, resolved_path)`
* never log token
* never log response bodies

```python id="nppeh8"
def dereference_openapi_document(document: dict[str, Any]) -> dict[str, Any]:
    ...
```

Use `prance` or `jsonref`-style behavior through the chosen library.

```python id="hn802y"
def operation_request_schema(
    document: dict[str, Any],
    operation: dict[str, Any],
    media_type_family: str,
) -> dict[str, Any] | None:
    ...
```

Used for:

```text id="eit2kx"
multipart/form-data
application/json
```

```python id="n6q1eh"
def operation_response_schema(
    document: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any] | None:
    ...
```

```python id="i4vyyz"
def extract_json_path(payload: Any, path: str) -> Any:
    ...
```

Behavior:

* support old dot paths:

  * `text`
  * `result.text`
* support JSONPath:

  * `$.choices[0].message.content`
  * `$.results[0].alternatives[0].transcript`

```python id="0i00z8"
def display_default_from_schema_property(prop: dict[str, Any]) -> str | None:
    ...
```

Use priority:

```text id="m98evc"
default
example
first enum value
None
```

---

# 5. Database migration plan

## 5.1 STT config table

Add columns to `team_stt_configs`:

```python id="clpjkt"
model_field_name = sa.Column(sa.String(length=255), nullable=True)
language_field_name = sa.Column(sa.String(length=255), nullable=True)
segments_path = sa.Column(sa.String(length=255), nullable=True)
segment_text_field = sa.Column(sa.String(length=255), nullable=True)
segment_start_field = sa.Column(sa.String(length=255), nullable=True)
segment_end_field = sa.Column(sa.String(length=255), nullable=True)
segment_speaker_field = sa.Column(sa.String(length=255), nullable=True)
```

Minimum required:

```text id="3b28xk"
model_field_name
language_field_name
```

Recommended to include segment fields now because they are part of STT response inspection and can be nullable.

## 5.2 Backfill

For existing configs:

```sql id="btfzy4"
UPDATE team_stt_configs
SET model_field_name = 'model'
WHERE model_name IS NOT NULL AND model_field_name IS NULL;

UPDATE team_stt_configs
SET language_field_name = 'language'
WHERE language IS NOT NULL AND language_field_name IS NULL;
```

This preserves current behavior.

## 5.3 Rollback

Drop the added nullable columns.

---

# 6. Model and schema changes

## 6.1 `app/models.py`

Update `TeamSttConfig` with:

```python id="q85r30"
model_field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
language_field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
segments_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
segment_text_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
segment_start_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
segment_end_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
segment_speaker_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

## 6.2 `app/schemas/stt.py`

Update:

```text id="ujmrq8"
SttConfigUpsert
SttConfigDetail
SttInspectResult
```

Add:

```python id="wg6bgy"
model_field_name: str | None = Field(default="model", max_length=255)
language_field_name: str | None = Field(default="language", max_length=255)
segments_path: str | None = Field(default=None, max_length=255)
segment_text_field: str | None = Field(default=None, max_length=255)
segment_start_field: str | None = Field(default=None, max_length=255)
segment_end_field: str | None = Field(default=None, max_length=255)
segment_speaker_field: str | None = Field(default=None, max_length=255)
```

Validation:

* blank string becomes `None` for optional fields
* if `model_name` is present and `model_field_name` is blank, default to `"model"`
* if `language` is present and `language_field_name` is blank, default to `"language"`

OpenAI adapters:

```text id="06qbl5"
model_field_name = model
language_field_name = language
file_field_name = file
response_text_path = text
transcribe_path = /v1/audio/transcriptions
```

## 6.3 `app/schemas/llm.py`

Update `LlmConfigInspectResult`:

```python id="9f7gls"
discovery_status: Literal["fetched", "fallback", "manual_required", "failed"]
default_model_source: Literal["provider", "builtin", "manual", "none"]
requires_bearer_token: bool
supports_model_discovery: bool
warnings: list[str] = Field(default_factory=list)
```

Keep existing `notes` for human-readable detail.

---

# 7. STT service changes

File:

```text id="efohwz"
app/services/stt.py
```

## 7.1 Add STT OpenAPI helper functions

Add business-specific inference helpers:

```python id="slknpa"
def _candidate_stt_openapi_paths(openapi_path: str | None) -> list[str]:
    ...
```

Candidate paths:

```text id="whi7zr"
/openapi.json
/docs → /openapi.json
/redoc → /openapi.json
provided .json path
```

```python id="j6w9q4"
def _select_stt_operation(document: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    ...
```

Selection criteria:

* `POST`
* has `multipart/form-data`
* has likely file field
* text score includes:

  * `transcribe`
  * `transcription`
  * `speech`
  * `audio`
  * `stt`
  * `whisper`

Candidate score:

```text id="kstkz1"
+5 multipart/form-data
+5 binary/string file field
+3 path contains transcribe/transcription
+3 summary/operationId contains transcribe/stt/audio
+1 response schema contains text/transcript
```

```python id="s688ox"
def _infer_stt_request_contract(schema: dict[str, Any]) -> SttRequestContract:
    ...
```

Infer:

```text id="l1r0i7"
file_field_name
model_field_name
model_name
language_field_name
language
extra_form_fields_json
field_tips
```

Preferred model fields:

```text id="zbj42v"
model
model_id
model_name
engine
deployment
deployment_id
```

Preferred language fields:

```text id="344rey"
language
lang
locale
language_code
languageCode
```

File fields:

```text id="633is9"
file
audio
audio_file
upload
media
```

```python id="3d8s39"
def _infer_stt_response_contract(schema: dict[str, Any] | None) -> SttResponseContract:
    ...
```

Preferred transcript paths:

```text id="7l09t7"
text
transcript
transcription
result.text
data.text
```

Preferred segment paths:

```text id="8qk0y0"
segments
results
words
utterances
```

Segment fields:

```text id="kn8feq"
text: text/transcript/word
start: start/start_time/begin
end: end/end_time/stop
speaker: speaker/speaker_id/channel
```

## 7.2 Update `inspect_stt_contract`

Behavior:

### OpenAI cloud

* no OpenAPI fetch
* call models API if token provided
* else fallback to built-in transcription model list
* return standard OpenAI contract

### OpenAI-compatible REST

* try model discovery if token/base URL works
* default contract:

  ```text
  /v1/audio/transcriptions
  file
  model
  language
  text
  ```
* optionally allow OpenAPI path if provided later

### Generic REST

* fetch OpenAPI
* validate/dereference
* infer endpoint and fields
* optionally run synthetic ping if enough fields are known and bearer token is supplied when needed

## 7.3 Add STT synthetic ping

Add:

```python id="jrbtpj"
def _inspect_stt_provider_ping(
    *,
    base_url: str,
    transcribe_path: str,
    file_field_name: str,
    model_field_name: str | None,
    model_name: str | None,
    language_field_name: str | None,
    language: str | None,
    response_text_path: str,
    extra_form_fields_json: dict[str, str],
    bearer_token: str | None,
    sample_path: Path = DEFAULT_STT_SAMPLE_PATH,
) -> SttSyntheticTestResult:
    ...
```

Result schema can be inline initially:

```python id="00l6lt"
class SttSyntheticTestResult(BaseModel):
    success: bool
    duration_ms: int
    transcript_preview: str | None
    response_top_level_keys: list[str]
    error_code: str | None
    error_message: str | None
```

Add to `SttInspectResult` only if useful for UI. Otherwise include notes.

## 7.4 Update runtime `_transcribe_via_http`

Current behavior hard-codes `model` and `language`. Replace with:

```python id="yjxq49"
if model_name and model_field_name:
    form_fields[model_field_name] = model_name

if language and language_field_name:
    form_fields[language_field_name] = language
```

Update function signature:

```python id="f22omb"
def _transcribe_via_http(
    *,
    base_url: str,
    transcribe_path: str,
    file_field_name: str,
    response_text_path: str,
    extra_form_fields_json: dict[str, str] | None,
    bearer_token: str | None,
    model_name: str | None,
    model_field_name: str | None,
    language: str | None,
    language_field_name: str | None,
    ...
) -> str:
```

Update callers:

```text id="ffef06"
transcribe_with_team_stt
transcribe_with_stt_snapshot
run_saved_stt_config_test
```

## 7.5 Snapshot support

If queued transcript jobs snapshot STT config fields, update the job snapshot fields to include:

```text id="42vxvx"
model_field_name
language_field_name
segments_path
segment_* fields
```

If current queue snapshots are incomplete, add backward-compatible fallback:

```python id="l0zkvb"
model_field_name = snapshot_model_field_name or "model"
language_field_name = snapshot_language_field_name or "language"
```

---

# 8. LLM service changes

File:

```text id="oqj47u"
app/services/llm.py
```

## 8.1 Make discovery state explicit

Update `inspect_llm_contract`.

Current logic can stay mostly intact, but each branch must set:

```text id="shk4l5"
discovery_status
default_model_source
requires_bearer_token
supports_model_discovery
warnings
```

## 8.2 OpenAI chat behavior

If token provided and models fetched:

```python id="jifemy"
discovery_status = "fetched"
default_model_source = "provider"
supports_model_discovery = True
requires_bearer_token = True
warnings = []
```

If no token:

```python id="ck8ca1"
discovery_status = "fallback"
default_model_source = "builtin"
warnings = ["No API key was provided; using built-in OpenAI chat model defaults."]
```

If token provided but fetch fails:

```python id="d4h1a3"
discovery_status = "fallback"
default_model_source = "builtin"
warnings = ["Live OpenAI model discovery failed; verify API key/base URL."]
```

## 8.3 Bedrock behavior

If token provided and models fetched:

```python id="vy6vxk"
discovery_status = "fetched"
default_model_source = "provider"
```

If no token or discovery fails:

```python id="7wn1rp"
discovery_status = "manual_required"
default_model_source = "manual"
available_models = []
model_name = None
warnings = ["Could not load region-specific Bedrock models. Enter a model ID manually or inspect again with credentials."]
```

## 8.4 Ollama behavior

If `/api/tags` succeeds:

```python id="ykdv6n"
discovery_status = "fetched"
default_model_source = "provider"
requires_bearer_token = False
supports_model_discovery = True
```

If it fails:

```python id="xz4ypb"
discovery_status = "manual_required"
default_model_source = "manual"
warnings = ["Could not reach Ollama /api/tags. Verify the base URL and network access."]
```

## 8.5 Save behavior

Do not make inspection status block saving. Admin can save a manual model for Bedrock/Ollama if validation passes.

Keep `upsert_llm_config` validation:

* model name required for known adapters
* bearer token required for OpenAI/Bedrock creation
* Ollama can be no-auth

---

# 9. Admin UI flow changes

Likely files:

```text id="fj0ykb"
app/templates/admin.html
app/routes/web_admin.py
app/web/presentation.py
```

The current admin template is very large. Prefer adding small, targeted sections. If possible, extract provider forms into partial-like helper functions or template includes in a later refactor, but avoid mixing this inspection work with a broad admin-page rewrite.

## 9.1 STT admin flow

### Screen section: “Provision STT provider”

Fields:

```text id="064grl"
Team
Label
Adapter kind
Base URL
OpenAPI/docs path
Bearer token
```

Primary button:

```text id="5nkbhl"
Inspect STT API
```

Secondary:

```text id="3ditjj"
Save manually
```

### Inspect result panel

Show:

```text id="xwcuq7"
Inspection status
Selected transcription endpoint
Candidate endpoints
File field
Model field
Default model
Available models
Language field
Default language
Response text path
Segments path, if detected
Extra form fields
Warnings/notes
Synthetic ping preview, if run
```

### Save form after inspect

Prefill:

```text id="dwjzll"
transcribe_path
file_field_name
model_field_name
model_name
language_field_name
language
response_text_path
extra_form_fields_json
segments fields
```

Do not prefill bearer token. Show:

```text id="4zx05a"
Re-enter bearer token to save credentials. Tokens are not retained after inspection responses.
```

## 9.2 LLM admin flow

Rename UI action from “inspect” to:

```text id="7obhq8"
Discover models
```

Fields:

```text id="qbrvk0"
Team
Label
Adapter kind
Base URL
Bearer token
Bedrock region
```

Inspect result panel:

```text id="nnzn73"
Discovery status
Default model source
Available models
Default model
Warnings
Manual model input, if required
```

Examples:

* OpenAI without key:

  ```text
  Built-in defaults shown. Enter an API key to fetch live provider models.
  ```

* Bedrock without key:

  ```text
  Model discovery requires credentials. Enter model ID manually or inspect again with a key.
  ```

* Ollama failure:

  ```text
  Could not reach Ollama. Verify local service and base URL.
  ```

## 9.3 Selection UI remains separate

Do not combine provisioning with team selection.

Keep:

```text id="kwh5ye"
System admin provisions providers.
Team leader selects active provider/model.
User picks personal LLM preference within team-allowed list.
```

## 9.4 Error UI

If inspection fails:

* retain non-secret inputs
* show safe error message
* do not show raw exception
* do not show token
* do not log raw provider response bodies

---

# 10. API behavior

Existing API routes can remain:

```text id="jol9cj"
/api/v1/stt-configs/inspect
/api/v1/stt-configs
/api/v1/llm-configs/inspect
/api/v1/llm-configs
```

The current API route structure already separates inspect and save. 

## 10.1 Backward compatibility

Existing clients sending old STT payloads without `model_field_name` or `language_field_name` should continue working.

Default:

```text id="h9v1uc"
model_field_name = model
language_field_name = language
```

## 10.2 Response versioning

No API version bump needed if fields are additive.

---

# 11. Testing plan

## 11.1 STT unit tests

Add tests for:

### OpenAPI inference

Fixture:

```json id="zcne6t"
{
  "paths": {
    "/speech/transcribe": {
      "post": {
        "requestBody": {
          "content": {
            "multipart/form-data": {
              "schema": {
                "type": "object",
                "properties": {
                  "audio_file": {"type": "string", "format": "binary"},
                  "model_id": {"type": "string", "default": "clinic-whisper"},
                  "lang": {"type": "string", "example": "en"}
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "content": {
              "application/json": {
                "schema": {
                  "type": "object",
                  "properties": {
                    "transcript": {"type": "string"}
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

Assert:

```text id="d0kt5i"
transcribe_path = /speech/transcribe
file_field_name = audio_file
model_field_name = model_id
model_name = clinic-whisper
language_field_name = lang
language = en
response_text_path = transcript
```

### Runtime field use

Mock `httpx.post`.

Assert request data uses:

```text id="rrx9ho"
model_id
lang
```

and does not send:

```text id="prbyu6"
model
language
```

unless those are the configured field names.

### Fallback/backcompat

Existing configs with no new fields use:

```text id="ovwym6"
model
language
```

### Response extraction

Test:

```text id="r4pknh"
text
result.text
$.results[0].alternatives[0].transcript
```

### Error handling

Test:

```text id="2xhtj5"
invalid OpenAPI JSON
no multipart POST operation
401/403 on OpenAPI fetch
network timeout
invalid response JSON on ping
```

## 11.2 LLM unit tests

Add tests for:

### OpenAI fetched

Mock OpenAI model list.

Assert:

```text id="virlvu"
discovery_status = fetched
default_model_source = provider
available_models non-empty
```

### OpenAI fallback

No token or failed fetch.

Assert:

```text id="3at24z"
discovery_status = fallback
default_model_source = builtin
warnings non-empty
```

### Bedrock manual required

No token.

Assert:

```text id="qw5b9k"
discovery_status = manual_required
default_model_source = manual
available_models = []
```

### Ollama fetched

Mock `/api/tags`.

Assert models are parsed.

### Ollama failure

Assert manual required with warning.

## 11.3 Admin UI tests

Add/extend tests in `tests/test_admin_ui.py`.

Test:

1. STT inspect page renders inferred dynamic fields.
2. STT inspect result does not render bearer token.
3. STT save form requires re-entered bearer token for new bearer-auth config.
4. LLM “Discover models” wording appears.
5. LLM discovery statuses render differently:

   * fetched
   * fallback
   * manual required
6. Failed inspect preserves non-secret inputs.
7. Provider selection UI still works after schema changes.

## 11.4 Migration tests

Add migration test verifying:

```text id="njtqfh"
new columns exist
existing STT configs default to model/language behavior
downgrade drops columns
```

---

# 12. Documentation plan

Update:

```text id="5efzis"
docs/api.md
docs/stt-config.md
docs/admin_brief.md
docs/testing.md
docs/progress.md
```

## Include

### Inspect/save/select lifecycle

```text id="q53k5x"
Inspect proposes values.
Save persists provider config and Vault secret references.
Select activates a saved provider for a team.
User preference chooses among team-allowed LLM models.
```

### STT field mapping

Document:

```text id="ztaznq"
file_field_name
model_field_name
language_field_name
response_text_path
segments_path
extra_form_fields_json
```

### LLM discovery states

Document:

```text id="x0o7z3"
fetched
fallback
manual_required
failed
```

### Security

Document:

```text id="ysba1u"
No tokens rendered after inspect.
No real clinical content sent during inspect.
Provider response bodies are not logged.
Remote HTTP blocked except local/private endpoints as already implemented.
```

---

# 13. Work breakdown for agents

## Agent 1 — Schema and migration

### Scope

Implement DB/model/schema additions for STT dynamic request/response fields and LLM discovery status fields.

### Files

```text id="eh0m8k"
app/models.py
app/schemas/stt.py
app/schemas/llm.py
alembic/versions/<new_revision>_add_stt_dynamic_contract_fields.py
tests/test_migrations.py
```

### Tasks

1. Add STT fields to `TeamSttConfig`.
2. Add Pydantic fields to STT upsert/detail/inspect schemas.
3. Add LLM inspect status fields.
4. Add Alembic migration.
5. Add migration tests.
6. Preserve current behavior with defaults.

### Acceptance criteria

```text id="4p3b58"
pytest migration tests pass
old STT payloads still validate
new STT fields round-trip through schema
LLM inspect result schema exposes machine-readable status fields
```

---

## Agent 2 — Shared OpenAPI/JSON-path helpers

### Scope

Introduce reusable inspection helpers without changing business behavior yet.

### Files

```text id="e6iow1"
app/services/provider_inspection.py
tests/test_provider_inspection.py
requirements.txt
```

### Tasks

1. Add dependencies.
2. Implement OpenAPI fetch/validate/dereference helpers.
3. Implement schema helpers.
4. Implement JSONPath extraction with backward-compatible dot path support.
5. Add tests with synthetic OpenAPI docs.

### Acceptance criteria

```text id="a1bpx0"
local refs resolve
nested refs resolve
invalid OpenAPI documents fail safely
dot paths and JSONPath both work
no secrets included in raised AppError details
```

---

## Agent 3 — STT inspection and runtime contract

### Scope

Upgrade STT inspection and runtime transcription to use dynamic field names.

### Files

```text id="i1j7hq"
app/services/stt.py
tests/test_stt.py or tests/test_admin_ui.py as appropriate
```

### Tasks

1. Add STT OpenAPI operation selection.
2. Add request contract inference.
3. Add response contract inference.
4. Add optional synthetic ping.
5. Update `_transcribe_via_http` to use `model_field_name` and `language_field_name`.
6. Update all callers.
7. Add focused tests.

### Acceptance criteria

```text id="9nt0ql"
generic OpenAPI STT contract inferred
runtime sends configured model/language field names
OpenAI-compatible behavior unchanged
bearer token never rendered/logged
synthetic audio only used in ping
```

---

## Agent 4 — LLM discovery status upgrade

### Scope

Make LLM inspection states explicit and UI-friendly.

### Files

```text id="j66wff"
app/services/llm.py
app/schemas/llm.py
tests/test_llm.py or equivalent
```

### Tasks

1. Add explicit status fields to inspect result construction.
2. Update OpenAI branch.
3. Update Bedrock branch.
4. Update Ollama branch.
5. Preserve existing model validation and selection behavior.
6. Add tests.

### Acceptance criteria

```text id="vm869c"
OpenAI fetched/fallback states distinguishable
Bedrock manual-required state distinguishable
Ollama fetched/manual-required states distinguishable
existing LLM config save/select tests pass
```

---

## Agent 5 — Admin UI upgrade

### Scope

Improve admin provider provisioning flow for STT and LLM without broad layout refactor.

### Files

```text id="90s4bq"
app/routes/web_admin.py
app/templates/admin.html
app/web/presentation.py
tests/test_admin_ui.py
```

### Tasks

1. Add STT inspect result panel.
2. Add dynamic STT field inputs.
3. Ensure save form is prefilled from inspect result.
4. Rename LLM action to “Discover models.”
5. Render LLM discovery status badges/messages.
6. Ensure bearer token is never rendered back into HTML.
7. Add UI tests.

### Acceptance criteria

```text id="ykw1xh"
admin can inspect STT API
admin can review inferred fields
admin can save after re-entering token
admin can discover LLM models
fallback/manual-required states are clear
no secret token appears in response HTML
```

---

## Agent 6 — Docs and final integration

### Scope

Update docs and run full validation.

### Files

```text id="5zpdxk"
docs/api.md
docs/stt-config.md
docs/admin_brief.md
docs/testing.md
docs/progress.md
```

### Tasks

1. Document inspect/save/select lifecycle.
2. Document STT field mapping.
3. Document LLM discovery states.
4. Document security rules.
5. Run full tests.
6. Add progress note.

### Acceptance criteria

```text id="60jrzh"
docs explain new behavior
progress note added
full pytest suite passes
open risks documented
```

---

# 14. Agent-facing prose to include in task brief

Use this as the guiding text for coding agents:

```text id="z3xc9v"
Implement STT and LLM provider inspection upgrades without weakening OpenScribe’s privacy, ownership, provider-secret, or deletion invariants.

Provider inspection is a configuration discovery step only. It must not activate providers, create selections, persist raw bearer tokens from inspection, or send user transcript/note content to remote services.

For STT, upgrade the saved provider contract so runtime requests are built from persisted field names, not hard-coded model/language keys. Generic OpenAPI STT providers should infer endpoint path, file field, model field, language field, extra form fields, response text path, and optional segment fields. Runtime transcription must use those saved fields exactly.

For LLM, keep scope to known adapter families: OpenAI-compatible chat, Bedrock Mantle, and Ollama. Do not implement arbitrary generic LLM REST contracts in this change. Improve model discovery results by returning machine-readable statuses: fetched, fallback, manual_required, or failed.

Admin UI must make the lifecycle clear:
Inspect or discover → review inferred values → save provider → separately select provider/model for team. Bearer tokens must never be rendered back into HTML, JSON responses, logs, or hidden fields.

Use synthetic-only inspection samples. STT inspection may use bundled synthetic audio. No transcript text, note text, prompt containing patient data, generated output, or redaction original values may be used for provider inspection.

Every meaningful change must include tests and docs. Follow AGENTS.md checklist/checkpoint workflow. Keep changes small and vertical. Avoid broad admin template refactors unless necessary for the feature.
```

---

# 15. Suggested implementation order

## Slice 1 — Safe schema foundation

```text id="kau69r"
migration
models
schemas
migration tests
```

No behavior changes yet.

## Slice 2 — Shared OpenAPI/JSONPath helpers

```text id="dmqptk"
provider_inspection.py
unit tests
requirements
```

No UI changes yet.

## Slice 3 — STT runtime correctness

```text id="r84rox"
dynamic model/language fields
backward-compatible defaults
runtime tests
```

This is the most important correctness fix.

## Slice 4 — STT inspection inference

```text id="h4z9os"
OpenAPI inference
synthetic ping
service tests
```

## Slice 5 — LLM discovery status

```text id="fz9ze4"
machine-readable discovery state
branch-specific warnings
tests
```

## Slice 6 — Admin UI

```text id="fvtsuv"
STT review panel
LLM discovery panel
secret-safe rendering
UI tests
```

## Slice 7 — Docs and full regression

```text id="rdiw5e"
docs
progress note
full pytest
```

---

# 16. Definition of done

The work is complete when:

```text id="d5vpst"
1. Existing STT configs still work.
2. STT generic REST configs can store model_field_name and language_field_name.
3. STT runtime sends provider-specific model/language fields.
4. STT inspect can infer fields from a synthetic OpenAPI document.
5. LLM inspect returns explicit discovery statuses.
6. Admin UI clearly distinguishes STT inspection from LLM model discovery.
7. Bearer tokens are never rendered after inspect.
8. Inspection sends only synthetic data.
9. Migration tests pass.
10. Provider service tests pass.
11. Admin UI tests pass.
12. Docs and progress note are updated.
```

## Main risk to watch

The main risk is accidentally turning inspection into a runtime inference system. Avoid that. Runtime must remain boring: load saved config, build request from saved fields, call provider, parse response from saved path.
