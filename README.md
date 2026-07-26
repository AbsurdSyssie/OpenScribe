# OpenScribe

## Documentation

Start with the [documentation index](docs/README.md), which separates maintained current references from implemented design history, future roadmaps, and dated compliance evidence.

Primary references:

- setup and local run: [docs/setup.md](docs/setup.md)
- persistent Docker runtime: [docs/docker.md](docs/docker.md)
- environment variables: [docs/environment.md](docs/environment.md)
- authentication and account recovery: [docs/auth.md](docs/auth.md), [docs/account_recovery_brief.md](docs/account_recovery_brief.md)
- security and encryption: [docs/security.md](docs/security.md), [docs/dek-kek-production-plan.md](docs/dek-kek-production-plan.md)
- API and persistence contracts: [docs/api.md](docs/api.md), [docs/DatabasePlan.md](docs/DatabasePlan.md)
- permanent user workspace and Scribe: [docs/workspace.md](docs/workspace.md), [docs/transcript-capture.md](docs/transcript-capture.md)
- STT/LLM provider configuration: [docs/stt-config.md](docs/stt-config.md), [docs/llm-providers.md](docs/llm-providers.md)
- admin workspace and Usage reporting: [docs/admin_workspace_function_map.md](docs/admin_workspace_function_map.md), [docs/usage_tab.md](docs/usage_tab.md)
- testing and database-test safety: [docs/testing.md](docs/testing.md), [docs/dbtesting.md](docs/dbtesting.md)
- role-based product tutorials: [docs/tutorials/README.md](docs/tutorials/README.md)
- current focused backlog: [docs/feature_todo.md](docs/feature_todo.md)

## Primary local URLs

- API docs: `http://127.0.0.1:8080/docs`
- Account request page: `http://127.0.0.1:8080/request-access`
- Login / bootstrap: `http://127.0.0.1:8080/login`
- Onboarding: `http://127.0.0.1:8080/onboarding`
- MFA challenge: `http://127.0.0.1:8080/mfa/challenge`
- User workspace and Scribe: `http://127.0.0.1:8080/workspace`
- Account: `http://127.0.0.1:8080/workspace/account`
- Preferences: `http://127.0.0.1:8080/workspace/preferences`
- Libraries: `http://127.0.0.1:8080/workspace/library/templates`
- Leader AI services: `http://127.0.0.1:8080/workspace/team/ai-services`
- Admin UI: `http://127.0.0.1:8080/admin`

The workspace migration is transitional. `/transcribe` and `/settings` redirect to canonical `/workspace` routes, while successful normal-user login currently still lands on the separately rendered `/home` compatibility surface. Preview routes such as `/transcribe-claude`, `/transcribe-glm-2`, and `/transcriber_col_changes` are development surfaces rather than primary navigation.

## Quick start

For host-based development with live reload:

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m spacy download en_core_web_sm
./start-dev.sh
```

The development requirements include the runtime set plus test tools. The Docker image installs `requirements.txt` only.

For a persistent restartable Docker runtime:

```bash
cp .env.example .env
docker compose --profile runtime up -d --build
docker compose logs -f openscribe
curl --fail http://127.0.0.1:8080/health
```

The persistent profile stores PostgreSQL, Redis, Vault, and Vault bootstrap state in named volumes. It initializes/unseals Vault, applies migrations, and supervises the web server, one Celery worker, and Celery Beat whenever the application container starts. Beat publishes pending task-dispatch rows every second and runs retention, transcript-audio cleanup, provider-secret cleanup, and quota lifecycle processing every 10 seconds. Read [docs/docker.md](docs/docker.md) before migrating an existing `start-dev.sh` instance or exposing the service beyond localhost.

Local `.env` should include `APP_ENV=local` and `COOKIE_SECURE_MODE=auto`. The application defaults to production behavior when no environment selector is set. Production must use `APP_ENV=production`, `COOKIE_SECURE_MODE=always`, an explicit or Vault-backed stable CSRF secret, intentional proxy trust, and exactly one HSTS owner through `HSTS_SOURCE`.

`./start-dev.sh` bootstraps a persistent local Vault, stores local root-token and unseal material under `.local/vault/`, and seeds localhost-only test accounts by default. It keeps FastAPI, PostgreSQL, Redis, and Vault localhost-only unless remote binding and service exposure are enabled explicitly.

## Resend transactional email

OpenScribe can use Resend for account setup, password reset, and manager-assisted recovery email. Email is instance-level platform infrastructure, not team-scoped provider configuration.

Set a verified sender and the public application URL:

```env
MAIL_TRANSPORT=resend
APP_PUBLIC_URL=https://your-openscribe.example.com
MAIL_FROM_ADDRESS=no-reply@your-verified-domain.example
MAIL_FROM_NAME=OpenScribe
MAIL_REPLY_TO=support@your-verified-domain.example
RESEND_API_KEY=re_your_key_here
```

Keeping `RESEND_API_KEY` in an uncommitted `.env` is acceptable only for controlled local development. Production should inject the key from a deployment secret or set `RESEND_API_KEY_VAULT_REF` to a provisioned Vault secret. Restart all OpenScribe processes after changing mail settings, then test delivery:

```bash
source .venv/bin/activate
python scripts/send_test_email.py --to you@example.com
```

See [docs/environment.md](docs/environment.md) for the complete mail configuration and [docs/setup.md](docs/setup.md) for local behavior when mail is disabled or uses stdout.
