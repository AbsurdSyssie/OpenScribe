# LLM resilience and operational monitoring

Status: implemented and verified locally on 14 October 2026, authorised in the conversation. No production deployment, instance resizing, commit or push is included. Maintained operational documents describe current implemented behaviour.

## Outcome

Bound slow provider calls, recover from definite temporary capacity rejection, keep ordinary users informed without technical alarms, and expand existing admin Usage telemetry into useful operational monitoring. Preserve all existing pending split-deletion, Gemini thinking and checker debug-visibility fixes.

## Existing seams and constraints

- `llm_adapters/runtime.py` is the shared synchronous, database-free transport seam; Gemini has a separate client builder. Current transport timeouts do not enforce total wall duration. Celery currently uses its default prefork pool.
- Ordinary generation and checking share a document but currently have independent 900-second deadlines. Lifecycle reconciliation can expire the main attempt while checking runs. Split analysis, generation and verification are separate durable jobs, currently with 600-second attempt deadlines.
- `ProviderAttempt`, `ProviderUsageEvent`, generated documents and outbox rows already provide correlation, safe error codes and lifecycle timestamps. Admin Usage has aggregates and detailed split failures; Security Audit is a different purpose.
- Workspace ordinary failures currently display server error wording. Split batches lack terminal diagnostic codes. Owner-only content access remains unchanged; role-specific details apply only within existing authorised owner views. Team leaders do not gain access to other users' consultations or the system-admin Usage page.
- Retry one frozen logical request after confirmed temporary HTTP 429 only. Never automatically replay timeout, ambiguous connection failure, 503, invalid output or configuration errors. Preserve existing checker invalid-output retry policy and quota settlement, but fit it within the checker budget.

## Implementation

1. Add shared execution policy with monotonic budgets: connection at most 10 seconds, main call at most 90 seconds, ordinary generation/checking at most 150 seconds total from initial submission, checker stage at most 30 seconds including its existing validation retry. Split analysis and generation each get a 90-second execution budget; verification gets 30 seconds. Suggestions get 90 seconds. Queue waiting is measured separately.
2. Enforce transport timeouts and a scoped wall timer in supported Unix main-thread synchronous execution. Pin maintained workers to prefork. Restore prior signal/timer state correctly; unsupported execution must fail safely rather than silently lose the wall bound. No abandoned helper threads or subprocess IPC for clinical data. Close clients on every path.
3. Allow at most four HTTP requests for confirmed temporary 429, equal-jitter sleeps of 1–2, 2–4, 4–8 seconds, validated Retry-After respected within at most 30 seconds cumulative waiting and remaining stage budget. Disable SDK retries. Hard-quota rejection must not retry. Stop if durable attempt is no longer active. Preserve the frozen request, credential, one logical quota reservation and submitted-before-call boundary.
4. Expand ProviderAttempt with bounded, structured transport-event metadata if existing columns cannot represent individual failed HTTP calls and recovered retries. Only timestamp, request ordinal, duration, controlled code/status and numeric wait are allowed. No response/header body, exception text, credential, prompt or clinical content. Add an additive migration and constraints. Shared persistence helper updates only a still-submitted, live attempt and commits observed retry metadata before waiting.
5. Reconcile ordinary main/checker deadlines and checker fail-open lifecycle. Validated draft survives checker failure; expired/deleted roots remain unavailable and late results cannot overwrite terminal or newer state. Preserve conservative settlement after ambiguous submission. Focused fault tests must cover this before release.
6. Reuse ordinary/split renderers for quiet delayed status and plain terminal messages. Distinguish temporary failures from administrator-action errors. Show stable operation reference and time as support details; expandable controlled technical details only for the owning user's team-leader role. Show checker-unavailable only when checking actually failed, not when no checker was selected. Add bounded safe split diagnostic projections within existing owner authorisation.
7. Expand system-admin Usage with bounded, team-filtered recent operational failures/recovered retries and lookup by reference. Reuse scalar projections from attempts/usage/documents; do not decrypt content. Include queue wait, call duration, safe category, model, stage, retries and final outcome. Add recent-window health summaries for provider errors, queue age and overdue attempts, grouped rather than per-error notification spam. No external messaging service or cross-owner leader dashboard.
8. Make isolated control/generation/ingestion workers an opt-in maintained runtime mode, with configurable concurrency and prefork/prefetch settings, preserving local compatibility. Ten generation slots are configurable, not enabled on the undersized production host in this task. No fairness semaphore, provider fallback, circuit breaker or paid capacity purchase is introduced.

## Delegation and verification

- Luna: discovery, UI/projections, admin reporting, operations/configuration, focused test execution and documentation.
- GPT-6.1 Sol: high-value shared timeout/retry and quota/lifecycle integration, additive telemetry schema and migration.
- Partition write ownership explicitly. Run database pytest/audit commands sequentially using project venv and `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app`; no live provider calls. Synthetic data only.
- Focused checks during implementation: retry/deadline/fault injection, signal restoration, safe telemetry constraints, checker fail-open, duplicate delivery, deletion/expiry, role-safe UI and admin team filters, worker topology/configuration.
- Review implementation before broad suites. Then run affected integration suites, migration checks, API authorization audit if route contracts changed, maintained-document validation and appropriate browser/Node checks. Do not claim skipped or unrun checks.
- Update maintained provider, checker, split, Usage, Docker/environment/testing docs as applicable. Record unrelated findings in DISCOVERED_ISSUES.md.

## Completion evidence

- Luna performed discovery, initial UI/admin implementation and the first full verification; GPT-6.1 Sol completed and reviewed integration when agent usage limits interrupted work.
- Added migration `b9c0d1e2f3a6`: bounded provider transport metadata and the validated-draft checkpoint timestamp. Actual PostgreSQL upgrade/downgrade, constraints, expiry, late response and duplicate-delivery checks passed.
- Full repository suite: 2,370 passed. API authorization audit passed. After the final monitoring correction to count every failed transport category once, the complete admin UI and presentation suites passed 313 tests.
- Maintained-document validation checked 45 files. Shell syntax and whitespace checks passed. Verification used synthetic providers and isolated test infrastructure; no production changes were made.
- Reconciled stale exact projection/cache/timeout assertions and accepted-intent preference expectations against current contracts. Stabilised pre-existing random parameter IDs for parallel collection. Strengthened executable XSS checks and fixed the expired-ingestion retry UI projection using the existing expiry service, preserving earliest-gap ordering.
- Recorded the unsupported-worker preflight follow-up: supported launchers pin prefork, but an unsupported execution context discovered after submission retains conservative quota settlement. No provider fallback, external paging, fairness semaphore or capacity purchase was introduced.
