#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
APP_MODULE="app:app"
PORT="${PRESIDIO_API_PORT:-8010}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Error: virtualenv interpreter not found at $VENV_PY"
  echo "Run ./setup_portable.sh first."
  exit 1
fi

cd "$SCRIPT_DIR"
unset PYTHONHOME
unset PYTHONPATH
unset VIRTUAL_ENV
export PYTHONNOUSERSITE=1

echo "Starting portable Presidio API on port $PORT"
exec "$VENV_PY" -m uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$PORT"
