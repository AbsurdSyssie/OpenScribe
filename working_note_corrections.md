# Working-note correction critique

## Decisions

1. Keep: explicit dirty Working-note timestamp sentinel.

Reason: `""` means editing began before any saved Working note existed. `||` fallback could silently advance the save baseline after another tab creates the first note, causing overwrite instead of `409`.

Outcome: Working-note save payload now uses `dirtyNoteExpectedUpdatedAt !== null` for dirty same-target edits, then sends `null` when baseline was no saved note.

2. Keep, narrowed: central generation success handling in `app.js`.

Reason: normal Generate and dictation Save & generate had duplicate post-queue behavior and could drift. `app.js` owns enqueue state and selected-note reset, so it should own shared post-success UI flow too.

Outcome: `enqueueTemplateGeneration()` now calls one `handleTemplateGenerationQueued()` helper. Normal Generate only triggers enqueue. Dictation passes `closeDictationModal: true`.

3. Keep: remove unused `silent` parameter.

Reason: dead parameter adds misleading API surface with no behavior.

Outcome: `saveWorkingNoteBeforeGeneration()` takes no options.

## Rejected / modified

- Rejected broad action API redesign beyond what was needed. `actions.js` still owns form events; `app.js` owns enqueue request and shared post-success effects.
- Did not change server API, schema, redaction, provider, ownership, or deletion behavior.

## Architecture checkpoints

- Privacy: generation request remains `template_id` only; no transcript-derived content added to client payloads or logs.
- Ownership: no route/auth changes; server still resolves saved transcript/Working-note sources for owner.
- Deletion: no lifecycle/cascade/retention changes.
- Provider: no provider selection, credential, redaction provider, or LLM payload contract changes.
- Structured note: EMIS section contract unchanged.
