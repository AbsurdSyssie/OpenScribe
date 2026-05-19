## Working-note correction plan

This list is pruned after code review. Goal is lower debt without turning the working-note slice into a broad renderer rewrite.

### Keep now

#### 1. Split working-note save-before-generation from structured-context persistence

Current problem: `saveStructuredContext()` now means "persist active working note before generation" when the selected note target is `working:*`. That name is stale and the helper is used as a generation gate. The dictation generation path also catches and ignores failures:

```js
try {
  await saveStructuredContext({ silent: true });
} catch (_) {}
```

Fix:

```js
saveStructuredContext()
saveWorkingNoteBeforeGeneration()
```

Generation entry points must call `saveWorkingNoteBeforeGeneration()` and must block generation when a dirty working note cannot be saved. Do not swallow that failure in dictation.

Expected tests:

- note generation blocks when dirty working-note save fails
- dictation save-and-generate blocks when dirty working-note save fails
- generated-note generation still does not try to save a working note

Architecture checkpoint:

- preserves owner-only note access through existing endpoints
- does not alter deletion, provider resolution, or EMIS section contract

#### 2. Centralise note target IDs

Current problem: `workingNoteTargetId()` / `isWorkingNoteTargetId()` live in `app.js`, while `workingNoteDocumentId()` lives in `documents.js`. Same identifier concept has two implementations.

Fix: add small shared helper:

```js
// app/static/js/transcribe/noteTargets.js
export const workingNoteTargetId = (transcriptId = '') => `working:${transcriptId || ''}`;
export const isWorkingNoteTargetId = (targetId = '') => String(targetId || '').startsWith('working:');
export const generatedNoteTargetId = (documentId = '') => documentId || '';
```

Use in `app.js` and `documents.js`. Keep helper intentionally tiny; do not move selection policy into it.

Expected tests:

- update existing JS/source-shape tests that assert old inline helpers
- keep navigator tests covering working-note fallback selection

#### 3. Move note editor save serialization into `structured.js`

Current problem: `buildNoteSaveRequest()` in `app.js` walks structured/freeform DOM directly. `structured.js` already owns editor draft sync, row shape, selection, and empty-line filtering. Serialization belongs with editor state.

Fix: export one editor method from `createStructuredEditor()`:

```js
serializeCurrentNoteEditor()
```

Return:

```js
{ mode: 'structured', sections: [...] }
```

or:

```js
{ mode: 'freeform', edited_output_text: '...' }
```

Then `app.js` only adds endpoint, target ID, and `expected_updated_at`.

Important detail: serializer should call existing draft sync helpers before reading state:

- `syncGeneratedStructuredDraftFromDom()`
- `syncGeneratedFreeformDraftFromDom()`

Expected tests:

- structured generated-note edit payload preserves section keys/order/text
- freeform generated-note edit payload preserves edited text
- working-note structured/freeform payload uses same serializer path where possible

#### 4. Centralise synthetic working-note document shape

Current problem: `documents.js` builds a virtual editor document inline. This shape is now a contract between navigator, renderer, save logic, and tests.

Fix:

```js
export function workingNoteToEditorDocument({ transcriptId, workingNote, selectedTemplateMode }) {
  ...
}
```

Use inside `documents.js`; tests may import/use it directly if useful.

Keep it in the document/navigation layer, not in backend-facing code. It is a UI adapter, not an API model.

Expected tests:

- virtual document ID uses shared `workingNoteTargetId`
- freeform working note maps to `document_mode: "freeform"`
- structured working note maps sections with stable keys/order/text
- no saved mode means current selected template mode controls editor mode

### Keep, but lower priority

#### 5. Let `renderSelectedNote()` own preserve checks

Current problem: `applyWorkspacePayload()` computes `preserveDirtyNoteEditor`, then `renderSelectedNote()` repeats `shouldPreserveNoteEditorRender()`. This is not runtime-expensive, but it adds decision noise.

Fix: change API to:

```js
renderSelectedNote({ forcePreserveEditor = false } = {})
```

Callers normally do not precompute preservation. Only pass `forcePreserveEditor` for exceptional UI flows.

Expected tests:

- workspace refresh preserves focused/dirty working-note editor
- switching to a different note still saves or blocks before render

#### 6. Extract backend working-note timestamp conflict helper

Current problem: `save_working_note()` includes timestamp normalization/conflict logic inline.

Fix:

```py
def _assert_working_note_update_current(transcript, expected_updated_at):
    ...
```

Then `save_working_note()` reads as policy steps:

```py
_assert_working_note_update_current(transcript, payload.expected_updated_at)
_assert_working_note_mode_can_change(...)
_save_freeform_working_note(...) or _save_structured_working_note(...)
```

Keep behavior identical. This is maintainability only.

Expected tests:

- existing 409 stale-working-note test must still pass
- add one timezone-normalization case only if not already covered

### Defer / do not require for this correction slice

#### 7. Renderer churn reduction in `structured.js`

Original idea: avoid clearing both structured and freeform panels before rendering one.

Decision: defer. It may help focus stability, but renderer currently has coupled copy-review, empty-state, toolbar, draft, and focus restore behavior. Changing clear/hide order risks subtle regressions. Do only with browser-level coverage around focus preservation, copy-review gates, empty states, and template-mode switching.

#### 8. Copy-review state API redesign

Original idea:

```js
renderNoteEditor(document, { copyReviewRequired: document.kind !== 'working_note' })
```

Decision: defer. Making copy-review policy explicit is good, but it is a renderer API redesign rather than a working-note debt fix. Keep current global state unless a focused copy-review refactor is planned with tests.

## Revised priority order

1. Add `saveWorkingNoteBeforeGeneration()` and remove swallowed dictation failure.
2. Add shared `noteTargets.js`.
3. Move note save serialization into `structured.js`.
4. Add `workingNoteToEditorDocument()`.
5. Simplify note render preservation API.
6. Extract backend timestamp conflict helper.

Items 7-8 are not expected fixes now.
