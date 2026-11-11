# Telehealth review fixes

Date: 5 November 2026

Status: complete. Luna implemented packages A, B, and C; Terra independently
reviewed all three with no unresolved material findings; Sol completed integration
and verification.

## Outcome and boundaries

Fix the five findings from the telehealth branch review. Preserve the existing
uncommitted Deepgram silence fix. Sol oversees integration; Luna implements;
Terra independently reviews the changes and regression coverage.

Current executable behavior was checked against `origin/master` and the working
tree. The previous focused baseline was 109 passing telehealth/STT tests. This
plan describes intended changes, not current behavior.

Keep owner-only access, transcript-root retention/deletion, encrypted audio,
source identity/timing, incomplete-capture acknowledgement, provider accounting,
and durable cleanup unchanged. Do not extend recovery retention or keep keys
indefinitely to conceal a concurrency defect. Use synthetic fixtures only.
No schema migration, new API shape, provider dispatch policy, or deployment
configuration change is expected. Stop for any material conflict with those
boundaries; routine implementation choices remain delegated.

## Work packages

### A. Interrupted source handling and stopped-capture retry

Owner: Luna, frontend execution. Review: Terra, then Sol integration.

Files: `telehealthRecordingClient.js`, `telehealthCapture.js`, `telehealthUI.js`
under `app/static/js/transcribe/`, their focused tests, and required asset-version
references. Do not redesign the recorder or its controls.

1. Serialize stop interruption metadata as `interrupted_channel` and
   `failure_code` for both initial stop and upload retry. Keep the internal
   callback contract separate if needed. Test actual serialized bodies against
   `TelehealthCaptureStop`, including channel-less failures and exact replay.
2. Treat capture-track mute as an interruption, including an already-muted
   source at startup. Stop through the established flush/stop path, preserve
   accepted/buffered audio, and retain the incomplete warning after unmute.
   Muting a participant inside the call application is not itself a new event
   contract: respond to the capture track's actual mute signal.
3. Provide an explicit submission retry when stop/seal succeeded but
   `/transcribe` failed or its response was lost. Distinguish it from pending
   audio upload retry. Keep the same capture ID, rely on existing queue
   idempotency, prevent duplicate clicks, and preserve incomplete acknowledgement.

Acceptance: source end, mute/unmute, processor/backpressure failures, and clean
stop produce schema-valid requests; interrupted captures cannot silently become
complete; a sealed capture can submit successfully after a failed/uncertain
request without reload or discard. UI tests must exercise the rendered action
and click path, not just a client method or static source strings.

### B. Cross-tab encrypted journal coordination

Owner: Luna, separate bounded implementation after A or in a disjoint area.
Review: Terra before final integration.

Files: `app/static/js/transcribe/telehealthRecoveryJournal.js` and its tests.

Serialize all journal operations that read or change key/window lifecycle across
same-origin tabs, including prune, persist, remove, clear, and recovery reads.
The existing promise queue protects only one journal instance. A shared browser
lock is a suitable implementation if supported: acquire it only for local
storage/crypto work, never network uploads, and fail closed before recording if
safe coordination is unavailable. An atomic IndexedDB alternative is acceptable
if it proves the same invariant without holding a transaction across unsupported
async crypto work. Do not silently fall back to unsafe per-tab serialization.

Preserve non-extractable AES-GCM keys, authenticated encryption, the eight-window
limit, six-hour age limit, acknowledged-window deletion, and key cleanup. If the
implementation adds a browser capability requirement, document it explicitly.

Acceptance: deterministically interleave two instances while one encrypts a new
window and the other removes the last old window; the surviving window must
remain decryptable. Cover concurrent clear/prune, creation races, storage errors,
lock release after rejection, unsupported coordination, and existing replay
identity/cleanup/limits. Tests must model cross-instance coordination.

### C. Propagate handed-off source expiry

Owner: Luna, backend execution. Review: Terra, then Sol integration.

Files: `app/services/transcripts.py`, relevant telehealth expiry/retry tests, and
the closest maintained lifecycle documentation.

When ingestion expiry terminalizes a telehealth job, also terminalize its linked
part and capture through the existing failure/lifecycle abstractions. Preserve
an original capture interruption and acknowledgement; use a bounded expiry code
for a previously complete capture. Do not change non-telehealth expiry behavior,
quota cancellation/settlement, provider-call fencing, retention deadlines, or
the commit-before-external-secret-deletion boundary. A late provider result must
not revive an expired job or apply a one-sided transcript.

Acceptance: queued and submitted/processing expiry leave the job, part, capture,
and transcript in consistent terminal states; expired audio remains unretained
and non-retryable; cleanup intents are durable; original clinical warnings and
accounting survive. Test the public expiry workflow or correct shared helper,
including a subsequent successful sibling result and a late result where useful.

## Integration, documentation, and verification

Write ownership is exclusive per package. Sol owns this plan and final
integration decisions; implementation agents must not overwrite each other's
changes. Terra reviews without editing. Luna resolves review findings, and Terra
checks the resolution before completion. All agents must identify pre-existing
working-tree changes and preserve them.

Use regression tests that fail on the reviewed behavior before applying fixes.
Run focused checks with the project virtual environment, then the combined
telehealth/STT suite. Set `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app`.
All pytest and API-audit commands run sequentially under the shared test-service
lock. Follow `docs/testing.md` and `docs/dbtesting.md`, including the documented
sandbox escalation procedure; do not restart services or alter DSNs to bypass
failures. The API route shape is unchanged, so audit manifest changes are not
expected; run the focused API authorization checks and escalate to the full audit
if implementation changes route behavior beyond fixing client serialization.

Update `docs/transcript-capture.md`, `docs/cookies-browser-storage.md`, and relevant
testing/browser-walkthrough instructions to reflect implemented recovery,
compatibility, and expiry behavior. Do not rewrite dated compliance evidence.
Run `.venv/bin/python .github/scripts/check-operational-docs.py` and diff checks.
Use a browser smoke test if the local environment permits a synthetic setup;
otherwise explicitly report that physical tab capture was not verified.

Completion requires all five fixes, focused and combined checks, no unresolved
material Terra findings, and a concise report of changed files, documentation,
verification limits, and any discovered follow-up issues. No commit, push,
deployment, or live recording/provider call is part of this request.

## Completion record

All five findings are implemented. Regression coverage exercises API-schema
validation of serialized interruption bodies, live/startup track mute, rendered
submission retry and action races, two-instance journal/key interleavings,
unsupported browser coordination, handed-off expiry with reserved/submitted
accounting, durable cleanup failure, and late/sibling provider-result fencing.
The earlier Deepgram silence fix remains in the working tree.

Terra's review also caught submission/discard overlap, record-button restoration,
and the outer app script cache version; Luna resolved these and Terra checked
the resolutions. Failure status now takes precedence over pending sibling jobs,
consistent with the existing failed-stream behavior; an explicit retained-source
retry restores processing normally.

Final combined command:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q tests/test_telehealth* tests/test_stt_diarization.py tests/test_dspt_retention.py tests/test_retention.py tests/test_transcript_ingestion_diagnostics.py
```

Result: 160 passed, with four third-party deprecation warnings. After the final
test-only strengthening, `tests/test_telehealth_ui_runtime_js.py` passed again
(one test, two third-party deprecation warnings). Sandbox-restricted test-service
connections were verified through the documented elevated execution path.
Operational-document validation passed for 54 maintained documents, and
`git diff --check` passed.

Updated operational references: `docs/transcript-capture.md`,
`docs/cookies-browser-storage.md`, `docs/dbtesting.md`, and
`docs/telehealth-crash-walkthrough.md`. No schema migration, API route change,
deployment configuration change, or retention extension was needed. Web Locks
is now an explicit browser capability requirement for encrypted Telehealth
recording. Existing source ownership, encryption and provider accounting remain
in place.

No physical microphone/tab capture, actual provider request, or production
browser profile was used for verification. Run the maintained synthetic physical
walkthrough after reloading the updated app; this remains a manual validation
limit, not a claimed test result. No additional discovered issues were recorded.
Changes remain uncommitted; no push or deployment was performed.
