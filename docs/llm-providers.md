# LLM Provider Presets

OpenScribe LLM provisioning is system-admin-only. Team leaders and users can select only from active, system-admin-provisioned configs and never receive raw credentials.

## Setup Status

LLM provider setup is stateful:

- `pending_model_selection` means the system admin has checked the provider key/endpoint, and any supplied key is saved in Vault, but no default model has been chosen yet.
- `ready` means the provider has a label, default model, setup-complete status, and may be made available for team selection.

Pending providers are visible only to system admins as `Setup incomplete`. Team leaders and users never receive pending providers from selection-option routes and cannot select a pending provider by ID. Selectable providers must be `ready`, `is_active=true`, and have a non-empty `model_name`.

Credential replacement uses a versioned Vault path rather than overwriting the live path before database commit. Replacement, removal, draft cancellation, revision promotion, provider deletion, and team deletion commit a FK-free cleanup intent with the exact retired reference; a scheduled worker retries Vault deletion and first verifies the reference is no longer live.

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

For Bedrock HTTP gateway, use the region selector for standard `bedrock-mantle.<region>.api.aws` endpoints. The admin UI force-applies provider defaults when the selected provider changes, derives the standard base URL from the selected Bedrock region, hides the editable Base URL field while the Bedrock preset is selected, and shows the derived endpoint beside the region selector. The server treats an explicit Bedrock region as the source of truth even if the browser submits a stale base URL. A non-Mantle custom Bedrock URL is saved as Custom OpenAI-compatible so runtime adapter semantics stay explicit.

## Model Discovery

Model lists come from live discovery. OpenScribe no longer supplies built-in LLM model fallback lists for failed discovery.

- Successful discovery stores provider-returned models in `available_models_json`.
- The admin wizard first creates a pending draft after `Check API key and find models`; the API key is not returned to the browser or carried in hidden inputs after this step.
- Provider credential rejection (`401`/`403` or SDK authentication errors) returns `llm_invalid_credential`, does not create a draft config, and does not write a Vault secret. This remains true even when a manual model name is supplied. Browser setup stays on the Provider/API key step with the API-key rejection message.
- Successful discovery that returns zero compatible chat models is treated as `manual_required`, not `fetched`, so admins must enter an explicit model name before save.
- When successful discovery returns models, the saved `model_name` must be one of those discovered models. If no model is submitted, OpenScribe uses the first discovered model.
- Failed non-auth discovery returns `manual_required` and allows a system admin to enter a model name manually. The browser warns that generation may fail if the endpoint, key, or model name is wrong. When saved, that manual model is stored as the only selectable model so team selection and user preference validation keep working.
- Save without a model name remains invalid.
- Editing a saved provider's preset, adapter, or base URL while keeping the existing Vault-backed secret triggers fresh model discovery with that saved secret. If rediscovery fails, OpenScribe clears stale provider models and stores only the submitted manual model.
- OpenAI-specific model prefix filtering applies only to the OpenAI preset. Other OpenAI-compatible providers keep valid non-OpenAI model IDs after removing non-chat categories such as embeddings, transcription, TTS, moderation, and image models.
- Mistral and Together AI discovery uses provider metadata before accepting models. Mistral keeps non-archived records with `capabilities.completion_chat=true`; Together keeps `chat`, `language`, and `code` model types.
- `inspection_metadata_json` is service-owned operational metadata. Public save requests cannot set or forge discovery status, warnings, notes, or provider display data.
- Discovery metadata includes `inspected_at` so stale provider inspection state is visible without exposing secrets or content.
- Team LLM selection responses include the selected provider preset and display name so leader/user UI can show the provider brand without exposing credentials.
- Saved-provider re-inspection persists the latest discovery metadata even when discovery fails, without overwriting the previous selectable model list unless new models are returned.
- Editing a saved provider without replacing its credential still validates the submitted default model against any non-empty saved provider model list. Manual entry remains allowed only when live discovery requires manual fallback and no provider models are available.

## Secrets

Bearer tokens remain Vault-backed. API responses expose `has_secret` only and never return `vault_secret_ref` or raw secret material.

Required-token presets must have an existing or replacement Vault-backed token. Ollama can be saved without a token for local deployments.

Blank revision credentials are inherited only by required-token presets. The draft reads the target token for inspection, then immediately writes it to a unique versioned Vault path owned by the draft config; it never persists the target reference. This lets root credential replacement and retired-reference cleanup proceed without breaking a pending revision. Revising a credentialed provider to Ollama with no submitted token creates a no-auth draft and retires the old Vault reference only after promotion commits. Supplying an optional Ollama token keeps bearer authentication. Runtime and saved inspection read a Vault reference only when the config's `auth_mode` is `bearer`.

When a ready provider's model catalog changes, OpenScribe reconciles dependent selections in the same database transaction. Existing leader-approved models are retained only when still advertised; a disjoint catalog narrows access to the new provider default. Removed team-default models move to that default, and a removed hallucination-check override clears so the checker uses the same provider config's default rather than another provider.

Replacing a saved key reruns discovery, marks the config `pending_model_selection`, disables team availability, and requires the admin to save a default model again.

Queued or processing generated documents block normal provider edits and provider deletion so runtime snapshots stay stable. Credential correction is the exception: a system admin may replace the Vault-backed key for the same provider endpoint while generated documents are queued or processing, because an invalid key can otherwise leave failed or stuck generation work blocking the fix. During in-flight work, the replacement is rejected unless live inspection confirms that the corrected credential exposes the saved default model used by those queued documents. This prevents swapping to a different account/provider that cannot run the snapshotted model. If the corrected key still exposes the saved default model, the provider remains `ready` and keeps its current availability instead of being moved back to incomplete setup. If the full edit form submits incidental label/model/availability changes during this correction, OpenScribe keeps those existing provider fields unchanged and updates only the credential/discovery metadata. A provider already left in `pending_model_selection` by an earlier credential correction may be finalized while generated documents remain queued or processing. A ready provider may also be toggled between available/unavailable during queued or processing generations when label, endpoint, and model stay unchanged.

## Generated Document Request Payloads

New generated documents store an encrypted snapshot of the outbound LLM request on `generated_documents.llm_request_payload_json_encrypted`.

- While a template note is queued, the field may temporarily hold saved note-generation options so a later preference edit cannot change that queued job.
- When the worker starts, the snapshot is replaced with the exact request body/messages sent to the provider adapter, including redacted transcript/dictation text and redacted template or quick-action prompt where applicable.
- Raw provider secrets are not stored in the snapshot.
- The generated-document detail API decrypts and returns `llm_request_payload_json` only through existing generated-document owner access paths.
- Older generated documents may have `llm_request_payload_json=null`; the transcript UI shows `LLM request not available for this document.`

Template note output caps are adapter-specific:

- OpenAI-compatible providers and the Bedrock HTTP gateway receive `max_completion_tokens`.
- Ollama `/api/chat` receives `options.num_predict`.
- Saved length presets map to `short=800`, `normal=1600`, and `long=3200`; absent preferences use `normal`.
- Length/detail presets apply only to template note generation. Follow-ups and quick actions keep their existing request shape.

## Labels

LLM config labels are unique per team after trimming surrounding whitespace and comparing case-insensitively. Draft creation, finalization, and legacy save paths all return `409 conflict` with `An LLM provider with this name already exists for this team.` before saving duplicates.

When a system admin supplies a label during draft creation, OpenScribe preserves it. If no label is supplied, the backend generates the default provider/team label.
