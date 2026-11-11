# Telehealth partial salvage and automatic recovery

Date: 7 November 2026. Status: implemented and locally verified on 8 November 2026;
remote CI requires a run containing the local fixes.

## Authorized outcome

The maintainer selected partial salvage instead of abandoning a submitted
capture, then declined an additional review step for recovered speech. Publish
available successful results after explicit acknowledgement of an incomplete
capture; retain failed source audio under its original retry/expiry rules.
Automatically incorporate later successful retries into a new transcript
version. Do not discard a consultation because one source window failed.

Preserve all existing merge-review fixes. The initial scope excluded commit and
push. After local verification, the maintainer explicitly authorized committing
and pushing this branch to verify remote CI. Merge, deployment, physical capture,
and real provider requests remain outside the authorized scope.

## Current implementation and drift

Each source part already has independent encrypted results and accounting.
Successful siblings remain completed while a failure makes the capture and
transcript failed. Application currently requires all parts to succeed; the
unresolved barrier then blocks editing, generation, and further recording.
Existing incomplete-capture acknowledgement permits absent captured audio, but
does not permit a retained part's STT failure. Completing the current capture
would disable its retry path; reapplying the whole capture would duplicate or
overwrite previously published/edited text. All-silent results also have no
explicit owner resolution. The 5 November plan is historical context, not this
work's intended contract.

## Implementation contract

- Add a durable `partial` capture outcome with an additive migration. It means
  the owner explicitly accepted using available source, not that missing audio
  was recovered. Existing active/unsubmitted states retain their contracts.
- An owner-only, feature-gated, CSRF-protected action accepts available results
  for a submitted failed capture. Preserve the original capture interruption
  and acknowledgement. No provider call, quota refund, source deletion, or
  retention extension occurs merely because available source is accepted.
- Publish only completed, decryptable, unapplied source jobs; mark only those
  jobs applied, atomically with their transcript version. Already applied jobs
  are durable deduplication markers. Preserve all earlier source text, turn
  identities, saved clinician edits, and generated documents.
- A visible partial-source notice must also enter saved clinical source before
  redaction, so generation sees that coverage was incomplete. Show current
  missing channel/window intervals from existing bounded timing metadata.
  Historic acceptance warnings must not falsely claim a recovered interval is
  still missing. Original uncaptured-audio warnings remain conservative.
- Partial acceptance releases this capture's editing/generation/input barrier.
  Its retained failed jobs remain individually retryable through existing
  source identity, credential, quota, outbox, claim, and expiry services.
  Accepted missing jobs must not re-block the root through generic latest-job
  or pending-job status reconciliation. Other unresolved sources still block.
- Later results automatically create a new source version exactly once.
  Insert new timed material in its evidenced capture order while preserving
  existing turns' relative order and text. If chronology cannot be established,
  retain source labels and explicitly identify the recovered block rather than
  invent timing. Never rebuild an edited capture from original provider text.
  Existing generated notes and immutable queued generation snapshots remain
  unchanged. Existing editor revision-conflict handling protects dirty edits.
- Defer automatic source insertion while another recording/unresolved source
  holds the transcript's writer boundary; retain encrypted results and use
  durable reconciliation to apply later without another provider call.
- Expiry of an accepted missing part preserves its gap metadata and durable
  cleanup but does not revive a global failure barrier. Late/duplicate results
  remain fenced by existing attempt and source-deadline rules.
- Explicit owner acceptance must also resolve all-silent/no-usable-result
  captures without inventing clinical text, deleting prior content, or enabling
  note generation from a warning alone. Retain any still-retryable failed audio.
- Owner-only access, root expiry/deletion, encryption, redaction fail-closed,
  structured-note validation, metadata-only diagnostics, and provider accounting
  remain unchanged.

## Ownership and coordination

Sol coordinates design, integration, maintained docs, and high-stakes checks.
Luna backend owns models/migration, services, schema/API/audit integration and
backend tests. Luna frontend owns Telehealth UI/templates/cache references and
frontend tests, using an agreed metadata-only API contract. Do not overlap
writes; agree the API before frontend implementation. Terra reviews changes
independently; Luna fixes findings and review repeats until no merge blocker.

## Verification and documentation

Use synthetic sources only. Test successful/failed/pending/expired/silent
combinations, explicit confirmation/cancellation, owner/admin/CSRF/expired-root
boundaries, exact replay, retry accounting, automatic addition and duplicate
delivery, clinician edits/deletions, existing notes/snapshots, deferred insertion
during another capture, source expiry/deletion cleanup, migration round-trip and
downgrade refusal, and partial-source generation/redaction warnings.

Run focused pytest using `.venv` with `APP_ENV=test`,
`COOKIE_SECURE_MODE=auto`, and `HSTS_SOURCE=app`. One agent owns pytest/API audit
execution at a time. Follow the documented elevated retry for sandbox-restricted
test services; never alter DSNs or isolation guards. Update API audit entries,
run the authorization audit, standalone Node/Stop smoke checks, relevant
lifecycle/structured-source regressions, docs validation and diff checks.

Update `docs/transcript-capture.md`, `docs/api.md`, `docs/dbtesting.md`, relevant
browser storage/testing/walkthrough notes, the issue log, and a dated addendum
to the merge-review record. Preserve historical evidence and accurately report
what was run. Remote CI needs a fresh run after a separately authorized push.

## Completion evidence — 8 November 2026

Implemented the contract with additive migration `a1b2c3d4e5f6`. Owner-only
metadata now includes paginated accepted captures and an explicit applied-result
indicator so deferred speech remains visibly outstanding. The review loop also
fixed transaction flush ordering, independent input/generation barriers, original
interruption preservation for silent captures, and pagination refresh.

Luna implemented backend/UI changes and regression tests; Terra reviewed and
re-reviewed the source; Sol coordinated the approved boundaries, integration
review, and maintained documentation. No further concrete source blocker remains.

Final combined verification: **514 passed, 1 skipped, 6 dependency warnings** in
275 seconds. The skip requires a local `redis-server` binary for an isolated
transport check. Focused application/salvage/migration checks passed 22 tests.
The sequential API authorization audit, both standalone Telehealth smoke checks,
54-file maintained-document validation, and diff whitespace checks passed.
The [dated review record](../docs/reviews/telehealth-merge-review-2026-11-07.md#8-november-addendum--approved-partial-salvage)
preserves the original failing runs and final results. At this local-verification
milestone no commit, push, merge, deployment, physical capture, or real provider
call had been performed. Commit and push were then authorized to verify remote
CI. The existing physical/clinical release gates remain outstanding.
