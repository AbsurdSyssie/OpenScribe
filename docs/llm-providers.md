# LLM Provider Presets

OpenScribe LLM provisioning is system-admin-only. Team leaders can choose their team's active writing provider; users can choose a personal model only within that provider's saved allowance. Neither receives raw credentials.

## Setup Status

LLM provider setup is stateful:

- `pending_model_selection` means the system admin has checked the provider key/endpoint or runtime identity, and any supplied secret is saved in Vault, but setup has not been finalized.
- `ready` means connection setup has been finalized and the provider may be assigned to team roles. Its setup model is separate from the team's saved model policy.

Pending providers are visible only to system admins as `Setup incomplete`. Team leaders and users never receive pending providers from selection-option routes and cannot select a pending provider by ID. A writing provider must be `ready`, `is_active=true`, and have a valid saved enabled default; `model_name` remains setup/discovery metadata.

Ready configs offer **Edit settings** with **Connection** and **Models** tabs. Each provider has a persistent per-team model allowance and writing default, including while it is not the active writing provider. The Models tab edits only that provider's policy; it does not switch the writing provider. New policies start with no enabled models. An empty allowance means no model is available, and a non-empty allowance requires an enabled default. Existing personal model preferences apply only when their model remains enabled for the active writing provider.
Name and availability changes use the existing scoped details route without
inspecting the provider or changing its credential. A connection change uses
the existing revision flow and saves directly when the saved model remains in
the inspected catalog; when the catalog omits it, choose a replacement before
saving. If discovery returns no catalog, the saved model ID remains available
for confirmation or replacement where the existing manual-model path permits
it. Models controls the models the team can use across writing, backup, and
hallucination checking, plus that provider's writing default. Backup and checker
assignments remain pinned when the writing default changes. Saving a policy
does not switch the active writing provider. Connection has one password API-key field; a saved key is indicated
without being returned to the browser, and pasting a new key replaces it on
Save. **Test connection** checks a fresh draft without promoting it. Save
reuses an unchanged tested draft; if no test was run, Save checks the connection
and can complete the revision automatically. A changed endpoint or key causes
the old draft to be durably discarded before Save.
Only one pending revision may exist for a saved provider. A concurrent attempt
returns a safe `409 conflict`; when the pending draft still exists, the response
includes its ID so the admin can resume or discard it. If it has already been
removed, reload the provider and retry. OpenScribe does not discard drafts
automatically.
System admins can review saved and pending config metadata through the scoped
`GET /api/v1/llm-configs/{config_id}?team_id=...` route. It returns the normal
config detail, including revision linkage and a boolean secret-presence flag;
it does not return Vault references or credentials.
The scoped `GET` and `PUT /api/v1/llm-configs/{config_id}/model-policy` routes
read and save enabled model IDs and the provider's writing default. They return
catalog membership, policy validity, and affected writing/backup/checker
warnings. Only system admins can change provider allowances and defaults; team
leaders can switch the writing-provider pointer within their own team.
Ollama uses no authentication when no key is configured. Removing an existing
Ollama bearer key requires the explicit **Remove saved key** action; a blank
field cannot silently remove it.

Credential replacement uses a versioned Vault path rather than overwriting the live path before database commit. Replacement, removal, draft cancellation, revision promotion, provider deletion, and team deletion commit a FK-free cleanup intent with the exact retired reference; a scheduled worker retries Vault deletion and first verifies the reference is no longer live.

## Team backup provider

System administrators can set or clear **Writing assistant backup** in the selected team's **Provider policy** table. Choose an active, ready provider from that team and one of its currently advertised, enabled models. Team leaders' primary-provider selections and users' primary-model preferences do not change this admin-owned setting.

Queued LLM work freezes the backup choice under the content owner's encryption boundary. Changing or clearing the setting affects new jobs. An unavailable backup does not prevent new primary work. A worker skips a backup that matches the primary provider and model.

The backup applies to note generation, follow-ups, Quick Actions, regeneration, template suggestions, consultation-split analysis and generation/recovery, and enabled hallucination checks and split verification. It does not enable an optional workflow that the team or owner has disabled.

After any provider response failure or output-validation failure, including provider-side rate-limit or quota rejection, the worker may submit one backup attempt using the same frozen redacted messages and output contract. It rebuilds the adapter envelope for the backup model and validates the backup response before accepting it. Local application quota exhaustion, lost dispatch claims, expired deadlines, and redaction, ownership, retention, cancellation, encrypted-storage or source-binding failures remain terminal.

For ordinary Template note generation (freeform and structured templates), malformed, ambiguous, or schema-invalid JSON classified as `llm_generation_invalid_json` permits one retry with the same primary provider and model. The retry uses the unchanged frozen redacted request, strict note validation, and no repair prompt. It receives a separate quota reservation and accounting; the latest attempt controls the current result and checkpoint. If the retry fails, the configured backup remains available once under the normal backup policy.

This primary retry applies only to ordinary Template notes. It does not apply to split analysis or generation/recovery, checkers, suggestions, follow-ups, Quick Actions, or plaintext workflows, and it is not repeated for provider errors or timeouts. If an ordinary Template note's backup returns invalid JSON, one separately reserved repair call sends its redacted output and the expected schema to that same frozen backup model. It requests formatting repair without changing clinical content or placeholders. The repaired output must pass the same validation and remains a draft for clinician review. Existing timeout, retention, lost-claim and quota guards remain in effect; repair is not repeated.

Each backup has a unique link to its primary quota attempt, its own token reservation, and a durable submission claim. Workers resolve credentials before submission. An explicit LLM timeout, or expiry of a submitted LLM attempt's durable deadline without a result, settles that attempt with zero token units. Its failure and transport metadata remain available for operational monitoring. A successful backup, primary, or checker attempt is accounted independently from any timed-out attempt. Other submitted failures with unknown usage remain conservatively charged; deletion before the attempt deadline does not imply a timeout. Duplicate task delivery does not replay a submitted call.

Provider edits and deletion protect queued primary and backup references. Credential correction must preserve the endpoint, authentication mode and models frozen for live backup work. Deleting a backup config clears its paired encrypted backup metadata from terminal sources before the config is removed. A later retry of that source can use its frozen primary request without consulting a newer backup selection. The server reads only dedicated provider metadata to check live backup models; admin authority does not grant access to clinical source content.

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
- Gemini Enterprise
- Custom OpenAI-compatible - advanced

`provider_preset` records the admin-facing brand or custom classification. `adapter_kind` remains the runtime protocol implementation:

- OpenAI-compatible presets use `openai_chat`
- Ollama uses `ollama_chat`
- Bedrock HTTP gateway uses `bedrock_chat`
- Gemini Enterprise uses `gemini_enterprise`

`openai_chat` currently means the OpenAI-compatible chat protocol adapter, not only first-party OpenAI. A future migration may rename the enum, but this slice keeps the existing value for compatibility.

If a branded OpenAI-compatible preset is saved with a changed base URL, OpenScribe stores it as `custom_openai_compatible`. Bedrock remains the existing HTTP gateway path; native AWS Bedrock Converse/IAM is not part of this slice.

For Bedrock HTTP gateway, use the region selector for standard `bedrock-mantle.<region>.api.aws` endpoints. The add and saved-connection editors use the same region choices. On Add, the server derives the standard base URL from the selected region; the saved-connection editor updates a read-only endpoint preview automatically when the region changes. A previously saved region outside the current choices remains available when editing. The server treats an explicit Bedrock region as the source of truth even if the browser submits a stale base URL. A non-Mantle custom Bedrock URL is saved as Custom OpenAI-compatible so runtime adapter semantics stay explicit.

## Model Discovery

Model lists come from live discovery. OpenScribe no longer supplies built-in LLM model fallback lists for failed discovery.

- Successful discovery stores provider-returned models in `available_models_json`.
- The admin wizard first creates a pending draft after `Check API key and find models`; the API key is not returned to the browser or carried in hidden inputs after this step.
- Provider credential rejection (`401`/`403` or SDK authentication errors) returns `llm_invalid_credential`, does not create a draft config, and does not write a Vault secret. This remains true even when a manual model name is supplied. Browser setup stays on the Provider/API key step with the API-key rejection message.
- Successful discovery that returns zero compatible chat models is treated as `manual_required`, not `fetched`, so admins must enter an explicit model name before save.
- During initial setup, when successful discovery returns models, the setup `model_name` must be one of those discovered models. If no model is submitted, OpenScribe uses the first discovered model. This value bootstraps setup; after finalization, the per-provider model policy is authoritative for enabled models and the writing default.
- Failed non-auth discovery returns `manual_required` and allows a system admin to enter a model name manually. The browser warns that generation may fail if the endpoint, key, or model name is wrong. When saved, that manual model is stored as the only selectable model so team selection and user preference validation keep working.
- The canonical admin workspace and its server-rendered completion fallback both expose manual model entry when discovery returns no models. Pending setup also permits API-key replacement without ever rendering the saved credential.
- Save without a model name remains invalid.
- Editing a saved provider's preset, adapter, or base URL while keeping the existing Vault-backed secret triggers fresh model discovery with that saved secret. Discovery updates the catalog only when it completes successfully, including a successful empty result; a failed or inconclusive check does not rewrite the saved model policy.
- OpenAI-specific model prefix filtering applies only to the OpenAI preset. Other OpenAI-compatible providers keep valid non-OpenAI model IDs after removing non-chat categories such as embeddings, transcription, TTS, moderation, and image models.
- Mistral and Together AI discovery uses provider metadata before accepting models. Mistral keeps non-archived records with `capabilities.completion_chat=true`; Together keeps `chat`, `language`, and `code` model types.
- `inspection_metadata_json` is service-owned operational metadata. Public save requests cannot set or forge discovery status, warnings, notes, or provider display data.
- Discovery metadata includes `inspected_at` so stale provider inspection state is visible without exposing secrets or content.
- Team LLM selection responses include the selected provider preset and display name so leader/user UI can show the provider brand without exposing credentials.
- Saved-provider re-inspection persists the latest discovery metadata even when discovery fails. A completed discovery replaces the catalog, while a failed or inconclusive check leaves it unchanged; the saved enabled IDs, writing default, and pinned role assignments are never silently rewritten by refresh.
- During initial setup or finalization after a connection change, the setup `model_name` is checked against a non-empty discovered catalog. This setup check does not replace the saved provider policy used after setup.

Gemini discovery/finalization differs from API-key providers and is described below.

OpenAI-compatible, Mistral, and Together model discovery share a bounded JSON transport in `app/services/llm.py`. It retains provider URL validation, a 10-second timeout, and a 1 MiB response limit; each preset keeps its own model filtering and public error messages.

## Secrets

Bearer tokens remain Vault-backed. API responses expose `has_secret` only and never return `vault_secret_ref` or raw secret material.

Saved credentials may be kept only while the target authentication type matches the config's existing authentication mode. Switching between bearer authentication and Gemini service-account authentication requires credential replacement; the service rejects the edit before reading Vault or contacting the provider. Switching Gemini to Application Default Credentials uses credential removal and durably retires the old Vault reference through the established cleanup path.

Required-token presets must have an existing or replacement Vault-backed token. Ollama can be saved without a token for local deployments.

When OpenScribe runs in Docker, an Ollama Base URL on `localhost` points to the OpenScribe container, not the Docker host. Use a host address that the container can reach, such as the host machine's LAN IP. Bare-metal OpenScribe installs can continue to use `http://localhost:11434`.

Blank revision credentials are inherited only by required-token presets. The draft reads the target token for inspection, then immediately writes it to a unique versioned Vault path owned by the draft config; it never persists the target reference. This lets root credential replacement and retired-reference cleanup proceed without breaking a pending revision. Revising a credentialed provider to Ollama with no submitted token creates a no-auth draft and retires the old Vault reference only after promotion commits. Supplying an optional Ollama token keeps bearer authentication. Runtime and saved inspection read a Vault reference only when the config's `auth_mode` is `bearer`.

Refreshing a ready provider updates its discovered catalog only. Identical IDs keep their saved allowance; new IDs remain disabled. Removed IDs and defaults stay in the saved policy and role assignments so admins can see and repair them. A refresh never saves a policy or silently substitutes writing, backup, or checker models. The provider table and Provider policy show affected-role warnings. The last explicit connection check is stored separately as a timestamp, passed/failed state, and allowlisted reason; rendering the admin page does not call a provider.

Replacing a saved key reruns discovery and marks the config `pending_model_selection`. The setup `model_name` may need confirmation before finalization; the saved per-provider allowance and writing default remain intact, including IDs absent from the refreshed catalog, so an admin can repair the policy without an automatic substitution.

Queued or processing dependent LLM work—generated documents, template suggestions, or consultation-split executions—blocks normal provider edits and provider deletion so runtime snapshots stay stable. Credential correction is the exception: a system admin may replace the Vault-backed key for the same provider endpoint while generated documents are queued or processing, because an invalid key can otherwise leave failed or stuck generation work blocking the fix. During in-flight work, the replacement is rejected unless live inspection confirms that the corrected credential exposes the saved default model used by those queued documents. This prevents swapping to a different account/provider that cannot run the snapshotted model. If the corrected key still exposes the saved default model, the provider remains `ready` and keeps its current availability instead of being moved back to incomplete setup. If the full edit form submits incidental label/model/availability changes during this correction, OpenScribe keeps those existing provider fields unchanged and updates only the credential/discovery metadata. A provider already left in `pending_model_selection` by an earlier credential correction may be finalized while generated documents remain queued or processing. A ready provider may also be toggled between available/unavailable during queued or processing generations when label, endpoint, and model stay unchanged, except while migration-era queued checker-capable work still has no frozen checker snapshot: availability or checker-relevant connection changes to its selected provider are then held until that work drains. New accepted work freezes its checker choice, so it does not impose this temporary edit guard.

Queued generation resolves its Vault-backed credential before the provider attempt is marked submitted. A definite credential-read failure therefore fails the generated document and cancels its reservation without quota usage; only work that reaches the provider-dispatch boundary may consume quota. If duplicate workers both observe queued work, a preflight failure from the worker that loses the atomic dispatch claim cannot fail or settle the winner's already submitted attempt.

## Generated Document Request Payloads

Generation uses a monotonic execution budget and a scoped Unix main-thread wall timer. Maintained workers use Celery prefork. Thread, greenlet, eventlet and gevent execution is unsupported. Each queued generation, checker, suggestion and split workflow checks worker capability before marking its provider attempt submitted; a definite unsupported context cancels reserved quota without an HTTP event. An explicit LLM timeout and expiry of a submitted LLM attempt's durable deadline settle that attempt at zero token units. The same timeout contributes no token or estimated-cost values to usage reports, while failure counts and content-free transport telemetry remain available. Other submitted failures with unknown usage remain conservative; STT settlement is unchanged. No helper thread continues a clinical request after timeout. Client cleanup restores the previous signal handler and timer. Connection setup is limited to 10 seconds; ordinary and split generation calls and split analysis have a 90-second budget. Suggestions have 90 seconds. Each ordinary generation attempt and its checking share a 150-second deadline from that generation attempt's submission. A configured backup gets a separate bounded attempt. Checker and split-verification calls have at most 30 seconds per provider. Queue waiting is separate.

SDK retries are disabled. The shared transport retries a temporary HTTP 429 for the same frozen request and credential, with at most four HTTP requests under one logical quota attempt. Equal-jitter waits are 1–2, 2–4 and 4–8 seconds; a valid Retry-After can increase the wait within a 30-second cumulative limit and the remaining stage budget. Gemini generic 429 responses are treated as temporary unless structured details identify hard quota or billing exhaustion. The transport does not replay hard-quota rejection, timeout, ambiguous connection failure, HTTP 503 or invalid output against that same request. A distinct configured [team backup](#team-backup-provider) can handle eligible failures after this transport policy finishes. Durable lost claims or expired transcript roots stop further requests.

The ordinary note JSON retry above is a generation-level second attempt, with its own reservation and claim. It is separate from transport retries: invalid output is never replayed inside the same provider attempt.

An enabled Gemini Priority policy replaces the normal temporary-capacity retry
sequence with one ordinary request followed by at most one Priority request.
The first temporary-capacity 429 triggers escalation after the bounded wait;
a failed Priority request returns to existing backup handling. Both requests
share the same submitted attempt, quota reservation and deadline. Every new
logical request starts with its saved ordinary capacity mode.

Provider attempts retain at most four content-free transport events: UTC observation time, request ordinal, duration, controlled code/status, numeric retry wait and optional allowlisted requested/served tiers. Auto-mode baseline requests do not claim an explicitly requested Standard tier; served tier is recorded only from recognised provider metadata. Observations commit independently before waiting, including a final timeout observed after the attempt deadline. They contain no provider payload, header body, prompt, clinical content or credentials.

Template generation, follow-ups, Quick Actions, regeneration, template suggestions, and hallucination checks use the shared generation dispatcher in `app/services/templates.py`. Callers supply the selected request and provider settings and retain responsibility for credential resolution and attempt submission. Generated-document failures after submission share terminal persistence and quota settlement; pre-submit cancellation remains separate.

New generated documents store an encrypted snapshot of the outbound LLM request on `generated_documents.llm_request_payload_json_encrypted`.

- While a template note is queued, the field may temporarily hold saved note-generation options so a later preference edit cannot change that queued job.
- When the worker starts, the snapshot is replaced with the exact request body/messages sent to the provider adapter, including redacted transcript/dictation text and redacted template or quick-action prompt where applicable.
- Raw provider secrets are not stored in the snapshot.
- The generated-document detail API decrypts and returns `llm_request_payload_json` only through existing generated-document owner access paths.
- Older generated documents may have `llm_request_payload_json=null`; the transcript UI shows `LLM request not available for this document.`

Template note output caps are adapter-specific:

- OpenAI-compatible providers and the Bedrock HTTP gateway receive `max_completion_tokens`.
- Ollama `/api/chat` receives `options.num_predict`.
- Saved length selections map to `short=800`, `normal=1600`, and `long=3200`; absent preferences use `normal`. For Gemini, the selection remains saved and snapshotted as preference metadata, but currently neither adds a semantic prompt guard nor lowers the fixed `max_output_tokens=30000` provider ceiling.
- Length/detail presets apply only to template note generation. Follow-ups and quick actions keep their existing request shape.

Generation completion is checked before note parsing. OpenAI-compatible responses with `finish_reason=length` fail as `llm_generation_truncated`; other explicit non-`stop` finish reasons fail as incomplete provider responses. A missing finish reason remains accepted for compatibility when the response contains usable text. Ollama must provide a terminal `/api/chat` frame with `done: true`; `done_reason=length` is truncation, and other explicit non-`stop` reasons are incomplete. A missing Ollama `done_reason` remains accepted on a terminal frame for compatibility. An absent terminal frame fails closed. Arbitrary provider reason text is never copied into error metadata; known classifications use fixed application metadata.

OpenAI-compatible and Gemini SDK responses are bounded as HTTPX reads the response, with an 8 MiB ceiling per response. The response hook stops consuming an oversized body and returns `llm_provider_bad_response` with `provider_error_code=response_too_large`; it does not rely on a post-read size check. Requests ask for identity encoding, and encoded responses are rejected before SDK parsing to prevent decompression from bypassing the byte limit. The SDK's normal HTTPX proxy and TLS configuration remains in use. Gemini HTTP 400 responses are classified as `llm_provider_bad_request` unless a structured Google reason identifies an unavailable model or location. Provider message text is never used for this classification.

These completion checks apply to Template notes, follow-ups, Quick Actions, and checker calls. An incomplete submitted call is terminalized with the existing conservative attempt settlement; duplicate task delivery does not replay it.

Ordinary Template JSON output, checker JSON, and bundled split-verification JSON reject duplicate object keys at any depth before content or patches are accepted. Existing balanced-object and fenced-JSON compatibility remains in place where the ordinary Template parser supports it.

Operational generation events through `openscribe.usage` include controlled stage (`queue` or `generation`) and generator (`template`, `followup`, `quick_action`) categories. Default log messages include IDs, status and a bounded error category; structured fields retain safe error codes and usage metadata. The checker emits its own events with `stage=checker`; see [hallucination-check-design.md](hallucination-check-design.md). Task-boundary failures use content-free errors before Celery handles them.

Template suggestions retain their nominal 220-token output cap; Gemini uses the shared 30,000-token ceiling. Suggestion queue reservations account for the actual adapter output cap, and dispatch expands the reservation when the final redacted input requires more units. A failed expansion cancels only still-reserved work; it cannot cancel a concurrent submitted attempt. Split generation, targeted recovery and bundled verification also reserve from their exact adapter requests. See the [stage review](reviews/generation-stage-cost-logging-review-2026-10-16.md) for remaining cap and efficiency decisions.

## Labels

LLM config labels are unique per team after trimming surrounding whitespace and comparing case-insensitively. Draft creation, finalization, and legacy save paths all return `409 conflict` with `An LLM provider with this name already exists for this team.` before saving duplicates.

When a system admin supplies a label during draft creation, OpenScribe preserves it. If no label is supplied, the backend generates the default provider/team label.

## Gemini Enterprise

Gemini Enterprise is a native `gemini_enterprise` adapter using the stable `v1` Google Gen AI SDK. It does not use an editable base URL or an API key. Admins must provide a Google Cloud project, explicit location, capacity mode, and one credential source:

- Application Default Credentials (recommended): uses the application's runtime identity and stores no Vault secret.
- Service-account JSON (advanced): accepts only a bounded `service_account` JSON file, validates it before draft creation, and stores it in Vault as a typed secret. It is never rendered or logged.

Workload Identity Federation belongs in deployment configuration and is consumed through ADC; the provider API rejects uploaded `external_account` configuration. Global locations may process data globally, so deployments with residency requirements must select an appropriate jurisdictional or regional location.

Model discovery uses Google's `v1beta1` publisher-model catalog and filters it to Gemini text-generation model IDs. Because regional catalogs can lag jurisdictional catalogs, `europe-*` results are merged with `eu`, and US/North America regional results with `us`. PublisherModel action metadata is not used because `google-genai` does not expose it consistently. Empty or transiently unavailable discovery permits manual model entry, which is checked with a fixed synthetic stable-`v1` `count_tokens` request before finalization. Definitive credential, IAM, disabled-API, and location failures create neither draft nor secret.

Queued jobs snapshot project, location, API version, capacity mode, and the team's `priority_on_capacity_429` policy. The policy defaults off and is controlled by system administrators in team Gemini settings. When enabled, one temporary capacity 429 may trigger a Priority PayGo request before existing backup handling. New generations start with the configured usual tier; dedicated capacity mode is incompatible with this fallback. The Priority service tier can cost more, and Google may report a served tier different from the requested tier. Credentials, Vault references, private-key identifiers, and access tokens are excluded. Gemini request snapshots use `contents` plus `system_instruction`; JSON-producing requests use the canonical `response_json_schema` config key. Snapshot replay still accepts the earlier `response_schema` key for compatibility. Token usage maps into existing accounting fields. Template notes, structured notes, follow-ups, quick actions, and hallucination checks share this path.

Gemini note generation applies a model-aware low-thinking policy. The exact Gemini 3.7 Flash, Gemini 3.8 Flash, and Gemini 3.8 Flash Cyber model IDs use `LOW`, because those models support `LOW`, `MEDIUM`, and `HIGH` rather than `MINIMAL`; the existing Gemini 3.5 Flash policy remains `MINIMAL`. Every Gemini generation request uses a fixed `max_output_tokens=30000` provider ceiling so hidden reasoning and the response share enough output capacity. The user's short/normal/long choice remains saved and snapshotted as preference metadata, but does not yet add a semantic prompt guard or lower the Gemini provider cap. Quota reservation uses the same 30,000-token request ceiling and unused units are released during settlement. JSON-producing template and hallucination-check requests carry an explicit JSON Schema in the encrypted request snapshot and provider request; the existing backend parser remains authoritative. Follow-ups and quick actions use plain-text mode. A provider `MAX_TOKENS` finish is reported as `llm_generation_truncated` instead of being misclassified as malformed JSON.

Set `ENABLE_GEMINI_ENTERPRISE_PROVIDER=false` to hide and reject new Gemini Enterprise setup during rollout. Default is enabled. Existing persisted Gemini configs remain visible to authorized admins, usable by existing team selections, and re-inspectable with their saved Vault-backed credential; disabling creation must not make persisted provider rows unreadable or return server errors.

See [Gemini Enterprise setup](gemini-enterprise-setup.md) for bare-metal and Docker deployment instructions. Publisher-model discovery uses Google's `v1beta1` Model Garden catalog; token counting and generation use stable `v1`. Discovery failure still permits manual selection, with `count_tokens` providing the authoritative access check during finalization.


## Owner generation trace

System administrators can enable optional capture per user. Future supported provider attempts can record redacted request snapshots, model text and bounded transport/decision metadata under the owning user's encryption key. Capture is best effort; incomplete events and unavailable history must not be interpreted as evidence that no provider request occurred. Encrypted content expires at the earlier of 24 hours or transcript expiry, and the transcript expiry sweep removes expired trace rows. Disabling capture stops new capture; owners can clear existing rows. Provider-attempt, quota, outbox, usage and audit records remain content-free. Existing generated-document owner views provide the final editor content. The Gemini snapshot represents SDK request arguments, not literal wire bytes.

The generation-trace migration adds a default-off user flag and transcript-cascaded trace table. Downgrading it deletes all stored traces and the capture flags; export is not provided. The scheduled transcript expiry sweep also removes expired trace rows even when no transcript root expires in that run.
