# OpenScribe

Entry points:

- setup and local run: [docs/setup.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/setup.md)
- authentication and access control: [docs/auth.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/auth.md)
- frontend direction and migration plan: [docs/frontend-roadmap.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/frontend-roadmap.md)
- API contract and behavior: [docs/api.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/api.md)
- test strategy and non-DB coverage: [docs/testing.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/testing.md)
- database behavior, DB safety, and DB-specific tests: [docs/dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md)

Documentation convention:

- keep testing docs split by concern
- describe behavior first, then show the test shape briefly
- record DB-specific invariants in `docs/dbtesting.md`

Primary local URLs:

- API docs: `http://127.0.0.1:8000/docs`
- Account request page: `http://127.0.0.1:8000/request-access`
- Login / bootstrap: `http://127.0.0.1:8000/login`
- Onboarding: `http://127.0.0.1:8000/onboarding`
- User home: `http://127.0.0.1:8000/home`
- Admin UI: `http://127.0.0.1:8000/admin`

Quick start:

- run `./start-dev.sh` from the project root to start infra, apply migrations, and launch the dev server
