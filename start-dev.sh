#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo ".env not found. Copy .env.example to .env first." >&2
  exit 1
fi

if [[ ! -x ".venv/bin/fastapi" ]]; then
  echo ".venv is missing or incomplete. Create it and install requirements first." >&2
  exit 1
fi

set -a
source .env
set +a

: "${APP_HOST:=0.0.0.0}"
: "${DEV_ALLOW_REMOTE_BIND:=true}"
: "${DEV_PURGE_CELERY_QUEUE:=true}"

docker compose up -d

.venv/bin/python scripts/security/check_service_exposure.py

echo "Bootstrapping persistent local Vault..."
.venv/bin/python scripts/bootstrap_local_vault.py

if [[ -f ".local/vault/root-token" ]]; then
  export VAULT_TOKEN_FILE="${ROOT_DIR}/.local/vault/root-token"
  unset VAULT_TOKEN
fi

APP_BIND_HOST="$(
.venv/bin/python - <<'PY'
import os

from app.dev_safety import resolve_dev_bind_host

allow_remote = os.getenv("DEV_ALLOW_REMOTE_BIND", "false").strip().lower() == "true"
print(resolve_dev_bind_host(host=os.getenv("APP_HOST"), allow_remote=allow_remote))
PY
)"

if [[ "${DEV_RESTART_EXISTING_PROCESSES:-true}" == "true" ]]; then
  echo "Stopping existing processes..."

  pkill -f 'fastapi dev app/main.py' 2>/dev/null || true
  pkill -f 'celery -A app.celery_app:celery_app worker' 2>/dev/null || true
  pkill -f 'brave-browser.*--remote-debugging-port=9222' 2>/dev/null || true

  sleep 1
fi

echo "Waiting for Postgres to accept connections..."
.venv/bin/python - <<'PY'
import os
import time

from sqlalchemy import create_engine, text

database_url = os.environ["DATABASE_URL"]
engine = create_engine(database_url, future=True)

deadline = time.time() + 30
last_error = None

while time.time() < deadline:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        break
    except Exception as exc:
        last_error = exc
        time.sleep(1)
else:
    raise SystemExit(f"Postgres did not become ready in time: {last_error}")
PY

.venv/bin/alembic upgrade head

if [[ "${DEV_SEED_TEST_ACCOUNTS:-true}" == "true" ]]; then
  .venv/bin/python scripts/seed_dev_accounts.py
fi

CELERY_WORKER_PID=""
BRAVE_PID=""

cleanup() {
  echo "Cleaning up processes..."

  if [[ -n "${CELERY_WORKER_PID}" ]]; then
    kill "${CELERY_WORKER_PID}" 2>/dev/null || true
  fi

  if [[ -n "${BRAVE_PID}" ]]; then
    kill "${BRAVE_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

if [[ "${DEV_START_CELERY:-true}" == "true" ]]; then
  if [[ "${DEV_PURGE_CELERY_QUEUE}" == "true" ]]; then
    echo "Purging stale Celery tasks from the dev queue..."
    .venv/bin/celery -A app.celery_app:celery_app purge -f >/dev/null 2>&1 || true
  fi
  echo "Starting Celery worker..."
  .venv/bin/celery -A app.celery_app:celery_app worker --loglevel "${CELERY_LOG_LEVEL:-INFO}" &
  CELERY_WORKER_PID=$!
fi

if [[ "${DEV_START_BRAVE:-true}" == "true" ]]; then
  echo "Starting Brave with remote debugging..."

  brave-browser \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/brave-mcp-profile \
    "http://localhost:8080" \
    >/dev/null 2>&1 &

  BRAVE_PID=$!
fi

echo "Starting FastAPI on ${APP_BIND_HOST}:${APP_PORT}..."
.venv/bin/fastapi dev app/main.py --host "${APP_BIND_HOST}" --port "${APP_PORT}"
