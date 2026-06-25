# ZAP External Admin Private IP Retest Pass - 2026-06-24

Target: `https://openscribe.co.uk`

Tool: OWASP ZAP `2.17.0` daemon proxy plus safe GET-only Python client.

Role: dedicated OWASP system-admin session; account identifier redacted.

Scope:

- `GET /api/v1/auth/me`
- `GET /admin?tab=audit`
- `GET /admin2?tab=audit`
- `GET /admin?tab=audit&audit_since=24h`
- `GET /admin2?tab=audit&audit_since=24h`
- Representative admin metadata tabs and transcript/workspace empty-content probes.

Exclusions: no POST, DELETE, uploads, provider inspect/test calls, active scan, content generation, or raw ZAP history export.

## Result

Passed. ZAP reported zero `Private IP Disclosure` alerts, and direct HTML checks found no private/internal IPv4 strings in the audited admin responses.

## Counts

| Metric | Count |
| --- | ---: |
| Scripted GET requests | 15 |
| `200` responses | 15 |
| ZAP URLs | 14 |
| HTML routes with private-IP regex hit | 0 |
| Routes showing masked private/internal label | 4 |
| ZAP `Private IP Disclosure` alerts | 0 |

Routes showing `Private/internal IP masked`:

- `/admin?tab=audit`
- `/admin?tab=audit&audit_since=24h`
- `/admin2?tab=audit`
- `/admin2?tab=audit&audit_since=24h`

## Alert Counts

| Alert | Count |
| --- | ---: |
| `Absence of Anti-CSRF Tokens` | 312 |
| `User Controllable HTML Element Attribute (Potential XSS)` | 87 |
| `Re-examine Cache-control Directives` | 9 |
| `Cookie No HttpOnly Flag` | 9 |
| `Session Management Response Identified` | 9 |
| `Timestamp Disclosure - Unix` | 3 |
| `Content Security Policy (CSP) Report-Only Header Found` | 2 |

Existing accepted scanner classes remain unchanged. `Private IP Disclosure` is absent after the follow-up signal-key masking deployment.

## Redaction

No cookies, CSRF tokens, passwords, session tokens, transcript/note content, prompts, provider responses, provider secrets, or audio were committed. Raw ZAP history was not exported.
