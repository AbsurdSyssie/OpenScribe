# Authentication

This document describes the implemented MVP auth and onboarding flow, including post-onboarding TOTP challenges and the bounded trusted-device freshness window.

Frontend direction and longer-term migration planning remain in [frontend-roadmap.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/frontend-roadmap.md).

## Current auth model

- authentication uses an opaque cookie-backed session token
- session state is stored in the database, not in readable signed cookie payloads
- login is email + password
- completed MFA-enabled accounts do not receive full access immediately on password success
- system admins can bootstrap the first account only while the database has zero users
- account-request onboarding replaces invite acceptance for MVP

## Public landing route

- `/` renders the public OpenScribe splash page for anonymous browser users
- signed-in users visiting `/` are redirected through the existing post-login destination logic
- normal users land on `/home`, system administrators land on `/admin`, and partial sessions remain routed to onboarding or MFA challenge pages
- splash page calls to action link only to implemented browser routes: `/login` and `/request-access`

## End-to-end onboarding flow

### Public request flow

- prospective users open `/request-access`
- they submit:
  - name
  - email
  - team name
  - optional details
- the system creates a pending account request unless:
  - a matching pending request already exists for the same normalized email + team
  - a real user already exists for that normalized email

### Manager review flow

- system admins may review all account requests
- team leaders may review only requests for their own team
- leaders/admins may:
  - approve a request and create the real user account
  - reject a request with notes
  - create a user directly without any prior request

### Managed account creation

Managed accounts are created by a leader or system admin with:

- email
- optional full name
- team / team role
- temporary password
- active status

The temporary password is shared out-of-band by the creator.

Leader and system-admin managed-account forms can generate a 12-character temporary password in place. Generated values always include uppercase, lowercase, numeric, and special characters, use browser cryptographic randomness, avoid ambiguous characters, and are copied to the clipboard when browser permission allows. Compact generate, copy, and show/hide controls sit inside the password field; copy appears after generation and remains available until the value is manually edited. The field remains populated with a warning toast when clipboard access is unavailable. Existing manually entered values require confirmation before replacement without changing server-side password policy.

### First login and restricted onboarding

If the account was created with a temporary password:

1. the user logs in with email + temporary password
2. the system creates an onboarding-only session
3. the user is forced to `/onboarding`
4. the user must change their password
5. the user must enroll TOTP
   - the app shows the shared secret
   - the app shows the provisioning URI
   - the app renders a Segno-generated QR code for authenticator apps
6. the user may optionally generate recovery codes
7. only then does the session become a full-access session

While onboarding is incomplete, the user cannot access normal app features.

## Login flow after onboarding completes

### Password step

For users whose onboarding is already complete:

1. the user submits email + password
2. the system checks for a valid trusted-device cookie for that same user
3. if MFA is required and no fresh trusted device exists:
   - the system creates a `pending_mfa` session
   - the user is redirected to `/mfa/challenge`
4. if a fresh trusted device exists:
   - the system creates a normal `full` session immediately

### TOTP challenge step

- `/mfa/challenge` is the browser challenge page
- `POST /api/v1/auth/mfa/totp` is the JSON challenge endpoint
- successful TOTP verification rotates the `pending_mfa` session into a normal `full` session
- if the user opts in to remembering the browser, the app also issues a trusted-device cookie

### Trusted-device freshness

- trusted devices do not authenticate by themselves
- they only allow a successful password login to skip the TOTP step
- the skip window is currently 24 hours from the last real MFA verification
- using a trusted device without performing MFA does not extend that 24-hour window
- once the freshness window expires, the same browser must complete TOTP again

## Session rules

- the cookie stores only an opaque session token
- the database stores only the hashed session token
- each session has an auth level:
  - `onboarding`
  - `pending_mfa`
  - `full`
- each session has a lifecycle status:
  - `active`
  - `revoked`
  - `expired`
- locking or disabling a user revokes all active sessions immediately on the next auth check

## Trusted-device rules

- the browser gets a separate `openscribe_trusted_device` cookie
- the cookie stores only an opaque random token
- the database stores only the hashed trusted-device token
- trusted-device records track:
  - expiry
  - last seen time
  - last MFA verification time
  - revocation time and reason
- locking or disabling a user revokes trusted-device records as well as active sessions

## Implemented auth endpoints

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/totp`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- `POST /api/v1/auth/account-activation/confirm`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`

### Account activation and recovery

- activation and password-reset links are backed by `auth_email_tokens`
- token plaintext is emailed only and is not stored in the database
- public password-reset request returns generic success for existing and missing accounts
- when outbound mail is disabled, self-service password reset is hidden in the browser and the reset request endpoint returns `503 mail_transport_disabled`; users must ask a team leader or system administrator for recovery
- password reset changes password auth material only; it does not rotate or delete the user DEK
- password reset revokes sessions and trusted devices
- password reset/setup confirmation validates the token before running Argon2id password hashing
- user-chosen permanent passwords for onboarding, activation, and reset must be at least 12 characters and include uppercase, lowercase, and number characters
- password hashes use Argon2id with OWASP baseline parameters; non-Argon2id local dev hashes can be rotated with `scripts/force_argon2id_password_rotation.py`
- activation/setup links are only valid before first password setup; they set the user's first real password and then force TOTP onboarding before full access
- manager recovery actions are metadata-only and never expose transcript-derived content:
  - `POST /api/v1/users/{user_id}/send-activation`
  - `POST /api/v1/users/{user_id}/send-password-reset` sends a manager-created email reset token when mail is enabled
  - `POST /api/v1/users/{user_id}/send-account-recovery` sends a manager-created email recovery token that resets password and MFA on confirmation
  - `POST /api/v1/users/{user_id}/break-glass-password-reset` is available only when break-glass policy allows it, requires the manager's current TOTP code, reason, and confirmation that email is unavailable, then generates an expiring temporary password, stores only its hash, revokes sessions/trusted devices, forces `pending_password_change`, preserves existing TOTP/recovery-code state, and records a security audit event
  - `POST /api/v1/users/{user_id}/reset-mfa` clears TOTP/recovery-code state, revokes sessions/trusted devices, and forces TOTP reenrollment unless the user is still in `pending_password_change`
  - `POST /api/v1/users/{user_id}/break-glass-account-recovery` performs the same break-glass checks and temporary-password flow, and also clears MFA/recovery-code state before forced password/TOTP onboarding
  - legacy `recover-password` and `recover-account` endpoints fail closed with `410 deprecated_recovery_endpoint`

## Brute-force protection

- login routes are rate-limited at `5 per 5 minutes` per client IP
- TOTP challenge and break-glass recovery routes are rate-limited at `10 per 10 minutes` per client IP
- public account-request submission is rate-limited at `3 per hour` per client IP
- whole-file transcription upload routes are rate-limited at:
  - `1 per 5 seconds`
  - `100 per day`
- whole-file upload throttling keys to the authenticated user when a valid session can be resolved, with safe fallback to hashed session token or client IP
- the browser and JSON variants of each route share the same limiter bucket
- browser rate-limit responses render a generic wait-and-retry page
- rate-limit hits are recorded in the server logs through `openscribe.security`

## Browser CSRF protection

- browser state-changing routes now require a CSRF token in addition to the normal session cookie
- the app issues a separate `openscribe_csrf` cookie for browser flows
- authenticated CSRF tokens are stable for one login session, HMAC-bound to the opaque session token, and invalidated when that session rotates
- browser forms render and submit the CSRF token as `_csrf_token`; the shared nonce-protected CSRF script refreshes or adds the field as a client-side fallback
- browser JavaScript requests submit the token as `X-CSRF-Token` from server-rendered page state, not from a JavaScript-readable cookie
- unsafe `/api/v1` requests with cookie-backed authority require same-origin `Origin` or `Referer` and the session-bound `X-CSRF-Token`; safe API methods remain exempt
- CSRF protection reduces cross-site request forgery risk; it does not prevent an already-authenticated user from scripting their own browser

### Account requests

- `POST /api/v1/account-requests`
- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`

### Onboarding

- `POST /api/v1/onboarding/password`
- `POST /api/v1/onboarding/totp/start`
- `POST /api/v1/onboarding/totp/verify`
- `POST /api/v1/onboarding/recovery-codes`
- `POST /api/v1/onboarding/skip-recovery-codes`

## Browser routes

- `/login`
- `/forgot-password`
- `/reset-password`
- `/activate-account`
- `/request-access`
- `/onboarding`
- `/mfa/challenge`
- `/home`
- `/admin`
- `/logout`

## Authorization rules

### Session requirements

- unauthenticated requests receive `401` on JSON routes
- `pending_mfa` sessions receive `403 mfa_required` on normal JSON routes
- onboarding-only sessions receive `403 onboarding_incomplete` on normal JSON routes
- browser routes redirect onboarding sessions to `/onboarding` when appropriate
- browser routes redirect `pending_mfa` sessions to `/mfa/challenge` when appropriate

### Management authority

- `/admin` is system-admin-only
- team and user creation through JSON APIs is available to:
  - system admins across all teams
  - leaders for their own team only
- leaders may not create system-admin accounts
- STT config management through JSON and browser routes is available to:
  - system admins for an explicitly selected team
  - leaders for their own team only
- STT config management remains metadata-only and does not imply transcript readability

### Planned account-administration authority

The next account-administration slice should make suspension and deletion authority explicit rather than treating user lifecycle as an implicit side effect of generic edit permissions.

Planned manager scope:

- system admins may suspend, reactivate, and delete any non-protected account
- team leaders may suspend, reactivate, and delete non-system-admin users in their own team only
- leaders may not act on users outside their own team
- leaders may not act on system-admin accounts

Planned guardrails:

- never allow deletion or suspension of the last active system-admin account
- do not let leaders or system admins gain transcript readability through account-administration routes
- revocation of active sessions and trusted devices must happen immediately on suspension
- full user deletion remains a hard-delete operation, not a soft-delete

Planned operational meanings:

- suspension is the reversible manager action for stopping login and access without deleting content
- security disable remains a separate concern from manager suspension
- temporary security lockouts remain a separate concern from both suspension and disable
- deletion is destructive and cascades according to the user-deletion rules already defined elsewhere

Planned status semantics:

- `active`
  - normal access allowed
- `suspended`
  - manager action by leader or system admin
  - reversible
  - blocks login and normal access
- `locked`
  - temporary security/auth-abuse state
  - blocks login and normal access
- `disabled`
  - stronger security or platform action
  - blocks login and normal access

Planned reactivation rule for the first slice:

- reinstating a user from either `suspended` or `disabled` should require password reset and MFA trust reset
- this is stricter than a soft operational reinstatement, but keeps the first implementation simple and safe

Implemented now in the first account-administration slice:

- `suspended` is the manager-controlled reversible state
- suspended users cannot log in
- leaders may suspend and reactivate non-system-admin users in their own team only
- system admins may suspend and reactivate other accounts across teams
- manager reactivation currently resets the user into password-change onboarding and disables prior MFA setup
- self-management through the suspend/reactivate routes is blocked

Implemented now in the destructive manager-delete slice:

- leaders may delete non-system-admin users in their own team only
- system admins may delete other accounts across teams
- self-delete through manager routes is blocked
- user deletion is immediate hard delete
- user deletion removes currently implemented user-owned transcript rows and transcript versions
- account-request links to the deleted user are nulled so review records remain structurally valid

### Transcript privacy boundary

Transcript routes still require full authenticated access and remain owner-only:

- only the owning user may create a transcript for themselves
- only the owning user may commit transcript versions
- only the owning user may list their transcripts

System-admin or leader authority does not grant transcript-content access.
