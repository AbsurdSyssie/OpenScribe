# Discovered issues

## SPLIT-PREFERENCE-BROWSER-CHECKBOX-CLICK — 2026-10-01

- Discovery commit: `32eaf7caf2f6a26929195ee1d06e8f9b2dab97d7`.
- Location: `tests/test_consultation_split_browser.py:252`, `test_browser_team_leader_split_preference_persists_across_workspace_navigation`; `app/static/css/components.css:208-209`, compact switch styling.
- Exact excerpt: `split_toggle.check()` targets the 1px checkbox while `.switch-compact__track` occupies the visible switch area.
- Observed problem: Playwright times out because the track intercepts pointer events. The test failed the same way on its own rerun; the other four browser tests passed.
- Likely impact: this browser regression cannot verify preference persistence; the styled label may still respond to ordinary user clicks.
- Not fixed during the note-split confirmation change because the preference control and its test are outside the affected flow.
- Follow-up: test the accessible switch through its visible label or adjust the control's hit target, then rerun this browser regression.

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
