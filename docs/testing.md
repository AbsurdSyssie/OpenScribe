# Testing

This document covers non-database testing. Database-specific behavior, safety rules, and persistence-level checks belong in [dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md).

Documentation convention:

- split test docs by concern
- explain the behavior or contract in plain language first
- show the test shape briefly after the behavior description

## Run the suite

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
pytest
```

## What the tests currently cover

### API contract

- public account-request submission
- duplicate account-request conflict behavior
- direct manager-created user onboarding state
- leader review scope limited to their own team
- onboarding-only sessions blocking normal routes
- password change + TOTP + recovery-code completion flow
- session revocation when a user is locked
- transcript owner-only access and version history

### Admin and browser UI

- bootstrap flow when the database is empty
- public `/request-access` form
- bootstrap redirect to onboarding
- leader home page with request-review and direct-user-create tools
- admin page showing teams, users, and account requests

### Auth unit tests

- password verification success
- password verification failure
- malformed stored hash rejection
- session/recovery-code hashing behavior

### Migrations

- `alembic upgrade head` builds the expected schema from scratch
- head schema includes account-request, session, MFA, and recovery-code tables
- migration behavior and database safety rules are documented in [dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md)
