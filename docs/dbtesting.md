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
- normal API and UI tests reset tables in the test database only
- migration tests reset the `public` schema in the test database only
- the application database is not dropped or recreated by pytest

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
persisted_user.password_hash.startswith("scrypt$")
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
- onboarding sessions and full sessions are tracked explicitly
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

## Migration coverage

Current migration tests verify:

- `alembic upgrade head` builds the schema from scratch
- head schema includes:
  - `account_requests`
  - `user_sessions`
  - `user_mfa_methods`
  - `user_recovery_codes`
- `users` now includes:
  - `full_name`
  - `must_change_password`
  - `onboarding_state`
- normalized uniqueness rules for teams and emails still hold at head
