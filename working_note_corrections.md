# Working-note correction critique

## Kept

1. Snapshot `transcriptId` before async Working-note save.

Reason: low-probability race, cheap fix. Generation now uses `generationTranscriptId` for validation and POST URL after `await saveWorkingNoteBeforeGeneration()`.

2. Return existing in-flight generation promise on duplicate submit.

Reason: cleaner than silent `false`, avoids second POST, and lets duplicate callers observe same success/error. No warning toast added; disabled controls already provide normal feedback.

3. Keep removed `silent` option deleted.

Reason: helper has one behavior. No fake API surface.

4. Inline `handleTemplateGenerationQueued()`.

Reason: one caller only. Inlining removes function hop without changing post-success flow.

## Modified / rejected

- Rejected removing final `syncGenerationAvailability()` / `syncDictationControls()` in `finally`. It is partly redundant after successful `fetchWorkspace()`, but still protects failed save, failed POST, thrown refresh, and no-op paths.
- Did not add a new JS harness test. Current `app.js` is a large DOM module not shaped for isolated enqueue testing; adding a brittle VM harness would increase debt. Added focused static assertions for the new guard/snapshot behavior instead. Future debt fix: extract generation enqueue into a small module, then add behavioral Node tests.
- Did not change server idempotency. This is client-side race hardening only.

## Architecture checkpoints

- Privacy: generation request remains `template_id` only; no transcript-derived content added to client payloads or logs.
- Ownership: no route/auth changes; server still resolves saved transcript/Working-note sources for owner.
- Deletion: no lifecycle/cascade/retention changes.
- Provider: no provider selection, credential, redaction provider, or LLM payload contract changes.
- Structured note: EMIS section contract unchanged.
