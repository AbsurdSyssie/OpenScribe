# API Behavior

Canonical JSON API routes are versioned under `/api/v1`.

## Implemented endpoint groups

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/totp`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`

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
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `DELETE /api/v1/users/{user_id}`

### Transcripts

- `POST /api/v1/transcripts`
- `POST /api/v1/transcripts/{transcript_id}/commit`
- `GET /api/v1/users/{user_id}/transcripts`

Planned next additions:

- `POST /api/v1/transcripts/start`
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`

### Team transcription configuration

- `GET /api/v1/stt-config`
- `POST /api/v1/stt-config/inspect`
- `POST /api/v1/stt-config`
- `DELETE /api/v1/stt-config`
- these are metadata and secret-reference routes, not transcript-content routes

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

Rate-limited requests return the same envelope with:

- status `429`
- code `rate_limited`
- message `Too many requests`

## Current auth and authorization rules

### Authentication

- protected JSON routes require a valid opaque session cookie
- unauthenticated access returns `401 unauthorized`
- invalid login credentials return the same `401 unauthorized` response shape
- completed MFA-enabled users may receive `auth_level = pending_mfa` after password success
- login is rate-limited at `5 per 5 minutes` per client IP

### Pending-MFA sessions

- accounts with completed onboarding and active TOTP may still require a second step after password login
- the resulting session has `auth_level = pending_mfa`
- pending-MFA sessions may use only:
  - `auth/me`
  - `auth/mfa/totp`
  - `auth/logout`
  - `auth/trusted-device`
- normal JSON routes return:
  - `403`
  - code `mfa_required`

### Trusted-device freshness

- trusted-device cookies only influence post-password MFA skipping
- they do not replace password login
- a fresh trusted device currently means:
  - same browser still holds the opaque trusted-device cookie
  - the server-side record is not revoked or expired
  - the last MFA verification was within 24 hours

### Public account requests

- account-request submission is rate-limited at `3 per hour` per client IP

### MFA challenge

- TOTP challenge submission is rate-limited at `10 per 10 minutes` per client IP

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
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `DELETE /api/v1/users/{user_id}`
- `GET /api/v1/stt-config`
- `POST /api/v1/stt-config/inspect`
- `POST /api/v1/stt-config`

Managers are:

- system admins
- team leaders

Leader scope is restricted to their own team.

Current account-administration behavior:

- leaders may suspend and reactivate non-system-admin users in their own team only
- leaders may delete non-system-admin users in their own team only
- system admins may suspend and reactivate other users across teams
- system admins may delete other users across teams
- no manager may suspend their own account through these routes
- no manager may delete their own account through these routes
- suspended users cannot log in
- reactivated users are forced back into password-change onboarding and must re-establish MFA setup
- delete is immediate hard delete and returns `204`
- deleting a user removes currently implemented user-owned transcript data immediately

Current STT-configuration behavior:

- leaders may read and update only their own team's STT config
- leaders may clear only their own team's STT config
- system admins may read and update any team's STT config, but must supply `team_id`
- system admins may clear any team's STT config, but must supply `team_id`
- leaders may inspect only their own team's STT endpoint candidate
- system admins may inspect only for the selected team they are editing
- normal users may not access these routes
- onboarding-only and pending-MFA sessions may not access these routes
- `generic_rest` inspection fetches `base_url + openapi_path` and returns inferred fields without saving
- `openai_cloud` inspection uses the official OpenAI SDK server-side to return built-in contract defaults plus a filtered `available_models` list
- if OpenAI model discovery fails, `openai_cloud` inspection falls back to a built-in transcription-model allowlist and still returns `200`
- `openai_cloud` inspection also returns labeled model-option metadata so the UI can show whether each choice was `fetched` live or supplied from the built-in `default` list
- `openai_compatible_rest` inspection returns built-in known-contract defaults without OpenAPI fetch
- inspection also returns documented field descriptions and required flags when the provider's OpenAPI schema exposes them
- saved STT config now carries an explicit `adapter_kind`
- currently supported adapter families are `generic_rest`, `openai_cloud`, and `openai_compatible_rest`
- the API never returns the bearer token
- the API currently returns metadata plus `has_secret`, not the raw Vault secret reference
- one team has at most one STT config row in the current slice
- clearing the STT config removes the current row and returns the team to a blank add flow

Planned authority refinement:

- system admins provision STT endpoints and credentials for teams
- team leaders choose which provisioned service/model is active for their team
- team leaders may clear the active team STT selection
- team leaders do not gain raw credential visibility in that flow

### System-admin-only routes

These require a full authenticated system-admin session:

- `POST /api/v1/teams`
- `GET /api/v1/teams`

### Transcript routes

Transcript routes require a full authenticated user and remain owner-only:

- `POST /api/v1/transcripts/start` creates the transcript root for the current user
- `/api/v1/transcripts/start` records or implies `ingestion_mode`
- a user may create a transcript only for `owner_user_id == current_user.id`
- a user may commit only their own transcript
- a user may list only their own transcripts
- a user may upload audio chunks only for their own transcript

Current transcript-start behavior:

- the current user becomes `owner_user_id`
- `team_id` is derived from the current user
- system-admin accounts are blocked from owning transcript content
- `ingestion_mode` is persisted on the transcript root and currently supports:
  - `file_upload`
  - `microphone_batch`
  - `live_chunked`
- if the caller omits `ingestion_mode`, the route currently implies `live_chunked`
- team retention defaults are applied when no explicit retention override is supplied

Current live chunk-ingestion behavior:

- the backend resolves the active team transcription endpoint
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks` accepts multipart audio upload for owner-only live chunked transcripts
- the route currently requires:
  - `audio`
  - `chunk_sequence_no`
- the route currently accepts:
  - `declared_duration_seconds`
- chunk uploads are rejected unless the transcript `ingestion_mode` is `live_chunked`
- declared chunk duration currently rejects values above the current 30-second maximum
- the backend normalizes the uploaded audio to `16 kHz` mono PCM WAV with `ffmpeg`
- the backend resolves the active team transcription endpoint
- the backend forwards the normalized chunk to the external STT service
- the backend appends returned text into `current_draft_text_encrypted`
- the transcript status moves to `transcribing` because more live chunks may still arrive
- leaders/admins may configure team transcription metadata without gaining transcript readability

Current whole-file ingestion behavior:

- `POST /api/v1/transcripts/{transcript_id}/audio-file` accepts multipart audio upload for owner-only `file_upload` and `microphone_batch` transcripts
- file ingestion is rejected unless the transcript `ingestion_mode` is `file_upload` or `microphone_batch`
- the backend normalizes the uploaded audio to `16 kHz` mono PCM WAV with `ffmpeg`
- the backend resolves the active team transcription endpoint
- the backend forwards the normalized audio file to the external STT service
- the backend writes the returned transcript text into `current_draft_text_encrypted`
- the transcript status moves to `ready` when the provider returns successfully

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
