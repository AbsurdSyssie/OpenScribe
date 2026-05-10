Existing-config edits can still save an invalid model name

In upsert_llm_config, model validation only rejects an invalid model_name when discovery_succeeded is True. But when editing an existing config without changing provider/base URL and without replacing the credential, the code reuses config.available_models_json, leaves discovery_succeeded = False, and then allows any submitted model_name.

Bad state still possible:

available_models_json = ["gpt-4.1", "gpt-4.1-mini"]
model_name = "not-a-real-model"

This is not catastrophic, because later selection logic may fall back, but it creates inconsistent saved config state and violates the intended rule: if we have a provider model list, the saved default should be in that list.

Fix:

After resolving model_name, validate against available_models_json whenever that list is non-empty and discovery was not manual-required because of failure.

Minimal patch shape:

if available_models_json and model_name not in available_models_json:
    raise AppError(
        422,
        "business_rule_violation",
        "Selected model is not available for this provider",
        {"field": "model_name"},
    )

But preserve manual mode:

manual_required = (
    discovery_metadata.get("discovery_status") == "manual_required"
    and len(available_models_json) == 0
)

if available_models_json and not manual_required and model_name not in available_models_json:
    ...

Add a test:

Existing config has available_models_json=["model-a"].
Admin edits label/base unchanged, keeps credential, submits model_name="model-b".
Expected: 422.
Feature/parity check before merge
Confirm /admin2 has the same LLM provider UX as /admin

/admin2 is an active admin route and renders admin2.html. The branch changed both admin templates, but from the diff size, admin.html appears to have received the fuller provider-preset treatment while admin2.html changed only minimally. I could not fully verify template parity from the truncated file output.

Before merging, explicitly test /admin2?tab=llm for:

OpenRouter option
xAI option
Groq option
Mistral option
DeepSeek option
Together AI option
Bedrock HTTP gateway option
Custom OpenAI-compatible · advanced option
Bedrock region selector
manual model fallback copy
base URL override/reclassification copy

The branch added admin UI tests, but I would specifically require a test that /admin2 renders the same provider options and Bedrock region selector.