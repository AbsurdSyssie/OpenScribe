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
- Vault dev address: `http://127.0.0.1:8200`
- Vault dev token: `root`
- cookie security mode: `auto`

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

This starts Docker services, loads `.env`, applies migrations, and runs the FastAPI dev server.

It also starts a local Celery worker by default so queued transcript-ingestion jobs are processed during manual testing.
Before launching, it now proactively stops any existing OpenScribe FastAPI dev server and Celery worker processes so stale workers do not keep consuming jobs with old Python code.
The default dev configuration keeps Postgres, Redis, and Vault on localhost while exposing FastAPI for reverse-proxied or off-box frontend access.
Before the server starts, `./start-dev.sh` now also checks the live Docker port bindings for Postgres, Redis, and Vault and prints an error to the terminal if any of them are published beyond localhost.

Important:

- if you change transcript/job enums, Celery task code, or other worker-loaded Python models, restart `./start-dev.sh`
- `./start-dev.sh` now replaces existing OpenScribe dev server and Celery worker processes automatically; set `DEV_RESTART_EXISTING_PROCESSES=false` only if you explicitly do not want that behavior
- `APP_HOST` now defaults to `0.0.0.0`
- `./start-dev.sh` allows the FastAPI frontend bind off-box by default; set `APP_HOST=127.0.0.1` and `DEV_ALLOW_REMOTE_BIND=false` if you want localhost-only app access
- `./start-dev.sh` also refuses non-local Docker port publication for Postgres, Redis, or Vault unless `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`
- otherwise the FastAPI app may be running newer code while the worker is still running stale imports
- in practice this can leave transcript-ingestion jobs stuck at `queued` or transcripts stuck at `transcribing` until the worker is restarted

By default, `./start-dev.sh` also seeds a reusable dev team and two dev accounts into the app database:

- team: `Dev Test Team`
- leader: `dev.leader@example.com` / `test1234`
- user: `dev.user@example.com` / `test1234`

These seeded accounts are:

- active
- onboarding-complete
- `mfa_required = false`
- `mfa_enabled = false`
- restricted to localhost requests only; non-local login attempts are rejected and any reused non-local session is revoked immediately
- on localhost, these seeded dev accounts also get a `/transcribe` redaction-debug view for the latest note/follow-up so PHI placeholdering can be verified during development without exposing that view to normal users

You can disable this behavior by setting:

```bash
DEV_SEED_TEST_ACCOUNTS=false
```

You can override the defaults with:

- `DEV_TEST_TEAM_NAME`
- `DEV_TEST_LEADER_EMAIL`
- `DEV_TEST_LEADER_PASSWORD`
- `DEV_TEST_USER_EMAIL`
- `DEV_TEST_USER_PASSWORD`

The current STT-config slice writes bearer tokens into Vault through:

- `VAULT_ADDR`
- `VAULT_TOKEN`
- `VAULT_KV_MOUNT`

The default local values in `.env.example` match the Docker dev Vault container.

The queued transcript-ingestion path uses:

- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_LOG_LEVEL`

Cookie security uses:

- `COOKIE_SECURE_MODE=auto` by default
- set `COOKIE_SECURE_MODE=always` on public HTTPS deployments if proxy/scheme handling is ambiguous

You can disable the worker startup in `./start-dev.sh` with:

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

The checked-in dev defaults now expose only the app itself off-box:

- Docker publishes Postgres, Redis, and Vault on `127.0.0.1` only
- FastAPI defaults to `APP_HOST=0.0.0.0`

If you want localhost-only app access instead, override it in `.env`:

```bash
APP_HOST=127.0.0.1
DEV_ALLOW_REMOTE_BIND=false
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

### First login for managed accounts

- log in with the temporary password
- the app redirects to `/onboarding`
- complete:
  - password change
  - TOTP setup
  - optional recovery code generation
- only then does normal app access unlock

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

### Whole-file ingestion caps

Whole-file uploads are bounded by both size and duration:

- raw upload size default: `25 MB`
- normalized whole-file duration default: `30 minutes`

These are configurable with:

- `WHOLE_FILE_MAX_UPLOAD_BYTES`
- `WHOLE_FILE_MAX_DURATION_SECONDS`

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
