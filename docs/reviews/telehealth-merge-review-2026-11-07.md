# Telehealth merge review — 7 November 2026

Current status (8 November): partial salvage is implemented and locally verified
under the maintainer-approved
[plan](../../plans/telehealth-partial-salvage-2026-11-07.md). No further concrete
source-review blocker remains. Remote CI needs a fresh run after the local fixes
are committed and pushed. The original evidence below predates the lifecycle
change; the 8 November addendum records its verification.

## Scope and boundaries

Compared `feature/telehealth-dual-channel-capture` at
`923db5d19c5d37cf18a7b69cbcdcacf8c9d87ea7` with local `master` at
`bf63c2879dd8526db582a9598663db92df13642b`, which also matched `origin/master`
after fetching the merge target during review. The working tree was clean at review start. This is
a dated review record, not the current runtime contract.

Terra reviewed backend and frontend paths independently and re-reviewed their
fixes; its second frontend pass found the deferred-switch race. Luna implemented
bounded fixes and ran focused verification. Sol coordinated scope, reviewed
clinical-content and lifecycle risks, and completed the final navigation and
standalone-harness review after the last frontend fixes. Review used
executable sources and tests; knowledge-graph output was supporting discovery
and did not establish coverage or correctness.

Preserve owner-only clinical content, transcript-root deletion and retention,
owner encryption, durable external-secret cleanup, provider snapshots,
credential-before-submission accounting, dispatch idempotency, source identity,
redaction boundaries, and clinician review of generated drafts.

## Findings and remediation

1. **Clinical source text could lose words.** Telehealth paths could derive the
   saved source from timing segments before comparing those segments with it.
   A synthetic reproduction turned `No chest pain` into `chest pain` and passed
   the comparison. The fix takes canonical provider text independently and
   retains optional timing only when it matches the complete text. Coverage
   includes generic REST, Deepgram, and ElevenLabs; invalid canonical text
   fails closed and explicit valid Deepgram silence remains supported.
2. **Reconciliation could starve crash-completed work.** Repeatedly selecting
   the oldest pending captures could exclude later ready captures. The fix
   selects eligible ready captures and rotates failed application candidates
   behind eligible peers, preserving bounded work and the existing lock order.
3. **Journal persistence could never recover after a transient failure.** The
   client cached a rejected preparation promise. The fix retries persistence
   while retaining the exact encoded audio, key, sequence, and timing.
4. **A lost capture-create response could hide committed work.** A cached empty
   status check prevented recovery without reload. The fix invalidates stale
   checks and refreshes owner-scoped unresolved state, including a previous
   empty check still in flight. Cleanup is scoped to the starting client and
   consultation. Re-review also found a deferred consultation switch could
   complete while capture startup was pending. Failure cleanup now rebinds the
   selected consultation, and pending workspace responses and navigation
   fallbacks recheck the existing recording-switch barrier before changing
   consultation. Changed browser modules have refreshed entry-point versions.
5. **The migration head assertion omitted new tables.** Verified the migrations
   and models, then added both Telehealth tables to the expected schema set.
6. **Submitted failed captures have no resolution action.** Silent results or
   expired failed audio can leave a consultation permanently blocked. Existing
   discard explicitly permits only unsubmitted captures. This remains a merge
   blocker pending the maintainer's lifecycle decision; see
   [the issue log](../../DISCOVERED_ISSUES.md#telehealth-submitted-failure-resolution--2026-11-07).
7. **The standalone CI capture harness used the old startup contract.**
   [PR 8](https://github.com/AbsurdSyssie/OpenScribe/pull/8)'s
   [latest smoke run at the reviewed commit](https://github.com/AbsurdSyssie/OpenScribe/actions/runs/42370495457)
   failed before Docker startup because the harness called capture startup
   without the newly required prepared source. The harness now exercises
   explicit source preparation before recording, retaining its existing
   source-isolation and failure assertions. The separate Stop UI script also
   needed its module and capture-fixture mappings updated. Both standalone
   scripts pass locally; the remote run still describes the unmodified pushed
   commit and has not been rerun against these uncommitted fixes.
8. **Contradictory silence metadata must fail closed.** The combined regression
   run exposed empty canonical Deepgram text and words alongside nonempty
   configured segments. Silence validation now rejects nonempty or malformed
   configured segments, preserving genuine silence when that optional field is
   omitted or an empty list. The old segment-recovery assertion was replaced
   with rejection and valid-empty cases after establishing this clinical-safety
   defect; output validation was not weakened to satisfy the test.

## Verification

Baseline focused checks: 188 Telehealth/STT/input-exclusion tests and 76
structured-transcript/diarisation tests passed. The corrected migration-head
test passed. The API authorization audit exited successfully. Operational
documentation validation checked 54 maintained files. Frontend remediation
checks passed four tests, and JavaScript syntax and diff whitespace checks
passed. Runs requiring local test services passed through the supported elevated
execution path after sandbox-restricted PostgreSQL connections failed before
collection. No isolation guards or connection settings were changed.

The combined regression command was:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q \
  tests/test_telehealth_*.py tests/test_mic_visualizer.py tests/test_stt_diarization.py \
  tests/test_consultation_split_writer_locks.py tests/test_batch_capture_startup.py \
  tests/test_structured_transcripts.py tests/test_structured_transcript_security.py \
  tests/test_diarised_editor_frontend_js.py tests/test_diarised_transcript_browser.py \
  tests/test_diarised_generation.py tests/test_split_review_js.py \
  tests/test_dspt_retention.py tests/test_retention.py \
  tests/test_transcript_ingestion_diagnostics.py tests/test_task_outbox.py \
  tests/test_quota_lifecycle.py tests/test_stream_generation_barrier.py \
  tests/test_deepgram_live_route.py tests/test_migrations.py tests/test_docker_runtime.py
```

Result: **470 passed, 1 skipped, 1 failed**, with six warnings, in 180.13 seconds.
The failure exposed contradictory Deepgram silence metadata: empty canonical
text and words with nonempty configured segments. The old assertion expected
segment text; accepting empty text would also be unsafe. Validation now rejects
that contradictory response while preserving valid silence.
The remaining migration, Docker runtime, browser, ownership, redaction, quota,
outbox, retention, and source-lifecycle checks passed in this run.

After the combined run, the API authorization audit exited 0 again,
operational-document validation checked 54 maintained files, and diff whitespace
validation passed. Both standalone commands passed locally:
`node tests/telehealth-browser-smoke.mjs` and
`python3 scripts/smoke_telehealth_stop_ui.py`.

Final focused verification, independently repeated after the guard:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q \
  tests/test_stt_diarization.py tests/test_telehealth_stt_timing.py
```

Result: **60 passed**, with four warnings, in 0.48 seconds. Both standalone smoke
scripts, maintained-document validation, and diff whitespace checks passed
again. The combined suite was not rerun after this last bounded STT change;
its original failing result above is retained accurately. The skip reason was
not emitted by the combined `-q` run and is not inferred.

Terra's backend re-review found no further blocker in the canonical-text and
fair-reconciliation fixes. Its frontend re-review prompted the additional
navigation guards, which Sol reviewed after implementation. Sol also reviewed
the final contradictory-silence validation. No further concrete blocker was
found in those bounded fixes. The unresolved lifecycle decision remains logged
in `DISCOVERED_ISSUES.md` and the maintained capture reference.

## Limits

Physical microphone/tab/window recording, actual provider requests, production
browser profiles, and Windows/macOS capture were not verified by these checks.
The feature remains default-off; the maintained synthetic physical walkthrough
and existing privacy/clinical release gates still apply. No migration,
configuration, provider accounting, or submitted-capture lifecycle change has
been introduced by these review fixes. No merge, commit, push, deployment, or
live clinical recording was performed.

## 8 November addendum — approved partial salvage

The maintainer selected partial salvage and automatic addition of later recovered
speech, without another acceptance step for recovery. The active implementation
plan records that decision. This supersedes the pending decision in finding 6;
the earlier review results above remain historical evidence.

The change adds the durable `partial` capture state, explicit owner acceptance,
and automatic application of unapplied successful jobs into new encrypted source
versions. Accepted missing jobs retain their original retry and expiry rules.
Existing clinician edits, deletions, generated notes, and frozen generation
snapshots remain intact. Coverage notices enter saved clinical source before
redaction, cannot be edited as speech, and cannot enable generation by themselves.
Another unresolved writer defers automatic insertion. Paginated owner metadata
keeps older captures discoverable and distinguishes transcribed results awaiting
application from results already included in the transcript.

Luna implemented backend and frontend changes and independently added database
regressions. Terra reviewed transaction ordering, readiness barriers, source
preservation, owner access, and the recovery display. Sol coordinated the approved
contract, reviewed clinical-source and transaction risks, and maintained docs.
The review loop fixed acceptance/application races, disabled-autoflush status
reloads, independent generation/input barriers, silent-source interruption
provenance, pagination refresh, and the deferred-result receipt. Terra's final
source review found no further concrete blocker, contingent on verification.

Focused salvage and migration checks passed 13 tests. The first expanded combined
run passed 512 tests, skipped one, and failed two in 164.94 seconds. Both failures
were stale fixture hooks in the original application test: it intercepted the old
append helper after application moved to the preserving merge helper. The test
was updated to verify persisted turns and versions. The skipped Docker test
requires a local `redis-server` binary for an isolated transport check; no service
was installed or isolation guard changed.

After the fixture repair, focused application, salvage, and migration checks
passed **22 tests** in 5.71 seconds. The expanded combined command is the original
command above with `-rs` and `tests/test_transcript_streaming.py` added; the
Telehealth glob also includes the new salvage and migration tests. Its final
result was **514 passed, 1 skipped, 6 warnings in 275.00 seconds**. The skip remains
the isolated transport check requiring `redis-server`. The warnings are dependency
deprecations. The API authorization audit exited **0** after pytest finished.
Logs: `/tmp/openscribe-final-partial-pytest.log` (first expanded run) and
`/tmp/openscribe-final-partial-pytest-rerun.log` (passing rerun).
Both standalone Telehealth commands passed, operational-document validation
checked 54 maintained files, and diff whitespace validation passed. Test-service
connections again required the supported elevated execution path; connection
settings and isolation guards were not changed.

Deployment requires additive Alembic revision `a1b2c3d4e5f6`; downgrade refuses
while partial captures remain. Feature defaults and provider accounting remain
unchanged. The remote smoke check still fails on pushed HEAD
`923db5d19c5d37cf18a7b69cbcdcacf8c9d87ea7`, which does not contain these working-tree
fixes. Physical capture, real providers, Windows/macOS recording, and rollout
readiness remain outside this synthetic verification. At this local-verification
milestone no commit, push, merge, deployment, or live clinical recording had been
performed. The maintainer subsequently authorized committing and pushing the
reviewed branch to verify remote CI; merge and deployment remain out of scope.

### First push and CI environment finding

The reviewed fixes were committed and pushed as
`db6ed17af88085eeb5d4dcc11f6a64307e02b407`. The worktree was clean and synced with
the existing branch upstream. No merge or deployment was performed.
[Run 42435116802](https://github.com/AbsurdSyssie/OpenScribe/actions/runs/42435116802)
passed docs, syntax, and both Telehealth smoke checks, then failed before runtime
build: the hosted Ubuntu runner used Python 3.14, while pinned
`presidio-analyzer==2.2.360` requires Python below 3.14. The application Docker
runtime uses Python 3.12. Aligning the audit interpreter with that existing runtime
is an environment correction; the dependency audit and dependency pins remain
intact. Runtime build, container tests, and container authorization audit were
skipped in this failed run and still require fresh CI evidence.
