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
- by default `./start-dev.sh` also seeds a dev team plus one leader and one user account with no MFA so manual scripts can exercise features quickly
- the default dev bind exposes FastAPI on `0.0.0.0` so a reverse proxy or another machine can reach the frontend
- Postgres, Redis, and Vault still stay localhost-only unless you explicitly change their Docker port bindings and opt into `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`
- `./start-dev.sh` now also checks live Docker port bindings for Postgres, Redis, and Vault and aborts with a terminal error if they are exposed beyond localhost unless `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true`
