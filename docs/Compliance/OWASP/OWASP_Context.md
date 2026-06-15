# OWASP Context

Date created: 2026-06-14  
Current phase: public passive recon, remediation, authenticated local crawl, and retest tracking  
Evidence root: `docs/Compliance/OWASP/security-evidence/owasp/`

## Purpose

This file is carry-forward context for future agents working on the OWASP pack. Keep it current when adding workstreams, dated evidence folders, or remediation logs.

## Directory Rules

- Keep OWASP planning and evidence under `docs/Compliance/OWASP`.
- Use dated evidence folders: `security-evidence/owasp/YYYY-MM-DD/`.
- Store redacted summaries in git. Do not commit raw proxy dumps, cookies, tokens, patient content, transcript text, note text, prompts, provider responses with clinical content, audio, or raw secrets.
- If sensitive raw evidence is needed, reference its controlled private evidence-vault location from a redacted summary.
- Keep status labels consistent with `README.md`: `Repo-evidenced`, `Test-evidenced`, `Partially evidenced`, `Gap`, `Not in scope`.

## Current Structure

- `README.md`: OWASP pack index, status labels, evidence handling rules.
- `00-scope-and-evidence-pack.md`: authorised scope, repo-backed evidence, gaps, initial tasks.
- `01-information-gathering.md`: WSTG information-gathering plan.
- `security-evidence/owasp/2026-06-14/`: initial evidence pack seeded from repo docs.

## 2026-06-14 Evidence Pack Contents

- `00-scope.md`: confirmed local scope, restrictions, evidence sources, open authorisation gaps.
- `01-route-inventory.csv`: repo-seeded browser/API route inventory with auth and OWASP mappings.
- `02-role-access-matrix.csv`: expected allow/deny matrix by role and route group.
- `03-architecture-map.md`: repo-seeded trust-boundary map for browser, app, DB, Redis, Vault, Celery, mail, STT, LLM, de-ID, and clinical NLP.
- `04-passive-recon.md`: passive recon checklist and repo-backed findings.
- `05-server-fingerprinting.md`: local infra/server fingerprinting plan and repo-backed defaults.
- `06-proxy-crawl-summary.md`: crawl plan and placeholder summaries for each role.
- `09-findings-and-remediation.md`: initial finding/gap register.
- `10-retest-log.md`: retest log with public-form CSRF production retest evidence.
- `11-remediation-plan.md`: remediation plan for public passive recon/server fingerprinting findings.
- `owasp-top-10-matrix.md`: OWASP Top 10 coverage matrix seeded from repo docs.
- `07-tool-outputs/`: redacted public passive HTTP/TLS summary and OWASP ZAP baseline outputs for `openscribe.co.uk`.

## 2026-06-14 Public Passive Capture

- Target: `https://openscribe.co.uk`.
- Tools: Python stdlib HTTP/TLS capture; OWASP ZAP Docker baseline (`zaproxy/zap-stable`) with 1-minute unauthenticated baseline spider/passive scan.
- ZAP output path: `security-evidence/owasp/2026-06-14/07-tool-outputs/zap/`.
- ZAP result: 12 URLs observed, 0 fail alerts, 9 warning alert types, 58 pass rules.
- Public findings opened: unauthenticated `/docs` and `/openapi.json`, missing `Permissions-Policy`/COEP hardening, CSRF scanner mismatch on public forms, duplicate-HSTS triage, missing/redirected `security.txt`/robots/sitemap metadata.
- ZAP baseline submitted public forms with dummy/default values and received 403 responses. It was not an authenticated crawl and did not run active attack scans.
- Remediation plan for the public passive capture lives at `security-evidence/owasp/2026-06-14/11-remediation-plan.md`.
- Project owner accepted public `/docs` and `/openapi.json` exposure because OpenScribe is open source; future cycles should still review the schema for secrets/internal-only details.
- CSRF scanner mismatch was remediated in code on 2026-06-14 by server-rendering hidden `_csrf_token` inputs on public forms. Production ZAP baseline retest on 2026-06-14 passed `Absence of Anti-CSRF Tokens [10202]`; evidence is in `07-tool-outputs/zap/zap-baseline-retest-2026-06-14.*` and `10-retest-log.md`.
- Header hardening was remediated in code on 2026-06-14 by adding `Permissions-Policy`, `Cross-Origin-Embedder-Policy: credentialless`, public auth-page `no-store`, and HSTS ownership controls. Production ZAP retests resolved `OWASP-2026-06-14-010` and `OWASP-2026-06-14-012`. Current production HSTS is Cloudflare-owned: 6 months, include subdomains, preload off; keep app `HSTS_SOURCE=proxy` while that remains true.
- Public metadata follow-up was resolved on 2026-06-14: `/robots.txt` and `/.well-known/security.txt` are explicit public text responses, `/sitemap.xml` returns intentional `404`, and metadata/static GETs do not issue CSRF cookies. After Cloudflare `/robots.txt` cache purge, production ZAP retest no longer crawled literal `/$`; `OWASP-2026-06-14-013` is resolved.
- ZAP `Cookie No HttpOnly Flag` on `openscribe_csrf` is accepted as `OWASP-2026-06-14-014`: the cookie is a signed, non-authoritative CSRF token that must be readable for browser `X-CSRF-Token`; auth-bearing `openscribe_session` and `openscribe_trusted_device` remain `HttpOnly`, anonymous nonce cookie remains `HttpOnly`, and CSRF cookie alone does not authenticate API access.
- Cache-control triage is accepted as `OWASP-2026-06-14-015`: `/`, public auth/account pages, and `/api`/`/api/` are `no-store`; metadata and static assets are cookie-free public cache. Production ZAP retest left only informational design/heuristic cache alerts: no-store routes, non-cacheable redirects, and public cached metadata/static content.
- Auth/session scanner auto-detection is accepted as `OWASP-2026-06-14-016`: ZAP identified expected `/login` auth fields and anonymous CSRF cookies, not public auth-bearing session exposure. Regression tests prove CSRF cookies alone do not authenticate and auth-bearing cookies remain `HttpOnly`.
- Public passive search/archive/DNS recon completed 2026-06-14: zero search engine indexed pages (DuckDuckGo, Bing confirmed; Google bot-blocked on `webfetch`), zero Wayback Machine captures (CDX empty, never archived), Cloudflare-only DNS edge with no origin IP exposed, all production hardening headers confirmed via HackerTarget. Shodan and Censys require authenticated sessions; marked `Not in scope` for this public passive cycle. Evidence: `07-tool-outputs/passive-recon-search-archive-exposure-2026-06-14.md`. `OWASP-2026-06-14-001` closed; `OWASP-2026-06-14-002` (authenticated role crawl) remains open pending synthetic accounts and authorised test window.
- Dependency/SBOM scan completed 2026-06-14 using `pip-audit`. 9 of 11 pinned-package vulns resolved: cryptography 46.0.3→46.0.7, requests 2.32.5→2.33.0, python-multipart 0.0.22→0.0.32, idna 3.11→3.18. 5 remaining vulns in starlette 0.38.6 (FastAPI-constrained, →`OWASP-2026-06-14-017`) and pytest 8.3.3 (dev-only, accepted). Static vendor assets (lucide, sortable, vad-web, onnxruntime-web) and Docker infra images (postgres:16, redis:7, hashicorp/vault:1.17) inventoried. Evidence: `07-tool-outputs/sbom-pip-audit-2026-06-14.md`. `OWASP-2026-06-14-006` closed; starlette constraint tracked as `OWASP-2026-06-14-017`/`R-010`.
- TLS/header/cookie evidence formalized 2026-06-14: consolidated all prior production ZAP retest results (HSTS, Permissions-Policy, COEP, CSRF headers), Python stdlib header/cache captures, HackerTarget DNS/HTTP passive recon, and local regression tests into one evidence file. All 12 hardening headers production-confirmed; cookie contract verified; cache policy by route class documented. Evidence: `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`; `05-server-fingerprinting.md` updated. `OWASP-2026-06-14-007` closed.
- Starlette CVEs resolved 2026-06-14: FastAPI upgraded 0.115.0→0.137.0, starlette upgraded 0.38.6→1.3.1. pip-audit requirements.txt now shows only 1 remaining vuln (pytest, dev-only). Test suite: 44/44 security tests pass, 278 API smoke pass, 1 test fixed for starlette 1.x route lookup change (`api.routes` vs `app.routes`). `OWASP-2026-06-14-017` resolved.
- XSS coverage completed 2026-06-14: 2 bugs found and fixed (structured.js unescaped error_message, _workspace.html tojson without forceescape). 28 new XSS regression tests pass. Verified zero `|safe` filter usages, all innerHTML sites use escapeHtml(), CSP script-src-attr 'none' blocks inline event handlers. `OWASP-2026-06-14-003` closed.
- SSRF canary completed 2026-06-14: mapped all provider inspect/config/test endpoints. 23 canary tests pass covering URL validation (scheme, host, private-IP rules), auth gates (401/403 on inspect endpoints), and httpx redirect behavior (follow_redirects=False by default). No host allowlist by design (system admins configure providers). `OWASP-2026-06-14-004` closed.
- AI safety plan completed 2026-06-14: documented 6 threat cases (prompt injection, hallucination, data leakage, redaction failure, malicious model, model DoS) with mitigations mapped and acceptance criteria. Evidence: `08-ai-safety-plan.md`. `OWASP-2026-06-14-008` closed.
- Audit logging gap assessed 2026-06-14: `security_audit_events` table and `record_security_event()` service exist but only cover recovery flows (15 call sites). Account lifecycle, login, session, MFA, template/provider changes have zero audit persistence. Infrastructure exists for expansion. `OWASP-2026-06-14-005` remains open as `R-014`.
- Local authenticated ZAP crawl completed 2026-06-15 for seeded dev accounts. Scope: anonymous negative checks, seeded normal user, seeded team leader. Result: 95 ZAP messages, 46 unique paths, 75 role requests. Anonymous protected routes redirected/401; normal user content/personal surfaces allowed and admin/provider/team-management surfaces denied; team leader own-team metadata/config surfaces allowed and system/provider config lists denied. Evidence: `06-proxy-crawl-summary.md` and `07-tool-outputs/zap/zap-auth-crawl-local-2026-06-15-summary.md`. `OWASP-2026-06-14-002` is now partial, not blocked. Remaining: onboarding-only, pending-MFA, system-admin, browser JS crawl, and staging/production auth crawl if required.

## Evidence Boundaries

- Treat transcript-derived content as private owner-only content.
- Treat admin/leader evidence as metadata/config evidence only, not transcript readability evidence.
- Use synthetic accounts and synthetic consultation text only.
- Third-party STT, LLM, mail, de-identification, clinical NLP, and external scanning targets require explicit authorisation before active tests.
- Production is passive/read-only unless a written test window exists.

## Next Agent Tasks

- Start with `00-scope-and-evidence-pack.md` and `01-information-gathering.md`.
- If running local app, update `2026-06-14/01-route-inventory.csv` from actual OpenAPI/browser crawl output and mark tested routes `Test-evidenced` only when command/proxy evidence exists.
- Extend role crawl summaries in `06-proxy-crawl-summary.md`; do not commit unredacted proxy histories. Current local coverage includes anonymous, normal seeded user, and seeded team leader only.
- Convert remaining gaps in `09-findings-and-remediation.md` into tracked tickets or code/docs changes. `OWASP-2026-06-14-011` is resolved; do not reopen unless a future scan again loses `_csrf_token` evidence or form validation changes.
- Add new dated folder for a new test cycle rather than overwriting historic evidence, unless correcting obvious mistakes in this initial repo-seeded pack.
