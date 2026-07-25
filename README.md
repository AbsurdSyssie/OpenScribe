# OpenScribe

Entry points:

- setup and local run: [docs/setup.md](docs/setup.md)
- persistent Docker runtime: [docs/docker.md](docs/docker.md)
- environment variables: [docs/environment.md](docs/environment.md)
- authentication and access control: [docs/auth.md](docs/auth.md)
- frontend direction and migration plan: [docs/frontend-roadmap.md](docs/frontend-roadmap.md)
- Next.js frontend implementation notes: [docs/frontend-nextjs.md](docs/frontend-nextjs.md)
- API contract and behavior: [docs/api.md](docs/api.md)
- team STT configuration and Vault fit: [docs/stt-config.md](docs/stt-config.md)
- transcript capture and team STT planning: [docs/transcript-capture.md](docs/transcript-capture.md)
- XSS testing plan and probe script: [docs/security-xss.md](docs/security-xss.md)
- test strategy and non-DB coverage: [docs/testing.md](docs/testing.md)
- database behavior, DB safety, and DB-specific tests: [docs/dbtesting.md](docs/dbtesting.md)
- admin usage observability design: [docs/usage_tab.md](docs/usage_tab.md)

Documentation convention:

- keep testing docs split by concern
- describe behavior first, then show the test shape briefly
- record DB-specific invariants in `docs/dbtesting.md`

Primary local URLs:

- API docs: `http://127.0.0.1:8080/docs`
- Account request page: `http://127.0.0.1:8080/request-access`
- Login / bootstrap: `http://127.0.0.1:8080/login`
- Onboarding: `http://127.0.0.1:8080/onboarding`
- MFA challenge: `http://127.0.0.1:8080/mfa/challenge`
- User home: `http://127.0.0.1:8080/home`
- User and leader settings: `http://127.0.0.1:8080/settings`
- Restyled home preview: `http://127.0.0.1:8080/home-restyled`
- Transcription workspace: `http://127.0.0.1:8080/transcribe`
- Claude transcribe preview: `http://127.0.0.1:8080/transcribe-claude`
- GLM transcribe workspace: `http://127.0.0.1:8080/transcribe-glm-2`
- Admin UI: `http://127.0.0.1:8080/admin`

Preview note:

- the preview routes reuse the real owner-only transcribe workspace context
- the GLM 2 route now keeps its own restored shell while using the same owner-only workspace runtime for session switching, note/follow-up/history rendering, EMIS autosave, upload, and microphone flows

## Quick start

For host-based development with live reload:

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
./start-dev.sh
```

For a persistent restartable Docker runtime:

```bash
cp .env.example .env
docker compose --profile runtime up -d --build
docker compose logs -f openscribe
```

The persistent profile stores PostgreSQL, Redis, Vault, and Vault bootstrap state
in named volumes. It initializes or unseals Vault, applies migrations, and starts
the web server, Celery worker, and Celery Beat whenever the application container
starts. See [docs/docker.md](docs/docker.md) before exposing it beyond localhost.

Local `.env` should include `APP_ENV=local` and `COOKIE_SECURE_MODE=auto`.
Production must set `APP_ENV=production` and `COOKIE_SECURE_MODE=always`; CSRF
secret material is read from `CSRF_SECRET`/`SECRET_KEY` when set, otherwise the
app creates or reuses a stable Vault KV secret. Production must also choose HSTS
ownership through `HSTS_SOURCE`.

`./start-dev.sh` bootstraps a persistent local Vault, stores local root-token and
unseal material under `.local/vault/`, and seeds local test accounts by default.
It keeps the FastAPI frontend, PostgreSQL, Redis, and Vault localhost-only unless
remote binding and service exposure are enabled explicitly.

## Resend transactional email setup

OpenScribe can use Resend for account setup, password reset, and manager-assisted recovery email. Email is instance-level platform infrastructure, not team-scoped provider configuration.

1. Create or choose a Resend account for this OpenScribe instance.
2. In Resend, add and verify the sending domain you want OpenScribe to use.
3. Add Resend DNS records at your DNS host and wait until Resend shows the domain as verified.
4. Create a Resend API key. Use the least-privileged or sending-restricted key Resend allows for your account.
5. Edit `.env` for this instance:

```env
MAIL_TRANSPORT=resend
APP_PUBLIC_URL=https://your-openscribe.example.com
MAIL_FROM_ADDRESS=no-reply@your-verified-domain.example
MAIL_FROM_NAME=OpenScribe
MAIL_REPLY_TO=support@your-verified-domain.example
RESEND_API_KEY=re_your_key_here
```

6. For local development only, keeping `RESEND_API_KEY` in `.env` is acceptable. Do not commit `.env` or paste the key into logs/issues.
7. For production, store the Resend API key in Vault or your deployment secret store and set `RESEND_API_KEY_VAULT_REF` instead of a plaintext `RESEND_API_KEY` when that secret path is wired for the deployment.
8. Restart OpenScribe so the mail config is reloaded.
9. Send a smoke-test email:

```bash
source .venv/bin/activate
python scripts/send_test_email.py --to you@example.com
```

10. Confirm the email arrives and Resend shows a successful send for the verified domain.

Troubleshooting:

- If startup fails with `COOKIE_SECURE_MODE=always is required in production`, either set `APP_ENV=local` for local development or set `COOKIE_SECURE_MODE=always` for production HTTPS.
- If startup fails with `CSRF_SECRET`/`SECRET_KEY` or Vault mentioned, either set a strong `CSRF_SECRET` or make Vault available with KV-v2 mounted at `VAULT_KV_MOUNT`.
- If ZAP or browser tooling reports multiple HSTS headers, ensure only one layer owns HSTS for that response. For Cloudflare/proxy-owned HSTS, set `HSTS_SOURCE=proxy`; if only static assets lack HSTS, set `HSTS_SOURCE=proxy_static_fallback`; otherwise keep `HSTS_SOURCE=app`.
- If the smoke test says mail is disabled, confirm `MAIL_TRANSPORT=resend` is in the active `.env` used by the running process.
- If Resend rejects the sender, confirm `MAIL_FROM_ADDRESS` uses the verified domain exactly.
- If reset/setup links point at localhost, set `APP_PUBLIC_URL` to the public HTTPS URL users use to reach this instance.
- If you do not want outbound email yet, use `MAIL_TRANSPORT=disabled` for manual temporary-password setup or `MAIL_TRANSPORT=stdout` plus `APP_ENV=local` for local email-body printing.
