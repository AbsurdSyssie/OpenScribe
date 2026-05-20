## Working-note correction critique

### 1. Template-change protection path

Verdict: keep, but modify.

`handleOutputTemplateChange()` is wired through `attachTranscribeActions()` and is called from `actions.js`. The original critique that the handler is never invoked is stale.

The real problem is narrower: `app.js` still had an eager `generateOutputTemplateSelect` listener that called `syncTemplatePickerUi` before the guarded async handler. Picker helpers also dispatched `change` and then called `syncTemplatePickerUi` directly. Those eager syncs could update template UI before a dirty Working note save succeeded or failed.

Fix kept:

- Remove direct `generateOutputTemplateSelect?.addEventListener('change', syncTemplatePickerUi)` from `app.js`.
- Remove direct picker/dictation-modal `syncTemplatePickerUi()` calls after dispatching `change`.
- Keep `actions.js` as the single owner of template-change policy: save dirty Working note, discard never-saved empty draft, revert on failed save, avoid mode-changing locked Working note.

Regression kept:

- Assert no eager `syncTemplatePickerUi` listener remains in `app.js`.
- Assert picker helpers dispatch `change` without immediate direct sync.
- Keep existing static assertions that guarded handler is present and `actions.js` calls it.

### 2. Dictation-only note generation

Verdict: already implemented, keep.

Server-side generation already allows an empty transcript snapshot when saved dictation exists. Workspace Create button availability also counts saved dictation. Existing focused tests cover both API generation and UI Create enablement.
