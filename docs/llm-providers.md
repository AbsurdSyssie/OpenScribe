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

If a branded OpenAI-compatible preset is saved with a changed base URL, OpenScribe stores it as `custom_openai_compatible`. Bedrock remains the existing HTTP gateway path; native AWS Bedrock Converse/IAM is not part of this slice.

## Model Discovery

Model lists come from live discovery. OpenScribe no longer supplies built-in LLM model fallback lists for failed discovery.

- Successful discovery stores provider-returned models in `available_models_json`.
- Failed discovery returns `manual_required` and allows a system admin to enter a model name manually.
- Save without a model name remains invalid.
- OpenAI-specific model prefix filtering applies only to the OpenAI preset. Other OpenAI-compatible providers keep valid non-OpenAI model IDs after removing non-chat categories such as embeddings, transcription, TTS, moderation, and image models.

## Secrets

Bearer tokens remain Vault-backed. API responses expose `has_secret` only and never return `vault_secret_ref` or raw secret material.

Required-token presets must have an existing or replacement Vault-backed token. Ollama can be saved without a token for local deployments.
