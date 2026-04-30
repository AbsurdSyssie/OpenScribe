# Database Testing

This document is the database-specific test reference: what behavior we enforce at the DB and service boundary, how we protect local data while testing, and which tests prove those rules.

## Database split

Tests run against `TEST_DATABASE_URL`, not `DATABASE_URL`.

By default:

- app and manual UI data live in `ambient_scribe`
- automated tests reset only `ambient_scribe_test`

This is enforced by:

- [tests/db_utils.py](/home/oscar/Documents/Code_Projects/OpenScribe/tests/db_utils.py)
- [tests/conftest.py](/home/oscar/Documents/Code_Projects/OpenScribe/tests/conftest.py)
- [tests/test_migrations.py](/home/oscar/Documents/Code_Projects/OpenScribe/tests/test_migrations.py)

## Safety guard

If `TEST_DATABASE_URL` matches `DATABASE_URL`, pytest fails immediately before any reset logic runs.

## Test database lifecycle

- the test helper creates `ambient_scribe_test` automatically if it does not exist
- normal API and UI tests reset the `public` schema in the test database before each test
- test engines use `NullPool` so Postgres connections do not hold stale cached plans across schema drops
- normal API and UI tests flush the test Redis rate-limit store only
- migration tests reset the `public` schema in the test database only
- the application database is not dropped or recreated by pytest

Why this matters:

- plain `drop_all()/create_all()` on a reused pooled Postgres connection can leave enum and cached-plan state behind after interrupted runs
- recreating the whole `public` schema is the more reliable isolation boundary for this repo’s Postgres test setup

## What we test at the DB boundary

### Team names

Behavior in plain language:

- teams store the original display name plus a canonical `name_key`
- `name_key` is built from trim + collapsed whitespace + Unicode normalization + case-folding
- `Clinic North`, `clinic north`, and `  Clinic   North  ` are duplicates
- punctuation is preserved, so `Clinic North` and `Clinic_North` are distinct

Brief test shape:

```python
first = create_team(client, name="Clinic North")
case_variant = create_team(client, name="clinic north")
whitespace_variant = create_team(client, name="  Clinic   North  ")
```

Expected:

- first succeeds
- normalized duplicates fail with `409 conflict`

### User emails

Behavior in plain language:

- user emails are normalized before persistence
- uniqueness is enforced case-insensitively by `lower(email)`

Brief test shape:

```python
first = create_user(client, email="Mixed.Case@Example.com")
second = create_user(client, email="mixed.case@example.com")
```

Expected:

- first succeeds
- second fails with `409 conflict`

### Managed user creation and password storage

Behavior in plain language:

- manager-created users are persisted immediately as active accounts
- the stored password is always a derived hash
- the user starts with:
  - `must_change_password = true`
  - `onboarding_state = pending_password_change`

Brief test shape:

```python
persisted_user.password_hash != "TempPass1"
persisted_user.password_hash.startswith("$argon2id$")
persisted_user.onboarding_state.value == "pending_password_change"
```

### Account requests

Behavior in plain language:

- pending requests are deduplicated by normalized email + normalized requested team name
- a real existing user blocks a new account request for the same normalized email
- approved requests link to the created user

Brief test shape:

```python
first = client.post("/api/v1/account-requests", json={...})
duplicate = client.post("/api/v1/account-requests", json={...})
```

Expected:

- first succeeds
- duplicate fails with `409 conflict`

### Sessions and revocation

Behavior in plain language:

- the cookie holds an opaque token, not serialized user state
- the DB stores only the hashed token in `user_sessions`
- onboarding, pending-MFA, and full sessions are tracked explicitly
- locking a user revokes all active sessions immediately

Brief test shape:

```python
user.status = UserStatus.locked
db_session.commit()
response = client.get("/api/v1/auth/me")
```

Expected:

- request fails with `401`
- existing session rows are marked revoked

### Manager suspension and reactivation

Behavior in plain language:

- `suspended` is a distinct persisted user status
- manager suspension blocks login without deleting content
- manager suspension revokes active sessions and trusted-device records immediately
- leader scope is limited to non-system-admin users in the leader’s own team
- manager reactivation currently resets the user into password-change onboarding and disables prior MFA setup

Brief test shape:

```python
suspended = client.post(f"/api/v1/users/{member.id}/suspend")
reactivated = client.post(f"/api/v1/users/{member.id}/reactivate")
```

Expected:

- suspend returns `status = suspended`
- later login attempts fail while suspended
- reactivate returns `status = active`
- reactivated user has:
  - `must_change_password = true`
  - `onboarding_state = pending_password_change`
  - `mfa_enabled = false`

### Manager deletion

Behavior in plain language:

- manager delete is a hard-delete path, not a soft-delete path
- leaders may delete only non-system-admin users in their own team
- system admins may delete other users across teams
- self-delete through manager routes is blocked
- deleting a user removes currently implemented transcript roots and transcript versions immediately
- account-request rows that point at the deleted user are preserved, but their nullable user references are cleared

Brief test shape:

```python
deleted = client.delete(f"/api/v1/users/{member.id}")
```

Expected:

- response is `204`
- the `users` row is gone
- owned `transcripts` rows are gone
- owned `transcript_versions` rows are gone via transcript-root cascade
- preserved `account_requests` rows have `linked_user_id = null`

### Manager-route auth boundary

Behavior in plain language:

- manager account routes are not public
- unauthenticated callers cannot suspend, reactivate, or delete users
- ordinary users cannot use those routes
- onboarding-only and pending-MFA sessions cannot use those routes

Brief test shape:

```python
client.post(f"/api/v1/users/{user_id}/suspend")
client.post(f"/api/v1/users/{user_id}/reactivate")
client.delete(f"/api/v1/users/{user_id}")
```

Expected:

- no session cookie returns `401`
- normal non-manager user returns `403 forbidden`
- onboarding-only session returns `403 onboarding_incomplete`
- pending-MFA session returns `403 mfa_required`

### Team STT configuration

Behavior in plain language:

- system admins provision one or more STT config rows per team
- each team may have at most one active STT selection row
- the row stores endpoint metadata and a Vault secret reference, never the raw bearer token
- inspection may fetch the OpenAPI document and infer defaults, but does not persist a row by itself
- browser inspection must render the inferred values back into the save form in the same response
- leaders may choose or clear only their own team's active STT selection
- leaders may not create, update, or delete credential-bearing config rows
- system admins may create, update, inspect, and delete a selected team's config rows
- normal users, onboarding sessions, and pending-MFA sessions may not access STT provisioning or selection routes
- the API returns `has_secret` but does not reveal the bearer token or the raw Vault ref
- remote non-local endpoints must use `https://`
- local and RFC1918 development HTTP endpoints are accepted in this first slice

Brief test shape:

```python
inspection = client.post("/api/v1/stt-configs/inspect", json={...})
created = client.post("/api/v1/stt-configs", json={...})
selection = client.post("/api/v1/stt-selection", json={...})
persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.id == config_id))
```

Expected:

- inspection returns inferred request and response fields without storing a row
- system-admin provisioning succeeds
- leader selection succeeds only for a provisioned own-team option
- clearing the team selection removes only the `team_stt_selections` row
- `fetched.json()["has_secret"] is True`
- `vault_secret_ref` is present only in the database row, not the response
- cross-team leader access fails with `403`
- invalid remote `http://` endpoint fails with `422`

### Team LLM configuration

Behavior in plain language:

- system admins provision one or more LLM config rows per team
- each team may have at most one active LLM selection row
- each user may have at most one preferred default-model row
- the row stores provider metadata and a Vault secret reference, never the raw API key
- inspection may fetch available OpenAI chat models through the SDK or available Ollama models through `/api/tags`, but does not persist a row by itself
- leaders may choose or clear only their own team's active LLM selection
- leaders may not create, update, or delete credential-bearing config rows
- team LLM selection now persists:
  - the active provider
  - a team default model
  - an allowed-model subset visible to team users
- normal users may set or clear only their own preferred default model from that allowed-model subset
- if a saved user preference is no longer allowed for the active team provider, runtime resolution falls back to the team-selected default model
- normal users, onboarding sessions, and pending-MFA sessions may not access LLM provisioning or team-selection routes
- the API returns `has_secret` but does not reveal the API key or the raw Vault ref
- remote non-local endpoints must use `https://`
- local and RFC1918 development HTTP endpoints are accepted in this first slice

Brief test shape:

```python
created = client.post("/api/v1/llm-configs", json={...})
selection = client.post("/api/v1/llm-selection", json={...})
preference = client.post("/api/v1/llm-preference", json={...})
persisted = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config_id))
```

Expected:

- system-admin provisioning succeeds
- leader selection succeeds only for a provisioned own-team option
- user preference succeeds only for the authenticated user and only for the leader-approved model subset
- clearing the team selection removes only the `team_llm_selections` row
- clearing the user preference removes only the `user_llm_preferences` row
- `created.json()["has_secret"] is True`
- `vault_secret_ref` is present only in the database row, not the response
- cross-team leader access fails with `403`
- invalid remote `http://` endpoint fails with `422`

### User app preferences

Behavior in plain language:

- only normal team users may manage `user_app_preferences`
- the row stores workflow metadata only, not transcript-derived content
- favourite/default template and quick-action ids must remain inside the caller's visible owner/team scope
- stale favourite/default ids are removed lazily if the referenced asset is later deleted or hidden
- clearing preferences removes only the `user_app_preferences` row

Brief test shape:

```python
saved = client.post(
    "/api/v1/app-preferences",
    json={
        "favorite_quick_action_ids": [str(team_quick_action.id)],
        "favorite_template_ids": [str(team_template.id)],
        "default_quick_action_id": str(team_quick_action.id),
        "llm_detail_level": "detailed",
        "preferred_recording_mode": "live_chunked",
    },
)
```

Expected:

- save/get/clear succeed for the authenticated owner only
- cross-team or hidden asset ids fail with `422`
- system-admin access fails with `403`
- deleting a favourited template/quick action causes later reads to return the row without the stale ids

### Transcript start and ingestion mode

Behavior in plain language:

- transcript start creates the root for the current authenticated owner
- `team_id` is derived from the current user rather than trusted from the request
- system-admin accounts may not own transcript content
- the transcript root persists `ingestion_mode` so later capture flows share one contract
- if omitted, the start flow currently implies `whole_file`

Brief test shape:

```python
started = client.post(
    "/api/v1/transcripts/start",
    json={"title": "Visit note", "ingestion_mode": "live_chunked"},
)
```

Expected:

- response is `201`
- `owner_user_id` matches the logged-in user
- `team_id` matches the logged-in user's team
- `ingestion_mode` is persisted and returned
- system-admin callers get `403`

### Live audio chunk ingestion

Behavior in plain language:

- live chunk uploads are owner-only
- live chunk uploads are allowed only when the transcript ingestion mode is `live_chunked`
- the API route queues an ingestion job and returns `202`
- live chunk processing normalizes uploaded audio before STT submission
- the worker requires an active team STT selection before provider execution
- provider-returned text is appended into the current transcript draft only when completed chunks can be applied in order
- transcript status stays `transcribing`

Brief test shape:

```python
uploaded = client.post(
    f"/api/v1/transcripts/{transcript_id}/audio-chunks",
    files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
    data={"chunk_sequence_no": "1"},
)
process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-audio")
```

Expected:

- unauthenticated callers get `401`
- non-owners get `403`
- non-`live_chunked` transcripts get `409`
- duplicate `chunk_sequence_no` gets `409`
- missing active team STT selection fails in worker processing and marks the job `failed`
- successful worker processing appends provider text to `current_draft_text_encrypted` in sequence order

### Whole-file ingestion

Behavior in plain language:

- whole-file ingestion is owner-only
- whole-file ingestion is allowed only when the transcript ingestion mode is `whole_file`
- the API route queues an ingestion job and returns `202`
- worker processing normalizes uploaded audio before STT submission
- the worker requires an active team STT selection before provider execution
- provider-returned text replaces the current transcript draft for the file-ingestion flow
- transcript status moves to `ready`

Brief test shape:

```python
uploaded = client.post(
    f"/api/v1/transcripts/{transcript_id}/audio-file",
    files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
)
process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-file-audio")
```

Expected:

- unauthenticated callers get `401`
- non-owners get `403`
- `live_chunked` transcripts get `409`
- missing active team STT selection fails in worker processing and marks the job `failed`
- successful worker processing writes provider text into `current_draft_text_encrypted`

### Trusted devices and MFA freshness

Behavior in plain language:

- trusted devices are stored separately from normal sessions
- the browser cookie stores only an opaque trusted-device token
- the DB stores only the hashed trusted-device token
- a trusted device lets a completed user skip TOTP only if the last real MFA verification was within 24 hours
- using the trusted device without redoing MFA does not extend the freshness window
- locking a user revokes trusted-device records as well as sessions

Brief test shape:

```python
challenge = client.post("/api/v1/auth/mfa/totp", json={"code": code, "remember_device": True})
client.post("/api/v1/auth/logout")
login_again = login(client, email="managed@example.com", password="BetterPass1")
```

Expected:

- the first post-onboarding login requires `pending_mfa`
- the challenge may issue a remembered-browser cookie
- a fresh trusted device allows a later password login to return `auth_level = full`
- if `last_mfa_verified_at` is stale, the same browser returns to `pending_mfa`

### MFA and recovery codes

Behavior in plain language:

- TOTP enrollment creates a stored MFA method
- recovery codes are stored hashed only
- generated recovery codes are displayed once and never persisted in plaintext

Brief test shape:

```python
recovery = client.post("/api/v1/onboarding/recovery-codes")
stored = list(db_session.scalars(select(UserRecoveryCode)))
```

Expected:

- plaintext codes appear in the response only
- stored `code_hash` values do not equal the returned codes

### Transcript persistence and version history

Behavior in plain language:

- transcript version commits create new `transcript_versions` rows
- version numbers increase monotonically
- owner-only access rules remain intact after the auth rewrite

### Templates and generated documents

Behavior in plain language:

- team templates are configuration roots scoped to one team and managed by team leaders
- personal templates are configuration roots scoped to one user and managed by that owner
- each template save creates a new immutable `template_versions` row
- generated note output is transcript-derived content rooted under the transcript owner and transcript id
- transcript delete cascades to generated documents

Brief test shape:

```python
team_template = leader_creates_team_template(...)
personal_template = owner_creates_personal_template(...)
generated = owner_generates_note(...)
deleted = client.delete(f"/api/v1/transcripts/{transcript_id}")
```

Expected:

- leaders can manage only their own team templates
- users can manage only their own personal templates
- leaders can manage only their own team quick actions
- users can manage only their own personal quick actions
- note generation creates both a `transcript_versions` snapshot and a `generated_documents` row
- quick action generation creates both a `transcript_versions` snapshot and a `generated_documents` row linked to a `quick_action_versions` snapshot
- generated-document rows now also snapshot prompt/provider execution metadata needed to survive later config or source-asset changes
- generated-document rows now also link to the `redaction_runs` snapshot used for outbound LLM generation
- `redaction_runs` are created lazily per `transcript_versions` snapshot and reused for later generation actions on the same version
- `redaction_entities` persist the PHI placeholder mapping needed to validate returned placeholders and re-identify the finished output
- generation usage metadata is now also persisted into `provider_usage_events` with team/user IDs and token/duration fields when available
- deleting a template or quick action no longer removes the prompt context needed by already-queued/generated output
- deleting the transcript removes the generated document immediately

## Migration coverage

Current migration tests verify:

- `alembic upgrade head` builds the schema from scratch
- head schema includes:
  - `account_requests`
  - `generated_documents`
  - `quick_actions`
  - `quick_action_versions`
  - `template_versions`
  - `templates`
  - `user_sessions`
  - `user_trusted_devices`
  - `user_mfa_methods`
  - `user_recovery_codes`
- `users` now includes:
  - `full_name`
  - `must_change_password`
  - `onboarding_state`
- head supports `users.status = suspended`
- normalized uniqueness rules for teams and emails still hold at head

## Rate-limit test isolation

Tests use `TEST_RATE_LIMIT_STORAGE_URL`, not `RATE_LIMIT_STORAGE_URL`.

By default:

- app limiter storage lives in Redis DB `0`
- test limiter storage lives in Redis DB `15`

The test harness flushes the test limiter store before and after each non-migration test so counters do not leak between cases.
