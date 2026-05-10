## Major issues to fix before merge

### 1. Manual model configs are saved, but team selection can break for them

The service now allows saving a config when discovery fails and the admin manually enters `model_name`. That matches the decision. However, if discovery fails, `available_models_json` is saved as an empty list. Later, `set_team_llm_selection()` rejects explicit allowed models or model overrides when `provider_available_models_json` is empty. The code path still treats an empty provider model list as “this provider does not expose selectable models.” 

That means a manually saved model may be difficult or impossible to select cleanly through the team selection UI if the form submits the model as an override or allowed model.

**Fix**

When discovery fails but `model_name` is manually supplied, store it as the only selectable model:

```python
if not available_models_json and model_name:
    available_models_json = [model_name]
    discovery_metadata["manual_model_name"] = model_name
```

Or update team-selection logic to treat `config.model_name` as selectable when `available_models_json` is empty.

Preferred fix: **store manual model as `available_models_json=[model_name]`**. It keeps existing selection/preference validation working.

---

### 2. Editing provider/base URL without replacing the secret can leave stale model lists

In `upsert_llm_config`, if an existing config is edited and the credential is kept, the service reuses the old `available_models_json`. 

This is risky:

```text
Existing config: OpenAI, models = ["gpt-4.1"]
Admin changes provider/base URL to OpenRouter, keeps credential
Config may still carry stale OpenAI model list
```

This violates the provider preset behavior we wanted: model availability should come from live discovery or manual entry, not stale models from a different provider.

**Fix**

Detect provider/base URL/adapter changes on existing configs.

If changed and credential is kept:

* either read the existing Vault secret and rediscover models against the new endpoint, or
* clear `available_models_json` and mark discovery as `manual_required`.

Suggested behavior:

```python
provider_endpoint_changed = (
    config is not None
    and (
        config.provider_preset != provider_preset
        or config.adapter_kind is not adapter_kind
        or config.base_url.rstrip("/") != base_url.rstrip("/")
    )
)

if provider_endpoint_changed and not replacing_secret:
    if config.vault_secret_ref:
        token = read_team_llm_bearer_token(team_id=team.id, config_id=config.id)
        try rediscovery with token
    else:
        available_models_json = []
        discovery_metadata = manual_required
```

If rediscovery is not implemented in this slice, clear stale models and require/manual-use the submitted `model_name`.

---

### 3. `inspection_metadata_json` is client-writable

`LlmConfigUpsert` accepts `inspection_metadata_json`, and `upsert_llm_config()` saves `payload.inspection_metadata_json or discovery_metadata`.  

This lets the browser/API client forge inspection metadata such as discovery status, warnings, notes, or provider display metadata. It is not a raw secret leak, but it makes operational metadata untrustworthy.

**Fix**

Remove `inspection_metadata_json` from the public upsert schema, or ignore it in the service.

Use only service-generated metadata:

```python
config.inspection_metadata_json = discovery_metadata
```

For updates where no discovery was attempted, preserve existing metadata:

```python
config.inspection_metadata_json = discovery_metadata or dict(config.inspection_metadata_json or {})
```

---

### 4. Failed saved-config inspection does not update inspection metadata

`inspect_saved_llm_config()` only updates the config if `inspection.available_models` is non-empty. If inspection fails or becomes `manual_required`, the failure metadata is not persisted. 

We agreed `inspection_metadata_json` should store last discovery status, warnings, and manual-required state.

**Fix**

Always update `inspection_metadata_json` after saved inspection.

Only update `available_models_json` and maybe `model_name` when models are returned.

```python
config.inspection_metadata_json = _inspection_metadata(inspection)

if inspection.available_models:
    config.available_models_json = list(inspection.available_models)
    if config.model_name not in inspection.available_models:
        config.model_name = inspection.model_name
```

---

### 5. The adapter rename was not implemented

The branch keeps `LlmAdapterKind.openai_chat` as the adapter for OpenAI, OpenRouter, xAI, Groq, Mistral, DeepSeek, Together, and Custom OpenAI-compatible. The preset catalog maps these providers to `LlmAdapterKind.openai_chat`. 

This is not necessarily a functional bug, but it diverges from the implementation brief, which said to evolve toward `openai_compatible_chat`.

**Decision needed**

Either:

* accept this as a deliberate compatibility choice for this slice, and document that `openai_chat` now means “OpenAI-compatible chat,” or
* add the enum rename/migration now.

My recommendation: **do not block merge on the rename** if the code works, but add a comment/doc note to avoid confusion:

```text
openai_chat is currently used as the OpenAI-compatible chat adapter.
```

Longer term, rename it.

---

## Medium issues / feature gaps

### 6. Branded presets appear to be wired more fully in `admin.html` than `admin2.html`

The branch changes `admin.html` substantially for provider/base URL sync, while `admin2.html` changed only minimally. The commit diff explicitly shows the JavaScript sync fix in `admin.html`; `admin2.html` has only a small change set.  

Because `/admin2` is an active admin route, this may mean the restyled admin page has incomplete provider-preset UX.

**Fix**

Verify `/admin2?tab=llm` manually and add tests for:

* provider preset dropdown
* provider default base URLs
* Bedrock region selector
* Custom OpenAI-compatible visible last
* provider/base URL reclassification note

If `admin2` intentionally uses the same form partials, fine. If not, this is a missing UI feature.

---

### 7. Bedrock custom base URL behavior may be stricter than agreed

`reclassify_preset_for_base_url()` reclassifies Bedrock to `custom_openai_compatible` if the base URL does not match the `bedrock-mantle.<region>.api.aws` pattern. 

This matches one version of the implementation brief, but it means “custom Bedrock HTTP gateway URL” is not preserved as Bedrock unless it follows the exact Mantle URL pattern.

**Confirm intended behavior**

If “custom region/base URL override” means “still Bedrock HTTP gateway,” then this is too strict.

If “custom full URL means Custom OpenAI-compatible,” then current behavior is correct.

Given our last brief said custom full URL can reclassify, this is acceptable, but the UI should make it explicit.

---

### 8. Provider preset defaults are duplicated

Defaults exist in `app/services/llm_presets.py`, but schema validators in `app/schemas/llm.py` also duplicate default base URLs and preset mapping.  

This creates drift risk.

**Fix**

Move provider default application out of Pydantic schemas as much as possible, or have schema validators call a single shared preset function. The service module should be the source of truth.

This is not a release blocker, but it will become a maintenance problem.

---

### 9. No CI status visible for the branch

I did not see commit statuses/checks for `51fc2102a9c41c2d3b0080aa2b458cb6ebeb0827`. The docs list focused pytest commands, but I cannot verify a full test run from the available status output.

**Before merge**

Run at least:

```bash
pytest -q tests/test_migrations.py
pytest -q tests/test_api.py -k llm
pytest -q tests/test_admin_ui.py -k llm
pytest -q
```

---

## Things implemented correctly

### Provider catalog and inference

The provider preset catalog includes the requested branded presets and maps them to existing adapters. It also includes Bedrock HTTP gateway regions and model filtering logic. 

Good:

* OpenAI
* OpenRouter
* xAI
* Groq
* Mistral
* DeepSeek
* Together
* Ollama
* Bedrock HTTP gateway
* Custom OpenAI-compatible

### Migration/backfill

The migration adds `provider_preset`, adds `inspection_metadata_json`, backfills existing configs by adapter/base URL, makes `provider_preset` non-null, and indexes it. 

This matches the agreed approach.

One caution: verify the multi-head `down_revision` is correct for the current Alembic graph. The migration declares two down revisions, so it appears to be a merge migration. 

### No built-in fallback models

The old OpenAI fallback constants are gone from the service, and failed discovery now returns `manual_required` with no suggested built-in model list. 

This matches your decision: **auto-discover only**, no curated provider defaults.

### OpenAI-only filtering no longer affects every provider

`filter_discovered_models()` blocks obvious non-chat model categories generally, then applies OpenAI prefix filtering only when `provider_preset == openai`. 

This fixes the original risk where OpenRouter/xAI/Groq/etc. model IDs would be hidden.

### Secret handling mostly preserved

The branch continues to use Vault for LLM bearer tokens via existing service functions and does not expose raw secrets in config response shape. 

The delete path was also improved to delete the DB record before cleaning up the Vault secret, reducing the risk of DB/Vault inconsistency on DB commit failure. 

---

## Missing features from the brief

These are either missing or only partially implemented:

1. **Manual model should become selectable**
   Currently manual save is allowed, but the saved model may not populate `available_models_json`, which breaks selection/preference ergonomics.

2. **Provider/base URL changes should not carry stale model lists**
   Existing configs need rediscovery or clearing when endpoint/provider changes.

3. **Inspection metadata should be service-owned**
   It should not be accepted from the public upsert payload.

4. **Saved inspection failures should persist metadata**
   `manual_required` should be recorded, not ignored.

5. **`admin2.html` parity needs verification**
   It may not fully expose the branded preset UX.

6. **Internal adapter name remains `openai_chat`**
   Decide whether to accept as compatibility debt or rename to `openai_compatible_chat`.

---

## Recommended patch list before merge

1. Remove or ignore `inspection_metadata_json` from `LlmConfigUpsert`.
2. Persist inspection metadata on every saved inspect attempt, including failures.
3. When manual `model_name` is supplied and discovery returns no models, save `available_models_json=[model_name]`.
4. Detect provider/base URL changes on existing configs and either rediscover with existing Vault token or clear stale model lists.
5. Add tests for manual model selection after failed discovery.
6. Add tests for editing an existing config from one provider/base URL to another without stale model leakage.
7. Add tests for `admin2` provider preset parity or explicitly remove/retire the old path.
8. Decide/document whether `openai_chat` is now the “OpenAI-compatible chat” adapter.

## Merge recommendation

Do **not** merge yet. The branch is close, but the stale model-list issue and manual-model selection issue are likely to create confusing admin/team behavior. Fix those before merge.
