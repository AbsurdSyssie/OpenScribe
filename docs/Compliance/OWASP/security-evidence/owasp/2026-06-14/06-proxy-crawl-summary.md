# 06 - Proxy Crawl Summary

Status: partial local authenticated crawl completed 2026-06-15 for anonymous, seeded normal user, and seeded team leader. Onboarding-only, pending-MFA, and system-admin crawl remain gaps.

## Evidence Rules

- Use synthetic accounts and synthetic consultation content.
- Redact cookies, CSRF tokens, reset/setup tokens, provider tokens, transcript text, note text, prompt text, model responses, audio, and patient identifiers before committing summaries.
- Store raw proxy histories outside git in controlled evidence storage if needed.
- Commit only summary counts, route coverage, excluded requests, and sanitized examples.

## Role Crawl Matrix

| Role/session | Account/data needed | Minimum routes | Status |
| --- | --- | --- | --- |
| Anonymous | None | `/`, `/login`, `/request-access`, reset/activation pages, denied protected APIs | Partial local evidence |
| Onboarding-only | Synthetic managed account before password/TOTP completion | `/onboarding`, onboarding APIs, denied normal/transcript/admin APIs | Gap |
| Pending-MFA | Synthetic account after password before TOTP | `/mfa/challenge`, TOTP APIs, denied normal/transcript/admin APIs | Gap |
| Normal user | Synthetic team user with owned transcript(s) | `/home`, `/transcribe`, transcript CRUD, upload, Working note, generated docs, personal templates/phrases/preferences | Partial local evidence |
| Team leader | Synthetic leader in same team | User/account request management, team template/action scope, provider selections, denied transcript content of other users | Partial local evidence |
| System admin | Synthetic system admin | `/admin`, teams/users/provider provisioning, account requests, usage metadata, denied content-read expansion | Gap |

## Crawl Summary Template

Use one section per crawl execution.

### Crawl: ROLE - YYYY-MM-DD HH:MM

| Field | Value |
| --- | --- |
| Tool | TBD |
| Environment | TBD |
| Base URL | TBD |
| Synthetic account | Redacted identifier only |
| Requests captured | TBD |
| Unique paths | TBD |
| Exclusions | Static assets, health checks, provider calls, or other exclusions TBD |
| Redactions applied | Cookies, CSRF, tokens, content, secrets |
| Findings created | TBD |
| Retest needed | TBD |

## Expected Negative Tests During Crawl

| Scenario | Expected result | OWASP focus |
| --- | --- | --- |
| Anonymous calls protected `/api/v1` route | `401 unauthorized` | A01 A07 |
| Onboarding session calls transcript route | `403 onboarding_incomplete` | A01 A07 |
| Pending-MFA session calls transcript route | `403 mfa_required` | A01 A07 |
| Normal user calls another user's transcript | Deny/not-found without content | A01 |
| Leader calls team user's transcript content | Deny/not-found without content | A01 |
| Leader provisions provider credential | Deny | A01 A02 |
| Normal user calls provider selection/provision routes | Deny | A01 |
| Unsafe API call with bad/missing CSRF in browser session | Deny | A05 A08 |
| Stored-content fields receive HTML/script payloads | Render escaped/sanitized; no execution | A03 |
| Provider inspect URL points at controlled internal canary | Block or safe behavior per SSRF policy | A10 |

## Crawl: Local Seeded Dev Accounts - 2026-06-15

| Field | Value |
| --- | --- |
| Tool | OWASP ZAP `2.17.0` daemon proxy plus safe GET-only Python client |
| Environment | Local dev app |
| Base URL | `http://127.0.0.1:8080` |
| Synthetic account | Seeded dev normal user and seeded dev team leader |
| Requests captured | 95 ZAP messages; 75 scripted role requests |
| Unique paths | 46 ZAP site-tree paths |
| Exclusions | Active scan, uploads, destructive POSTs, provider inspect/test calls, content generation, transcript/note content capture |
| Redactions applied | Cookies, CSRF, tokens, content, secrets |
| Findings created | None. Existing accepted scanner warnings still apply: readable CSRF cookie, session/auth auto-detection, authenticated-page CSRF heuristic. |
| Retest needed | Onboarding-only, pending-MFA, system-admin role crawl; optional browser-driven crawl with JS execution. |

### Local Crawl Results

| Scenario | Result |
| --- | --- |
| Anonymous protected browser routes | `/home`, `/transcribe`, `/admin` returned `303` to `/login`. |
| Anonymous protected API routes | `/api/v1/auth/me`, `/api/v1/transcripts`, `/api/v1/users` returned `401`. |
| Normal user allowed routes | `/home`, `/transcribe`, `/transcribe-glm-2`, `/api/v1/auth/me`, preferences, personal templates/actions, smart phrases, transcripts, and workspace returned `200`. |
| Normal user denied routes | Account requests, team/user management, team templates/actions, provider selections/configs, and `/admin` returned `403`. |
| Team leader allowed routes | Account requests, own-team users, provider selections/options, team templates/actions, own transcript/workspace surfaces returned `200`. |
| Team leader denied routes | System team listing, `/admin`, and system provider config lists returned `403`. |
| Query/context-required routes | Template editor routes returned `422`; no content exposure observed. |

### Local ZAP Alert Triage

| Alert | Count/Risk | Triage |
| --- | --- | --- |
| `Cookie No HttpOnly Flag` | 36 low | Existing accepted `openscribe_csrf` design; auth-bearing cookies remain `HttpOnly`. |
| `Absence of Anti-CSRF Tokens` | 42 medium, low confidence | Authenticated pages rely on signed CSRF cookie plus same-origin/origin checks and JS/header submission. Public forms already have hidden `_csrf_token` and passed production ZAP retest. |
| Auth/session identification | 45 informational including login/session notices | Expected scanner auto-detection. |

Evidence: `07-tool-outputs/zap/zap-auth-crawl-local-2026-06-15-summary.md`.

## Gap Notes

- Local proxy tool chosen for this slice: OWASP ZAP.
- Seeded local normal user and team leader confirmed; system-admin/onboarding/pending-MFA accounts still need setup for crawl evidence.
- Need decide raw evidence vault location for proxy histories.
- Need collect screenshots or HAR summaries with sensitive values redacted.
