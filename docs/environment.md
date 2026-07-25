# Environment variables

OpenScribe reads configuration from process environment variables. For local use,
copy `.env.example` to `.env`. The host-based development script sources that
file directly; Docker Compose also loads it for the `openscribe` service.

The persistent Compose runtime overrides only the addresses that must use Docker
service DNS (`postgres`, `redis`, and `vault`) plus the container's internal web
bind. All other values still come from `.env`.

## Configuration rules

- Never commit `.env` or put real credentials in `.env.example`.
- Treat blank values as intentionally unset. Defaults are documented below.
- Prefer `CSRF_SECRET`; `SECRET_KEY` is retained only as a compatibility alias.
- `APP_SECRET_KEY` is not a runtime setting and should not be used.
- Production must not use the local Compose database password, Vault root token,
  seeded test accounts, or development-only CSRF fallback.
- Settings are loaded when a process starts. Restart the web process, Celery
  worker, and Celery Beat after changing runtime configuration.

## Application and HTTP

| Variable | Default/example | Purpose |
| --- | --- | --- |
| `APP_ENV` | `local` | Runtime mode. Use `production` only with production cookie, secret, proxy, database, Redis, and Vault configuration. |
| `APP_HOST` | `127.0.0.1` | Host bind used by `start-dev.sh`. The container binds internally to `0.0.0.0`. |
| `APP_PORT` | `8080` | Host dev port and Docker-published port. The container's internal port remains `8080`. |
| `APP_PUBLIC_URL` | `http://127.0.0.1:8080` | Canonical browser URL used in generated links and transactional email. |
| `CSRF_SECRET` | unset | Preferred explicit CSRF signing secret. Required in production unless Vault secret bootstrap is available. |
| `SECRET_KEY` | unset | Compatibility alias for `CSRF_SECRET`. Do not set both to different values. |
| `CSRF_SECRET_VAULT_REF` | unset | Optional Vault KV reference for the platform CSRF secret. When production has no explicit secret, OpenScribe uses Vault bootstrap. |
| `COOKIE_SECURE_MODE` | `auto` | `auto`, `always`, or `never`. Production requires `always`. |
| `HSTS_SOURCE` | `app` | `app`, `proxy`, or `proxy_static_fallback`; selects the single owner of HSTS headers. |
| `PUBLIC_API_DOCS` | unset | Unset means public outside production and system-admin-only in production. Explicitly set `true` or `false` to override. |
| `AUDIT_TRUST_X_FORWARDED_FOR` | `false` | Trust sanitized `X-Forwarded-For` values for audit IP capture. Enable only when direct origin access is blocked. |
| `AUDIT_TRUST_CLOUDFLARE` | `false` | Trust sanitized Cloudflare client-IP headers. Enable only behind the configured Cloudflare path. |

`ENVIRONMENT` and `ENV` are accepted as fallbacks when `APP_ENV` is unset, but
new deployments should use `APP_ENV` consistently.

## Database, Redis, and Celery

| Variable | Local default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe` | Primary SQLAlchemy database URL. Compose overrides the host to `postgres`. |
| `TEST_DATABASE_URL` | `postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe_test` | Test database URL. Never point this at the live application database. |
| `RATE_LIMIT_STORAGE_URL` | `redis://localhost:6379/0` | Redis database used for application rate limiting. Compose overrides the host to `redis`. |
| `TEST_RATE_LIMIT_STORAGE_URL` | `redis://localhost:6379/15` | Isolated test rate-limit storage. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/2` | Celery broker. Compose overrides the host to `redis`. |
| `CELERY_RESULT_BACKEND` | broker URL | Celery result backend. |
| `CELERY_LOG_LEVEL` | `INFO` | Worker and Beat log level. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Test-only synchronous execution switch. Do not enable in a persistent runtime. |

The local Compose stack enables Redis append-only persistence. PostgreSQL remains
the authoritative application store; Redis persistence protects queued work and
rate-limit state across normal restarts but is not a substitute for database
backups.

## Vault

| Variable | Local default | Purpose |
| --- | --- | --- |
| `VAULT_ADDR` | `http://127.0.0.1:8200` | Vault API address. Compose overrides it to `http://vault:8200`. |
| `VAULT_TOKEN` | unset | Direct Vault token. Prefer a mounted token file or deployment secret injection. |
| `VAULT_TOKEN_FILE` | `.local/vault/root-token` | File containing the Vault token. Compose mounts persistent bootstrap material at `/app/.local/vault/root-token`. |
| `VAULT_KV_MOUNT` | `secret` | KV-v2 mount used for provider and platform secrets. |
| `VAULT_TRANSIT_MOUNT` | `transit` | Transit mount used to wrap and unwrap user content DEKs. |
| `VAULT_USER_CONTENT_KEK_KEY_NAME` | `openscribe-user-content-kek` | Transit key used for user-content DEKs. |
| `LOCAL_VAULT_WAIT_TIMEOUT_SECONDS` | `90` | Maximum bootstrap wait for local Vault. |
| `LOCAL_VAULT_WAIT_RETRY_INTERVAL_SECONDS` | `1` | Local Vault bootstrap retry interval. |

The local bootstrap stores a root token and unseal key. This is suitable only for
a controlled local or single-host development environment. Production should
pre-provision Vault mounts and keys and provide least-privilege runtime tokens.
Vault storage and the PostgreSQL data that references wrapped keys must be backed
up and restored as a consistent deployment set.

## Transactional email

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAIL_TRANSPORT` | `disabled` | `disabled`, `stdout`, or `resend`. `stdout` is restricted to local/test modes. |
| `MAIL_FROM_ADDRESS` | `no-reply@example.com` | Sender address; required when mail is enabled. |
| `MAIL_FROM_NAME` | `OpenScribe` | Sender display name. |
| `MAIL_REPLY_TO` | unset | Optional reply-to address. |
| `RESEND_API_KEY` | unset | Plaintext Resend key, acceptable only for controlled local development. |
| `RESEND_API_KEY_VAULT_REF` | unset | Vault reference for the Resend key. Prefer this or deployment secret injection outside local development. |

`APP_PUBLIC_URL` is also required when mail is enabled so account and recovery
links point to the correct instance.

## Provider safeguards and audio limits

| Variable | Default | Purpose |
| --- | --- | --- |
| `LIVE_CHUNK_UPLOAD_RATE_LIMIT` | `1/second` | Live audio chunk request rate limit. |
| `WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT` | `1/5 seconds` | Whole-file burst rate limit. |
| `WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT` | `100/day` | Whole-file daily request limit. |
| `LLM_GENERATION_BURST_RATE_LIMIT` | `20/3 minutes` | LLM generation burst limit. |
| `LLM_GENERATION_DAILY_RATE_LIMIT` | `200/day` | LLM generation daily limit. |
| `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS` | `3600` | Hourly live-audio duration safeguard. |
| `WHOLE_FILE_HOURLY_UPLOAD_BYTES` | `209715200` | Hourly whole-file byte safeguard (200 MiB). |
| `WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS` | `14400` | Hourly whole-file duration safeguard (4 hours). |
| `WHOLE_FILE_MAX_UPLOAD_BYTES` | `209715200` | Maximum accepted individual upload size (200 MiB). |
| `WHOLE_FILE_MAX_DURATION_SECONDS` | `14400` | Maximum accepted individual audio duration (4 hours). |
| `AUDIO_FFPROBE_TIMEOUT_SECONDS` | `15` | Duration-probe timeout. |
| `AUDIO_FFMPEG_TIMEOUT_SECONDS` | `1800` | Audio-normalization timeout. |

The Docker image installs `ffmpeg` and `ffprobe`; both are required for whole-file
audio inspection and normalization.

## Persistent Docker runtime

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_APP_BIND` | `127.0.0.1` | Host interface on which Compose publishes the web port. |
| `DOCKER_SEED_TEST_ACCOUNTS` | `false` | Seed reusable local test accounts during container startup. Keep disabled on persistent or shared instances. |
| `CELERY_WORKER_CONCURRENCY` | `1` | Concurrency for the single local runtime worker consuming all queues. |
| `CONTAINER_STARTUP_TIMEOUT_SECONDS` | `90` | Wait for PostgreSQL and Redis before bootstrap. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy IPs whose forwarded headers Uvicorn trusts. Do not use `*` unless direct origin access is blocked. |

See [docker.md](docker.md) for startup, upgrade, persistence, and reverse-proxy
instructions.

## Host development only

These variables affect `start-dev.sh` and are ignored by the persistent container
entrypoint:

- `DEV_START_CELERY`
- `DEV_START_BRAVE`
- `DEV_ALLOW_REMOTE_BIND`
- `DEV_FORWARDED_ALLOW_IPS`
- `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE`
- `DEV_RESTART_EXISTING_PROCESSES`
- `DEV_PURGE_CELERY_QUEUE`
- `DEV_SEED_TEST_ACCOUNTS`
- `DEV_TEST_TEAM_NAME`
- `DEV_TEST_ADMIN_EMAIL`, `DEV_TEST_ADMIN_PASSWORD`
- `DEV_TEST_LEADER_EMAIL`, `DEV_TEST_LEADER_PASSWORD`
- `DEV_TEST_USER_EMAIL`, `DEV_TEST_USER_PASSWORD`

The `OWASP_LIFECYCLE_*` and `HALLUCINATION_CHECK_DEBUG_UI` settings are security
or development test helpers. Leave them blank or disabled during normal runtime
use.
