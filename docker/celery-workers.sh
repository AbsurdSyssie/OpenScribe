#!/usr/bin/env bash
# Shared by the container and host development launchers.
validate_celery_workers() {
  CELERY_WORKER_MODE="${CELERY_WORKER_MODE-combined}"
  CELERY_WORKER_CONCURRENCY="${CELERY_WORKER_CONCURRENCY-1}"
  CELERY_CONTROL_CONCURRENCY="${CELERY_CONTROL_CONCURRENCY-1}"
  CELERY_GENERATION_CONCURRENCY="${CELERY_GENERATION_CONCURRENCY-2}"
  CELERY_INGESTION_CONCURRENCY="${CELERY_INGESTION_CONCURRENCY-1}"
  case "$CELERY_WORKER_MODE" in
    combined|isolated) ;;
    *) echo "CELERY_WORKER_MODE must be combined or isolated" >&2; return 1 ;;
  esac
  local setting
  for setting in CELERY_WORKER_CONCURRENCY CELERY_CONTROL_CONCURRENCY CELERY_GENERATION_CONCURRENCY CELERY_INGESTION_CONCURRENCY; do
    if [[ ! "${!setting}" =~ ^[1-9][0-9]*$ ]]; then
      echo "$setting must be a positive integer" >&2
      return 1
    fi
  done
}

start_celery_workers() {
  local command="$1" queue concurrency
  CELERY_WORKER_PIDS=()
  if [[ "$CELERY_WORKER_MODE" == combined ]]; then
    "$command" -A app.celery_app:celery_app worker \
      -Q control,generation,ingestion --pool prefork \
      --concurrency "$CELERY_WORKER_CONCURRENCY" \
      --loglevel "${CELERY_LOG_LEVEL:-INFO}" &
    CELERY_WORKER_PIDS+=("$!")
  else
    for queue in control generation ingestion; do
      case "$queue" in
        control) concurrency="$CELERY_CONTROL_CONCURRENCY" ;;
        generation) concurrency="$CELERY_GENERATION_CONCURRENCY" ;;
        ingestion) concurrency="$CELERY_INGESTION_CONCURRENCY" ;;
      esac
      "$command" -A app.celery_app:celery_app worker \
        -Q "$queue" --hostname "$queue@%h" --pool prefork \
        --concurrency "$concurrency" \
        --loglevel "${CELERY_LOG_LEVEL:-INFO}" &
      CELERY_WORKER_PIDS+=("$!")
    done
  fi
}
