# API Behavior

Canonical JSON API routes are versioned under `/api/v1`.

## Implemented endpoint groups

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

### Public account requests

- `POST /api/v1/account-requests`

### Manager review

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`

### Onboarding

- `POST /api/v1/onboarding/password`
- `POST /api/v1/onboarding/totp/start`
- `POST /api/v1/onboarding/totp/verify`
- `POST /api/v1/onboarding/recovery-codes`
- `POST /api/v1/onboarding/skip-recovery-codes`

### Team management

- `POST /api/v1/teams`
- `GET /api/v1/teams`

### User management

- `POST /api/v1/users`
- `GET /api/v1/users`

### Transcripts

- `POST /api/v1/transcripts`
- `POST /api/v1/transcripts/{transcript_id}/commit`
- `GET /api/v1/users/{user_id}/transcripts`

## Error envelope

All non-2xx JSON responses use:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {
      "issues": []
    }
  }
}
```

## Current auth and authorization rules

### Authentication

- protected JSON routes require a valid opaque session cookie
- unauthenticated access returns `401 unauthorized`
- invalid login credentials return the same `401 unauthorized` response shape

### Onboarding-only sessions

- accounts with incomplete onboarding authenticate successfully
- the resulting session has `auth_level = onboarding`
- onboarding-only sessions may use only onboarding routes, `auth/me`, and logout
- normal JSON routes return:
  - `403`
  - code `onboarding_incomplete`

### Manager routes

These routes require a full authenticated manager session:

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`
- `POST /api/v1/users`
- `GET /api/v1/users`

Managers are:

- system admins
- team leaders

Leader scope is restricted to their own team.

### System-admin-only routes

These require a full authenticated system-admin session:

- `POST /api/v1/teams`
- `GET /api/v1/teams`

### Transcript routes

Transcript routes require a full authenticated user and remain owner-only:

- a user may create a transcript only for `owner_user_id == current_user.id`
- a user may commit only their own transcript
- a user may list only their own transcripts

System-admin or leader authority does not grant transcript-content access.

## Current uniqueness and onboarding rules

### User email

- emails are normalized before persistence
- uniqueness is enforced case-insensitively by a unique index on `lower(email)`

### Team name

- teams keep the display `name`
- teams also store a canonical `name_key`
- `name_key` is built from Unicode normalization + trim + collapsed whitespace + case-folding
- uniqueness is enforced on `name_key`

### Account requests

- account requests are deduplicated by normalized email + normalized requested team name while pending
- creating a request for an existing user email returns `409 conflict`

### Managed users

- manager-created users are active immediately
- they are created with a temporary password hash
- they start with:
  - `must_change_password = true`
  - `onboarding_state = pending_password_change`
