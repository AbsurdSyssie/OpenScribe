# Review problems with explicit suggestions

Status: implemented and verified, authorised by the user's choice on 12 October 2026.

## Outcome

An eligible consultation owner can open **Review problems** at any time without
saving sources, requesting AI analysis, or generating notes merely by opening
the modal. A current saved review opens normally. Otherwise the modal explains
its state and offers **Suggest problems**, **Retry analysis**, or **Update
suggestions**, as appropriate. No blank editable server draft bypasses the
validated analysis contract.

Suggestion requests save the current Working note and dictation, then create an
explicit manual-review intent. Even a zero/one-problem result waits for clinician
review. Opening/reviewing does not change the consultation's Multiple problems
marker or automatic-splitting preference. Ordinary Create retains its current
automatic single-note behavior.

## API and retry boundary

Extend the existing intent POST with strict optional booleans
`manual_review_requested` and `retry_failed_analysis`, both default false.
An explicit manual-review action requires deployment availability and normal
owner eligibility but does not require the automatic preference or marker.
Existing-key replay remains authoritative and cannot retarget accepted work.

An explicit failed-analysis retry creates one fresh execution, provider attempt,
quota reservation and durable dispatch against the same current source-bound
analysis. Preserve historical terminal executions/attempts and encrypted source,
candidate and provider snapshots. Rebuild the new request through the corrected
canonical builder. Keep the frozen eligible provider binding; do not silently
change providers. Lock and validate the parent, source freshness, terminal
history and next attempt number. Only one active attempt may exist. Old duplicate
task deliveries must not terminalise the new attempt. An ordinary Create still
reuses failed analysis unless an explicit retry was requested.

No automatic Gemini/SDK retry, region change, quota-refund change, manual-first
draft contract, or historical production-row repair belongs to this work.

Do not prepare a partial source while recording or while transcription jobs are
pending: use existing readiness services and safe errors. Saved Working note or
dictation alone are valid sources. Retention, encryption, redaction, provider
eligibility and owner-only content access remain unchanged.

## UI states

- Current draft: review existing topics without new provider work.
- No analysis: explain the next action and offer Suggest problems.
- Analysis pending: show progress; prevent duplicate suggestions.
- Analysis failed: show controlled diagnostic guidance and explicit Retry analysis.
- Sources changed/unavailable draft: present read-only state and explicit Update suggestions.
- Confirmed/in-progress generation: inspect the frozen plan; do not generate or start another intent on open.
- Empty/recording/transcription pending: open explanatory state; disable work until ready.

Use existing review save/confirmation/one-note services and preserve dirty-edit,
session-switch, idempotency and focus/accessibility protections. Version browser
assets together.

## Validation

Meaningful controller tests must establish that opening never performs writes or
provider work, suggestions explicitly request manual review, one-problem results
do not auto-generate, failed retries actually queue new work once, and stale or
old task results cannot affect a new request. Retain ordinary one-note,
manual-marker and split-generation coverage. Add owner/gate/idempotency/quota/
outbox tests for the extended intent contract and the analysis retry transition.
Run pytest and the API authorization audit sequentially with the documented test
environment. Update the route-audit manifest where needed, maintained API/split
docs, and operational-document validation.

## Completion evidence

Backend intent, progression, queue, runtime, pre-submit and API checks passed
(192 tests). The split browser suite passed (16 tests), as did focused UI,
workspace and manual-review browser checks (61 tests, with overlap). The API
authorization audit passed all 160 routes. JavaScript syntax, whitespace and
maintained-document validation passed (45 documents).

Final review added protection against replaying an ambiguous submission with a
different action, and against starting suggestions during active generation.
Header controls wrap so partial-note recovery remains clickable. No migration,
provider configuration change or automatic provider retry was introduced.
