#!/usr/bin/env bash
# Wait for the OpenScribe runtime container to report healthy, then verify the
# HTTP health endpoint. Used by the docker-smoke workflow for both the initial
# start and the persistent-volume restart check.
set -euo pipefail

profile="${1:-runtime}"
attempts="${2:-60}"
interval_seconds="${3:-5}"

container_id="$(docker compose --profile "$profile" ps -q openscribe)"

diagnose() {
  echo "--- compose status ---"
  docker compose --profile "$profile" ps -a
  if [[ -n "$container_id" ]]; then
    echo "--- OpenScribe state ---"
    docker inspect --format '{{json .State}}' "$container_id" || true
  fi
  echo "--- OpenScribe logs ---"
  docker compose --profile "$profile" logs --no-color --tail=200 openscribe || true
}

if [[ -z "$container_id" ]]; then
  echo "OpenScribe container is not running" >&2
  diagnose
  exit 1
fi

for ((attempt = 1; attempt <= attempts; attempt++)); do
  status="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || echo missing)"
  if [[ "$status" == "healthy" ]]; then
    curl --fail --silent --show-error http://127.0.0.1:8080/health
    exit 0
  fi
  if [[ "$status" == "unhealthy" || "$status" == "missing" ]]; then
    echo "OpenScribe health status: ${status}" >&2
    diagnose
    exit 1
  fi
  sleep "$interval_seconds"
done
echo "OpenScribe did not become healthy in time" >&2
diagnose
exit 1
