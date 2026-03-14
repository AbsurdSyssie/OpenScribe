# Setup

## Start infrastructure

```bash
docker compose up -d
```

## Create or activate the virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The app and tests use separate databases by default:

- app DB: `ambient_scribe`
- test DB: `ambient_scribe_test`

## Apply database migrations

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
alembic upgrade head
```

## Run the app

```bash
./start-dev.sh
```

This starts Docker services, loads `.env`, applies migrations, and runs the FastAPI dev server.

## Local URLs

- API docs: `http://127.0.0.1:8000/docs`
- Account request page: `http://127.0.0.1:8000/request-access`
- Login / bootstrap: `http://127.0.0.1:8000/login`
- Onboarding: `http://127.0.0.1:8000/onboarding`
- User home: `http://127.0.0.1:8000/home`
- Admin UI: `http://127.0.0.1:8000/admin`

## First access

If the database has no users:

- open `/login`
- use the bootstrap form to create the first system admin
- bootstrap signs you in and sends you to `/onboarding`
- complete TOTP enrollment before using `/admin`

After the first user exists:

- bootstrap is disabled
- users either log in normally or submit `/request-access`

## Managed account workflow

### Request access

- open `/request-access`
- submit name, email, team name, and optional details
- a leader for that team or a system admin can review the request

### Direct account creation

- leaders can create users for their own team from `/home`
- system admins can create users from `/admin`
- creators set a temporary password and share it out-of-band

### First login for managed accounts

- log in with the temporary password
- the app redirects to `/onboarding`
- complete:
  - password change
  - TOTP setup
  - optional recovery code generation
- only then does normal app access unlock

## Reset local auth state and bootstrap again

Use this when you want to wipe local app data and create a fresh first system-admin account.

### What to clear

The reset must target the app database from `DATABASE_URL`, not the test database from `TEST_DATABASE_URL`.

### Browser/session reset

The app uses an opaque session cookie. Clear it before retrying:

- sign out through `/logout`, or
- open `/login` in a private/incognito window, or
- clear cookies for `127.0.0.1:8000`

### Database reset

From the project directory:

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
```

Clear the app data in dependency order:

```sql
TRUNCATE TABLE user_recovery_codes, user_mfa_methods, user_sessions, transcript_versions, transcripts, account_requests, users, teams RESTART IDENTITY CASCADE;
```

### Expected result after reset

After the reset:

- `/login` shows `Create first system admin`
- submitting that form creates the new bootstrap user
- the new bootstrap user lands in `/onboarding`
