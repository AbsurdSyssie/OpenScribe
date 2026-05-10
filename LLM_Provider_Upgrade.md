# Implementation Brief: Branded LLM Provider Presets

## Goal

Implement a first slice of branded LLM provider setup for OpenScribe.

System admins should be able to select a known provider brand, paste a key, auto-discover models, choose/save a model, and make the provider available for team selection. Team leaders and users should continue selecting only from system-admin-provisioned configs.

Current repo foundation:

* LLM configs already exist via `TeamLlmConfig`, with `adapter_kind`, `base_url`, `model_name`, `available_models_json`, `vault_secret_ref`, and active state. 
* LLM config/list/inspect/upsert/delete API routes already exist and are system-admin guarded. 
* LLM secrets are already stored through Vault via `write_team_llm_bearer_token`, `read_team_llm_bearer_token`, and `delete_team_llm_bearer_token`. 
* Existing Bedrock HTTP support already has `DEFAULT_BEDROCK_CHAT_REGION`, `bedrock_chat_base_url(region)`, and `bedrock_region_from_base_url(base_url)`. 

## Confirmed Product Decisions

### In scope

Implement branded presets for:

```text id="m9i09u"
OpenAI
OpenRouter
xAI
Groq
Mistral
DeepSeek
Together AI
Ollama
Bedrock HTTP gateway
Custom OpenAI-compatible
```

### Out of scope for this slice

```text id="uzexd4"
Anthropic native adapter
Gemini native adapter
Azure OpenAI native adapter
Native AWS Bedrock Converse/IAM
OpenAI Responses API migration
Streaming generation
Tool calling/function calling
Personal user API keys
Team-leader credential provisioning
```

### Important behavioral decisions

* Provider setup and credential management remain **system-admin-only**.
* The admin UI must expose **branded presets**, not “OpenAI-compatible” as the primary workflow.
* `Custom OpenAI-compatible` is visible from day one, placed last, and labeled advanced.
* Add `provider_preset` as a **first-class string column**, not only JSON metadata.
* Add `inspection_metadata_json` to LLM configs.
* Model lists come from **live auto-discovery only**. Do not maintain curated built-in provider model defaults.
* If live discovery fails, admins may still save a manually entered model name.
* Saving with no model remains invalid for active LLM providers.
* If a branded provider’s base URL is overridden, automatically reclassify the config as `custom_openai_compatible`.
* Bedrock remains the existing **HTTP/gateway-style provider**, not native AWS Bedrock Converse/IAM.
* Bedrock setup needs a geographical endpoint/region selector plus custom override.

## Provider Mapping

| Branded preset             | Internal adapter                                 | Notes                                                                                                                                                                                                 |
| -------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openai`                   | `openai_compatible_chat`                         | Use current Chat Completions-compatible path; do not migrate to Responses API in this slice. OpenAI still supports Chat Completions but recommends Responses for new projects. ([OpenAI Platform][1]) |
| `openrouter`               | `openai_compatible_chat`                         | OpenRouter request/response schemas are very similar to OpenAI Chat API. ([OpenRouter][2])                                                                                                            |
| `xai`                      | `openai_compatible_chat`                         | xAI exposes `/v1/chat/completions`, `/v1/responses`, `/v1/models`, and bearer auth with OpenAI REST compatibility. ([xAI Docs][3])                                                                    |
| `groq`                     | `openai_compatible_chat`                         | Groq documents OpenAI compatibility and base URL `https://api.groq.com/openai/v1`. ([GroqCloud][4])                                                                                                   |
| `mistral`                  | `openai_compatible_chat` initially               | Mistral exposes `/v1/chat/completions` with `messages` and `model`. ([Mistral AI][5])                                                                                                                 |
| `deepseek`                 | `openai_compatible_chat`                         | DeepSeek documents OpenAI-compatible config and base URL `https://api.deepseek.com`. ([DeepSeek API Docs][6])                                                                                         |
| `together`                 | `openai_compatible_chat`                         | Together exposes chat completions with `model` and `messages`. ([Together AI Docs][7])                                                                                                                |
| `ollama`                   | `ollama_chat`                                    | Preserve existing Ollama path.                                                                                                                                                                        |
| `bedrock_http_gateway`     | `bedrock_chat` or renamed `bedrock_gateway_chat` | Preserve current HTTP/gateway implementation.                                                                                                                                                         |
| `custom_openai_compatible` | `openai_compatible_chat`                         | Admin supplies base URL.                                                                                                                                                                              |

## Data Model Changes

### Add provider preset enum/string

Use a Python enum for validation, but store as a string DB column to avoid DB enum migration friction when adding providers later.

```python id="xrcn9g"
class LlmProviderPreset(str, enum.Enum):
    openai = "openai"
    openrouter = "openrouter"
    xai = "xai"
    groq = "groq"
    mistral = "mistral"
    deepseek = "deepseek"
    together = "together"
    ollama = "ollama"
    bedrock_http_gateway = "bedrock_http_gateway"
    custom_openai_compatible = "custom_openai_compatible"
```

### Add fields to `TeamLlmConfig`

```python id="lq5t4n"
provider_preset: str  # non-null, indexed if useful
inspection_metadata_json: dict[str, Any]  # JSON, default {}
```

Suggested semantic meaning:

| Field                      | Meaning                                                     |
| -------------------------- | ----------------------------------------------------------- |
| `provider_preset`          | What the admin selected or what migration inferred          |
| `adapter_kind`             | Protocol/client implementation                              |
| `base_url`                 | Actual endpoint used                                        |
| `model_name`               | Default model/deployment saved for this config              |
| `available_models_json`    | Live-discovered models, or empty if discovery failed/manual |
| `inspection_metadata_json` | Last inspection status, warnings, timestamps, source        |
| `vault_secret_ref`         | Vault secret location only                                  |

## Migration / Backfill Rules

Backfill `provider_preset` from existing rows.

```python id="ph1x0y"
def infer_llm_provider_preset(adapter_kind: str, base_url: str) -> str:
    host = urlparse(base_url or "").hostname or ""
    host = host.lower()

    if adapter_kind == "ollama_chat":
        return "ollama"

    if adapter_kind == "bedrock_chat":
        return "bedrock_http_gateway"

    if adapter_kind == "openai_chat":
        if host == "api.openai.com":
            return "openai"
        if host.endswith("openrouter.ai"):
            return "openrouter"
        if host == "api.x.ai":
            return "xai"
        if host == "api.groq.com":
            return "groq"
        if host == "api.deepseek.com":
            return "deepseek"
        if host == "api.mistral.ai":
            return "mistral"
        if host in {"api.together.xyz", "api.together.ai"}:
            return "together"
        return "custom_openai_compatible"

    return "custom_openai_compatible"
```

Migration behavior:

```text id="ss8h9k"
openai_chat + api.openai.com       -> openai
openai_chat + openrouter.ai        -> openrouter
openai_chat + api.x.ai             -> xai
openai_chat + api.groq.com         -> groq
openai_chat + api.deepseek.com     -> deepseek
openai_chat + api.mistral.ai       -> mistral
openai_chat + Together host        -> together
openai_chat + unknown host         -> custom_openai_compatible
bedrock_chat                       -> bedrock_http_gateway
ollama_chat                        -> ollama
```

## Provider Preset Catalog

Create a deep module responsible for preset metadata and defaults. It should be pure and easy to unit test.

Example shape:

```python id="fmd9pm"
from dataclasses import dataclass
from typing import Literal

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
```

Initial catalog:

```python id="wm5w25"
LLM_PROVIDER_PRESETS = {
    "openai": LlmProviderPresetDefinition(
        key="openai",
        display_name="OpenAI",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://api.openai.com/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "openrouter": LlmProviderPresetDefinition(
        key="openrouter",
        display_name="OpenRouter",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://openrouter.ai/api/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "xai": LlmProviderPresetDefinition(
        key="xai",
        display_name="xAI",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://api.x.ai/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "groq": LlmProviderPresetDefinition(
        key="groq",
        display_name="Groq",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://api.groq.com/openai/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "mistral": LlmProviderPresetDefinition(
        key="mistral",
        display_name="Mistral",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://api.mistral.ai/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "deepseek": LlmProviderPresetDefinition(
        key="deepseek",
        display_name="DeepSeek",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://api.deepseek.com",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "together": LlmProviderPresetDefinition(
        key="together",
        display_name="Together AI",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url="https://api.together.xyz/v1",
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "ollama": LlmProviderPresetDefinition(
        key="ollama",
        display_name="Ollama",
        adapter_kind=LlmAdapterKind.ollama_chat,
        default_base_url="http://localhost:11434",
        requires_bearer_token=False,
        supports_model_discovery=True,
        allow_manual_model=True,
    ),
    "bedrock_http_gateway": LlmProviderPresetDefinition(
        key="bedrock_http_gateway",
        display_name="Bedrock HTTP gateway",
        adapter_kind=LlmAdapterKind.bedrock_chat,
        default_base_url=None,
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
        default_bedrock_region="eu-west-2",
    ),
    "custom_openai_compatible": LlmProviderPresetDefinition(
        key="custom_openai_compatible",
        display_name="Custom OpenAI-compatible · advanced",
        adapter_kind=LlmAdapterKind.openai_compatible_chat,
        default_base_url=None,
        requires_bearer_token=True,
        supports_model_discovery=True,
        allow_manual_model=True,
        advanced=True,
    ),
}
```

Note: this PRD intentionally does **not** require built-in model defaults for providers. Auto-discover if possible; otherwise require manual model entry.

## Bedrock HTTP Region Selector

Keep the existing region-to-base-URL pattern.

```python id="wia9n7"
BEDROCK_HTTP_GATEWAY_REGIONS = [
    "eu-west-2",
    "eu-west-1",
    "eu-central-1",
    "us-east-1",
    "us-west-2",
    "ap-southeast-1",
    "ap-southeast-2",
]
```

Behavior:

```text id="n9g8ke"
Select Bedrock HTTP gateway
→ show region dropdown
→ default to eu-west-2
→ derive base_url as https://bedrock-mantle.<region>.api.aws/v1
→ allow advanced custom region/base URL override
```

If a full custom base URL is supplied, preserve `provider_preset=bedrock_http_gateway` only if it still matches the Bedrock gateway pattern. Otherwise, require Custom OpenAI-compatible or show an explicit warning.

## Schema / API Contract Changes

### Upsert request

Add:

```python id="azag8n"
provider_preset: LlmProviderPreset
inspection_metadata_json: dict[str, Any] | None = None  # internal/service-set preferred
```

Update default application logic:

```python id="zrv6gs"
def apply_provider_defaults(data: dict) -> dict:
    preset = get_llm_provider_preset(data.get("provider_preset") or "openai")

    data["adapter_kind"] = preset.adapter_kind

    if preset.key == "bedrock_http_gateway":
        region = data.get("bedrock_region") or preset.default_bedrock_region
        if not data.get("base_url"):
            data["base_url"] = bedrock_chat_base_url(region)

    elif preset.default_base_url and not data.get("base_url"):
        data["base_url"] = preset.default_base_url

    return data
```

### Base URL override reclassification

For branded OpenAI-compatible presets, if the submitted `base_url` differs from the preset’s default URL, set:

```python id="bb65du"
provider_preset = "custom_openai_compatible"
adapter_kind = LlmAdapterKind.openai_compatible_chat
```

This should apply to:

```text id="jlddsp"
openai
openrouter
xai
groq
mistral
deepseek
together
```

Do **not** silently keep the original brand if its endpoint is changed.

### Inspect request

Add:

```python id="td57a0"
provider_preset: LlmProviderPreset
```

Keep:

```python id="jgmt44"
team_id
base_url
bearer_token
bedrock_region
```

### Inspect response

Extend or preserve response with:

```python id="fbjo2m"
provider_preset
provider_display_name
adapter_kind
base_url
model_name
available_models
available_model_options
discovery_status
default_model_source
requires_bearer_token
supports_model_discovery
warnings
notes
```

Discovery status values should include at least:

```text id="jcnmuz"
fetched
manual_required
failed
```

Avoid `fallback` if no built-in provider model defaults are maintained.

## Model Discovery

Create a model discovery module with one public service-level interface.

```python id="hjxq73"
@dataclass(frozen=True)
class LlmModelDiscoveryInput:
    provider_preset: str
    adapter_kind: LlmAdapterKind
    base_url: str
    bearer_token: str | None
    bedrock_region: str | None = None

@dataclass(frozen=True)
class LlmModelDiscoveryResult:
    available_models: list[str]
    available_model_options: list[LlmModelOption]
    model_name: str | None
    discovery_status: Literal["fetched", "manual_required", "failed"]
    default_model_source: Literal["provider", "manual", "none"]
    warnings: list[str]
    notes: list[str]
```

Main behavior:

```python id="gsvv9t"
def discover_llm_models(payload: LlmModelDiscoveryInput) -> LlmModelDiscoveryResult:
    if payload.adapter_kind is LlmAdapterKind.ollama_chat:
        return discover_ollama_models(payload)

    if payload.adapter_kind in {
        LlmAdapterKind.openai_compatible_chat,
        LlmAdapterKind.bedrock_chat,
    }:
        return discover_openai_compatible_models(payload)

    raise AppError(
        422,
        "unsupported_llm_provider",
        "Unsupported LLM provider preset",
        {"provider_preset": payload.provider_preset},
    )
```

### OpenAI-compatible discovery

Use current OpenAI client model listing where compatible, but split filtering by provider.

```python id="chahsj"
def filter_discovered_models(provider_preset: str, model_ids: list[str]) -> list[str]:
    blocked_tokens = (
        "embedding",
        "transcribe",
        "whisper",
        "tts",
        "moderation",
        "image",
    )

    filtered = [
        model_id for model_id in model_ids
        if not any(token in model_id.lower() for token in blocked_tokens)
    ]

    if provider_preset == "openai":
        return [
            model_id for model_id in filtered
            if model_id.lower().startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))
        ]

    # Important: do not apply OpenAI prefix rules to other providers.
    return filtered
```

This fixes the current weakness where OpenAI-specific prefixes would hide valid models for OpenRouter, xAI, Groq, Mistral, DeepSeek, Together, and custom providers.

### Discovery failure behavior

If discovery fails:

```python id="ln0neu"
return LlmModelDiscoveryResult(
    available_models=[],
    available_model_options=[],
    model_name=None,
    discovery_status="manual_required",
    default_model_source="manual",
    warnings=[
        "Live model discovery failed. Verify the API key and endpoint, or enter a model name manually."
    ],
    notes=[],
)
```

Do not return provider-specific built-in fallback model names.

## Upsert Behavior

Service-level upsert rules:

```text id="weqj82"
1. Resolve provider preset.
2. Apply preset defaults.
3. If branded preset base URL changed, reclassify to custom_openai_compatible.
4. Validate base URL.
5. Validate credential action.
6. If provider requires secret, require existing or replacement secret.
7. If replacing secret, write it to Vault.
8. Discover models when possible.
9. Allow manual model if discovery failed.
10. Require model_name before save.
11. Save provider_preset, adapter_kind, base_url, model_name, available_models_json, inspection_metadata_json.
```

Pseudo-code:

```python id="o3x8wh"
def upsert_llm_config(db: Session, actor: User, payload: LlmConfigUpsert) -> TeamLlmConfig:
    team = resolve_admin_scoped_team(db, actor, team_id=payload.team_id)

    preset = resolve_provider_preset(payload.provider_preset)
    payload = apply_provider_defaults(payload, preset)

    if should_reclassify_as_custom(payload, preset):
        payload.provider_preset = LlmProviderPreset.custom_openai_compatible
        preset = resolve_provider_preset(payload.provider_preset)
        payload.adapter_kind = preset.adapter_kind

    validate_credential_policy(payload, existing_config, preset)

    discovery = None
    if preset.supports_model_discovery:
        discovery = try_discover_models(payload)

    available_models = discovery.available_models if discovery else []

    if payload.model_name:
        model_name = payload.model_name.strip()
    elif discovery and discovery.model_name:
        model_name = discovery.model_name
    else:
        raise AppError(
            422,
            "business_rule_violation",
            "Model name is required. Inspect models successfully or enter a model name manually.",
            {"field": "model_name"},
        )

    # Save config + inspection metadata.
```

## Admin UI Requirements

### Provider dropdown

Show:

```text id="vvmcw4"
OpenAI
OpenRouter
xAI
Groq
Mistral
DeepSeek
Together AI
Ollama
Bedrock HTTP gateway
Custom OpenAI-compatible · advanced
```

### Provider form behavior

| Provider                 | UI behavior                                                        |
| ------------------------ | ------------------------------------------------------------------ |
| OpenAI                   | Auto-fill base URL; show API key field; allow Inspect models.      |
| OpenRouter               | Auto-fill base URL; show API key field; allow Inspect models.      |
| xAI                      | Auto-fill base URL; show API key field; allow Inspect models.      |
| Groq                     | Auto-fill base URL; show API key field; allow Inspect models.      |
| Mistral                  | Auto-fill base URL; show API key field; allow Inspect models.      |
| DeepSeek                 | Auto-fill base URL; show API key field; allow Inspect models.      |
| Together AI              | Auto-fill base URL; show API key field; allow Inspect models.      |
| Ollama                   | Auto-fill local base URL; bearer token optional/hidden by default. |
| Bedrock HTTP gateway     | Show region dropdown; derive base URL; show API key field.         |
| Custom OpenAI-compatible | Require admin-supplied base URL; show API key field.               |

### Manual model fallback

If inspection fails:

```text id="rb62jn"
Show warning
Enable manual model-name input
Allow save if model_name is present and credential policy passes
```

### Base URL override behavior

If admin changes a branded preset’s base URL:

```text id="dvk475"
Show message:
"Changing this endpoint will save the config as Custom OpenAI-compatible."

On save:
provider_preset = custom_openai_compatible
```

## Acceptance Criteria

### Provider presets

* System admin can select each MVP branded provider.
* Preset auto-fills adapter kind and base URL where applicable.
* Custom OpenAI-compatible is visible, last, and labeled advanced.
* Branded provider with changed base URL saves as `custom_openai_compatible`.

### Bedrock HTTP gateway

* Bedrock provider shows region dropdown.
* Default Bedrock region is `eu-west-2`.
* Region selection derives the existing gateway URL pattern.
* Advanced custom region/base URL override is supported.
* Native Bedrock/IAM/Converse fields are not introduced.

### Model discovery

* Model discovery is attempted for supported providers.
* OpenAI-specific model prefix filtering applies only to OpenAI.
* Non-OpenAI OpenAI-compatible model IDs are not incorrectly filtered out.
* No built-in curated model defaults are returned for providers.
* Failed discovery allows manual model entry.
* Save without model remains invalid.

### Secrets

* API keys are written only through Vault.
* API responses never return raw credentials.
* Responses expose only `has_secret`.
* Replacing credentials explicitly writes a new Vault secret.
* Removing required credentials remains invalid.

### Authorization

* System admin can create/inspect/update/delete LLM configs.
* Team leader cannot provision credentials.
* Normal user cannot provision credentials.
* Team/user selection behavior remains scoped to active configs and allowed models.

### Migration

* Existing configs get `provider_preset` backfilled.
* Existing OpenAI configs become `openai`.
* Existing Bedrock configs become `bedrock_http_gateway`.
* Existing Ollama configs become `ollama`.
* Unknown OpenAI-compatible configs become `custom_openai_compatible`.

## Test Plan

### Unit tests

Test provider preset catalog:

```text id="vnqyx4"
- all expected preset keys exist
- each preset maps to expected adapter
- default base URLs are correct
- Custom OpenAI-compatible has no default base URL
- Bedrock has default region
- credential requirements are correct
```

Test provider inference:

```text id="x5yy5s"
- api.openai.com -> openai
- openrouter.ai -> openrouter
- api.x.ai -> xai
- api.groq.com -> groq
- api.deepseek.com -> deepseek
- api.mistral.ai -> mistral
- Together host -> together
- bedrock_chat -> bedrock_http_gateway
- ollama_chat -> ollama
- unknown openai_chat -> custom_openai_compatible
```

Test model filtering:

```text id="mvmjce"
- OpenAI keeps gpt/o/chatgpt model IDs
- OpenAI removes embeddings/transcribe/tts/image/moderation models
- OpenRouter model IDs are not removed by OpenAI prefix rules
- xAI model IDs are not removed by OpenAI prefix rules
- Groq/Mistral/DeepSeek/Together model IDs are not removed by OpenAI prefix rules
```

Test Bedrock URL helpers:

```text id="gaqey9"
- eu-west-2 -> https://bedrock-mantle.eu-west-2.api.aws/v1
- invalid region rejected
- region extracted from valid Bedrock gateway URL
```

### Service tests

Test inspect behavior:

```text id="vydglj"
- successful discovery returns fetched status and model options
- failed discovery returns manual_required
- no built-in fallback models returned on failure
```

Test upsert behavior:

```text id="v46zqh"
- provider_preset saved
- inspection_metadata_json saved
- manual model save allowed after failed discovery
- missing model rejected
- branded preset with changed base URL reclassifies to custom
- required credential missing rejected
- credential replace writes to Vault
```

### API/auth tests

```text id="ac61l2"
- system admin can inspect/create/update/delete provider configs
- team leader cannot provision provider credentials
- normal user cannot provision provider credentials
- returned config includes has_secret but not secret
```

### Migration tests

```text id="w8fxg9"
- migration adds provider_preset non-null
- migration adds inspection_metadata_json default {}
- existing configs backfilled correctly
- unknown configs fall back to custom_openai_compatible
```

## Suggested Agent Task Breakdown

### Task 1 — Provider preset model and catalog

* Add `LlmProviderPreset`.
* Add preset definition/catalog module.
* Add region list for Bedrock HTTP gateway.
* Add unit tests.

### Task 2 — Schema and migration

* Add `provider_preset` to LLM config.
* Add `inspection_metadata_json` to LLM config.
* Backfill existing rows.
* Update schema response/request models.
* Add migration tests.

### Task 3 — Model discovery extraction

* Extract current OpenAI/Ollama/Bedrock model discovery into a deep module.
* Generalize OpenAI-compatible discovery.
* Split provider-specific model filtering.
* Remove built-in fallback model suggestions except existing behavior that must be deleted/changed.
* Add discovery tests.

### Task 4 — LLM config service update

* Apply provider presets in inspect/upsert.
* Reclassify branded base URL overrides to custom.
* Save inspection metadata.
* Allow manual model save after failed discovery.
* Preserve Vault secret behavior.
* Add service tests.

### Task 5 — Admin UI update

* Replace adapter-first UI with provider dropdown.
* Add branded presets.
* Add Bedrock region selector.
* Add custom provider advanced base URL.
* Add manual model fallback UI after failed inspection.
* Add warning/reclassification behavior for base URL override.
* Add admin UI tests.

### Task 6 — Compatibility cleanup

* Preserve existing team selection/user preference flows.
* Ensure usage/generation paths still resolve active provider/model.
* Do not introduce Anthropic/Gemini/Azure/native Bedrock in this slice.

## Final Dev-Agent Instruction

Implement branded LLM provider presets as a thin UI/product layer over a small protocol-oriented adapter set. Keep credential provisioning system-admin-only and Vault-backed. Add `provider_preset` and `inspection_metadata_json` to LLM configs. Support OpenAI, OpenRouter, xAI, Groq, Mistral, DeepSeek, Together AI, Ollama, Bedrock HTTP gateway, and Custom OpenAI-compatible. Use live model discovery only; do not maintain provider fallback model lists. If discovery fails, allow a system admin to save a manually entered model. If a branded preset’s base URL is changed, save it as Custom OpenAI-compatible. Keep Bedrock as the existing HTTP/gateway implementation and add a region/geographical endpoint selector.

[1]: https://platform.openai.com/docs/api-reference/chat/create-chat-completion?utm_source=chatgpt.com "Chat Completions | OpenAI API Reference"
[2]: https://openrouter.ai/docs/api/reference/overview?utm_source=chatgpt.com "OpenRouter API Reference | Complete API Documentation | OpenRouter | Documentation"
[3]: https://docs.x.ai/docs/api-reference?api-key=6e6ce713-5ea6-44ba-82ae-afdd5d58b527&cluster=us-east-1&model=grok-2-1212&utm_source=chatgpt.com "REST API Reference | xAI"
[4]: https://console.groq.com/docs/openai?utm_source=chatgpt.com "OpenAI Compatibility - GroqDocs"
[5]: https://docs.mistral.ai/api/?utm_source=chatgpt.com "API Specs"
[6]: https://api-docs.deepseek.com/?utm_source=chatgpt.com "Your First API Call | DeepSeek API Docs"
[7]: https://docs.together.ai/reference?utm_source=chatgpt.com "Create Chat Completion - Together.ai Docs"
