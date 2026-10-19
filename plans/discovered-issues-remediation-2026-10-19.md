# Discovered issues remediation — 19 October 2026

User request: tidy `DISCOVERED_ISSUES.md`, fix its outstanding issues, update operational documentation, and review the changes for regressions.

Baseline: `97da191926db1da74fdf741e01f4f8490d60f6c8`; clean working tree before this work.

## Outcome and constraints

- Separate open defects, policy decisions, deployment follow-ups, upgrade watchpoints, and resolved history. Preserve original dated observations and resolution evidence.
- Reconcile each entry against executable code before changing it. Do not reimplement fixes already on this branch.
- Fix reproducible defects with synthetic regression coverage and update the closest maintained operational reference.
- Preserve ownership, root retention/deletion, owning-user encryption, redaction, output validation, durable dispatch/claims, no replay, and quota settlement contracts.
- Do not change selected deployment providers/models/regions, output-limit policy, or immutable request reconstruction without resolving the specific decision first.
- Run PostgreSQL/Redis-backed tests and the API audit sequentially with the project test environment. Use supported elevation if sandbox restrictions block local test services.

## Delegation

- Luna: sanitize test configuration diagnostics and reject duplicate template-suggestion JSON; then add capability checks before provider submission.
- Luna: consultation-content deletion confirmation, Scribe resize alternatives, and stale accessible UI assertions.
- Luna: SDK response bounds, Gemini 400 classification, and recovery schema compatibility assessment.
- Parent: reconcile historical/deployment entries, maintain the issue log, coordinate verification, assess contract decisions, and review the combined diff.

## Acceptance

Every original issue has an explicit disposition and evidence. Fixed issues have focused passing tests; unexecuted deployment checks and unresolved policy decisions remain explicit. Maintained docs and the issue log describe the actual result. A final independent regression review follows implementation and testing, and any resulting findings are fixed or recorded.

## Outcome

Eight code issues were fixed; 25 entries are resolved or superseded history. Focused regression runs passed (508 tests, then 177 final UI/compatibility checks), as did 16 split browser tests, the rendered Scribe resize browser test, the API authorization audit, and maintained-document validation. Separate reviewers found no remaining actionable regressions after corrections. Final verification completed on 20 October 2026. The changes are prepared for commit and not deployed. See the [dated review](../docs/reviews/discovered-issues-remediation-2026-10-19.md).

One Ollama output-ceiling policy choice awaits the user. Three deployment follow-ups and two dependency-upgrade watchpoints remain explicit in the issue log; no model/region selection or deployment was changed.
