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
- starts a `file_upload` transcript
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
- server-side security log emission on rate-limit hits
- password change + TOTP + QR-assisted setup + recovery-code completion flow
- session revocation when a user is locked
- session revocation when a user is suspended
- trusted-device revocation when a user is locked
- reactivation resetting a user into password-change onboarding
- hard-delete user removal with transcript/version cascade
- leader STT config creation and fetch for the leader's own team
- system-admin STT config creation and fetch for a selected team
- leader STT OpenAPI inspection and inferred-field response
- STT config route blocking for unauthenticated, ordinary-user, onboarding, and pending-MFA callers
- STT config validation for remote HTTPS-only and leader team scope
- transcript owner-only access and version history
- transcript start creating the root for the current user and persisting `ingestion_mode`
- transcript list responses including the persisted `ingestion_mode`
- owner-only live audio chunk upload
- live audio chunk upload rejecting non-`live_chunked` transcripts
- live audio chunk upload rejecting teams without an active STT config
- owner-only whole-file ingestion
- whole-file ingestion rejecting transcripts in the wrong ingestion mode
- whole-file ingestion rejecting teams without an active STT config
- whole-file ingestion moving the transcript to `ready` after successful provider completion
- STT provider execution using the team config, Vault secret, and configured response text path

### Admin and browser UI

- bootstrap flow when the database is empty
- public `/request-access` form
- bootstrap redirect to onboarding
- onboarding QR code rendering for TOTP setup
- leader home page with request-review and direct-user-create tools
- leader home page suspend/reactivate controls for manageable users
- leader home page delete control for manageable users
- leader home page STT config form
- leader home page STT inspect-before-save flow
- OpenAI Cloud STT inspection loading a server-side filtered model list into the browser form
- STT inspect pages rendering inferred values into the save form, not just the API inspection result
- browser manager-account routes redirecting unauthenticated requests to `/login`
- admin page showing teams, users, and account requests
- admin page protected-account marker for the current system-admin account
- admin page team-scoped STT config form
- admin page team-scoped STT inspection flow
- MFA challenge page and remember-browser option for completed users
- login form rate-limiting returning `429`
- login form rate-limiting returning a generic wait-and-retry page

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
- The STT browser forms use `provider_model` for HTML form posts, while the JSON API and persisted field remain `model_name`. That keeps the API stable while avoiding FastAPI/Pydantic protected-namespace warnings from generated form models.
