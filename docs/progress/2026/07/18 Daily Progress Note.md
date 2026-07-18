# 2026-07-18 Daily Progress Note

## 1. Scope

Made quota exhaustion actionable in owner-facing API and browser UI. Users now see that their quota is used up and should contact their administrator instead of a generic service-unavailable message.

## 2. Checklist

- [x] Code complete.
- [x] Focused API, browser UI, and error-boundary tests added or updated.
- [x] Active quota API, security, capture, testing, admin, and usage docs updated.
- [x] No schema or migration change.
- [x] No auth, ownership, deletion, provider-resolution, or structured-note change.
- [ ] Whole-repository suite not run.

## 3. Files changed

- `app/errors.py`: shared safe public quota message and sanitized public error mapping.
- `app/services/quotas.py`: browser-form quota failures use same safe message.
- `tests/test_errors.py`, `tests/test_api.py`, `tests/test_admin_ui.py`: boundary, API, and rendered browser regression coverage.
- Active quota contract docs: API, testing, DB testing, security, transcript capture, live STT, admin, and usage references.

## 4. Tests

- Focused regression: public `quota_disabled` and `quota_exceeded`, synchronous STT rejection, and browser note-generation rejection.
- Verified no internal limit, usage, remaining allowance, period, or reset metadata reaches owner-facing response/UI.

## 5. Documentation

Replaced stale `usage_unavailable` contract with safe public `quota_exceeded` behavior. Clarified that normal users receive failure guidance but no quota dashboard or policy metadata.

## 6. Risks / assumptions

- Both zero allowance and exhausted finite allowance intentionally use same public message. Internal codes and HTTP statuses remain distinct for service/admin diagnostics.
- “Administrator” means system administrator because quota policy remains system-admin-only.
- Clients continue treating quota failure as terminal and retry only route-level `rate_limited` responses.

## 7. Architecture checkpoint summary

- **Privacy:** only failure category and action guidance exposed; exact limits, consumption, remaining allowance, window, and reset time remain private admin metadata.
- **Ownership:** existing authenticated owner checks unchanged.
- **Deletion:** no persistence or lifecycle behavior changed.
- **Provider rules:** reservation and provider selection unchanged; rejected requests still make no provider call.
- **Structured-note contract:** EMIS structure and generated-content handling unchanged.
