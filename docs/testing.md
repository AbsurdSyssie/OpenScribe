# Testing

This document covers test execution, non-database test boundaries, and focused verification workflows. Database lifecycle and safety are in [dbtesting.md](dbtesting.md).

Consultation-split tests must cover durable accepted-intent dispatch and duplicate delivery, queued-analysis waiting, one-note consumption, exact intent-bound review-draft initialization without document creation, safe terminalization for stale/deleted/expired roots, and sanitized retries for unexpected task failures. Verification tests must cover the no-selection and mixed/freeform fail-open paths, the structured exact-patch success and all-or-none rejection paths, durable submitted-before-call and duplicate-delivery behavior, quota settlement, and transcript deletion/retention. Assertions may inspect safe lifecycle metadata and encrypted-envelope presence, but must not print clinical source, provider output, patches, or verifier reasoning.

Generation readiness tests must prove ordinary generation and current-source split analysis stay blocked while a live stream is active, finalizing, failed, or recovering, including the empty-transcript source path, and become available after completion or explicit discard. Workers may continue consuming already committed immutable source snapshots.

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

The sequential pytest process or xdist controller, and the standalone API authorization audit, acquire `/tmp/openscribe_pytest.lock` before creating or resetting test infrastructure. A concurrent pytest invocation or audit exits with status `2` instead of sharing/resetting the same PostgreSQL and Redis test infrastructure.

Xdist workers run under the controller lock. Each worker derives a separate PostgreSQL database from `TEST_DATABASE_URL` and receives an isolated SlowAPI key prefix. See [dbtesting.md](dbtesting.md).

The controller checks or creates the test database at startup, even for a pure/static selection. Those tests skip per-test schema and Redis reset work, but PostgreSQL must still be reachable. The lock file can remain after a run; `flock` ownership ends when the handle closes or the process exits. Do not delete the file to bypass contention.

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
pytest -q tests/test_auth_service.py tests/test_auth_email.py tests/test_oidc.py tests/test_cookie_csrf_security.py
```

DSPT legal-content and lifecycle controls:

```bash
.venv/bin/pytest -q tests/test_legal_content.py tests/test_dspt_retention.py
.venv/bin/pytest -q tests/test_migrations.py -k "operator_legal_content or dspt_retention or alembic_upgrade_head_creates_expected_schema"
```

These checks cover restricted Markdown parsing into safe legal blocks, including bold, italics and bounded tables. They also cover harmless formatting removal and warnings, preview and rendering, full-session system-administrator writes, publication and versioning, legal retention and holds, the 24-hour failed-source deadline and durable Vault cleanup, six-calendar-month audit expiry, bounded audit holds, browser state, schedules and additive migrations. They use synthetic content only.

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
pytest -q tests/test_workspace_navigation.py tests/test_workspace_frontend_contract.py tests/test_workspace_scribe_layout_contract.py tests/test_settings_quick_actions.py
```

Dictation modal actions use a synthetic browser harness to verify that Save and Save & Generate wait for final recorder events and one preview result before acting, while stale modal contexts and explicit retry behavior remain fenced:

```bash
pytest -q tests/test_dictation_modal_actions.py
```

Transcript ingestion/generation lifecycle:

```bash
pytest -q tests/test_api.py tests/test_transcript_streaming.py tests/test_retention.py tests/test_task_outbox.py tests/test_quota_lifecycle.py
```

Provider configuration:

```bash
pytest -q tests/test_stt_config.py tests/test_llm_config.py tests/test_deidentification.py
```

Deepgram live-stream browser protocol (synthetic PCM, no database or provider connection):

```bash
pytest -q tests/test_deepgram_live_stream_frontend.py
```

This Node-backed test covers the binary PCM header, pause/resume pre-roll offsets, one-way source ordering, bounded backpressure failure, and exact bounded recovery selection that excludes unsent/ring audio. The streaming route, quota ledger, recovery lifecycle, workspace capability and owner authorization are database-backed and belong in the sequential transcript/API test run.

Generation logging and token reservations:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q tests/test_generation_usage_logging.py tests/test_generation_task_diagnostics.py tests/test_transcript_ingestion_diagnostics.py tests/test_template_suggestions.py tests/test_consultation_split_generation.py tests/test_consultation_split_verification.py tests/test_consultation_split_generation_runtime.py
```

These tests cover content-free operational messages and task tracebacks, generation wait/retry compatibility, ingestion settlement, adapter-specific output-cap reservations, suggestion input expansion and duplicate-worker guards, and optional verification quota fallback. Structured checker call-site logging also has focused coverage in `tests/test_api.py`; select the structured hallucination-check success, invalid-response retry and quota-skip tests when changing those branches. Use synthetic provider payloads and inspect both default messages and structured log fields.

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

## Opt-in local accessibility audit

`tests/accessibility/` provides a first automated WCAG harness. It uses the
pinned local `axe-core` bundle and Playwright. It never loads axe from a CDN.
It starts a disposable localhost app server with the pytest database fixture
and a new synthetic user. It does not use a running local service or a seeded
development account.

Install the test-only axe bundle once:

```bash
cd tests/accessibility
npm ci --ignore-scripts
cd ../..
```

The audit uses the normal browser sign-in form for its synthetic user. The
fixture does not enable MFA, so this audit does not assess MFA. It creates a
synthetic owner, synthetic encrypted transcript/draft data for editor states,
and an empty transcript root for the dictation-dialog path. It never records
audio or submits provider work. `real_db_connections` tests use the isolated
pytest database; application tables are truncated after each such test. The
harness does not use a running local service or seeded account, save browser
authentication state, or capture screenshots. Service workers are disabled.
The browser permits only the exact ephemeral server origin, checks redirect
destinations before each request, and receives a marked synthetic empty
workspace event stream response. This stub does not exercise live workspace stream updates.

```bash
source .venv/bin/activate
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app \
  pytest -q --accessibility-audit tests/accessibility
```

The opt-in suite scans anonymous login, authenticated workspace, Help, empty Scribe,
synthetic editor/copy-review, edited draft, masked and revealed PII, dialog and
notification, rejected-login error, and responsive keyboard states. It also
checks keyboard login and tab activation, editor line add/remove, the existing
copy review gate, dialog focus entry/cancellation/return, select naming, text
contrast for selected elements, and no horizontal overflow at a 320 CSS pixel
viewport. The authenticated Help check adds axe scans for desktop, 320 CSS
pixel, and open mobile-drawer states; keyboard skip-to-main and focused topic
navigation; drawer focus containment and return; selected navigation target
sizes; sidebar resizing; 320/375/740 CSS-pixel reflow; text-spacing overrides;
and forced-colors focus visibility. These are focused automated checks, not complete keyboard or zoom
coverage. They do not test 200% zoom, all focus-obscuration cases, screen-reader
workflows, native microphone permission UI, all error recovery, or exported
document accessibility; use the criterion-level checklist at
[accessibility/wcag-2.2-manual-checklist-2026-10-15.md](accessibility/wcag-2.2-manual-checklist-2026-10-15.md).
The [human testing guide](accessibility/human-testing-guide.md) provides
isolated setup requirements and step-by-step procedures for screen readers,
contrast, keyboard/focus, actual browser zoom/reflow, text spacing, microphone
permission UI, authentication, and exported-document scope. Use its evidence
template and preserve results in the authorized restricted assessment location.
The [16 October evidence summary](accessibility/evidence-summary-2026-10-16.md)
records the next contrast investigation and expanded browser states separately
from the preserved 15 October records. The [18 October Help verification](accessibility/help-verification-2026-10-18.md)
records the authenticated Help-page results and remaining human checks.

`OPENSCRIBE_ACCESSIBILITY_REPORT_DIR` defaults to the ignored
`test-results/accessibility/` directory. Selecting the audit fails clearly if
Playwright, Chromium, the local axe bundle, or the isolated server are
unavailable. The audit is serialized and rejects `pytest -n`. Without
`--accessibility-audit`, browser audit tests are intentionally skipped and do
not affect the normal suite. Each test writes a separately named report; the
audit does not overwrite a report from another test state.

Reports contain bounded, redacted rule diagnostics, state coverage, keyboard
spot-check outcomes, and local axe rule counts for the requested WCAG tags.
Skipped or unexecuted states remain visible in the report. Axe `incomplete`
checks are marked for manual review. Reports do not contain DOM HTML,
screenshots, authentication state, or clinical content. The result is an
automated test record, not a WCAG conformance certification. Review sanitized
reports before preserving or transferring them; dated historical reports are
not overwritten.

## Consultation-split intent progression

The server-owned Create continuation cases are in `tests/test_consultation_split_intent_progress.py`. Run this focused database-backed file sequentially:

```bash
pytest -q tests/test_consultation_split_intent_progress.py
```

## Consultation-split browser regression

`tests/test_consultation_split_browser.py` uses the same disposable local
Uvicorn and Chromium setup. It drives the rendered **Create**, review, save,
confirm, generation, partial **Keep available notes**, and transcript-switch
paths. It also checks passive **Review problems** opening and explicit
**Suggest problems** with automatic splitting off, including single-problem
review without document generation. The harness uses synthetic transcript text and a fake provider response
derived from the confirmed template mode and section keys; it does not use a
live provider or log note content.

Run it with the Playwright setup above:

```bash
pytest -q tests/test_consultation_split_browser.py
```

## Manual file-ingestion smoke test

With a local app, worker, Beat, STT configuration, and test user available:

```bash
OPENSCRIBE_EMAIL='user@example.com' \
OPENSCRIBE_PASSWORD='password-1' \
./scripts/test_file_ingestion.sh tests/example_audio.wav
```

Before running it, add a synthetic, non-patient WAV file at `tests/example_audio.wav`; that local file is ignored by Git. The script stops before login or upload when the file is missing. It uses the real auth flow, prompts for TOTP only when required, starts a whole-file transcript, uploads the sample through the JSON API, and prints bounded response metadata. Do not use a real patient recording as a smoke-test fixture.

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
