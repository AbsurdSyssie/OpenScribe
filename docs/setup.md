# Local setup

This guide covers host-based development with `start-dev.sh`. For the persistent container runtime, use [docker.md](docker.md). Environment variable definitions and code defaults are maintained in [environment.md](environment.md).

## Requirements

- Python 3.12
- Docker with the Compose plugin
- `ffmpeg` and `ffprobe` on the host
- a browser with microphone support for recording workflows

PostgreSQL, Redis, and Vault run in Docker for host development. FastAPI, the Celery worker, and Celery Beat run in the local Python environment.

## First run

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m spacy download en_core_web_sm
./start-dev.sh
```

`requirements-dev.txt` includes the runtime dependencies plus test tools. The persistent Docker image installs `requirements.txt` only.

`start-dev.sh`:

- validates local exposure settings;
- starts only the `postgres`, `redis`, and `vault` Compose services;
- initializes or unseals the local persistent Vault;
- applies Alembic migrations;
- optionally clears queued development Celery work;
- seeds reusable localhost-only development accounts by default;
- starts the web process with reload, one Celery worker, and Celery Beat.

Local Vault root-token and unseal material are stored under `.local/vault/`. They are ignored by Git and must not be used as production credentials.

## Expected local settings

The checked-in sample uses:

```env
APP_ENV=local
APP_HOST=127.0.0.1
APP_PORT=8080
APP_PUBLIC_URL=http://127.0.0.1:8080
COOKIE_SECURE_MODE=auto
HSTS_SOURCE=app
DATABASE_URL=postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe
RATE_LIMIT_STORAGE_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/2
VAULT_ADDR=http://127.0.0.1:8200
VAULT_TOKEN_FILE=.local/vault/root-token
```

The application defaults to production behavior when `APP_ENV`, `ENVIRONMENT`, and `ENV` are all unset. Do not remove `APP_ENV=local` from a local configuration accidentally.

## Local URLs

- API docs: `http://127.0.0.1:8080/docs`
- Account request: `http://127.0.0.1:8080/request-access`
- Login/bootstrap: `http://127.0.0.1:8080/login`
- Onboarding: `http://127.0.0.1:8080/onboarding`
- MFA challenge: `http://127.0.0.1:8080/mfa/challenge`
- Permanent user workspace: `http://127.0.0.1:8080/workspace`
- Current normal-user compatibility landing: `http://127.0.0.1:8080/home`
- Admin: `http://127.0.0.1:8080/admin`
- Health: `http://127.0.0.1:8080/health`

`/transcribe` and `/settings` redirect to canonical workspace routes. Normal-user login still lands on `/home` during the transition. See [workspace.md](workspace.md).

## First account

When the database has no users:

1. open `/login`;
2. create the first system administrator through the bootstrap form;
3. complete password/TOTP onboarding;
4. use `/admin` to create teams and provision provider metadata.

After any user exists, bootstrap is disabled. Other users are created by managers or from approved account requests. See [auth.md](auth.md).

## Development accounts

`start-dev.sh` seeds local test accounts by default when `DEV_SEED_TEST_ACCOUNTS=true`. Their values come from `DEV_TEST_*` variables in `.env.example`.

These accounts are restricted to localhost-only requests by application checks. Do not enable seeding on a shared or production instance. Set:

```env
DEV_SEED_TEST_ACCOUNTS=false
```

The persistent Docker runtime uses the separate `DOCKER_SEED_TEST_ACCOUNTS` switch, which defaults to `false`.

## Celery behavior

The development worker consumes the `control`, `generation`, `ingestion`, and default `celery` queues. Beat schedules:

- task-dispatch outbox publication every 1 second;
- transcript retention cleanup every 10 seconds;
- transcript-audio cleanup retry every 10 seconds;
- provider-secret cleanup retry every 10 seconds;
- quota lifecycle processing every 10 seconds.

Disable both development worker and Beat startup with:

```env
DEV_START_CELERY=false
```

Queued asynchronous work will not complete without a worker, and durable periodic cleanup/outbox fallback will not run without Beat.

## Transactional email

Supported transports:

- `disabled`: no outbound email; managers use out-of-band temporary-password/setup procedures;
- `stdout`: prints message bodies only in local/test environments;
- `resend`: sends through the Resend API.

Local stdout example:

```env
MAIL_TRANSPORT=stdout
MAIL_FROM_ADDRESS=no-reply@example.com
APP_PUBLIC_URL=http://127.0.0.1:8080
```

Resend example:

```env
MAIL_TRANSPORT=resend
APP_PUBLIC_URL=https://your-openscribe.example.com
MAIL_FROM_ADDRESS=no-reply@your-verified-domain.example
MAIL_FROM_NAME=OpenScribe
RESEND_API_KEY=re_local_development_key
```

Test the active configuration:

```bash
source .venv/bin/activate
python scripts/send_test_email.py --to you@example.com
```

Do not commit API keys. Production should inject a secret or use a provisioned `RESEND_API_KEY_VAULT_REF`.

## Provider setup

System administrators provision credential-bearing STT, LLM, and de-identification provider rows. Team leaders select only from provisioned ready providers for their own team. Provider management does not grant transcript readability.

- STT: [stt-config.md](stt-config.md)
- LLM: [llm-providers.md](llm-providers.md)
- Gemini Enterprise: [gemini-enterprise-setup.md](gemini-enterprise-setup.md)

Gemini Enterprise setup is controlled by `ENABLE_GEMINI_ENTERPRISE_PROVIDER`, default `true`. Setting it to `false` hides and rejects new setup while preserving authorized use of existing persisted configs.

## Audio limits

Default whole-file safeguards:

- individual upload: 200 MiB;
- individual normalized duration: 4 hours;
- burst: one request per 5 seconds;
- daily: 100 requests;
- hourly aggregate: 200 MiB and 4 hours;
- ffprobe timeout: 15 seconds;
- ffmpeg timeout: 1,800 seconds;
- synchronous STT timeout: 14,400 seconds.

All values and variable names are in [environment.md](environment.md). Provider/accounting quotas can reject work before these transport limits.

## Local network exposure

The default stack publishes FastAPI, PostgreSQL, Redis, and Vault on loopback only.

To expose only the host FastAPI development server on a trusted local network:

```env
APP_HOST=0.0.0.0
DEV_ALLOW_REMOTE_BIND=true
DEV_FORWARDED_ALLOW_IPS=<reverse-proxy-ip>
```

PostgreSQL, Redis, and Vault remain loopback-bound unless Compose is deliberately changed and `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true` is set. Do not use wildcard forwarded-header trust while the origin is directly reachable.

For persistent Docker reverse-proxy configuration, use [docker.md](docker.md), not the host-development switches.

## Database reset

A reset must target `DATABASE_URL`, not `TEST_DATABASE_URL`. Prefer deleting only a disposable local instance rather than copying a hard-coded table list from documentation, because the schema evolves.

For a fully disposable persistent Compose instance:

```bash
docker compose --profile runtime down --volumes
```

This destroys PostgreSQL, Redis, Vault, and bootstrap volumes. It is irreversible and must never be used against an instance whose data must be retained.

For host development, stop the processes and use normal PostgreSQL/Alembic tooling after verifying the database name. Clear browser cookies or use a private window before bootstrapping again.

## Tests

```bash
source .venv/bin/activate
export APP_ENV=test
export COOKIE_SECURE_MODE=auto
export HSTS_SOURCE=app
pytest -q
```

Database-test safety and xdist behavior are documented in [testing.md](testing.md) and [dbtesting.md](dbtesting.md).

## Troubleshooting

- `COOKIE_SECURE_MODE=always is required in production`: set `APP_ENV=local` for local development, or configure production HTTPS and `COOKIE_SECURE_MODE=always`.
- CSRF/Vault startup failure: provide a stable `CSRF_SECRET`, or make local Vault available with KV-v2 and the token file.
- Audio normalization/probe errors: install `ffmpeg` and verify both `ffmpeg` and `ffprobe` are on `PATH`.
- Queued work never completes: confirm the Celery worker is running and consumes all configured queues.
- Durable cleanup/outbox fallback never runs: confirm Celery Beat is running.
- Setup/reset email disabled: configure `MAIL_TRANSPORT` and required sender/public URL values.
- Provider setup cannot discover models: use the provider-specific documentation and inspect application logs without copying credentials or raw provider responses into issues.
