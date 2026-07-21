# Setup

## Start infrastructure

```bash
docker compose up -d
```

## Create or activate the virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Native PHI redaction now depends on Presidio plus spaCy:

```bash
source .venv/bin/activate
python -m spacy download en_core_web_sm
```

The app and tests use separate databases by default:

- app DB: `ambient_scribe`
- test DB: `ambient_scribe_test`
- app rate-limit store: `redis://localhost:6379/0`
- test rate-limit store: `redis://localhost:6379/15`
- Celery broker/result backend: `redis://localhost:6379/2`
- local Vault address: `http://127.0.0.1:8200`
- local Vault token file: `.local/vault/root-token`
- app environment: `local`
- CSRF secret: local/test uses a development-only fallback when unset; production uses `CSRF_SECRET` or auto-bootstraps a stable Vault KV secret
- cookie security mode: `auto`

Provider-call safeguards are deployment-configurable and remain active even
when a user's system-admin quota is unlimited. Defaults are:

- `LIVE_CHUNK_UPLOAD_RATE_LIMIT=1/second`
- `WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT=1/5 seconds`
- `WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT=100/day`
- `LLM_GENERATION_BURST_RATE_LIMIT=20/3 minutes`
- `LLM_GENERATION_DAILY_RATE_LIMIT=200/day`
- `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS=3600`
- `WHOLE_FILE_HOURLY_UPLOAD_BYTES=209715200` (200 MiB/hour)
- `WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS=14400` (4 hours)

Login, MFA, and public account-request limits remain unchanged. Per-user STT/LLM
quotas are separate system-admin-only expenditure controls; these safeguards
cover users whose quota remains unlimited. Normal user and team-leader UI does
not show quota policy or consumption.

## Apply database migrations

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
alembic upgrade head
```

## Run the app

```bash
./start-dev.sh
```

This starts Docker services, initializes or unseals the persistent local Vault, loads `.env`, applies migrations, and runs the FastAPI dev server.

It also starts a local Celery worker and Celery Beat scheduler by default. Queued transcript-ingestion jobs are processed during manual testing. Beat publishes the task-dispatch outbox every 1 second; transcript retention, retry-audio Vault cleanup, provider-secret Vault cleanup, and quota lifecycle cleanup remain scheduled every 10 seconds.
The dev worker starts with all three queues (`control`, `generation`, `ingestion`) on a single process; production deployments should run separate workers per queue for isolation.
Before launching, it now proactively stops any existing OpenScribe FastAPI dev server, Celery worker, and Celery Beat processes so stale processes do not keep consuming or scheduling jobs with old Python code.
It also checks the configured FastAPI port before starting Celery or Brave; if another process still owns the port, it exits with a direct `APP_PORT`/stop-process message instead of leaving a worker running after server startup fails.
It exports derived dev defaults such as `APP_PORT` and `APP_BIND_HOST` before running child Python checks, so missing optional `.env` values still use the documented defaults.
It also purges stale queued Celery tasks from all queues by default before starting the fresh dev worker, so old Redis jobs do not replay against newer code or deleted dev rows.
The default dev configuration keeps FastAPI, Postgres, Redis, and Vault on localhost. Off-box FastAPI access requires explicit `APP_HOST=0.0.0.0` and `DEV_ALLOW_REMOTE_BIND=true` configuration.
Before the server starts, `./start-dev.sh` now also checks the live Docker port bindings for Postgres, Redis, and Vault and prints an error to the terminal if any of them are published beyond localhost.

Important:

- if you change transcript/job enums, Celery task code, or other worker-loaded Python models, restart `./start-dev.sh`
- `./start-dev.sh` now replaces existing OpenScribe dev server and Celery worker processes automatically; set `DEV_RESTART_EXISTING_PROCESSES=false` only if you explicitly do not want that behavior
- FastAPI reload watches `app/` only. Prototype files under `transcriber_changes/`, tests, and docs are served/read directly and no longer trigger disruptive Python reloads.
- expired transcript roots become inaccessible at their fixed `retention_expires_at` timestamp; cleanup physically deletes roots and cascading transcript-derived children on the next 10-second scheduler pass
- retention cleanup drains expired roots in locked 100-row batches; queued cleanup messages expire after 10 seconds so a stopped worker does not later replay stale scheduler backlog
- production deployments must run separate Celery workers per queue (`control`, `generation`, `ingestion`) and a shared Beat scheduler with the same application configuration; workers without Beat do not schedule retention or durable Vault cleanup
- queue routing is defined in `app/celery_app.py`: `control` handles retention/Vault/outbox/quota lifecycle tasks, `generation` handles document generation, `ingestion` handles transcript ingestion. The `task_routes` dict ensures tasks land on the correct queue regardless of which worker picks them up.
- outbox dispatch fast-path: after committing a transcript version or generation, the service attempts an immediate `publish_task_dispatch` for the associated outbox row instead of waiting for Beat. Beat remains the fallback and publishes the remainder every 1 second.
- completion-triggered dispatch: when a transcript ingestion job completes and the transcript transitions to `ready`, any pending generation dispatches for that transcript are published immediately, eliminating the 2-second retry poll on the generation worker.
- quota accounting also requires both processes: Beat publishes pending task-dispatch outbox rows every 1 second and terminalizes stale quota reservations/submissions every 10 seconds; workers execute those tasks and provider work. Each publisher transaction claims/publishes one outbox row, so concurrent publishers cannot reuse released batch locks. Outbox publish retries are safe through deterministic task IDs, and lifecycle cleanup locks normal owner/source parents before attempts (never outbox-first), safely cancels stale undispatched reservations, or conservatively settles stale submitted token attempts.
- worker timing: both `process_transcript_ingestion_job` and `process_generated_document` stamp `worker_received_at` on the associated row (transcript job or generated document) before running provider work, giving visibility into Celery scheduling latency.
- if port `APP_PORT` is owned by an unrelated process, stop it or change `APP_PORT` in `.env`
- `APP_HOST` defaults to `127.0.0.1`
- `./start-dev.sh` keeps the FastAPI frontend localhost-only by default; off-box access requires explicit `APP_HOST=0.0.0.0` and `DEV_ALLOW_REMOTE_BIND=true`
- `./start-dev.sh` also refuses non-local Docker port publication for Postgres, Redis, or Vault unless `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`
- `./start-dev.sh` stores the local Vault root token and unseal key in `.local/vault/`; that directory is ignored by git and should be treated as local secret material
- `./start-dev.sh` now purges queued Celery tasks before the dev worker starts; set `DEV_PURGE_CELERY_QUEUE=false` only if you intentionally want to keep the existing dev queue
- otherwise the FastAPI app may be running newer code while the worker is still running stale imports
- in practice this can leave transcript-ingestion jobs stuck at `queued` or transcripts stuck at `transcribing` until the worker is restarted

### Quota accounting deployment and rollback

For quota migration `c1d2e3f4a5b6`, use this production order:

1. Stop or drain existing Celery Beat and workers before schema change; old workers must not run against the new dispatch/quota flow.
2. Deploy application code and run `alembic upgrade head`.
3. Start workers with new code, then start Beat with same configuration. Confirm both `openscribe.process_task_dispatch_outbox` and `openscribe.process_quota_lifecycle` run every 10 seconds.

Do not downgrade this migration after quota use begins. Downgrade blocks if any user quota limit is populated or `user_quota_policy_events`, `provider_attempts`, or `task_dispatch_outbox` has rows. Treat rollback as an escalation/planned recovery; do not delete accounting or dispatch records merely to bypass blocker.

By default, `./start-dev.sh` also seeds a reusable dev team and three dev accounts into the app database:

- team: `Dev Test Team`
- admin: `dev.admin@example.com` / `test1234`
- leader: `dev.leader@example.com` / `test1234`
- user: `dev.user@example.com` / `test1234`

These seeded accounts are:

- active
- onboarding-complete
- `mfa_required = false`
- `mfa_enabled = false`
- restricted to localhost requests only; non-local login attempts are rejected and any reused non-local session is revoked immediately
- the seeded admin is system-admin-only, has no team, and does not receive a user content encryption key
- on localhost, these seeded dev accounts also get a `/transcribe` redaction-debug view for the latest note/follow-up so PHI placeholdering can be verified during development without exposing that view to normal users
- `Sectioned EMIS note`, `Patient follow-up message`, and `Referral letter` team assets are hard-coded into the dev seed and recreated if missing

You can disable this behavior by setting:

```bash
DEV_SEED_TEST_ACCOUNTS=false
```

You can override the defaults with:

- `DEV_TEST_TEAM_NAME`
- `DEV_TEST_ADMIN_EMAIL`
- `DEV_TEST_ADMIN_PASSWORD`
- `DEV_TEST_LEADER_EMAIL`
- `DEV_TEST_LEADER_PASSWORD`
- `DEV_TEST_USER_EMAIL`
- `DEV_TEST_USER_PASSWORD`

The current STT-config slice writes bearer tokens into Vault through:

- `VAULT_ADDR`
- `VAULT_TOKEN`
- `VAULT_TOKEN_FILE`
- `VAULT_KV_MOUNT`
- `VAULT_TRANSIT_MOUNT`
- `VAULT_USER_CONTENT_KEK_KEY_NAME`

The default local values in `.env.example` now match the persistent local Vault bootstrap flow. `VAULT_TOKEN_FILE` points at `.local/vault/root-token`, and `VAULT_TOKEN` is intentionally left blank for local development.

The current transcript-at-rest encryption slice also depends on:

- `cryptography`
- `hvac`

Current owner-content behavior:

- transcript drafts in `transcripts.current_draft_text_encrypted` are written as AES-GCM envelopes at rest
- committed transcript versions in `transcript_versions.text_encrypted` are written as AES-GCM envelopes at rest
- STT job result text in `transcript_ingestion_jobs.result_text_encrypted` is written as AES-GCM envelopes at rest
- normal content-owning users get one wrapped DEK recorded in `user_encryption_keys`
- Vault Transit wraps and unwraps those DEKs; Vault KV still stores provider credentials and retry-audio refs

Vault bootstrap behavior:

- normal owner-content reads and writes assume the configured KV mount, Transit mount, and `VAULT_USER_CONTENT_KEK_KEY_NAME` key already exist
- `./start-dev.sh` now initializes or unseals a persistent local Vault, restores the saved local root token/unseal key, and bootstraps the `secret/` KV-v2 mount plus the Transit KEK before FastAPI and Celery start
- if you change local Vault storage or intentionally wipe the Vault volume, expect any previously wrapped DEKs to become unreadable unless you also preserved the old Vault state
- use `python scripts/reset_unreadable_owner_content.py` to audit local content-owning accounts after a Vault reset, and rerun with `--apply` only if you explicitly want to delete unreadable transcript-derived content and issue fresh DEKs
- in production, pre-provision the Transit mount and KEK during infrastructure bootstrap and give the runtime app and worker only the Transit permissions they actually need (`datakey`, `decrypt`, `rewrap` as appropriate)
- least-privilege runtime tokens should not need `sys/mounts` or Transit key-management permissions just to read transcript-derived content

The queued transcript-ingestion path uses:

- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_LOG_LEVEL`

Cookie security uses:

- `APP_ENV=local|test|production`
- `CSRF_SECRET` or `SECRET_KEY` for an explicit CSRF signing secret
- `CSRF_SECRET_VAULT_REF` optional, defaulting to `secret:openscribe/platform/csrf` when Vault auto-bootstrap is used
- `COOKIE_SECURE_MODE=auto` by default
- `HSTS_SOURCE=app|proxy|proxy_static_fallback`, defaulting to `app`
- `PUBLIC_API_DOCS=true|false`, optional; unset means public outside production and system-admin-only in production
- production startup requires `COOKIE_SECURE_MODE=always`
- local development should use `APP_ENV=local` with `COOKIE_SECURE_MODE=auto`

Security header ownership:

- OpenScribe always emits `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, CSP, COOP, CORP, COEP, and `Permissions-Policy`.
- `HSTS_SOURCE=app` makes OpenScribe emit `Strict-Transport-Security: max-age=31536000; includeSubDomains` on all HTTPS responses.
- `HSTS_SOURCE=proxy` disables app HSTS emission so Cloudflare, nginx, Caddy, or another trusted edge can be the only HSTS source for every response.
- `HSTS_SOURCE=proxy_static_fallback` keeps dynamic responses proxy-owned but adds app HSTS to `/static/` responses. Use this when ZAP shows dynamic pages have one proxy HSTS header but static assets lack HSTS.
- Use exactly one HSTS owner per response. Duplicate HSTS is non-compliant and was flagged by ZAP.
- If Cloudflare has HSTS enabled for all responses, set `HSTS_SOURCE=proxy` in the OpenScribe runtime environment.
- If Cloudflare covers dynamic pages but static assets lack HSTS, set `HSTS_SOURCE=proxy_static_fallback`.
- If no proxy/edge emits HSTS, keep `HSTS_SOURCE=app`.

Production CSRF secret behavior:

- if `CSRF_SECRET` or `SECRET_KEY` is set, that value is used
- otherwise OpenScribe reads or creates a stable random secret in Vault KV-v2 at `CSRF_SECRET_VAULT_REF`, or `secret:openscribe/platform/csrf` by default
- the Vault secret field is `csrf_secret`
- Vault creation uses create-if-absent semantics so multiple app instances can start safely
- if Vault is unavailable and no explicit secret is set, startup fails intentionally

If startup fails with `COOKIE_SECURE_MODE=always is required in production`, the app is treating the environment as production. For local development, set:

```env
APP_ENV=local
COOKIE_SECURE_MODE=auto
```

For production HTTPS, set:

```env
APP_ENV=production
COOKIE_SECURE_MODE=always
PUBLIC_API_DOCS=false
# Use exactly one HSTS owner.
# If Cloudflare/reverse proxy emits HSTS for every response:
HSTS_SOURCE=proxy
# If Cloudflare/reverse proxy emits HSTS for dynamic pages but not /static/ assets:
# HSTS_SOURCE=proxy_static_fallback
# If OpenScribe should emit HSTS instead:
# HSTS_SOURCE=app
```

For local reverse-proxy access through nginx/Nginx Proxy Manager, `start-dev.sh` passes `--proxy-headers` and trusts only `DEV_FORWARDED_ALLOW_IPS`, defaulting to `192.168.1.234`. Set this to the reverse proxy host IP if it changes. Do not use `*` unless the FastAPI port is unreachable except from the proxy.

If production cannot use Vault auto-bootstrap, also set:

```env
CSRF_SECRET=<strong random secret>
```

Transactional email uses:

- `MAIL_TRANSPORT=disabled|stdout|resend`
- `APP_ENV=local|test|production` for stdout transport safety
- `APP_PUBLIC_URL`
- `MAIL_FROM_ADDRESS`
- `MAIL_FROM_NAME`
- `MAIL_REPLY_TO` optional
- `RESEND_API_KEY` for local development only, or `RESEND_API_KEY_VAULT_REF` once production secret storage is wired

Current behavior:

- `disabled` keeps the existing manual setup path with manager-created temporary passwords and no outbound email.
- `stdout` writes transactional email bodies to server stdout for local development and tests only when `APP_ENV`, `ENVIRONMENT`, or `ENV` is `local`, `dev`, `development`, `test`, or `testing`.
- `resend` sends through the Resend Email API. Production deployments should verify their Resend domain and use a sending-restricted API key before enabling it.

To test Resend after editing `.env`:

```bash
source .venv/bin/activate
python scripts/send_test_email.py --to you@example.com
```

For `MAIL_TRANSPORT=resend`, the test script uses `RESEND_API_KEY` or `RESEND_API_KEY_VAULT_REF`, `MAIL_FROM_ADDRESS`, `MAIL_FROM_NAME`, `MAIL_REPLY_TO`, and `APP_PUBLIC_URL`.
Do not commit the Resend API key.

You can disable both Celery worker and Beat startup in `./start-dev.sh` with:

```bash
DEV_START_CELERY=false
```

## Local URLs

- API docs: `http://127.0.0.1:8080/docs`
- Account request page: `http://127.0.0.1:8080/request-access`
- Login / bootstrap: `http://127.0.0.1:8080/login`
- Onboarding: `http://127.0.0.1:8080/onboarding`
- MFA challenge: `http://127.0.0.1:8080/mfa/challenge`
- User home: `http://127.0.0.1:8080/home`
- Admin UI: `http://127.0.0.1:8080/admin`

## Local network exposure

The checked-in dev defaults keep every service localhost-only:

- Docker publishes Postgres, Redis, and Vault on `127.0.0.1` only
- FastAPI defaults to `APP_HOST=127.0.0.1`

To expose only FastAPI on the local network, opt in explicitly in `.env`:

```bash
APP_HOST=0.0.0.0
DEV_ALLOW_REMOTE_BIND=true
```

That only affects the FastAPI bind and the startup guard. Postgres, Redis, and Vault remain localhost-only unless you deliberately change the Docker port bindings and enable `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`.

## Current manager STT configuration UI

- leaders manage only their own team's active STT selection from `/home`
- system admins provision and manage a selected team's STT endpoint rows from `/admin?team_id=<team_uuid>`
- system admins also manage the active team STT selection from `/admin?team_id=<team_uuid>`
- the UI accepts metadata and a replacement bearer token only in the admin provisioning flow
- the UI does not reveal the stored token or the raw Vault reference
- the admin inspect flow preserves the just-entered token only for the current rendered page so the immediate save can reuse it without retyping

## Current manager LLM configuration UI

- leaders manage only their own team's active LLM selection from `/home`
- normal team users may set their own preferred default LLM model from `/home`
- system admins provision and manage a selected team's LLM provider rows from `/admin?team_id=<team_uuid>`
- system admins also manage the active team LLM selection from `/admin?team_id=<team_uuid>`
- the implemented adapter families are `openai_chat`, `bedrock_chat`, and `ollama_chat`
- leader/admin team selection now uses provider-backed model controls instead of free-text:
  - leaders/admins choose a provider
  - choose which provider models are visible to team users
  - choose one team default model from that visible subset
- normal users choose their own default model from a populated dropdown of the leader-approved subset
- the UI accepts metadata and a replacement API key only in the admin provisioning flow
- the UI does not reveal the stored token or the raw Vault reference
- remote LLM endpoints must use `https`; `http` is accepted only for localhost/private-network hosts
- the admin inspect flow preserves the just-entered API key only for the current rendered page so the immediate save can reuse it without retyping
- Amazon Bedrock provisioning uses the OpenAI-compatible Bedrock Mantle endpoint format `https://bedrock-mantle.<region>.api.aws/v1`
- the admin Bedrock form now accepts a region and derives the standard Mantle base URL from it; the default region is `eu-west-2`
- local Ollama defaults to `http://localhost:11434`; model discovery uses `/api/tags`

## First access

If the database has no users:

- open `/login`
- use the bootstrap form to create the first system admin
- bootstrap signs you in and sends you to `/onboarding`
- complete TOTP enrollment before using `/admin`

After the first user exists:

- bootstrap is disabled
- users either log in normally or submit `/request-access`

## Managed account workflow

### Request access

- open `/request-access`
- submit name, email, team name, and optional details
- a leader for that team or a system admin can review the request

### Direct account creation

- leaders can create users for their own team from `/home`
- system admins can create users from `/admin`
- creators set a temporary password and share it out-of-band
- when transactional email is configured, managers can also send setup links from user-management actions

### First login for managed accounts

- log in with the temporary password
- the app redirects to `/onboarding`
- complete:
  - password change
  - TOTP setup
  - optional recovery code generation
- only then does normal app access unlock

### Account recovery

- users can open `/forgot-password` to request a password reset link only when outbound mail is configured
- when `MAIL_TRANSPORT=disabled`, the login page hides self-service reset and tells users to contact a team leader or system administrator
- reset requests show the same generic response whether the email exists or not
- managers can send setup links, generate a one-time visible temporary password, reset MFA, or recover password+MFA from user-management actions
- password reset and MFA reset revoke sessions and trusted-device trust

### Later logins for managed accounts

- after onboarding completes, email + password may still redirect to `/mfa/challenge`
- entering a valid TOTP code completes the login
- users may remember the current browser for 24 hours, which skips the TOTP step only within that freshness window

## Reset local auth state and bootstrap again

Use this when you want to wipe local app data and create a fresh first system-admin account.

### What to clear

The reset must target the app database from `DATABASE_URL`, not the test database from `TEST_DATABASE_URL`.

### Browser/session reset

The app uses an opaque session cookie. Clear it before retrying:

- sign out through `/logout`, or
- open `/login` in a private/incognito window, or
- clear cookies for `127.0.0.1:8080` if needed

Browser flows also use a separate CSRF cookie:

- cookie name: `openscribe_csrf`
- browser forms submit it as `_csrf_token`
- browser JavaScript requests submit it as `X-CSRF-Token`

### Force Argon2id password rotation

This dev repo does not keep plaintext passwords, so non-Argon2id hashes cannot be converted directly.
To force the local cutover, rotate every non-Argon2id user to a random temporary Argon2id password:

```bash
source .venv/bin/activate
python scripts/force_argon2id_password_rotation.py --confirm-dev-password-rotation
```

The script prints temporary passwords once, marks affected users for password change, revokes active sessions/trusted devices, and preserves existing TOTP/recovery-code state.

### Whole-file ingestion caps

Whole-file uploads are bounded by both size and duration:

- raw upload size default: `200 MB`
- normalized whole-file duration default: `4 hours`

These are configurable with:

- `WHOLE_FILE_MAX_UPLOAD_BYTES`
- `WHOLE_FILE_MAX_DURATION_SECONDS`

Long whole-file processing has separate timeout knobs:

- `AUDIO_FFMPEG_TIMEOUT_SECONDS` default: `1800`
- `STT_TRANSCRIPTION_TIMEOUT_SECONDS` default: `14400`

### Gemini Enterprise credentials

Enable `aiplatform.googleapis.com` and grant the runtime identity `roles/aiplatform.user` or a narrower approved custom role in the wizard project. For production on Google Cloud, prefer an attached service account. For local development, configure ADC with `gcloud auth application-default login`. If ADC deliberately uses a different quota project, the identity also needs `serviceusage.services.use` and the API enabled on that quota project.

Workloads outside Google Cloud should configure Workload Identity Federation in the deployment environment, then select Application Default Credentials in OpenScribe. Do not upload WIF `external_account` files through the admin wizard. Service-account key JSON is an advanced fallback and is stored in Vault.

Complete bare-metal, current Compose-layout, fully-containerized app/worker, networking, location, verification, and troubleshooting instructions are in [Gemini Enterprise setup](gemini-enterprise-setup.md).

Rollout control: `ENABLE_GEMINI_ENTERPRISE_PROVIDER=false` hides and rejects new Gemini provider submissions. Standard tests mock all Google calls; use a separate low-privilege staging project for live smoke checks in `global` and the intended production location.

### Database reset

From the project directory:

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
```

Clear the app data in dependency order:

```sql
TRUNCATE TABLE user_recovery_codes, user_mfa_methods, user_trusted_devices, user_sessions, transcript_versions, transcripts, account_requests, users, teams RESTART IDENTITY CASCADE;
```

### Expected result after reset

After the reset:

- `/login` shows `Create first system admin`
- submitting that form creates the new bootstrap user
- the new bootstrap user lands in `/onboarding`
