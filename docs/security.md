# Security

This document records the current security model and the explicit library and architecture decisions the project is following.

## Core rules

- transcript-derived content is owner-only
- admin or leader authority does not imply transcript readability
- deletion remains immediate where the architecture says deletion is final
- provider secrets must not be stored raw in the database
- session identifiers, recovery codes, and password material must not be stored in plaintext form where hashing is sufficient

## Authentication and onboarding model

### Supported flow in MVP

- email + password
- public account requests
- leader/admin-created managed accounts
- temporary-password first login for managed accounts
- forced password change
- forced TOTP enrollment
- optional recovery-code generation

Invite acceptance is not the active MVP onboarding path anymore.

### Onboarding session rules

- first login with a temporary password creates an onboarding-only session
- onboarding sessions may access only:
  - onboarding routes
  - current-user lookup
  - logout
- onboarding sessions may not access normal app routes or transcript features
- normal access begins only after password change and TOTP enrollment complete

### Session storage behavior

- browser cookie stores an opaque session token only
- database stores only the hashed session token
- sessions are tracked with:
  - auth level
  - status
  - expiry
  - revoke reason
- locking or disabling a user revokes all active sessions immediately

### Required cookie properties

- `HttpOnly`
- `SameSite=Lax`
- explicit expiry
- `Secure` should be enabled once deployment moves beyond localhost

### Forbidden

- storing session tokens in `localStorage` or `sessionStorage`
- exposing session identifiers to frontend JavaScript
- allowing locked or disabled users to retain active sessions

## MFA and recovery codes

- TOTP is the first MFA method
- TOTP setup is mandatory for managed-account onboarding
- recovery codes are optional to generate in MVP
- recovery codes are stored hashed only
- displayed recovery codes are one-time display material and must not be recoverable from the database in plaintext

## Account-request security rules

- account requests are public-facing and unauthenticated
- duplicate pending requests are rejected deterministically
- leader review scope is limited to the leader’s own team
- system admins may review all requests
- direct manager-created accounts and approved account requests both produce the same managed-account onboarding rules

## Authorization model

### Content access

- transcript-derived content remains owner-only
- transcript routes require full authenticated access
- system-admin or leader authority does not grant transcript-content access

### Metadata access

- system admins may manage teams and all requests/users
- leaders may manage users and account requests for their own team
- leader access remains metadata-only, not content-readable

## Current implementation direction

### Current frontend

- FastAPI + Jinja remains the active frontend
- do not introduce React just to solve auth or session hardening
- Next.js App Router remains the long-term frontend target

### Library decisions

- current TOTP library: `pyotp`
- future OAuth/OIDC/SSO library: `Authlib`
- `fastapi-users` is not the intended long-term auth foundation

### Session implementation note

The current implementation uses DB-backed opaque sessions. Redis-backed server-side session acceleration may still be added later if it clearly improves the architecture without weakening revocation or auditability.

## Threats this slice explicitly addresses

- password reuse of temporary passwords beyond first login
- accessing normal features before MFA enrollment
- session reuse after account lock/deactivate
- case-only duplicate identities
- cross-team leader management
- transcript access by admins or leaders
