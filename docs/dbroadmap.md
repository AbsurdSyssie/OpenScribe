# Development Roadmap

## Status Summary

- [x] Teams and users vertical slice
- [x] Versioned API contract and standardized error envelope
- [x] Managed onboarding rewrite for MVP
- [x] Account requests replacing invite-first onboarding for MVP
- [x] Opaque DB-backed sessions with onboarding/full auth levels
- [x] TOTP enrollment and optional recovery-code generation
- [ ] Transcript lifecycle and retention hardening

## Completed Milestone: Managed Onboarding and Session Hardening

### Objective

Replace invite-first onboarding with:

- public account requests
- leader/admin review
- direct manager-created accounts
- temporary-password first login
- restricted onboarding session
- password change + TOTP setup before normal access
- immediate session revocation on lock/deactivate

Status: `Completed`

### Checkpoints

- [x] Add onboarding/account-request/session/MFA schema
- [x] Public account-request submission
- [x] Leader/admin review and approval flow
- [x] Direct leader/admin user creation with temporary passwords
- [x] Onboarding-only session gating
- [x] Forced password change
- [x] Forced TOTP enrollment
- [x] Optional recovery-code generation
- [x] Lock/deactivate revokes active sessions
- [x] Transcript owner-only access preserved
- [x] API/UI/migration/docs coverage

### Implemented decisions

- account requests supersede invite acceptance for MVP
- system admins review all requests
- team leaders review only requests for their own team
- temporary passwords are manually set and shared out-of-band by the creator
- onboarding sessions may access only onboarding routes, `auth/me`, and logout
- full access begins only after onboarding completes

## Current Data Model Highlights

### Users

- `full_name`
- `email`
- `password_hash`
- `team_id`
- `team_role`
- `is_system_admin`
- `status`
- `must_change_password`
- `onboarding_state`
- `mfa_required`
- `mfa_enabled`

### New support tables

- `account_requests`
- `user_sessions`
- `user_mfa_methods`
- `user_recovery_codes`

## Next Milestone: Transcript Lifecycle and Retention Hardening

### Objective

Bring transcript behavior up to the same architectural standard as auth and onboarding.

### Planned checkpoints

- [ ] explicit transcript delete endpoint
- [ ] cascade deletion tests for transcript root deletion
- [ ] retention expiry behavior and tests
- [ ] transcript service layer cleanup
- [ ] docs for transcript lifecycle, deletion, and ownership rules

### Non-negotiables

- transcript-derived content stays owner-only
- admin or leader authority does not imply content readability
- deletion remains immediate, not soft-delete
