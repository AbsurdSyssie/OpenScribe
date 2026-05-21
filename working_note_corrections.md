# Working Note Corrections Critique

Review target: latest `working_note` branch suggestions.

## Keep / Modify

1. Rename duplicate close-modal flag.

   Valid. `noteGenerationShouldCloseDictationModal` was correct but subtle because later duplicate callers can promote the in-flight request to close the modal. Kept behavior, renamed to `noteGenerationCloseDictationAfterCurrentRequest` instead of adding a broader options object.

2. Extract note-save baseline handling.

   Valid. Dirty Working-note optimistic-lock baseline is important enough to isolate from DOM/editor code. Implemented tiny pure helpers in `noteSaveState.js`; app code now captures and resolves save baselines through those helpers.

3. Add focused dirty timestamp regression coverage.

   Valid. Added Node tests for preserved dirty Working-note baseline after a newer workspace refresh and for empty baseline serialising as `null`.

4. Duplicate generation with different template while in flight.

   Behavior remains first request wins. This is acceptable because template select, picker, and dictation template switching are disabled during `noteGenerationBusy`. Static coverage keeps those locks explicit.

## Reject / Delete

1. Typed enqueue return result.

   Rejected for now. `enqueueTemplateGeneration()` has only two callers and already returns `true`, `false`, or throws. Object results would add caller churn without fixing current behavior. Revisit only if more failure reasons appear.

2. Per-call generation options object.

   Rejected. Only close-dictation intent needs merge semantics. Boolean promotion is smaller and clearer after rename.

3. Broader enqueue extraction.

   Rejected. Request/UI flow has one app-owned success path. Splitting now would add names without reducing live risk.

## Tests To Keep

- `tests/test_note_save_state_js.py`: pure baseline behavior for dirty Working-note edits.
- Static frontend regression: generation in-flight state locks template controls.
- Static frontend regression: duplicate generation calls preserve dictation-modal close intent.
- Syntax checks: `node --check app/static/js/transcribe/app.js`, `node --check app/static/js/transcribe/actions.js`, `node --check app/static/js/transcribe/noteSaveState.js`.

## Architecture Checkpoints

- Schema: no migration/schema change.
- Auth/ownership: no route or permission change; owner-only server paths unchanged.
- Lifecycle/deletion: no retention, clear, cascade, or hard-delete behavior changed.
- Provider/privacy: generation request still sends `template_id` only; transcript-derived sources still load/redact server-side.
- Structured notes: EMIS section contract unchanged.
