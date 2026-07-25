# Persistent Docker deployment

This stack runs OpenScribe entirely in Docker and keeps state across container and host restarts.

## Services

- `web`: FastAPI application
- `worker-control`: retention, Vault cleanup, outbox, and quota lifecycle tasks
- `worker-generation`: generated-document tasks
- `worker-ingestion`: transcript ingestion tasks
- `beat`: shared Celery scheduler
- `postgres`: application database
- `redis`: durable Celery broker/result backend and rate-limit store
- `vault`: encrypted provider secrets and user-content key wrapping
- `vault-bootstrap`: initializes and automatically unseals the local Vault after restarts
- `migrate`: applies Alembic migrations before application services start

## First start

```bash
cp .env.docker.example .env.docker
```

Replace at least these values in `.env.docker`:

- `POSTGRES_PASSWORD`
- `APP_SECRET_KEY`
- `CSRF_SECRET`

Generate independent secrets, for example:

```bash
openssl rand -hex 32
```

Build and start the stack:

```bash
docker compose --env-file .env.docker -f docker-compose.persistent.yml up -d --build
```

Open `http://127.0.0.1:8080` by default.

## Routine operation

```bash
# Status
docker compose --env-file .env.docker -f docker-compose.persistent.yml ps

# Follow logs
docker compose --env-file .env.docker -f docker-compose.persistent.yml logs -f web worker-control worker-generation worker-ingestion beat

# Restart without deleting data
docker compose --env-file .env.docker -f docker-compose.persistent.yml restart

# Stop without deleting data
docker compose --env-file .env.docker -f docker-compose.persistent.yml down

# Pull code changes, rebuild, migrate, and restart
docker compose --env-file .env.docker -f docker-compose.persistent.yml up -d --build
```

Do not add `--volumes` to `down` unless you intentionally want to delete all local OpenScribe state.

## Persistent volumes

- `openscribe_postgres_data`: database rows
- `openscribe_redis_data`: queued tasks and Redis state
- `openscribe_vault_data`: Vault encrypted storage
- `openscribe_vault_bootstrap`: local Vault root token and unseal key
- `openscribe_celerybeat_data`: Celery Beat schedule state

The Vault data volume and bootstrap volume are a matched set. Losing either can make existing wrapped content keys unreadable. Back them up together with PostgreSQL.

## Exposure and reverse proxy

The default binds the web port to localhost only:

```env
APP_BIND_ADDRESS=127.0.0.1
```

Keep that setting when Caddy, nginx, or another reverse proxy runs on the same host. For direct LAN exposure, set `APP_BIND_ADDRESS=0.0.0.0`, but do not expose PostgreSQL, Redis, or Vault.

For a public HTTPS deployment, update `.env.docker`:

```env
APP_ENV=production
APP_PUBLIC_URL=https://openscribe.example.com
COOKIE_SECURE_MODE=always
HSTS_SOURCE=proxy
FORWARDED_ALLOW_IPS=<trusted-proxy-ip-or-subnet>
```

Use `HSTS_SOURCE=app` instead when the reverse proxy does not emit HSTS. Do not leave `FORWARDED_ALLOW_IPS=*` for an origin that can be reached directly from untrusted networks.

## Backups

Database example:

```bash
docker compose --env-file .env.docker -f docker-compose.persistent.yml exec -T postgres \
  pg_dump -U ambient -d ambient_scribe > openscribe.sql
```

Also back up the Docker volumes for Vault and `vault_bootstrap` together. A database-only backup is not sufficient for encrypted transcript-derived content.

## Troubleshooting

Check the one-shot migration service after a failed start:

```bash
docker compose --env-file .env.docker -f docker-compose.persistent.yml logs migrate
```

Check Vault initialization or unseal failures:

```bash
docker compose --env-file .env.docker -f docker-compose.persistent.yml logs vault vault-bootstrap
```

Re-run a failed migration after correcting configuration:

```bash
docker compose --env-file .env.docker -f docker-compose.persistent.yml run --rm migrate
```
