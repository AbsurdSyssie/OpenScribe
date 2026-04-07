# Development Roadmap

## Status Summary

- [x] Teams and users vertical slice
- [x] Versioned API contract and standardized error envelope
- [x] Managed onboarding rewrite for MVP
- [x] Account requests replacing invite-first onboarding for MVP
- [x] Opaque DB-backed sessions with onboarding/full auth levels
- [x] TOTP enrollment and optional recovery-code generation
- [x] Post-login TOTP challenge with bounded trusted-device freshness
- [x] First brute-force protection with Redis-backed route rate limits
- [x] Account suspension and deletion authority hardening
- [x] Team STT configuration and Vault-backed secret references
- [x] Team LLM configuration and team/user default resolution
- [x] Team and personal templates plus first generated-note output flow
- [x] Async generated-note queueing with Celery-backed worker processing
- [ ] Abuse controls and administrative unlock workflow
- [ ] Transcript lifecycle and retention hardening
- [ ] Structured transcription job logging and observability hardening

## Completed Milestone: Managed Onboarding and Session Hardening

### Objective

Replace invite-first onboarding with:

- public account requests
- leader/admin review
- direct manager-created accounts
- temporary-password first login
- restricted onboarding session
- password change + TOTP setup before normal access
- immediate session revocation on lock/deactivate

Status: `Completed`

### Checkpoints

- [x] Add onboarding/account-request/session/MFA schema
- [x] Public account-request submission
- [x] Leader/admin review and approval flow
- [x] Direct leader/admin user creation with temporary passwords
- [x] Onboarding-only session gating
- [x] Forced password change
- [x] Forced TOTP enrollment
- [x] Optional recovery-code generation
- [x] Lock/deactivate revokes active sessions
- [x] Transcript owner-only access preserved
- [x] API/UI/migration/docs coverage

### Implemented decisions

- account requests supersede invite acceptance for MVP
- system admins review all requests
- team leaders review only requests for their own team
- temporary passwords are manually set and shared out-of-band by the creator
- onboarding sessions may access only onboarding routes, `auth/me`, and logout
- full access begins only after onboarding completes

## Completed Milestone: MFA Challenge and Trusted-Device Freshness

### Objective

Require a second TOTP step on later logins for completed accounts, while allowing a bounded remembered-browser skip window that does not weaken revocation or transcript privacy.

Status: `Completed`

### Checkpoints

- [x] Add `pending_mfa` session auth level
- [x] Add trusted-device persistence table and migration
- [x] Add browser and JSON MFA challenge endpoints
- [x] Require TOTP after password login for completed MFA-enabled users
- [x] Allow remembered-browser skip only within a 24-hour freshness window
- [x] Revoke trusted devices on lock/disable
- [x] API/UI/migration/docs coverage

### Implemented decisions

- trusted devices are opaque hashed tokens, not JWTs
- trusted devices do not authenticate by themselves
- trusted devices only skip TOTP after a correct password login
- freshness is measured from the last successful MFA challenge, not from each later login
- pending-MFA sessions may not access normal routes or transcript features

## Completed Milestone: Initial Brute-Force Protection

### Objective

Add a first defensive layer against credential stuffing and repeated guessing attacks without changing the core auth model.

Status: `Completed`

### Checkpoints

- [x] Add Redis-backed rate-limiting dependency and configuration
- [x] Rate-limit login routes
- [x] Rate-limit TOTP challenge routes
- [x] Rate-limit public account-request submission
- [x] Split app and test rate-limit stores
- [x] API/UI/docs coverage

### Implemented decisions

- rate limiting uses `slowapi`
- limiter storage is configured by `RATE_LIMIT_STORAGE_URL`
- tests use `TEST_RATE_LIMIT_STORAGE_URL`
- login HTML and JSON routes share one limiter bucket
- MFA HTML and JSON routes share one limiter bucket
- public request HTML and JSON routes share one limiter bucket

## Planned Milestone: Abuse Controls and Administrative Unlock Workflow

### Objective

Extend the current route-level throttling into durable abuse controls without creating a broad new security-events subsystem or easy denial-of-service lockouts.

Status: `Planned`

### Planned checkpoints

- [ ] Add DB-backed failed-auth tracking for password and MFA failures
- [ ] Add temporary per-account cooldown or lock policy with explicit expiry semantics
- [ ] Add leader/admin unlock flow with clear authorization boundaries
- [ ] Add lock/unlock reason and actor recording
- [ ] Add tests for lock, unlock, expiry, and transcript/privacy invariants
- [ ] Add docs for lockout policy, unlock workflow, and non-goals

### Planned decisions

- start with per-account controls before broad IP bans
- prefer temporary cooldowns over immediate permanent lockouts
- treat full IP bans as deferred unless there is a demonstrated abuse need
- require auditability for any manual unlock action
- keep transcript/content authorization unchanged

### Explicit non-goals

- no permanent global IP block system in the first slice
- no broad persistent security-events platform in the first slice
- no lockout policy that lets leaders or admins gain transcript visibility

## Completed Milestone: Account Suspension and Deletion Authority Hardening

### Objective

Make manager account-lifecycle authority explicit, team-scoped, and auditable before adding destructive user-management actions to the UI.

Status: `Completed`

### Planned checkpoints

- [x] Add explicit `suspended` user status distinct from `locked` and `disabled`
- [x] Define explicit meanings for reversible suspension versus destructive deletion
- [x] Add manager authorization checks for suspend/reactivate/delete actions
- [x] Allow system admins to suspend/reactivate/delete other accounts across teams
- [x] Allow team leaders to suspend/reactivate/delete non-system-admin users in their own team only
- [x] Block suspension of the last active system-admin account
- [x] Revoke sessions and trusted devices immediately on suspension
- [x] Add destructive delete path with equivalent scope checks
- [x] Add metadata-only audit logging for account-lifecycle actions
- [x] Add API/UI/docs coverage for team-scope and privacy invariants
- [ ] Persist account-lifecycle actions into `audit_events`

### Planned decisions

- leader and system-admin account administration remains metadata-only and must not imply transcript readability
- full user deletion remains immediate hard delete with existing cascade behavior
- manager suspension is the reversible action; it does not delete content
- `locked` remains the temporary security/auth-abuse state
- `disabled` remains the stronger security/platform state
- reactivation from either `suspended` or `disabled` will initially share a password-reset and MFA-trust-reset path
- team leaders may never act on system-admin accounts
- self-suspension through manager routes is blocked in the first implementation
- self-delete through manager routes is blocked in the first implementation

### Explicit non-goals

- no soft-delete layer for users
- no leader access to transcript content as part of delete confirmation or review
- no cross-team leader account management
- no bypass of the last-active-system-admin safety rule for suspension
- no dedicated audit-events table in the first implementation; audit is logger-based metadata for now

## Current Data Model Highlights

### Users

- `full_name`
- `email`
- `password_hash`
- `team_id`
- `team_role`
- `is_system_admin`
- `status`
- `must_change_password`
- `onboarding_state`
- `mfa_required`
- `mfa_enabled`

### New support tables

- `account_requests`
- `user_sessions`
- `user_trusted_devices`
- `user_mfa_methods`
- `user_recovery_codes`

## Completed Milestone: Templates and First Generated Output

### Objective

Add the first reusable note-template management surface and one owner-only LLM generation path without weakening transcript privacy or provider boundaries.

Status: `Completed`

### Checkpoints

- [x] Add template roots and immutable template versions
- [x] Add owner-only generated-document roots under transcripts
- [x] Add leader team-template CRUD on `/home`
- [x] Add user personal-template CRUD on `/home`
- [x] Add owner-only note generation from `/transcribe`
- [x] Snapshot transcript draft into a committed transcript version before generation
- [x] Persist generated output under the transcript root
- [x] Preserve transcript-delete cascade into generated documents
- [x] Add API/UI/migration/docs coverage

### Implemented decisions

- templates are normal configuration data, not transcript-derived content
- team templates are leader-managed and available to team members as selectable inputs
- personal templates are user-managed and available only to their owner
- quick actions are now normal configuration data, parallel to templates, with the same team/personal scope split
- implemented generation modes are freeform template output, freeform follow-ups, and quick actions
- generation is asynchronous and uses the resolved active LLM provider/model for the owner user
- outbound LLM generation now uses lazy transcript-version pseudonymisation via `redaction_runs` and `redaction_entities`
- generated output is stored in `generated_documents` and shown in the workspace Output tab

## Next Milestone: Transcript Lifecycle and Retention Hardening

### Objective

Bring transcript behavior up to the same architectural standard as auth and onboarding.

The foundation should support three future ingestion modes on one backend model:

- file upload
- microphone batch transcription
- live chunked transcription with VAD and max-length chunking

### Planned checkpoints

- [x] team-managed transcription provider configuration
- [x] Vault-backed team credential storage using DB secret references only
- [x] leader and system-admin STT config UI
- [x] transcript start endpoint that creates the root at recording start
- [x] transcript start route records or implies the intended ingestion mode
- [x] audio-chunk ingestion endpoint for client VAD chunks
- [x] backend audio normalization to `16 kHz` mono PCM WAV before STT submission
- [x] draft update flow through backend-owned STT service
- [x] file-upload and microphone-batch ingestion route on the same backend contract
- [ ] explicit transcript delete endpoint
- [ ] cascade deletion tests for transcript root deletion
- [ ] retention expiry behavior and tests
- [ ] transcript service layer cleanup
- [ ] docs for transcript lifecycle, deletion, and ownership rules

### Non-negotiables

- transcript-derived content stays owner-only
- admin or leader authority does not imply content readability
- deletion remains immediate, not soft-delete
- provider secrets remain in Vault, not raw in the database

### Planned implementation order

1. document transcript capture and team STT configuration flow
2. add leader/admin team transcription configuration UI and backend
3. add transcript start flow with ingestion-mode-aware service boundaries
4. add live chunk ingestion flow
5. keep file-upload and microphone-batch routes planned against the same backend contract
6. harden delete, cascade, and retention behavior around the new capture path

## Completed Milestone: Team STT Configuration and Vault Secret Boundary

### Objective

Add the first STT configuration surface without expanding transcript visibility or storing raw provider credentials in Postgres, then refine it into the intended admin-provisioned and leader-selected authority split.

Status: `Completed`

### Checkpoints

- [x] add `team_stt_configs` schema and migration
- [x] add explicit STT adapter family classification
- [x] limit the first auth mode to bearer token only
- [x] store bearer tokens in Vault and persist only `vault_secret_ref` in Postgres
- [x] add leader own-team STT selection UI on `/home`
- [x] add system-admin selected-team STT provisioning UI on `/admin`
- [x] remove leader access to STT credential provisioning and secret rotation
- [x] allow multiple admin-provisioned STT endpoint rows per team
- [x] add separate team-level active STT selection that leaders may change without touching secrets
- [x] add `GET /api/v1/stt-configs`
- [x] add `GET /api/v1/stt-configs/{config_id}`
- [x] add `POST /api/v1/stt-configs`
- [x] add `DELETE /api/v1/stt-configs/{config_id}`
- [x] add `GET /api/v1/stt-selection`
- [x] add `GET /api/v1/stt-selection/options`
- [x] add `POST /api/v1/stt-selection`
- [x] add `DELETE /api/v1/stt-selection`
- [x] block ordinary users, onboarding sessions, and pending-MFA sessions from STT config routes
- [x] reject unsafe remote non-HTTPS endpoints while allowing local/dev HTTP targets
- [x] add API, UI, migration, and docs coverage

### Implemented decisions

- adapter family is explicit and currently includes `generic_rest`, `openai_cloud`, and `openai_compatible_rest`
- constrained REST metadata only, not arbitrary request scripting
- OpenAPI inspection is generic-only; known OpenAI adapters use built-in request defaults
- bearer token rotation is write-only
- responses expose only `has_secret`, not the raw token or Vault reference
- this slice now feeds the transcript-ingestion runtime through the active team STT selection
- the implemented steady-state authority split is:
  - system admins provision STT endpoints and credentials
  - leaders choose or clear the active service/model for their team
  - leaders do not rotate or delete credentials

## Completed Milestone: Team LLM Configuration and Default Resolution

### Objective

Add the first LLM provider-management surface without widening transcript visibility or storing raw provider credentials in Postgres.

Status: `Completed`

### Checkpoints

- [x] add `team_llm_configs`, `team_llm_selections`, and `user_llm_preferences`
- [x] store LLM API keys in Vault and persist only `vault_secret_ref` in Postgres
- [x] restrict LLM provisioning to system admins
- [x] add leader own-team LLM selection UI on `/home`
- [x] add user own-model preference UI on `/home`
- [x] add system-admin selected-team LLM provisioning UI on `/admin`
- [x] add `GET /api/v1/llm-configs`
- [x] add `POST /api/v1/llm-configs/inspect`
- [x] add `POST /api/v1/llm-configs`
- [x] add `DELETE /api/v1/llm-configs/{config_id}`
- [x] add `GET /api/v1/llm-selection`
- [x] add `GET /api/v1/llm-selection/options`
- [x] add `POST /api/v1/llm-selection`
- [x] add `DELETE /api/v1/llm-selection`
- [x] add `GET /api/v1/llm-preference`
- [x] add `POST /api/v1/llm-preference`
- [x] add `DELETE /api/v1/llm-preference`
- [x] reject unsafe remote non-HTTPS endpoints while allowing local/dev HTTP targets
- [x] add API, UI, migration, and docs coverage

### Implemented decisions

- the implemented adapter families are `openai_chat`, `bedrock_chat`, and `ollama_chat`
- model discovery uses the OpenAI SDK server-side rather than a generic OpenAPI executor
- Amazon Bedrock uses its OpenAI-compatible Bedrock Mantle endpoint within the same SDK-based discovery and generation path, with a region-driven URL format of `https://bedrock-mantle.<region>.api.aws/v1`
- Ollama model discovery uses the configured host’s `/api/tags` endpoint and chat generation uses `/api/chat`
- multiple provisioned LLM provider rows are allowed per team
- one team may have one active LLM selection row
- one user may have one preferred default model row
- the active team LLM selection carries both a team default model and the allowed-model subset visible to normal users
- if a saved user preference is no longer allowed for the active team provider, runtime resolution falls back to the team-selected default model
- the implemented steady-state authority split is:
  - system admins provision LLM providers and credentials
  - leaders choose or clear the active provider, choose the team default model, and filter which provider models are visible to users
  - users choose their own preferred default model from the leader-approved subset
