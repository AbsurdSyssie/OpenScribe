# Split generation diagnostics

Status: implemented and reviewed, 8 October 2026. Maintained behavior is
documented in docs/consultation-splitting-preference.md and docs/usage_tab.md.

## Outcome

Make dictation/Create/Regenerate split failures actionable for clinicians and
debuggable from the existing system-administrator Usage surface, using only
controlled operational metadata.

## Current behavior and boundaries

- Split analysis precedes generation. An unchanged source can reuse failed
  analysis, and explicit Continue as one note queues ordinary generation.
- Owner UI collapses terminal analysis, draft preparation, transport, and
  generation failures into generic warnings, discarding available safe codes.
- Analysis persists normalized provider error codes and HTTP status; confirmed
  split generation drops them. Admin failure hotspots prefer the generic error.
- Keep failed-analysis reuse, continuation, provider selection, quota/outbox,
  submission/idempotency, encryption, retention, and clinical validation intact.
- No real provider calls, raw exception text, request/response bodies, clinical
  content, topic titles, credentials, or content-access expansion in diagnostics.
- Reuse existing metadata fields. No migration or configuration change expected.

## Implementation and write ownership

1. Backend runtime: retain bounded provider error classification and HTTP status
   on split-generation execution and usage failure event, following established
   analysis behavior. Extend focused runtime tests with synthetic capacity,
   timeout, unknown, and unsafe-detail cases as appropriate. Own generation
   runtime, provider-error helper only if required, and runtime tests.
2. Owner UI: map existing safe error/status codes to stage-specific controlled
   messages for analysis, draft preparation, confirmation, continuation, and
   generation/regeneration. Explain explicit one-note recovery and failed
   analysis reuse without suggesting automatic retry. Include safe error code
   and operation identifier where available for support correlation; never
   render arbitrary server/provider message text. Preserve asynchronous state
   guards and controls. Own transcribe JS/cachebusters and focused UI tests.
3. Admin: distinguish split analysis from split generation in failure summaries,
   retain generic application and specific provider codes plus HTTP status, and
   add a bounded recent split-failure diagnostic table in existing Usage views.
   Show timestamp, stage, execution/operation UUID, safe codes and HTTP status.
   Apply existing full-admin and validated-team scope, range and retention.
   Use explicit safe projections, not raw ORM/event objects or encrypted content.
   Own admin service/routes/template, focused admin tests, and maintained docs.

The coordinator owns integration and final validation. Writers must flag shared
file needs before editing another workstream's files. Pytest/API audits run
sequentially through the coordinator because they share test infrastructure.

## Acceptance and verification

- A synthetic provider failure produces an accurate stage-specific warning;
  normal single-problem auto-continuation and explicit recovery still work.
- Unsafe/unknown codes and response/exception text never enter user/admin output.
- Synthetic Gemini HTTP 429 survives as controlled rate-limit metadata in split
  generation telemetry; quota settlement and duplicate-delivery behavior remain.
- Admin diagnostics distinguish stages, permit UUID correlation, constrain team
  views, respect ranges and bounded result sizes, and expose metadata only.
- Run focused UI, generation-runtime, admin Usage/auth tests in project venv with
  required test environment. Run API audit if API routes/contracts change; update
  manifest if applicable. Run maintained-document validation. Review final diff.
- Update consultation-splitting-preference.md and usage_tab.md with actual
  implemented messages, diagnostic fields, limitations for historical records,
  and unchanged failed-analysis reuse behavior.

## Out of scope

Automatic retries of failed analysis, provider fallback policy changes, new
logging stores, migrations, broader observability redesign, and live incident
data inspection.

## Completion evidence

- Terra implemented the three workstreams with separate write ownership; Luna
  performed a focused independent review. Review corrections were integrated.
- UI/workspace focused checks: 55 passed. Admin Usage focused checks: 12 passed,
  including scope, authorization, history, result bounds, and content exclusion.
- Split-generation runtime file: 57 passed and four unrelated preference-gate
  verifier failures. An isolated run loading the unchanged baseline runtime
  reproduced the same four failures; four corresponding deployment-gate cases
  passed. The mismatch is recorded in DISCOVERED_ISSUES.md.
- Maintained-document validation checked 45 files; diff whitespace checks passed.
- No API contract, migration, configuration, provider policy, or automatic retry
  changes. Older telemetry cannot reconstruct provider details never persisted.
