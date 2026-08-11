# Persistent Docker Runtime

The `runtime` Compose profile runs OpenScribe as a restartable single-host stack. It replaces `start-dev.sh` when the application must continue after a shell closes and return after host/Docker daemon restart.

The profile starts:

- PostgreSQL with a named data volume;
- Redis with append-only persistence;
- Vault with a named storage volume;
- one OpenScribe container that bootstraps/unseals local Vault, applies migrations, and supervises FastAPI, one Celery worker consuming all queues, and Celery Beat.

This preserves the current local architecture. It is not a production reference topology: bundled database credentials and persistent local Vault root/unseal material must not be used as production secrets, and production workers should be isolated by queue.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose`);
- Buildx is recommended; legacy builder fallback can still complete with a deprecation warning.

## First start

```bash
cp .env.example .env
# Review APP_PUBLIC_URL, ALLOWED_HOSTS, BOOTSTRAP_ADMIN_TOKEN, secrets, mail, proxy trust, and Docker settings.
scripts/deploy-compose.sh
```

The deployment script accepts only a clean commit available on the current branch's upstream. It passes the commit SHA and its source URL to the recreated application container. Run the same command for every release.

The web application publishes on `127.0.0.1:8080` by default.

```bash
docker compose --profile runtime ps
docker compose logs -f openscribe
curl --fail http://127.0.0.1:8080/health
```

On every application-container start, the entrypoint:

1. fixes ownership of the named Vault-bootstrap mount while running as root;
2. re-executes itself as the unprivileged `openscribe` user (UID/GID `10001`);
3. waits for PostgreSQL and Redis;
4. initializes or unseals persistent local Vault;
5. ensures KV-v2, Transit, and the user-content KEK exist;
6. applies `alembic upgrade head`;
7. optionally seeds local test accounts;
8. starts the Celery worker, Celery Beat, and Uvicorn.

If any supervised long-running process exits, the container stops and `restart: unless-stopped` restarts the complete runtime consistently.

The entrypoint never purges Redis/Celery queues. Queued work resumes after restart. Beat publishes pending durable task-dispatch rows every second and runs transcript-root retention, failed-ingestion source expiry, transcript-audio cleanup, security-audit expiry, legal-document retention, provider-secret cleanup, and quota lifecycle processing every 10 seconds.

## Common operations

```bash
# Status
docker compose --profile runtime ps

# Follow web, worker, and Beat output
docker compose logs -f openscribe

# Restart only the application runtime
docker compose restart openscribe

# Rebuild after code/dependency changes
docker compose --profile runtime up -d --build

# Stop while preserving named volumes
docker compose --profile runtime down

# Start again without rebuilding
docker compose --profile runtime up -d
```

Do not add `--volumes` unless PostgreSQL, Redis, Vault, and bootstrap data should be destroyed intentionally.

## Migrating from `start-dev.sh`

The host development flow and runtime profile share the same Compose project. Existing PostgreSQL and Vault named volumes carry over. Wrapped user DEKs/provider credentials remain usable only when matching PostgreSQL, Vault storage, and bootstrap material are kept together.

Redis did not originally use a named volume. Its old container-local queue/result/rate-limit state does not automatically migrate when Compose creates `redis_data`.

Before the first runtime start:

1. Stop the host FastAPI server, Celery worker, and Celery Beat. Running both workflows competes for `APP_PORT` and queues, and multiple Beat processes duplicate periodic work.
2. Copy host Vault bootstrap files into `vault_bootstrap` when the existing `vault_data` was initialized by `start-dev.sh`:

```bash
docker compose --profile runtime build openscribe

docker compose --profile runtime run --rm --no-deps \
  --volume "$PWD/.local/vault:/host-bootstrap:ro" \
  --entrypoint sh \
  openscribe \
  -c 'install -o 10001 -g 10001 -m 600 /host-bootstrap/root-token /app/.local/vault/root-token && install -o 10001 -g 10001 -m 600 /host-bootstrap/unseal-key /app/.local/vault/unseal-key'
```

The command copies without printing values. Skip only when Vault has never been initialized on this host.

On first start, Compose can recreate dependency containers. PostgreSQL/Vault named volumes persist. `redis_data` starts empty and does not copy an earlier unpersisted Redis container; drain or accept loss of important queued work/results before migration.

## Persistent data

| Volume | Contents |
| --- | --- |
| `postgres_data` | Application database and migration state. |
| `redis_data` | Celery broker/results and rate-limit state. |
| `vault_data` | Vault file storage, KV secrets, Transit key state. |
| `vault_bootstrap` | Local Vault root token and unseal key used by the entrypoint. |

Back up and restore `postgres_data`, `vault_data`, and `vault_bootstrap` as one encrypted-content recovery set. Database rows contain wrapped user keys requiring the corresponding Vault Transit state, and the runtime requires bootstrap material to unseal/authenticate to this local Vault.

Include `redis_data` when queued work/result/limiter continuity matters, but PostgreSQL remains authoritative and Redis is not a database backup.

Do not wipe/reinitialize Vault while retaining encrypted application data unless following the explicit unreadable-content recovery procedure.

## Compose configuration

Compose reads `.env` for interpolation and explicitly maps supported runtime variables. It overrides addresses that must use Docker service DNS:

- `DATABASE_URL` -> `postgres:5432`;
- limiter storage -> `redis:6379/0`;
- Celery broker/result backend -> `redis:6379/2`;
- `VAULT_ADDR` -> `vault:8200`;
- `VAULT_TOKEN_FILE` -> `/app/.local/vault/root-token`;
- web bind -> internal `0.0.0.0:8080`.

`APP_PORT` controls the published host port:

```env
APP_PORT=8090
APP_PUBLIC_URL=http://127.0.0.1:8090
```

Then recreate:

```bash
docker compose --profile runtime up -d
```

See [environment.md](environment.md) for all mapped settings and code defaults.

## Google Application Default Credentials

Gemini Enterprise can use ADC. Do not copy credential JSON into the image or `.env`.

The optional `docker-compose.adc.yml` mounts one host file read-only into the application container and sets `GOOGLE_APPLICATION_CREDENTIALS`:

```bash
export GOOGLE_ADC_HOST_FILE="$HOME/.config/gcloud/application_default_credentials.json"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  --profile runtime \
  up -d --build
```

Verify identity resolution without printing a token/credential:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  exec openscribe \
  python -c 'import google.auth; c,p=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"]); print(type(c).__name__, p, c.quota_project_id)'
```

Application processes run as UID `10001`; the mounted file must be readable by that UID. Do not make it world-readable. Production should prefer attached workload identity/Workload Identity Federation over a user ADC refresh-token file.

## Reverse proxy and network exposure

Safe default:

```env
DOCKER_APP_BIND=127.0.0.1
```

A host reverse proxy can connect to loopback. When a proxy in another container/private network must connect, bind deliberately and firewall the origin.

Configure all relevant boundaries independently:

- `APP_PUBLIC_URL` = public HTTPS origin;
- `ALLOWED_HOSTS` = exact public hostnames, with no wildcard. If the edge redirects `www` to the canonical host, list only the canonical host; otherwise list both exact hosts;
- `APP_HEALTHCHECK_HOST` = one exact `ALLOWED_HOSTS` entry, normally the canonical public hostname. Compose sends this Host header to the loopback health endpoint rather than weakening host validation for localhost;
- `APP_ENV=production`;
- `COOKIE_SECURE_MODE=always`;
- `BOOTSTRAP_ADMIN_TOKEN` = a deployment secret available only until the first system administrator is created;
- one HSTS owner through `HSTS_SOURCE`;
- `FORWARDED_ALLOW_IPS` = actual trusted proxy addresses accepted by Uvicorn;
- `TRUST_FORWARDED_ORIGIN_HEADERS=true` only when that proxy sanitizes forwarded host/proto and direct origin access is blocked;
- `AUDIT_TRUST_X_FORWARDED_FOR`/`AUDIT_TRUST_CLOUDFLARE` only for the expected sanitizing proxy/CDN path.
- `RATE_LIMIT_TRUST_CLOUDFLARE=true` only after Cloudflare is the sole route to the origin and overwrites `CF-Connecting-IP`; use `RATE_LIMIT_TRUST_X_FORWARDED_FOR=true` only for an equivalent trusted proxy that overwrites `X-Forwarded-For`. Cloudflare takes precedence when both are true.
- request-body limits at the reverse proxy/CDN that match or are lower than the application upload limits, including the 24 MiB live-chunk limit.

These switches are separate. Trusting Uvicorn forwarded headers does not automatically trust them for CSRF origin reconstruction, audit client IP, or rate-limit client IP.

Do not use `FORWARDED_ALLOW_IPS=*` on an origin that is directly reachable.

## Test-account seeding

Persistent runtime does not seed accounts by default. For a private localhost disposable instance:

```env
DOCKER_SEED_TEST_ACCOUNTS=true
```

`DEV_TEST_*` values control seeded account data. Disable before adding real/shared data. Seeded accounts are localhost-only in application policy and are not production accounts.

## Troubleshooting

### Vault initialized but bootstrap files missing

The entrypoint fails closed when existing `vault_data` is initialized but `/app/.local/vault/root-token` or `unseal-key` is missing/empty.

Recovery:

1. restore the matching original `.local/vault/root-token` and `.local/vault/unseal-key` or deployment backup;
2. copy them using the migration command above without printing them;
3. restart:

```bash
docker compose --profile runtime up -d
```

If those files/Vault state are unrecoverable, existing wrapped DEKs cannot be re-derived. Removing Vault/bootstrap volumes or running `down --volumes` is content/key loss, not recovery.

Only when destructive loss handling is explicit, audit affected owners with:

```bash
python scripts/reset_unreadable_owner_content.py
```

Run again with `--apply` only when deleting unreadable transcript-derived content and issuing fresh DEKs is the approved intent.

### Runtime unhealthy

```bash
docker compose --profile runtime ps -a
docker inspect --format '{{json .State.Health}}' "$(docker compose --profile runtime ps -q openscribe)"
docker compose --profile runtime logs --tail=300 openscribe
```

Check migrations, Vault bootstrap/unseal, PostgreSQL/Redis connectivity, stable CSRF secret, production cookie settings, and worker/Beat startup.

## Host development remains available

`start-dev.sh` starts only PostgreSQL, Redis, and Vault in Compose, then runs FastAPI/Celery/Beat and optional Brave on the host virtualenv for reload/debugging.

Do not run it alongside the `runtime` profile.

## Production boundary

Before clinical production:

- use separately managed strong database/Redis credentials and network/TLS controls;
- pre-provision Vault and inject least-privilege runtime identities instead of persistent root credentials;
- split Celery workers by queue and monitor worker/Beat independently;
- use a configured HTTPS reverse proxy and deliberate forwarded-header trust;
- coordinate PostgreSQL/Vault backup and restore drills;
- monitor web, worker, Beat, database, Redis, Vault, transcript/legal/audit retention, source-audio cleanup, provider-secret cleanup, and quota/outbox lifecycle;
- establish deployment secret rotation, audit review, recovery, and destructive deletion procedures.

## Operator actions still required

The application changes do not complete edge or supply-chain work. Before relying on the related controls:

- restrict origin access and confirm Cloudflare overwrites client-IP headers before enabling `RATE_LIMIT_TRUST_CLOUDFLARE`;
- add Cloudflare WAF rate rules for public abuse paths;
- set and test proxy/CDN request-body limits;
- redirect `www` to the canonical host and remove wildcard DNS records that are not needed;
- lock container image references by digest and dependency artifacts by full hashes; this work remains open;
- use the current password-reset behaviour as implemented. A durable asynchronous mail design is still needed if stronger response-timing uniformity is required.
