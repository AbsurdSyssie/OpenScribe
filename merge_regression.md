# OWASP Merge Regression Review

> Point-in-time review captured before follow-up remediation. Several findings documented below have since been fixed or accepted. See `docs/progress.md` and the OWASP remediation evidence for current status.

## 1. Security Audit Logging

A broader audit layer has been added or expanded across the application.

The audit service now:

* Removes sensitive values before storing audit details
* Filters keys such as passwords, tokens, cookies, CSRF values, prompts, transcript text, provider responses, and secrets
* Truncates long strings
* Records request method and route
* Captures request IP
* Supports optional trusted proxy/CDN IP headers through environment flags

New audit events are recorded for authentication, access control, CSRF rejection, rate limiting, provider configuration changes, template/quick-action changes, generated-document deletion, transcript lifecycle actions, and break-glass recovery actions.

### Section 1 Regression Review

The expanded audit coverage is directionally positive, but the current implementation has several regression risks that should be addressed before relying on it as a production security-control layer.

#### Finding 1 — Audit helper commits the caller’s database session

`record_security_event()` directly calls `db.commit()` on the session passed by the caller.

This creates a transaction-boundary risk. Any pending unrelated ORM changes in the same session may be committed earlier than the calling code expects. This pattern existed before the OWASP branch, but the OWASP branch significantly increases the number of call sites, so the regression surface is larger.

Recommended fix:

* Move audit writes to a dedicated short-lived database session, or
* Make `record_security_event()` add/flush only and let the caller own commit/rollback, or
* Provide two explicit helpers:

  * `record_security_event_in_current_transaction()`
  * `record_security_event_durable_best_effort()`

For security audit logging, the safest pattern is usually a dedicated best-effort session that cannot commit or roll back unrelated application work.

#### Finding 2 — Audit failures can break primary user flows

Several routes call `record_security_event()` without guarding against audit-write failure.

Example risk pattern:

1. Main business action succeeds and commits.
2. Audit write runs afterward.
3. Audit write fails.
4. User receives a 500 even though the main action already happened.

This can cause inconsistent UX and operational confusion. For example, a login session may already have been created, but the response can fail if the audit insert fails afterward.

Recommended fix:

* Make audit writes best-effort in user-facing request paths.
* Catch and log audit-write exceptions without breaking the primary flow, except for workflows where audit durability is explicitly a hard requirement.
* Add regression tests that simulate audit database failure after successful business-state mutation.

#### Finding 3 — Request IP length can exceed database column size

The `request_ip` database column is limited to 255 characters, but the audit sanitiser permits strings up to 1024 characters. Trusted headers such as Cloudflare or `X-Forwarded-For` are accepted when enabled, but the resulting value is not bounded to the database column length.

If a proxy or malicious direct request supplies an oversized header and the deployment trusts that header, audit insertion can fail.

Recommended fix:

* Add a separate `MAX_AUDIT_IP_LENGTH = 255`.
* Validate that captured request IPs parse as IP addresses where possible.
* Truncate or reject overlong IP/header values before constructing the audit event.
* Add tests for oversized `CF-Connecting-IP` and `X-Forwarded-For` values.

#### Finding 4 — Subject hashes are deterministic and unsalted

`audit_subject_hash()` uses plain SHA-256 over the normalised subject value.

This avoids storing the raw email address, but it is still vulnerable to dictionary attacks because email addresses are low-entropy identifiers. Anyone with audit table access could hash likely emails and compare them to stored values.

Recommended fix:

* Replace plain SHA-256 with a keyed HMAC using an application secret.
* Consider versioning the hash format so older audit entries remain interpretable.
* Document whether subject hashes are intended for cross-environment correlation. If not, use an environment-specific key.

#### Finding 5 — Some audit events are too sparse for incident correlation

Some newly recorded events lack enough context to support reliable investigation.

Examples:

* Logout events are recorded with request context but no actor/user association.
* Password-reset confirmation success is recorded without request context, actor, target, route, or subject hash.

This reduces the value of the audit trail during incident review.

Recommended fix:

* For logout, resolve the session before revocation and record actor, team, session/auth-level metadata, and request context.
* For password-reset confirmation success, have the service return the affected user or safe user identifier so the audit event can include target user and subject hash.
* Define minimum required fields for each audit event category.

Suggested minimum fields:

| Event category     | Minimum fields                                                               |
| ------------------ | ---------------------------------------------------------------------------- |
| Authentication     | outcome, reason code where relevant, subject hash, route, method, request IP |
| Session            | actor user ID where available, route, method, reason code                    |
| MFA                | actor user ID, outcome, reason code on failure                               |
| Provider config    | actor user ID, team ID, provider type, object type, object ID                |
| Access denial      | route, method, reason code, actor or anonymous marker                        |
| Destructive action | actor user ID, target/object ID, team ID, outcome                            |

#### Finding 6 — Audit payload size is not globally bounded

The sanitiser truncates individual string values, but nested dictionaries and lists are not globally size-capped. This means a large nested payload can still produce a large audit row.

This is especially relevant for validation errors or provider metadata, where structured lists can grow quickly.

Recommended fix:

* Add a maximum audit-detail JSON size.
* Add maximum list length and maximum dict key count during sanitisation.
* Drop or summarise excess entries with a marker such as `"[truncated_items]"`.
* Prefer counts and stable reason codes over full structured diagnostic payloads.

### Suggested Regression Tests

Add tests for the following:

1. Audit insert failure does not break login after successful authentication/session creation.
2. `record_security_event()` does not commit unrelated pending ORM changes unexpectedly.
3. Oversized `X-Forwarded-For` and `CF-Connecting-IP` values do not break audit insertion.
4. Sensitive nested values are removed from dictionaries inside lists.
5. Large nested audit details are bounded.
6. Logout audit event includes the resolved actor where a valid session exists.
7. Password-reset confirmation success includes request context and target/subject correlation.
8. Subject hashes use a keyed HMAC rather than unsalted SHA-256.

### Section 1 Risk Rating

Overall risk: **Medium-High**

The audit coverage is valuable, but the helper’s transaction behaviour and unguarded audit writes create meaningful regression risk. The most important remediation is to isolate audit writes from primary business transactions and make audit failures non-disruptive in normal request paths.


# Regression Review Addendum — Sections 2–13

## 2. New Audit Detection Service

### Regression Review

The new audit-detection service is useful, but it introduces performance and reliability risks around audit volume.

#### Finding 2.1 — Audit summary loads all matching events into memory

The summary path fetches all security audit events since the selected timestamp, then groups and analyses them in Python.

Regression risk:

* A long lookback window could load a large number of rows.
* A busy system could make the admin audit page slow or unavailable.
* This creates a possible self-inflicted denial-of-service path for system admins.

Recommended fix:

* Add a hard maximum lookback window.
* Add database-side aggregation for counts and bursts.
* Add pagination or sampling for detailed signal generation.
* Add a maximum event count for summary calculations.

#### Finding 2.2 — Event listing filters too late

The event-listing path applies some filters after loading matching events from the database. Category and outcome are filtered in Python rather than in SQL.

Regression risk:

* Filtering by category/outcome may still load a large result set.
* The visible limit is applied only after filtering.
* Admin audit views can become slow as the audit table grows.

Recommended fix:

* Push category/outcome filtering into SQL where possible.
* Consider JSON-field indexes for commonly filtered fields.
* Apply database-level `LIMIT` and pagination before returning results.

#### Finding 2.3 — `since` parsing needs defensive bounds

The parser accepts relative values such as hours and days, plus ISO timestamps. Invalid or extreme values could cause exceptions or expensive queries.

Regression risk:

* Bad admin filter input could produce a 500.
* Very large lookback values could make the query expensive.

Recommended fix:

* Validate `since` input at the route boundary.
* Return a controlled 422 for invalid values.
* Clamp lookback to a safe maximum, such as 30 or 90 days.
* Add tests for malformed and extreme `since` values.

### Section 2 Risk Rating

**Medium**

The detection logic is useful, but it needs database-side bounds before it is safe for production-scale audit volume.

---

## 3. API Documentation Access Control

### Regression Review

The branch correctly removes default public FastAPI docs and reintroduces docs behind explicit access logic. However, there are two regression risks.

#### Finding 3.1 — Docs may become public if environment is misclassified

The docs-public decision defaults to public outside recognised production environment names.

Regression risk:

* If the production environment variable is missing or set to an unexpected value, API docs may be public.
* This is a configuration-footgun rather than a code bypass.

Recommended fix:

* Prefer fail-closed behaviour.
* Make docs private by default unless `PUBLIC_API_DOCS=true`.
* Treat unknown environment names as production-like for this decision.
* Add startup logging that clearly states whether API docs are public or protected.

#### Finding 3.2 — Swagger/ReDoc may be broken by CSP

The new CSP only allows scripts and styles from self plus nonce-based inline content. FastAPI’s default Swagger/ReDoc helpers commonly depend on external assets unless explicitly configured.

Regression risk:

* `/docs` and `/redoc` may authenticate correctly but fail to render.
* Operators may think docs are broken after the OWASP branch merge.
* Developers may weaken CSP later to make docs work, undoing hardening.

Recommended fix:

* Serve Swagger/ReDoc assets locally.
* Or create a docs-specific CSP that safely permits the exact required assets.
* Add browser-level regression tests for `/docs` and `/redoc` under production-like CSP.

### Section 3 Risk Rating

**Medium**

Access control is improved, but the default-open environment logic and CSP/docs interaction need hardening.

---

## 4. CSRF Enforcement

### Regression Review

The CSRF changes are directionally strong, but they add some compatibility and resource-use risks.

#### Finding 4.1 — Missing CSRF header can force form parsing

If the `X-CSRF-Token` header is missing, the server attempts to parse form data to find `_csrf_token`.

Regression risk:

* Unsafe API requests with cookies and large multipart bodies may consume resources before being rejected.
* File upload endpoints are especially sensitive to this pattern.
* Attackers may exploit this as a low-grade resource exhaustion vector.

Recommended fix:

* For API routes, require the CSRF token in the header only.
* Reserve form-body token fallback for browser-rendered HTML forms.
* Reject large unsafe requests before form parsing when the header is absent.
* Add tests for oversized multipart requests with missing CSRF headers.

#### Finding 4.2 — Origin validation trusts forwarded headers without a local trust gate

The origin comparison uses forwarded scheme/host headers when present.

Regression risk:

* Deployments that do not strictly strip or control forwarded headers may get false accepts or false rejects.
* Correctness becomes dependent on proxy configuration.

Recommended fix:

* Only trust forwarded host/proto headers when an explicit proxy-trust setting is enabled.
* Otherwise use the direct request URL/host.
* Add deployment tests for proxy and non-proxy modes.

#### Finding 4.3 — CSRF helper logic is duplicated

There is both a static JavaScript CSRF helper and an inline template fetch wrapper.

Regression risk:

* The two implementations can drift.
* Some pages may use one path while others use the other.
* Bugs in one helper may not be caught by tests covering the other.

Recommended fix:

* Consolidate token injection and fetch wrapping into one shared implementation.
* Keep the inline template as a small bootstrapper only.
* Add tests for same-origin unsafe API requests, safe requests, cross-origin requests, and form POSTs.

### Section 4 Risk Rating

**Medium**

The CSRF model is stronger, but the form-parsing fallback and proxy-header dependency should be tightened.

---

## 5. HTTP Security Headers and Cache Controls

### Regression Review

The security headers are significantly stronger, but there are compatibility risks.

#### Finding 5.1 — CSP may break legitimate frontend behaviour

The CSP is strict and nonce-based. This is desirable, but it can break any template or third-party asset that was not updated to use nonces or local assets.

Regression risk:

* Admin pages, docs pages, or older templates may silently lose script/style behaviour.
* Browser console errors may appear only in manual testing.
* Future developers may weaken the CSP to fix broken UI.

Recommended fix:

* Add browser-based smoke tests for all major pages.
* Add CSP violation reporting in non-test environments.
* Audit templates for inline scripts, inline styles, and external assets.
* Keep an allowlist of intentional CSP exceptions.

#### Finding 5.2 — Cross-Origin-Embedder-Policy may create browser compatibility issues

The middleware sets `Cross-Origin-Embedder-Policy: credentialless`.

Regression risk:

* This can affect embedded resources, workers, and cross-origin assets.
* It may break functionality that was not designed for cross-origin isolation behaviour.

Recommended fix:

* Confirm that all pages still load scripts, fonts, workers, audio/media, and docs assets.
* Consider applying COEP only to routes that need it.
* Add browser tests for transcription and admin pages.

#### Finding 5.3 — Cache-control policy needs route-level verification

The branch adds no-store rules for many sensitive paths. This is correct, but sensitive page routes outside the listed prefixes may still need explicit testing.

Regression risk:

* New future sensitive routes may miss no-store treatment.
* Public metadata routes are cached, which is acceptable but should be intentional.

Recommended fix:

* Add a route inventory test asserting cache headers for sensitive paths.
* Include account, settings, admin, transcript, generated document, login, reset, and activation pages.
* Fail tests when new sensitive routes are added without cache policy coverage.

### Section 5 Risk Rating

**Medium**

Security is improved, but strict browser headers need end-to-end UI regression tests.

---

## 6. Access-Control Auditing

### Regression Review

Access-denial auditing is improved, but coverage is not complete.

#### Finding 6.1 — Realtime/token-based context helper is not audited

The token-based full-context helper still raises authentication/MFA/onboarding errors without recording access-denied audit events.

Regression risk:

* Realtime or workspace flows may produce unaudited access failures.
* Incident review may show gaps between browser/API denials and realtime denials.

Recommended fix:

* Add audit calls to the token-based helper.
* Include route/channel, actor where resolved, reason code, and auth level.
* Add regression tests for invalid token, pending MFA, auth-level mismatch, and incomplete onboarding.

#### Finding 6.2 — Access-denied auditing can amplify attack traffic

Every denied access attempt can now cause a database write.

Regression risk:

* Repeated unauthenticated or unauthorized requests can create audit-table write pressure.
* This could be used as a resource amplification path.

Recommended fix:

* Rate-limit high-volume denied-access paths.
* Batch or sample anonymous access-denied events where appropriate.
* Keep full-fidelity logging for authenticated users and sensitive admin/provider routes.

#### Finding 6.3 — Redirect-only page guards are not clearly audited

Some browser page flows redirect unauthenticated or incomplete sessions rather than raising the same audited access-control path.

Regression risk:

* Audit coverage may differ between API and page access.
* Security teams may assume all denied access is visible when only API/dependency denials are covered.

Recommended fix:

* Decide whether page redirects should be audited.
* If yes, add lower-severity access-control audit events for protected page redirects.
* Document which denials are intentionally not audited.

### Section 6 Risk Rating

**Medium**

Coverage is better, but not yet consistent enough to call complete.

---

## 7. Password Strength Enforcement

### Regression Review

The password-strength changes are useful but limited in scope.

#### Finding 7.1 — Legacy weak passwords are not forced to rotate

The strength validator applies to password change, password reset, and account activation. It does not force existing weak passwords to rotate at login.

Regression risk:

* Existing weak passwords can remain valid indefinitely.
* The system may appear fully hardened while old credentials remain below the new policy.

Recommended fix:

* Add a password-policy version field or password-created timestamp.
* Require password change for accounts below the current policy version.
* Add an admin report for accounts that need password rotation.

#### Finding 7.2 — Login schema still advertises an 8-character minimum

The login request still has a minimum password length of 8, while new password flows require 12 plus character-class checks.

Regression risk:

* This is not a direct vulnerability, but it may confuse API consumers and tests.
* It obscures the difference between “existing password accepted for login” and “new password policy”.

Recommended fix:

* Leave login permissive if supporting legacy passwords is intentional.
* Add comments or schema descriptions explaining the distinction.
* Consider a forced-rotation mechanism for legacy weak passwords.

#### Finding 7.3 — Password policy is still basic

The policy checks length, lowercase, uppercase, and number. It does not check known-compromised passwords, email/name similarity, or common passwords.

Regression risk:

* Users can still choose predictable passwords that satisfy the basic rule.

Recommended fix:

* Add a blocklist for common passwords.
* Optionally check against a compromised-password service or local k-anonymity dataset.
* Reject passwords containing the user’s email local part or full name.

### Section 7 Risk Rating

**Medium-Low**

The branch improves new password flows, but it does not fully remediate existing weak-password risk.

---

## 8. Authentication and Recovery Flow Auditing

### Regression Review

Authentication audit coverage is much broader, but some event quality and ordering issues remain.

#### Finding 8.1 — Some success events are too sparse

Some success events, especially password-reset confirmation success, do not include enough request or subject correlation data.

Regression risk:

* Incident review may show that a password reset happened but not enough context to correlate it to source IP, route, or subject hash.
* This weakens the value of the new audit trail.

Recommended fix:

* Have reset/activation services return the affected user or a safe identifier.
* Include target user, subject hash, request context, route, and method in success events.
* Define required fields for all auth audit events.

#### Finding 8.2 — Some state-changing flows commit before audit

Several auth/onboarding/MFA flows mutate state, commit, and then record an audit event.

Regression risk:

* If the audit write fails, the user-facing operation may return a 500 after the mutation succeeded.
* The audit trail may be missing the event despite the state change.

Recommended fix:

* Use a best-effort durable audit writer isolated from primary transactions.
* Add tests where audit insertion fails after state mutation.
* Decide which flows require hard audit durability and handle those explicitly.

#### Finding 8.3 — Recovery-code and TOTP responses expose highly sensitive values by design

The onboarding endpoints return TOTP provisioning material and recovery codes. This is expected, but the new audit/event changes increase the need for response handling discipline.

Regression risk:

* Logging middleware, debugging tools, or browser extensions could capture sensitive one-time material.
* Mistaken future audit additions could record these values if not covered by sanitisation.

Recommended fix:

* Ensure response bodies for these endpoints are never logged.
* Add tests proving recovery codes, TOTP secrets, and MFA codes are not present in audit rows.
* Keep audit events for these flows metadata-only.

### Section 8 Risk Rating

**Medium**

Coverage is improved, but event consistency and post-commit audit behaviour need tightening.

---

## 9. Rate Limit and Validation Error Handling

### Regression Review

The rate-limit and validation changes reduce information leakage, but there are some regressions to watch.

#### Finding 9.1 — Security validation detection depends on exact message text

The validation handler classifies security-relevant validation issues by matching specific error-message text.

Regression risk:

* If Pydantic or service validation messages change, security-relevant events may stop being audited.
* Refactors could silently reduce detection coverage.

Recommended fix:

* Use stable error codes instead of message substring matching.
* Add tests for every security-relevant validation path.
* Keep message text separate from event classification.

#### Finding 9.2 — HTML 429 response includes inline CSS under strict CSP

The rate-limit HTML response contains an inline `<style>` block. The global CSP blocks inline styles unless they carry a nonce.

Regression risk:

* Browser-facing rate-limit pages may render unstyled.
* CSP violations may pollute browser logs or reports.

Recommended fix:

* Move rate-limit styles to a static stylesheet, or
* Add a nonce to the style tag, or
* Return a minimal no-style HTML page.

#### Finding 9.3 — Validation responses may become too opaque for clients

Returning only `issue_count` is safer, but clients lose field-level detail.

Regression risk:

* Existing frontend form handling may no longer know which field failed.
* Users may see generic errors where actionable field errors existed before.

Recommended fix:

* For non-sensitive validation failures, return safe field-level errors.
* For security-relevant failures, keep the generic response.
* Split normal validation errors from security-sensitive validation rejections.

### Section 9 Risk Rating

**Medium-Low**

The changes are security-positive, but message-based classification and CSP compatibility need review.

---

## 10. Provider Management Auditing

### Regression Review

Provider-management audit coverage is better, but several flows still have ordering and cleanup risks.

#### Finding 10.1 — Provider mutations often commit before audit

STT selection, deletion, and similar provider operations commit the state change and then record the audit event.

Regression risk:

* Audit write failure can cause a 500 after a successful provider change.
* The audit trail can miss the event even though the configuration changed.

Recommended fix:

* Move audit to a durable best-effort session.
* Or include audit writes in the same transaction and treat failure as a controlled rollback.
* Be explicit per operation whether audit failure should block the mutation.

#### Finding 10.2 — Secret cleanup happens after database deletion

STT config deletion deletes the database record, commits, then attempts to delete the provider secret. If secret deletion fails, only a warning is logged.

Regression risk:

* Orphaned secrets may remain in the secret store.
* The deleted config may no longer provide an obvious retry path.

Recommended fix:

* Delete or validate secret deletion before removing the DB record, where feasible.
* Or mark the config as pending deletion until external secret cleanup succeeds.
* Add a cleanup job for orphaned provider secrets.

#### Finding 10.3 — Provider credential fingerprint fallback may be weak in misconfigured environments

Provider credential fingerprinting falls back through several secrets and finally to a development fallback string.

Regression risk:

* If production secrets are misconfigured, fingerprints may be generated with a predictable key.
* Cross-environment correlation may become possible if the same fallback is used.

Recommended fix:

* Fail startup in production if no explicit provider fingerprint secret is configured.
* Do not use development fallback material outside local/test environments.
* Add a startup health check for provider-secret configuration.

### Section 10 Risk Rating

**Medium**

Audit coverage improves traceability, but mutation/audit ordering and external secret cleanup need hardening.

---

## 11. Data Lifecycle and Deletion Safety

### Regression Review

The branch improves deletion safety, especially around audit retention and foreign keys. However, cleanup ordering still has edge cases.

#### Finding 11.1 — Generated-document deletion commits before audit

Generated-document deletion detaches provider usage events, deletes the document, commits, then records the audit event.

Regression risk:

* Audit failure can return a 500 after the document was deleted.
* The deletion may be missing from the audit table.

Recommended fix:

* Isolate audit writes so audit failure does not break the response.
* Or include deletion and audit in one transaction with controlled rollback.
* Add a regression test for audit failure after generated-document deletion.

#### Finding 11.2 — Template and quick-action deletion can be expensive

Template and quick-action deletion loops over generated documents to detach version references before deleting the template/action.

Regression risk:

* Teams with many generated documents may see slow deletion.
* Large detach loops may hold transactions longer than expected.

Recommended fix:

* Use bulk update statements for detachment.
* Add performance tests with large generated-document counts.
* Add database indexes for template and quick-action version references if not already present.

#### Finding 11.3 — Retry audio cleanup can leave inconsistent external state

Retry audio cleanup clears DB storage references and then deletes backing secrets. If external deletion fails, it attempts to restore the vault reference.

Regression risk:

* Partial cleanup may leave orphaned secrets or unclear retry state.
* External secret failures may only be visible in logs.

Recommended fix:

* Add a retryable cleanup queue for failed external secret deletion.
* Track cleanup status explicitly.
* Add tests for secret-store failure paths.

### Section 11 Risk Rating

**Medium**

The deletion model is safer than before, but cleanup operations still need transactional clarity and retry mechanisms.

---

## 12. Admin UI Updates

### Regression Review

The admin UI changes support the new audit/security model, but they inherit backend risks and add browser compatibility risk.

#### Finding 12.1 — Audit UI depends on potentially expensive backend queries

The admin audit UI appears to depend on the new audit summary/filter services, which currently perform some in-memory aggregation and filtering.

Regression risk:

* Admin pages may become slow as audit volume grows.
* Security monitoring may degrade when it is most needed.

Recommended fix:

* Optimise audit summary queries before relying on the UI operationally.
* Add pagination, lookback bounds, and database-side aggregation.
* Add admin-page load tests with large audit tables.

#### Finding 12.2 — CSP compatibility must be tested across all admin templates

Admin templates include nonce-aware inline content, which is correct, but any missed script/style or third-party asset will break under the strict CSP.

Regression risk:

* Specific admin panes may fail only in browser testing.
* Future UI edits may accidentally introduce blocked inline styles/scripts.

Recommended fix:

* Add browser smoke tests for every admin pane.
* Add CSP-report-only mode in staging.
* Require nonce use or static assets for all admin scripts/styles.

#### Finding 12.3 — Displaying public IPs in audit filters may be sensitive

The audit filter options expose public request IPs to the admin UI. This may be acceptable for system admins, but it is still operationally sensitive.

Regression risk:

* If admin access expands later, IP visibility may exceed intended scope.
* Screenshots/exported reports may leak IP data.

Recommended fix:

* Confirm only system admins can access the audit UI.
* Consider masking or role-gating public IP display.
* Add an export/screenshot policy for audit data.

### Section 12 Risk Rating

**Medium-Low**

The UI changes are mostly dependent on backend scalability and CSP compatibility.

---

## 13. Public Metadata Routes

### Regression Review

The public metadata routes are simple and low-risk, but they contain static deployment assumptions.

#### Finding 13.1 — `security.txt` is hard-coded

The security contact, expiry, and canonical URL are static.

Regression risk:

* Non-production or alternate-domain deployments may publish incorrect canonical metadata.
* The expiry date may become stale.
* Contact changes require code changes.

Recommended fix:

* Move security.txt values to environment/config.
* Add a test that expiry remains in the future.
* Add deployment-specific canonical URL configuration.

#### Finding 13.2 — `robots.txt` is advisory only

The route disallows sensitive paths, but robots rules do not provide access control.

Regression risk:

* Teams may overestimate the protection provided by robots.txt.
* Sensitive routes still need authentication, no-store headers, and noindex headers where appropriate.

Recommended fix:

* Treat robots.txt as metadata only.
* Add `X-Robots-Tag: noindex, nofollow` to sensitive authenticated routes if search-indexing risk exists.
* Keep route-level auth and cache controls as the real protection.

#### Finding 13.3 — `sitemap.xml` returns a cached 404-style response

The sitemap route intentionally returns a 404 with “Sitemap not published.”

Regression risk:

* Public metadata cache rules may cache this response.
* This is probably acceptable, but it should be intentional.

Recommended fix:

* Confirm expected SEO behaviour.
* If no sitemap is intended, keep this as-is.
* If a sitemap is planned, avoid long-lived cache on the placeholder response.

### Section 13 Risk Rating

**Low**

The metadata routes are simple; the main risk is stale or environment-specific static content.

---

# Consolidated Regression Priorities

## Highest Priority

1. Isolate audit writes from primary database transactions.
2. Make audit failures non-disruptive for normal user-facing flows.
3. Add hard bounds and database-side aggregation for audit detection/admin views.
4. Fix API docs rendering under CSP.
5. Complete access-denied audit coverage for token/realtime helper paths.

## Medium Priority

6. Harden CSRF handling for API uploads and proxy-header trust.
7. Add CSP browser tests for admin, docs, auth, and transcription pages.
8. Add cleanup retry paths for provider secrets and transcript retry audio.
9. Improve audit event consistency for auth/recovery success events.
10. Add forced rotation or reporting for legacy weak passwords.

## Lower Priority

11. Move security.txt values into config.
12. Document robots.txt as advisory only.
13. Add noindex policy review for authenticated/sensitive pages.
