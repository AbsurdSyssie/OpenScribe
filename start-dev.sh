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

docker compose up -d

set -a
source .env
set +a

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
    except Exception as exc:  # pragma: no cover - shell startup helper
        last_error = exc
        time.sleep(1)
else:
    raise SystemExit(f"Postgres did not become ready in time: {last_error}")
PY

.venv/bin/alembic upgrade head

if [[ "${DEV_SEED_TEST_ACCOUNTS:-true}" == "true" ]]; then
  .venv/bin/python scripts/seed_dev_accounts.py
fi

exec .venv/bin/fastapi dev app/main.py --host "${APP_HOST}" --port "${APP_PORT}"
