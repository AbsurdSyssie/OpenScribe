# Agent Plan: Finish LLM Provider Preset Branch

Target branch/head:

```text id="kk3oa4"
LLM_Provider_Upgrade
4e7e92dab32f6fda49dbf2db06239e574b388f9e
```

CI/status note: no commit statuses were returned for the head SHA, so run the full test suite locally/CI before merge.

## Objective

Finish the branded LLM provider preset implementation and make it safe to merge.

The branch is broadly correct. It already adds:

* `LlmProviderPreset`
* `provider_preset` on LLM configs
* `inspection_metadata_json` on LLM configs
* provider preset catalog
* Bedrock HTTP gateway region list
* model filtering split by provider
* manual model fallback
* Vault-backed secret behavior preserved
* provider/base URL reclassification to Custom OpenAI-compatible

The remaining work is mostly tightening correctness.

---

# 1. Blocker: Validate `model_name` against discovered models

## Problem

`upsert_llm_config()` currently accepts a manually submitted `model_name` even when live discovery succeeded and returned a provider model list.

Bad state possible:

```text id="eis5cw"
available_models_json = ["gpt-4.1", "gpt-4.1-mini"]
model_name = "not-a-real-model"
```

Manual model names should be allowed only when discovery failed/manual discovery is required.

## Required behavior

| Discovery result                               | Submitted model behavior                          |
| ---------------------------------------------- | ------------------------------------------------- |
| Discovery succeeded and returned models        | `model_name` must be one of the discovered models |
| Discovery failed / manual required             | manually submitted `model_name` is allowed        |
| No model submitted and discovered models exist | use first discovered model                        |
| No model submitted and no discovered models    | reject save                                       |

## Implementation guidance

In `upsert_llm_config()`, track whether models came from successful live discovery.

Suggested shape:

```python id="2gp35m"
discovery_succeeded = False
available_models_json: list[str] = []

# When provider model discovery succeeds:
available_models_json = discovered_models
discovery_succeeded = True

# When provider discovery fails/manual mode:
available_models_json = []
discovery_succeeded = False
```

Then enforce:

```python id="f8pe1s"
model_name = payload.model_name.strip() if payload.model_name else (
    available_models_json[0] if available_models_json else None
)

if not model_name:
    raise AppError(
        422,
        "business_rule_violation",
        "Model name is required. Inspect models successfully or enter a model name manually.",
        {"field": "model_name"},
    )

if discovery_succeeded and available_models_json and model_name not in available_models_json:
    raise AppError(
        422,
        "business_rule_violation",
        "Selected model is not available for this provider",
        {"field": "model_name"},
    )

if not available_models_json and model_name:
    available_models_json = [model_name]
    discovery_metadata = discovery_metadata or _discovery_metadata(
        provider_preset=provider_preset,
        discovery_status="manual_required",
        default_model_source="manual",
        warnings=[],
        notes=[],
    )
    discovery_metadata["manual_model_name"] = model_name
```

## Tests to add

```text id="xfgz3v"
- Live discovery returns ["model-a", "model-b"]; saving "model-a" succeeds.
- Live discovery returns ["model-a", "model-b"]; saving "missing-model" fails.
- Discovery fails; saving manual "manual-model" succeeds.
- Discovery fails; saving with no model fails.
```

---

# 2. Blocker: Improve provider model filtering for Mistral and Together

## Problem

The branch uses generic OpenAI SDK model listing and then filters by ID substrings. That is not enough for providers whose model-list APIs expose capabilities/type metadata.

Examples:

* Mistral model records can expose capabilities like `completion_chat`.
* Together model records can expose `type` values like `chat`, `language`, `image`, `embedding`, `rerank`, etc.

If metadata is discarded, non-chat models can become selectable if their IDs do not contain blocked words.

## Required behavior

Use provider-specific metadata where available.

For this branch, implement provider-specific model listing/filtering for at least:

```text id="l5zn2w"
mistral
together
```

Alternatively, mark those providers manual-model-only until metadata-aware filtering is implemented. Preferred: implement metadata-aware filtering.

## Implementation guidance

Keep current generic discovery as a fallback, but add provider-specific direct `httpx` model-list functions.

### Mistral

Suggested function:

```python id="n1kqpc"
def _list_mistral_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            502,
            "llm_inspection_failed",
            "Could not load available Mistral chat models",
        ) from exc

    models: set[str] = set()
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue

        model_id = item.get("id")
        capabilities = item.get("capabilities") or {}

        if not isinstance(model_id, str) or not model_id.strip():
            continue

        if item.get("archived") is True:
            continue

        if capabilities.get("completion_chat") is True:
            models.add(model_id.strip())

    return sorted(models)
```

### Together

Suggested function:

```python id="ijv4il"
def _list_together_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            502,
            "llm_inspection_failed",
            "Could not load available Together AI chat models",
        ) from exc

    # Together may return either a raw list or {"data": [...]}; support both.
    records = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        records = []

    models: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue

        model_id = item.get("id") or item.get("name")
        model_type = item.get("type")

        if not isinstance(model_id, str) or not model_id.strip():
            continue

        if model_type in {"chat", "language", "code"}:
            models.add(model_id.strip())

    return sorted(models)
```

### Route through provider-specific discovery

In `_list_openai_compatible_chat_models()`:

```python id="7gbe5l"
def _list_openai_compatible_chat_models(
    *,
    provider_preset: str,
    api_key: str,
    base_url: str,
) -> list[str]:
    if provider_preset == LlmProviderPreset.openai.value:
        return _list_openai_chat_models(api_key=api_key, base_url=base_url)

    if provider_preset == LlmProviderPreset.mistral.value:
        return _list_mistral_chat_models(api_key=api_key, base_url=base_url)

    if provider_preset == LlmProviderPreset.together.value:
        return _list_together_chat_models(api_key=api_key, base_url=base_url)

    try:
        models = _list_openai_compatible_models(api_key=api_key, base_url=base_url)
    except AppError as exc:
        raise AppError(
            502,
            "llm_inspection_failed",
            "Could not load available OpenAI-compatible chat models",
        ) from exc

    return filter_discovered_models(provider_preset, models)
```

## Tests to add

```text id="ogzlia"
- Mistral discovery keeps models with capabilities.completion_chat = true.
- Mistral discovery drops archived models.
- Mistral discovery drops models without chat capability.
- Together discovery keeps type=chat/language/code.
- Together discovery drops type=image/embedding/rerank/moderation.
- Generic OpenAI-compatible discovery still works for OpenRouter/xAI/Groq/DeepSeek/custom.
```

---

# 3. Blocker: Remove duplicated provider defaults from schemas

## Problem

Provider default URLs are defined in both:

```text id="c5hbj0"
app/services/llm_presets.py
app/schemas/llm.py
```

This creates drift risk. The preset catalog should be the single source of truth.

## Required behavior

Schemas should validate payload shape. Provider defaults should be applied in service/preset code.

## Implementation guidance

### Preferred approach

In `LlmConfigUpsert` and `LlmInspectRequest`:

* Keep `provider_preset`
* Keep `adapter_kind` only for backwards compatibility if needed
* Do not duplicate the default base URL map
* Do not independently derive provider defaults in schema validators

Move all of this logic into:

```text id="gmli79"
apply_provider_defaults()
reclassify_preset_for_base_url()
get_llm_provider_preset()
```

### Minimal safe fix

If removing the schema validators is too invasive, at least make them call the shared helper:

```python id="04gztn"
from app.services.llm_presets import apply_provider_defaults

@model_validator(mode="before")
@classmethod
def apply_provider_defaults(cls, data: object) -> object:
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    provider_preset, adapter_kind, base_url, bedrock_region = apply_provider_defaults(
        provider_preset=normalized.get("provider_preset"),
        base_url=normalized.get("base_url"),
        bedrock_region=normalized.get("bedrock_region"),
    )

    normalized["provider_preset"] = provider_preset
    normalized["adapter_kind"] = adapter_kind
    normalized["base_url"] = base_url
    normalized["bedrock_region"] = bedrock_region

    return normalized
```

Risk: this creates an import from schemas to services. If that introduces a layering/circular import problem, extract the preset catalog into a neutral module such as:

```text id="ghjfl1"
app/llm_presets.py
app/domain/llm_presets.py
app/services/llm_presets.py  # only if no cycle
```

The important point is: **one default map only**.

## Tests to add

```text id="7vogmm"
- Changing preset catalog default affects inspect/upsert default behavior.
- There is no separate default URL map in llm schemas.
```

---

# 4. Medium: Add inspection timestamp

## Problem

`inspection_metadata_json` captures status/warnings/notes but no timestamp.

## Required behavior

Include `inspected_at` or `discovered_at` in service-generated metadata.

## Implementation guidance

Use the existing `utcnow()` helper from models if appropriate.

```python id="vafwsm"
from app.models import utcnow

def _discovery_metadata(...):
    return {
        "provider_preset": preset.key,
        "provider_display_name": preset.display_name,
        "discovery_status": discovery_status,
        "default_model_source": default_model_source,
        "warnings": list(warnings),
        "notes": list(notes),
        "inspected_at": utcnow().isoformat(),
    }
```

Also update `_inspection_metadata()`:

```python id="pvucr5"
def _inspection_metadata(inspection: LlmConfigInspectResult) -> dict[str, object]:
    return {
        "provider_preset": inspection.provider_preset,
        "provider_display_name": inspection.provider_display_name,
        "discovery_status": inspection.discovery_status,
        "default_model_source": inspection.default_model_source,
        "warnings": list(inspection.warnings),
        "notes": list(inspection.notes),
        "inspected_at": utcnow().isoformat(),
    }
```

## Tests to add

```text id="5ww43t"
- Saved config inspection stores inspected_at.
- Upsert with discovery success stores inspected_at.
- Upsert with manual-required discovery stores inspected_at.
```

---

# 5. Medium: Document `openai_chat` compatibility meaning

## Problem

The implementation keeps `LlmAdapterKind.openai_chat` as the shared adapter for OpenAI, OpenRouter, xAI, Groq, Mistral, DeepSeek, Together, and Custom OpenAI-compatible.

That is acceptable for this slice, but the name is now misleading.

## Required behavior

Do not rename the enum in this branch unless you want a larger migration. Instead document that `openai_chat` currently means “OpenAI-compatible chat adapter.”

## Implementation guidance

Add a comment near `LlmAdapterKind`:

```python id="gtyay0"
class LlmAdapterKind(str, enum.Enum):
    # Historical name. In the provider-preset layer this is used as the
    # OpenAI-compatible chat adapter for OpenAI, OpenRouter, xAI, Groq,
    # Mistral, DeepSeek, Together AI, and custom OpenAI-compatible endpoints.
    openai_chat = "openai_chat"
    bedrock_chat = "bedrock_chat"
    ollama_chat = "ollama_chat"
```

Add the same explanation in `docs/llm-providers.md`.

---

# 6. Medium: Confirm Bedrock custom URL UI copy

## Problem

Current behavior reclassifies Bedrock HTTP gateway to Custom OpenAI-compatible if the base URL does not match:

```text id="tg41yt"
bedrock-mantle.<region>.api.aws
```

That is acceptable if the product decision is:

```text id="jhqrsl"
A full custom Bedrock URL becomes Custom OpenAI-compatible.
```

## Required behavior

Make the UI explicit.

## Implementation guidance

In admin LLM provider form, show copy like:

```text id="7sgzec"
Changing the generated Bedrock HTTP gateway URL to a non-Mantle endpoint will save this provider as Custom OpenAI-compatible.
```

For Bedrock:

```text id="8hhf8v"
Use the region selector for standard Bedrock HTTP gateway endpoints. Use Custom OpenAI-compatible for non-standard gateway URLs.
```

## Tests to add

```text id="ri0m32"
- Bedrock region eu-west-2 derives expected URL.
- Bedrock standard generated URL preserves bedrock_http_gateway.
- Bedrock non-Mantle URL reclassifies to custom_openai_compatible.
```

---

# 7. Medium: Verify both admin templates/routes expose the same LLM features

## Problem

The branch changes both `admin.html` and `admin2.html`, but not equally. `/admin2` is an active route. Verify the provider-preset UX works in both.

## Required behavior

Both admin views that expose LLM config must support:

```text id="ny57xo"
- branded provider dropdown
- provider default base URL
- Bedrock region selector
- Custom OpenAI-compatible visible last
- manual model fallback
- base URL override warning/reclassification copy
```

## Implementation guidance

If duplicated template logic is drifting, prefer extracting a shared LLM provider form partial.

Possible structure:

```text id="6gxp1l"
app/templates/partials/llm_provider_form.html
```

Then include it from both admin templates.

## Tests to add

```text id="nle9wv"
- /admin renders OpenRouter, xAI, Groq, Mistral, DeepSeek, Together AI, Bedrock HTTP gateway, and Custom OpenAI-compatible.
- /admin2 renders the same provider options.
- /admin2 renders Bedrock region selector.
```

---

# 8. Optional: Keep or restore removed docs intentionally

## Problem

The branch removes:

```text id="mcgso4"
API_Inspection_Upgrade.md
main_refactor_plan.md
```

and adds:

```text id="qcc9ow"
LLM_Provider_Upgrade.md
docs/llm-providers.md
```

This may be intentional, but confirm before merge.

## Required behavior

Do not accidentally delete active planning docs.

## Implementation guidance

Either:

```text id="5vudkj"
- restore removed docs, or
- move their still-relevant content into docs/, or
- leave deletion but mention it in the PR summary
```

---

# Required final test pass

Run at minimum:

```bash id="jr06pt"
pytest tests/test_api.py -q
pytest tests/test_admin_ui.py -q
pytest tests/test_migrations.py -q
```

Prefer full suite:

```bash id="8sx3de"
pytest
```

Also run lint/type checks if configured.

---

# Acceptance checklist

## Must pass before merge

```text id="gvbv6y"
[ ] Saving a discovered provider model validates model_name against discovered models.
[ ] Manual model save works only when discovery is manual_required/no provider list exists.
[ ] Mistral model discovery filters using chat capability metadata.
[ ] Together model discovery filters using model type metadata.
[ ] Provider defaults have one source of truth.
[ ] No raw credentials are exposed in API responses.
[ ] System-admin-only provisioning still holds.
[ ] Bedrock region selector still derives standard gateway URLs.
[ ] Branded base URL override still reclassifies to custom_openai_compatible.
[ ] Existing migrations pass from empty DB to head.
```

## Should pass before merge

```text id="c4z45j"
[ ] inspection_metadata_json includes inspected_at.
[ ] openai_chat compatibility meaning is documented.
[ ] /admin and /admin2 both expose equivalent LLM provider preset UX.
[ ] PR summary explains any removed docs.
```

---

# Suggested implementation order

## Step 1 — Fix model validation

Implement `discovery_succeeded` tracking and reject invalid `model_name` when discovery succeeds.

This is the highest-risk data consistency bug.

## Step 2 — Add metadata-aware Mistral/Together discovery

Implement provider-specific direct model listing for Mistral and Together.

Keep generic OpenAI-compatible discovery for:

```text id="yv71fh"
OpenAI
OpenRouter
xAI
Groq
DeepSeek
Custom OpenAI-compatible
Bedrock HTTP gateway
```

## Step 3 — Deduplicate provider defaults

Make `llm_presets.py` the single source of truth.

## Step 4 — Add inspection timestamps

Add `inspected_at` to metadata helpers.

## Step 5 — UI/admin parity pass

Verify both `/admin` and `/admin2`.

## Step 6 — Docs and test cleanup

Document `openai_chat` as currently OpenAI-compatible. Confirm deleted docs are intentional.

---

# Agent prompt

Finish the LLM_Provider_Upgrade branch. The implementation is close but needs correctness fixes before merge.

First, update upsert_llm_config so that if live model discovery succeeds and returns a provider model list, the submitted model_name must be in that list. Manual model_name should only be accepted when discovery failed/manual_required or no provider model list is available. When manual model_name is accepted, store it as available_models_json=[model_name] so team selection and user preferences still work.

Second, improve model discovery filtering for Mistral and Together AI. Do not rely only on model ID substring filtering for these providers. Add provider-specific model-list functions that preserve response metadata and only return chat/text-generation-capable models. For Mistral, prefer capabilities.completion_chat == true and exclude archived models. For Together, prefer type in {"chat", "language", "code"} and exclude image/embedding/rerank/moderation models. Keep generic OpenAI-compatible discovery for OpenAI, OpenRouter, xAI, Groq, DeepSeek, Custom OpenAI-compatible, and Bedrock HTTP gateway.

Third, remove duplicated provider default URL maps from schemas. Make the provider preset catalog/default helper the single source of truth for provider defaults. Schemas should validate payload shape, not independently define provider defaults.

Fourth, add inspected_at timestamps to inspection_metadata_json for saved inspection and upsert discovery metadata.

Fifth, document that LlmAdapterKind.openai_chat is currently used as the OpenAI-compatible chat adapter in this slice. Do not rename the enum unless you intentionally add a migration.

Sixth, verify both /admin and /admin2 expose the same LLM provider preset UX: branded provider dropdown, Custom OpenAI-compatible last and advanced, Bedrock region selector, manual model fallback, and base URL override warning.

Finally, add/adjust tests for all of the above and run the targeted test suite.
