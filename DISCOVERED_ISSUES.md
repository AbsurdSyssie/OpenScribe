# Discovered issues

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
