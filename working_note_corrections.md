# Working Note Corrections Critique

Review target: latest Working-note enqueue/concurrency changes on `working_note` branch.

## Keep And Implement

1. Generated-note save baseline must be target-scoped.

   The original suggestion is valid. Working-note saves already guard `dirtyNoteExpectedUpdatedAt` by `dirtyNoteTargetId`; generated-note saves reused the dirty timestamp without checking target. If a preserved dirty editor and workspace refresh leave stale target state, a generated-note save could send another note's optimistic-lock baseline.

   Decision: keep, but implement through one small helper: `noteSaveExpectedUpdatedAtForTarget(targetId)`. Both Working-note and generated-note branches now use same target guard.

2. Template controls should lock while generation is in flight.

   The original suggestion is valid. Backend request snapshots `template_id`, but allowing selector/picker changes during an in-flight enqueue makes UI state lie about which template was queued.

   Decision: keep and broaden slightly. Disable global template select, custom picker button, picker options, and dictation template switching while `noteGenerationBusy` is true. Close picker if generation starts while open.

3. Duplicate generation calls should merge modal-close intent.

   The original concern is valid but low risk. Returning one in-flight promise is correct for duplicate suppression, but a second caller with `closeDictationModal: true` should not lose that intent.

   Decision: keep with minimal state, not a larger option object. Track `noteGenerationShouldCloseDictationModal`; any duplicate call can promote it to true before shared promise resolves.

4. Remove unused `syncGenerationAvailability` action parameter.

   Valid low-risk cleanup. `actions.js` accepted parameter but did not use it.

   Decision: remove from action signature and call site.

## Modify / Narrow

1. Extract expected timestamp logic.

   Keep only because it now removes real duplicated target-guard logic. Do not add broader save-request abstraction.

2. Duplicate-generation tests.

   Keep static regression coverage for current frontend layout. Direct JS behavioral tests remain better future work once enqueue logic is extracted from large DOM module.

## Reject / Delete

1. Split `enqueueTemplateGeneration()` into request and UI finalisation helpers.

   Rejected. Current helper centralizes one app-owned flow. Splitting now adds names and call choreography without a second real caller needing different behavior.

2. Replace `noteGenerationInFlight` and `noteGenerationBusy` with a state object.

   Rejected. Current pair is small and explicit. A state object would not reduce enough risk to justify churn.

3. Add broad in-flight option metadata object.

   Rejected. Only option needing merge semantics is modal close. Boolean promotion is clearer and smaller.

## Tests To Keep

- Static frontend regression: generated-note saves use target-scoped baseline helper.
- Static frontend regression: template select/picker lock while `noteGenerationBusy`.
- Static frontend regression: duplicate generation calls preserve dictation-modal close intent.
- Static frontend regression: unused `syncGenerationAvailability` action wiring removed.
- Syntax checks: `node --check app/static/js/transcribe/app.js` and `node --check app/static/js/transcribe/actions.js`.

## Architecture Checkpoints

- Schema: no migration/schema change.
- Auth/ownership: no route or permission change; owner-only server paths unchanged.
- Lifecycle/deletion: no retention, clear, cascade, or hard-delete behavior changed.
- Provider/privacy: generation request still sends `template_id` only; transcript-derived sources still loaded/redacted server-side.
- Structured notes: EMIS section contract unchanged.
