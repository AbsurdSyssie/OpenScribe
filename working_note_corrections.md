## Working-note correction plan

## Critique result

All three suggestions are valid debt/regression fixes. Keep them, with one adjustment:

- Fix 1 is correct, but the guard must run only for a dirty active Working note; clean empty/no-note state may still return normally.
- Fix 2 is correct for timestamped notes. First save must still omit `expected_updated_at`; migrated structured notes already backfill `working_note_updated_at`.
- Fix 3 is correct and broader than one payload call: working-note content detection also reuses save serialization, so unchecked visible lines must count as content too.

## Accepted fixes

1. Dirty-empty working note can still generate from stale saved content

saveWorkingNoteBeforeGeneration() returns null when the current working note has no visible content, even if the editor is dirty. The normal Generate flow then ignores that return value and proceeds to POST /generate-output. The dictation “Save & generate” path does the same.

Regression scenario:

Save a working note.
Delete all visible lines.
Click Generate.
Empty PATCH is not sent.
Generation still queues.
Backend snapshots the old saved working note from the transcript.

Fix: in `saveWorkingNoteBeforeGeneration()`, if `isWorkingNoteTargetId(...) && noteEditorDirty && !workingNoteHasContent()`, throw:

```js
throw new Error('Clear the working note before generating.');
```

Do not return null.

2. Working-note conflict protection is still bypassable

_assert_working_note_update_current() currently returns without conflict if expected_updated_at is omitted, even when the transcript already has working_note_updated_at.

That means any client can overwrite an existing working note by omitting expected_updated_at.

Fix:

```py
def _assert_working_note_update_current(transcript, expected_updated_at):
    if transcript.working_note_updated_at is None:
        return

    if expected_updated_at is None:
        raise AppError(409, "conflict", "Working note changed elsewhere. Reload before saving again.")

    ...
```

Add the missing test: second save with omitted expected_updated_at must return 409.

3. Structured working-note save treats unchecked lines as deleted

For generated structured notes, save serialization calls:

```js
structuredEditor.serializeCurrentNoteEditor({ mode: 'structured' })
```

which defaults includeUncheckedStructuredLines = true. For working notes, the call explicitly sets includeUncheckedStructuredLines: false.

The serializer then drops unchecked lines when that flag is false:

```js
.filter((line) => includeUncheckedStructuredLines || line.checked !== false)
```

This diverges from generated-note behaviour. If a user unchecks a structured working-note line for copy/selection reasons, autosave will remove that line from the persisted working note. That is not “same note writer logic”.

Fix: remove `includeUncheckedStructuredLines: false` for working-note persistence. Save all visible text lines, like generated notes. Use checkboxes only for copy selection.
