# Gemini Priority capacity retry

Status: authorised on 10 November 2026; implemented and verified locally on 11 November 2026. Maintained provider and Usage documentation describe implemented behaviour. No deployment or live provider generation was performed.

## Outcome

Allow system administrators to opt each team's Gemini configuration into one Priority PayGo request after a temporary-capacity HTTP 429. Expose requested and observed service tiers in the existing Admin Usage failure/retry panel.

## Boundaries and acceptance

- Default off. Preserve existing `auto`, `shared` and `dedicated` capacity semantics; do not silently replace dedicated capacity with shared traffic.
- Each independent logical request starts with its configured ordinary capacity mode. After its first temporary-capacity 429, wait within the existing Retry-After, cumulative wait and stage budget, then send at most one Priority request.
- Priority uses shared request type plus the Priority shared-request header. Keep the same frozen redacted request, model, location and credential. Do not replay hard quota/billing failures, local quota rejection, authentication failures, timeouts or ambiguous connection failures as Priority.
- Fit both requests inside the existing transport limit, submitted provider attempt and quota reservation. Preserve durable claims, active-root/deadline guards, settlement and existing eligible backup behaviour.
- Freeze policy with existing provider snapshots. Older configurations and queued snapshots retain the disabled default. No sticky state across jobs.
- Keep admin authority and team scope unchanged. UI help explains increased provider pricing. Existing backup settings remain separate.
- Record only bounded, allowlisted requested/served tier metadata. Unknown served tier remains unknown; a requested Priority tier does not establish that Google served Priority. Preserve clinical-content and secret exclusions.
- Extend the transport metadata database constraint through an additive migration; preserve historical migrations and rejection of arbitrary keys.

## Execution and verification

Backend execution and admin UI/reporting have separate delegated write ownership. The parent integrates and reviews changes and runs PostgreSQL/Redis-backed checks sequentially using the project virtual environment and required test settings.

Verify opt-in persistence and admin scope, old snapshot compatibility, exact Priority headers, single escalation, terminal backup eligibility, quota/claim boundaries, cancellation/deadline guards, metadata constraints/migration, rendered UI and maintained documentation. Use synthetic providers and data; no production requests or deployment are part of implementation verification.

## Completion evidence

- Added the default-off Gemini configuration flag, request/response plumbing,
  backwards-compatible frozen snapshots, exact Priority headers, bounded
  escalation and allowlisted tier observations. The provider usage return shape,
  quota reservation, submitted claim and existing backup policy remain intact.
- Added migration `02b3c4d5e6f7` after `01a2b3c4d5e6`. Upgrade accepts legacy
  observations; downgrade removes tier keys while retaining request observations.
  Deploy the migration before updated workers.
- Added controls to the provider wizard and saved settings editor. Both global
  and team Usage tables show Priority result and observed served tier separately
  from the overall outcome. Root-reference lookup includes team-scoped backup
  and repair lineage, ordered by authorization time.
- Updated maintained provider, Gemini setup, Usage, testing and database-testing
  references. No new environment configuration is required.
- Final combined focused suite: **295 passed**. It covered provider configuration,
  runtime/snapshot/retry boundaries, telemetry, actual migration upgrade/downgrade,
  backup workflows and repair reporting, owner traces and synthetic browser UI.
- Complete upgrade-to-head schema check: **1 passed**. API authorization audit
  passed. The separate admin Usage selection passed **21 tests** before the final
  team-template browser correction; the corrected browser check then passed and
  was included in the final combined run.
- Maintained-document validation checked **54 files**; `git diff --check` passed.
  Alembic reports the single head `02b3c4d5e6f7`. Desktop/mobile settings and failure
  table screenshots were inspected using synthetic data.
- Luna agents implemented the backend, UI/reporting and focused regression tests.
  Central review checked headers, backwards compatibility, quota/claim boundaries,
  migration lineage, metadata scope and rendered results.
- Corrected one existing test helper that assumed unspecified database ordering
  for primary and backup attempts. No unrelated production issues were changed or
  recorded. Existing design-sidecar drift was left outside this feature's scope.
- Production configuration remains unchanged. The setting must be enabled by a
  system administrator after deployment; live EU Priority availability remains
  unverified by these synthetic checks.

## Pre-commit review — 11 November 2026

Independent backend and UI/reporting source reviews found one reporting defect:
known served-tier metadata was hidden when no Priority request ran. The fix uses
known Gemini served tiers for those rows, without letting a successful backup
overwrite an unknown Priority result. Both Usage templates support a known
served tier when the request tier is unspecified. Added regression coverage;
the affected Usage and browser selection passed **24 tests** after the fix.
Documentation and whitespace validation passed again. No blocking findings
remain. The graph tool could not read the newer local graph schema, so review
used direct source and call-site inspection without modifying graph data.
