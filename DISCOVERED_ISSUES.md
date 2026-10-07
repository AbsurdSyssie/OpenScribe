# Discovered issues

## API-AUDIT-SHARED-INFRASTRUCTURE-LOCK — 2026-10-05

- Discovery commit: unavailable (working tree investigation).
- Location: `scripts/audit_api_auth.py`, `main` and `reset_public_schema`.
- Exact excerpt: `reset_public_schema()` followed by `rate_limit_redis.flushdb()`.
- Observed problem: the standalone API audit resets the canonical test database and limiter store without acquiring the `/tmp/openscribe_pytest.lock` used by pytest.
- Likely impact: running the audit beside an active pytest process can reset that process's shared test infrastructure. Static tracing established the gap; no concurrent destructive run was attempted.
- Not fixed during the Multiple problems toggle because changing audit infrastructure is separate work. Audit verification remains pending while the shared lock is occupied.
- Follow-up: share the existing test-run lock with the audit before any infrastructure initialization or reset, then add an isolated contention regression.
- Resolved 2026-10-07: pytest and the API audit share the nonblocking lock helpers in `tests/db_utils.py`. Both acquire the lock before database creation; the audit holds it through final Redis cleanup and exits with code `2` when busy. An isolated subprocess regression verifies that contention causes no database initialization, schema reset, or Redis flush.

## SPLIT-GENERATION-STATUS-HIDDEN — 2026-10-05

- Discovery commit: unavailable (working tree investigation).
- Location: `app/static/js/transcribe/app.js`, `createSplitGenerateController` setup.
- Exact excerpt: `setStatus: () => {},`
- Observed problem: terminal split-generation status messages, including a rejected or failed one-note continuation, are discarded by the workspace integration.
- Likely impact: a clinician may not receive the controller's safe actionable failure message after the dictation modal closes.
- Not fixed during the queued-intent restoration repair because presenting and deduplicating split lifecycle feedback is a separate workspace feedback contract.
- Follow-up: define a visible, accessible, non-noisy status surface for terminal split-generation failures and add browser coverage for rejected continuation feedback.
- Resolved 2026-10-07: the workspace now forwards split-generation `warning` and `error` statuses to the existing accessible flash banner via `showFlash`; controller coverage continues to assert safe terminal guidance and rejected continuation feedback.

## SPLIT-PREFERENCE-BROWSER-CHECKBOX-CLICK — 2026-10-01

- Discovery commit: `32eaf7caf2f6a26929195ee1d06e8f9b2dab97d7`.
- Location: `tests/test_consultation_split_browser.py:252`, `test_browser_team_leader_split_preference_persists_across_workspace_navigation`; `app/static/css/components.css:208-209`, compact switch styling.
- Exact excerpt: `split_toggle.check()` targets the 1px checkbox while `.switch-compact__track` occupies the visible switch area.
- Observed problem: Playwright times out because the track intercepts pointer events. The test failed the same way on its own rerun; the other four browser tests passed.
- Likely impact: this browser regression cannot verify preference persistence; the styled label may still respond to ordinary user clicks.
- Not fixed during the note-split confirmation change because the preference control and its test are outside the affected flow.
- Follow-up: test the accessible switch through its visible label or adjust the control's hit target, then rerun this browser regression.
- Resolved 2026-10-07: inspection at `fd599225` confirms the browser regression now clicks the visible preference label instead of calling `.check()` on the hidden checkbox. Browser execution is recorded separately from this code verification.

## Stale Scribe stylesheet cache key assertion — 2026-09-24 (resolved 2026-09-29)

- Discovery commit: `118ed37`.
- Location: `tests/test_workspace_scribe_layout_contract.py`, `test_scribe_mobile_dictation_modal_owns_the_top_layer_and_safe_viewport`.
- Assertion: `assert 'transcribe.css?v=20260911-note-regeneration-layer-fix' in read("app/templates/transcribe/_head_assets.html")`.
- Observed problem: the template at HEAD uses `transcribe.css?v=20260920-session-sidebar-timestamp-skeleton`, so this unrelated contract test fails despite no change to that stylesheet link in the recording-lock work.
- Impact: the broader workspace layout contract run reports a failure unrelated to the behavior under change.
- Not fixed during the recording navigation lock change because stylesheet cache-key maintenance was outside its scope.
- Resolved by updating the assertion to match the maintained asset key in `tests/test_workspace_scribe_layout_contract.py`.

## SPLIT-SOURCE-ONLY-MATERIALIZATION — 2026-09-26 (resolved 2026-09-27)

- Discovery commit: `118ed375caefb73e42617412b00a8e001622602c` (inspected current working tree).
- Location: `app/services/consultation_split_partial.py:167-169`, `_materialize_available_split_notes_locked`.
- Exact excerpt: `transcript_version_id=batch.analysis.transcript_version_id`.
- Observed problem: Working-note-only and dictation-only analyses intentionally have a null transcript version. Confirmation binds an encrypted empty version to the batch instead. Ordinary split generation uses that materialization version, but Keep Available and verification finalization use the analysis version through this shared materializer.
- Likely impact: inserting a source-only child document violates the non-null `GeneratedDocument.transcript_version_id` constraint and rolls back materialization. Established by static tracing; not reproduced in a test during this inspection.
- Resolved by `consultation_split_materialization.require_batch_materialization_version`, used before all split document materialization. Verification now terminalizes a missing or wrong-root binding with existing failed/unchecked states after settling or cancelling its attempt, instead of rolling back and retrying finalization. Working-note-only and dictation-only Keep Available and verification-finalization regressions prove the confirmation-bound version is used.

## SPLIT-INTENT-PREFERENCE-OPTOUT-VISIBILITY — 2026-10-06

- Discovery commit: unavailable (working tree review).
- Location: `app/services/consultation_split_gates.py`, `analysis_split_enabled` and `intent_split_enabled`; `app/services/consultation_split_api.py`, `read_workspace_split_analysis`/`read_workspace_split_intent`; `app/services/consultation_split_drafts.py`, `read_split_draft`; `app/services/consultation_split_confirmation.py`, `confirm_split_draft`.
- Observed problem: an already accepted automatic-preference intent continues server-side after the owner opts out, but current analysis/draft projection and explicit confirmation still use the existing preference gates. With the automatic preference off, `analysis_split_enabled` permits only an accepted manual-review intent; otherwise workspace analysis is omitted, an active fresh draft can be projected stale, and `intent_split_enabled` rejects confirmation.
- Likely impact: a ready draft created by durable server progress can be hidden/unreviewable after opt-out while the deployment gate remains available. This is an existing gate boundary, separate from Create navigation durability.
- Not changed during durable Create continuation because this task preserves current preference/deployment authorization for workspace and clinician review actions; broadening accepted-work authorization needs an explicit product/security decision.
- Follow-up: decide whether an exact accepted intent should retain workspace visibility and clinician confirmation authority after preference opt-out, then update the gate helpers and focused authorization/workspace tests together.
- Resolved 2026-10-07 under the supplied plan: preference opt-out blocks new unmarked automatic intents but preserves the exact accepted intent through analysis, draft review, confirmation, and batch access. Deployment availability, owner/team scope, source freshness, and retention still apply. A real-runtime regression reproduces the old rejection and verifies the accepted-work flow after opt-out; focused progress, runtime, and Multiple problems tests pass.

## TRANSCRIPT-COMBINED-PATCH-DROPS-EARLY-FIELDS — 2026-10-07

- Discovery commit: `88e487718e627643d9c3f8a99609f137bacd0a54`.
- Location: `app/services/transcripts.py`, `update_transcript`.
- Exact excerpt: title and ingestion-mode assignments occur before `_lock_split_source_writer_transcript(...)`, whose query reloads the same row with `populate_existing=True` while autoflush is disabled.
- Observed problem: when one transcript PATCH combines `structured_context_json` with a title or ingestion-mode change, the source-writer reload replaces those earlier in-memory assignments with database values before commit.
- Likely impact: the request can succeed while silently dropping its title or ingestion-mode update. The Multiple problems marker had the same reload ordering risk, but this change now assigns that new marker after the reload and covers the combined marker/structured-context case.
- Not fixed during the Multiple problems toggle because title and ingestion-mode update ordering is older, unrelated behavior and broadening the patch would expand scope beyond the new marker contract.
- Follow-up: preserve validated title and ingestion-mode changes across the source-writer reload, then add focused combined-PATCH regressions for each field.
- Resolved 2026-10-07: `update_transcript` acquires the source-writer lock before applying metadata for structured-context requests. Independent title and ingestion-mode PATCH regressions reproduce the dropped fields on the old ordering and pass with the fix; metadata-only requests retain the lighter owner lookup.
