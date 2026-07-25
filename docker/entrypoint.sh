#!/bin/sh
set -eu

wait_for_database() {
  python - <<'PY'
import os
import time
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
deadline = time.time() + int(os.getenv("DATABASE_WAIT_TIMEOUT_SECONDS", "90"))
last_error = None
while time.time() < deadline:
    try:
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)
raise SystemExit(f"database did not become ready: {last_error}")
PY
}

wait_for_vault_token() {
  token_file="${VAULT_TOKEN_FILE:-/app/.local/vault/root-token}"
  timeout="${VAULT_TOKEN_WAIT_TIMEOUT_SECONDS:-90}"
  elapsed=0
  while [ ! -s "$token_file" ]; do
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "Vault token file did not become ready at $token_file" >&2
      exit 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

case "${1:-web}" in
  migrate)
    wait_for_database
    wait_for_vault_token
    exec alembic upgrade head
    ;;
  web)
    wait_for_database
    wait_for_vault_token
    exec fastapi run app/main.py \
      --host "${APP_HOST:-0.0.0.0}" \
      --port "${APP_PORT:-8080}" \
      --proxy-headers \
      --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
    ;;
  worker)
    queue="${CELERY_QUEUE:?CELERY_QUEUE is required for worker mode}"
    wait_for_database
    wait_for_vault_token
    exec celery -A app.celery_app:celery_app worker \
      -Q "$queue" \
      -n "${queue}@%h" \
      --loglevel "${CELERY_LOG_LEVEL:-INFO}"
    ;;
  beat)
    wait_for_database
    wait_for_vault_token
    exec celery -A app.celery_app:celery_app beat \
      --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
      --schedule /app/.runtime/celerybeat-schedule
    ;;
  vault-bootstrap)
    while true; do
      python scripts/bootstrap_local_vault.py
      sleep "${VAULT_BOOTSTRAP_INTERVAL_SECONDS:-30}"
    done
    ;;
  *)
    exec "$@"
    ;;
esac
