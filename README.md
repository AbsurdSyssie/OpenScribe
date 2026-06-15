# OpenScribe

Entry points:

- setup and local run: [docs/setup.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/setup.md)
- authentication and access control: [docs/auth.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/auth.md)
- frontend direction and migration plan: [docs/frontend-roadmap.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/frontend-roadmap.md)
- Next.js frontend implementation notes: [docs/frontend-nextjs.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/frontend-nextjs.md)
- API contract and behavior: [docs/api.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/api.md)
- team STT configuration and Vault fit: [docs/stt-config.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/stt-config.md)
- transcript capture and team STT planning: [docs/transcript-capture.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/transcript-capture.md)
- XSS testing plan and probe script: [docs/security-xss.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/security-xss.md)
- test strategy and non-DB coverage: [docs/testing.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/testing.md)
- database behavior, DB safety, and DB-specific tests: [docs/dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md)
- admin usage observability design: [docs/usage_tab.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/usage_tab.md)

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
- Restyled home preview: `http://127.0.0.1:8080/home-restyled`
- Transcription workspace: `http://127.0.0.1:8080/transcribe`
- Claude transcribe preview: `http://127.0.0.1:8080/transcribe-claude`
- GLM transcribe workspace: `http://127.0.0.1:8080/transcribe-glm-2`
- Admin UI: `http://127.0.0.1:8080/admin`

Preview note:

- the preview routes reuse the real owner-only transcribe workspace context
- the GLM 2 route now keeps its own restored shell while using the same owner-only workspace runtime for session switching, note/follow-up/history rendering, EMIS autosave, upload, and microphone flows

Quick start:

- run `./start-dev.sh` from the project root to start infra, apply migrations, launch the Celery worker, and launch the dev server
- local `.env` should include `APP_ENV=local` and `COOKIE_SECURE_MODE=auto`; copy the current `.env.example` if these are missing
- production must set `APP_ENV=production` and `COOKIE_SECURE_MODE=always`; CSRF secret material is read from `CSRF_SECRET`/`SECRET_KEY` when set, otherwise the app creates or reuses a stable Vault KV secret
- production must choose HSTS ownership: keep `HSTS_SOURCE=app` when OpenScribe emits `Strict-Transport-Security`, set `HSTS_SOURCE=proxy` when Cloudflare/reverse proxy emits HSTS for all responses, or set `HSTS_SOURCE=proxy_static_fallback` when the proxy covers dynamic pages but misses `/static/` assets
- `./start-dev.sh` now bootstraps a persistent local Vault, stores the local root token and unseal key under `.local/vault/`, and keeps Postgres/Vault state aligned across restarts
- by default `./start-dev.sh` also seeds a dev team plus one leader and one user account with no MFA so manual scripts can exercise features quickly
- the default dev bind exposes FastAPI on `0.0.0.0` so a reverse proxy or another machine can reach the frontend
- Postgres, Redis, and Vault still stay localhost-only unless you explicitly change their Docker port bindings and opt into `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`
- `./start-dev.sh` now also checks live Docker port bindings for Postgres, Redis, and Vault and aborts with a terminal error if they are exposed beyond localhost unless `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`

Resend transactional email setup:

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
