# ZAP Authenticated Local Crawl Summary - 2026-06-15

## Scope

- Tool: OWASP ZAP `2.17.0` daemon proxy.
- Environment: local dev app, `http://127.0.0.1:8080`.
- Proxy mode: ZAP ran with Docker host networking so localhost-only seeded dev accounts remained local-only from the app viewpoint.
- Accounts: seeded dev normal user and seeded dev team leader. Account identifiers are documented in `docs/setup.md`; no cookies, passwords, session tokens, CSRF tokens, transcript text, note text, prompt text, provider responses, or audio are stored here.
- Method: API login through ZAP, then safe GET-only role crawl with in-memory cookie replay. No destructive POST routes, upload routes, provider inspect/test calls, or content-generating actions were run.

## Execution Notes

- Initial scripted web-form login attempts failed because the app correctly rejects unsafe requests without same-origin CSRF proof.
- Local app was configured to issue `Secure` cookies over HTTP, so the script extracted `Set-Cookie` values in memory and replayed them only as request headers through ZAP. Values were not written to disk.
- Login throttle key `LIMITS:LIMITER/127.0.0.1/login/5/5/minute` was cleared in local Redis after failed script setup attempts. No application DB rows or transcript-derived content were cleared.

## Crawl Totals

| Metric | Value |
| --- | --- |
| Role requests | 75 |
| ZAP messages | 95 |
| Unique paths | 46 |
| Status counts | `200`: 42, `303`: 5, `401`: 3, `403`: 22, `422`: 3 |
| ZAP alert risks | `Medium`: 42, `Low`: 36, `Informational`: 45 |

## Role Results

| Scenario | Result |
| --- | --- |
| Anonymous `/home`, `/transcribe`, `/admin` | `303` to `/login` |
| Anonymous `/api/v1/auth/me`, `/api/v1/transcripts`, `/api/v1/users` | `401` |
| Normal user login | `200`, redirect target `/home` |
| Normal user `/home`, `/transcribe`, `/transcribe-glm-2` | `200` |
| Normal user `/api/v1/auth/me`, preferences, personal templates/actions, smart phrases, transcripts, workspace | `200` |
| Normal user `/api/v1/account-requests`, teams, users, team templates/actions, provider selections | `403` |
| Normal user `/admin`, `/api/v1/stt-configs`, `/api/v1/llm-configs`, `/api/v1/deidentification-providers` | `403` |
| Team leader login | `200`, redirect target `/home` |
| Team leader `/home`, `/transcribe`, `/transcribe-glm-2` | `200` |
| Team leader account requests, users, team templates/actions, provider selections/options | `200` |
| Team leader `/api/v1/teams`, `/admin`, system provider config lists | `403` |
| Template editor routes requiring query/context | `422`; no content exposure observed |

## Unique Paths Observed

- `/`
- `/admin`
- `/admin/templates/editor`
- `/api/v1/account-requests`
- `/api/v1/app-preferences`
- `/api/v1/auth/login`
- `/api/v1/auth/me`
- `/api/v1/auth/trusted-device`
- `/api/v1/clinical-nlp-selection`
- `/api/v1/clinical-nlp-selection/options`
- `/api/v1/deidentification-providers`
- `/api/v1/deidentification-selection`
- `/api/v1/deidentification-selection/options`
- `/api/v1/llm-configs`
- `/api/v1/llm-preference`
- `/api/v1/llm-selection`
- `/api/v1/llm-selection/options`
- `/api/v1/quick-actions/available`
- `/api/v1/quick-actions/personal`
- `/api/v1/quick-actions/team`
- `/api/v1/smart-phrases/available`
- `/api/v1/smart-phrases/personal`
- `/api/v1/stt-configs`
- `/api/v1/stt-selection`
- `/api/v1/stt-selection/options`
- `/api/v1/teams`
- `/api/v1/templates/available`
- `/api/v1/templates/personal`
- `/api/v1/templates/team`
- `/api/v1/transcribe/workspace`
- `/api/v1/transcripts`
- `/api/v1/users`
- `/home`
- `/home/templates/editor`
- `/transcribe`
- `/transcribe-glm-2`

ZAP also recorded parent path nodes such as `/api`, `/api/v1`, `/api/v1/templates`, and `/api/v1/quick-actions` as site-tree containers.

## Alert Triage

| Alert | Risk | Triage |
| --- | --- | --- |
| `Cookie No HttpOnly Flag` | Low | Existing accepted design for `openscribe_csrf`, a signed non-authoritative CSRF token readable by same-origin JS. Auth-bearing `openscribe_session` remains `HttpOnly`; values were not committed. See `OWASP-2026-06-14-014`. |
| `Absence of Anti-CSRF Tokens` on authenticated app pages | Medium, low confidence | Scanner heuristic on JS/header-driven authenticated pages. Unsafe routes require same-origin `Origin`/`Referer` plus signed CSRF token; local tests cover missing/mismatched/cross-origin rejection. Public server-rendered forms were already ZAP-retested with visible `_csrf_token`. |
| `Authentication Request Identified` / `Session Management Response Identified` | Informational | Expected identification of login/session behavior. See `OWASP-2026-06-14-016`. |
| `Information Disclosure - Suspicious Comments` on `/home` | Informational | Needs manual source review before creating a finding; no sensitive value was committed in this summary. |

## Residual Gaps

- This crawl covered anonymous, normal seeded dev user, and seeded dev team leader only.
- Onboarding-only, pending-MFA, and system-admin sessions were not crawled.
- This was local dev evidence, not production/staging live evidence.
- No active scan, fuzzing, content mutation, upload, or destructive route testing was performed.
