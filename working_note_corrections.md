## Working-note correction critique

Status: reviewed and applied on 2026-05-21.

### Kept

1. Replace fixed 3 second Generate guard with in-flight state.

Reason: timer guard can expire while slow Working-note save or `/generate-output` enqueue still runs. Correctness must follow request lifecycle, not wall-clock debounce.

Applied rule:

```js
if (noteGenerationInFlight) return false;
```

2. Share template-generation enqueue path across normal Generate and dictation Save & generate.

Reason: both paths save current Working note, then post same `template_id` payload. One helper avoids drift and blocks accidental second enqueue from either entry point while first request is active.

3. Remove dataset-backed Generate guard state.

Reason: DOM `dataset` was duplicating JS state. `noteGenerationBusy` now drives button disabling directly.

4. Remove remaining structured-context autosave no-op path.

Reason: saved Working note is generation source. `persistStructuredContextSilently`, `emisSaveTimer`, and `lastSavedStructuredContext` no longer saved anything useful and confused ownership of structured editor changes.

5. Centralise request body construction.

Reason: `JSON.stringify({ template_id: templateId })` now exists only in `app.js` helper. `actions.js` no longer constructs generation payloads.

### Modified

1. Dropped optional UI debounce.

Reason: in-flight guard already disables normal Generate and dictation Save & generate until settle. Extra timer would add second state source without extra correctness.

2. Kept helper frontend-only.

Reason: this slice fixes client duplicate-submit behavior. Backend idempotency/rate limiting remains separate hardening if product wants cross-tab or malicious-client protection.

### Checkpoints

- Schema checkpoint: no DB/migration change.
- Auth/ownership checkpoint: no route/auth change; generation still uses owner-scoped transcript API.
- Lifecycle/deletion checkpoint: no retention, cascade, or clear/delete change.
- Docs/tests checkpoint: working-note docs, progress note, static frontend regression checks updated.
