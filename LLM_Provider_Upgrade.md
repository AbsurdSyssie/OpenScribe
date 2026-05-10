## Review verdict

The branch is **mostly aligned with the agreed implementation brief**. It adds the core provider-preset model, migration, preset catalog, Bedrock HTTP region handling, provider-specific discovery for Mistral/Together, manual model fallback, and keeps system-admin-only provisioning. I would not merge yet without a test run, but I only see a few concrete issues/risks.

Branch reviewed: `LLM_Provider_Upgrade` at `5177f2d655adcb3de842156e0b3839e8806b5864`.

No GitHub status checks or workflow runs were available for this commit, so the final gate should still be a local/CI test pass.

## What looks correctly implemented

### 1. Provider preset model exists

The branch adds `LlmProviderPreset` with the expected first-slice providers:

```text id="ybh9ba"
openai
openrouter
xai
groq
mistral
deepseek
together
ollama
bedrock_http_gateway
custom_openai_compatible
```

It also documents that `LlmAdapterKind.openai_chat` remains the historical adapter name for the broader OpenAI-compatible chat protocol. 

### 2. DB migration broadly matches the decision

The migration adds:

* `provider_preset`
* `inspection_metadata_json`
* an index on `provider_preset`
* backfill logic for OpenAI, OpenRouter, xAI, Groq, DeepSeek, Mistral, Together, Bedrock, Ollama, and fallback custom OpenAI-compatible. 

This matches the agreed “first-class string column + URL inference” approach.

### 3. Preset catalog is present

`app/services/llm_presets.py` contains the branded preset catalog, Bedrock HTTP regions, base URLs, provider inference, base URL reclassification, and provider-specific filtering. 

Notable correct pieces:

* Custom OpenAI-compatible is present and marked advanced.
* Bedrock region list exists.
* Branded OpenAI-compatible presets are identified for reclassification.
* Non-OpenAI providers avoid OpenAI prefix filtering.

### 4. Bedrock HTTP gateway stayed HTTP/gateway-style

The branch preserves the existing Bedrock HTTP URL pattern:

```text id="2mq28a"
https://bedrock-mantle.<region>.api.aws/v1
```

The helper functions were moved into `app/llm_provider_defaults.py`, including `normalize_bedrock_region`, `bedrock_chat_base_url`, and `bedrock_region_from_base_url`. 

### 5. LLM schemas expose provider preset and inspection metadata

`LlmConfigUpsert`, `LlmInspectRequest`, and `LlmConfigDetail` now include provider preset fields. `LlmConfigInspectResult` now includes provider display name, discovery status, warnings, and notes. 

### 6. Manual model fallback is implemented

The service allows manual model entry when discovery is unavailable and persists the manual model into `available_models_json`, which is necessary for team selection/user preference flows. 

### 7. Mistral/Together got provider-specific model discovery

The service adds `_list_mistral_chat_models()` and `_list_together_chat_models()` and routes those presets through the provider-specific paths rather than generic OpenAI-compatible discovery. 

### 8. Secret handling remains Vault-backed

The branch keeps LLM secrets behind Vault through the existing Vault service. It also improves delete behavior by deleting provider secrets after DB commit for config deletion, reducing the risk of DB/Vault inconsistency. 

## Findings to fix or confirm

### Finding 1 — Empty successful discovery is treated inconsistently

If a provider discovery call succeeds but returns **zero models**, the service currently treats discovery as `"fetched"` in some paths. Then, if a manual `model_name` is supplied, it accepts the model and adds it to `available_models_json`, but the metadata can still say discovery was fetched/provider-sourced rather than manual.

This can happen with provider-specific filters if the endpoint responds successfully but the filtered list is empty.

**Expected behavior**

If discovery returns an empty list, treat it like manual-required:

```text id="1vtktj"
available_models = []
discovery_status = manual_required
default_model_source = manual
warning = "No compatible chat models were returned. Enter a model name manually."
```

**Suggested fix**

After every successful model-list call:

```python id="cywd0s"
if not models:
    discovery_status = "manual_required"
    default_model_source = "manual"
    warnings = ["No compatible chat models were returned. Enter a model name manually."]
else:
    discovery_status = "fetched"
    default_model_source = "provider"
```

This applies to:

* `inspect_llm_contract`
* `upsert_llm_config`
* Mistral discovery
* Together discovery
* generic OpenAI-compatible discovery
* Bedrock gateway discovery
* Ollama discovery

Severity: **medium**, because it affects metadata/admin UX more than runtime safety.

### Finding 2 — `/admin2` parity still needs explicit verification

The branch modifies both `admin.html` and `admin2.html`, but `admin.html` appears to have the fuller provider-preset treatment while `admin2.html` changed less. I could not fully verify the rendered `/admin2` LLM form from the truncated file output.

Since `/admin2` is an active admin route, require an explicit test that `/admin2?tab=llm` renders:

```text id="08ccox"
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
Bedrock region selector
manual model fallback copy
base URL override/reclassification copy
```

Severity: **medium**, because a missing `/admin2` control would make the feature feel half-implemented even if the API works.

### Finding 3 — `LlmSelectionDetail` does not expose provider preset/display name

`LlmConfigDetail` includes `provider_preset`, but `LlmSelectionDetail` still exposes only selected config label, adapter kind, base URL, available models, allowed models, and resolved model. 

This is not a blocker if the selection UI only needs the config label. But if team leaders/users should see “OpenRouter,” “xAI,” etc. when reviewing the active selection, selection responses should include:

```python id="ligzd8"
selected_config_provider_preset: str
selected_config_provider_display_name: str
```

Severity: **low/medium**, depending on the desired UI.

### Finding 4 — Migration is PostgreSQL-specific

The migration uses PostgreSQL-specific syntax such as:

```sql id="4idkv1"
adapter_kind::text
'{}'::json
```

That may be fine because the migration tests use a real test database URL and reset the public schema, which indicates PostgreSQL-oriented migration testing. 

Still, confirm the app does not need SQLite migration compatibility. If SQLite is not supported for Alembic tests, this is fine.

Severity: **low**, unless SQLite migrations are expected.

### Finding 5 — Removed docs should be intentional

The branch removes:

```text id="8bl5bu"
API_Inspection_Upgrade.md
main_refactor_plan.md
```

and adds:

```text id="1mz7lo"
LLM_Provider_Upgrade.md
docs/llm-providers.md
```

The compare output shows these as explicit file changes. If those removed docs are obsolete planning artifacts, fine. If they are still referenced elsewhere, restore or move their remaining useful content.

Severity: **low**.

## Features from the brief that appear added

| Feature                                         | Status                                  |
| ----------------------------------------------- | --------------------------------------- |
| Branded presets                                 | Added                                   |
| First-slice providers                           | Added                                   |
| Custom OpenAI-compatible visible/advanced       | Added in catalog; verify both templates |
| `provider_preset` DB field                      | Added                                   |
| `inspection_metadata_json` DB field             | Added                                   |
| Migration backfill                              | Added                                   |
| Bedrock HTTP region list                        | Added                                   |
| Bedrock HTTP URL helpers                        | Added                                   |
| Base URL override reclassification              | Added                                   |
| Live discovery only; no curated fallback models | Mostly added                            |
| Manual model save after discovery failure       | Added                                   |
| Mistral/Together metadata-aware discovery       | Added                                   |
| System-admin-only provisioning                  | Preserved                               |
| Vault-backed secrets                            | Preserved                               |
| Native Anthropic/Gemini/Azure/Bedrock           | Correctly not added                     |

## Recommended final patch before merge

### 1. Normalize zero-model discovery to manual-required

Add helper:

```python id="mb32vb"
def _model_discovery_result(
    *,
    provider_preset: str,
    models: list[str],
    empty_warning: str,
    fetched_note: str | None = None,
) -> tuple[list[str], str, str, list[str], list[str]]:
    if models:
        return models, "fetched", "provider", [], [fetched_note] if fetched_note else []
    return [], "manual_required", "manual", [empty_warning], []
```

Use it in inspect and upsert paths.

### 2. Add `/admin2` parity tests

Add assertions for the same provider options and Bedrock region selector on both admin templates/routes.

### 3. Consider enriching selection response

Add provider preset/display fields to `LlmSelectionDetail` if the UI will show selected provider brand outside the config list.

### 4. Run tests

At minimum:

```bash id="qrx2wx"
pytest tests/test_api.py -q
pytest tests/test_admin_ui.py -q
pytest tests/test_migrations.py -q
```

Prefer:

```bash id="h8t2ky"
pytest
```

## Merge recommendation

Do **not** merge blind. Merge after:

1. zero-model discovery metadata is fixed or explicitly accepted,
2. `/admin2` parity is verified by tests,
3. the targeted test suite passes,
4. removed docs are confirmed intentional.

The core implementation is in good shape and does not look like it missed the major agreed features.
