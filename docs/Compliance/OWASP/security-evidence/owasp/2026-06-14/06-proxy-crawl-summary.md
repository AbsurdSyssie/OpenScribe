# 06 - Proxy Crawl Summary

Status: local authenticated crawl complete for anonymous, onboarding-only, pending-MFA, seeded normal user, seeded team leader, and seeded system admin. A shallow ZAP AJAX Spider browser crawl is also complete. External production system-admin GET-only crawl completed on 2026-06-24. Additional staging/production role crawls remain optional and require explicit authorization.

## Evidence Rules

- Use synthetic accounts and synthetic consultation content.
- Redact cookies, CSRF tokens, reset/setup tokens, provider tokens, transcript text, note text, prompt text, model responses, audio, and patient identifiers before committing summaries.
- Store raw proxy histories outside git in controlled evidence storage if needed.
- Commit only summary counts, route coverage, excluded requests, and sanitized examples.

## Role Crawl Matrix

| Role/session | Account/data needed | Minimum routes | Status |
| --- | --- | --- | --- |
| Anonymous | None | `/`, `/login`, `/request-access`, reset/activation pages, denied protected APIs | Partial local evidence |
| Onboarding-only | Synthetic managed account before password/TOTP completion | `/onboarding`, onboarding APIs, denied normal/transcript/admin APIs | Local evidence |
| Pending-MFA | Synthetic account after password before TOTP | `/mfa/challenge`, TOTP APIs, denied normal/transcript/admin APIs | Local evidence |
| Normal user | Synthetic team user with owned transcript(s) | `/home`, `/transcribe`, transcript CRUD, upload, Working note, generated docs, personal templates/phrases/preferences | Partial local evidence |
| Team leader | Synthetic leader in same team | User/account request management, team template/action scope, provider selections, denied transcript content of other users | Partial local evidence |
| System admin | Synthetic system admin | `/admin`, teams/users/provider provisioning, account requests, usage metadata, denied content-read expansion | Local evidence; external production GET-only evidence |

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

## Crawl: Local Seeded System Admin - 2026-06-23

| Field | Value |
| --- | --- |
| Tool | OWASP ZAP `2.17.0` daemon proxy plus safe GET-only Python client |
| Environment | Local dev app |
| Base URL | `http://127.0.0.1:8080` |
| Synthetic account | Seeded dev system admin |
| Requests captured | 72 ZAP messages; 56 scripted role requests |
| Unique paths | 46 ZAP site-tree paths |
| Exclusions | Active scan, uploads, destructive POST/DELETEs, provider inspect/test calls, content generation, raw proxy history export, transcript/note content capture |
| Redactions applied | Cookies, CSRF, tokens, content, secrets |
| Findings created | None. Existing accepted scanner warnings still apply: readable CSRF cookie, session/auth auto-detection, authenticated-page CSRF heuristic. ZAP also raised an informational user-controllable-attribute heuristic on admin query views; no executable payload evidence was captured. |
| Retest needed | Onboarding-only and pending-MFA role crawl; optional browser-driven crawl with JS execution; staging/production authenticated crawl only with explicit authorization. |

### Local System-Admin Crawl Results

| Scenario | Result |
| --- | --- |
| System-admin login | `200`, redirect target `/admin`. |
| Admin browser shell | `/admin`, `/admin2`, tabbed admin views, and default template editor returned `200`. |
| Admin metadata APIs | Teams, users, account requests, provider metadata, and team-scoped STT/LLM/de-identification/clinical-NLP selection/config routes returned `200`. |
| Admin provider routes without required team context | STT and LLM config list routes without `team_id` returned `422`; team-scoped equivalents returned `200`. |
| Admin personal/team user-library routes | Personal/team templates, quick actions, smart phrases, app preferences, and LLM preference user surfaces returned `403` where system admins should not own user libraries. |
| Admin browser content surfaces | `/home`, `/transcribe`, and `/transcribe-glm-2` returned `303` to `/admin`. |
| Admin transcript list/workspace probes | `/api/v1/transcripts` returned `200` with `0` items; `/api/v1/transcribe/workspace` returned `200` with `active_transcript = null`. No transcript/note content was printed or committed. |

### Local System-Admin ZAP Alert Triage

| Alert | Count/Risk | Triage |
| --- | --- | --- |
| `Cookie No HttpOnly Flag` | 42 low | Existing accepted `openscribe_csrf` design; auth-bearing cookies remain `HttpOnly`. |
| `Absence of Anti-CSRF Tokens` | 241 medium, low confidence | Authenticated admin pages rely on signed CSRF cookie plus same-origin/origin checks and JS/header submission. Public forms already have hidden `_csrf_token` and passed production ZAP retest. |
| Auth/session identification | 43 informational including login/session notices | Expected scanner auto-detection. |
| `User Controllable HTML Element Attribute (Potential XSS)` | 40 informational | Passive heuristic on `/admin` and `/admin2` query-driven views. Existing XSS coverage remains the controlling evidence unless a future active/manual test produces executable payload evidence. |

Evidence: `07-tool-outputs/zap/zap-auth-crawl-local-admin-2026-06-23-summary.md`.

## Crawl: External System Admin - 2026-06-24

| Field | Value |
| --- | --- |
| Tool | OWASP ZAP `2.17.0` daemon proxy plus safe GET-only Python client |
| Environment | Production public URL |
| Base URL | `https://openscribe.co.uk` |
| Synthetic account | Dedicated OWASP system-admin session cookie; identifier redacted |
| Requests captured | 38 ZAP URLs; 31 scripted role requests |
| Unique paths | 38 ZAP URLs |
| Exclusions | Active scan, POST/DELETE, uploads, provider inspect/test calls, content generation, raw proxy history export, transcript/note content capture |
| Redactions applied | Cookies, CSRF, tokens, content, secrets |
| Findings created | `OWASP-2026-06-14-019`: one low `Private IP Disclosure` alert on admin-only audit metadata. |
| Retest needed | None for private-IP disclosure. Optional external normal-user/team-leader/onboarding/pending-MFA crawls if those accounts are provisioned and authorised. |

### External System-Admin Crawl Results

| Scenario | Result |
| --- | --- |
| System-admin session validation | `/api/v1/auth/me` returned `200` with `is_system_admin=true` and `auth_level=full`. |
| Admin browser shell | `/admin`, `/admin2`, admin tabs, and default template editor returned `200`. |
| Admin metadata APIs | Teams, users, account requests, STT/LLM config/selection routes returned `200` with team context. |
| Admin personal/team user-library routes | Personal/team templates, quick actions, and smart phrases returned `403`. |
| Admin browser content surfaces | `/home`, `/transcribe`, and `/transcribe-glm-2` returned `303` to `/admin`. |
| Admin transcript list/workspace probes | `/api/v1/transcripts` returned `0` items; `/api/v1/transcribe/workspace` returned `active_transcript = null`. |

### External System-Admin ZAP Alert Triage

| Alert | Count/Risk | Triage |
| --- | --- | --- |
| `Absence of Anti-CSRF Tokens` | 209 medium, low confidence | Authenticated admin pages rely on signed CSRF cookie plus same-origin/origin checks and JS/header submission. Public forms already have hidden `_csrf_token` and passed production ZAP retest. |
| `Cookie No HttpOnly Flag` | 27 low | Existing accepted `openscribe_csrf` design; auth-bearing cookies remain `HttpOnly`. |
| Auth/session identification | 32 informational | Expected scanner auto-detection. |
| `User Controllable HTML Element Attribute (Potential XSS)` | 58 informational | Passive heuristic on query-driven admin views. Existing XSS coverage remains the controlling evidence unless a future active/manual test produces executable payload evidence. |
| `Private IP Disclosure` | 1 low | Admin-only audit tab displayed private IP evidence `192.168.1.234`. First production retest after restart still found 4 alerts because audit detection signal keys rendered the raw private IP. Follow-up remediation masks private/internal origin IPs in table text, dropdown values, and signal display keys. Final production retest passed with 0 private-IP regex hits and 0 `Private IP Disclosure` alerts. |

Evidence: `07-tool-outputs/zap/zap-auth-crawl-external-admin-2026-06-24-summary.md`.

## Crawl: Local Onboarding And Pending-MFA Sessions - 2026-06-23

| Field | Value |
| --- | --- |
| Tool | OWASP ZAP `2.17.0` daemon proxy plus safe GET-only Python client |
| Environment | Local dev app |
| Base URL | `http://127.0.0.1:8080` |
| Synthetic account | Synthetic local onboarding-only user and synthetic local pending-MFA user |
| Requests captured | 34 ZAP messages; 26 scripted role requests |
| Unique paths | 22 ZAP site-tree URLs |
| Exclusions | Active scan, state-changing onboarding/MFA completion POSTs, uploads, destructive routes, provider inspect/test calls, content generation, raw proxy history export, transcript/note content capture |
| Redactions applied | Cookies, CSRF, tokens, TOTP secrets/codes, content, secrets |
| Findings created | None. Existing accepted scanner warnings still apply: readable CSRF cookie, session auto-detection, authenticated-page CSRF heuristic. |
| Retest needed | Staging/production authenticated crawl only with explicit authorization. |

### Local Onboarding And Pending-MFA Results

| Scenario | Result |
| --- | --- |
| Onboarding page | `/onboarding` returned `200`. |
| Onboarding self/status APIs | `/api/v1/auth/me` and `/api/v1/auth/trusted-device` returned `200`. |
| Onboarding browser protected routes | `/home`, `/transcribe`, and `/admin` returned `303` to `/onboarding`. |
| Onboarding protected API routes | Transcript, workspace, user/team management, provider config, template, and quick-action routes returned `403`. |
| Pending-MFA page | `/mfa/challenge` returned `200`. |
| Pending-MFA self/status APIs | `/api/v1/auth/me` and `/api/v1/auth/trusted-device` returned `200`. |
| Pending-MFA browser protected routes | `/home`, `/transcribe`, and `/admin` returned `303` to `/mfa/challenge`. |
| Pending-MFA protected API routes | Transcript, workspace, user/team management, provider config, template, and quick-action routes returned `403`. |

Evidence: `07-tool-outputs/zap/zap-auth-crawl-local-onboarding-mfa-2026-06-23-summary.md`.

## Crawl: Local ZAP AJAX Spider JS Browser Crawl - 2026-06-23

| Field | Value |
| --- | --- |
| Tool | OWASP ZAP `2.17.0` AJAX Spider with bundled headless browser |
| Environment | Local dev app |
| Base URL | `http://127.0.0.1:8080` |
| Synthetic account | Seeded dev normal user via in-memory ZAP header rule |
| Requests captured | 31 ZAP messages; 23 AJAX spider browser states |
| Unique paths | 11 ZAP URLs, including `/home`, `csrf.js`, `smart-phrases.js`, and local `lucide` static asset paths |
| Exclusions | Active scan, broad authenticated workflow crawling, random inputs, default element clicking, uploads, destructive routes, provider inspect/test calls, content generation, raw proxy history export |
| Redactions applied | Cookies, CSRF, tokens, content, secrets |
| Findings created | None. Existing accepted scanner warnings still apply. One informational suspicious-comment heuristic matched a harmless CSS/comment fragment on `/home`. |
| Retest needed | Broader JS/browser workflow crawl only if explicitly needed; staging/production authenticated crawl only with explicit authorization. |

Evidence: `07-tool-outputs/zap/zap-js-browser-crawl-local-2026-06-23-summary.md`.

## Gap Notes

- Local proxy tool chosen for this slice: OWASP ZAP.
- Local anonymous, onboarding-only, pending-MFA, normal user, team leader, and system admin roles now have local crawl evidence.
- Need decide raw evidence vault location for proxy histories.
- Need collect screenshots or HAR summaries with sensitive values redacted.
