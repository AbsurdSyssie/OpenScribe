# Persistent Docker runtime

The `runtime` Compose profile runs OpenScribe as a restartable single-host stack.
It is intended to replace `start-dev.sh` when the application should keep running
after the shell closes and should return after a host or Docker daemon restart.

The profile starts:

- PostgreSQL with a named data volume
- Redis with append-only persistence
- Vault with a named storage volume
- one OpenScribe application container running migrations, local Vault bootstrap,
  the FastAPI web server, one Celery worker for all queues, and Celery Beat

This profile preserves the current local architecture. It is not a production
reference topology: the bundled database credentials and local Vault root-token
bootstrap must not be used as production secrets, and production workers should
be isolated by queue.

## Prerequisites

- Docker Engine with the Compose v2 plugin (`docker compose`).
- Buildx is recommended and is the default builder on current Docker Engine and
  Docker Desktop. Without it, Compose falls back to the legacy builder and may
  print a deprecation warning; the build still completes.

## First start

```bash
cp .env.example .env
# Review APP_PUBLIC_URL, secrets, mail, and Docker settings before starting.

docker compose --profile runtime up -d --build
```

The web application is published on `127.0.0.1:8080` by default. Check startup:

```bash
docker compose --profile runtime ps
docker compose logs -f openscribe
curl --fail http://127.0.0.1:8080/health
```

On every application-container start, the entrypoint:

1. waits for PostgreSQL and Redis;
2. initializes or unseals the persistent local Vault;
3. ensures the KV-v2 and Transit mounts and user-content KEK exist;
4. applies `alembic upgrade head`;
5. optionally seeds local test accounts;
6. starts the Celery worker, Celery Beat, and Uvicorn.

If any of those three long-running processes exits, the container stops so the
`unless-stopped` restart policy can restart the complete runtime consistently.

The entrypoint never purges the Celery queues. Work already queued in Redis is
consumed after every start, and Beat resumes scheduling retention and
Vault-cleanup tasks immediately on each container start.

## Common operations

```bash
# Status
docker compose --profile runtime ps

# Follow application, worker, and scheduler output
docker compose logs -f openscribe

# Restart only the OpenScribe runtime
docker compose restart openscribe

# Rebuild after code or dependency changes
docker compose --profile runtime up -d --build

# Stop the stack but preserve all named volumes
docker compose --profile runtime down

# Start it again without rebuilding
docker compose --profile runtime up -d
```

Do not add `--volumes` to `docker compose down` unless the PostgreSQL, Redis,
Vault, and Vault-bootstrap data should be destroyed intentionally.

## Migrating from `./start-dev.sh`

The host development flow and the runtime profile share the same Compose
project. PostgreSQL and Vault data already stored in their named volumes carry
over. Wrapped user DEKs and Vault-stored provider credentials therefore remain
valid only when the matching PostgreSQL, Vault, and bootstrap material are kept
together. Redis did not use a named volume before this runtime profile, so its
old container-local queue, result, and rate-limit state does not automatically
migrate when Compose recreates Redis with `redis_data`.

Before the first runtime start:

1. Stop the host processes. `./start-dev.sh` and its FastAPI server, Celery
   worker, and Celery Beat must not run alongside the runtime profile: they
   compete for the same `APP_PORT` and broker queues, and two active Beats
   double-schedule retention and Vault cleanup.
2. Copy the Vault bootstrap files into the `vault_bootstrap` volume. The host
   flow stores them at `.local/vault/`; the container reads them from
   `/app/.local/vault/` on the named volume. If `vault_data` was already
   initialized by the host flow while the volume is empty, the application
   fails closed on startup (see Troubleshooting). Copy the files without
   printing their contents:

   ```bash
   docker compose --profile runtime build openscribe

   docker compose --profile runtime run --rm --no-deps \
     --volume "$PWD/.local/vault:/host-bootstrap:ro" \
     --entrypoint sh \
     openscribe \
     -c 'install -o 10001 -g 10001 -m 600 /host-bootstrap/root-token /app/.local/vault/root-token && install -o 10001 -g 10001 -m 600 /host-bootstrap/unseal-key /app/.local/vault/unseal-key'
   ```

   The command copies the files without displaying them. Skip this step only
   when Vault has never been initialized on this host.

On the first runtime start, Compose may recreate the PostgreSQL, Redis, and
Vault containers to apply the runtime configuration. The named volumes
for PostgreSQL and Vault persist, but expect a short dependency outage while
the containers are replaced. The first runtime start creates `redis_data`; it
does not copy state from the earlier unpersisted Redis container, so queued
broker work, task results, and rate-limit counters held only there may be lost.
After this migration, Redis uses append-only persistence and future runtime
restarts retain its state. Stop or drain important queued work before the first
migration rather than assuming it will be replayed.

## Persistent data

| Volume | Contents |
| --- | --- |
| `postgres_data` | Application database and migration state. |
| `redis_data` | Celery broker/result data and rate-limit state. |
| `vault_data` | Vault file storage, including KV and Transit state. |
| `vault_bootstrap` | Local Vault root token and unseal key used by the application entrypoint. |

PostgreSQL content, Vault key material, and the Vault bootstrap credentials are
linked: database rows contain wrapped user keys that require the corresponding
Vault Transit state, and the runtime cannot unseal or authenticate to Vault
without the bootstrap files. Back up `postgres_data`, `vault_data`, and
`vault_bootstrap` together and restore them as one deployment set. `redis_data`
holds queued Celery and rate-limit state; include it when queued work must
survive host loss, but it is not part of the encrypted-content set. Do not wipe
or replace the Vault volumes while retaining encrypted application data unless
the established unreadable-content recovery procedure is being followed
deliberately (see Troubleshooting).

## Configuration inside Compose

Compose reads `.env` for variable interpolation, then overrides addresses that
must use the internal Docker network:

- `DATABASE_URL` uses `postgres:5432`
- `RATE_LIMIT_STORAGE_URL` uses `redis:6379/0`
- Celery broker and result backend use `redis:6379/2`
- `VAULT_ADDR` uses `vault:8200`
- `VAULT_TOKEN_FILE` uses `/app/.local/vault/root-token`
- the web process binds to container port `8080`

`APP_PORT` controls the host-published port. For example:

```env
APP_PORT=8090
APP_PUBLIC_URL=http://127.0.0.1:8090
```

Then recreate the service:

```bash
docker compose --profile runtime up -d
```

See [environment.md](environment.md) for the complete configuration reference.

## Google Application Default Credentials

Gemini Enterprise can use Application Default Credentials (ADC). Do not copy a
credential file into the image or put its JSON in `.env`. The optional
`docker-compose.adc.yml` override mounts one host credential file read-only into
the single container that runs both the web and generation-worker processes.

Set the host path, then include both Compose files:

```bash
export GOOGLE_ADC_HOST_FILE="$HOME/.config/gcloud/application_default_credentials.json"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  --profile runtime \
  up -d --build
```

Confirm resolution without printing a token or credential content:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  exec openscribe \
  python -c 'import google.auth; c,p=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"]); print(type(c).__name__, p, c.quota_project_id)'
```

The container runs as UID `10001`; the mounted file must be readable by that UID.
Do not make the credential world-readable to bypass a permission problem. For a
production workload, prefer an attached service identity or Workload Identity
Federation rather than a user ADC refresh-token file.

## Reverse proxy and network exposure

The safe default publishes only to localhost:

```env
DOCKER_APP_BIND=127.0.0.1
```

A reverse proxy running directly on the host can connect to that address. When a
proxy in another container or a private network must connect, set an appropriate
bind address deliberately and firewall the origin. Also configure:

- `APP_PUBLIC_URL` to the public HTTPS URL;
- `COOKIE_SECURE_MODE=always` for production HTTPS;
- exactly one HSTS owner through `HSTS_SOURCE`;
- `FORWARDED_ALLOW_IPS` to the actual trusted proxy address or addresses;
- audit forwarded-header trust only when the proxy sanitizes those headers and
  direct origin access is blocked.

Do not use `FORWARDED_ALLOW_IPS=*` as a convenience setting on an origin that can
be reached directly.

## Seeding test accounts

The persistent profile does not seed test users by default. For a private local
instance only:

```env
DOCKER_SEED_TEST_ACCOUNTS=true
```

The `DEV_TEST_*` values in `.env` control the seeded names and credentials. Turn
this back off before using a persistent instance with real data or additional
users.

## Troubleshooting

### Vault is initialized but the bootstrap files are missing

The application entrypoint fails closed and the container stops when
`docker compose logs openscribe` shows one of:

- `Local Vault is initialized but root token is missing at /app/.local/vault/root-token`
- `Local Vault is initialized but unseal key is missing at /app/.local/vault/unseal-key`
- either message with `is empty at` instead of `is missing at`

The Vault server in `vault_data` was already initialized — usually by an
earlier `./start-dev.sh` run or a previous stack — but the `vault_bootstrap`
volume does not hold the matching root-token and unseal-key files. The runtime
refuses to reinitialize Vault or invent credentials.

Recovery:

1. Locate the original bootstrap files. For a host-developed instance they are
   `.local/vault/root-token` and `.local/vault/unseal-key`; otherwise restore
   them from the deployment backup.
2. Copy them into the volume without printing them, using the command in
   "Migrating from `./start-dev.sh`".
3. Start the runtime again:

   ```bash
   docker compose --profile runtime up -d
   ```

If the original files are unrecoverable, the Vault Transit key material that
wraps existing user DEKs cannot be re-derived. Do not wipe `vault_data` or
reinitialize Vault while retaining encrypted application data: affected rows
would look intact but remain permanently unreadable. A destructive reset —
removing the `vault_data` and `vault_bootstrap` volumes, or
`docker compose --profile runtime down --volumes` — destroys every wrapped DEK
and Vault-stored provider credential. That is content loss, not recovery:
afterwards, follow the established unreadable-content recovery procedure and
audit local content-owning accounts with
`python scripts/reset_unreadable_owner_content.py`, rerunning with `--apply`
only when deleting the unreadable transcript-derived content and issuing fresh
DEKs is the explicit intent.

## Host development remains available

`./start-dev.sh` still starts only PostgreSQL, Redis, and Vault in Compose, then
runs FastAPI, Celery, Beat, and optional Brave processes from the host virtualenv.
Use that workflow for live reload and local debugging. Do not run `start-dev.sh`
and the `runtime` profile at the same time: stop the host FastAPI server, Celery
worker, and Celery Beat before starting the runtime, and see "Migrating from
`./start-dev.sh`" when moving an existing host-developed instance across.

## Production boundary

Before treating this as a production deployment, replace the local assumptions:

- use strong, separately managed database and Redis credentials;
- use TLS and network controls for all backing services;
- pre-provision Vault and inject least-privilege runtime tokens rather than a
  persisted root token;
- run separate Celery workers for `control`, `generation`, and `ingestion`;
- place the web service behind a configured HTTPS reverse proxy;
- establish coordinated PostgreSQL and Vault backup/restore procedures;
- monitor web, worker, Beat, database, Redis, and Vault health independently.
