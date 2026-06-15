# 00 - Scope and evidence pack

Date started: 2026-06-14  
Branch: `OWASP`  
Repository: `AbsurdSyssie/OpenScribe`

This document defines the first OpenScribe OWASP workstream: authorised scope, evidence structure, existing repo-backed evidence, gaps, and first evidence tasks.

## 1. Objective

Create a repeatable evidence pack that shows OpenScribe has been reviewed against the OWASP Top 10 and the OWASP Web Security Testing Guide, starting with information gathering.

The evidence pack must support later DTAC and supplier-assurance review by showing:

- what was tested;
- which roles and routes were covered;
- which tools were used;
- what evidence was captured;
- what issues were found;
- what remediation and retesting occurred.

## 2. Product scope

OpenScribe is treated as a web application with clinical-scribe functionality. The current repo evidence shows:

- Browser routes for public access, login, onboarding, MFA, user home, transcription workspace, and admin UI. See `README.md`.
- Versioned API routes under `/api/v1`. See `docs/api.md`.
- Transcript capture, audio upload, live chunk ingestion, generated documents, working notes, redaction, and clinical NLP provider selection. See `docs/api.md`, `docs/transcript-capture.md`, and `docs/dbtesting.md`.
- Team-scoped STT and LLM provider configuration with Vault-backed credentials. See `docs/stt-config.md`, `docs/security.md`, and `docs/dbtesting.md`.
- Local development infrastructure using Postgres, Redis, and Vault. See `docker-compose.yml` and `docs/setup.md`.

## 3. Authorised environments

| Environment | Status | Notes |
| --- | --- | --- |
| Local development | In scope | Preferred environment for exploratory testing, destructive regression tests, and evidence collection using synthetic data. |
| Staging | To confirm | Intended target for realistic proxy crawling, role-based access testing, and scanner evidence once staging is available and approved. |
| Production | Restricted | Passive, read-only checks only unless a written test window is approved. No destructive or high-volume testing by default. |
| Third-party provider systems | Out of scope by default | STT, LLM, mail, NLP, and other provider infrastructure must not be tested beyond normal configured product interactions unless the provider has explicitly authorised it. |

## 4. In-scope OpenScribe areas

| Area | Initial scope | Primary OWASP mapping |
| --- | --- | --- |
| Public pages | `/`, `/login`, `/request-access`, password reset and activation pages | A03, A05, A07, A09 |
| Authentication and session flows | Login, logout, onboarding, MFA, trusted devices, reset/recovery | A01, A07, A09 |
| Browser CSRF and security headers | CSRF cookies, session-bound CSRF, CSP, HSTS, no-store responses | A02, A05, A08 |
| Account requests and user management | Account requests, approval/rejection, suspend/reactivate/delete, manager recovery | A01, A07, A09 |
| Team and role boundaries | Normal user, team leader, system admin | A01, A04, A09 |
| Transcript roots | Start, list, get, update, delete, commit/version history | A01, A02, A03, A09 |
| Audio ingestion | Whole-file upload, live audio chunks, retry source audio | A01, A02, A03, A05, A10 |
| Generated documents | Generated notes, follow-up messages, quick actions, structured sections | A01, A03, A04, A09, AI safety |
| Templates and smart phrases | Team and personal template/phrase CRUD | A01, A03, A08 |
| PII and redaction | Redaction runs, manual PII, PII reveal controls, debug views | A01, A02, A03, A09, AI safety |
| STT provider configuration | Provider setup, credential lifecycle, OpenAPI inspection, team selection | A01, A02, A05, A10 |
| LLM provider configuration | Provider setup, credential lifecycle, model discovery, team/user selection | A01, A02, A05, A10, AI safety |
| De-identification and clinical NLP providers | Endpoint configuration, inspection, assignment, selection | A01, A02, A05, A10, AI safety |
| Admin usage observability | Metadata-only usage tab and provider telemetry | A01, A09 |
| Local infrastructure defaults | Postgres, Redis, Vault, Celery broker/result backend | A02, A05, A06, A08 |
| Dependency and build chain | Python dependencies, static browser assets, local vendor assets | A06, A08 |

## 5. Out-of-scope and constraints

- Real patient data.
- Live clinical encounters.
- Unauthorised third-party infrastructure testing.
- Denial-of-service testing or high-volume load testing without approval.
- Social engineering.
- Physical security.
- Endpoint compromise of tester machines or user devices.
- Destructive production testing without a written test window.
- Retention, legal, and clinical-safety assertions that require organisational sign-off rather than repo evidence.

## 6. Evidence-pack structure

Store evidence inside this OWASP directory under a dated folder using this pattern:

```text
docs/Compliance/OWASP/security-evidence/owasp/YYYY-MM-DD/
  00-scope.md
  01-route-inventory.csv
  02-role-access-matrix.csv
  03-architecture-map.md
  04-passive-recon.md
  05-server-fingerprinting.md
  06-proxy-crawl-summary.md
  07-tool-outputs/
  08-screenshots/
  09-findings-and-remediation.md
  10-retest-log.md
```

Do not store sensitive raw evidence in the repo. If an artefact contains secrets, tokens, patient-identifiable content, transcript text, audio, or generated clinical notes, redact it or store it in a controlled private evidence vault and commit only a redacted summary.

## 7. Current repo-backed evidence

This section records what is already supported by checked-in documentation. Status is `Repo-evidenced` only; it still needs current test execution before it becomes `Test-evidenced`.

| Evidence area | Status | Repo source | Notes |
| --- | --- | --- | --- |
| Route entry points | Repo-evidenced | `README.md`, `docs/api.md` | README lists primary browser URLs. API docs list implemented `/api/v1` groups including auth, transcripts, templates, smart phrases, STT, LLM, de-identification, and clinical NLP. |
| Local infrastructure | Repo-evidenced | `docker-compose.yml`, `.env.example`, `docs/setup.md` | Local stack includes Postgres, Redis, Vault, Celery configuration, and localhost-only service bindings for Postgres/Redis/Vault. |
| Authentication model | Repo-evidenced | `docs/auth.md`, `docs/security.md`, `docs/dbtesting.md` | Opaque cookie-backed sessions, database-stored session state, onboarding-only sessions, pending-MFA sessions, TOTP, trusted-device freshness, and session revocation are documented. |
| Password and recovery handling | Repo-evidenced | `docs/auth.md`, `docs/security.md`, `docs/dbtesting.md` | Argon2id password hashing, hashed reset/setup tokens, reset session revocation, and manager recovery controls are documented. |
| CSRF and security headers | Repo-evidenced | `docs/security.md`, `security_remediation_plan.md`, `docs/testing.md` | CSRF model, production secure-cookie requirement, HSTS, CSP, no-store sensitive responses, and regression tests are documented. |
| Access-control boundaries | Repo-evidenced | `docs/security.md`, `docs/auth.md`, `docs/dbtesting.md` | Transcript-derived content is owner-only. Leaders/admins manage metadata and configuration but do not gain transcript readability. |
| Route audit guardrail | Repo-evidenced | `docs/security.md`, `docs/testing.md` | API auth audit manifest and script are documented as a guardrail for new `/api/v1` routes. |
| Encryption at rest | Repo-evidenced | `docs/setup.md`, `docs/transcript-capture.md`, `docs/dbtesting.md` | Transcript drafts, versions, STT results, generated-document content, redaction output, manual PII, and working notes are documented as encrypted at rest. |
| Secrets management | Repo-evidenced | `docs/stt-config.md`, `docs/security.md`, `docs/dbtesting.md` | STT/LLM secrets are Vault-backed; APIs return metadata such as `has_secret`, not raw provider secrets or raw Vault references. |
| Provider endpoint controls | Repo-evidenced | `docs/stt-config.md`, `docs/security.md`, `docs/dbtesting.md` | Remote non-local endpoints must use HTTPS; local/RFC1918 endpoints are allowed for development. Generic REST/OpenAPI inspection exists and requires SSRF-focused review. |
| Upload and ingestion controls | Repo-evidenced | `docs/transcript-capture.md`, `docs/dbtesting.md`, `docs/testing.md` | Owner-only uploads, ingestion-mode checks, duplicate chunk rejection, rate limits, audio normalization, and missing-STT failure paths are documented. |
| XSS coverage | Partially evidenced | `docs/security-xss.md`, `security_remediation_plan.md` | Public XSS checks and a probe script are documented. Authenticated/stored XSS coverage has recommended follow-up items. |
| Rate limiting | Repo-evidenced | `docs/auth.md`, `docs/security.md`, `docs/testing.md` | Login, TOTP, account request, live chunk, and whole-file upload rate limiting are documented. Current limitations are documented. |
| Logging and monitoring | Partially evidenced | `docs/security.md`, `docs/usage_tab.md`, `docs/testing.md` | Security and audit logging exists in docs, and usage telemetry is metadata-only. Persistent audit-event storage remains a gap for some actions. |
| Dependency and asset integrity | Partially evidenced | `docs/security.md`, `security_remediation_plan.md` | CSP/local asset rules are documented. A full SBOM/dependency scanning evidence pack still needs to be collected. |
| Test suite coverage | Repo-evidenced | `docs/testing.md`, `docs/dbtesting.md` | Pytest, CSRF browser tests, file-ingestion smoke test, route audit, and many auth/access/encryption/provider tests are documented. |

## 8. Initial gap register

| Gap | OWASP mapping | Required evidence/action |
| --- | --- | --- |
| Current route inventory has not yet been exported from a live app crawl. | A01, A03, A05, A09 | Use ZAP/Burp/manual browser crawl as anonymous, user, leader, and admin. Export route inventory. |
| No current dated proxy evidence pack exists for each role. | A01, A07 | Capture request/response samples and role matrix using synthetic users. |
| Authenticated stored-XSS coverage is not yet complete. | A03 | Run documented XSS probe authenticated suite and add coverage for team templates, team quick actions, account request review screens, provider labels, and generated-document titles. |
| SSRF testing for provider inspection/configuration is not yet evidenced. | A10 | Build safe internal test cases for OpenAPI inspection and provider URLs using controlled canary endpoints. |
| Persistent audit-event storage remains incomplete for some lifecycle actions. | A09 | Create remediation ticket for durable security/audit event storage where logger-only audit is currently documented. |
| No dated dependency/SBOM evidence has been committed. | A06, A08 | Run dependency and container scanning; store redacted reports or summary evidence. |
| No current external TLS/header report is attached. | A02, A05 | Capture TLS and header evidence for approved environment. |
| No formal threat model is present in this OWASP pack yet. | A04 | Create architecture/trust-boundary diagram and misuse cases for transcript content, provider configuration, and generated output. |
| No clinical/AI safety testing plan is attached yet. | A04, AI safety | Add prompt-injection, hallucination, redaction, and human-review evidence plan. |

## 9. First evidence tasks

| Task | Owner | Output | Status |
| --- | --- | --- | --- |
| Confirm environments and authorised test window. | OWASP evidence agent | `security-evidence/owasp/2026-06-14/00-scope.md` | Repo-seeded; staging/prod windows remain gap |
| Export API route inventory from repo and running app. | OWASP evidence agent | `01-route-inventory.csv` | Repo-seeded; live crawl remains gap |
| Crawl public routes without authentication. | TBD | Proxy export and screenshot summary | Gap |
| Crawl normal-user routes. | TBD | Proxy export and route matrix | Gap |
| Crawl team-leader routes. | TBD | Proxy export and route matrix | Gap |
| Crawl system-admin routes. | TBD | Proxy export and route matrix | Gap |
| Produce architecture/trust-boundary map. | OWASP evidence agent | `03-architecture-map.md` | Repo-seeded; diagram/export remains gap |
| Run passive search/recon checklist. | OWASP evidence agent | `04-passive-recon.md` | Checklist seeded; external recon remains gap |
| Run approved server fingerprinting. | OWASP evidence agent | `05-server-fingerprinting.md` | Local repo evidence seeded; live headers/TLS remain gap |
| Seed OWASP Top 10 matrix from this document. | OWASP evidence agent | `owasp-top-10-matrix.md` | Repo-seeded |

## 10. Completion criteria for this phase

This first phase is complete when:

- every public and authenticated route has an owner, auth requirement, role expectation, and test status;
- every role has a captured proxy crawl summary;
- the architecture map identifies browser, app, database, Redis, Vault, workers, mail, STT, LLM, de-identification, and clinical NLP trust boundaries;
- evidence is stored in the dated evidence pack with sensitive values redacted;
- all gaps are converted into tracked remediation or follow-up test items.
