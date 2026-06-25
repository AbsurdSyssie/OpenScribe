# 14 - Audit Detection And Response Playbook

Status: local/manual detection playbook.  
Finding: `OWASP-2026-06-14-005` follow-up.  
Scope: using `security_audit_events` to spot irregular access and guide response.

## What Audit Logging Enables Now

OpenScribe can now support manual security review from durable DB audit events:

- reconstruct who acted, when, from where, and against which user/team/object
- investigate login/MFA/reset-token failures
- spot repeated denied access or CSRF failures
- review rate-limit events
- review admin lifecycle actions
- review provider configuration changes
- review destructive actions such as transcript/user/team deletion
- review upload/generation queue activity by user/team

Current limitation:

- no automatic alerting
- no SIEM/export pipeline
- no account lockout workflow driven by audit events
- no audit API

## Report Tool

Use:

```bash
python3 scripts/security/audit_events_report.py --since 24h
python3 scripts/security/audit_events_report.py --since 7d --json
```

System-admin browser view:

- open `/admin2?tab=audit`
- use `audit_since`, `audit_action`, and `audit_request_ip` filters for manual review
- confirm only allowlisted metadata appears; raw `details_json` is not dumped

Output includes:

- event count
- action counts
- category counts
- outcome counts
- detection signals

The report is metadata-only. It does not print raw cookies, tokens, passwords, prompts, transcript text, note text, provider responses, audio, or filenames.

## Detection Signals

| Signal | Meaning | Initial severity | First response |
| --- | --- | --- | --- |
| `auth_failure_burst_by_subject` | Repeated auth failures for same normalized subject hash | Medium | Check whether account owner expected failures; review IP/user-agent; consider password reset or temporary lockout if repeated. |
| `security_event_burst_by_ip` | Repeated auth/CSRF/rate-limit/validation events from one origin IP | Medium/High | Check IP reputation/context; consider edge block/rate-limit adjustment; inspect target routes. |
| `security_event_burst` | Repeated abuse/security validation events even without reliable IP | Medium/High | Review event class; inspect routes and source metadata; check for probing or automation. |
| `access_denied_burst_by_actor_route` | Same actor/IP repeatedly denied on same route | Medium | Check whether user role is wrong, session state is stale, or actor is probing. |
| `high_risk_admin_or_destructive_action` | User/team lifecycle, break-glass, or delete action | Medium/High | Confirm authorised change ticket/window; check actor role and object IDs. |
| `provider_configuration_change` | STT/LLM/de-ID/clinical-NLP config or selection changed | Medium | Confirm change owner, team, and provider metadata; verify no unexpected provider route. |

## Manual SQL Triage

Use a safe DB client. Do not export raw table dumps into git.

Recent events:

```sql
SELECT action, actor_user_id, target_user_id, team_id, request_ip, details_json, created_at
FROM security_audit_events
WHERE created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC
LIMIT 200;
```

Repeated auth failures by subject hash:

```sql
SELECT details_json->>'subject_hash' AS subject_hash, COUNT(*) AS failures, MIN(created_at) AS first_seen, MAX(created_at) AS last_seen
FROM security_audit_events
WHERE action = 'login_failure'
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY details_json->>'subject_hash'
HAVING COUNT(*) >= 5
ORDER BY failures DESC;
```

Repeated denied access:

```sql
SELECT actor_user_id, request_ip, details_json->>'route' AS route, details_json->>'reason_code' AS reason_code, COUNT(*) AS denied_count
FROM security_audit_events
WHERE action = 'access_denied'
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY actor_user_id, request_ip, details_json->>'route', details_json->>'reason_code'
HAVING COUNT(*) >= 5
ORDER BY denied_count DESC;
```

Abuse signals:

```sql
SELECT action, request_ip, COUNT(*) AS event_count, MIN(created_at) AS first_seen, MAX(created_at) AS last_seen
FROM security_audit_events
WHERE action IN ('csrf_rejected', 'rate_limit_exceeded', 'security_validation_rejected')
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY action, request_ip
ORDER BY event_count DESC;
```

High-risk admin/destructive actions:

```sql
SELECT action, actor_user_id, target_user_id, team_id, details_json, created_at
FROM security_audit_events
WHERE action IN (
  'user_created',
  'account_suspended',
  'account_reactivated',
  'account_deleted',
  'team_created',
  'team_deleted',
  'team_delete_blocked',
  'break_glass_password_reset_generated',
  'break_glass_account_recovery_generated',
  'transcript_root_deleted',
  'generated_document_deleted'
)
  AND created_at >= NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

Provider configuration changes:

```sql
SELECT action, actor_user_id, team_id, details_json, created_at
FROM security_audit_events
WHERE details_json->>'category' = 'provider'
  AND created_at >= NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

## Response Protocol

For each signal:

1. Record signal in run/evidence summary.
2. Confirm environment and authorised test windows.
3. Identify actor, target, team, route, object ID, IP, user agent, action, outcome, reason code.
4. Check if matching change ticket, deployment, test run, or support action exists.
5. If benign, record as `Accepted` with reason.
6. If suspicious, preserve redacted evidence and escalate.

Possible responses:

- force password reset
- revoke sessions/trusted devices
- suspend or lock account through existing admin flow
- rotate provider credentials
- disable suspicious provider config
- tighten Cloudflare/rate-limit rule
- block source IP at edge
- open incident record
- run targeted role/access crawl

Do not:

- inspect transcript/note content unless a separate authorised incident process permits it
- export raw cookies/tokens/secrets
- delete provider secrets before DB transaction ordering rules are satisfied
- change ownership/deletion/privacy model during incident response

## Evidence Record Template

Add this section to an audit test run summary when detection review is performed:

```markdown
## Detection Review

- Report command:
- Window:
- Total audit events:
- Signals found:
- Signals accepted as expected:
- Signals requiring follow-up:

| Signal | Severity | Count | Actor/IP/key | Action/route | Verdict | Follow-up |
| --- | --- | --- | --- | --- | --- | --- |

## Response Actions

| Action | Actor | Target | Reason | Evidence | Status |
| --- | --- | --- | --- | --- | --- |

## Residual Risk

- 
```

## Test Coverage

Automated tests:

```bash
.venv/bin/pytest -q tests/test_audit_detection.py
```

This verifies:

- auth failure bursts produce signals
- repeated access denials produce signals
- rate-limit and validation bursts produce signals
- high-risk admin/team-delete blocker actions produce signals
- provider config changes produce signals
- relative and ISO time windows parse correctly

## Next Hardening

Future slices:

- scheduled detection job
- dedicated admin/security-operator audit API or SIEM export
- SIEM/OTLP export
- alert thresholds per environment
- account lockout/cooldown workflow tied to auth failures
- edge-rate-limit automation for confirmed source abuse
