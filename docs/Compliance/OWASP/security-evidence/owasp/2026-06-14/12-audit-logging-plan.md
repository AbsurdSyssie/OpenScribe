# 12 - Audit Logging Remediation Plan

Status: local implementation and policy slice complete; live/staging audit proof not captured.  
Finding: `OWASP-2026-06-14-005`  
OWASP: A09 Security Logging and Monitoring Failures  
Date: 2026-06-15

## Scope

This plan covers application security audit events for OpenScribe. It does not replace infrastructure logs, Cloudflare logs, database logs, Vault audit logs, or provider usage telemetry.

Evidence split:

- Live/public: production ZAP/header/cache/metadata evidence only.
- Local authenticated: role crawl evidence for anonymous, seeded normal user, seeded team leader.
- Local tests: existing auth/CSRF/XSS/SSRF/dependency tests.
- Remaining gap: no live/staging authenticated audit proof and no external aggregation/SIEM. A system-admin-only read-only Audit UI exists; no audit API exists in MVP.

## OWASP Baseline

OWASP Logging Cheat Sheet guidance used for this plan:

- Log security events from application code because it knows user identity, roles, permissions, target, action, and outcome.
- Always log authentication success/failure, authorization failures, session management failures, application/system events, admin actions, configuration changes, sensitive-data access, crypto/key activity, user-generated content uploads, data import/export, suspicious business logic, and validation failures where security-relevant.
- Capture enough "when, where, who, what": timestamp, app/service/route, source address, user identity, event type, severity, action, object, result, reason, HTTP status, user agent, confidence/classification.
- Exclude or sanitize session IDs, access tokens, passwords, health data, secrets, keys, database connection strings, and sensitive personal data.
- Use existing framework logging/collection where possible, centralize handlers, sanitize against log injection, and test logging behavior, failure behavior, access control, and resource exhaustion.

Source: <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>

## Current State

Existing reusable pieces:

- `security_audit_events` table exists with indexes by actor, target, action, and timestamp.
- `record_security_event()` exists in `app/services/security_audit.py`.
- Current fields: action, actor user, target user, team, request IP, user agent, details JSON, created timestamp.
- Current sanitization removes obvious sensitive detail keys containing password, temporary password, token, MFA code, TOTP, secret, authorization.
- Account lifecycle has a runtime logger helper `_log_account_lifecycle_event()` in `app/services/admin.py`.
- Rate-limit events are written to `openscribe.security` runtime logger in `app/errors.py`.
- Provider secret cleanup failures are runtime-logged with metadata-only IDs/error codes in STT/LLM/de-identification services.
- Provider usage events exist for LLM/STT usage telemetry but are not a security audit trail.

Current durable audit call sites:

- Temporary recovery password login.
- Manager password reset email sent.
- Manager account recovery email sent.
- Break-glass password reset generated.
- Break-glass account recovery generated.
- Same flows through API/admin/team-management route variants.
- 2026-06-15 implementation slice added login success/failure, logout, password reset request/completion, activation completion, MFA challenge success/failure, onboarding password/TOTP/recovery-code events, account request/user/team lifecycle events, STT/LLM/de-identification provider config/selection events, template/quick-action CRUD events, generated-document deletion, and transcript-root deletion.
- 2026-06-15 follow-up slices added env-gated `CF-Connecting-IP` capture with `AUDIT_TRUST_CLOUDFLARE=true`, CSRF rejection audit, authorization-denial audit, rate-limit audit, invalid reset/setup token failure audit, provider inspect/test audit, default asset/preference/smart-phrase audit, generation queue audit, and audio ingestion queue audit.

Remaining observed gaps:

- Upload and generation queue events now have first-slice durable audit coverage; import/export-specific routes still need review if added.
- Authorization failures, CSRF failures, and rate-limit events have first-slice durable coverage through shared dependencies/handler.
- Session creation/expiry/rotation is partly inferred from login/MFA/logout events but not fully modeled as standalone durable events.
- Provider inspect/test calls and default asset/smart-phrase/preference changes have first-slice durable coverage.
- Audit write-failure behavior is documented: normal audit writes raise on failure; error-handler telemetry is best-effort.
- Audit retention/access-control policy is documented: audit metadata retention is separate from transcript retention; no product UI/API exposes audit rows in MVP.
- Admin detection summaries aggregate the full selected window in SQL; a dedicated creation-time index supports bounded-lookback queries without silently sampling high-volume windows.

## Design Direction

Reuse first:

- Keep `security_audit_events` as durable audit sink.
- Expand `record_security_event()` into a stricter application audit API instead of adding scattered bespoke log rows.
- Keep Python `logging` for operational/runtime telemetry; do not treat runtime logs as compliance audit evidence unless shipped to controlled storage.
- Use SQLAlchemy/FastAPI request context already present; no new logging platform required for first remediation slice.

Likely later integration:

- If external aggregation/SIEM is needed, add structured logging via a maintained package such as `structlog`, or OpenTelemetry/OTLP export through maintained instrumentation. That is phase 2, after durable app audit semantics are correct.

Avoid:

- No request/response body logging.
- No transcript/note/prompt/provider response/audio logging.
- No raw cookie/session/token/authorization/header logging.
- No provider secret, Vault ref secret value, TOTP secret, recovery code, temporary password, reset token, invite/activation token logging.
- No route middleware that blindly writes every request to `security_audit_events`.

## Proposed Audit Event Contract

Add or emulate these fields through schema migration or normalized `details_json` keys:

- `event_type`: canonical action name, stable snake_case.
- `category`: one of `auth`, `session`, `mfa`, `account`, `access_control`, `csrf`, `provider`, `template`, `transcript`, `generated_document`, `upload`, `system`, `rate_limit`.
- `severity`: `info`, `warning`, `error`, `critical`.
- `outcome`: `success`, `failure`, `denied`, `blocked`, `revoked`, `expired`.
- `actor_user_id`: acting user when authenticated.
- `target_user_id`: affected user when applicable.
- `team_id`: affected team.
- `object_type`: stable object class, e.g. `team_stt_config`, `prompt_template`, `transcript`.
- `object_id`: UUID only, never content.
- `route`: request path template or safe path.
- `method`: HTTP method.
- `status_code`: response code where known.
- `reason_code`: stable error/business code, not raw exception text.
- `request_ip`: existing.
- `user_agent`: existing, length-capped and CR/LF sanitized.
- `details_json`: metadata-only allowlist.

Schema choice:

- Phase 1 can keep `details_json` for new fields to reduce migration risk.
- Phase 2 should promote high-cardinality query fields (`category`, `severity`, `outcome`, `object_type`, `object_id`, `route`, `status_code`, `reason_code`) to columns with indexes.

## Event Coverage Plan

Phase 1 - Core audit service hardening:

- [x] Make `record_security_event()` sanitize CR/LF and length-cap string values.
- [x] Add recursive sensitive-key filtering for detail payloads.
- [x] Add safe request metadata extraction: method, path, user agent, IP.
- [x] Add tests proving sensitive keys and nested token/secret/password-ish values are dropped.
- Audit write-failure policy documented: critical/normal audit writes raise; error-handler telemetry logs best-effort failures without sensitive payloads.

Phase 2 - Auth/session/MFA:

- [x] `api_login()`/browser login: audit login success/failure without raw password/email. Failed login stores normalized subject hash only.
- [x] Login success records auth level and trusted-device use flag.
- [x] Logout records session revocation reason.
- Session expiry/revocation/rotation: audit metadata counts for user-level revocations, not raw token hashes.
- [x] MFA challenge: audit success/failure and trusted-device creation flag.
- [x] Onboarding: audit password changed, TOTP enrollment started/verified, recovery codes generated or skipped.
- [x] Password reset/activation confirms: audit successful completion without raw token.
- [x] Invalid/expired password reset and account activation token failures are durable audited without raw token.

Phase 3 - Account/team lifecycle:

- [x] Replace account suspend/reactivate runtime-only logging with durable audit plus runtime log.
- [x] Audit user create, suspend, reactivate, delete.
- [x] Audit account request create/approve/reject with request ID, requested team key, reviewer, outcome. Free-text request details/review notes are excluded.
- [x] Audit bootstrap system-admin creation.
- [x] Audit team create/delete.
- [x] Team hard-delete blockers are durable audited when a linked system-admin account blocks deletion.

Phase 4 - Provider/security configuration:

- [x] STT: draft create, draft finalize, credential replacement, config upsert/delete, team selection set/clear.
- [x] LLM: draft create, draft finalize, credential replacement, config upsert/delete, team selection set/clear, hallucination-check selection set/clear, user preference set/clear.
- [x] De-identification/clinical NLP: provider upsert/delete, assignment add/remove, team selection set/clear.
- [x] Provider inspect calls and saved STT test calls are durable audited without provider responses, sample transcript, or bearer tokens.
- Vault: log metadata-only secret write/delete failure/success at DB transaction boundary where safe. Never log secret values or full bearer tokens.

Phase 5 - Assets and transcript lifecycle:

- [x] Templates/quick actions: create/update/delete metadata-only.
- [x] Default asset create/update/delete events are durable audited without names/descriptions/prompt text.
- [x] User preferences and smart phrases are metadata-only audited; smart phrase trigger/description/expansion text is not stored in audit rows.
- [x] Transcript root deletion: audit owner, transcript IDs, team ID, delete count.
- [x] Generated document deletion: audit owner, document ID, transcript ID.
- [x] Upload queue: audio ingestion accepted events audit size/duration/job/transcript IDs only, no filename/audio content.
- [x] Generation queue: audit generator type, template/quick-action ID, provider config ID, transcript/document IDs, and waiting flag; never prompt/output/transcript text.

Phase 6 - Denials and abuse signals:

- [x] Authorization failures: shared dependencies persist denied route/method/reason and actor/team when known.
- [x] CSRF failures: persist failed unsafe request route/method/reason only.
- [x] Rate limits: persist rate-limit events from the shared rate-limit handler.
- [x] Validation failures: persist only high-signal security validation failures, not every 422. Current coverage includes remote non-HTTPS provider endpoint rejection and secret-bearing de-identification header/body rejection.

## Tests Needed

- Unit: `record_security_event()` redacts sensitive keys recursively, sanitizes CR/LF, length-caps user-agent/details strings.
- Unit/API: request IP honors proxy/CDN trust env only when enabled (`AUDIT_TRUST_X_FORWARDED_FOR`, `AUDIT_TRUST_CLOUDFLARE`).
- API: login success/failure creates correct audit events without raw password/email enumeration leakage.
- API: MFA success/failure/trusted-device creation audited without codes/secrets.
- API: suspend/reactivate/delete user audited and sessions revoked.
- API: provider config upsert/delete/credential replacement/inspect/test audited without secrets, Vault secret payloads, provider responses, or sample transcript.
- API/service: template/quick-action/default-asset/preference/smart-phrase CRUD audited without prompt/template/smart-phrase text.
- API: transcript/generated-document deletion audited without content and after ownership checks.
- Failure: audit write-failure behavior is documented; focused tests cover best-effort error-handler audit rows excluding submitted secrets.
- Migration/schema: new audit columns/indexes, if added, match expected schema.

Run focused tests sequentially with `.venv/bin/pytest -q ...`.

2026-06-15 focused tests:

- `.venv/bin/pytest -q tests/test_security_audit.py` -> 14 passed after token/provider/preference/default-asset/generation/upload slice.
- `.venv/bin/pytest -q tests/test_security_audit.py` -> 16 passed after team-delete blocker and high-signal validation audit slice.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py tests/test_api.py -k "csrf or auth or mfa or login"` -> 52 passed, 325 deselected.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py tests/test_api.py -k "csrf or auth or mfa or login or rate_limited or rate_limit"` -> 53 passed, 324 deselected.

## Acceptance Criteria

- `OWASP-2026-06-14-005` can close as local/test-evidenced when durable audit exists for representative auth/session/MFA/account/admin/provider/template/lifecycle events and remaining exclusions are documented as accepted or out of MVP scope.
- Audit events contain enough metadata to reconstruct actor, target, action, outcome, time, route, team, and object ID.
- Audit events contain no transcript/note/prompt/provider response/audio content and no secrets/tokens/cookies/passwords/MFA codes.
- Tests cover redaction/sanitization and representative events across auth, account lifecycle, provider config, template config, and deletion flows.
- Docs describe retention, access control, and evidence boundaries.

## Open Decisions

- Audit retention period: documented as security/compliance metadata retention, separate from transcript retention. No automatic audit purge in MVP.
- Audit UI/API: system-admin read-only UI exists for detection signals and allowlisted metadata-only recent events. It shows all events in the selected window by default and DB-populates action/category/outcome/origin-IP dropdowns from `security_audit_events`. Audit API/export is absent in MVP. Team-leader audit views need separate design before implementation.
- Audit write failure behavior: documented. Normal audit writes raise; shared error-handler telemetry is best-effort and runtime-logged on audit failure.
- Whether to store hashed normalized email for failed login correlation. Useful for attack detection, but privacy tradeoff.
- Whether to add external log aggregation now or defer; deferred for this local evidence pack.

## Architecture Checkpoint

- Privacy: plan forbids transcript-derived content, prompts, provider responses, audio, secrets, tokens, cookies, and MFA codes in audit data.
- Ownership: audit events track actor/target/team IDs only; no new content visibility is introduced.
- Deletion: transcript-root and user/team deletion should create metadata-only audit before/at deletion; audit retention must be explicitly separate from deleted content retention.
- Provider rules: provider audit is metadata-only and must not expose raw credentials; Vault cleanup ordering remains unchanged.
- Structured-note contract: no schema or structured-note output behavior change.
