# Implementation Plan: STT Branded Presets + Wizard Flow

## Current `master` state

STT is currently more “adapter/config form” than “provider wizard.”

The main branch has:

* `SttAdapterKind` values:

  * `generic_rest`
  * `openai_cloud`
  * `openai_compatible_rest`
* `TeamSttConfig` fields for endpoint shape, model/language fields, response paths, extra form fields, credential status, fingerprint, inspection metadata, active state, and Vault secret ref. 
* `SttConfigUpsert` applies defaults from `adapter_kind`, not from a branded provider preset. 
* `SttInspectRequest` also applies defaults from `adapter_kind`, with OpenAI cloud using `https://api.openai.com/v1` and generic REST using `/openapi.json`. 
* `stt_form_defaults()` still exposes a dense adapter-first form with many endpoint/path/field inputs. 
* The STT service already has useful mature pieces that LLM did not initially have: `credential_status`, `credential_fingerprint`, duplicate-credential checks, inspection metadata, and invalid credential handling. 

So the right approach is **not** to rewrite STT. It is to add the LLM-style provider preset + draft/finalize wizard on top of the existing STT inspection/credential infrastructure.

---

# Product Goal

Make admin STT setup feel like the new LLM setup flow:

```text
Choose provider
→ enter only API key / endpoint details
→ check API key and find transcription options
→ create pending draft and save credential
→ choose model/language/default settings
→ save provider
```

Admins should not need to understand:

```text
adapter_kind
transcribe_path
file_field_name
response_text_path
segments_path
OpenAPI path
model_field_name
language_field_name
```

unless they choose an advanced/custom provider.

---

# Recommended MVP Scope

## Include in first STT preset slice

### 1. OpenAI

Keep the current `openai_cloud` adapter.

Current code already knows OpenAI transcription models:

```text
gpt-4o-mini-transcribe
gpt-4o-transcribe
gpt-4o-transcribe-diarize
whisper-1
```

The service already has `SUPPORTED_OPENAI_TRANSCRIPTION_MODELS` and a model listing path for OpenAI transcription models. 

### 2. Custom OpenAI-compatible STT

Keep current `openai_compatible_rest`.

This covers self-hosted or compatible APIs that implement OpenAI-style:

```text
POST /v1/audio/transcriptions
multipart file field = file
model field = model
response text path = text
```

### 3. Generic REST / OpenAPI-discovered STT

Keep current `generic_rest`.

This remains the advanced escape hatch for services with OpenAPI docs or custom field mappings.

### 4. Deepgram

Deepgram is a major STT provider. Its prerecorded audio API uses `POST /v1/listen`, supports a model query such as `model=nova-3`, and authenticates with an API key. Its docs show `https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true`. ([Deepgram Docs][1])

Deepgram does **not** map perfectly to the current generic multipart shape, so implement it as a branded native-ish HTTP preset/adapter rather than forcing it through OpenAI-compatible.

### 5. ElevenLabs

ElevenLabs now has a first-class Speech to Text API with Scribe models, timestamps, speaker diarization, entity detection, and support for long files. Its docs describe Scribe v2 and a speech-to-text API response containing `text`, `words`, language information, and speaker IDs. ([ElevenLabs][2])

This should be a branded preset. Whether it uses a dedicated adapter or generic multipart depends on its exact endpoint request shape, but the UI should not expose that complexity.

---

## Defer from first slice

### Azure AI Speech

Azure Speech to Text REST API has fast transcription and batch transcription, and the latest generally available REST version is `2025-10-15`. Its fast transcription endpoint is `/speechtotext/transcriptions:transcribe`. ([Microsoft Learn][3])

Defer unless you want a dedicated Azure STT adapter now. Azure brings region/resource endpoint semantics and API-versioning complexity similar to Azure OpenAI.

### Google Cloud Speech-to-Text

Google Cloud Speech-to-Text v2 has recognizers and `recognize` / `batchRecognize` endpoints under `speech.googleapis.com`. ([Google Cloud][4])

Defer because it requires Google Cloud project/location/recognizer semantics and typically OAuth/service-account auth rather than simple bearer-key setup.

### AWS Transcribe

AWS Transcribe’s `StartTranscriptionJob` expects media in S3 and creates an async transcription job with region, job name, media URI, and language parameters. ([AWS Documentation][5])

Defer because the current OpenScribe STT path is synchronous/direct-audio upload, while AWS Transcribe is usually S3 + async job orchestration.

### AssemblyAI / Speechmatics

These are good later candidates, but both are more naturally async job-style providers. Do them after the synchronous HTTP preset architecture is stable.

---

# Proposed Provider Presets

Add:

```python
class SttProviderPreset(str, enum.Enum):
    openai = "openai"
    deepgram = "deepgram"
    elevenlabs = "elevenlabs"
    custom_openai_compatible = "custom_openai_compatible"
    custom_rest_openapi = "custom_rest_openapi"
```

Optional later:

```python
azure_speech = "azure_speech"
google_speech = "google_speech"
aws_transcribe = "aws_transcribe"
assemblyai = "assemblyai"
speechmatics = "speechmatics"
```

## Preset mapping

| UI preset                | Adapter/protocol                                               | MVP status |
| ------------------------ | -------------------------------------------------------------- | ---------- |
| OpenAI                   | `openai_cloud`                                                 | Include    |
| Deepgram                 | new `deepgram_prerecorded` or generic native HTTP adapter      | Include    |
| ElevenLabs               | new `elevenlabs_speech_to_text` or generic native HTTP adapter | Include    |
| Custom OpenAI-compatible | `openai_compatible_rest`                                       | Include    |
| Custom REST/OpenAPI      | `generic_rest`                                                 | Include    |
| Azure Speech             | future `azure_speech_fast_transcription`                       | Defer      |
| Google Speech-to-Text    | future `google_speech_v2`                                      | Defer      |
| AWS Transcribe           | future async job adapter                                       | Defer      |

---

# Data Model Changes

## Add provider preset to STT config

Add to `TeamSttConfig`:

```python
provider_preset: Mapped[str] = mapped_column(
    String(64),
    default=SttProviderPreset.custom_rest_openapi.value,
    nullable=False,
)
```

## Add setup status to STT config

Mirror LLM:

```python
class SttConfigSetupStatus(str, enum.Enum):
    pending_model_selection = "pending_model_selection"
    ready = "ready"
```

Add:

```python
setup_status: Mapped[SttConfigSetupStatus] = mapped_column(
    Enum(SttConfigSetupStatus),
    default=SttConfigSetupStatus.ready,
    server_default=SttConfigSetupStatus.ready.value,
    nullable=False,
)
```

Reason: `is_active=False` should mean “not available for selection,” not “setup incomplete.” LLM now has an explicit `setup_status`; STT should match.

## Add unique label index

LLM now enforces unique labels per team. Do the same for STT:

```python
Index(
    "uq_team_stt_configs_team_label_lower",
    "team_id",
    text("lower(btrim(label))"),
    unique=True,
)
```

Backfill duplicate labels with `copy N` using the same pattern as the LLM label migration.

---

# Migration Plan

Create a new Alembic migration after the current `master` head.

## Migration fields

Add:

```text
team_stt_configs.provider_preset
team_stt_configs.setup_status
```

Backfill:

| Existing STT config      | New preset                 |
| ------------------------ | -------------------------- |
| `openai_cloud`           | `openai`                   |
| `openai_compatible_rest` | `custom_openai_compatible` |
| `generic_rest`           | `custom_rest_openapi`      |

Backfill setup status:

```text
model_name exists OR adapter is generic_rest and response path exists → ready
otherwise → pending_model_selection
```

Given current validation requires strong config shape, most existing rows should become `ready`.

Add unique label index after deduplication.

---

# STT Provider Preset Catalog

Create:

```text
app/services/stt_presets.py
```

Equivalent to `llm_presets.py`.

Suggested structure:

```python
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
    default_model_field_name: str | None = "model"
    default_file_field_name: str = "file"
    default_language_field_name: str | None = "language"
    default_response_text_path: str = "text"
    default_segments_path: str | None = None
    help_text: str = ""
```

Initial presets:

```python
STT_PROVIDER_PRESETS = {
    "openai": SttProviderPresetDefinition(
        key="openai",
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
    "deepgram": SttProviderPresetDefinition(
        key="deepgram",
        display_name="Deepgram",
        adapter_kind=SttAdapterKind.generic_rest,  # or new SttAdapterKind.deepgram_prerecorded
        default_base_url="https://api.deepgram.com",
        transcribe_path="/v1/listen",
        auth_header_style="token",
        requires_api_key=True,
        supports_model_discovery=False,
        supports_language_selection=True,
        supports_diarization=True,
        default_response_text_path="results.channels.0.alternatives.0.transcript",
    ),
    "elevenlabs": SttProviderPresetDefinition(
        key="elevenlabs",
        display_name="ElevenLabs",
        adapter_kind=SttAdapterKind.generic_rest,  # or new SttAdapterKind.elevenlabs_speech_to_text
        default_base_url="https://api.elevenlabs.io",
        transcribe_path="/v1/speech-to-text",
        auth_header_style="xi-api-key",
        requires_api_key=True,
        supports_model_discovery=False,
        supports_language_selection=True,
        supports_diarization=True,
        default_response_text_path="text",
    ),
    "custom_openai_compatible": SttProviderPresetDefinition(
        key="custom_openai_compatible",
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
    "custom_rest_openapi": SttProviderPresetDefinition(
        key="custom_rest_openapi",
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
```

---

# Adapter Decision

## Recommended first implementation

Do **not** build full async STT job infrastructure yet.

Use two synchronous adapter families:

```text
openai_stt
generic_multipart_stt
```

Then add provider-specific auth/header/body behavior for Deepgram and ElevenLabs.

## Needed extension to current STT transport

Current `_transcribe_via_http()` assumes:

```text
Authorization: Bearer <token>
multipart form file field
form fields for model/language
JSON response text path
```

That is enough for OpenAI-compatible APIs, but not enough for all branded providers.

Add a small deep module:

```text
app/services/stt_transport.py
```

or keep in `stt.py` initially but with clean functions:

```python
@dataclass(frozen=True)
class SttTransportRequest:
    provider_preset: str
    base_url: str
    transcribe_path: str
    bearer_token: str | None
    audio_bytes: bytes
    filename: str
    content_type: str
    model_name: str | None
    language: str | None
    extra_form_fields: dict[str, str]
```

and:

```python
def transcribe_with_stt_transport(request: SttTransportRequest) -> dict[str, Any]:
    ...
```

This function can handle:

| Provider          | Request style                          |
| ----------------- | -------------------------------------- |
| OpenAI            | OpenAI SDK or multipart                |
| OpenAI-compatible | multipart                              |
| Deepgram          | provider-specific headers/query/body   |
| ElevenLabs        | provider-specific headers/multipart    |
| Custom REST       | current generic multipart/OpenAPI path |

---

# Wizard UX

## Step 1: Choose provider and credential

Show only:

```text
Team
Provider
API key
[Check API key and find transcription options]
```

Provider-specific additions:

### OpenAI

```text
Provider: OpenAI
API key
```

No base URL by default.

### Deepgram

```text
Provider: Deepgram
API key
```

Optional advanced:

```text
Base URL
Default query params
```

### ElevenLabs

```text
Provider: ElevenLabs
API key
```

Optional advanced:

```text
Base URL
Model ID
```

### Custom OpenAI-compatible

```text
Provider: Custom OpenAI-compatible
Base URL
API key
```

### Custom REST/OpenAPI

```text
Provider: Custom REST/OpenAPI
Base URL
OpenAPI path
API key optional
```

Only custom REST should show the advanced field mapping controls by default.

---

## Step 2: Inspection / draft result

After successful inspection:

```text
Credential: saved
Provider name
Default model
Default language
Available for team selection
[Save provider]
[Replace API key]
[Delete incomplete setup]
```

For providers without live model discovery:

```text
Credential: saved
Provider name
Model
Language
Available for team selection
[Save provider]
```

Do not show the API key again.

---

## Step 3: Ready provider

Provider cards should mirror LLM:

```text
Setup incomplete
Ready · available
Ready · unavailable
Invalid credential
Partial inspection
```

STT already has `credential_status`, so STT can display a richer status than LLM:

| `setup_status`            | `credential_status` | Display                       |
| ------------------------- | ------------------- | ----------------------------- |
| `pending_model_selection` | any                 | Setup incomplete              |
| `ready`                   | `verified`          | Ready · available/unavailable |
| `ready`                   | `partial`           | Ready with warnings           |
| `ready`                   | `invalid`           | Invalid credential            |
| `ready`                   | `degraded`          | Degraded                      |

---

# API Shape

Follow LLM naming and behavior.

## New schemas

```python
class SttConfigDraftCreate(BaseModel):
    team_id: UUID
    provider_preset: SttProviderPreset
    label: str | None = Field(default=None, max_length=255)
    base_url: str = Field(default="", max_length=2048)
    openapi_path: str | None = Field(default=None, max_length=255)
    bearer_token: str | None = Field(default=None, min_length=1)
```

```python
class SttConfigDraftCreateResult(BaseModel):
    config: SttConfigDetail
    provider_display_name: str
    available_models: list[str]
    available_model_options: list[SttModelOption]
    credential_status: ProviderCredentialStatus
    warnings: list[str] = []
    notes: list[str] = []
```

```python
class SttConfigFinalize(BaseModel):
    team_id: UUID
    config_id: UUID
    label: str
    model_name: str | None = None
    language: str | None = None
    is_active: bool = True
```

```python
class SttConfigDraftReplaceCredential(BaseModel):
    team_id: UUID
    config_id: UUID
    bearer_token: str
```

## New API routes

```text
POST /api/v1/stt-configs/drafts
POST /api/v1/stt-configs/{config_id}/finalize
POST /api/v1/stt-configs/{config_id}/replace-credential
```

Keep existing:

```text
POST /api/v1/stt-configs
POST /api/v1/stt-configs/inspect
POST /api/v1/stt-configs/{config_id}/inspect
DELETE /api/v1/stt-configs/{config_id}
```

The old upsert path remains for backwards compatibility and advanced full-form edits.

---

# Service Layer Plan

## Add draft creation

```python
def create_stt_config_draft(
    db: Session,
    actor: User,
    payload: SttConfigDraftCreate,
) -> tuple[TeamSttConfig, SttInspectResult]:
    ...
```

Behavior:

1. Require system admin.
2. Resolve team.
3. Resolve provider preset.
4. Apply preset defaults.
5. Require API key if preset requires one.
6. Inspect provider.
7. Derive credential status:

   * invalid key → error; do not create draft
   * reachable + usable → `verified`
   * reachable but incomplete metadata → `partial`
   * custom/manual → `pending_inspection` or `partial`
8. Create `TeamSttConfig`:

   * `setup_status=pending_model_selection`
   * `is_active=False`
   * no team selection
   * credential saved in Vault
   * `credential_fingerprint` stored
   * `inspection_metadata_json` saved
9. Return config + inspection.

## Add finalization

```python
def finalize_stt_config_draft(
    db: Session,
    actor: User,
    payload: SttConfigFinalize,
) -> TeamSttConfig:
    ...
```

Behavior:

1. Require system admin.
2. Load draft by team/config ID.
3. Validate label uniqueness.
4. If provider has model list, require selected model to be in list.
5. If provider does not expose model list, allow manual model if preset allows it.
6. Set:

   * label
   * model
   * language
   * `setup_status=ready`
   * `is_active=payload.is_active`
7. Do not select it as the team default STT provider automatically.

## Add credential replacement

```python
def replace_stt_config_draft_credential(
    db: Session,
    actor: User,
    payload: SttConfigDraftReplaceCredential,
) -> tuple[TeamSttConfig, SttInspectResult]:
    ...
```

Behavior:

1. Re-run inspection with new key.
2. Store new Vault secret.
3. Update fingerprint.
4. Update inspection metadata.
5. Reset `setup_status=pending_model_selection`.
6. Set `is_active=False`.
7. Clear stale model if no longer available.

---

# Selection Rules

Tighten selection to mirror LLM.

Current `list_selectable_stt_configs()` filters only:

```text
team_id
is_active=True
```

and `set_team_stt_selection()` filters active and non-invalid credential status. 

Change both to require:

```python
TeamSttConfig.is_active.is_(True)
TeamSttConfig.setup_status == SttConfigSetupStatus.ready
TeamSttConfig.credential_status != ProviderCredentialStatus.invalid
```

Also require a usable credential through the existing `ensure_stt_config_credential_ready()` call.

---

# UI Plan

Update both:

```text
app/templates/admin.html
app/templates/admin2.html
```

## Provider list

Show cards/table rows with:

```text
Provider label
Provider brand
Credential status
Setup status
Default model
Language
Actions
```

Statuses:

```text
Setup incomplete
Ready · available
Ready · unavailable
Invalid credential
Partial inspection
```

Actions for incomplete setup:

```text
Continue setup
Delete incomplete setup
```

Actions for ready setup:

```text
Edit
Replace API key
Delete
```

## New provider form

### Initial step

```text
Provider
API key
[Check API key and find transcription options]
```

Only show advanced endpoint fields for:

```text
Custom OpenAI-compatible
Custom REST/OpenAPI
```

### Model/settings step

```text
Credential: saved
Provider name
Default model
Default language
Available for team selection
[Save provider]
[Replace API key]
[Delete incomplete setup]
```

### Custom REST/OpenAPI advanced step

For custom OpenAPI only, keep the detailed technical fields:

```text
Base URL
OpenAPI path
Transcribe path
File field
Model field
Language field
Response text path
Segments path
Segment field mapping
Extra form fields
```

This preserves the current power-user functionality without forcing it on normal admins.

---

# Provider Inspection Behavior

## OpenAI

Use existing OpenAI model listing and transcription model filter. The code already has `SUPPORTED_OPENAI_TRANSCRIPTION_MODELS`. 

If model listing fails due to credential rejection, fail hard and do not create draft.

If model listing is unavailable for non-auth reasons, allow manual model entry only if you decide to preserve current fallback behavior. I would avoid model defaults for consistency with LLM, except OpenAI’s current known transcription list already exists in code.

## Deepgram

First implementation can use static model choices or manual model entry, because Deepgram’s API does not require a model list endpoint for basic use. Use a default model field like:

```text
nova-3
```

But if you want consistency with LLM’s “no curated defaults” rule, then make model optional and rely on Deepgram’s endpoint defaults. Deepgram docs show `model=nova-3` in examples, but the provider can operate based on API parameters. ([Deepgram Docs][1])

Recommended UX:

```text
Model: optional
Smart format: on by default
Diarization: optional toggle later
```

## ElevenLabs

ElevenLabs docs currently surface Scribe v2 as the primary STT model and show a response with `text` and word-level metadata. ([ElevenLabs][6])

Recommended UX:

```text
Model: Scribe v2 or manual model ID
Language: optional
Diarization: optional later
```

If you want strict “no defaults,” make model manually entered or inferred from provider response, but this will make ElevenLabs setup worse. I recommend allowing provider preset defaults for STT because STT providers often have fewer exposed model-list APIs than LLM providers.

---

# Key Product Decision Needed

For LLM we decided:

```text
live auto-discovery only; no curated model defaults
```

For STT, that rule may be too strict. Many STT providers do not expose a clean model-list endpoint, or they expose model choice as query/form params rather than a formal `/models` API.

## Recommendation

For STT, use a different rule:

```text
Use live discovery where available.
Use provider preset defaults where the provider does not expose model discovery.
Allow manual model override.
```

This is more practical for STT.

---

# Implementation Sequence

## Phase 1 — Preset catalog and schema

1. Add `SttProviderPreset`.
2. Add `SttConfigSetupStatus`.
3. Add `provider_preset` and `setup_status` to `TeamSttConfig`.
4. Add `app/services/stt_presets.py`.
5. Add `SttConfigDraftCreate`, `SttConfigDraftCreateResult`, `SttConfigFinalize`, `SttConfigDraftReplaceCredential`.
6. Extend `SttConfigDetail` with:

   * `provider_preset`
   * `provider_display_name`
   * `setup_status`
   * `setup_status_label`

## Phase 2 — Migration

1. Add columns.
2. Backfill provider presets from adapter kind.
3. Backfill setup status.
4. Deduplicate labels.
5. Add normalized unique label index.
6. Add migration tests.

## Phase 3 — Service layer

1. Add provider default resolution.
2. Add `create_stt_config_draft()`.
3. Add `finalize_stt_config_draft()`.
4. Add `replace_stt_config_draft_credential()`.
5. Tighten selectable STT config queries.
6. Keep existing upsert path for advanced/backwards-compatible saves.
7. Preserve duplicate credential fingerprint checks.

## Phase 4 — Provider-specific transport

1. Keep OpenAI path as-is.
2. Add Deepgram request handling.
3. Add ElevenLabs request handling.
4. Keep generic OpenAI-compatible multipart.
5. Keep generic REST/OpenAPI mapping.
6. Add tests with mocked HTTP responses.

## Phase 5 — API routes

Add:

```text
POST /api/v1/stt-configs/drafts
POST /api/v1/stt-configs/{config_id}/finalize
POST /api/v1/stt-configs/{config_id}/replace-credential
```

Update response helpers.

## Phase 6 — Admin browser routes

Add browser form routes matching the API:

```text
POST /admin/stt-configs/drafts
POST /admin/stt-configs/{config_id}/finalize
POST /admin/stt-configs/{config_id}/replace-credential
```

Reuse existing delete route for incomplete setup deletion.

## Phase 7 — Templates

Update `admin.html` and `admin2.html` with the STT wizard states.

Keep advanced fields only for custom REST/OpenAPI.

## Phase 8 — Tests

### Unit tests

```text
stt preset catalog returns expected defaults
provider preset inference from existing adapter kind
label uniqueness helper
Deepgram response extraction
ElevenLabs response extraction
OpenAI model discovery still works
```

### Migration tests

```text
provider_preset added/backfilled
setup_status added/backfilled
duplicate STT labels deduped
unique label index exists
```

### API tests

```text
system admin can create STT draft
draft saves credential to Vault
draft does not return raw API key
draft is setup_status=pending_model_selection
draft is not selectable
finalize makes ready
ready + active is selectable
pending cannot be selected by direct POST
replace credential re-inspects and resets to pending
delete incomplete setup deletes Vault secret
duplicate label rejected
invalid credential rejected without draft creation
```

### Admin UI tests

```text
new STT setup shows provider/API-key step only
technical fields hidden for branded presets
custom REST shows advanced fields
after draft creation API key field hidden
Continue setup skips API key
Replace API key visible
Save provider finalizes
Setup incomplete card visible
Ready · available/unavailable states visible
```

---

# Suggested First PR Scope

Keep the first PR focused:

```text
OpenAI
Deepgram
ElevenLabs
Custom OpenAI-compatible
Custom REST/OpenAPI
```

Do **not** include Azure, Google, AWS, AssemblyAI, or Speechmatics in the first PR.

Reason: those providers either require async job orchestration, project/resource/region semantics, cloud auth, object storage, or a different lifecycle. The current STT engine is synchronous direct-upload; expanding beyond that should be a separate PR.

---

# Final Agent Instruction

Implement an LLM-style STT provider setup wizard on `master`. Add branded STT provider presets, setup status, draft/finalize routes, and a simplified admin UI. Preserve the existing STT inspection, credential status, credential fingerprint, duplicate detection, and generic REST/OpenAPI power-user path. Include OpenAI, Deepgram, ElevenLabs, Custom OpenAI-compatible, and Custom REST/OpenAPI in the first slice. Pending STT configs must be visible to system admins as setup incomplete, but never selectable by team leaders/users until finalized as ready and active.

[1]: https://developers.deepgram.com/docs/pre-recorded-audio?utm_source=chatgpt.com "Getting Started | Deepgram's Docs"
[2]: https://elevenlabs.io/docs/capabilities/speech-to-text?utm_source=chatgpt.com "Transcription | ElevenLabs Documentation"
[3]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text?utm_source=chatgpt.com "Speech to text REST API - Speech service - Foundry Tools | Microsoft Learn"
[4]: https://cloud.google.com/speech-to-text/v2/docs/reference/rest?utm_source=chatgpt.com "Cloud Speech-to-Text API  |  Google Cloud"
[5]: https://docs.aws.amazon.com/transcribe/latest/APIReference/API_StartTranscriptionJob.html?utm_source=chatgpt.com "StartTranscriptionJob - Amazon Transcribe"
[6]: https://elevenlabs.io/docs/overview/capabilities/speech-to-text?utm_source=chatgpt.com "Transcription | ElevenLabs Documentation"
