# 15 - Live Lifecycle Deletion Probe

Date: 2026-06-23  
Status: local live probe passed on 2026-06-24; not yet run against staging or production.

## Purpose

Automated live-safe probe for A04 lifecycle/deletion evidence. The probe creates only synthetic tenant data, verifies role boundaries and transcript-root deletion behavior, then deletes the synthetic users and team.

## Script

```bash
scripts/security/live_lifecycle_deletion_probe.py
```

Dry-run is the default:

```bash
.venv/bin/python scripts/security/live_lifecycle_deletion_probe.py \
  --base-url https://staging.openscribe.co.uk \
  --run-id owasp-lifecycle-YYYYMMDD
```

Execution requires explicit run-id confirmation:

```bash
export OWASP_LIFECYCLE_ADMIN_EMAIL=...
export OWASP_LIFECYCLE_ADMIN_PASSWORD=...

.venv/bin/python scripts/security/live_lifecycle_deletion_probe.py \
  --base-url https://staging.openscribe.co.uk \
  --run-id owasp-lifecycle-YYYYMMDD \
  --execute \
  --confirm-run-id owasp-lifecycle-YYYYMMDD \
  --output docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/lifecycle/live-lifecycle-owasp-lifecycle-YYYYMMDD.json
```

For local dev only, the seeded crawlable admin is configured by `DEV_TEST_ADMIN_EMAIL` and `DEV_TEST_ADMIN_PASSWORD` in `.env.example`. Do not assume those credentials exist in staging or production.

Production requires an extra approval flag:

```bash
--allow-production
```

## Probe Coverage

The probe verifies:

- system admin can create a synthetic team and synthetic users
- synthetic users complete password onboarding
- owner creates a transcript root, committed transcript version, and working note
- peer cannot read or delete owner transcript
- team leader cannot read owner transcript content
- system admin cannot read owner transcript content
- owner can delete own transcript root
- deleted transcript root is no longer readable
- team leader suspension revokes owner session
- team leader reactivation preserves lifecycle rule by resetting onboarding
- cleanup deletes synthetic users and synthetic team

## Safety Guardrails

- dry-run default
- `--execute` requires `--confirm-run-id` equal to `--run-id`
- probable production host refuses execution unless `--allow-production` is present
- synthetic emails use the configured `--email-domain`, defaulting to `owasp-probe.openscribe.co.uk`
- output summary stores IDs, statuses, and counts only
- output summary must not include transcript text, note text, passwords, cookies, tokens, provider secrets, prompts, or model responses

## Current Evidence State

Local live probe passed on 2026-06-24 using a fresh local app instance on `http://127.0.0.1:8092`.

Evidence:

```text
07-tool-outputs/lifecycle/live-lifecycle-owasp-local-20260624-6.json
```

Summary:

- run id: `owasp-local-20260624-6`
- result: passed
- synthetic users created/onboarded: owner, peer, leader
- owner transcript root, committed version, and working note created
- peer read/delete of owner transcript denied with `403`
- team leader read of owner transcript denied with `403`
- system admin read of owner transcript denied with `403`
- owner transcript-root delete returned `204`
- deleted transcript read returned `404`
- leader suspend revoked owner session; owner transcript read returned `401`
- leader reactivate returned `200`
- cleanup deleted all three synthetic users and synthetic team
- follow-up DB cleanup check found `0` synthetic users, `0` synthetic teams, and `0` synthetic transcripts

The local run surfaced a stale database FK issue: `security_audit_events` audit references blocked synthetic user deletion. Migration `u2v3w4x5y6z7` repairs audit event user/team foreign keys to `ON DELETE SET NULL`, matching the SQLAlchemy model and preserving audit metadata without blocking deletion. Focused migration regression passed.

No staging or production run has been captured yet. When run, store JSON output under:

```text
07-tool-outputs/lifecycle/
```

Then update:

- `09-findings-and-remediation.md`
- `10-retest-log.md`
- `11-remediation-plan.md`
- `owasp-top-10-matrix.md`
- `OWASP_Context.md`
