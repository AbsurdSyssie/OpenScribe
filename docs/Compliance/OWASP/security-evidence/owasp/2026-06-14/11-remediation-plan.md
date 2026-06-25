# 11 - Remediation Plan

Date: 2026-06-14  
Scope: remediation plan for public passive recon and server fingerprinting findings from `openscribe.co.uk`.  
Status: active remediation tracker. R-002 is implemented, deployed, and ZAP-retested successfully; authenticated role crawl is closed as local/test-evidenced; audit logging is closed as local/test-evidenced, not live/staging-proven. R-019 is implemented, deployed, and production-retested successfully after follow-up signal-key masking.

## Principles

- Do not weaken owner-only transcript-derived content boundaries.
- Do not change deletion, retention, encryption, provider-resolution, or structured-note contracts without architecture review.
- Prefer environment-gated production hardening over removing useful local development behavior.
- Keep retest evidence in `10-retest-log.md` and update `09-findings-and-remediation.md` when each item is fixed or accepted.
- Treat scanner alerts as triage inputs, not proof, until reproduced with focused tests.

## Priority Order

| Priority | Finding | Reason |
| --- | --- | --- |
| 1 | `OWASP-2026-06-14-005` Audit event logging | Security events are logger-only, not durable audit records. |
| 2 | `OWASP-2026-06-14-009` public `/docs` and `/openapi.json` | Public route/schema inventory helps attackers enumerate admin/provider/transcript surfaces. Implemented and production-retested for unauthenticated exposure closure. |
| 3 | `OWASP-2026-06-14-002` Auth role crawl | Local role crawl complete; staging/production auth crawl remains optional and requires explicit authorization. |
| 4 | `OWASP-2026-06-14-019` Private IP disclosure in admin audit UI | External admin crawl found raw private audit origin metadata in system-admin HTML. Table/dropdown masking deployed, first retest found raw private IP in signal key text, follow-up signal-key masking deployed, final ZAP retest passed. |

## R-019 Private IP Disclosure In Admin Audit UI

Finding: `OWASP-2026-06-14-019`
Severity: Low
OWASP: A01 Broken Access Control, A05 Security Misconfiguration, A09 Security Logging and Monitoring

Current status: resolved and production-retested on `https://openscribe.co.uk`.

### Target Behavior

- Admin audit pages should not render raw private, loopback, or link-local origin IPs in HTML.
- Durable audit storage should retain raw request IP metadata for incident response.
- Public origin IPs may remain visible/filterable to system admins.
- Private/internal origin filtering should not require putting the raw private IP in form option values.

### Implementation Plan

1. Add a presentation helper around audit-event origin IP rendering.
2. Mask private, loopback, and link-local addresses as `Private/internal IP masked`.
3. Keep raw `security_audit_events.request_ip` unchanged for durable audit metadata and direct operator filtering.
4. Use masked display values in both legacy and restyled admin audit tables.
5. Omit private/internal origins from audit filter dropdown option values so raw private IPs are not emitted in HTML.
6. Mask private/internal audit detection signal display keys while keeping internal grouping keyed on raw IP.
7. Preserve subject-hash signal redaction placeholders in presentation code.
8. Add a focused admin UI regression test proving the raw private IP is absent from the rendered page, including a private-IP burst signal.

### Tests

- Local focused test: `.venv/bin/pytest -q tests/test_admin_ui.py -k "audit"` passed on 2026-06-24.
- Test verifies public origin IPs still render, private origin IP `192.168.1.234` is absent from table, dropdown, and signal HTML, and `Private/internal IP masked` is rendered.
- First production ZAP retest after restart failed with 4 `Private IP Disclosure` alerts because signal text still used the raw private IP; evidence is in `07-tool-outputs/zap/zap-auth-crawl-external-admin-private-ip-retest-2026-06-24-summary.md`.
- Final production ZAP retest after follow-up restart passed with 0 private-IP regex hits in audit HTML and 0 `Private IP Disclosure` alerts; evidence is in `07-tool-outputs/zap/zap-auth-crawl-external-admin-private-ip-retest-pass-2026-06-24-summary.md`.

### Acceptance Criteria

- Local regression test passes. Complete.
- Production safe external system-admin ZAP GET-only crawl confirms `Private IP Disclosure` no longer appears for audit pages. Complete.

## R-001 Public API Docs And OpenAPI Schema

Finding: `OWASP-2026-06-14-009`  
Severity: Medium  
OWASP: A05 Security Misconfiguration, A09 Security Logging and Monitoring

Current status: resolved and production-retested for unauthenticated exposure closure. Project owner initially accepted public docs because OpenScribe is open source, then chose to require auth in production to raise the recon barrier.

### Target Behavior

Production should not expose development/API documentation unless explicitly accepted by product/security owners. If this acceptance changes, preferred behavior is:

- Local/dev: `/docs`, `/redoc`, and `/openapi.json` remain available for developer use.
- Production: `/docs`, `/redoc`, and `/openapi.json` require full system-admin authentication by default.
- Explicit override: `PUBLIC_API_DOCS=true` can expose docs when a deployment intentionally accepts public schema exposure.

### Implementation Plan

1. Done: inspected FastAPI app construction and docs/openapi configuration.
2. Done: disabled built-in public docs routes and added custom `/docs`, `/redoc`, and `/openapi.json`.
3. Done: local/test default to public docs; production defaults to full system-admin auth.
4. Done: added explicit `PUBLIC_API_DOCS=true|false` override.
5. Done: ensured route audit/test tooling can still access OpenAPI in local/test mode.
6. Done: updated setup/security docs and OWASP evidence with the production policy.

### Tests

- Done: local focused test `.venv/bin/pytest -q tests/test_api.py -k "api_docs"`.
- Assert local/test mode exposes `/docs`, `/redoc`, and `/openapi.json` for developer tooling.
- Assert production mode denies unauthenticated docs/schema, denies normal user, allows full system admin, and respects explicit `PUBLIC_API_DOCS=true`.
- Done: production unauthenticated retest confirmed `/docs`, `/redoc`, and `/openapi.json` return `401`.
- Note: configured OWASP admin session was invalid during the 2026-06-25 live check, so authenticated docs usability can be retested later with a fresh full-admin session if needed.

### Acceptance Criteria

- Local/test regression passes. Complete.
- Production URL no longer exposes API docs/schema to unauthenticated users after deploy. Complete.
- Retest evidence in `10-retest-log.md` shows final status. Complete for unauthenticated exposure closure.

## R-002 Public Form CSRF Evidence Mismatch

Finding: `OWASP-2026-06-14-011`  
Severity: Medium  
OWASP: A05 Security Misconfiguration, A08 Software and Data Integrity Failures

Current status: resolved. Remediated in code on 2026-06-14, deployed to `https://openscribe.co.uk`, and production ZAP baseline retest passed for anti-CSRF token evidence on 2026-06-14.

### Target Behavior

Public browser forms must have CSRF protection that is visible and testable:

- `/login`, `/forgot-password`, and `/request-access` should include a hidden CSRF field if server-side form validation expects it.
- If protection relies on signed readable CSRF cookie plus header/origin checks, public form POST behavior must be explicitly documented and tested.
- Invalid/missing CSRF submissions should fail safely without account enumeration or sensitive error detail.

### Implementation Plan

1. Inspect rendered forms for hidden CSRF field names and server-side validation expectations.
2. Reproduce ZAP observation with `curl`/browser: GET page, inspect form, POST with no token, POST with invalid token, POST with valid token.
3. If forms lack hidden token but server expects one, add `_csrf_token` hidden fields consistently. Completed for login, bootstrap system-admin, forgot-password, and request-access forms.
4. If public forms intentionally use cookie/origin-only protection, document this and add regression tests proving missing/bad origin/cookie/token behavior.
5. Avoid logging submitted passwords, reset emails, names, or request details during tests.

### Tests

- Browser/API CSRF tests for `/login`, `/forgot-password`, `/request-access`.
- Negative tests: missing CSRF, invalid CSRF, cross-origin `Origin`/`Referer`.
- Positive tests: valid public form submission with synthetic data.
- Re-run ZAP baseline or focused passive check; record whether alert resolved or accepted as false positive. Completed on production with `zap-baseline-retest-2026-06-14.*`.

### Acceptance Criteria

- CSRF behavior is deterministic, documented, and covered by tests. Completed locally.
- ZAP alert is resolved by production retest evidence. `Absence of Anti-CSRF Tokens [10202]` passed, and retest report records `_csrf_token` on public form nodes.

## R-003 Duplicate HSTS Header Triage

Finding: `OWASP-2026-06-14-012`  
Severity: Low  
OWASP: A05 Security Misconfiguration

Current status: resolved by Cloudflare configuration and production retest. Cloudflare HSTS is enabled for 6 months with include subdomains and preload off; `/login` and `/static/vendor/lucide/1.8.0/lucide.min.js` both sampled one Cloudflare HSTS header (`max-age=15552000; includeSubDomains`). ZAP reported `PASS: Strict-Transport-Security Header [10035]`.

### Target Behavior

Each HTTPS response should emit one valid `Strict-Transport-Security` header from one layer.

### Implementation Plan

1. Reproduce exact ZAP paths/methods with header capture:
   - `GET /robots.txt`
   - `GET /sitemap.xml`
   - public-form POSTs that returned `403`
2. Capture raw headers without following redirects.
3. Identify whether HSTS is set by FastAPI middleware, reverse proxy, Cloudflare, or multiple layers.
4. Choose one layer as source of truth.
5. Remove duplicate header emission from other layers, preserving current HSTS strength. Implemented app-side switch with `HSTS_SOURCE=app|proxy`; production retest shows duplicate HSTS gone.
6. Ensure static asset responses also receive HSTS from Cloudflare/proxy or app. Header retest found `/static/vendor/lucide/1.8.0/lucide.min.js` missing HSTS; Cloudflare HSTS configuration was updated and static HSTS now passes. `HSTS_SOURCE=proxy_static_fallback` remains available for deployments where proxy HSTS cannot cover static assets, but current production should use `HSTS_SOURCE=proxy`.

### Tests

- Add/adjust header tests for representative HTML, JSON, redirect, and error responses.
- Production retest with `curl -s -D - -o /dev/null` and ZAP baseline.

### Acceptance Criteria

- Every sampled HTTPS response has exactly one HSTS header. Completed for sampled dynamic and static paths.
- HSTS value is documented and intentionally configured in Cloudflare as 6 months, include subdomains, preload off.

## R-004 Security Header Hardening: Permissions-Policy And COEP

Finding: `OWASP-2026-06-14-010`  
Severity: Low  
OWASP: A05 Security Misconfiguration

Current status: resolved by production retest. Shared header middleware emits `Permissions-Policy` and `Cross-Origin-Embedder-Policy: credentialless`; ZAP header retest passed both missing-header checks.

### Target Behavior

Public and authenticated browser responses should declare intentional browser capability boundaries.

Recommended starting policy, subject to compatibility testing:

```text
Permissions-Policy: camera=(), geolocation=(), payment=(), usb=(), fullscreen=(self), microphone=(self)
```

COEP needs caution because strict `require-corp` can break resources, audio capture helpers, WASM/ONNX/runtime assets, or third-party integrations. Preferred path:

- Keep COOP/CORP as current baseline.
- Evaluate COEP on local/staging first.
- Add COEP only if compatible with transcribe/audio/static vendor behavior.

### Implementation Plan

1. Inventory browser features used by OpenScribe: microphone, media blobs, worker/blob, local vendor JS/WASM if active, fonts/images.
2. Add `Permissions-Policy` header in shared security-header middleware.
3. Test microphone recording, upload, login, request access, admin, and transcribe flows.
4. Decide COEP policy:
   - `require-corp` if fully compatible.
   - omit with documented acceptance if it breaks required flows.
   - consider `credentialless` only after browser support review. Implemented `credentialless` as the least disruptive valid COEP policy for current same-origin assets and public docs compatibility.

### Tests

- Header presence test for public and authenticated HTML routes.
- Browser/manual smoke for microphone capture and upload if `Permissions-Policy` restricts microphone to self.
- ZAP baseline retest.

### Acceptance Criteria

- `Permissions-Policy` exists and does not break microphone/audio workflows.
- COEP is either implemented with compatibility evidence or explicitly deferred with rationale.

## R-005 Cache-Control On Public/Auth Pages

Finding source: ZAP `Re-examine Cache-control Directives` and `Storable and Cacheable Content` alerts  
Severity: Info/Low depending on page content  
OWASP: A05 Security Misconfiguration

Current status: accepted after production retest. Public splash/auth/account/API responses emit `Cache-Control: no-store`, `Pragma: no-cache`, and `Expires: 0`; public metadata and static assets emit explicit/cache-edge public caching and do not issue CSRF cookies. Residual ZAP cache alerts are informational design/heuristic alerts.

### Target Behavior

- Public metadata/static pages can be cacheable if they contain no user-specific data and no cookies.
- Public splash and auth-related pages (`/`, `/login`, `/forgot-password`, `/reset-password`, `/activate-account`, `/request-access`) should explicitly set conservative cache headers because they create CSRF cookies or support account/recovery flows.
- `/api/` responses and authenticated transcript/generated-document APIs should use `no-store`.

### Implementation Plan

1. Done: classified route groups in shared security-header middleware.
2. Done: added explicit `no-store` for `/`, public auth/account pages, all `/api/` responses, and sensitive content API prefixes.
3. Done: added explicit `public, max-age=3600` for cookie-free metadata and static assets.

### Tests

- Done: focused local header tests for `/`, `/login`, `/forgot-password`, `/request-access`, `/api/`, metadata routes, and static assets.
- Done: production header sample and ZAP baseline retest after deploy/restart.

### Acceptance Criteria

- Cache policy is explicit for every public/auth page class.
- ZAP cache alerts are either resolved or documented as accepted for public no-store/static/metadata content after production retest.

## R-006 Public Metadata Files: `robots.txt`, `sitemap.xml`, `security.txt`

Finding: `OWASP-2026-06-14-013`  
Severity: Info  
OWASP: A05 Security Misconfiguration, A09 Security Logging and Monitoring

Current status: resolved. OpenScribe publishes explicit `robots.txt` and `security.txt`; `sitemap.xml` returns intentional `404` because no public sitemap is published. Production retest confirmed metadata paths no longer redirect to login and do not set cookies. Follow-up adjusted `robots.txt` from `Allow: /$` to `Allow: /`; after Cloudflare cache purge, ZAP no longer crawled literal `/$`.

### Target Behavior

Decide and document public metadata posture:

- `/.well-known/security.txt`: recommended to publish a supplier-assurance/security contact if the organisation can monitor it.
- `robots.txt`: publish explicit crawl guidance, even if `Disallow: /` is chosen.
- `sitemap.xml`: publish only if public marketing pages should be indexed; otherwise return `404` or omit intentionally.

### Implementation Plan

1. Confirm support/security contact and disclosure policy owner. Completed: `oscar@meddleapp.com`.
2. Add static routes for `/.well-known/security.txt` and `/robots.txt` if approved. Completed.
3. Decide whether public splash page should be indexed; add `sitemap.xml` only if useful. Decision: no sitemap for MVP app; `/sitemap.xml` returns intentional `404`.
4. Ensure metadata routes do not set session or CSRF cookies unnecessarily. Completed for metadata routes and static assets.

### Tests

- Public GET tests for metadata files.
- Header tests: correct content type, no sensitive cookies, appropriate cache behavior.
- Retest public URL with Python capture and ZAP spider.

### Acceptance Criteria

- Metadata-file behavior is intentional and documented. Completed and production-retested.
- `security.txt` exists because a monitored contact exists. Completed and production-retested.

## R-007 CSRF Cookie `HttpOnly` Scanner Warning

Finding source: ZAP `Cookie No HttpOnly Flag` on `openscribe_csrf`  
Severity: Low scanner warning; likely design-accepted  
OWASP: A02 Cryptographic Failures, A05 Security Misconfiguration

Current status: accepted with regression evidence. `openscribe_csrf` remains intentionally readable by same-origin JavaScript so browser API calls can submit `X-CSRF-Token`. It is signed and non-authoritative, while auth-bearing session/trusted-device cookies remain `HttpOnly`.

### Target Behavior

- Session and trusted-device cookies must be `HttpOnly`, `Secure` on HTTPS, and `SameSite=Lax` or stricter where compatible.
- Readable CSRF cookie may remain non-`HttpOnly` only if JavaScript must read it to submit `X-CSRF-Token` and the cookie is signed/non-authoritative.
- Anonymous CSRF cookie design must be documented.

### Implementation Plan

1. Verify `openscribe_session` and `openscribe_trusted_device` flags on authenticated synthetic local/staging sessions. Completed by regression tests.
2. Confirm `openscribe_csrf` contains signed CSRF nonce only and is not a session/auth bearer secret. Completed by regression tests.
3. If browser JS no longer needs readable cookie, switch to hidden/meta token pattern and set `HttpOnly` where possible.
4. Otherwise document scanner exception in OWASP findings and security docs. Completed.

### Tests

- Cookie flag tests for session, trusted-device, anonymous CSRF, authenticated CSRF. Completed in `tests/test_cookie_csrf_security.py`.
- XSS tests remain important because readable CSRF cookie is exposed to same-origin script by design.
- ZAP retest should be marked accepted false positive if design remains. Completed as `OWASP-2026-06-14-014`.

### Acceptance Criteria

- Auth-bearing cookies verified `HttpOnly`. Completed.
- CSRF cookie non-`HttpOnly` has signed-token rationale and regression tests. Completed.

## R-008 Public Search/Archive/Exposure Recon Gaps

Findings: `OWASP-2026-06-14-001`, `OWASP-2026-06-14-002`, passive recon checklist gaps  
Severity: Info  
OWASP: A01, A05, A09

Current status: closed for passive recon evidence. Search engine and Wayback archive checks are complete with evidence (zero indexed pages, zero historical captures). External passive DNS confirms Cloudflare-only edge with effective origin IP protection. Shodan and Censys require authenticated sessions so are marked `Not in scope` for this public passive cycle. `OWASP-2026-06-14-001` is closed because public passive search/archive/DNS recon is evidenced. `OWASP-2026-06-14-002` (authenticated role crawl) is separately closed as local/test-evidenced; staging/production role crawl remains optional and requires explicit authorisation. Evidence: `07-tool-outputs/passive-recon-search-archive-exposure-2026-06-14.md`.

### Target Behavior

Complete passive recon without exceeding authorised scope.

### Implementation Plan

1. Confirm allowed public domains/subdomains.
2. Run browser search and archive checks for `openscribe.co.uk` and approved subdomains.
3. Use Shodan/Censys only if authorised and only for public exposure summary.
4. Store redacted summaries only.

### Tests / Evidence

- Update `04-passive-recon.md` with search/archive/exposure summaries.
- Add new findings if unexpected indexed files, errors, or services appear.

### Acceptance Criteria

- Passive checklist rows move from `Gap` to `Test-evidenced` or documented `Not in scope`.

## R-009 Auth/Session Scanner Auto-Detection

Finding: `OWASP-2026-06-14-016`  
Finding source: ZAP `Authentication Request Identified` and `Session Management Response Identified`  
Severity: Informational  
OWASP: A02 Cryptographic Failures, A05 Security Misconfiguration, A07 Identification and Authentication Failures

Current status: accepted with regression evidence. ZAP identified the expected public `/login` authentication form and anonymous CSRF cookies. It did not identify an exposed auth-bearing session cookie in public responses.

### Target Behavior

- `/login` remains the public browser authentication endpoint and may be identified by scanners as authentication.
- Anonymous CSRF cookies may be issued on public pages, but they must not authenticate the user by themselves.
- Auth-bearing `openscribe_session` and `openscribe_trusted_device` cookies remain `HttpOnly`, `Secure` in production, and `SameSite=Lax`.

### Implementation Plan

1. Confirm ZAP identified only expected auth form fields and CSRF cookie names. Completed.
2. Confirm CSRF cookies alone do not authenticate `/api/v1/auth/me`. Completed by regression tests.
3. Confirm auth-bearing cookies remain `HttpOnly`. Completed by regression tests.
4. Document scanner warning as accepted auto-detection behavior. Completed.

### Tests

- Cookie/session tests for CSRF-cookie-alone non-authentication and auth-bearing cookie flags. Completed in `tests/test_cookie_csrf_security.py`.
- API CSRF tests for unsafe cookie-authenticated requests. Completed in `tests/test_api.py`.

### Acceptance Criteria

- Scanner warnings are tied to expected login/CSRF behavior, not auth-cookie exposure. Completed.
- Regression evidence proves CSRF controls are not auth bearer tokens. Completed.

## R-010 Dependency Upgrade: Starlette CVEs (FastAPI-Constrained)

Finding: `OWASP-2026-06-14-017`
Severity: Medium
OWASP: A06 Vulnerable and Outdated Components, A08 Software and Data Integrity Failures

Current status: resolved on 2026-06-14. FastAPI upgraded from 0.115.0 to 0.137.0; starlette upgraded from 0.38.6 to 1.3.1. pip-audit confirms zero starlette vulns from requirements.txt (only pytest dev-only vuln remains).

### Mitigating Controls in Place

- PYSEC-2026-161 (Host header injection): Cloudflare proxy normalizes Host headers; app does not use `request.url.path` for auth decisions.
- CVE-2024-47874 (form buffering DoS): app has file upload size limits and rate limiting via slowapi.
- CVE-2025-54121 (thread block on upload): same mitigations as CVE-2024-47874.

### Implementation Plan

1. Upgrade FastAPI from 0.115.0 to >=0.137.0 in a dedicated change.
2. Run full test suite to verify no regressions.
3. Confirm starlette upgraded to >=1.0.1.
4. Rerun pip-audit to confirm zero starlette vulns.
5. Deploy and run ZAP baseline retest.

### Tests

- Full pytest suite (all tests).
- pip-audit confirmation.
- Production ZAP baseline.

### Acceptance Criteria

- starlette >=1.0.1 with zero pip-audit vulns.
- No test regressions.
- ZAP baseline passes unchanged.

## R-011 SBOM / Dependency Vulnerability Scan

Finding: `OWASP-2026-06-14-006`
Severity: Low (Info before scan, now Low with resolved direct vulns)
OWASP: A06 Vulnerable and Outdated Components, A08 Software and Data Integrity Failures

Current status: closed on 2026-06-14. pip-audit scan completed; 4 direct dependency vulns fixed (cryptography, requests, python-multipart, idna). Remaining vulns are starlette (FastAPI-constrained, see R-010), pytest (dev-only), and transitive-only packages (mako Windows-only, pip build-time, pygments local ReDoS, urllib3 low-severity).

### Implementation Plan

1. Installed pip-audit in venv.
2. Scanned requirements.txt (27 packages) and full venv (all transitive).
3. Upgraded: cryptography 46.0.3→46.0.7, requests 2.32.5→2.33.0, python-multipart 0.0.22→0.0.32, idna 3.11→3.18.
4. Re-ran pip-audit: 5 remaining vulns in 2 pinned packages (starlette, pytest).
5. Static vendor assets and Docker images inventoried for completeness.
6. Evidence stored in `07-tool-outputs/sbom-pip-audit-2026-06-14.md`.

### Acceptance Criteria

- Direct dependency vulns remediated or documented with compensating controls. Completed.
- SBOM evidence file exists with redacted summary. Completed.
- Remaining starlette CVEs tracked as separate finding (017/R-010).
- Operational: add container image scanning to CI pipeline for postgres:16, redis:7, hashicorp/vault:1.17.

## R-012 XSS Coverage

Finding: `OWASP-2026-06-14-003`
Severity: Medium (2 bugs fixed, now closed)
OWASP: A03 Injection

Current status: closed on 2026-06-14. Two XSS bugs found and fixed. 28 new regression tests pass.

### Implementation Plan

1. Completed: full XSS surface audit (19 templates, all innerHTML sites, CSP config).
2. Completed: fixed `structured.js:1232` — added `escapeHtml()` wrapper for `generatedDocument.error_message`.
3. Completed: fixed `_workspace.html:359` — added `|forceescape` to `|tojson` in single-quoted attribute.
4. Completed: verified zero `|safe` filter usages across all templates.
5. Completed: verified all `innerHTML` assignments use `escapeHtml()` for user values.
6. Completed: verified CSP `script-src-attr 'none'` blocks inline event handlers.

### Acceptance Criteria

- [x] All XSS vectors identified and assessed.
- [x] Bugs fixed and verified.
- [x] Regression tests pass (28/28).
- [x] No `|safe` filter usages.
- [x] CSP blocks inline event handlers.

## R-013 SSRF Canary

Finding: `OWASP-2026-06-14-004`
Severity: Medium
OWASP: A10 Server-Side Request Forgery

Current status: closed on 2026-06-14. SSRF surface mapped and tested. 23 canary tests pass.

### Implementation Plan

1. Completed: mapped all provider inspect/config/test endpoints accepting URLs.
2. Completed: tested URL validation schemas for STT, LLM, de-ID providers.
3. Completed: verified auth requirements (401/403 on unauthenticated inspect calls).
4. Completed: confirmed httpx defaults to `follow_redirects=False`.
5. Recommended future hardening: add metadata-service IP blocklist (169.254.169.254, 100.64.0.0/10) to URL validators.

### Acceptance Criteria

- [x] URL validation contract tested (remote HTTP rejected, HTTPS accepted, local HTTP by design).
- [x] Auth gates verified.
- [x] Redirect SSRF mitigated by httpx default.
- [x] Design constraints documented (no host allowlist, system-admin-only provider config).

## R-014 Audit Logging Expansion

Finding: `OWASP-2026-06-14-005`
Severity: Medium
OWASP: A09 Security Logging and Monitoring

Current status: closed as local/test-evidenced. `security_audit_events` table and `record_security_event()` now cover representative auth/session/MFA/account/provider/template/default-asset/preference/smart-phrase/generation/upload/deletion events plus invalid reset/setup token failures, team-delete blockers, high-signal provider/de-ID validation failures, and CSRF/authz/rate-limit abuse signals. Retention/access and write-failure policies are documented. System-admin read-only Audit UI now shows detection signals and recent allowlisted metadata-only events. External aggregation/SIEM and automatic alerting are deferred/out of MVP scope.

### Implementation Plan

Detailed plan: `12-audit-logging-plan.md`.

1. Complete first slice: `record_security_event()` recursive sensitive-key redaction, CR/LF sanitization, length caps, safe request metadata.
2. Complete first slice: durable audit events for login success/failure, logout, MFA challenge success/failure, onboarding TOTP/recovery-code flows, password reset/activation completion.
3. Complete first slice: account lifecycle durable audit for user create/suspend/reactivate/delete, account request create/approve/reject, bootstrap admin, team create/delete.
4. Complete first slice: provider/security config durable audit for STT/LLM/de-identification config/selection/assignment changes, metadata only.
5. Complete first slice: template/quick-action CRUD, transcript-root deletion, generated-document deletion, metadata only.
6. Complete first slice: abuse-signal audit for authorization failures, CSRF failures, and rate-limit events.
7. Complete follow-up slice: invalid reset/setup token failures, provider inspect/test, default assets, preferences, smart phrases, generation queue, and audio ingestion queue.
8. Complete high-signal validation-failure audit for remote non-HTTPS provider URLs and secret-bearing de-ID header/body rejection.
9. Define audit retention/access policy. Do not assume transcript retention applies to security audit metadata.
10. Ensure no transcript/note/prompt/provider response/audio content, cookies, tokens, passwords, MFA codes, TOTP secrets, recovery codes, Vault secret values, raw provider credentials, filenames, or smart-phrase content reach audit table or runtime logs.

### Acceptance Criteria

- [x] Representative security-relevant events persisted to `security_audit_events`; remaining event families are accepted/deferred for MVP.
- [x] System-admin read-only Audit UI exposes detection signals and recent allowlisted metadata-only event rows.
- [x] Account lifecycle events migrated from logger for create/suspend/reactivate/delete.
- [x] Login success/failure recorded.
- [x] Auth/session/MFA first-slice events recorded without raw credentials, session IDs, MFA codes, or tokens.
- [x] Provider config/credential first-slice events recorded without raw provider secrets or Vault secret payloads.
- [x] Template/action first-slice events recorded without prompt/template body text.
- [x] Transcript/generated-document deletion events recorded without transcript/note content.
- [x] Redaction/sanitization and representative event tests added.
- [x] CSRF/authz/rate-limit abuse-signal first-slice events recorded without cookies, tokens, or request bodies.
- [x] Invalid reset/setup token failures recorded without raw token or password.
- [x] Provider inspect/test events recorded without provider responses, sample transcript, bearer tokens, or raw secrets.
- [x] Default asset, preference, smart-phrase, generation queue, and upload queue events recorded without prompt, transcript, filename, audio, or smart-phrase content.
- [x] Team-delete blocker events recorded.
- [x] High-signal provider/de-ID validation failures recorded without submitted secret values.
- [x] Audit retention and access-control policy defined.

## R-015 AI Safety Plan

Finding: `OWASP-2026-06-14-008`
Severity: Info
OWASP: A04 AI Safety

Current status: closed on 2026-06-14. AI safety threat model documented with 6 threat cases, mitigations, and acceptance criteria.

### Implementation Plan

1. Completed: documented threat cases T-001 through T-006.
2. Completed: mapped mitigations per threat.
3. Completed: validated structured note JSON contract per AGENTS.md.
4. Future: add prompt-injection detection.
5. Future: add mandatory hallucination checker for freeform outputs.
6. Future: add content filter on LLM output.
7. Future: add provider egress content audit log.

### Acceptance Criteria

- [x] Threat model documented.
- [x] Mitigations mapped.
- [x] Gaps identified with severity.
- [x] Structured note contract validated.

## R-016 Authenticated Role Crawl Evidence

Finding: `OWASP-2026-06-14-002`  
Severity: Info  
OWASP: A01 Broken Access Control, A07 Identification and Authentication Failures

Current status: closed as local/test-evidenced, with external production system-admin GET-only evidence added on 2026-06-24. Local ZAP authenticated crawl completed for anonymous, onboarding-only, pending-MFA, seeded normal user, seeded team leader, and seeded system admin. A shallow ZAP AJAX Spider JS browser crawl also completed. Evidence is in `06-proxy-crawl-summary.md`, `07-tool-outputs/zap/zap-auth-crawl-local-2026-06-15-summary.md`, `07-tool-outputs/zap/zap-auth-crawl-local-admin-2026-06-23-summary.md`, `07-tool-outputs/zap/zap-auth-crawl-local-onboarding-mfa-2026-06-23-summary.md`, `07-tool-outputs/zap/zap-js-browser-crawl-local-2026-06-23-summary.md`, and `07-tool-outputs/zap/zap-auth-crawl-external-admin-2026-06-24-summary.md`.

### Target Behavior

- Anonymous users should be redirected/denied for protected browser/API routes.
- Normal users should reach only own content, own preferences, personal assets, and allowed shared available assets.
- Normal users should not access account-request review, team/user management, provider config/selection, or admin pages.
- Team leaders should reach own-team metadata/config surfaces but not transcript-derived content owned by others by virtue of role.
- System admins should manage metadata/config but not gain transcript-derived content-read authority.
- Onboarding-only and pending-MFA sessions should be blocked from normal/transcript/admin surfaces.

### Completed Local Evidence

- Anonymous browser protected routes returned `303` to `/login`; protected APIs returned `401`.
- Seeded normal user reached `/home`, `/transcribe`, `/transcribe-glm-2`, auth/me, preferences, personal assets, transcripts, and workspace.
- Seeded normal user was denied account requests, team/user management, team asset write surfaces, provider selections/configs, and `/admin`.
- Seeded team leader reached account requests, own-team users, provider selections/options, team assets, own transcript/workspace surfaces.
- Seeded team leader was denied system team listing, `/admin`, and system provider config lists.
- Seeded system admin reached admin browser shell, teams/users/account requests, and team-scoped provider metadata/config surfaces.
- Seeded system admin was denied personal/team user-library ownership surfaces; browser content surfaces redirected to `/admin`; transcript list returned `0` items and workspace had `active_transcript = null`.
- Synthetic onboarding-only session reached `/onboarding` and self/status APIs, while normal/admin/content routes redirected or returned `403`.
- Synthetic pending-MFA session reached `/mfa/challenge` and self/status APIs, while normal/admin/content routes redirected or returned `403`.
- ZAP AJAX Spider executed a shallow JavaScript browser crawl from `/home` and discovered expected home/static JavaScript and local vendor asset paths.
- External production system-admin GET-only crawl verified admin shell/API metadata access, user-library denial, content browser redirects to `/admin`, empty transcript list, and null workspace active transcript.

### Remaining Work

1. Run additional staging/production authenticated crawls only with explicit authorisation and synthetic accounts for normal user, team leader, onboarding-only, and pending-MFA roles.
2. Run broader browser workflow crawl only if explicitly needed; current JS evidence is shallow and intentionally conservative.
3. Add focused authorization tests if any future crawl result conflicts with the role matrix.

### Scanner Triage

- `Cookie No HttpOnly Flag` remains accepted for signed readable `openscribe_csrf`; auth-bearing cookies remain `HttpOnly`.
- `Absence of Anti-CSRF Tokens` on authenticated app pages is low-confidence scanner heuristic for JS/header-driven CSRF. Public server-rendered forms already passed production ZAP retest with visible `_csrf_token`; local tests cover origin/header/cookie CSRF rejection.
- Auth/session identification alerts are expected scanner auto-detection.

## Remediation Tracking Table

| Plan ID | Finding(s) | Owner | Target evidence | Status |
| --- | --- | --- | --- | --- |
| R-001 | `OWASP-2026-06-14-009` | TBD | Production docs/OpenAPI retest | Resolved and production-retested |
| R-002 | `OWASP-2026-06-14-011` | TBD | CSRF focused tests + ZAP retest | Resolved |
| R-003 | `OWASP-2026-06-14-012` | TBD | Header capture + ZAP retest | Resolved |
| R-004 | `OWASP-2026-06-14-010` | TBD | Header tests + browser compatibility smoke | Resolved |
| R-005 | `OWASP-2026-06-14-015` | TBD | Header tests + ZAP retest | Accepted |
| R-006 | `OWASP-2026-06-14-013` | TBD | Public metadata route retest | Resolved |
| R-007 | `OWASP-2026-06-14-014` | TBD | Cookie flag tests + documented exception or fix | Accepted |
| R-008 | passive recon gaps | TBD | Search/archive/exposure summaries | Closed (search/archive evidenced; Shodan/Censys not in scope) |
| R-009 | `OWASP-2026-06-14-016` | TBD | Cookie/session tests + documented scanner triage | Accepted |
| R-010 | `OWASP-2026-06-14-017` | TBD | FastAPI upgrade + starlette pip-audit + ZAP retest | Resolved (FastAPI 0.137.0, starlette 1.3.1, zero starlette vulns) |
| R-011 | `OWASP-2026-06-14-006` | TBD | pip-audit scan + dependency upgrades + SBOM evidence | Closed (direct vulns fixed; starlette tracked as R-010) |
| R-012 | `OWASP-2026-06-14-003` | TBD | XSS surface audit + bug fixes + regression tests | Closed (2 bugs fixed, 28 tests pass) |
| R-013 | `OWASP-2026-06-14-004` | TBD | SSRF canary tests + URL validation audit | Closed (23 tests pass, no host allowlist by design) |
| R-014 | `OWASP-2026-06-14-005` | TBD | Extend record_security_event() to all security-relevant actions | Closed local/test-evidenced |
| R-015 | `OWASP-2026-06-14-008` | TBD | AI safety threat model documentation | Closed (6 threat cases documented) |
| R-016 | `OWASP-2026-06-14-002` | TBD | Authenticated role crawl with synthetic accounts | Closed local/test-evidenced |
| R-017 | A04 lifecycle/deletion evidence | TBD | Live-safe synthetic lifecycle/deletion probe | Closed local/test-evidenced |
