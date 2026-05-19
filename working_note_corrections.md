## Diagnosis

I found the `working_note` branch in `AbsurdSyssie/OpenScribe`. The backend work is broadly aligned with the feature spec, but the frontend has drifted from the existing generated-note editor contract.

The existing generated-note editor already has the behaviour you want to reuse: dirty tracking, document identity tracking, edit-version checks, save timers, in-flight/queued save handling, conflict handling, focus-preserving render guards, on-blur immediate save, and pagehide keepalive save. Those live primarily in `app/static/js/transcribe/app.js`, with rendering/focus mechanics in `structured.js` and selection switching in `documents.js`.   

The branch partially reuses `structured.js`, but it adds a parallel working-note path: `activeWorkingNote`, `workingNoteDirty`, `lastSavedWorkingNoteSerialized`, `activeEditorSource`, `renderWorkingNote`, and special `kind === "working_note"` save branching. That means the working note is *rendered in the same visual area* but is not truly a first-class target of the existing note editor state machine.    

The fix should be: **model “Working note” as a synthetic note document target inside the existing note selector/editor**, not as a separate editor mode.

This matches the branch’s own implementation document: it says the workspace should render Working note as a virtual note version in the existing note-builder editor, using the same freeform/sectioned line editor, with only the save endpoint differing.  It also matches the uploaded feature spec: one living working note per transcript, autosave/on-blur persistence, mode lock on first non-empty save, generation saves first, and generated notes snapshot the working-note input. 

---

## Target design

### Core rule

There should be **one note editor state machine**.

It should not know whether it is saving a generated note or a working note except through a small “save adapter”.

Use a target identity such as:

```text
generated:<generated_document_id>
working:<transcript_id>
```

Then all existing logic becomes target-agnostic:

```text
noteEditorDirty
dirtyNoteTargetId
noteEditVersion
noteSaveTimer
noteSaveInFlight
noteSaveQueued
noteSaveConflictShown
shouldPreserveNoteEditorRender()
persistNoteEditsSilently()
scheduleNoteAutosave()
```

The working note becomes just another selected note target, with different persistence.

---

## Frontend plan

### 1. Make Working note a synthetic document in `documents.js`

In `app/static/js/transcribe/documents.js`, build the note selector from:

```text
[Working note synthetic item] + generated template documents
```

The synthetic item should look enough like a generated note for the existing render path to consume it:

```js
{
  id: `working:${transcriptId}`,
  kind: 'working_note',
  title: 'Working note',
  status: 'ready',
  document_mode: activeWorkingNote?.mode || selectedTemplateMode,
  edited_output_text: activeWorkingNote?.freeform_text || '',
  sections: activeWorkingNote?.structured_note?.sections || {},
  updated_at: activeWorkingNote?.updated_at || '',
}
```

Do **not** create a second panel or a second editor. The selector should call the same `selectDocumentFromUi`, `renderSelectedNote`, switch guard, and pending-save code that generated notes already use. The existing progress notes explicitly say the selection guard preserves dirty generated/follow-up edits and blocks unsafe switching on failed/conflicted saves; working note should use that same guard. 

### 2. Remove the branch’s separate working-note editor source

From `app/static/js/transcribe/app.js` and `structured.js`, remove or collapse:

```js
activeEditorSource
currentWorkingNote
workingNoteDirty
lastSavedWorkingNoteSerialized
renderWorkingNote()
setWorkingNoteStatus()
hasProtectedWorkingNoteEditor()
```

Replace them with general note-target logic.

Current branch code sets `dirtyNoteDocumentId = 'working_note'` and branches throughout the note save path.  That should become a normal target ID:

```js
dirtyNoteTargetId = currentRenderedNoteTargetId();
```

### 3. Generalise save payloads, not save behaviour

Keep the existing `persistNoteEditsSilently()` behaviour almost unchanged. The only new abstraction should be payload construction:

```js
const buildNoteSaveRequest = () => {
  const target = currentNoteTarget();

  if (target.kind === 'working_note') {
    return {
      targetId: target.id,
      endpoint: `/api/v1/transcripts/${transcriptId}/working-note`,
      method: 'PATCH',
      payload: buildWorkingNotePayload(),
      responseKind: 'working_note',
    };
  }

  return {
    targetId: target.id,
    endpoint: `/api/v1/generated-documents/${target.generatedDocumentId}`,
    method: 'PATCH',
    payload: buildGeneratedDocumentPayload(),
    responseKind: 'generated_document',
  };
};
```

Everything else should stay identical:

```text
if save in flight -> queue
capture requestVersion
PATCH
409 -> show conflict and keep dirty editor visible
non-OK -> show error and keep dirty editor visible
success -> update target updated_at
clear dirty only if requestVersion still current
finally -> run queued save
```

The generated-note code already does this correctly using `expected_updated_at`, save-version checks, dataset updates, and conflict handling. 

### 4. Add optimistic concurrency to working-note saves

The branch’s generated-document editing path already uses `expected_updated_at`; working-note PATCH should mirror that. The current working-note service stores `working_note_updated_at`, but the save payload does not appear to use the same optimistic conflict contract. 

Add to `WorkingNoteUpdate`:

```py
expected_updated_at: datetime | None = None
```

Then in `save_working_note`:

```py
if transcript.working_note_updated_at and payload.expected_updated_at:
    if transcript.working_note_updated_at != payload.expected_updated_at:
        raise AppError(409, "conflict", "Working note changed elsewhere. Reload before saving again.")
```

For an empty/unlocked working note, allow `expected_updated_at = null`.

This lets working note use the same conflict behaviour as generated notes.

### 5. Reuse `structured.js` rendering without a working-note mode flag

`structured.js` already contains the reusable note writer primitives: statement rows, autosize, focus capture/restore, structured/freeform rendering, row selection, line add/delete, keyboard navigation, and `onNoteEditorChanged`.   

Instead of adding `renderWorkingNote()`, make the existing render function accept a document-like editor target. Either:

* keep `renderGeneratedOutput(target, structuredContext)` but pass a working-note-shaped object, or
* rename it to `renderNoteEditorTarget(target, structuredContext)`.

The internal row/focus/autosize code must not branch on working note vs generated note. Only copy-review rules may need a flag, because working note is not generated output and should not require “scroll before copy”.

### 6. Make focus preservation target-based

Current master protects generated-note focus during workspace refresh with:

```js
shouldPreserveNoteEditorRender(nextSelectedNoteDocumentId)
```

and `applyWorkspacePayload()` passes `preserveEditor` into `renderSelectedNote`. 

Change this to:

```js
shouldPreserveNoteEditorRender(nextTargetId)
```

where `nextTargetId` can be:

```text
working:<transcript_id>
generated:<generated_document_id>
```

This avoids special-casing “working note focused” and makes focus-breaking autosave behave identically.

### 7. Make generation save the working note first

For `app/static/js/transcribe/actions.js`, generation submit should do:

```js
const saved = await persistNoteEditsSilently({ immediate: true });

if (workingNoteTargetHasDirtyOrFailedSave && !saved) {
  showFlash('Save the working note before generating.', 'error');
  return;
}
```

Important: do **not** send working-note content in the generation request. The branch’s backend already loads saved working-note state from the DB and snapshots it during generation. 

Generation should block only when the working-note editor has unsaved or failed-save state, as the uploaded spec requires. 

### 8. Mode locking UX

Use the selected template mode only while the working note is empty/unlocked.

Rules:

| State                                          | UI behaviour                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- |
| No working-note content                        | User may switch template mode; Working note preview follows selected template mode  |
| First non-empty save succeeds                  | Server returns locked mode; UI stores mode from response                            |
| User tries different mode after content exists | UI requires Clear working note confirmation                                         |
| Clear succeeds                                 | Content removed, mode unlocked, working note re-renders using current template mode |

Do not lock mode on client input alone. The spec says mode lock is final only after server confirms save. 

---

## Backend plan

Most of the backend branch can be kept.

### Keep

The branch already has the right backend concepts:

* `TranscriptWorkingNoteMode`
* encrypted `freeform_working_note_encrypted`
* `working_note_updated_at`
* structured working-note normalization using existing structured context storage
* `working_note_detail`
* `save_working_note`
* `clear_working_note`
* generation snapshots on `GeneratedDocument`
* working-note prompt formatting and redaction before LLM calls   

### Change

Add optimistic concurrency to working-note PATCH, as above.

Ensure the API response for save is always `WorkingNoteDetail`, not a raw `Transcript`, so the frontend gets:

```json
{
  "transcript_id": "...",
  "mode": "freeform",
  "freeform_text": "...",
  "structured_note": null,
  "updated_at": "..."
}
```

Keep `DELETE /working-note` as the only empty-save path. The current service already rejects empty freeform and empty structured PATCHes, which matches the spec. 

---

## Files to change

| File                                       | Plan                                                                                                                                                          |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/static/js/transcribe/documents.js`    | Add Working note as a synthetic selector item. Generalise selected note identity to `working:<transcriptId>` / `generated:<id>`. Reuse existing switch guard. |
| `app/static/js/transcribe/app.js`          | Replace branch-specific working-note dirty/save state with target-agnostic note editor state. Keep one `persistNoteEditsSilently()` path.                     |
| `app/static/js/transcribe/structured.js`   | Remove separate `activeEditorSource` / `renderWorkingNote()` path. Render document-like note targets through the same structured/freeform editor.             |
| `app/static/js/transcribe/actions.js`      | Before template generation, save dirty working-note target and block generation if save fails.                                                                |
| `app/templates/transcribe/_workspace.html` | Add clear-working-note control/status hooks only. Do not add a separate editor area.                                                                          |
| `app/schemas/transcripts.py`               | Add `expected_updated_at` to `WorkingNoteUpdate`.                                                                                                             |
| `app/services/transcripts.py`              | Enforce optimistic conflict check for working-note PATCH. Keep mode lock, clear, validation, encryption.                                                      |
| `app/services/templates.py`                | Keep branch snapshot/redaction/prompt work; verify quick actions and follow-ups still exclude working note.                                                   |
| `tests/test_api.py`                        | Add/adjust working-note ownership, mode lock, clear, conflict, generation snapshot, redaction fail-closed tests.                                              |
| `tests/test_web_refactor.py`               | Add static regression tests requiring the working note to use the generated-note editor path and switch guard.                                                |

---

## Specific cleanup from the current branch

Remove the current frontend pattern:

```js
if (structuredEditor?.getActiveEditorSource?.() === 'working_note') { ... }
```

and replace it with:

```js
const target = currentNoteTarget();
if (target.kind === 'working_note') { ...only build endpoint/payload... }
```

The only acceptable working-note-specific code in the editor flow should be:

```text
target construction
working-note API payload construction
working-note response normalisation
clear-working-note action
mode-lock messaging
```

Everything else—dirty state, autosave, on-blur save, in-flight save, queued save, focus preservation, pagehide save, and switch protection—should be the same code path as generated notes.

---

## Test plan

### Frontend regression tests

Add tests that fail if Working note is a parallel editor:

1. Working note appears in the note selector as a virtual note target.
2. Typing in Working note calls the same `markNoteEditorDirty`.
3. Focusout from a working-note freeform line calls `scheduleNoteAutosave({ immediate: true })`.
4. Focusout from a working-note structured line calls the same immediate autosave.
5. Workspace refresh while a working-note row is focused preserves editor DOM/focus.
6. Switching from dirty Working note to generated note first saves or blocks on failure.
7. `pagehide` persists dirty Working note through the same keepalive path.
8. Template generation blocks if working-note save fails.

### Backend/API tests

1. Owner can GET/PATCH/DELETE working note.
2. Non-owner, team leader, and system admin do not gain working-note visibility.
3. Freeform save encrypts content and returns plaintext only to owner.
4. Empty freeform PATCH returns 422; clear uses DELETE.
5. Structured save accepts only allowed EMIS keys and enforces limits.
6. Mode locks after first non-empty save.
7. Clear unlocks mode.
8. PATCH with stale `expected_updated_at` returns 409.
9. Generation can proceed with working note only and empty transcript/dictation.
10. Generation blocks when transcript, dictation, and working note are all empty.
11. Generated document snapshots only the active working-note mode/content.
12. Working-note redaction failure fails closed.
13. Quick actions and follow-ups do not automatically include working note.

---

## Suggested implementation order

1. **Keep backend branch work**, add `expected_updated_at`, and tighten tests.
2. **Refactor frontend target identity** in `documents.js` and `app.js`.
3. **Remove `renderWorkingNote()` / `activeEditorSource`** from `structured.js`.
4. **Wire Working note into the existing selector** as a virtual note.
5. **Make generation save dirty Working note first**.
6. **Add clear/unlock UI**.
7. **Run focused frontend regression tests** around autosave, blur, focus preservation, and switching.
8. **Run backend generation/redaction snapshot tests**.
