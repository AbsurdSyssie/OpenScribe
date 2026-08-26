# Authentication and access control

This document describes the implemented authentication, onboarding, account recovery, and management boundaries. Frontend migration direction is tracked separately in [frontend-roadmap.md](frontend-roadmap.md).

## Current model

- Browser authentication uses an opaque `openscribe_session` cookie.
- The browser never receives serialized user or permission state in that cookie.
- PostgreSQL stores only the hashed session token plus explicit auth level, status, and expiry metadata.
- Login uses email and password. A deployment may also enable Google, Microsoft, NHS Care Identity (CIS2), and one custom OpenID Connect (OIDC) provider for accounts that users have linked themselves.
- Completed MFA-enabled accounts normally require a TOTP challenge after password verification.
- A fresh trusted-device record can skip the TOTP challenge after successful password or linked-OIDC primary authentication.
- The first system administrator can be created only while the database contains zero users. Production also requires `BOOTSTRAP_ADMIN_TOKEN`; the submitted deployment credential must match it.
- Normal managed accounts are created by a leader/system administrator or from an approved account request.

## Browser destinations

- `/` is the public splash page. Signed-in users are redirected using their current auth state.
- Partial sessions go to `/onboarding` or `/mfa/challenge`.
- System administrators go to `/admin`.
- Full normal-user and team-leader sessions go directly to `/workspace`.
- Legacy `GET /home` links redirect into the canonical workspace; `/home` is no longer a separately rendered landing page. See [workspace.md](workspace.md).

## Account requests and managed creation

Anonymous users can submit `/request-access` with name, email, requested team, and optional details. Pending requests are deduplicated by normalized email and normalized requested team name, including a database partial unique index for concurrent submissions, and an existing user blocks a new request for that email. The public API always returns the same `202 Accepted` status and response body for a new request, a duplicate pending request, or an existing user. Durable asynchronous handling would be required to make processing time fully uniform as well.

Management scope:

- system administrators can review all requests and create users across teams;
- team leaders can review matching requests and create non-system-admin users only in their own team;
- leaders cannot create or promote system administrators.

A manager-created account starts active but with a temporary password, `must_change_password=true`, and password-change onboarding. The creator shares the temporary password out of band unless transactional email is used for an activation/setup link.

## Onboarding

A temporary-password or activation account receives an onboarding-only session. The user must:

1. set a permanent password;
2. enroll a TOTP authenticator;
3. optionally generate recovery codes;
4. complete onboarding before receiving full application access.

Permanent passwords used by onboarding, activation, reset, and self-service password changes must be at least 12 characters and include uppercase, lowercase, and numeric characters. Password hashes use Argon2id.

New and re-enrolled TOTP seeds are stored as AES-GCM envelopes under the owning user's DEK. Associated data binds the envelope to the user and MFA-method row. Encrypted methods fail closed when Vault/key material is unavailable. Legacy plaintext Base32 seeds remain read-compatible only; see [mfa-secret-encryption.md](mfa-secret-encryption.md).

Recovery codes are stored as one-way hashes and cannot be recovered from database state.

## Login and MFA

Login verifies the submitted password against Argon2id work even when the email does not match a user. Unknown and invalid credentials still receive the same `401` response. This reduces the timing signal for account enumeration; it does not replace rate limiting.

For an onboarded account:

1. password verification or linked-OIDC authentication succeeds;
2. OpenScribe checks for a non-revoked trusted-device record for the same user;
3. without fresh trust, it creates a `pending_mfa` session and redirects to `/mfa/challenge`;
4. successful TOTP verification rotates that session to `full`;
5. when the user chooses to remember the browser, OpenScribe issues a separate opaque trusted-device cookie.

Trusted-device behavior:

- the trusted-device cookie cannot authenticate by itself;
- the current freshness window is 24 hours from the most recent real MFA verification;
- password login through a trusted device does not extend that window;
- the database stores only the hashed trusted-device token;
- password reset, sensitive account recovery, suspension/disable, and relevant account changes revoke trust.

## Session and cookie rules

Session auth levels:

- `onboarding`
- `pending_mfa`
- `full`

Session lifecycle states:

- `active`
- `revoked`
- `expired`

Session, trusted-device, CSRF, and anonymous-CSRF-nonce cookies are `HttpOnly`, `SameSite=Lax`, and use `Secure` according to `COOKIE_SECURE_MODE`. Production startup requires `COOKIE_SECURE_MODE=always`.

The normal session lifetime is 12 hours. Trusted-device cookies can remain present for 30 days, but TOTP bypass still uses the shorter 24-hour MFA freshness window.

## CSRF and same-origin enforcement

Browser state-changing routes require a valid CSRF token in addition to any cookie authority.

- Before login, the token is HMAC-bound to an anonymous nonce.
- After login, it is HMAC-bound to the opaque session token.
- Session rotation invalidates the previous authenticated token.
- Server-rendered forms submit `_csrf_token`.
- Same-origin browser JavaScript sends `X-CSRF-Token` from server-rendered page state; the CSRF cookie is not JavaScript-readable.
- Unsafe `/api/v1` requests carrying session or trusted-device cookies require a matching `Origin` or `Referer` and the session-bound header token.
- Unsafe API requests without cookie-backed authority are not treated as browser-cookie requests by the CSRF dependency.

Behind a reverse proxy, `TRUST_FORWARDED_ORIGIN_HEADERS=true` permits CSRF expected-origin reconstruction from sanitized forwarded host/protocol headers. Enable it only when direct origin access is blocked. See [environment.md](environment.md) and [security.md](security.md).

## Implemented auth and onboarding API routes

Auth:

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/totp`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- `POST /api/v1/auth/account-activation/confirm`

Onboarding:

- `POST /api/v1/onboarding/password`
- `POST /api/v1/onboarding/totp/start`
- `POST /api/v1/onboarding/totp/verify`
- `POST /api/v1/onboarding/recovery-codes`
- `POST /api/v1/onboarding/skip-recovery-codes`

Account requests:

- `POST /api/v1/account-requests`
- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`

The complete endpoint inventory and response contracts are in [api.md](api.md).

## Activation and recovery

Activation and reset links use `auth_email_tokens`. Plaintext tokens are sent to the user and only token hashes are persisted.

Self-service reset:

- password-reset requests return a generic response for existing and missing users when mail is enabled;
- when mail is disabled, browser self-service reset is hidden and the API returns `503 mail_transport_disabled`;
- successful password reset revokes sessions and trusted devices;
- password-only reset preserves TOTP methods and recovery codes;
- activation links are first-use setup links and force TOTP onboarding before full access.

Manager recovery actions are metadata-only and do not grant content visibility:

- send activation/setup link;
- send password-reset link;
- send full account-recovery link;
- reset MFA;
- break-glass password reset;
- break-glass full account recovery.

Break-glass temporary-password actions are not ordinary manager reset buttons. They require the applicable break-glass policy, the manager's current TOTP code, a reason, and confirmation that email recovery is unavailable. They generate an expiring temporary password, persist only its hash, revoke authority, force password-change onboarding, and write a security audit event. Full account recovery also clears MFA/recovery-code state. Legacy `recover-password` and `recover-account` endpoints fail closed with `410 deprecated_recovery_endpoint`.

## Self-service account changes

Normal users and team leaders can update their own account from `/workspace/account`.

- Name changes affect only the authenticated owner.
- Email changes require the current password and a fresh TOTP code when TOTP is active.
- Password changes require current password, confirmation, password-strength validation, and fresh TOTP when active.
- Email uniqueness is checked against normalized database identity.
- Successful email/password changes revoke all sessions and trusted devices and issue one replacement session to the initiating browser.
- Audit events record action/outcome/field metadata, not submitted names, emails, passwords, or TOTP codes.

When OIDC is enabled, normal users and team leaders can link or remove each configured provider from `/workspace/account`. One account may link Google, Microsoft, Care Identity, and a custom provider.

- Linking starts from a full owner session and requires the current password. The provider then authenticates the second account. OpenScribe does not ask for another TOTP code during this flow.
- The authorization callback is bound to that user, that session, one-time state, a nonce, and an `S256` PKCE verifier.
- OpenScribe keys a linked identity by the provider issuer and its case-sensitive `sub` claim. It never links or creates an account from an email claim.
- Google accepts any account. Microsoft uses the `common` tenant endpoint but requires a signed `email` or `preferred_username` claim in `nhs.net`, `nhs.uk`, or a real subdomain of `nhs.uk` by default. This eligibility check runs on linking and every login. The claim is not stored or used as identity.
- Microsoft discovery uses its documented issuer template. Microsoft documents S256 PKCE support but omits the PKCE capability field from its `common` discovery document, so the built-in Microsoft profile accepts that omission only; OpenScribe still sends and verifies S256 PKCE, and every other provider must advertise it. Each signed token must contain a UUID `tid` whose value matches the tenant segment in the signed `iss` claim.
- The dedicated Care Identity provider uses deployment-supplied CIS2 registration details and the fixed `/auth/oidc/cis2/callback` path. It authenticates an issuer-bound OIDC subject; it does not treat an NHS.net address as identity proof or import national RBAC. See [cis2.md](cis2.md).
- OIDC login is available only after linking. It still applies account status, onboarding, TOTP, trusted-device, and local-development account checks.
- Linking or removal revokes all sessions and trusted devices, then gives the initiating browser one replacement session.
- The database stores no provider access, refresh, or ID token. It stores only the issuer, a versioned issuer-bound HMAC of the subject, provider label, owner, and use timestamps.
- System-administrator accounts cannot link OIDC through the normal-user account page.
- Logout ends the OpenScribe session only. Provider logout and OIDC back-channel logout are not implemented.

### Microsoft NHS rollout status

Microsoft sign-in for NHS accounts is implemented but remains a deployment task. An NHS tenant can block an unapproved multi-tenant app even when OpenScribe and the Entra registration are configured correctly. Before enabling Microsoft sign-in for NHS users in production:

1. register the production HTTPS callback in the Entra application;
2. request only the OIDC scopes OpenScribe uses: `openid profile email`;
3. give the NHS tenant administrator the application ID, callback, requested scopes, privacy information, and publisher details they require;
4. obtain tenant consent or approval under that tenant's app-consent policy;
5. test linking and login with an NHS account before enabling the provider for users.

This approval sits outside OpenScribe. Until it is granted, keep Microsoft disabled in that deployment or treat it as unavailable. Microsoft explains the tenant consent model in its [user and admin consent guide](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/user-admin-consent-overview).

Browser routes:

- `POST /auth/oidc/{provider_key}/login`
- `GET|POST /auth/oidc/{provider_key}/callback` (`POST`/`form_post` is required in production)
- `POST /settings/account/oidc/{provider_key}/link`
- `POST /settings/account/oidc/{provider_key}/unlink`

The callback verifies that the route provider matches the provider stored with the one-time authorization request. Existing password login and recovery remain available.

Pending-address verification is not implemented; an accepted email change applies immediately after strong reauthentication.

## Account lifecycle authority

Implemented manager lifecycle rules:

- `suspended` is the reversible manager-controlled state and blocks login/access;
- leaders can suspend/reactivate/delete non-system-admin users only in their own team;
- system administrators can manage other users across teams subject to protected-account checks;
- self-suspension, self-reactivation, and self-deletion through manager routes are blocked;
- reactivation currently forces password-change onboarding and clears prior MFA trust;
- manager deletion is an immediate hard delete with the implemented user/transcript cascades;
- preserved account-request records have their nullable user reference cleared.

Manager authority never grants transcript, working-note, dictation, generated-document, redaction, or other owner-content readability.

## Rate limits

Fixed authentication/account limits:

- login and password-reset request: `5/5 minutes` per client IP;
- TOTP challenge and break-glass account-security actions: `10/10 minutes` or the account-security limiter applicable to the route;
- self-service sensitive account changes: `5/5 minutes` per client IP;
- public account request: `3/hour` per client IP.

Configurable provider-call limits:

- live chunks: `LIVE_CHUNK_UPLOAD_RATE_LIMIT`, default `1/second`;
- whole-file upload burst: `WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT`, default `1/5 seconds`;
- whole-file uploads per day: `WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT`, default `100/day`;
- LLM generation burst: `LLM_GENERATION_BURST_RATE_LIMIT`, default `20/3 minutes`;
- LLM generations per day: `LLM_GENERATION_DAILY_RATE_LIMIT`, default `200/day`.

Authenticated upload/generation limits key to the resolved user where possible, with a hashed-session or client-IP fallback. Browser and JSON variants sharing a limiter scope consume the same bucket. See [environment.md](environment.md) for all quota and duration controls.

For IP-keyed limits, proxy headers are ignored by default. `RATE_LIMIT_TRUST_CLOUDFLARE=true` uses a valid `CF-Connecting-IP`; otherwise `RATE_LIMIT_TRUST_X_FORWARDED_FOR=true` uses the first valid `X-Forwarded-For` address. If neither trusted value is valid, OpenScribe uses the socket peer. Enable either setting only after the proxy/CDN is the sole route to the origin and overwrites the matching header. See [security.md](security.md).

## Authorization response behavior

- Unauthenticated JSON requests receive `401 unauthorized`.
- `pending_mfa` sessions receive `403 mfa_required` on full-access routes.
- onboarding sessions receive `403 onboarding_incomplete` on full-access routes.
- Browser pages redirect partial sessions to the corresponding onboarding/MFA page.
- System-admin-only APIs return `403 forbidden` to authenticated non-admins.
- Owner-scoped content lookups generally return `404` when another user attempts to address an object, preventing cross-owner existence disclosure.
