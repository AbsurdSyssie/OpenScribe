# Testing

This document covers non-database testing. Database-specific behavior, safety rules, and persistence-level checks belong in [dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md).

Documentation convention:

- split test docs by concern
- explain the behavior or contract in plain language first
- show the test shape briefly after the behavior description

## Run the suite

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
pytest
```

The test harness now takes a session-level file lock on `/tmp/openscribe_pytest.lock`.

Why:

- the test suite resets the shared test database schema
- concurrent pytest runs against the same `TEST_DATABASE_URL` can deadlock or drop tables out from under each other

Current behavior:

- the first pytest run acquires the lock and proceeds
- a second concurrent run exits immediately with a clear message instead of colliding with the shared test DB
- the browser-style `client` fixture also auto-injects the CSRF token for non-API state-changing routes so existing UI tests behave like a rendered browser page

## Manual file-ingestion smoke test

For a real end-to-end file upload against a running local app, use:

```bash
OPENSCRIBE_EMAIL='user@example.com' \
OPENSCRIBE_PASSWORD='password-1' \
./scripts/test_file_ingestion.sh tests/MoreOrLess.wav
```

What it does:

- logs in through the real auth flow
- prompts for TOTP only if the login comes back as `pending_mfa`
- starts a `whole_file` transcript
- uploads the provided audio file through `/api/v1/transcripts/{id}/audio-file`
- prints the start and upload JSON responses

## What the tests currently cover

### API contract

- public account-request submission
- duplicate account-request conflict behavior
- direct manager-created user onboarding state
- leader review scope limited to their own team
- leader suspend/reactivate scope limited to their own non-system-admin team users
- leader delete scope limited to their own non-system-admin team users
- unauthenticated suspend/reactivate/delete attempts returning `401`
- ordinary users being blocked from manager account routes with `403`
- onboarding and pending-MFA sessions being blocked from manager account routes with `403`
- onboarding-only sessions blocking normal routes
- completed-user login producing `pending_mfa` until TOTP challenge succeeds
- remembered-browser login skipping TOTP only within the freshness window
- expired remembered-browser login requiring TOTP again
- repeated bad login attempts returning `429 rate_limited`
- repeated bad TOTP challenge attempts returning `429 rate_limited`
- repeated public account-request submissions returning `429 rate_limited`
- repeated whole-file transcript uploads returning `429 rate_limited`
- browser and JSON whole-file upload routes sharing the same authenticated rate-limit bucket
- whole-file upload rate limiting being isolated per authenticated user instead of globally by shared test-client IP
- browser state-changing routes rejecting missing CSRF tokens
- whole-file uploads rejecting oversize payloads before queueing
- whole-file ingestion jobs failing when normalized duration exceeds the configured maximum
- server-side security log emission on rate-limit hits
- password change + TOTP + QR-assisted setup + recovery-code completion flow
- session revocation when a user is locked
- session revocation when a user is suspended
- trusted-device revocation when a user is locked
- reactivation resetting a user into password-change onboarding
- hard-delete user removal with transcript/version cascade
- system-admin STT config provisioning, fetch, inspection, and delete for a selected team
- leader team STT selection and clear flow using admin-provisioned options
- STT browser model selection using provider-populated dropdowns instead of free-text overrides
- STT provisioning/selection route blocking for unauthenticated, ordinary-user, onboarding, and pending-MFA callers
- STT config validation for remote HTTPS-only and leader team-selection scope
- transcript owner-only access and version history
- transcript start creating the root for the current user and persisting `ingestion_mode`
- transcript list responses including the persisted `ingestion_mode`
- owner-only transcript detail fetch for browser polling
- owner-only live audio chunk queueing
- live audio chunk upload rejecting non-`live_chunked` transcripts
- duplicate live chunk sequence rejection
- sequence-aware live chunk worker application
- live chunk worker failure when no active team STT selection exists
- owner-only whole-file ingestion queueing
- whole-file ingestion rejecting transcripts in the wrong ingestion mode
- whole-file queueing failing early when no active team STT selection exists
- whole-file ingestion moving the transcript to `ready` after successful provider completion
- STT provider execution using the team config, Vault secret, and configured response text path
- queued STT jobs snapshotting the resolved provider/model so later team STT selection changes do not retarget already-uploaded audio
- STT config edits/deletes being blocked while queued or processing ingestion jobs still reference that config
- leader team-template create/update/delete scope
- user personal-template create/update/delete scope
- owner-only generated-document listing per transcript
- owner-only template-based note generation creating a transcript-version snapshot and generated-document row
- owner-only template generation using either OpenAI chat or Ollama chat provider adapters
- owner-only template generation now queues a generated-document job instead of blocking inline
- owner-only follow-up generation now queues a generated-document job using the same async worker path
- generated-document prompt snapshots surviving later template or quick-action deletion
- generated-document worker lazily creating or reusing a `redaction_runs` snapshot for the queued transcript version
- generated-document worker sending only redacted transcript text to the LLM and re-identifying the finished output before persistence
- generated-document worker failing closed when the LLM returns malformed or unknown PHI placeholders
- LLM config edits/deletes being blocked while queued or processing generated documents still reference that config
- server-side STT/LLM model validation rejecting API-submitted model names outside the provider-discovered list
- leader team-quick-action create/update/delete scope
- user personal-quick-action create/update/delete scope
- owner-only quick action generation now queues a generated-document job using the same async worker path and persists quick action provenance
- generated-document worker processing updates queued documents to `ready`, persists `provider_usage_events`, and logs metadata-only usage counts
- provider failure tests now verify sanitized provider HTTP/error metadata is persisted without logging prompts or output text
- generated-document generation route rate limiting per authenticated user
- transcript delete cascade removing generated documents

### Admin and browser UI

- bootstrap flow when the database is empty
- public `/request-access` form
- bootstrap redirect to onboarding
- onboarding QR code rendering for TOTP setup
- leader home page with request-review and direct-user-create tools
- leader home page suspend/reactivate controls for manageable users
- leader home page delete control for manageable users
- leader home page STT selection form
- leader home page STT selection clear flow
- leader home page LLM selection using provider-backed allowed-model controls
- user home page LLM preference using a populated dropdown instead of free-text
- owner transcription workspace at `/transcribe`
- owner transcription workspace file-upload form
- owner transcription workspace missing-STT error that names the team leader email when available
- owner transcription workspace sidebar session list and tabbed transcript shell
- owner transcription workspace post/redirect/get upload flow so refresh does not resubmit the form
- owner transcription workspace session header showing the resolved user LLM model instead of the raw team default when a user preference is active
- leader home page team-template management form
- user home page personal-template management form
- leader home page team-quick-action management form
- user home page personal-quick-action management form
- owner transcription workspace output-tab note generation flow
- owner transcription workspace follow-ups tab queueing a follow-up request into the same async generated-document pipeline
- owner transcription workspace follow-ups tab quick-action dropdown queueing a quick action into the same async generated-document pipeline
- localhost-only seeded dev-account access to generated-document redaction debug for manual verification that the outbound LLM path used the redacted transcript payload
- OpenAI Cloud STT inspection loading a server-side filtered model list into the browser form
- Ollama LLM inspection and save flow without a required API key for local hosts
- STT inspect pages rendering inferred values into the save form, not just the API inspection result
- admin page provisioned-endpoint add/edit/delete flow
- admin page active STT selection flow for the selected team
- browser manager-account routes redirecting unauthenticated requests to `/login`
- admin page showing teams, users, and account requests
- admin page protected-account marker for the current system-admin account
- admin page team-scoped STT config form
- admin page team-scoped STT inspection flow
- MFA challenge page and remember-browser option for completed users
- login form rate-limiting returning `429`
- login form rate-limiting returning a generic wait-and-retry page
- seeded dev-account login is allowed from localhost but rejected for non-local browser requests
- seeded localhost dev accounts can inspect a dev-only redaction debug panel in `/transcribe` for the latest note/follow-up without exposing original PHI values
- seeded dev-account API sessions are revoked if reused from a non-local request

### Auth unit tests

- password verification success
- password verification failure
- malformed stored hash rejection
- session/recovery-code hashing behavior

### Migrations

- `alembic upgrade head` builds the expected schema from scratch
- head schema includes account-request, session, trusted-device, MFA, and recovery-code tables
- migration behavior and database safety rules are documented in [dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md)

## Current notes

- Postgres-backed tests need real socket access to the local test database. In this environment that means running them outside the restricted sandbox.
- The STT and LLM browser forms use `provider_model` for HTML form posts, while the JSON API and persisted field remain `model_name` / `model_name_override`. That keeps the API stable while avoiding FastAPI/Pydantic protected-namespace warnings from generated form models.
