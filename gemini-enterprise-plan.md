# Full implementation plan

## 1. Target architecture

Implement Gemini Enterprise as a **native LLM adapter** using the `google-genai` Python SDK:

```python
from google import genai
from google.genai import types

client = genai.Client(
    enterprise=True,
    project=project_id,
    location=location,
    credentials=credentials,
    http_options=types.HttpOptions(api_version="v1"),
)
```

`enterprise=True` selects the Gemini Enterprise Agent Platform endpoints. API keys apply to the Gemini Developer API, not this enterprise client mode, so OpenScribe should use Google credentials rather than add another bearer-token preset. ([Google APIs][1])

This implementation will support:

* Template note generation
* Structured note generation
* Follow-up generation
* Quick actions
* Hallucination checking
* Token usage accounting
* Live connection/model validation
* Pending draft, revision, credential replacement, deletion, and Vault cleanup
* Admin wizard and JSON API representations

It will not initially support:

* Gemini Developer API keys
* Gemini Express Mode
* Custom Google API gateways/base URLs
* Arbitrary uploaded Workload Identity Federation configuration
* Search or agent-app calls through the Discovery Engine APIs

## 2. Core design decisions

### Native adapter

Add:

```python
class LlmAdapterKind(str, enum.Enum):
    ...
    gemini_enterprise = "gemini_enterprise"


class LlmProviderPreset(str, enum.Enum):
    ...
    gemini_enterprise = "gemini_enterprise"
```

Gemini must not reuse `openai_chat`. That adapter currently represents the OpenAI-compatible chat protocol, while the runtime only supports OpenAI-compatible, Bedrock Mantle, and Ollama dispatch.

### Authentication modes

Extend `LlmAuthMode`, which currently only supports `none` and `bearer`:

```python
class LlmAuthMode(str, enum.Enum):
    none = "none"
    bearer = "bearer"
    google_adc = "google_adc"
    google_service_account = "google_service_account"
```

Supported wizard methods:

1. **Application Default Credentials**

   * Recommended
   * No provider-specific secret
   * Supports attached service accounts, local development credentials, and deployment-configured Workload Identity Federation

2. **Service-account JSON**

   * Advanced fallback
   * Stored in Vault
   * Explicit warning about long-lived key risk

ADC searches environment-provided credentials, local application-default credentials, and attached service accounts. Google recommends attached service accounts for production workloads on Google Cloud and identifies service-account keys as a security risk. ([Google Cloud Documentation][2])

Workload Identity Federation should be configured through OpenScribe’s deployment environment and consumed through ADC. The wizard should not accept arbitrary `external_account` JSON in the first release because those files can contain credential-source configuration that requires a separate SSRF and local-file-access security design. Google recommends WIF as a way to replace service-account keys for workloads outside Google Cloud. ([Google Cloud Documentation][3])

### Generic provider configuration

Add a non-secret JSON column to `TeamLlmConfig`:

```python
provider_config_json: Mapped[dict[str, Any]]
```

Gemini value:

```json
{
  "project_id": "clinical-platform-prod",
  "location": "europe-west2",
  "api_version": "v1",
  "capacity_mode": "auto"
}
```

Benefits:

* Avoids adding a column for every future native provider.
* Keeps credentials out of the database.
* Allows project and location to be snapshotted.
* Supports future Gemini settings without repeated migrations.

Use a typed Pydantic model at the service boundary:

```python
class GeminiEnterpriseProviderConfig(BaseModel):
    project_id: str
    location: str
    api_version: Literal["v1"] = "v1"
    capacity_mode: Literal["auto", "shared", "dedicated"] = "auto"
```

### Retain `base_url` as derived metadata

`base_url` is currently non-null throughout the model and schemas, and generated documents snapshot it.

To avoid an unnecessarily broad nullable-column migration:

* Keep `base_url`.
* Derive it server-side from the selected location.
* Do not show it as an editable wizard field.
* Do not use it to initialize the SDK client.
* Treat it as display, audit, and compatibility metadata.

Example helper:

```python
def gemini_enterprise_base_url(location: str) -> str:
    if location == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{location}-aiplatform.googleapis.com"
```

Custom base URLs should remain unsupported. The SDK documents that custom base URLs can bypass normal project, location, or authentication checks, which is inappropriate for the standard wizard. ([Google APIs][4])

## 3. Admin wizard

### Step 1 — Provider details

| Field                   | Rules                                                         |
| ----------------------- | ------------------------------------------------------------- |
| Provider                | Gemini Enterprise                                             |
| Configuration name      | Required, existing uniqueness rules                           |
| Google Cloud project ID | Required                                                      |
| Location                | Required explicit selection                                   |
| Authentication method   | ADC or service-account JSON                                   |
| Capacity mode           | Auto by default; Shared and Dedicated under Advanced          |
| Model ID                | Optional during connection test; required before finalization |

Do not silently default healthcare deployments to `global`. Prefill from a server-configured default when available; otherwise require an explicit choice.

Suggested location controls:

* `global`
* `eu`
* `us`
* `europe-west2`
* Custom supported location

The wizard must explain:

> Global maximizes availability and model access but does not provide regional processing isolation. Select a regional or jurisdictional endpoint when data-residency requirements apply.

Google states that global endpoints can route and process data globally, while jurisdictional and locational endpoints provide geographic processing controls. Gemini Enterprise availability and model support also vary by location. ([Google Cloud Documentation][5])

### Step 2 — Authentication

#### ADC

Show:

* No credential upload.
* Expected runtime identity, when detectable.
* Required project and location.
* Operational guidance for local development and production.

The test runs using the application’s actual runtime ADC.

#### Service-account JSON

Use a file upload rather than a repopulated textarea.

Validation:

* Maximum size, for example 64 KiB.
* Valid UTF-8 JSON object.
* `type == "service_account"`.
* Required fields:

  * `client_email`
  * `private_key`
  * `private_key_id`
  * `token_uri`
* Reject unexpected credential types.
* Never render the submitted JSON after an error.
* Never include the JSON in request logs, audit details, exceptions, or validation responses.

Do not require the service account JSON’s `project_id` to equal the target project. A service account can be granted access to another project. Display its email and home project only as informational metadata.

### Step 3 — Connection and model inspection

Rename the current button from:

> Check API key and find models

to:

> Check credentials and find models

The current wizard assumes a base URL and API-key field for every non-Ollama provider.

Inspection performs:

1. Resolve and refresh credentials.
2. Initialize the enterprise client.
3. Verify access to the project and location.
4. Attempt model discovery.
5. Validate a supplied model with `count_tokens`.
6. Create a pending draft only after credentials are usable.
7. Store service-account JSON in Vault only after successful credential validation.

The SDK exposes `models.count_tokens`, which can validate a selected model without generating clinical content. ([Google Cloud Documentation][6])

### Step 4 — Model finalization

* Show discovered compatible models when available.
* Filter for models advertising a content-generation action.
* If model enumeration is unavailable or empty, expose manual model entry.
* Validate manually entered models with `count_tokens` where possible.
* Never hard-code a permanent fallback model catalogue.
* Store the manually entered model as the only selectable model when discovery remains unavailable, matching the existing provider lifecycle.

Model listing for managed publisher models needs a short SDK compatibility spike against the pinned `google-genai` version. The SDK exposes model metadata including `supported_actions`, but the implementation must not assume every enterprise location returns the same catalogue. ([Google APIs][7])

## 4. Database migration

Create one Alembic migration containing:

```sql
ALTER TYPE llmadapterkind
ADD VALUE IF NOT EXISTS 'gemini_enterprise';

ALTER TYPE llmauthmode
ADD VALUE IF NOT EXISTS 'google_adc';

ALTER TYPE llmauthmode
ADD VALUE IF NOT EXISTS 'google_service_account';
```

The repository already uses this pattern for adapter and authentication enum additions.

Add:

```python
op.add_column(
    "team_llm_configs",
    sa.Column(
        "provider_config_json",
        sa.JSON(),
        nullable=False,
        server_default=sa.text("'{}'::json"),
    ),
)

op.add_column(
    "generated_documents",
    sa.Column(
        "llm_provider_config_json",
        sa.JSON(),
        nullable=True,
    ),
)
```

Then:

* Backfill existing provider rows with `{}`.
* Remove the server default from `provider_config_json`.
* Leave existing provider data unchanged.
* Add no index unless project/location filtering is introduced later.

PostgreSQL enum values are difficult to remove safely. The downgrade should remove the new columns but document that enum labels remain, matching the existing migration style.

## 5. Provider preset

Extend `LlmProviderPresetDefinition`. Its existing fields already capture display name, adapter, base URL, credential requirement, discovery support, manual-model support, and help text.

```python
LlmProviderPreset.gemini_enterprise.value:
    LlmProviderPresetDefinition(
        key=LlmProviderPreset.gemini_enterprise.value,
        display_name="Gemini Enterprise",
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        default_base_url=None,
        requires_bearer_token=False,
        supports_model_discovery=True,
        allow_manual_model=True,
        help_text=(
            "Uses a Google Cloud project, location, and Application "
            "Default Credentials or a Vault-backed service-account credential."
        ),
    )
```

Do not include Gemini in `BRANDED_OPENAI_COMPATIBLE_PRESETS`. Ensure URL-based preset reclassification leaves it unchanged. The current reclassification logic is specifically designed for OpenAI-compatible presets and Bedrock Mantle.

## 6. Schema changes

Update all three input paths:

* `LlmConfigUpsert`
* `LlmConfigDraftCreate`
* `LlmInspectRequest`

Add:

```python
provider_config_json: dict[str, object] = Field(default_factory=dict)
google_project_id: str | None = None
google_location: str | None = None
google_auth_method: Literal[
    "application_default",
    "service_account_json",
] | None = None
google_service_account_json: dict[str, object] | None = None
capacity_mode: Literal["auto", "shared", "dedicated"] = "auto"
```

The browser routes can construct `provider_config_json`; public JSON APIs should accept explicit typed Gemini fields rather than arbitrary provider JSON.

Validation rules:

* Gemini requires project ID and location.
* Gemini rejects `bearer_token`.
* ADC rejects submitted credential JSON.
* Service-account mode requires credential JSON for a new config or replacement.
* Non-Gemini providers reject Gemini-only fields.
* Base URL is generated before the existing URL validator runs.
* API version is always forced to `v1`.

Extend response models with non-secret fields:

```python
google_project_id: str | None
google_location: str | None
google_auth_method: str | None
capacity_mode: str | None
```

Never expose `provider_config_json` wholesale if it could later gain internal settings.

## 7. Vault refactor

The current LLM Vault functions store and retrieve only:

```json
{"bearer_token": "..."}
```

Introduce generic helpers:

```python
def write_team_llm_secret(
    *,
    team_id: UUID,
    config_id: UUID,
    secret_payload: dict[str, object],
    secret_id: UUID | None = None,
) -> str:
    ...

def read_team_llm_secret(...) -> dict[str, object]:
    ...
```

Secret formats:

```json
{
  "secret_type": "bearer_token",
  "bearer_token": "..."
}
```

```json
{
  "secret_type": "google_service_account_json",
  "credential_json": {
    "...": "..."
  }
}
```

Retain compatibility wrappers:

```python
write_team_llm_bearer_token(...)
read_team_llm_bearer_token(...)
```

These wrappers call the generic functions and continue reading legacy records without `secret_type`.

ADC configs:

* `auth_mode = google_adc`
* `vault_secret_ref = ""`
* `has_secret = false`
* UI shows `Runtime identity`, not `Secret: no`

Update all secret lifecycle paths:

* Draft creation
* Revision creation
* Credential replacement
* Revision promotion
* Draft cancellation
* Provider deletion
* Team deletion
* Orphan cleanup
* Retry worker

The existing versioned Vault references and cleanup queue should remain unchanged; the path validation already operates on the provider reference rather than the secret contents.

## 8. Native Gemini adapter

Create:

```text
app/services/llm_adapters/
    __init__.py
    types.py
    gemini_enterprise.py
```

### Common adapter types

```python
@dataclass(frozen=True)
class LlmGenerationRequest:
    model: str
    system_message: str
    user_message: str
    temperature: float
    max_output_tokens: int
    expect_json: bool


@dataclass(frozen=True)
class LlmGenerationResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    provider_duration_ms: int | None
    finish_reason: str | None
```

### Gemini module responsibilities

```python
def build_gemini_client(...)
def discover_gemini_models(...)
def validate_gemini_model(...)
def generate_gemini_text(...)
def translate_gemini_error(...)
def gemini_request_snapshot(...)
```

Client construction:

* `enterprise=True`
* Explicit project and location
* Explicit credentials for service-account mode
* ADC when no credentials object is passed
* Stable `v1`
* Optional capacity-routing headers
* Explicit client close in `finally`

The SDK recommends explicitly closing the client to release its underlying HTTP connections. ([Google APIs][8])

### Request mapping

Map OpenScribe’s internal request to Gemini:

| OpenScribe       | Gemini                                                   |
| ---------------- | -------------------------------------------------------- |
| System message   | `system_instruction`                                     |
| User message     | `contents` with user role                                |
| `temperature`    | generation config                                        |
| Output token cap | `max_output_tokens`                                      |
| JSON expected    | JSON MIME/config controls, after SDK compatibility tests |
| Model            | `model`                                                  |

Do not pass OpenAI fields such as:

* `messages`
* `max_completion_tokens`
* `user`

The current request builder creates OpenAI-specific or Ollama-specific outbound bodies, so Gemini needs a separate request snapshot shape.

### Usage metadata

Map the pinned SDK’s usage metadata to:

* `input_tokens`
* `output_tokens`
* `total_tokens`

Google generation responses expose prompt, candidate, and total token counts. ([Google Cloud Documentation][6])

## 9. Central runtime dispatch

Create a single service entry point:

```python
def generate_llm_text(
    *,
    config: TeamLlmConfig,
    provider_config: dict[str, object],
    credential: object | None,
    request: LlmGenerationRequest,
) -> tuple[str, GenerationUsage, dict[str, object]]:
    ...
```

It dispatches to:

* OpenAI-compatible
* Bedrock Mantle
* Ollama
* Gemini Enterprise

Use it from both:

1. Main generated-document processing
2. Hallucination checking

This removes the current duplicated adapter branching. Main generation currently branches between OpenAI/Bedrock and Ollama, while the checker repeats the same whitelist and dispatch.

### Credential resolver

Add:

```python
def resolve_llm_runtime_credential(
    *,
    config: TeamLlmConfig,
) -> str | Credentials | None:
    ...
```

Rules:

* `bearer` → Vault bearer token
* `none` → `None`
* `google_adc` → ADC, no Vault read
* `google_service_account` → Vault JSON converted to Google credentials

Use the same resolver for:

* Inspection
* Main generation
* Hallucination checker
* Credential correction
* Saved-provider reinspection

## 10. Request and provider snapshots

Add `llm_provider_config_json` to generated documents at queue time:

```json
{
  "project_id": "clinical-platform-prod",
  "location": "europe-west2",
  "api_version": "v1",
  "capacity_mode": "auto"
}
```

Do not include:

* Service-account JSON
* Access tokens
* Vault references
* Service-account private-key identifiers

Update `_checker_provider_snapshot()` to include non-secret provider configuration.

The existing design already stores the exact outbound LLM request after redaction and excludes provider secrets.

For Gemini, the encrypted outbound snapshot should resemble:

```json
{
  "model": "gemini-model-id",
  "contents": [
    {
      "role": "user",
      "parts": [{"text": "...redacted content..."}]
    }
  ],
  "config": {
    "system_instruction": "...redacted instruction...",
    "temperature": 0.2,
    "max_output_tokens": 1600
  }
}
```

## 11. Inspection and error handling

### Error codes

Add deterministic translations:

| Provider condition                    | OpenScribe code             |
| ------------------------------------- | --------------------------- |
| Credential cannot be loaded/refreshed | `llm_invalid_credential`    |
| IAM permission denied                 | `llm_permission_denied`     |
| API not enabled                       | `llm_provider_api_disabled` |
| Unsupported or inaccessible location  | `llm_location_unavailable`  |
| Model not found/not allowed           | `llm_model_unavailable`     |
| Shared capacity exhausted             | `llm_provider_rate_limited` |
| Deadline exceeded                     | `llm_provider_timeout`      |
| Network failure                       | `llm_provider_unreachable`  |
| Invalid provider response             | `llm_provider_bad_response` |
| Other Google API failure              | `llm_generation_failed`     |

Google documents `aiplatform.user` as the normal user role for generative features, with `aiplatform.admin` providing broader administration access. ([Google Cloud Documentation][9])

### Draft persistence rules

Do not create a draft or write a Vault secret for:

* Invalid credentials
* Definitive permission denial
* Disabled API
* Invalid project
* Invalid service-account JSON

Allow a pending manual-model draft for:

* Successful authentication but empty model discovery
* Model-list operation unsupported by the SDK/API
* Non-authentication transient discovery failure

This preserves the existing rule that rejected credentials create neither a provider draft nor a Vault secret.

### Capacity and retry behaviour

For shared capacity:

* Retry only safe transient `429` and `5xx` failures.
* Preserve the invariant that one durable `ProviderAttempt` maps to one outbound request.
* Do not retry after an ambiguous response unless quota accounting treats the attempt as unknown.

Google distinguishes shared pay-as-you-go capacity from provisioned throughput and notes that shared-capacity `429` errors can be transient. ([Google Cloud Documentation][10])

## 12. Admin routes and presentation

### Routes

Update:

* `/admin/llm-configs/inspect`
* `/admin/llm-configs/drafts`
* `/admin/llm-configs`
* `/admin/llm-configs/{id}/replace-credential`
* `/admin/llm-configs/{id}/inspect`

Add form parameters:

```python
google_project_id: str = Form("")
google_location: str = Form("")
google_auth_method: str = Form("")
google_service_account_file: UploadFile | None = File(None)
capacity_mode: str = Form("auto")
```

Create one form parser:

```python
def _llm_provider_submission_from_form(...) -> LlmProviderSubmission:
    ...
```

This should normalize provider-specific input once rather than duplicating it across inspect, draft, upsert, and replacement routes. Current routes separately reconstruct schemas from base URL, Bedrock region, and bearer-token fields.

### Presentation model

Extend `llm_form_defaults()` and `llm_config_response()` with:

* Project ID
* Location
* Authentication method
* Capacity mode
* Runtime-identity/service-account label
* Whether credential replacement is applicable

The existing presentation layer assumes every non-Ollama provider needs credential replacement and a base URL.

### Templates

The current `/admin` route renders `admin_mockup.html`, while additional provider forms exist in `admin.html` and `admin2.html`.

Avoid maintaining three divergent Gemini forms. Extract:

```text
app/templates/admin/_llm_provider_wizard.html
app/static/js/admin/llm_provider_wizard.js
```

Include the shared partial from every active admin presentation.

### JavaScript

Replace hard-coded conditions such as:

```javascript
adapter === "openai_chat"
adapter === "bedrock_chat"
adapter === "ollama_chat"
```

with provider metadata attributes:

```html
<option
  data-adapter-kind="gemini_enterprise"
  data-requires-base-url="false"
  data-auth-options="google_adc,google_service_account"
  data-requires-project="true"
  data-requires-location="true"
>
```

The current adapter-state script only understands OpenAI, Bedrock, and Ollama, including token labels and base-URL visibility.

### Provider card

Gemini cards should display:

```text
Gemini Enterprise
Project: clinical-platform-prod
Location: europe-west2
Authentication: Application Default Credentials
Default model: …
Setup: Ready · available
```

Replace `Secret: no` for ADC with `Credential source: Runtime identity`.

## 13. Dependency changes

Add and pin a tested version of:

```text
google-genai==<tested-version>
google-auth==<compatible-version>
```

The repository currently installs the OpenAI SDK but no Google Gen AI SDK.

Pin after completing a compatibility test for:

* `enterprise=True`
* Explicit credentials
* `models.list`
* `models.count_tokens`
* `models.generate_content`
* Response text extraction
* Usage metadata names
* Exception classes
* Client close behaviour

## 14. Testing plan

### New test module

```text
tests/test_gemini_enterprise_llm.py
```

Cover:

#### Configuration

* Valid project/location.
* Missing project.
* Missing location.
* Invalid auth method.
* Gemini rejects bearer tokens.
* Non-Gemini providers reject Gemini fields.
* Base URL is derived and cannot be overridden.
* Global-location warning.
* Capacity-mode validation.

#### Credentials

* ADC success.
* ADC unavailable.
* Service-account JSON success.
* Malformed JSON.
* Wrong credential type.
* Missing private key.
* Credential refresh failure.
* Vault write only after validation.
* Credential JSON never appears in response or audit details.

#### Inspection

* Model discovery success.
* Empty discovery → manual required.
* Listing unsupported → manual required.
* Manual model passes `count_tokens`.
* Manual model not found.
* Permission denied.
* API disabled.
* Location unavailable.
* Transient provider failure.

#### Generation

* Freeform template.
* Structured template JSON.
* Follow-up.
* Quick action.
* Hallucination checker.
* System instruction mapping.
* Output-token cap mapping.
* Usage metadata mapping.
* Empty response.
* Blocked/no-candidate response.
* Timeout, 429, 403, 404, and 5xx translation.
* Client is closed after success and failure.

#### Lifecycle

* ADC draft has no Vault reference.
* Service-account draft has a versioned Vault reference.
* Finalize discovered model.
* Finalize manual model.
* Revision inherits service-account credentials securely.
* Revision from service account to ADC retires old secret.
* Revision from ADC to service account creates a new secret.
* Credential replacement.
* Draft cancellation cleanup.
* Provider deletion cleanup.
* Team deletion cleanup.
* In-flight generation edit restrictions.
* In-flight credential correction preserves the saved model.

#### Snapshot and accounting

* Provider configuration is snapshotted.
* Secrets are absent from snapshots.
* Token accounting settles correctly.
* Duplicate workers still produce one outbound request.
* Checker retries preserve attempt numbering and accounting.

### Existing tests to update

* `tests/conftest.py`
* `tests/test_migrations.py`
* `tests/test_provider_secret_cleanup.py`
* Admin-route tests
* Schema/OpenAPI tests
* Provider selection tests
* Generated-document snapshot tests
* Hallucination-check tests

All standard tests should mock the adapter boundary. A separately invoked staging smoke test can use a real Google project and low-privilege service account.

## 15. Documentation

Update:

| File                    | Content                                                    |
| ----------------------- | ---------------------------------------------------------- |
| `docs/llm-providers.md` | Gemini preset, setup states, auth, model validation        |
| `docs/setup.md`         | ADC deployment, local `gcloud` setup, API enablement       |
| `docs/security.md`      | Service-account risk, Vault handling, WIF policy           |
| `docs/api.md`           | New adapter, auth modes, request/response fields           |
| `docs/testing.md`       | Mock client and optional live smoke test                   |
| `requirements.txt`      | Pinned SDK                                                 |
| Deployment examples     | Attached service account and ADC environment configuration |

Document the minimum runtime role as `roles/aiplatform.user`, subject to any narrower custom-role policy. ([Google Cloud Documentation][9])

## 16. Implementation sequence

### PR 1 — Data and credential foundation

* Add enums.
* Add database migration.
* Add `provider_config_json`.
* Add generated-document provider-config snapshot.
* Generalize Vault LLM secrets.
* Preserve bearer-token compatibility.
* Extend schemas and presentation response models.
* Add migration and secret-cleanup tests.

### PR 2 — Native adapter and inspection

* Add `google-genai`.
* Implement client and credential factories.
* Implement model discovery and `count_tokens` validation.
* Implement provider error translation.
* Add Gemini preset/default helpers.
* Integrate inspection, draft creation, saved reinspection, and credential replacement.
* Add adapter and service tests.

### PR 3 — Runtime generation

* Introduce common generation request/result types.
* Centralize adapter dispatch.
* Add Gemini request mapping.
* Integrate main generation.
* Integrate hallucination checker.
* Add request snapshots and usage extraction.
* Add concurrency, quota, and structured-output tests.

### PR 4 — Admin wizard and API

* Extract shared wizard partial and JavaScript.
* Add project, location, authentication, and credential controls.
* Add provider cards and pending-setup views.
* Update browser routes and JSON APIs.
* Add route, rendering, CSRF, and credential-non-echo tests.

### PR 5 — Hardening and rollout

* Complete documentation.
* Add feature flag such as `ENABLE_GEMINI_ENTERPRISE_PROVIDER`.
* Run migration tests from supported database states.
* Run staging smoke tests in global and intended production location.
* Verify Vault retirement and rollback behaviour.
* Enable the preset after staging validation.

## 17. Acceptance criteria

The implementation is complete when:

* Gemini Enterprise appears in the admin provider wizard.
* The wizard requires project, location, and authentication method.
* No editable base URL is shown.
* ADC works without a Vault secret.
* Service-account JSON is Vault-backed and never rendered or logged.
* Definitive authentication or IAM failures create no draft or secret.
* Model discovery is attempted without relying on a hard-coded catalogue.
* Manual models can be validated and finalized when discovery is unavailable.
* Template, structured, follow-up, quick-action, and checker calls all work.
* Token usage and provider errors populate existing accounting fields.
* Queued jobs snapshot non-secret Gemini configuration.
* Reinspection, revisions, replacements, cancellation, deletion, and cleanup preserve existing lifecycle guarantees.
* Existing OpenAI-compatible, Bedrock, and Ollama configurations remain unchanged.
* Standard CI performs no live Google calls.
* All migration, security, route, runtime, quota, and provider-lifecycle tests pass.

[1]: https://googleapis.github.io/python-genai/genai.html?utm_source=chatgpt.com "Submodules - Google Gen AI SDK documentation"
[2]: https://docs.cloud.google.com/docs/authentication/application-default-credentials?utm_source=chatgpt.com "How Application Default Credentials works  |  Authentication  |  Google Cloud Documentation"
[3]: https://docs.cloud.google.com/iam/docs/best-practices-for-using-workload-identity-federation?hl=en&utm_source=chatgpt.com "Best practices for using Workload Identity Federation  |  Identity and Access Management (IAM)  |  Google Cloud Documentation"
[4]: https://googleapis.github.io/python-genai/?utm_source=chatgpt.com "Google Gen AI SDK documentation"
[5]: https://docs.cloud.google.com/gemini/enterprise/docs/locations?utm_source=chatgpt.com "Data residency for Gemini Enterprise Standard and Plus Editions and Gemini Notebook Enterprise  |  Google Cloud Documentation"
[6]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/generativeaionvertexai-gemini-token-count-multimodal?hl=en&utm_source=chatgpt.com "Count tokens for Gemini  |  Generative AI on Vertex AI  |  Google Cloud Documentation"
[7]: https://googleapis.github.io/python-genai/modules.html?utm_source=chatgpt.com "google - Google Gen AI SDK documentation"
[8]: https://googleapis.github.io/python-genai/index.html?utm_source=chatgpt.com "Google Gen AI SDK documentation"
[9]: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/access-control?utm_source=chatgpt.com "Access control  |  Gemini Enterprise Agent Platform  |  Google Cloud Documentation"
[10]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/resources/throughput-quota?utm_source=chatgpt.com "Throughput quota  |  Generative AI on Vertex AI  |  Google Cloud Documentation"
