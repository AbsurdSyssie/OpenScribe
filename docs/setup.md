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
./start-dev.sh
```

`requirements-dev.txt` includes the runtime dependencies plus test tools. The requirements install the pinned `en_core_web_sm` model wheel; do not run a separate `spacy download` command. The persistent Docker image installs `requirements.txt` only.

`start-dev.sh`:

- validates local exposure settings;
- starts only the `postgres`, `redis`, and `vault` Compose services;
- initializes or unseals the local persistent Vault;
- applies Alembic migrations;
- optionally clears queued development Celery work;
- seeds reusable localhost-only development accounts by default;
- starts the web process with reload, one Celery worker, and Celery Beat.

Local Vault root-token and unseal material are stored under `.local/vault/`. They are ignored by Git and must not be used as production credentials.

For local Google or Microsoft login, register the HTTP localhost callback with the provider and set its response mode to `query`. The `form_post` mode requires HTTPS. Then use the hidden-input commands in [docker.md](docker.md#oidc-secrets-in-vault) to store each issued client secret in local Vault. Keep the corresponding client ID, callback URL, enable switch, and non-secret Vault reference in `.env`; do not put the client secret there.

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
- Admin: `http://127.0.0.1:8080/admin`
- Health: `http://127.0.0.1:8080/health`

Full normal-user and team-leader login lands on `/workspace`. Legacy `GET /home`, `/transcribe`, and `/settings` links redirect to canonical workspace routes. See [workspace.md](workspace.md).

## First account

When the database has no users:

1. open `/login`;
2. in production, set `BOOTSTRAP_ADMIN_TOKEN` in deployment secret storage and enter its value in the bootstrap form;
3. create the first system administrator through the bootstrap form;
4. complete password/TOTP onboarding;
5. use `/admin` to create teams and provision provider metadata.

After any user exists, bootstrap is disabled. Creation takes a database transaction advisory lock and rechecks the user count, so concurrent first-admin requests cannot both succeed. Other users are created by managers or from approved account requests. See [auth.md](auth.md).

## Development accounts

`start-dev.sh` seeds local test accounts by default when `DEV_SEED_TEST_ACCOUNTS=true`. Their values come from `DEV_TEST_*` variables in `.env.example`.

These accounts are restricted to localhost-only requests by application checks. Do not enable seeding on a shared or production instance. Set:

```env
DEV_SEED_TEST_ACCOUNTS=false
```

The persistent Docker runtime uses the separate `DOCKER_SEED_TEST_ACCOUNTS` switch, which defaults to `false`.

## Celery behavior

The default combined development worker consumes `control`, `generation`, and `ingestion`, with `CELERY_WORKER_CONCURRENCY=1`. Set `CELERY_WORKER_MODE=isolated` to start separate workers with control/generation/ingestion concurrency 1/2/1. Override the corresponding `CELERY_CONTROL_CONCURRENCY`, `CELERY_GENERATION_CONCURRENCY`, and `CELERY_INGESTION_CONCURRENCY` settings with positive integers. Invalid settings fail before service startup. Both modes use prefork and prefetch multiplier 1, with one Beat; a web, worker, or Beat exit stops the development runtime and shuts down its children. Check host resources before increasing concurrency; ten generation slots are unsuitable for a 1 CPU / 2 GB host without capacity assessment. See [environment.md](environment.md).

Beat schedules:

- task-dispatch outbox publication every 1 second;
- transcript retention cleanup every 10 seconds;
- transcript-audio cleanup retry every 10 seconds;
- failed-ingestion source-audio expiry every 10 seconds;
- security-audit event expiry every 10 seconds;
- legal-document version expiry every 10 seconds;
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

Each live chunk is limited to 24 MiB. The application uses bounded upload readers, but a public proxy/CDN must also set request-body caps so it rejects oversized bodies before forwarding them.

All values and variable names are in [environment.md](environment.md). Provider/accounting quotas can reject work before these transport limits.

## Local network exposure

The default stack publishes FastAPI, PostgreSQL, Redis, and Vault on loopback only.

To expose only the host FastAPI development server on a trusted local network:

```env
APP_HOST=0.0.0.0
DEV_ALLOW_REMOTE_BIND=true
DEV_FORWARDED_ALLOW_IPS=<reverse-proxy-ip>
```

`start-dev.sh` launches FastAPI's development server without the runtime
Uvicorn WebSocket message and queue caps. Keep this remote-bind option on a
trusted development network. For networked live-audio testing, use the capped
Docker runtime described in [docker.md](docker.md).

PostgreSQL, Redis, and Vault remain loopback-bound unless Compose is deliberately changed and `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true` is set. Do not use wildcard forwarded-header trust while the origin is directly reachable.

`DEV_FORWARDED_ALLOW_IPS` defaults to `127.0.0.1`. Set it to the exact reverse-proxy address only when the proxy is the sole path to the development server.

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
- Deepgram live recording stops with an error: search `.local/logs/deepgram-live.jsonl` for the recording's `diagnostic_id`; the `recording_failed` record shows the active stage and last completed stage. See [live STT diagnostics](live_stt.md#live-diagnostics). In Docker, inspect the `openscribe` container stdout with the documented Compose log command.
- Queued work never completes: confirm the Celery worker is running and consumes all configured queues.
- Durable cleanup/outbox fallback never runs: confirm Celery Beat is running.
- Setup/reset email disabled: configure `MAIL_TRANSPORT` and required sender/public URL values.
- Provider setup cannot discover models: use the provider-specific documentation and inspect application logs without copying credentials or raw provider responses into issues.
