# ZAP External Authenticated Admin Crawl Summary - 2026-06-24

## Scope

- Target: `https://openscribe.co.uk`.
- Tool: OWASP ZAP `2.17.0` daemon proxy.
- Account: externally configured OWASP system-admin session cookie. No cookies, passwords, session tokens, CSRF tokens, transcript text, note text, prompt text, provider responses, provider secrets, or audio are stored here.
- Method: safe GET-only admin role crawl through ZAP using in-memory session cookie. No POST, DELETE, upload, provider inspect/test, content generation, active scan, or raw ZAP history export.

## Summary

- Role requests: `31`
- ZAP unique URLs: `38`
- Status counts: `{'200': 19, '303': 3, '403': 6, '404': 3}`
- Alert risks: `{'Informational': 105, 'Low': 28, 'Medium': 209}`

## Route Results

| Route | Status | Redirect |
| --- | ---: | --- |
| `/admin` | `200` | `` |
| `/admin2` | `200` | `` |
| `/admin?tab=directory` | `200` | `` |
| `/admin?tab=providers` | `200` | `` |
| `/admin?tab=defaults` | `200` | `` |
| `/admin?tab=usage` | `200` | `` |
| `/admin?tab=audit` | `200` | `` |
| `/admin/templates/editor?scope=default` | `200` | `` |
| `/api/v1/auth/me` | `200` | `` |
| `/api/v1/auth/trusted-device` | `200` | `` |
| `/api/v1/teams` | `200` | `` |
| `/api/v1/users` | `200` | `` |
| `/api/v1/account-requests` | `200` | `` |
| `/api/v1/transcripts` | `200` | `` |
| `/api/v1/transcribe/workspace` | `200` | `` |
| `/api/v1/templates/available` | `403` | `` |
| `/api/v1/templates/personal` | `403` | `` |
| `/api/v1/templates/team` | `403` | `` |
| `/api/v1/quick-actions/personal` | `403` | `` |
| `/api/v1/quick-actions/team` | `403` | `` |
| `/api/v1/smart-phrases/personal` | `403` | `` |
| `/home` | `303` | `/admin` |
| `/transcribe` | `303` | `/admin` |
| `/transcribe-glm-2` | `303` | `/admin` |
| `/api/v1/stt-configs?team_id=<team_id>` | `200` | `` |
| `/api/v1/stt-selection?team_id=<team_id>` | `200` | `` |
| `/api/v1/llm-configs?team_id=<team_id>` | `200` | `` |
| `/api/v1/llm-selection?team_id=<team_id>` | `200` | `` |
| `/api/v1/deidentification/providers?team_id=<team_id>` | `404` | `` |
| `/api/v1/deidentification/selection?team_id=<team_id>` | `404` | `` |
| `/api/v1/clinical-nlp/selection?team_id=<team_id>` | `404` | `` |

## Alerts

| Alert | Count | Risk |
| --- | ---: | --- |
| `Absence of Anti-CSRF Tokens` | `209` | `Medium` |
| `User Controllable HTML Element Attribute (Potential XSS)` | `58` | `Informational` |
| `Session Management Response Identified` | `32` | `Informational` |
| `Cookie No HttpOnly Flag` | `27` | `Low` |
| `Re-examine Cache-control Directives` | `15` | `Informational` |
| `Private IP Disclosure` | `1` | `Low` |

## Notes

- Authenticated admin session validated separately via `/api/v1/auth/me`: `is_system_admin=true`, `auth_level=full`.
- User-content/browser routes redirected to `/admin` or denied, matching system-admin admin-only posture.
- Follow-up direct checks confirmed `/api/v1/transcripts` returned `0` items and `/api/v1/transcribe/workspace` returned `active_transcript = null`.
- Treat ZAP anti-CSRF/cookie/session alerts as scanner triage input; existing public-form CSRF and cookie-contract tests remain source of truth unless executable/manual evidence appears.
- `Private IP Disclosure` appeared once on `/admin?tab=audit` with evidence `192.168.1.234`. This is system-admin-only audit metadata, not public output or transcript-derived content; keep under review if audit UI audience broadens.
