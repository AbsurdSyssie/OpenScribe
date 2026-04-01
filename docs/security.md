# Security

This document records the current security model and the explicit library and architecture decisions the project is following.

Repeatable XSS checks and the current probe plan are documented in [security-xss.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/security-xss.md).

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

### Post-onboarding MFA challenge rules

- password success is not enough for completed MFA-enabled accounts
- those users receive a `pending_mfa` session until they complete a TOTP challenge
- `pending_mfa` sessions may access only:
  - `auth/me`
  - TOTP challenge endpoints
  - logout
- `pending_mfa` sessions may not access normal app routes or transcript features

### Trusted-device and freshness rules

- the trusted-device cookie is a second opaque bearer secret and must be treated like a sensitive session cookie
- trusted-device cookies are not JWTs and do not embed user identity or policy state
- the database stores only the hashed device token
- trusted devices only skip the TOTP step after a correct password login
- trusted devices do not authenticate a user by themselves
- current freshness policy:
  - a remembered browser may skip TOTP for 24 hours from the last successful MFA verification
  - using the remembered browser without redoing MFA does not extend that freshness window
- trusted-device records are revoked when a user is locked or disabled

Threats and controls for trusted devices:

- theft or browser compromise:
  - mitigate with `HttpOnly`, `SameSite=Lax`, and `Secure` outside localhost
- forgery:
  - mitigate with high-entropy opaque tokens and server-side hash verification
- replay on another browser or machine:
  - limit blast radius with bounded freshness and revocation
- leakage:
  - do not log tokens, put them in URLs, or expose them to JavaScript

### Required cookie properties

- `HttpOnly`
- `SameSite=Lax`
- explicit expiry
- `Secure` should be enabled once deployment moves beyond localhost

Current implementation:

- session and trusted-device cookies are `HttpOnly`
- the CSRF cookie is intentionally readable by browser JavaScript so browser flows can submit `X-CSRF-Token`
- `COOKIE_SECURE_MODE` controls the `Secure` flag:
  - `auto`: set `Secure` on non-local HTTPS requests
  - `always`: always set `Secure`
  - `never`: never set `Secure` and use only for local development

## Local infrastructure exposure

- checked-in dev defaults must not publish Postgres, Redis, or Vault beyond localhost
- checked-in dev defaults must not bind the FastAPI dev server beyond localhost
- remote/LAN binding for development must require explicit operator opt-in
- Vault dev mode is development-only and must never be treated as remotely safe
- startup should fail loudly in the server terminal if live Docker port bindings expose Postgres, Redis, or Vault beyond localhost
- Celery does not expose its own public port in this stack, but exposing Redis exposes the Celery broker/result backend indirectly

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
- this remains true even when leaders or admins manage team transcription endpoints and credentials

### Metadata access

- system admins may manage teams and all requests/users
- leaders may manage users and account requests for their own team
- leader access remains metadata-only, not content-readable
- STT management is metadata and secret-reference management, not content visibility

### Route-audit guardrail

- `/api/v1` routes are now covered by an explicit auth-audit manifest
- the manifest treats only these routes as session-public:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/logout`
  - `POST /api/v1/account-requests`
- all other `/api/v1` routes must deny anonymous access
- routes requiring full access must also deny onboarding and pending-MFA sessions
- manager and admin routes must also deny lower-privilege sessions
- the audit script and regression test fail if a new API route appears without a declared auth expectation

## Planned account suspension and deletion scope

This is the next account-lifecycle planning area before implementation.

Planned authority:

- system admins may suspend, reactivate, and delete any non-protected account
- team leaders may suspend, reactivate, and delete non-system-admin accounts in their own team only
- leaders may never suspend or delete system-admin accounts
- neither role gains transcript-content visibility through these routes

Planned status meanings:

- `suspended`
  - manager-triggered reversible hold
  - intended for leader/admin operational action
- `locked`
  - temporary security/auth-abuse state
- `disabled`
  - stronger security or platform action

These states should not be collapsed into one generic “inactive” meaning, because the audit trail, UI wording, and later recovery policies are different.

Implemented now:

- `suspended` is available as a distinct user status
- manager-triggered suspension revokes active sessions immediately
- manager-triggered suspension revokes trusted-device records immediately
- manager-triggered reactivation currently resets the account into password-change onboarding and requires fresh MFA setup
- self-suspension through manager routes is blocked
- manager-triggered delete is now implemented with the same team-scope and self-protection boundaries
- delete is immediate hard delete, not soft-delete
- delete currently removes user-owned transcript roots and transcript versions immediately
- account-lifecycle actions now emit metadata-only logger audit entries through `openscribe.audit`

Planned security rules:

- suspension must revoke all active sessions immediately
- suspension must revoke trusted-device records immediately
- security disable must revoke all active sessions immediately
- security disable must revoke trusted-device records immediately
- deleted users must lose all active sessions immediately as part of the delete flow
- no account-administration route may expose transcript, note, or prompt content
- full deletion remains immediate and destructive, not soft-delete

Planned reinstatement simplification for MVP:

- returning a user from either `suspended` or `disabled` to `active` should require password reset and MFA trust reset
- this keeps reinstatement logic shared initially, even though manager suspension is conceptually softer than security disable

Current implementation note:

- audit persistence is currently logger-based metadata, not a dedicated DB audit-events write path
- account-lifecycle audit should still move into persistent `audit_events` storage later so suspend, reactivate, and delete actions survive process restarts and log rotation

Required safety checks:

- do not allow deletion or suspension of the last active system-admin account
- require clear scope checks before any leader action against a team user
- record actor, target, reason, and scope metadata for suspend/reactivate/delete actions
- preserve enough metadata in audit records that a later review still makes sense after hard deletion removes the `users` row

Design caution:

Leader deletion is powerful because full user deletion immediately removes private transcript-derived content and personal assets. That power must be intentional, well-confirmed in the UI, and explicitly audited.

## Transcript capture and provider-secret rules

The next transcript slice will let teams configure a transcription endpoint and let users upload VAD-produced audio chunks for draft transcription.

Security rules for that slice:

- the team transcription endpoint is a configuration concern, not a visibility concern
- system admins provision STT endpoints and credentials for teams
- leaders may choose or clear the active transcription service/model only for their own team
- leaders may not recover raw STT credentials
- users may send audio only for transcripts they own
- managers do not gain transcript readability by configuring the endpoint
- the backend fetches STT credentials from Vault when forwarding requests
- the database stores Vault references and provider metadata only
- raw provider secrets must not be persisted in Postgres
- raw audio blobs must not be persisted in Postgres in the first STT slice
- logs may contain provider metadata and failure codes, but not transcript text, audio payloads, or secret material

Implemented now in the STT configuration slice:

- each team may have multiple provisioned STT config rows in `team_stt_configs`
- the active team STT policy is stored separately in `team_stt_selections`
- leaders may manage only their own team's active selection
- system admins may manage any team's provisioned config rows and active selection, but must choose the team explicitly
- the first auth mode is bearer token only
- the first request shape is constrained REST metadata for multipart upload, not arbitrary request scripting
- the official OpenAI adapter is a known-contract path and is intended to use the official Python SDK at runtime rather than OpenAPI discovery
- OpenAPI inspection remains only for `generic_rest`
- the bearer token is written to Vault
- Postgres stores only `vault_secret_ref` plus non-secret request metadata
- the UI and API expose only whether a secret exists, not the secret value
- onboarding-only and pending-MFA sessions are blocked from STT management routes

Implemented now in the first transcript chunk-ingestion slice:

- owner-only `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- owner-only `POST /api/v1/transcripts/{transcript_id}/audio-file`
- queued transcript-ingestion jobs in `transcript_ingestion_jobs`
- backend audio normalization to `16 kHz` mono PCM WAV before provider submission
- backend fetch of the selected team STT bearer token from Vault at processing time
- sequence-aware application of completed live chunks using `next_live_chunk_sequence_no_applied`
- backend append of provider-returned live chunk text into the transcript draft
- backend replacement of the draft text for completed file/batch ingestion
- no raw audio persistence in Postgres

Planned storage direction:

- use the provider domain for transcription configuration
- keep `team_provider_credentials.vault_secret_ref` as the secret boundary
- treat any API key, bearer token, or endpoint credential as secret material

## Current implementation direction

### Current frontend

- FastAPI + Jinja remains the active frontend
- do not introduce React just to solve auth or session hardening
- Next.js App Router remains the long-term frontend target

### Library decisions

- current TOTP library: `pyotp`
- current QR rendering library for TOTP enrollment: `segno`
- trusted-device implementation is application-owned on top of the existing DB-backed session model
- future OAuth/OIDC/SSO library: `Authlib`
- `fastapi-users` is not the intended long-term auth foundation

### Session implementation note

The current implementation uses DB-backed opaque sessions. Redis-backed server-side session acceleration may still be added later if it clearly improves the architecture without weakening revocation or auditability.

## Threats this slice explicitly addresses

- password reuse of temporary passwords beyond first login
- accessing normal features before MFA enrollment
- accessing normal features after password login but before TOTP challenge
- indefinite MFA skipping from the same remembered browser without a fresh challenge
- high-volume brute-force attempts against login, MFA, and public account-request endpoints
- session reuse after account lock/deactivate
- case-only duplicate identities
- cross-team leader management
- transcript access by admins or leaders

## Rate limiting

- rate limiting is now enforced with `slowapi`
- limiter storage is Redis-backed via `RATE_LIMIT_STORAGE_URL`
- current route groups:
  - login: `5 per 5 minutes`
  - TOTP challenge: `10 per 10 minutes`
  - public account requests: `3 per hour`
- rate-limit hits are also logged through the server logger `openscribe.security`
- HTML and JSON login routes share the same login bucket
- HTML and JSON TOTP challenge routes share the same MFA bucket
- HTML and JSON account-request submission routes share the same request bucket

Current limitations:

- the implemented limiter is IP-based, not account-based
- rotating-IP attacks are still a future hardening area
- rate-limit events are logged but not yet persisted in a dedicated security-events table
- `Retry-After` is not yet emitted on 429 responses

## Planned next hardening: lockouts and unlock workflow

This is intentionally not implemented in the current slice.

Planned direction:

- add DB-backed failed-auth tracking for:
  - repeated password failures per account
  - repeated MFA failures per account
  - repeated failures per client IP or network key where appropriate
- add temporary account cooldowns before considering broader lock semantics
- add explicit unlock workflow for leaders/admins only where the authorization model supports it
- record lock and unlock decisions with reason and actor

Likely database additions:

- to `users` or a related auth-state table:
  - `locked_at`
  - `lock_reason`
  - `unlock_at`
  - `locked_by_user_id`
  - `unlock_reason`
- and/or a dedicated failed-auth tracking table for rolling windows and counters

Design cautions:

- automatic permanent account lockouts can become a denial-of-service vector
- broad IP lockouts can harm shared networks and clinical/office environments
- leader/admin unlock actions need auditability and clear team-scope rules
- transcript/content authorization must remain unchanged regardless of auth-abuse controls

Non-goals for the next small slice:

- no permanent global IP bans
- no broad security-event dashboard
- no lockout logic that can silently expand transcript or metadata visibility
