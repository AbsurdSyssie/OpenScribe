# 2026-07-16 Daily Progress Note

## 1. Scope

Implemented race-safe per-user daily/monthly token and audio quotas, durable provider-attempt accounting, task dispatch outbox/lifecycle reconciliation, all LLM/STT billable-call integration, and system-admin quota management in canonical `/admin`.

## 2. Checklist

- [x] Schema, migration, constraints, indexes, deletion FKs.
- [x] UTC windows, grants, resets, prospective activation, atomic reservations and settlement.
- [x] Durable generation/ingestion dispatch and duplicate-delivery protection.
- [x] Main/checker LLM, async/sync STT, retry, and provider-test accounting.
- [x] User/transcript/document/team deletion and retention lifecycle.
- [x] System-admin limits, grants, resets, revocation, history, CSRF, PRG, and idempotency.
- [x] Documentation and focused/full affected-suite verification.
- [ ] Whole-repository suite not run. Selected unrelated admin UI failures were reproduced on clean `HEAD`.
- [ ] No commit made.

## 3. Files changed

- Schema/migration: `app/models.py`, `alembic/versions/c1d2e3f4a5b6_add_quota_accounting_foundation.py`.
- Accounting/lifecycle: `app/services/quotas.py`, `admin_quotas.py`, `task_outbox.py`, `quota_lifecycle.py`, `provider_errors.py`.
- Provider paths: `templates.py`, `transcripts.py`, `dictations.py`, `stt.py`, STT schema, routes, Celery tasks/schedule.
- Admin UI: `web_admin.py`, presentation context, `admin_mockup.html`.
- Tests: migration, quota, outbox, lifecycle, API, admin, CSRF, retention, security audit.
- Docs: database, API, setup, testing, DB testing, admin, usage, implementation plan.
- `AGENTS.md` had a pre-existing modification and was not changed by this work.

## 4. Tests

- `.venv/bin/pytest -q tests/test_api.py` — **400 passed**.
- Migration/quota/outbox/lifecycle/retention matrix — **86 passed**.
- Selected quota/admin/browser matrix — **13 passed**.
- Quota CSRF matrix — **4 passed**.
- `git diff --check` — passed.
- Warnings: existing Starlette/httpx and HTTP 422 deprecations only.

## 5. Documentation

Updated database, API, setup, testing, admin workspace, usage, DB-testing, and quota-plan documentation. Added this dated progress note.

## 6. Risks / assumptions

- Quota enforcement starts prospectively; `ProviderAttempt` has no historical telemetry backfill.
- Provider-attempt owner/content team scope is enforced centrally in `reserve_provider_attempt`; independent nullable `SET NULL` FKs prevent a simple composite FK without changing deletion semantics.
- Existing `ProviderUsageEvent` rows remain reporting telemetry; `ProviderAttempt` is authoritative for enforcement.
- Free-text quota reasons are admin-visible metadata. UI/docs prohibit patient, transcript, prompt, credential, or other confidential content; security audit stores controlled reason codes only.
- Selected unrelated admin UI failures exist on clean `HEAD`; not changed to chase whole-suite green.

## 7. Architecture checkpoint summary

- **Privacy:** attempt/outbox rows contain metadata only. Raw prompts, transcripts, notes, audio, secrets, and provider-returned error strings are excluded. Legacy STT URLs are sanitized.
- **Ownership:** all normal-user attempts validate owner, team, and linked content scope. Only system admins can mutate quota policy; target must be a normal team user.
- **Deletion:** active reservations cancel; submitted calls settle conservatively; content/user links become null; transcript children cascade; team deletion removes attempts; outbox metadata is removed with hard-deleted sources.
- **Provider rules:** existing team/user provider selection remains authoritative. One explicit request maps to one attempt; hidden OpenAI retries are disabled. Token usage settles reported/unknown; audio settles server measurement.
- **Structured-note contract:** EMIS keys, JSON validation, redaction, and ownership behavior remain unchanged. Hallucination-check quota exhaustion preserves the valid main note and records `skipped_quota`.

---

## Follow-up: relaxed provider-call safeguards

### Scope

Relaxed STT and LLM route throttles now that per-user quota accounting is authoritative. Quotas remain system-admin-only abuse-monitoring metadata; normal users and team leaders receive no quota values, remaining allowance, warnings, or reset times.

### Checklist and checkpoints

- [x] Auth safeguards unchanged: login, MFA, and public account-request limits retain existing values.
- [x] Live, whole-file, and LLM route safeguards raised and made environment-configurable.
- [x] Rolling audio-duration rate budgets disabled by default; audio quota remains authoritative.
- [x] Whole-file byte ceiling retained and raised to 1 GiB/hour because quota does not bound upload/storage bytes.
- [x] Internal quota failures map to generic owner-facing `usage_unavailable` without quota details.
- [x] Live client retries only `rate_limited` and honors `Retry-After`.
- [x] Admin quota table uses readable token/audio units and distinguishes accepted in-progress work.
- [x] No schema, ownership, deletion, provider-selection, or structured-note contract change.

### Files and documentation

- Runtime/UI: `app/main.py`, `app/errors.py`, `app/services/quotas.py`, `app/services/transcripts.py`, `app/static/js/transcribe/app.js`, `app/static/js/transcribe/media.js`, `app/templates/admin_mockup.html`, `app/web/templates.py`, `.env.example`.
- Tests: focused API/error/admin UI coverage for new defaults, public error sanitization, limiter behavior, and admin formatting.
- Docs: API, auth, setup, security, live/transcript capture, testing, DB testing, admin workspace, and usage references updated.

### Risks / assumptions

- Request ceilings remain emergency infrastructure safeguards, not expenditure controls.
- Administrators are responsible for configuring finite quota policy where desired; nullable quota values remain unlimited by contract.
- Exact quota state remains available only through system-admin quota management.

### Verification

- Focused limiter/error/admin UI matrix: **13 passed**, 625 deselected.
- Broader quota service and admin UI regression: **28 passed**, 223 deselected.
- JavaScript syntax, Python compile, and `git diff --check`: passed.
- Existing Starlette/httpx deprecation warning only.
