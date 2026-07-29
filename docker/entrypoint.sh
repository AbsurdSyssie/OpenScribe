#!/usr/bin/env bash

set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p /app/.local/vault
  chown -R openscribe:openscribe /app/.local/vault
  if [[ -d /app/.local/demo ]]; then
    chown -R openscribe:openscribe /app/.local/demo
  fi
  exec gosu openscribe "$0" "$@"
fi

wait_for_postgres() {
  python - <<'PY'
import os
import time

from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
deadline = time.time() + float(os.getenv("CONTAINER_STARTUP_TIMEOUT_SECONDS", "90"))
last_error = None

while time.time() < deadline:
    try:
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)

raise SystemExit(f"Postgres did not become ready: {last_error}")
PY
}

wait_for_redis() {
  python - <<'PY'
import os
import time

from redis import Redis

url = os.environ["CELERY_BROKER_URL"]
deadline = time.time() + float(os.getenv("CONTAINER_STARTUP_TIMEOUT_SECONDS", "90"))
last_error = None

while time.time() < deadline:
    try:
        client = Redis.from_url(url)
        client.ping()
        client.close()
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)

raise SystemExit(f"Redis did not become ready: {last_error}")
PY
}

bootstrap_runtime() {
  echo "Waiting for Postgres and Redis..."
  wait_for_postgres
  wait_for_redis

  echo "Initializing or unsealing Vault..."
  python scripts/bootstrap_local_vault.py

  echo "Applying database migrations..."
  alembic upgrade head

  if [[ "${DOCKER_SEED_TEST_ACCOUNTS:-false}" == "true" ]]; then
    echo "Seeding local test accounts..."
    python scripts/seed_dev_accounts.py
  fi
}

run_runtime() {
  bootstrap_runtime

  local worker_pid beat_pid app_pid
  local -a child_pids

  echo "Starting Celery worker..."
  celery -A app.celery_app:celery_app worker \
    -Q control,generation,ingestion \
    --concurrency "${CELERY_WORKER_CONCURRENCY:-1}" \
    --loglevel "${CELERY_LOG_LEVEL:-INFO}" &
  worker_pid=$!

  echo "Starting Celery Beat..."
  celery -A app.celery_app:celery_app beat \
    --schedule /tmp/celerybeat-schedule \
    --loglevel "${CELERY_LOG_LEVEL:-INFO}" &
  beat_pid=$!

  echo "Starting OpenScribe web server..."
  uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${CONTAINER_APP_PORT:-8080}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" &
  app_pid=$!

  child_pids=("$worker_pid" "$beat_pid" "$app_pid")

  shutdown() {
    trap - INT TERM
    echo "Stopping OpenScribe processes..."
    kill -TERM "${child_pids[@]}" 2>/dev/null || true
    wait "${child_pids[@]}" 2>/dev/null || true
  }

  trap 'shutdown; exit 143' INT TERM

  set +e
  wait -n "${child_pids[@]}"
  status=$?
  set -e

  echo "An OpenScribe process exited with status ${status}; stopping the container."
  shutdown
  return "$status"
}

case "${1:-runtime}" in
  runtime)
    run_runtime
    ;;
  bootstrap)
    bootstrap_runtime
    ;;
  *)
    exec "$@"
    ;;
esac
