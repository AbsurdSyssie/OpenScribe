# API Behavior

Canonical JSON API routes are versioned under `/api/v1`.

Browser navigation behavior:

- invalid non-API browser routes now redirect by session state:
  - unauthenticated users -> `/login`
  - authenticated users -> `/home`
- invalid `/api/*` routes still return JSON `404` responses and are not redirected

## Implemented endpoint groups

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/totp`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/password-reset/request`
  - returns generic success only when outbound mail is enabled
  - returns `503 mail_transport_disabled` when email reset is not configured, so clients can tell users to contact a team leader or system administrator
- `POST /api/v1/auth/password-reset/confirm`
- `POST /api/v1/auth/account-activation/confirm`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`

### Public account requests

- `POST /api/v1/account-requests`

### Manager review

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`

### Onboarding

- `POST /api/v1/onboarding/password`
- `POST /api/v1/onboarding/totp/start`
- `POST /api/v1/onboarding/totp/verify`
- `POST /api/v1/onboarding/recovery-codes`
- `POST /api/v1/onboarding/skip-recovery-codes`

### Team management

- `POST /api/v1/teams`
- `GET /api/v1/teams`

### User management

- `POST /api/v1/users`
- `GET /api/v1/users`
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `POST /api/v1/users/{user_id}/send-activation`
- `POST /api/v1/users/{user_id}/send-password-reset`
- `POST /api/v1/users/{user_id}/break-glass-password-reset` returns a one-time visible expiring `temporary_password` when break-glass policy allows it
- `POST /api/v1/users/{user_id}/reset-mfa`
- `POST /api/v1/users/{user_id}/send-account-recovery`
- `POST /api/v1/users/{user_id}/break-glass-account-recovery` returns a one-time visible expiring `temporary_password` and resets MFA when break-glass policy allows it
- `POST /api/v1/users/{user_id}/recover-password` and `POST /api/v1/users/{user_id}/recover-account` are deprecated and return `410`
- `DELETE /api/v1/users/{user_id}`

### Transcripts

- `POST /api/v1/transcripts`
- `POST /api/v1/transcripts/start`
- `PATCH /api/v1/transcripts/{transcript_id}`
- `DELETE /api/v1/transcripts/{transcript_id}`
- `GET /api/v1/transcripts/{transcript_id}`
- `POST /api/v1/transcripts/{transcript_id}/commit`
- `POST /api/v1/transcripts/{transcript_id}/finalize-live-capture`
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- `POST /api/v1/transcripts/{transcript_id}/audio-file`
- `POST /api/v1/transcripts/{transcript_id}/retry-audio-file`
- `POST /api/v1/transcripts/{transcript_id}/manual-pii`
- `DELETE /api/v1/transcripts/{transcript_id}/manual-pii/{entity_id}`
- `GET /api/v1/transcripts/{transcript_id}/post-consultation-dictation`
- `PATCH /api/v1/transcripts/{transcript_id}/post-consultation-dictation`
- `POST /api/v1/transcripts/{transcript_id}/post-consultation-dictation/preview-audio-file`
- `POST /api/v1/transcripts/{transcript_id}/post-consultation-dictation/audio-file`
- `POST /api/v1/transcripts/{transcript_id}/quick-action-context/preview-audio-file`
- `GET /api/v1/transcripts/{transcript_id}/working-note`
- `PATCH /api/v1/transcripts/{transcript_id}/working-note`
- `DELETE /api/v1/transcripts/{transcript_id}/working-note`
- `GET /api/v1/transcripts/{transcript_id}/generated-documents`
- `PATCH /api/v1/generated-documents/{generated_document_id}`
- `GET /api/v1/generated-documents/{generated_document_id}/redaction-debug`
- `POST /api/v1/transcripts/{transcript_id}/generate-output`
- `GET /api/v1/users/{user_id}/transcripts`
- whole-file upload rejects oversize payloads with:
  - status `413`
  - code `payload_too_large`
  - message `Audio file exceeds the current maximum upload size`

### Templates

- `GET /api/v1/templates/available`
- `GET /api/v1/templates/team`
- `POST /api/v1/templates/team`
- `DELETE /api/v1/templates/team/{template_id}`
- `GET /api/v1/templates/personal`
- `POST /api/v1/templates/personal`
- `DELETE /api/v1/templates/personal/{template_id}`

### Smart Phrases

- `GET /api/v1/smart-phrases/available`
- `GET /api/v1/smart-phrases/personal`
- `POST /api/v1/smart-phrases/personal`
- `PATCH /api/v1/smart-phrases/personal/{smart_phrase_id}`
- `DELETE /api/v1/smart-phrases/personal/{smart_phrase_id}`
- `POST /api/v1/smart-phrases/personal/{smart_phrase_id}/used`

### Team transcription configuration

- `GET /api/v1/stt-configs`
- `GET /api/v1/stt-configs/{config_id}`
- `POST /api/v1/stt-configs/inspect`
- `POST /api/v1/stt-configs/{config_id}/inspect`
- `POST /api/v1/stt-configs/drafts`
- `POST /api/v1/stt-configs/{config_id}/finalize`
- `POST /api/v1/stt-configs/{config_id}/replace-credential`
- `POST /api/v1/stt-configs`
- `DELETE /api/v1/stt-configs/{config_id}`
- `GET /api/v1/stt-selection`
- `GET /api/v1/stt-selection/options`
- `POST /api/v1/stt-selection`
- `DELETE /api/v1/stt-selection`
- `GET /api/v1/stt-selection` now accepts optional `purpose` query param:
  - `conversation` default
  - `post_consultation_dictation`
- `DELETE /api/v1/stt-selection` now accepts same optional `purpose` query param
- `POST /api/v1/stt-selection` now accepts `purpose` in JSON body with same values
- these are metadata and secret-reference routes, not transcript-content routes
- inspect validates/dereferences OpenAPI documents, then proposes `transcribe_path`, `file_field_name`, `model_field_name`, `language_field_name`, `response_text_path`, optional segment fields, and extra form defaults; save persists those fields for runtime use
- runtime response parsing supports configured segment paths/field names and JSONPath response extraction through `jsonpath-ng`; queued ingestion snapshots persist the segment mapping used when the job was queued
- STT config responses include credential `credential_status` and sanitized `inspection_metadata_json`, but never `vault_secret_ref` or raw bearer token
- STT draft finalization and draft credential replacement take `config_id` from the path; JSON bodies include team/label/model or replacement token fields only and do not require a duplicate body `config_id`
- STT create/update accepts explicit `credential_action: keep | replace | remove`; a supplied `bearer_token` is treated as `replace` for backward compatibility
- blank `bearer_token` on edit keeps the saved credential only when `credential_action` is `keep`; `remove` clears credential-derived state and deletes the saved Vault secret
- create/update with a bearer token computes a server-side credential fingerprint and warns with `409 provider_credential_duplicate_warning` before any Vault write or provider inspection when same team, adapter, endpoint, and credential already exist; callers may retry with `confirm_duplicate: true`
- create/update with a bearer token validates/inspects server-side before replacing a saved STT secret, records `verified` or `partial`, and rejects invalid credentials without deleting an existing config or selection
- manual `generic_rest` and `openai_compatible_rest` save-time validation uses the saved transcribe path, field names, response path, and bundled synthetic audio sample instead of depending on default OpenAPI discovery or static metadata only
- saved-provider re-inspection uses `POST /api/v1/stt-configs/{config_id}/inspect` and the saved Vault reference; credential rejection marks the provider `invalid` and clears active STT selections using it
- old clients that omit STT model/language field names keep `model` and `language` defaults when values are present
- bearer tokens supplied to standalone inspect are never returned or preserved in hidden browser fields; save-and-inspect tokens are written to Vault and never returned

### Team LLM configuration

- `GET /api/v1/llm-configs`
- `POST /api/v1/llm-configs/inspect`
- `POST /api/v1/llm-configs/{config_id}/inspect`
- `POST /api/v1/llm-configs/drafts`
- `POST /api/v1/llm-configs/{config_id}/finalize`
- `POST /api/v1/llm-configs/{config_id}/replace-credential`
- `POST /api/v1/llm-configs`
- `DELETE /api/v1/llm-configs/{config_id}`
- `GET /api/v1/llm-selection`
- `GET /api/v1/llm-selection/options`
- `POST /api/v1/llm-selection`
- `DELETE /api/v1/llm-selection`
- `GET /api/v1/llm-preference`
- `POST /api/v1/llm-preference`
- `DELETE /api/v1/llm-preference`
- `GET /api/v1/app-preferences`
- `POST /api/v1/app-preferences`
- `DELETE /api/v1/app-preferences`
- these are metadata and secret-reference routes, not transcript-content routes
- LLM inspect accepts branded `provider_preset` values and returns `provider_preset`, `provider_display_name`, `discovery_status`, `default_model_source`, `requires_bearer_token`, `supports_model_discovery`, and `warnings` so clients can distinguish fetched, manual-required, and failed discovery states
- LLM inspect remains scoped to known protocol adapter families (`openai_chat`, `bedrock_chat`, `ollama_chat`); it does not save or activate a provider
- LLM draft creation is system-admin-only; it saves the submitted credential to Vault, stores discovered model metadata, returns `has_secret=true`, and never returns raw keys or Vault refs
- LLM draft finalization sets `setup_status=ready`, stores the chosen default model, and applies the `is_active` availability toggle without changing the team's active LLM selection
- LLM credential replacement reruns discovery, clears availability, and returns the config to `pending_model_selection`
- saved LLM provider inspect uses the existing Vault-backed credential when present, refreshes sanitized available-model metadata, and never returns the raw key
- LLM create/update accepts explicit `credential_action: keep | replace | remove`; `remove` is allowed for optional-token local adapters such as Ollama, while OpenAI and Bedrock configs require either a replacement bearer token or an existing saved bearer token when `credential_action` is `keep`
- LLM `credential_action=remove` deletes the Vault secret before clearing the DB reference; Vault delete failure aborts the request with the saved DB reference intact, stale/missing Vault content can still be cleared, and DB commit failure triggers best-effort Vault secret restoration when the old token was readable
- persisted credential status/fingerprint metadata is STT-only in this slice; LLM stores last inspection metadata in `inspection_metadata_json`
- LLM selection options and selection writes require `setup_status=ready`, `is_active=true`, and a non-empty default `model_name`; pending provider drafts are hidden from leaders/users and rejected by ID

### Shared NLP endpoint configuration

- `GET /api/v1/deidentification-providers`
- `POST /api/v1/deidentification-providers`
- `POST /api/v1/deidentification-providers/inspect`
- `DELETE /api/v1/deidentification-providers/{provider_id}`
- `GET /api/v1/deidentification-provider-assignments`
- `POST /api/v1/deidentification-provider-assignments`
- `DELETE /api/v1/deidentification-provider-assignments`
- `GET /api/v1/deidentification-selection`
- `GET /api/v1/deidentification-selection/options`
- `POST /api/v1/deidentification-selection`
- `DELETE /api/v1/deidentification-selection`
- `GET /api/v1/clinical-nlp-selection`
- `GET /api/v1/clinical-nlp-selection/options`
- `POST /api/v1/clinical-nlp-selection`
- `DELETE /api/v1/clinical-nlp-selection`
- these routes keep the historical `deidentification` API name, but the saved generic REST endpoint can be used for PII redaction, clinical entity extraction, or both
- built-in native provider remains selectable for every team as the PII redaction fallback
- clinical entity extraction is separate from PII redaction: admins mark endpoints as clinical NLP-capable and assign them to a team; team leaders enable one assigned clinical NLP endpoint through the clinical selection routes
- clinical NLP has no built-in fallback; no clinical selection means disease/symptom extraction is off
- external endpoints require explicit admin assignment before team selection
- inspect can load `/docs`, `/redoc`, or OpenAPI JSON paths to infer detect path, request text/language fields, extra body defaults, and response entity fields before saving
- inspect separates `openapi_path` (docs/schema discovery, not saved for runtime) from `detect_path` (selected POST endpoint saved and used for runtime redaction)
- after docs discovery, callers may pass `openapi_path` plus a selected `detect_path` from `candidate_paths` to infer and ping that specific endpoint contract
- inspect pings against a concrete or inferred detect path use caller-supplied synthetic sample text only and return parsed entity spans plus the raw provider JSON response for admin testing
- raw inspect responses are for synthetic provider tests only; runtime redaction does not expose provider responses or transcript-derived content in admin routes
- runtime generic REST parsing accepts either offset entities (`start`, `end`, label/type, optional score/confidence) or value-only entities with detected text plus label; value-only entities are matched back into the submitted source text to derive offsets
- inspect can adjust response field settings from a successful synthetic ping when OpenAPI schemas say `entity_type`/`score` but the actual response uses common clinical-NLP fields such as `label`/`confidence`
- the same saved provider contract can opt into disease/symptom detection for transcript snapshots
- when clinical detection is enabled, remote/public endpoints receive the redacted transcript text from the linked redaction run; unredacted transcript text is sent only when the admin enabled that option and the endpoint host is localhost, private, link-local, or unspecified
- these are metadata and secret-reference routes, not transcript-content routes

## Error envelope

All non-2xx JSON responses use:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {
      "issues": []
    }
  }
}
```

Rate-limited requests return the same envelope with:

- status `429`
- code `rate_limited`
- message `Too many requests`

## Current auth and authorization rules

### Authentication

- protected JSON routes require a valid opaque session cookie
- unauthenticated access returns `401 unauthorized`
- invalid login credentials return the same `401 unauthorized` response shape
- the explicit session-public API route allowlist is currently:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/logout`
  - `POST /api/v1/account-requests`
- completed MFA-enabled users may receive `auth_level = pending_mfa` after password success
- login is rate-limited at `5 per 5 minutes` per client IP
- whole-file transcript uploads are rate-limited at:
  - `1 per 5 seconds`
  - `100 per day`
- whole-file upload throttling is shared across:
  - `POST /api/v1/transcripts/{transcript_id}/audio-file`
  - `POST /transcribe/upload`
- whole-file upload throttling keys to the authenticated user when a valid session resolves, with hashed-session/IP fallback only when user resolution is unavailable
- whole-file uploads are also capped by:
  - raw upload size: `200 MB`
  - normalized whole-file duration: `4 hours`

### Pending-MFA sessions

- accounts with completed onboarding and active TOTP may still require a second step after password login
- the resulting session has `auth_level = pending_mfa`
- pending-MFA sessions may use only:
  - `auth/me`
  - `auth/mfa/totp`
  - `auth/logout`
  - `auth/trusted-device`
- normal JSON routes return:
  - `403`
  - code `mfa_required`

### Trusted-device freshness

- trusted-device cookies only influence post-password MFA skipping
- they do not replace password login
- a fresh trusted device currently means:
  - same browser still holds the opaque trusted-device cookie
  - the server-side record is not revoked or expired
  - the last MFA verification was within 24 hours

### Public account requests

- account-request submission is rate-limited at `3 per hour` per client IP

### MFA challenge

- TOTP challenge submission is rate-limited at `10 per 10 minutes` per client IP

### Onboarding-only sessions

- accounts with incomplete onboarding authenticate successfully
- the resulting session has `auth_level = onboarding`
- onboarding-only sessions may use only onboarding routes, `auth/me`, and logout
- normal JSON routes return:
  - `403`
  - code `onboarding_incomplete`

## Route auth audit

- run `./.venv/bin/python scripts/audit_api_auth.py` to probe every `/api/v1` route with:
  - no session cookie
  - an invalid session cookie
  - onboarding, pending-MFA, normal-user, and leader sessions where denial is expected
- the script exits non-zero if:
  - a protected route does not deny the expected scenario
  - a new `/api/v1` route exists without an audit manifest entry

### Manager routes

These routes require a full authenticated manager session:

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`
- `POST /api/v1/users`
- `GET /api/v1/users`
- `POST /api/v1/users/{user_id}/send-activation`
- `POST /api/v1/users/{user_id}/send-password-reset`
- `POST /api/v1/users/{user_id}/send-account-recovery`
- `POST /api/v1/users/{user_id}/break-glass-password-reset`
- `POST /api/v1/users/{user_id}/break-glass-account-recovery`
- `POST /api/v1/users/{user_id}/reset-mfa`
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `DELETE /api/v1/users/{user_id}`
- `GET /api/v1/stt-selection`
- `GET /api/v1/stt-selection/options`
- `POST /api/v1/stt-selection`
- `DELETE /api/v1/stt-selection`

Managers are:

- system admins
- team leaders

Leader scope is restricted to their own team.

Current account-administration behavior:

- leaders may suspend and reactivate non-system-admin users in their own team only
- leaders may delete non-system-admin users in their own team only
- system admins may suspend and reactivate other users across teams
- system admins may delete other users across teams
- no manager may suspend their own account through these routes
- no manager may delete their own account through these routes
- suspended users cannot log in
- reactivated users are forced back into password-change onboarding and must re-establish MFA setup
- delete is immediate hard delete and returns `204`
- deleting a user removes currently implemented user-owned transcript data immediately
- email recovery routes require configured mail transport; break-glass routes require manager TOTP, reason, explicit email-unavailable confirmation, and write metadata-only security audit rows

Current STT-configuration behavior:

- system admins provision STT endpoint rows and Vault-backed secrets per team
- system admins may list, inspect, create, update, and delete provisioned STT configs, but must supply `team_id`
- leaders may not provision, rotate, or delete STT credentials
- leaders may read only their own team's selectable provisioned endpoints through the selection routes
- leaders may set or clear only their own team's active STT selection
- normal users may not access provisioning or selection routes
- onboarding-only and pending-MFA sessions may not access provisioning or selection routes
- `generic_rest` inspection fetches `base_url + openapi_path` and returns inferred fields without saving
- `openai_cloud` inspection uses the official OpenAI SDK server-side to return built-in contract defaults plus a filtered `available_models` list
- if OpenAI model discovery fails, `openai_cloud` inspection falls back to a built-in transcription-model allowlist and still returns `200`
- `openai_cloud` inspection also returns labeled model-option metadata so the UI can show whether each choice was `fetched` live or supplied from the built-in `default` list
- `openai_compatible_rest` inspection returns built-in known-contract defaults without OpenAPI fetch
- inspection also returns documented field descriptions and required flags when the provider's OpenAPI schema exposes them
- the admin HTML inspect flow discards the entered token after the request; saving a newly inspected credential requires token re-entry, while saved-provider re-inspection uses the Vault reference server-side
- saved STT config now carries an explicit `adapter_kind`
- currently supported adapter families are `generic_rest`, `openai_cloud`, and `openai_compatible_rest`
- the API never returns the bearer token
- the API currently returns metadata plus `has_secret`, not the raw Vault secret reference
- one team may have multiple provisioned STT config rows
- one team may have only one active STT selection row

Current LLM-configuration behavior:

- system admins provision LLM provider rows and Vault-backed secrets per team
- system admins may list, inspect, create, update, and delete provisioned LLM configs, but must supply `team_id`
- leaders may not provision, rotate, or delete LLM credentials
- leaders may read only their own team's selectable provisioned LLM providers through the selection routes
- leaders may set or clear only their own team's active LLM selection
- the active team selection stores both:
  - a team default model
  - an allowed-model subset that controls which models normal users can see and choose
- normal users may not access provisioning or team-selection routes
- normal users may set or clear only their own preferred default model through `/api/v1/llm-preference`
- normal team users may read, write, and clear only their own `/api/v1/app-preferences` row
- `user_app_preferences` currently stores validated workflow metadata only:
  - favourite quick action ids
  - favourite template ids
  - default quick action/template ids
  - `llm_detail_level`
  - preferred recording mode
  - preferred transcribe tab
- `user_app_preferences` rejects template/quick-action ids outside the caller's currently visible owner/team scope
- when referenced templates or quick actions are later deleted or hidden, `/api/v1/app-preferences` drops those stale ids lazily on read
- if the user's preferred model is no longer allowed for the active team provider, runtime resolution falls back to the team-selected default model
- the implemented LLM adapter families are `openai_chat`, `bedrock_chat`, and `ollama_chat`
- `openai_chat` inspection uses the official OpenAI SDK server-side to return built-in contract defaults plus a filtered `available_models` list
- if OpenAI model discovery fails, `openai_chat` inspection falls back to a built-in chat-model list and still returns `200`
- `bedrock_chat` uses Amazon Bedrock's OpenAI-compatible Bedrock Mantle endpoint and the existing OpenAI SDK integration for both `/models` discovery and Chat Completions generation
- `bedrock_chat` accepts an optional `bedrock_region`; when `base_url` is blank OpenScribe derives `https://bedrock-mantle.<region>.api.aws/v1`
- `bedrock_chat` does not use a built-in fallback model list because the available models are region- and account-specific; admins may still save a model manually if discovery is unavailable
- `ollama_chat` inspection calls `GET /api/tags` on the configured Ollama host and generation uses streaming `POST /api/chat`
- local Ollama may run without an API key; remote Ollama endpoints must still use `https`
- the admin HTML inspect flow discards the entered API key after the request; saving a newly inspected credential requires key re-entry, while saved-provider re-inspection uses the Vault reference server-side
- remote LLM endpoints must use `https`; `http` is accepted only for localhost/private-network hosts
- the API never returns the bearer token
- the API currently returns metadata plus `has_secret`, not the raw Vault secret reference
- normal team users may patch only their own ready note documents through `/api/v1/generated-documents/{generated_document_id}`
- note save requests must include `expected_updated_at`; stale revisions return `409 conflict`
- Working-note save and clear requests use `expected_updated_at` for optimistic concurrency. Clearing an existing Working note without the current token, or saving with a stale token after another tab clears it, returns `409 conflict`.
- one team may have multiple provisioned LLM config rows
- one team may have only one active LLM selection row

Current template behavior:

- team templates are normal configuration data, not transcript-derived content
- leaders may create, update, list, and delete team templates for their own team
- normal users may create, update, list, and delete only their own personal templates
- system admins do not own or manage transcript-derived generation output through these routes
- template updates create a new immutable `template_versions` row while updating the logical template root metadata
- quick actions now follow the same team/personal scope model as templates:
  - leaders may create, update, list, and delete team quick actions for their own team
  - normal users may create, update, list, and delete only their own personal quick actions
  - quick action updates create a new immutable `quick_action_versions` row while updating the logical quick action root metadata
- smart phrases are personal configuration only:
  - normal team users may create, update, list, mark-used, and hard-delete only their own smart phrases
  - triggers are stored uppercase without the leading slash and unique per owner case-insensitively
  - system admins do not own smart phrases

Current generation behavior:

- note generation is owner-only and runs against the selected transcript root
- follow-up generation is also owner-only and runs against the selected transcript root
- quick action generation is owner-only and runs against the selected transcript root
- generation snapshots the current transcript draft into a new `transcript_versions` row before calling the LLM
- queued generated-document rows now also snapshot:
  - resolved `llm_config_id`
  - resolved `model_used`
  - prompt text for template and quick-action runs
  - provider execution metadata needed to keep the worker stable if team defaults later change
- generation resolves the active team LLM provider plus the user's preferred/default model through the existing provider-selection path
- generation currently supports both OpenAI chat-style providers and Ollama chat hosts
- generation now applies native PHI pseudonymisation before outbound LLM calls:
  - a successful reusable `redaction_runs` row is created lazily per `transcript_versions` snapshot when first needed
  - `redaction_entities` persist the placeholder-to-original mapping for later reconstruction
  - generated-document rows keep the `redaction_run_id` used for that run
  - transcript text is sent to the external LLM only in redacted form
  - static template and quick-action asset instructions are sent as configured, without PHI redaction
  - dynamic clinician/user/patient-originated prompt inputs such as dictation, Working note, follow-up requests, quick-action additional context, and structured context strings are redacted transiently before the provider call
  - quick-action context audio preview uses the post-consultation dictation STT selection, returns `{ "text": "..." }`, and does not persist a separate transcript-derived row before the client submits the existing quick-action context field
  - generated output is validated so only well-formed known placeholders survive to re-identification
  - final stored output is re-identified before being written back into `generated_documents`
- clinical NLP snapshots are created beside successful redaction runs when the team has selected a clinical NLP endpoint:
  - `clinical_entity_runs` records owner/team/transcript/version scope, provider snapshot metadata, status, and whether the submitted source text was redacted
  - `clinical_entities` stores detected disease/symptom values encrypted per owner, with owner-keyed normalized hashes for duplicate matching
  - long clinical NLP payloads are split into bounded text chunks before generic REST calls; returned spans are offset back into the original transcript text before persistence
  - local `/analyze` clinical endpoints default to `sentence_detection=false` unless the provider config explicitly supplies that field, avoiding observed long-input timeouts on the OpenMedNER dev service
  - deleting a clinical NLP provider clears active clinical NLP selections and preserves historical clinical runs by setting their provider reference to null
  - clinical detection failure does not expose provider output or transcript text through admin routes
- a dev-only verification endpoint now exists for localhost seeded test accounts:
  - `GET /api/v1/generated-documents/{generated_document_id}/redaction-debug`
  - it remains owner-only
  - it returns the redacted transcript payload and placeholder inventory for the linked `redaction_run`
  - it does not return the original PHI values
- the implemented generators are:
  - template-based note output that now requires the LLM to return JSON with:
    - `title`: a short user-facing consultation summary
    - `content`: the full note body for `freeform` templates, or an object keyed by selected EMIS section names for `structured` templates
  - freeform follow-up output
  - quick action freeform output written back into the follow-up lane
- template mode now supports:
  - `freeform`
  - `structured`
- the first structured profile is EMIS with allowed section keys:
  - `problem`
  - `history`
  - `family_history`
  - `social_history`
  - `examination`
  - `comment`
  - `tasks`
  - `investigations`
- structured template versions store per-section instructions in `template_versions.config_json`
- structured generation uses saved transcript/dictation/Working-note sources only; `POST /generate-output` accepts `template_id` and rejects transient `structured_context`
- follow-up generation always uses saved transcript/dictation/Working-note sources plus the typed follow-up request; saved Working note content is redacted and included, not opt-in. At least transcript text or saved Working note content is required
- quick action generation always uses saved transcript/dictation/Working-note sources plus selected quick-action instructions and optional submitted quick-action context; saved Working note content is redacted and included, not opt-in. At least one saved consultation source is required
- the current transcript session stores structured Working note content in `transcripts.structured_context_json`
- legacy `PATCH /api/v1/transcripts/{transcript_id}` structured Working-note writes accept `expected_updated_at` and enforce the same stale-write guard as `/working-note`
- `/transcribe` reloads EMIS context fields from that transcript-backed state
- when template, follow-up, or quick-action generation is queued, the saved Working note is snapshotted onto generated-document Working-note snapshot fields
- for structured notes, backend validation:
  - rejects user-submitted section keys outside the configured EMIS subset
  - rejects unsupported saved Working-note section keys instead of dropping them
  - validates saved Working note EMIS sections through the Working-note API
  - drops empty sections
  - preserves configured section order
  - renders full note text into `generated_documents`
  - persists section parts into `generated_document_sections`
- for template-generated notes, the returned JSON `title` is persisted into `generated_documents.title`
- if a template-generated note returns invalid JSON or omits `title`/`content`, generation fails with `llm_generation_invalid_json`
- template-note JSON parsing applies only mild coercion before failure:
  - strips markdown code fences
  - extracts the first balanced JSON object if the model wraps it in surrounding prose
- if note JSON still fails, the raw redacted provider output is retained on the generated document for localhost dev-account debugging only
- generation is now asynchronous:
  - `POST /api/v1/transcripts/{transcript_id}/generate-output` returns `202`
  - `POST /api/v1/transcripts/{transcript_id}/generate-followup` returns `202`
  - `POST /api/v1/transcripts/{transcript_id}/run-quick-action` returns `202`
  - the app creates a `generated_documents` row immediately with status `queued`
  - a Celery worker later moves it through `processing` to `ready` or `failed`
- follow-up generation stores the typed follow-up request on the queued generated-document row and uses the same worker, rate limits, and metadata-only usage logging as note generation
- quick action generation stores the selected `quick_action_version_id` plus the quick action name on the queued generated-document row and uses the same worker, rate limits, and metadata-only usage logging as notes/follow-ups
- generated output is persisted into `generated_documents` and remains private to the transcript owner
- template or quick-action deletion no longer breaks queued/generated output:
  - generated documents retain their prompt snapshot
  - source version references may be cleared when the source asset is deleted
  - already queued work still has enough context to run
- generation routes are throttled per authenticated user:
  - `1 per 5 seconds`
  - `100 per day`
- browser and JSON generation routes share the same authenticated limiter bucket
- generation workers now persist metadata-only usage events in `provider_usage_events` as well as emitting runtime usage logs
- generation metadata now carries team/user IDs, provider/model names, statuses, durations, input/output/total token counts, and safe provider error metadata when available
- generated-document rows now retain per-run input/output/total token counts, durations, provider HTTP status, and safe provider error codes for later debugging
- failed generations now keep a more specific safe reason where available, such as provider timeout, unreachable provider, rejected credentials, missing model, or provider-side rate limiting
- transcript deletion cascades to generated documents through the transcript-root delete path

### System-admin-only routes

These require a full authenticated system-admin session:

- `POST /api/v1/teams`
- `GET /api/v1/teams`

Team retention policy:

- `POST /api/v1/teams` accepts `default_retention_days` only as admin-managed team policy
- `default_retention_days` must be between `1` and `MAX_RETENTION_DAYS` days; default max is `90`

### Transcript routes

Transcript routes require a full authenticated user and remain owner-only:

- `POST /api/v1/transcripts/start` creates the transcript root for the current user
- `/api/v1/transcripts/start` records or implies `ingestion_mode`
- a user may create a transcript only for `owner_user_id == current_user.id`
- a user may commit only their own transcript
- a user may list only their own transcripts
- a user may upload audio chunks only for their own transcript

Current transcript-start behavior:

- the current user becomes `owner_user_id`
- `team_id` is derived from the current user
- `title` lives on the transcript root and is the current browser-level session title
- the transcript root remains the current session root for retention, versions, and derived-document lineage
- creating a new transcript root is rejected when the owner's latest transcript is still blank:
  - title-only does not count as content
  - a non-empty draft, a transcript version, or an ingestion job does count
- creating a new transcript root is also rejected while the owner's latest session is still `transcribing`
- `PATCH /api/v1/transcripts/{transcript_id}` currently supports owner-only title updates
- `PATCH /api/v1/transcripts/{transcript_id}` also supports owner-only `ingestion_mode` switching between `whole_file` and `live_chunked`
- mode switching is allowed only while the session is still blank and idle
- `POST /api/v1/transcripts/{transcript_id}/finalize-live-capture` is owner-only and valid only for `live_chunked` transcripts:
  - moves an active live transcript out of `recording`
  - applies completed chunks in sequence
  - returns `transcribing` without creating a redaction run if chunks are still queued or processing
  - creates or reuses a transcript version and owner-scoped redaction run once the final draft is `ready`
- `DELETE /api/v1/transcripts/{transcript_id}` hard-deletes the owner transcript root immediately and cascades to transcript versions, ingestion jobs, generated documents, post-consultation dictation, redaction runs, and manual PII rows
- `POST /api/v1/transcripts/{transcript_id}/manual-pii` lets the owning user persist a missed PII item for transcript review/highlighting:
  - JSON body: `entity_type`, `value`, optional `occurrence_count`
  - response: owner-only PII row with `id`, `entity_type`, plaintext `value`, `placeholder = "Manual"`, `occurrence_count`, and `source = "manual"`
  - stored value is encrypted with the owner content DEK
  - duplicate type/value rows for the same transcript return/update the existing row rather than creating another
  - saved manual PII is also applied as an outbound redaction layer for LLM generation and added to the PHI placeholder index for output validation/reidentification
- `DELETE /api/v1/transcripts/{transcript_id}/manual-pii/{entity_id}` hard-deletes one owner-created manual PII row
- `POST /api/v1/transcripts/{transcript_id}/pii-entities/reveal` returns original PII values for the owning user only:
  - non-owners receive `404` so transcript existence is not confirmed
  - default workspace and generated-document PII rows omit `value` and include `has_value` for explicit reveal UI
  - route uses POST so browser CSRF/origin checks apply
- system-admin accounts are blocked from owning transcript content
- `ingestion_mode` is persisted on the transcript root and currently supports:
  - `whole_file`
  - `live_chunked`
- if the caller omits `ingestion_mode`, the route currently implies `whole_file`
- team retention defaults are server-owned and always applied to new transcript roots
- public transcript create/start/update payloads cannot extend `retention_days_applied` or `retention_expires_at`
- transcript JSON detail responses expose plaintext draft as `current_draft_text`; DB/request storage fields keep `current_draft_text_encrypted`
- generated-document JSON responses expose plaintext output as `original_output_text` and `edited_output_text`; DB storage fields keep `_encrypted` names
- sensitive transcript/workspace/generated-document API responses include `Cache-Control: no-store` and `Pragma: no-cache`
- transcript JSON responses remain owner-plaintext where explicitly requested even though transcript drafts, transcript structured context, committed transcript versions, STT job result text, generated-document body fields, generated-document sections, follow-up prompts, redaction output text, and redaction entity values are now stored encrypted at rest per owner
- transcript and generated-document `title` fields remain plaintext metadata in this slice

Current live chunk-ingestion behavior:

- `POST /api/v1/transcripts/{transcript_id}/audio-chunks` accepts multipart audio upload for owner-only live chunked transcripts
- live chunk upload is rate-limited to `1 request/second` per authenticated user/session bucket
- live chunk queueing also enforces a rolling hourly audio budget per authenticated owner; the default ceiling is `3600` uploaded seconds per hour via `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS`
- the route currently requires:
  - `audio`
  - `chunk_sequence_no`
- the route currently accepts:
  - `declared_duration_seconds`
- chunk uploads are rejected unless the transcript `ingestion_mode` is `live_chunked`
- the server measures the uploaded audio duration before queueing:
  - that measured duration is what counts toward the rolling hourly budget
  - client-supplied `declared_duration_seconds` is no longer trusted for budgeting
- live chunk uploads reject measured durations above the current 30-second maximum
- the route queues a transcript-ingestion job and returns `202 Accepted`
- the response includes both the transcript summary and the queued ingestion job
- queued live chunk jobs now persist both `source_audio_size_bytes` and the measured chunk duration in `declared_duration_seconds` so upload volume can be aggregated later for dashboarding or broader ingestion policy
- queued chunk jobs now snapshot the resolved STT provider execution settings at enqueue time:
  - selected STT config id
  - adapter kind
  - base URL and transcribe path
  - resolved model and language
  - file field name, response text path, and extra form fields
- the backend worker normalizes the uploaded audio to `16 kHz` mono PCM WAV with `ffmpeg`; ffprobe/ffmpeg calls have bounded timeouts so stuck media inspection/normalization fails cleanly
- the backend worker reads the queued STT snapshot plus the selected provider credentials from Vault
- the backend worker forwards the normalized chunk to the external STT service
- the backend worker encrypts the returned live-chunk text at rest before later owner-visible draft reconciliation
- live chunk application is sequence-aware:
  - duplicate `chunk_sequence_no` values are rejected at queue time
  - completed chunks are appended only in order using `next_live_chunk_sequence_no_applied`
- live chunk jobs left queued or processing beyond `LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS` are marked `failed` with `ingestion_processing_stale` during transcript reconciliation, so later completed chunks can advance through the existing failed-gap path
- the transcript status remains `transcribing` while more live chunks may still arrive
- leaders/admins may configure team transcription metadata without gaining transcript readability

Current whole-file ingestion behavior:

- `POST /api/v1/transcripts/{transcript_id}/audio-file` accepts multipart audio upload for owner-only `whole_file` transcripts
- whole-file queueing now records both `source_audio_size_bytes` and `source_audio_duration_seconds` on the ingestion job for later upload reporting
- whole-file queueing enforces a rolling hourly upload budget per authenticated owner:
- upload bytes via `WHOLE_FILE_HOURLY_UPLOAD_BYTES` (default `209715200`)
- source audio duration via `WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS` (default `14400`)
- whole-file normalization uses `AUDIO_FFMPEG_TIMEOUT_SECONDS` (default `1800`) and STT provider requests use `STT_TRANSCRIPTION_TIMEOUT_SECONDS` (default `14400`) so long accepted uploads are not abandoned before the provider returns
- whole-file ingestion no longer persists newly uploaded source audio blobs in Postgres while the owner-content at-rest encryption path is still pending
- newly uploaded whole-file source audio is retained for retry in Vault-backed secret storage, with only a Vault reference stored on the ingestion job row
- `POST /api/v1/transcripts/{transcript_id}/retry-audio-file` works when the latest failed whole-file job still has a stored retry source, either as a legacy DB blob or a Vault-backed source-audio ref
- transcript deletion and user deletion now attempt best-effort cleanup of any Vault-backed retry audio before the owning rows are removed, without blocking the hard-delete path on a transient Vault outage
- applied whole-file jobs now keep `source_audio_size_bytes` and `source_audio_duration_seconds` so rolling hourly budgets continue to count recently completed uploads
- file ingestion is rejected unless the transcript `ingestion_mode` is `whole_file`
- file ingestion is rejected while another `audio_file` ingestion job for that transcript is already `queued` or `processing`
- the route queues a transcript-ingestion job and returns `202 Accepted`
- queueing now fails early if no active team STT selection exists
- queueing now also fails early with `stt_config_secret_missing` if the selected STT config expects a saved credential and Vault no longer has it
- queued file jobs snapshot the resolved STT provider execution settings at enqueue time, so later team-setting changes do not alter where an already-uploaded file is sent
- the backend worker normalizes the uploaded audio to `16 kHz` mono PCM WAV with `ffmpeg`; ffprobe/ffmpeg calls have bounded timeouts so stuck media inspection/normalization fails cleanly
- the backend worker uses the queued STT snapshot plus the saved bearer credential when the selected adapter needs one
- the backend worker forwards the normalized audio file to the external STT service
- the backend worker appends the returned transcript text into `current_draft_text_encrypted`
- transcript drafts, committed transcript versions, and STT job result text now use one wrapped user DEK per normal content-owning user, with the DEK wrap/unwrap path handled through Vault Transit
- the transcript status moves to `ready` when the provider returns successfully
- if the queued STT config no longer has a readable saved credential, the job is marked `failed` with the same `stt_config_secret_missing` message the browser upload path uses
- generic REST STT failures now keep safer detail at the job level:
  - connect failure -> `stt_unavailable`
  - timeout -> `stt_timeout`
  - upstream non-2xx -> `stt_request_failed` with `status_code`, `provider_status_code`, and a safe `provider_error_code` when the provider returns one
  - unreadable JSON or missing transcript text path -> `stt_response_invalid`
- `GET /api/v1/transcripts/{transcript_id}` now includes the latest ingestion failure metadata when present:
  - `next_live_chunk_sequence_no_upload`
  - `latest_ingestion_job_status`
  - `latest_ingestion_error_code`
  - `latest_ingestion_error_message`
  - `latest_ingestion_retry_available`
- `GET /api/v1/transcribe/workspace` now exposes the owner-facing read model for the `/transcribe` page:
  - `recent_transcripts`
  - `active_transcript`
  - `active_transcript_pii_entities`
  - `active_transcript_redaction_status` with latest owner-visible redaction status, entity count, and safe error code
  - `generated_documents`
  - `available_templates`
  - `available_quick_actions`
  - `active_structured_context`
  - current session-level capability flags like `can_create_new_session` and `can_switch_to_whole_file`
- `GET /api/v1/transcribe/workspace/stream` now exposes the same owner-facing workspace payload as an SSE stream for the `/transcribe` page.
- the SSE route validates auth using short-lived DB sessions and does not hold a request-scoped SQLAlchemy session open for the lifetime of the stream
  - emits `workspace` events
  - follows the same owner-only access rules as the JSON workspace endpoint
- the owner-facing `/transcribe` workspace now:
  - creates blank sessions from the session rail
  - blocks a second blank session until the latest session has draft content or descendant work, or is deleted
  - also blocks a new session while the latest session is still transcribing
  - requires an active selected session before upload
  - uses a single whole-file session type in the browser and lets the user choose file upload or microphone batch inside the session
  - queues file ingestion into the selected transcript root
  - records microphone batches locally in the browser with `MicVAD` voice-only gating plus short buffer and submits one captured WAV blob through the same `/transcribe/upload` file-ingestion path
  - supports bulk-delete of selected transcript sessions from the session rail
  - exposes `recent_transcripts[].has_transcript_content` as an owner-only boolean so the browser can require confirmation before deleting a non-empty session without exposing transcript text in the rail
  - exposes `active_transcript_pii_entities` as owner-only summary rows from the latest successful redaction run, disease/symptom rows from the latest successful clinical NLP run, plus owner-created manual PII rows for the active transcript; original values are omitted until explicit reveal
  - exposes `active_transcript_redaction_status` and `active_transcript_clinical_nlp_status` so empty review rows can distinguish not-run, failed, and succeeded-with-zero-results states without exposing transcript text
  - includes note-level `generated_documents[].pii_entities` summary rows without original values so switching selected notes refreshes the PII panel without a page reload
  - hydrates the active workspace state from `GET /api/v1/transcribe/workspace`
  - keeps an owner-scoped SSE connection to `GET /api/v1/transcribe/workspace/stream` for pushed workspace updates
  - falls back to polling the same owner-only workspace read model only while a live session is actively recording or restarting if SSE is unavailable or disconnected
  - creates new sessions through `POST /api/v1/transcripts/start`
  - deletes selected sessions through owner-scoped `DELETE /api/v1/transcripts/{transcript_id}` calls
  - switches a blank session back to `whole_file` through `PATCH /api/v1/transcripts/{transcript_id}`
  - switches the active session in place by refetching `GET /api/v1/transcribe/workspace?transcript_id=...` instead of full-page navigation
  - patches transcript session title and EMIS working context through `PATCH /api/v1/transcripts/{transcript_id}`
  - queues whole-file upload directly through `POST /api/v1/transcripts/{transcript_id}/audio-file`
  - recorded microphone upload rolls over before browser-captured WAV parts approach whole-file limits, sends each part through the same owner-only whole-file endpoint, and holds later parts in memory while the backend's one-active-file-job rule clears
  - offers retry through the same workspace when `active_transcript.latest_ingestion_retry_available` is true
  - queues note/follow-up/quick-action generation directly through the corresponding `/api/v1/transcripts/{transcript_id}/...` JSON routes
  - enforces the same 4000-character limit for quick-action additional context on the API path as the browser textarea, trimming blank-only values to null server-side
  - the non-JS `/transcribe/run-quick-action` form path now enforces that same quick-action additional-context limit before queueing work
  - shows recent owner transcripts and current draft text on refresh or poll completion
  - preserves structured EMIS note section rendering and copy-selected-lines behavior during workspace refreshes by rebuilding the section view from generated-document section data
  - includes each structured generated note's snapshotted allowed section definitions in workspace/API note payloads so deleted template provenance does not expand the editable section set on refresh
  - silently saves dirty owner note edits before switching note-history versions; if save fails or conflicts, the browser keeps the current editor state selected
  - now shows explicit session progress copy in the header and active rail row for local recording, uploading, queued, transcribing, ready, and failed states
- if no active team STT selection exists, the browser flow fails early with:
  - `No STT configured, please ask your team leader {email}`
  - or a generic team-leader message when no active leader email is available

System-admin or leader authority does not grant transcript-content access.

### Provider model enforcement

- STT and LLM selection flows now enforce server-provided model lists server-side, not only in the UI
- leader/team LLM allowed-model subsets must be chosen from the provider-discovered model list
- user LLM preferences must be chosen from the leader-approved allowed-model subset
- STT team selection rejects model overrides outside the provider-discovered model list
- if a provider does not return a selectable model list, the selection APIs reject free-text overrides rather than silently accepting them

## Current uniqueness and onboarding rules

### User email

- emails are normalized before persistence
- uniqueness is enforced case-insensitively by a unique index on `lower(email)`

### Team name

- teams keep the display `name`
- teams also store a canonical `name_key`
- `name_key` is built from Unicode normalization + trim + collapsed whitespace + case-folding
- uniqueness is enforced on `name_key`

### Account requests

- account requests are deduplicated by normalized email + normalized requested team name while pending
- creating a request for an existing user email returns `409 conflict`

### Managed users

- manager-created users are active immediately
- they are created with a temporary password hash
- they start with:
  - `must_change_password = true`
  - `onboarding_state = pending_password_change`
