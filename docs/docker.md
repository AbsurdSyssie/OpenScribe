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

## Persistent data

| Volume | Contents |
| --- | --- |
| `postgres_data` | Application database and migration state. |
| `redis_data` | Celery broker/result data and rate-limit state. |
| `vault_data` | Vault file storage, including KV and Transit state. |
| `vault_bootstrap` | Local Vault root token and unseal key used by the application entrypoint. |

PostgreSQL content and Vault Transit state are linked: database rows contain
wrapped user keys that require the corresponding Vault key material. Back up and
restore them as one deployment set. Do not wipe or replace the Vault volume while
retaining encrypted application data unless the established unreadable-content
recovery procedure is being followed deliberately.

## Configuration inside Compose

Compose loads `.env`, then overrides addresses that must use the internal Docker
network:

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

## Host development remains available

`./start-dev.sh` still starts only PostgreSQL, Redis, and Vault in Compose, then
runs FastAPI, Celery, Beat, and optional Brave processes from the host virtualenv.
Use that workflow for live reload and local debugging. Do not run `start-dev.sh`
and the `runtime` profile on the same `APP_PORT` at the same time.

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
