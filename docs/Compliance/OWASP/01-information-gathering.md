# 01 - Information gathering plan

This is the first operational OWASP workstream for OpenScribe. It follows the OWASP Web Security Testing Guide approach: build an application and infrastructure map before testing the individual OWASP Top 10 categories.

## 1. Purpose

Information gathering should produce a reliable test map:

- application routes and endpoints;
- request methods, parameters, uploads, headers, cookies, and redirects;
- authentication and role requirements;
- architecture and trust boundaries;
- exposed metadata, headers, and framework clues;
- external integrations and provider configuration paths;
- a dated evidence pack that later tests can reference.

## 2. Source material already available in the repo

Use these checked-in sources to seed the evidence pack before crawling the running app:

| Source | Use |
| --- | --- |
| `README.md` | Primary local URLs and documentation entry points. |
| `docs/api.md` | API route groups and endpoint inventory. |
| `docs/auth.md` | Authentication, onboarding, MFA, trusted-device, rate-limit, and authorization rules. |
| `docs/security.md` | Current security model, headers, CSP, CSRF, access-control rules, provider-secret rules, and known limitations. |
| `docs/setup.md` | Local environment, Vault, Postgres, Redis, Celery, encryption, CSRF, and mail setup. |
| `docs/stt-config.md` | STT provider configuration, OpenAPI inspection, credential storage, HTTPS rules, and authority split. |
| `docs/transcript-capture.md` | Transcript capture flows, upload modes, owner-only boundaries, VAD/chunking, and ingestion lifecycle. |
| `docs/security-xss.md` | XSS probe plan and current public/authenticated coverage. |
| `docs/testing.md` | Test strategy, route audit, CSRF browser regression, auth/access-control tests, encryption tests, and ingestion smoke tests. |
| `docs/dbtesting.md` | Database boundary tests, auth scope checks, encrypted-at-rest checks, provider secret checks, transcript ownership checks. |
| `docs/usage_tab.md` | Metadata-only admin usage telemetry scope. |
| `.env.example` | Local configuration variables and rate-limit/upload defaults. |
| `docker-compose.yml` | Local Postgres, Redis, and Vault services and binding assumptions. |
| `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/11-remediation-plan.md` | Current CSP, local browser assets, and security-header remediation evidence. |

## 3. WSTG information-gathering checklist

| WSTG area | OpenScribe task | Tooling | Output | Initial status |
| --- | --- | --- | --- | --- |
| Search-engine discovery | Search for exposed OpenScribe domains, docs, errors, cached pages, and accidentally indexed files. | Browser search, Wayback, Shodan/Censys if authorised | `04-passive-recon.md` | Not started |
| Server fingerprinting | Record response headers, TLS details, exposed ports, default error pages, and reverse-proxy clues. | Browser DevTools, curl, openssl, nmap, nikto, SSL tools | `05-server-fingerprinting.md` | Not started |
| Review webserver metafiles | Check `robots.txt`, `sitemap.xml`, security policy pages, static indexes, and common metadata paths. | Browser, curl, ZAP passive crawl | `04-passive-recon.md` | Not started |
| Enumerate applications | Identify all app surfaces: public app, admin UI, API docs, static assets, health endpoints if present. | Browser, ZAP/Burp, repo route review | `01-route-inventory.csv` | Repo seed only |
| Review webpage content | Review rendered HTML, comments, scripts, local storage usage, CSP, static assets, and hidden form fields. | Browser DevTools, ZAP/Burp | `06-proxy-crawl-summary.md` | Not started |
| Identify entry points | Record every route, method, parameter, upload field, cookie, redirect, and auth state. | ZAP/Burp, browser, app OpenAPI docs, curl | `01-route-inventory.csv` | Repo seed only |
| Map execution paths | Map multi-step flows such as onboarding, MFA, account recovery, transcript start/upload/generation, provider setup. | Browser, proxy, manual flow diagrams | `03-architecture-map.md` and flow notes | Repo seed only |
| Fingerprint framework | Identify framework and server headers without relying on banners alone. | Headers, HTML, static assets, error handling | `05-server-fingerprinting.md` | Not started |
| Fingerprint application | Identify OpenScribe version/commit/environment and enabled feature flags without leaking secrets. | Repo metadata, app UI, admin metadata if safe | `04-passive-recon.md` | Not started |
| Map architecture | Build trust-boundary diagram for browser, FastAPI, Postgres, Redis, Vault, Celery, mail, STT, LLM, de-ID, clinical NLP. | Repo docs, deployment notes, diagrams | `03-architecture-map.md` | Repo seed only |

## 4. Role-based crawl plan

Create synthetic accounts for each role and crawl separately.

| Role | Required coverage |
| --- | --- |
| Anonymous | `/`, `/login`, `/request-access`, reset/activation pages, static assets, denied API access. |
| Onboarding session | Allowed onboarding/current-user/logout routes only; blocked normal app and transcript routes. |
| Pending-MFA session | Allowed MFA/current-user/logout routes only; blocked normal app and transcript routes. |
| Normal user | `/home`, `/transcribe`, transcript start/list/detail/update/delete/commit, audio upload, generated documents, personal templates, personal smart phrases, preferences. |
| Team leader | Own-team user management, account request review, team templates, team smart phrases, team STT/LLM selection only, no transcript-content readability expansion. |
| System admin | `/admin`, team and user management, provider config, account requests, usage metadata, STT/LLM/de-ID/clinical NLP configuration, no transcript-content readability expansion. |

## 5. Initial route inventory seed

Seed the route inventory from `README.md` and `docs/api.md`, then verify it against the running app.

### Browser route seed

| Route | Expected auth state | Notes |
| --- | --- | --- |
| `/` | Public or redirect based on session | Splash page for anonymous users; signed-in users redirected by state. |
| `/login` | Public | Login/bootstrap. |
| `/request-access` | Public | Public account request form. |
| `/forgot-password` | Public when mail is enabled | Password reset request. |
| `/reset-password` | Public token flow | Reset confirmation. |
| `/activate-account` | Public token flow | First account activation/setup. |
| `/onboarding` | Onboarding session | Password change and TOTP enrolment. |
| `/mfa/challenge` | Pending-MFA session | TOTP challenge. |
| `/home` | Full authenticated user | User home. |
| `/transcribe` | Full authenticated owner | Main transcription workspace. |
| `/admin` | System admin | Admin UI. |
| `/logout` | Session-aware | Clears session-related cookies. |

### API route-group seed

| Group | Main OWASP focus |
| --- | --- |
| Auth | A07, A01, A09 |
| Public account requests | A03, A05, A07, A09 |
| Manager review | A01, A09 |
| Onboarding | A07, A01 |
| Team management | A01, A09 |
| User management | A01, A07, A09 |
| Transcripts | A01, A02, A03, A09 |
| Templates | A01, A03, A08 |
| Smart phrases | A01, A03, A08 |
| Team STT configuration | A01, A02, A05, A10 |
| Team LLM configuration | A01, A02, A05, A10, AI safety |
| De-identification and clinical NLP provider configuration | A01, A02, A05, A10, AI safety |

## 6. Evidence outputs to capture

| Evidence item | Minimum content |
| --- | --- |
| Route inventory | Path, method, auth requirement, role, parameters, body fields, upload fields, cookies, CSRF/header requirements, expected result. |
| Role matrix | Anonymous, onboarding, pending-MFA, normal user, leader, admin; expected allow/deny per major route group. |
| Proxy crawl summary | Tool, date, account role, number of requests captured, excluded requests, redactions applied. |
| Architecture map | Components, trust boundaries, data stores, provider calls, credential boundaries, content boundaries. |
| Header/TLS summary | Security headers, CSP, HSTS, cookie flags, TLS protocol/cipher summary, certificate identity. |
| Static/content review | Third-party script/style usage, CSP compatibility, static asset source, HTML comments, debug output. |
| Findings log | Finding ID, OWASP mapping, affected route, severity, evidence, remediation owner, status, retest result. |

## 7. Tooling plan

| Tool | Purpose | Evidence to keep |
| --- | --- | --- |
| Browser DevTools | Manual route and storage review. | Screenshots and notes. |
| OWASP ZAP | Passive crawl, spider, baseline checks, exported route list. | ZAP report, alert export, crawl summary. |
| Burp Suite or ZAP manual proxy | Request replay, role switching, parameter inventory. | Redacted proxy history, screenshots, repeater notes. |
| curl/httpie | Repeatable route checks and header captures. | Command transcript with sensitive values redacted. |
| OpenSSL/TLS tooling | TLS certificate and protocol evidence. | TLS summary report. |
| nmap/nikto or equivalent | Approved server fingerprinting and common misconfiguration checks. | Scan summary with scope confirmation. |
| ripgrep | Repo-level checks for routes, CDN dependencies, secrets patterns, debug flags, and unsafe sinks. | Command output summaries. |
| pytest | Existing regression evidence. | Test command, commit SHA, pass/fail summary. |

## 8. OpenScribe-specific information-gathering focus

### 8.1 Provider configuration and SSRF surface

The STT, LLM, de-identification, and clinical NLP configuration flows are high-priority because they allow admins to configure URLs or provider endpoints. Information gathering must record:

- which roles can create provider configs;
- which fields accept URLs;
- whether non-local endpoints require HTTPS;
- whether local or private-network endpoints are accepted only for development;
- whether OpenAPI inspection or provider discovery fetches remote documents;
- whether raw provider responses are ever exposed beyond synthetic inspection flows;
- whether raw credentials or Vault references are returned to browser/API clients.

### 8.2 Transcript-derived content boundary

Information gathering must distinguish metadata routes from content routes:

| Data type | Expected visibility |
| --- | --- |
| Transcript text | Owner only. |
| Generated document body | Owner only. |
| Working note | Owner only. |
| PII original values | Owner only through explicit reveal flow. |
| Provider credentials | Never exposed to normal users/leaders; never returned raw. |
| Admin usage telemetry | Metadata only, no transcript/note/prompt text. |
| Team/user metadata | Admin/leader scoped by role. |

### 8.3 Upload and ingestion paths

Record all upload paths and constraints:

- whole-file audio upload;
- live audio chunk upload;
- retry source-audio handling;
- post-consultation dictation preview/upload;
- quick-action context audio preview;
- accepted MIME types and size limits;
- rate limits;
- owner-only checks;
- ingestion mode checks;
- worker queue handoff;
- Vault-backed source/audio or secret references.

### 8.4 Browser security posture

For each browser route, record:

- CSP header;
- HSTS where HTTPS is used;
- `X-Frame-Options`;
- `X-Content-Type-Options`;
- `Referrer-Policy`;
- `Cache-Control` on sensitive routes;
- cookie flags;
- CSRF token handling;
- third-party scripts/styles/fonts;
- whether forms or JavaScript requests submit the expected CSRF token.

## 9. Initial deliverables

| Deliverable | Target file |
| --- | --- |
| Confirmed scope | `security-evidence/owasp/YYYY-MM-DD/00-scope.md` |
| Route inventory | `security-evidence/owasp/YYYY-MM-DD/01-route-inventory.csv` |
| Role/access matrix | `security-evidence/owasp/YYYY-MM-DD/02-role-access-matrix.csv` |
| Architecture map | `security-evidence/owasp/YYYY-MM-DD/03-architecture-map.md` |
| Passive recon notes | `security-evidence/owasp/YYYY-MM-DD/04-passive-recon.md` |
| Server fingerprinting notes | `security-evidence/owasp/YYYY-MM-DD/05-server-fingerprinting.md` |
| Proxy crawl summary | `security-evidence/owasp/YYYY-MM-DD/06-proxy-crawl-summary.md` |
| Findings and remediation log | `security-evidence/owasp/YYYY-MM-DD/09-findings-and-remediation.md` |

## 10. Exit criteria

Information gathering is complete when:

- every documented route group has been compared with the running app;
- every role has a crawl summary;
- all externally configurable URLs and provider discovery paths are identified;
- every upload path is recorded with size/rate/owner constraints;
- all security headers and cookie properties are sampled;
- all findings and evidence gaps have a tracked next action.
