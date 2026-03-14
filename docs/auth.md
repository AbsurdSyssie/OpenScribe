# Authentication

This document describes the implemented MVP auth and onboarding flow.

Frontend direction and longer-term migration planning remain in [frontend-roadmap.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/frontend-roadmap.md).

## Current auth model

- authentication uses an opaque cookie-backed session token
- session state is stored in the database, not in readable signed cookie payloads
- login is email + password
- system admins can bootstrap the first account only while the database has zero users
- account-request onboarding replaces invite acceptance for MVP

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

### First login and restricted onboarding

If the account was created with a temporary password:

1. the user logs in with email + temporary password
2. the system creates an onboarding-only session
3. the user is forced to `/onboarding`
4. the user must change their password
5. the user must enroll TOTP
6. the user may optionally generate recovery codes
7. only then does the session become a full-access session

While onboarding is incomplete, the user cannot access normal app features.

## Session rules

- the cookie stores only an opaque session token
- the database stores only the hashed session token
- each session has an auth level:
  - `onboarding`
  - `full`
- each session has a lifecycle status:
  - `active`
  - `revoked`
  - `expired`
- locking or disabling a user revokes all active sessions immediately on the next auth check

## Implemented auth endpoints

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

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
- `/request-access`
- `/onboarding`
- `/home`
- `/admin`
- `/logout`

## Authorization rules

### Session requirements

- unauthenticated requests receive `401` on JSON routes
- onboarding-only sessions receive `403 onboarding_incomplete` on normal JSON routes
- browser routes redirect onboarding sessions to `/onboarding` when appropriate

### Management authority

- `/admin` is system-admin-only
- team and user creation through JSON APIs is available to:
  - system admins across all teams
  - leaders for their own team only
- leaders may not create system-admin accounts

### Transcript privacy boundary

Transcript routes still require full authenticated access and remain owner-only:

- only the owning user may create a transcript for themselves
- only the owning user may commit transcript versions
- only the owning user may list their transcripts

System-admin or leader authority does not grant transcript-content access.
