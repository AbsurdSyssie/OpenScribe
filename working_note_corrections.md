## Working-note correction plan

## Implementation status

Implemented.

- Dirty-empty, never-saved Working note drafts now discard locally and no longer block generation or note switching.
- Dirty-empty edits after a saved Working note still block until explicit Clear/DELETE.
- Template changes now go through a guarded handler; dirty Working notes save first, failed saves revert the template selection, and locked saved Working notes do not re-render their editor.
- Server-rendered Create now uses `active_template_generation_input_available`, separate from the broader follow-up/quick-action `active_note_input_available`.

Focused tests run:

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "working_note or create_button or transcribe_workspace_static"`
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_static_asset_version_bumped_for_pii_source_visibility"`
- `.venv/bin/pytest -q tests/test_api.py -k "working_note or dictation_only_session_before_provider_call"`

## Critique result

Keep the three suggested fixes, but narrow them:

- Fix 1 is valid, but only the dirty-empty, never-saved Working note should be silently discarded. Dirty-empty after saved content must keep blocking generation/switching until the user clears the Working note, otherwise the backend may generate from stale saved content.
- Fix 2 is valid, but "save first" is too broad. Template changes should not silently mode-switch or re-render a dirty/locked Working note editor. Preserve the editor where possible; only re-render unlocked clean/empty Working notes.
- Fix 3 is valid, but do not reuse the broad follow-up/quick-action availability flag for template generation. Add or use a template-generation-specific source flag so generated-note content does not accidentally enable Create.

Already-fixed items from earlier review should stay deleted from this plan:

- working-note `PATCH` without `expected_updated_at` already returns `409` once `working_note_updated_at` exists, with API coverage.
- structured Working note persistence already serializes visible unchecked lines the same way generated structured notes do.

## Accepted fixes

1. High - dirty-empty new Working note can trap the user

A new, unsaved Working note can become dirty and empty. Example: user types into Working note, deletes everything, then tries to switch notes or generate.

Current behavior:

- `saveWorkingNoteBeforeGeneration()` throws whenever current Working note is dirty and `workingNoteHasContent()` is false.
- note switching calls `persistNoteEditsSilently()` before changing selection and aborts if save fails.
- backend correctly rejects empty Working-note `PATCH` requests.
- `clearWorkingNote()` returns early if there is no saved Working note and the current editor has no content.

This protects stale saved content, but it also traps a dirty-empty Working note that has never been saved.

Fix:

- Add a small client helper for "discardable empty Working-note draft": current target is Working note, editor is dirty, `workingNoteHasContent()` is false, and `activeWorkingNote?.mode` is absent.
- In generation pre-save and note-switch pre-save paths, clear dirty state and allow the action for that discardable case.
- Keep the existing block for dirty-empty Working note when `activeWorkingNote?.mode` exists. User must use Clear/DELETE so stale saved Working-note content cannot feed generation.
- Make Clear action clear local dirty state for discardable empty drafts without calling DELETE; call DELETE only when saved Working-note content/mode exists.

Tests:

- browser/static JS test or focused UI test proving dirty-empty never-saved Working note does not block note switching/generation.
- regression assertion that dirty-empty after a saved Working note still blocks generation until Clear.

2. High - template change can discard unsaved Working-note edits

The template change handler calls:

```js
structuredEditor.syncStructuredTemplateUi();
```

When current rendered document is the Working note, `syncStructuredTemplateUi()` can call `renderGeneratedOutput(nextDocument, {})`. That render path clears editor panels and re-renders from `currentRenderedDocument`, not the live DOM draft. Unsaved Working-note edits can disappear.

Fix:

- Add a template-change guard before destructive template UI sync.
- If current target is a dirty Working note with content, persist the current Working note first and continue only on success, or block/revert template change with a clear message.
- If current target is a dirty-empty never-saved Working note, clear dirty state and allow re-render.
- If current target is a locked saved Working note, do not re-render the note editor on template change. Update template picker/badge UI only; `activeWorkingNote.mode` remains the Working-note editor mode.
- Keep generated-note template preview behavior unchanged.

Tests:

- JS-focused test for changing template while dirty Working note is rendered: no editor wipe unless save succeeds.
- static regression check that the template change handler uses the guard instead of directly calling `syncStructuredTemplateUi()`.

3. Medium - server-rendered Create button ignores saved Working note and dictation

Initial HTML disables Create with this condition:

```jinja
not active_transcript.current_draft_text and not active_structured_context
```

That misses saved freeform Working note and saved post-consultation dictation. Client-side `syncGenerationAvailability()` can correct it later, but initial render can show Create disabled when template generation is valid.

Fix:

- Add a backend-provided `active_template_generation_input_available` boolean, or equivalent, containing only template-generation sources:
  - transcript draft text
  - structured Working note/context
  - freeform Working note
  - saved dictation text
- Use that boolean for the server-rendered Create button.
- Keep the existing broader `active_note_input_available` semantics separate for follow-up/quick-action UI unless those flows are intentionally changed and tested.

Tests:

- server-rendered transcribe page with saved freeform Working note and empty transcript has enabled Create.
- server-rendered transcribe page with saved dictation and empty transcript has enabled Create.
- empty transcript with only an old generated note does not enable Create unless another valid template-generation source exists.
