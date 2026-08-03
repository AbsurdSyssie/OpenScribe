# Testing

This document covers test execution, non-database test boundaries, and focused verification workflows. Database lifecycle and safety are in [dbtesting.md](dbtesting.md).

## Install test dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes the runtime dependencies plus pytest and pytest-xdist. The requirements install the pinned `en_core_web_sm` model wheel; do not run a separate `spacy download` command. The persistent Docker runtime image installs runtime requirements only and is not intended to run the complete test suite.

## Run the suite

```bash
source .venv/bin/activate
export APP_ENV=test
export COOKIE_SECURE_MODE=auto
export HSTS_SOURCE=app
pytest -q
```

Explicit xdist run:

```bash
pytest -q -n 4
```

Four workers has been a useful local full-suite setting, but worker count remains host-dependent. Focused one-test or small-file runs should normally remain sequential to avoid worker startup cost.

## Shared-infrastructure lock

The sequential pytest process or xdist controller acquires `/tmp/openscribe_pytest.lock`. A second concurrent test invocation exits instead of sharing/resetting the same PostgreSQL and Redis test infrastructure.

Xdist workers run under the controller lock. Each worker derives a separate PostgreSQL database from `TEST_DATABASE_URL` and receives an isolated SlowAPI key prefix. See [dbtesting.md](dbtesting.md).

## Test categories

The suite contains ordinary pytest tests plus markers for behavior that needs special infrastructure.

- Pure/unit/static tests should not require PostgreSQL or Redis fixtures.
- Ordinary database-backed tests use rollback isolation on a canonical per-worker schema.
- `real_db_connections` tests use independent committed connections and trusted-metadata cleanup.
- Migration tests own their schema lifecycle and force the next ordinary database test to rebuild canonical metadata.
- Optional Playwright tests skip when browser dependencies are unavailable.
- External-provider tests use fakes/mock transports unless a manual diagnostic explicitly requests a real provider.

Do not add tests that call public provider endpoints or the public internet during the normal suite.

## Focused commands

Authentication, cookies, CSRF, and headers:

```bash
pytest -q tests/test_auth.py tests/test_auth_email.py tests/test_cookie_csrf_security.py
```

API authorization manifest:

```bash
./.venv/bin/python scripts/audit_api_auth.py
```

The audit compares the manifest with the live `/api/v1` route inventory, probes anonymous/invalid/partial/full/manager/admin session boundaries as applicable, exits `1` on behavior mismatch, and exits `2` when a route has no audit entry.

CSP and XSS coverage:

```bash
pytest -q tests/test_xss_coverage.py tests/test_cookie_csrf_security.py
rg '\bstyle\s*=' app/templates
rg 'cdn\.jsdelivr\.net|cdn\.tailwindcss\.com|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com' app/templates app/static/js
```

Workspace/browser route coverage:

```bash
pytest -q tests/test_workspace_shell.py tests/test_workspace_responsive.py tests/test_settings_workspace.py
```

Transcript ingestion/generation lifecycle:

```bash
pytest -q tests/test_transcripts.py tests/test_transcript_ingestion.py tests/test_task_outbox.py tests/test_quota_lifecycle.py
```

Provider configuration:

```bash
pytest -q tests/test_stt_config.py tests/test_llm_config.py tests/test_deidentification.py
```

Security-remediation focus:

```bash
pytest -q tests/test_api.py tests/test_admin_ui.py tests/test_docker_runtime.py tests/test_security_audit.py tests/test_ssrf_canary.py tests/test_provider_response_bounds.py
python -m pip install pip-audit
python -m pip_audit -r requirements.txt
```

The Docker smoke workflow runs the runtime dependency audit. It does not prove that deployed image references are digest-locked or that all dependency artifacts use full hashes.

Use the actual file names present on the branch when a focused area has been split or renamed; `pytest --collect-only -q` is the authoritative collection view.

## Browser CSRF regression

`tests/test_csrf_browser.py` is an optional Playwright-backed regression. It starts FastAPI on localhost, logs in through the real browser form, follows the compatibility `/transcribe` redirect into the canonical `/workspace` Scribe shell, creates a consultation, and verifies that the browser sends the current session-bound `X-CSRF-Token` to the transcript-start API without rotating it during ordinary same-session navigation.

Run it with:

```bash
source .venv/bin/activate
pip install playwright
python -m playwright install chromium
export APP_ENV=test
export COOKIE_SECURE_MODE=auto
export HSTS_SOURCE=app
pytest -q tests/test_csrf_browser.py
```

Missing Playwright/browser binaries cause this optional test to skip rather than fail the normal suite.

## Manual file-ingestion smoke test

With a local app, worker, Beat, STT configuration, and test user available:

```bash
OPENSCRIBE_EMAIL='user@example.com' \
OPENSCRIBE_PASSWORD='password-1' \
./scripts/test_file_ingestion.sh tests/MoreOrLess.wav
```

The script uses the real auth flow, prompts for TOTP only when required, starts a whole-file transcript, uploads the sample through the JSON API, and prints bounded response metadata. Do not use a real patient recording as a smoke-test fixture.

## Persistent Docker smoke workflow

`.github/workflows/docker-smoke.yml` validates Compose, builds the image, starts the `runtime` profile, waits for the `/health` healthcheck, and prints concise container diagnostics on failure. It does not prove provider connectivity or production readiness.

Local equivalent:

```bash
cp -n .env.example .env
docker compose --profile runtime config
docker compose --profile runtime up -d --build
docker compose --profile runtime ps
curl --fail http://127.0.0.1:8080/health
docker compose --profile runtime down
```

Use `down --volumes` only for a deliberately disposable instance.

## What must be tested for behavior changes

### Authentication and authorization

- anonymous, onboarding, pending-MFA, normal-user, leader, and system-admin boundaries;
- own-team versus cross-team manager access;
- owner-only transcript/generated-content access and non-disclosing cross-owner failures;
- session/trusted-device revocation after lifecycle/security changes;
- CSRF and same-origin enforcement for browser-cookie authority.

### Data lifecycle

- retention snapshotting and expired-root denial before cleanup;
- hard-delete cascades and preserved metadata references where designed;
- durable task outbox publication/idempotency;
- provider-attempt reservation/submission/settlement;
- temporary audio and retired provider-secret cleanup;
- rollback compensation when external Vault writes precede database failure.

### Confidentiality

- ciphertext at rest for designated fields;
- owner-bound envelope/AAD behavior;
- no plaintext secrets/content in logs, audit details, task payloads, or provider-error persistence;
- Vault/key failure fails closed;
- API responses expose plaintext only through authorized owner paths and use `no-store`.

### Provider behavior

- ready/pending setup states;
- credential replacement/removal and cleanup;
- discovered/manual model validation;
- provider-specific request/response mapping;
- SSRF/transport/redirect/size guards;
- sanitized errors and quota outcomes.

### Browser surfaces

- canonical workspace redirects and role-gated navigation;
- no transcript-history queries/decryption on non-Scribe pages;
- responsive navigation and recording navigation lock;
- local-only CSP-compatible assets;
- accessible server-rendered forms and fallback behavior.

## Documentation checks

When code changes user-facing routes, environment settings, lifecycle schedules, provider behavior, or security boundaries:

1. update the closest operational document;
2. update the relevant README section;
3. update the API auth audit manifest for new routes;
4. avoid rewriting dated compliance evidence—add a new assessment result instead;
5. use repository-relative links.
