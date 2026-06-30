# Review regressions worklist

Source: patch review received 2026-06-30. This file is an implementation queue, not proof that every reported behavior has been reproduced.

## Required workflow

- [ ] Reproduce each item before changing code. Preserve a failing regression test where practical. Non-browser regressions reproduced through focused tests; CSP browser test is added but local env skipped because `playwright.sync_api` is unavailable.
- [x] Keep fixes scoped. Do not weaken CSP, password policy, validation redaction, audit durability, or production secret handling to make tests pass.
- [x] Run focused tests after each item, then the full `.venv/bin/pytest -q` suite when all items are complete.
- [x] Update relevant security/auth/testing docs and `docs/progress` daily note with final evidence.

## P1 — verify and preserve dynamic UI styling under strict CSP

### Reported regression

[`app/security_headers.py:18`](app/security_headers.py#L18) emits `style-src-attr 'none'`. Review reports that runtime `HTMLElement.style` mutations are blocked, breaking workspace resizing, smart-phrase positioning, tours, textarea autosizing, visualizers, and admin charts.

Current docs explicitly claim direct CSSOM property assignment is permitted under this policy: [`docs/security.md:160`](docs/security.md#L160). Treat this code/doc/review conflict as first investigation target. Do not remove `style-src-attr 'none'` merely to restore behavior.

### Known dynamic-style call sites

- Workspace split and drag state: [`app/static/js/transcribe/layout.js:43`](app/static/js/transcribe/layout.js#L43), [`app/static/js/transcribe/layout.js:87`](app/static/js/transcribe/layout.js#L87), [`app/static/js/transcribe/layout.js:100`](app/static/js/transcribe/layout.js#L100).
- Smart-phrase menu position: [`app/static/js/transcribe/smart-phrases.js:63`](app/static/js/transcribe/smart-phrases.js#L63).
- Structured textarea autosize: [`app/static/js/transcribe/structured.js:40`](app/static/js/transcribe/structured.js#L40).
- Audio visualizer CSS variables: [`app/static/js/transcribe/media.js:140`](app/static/js/transcribe/media.js#L140) and [`app/static/js/transcribe/app.js:1411`](app/static/js/transcribe/app.js#L1411).
- Transcribe tour geometry: [`app/static/js/transcribe/tour.js:47`](app/static/js/transcribe/tour.js#L47).
- Home tour geometry: [`app/templates/home.html:2761`](app/templates/home.html#L2761).
- Admin chart percentages: [`app/templates/admin.html:3104`](app/templates/admin.html#L3104).
- Admin floating panels/menus: [`app/templates/admin2.html:1188`](app/templates/admin2.html#L1188) and [`app/templates/admin2.html:1394`](app/templates/admin2.html#L1394).

### Fix requirements

- [ ] Reproduce in Chromium with console CSP violation capture while exercising every behavior above. Existing [`tests/test_csrf_browser.py:95`](tests/test_csrf_browser.py#L95) checks only static home styles and does not exercise mutations. Added regression coverage in `tests/test_csrf_browser.py`; local run skips until Playwright is installed.
- [x] If direct property assignment works, retain strict CSP and add browser tests proving all critical interactions; correct review premise/document any browser-specific exception found.
- [ ] If any mutation is blocked, replace affected mutation with same-origin stylesheet classes, bounded `data-*` state, or another CSP-compatible mechanism. Use nonce-authorized `<style>` only where fixed classes cannot express required geometry. Never interpolate untrusted text into CSS.
- [ ] Keep static CSP assertions in [`tests/test_xss_coverage.py:150`](tests/test_xss_coverage.py#L150), [`tests/test_cookie_csrf_security.py:257`](tests/test_cookie_csrf_security.py#L257), and [`tests/test_csrf_browser.py:128`](tests/test_csrf_browser.py#L128).

### Acceptance evidence

- No CSP console violations during resize, menu placement, tours, autosize, visualizer, or admin chart/panel interaction.
- Dynamic computed styles visibly change as expected.
- `style-src-attr 'none'` remains in response CSP.
- Focused: `.venv/bin/pytest -q tests/test_csrf_browser.py tests/test_xss_coverage.py tests/test_cookie_csrf_security.py`.

## P1 — complete permanent-password policy rollout

### Reported regression

Permanent passwords now require 12 characters plus uppercase, lowercase, and number in [`app/services/passwords.py:42`](app/services/passwords.py#L42) and request schemas such as [`app/schemas/auth.py:21`](app/schemas/auth.py#L21). `BetterPass1` is only 11 characters, so onboarding/MFA flows that submit it no longer advance.

Stale UI contract:

- Onboarding says “at least 8” and uses `minlength="8"`: [`app/templates/onboarding.html:54`](app/templates/onboarding.html#L54).
- Activation/reset confirmation uses `minlength="8"`: [`app/templates/password_reset_confirm.html:25`](app/templates/password_reset_confirm.html#L25).
- Bootstrap creates a permanent system-admin password but uses `minlength="8"`: [`app/templates/login.html:406`](app/templates/login.html#L406).

Do not mechanically change normal login or administrator-created temporary-password fields. Existing/temporary passwords have a distinct contract: [`app/schemas/auth.py:10`](app/schemas/auth.py#L10), [`app/templates/login.html:433`](app/templates/login.html#L433), and temporary-password forms such as [`app/templates/home.html:2335`](app/templates/home.html#L2335).

### Failing/stale test references

- [`tests/test_api.py:2346`](tests/test_api.py#L2346), [`tests/test_api.py:10801`](tests/test_api.py#L10801), and onboarding/MFA block [`tests/test_api.py:11140`](tests/test_api.py#L11140) through [`tests/test_api.py:11410`](tests/test_api.py#L11410).
- Web onboarding flow: [`tests/test_admin_ui.py:6937`](tests/test_admin_ui.py#L6937).
- Documentation example: [`docs/dbtesting.md:434`](docs/dbtesting.md#L434).
- Policy source: [`docs/auth.md:152`](docs/auth.md#L152) and [`docs/security.md:31`](docs/security.md#L31).

### Fix requirements

- [x] Replace `BetterPass1` with one shared, synthetic, policy-compliant test value or fixture; keep deliberate weak-password cases weak.
- [x] Change new permanent-password inputs to `minlength="12"` and show full complexity requirements.
- [x] Confirm bootstrap route uses `validate_password_strength`; add coverage if absent.
- [x] Leave current-login and temporary-password minimums unchanged unless their server-side contract is intentionally changed and documented.
- [x] Add assertions that 11-character onboarding passwords fail and compliant passwords advance onboarding without bypassing MFA.

### Acceptance evidence

- Focused: `.venv/bin/pytest -q tests/test_api.py tests/test_admin_ui.py tests/test_auth_email.py -k "password or onboarding or mfa or bootstrap or activation"`.
- No remaining `BetterPass1` references outside this worklist/source description.
- Permanent-password browser copy matches backend policy.

## P1 — bound audit User-Agent including truncation marker

### Reported regression

[`app/services/security_audit.py:55`](app/services/security_audit.py#L55) slices to 1024 characters and then appends `...[truncated]`, producing a value longer than the [`SecurityAuditEvent.user_agent` 1024-character column](app/models.py#L395). PostgreSQL can reject the insert; [`record_security_event`](app/services/security_audit.py#L142) catches the exception, so an attacker-controlled long header can suppress that audit event.

### Fix requirements

- [x] Make truncation result, including marker, no longer than the requested/storage limit. Prefer a reusable helper accepting `max_length`; keep details/IP limits explicit.
- [x] Test exact boundary, one character over, newline expansion near boundary, and a long request `User-Agent` persisted through `record_security_event`.
- [x] Assert stored value length is `<= 1024` and ends with the marker when truncated.
- [x] Preserve best-effort isolated audit transaction behavior; do not let audit failure alter request outcome.

### Acceptance evidence

- Add regression coverage near [`tests/test_security_audit.py:23`](tests/test_security_audit.py#L23) and long-header integration coverage near [`tests/test_security_audit.py:132`](tests/test_security_audit.py#L132).
- Focused: `.venv/bin/pytest -q tests/test_security_audit.py tests/test_auth_email.py -k "audit or user_agent"`.

## P1 — update validation-response assertion to redacted contract

### Reported regression

Validation responses intentionally expose only `issue_count`: [`app/errors.py:41`](app/errors.py#L41). [`test_generate_output_rejects_transient_structured_context_payload`](tests/test_api.py#L8009) still asserts response text contains `structured_context` and `extra_forbidden` at [`tests/test_api.py:8064`](tests/test_api.py#L8064), contradicting that redacted contract.

The endpoint must still reject transient `structured_context`; saved sources remain the only generation inputs: [`docs/api.md:501`](docs/api.md#L501) and [`docs/working_note_implementation.md:129`](docs/working_note_implementation.md#L129).

### Fix requirements

- [x] Keep status `422` and assert exact public error shape/code plus `details.issue_count` as emitted by the validation handler.
- [x] Assert `structured_context`, rejected values, field locations, and Pydantic error types are absent from response.
- [x] Preserve assertions that no generated document exists and no transcript structured context is persisted.

### Acceptance evidence

- Focused: `.venv/bin/pytest -q tests/test_api.py -k "generate_output_rejects_transient_structured_context_payload"`.

## P2 — use production secret material for audit subject hashes

### Reported regression

[`app/services/security_audit.py:128`](app/services/security_audit.py#L128) falls back to public constant `openscribe-dev-audit-subject-hash` when `AUDIT_SUBJECT_HASH_SECRET`, `SECRET_KEY`, and `CSRF_SECRET` are absent. Production supports that exact environment shape by resolving a stable secret from Vault in [`app/services/csrf.py:20`](app/services/csrf.py#L20) via [`app/services/vault.py:482`](app/services/vault.py#L482). Result: login email hashes can be dictionary attacked with known key.

Docs currently promise fallback to configured application secret: [`docs/security.md:300`](docs/security.md#L300).

### Fix requirements

- [x] Keep `AUDIT_SUBJECT_HASH_SECRET` as highest-priority explicit override.
- [x] In production, use resolved stable application/Vault secret or fail startup unless a dedicated audit secret is configured. Never use public fallback in production.
- [x] Avoid importing `app.services.csrf` directly into `security_audit`: `app.errors` imports `security_audit`, while `csrf` imports `app.errors`, creating circular-import risk. Extract a neutral secret resolver or validate/cache production audit secret during startup.
- [x] Keep dev/test fallback only in explicit local/dev/test environments.
- [x] Add tests for override precedence, explicit app secret, Vault-only production resolution, production failure when resolution fails, and local fallback.
- [x] Never log secret material or raw email addresses.

### Acceptance evidence

- Extend [`tests/test_security_audit.py:148`](tests/test_security_audit.py#L148) and production-secret tests in [`tests/test_cookie_csrf_security.py`](tests/test_cookie_csrf_security.py).
- Focused: `.venv/bin/pytest -q tests/test_security_audit.py tests/test_cookie_csrf_security.py tests/test_auth_email.py -k "secret or subject_hash or login"`.

## Completion checklist

- [x] Code complete for all reproduced findings.
- [x] Relevant tests added/updated for cause, not only current output.
- [x] Full suite passes: `.venv/bin/pytest -q`.
- [x] Security/auth/API/testing docs match final behavior.
- [x] Open issues and browser/environment assumptions recorded.
- [x] Architecture checkpoint: no transcript content exposure, ownership/deletion/provider behavior unchanged, transient structured-note input still rejected, no secret or raw subject added to logs.
