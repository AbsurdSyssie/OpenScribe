# OpenScribe

Entry points:

- setup and local run: [docs/setup.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/setup.md)
- authentication and access control: [docs/auth.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/auth.md)
- frontend direction and migration plan: [docs/frontend-roadmap.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/frontend-roadmap.md)
- API contract and behavior: [docs/api.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/api.md)
- team STT configuration and Vault fit: [docs/stt-config.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/stt-config.md)
- transcript capture and team STT planning: [docs/transcript-capture.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/transcript-capture.md)
- test strategy and non-DB coverage: [docs/testing.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/testing.md)
- database behavior, DB safety, and DB-specific tests: [docs/dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md)

Documentation convention:

- keep testing docs split by concern
- describe behavior first, then show the test shape briefly
- record DB-specific invariants in `docs/dbtesting.md`

Primary local URLs:

- API docs: `http://0.0.0.0:8080/docs` locally, or `http://<your-lan-ip>:8080/docs` from another machine
- Account request page: `http://0.0.0.0:8080/request-access`
- Login / bootstrap: `http://0.0.0.0:8080/login`
- Onboarding: `http://0.0.0.0:8080/onboarding`
- MFA challenge: `http://0.0.0.0:8080/mfa/challenge`
- User home: `http://0.0.0.0:8080/home`
- Transcription workspace: `http://0.0.0.0:8080/transcribe`
- Admin UI: `http://0.0.0.0:8080/admin`

Quick start:

- run `./start-dev.sh` from the project root to start infra, apply migrations, launch the Celery worker, and launch the dev server
- by default `./start-dev.sh` also seeds a dev team plus one leader and one user account with no MFA so manual scripts can exercise features quickly
