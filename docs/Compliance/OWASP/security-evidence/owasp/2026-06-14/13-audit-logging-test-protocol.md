# 13 - Audit Logging Test Protocol

Status: reusable test protocol for `OWASP-2026-06-14-005` retests.  
Owner: security/engineering agent running the OWASP evidence cycle.  
Scope: local/staging/production audit evidence for OpenScribe application audit logging.

## Purpose

Use this protocol every time audit logging is retested. It creates a repeatable record of:

- when the test happened
- who/what environment ran it
- what actions were tested
- what passed
- what failed
- what evidence was stored
- what was deliberately not tested

Do not overwrite previous run records. Add a new dated record for each run.

## Evidence Locations

For each run, create one summary file:

```text
docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/audit/audit-logging-test-YYYY-MM-DD[-env][-sequence].md
```

Examples:

```text
07-tool-outputs/audit/audit-logging-test-2026-06-15-local.md
07-tool-outputs/audit/audit-logging-test-2026-06-20-staging-01.md
07-tool-outputs/audit/audit-logging-test-2026-06-20-production-readonly.md
```

Then update:

- `10-retest-log.md` with one row for the run.
- `09-findings-and-remediation.md` only if finding status changes.
- `owasp-top-10-matrix.md` only if A09 status changes.
- `OWASP_Context.md` if the run changes carry-forward status.
- `docs/progress/` daily note for local working notes.

## Evidence Safety Rules

Allowed in committed evidence:

- audit event action names
- row counts
- synthetic user IDs
- team IDs
- object IDs
- route/method/status
- event outcomes
- reason codes
- timestamps
- pass/fail summaries
- redacted SQL/query snippets

Forbidden in committed evidence:

- cookies
- CSRF tokens
- session/trusted-device tokens
- reset/setup/invite tokens
- passwords or temporary passwords
- MFA/TOTP secrets or codes
- recovery codes
- provider bearer tokens/API keys
- raw Vault secret values
- transcript text
- note text
- prompts
- provider request/response bodies
- generated document text
- audio content
- filenames if they may contain patient data
- submitted secret-bearing header/body values

If raw evidence is needed, store it outside git in a controlled evidence vault and reference only the vault location/name in the summary.

## Pre-Run Checklist

Record these in the run summary before testing:

- Date and timezone.
- Environment: local, staging, production.
- Git commit/branch if local or staging.
- App URL.
- Database target type, not credentials.
- Synthetic account list by role.
- Explicit authorisation/scope.
- Whether actions are read-only or state-changing.
- Whether raw audit rows will be queried.
- Confirmation that no secrets/content will be committed.

Production/staging rules:

- Use only authorised synthetic accounts.
- Do not use localhost dev seed accounts.
- Do not run destructive actions unless explicitly authorised.
- Do not create patient-like transcript/note/prompt content.
- Prefer staging for write-heavy audit validation.

## Required Test Set

### 1. Static/Unit Audit Tests

Run:

```bash
python3 -m py_compile app/errors.py app/services/security_audit.py app/services/admin.py tests/test_security_audit.py
.venv/bin/pytest -q tests/test_security_audit.py
```

Pass criteria:

- Tests pass.
- Audit redaction/sanitization works.
- Cloudflare/IP trust flags remain env-gated.
- Login success/failure events contain no raw password/email.
- Invalid reset token audit contains no raw token or password.
- CSRF/authz/rate-limit events contain no cookies/tokens/bodies.
- Account lifecycle and team-delete blocker events are persisted.
- Provider/de-ID validation rejection events contain no submitted secret values.
- Template/default-asset/preference/smart-phrase/generation/upload/delete events contain no prompt/transcript/audio/filename/smart-phrase content.

### 2. Focused Regression Smoke

Run sequentially:

```bash
.venv/bin/pytest -q tests/test_security_audit.py tests/test_cookie_csrf_security.py tests/test_api.py -k "csrf or auth or mfa or login or rate_limited or rate_limit or preference or smart_phrase or template or quick_action"
```

Pass criteria:

- Tests pass.
- No shared DB guard failure.
- Existing auth/CSRF/rate-limit/template behavior remains stable.

### 3. DB Audit Row Spot Check

Use local/staging DB query tooling. Do not print secret values.

Minimum query shape:

```sql
SELECT action, actor_user_id, target_user_id, team_id, request_ip, user_agent, details_json, created_at
FROM security_audit_events
WHERE created_at >= :run_started_at
ORDER BY created_at ASC;
```

Check:

- expected actions exist
- actor/target/team IDs are populated where expected
- request route/method/status/reason exist where expected
- details are metadata-only
- no forbidden strings appear

For committed evidence, record counts and redacted examples only.

### 4. Manual Synthetic Action Matrix

Run only actions allowed by the environment scope.

| Area | Action | Expected audit action(s) | Required checks |
| --- | --- | --- | --- |
| Auth | failed login | `login_failure` | subject hash only; no email/password |
| Auth | successful login | `login_success` | actor/target IDs; auth level |
| Auth | logout | `logout` | no raw session token |
| Auth token | invalid reset confirm | `auth_email_token_failure` | no raw token/password |
| MFA | failed challenge, if available | `mfa_challenge_failure` | no TOTP code |
| Abuse | missing/bad CSRF unsafe request | `csrf_rejected` | no CSRF/cookie values |
| Abuse | unauthorised protected route | `access_denied` | actor if known; route/method |
| Abuse | safe rate-limit probe | `rate_limit_exceeded` | no submitted password/body |
| Account | user create/suspend/reactivate/delete | `user_created`, `account_suspended`, `account_reactivated`, `account_deleted` | metadata only |
| Team | blocked hard delete | `team_delete_blocked` | blocker reason and IDs only |
| Provider | inspect/config/selection | `*_inspected`, `*_config_*`, `*_selection_*` | no bearer token/provider response |
| Validation | remote HTTP provider URL or secret-bearing de-ID header/body | `security_validation_rejected` | no submitted secret values |
| Assets | template/quick-action/default asset create/update/delete | `template_*`, `quick_action_*`, `default_*` | no prompt/name/description text unless explicitly safe |
| Preferences | user app preference set/clear | `user_app_preferences_*` | keys only |
| Smart phrase | create/update/delete | `smart_phrase_*` | no trigger/description/expansion text |
| Generation | queue note/follow-up/quick action | `generation_queued` | IDs only, no prompt/transcript/output |
| Upload | queue audio file/chunk | `audio_ingestion_queued` | size/duration only, no filename/audio |
| Deletion | transcript/generated document delete | `transcript_root_deleted`, `generated_document_deleted` | IDs/counts only |

## Forbidden String Scan

For local/staging only, create a temporary redacted/exported audit JSON outside git or in `/tmp`.

Search for known synthetic forbidden strings used in the run:

```bash
rg -n "PASSWORD_VALUE|RAW_TOKEN_VALUE|COOKIE_VALUE|PROMPT_SNIPPET|TRANSCRIPT_SNIPPET|AUDIO_FILENAME|PROVIDER_SECRET|DEID_SECRET_VALUE" /tmp/openscribe-audit-export.json
```

Pass criteria:

- No matches.
- If match appears, mark run failed and open remediation.
- Do not commit the raw export.

## Failure Classification

Use these labels in the run summary:

- `Pass`: expected audit row exists and contains only allowed metadata.
- `Fail`: expected audit row missing, wrong actor/team/object, wrong outcome, or sensitive value present.
- `Not run`: action not authorised or not applicable.
- `Accepted`: gap is known and documented as out of MVP/current scope.
- `Blocked`: test could not run because environment/account/tooling unavailable.

## Run Summary Template

Create a new file from this template for each run.

```markdown
# Audit Logging Test Run - YYYY-MM-DD - ENV

## Scope

- Finding: OWASP-2026-06-14-005
- Environment:
- App URL:
- Git commit/branch:
- Tester/agent:
- Start time:
- End time:
- Authorisation:
- Accounts/roles:
- State-changing actions allowed: yes/no

## Safety Confirmation

- [ ] Synthetic accounts only.
- [ ] No real patient/transcript/note/prompt/provider/audio content used.
- [ ] No cookies/tokens/passwords/secrets committed.
- [ ] Raw DB/proxy/tool output stored only outside git or redacted before commit.

## Commands

```bash
# commands run
```

## Automated Results

| Check | Command | Result | Notes |
| --- | --- | --- | --- |
| Py compile |  | Pass/Fail |  |
| Audit tests |  | Pass/Fail |  |
| Focused smoke |  | Pass/Fail |  |

## Manual Action Results

| Area | Action | Expected audit action | Result | Evidence summary | Notes |
| --- | --- | --- | --- | --- | --- |
| Auth | failed login | login_failure | Pass/Fail/Not run |  |  |
| Auth | successful login | login_success | Pass/Fail/Not run |  |  |
| Auth | logout | logout | Pass/Fail/Not run |  |  |
| Auth token | invalid reset confirm | auth_email_token_failure | Pass/Fail/Not run |  |  |
| Abuse | CSRF rejection | csrf_rejected | Pass/Fail/Not run |  |  |
| Abuse | access denied | access_denied | Pass/Fail/Not run |  |  |
| Abuse | rate limit | rate_limit_exceeded | Pass/Fail/Not run |  |  |
| Account | lifecycle | user/account events | Pass/Fail/Not run |  |  |
| Team | delete blocker | team_delete_blocked | Pass/Fail/Not run |  |  |
| Provider | inspect/config/selection | provider events | Pass/Fail/Not run |  |  |
| Validation | high-signal rejection | security_validation_rejected | Pass/Fail/Not run |  |  |
| Assets | template/action/default assets | asset events | Pass/Fail/Not run |  |  |
| Preferences | app preferences | user_app_preferences_* | Pass/Fail/Not run |  |  |
| Smart phrase | create/update/delete | smart_phrase_* | Pass/Fail/Not run |  |  |
| Generation | queue generation | generation_queued | Pass/Fail/Not run |  |  |
| Upload | queue ingestion | audio_ingestion_queued | Pass/Fail/Not run |  |  |
| Deletion | transcript/document delete | delete events | Pass/Fail/Not run |  |  |

## DB Spot Check

- Query window:
- Total audit rows observed:
- Expected actions present:
- Missing actions:
- Redacted example rows:

## Forbidden String Scan

- Export location:
- Scan command:
- Result:
- Matches:

## Failures

| ID | Severity | Description | Evidence | Required fix | Owner |
| --- | --- | --- | --- | --- | --- |

## Accepted / Not Run

| Item | Reason | Follow-up |
| --- | --- | --- |

## Verdict

- Overall result: Pass/Fail/Partial/Blocked
- Finding status impact:
- Retest-log update needed: yes/no
- Findings/matrix/context update needed: yes/no

## Redaction Statement

This summary contains no cookies, tokens, passwords, reset/setup tokens, MFA secrets/codes, recovery codes, provider secrets, Vault secret values, transcript/note text, prompts, provider responses, filenames, audio content, or generated clinical content.
```

## Post-Run Documentation Protocol

After each run:

1. Save the completed run summary under `07-tool-outputs/audit/`.
2. Add a row to `10-retest-log.md`.
3. If any test fails, update `09-findings-and-remediation.md` with a new or reopened finding.
4. If A09 status changes, update `owasp-top-10-matrix.md`.
5. If future agents need the result, update `OWASP_Context.md`.
6. Add a daily note under `docs/progress/`.
7. Run:

```bash
git diff --check
rg -n "RAW_TOKEN|PASSWORD_VALUE|COOKIE_VALUE|PROMPT_SNIPPET|TRANSCRIPT_SNIPPET|PROVIDER_SECRET|DEID_SECRET_VALUE" docs/Compliance/OWASP docs/security.md docs/testing.md
```

8. Do not commit raw DB exports, raw proxy history, raw cookies/tokens, or raw provider payloads.

## Close/Reopen Rule

`OWASP-2026-06-14-005` remains closed only while:

- representative audit event tests pass
- audit rows remain metadata-only
- retention/access/write-failure policy remains documented
- new security-relevant flows add audit coverage or are explicitly accepted

Reopen if:

- sensitive content/secrets are found in audit rows
- a major auth/admin/provider/deletion flow lacks durable audit
- audit writes silently fail for critical state-changing actions
- a new audit viewer/API exposes rows outside approved scope
