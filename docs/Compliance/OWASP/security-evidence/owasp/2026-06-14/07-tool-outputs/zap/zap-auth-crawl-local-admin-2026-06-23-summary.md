# ZAP Authenticated Local Admin Crawl Summary - 2026-06-23

## Scope

- Tool: OWASP ZAP `2.17.0` daemon proxy.
- Environment: local dev app, `http://127.0.0.1:8080`.
- Proxy mode: ZAP ran with Docker host networking so the localhost-only seeded dev admin remained local-only from the app viewpoint.
- Account: seeded dev system admin `dev.admin@example.com`. No cookies, passwords, session tokens, CSRF tokens, transcript text, note text, prompt text, provider responses, provider secrets, or audio are stored here.
- Method: API login through ZAP, then safe GET-only admin role crawl with in-memory cookie replay. No destructive POST/DELETE routes, upload routes, provider inspect/test calls, content-generating actions, or raw ZAP history exports were run.

## Execution Notes

- Local app was available on `127.0.0.1:8080`; `alembic upgrade head` and `scripts/seed_dev_accounts.py` were run before the crawl.
- ZAP API ran on `127.0.0.1:8095`.
- Local app issued `Secure` cookies over HTTP, so the script extracted `Set-Cookie` values in memory and replayed them only as request headers through ZAP. Values were not written to disk.
- Team-scoped admin metadata routes used the first seeded dev team ID in memory; the UUID is not committed in this summary.

## Crawl Totals

| Metric | Value |
| --- | --- |
| Role requests | 56 |
| ZAP messages | 72 |
| ZAP unique URLs | 55 |
| Unique site-tree paths | 46 |
| Status counts | `200`: 41, `303`: 3, `403`: 10, `422`: 2 |
| ZAP alert risks | `Medium`: 241, `Low`: 42, `Informational`: 83 |

## Role Results

| Scenario | Result |
| --- | --- |
| System-admin login | `200`, redirect target `/admin`. |
| Admin browser shell | `/admin`, `/admin2`, tabbed admin views, and default template editor returned `200`. |
| Admin account/team metadata | `/api/v1/teams`, `/api/v1/users`, and `/api/v1/account-requests` returned `200`. |
| Admin provider metadata | Team-scoped STT, LLM, hallucination-check, de-identification, and clinical NLP selection/config metadata routes returned `200`. |
| Admin provider routes without required team context | `/api/v1/stt-configs` and `/api/v1/llm-configs` without `team_id` returned `422`; team-scoped equivalents returned `200`. |
| Admin personal/team user-asset routes | Personal/team template, quick-action, smart-phrase, app-preference, and LLM-preference user surfaces returned `403` where system admins should not own personal/team user libraries. |
| Admin browser content surfaces | `/home`, `/transcribe`, and `/transcribe-glm-2` returned `303` to `/admin`. |
| Admin transcript list/workspace probes | `/api/v1/transcripts` returned `200` with `0` items; `/api/v1/transcribe/workspace` returned `200` with `active_transcript = null`. No transcript/note content was printed or committed. |

## Unique Paths Observed

- `/`
- `/admin`
- `/admin/templates`
- `/admin/templates/editor`
- `/admin2`
- `/api`
- `/api/v1`
- `/api/v1/account-requests`
- `/api/v1/app-preferences`
- `/api/v1/auth`
- `/api/v1/auth/login`
- `/api/v1/auth/me`
- `/api/v1/auth/trusted-device`
- `/api/v1/clinical-nlp-selection`
- `/api/v1/clinical-nlp-selection/options`
- `/api/v1/deidentification-provider-assignments`
- `/api/v1/deidentification-providers`
- `/api/v1/deidentification-selection`
- `/api/v1/deidentification-selection/options`
- `/api/v1/hallucination-check-selection`
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
- `/transcribe`
- `/transcribe-glm-2`

ZAP also recorded parent path nodes such as `/api/v1/templates`, `/api/v1/quick-actions`, and `/api/v1/smart-phrases` as site-tree containers.

## Alert Triage

| Alert | Risk/count | Paths | Triage |
| --- | --- | --- | --- |
| `Absence of Anti-CSRF Tokens` | Medium / 241 | `/admin`, `/admin/templates/editor` | Scanner heuristic on authenticated HTML pages. Unsafe routes require same-origin `Origin`/`Referer` plus signed CSRF token; public server-rendered forms already have visible `_csrf_token` and passed production ZAP retest. |
| `Cookie No HttpOnly Flag` | Low / 42 | Authenticated admin/API paths | Existing accepted design for `openscribe_csrf`, a signed non-authoritative CSRF token readable by same-origin JS. Auth-bearing `openscribe_session` remains `HttpOnly`; values were not committed. See `OWASP-2026-06-14-014`. |
| `Authentication Request Identified` | Informational / 1 | `/api/v1/auth/login` | Expected identification of login behavior. See `OWASP-2026-06-14-016`. |
| `Session Management Response Identified` | Informational / 42 | Authenticated admin/API paths | Expected scanner session/cookie auto-detection. See `OWASP-2026-06-14-016`. |
| `User Controllable HTML Element Attribute (Potential XSS)` | Informational / 40 | `/admin`, `/admin2` | ZAP heuristic on admin query-driven views. Existing XSS coverage verifies Jinja autoescaping, `tojson|forceescape`, CSP `script-src-attr 'none'`, and escaped `innerHTML`; no new finding created from this passive heuristic alone. Re-review if a future active/manual test produces executable payload evidence. |

## Residual Gaps

- This crawl covered the seeded local system-admin role only.
- Onboarding-only and pending-MFA sessions were covered later on 2026-06-23 in `zap-auth-crawl-local-onboarding-mfa-2026-06-23-summary.md`.
- This was local dev evidence, not staging/production authenticated evidence.
- No browser JavaScript crawl, active scan, fuzzing, content mutation, upload, destructive route testing, or provider inspect/test calls were performed.
