# Team STT Configuration

This document defines the current speech-to-text endpoint-management model and the intended boundaries around secrets, team policy, and runtime provider use.

The goal is to let a team use one active STT selection that users later consume indirectly through transcript capture, without exposing credentials or transcript content to leaders or users.

## Objective

Add the first STT-management surface with:

- admin-provisioned STT endpoint rows per team
- one active team STT selection at a time
- OpenAPI inspection only for the generic adapter family
- known-contract adapters for OpenAI-hosted and OpenAI-compatible endpoints
- explicit adapter families instead of fully generic runtime execution
- REST endpoint metadata stored in Postgres
- bearer token stored in Vault, not in Postgres
- a clear path to the intended authority split where:
  - system admins provision STT endpoints and credentials
  - team leaders choose or clear the active service/model for their team
- no credential reveal path

This configuration model now feeds the transcript-ingestion runtime. Users consume only the resolved active team STT selection during chunk or file upload.

Credential replacement writes a versioned Vault secret, commits the new database reference and an outbox intent for the retired reference atomically, then lets the scheduled cleanup worker delete Vault data. Deletion, draft cancellation, revision promotion, and team deletion use the same FK-free durable cleanup path. The worker never deletes a reference still present in any provider configuration.

## Why this shape

The broader architecture already defines a future provider layer with:

- `providers`
- `team_provider_credentials`
- `team_provider_policies`

That full provider layer is not implemented in runtime code yet.

The corrected and now-implemented shape preserves the same architectural rules:

- metadata in Postgres
- secret reference in Postgres
- raw secret in Vault
- team-scoped manager authority
- no content visibility expansion

This keeps the first slice small without inventing a second secret model.

Implemented authority split:

- raw STT endpoints and credentials are system-admin-managed
- team leaders configure policy, not secrets
- team leaders may clear their team STT selection, but may not recover or rotate raw credentials directly

## Supported configuration model

The first implementation now supports a split model:

- a generic OpenAPI-inspected REST adapter
- known-contract OpenAI adapters that do not need OpenAPI inspection to get started

Supported assumptions:

- REST endpoint
- `POST`
- bearer-token auth
- multipart file upload
- optional fixed extra form fields
- simple JSON path extraction for transcript text later

Current adapter families:

- `generic_rest`
- `openai_cloud`
- `openai_compatible_rest`
- `elevenlabs_speech_to_text`

Provider setup now also has an LLM-style wizard layer above these adapter families:

- `openai`
- `deepgram`
- `elevenlabs`
- `custom_openai_compatible`
- `custom_rest_openapi`

Wizard-created configs start as `setup_status=pending_model_selection` and `is_active=false`. System admins can see and continue these incomplete configs, but team leaders/users cannot select them until finalization sets `setup_status=ready` and `is_active=true`.

`generic_rest` is the fallback:

- OpenAPI inspection uses `base_url + openapi_path`
- OpenAPI documents are schema-validated and local `$ref` entries are dereferenced with OpenAPI tooling before inference
- manager confirms the inferred request/response fields before save
- this is for providers whose contract is not already built into OpenScribe
- inspection can infer provider-specific `model_field_name` and `language_field_name`, such as `model_id` and `lang`
- runtime does not infer provider shape; it uses only the saved contract fields
- runtime uses saved segment mapping fields when a provider returns timestamped segment arrays

`openai_cloud` is the official-hosted OpenAI path:

- known `POST /v1/audio/transcriptions` contract
- bearer auth
- multipart upload
- required `file`
- required `model`
- no OpenAPI path needed
- inspection requires the API key and uses the official OpenAI Python SDK to load a filtered list of supported transcription models
- if live model discovery fails, OpenScribe falls back to a built-in supported transcription-model list instead of blocking setup
- intended runtime direction is the official OpenAI Python SDK
- OpenScribe uses the official API base path `https://api.openai.com/v1` for this adapter
- UI should ask only for:
  - label
  - API key at inspect time
  - then model selection and optional language on save-and-inspect
- the model dropdown now marks each option as:
  - `(fetched)` when returned by the live SDK model lookup
  - `(default)` when OpenScribe is using its built-in fallback list
- generic fields like `Base URL` and `OpenAPI path` are hidden for this adapter in the browser UI
- hidden generic fields are also disabled client-side so browser validation does not block `openai_cloud` submission with stale required inputs

Local diagnostic helper:

- `scripts/test_openai_models.py` reads `OPENAI_API_KEY` from the repo-root `.env` if needed
- it calls the official Python SDK `client.models.list()`
- it prints the transcription-related models whose ids contain `transcribe` or `whisper`

`openai_compatible_rest` is the known-contract REST path for custom hosts:

- `POST /v1/audio/transcriptions`
- bearer auth
- multipart upload
- required `file`
- required `model`
- no OpenAPI path needed
- meant for local/private-network or vendor endpoints that intentionally mimic the OpenAI transcription request shape
- save-time token replacement uploads the bundled synthetic audio sample to the configured transcribe path before writing the replacement token to Vault

Both OpenAI adapter families still keep:

- metadata in Postgres
- optional bearer token in Vault for self-hosted endpoints that do not require auth

`deepgram` is a provider-specific known contract on top of `generic_rest`:

- inspection requires an API key and calls `GET https://api.deepgram.com/v1/models`
- admin save/inspect paths normalize `https://api.deepgram.com` to the Deepgram contract only when the adapter is `generic_rest`; incompatible adapters are rejected, while existing runtime rows continue to honor their stored adapter instead of switching transport by hostname alone
- discovery uses `Authorization: Token <api key>` and stores only `stt` models where `batch` is not `false`
- invalid Deepgram credentials fail draft creation and do not create a config row
- successful discovery saves returned model ids in `available_models_json`; finalization must choose from that list when non-empty
- admin draft finalization pages render saved discovered models as a dropdown in both admin UIs, without re-rendering the saved API key field
- runtime transcription calls `POST /v1/listen` with raw audio bytes, not multipart form data
- Deepgram query options use the existing `extra_form_fields_json` metadata field; for this provider those values are sent as query params, including `smart_format=true` and mandatory `mip_opt_out=true`
- Deepgram configs cannot opt in to model improvement processing: missing `mip_opt_out` is added on save, explicit non-true values are rejected, and runtime forces `mip_opt_out=true` for old saved rows
- runtime sends `model` and optional `language` as query params and extracts transcript text from `results.channels.0.alternatives.0.transcript`
- raw API keys remain Vault-backed and are never returned by API/admin responses

`elevenlabs` is a provider-specific known contract using the dedicated `elevenlabs_speech_to_text` adapter:

- inspection requires an API key and calls `GET https://api.elevenlabs.io/v1/models`
- the `/v1/models` call is a credential/catalog probe only; selectable STT models are hard-coded to synchronous ids `scribe_v2` and `scribe_v1`
- invalid ElevenLabs credentials fail draft creation and do not create a config row
- realtime-only `scribe_v2_realtime` and non-STT models are not shown in the wizard dropdown
- runtime transcription calls `POST /v1/speech-to-text` as multipart form data with `xi-api-key` auth, `file`, `model_id`, and optional `language_code`
- finalize, direct save, selection override, and runtime reject non-sync ElevenLabs models
- response formatting reads `text` and timestamp/speaker data from `words` using `speaker_id`
- provider-default language values such as blank, `None`, `auto`, and `default` are normalized to no language value and are not sent to providers
- raw API keys remain Vault-backed and are never returned by API/admin responses

## Admin diagnostics

System admins can now run a saved-config STT diagnostic directly from `/admin` for the selected team.

The browser action:

- uses the saved STT config metadata from Postgres
- reads the saved bearer/API secret from Vault
- health-checks the provider first when the adapter is not `openai_cloud`
- uploads the bundled fixture `tests/MoreOrLess.wav`
- renders the outcome back under the STT area without creating a transcript or ingestion job

The rendered result is metadata-safe and may include:

- health status
- transcribe URL
- configured default model/language
- sample filename and byte count
- duration
- either the returned transcript text for the bundled sample or the provider error code/message

This diagnostic is system-admin-only and does not reveal raw provider credentials.
- no secret reveal path

- system admins add the team STT endpoints and credentials
- team leaders choose which provisioned service and model are active for their team
- team leaders may clear the active team STT selection
- normal users later consume only the resolved active team STT policy during transcript capture

Supported metadata fields:

- `label`
- `provider_preset`
- `adapter_kind`
- `base_url`
- `transcribe_path`
- `model_name`
- `model_field_name`
- `file_field_name`
- `language`
- `language_field_name`
- `response_text_path`
- `segments_path`
- `segment_text_field`
- `segment_start_field`
- `segment_end_field`
- `segment_speaker_field`
- `extra_form_fields_json`
- `setup_status`
- `is_active`

Secret fields:

- optional bearer token; `auth_mode=none` is explicit for providers configured without one
- stored in Vault
- represented in Postgres only by `vault_secret_ref`
- runtime reads the Vault reference only when `auth_mode=bearer`

## URL and transport rules

- non-local endpoints must use `https://`
- `http://` is allowed only for local development targets such as:
  - `localhost`
  - `127.0.0.1`
  - RFC1918 private-network hosts for local/dev lab use
- unsafe schemes are rejected

This keeps the first implementation practical for local STT services while still blocking obviously unsafe remote transport defaults.

## Management authority

### Leaders

- may configure only their own team's active STT selection
- may choose which admin-provisioned STT service/model their team uses
- may clear their own team's active STT selection
- may not manage another team's credentials
- may not view or recover raw provider secrets
- may not gain transcript readability through this page

### System admins

- provision STT endpoints and credentials for any team
- may manage team STT metadata and Vault-backed secrets platform-wide
- must explicitly choose the team they are editing where team-scoped policy is involved
- do not gain transcript readability through this page

### Normal users

- may not access the STT config management routes
- will later use the resolved team STT config only indirectly through transcript chunk upload
- never receive or decode the bearer token

## Secret handling

- the bearer token is never stored raw in Postgres
- the bearer token is written to Vault when `credential_action` is `replace`
- Postgres stores:
  - `vault_secret_ref`
  - config metadata
  - actor/team linkage
- the UI may indicate whether a secret is configured
- the UI must never reveal the current secret value
- inspect/discover responses never render the entered bearer token back into HTML or JSON
- browser save forms do not preserve bearer tokens in hidden fields after standalone inspection; admins must re-enter a key when saving a new credential from an inspected draft
- save-and-inspect writes the submitted credential to Vault once, validates/inspects server-side, and does not require second key entry
- manual `generic_rest` save-and-inspect validates the saved runtime contract with the bundled synthetic audio sample; it does not rely on default `/openapi.json` discovery
- duplicate detection uses same team, adapter, base URL, and a server-side non-reversible credential fingerprint; unconfirmed duplicates warn before Vault write or provider inspection
- saved-provider re-inspection reads the Vault reference and never asks the admin to re-enter the token
- provider edit revisions with a blank required credential read the active config's exact stored Vault reference, including generated secret suffixes, for inspection only; they immediately write that token to a unique versioned Vault path under the revision id and never persist the active reference
- replacement and inherited credentials are first staged under the revision id; finalization copies the credential to a fresh target-config Vault path before atomically promoting metadata into the stable config id
- failed promotion removes only the fresh target copy; successful promotion removes replaced target and revision secrets only after the database no longer references them

First implementation rules:

- system admins choose `credential_action` explicitly: `keep`, `replace`, or `remove`
- blank secret field on edit keeps the current secret only with `credential_action=keep`
- `credential_action=remove` clears the DB secret reference and deletes the Vault secret after the DB commit
- system admins may save self-hosted `generic_rest` or `openai_compatible_rest` endpoints without any bearer token when the provider does not require auth; browser forms default those optional-token adapters to `credential_action=keep` so a blank token is saved as no credential instead of a failed replacement
- `openai_cloud` and `elevenlabs_speech_to_text` require `auth_mode=bearer`; an update cannot set `auth_mode=none`, including `credential_action=keep` with an existing Vault reference
- `openai_cloud` still requires a saved API key
- if a selected STT config expects a saved credential and Vault no longer has it, selection and file/chunk queueing now fail immediately with `stt_config_secret_missing` instead of letting the worker fail later
- saved-config diagnostics surface safe provider failure metadata to system admins, including HTTP status and provider error code such as `quota_exceeded`, without exposing raw secrets or provider error messages
- leaders should eventually configure team policy without touching raw credential material
- responses and logs must never echo the raw secret

Credential statuses:

- `unknown`: existing/no-auth provider not yet inspected through combined flow
- `verified`: credential validation and useful metadata inspection succeeded
- `partial`: credential was saved but metadata discovery failed or fell back after validation attempt
- `degraded`: saved-provider re-inspection failed without credential rejection
- `invalid`: saved-provider re-inspection saw credential rejection; new selections are blocked and active selections using it are cleared

## Database fit

Implemented storage model:

- `team_stt_configs`
  - admin-provisioned endpoint rows for a team
  - contains endpoint metadata plus `vault_secret_ref`
- `team_stt_selections`
  - one row per team
  - points at one provisioned STT config row
  - stores leader/system-admin selection overrides such as active model/language when they differ from the provisioned defaults

## Post-consultation dictation Phase 0 lock

Dictation should not introduce second STT config store. Current `team_stt_configs` model already holds right secrets and endpoint metadata for both normal consultation transcription and post-consultation dictation.

Locked direction:

- reuse `team_stt_configs` for both conversation and dictation endpoints
- keep admin provisioning path unchanged: system admins still provision team STT endpoints and Vault-backed secrets
- split active team policy by purpose at selection layer, not config layer
- intended next schema change: extend selection model to support explicit purpose such as:
  - `conversation`
  - `post_consultation_dictation`
- team may therefore hold different active STT selections for consultation capture and dictation capture at same time
- runtime resolution must request purpose explicitly
- if dictation purpose has no active selection, runtime must fail explicitly rather than silently falling back to conversation selection

Implemented now:

- `team_stt_selections` carries explicit `purpose`
- supported purposes:
  - `conversation`
  - `post_consultation_dictation`
- uniqueness is now one active selection per team per purpose, not one total selection per team
- existing consultation transcription flows continue to use default `conversation` purpose
- dictation callers must resolve `post_consultation_dictation` explicitly

Why this shape:

- preserves one secret/config authority model
- avoids duplicate provider metadata tables
- keeps provider-type boundary explicit
- prevents accidental use of wrong STT endpoint for clinician dictation

Recommended fields:

- `id`
- `team_id`
- `label`
- `base_url`
- `transcribe_path`
- `auth_mode`
- `model_name`
- `file_field_name`
- `language`
- `response_text_path`
- `extra_form_fields_json`
- `vault_secret_ref`
- `is_active`
- `created_by_user_id`
- `updated_by_user_id`
- `created_at`
- `updated_at`

Constraints:

- multiple config rows per team
- no team-level unique constraint on `team_stt_configs.team_id`
- one active selection per team via `team_stt_selections.team_id`
- foreign keys to `teams` and actor `users`

Future fit:

- this table can later be folded into the broader provider model or replaced by `team_provider_credentials` + `team_provider_policies`
- the important invariant is preserved now:
  - metadata in DB
  - secrets in Vault

## Planned UI surface

### Leader home

The implemented leader UI shows:

- provisioned endpoint list for the leader's team
- active team STT selection summary
- choose-active flow
- model selection for the chosen provisioned service
- clear-selection action

Leaders do not see bearer-token entry fields in the steady-state UI.

### Admin page

The implemented system-admin STT panel on `/admin` includes:

- team selector
- current active team selection summary for the selected team
- provisioned endpoint list for the selected team
- initial inspect form for the selected team
- inferred field summary after inspection
- create/update form prefilled from the inspection result
- delete action for provisioned endpoints

## API shape

Current routes:

- `GET /api/v1/stt-configs`
  - system admin only
  - requires `team_id`
- `GET /api/v1/stt-configs/{config_id}`
  - system admin only
  - requires `team_id`
- `POST /api/v1/stt-configs/inspect`
  - system admin only
  - requires `team_id`
- `generic_rest`: fetches `base_url + openapi_path`
- `openai_cloud`: returns built-in contract defaults and a filtered model list through the SDK without OpenAPI fetch
- `openai_compatible_rest`: returns built-in contract defaults without OpenAPI fetch
  - returns inferred or adapter-default request/response fields without saving config
- `POST /api/v1/stt-configs`
  - system-admin-only create/update of a provisioned endpoint row
  - with bearer token, saves and inspects in one pass
  - accepts `confirm_duplicate` for explicit duplicate override
- `POST /api/v1/stt-configs/{config_id}/inspect`
  - system-admin-only saved-provider re-inspection using Vault reference
- `DELETE /api/v1/stt-configs/{config_id}`
  - system-admin-only delete of a provisioned endpoint row and its current Vault-backed secret reference; active selection rows are cleared before the DB row is removed, then Vault cleanup runs after commit
- `GET /api/v1/stt-selection`
  - leader for own team
  - system admin with explicit `team_id`
- `GET /api/v1/stt-selection/options`
  - leader for own team
  - system admin with explicit `team_id`
- `POST /api/v1/stt-selection`
  - leader for own team
  - system admin with explicit `team_id`
- `DELETE /api/v1/stt-selection`
  - leader for own team
  - system admin with explicit `team_id`

## Inspection rules

- inspection is adapter-aware, not arbitrary endpoint probing
- `generic_rest` is OpenAPI-first
- the app fetches only `base_url + openapi_path`, not an unrelated URL
- the known OpenAI adapters do not require or use `openapi_path`
- optional bearer auth may be supplied for the inspection request
- `openai_cloud` requires the API key for inspection because model discovery is server-side
- for `generic_rest`, the app infers:
  - candidate transcription path
  - file field name
  - model default when exposed
  - language default when exposed
  - simple response text path
  - optional timestamped segment array path and segment text/start/end/speaker field names
  - extra fixed form fields with simple defaults/examples
- configured response paths may use legacy dot paths or JSONPath expressions supported by `jsonpath-ng`
- the app also surfaces field descriptions and required/optional hints when the OpenAPI schema provides them
- the app classifies recognized OpenAI-style OpenAPI documents as `openai_compatible_rest`
- the app returns built-in defaults for `openai_cloud` and `openai_compatible_rest`
- the app returns a filtered `available_models` list for `openai_cloud`
- inspection does not persist anything by itself
- inspection never returns the provided bearer token
- standalone inspection does not persist anything by itself
- save-and-inspect persists the final config and sanitized status/metadata in one request

## Security rules

- no route may reveal the current bearer token after creation
- no transcript content is shown or inferred here
- no arbitrary request scripting or custom code hooks
- no arbitrary auth scheme beyond bearer token in the first slice
- no config route may be available to onboarding-only or pending-MFA sessions
- adapter family is explicit so later runtime transcription calls can choose:
  - the official OpenAI SDK for `openai_cloud`
  - a fixed REST request builder for `openai_compatible_rest`
  - the generic adapter path only where needed

## Testable checkpoints

- system admin can create/update/delete a chosen team's provisioned STT endpoint rows
- leader can choose/clear only their own team STT selection
- system admin can inspect a generic STT OpenAPI document for a selected team and receive inferred defaults
- system admin can inspect `openai_cloud` without any OpenAPI document and receive a filtered model list
- ordinary user cannot access STT provisioning or selection routes
- onboarding-only and pending-MFA sessions cannot access STT provisioning or selection routes
- DB stores Vault secret reference only
- UI never reveals the stored secret
- invalid non-HTTPS remote URLs are rejected
- local/dev HTTP URLs are accepted
- one team may have many provisioned STT config rows but only one active selection row

## Explicit non-goals

- no provider health-check execution yet
- no arbitrary custom headers beyond bearer auth
- no full OpenAPI ingestion and dynamic form generation
- no transcript-provider usage events yet
