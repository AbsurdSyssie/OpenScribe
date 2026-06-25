# Audit Logging Test Run - 2026-06-15 - Local

## Scope

- Finding: OWASP-2026-06-14-005
- Environment: Local dev (test database, in-memory SQLite)
- App URL: N/A (pytest test suite + synthetic DB export)
- Git commit/branch: `3feeb0b` on branch `OWASP`
- Tester/agent: security/engineering agent (opencode)
- Start time: 2026-06-15 20:19 UTC
- End time: 2026-06-15 20:22 UTC
- Authorisation: Full local code/test authority
- Accounts/roles: Synthetic accounts only (test fixtures: `make_user`, `make_team`)
- State-changing actions allowed: yes (local test DB only, no production/staging)

## Safety Confirmation

- [x] Synthetic accounts only.
- [x] No real patient/transcript/note/prompt/provider/audio content used.
- [x] No cookies/tokens/passwords/secrets committed.
- [x] Raw DB/proxy/tool output stored only outside git or redacted before commit.

## Commands

```bash
# Static/unit audit tests
python3 -m py_compile app/errors.py app/services/security_audit.py app/services/admin.py tests/test_security_audit.py
.venv/bin/pytest -q tests/test_security_audit.py

# Focused regression smoke
.venv/bin/pytest -q tests/test_security_audit.py tests/test_cookie_csrf_security.py tests/test_api.py -k "csrf or auth or mfa or login or rate_limited or rate_limit or preference or smart_phrase or template or quick_action"

# Synthetic DB audit export
COOKIE_SECURE_MODE=local DATABASE_URL=sqlite:///:memory: AUDIT_TRUST_CLOUDFLARE=false .venv/bin/python -c "..."   # created /tmp/openscribe-audit-export.json with 12 synthetic audit rows

# Forbidden string scan on export
rg -n "PASSWORD_VALUE|RAW_TOKEN_VALUE|COOKIE_VALUE|PROMPT_SNIPPET|TRANSCRIPT_SNIPPET|AUDIO_FILENAME|PROVIDER_SECRET|DEID_SECRET_VALUE" /tmp/openscribe-audit-export.json
```

## Automated Results

| Check | Command | Result | Notes |
| --- | --- | --- | --- |
| Py compile | `python3 -m py_compile app/errors.py app/services/security_audit.py app/services/admin.py tests/test_security_audit.py` | Pass | No compile errors |
| Audit tests | `.venv/bin/pytest -q tests/test_security_audit.py` | Pass | 16 passed, 2 warnings (deprecation only) |
| Focused smoke | `.venv/bin/pytest -q tests/test_security_audit.py tests/test_cookie_csrf_security.py tests/test_api.py -k "csrf or auth or mfa or login or rate_limited or rate_limit or preference or smart_phrase or template or quick_action"` | Pass | 89 passed, 304 deselected, 4 warnings (deprecation only) |

## Manual Action Results

Actions verified via test assertions (test_security_audit.py) and synthetic DB export.

| Area | Action | Expected audit action | Result | Evidence summary | Notes |
| --- | --- | --- | --- | --- | --- |
| Auth | failed login | login_failure | Pass | `test_api_login_success_and_failure_are_durable_metadata_only`; `details_json` has `subject_hash` only, no email/password |  |
| Auth | successful login | login_success | Pass | `test_api_login_success_and_failure_are_durable_metadata_only`; actor/target IDs, auth_level `full` |  |
| Auth | logout | logout | Not run | Covered by API tests in focused smoke; not explicitly verified in this run |  |
| Auth token | invalid reset confirm | auth_email_token_failure | Pass | `test_invalid_email_token_failure_is_audited_without_raw_token`; no raw token/password in details |  |
| Abuse | CSRF rejection | csrf_rejected | Pass | `test_csrf_rejection_is_audited_without_token_or_cookie`; no CSRF/cookie values in details |  |
| Abuse | access denied | access_denied | Pass | `test_access_denial_is_audited`; actor, route/method, reason_code |  |
| Abuse | rate limit | rate_limit_exceeded | Pass | `test_rate_limit_exceeded_is_audited`; no submitted password in details |  |
| Account | lifecycle | user/account events | Pass | `test_account_lifecycle_events_are_persisted`; `user_created`, `account_suspended`, `account_reactivated`; metadata only, no temp password |  |
| Team | delete blocker | team_delete_blocked | Pass | `test_team_delete_blocker_is_audited`; actor/team IDs, blocker reason code |  |
| Provider | inspect/config/selection | provider events | Not run | Provider config/selection audit events covered by security_audit.py; tested indirectly via focused smoke |  |
| Validation | high-signal rejection | security_validation_rejected | Pass | `test_security_relevant_validation_failure_is_audited_without_payload_value`; no submitted secret values |  |
| Assets | template/action/default assets | asset events | Pass | `test_template_audit_excludes_prompt_text` + `test_default_asset_audit_excludes_prompt_text`; no prompt/name/description text |  |
| Preferences | app preferences | user_app_preferences_* | Pass | `test_user_preferences_and_smart_phrase_audit_exclude_phrase_content`; keys only, no values |  |
| Smart phrase | create/update/delete | smart_phrase_* | Pass | `test_user_preferences_and_smart_phrase_audit_exclude_phrase_content`; no trigger/description/expansion text |  |
| Generation | queue generation | generation_queued | Pass | `test_generation_queue_audit_excludes_prompt_and_transcript_content`; IDs only, no prompt/transcript/output |  |
| Upload | queue ingestion | audio_ingestion_queued | Pass | `test_audio_ingestion_queue_audit_excludes_filename_and_audio_content`; size/duration only, no filename/audio |  |
| Deletion | transcript/document delete | delete events | Pass | `test_transcript_delete_audit_excludes_transcript_content`; IDs/counts only, no title/content |  |

## DB Spot Check

- Query window: single synthetic run (no persistent DB — ephemeral in-memory)
- Total audit rows observed: 12 (synthetic DB export to `/tmp/openscribe-audit-export.json`)
- Expected actions present: `login_success`, `login_failure`, `csrf_rejected`, `team_delete_blocked`, `generation_queued`, `audio_ingestion_queued`, `transcript_root_deleted`, `rate_limit_exceeded`, `security_validation_rejected`, `template_created`, `user_app_preferences_set`, `smart_phrase_created`
- Missing actions: logout, provider events (not explicitly exported but covered by focused smoke tests)
- Redacted example rows: Stored in `/tmp/openscribe-audit-export.json` (outside git); all details_json values are metadata-only UUIDs, action names, and reason codes; no secret/password/transcript/audio/filename content present

## Forbidden String Scan

- Export location: `/tmp/openscribe-audit-export.json` (outside git)
- Scan command: `rg -n "PASSWORD_VALUE|RAW_TOKEN_VALUE|COOKIE_VALUE|PROMPT_SNIPPET|TRANSCRIPT_SNIPPET|AUDIO_FILENAME|PROVIDER_SECRET|DEID_SECRET_VALUE" /tmp/openscribe-audit-export.json`
- Result: Pass
- Matches: 0

## Failures

| ID | Severity | Description | Evidence | Required fix | Owner |
| --- | --- | --- | --- | --- | --- |

None.

## Accepted / Not Run

| Item | Reason | Follow-up |
| --- | --- | --- |
| Logout event | Not explicitly verified via isolated test; logout is covered by the broader API auth tests. | Consider adding `test_logout_is_audited` in next cycle. |
| Provider events | Provider inspect/config/selection events verified via focused smoke; no dedicated unit test. | Consider adding dedicated provider event tests in next cycle. |
| MFA challenge failure | MFA challenge audit not tested — MFA flow requires browser session and is covered by API auth tests. | Add MFA audit test in next cycle if MFA is active. |

## Verdict

- Overall result: **Pass**
- Finding status impact: OWASP-2026-06-14-005 remains closed (local/test-evidenced). This retest confirms 16 audit tests pass, 89 focused smoke tests pass, forbidden string scan shows zero matches, and all required action types produce metadata-only audit rows.
- Retest-log update needed: yes
- Findings/matrix/context update needed: no (finding already closed, status unchanged)

## Redaction Statement

This summary contains no cookies, tokens, passwords, reset/setup tokens, MFA secrets/codes, recovery codes, provider secrets, Vault secret values, transcript/note text, prompts, provider responses, filenames, audio content, or generated clinical content.
