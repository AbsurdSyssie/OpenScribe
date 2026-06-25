# ZAP Authenticated Local Onboarding And Pending-MFA Crawl Summary - 2026-06-23

## Scope

- Tool: OWASP ZAP `2.17.0` daemon proxy.
- Environment: local dev app, `http://127.0.0.1:8080`.
- Accounts: synthetic local onboarding-only user and synthetic local pending-MFA user. No cookies, passwords, session tokens, CSRF tokens, TOTP secrets/codes, transcript text, note text, prompt text, provider responses, provider secrets, or audio are stored here.
- Method: direct synthetic DB setup, then safe GET-only role crawl with in-memory cookie replay through ZAP. No onboarding completion POSTs, TOTP verification POSTs, destructive routes, upload routes, provider inspect/test calls, content-generating actions, or raw ZAP history exports were run.

## Crawl Totals

| Metric | Value |
| --- | --- |
| Role requests | 26 |
| ZAP messages | 34 |
| ZAP unique URLs | 22 |
| Status counts | `200`: 6, `303`: 6, `403`: 14 |
| ZAP alert risks | `Medium`: 4, `Low`: 14, `Informational`: 16 |

## Role Results

| Scenario | Result |
| --- | --- |
| Onboarding page | `/onboarding` returned `200`. |
| Onboarding session self/status APIs | `/api/v1/auth/me` and `/api/v1/auth/trusted-device` returned `200`. |
| Onboarding browser protected routes | `/home`, `/transcribe`, and `/admin` returned `303` to `/onboarding`. |
| Onboarding protected API routes | Transcript, workspace, user/team management, provider config, template, and quick-action routes returned `403`. |
| Pending-MFA page | `/mfa/challenge` returned `200`. |
| Pending-MFA session self/status APIs | `/api/v1/auth/me` and `/api/v1/auth/trusted-device` returned `200`. |
| Pending-MFA browser protected routes | `/home`, `/transcribe`, and `/admin` returned `303` to `/mfa/challenge`. |
| Pending-MFA protected API routes | Transcript, workspace, user/team management, provider config, template, and quick-action routes returned `403`. |

## Unique Paths Observed

- `/`
- `/admin`
- `/api`
- `/api/v1`
- `/api/v1/auth`
- `/api/v1/auth/me`
- `/api/v1/auth/trusted-device`
- `/api/v1/quick-actions/available`
- `/api/v1/stt-configs`
- `/api/v1/teams`
- `/api/v1/templates/available`
- `/api/v1/transcribe/workspace`
- `/api/v1/transcripts`
- `/api/v1/users`
- `/home`
- `/mfa`
- `/mfa/challenge`
- `/onboarding`
- `/transcribe`

ZAP also recorded parent path nodes such as `/api/v1/templates`, `/api/v1/quick-actions`, and `/api/v1/transcribe`.

## Alert Triage

| Alert | Risk/count | Paths | Triage |
| --- | --- | --- | --- |
| `Absence of Anti-CSRF Tokens` | Medium / 4 | `/onboarding`, `/mfa/challenge` | Scanner heuristic on authenticated HTML pages. Unsafe routes require same-origin `Origin`/`Referer` plus signed CSRF token; public server-rendered forms already have visible `_csrf_token` and passed production ZAP retest. |
| `Cookie No HttpOnly Flag` | Low / 14 | Auth/session and denied protected paths | Existing accepted design for `openscribe_csrf`, a signed non-authoritative CSRF token readable by same-origin JS. Auth-bearing `openscribe_session` remains `HttpOnly`; values were not committed. See `OWASP-2026-06-14-014`. |
| `Session Management Response Identified` | Informational / 14 | Auth/session and denied protected paths | Expected scanner session/cookie auto-detection. See `OWASP-2026-06-14-016`. |
| `Modern Web Application` | Informational / 2 | `/onboarding`, `/mfa/challenge` | Expected JavaScript-enabled page detection. |

## Residual Gaps

- This crawl covered local onboarding-only and pending-MFA roles.
- This was local dev evidence, not staging/production authenticated evidence.
- No browser JavaScript crawl, active scan, fuzzing, state-changing onboarding/MFA completion, upload, destructive route testing, or provider inspect/test calls were performed in this slice.
