# 00 - Scope

Date: 2026-06-14  
Branch: `OWASP`  
Repository: `AbsurdSyssie/OpenScribe`  
Evidence status: `Repo-evidenced` seed, not live `Test-evidenced` yet.

## Objective

Create the first dated OWASP evidence pack for OpenScribe, using checked-in repository evidence as the initial source and preserving clear gaps for live crawling, proxy capture, and scanner execution.

## Authorised Scope

| Environment | Status | Evidence action |
| --- | --- | --- |
| Local development | In scope | Use synthetic accounts/data for route inventory, role checks, destructive regression tests, and scanner-safe exploration. |
| Staging | To confirm | Do not actively scan until target URL, accounts, and test window are approved. |
| Production | Restricted | Passive/read-only checks only unless written test window exists. |
| Third-party providers | Out of scope by default | Do not test provider infrastructure beyond normal configured OpenScribe interactions unless provider authorises it. |

## In-Scope Application Areas

| Area | Included surfaces | OWASP focus |
| --- | --- | --- |
| Public browser routes | `/`, `/login`, `/request-access`, reset/activation pages | A03, A05, A07, A09 |
| Auth/session flows | Login, logout, onboarding, MFA, trusted-device, password reset/account activation | A01, A02, A07, A09 |
| Account management | Account requests, approval/rejection, user creation, suspend/reactivate/delete, recovery | A01, A07, A09 |
| Role boundaries | Normal user, team leader, system admin, onboarding, pending-MFA | A01, A04, A09 |
| Transcript roots | Start, list, detail, update, commit, delete | A01, A02, A03, A09 |
| Audio ingestion | Live chunks, whole-file upload, retry audio, dictation preview/upload | A01, A02, A03, A05, A10 |
| Generated documents | Notes, follow-ups, quick actions, structured sections, redaction-debug route | A01, A03, A04, A09, AI safety |
| Templates/smart phrases | Team and personal CRUD/watch/fork behavior where implemented | A01, A03, A08 |
| Provider configuration | STT, LLM, de-identification, clinical NLP, Vault-backed secrets, URL inspection | A01, A02, A05, A10, AI safety |
| Observability | Usage metadata, safe provider errors, audit/security logs | A01, A09 |
| Local infrastructure | Postgres, Redis, Vault, Celery, dev binding checks | A02, A05, A06, A08 |
| Dependencies/assets | Python packages, static vendor assets, browser CSP | A05, A06, A08 |

## Out Of Scope

- Real patient data or live clinical encounters.
- Unauthorised third-party provider infrastructure tests.
- Denial-of-service or high-volume load tests without approval.
- Social engineering, physical security, tester endpoint compromise.
- Destructive production testing without written approval.
- Legal/clinical-safety sign-off claims that need organisational authority beyond repo evidence.

## Evidence Sources Used For This Seed

| Source | Evidence use |
| --- | --- |
| `README.md` | Local URLs, browser route seed, local setup expectations. |
| `docs/api.md` | API groups, auth rules, route behavior, transcript/provider contracts. |
| `docs/auth.md` | Auth/session/onboarding/MFA/password reset/manager recovery rules. |
| `docs/security.md` | CSRF, headers, CSP, content boundary, local infra exposure, provider secret rules. |
| `docs/transcript-capture.md` | Transcript/audio ingestion lifecycle and owner boundaries. |
| `docs/stt-config.md` | STT provider configuration and Vault-backed credential rules. |
| `docs/dbtesting.md` | DB-backed security, encryption, ownership, provider, deletion tests. |
| `docs/testing.md` | Test commands, route audit, CSRF/XSS/security checks. |
| `docker-compose.yml` | Local Postgres/Redis/Vault binding evidence. |

## Evidence Handling

- This dated folder contains redacted repo-seeded summaries only.
- Do not commit raw proxy exports, auth cookies, reset/setup tokens, provider tokens, transcript text, note text, prompts, model responses, audio, or patient identifiers.
- Mark evidence `Test-evidenced` only after a command, test run, crawl, scan, or proxy workflow has been executed and summarized.

## Current Status

| Item | Status | Notes |
| --- | --- | --- |
| Scope | Repo-evidenced | Local is in scope; staging/prod approval remains open. |
| Route inventory | Repo-evidenced | Seeded from docs; needs live OpenAPI/browser crawl comparison. |
| Role matrix | Repo-evidenced | Expected access model seeded from docs; needs proxy/API execution. |
| Architecture map | Repo-evidenced | Text map created; diagram and live deployment details remain open. |
| Passive recon | Gap | Checklist created; no external searches performed. |
| Server fingerprinting | Gap | Local defaults documented; no live header/TLS captures performed. |
| Proxy crawls | Gap | Role crawl placeholders created only. |
| Findings log | Partially evidenced | Existing gaps logged; exploit/test findings not yet collected. |
