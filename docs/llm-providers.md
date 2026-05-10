# LLM Provider Presets

OpenScribe LLM provisioning is system-admin-only. Team leaders and users can select only from active, system-admin-provisioned configs and never receive raw credentials.

## Preset Layer

The admin UI exposes branded presets:

- OpenAI
- OpenRouter
- xAI
- Groq
- Mistral
- DeepSeek
- Together AI
- Ollama
- Bedrock HTTP gateway
- Custom OpenAI-compatible - advanced

`provider_preset` records the admin-facing brand or custom classification. `adapter_kind` remains the runtime protocol implementation:

- OpenAI-compatible presets use `openai_chat`
- Ollama uses `ollama_chat`
- Bedrock HTTP gateway uses `bedrock_chat`

`openai_chat` currently means the OpenAI-compatible chat protocol adapter, not only first-party OpenAI. A future migration may rename the enum, but this slice keeps the existing value for compatibility.

If a branded OpenAI-compatible preset is saved with a changed base URL, OpenScribe stores it as `custom_openai_compatible`. Bedrock remains the existing HTTP gateway path; native AWS Bedrock Converse/IAM is not part of this slice.

For Bedrock HTTP gateway, use the region selector for standard `bedrock-mantle.<region>.api.aws` endpoints. A non-Mantle custom Bedrock URL is saved as Custom OpenAI-compatible so runtime adapter semantics stay explicit.

## Model Discovery

Model lists come from live discovery. OpenScribe no longer supplies built-in LLM model fallback lists for failed discovery.

- Successful discovery stores provider-returned models in `available_models_json`.
- When successful discovery returns models, the saved `model_name` must be one of those discovered models. If no model is submitted, OpenScribe uses the first discovered model.
- Failed discovery returns `manual_required` and allows a system admin to enter a model name manually. When saved, that manual model is stored as the only selectable model so team selection and user preference validation keep working.
- Save without a model name remains invalid.
- Editing a saved provider's preset, adapter, or base URL while keeping the existing Vault-backed secret triggers fresh model discovery with that saved secret. If rediscovery fails, OpenScribe clears stale provider models and stores only the submitted manual model.
- OpenAI-specific model prefix filtering applies only to the OpenAI preset. Other OpenAI-compatible providers keep valid non-OpenAI model IDs after removing non-chat categories such as embeddings, transcription, TTS, moderation, and image models.
- Mistral and Together AI discovery uses provider metadata before accepting models. Mistral keeps non-archived records with `capabilities.completion_chat=true`; Together keeps `chat`, `language`, and `code` model types.
- `inspection_metadata_json` is service-owned operational metadata. Public save requests cannot set or forge discovery status, warnings, notes, or provider display data.
- Discovery metadata includes `inspected_at` so stale provider inspection state is visible without exposing secrets or content.
- Saved-provider re-inspection persists the latest discovery metadata even when discovery fails, without overwriting the previous selectable model list unless new models are returned.
- Editing a saved provider without replacing its credential still validates the submitted default model against any non-empty saved provider model list. Manual entry remains allowed only when live discovery requires manual fallback and no provider models are available.

## Secrets

Bearer tokens remain Vault-backed. API responses expose `has_secret` only and never return `vault_secret_ref` or raw secret material.

Required-token presets must have an existing or replacement Vault-backed token. Ollama can be saved without a token for local deployments.
