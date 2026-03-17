# Team STT Configuration

This document defines the first implementation slice for team speech-to-text endpoint management.

The goal is to let a team use one active STT configuration that users can later consume indirectly through transcript capture, without exposing credentials or transcript content to leaders or users.

## Objective

Add the first STT-management surface with:

- one active STT config per team in MVP
- OpenAPI inspection only for the generic adapter family
- known-contract adapters for OpenAI-hosted and OpenAI-compatible endpoints
- explicit adapter families instead of fully generic runtime execution
- REST endpoint metadata stored in Postgres
- bearer token stored in Vault, not in Postgres
- a clear path to the intended authority split where:
  - system admins provision STT endpoints and credentials
  - team leaders choose or clear the active service/model for their team
- no credential reveal path

This is a configuration slice only. It does not yet implement audio upload to the provider.

## Why this shape

The broader architecture already defines a future provider layer with:

- `providers`
- `team_provider_credentials`
- `team_provider_policies`

That full provider layer is not implemented in runtime code yet.

So the first STT implementation uses a dedicated team STT config table that still matches the same architectural rules:

- metadata in Postgres
- secret reference in Postgres
- raw secret in Vault
- team-scoped manager authority
- no content visibility expansion

This keeps the first slice small without inventing a second secret model.

Decision update:

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

`generic_rest` is the fallback:

- OpenAPI inspection uses `base_url + openapi_path`
- manager confirms the inferred request/response fields before save
- this is for providers whose contract is not already built into OpenScribe

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
  - then model selection and optional language on save
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

Both OpenAI adapter families still keep:

- metadata in Postgres
- bearer token in Vault
- no secret reveal path

Current management flow:

- one current STT endpoint summary per team
- a clear action that removes the saved config row and its Vault-backed secret reference
- a blank add flow after clear
- adapter-specific add fields so known adapters only ask for the fields they actually need

Target authority split:

- system admins add the team STT endpoints and credentials
- team leaders choose which provisioned service and model are active for their team
- team leaders may clear the active team STT selection
- normal users later consume only the resolved active team STT policy during transcript capture

Supported metadata fields:

- `label`
- `adapter_kind`
- `base_url`
- `transcribe_path`
- `model_name`
- `file_field_name`
- `language`
- `response_text_path`
- `extra_form_fields_json`
- `is_active`

Secret fields:

- bearer token only in the first implementation
- stored in Vault
- represented in Postgres only by `vault_secret_ref`

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
- the bearer token is written to Vault when the config is created or updated
- Postgres stores:
  - `vault_secret_ref`
  - config metadata
  - actor/team linkage
- the UI may indicate whether a secret is configured
- the UI must never reveal the current secret value

First implementation rules:

- system admins may replace the secret
- system admins may leave the secret field blank when editing to keep the current secret
- leaders should eventually configure team policy without touching raw credential material
- responses and logs must never echo the raw secret

## Database fit

First implementation table:

- `team_stt_configs`

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

- one config row per team in the first slice
- `team_id` unique
- foreign keys to `teams` and actor `users`

Future fit:

- this table can later be folded into the broader provider model or replaced by `team_provider_credentials` + `team_provider_policies`
- the important invariant is preserved now:
  - metadata in DB
  - secrets in Vault

## Planned UI surface

### Leader home

Add a dedicated STT panel on `/home` with:

- current team STT summary
- initial inspect form:
  - adapter family
  - base URL
  - OpenAPI path only for `generic_rest`
  - optional bearer token for inspection
- inferred field summary after inspection
- create/update form prefilled from the inspection result or known adapter defaults
- `openai_cloud` uses a model selector after inspection when the SDK returns available models
- secret rotation field
- active flag

### Admin page

Add a system-admin STT panel on `/admin` with:

- team selector
- current config summary for the selected team
- initial inspect form for the selected team
- inferred field summary after inspection
- create/update form prefilled from the inspection result
- active flag

## API shape

First implementation routes:

- `GET /api/v1/stt-config`
  - leader: current team config
  - system admin: requires `team_id`
- `POST /api/v1/stt-config/inspect`
  - leader: inspect for own team scope
  - system admin: inspect for selected team scope
- `generic_rest`: fetches `base_url + openapi_path`
- `openai_cloud`: returns built-in contract defaults and a filtered model list through the SDK without OpenAPI fetch
- `openai_compatible_rest`: returns built-in contract defaults without OpenAPI fetch
  - returns inferred or adapter-default request/response fields without saving config
- `POST /api/v1/stt-config`
  - create or replace the config for the scoped team

The first slice keeps this intentionally small. We do not need multiple CRUD endpoints for one-per-team configuration yet.

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
  - extra fixed form fields with simple defaults/examples
- the app also surfaces field descriptions and required/optional hints when the OpenAPI schema provides them
- the app classifies recognized OpenAI-style OpenAPI documents as `openai_compatible_rest`
- the app returns built-in defaults for `openai_cloud` and `openai_compatible_rest`
- the app returns a filtered `available_models` list for `openai_cloud`
- inspection does not persist anything by itself
- inspection never returns the provided bearer token
- users still need to save the final config explicitly

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

- leader can create/update only their own team STT config
- system admin can create/update a chosen team's STT config
- leader can inspect a generic STT OpenAPI document and receive inferred config defaults
- system admin can inspect a generic STT OpenAPI document for a selected team and receive inferred defaults
- system admin can inspect `openai_cloud` without any OpenAPI document and receive a filtered model list
- leader can inspect `openai_compatible_rest` without any OpenAPI document
- ordinary user cannot access STT config routes
- onboarding-only and pending-MFA sessions cannot access STT config routes
- DB stores Vault secret reference only
- UI never reveals the stored secret
- invalid non-HTTPS remote URLs are rejected
- local/dev HTTP URLs are accepted
- one team has at most one STT config row in the first slice

## Explicit non-goals

- no audio chunk upload in this slice
- no provider health-check execution in this slice
- no arbitrary custom headers beyond bearer auth
- no full OpenAPI ingestion and dynamic form generation
- no transcript-provider usage events yet
