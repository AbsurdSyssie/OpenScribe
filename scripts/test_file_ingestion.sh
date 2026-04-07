#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
COOKIE_JAR="${COOKIE_JAR:-/tmp/openscribe_file_ingestion.cookies}"
AUDIO_PATH="${1:-$ROOT_DIR/tests/MoreOrLess.wav}"
EMAIL="${OPENSCRIBE_EMAIL:-}"
PASSWORD="${OPENSCRIBE_PASSWORD:-}"
TITLE="${OPENSCRIBE_TITLE:-Manual file ingestion test}"

if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
  echo "Set OPENSCRIBE_EMAIL and OPENSCRIBE_PASSWORD before running this script." >&2
  exit 1
fi

if [[ ! -f "$AUDIO_PATH" ]]; then
  echo "Audio file not found: $AUDIO_PATH" >&2
  exit 1
fi

cleanup() {
  rm -f "$COOKIE_JAR"/
}
trap cleanup EXIT

echo "Logging in to $BASE_URL as $EMAIL"
LOGIN_RESPONSE="$(curl -sS -c "$COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/v1/auth/login" \
  -d "$(printf '{"email":"%s","password":"%s"}' "$EMAIL" "$PASSWORD")")"

AUTH_LEVEL="$(printf '%s' "$LOGIN_RESPONSE" | .venv/bin/python -c 'import json,sys; body=json.load(sys.stdin); print(body.get("auth_level",""))')"

if [[ "$AUTH_LEVEL" == "pending_mfa" ]]; then
  read -r -p "Enter current TOTP code for $EMAIL: " TOTP_CODE
  MFA_RESPONSE="$(curl -sS -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -X POST "$BASE_URL/api/v1/auth/mfa/totp" \
    -d "$(printf '{"code":"%s","remember_device":false}' "$TOTP_CODE")")"
  MFA_LEVEL="$(printf '%s' "$MFA_RESPONSE" | .venv/bin/python -c 'import json,sys; body=json.load(sys.stdin); print(body.get("auth_level",""))')"
  if [[ "$MFA_LEVEL" != "full" ]]; then
    echo "MFA challenge did not complete successfully:" >&2
    printf '%s\n' "$MFA_RESPONSE" >&2
    exit 1
  fi
elif [[ "$AUTH_LEVEL" != "full" ]]; then
  echo "Login did not return a full session:" >&2
  printf '%s\n' "$LOGIN_RESPONSE" >&2
  exit 1
fi

echo "Starting file-upload transcript"
START_RESPONSE="$(curl -sS -b "$COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/v1/transcripts/start" \
  -d "$(printf '{"title":"%s","ingestion_mode":"whole_file"}' "$TITLE")")"

TRANSCRIPT_ID="$(printf '%s' "$START_RESPONSE" | .venv/bin/python -c 'import json,sys; body=json.load(sys.stdin); print(body.get("id",""))')"
if [[ -z "$TRANSCRIPT_ID" ]]; then
  echo "Transcript start failed:" >&2
  printf '%s\n' "$START_RESPONSE" >&2
  exit 1
fi

echo "Uploading $AUDIO_PATH to transcript $TRANSCRIPT_ID"
UPLOAD_RESPONSE="$(curl -sS -b "$COOKIE_JAR" \
  -X POST "$BASE_URL/api/v1/transcripts/$TRANSCRIPT_ID/audio-file" \
  -F "audio=@${AUDIO_PATH}")"

echo
echo "Transcript start response:"
printf '%s\n' "$START_RESPONSE" | .venv/bin/python -m json.tool
echo
echo "Upload response:"
printf '%s\n' "$UPLOAD_RESPONSE" | .venv/bin/python -m json.tool
