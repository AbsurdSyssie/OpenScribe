# ZAP External Admin Private IP Retest - 2026-06-24

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

Failed. The first production restart included table/dropdown masking, but audit detection signal text still rendered a raw private origin IP as the signal key.

## Counts

| Metric | Count |
| --- | ---: |
| Scripted GET requests | 15 |
| `200` responses | 15 |
| ZAP URLs | 14 |
| HTML routes with private-IP regex hit | 4 |
| ZAP `Private IP Disclosure` alerts | 4 |

Affected ZAP URLs:

- `https://openscribe.co.uk/admin?tab=audit`
- `https://openscribe.co.uk/admin?tab=audit&audit_since=24h`
- `https://openscribe.co.uk/admin2?tab=audit`
- `https://openscribe.co.uk/admin2?tab=audit&audit_since=24h`

## Alert Counts

| Alert | Count |
| --- | ---: |
| `Absence of Anti-CSRF Tokens` | 312 |
| `User Controllable HTML Element Attribute (Potential XSS)` | 87 |
| `Re-examine Cache-control Directives` | 9 |
| `Cookie No HttpOnly Flag` | 9 |
| `Session Management Response Identified` | 9 |
| `Private IP Disclosure` | 4 |

Existing accepted scanner classes remain unchanged; only `Private IP Disclosure` is part of this retest.

## Follow-Up Fix

Local code now adds `display_key` to audit signals, masks private/loopback/link-local IP signal keys, and keeps subject-hash placeholder redaction intact. Focused local test passed after the follow-up fix:

```bash
.venv/bin/pytest -q tests/test_admin_ui.py -k "audit"
```

Next action: deploy/restart again, then rerun this same safe GET-only ZAP retest.
