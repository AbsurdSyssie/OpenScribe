# Working Note Implementation Plan

Temporary design notes for the working-note slice.

## Goal

Preserve clinician-authored note content separately from generated note output. Users can review, edit, and regenerate from their own working note without the LLM output destroying that source content.

## Domain

- User-facing label: "Working note".
- Help text may say: "Your own notes used as context for generation."
- Working note is transcript-derived, owner-only content.
- Transcript text remains separate: it is the consultation transcript/STT draft.
- Generated note output remains separate: it is LLM-created and may be edited per generated document.

## Mode

- Each transcript has one working-note mode: `freeform` or `structured`.
- Mode is nullable until first non-empty save.
- First non-empty save locks the mode.
- User may browse/switch templates before entering content.
- To switch mode after content exists, user must clear the working note with confirmation.
- Clearing working note removes content and unlocks mode.
- Clearing working note does not delete transcript text, generated outputs, or generated-output snapshots.

## Storage Direction

- Add explicit working-note mode on transcript.
- Add one encrypted freeform working-note text source on transcript.
- Reuse existing structured context storage as structured working-note storage for MVP.
- Do not rename existing structured context DB column in first slice unless needed.
- Structured working note supports EMIS profile in MVP.
- Validate structured content against allowed EMIS section keys.
- Migration backfills mode to `structured` only when existing structured context has at least one non-empty allowed section.

## API Direction

- Add dedicated owner-only working-note routes:
- `GET /api/v1/transcripts/{transcript_id}/working-note`
- `PATCH /api/v1/transcripts/{transcript_id}/working-note`
- `DELETE /api/v1/transcripts/{transcript_id}/working-note`
- Keep generic transcript metadata updates separate from working-note content and mode-lock rules.
- Existing `structured_context_json` transcript patch behavior may remain temporarily during UI migration.
- `PATCH /working-note` accepts either freeform or structured payloads:

```json
{
  "mode": "freeform",
  "freeform_text": "Patient feels better..."
}
```

```json
{
  "mode": "structured",
  "structured_note": {
    "profile": "emis",
    "sections": {
      "problem": ["Headache"],
      "history": ["Improving"]
    }
  }
}
```

- Mode is required on non-empty save.
- Only content matching the requested mode is accepted.
- Empty saves should use `DELETE /working-note` rather than `PATCH`.
- `PATCH` must not accidentally wipe working-note content.
- `GET /working-note` returns owner-visible plaintext content and mode state:

```json
{
  "transcript_id": "...",
  "mode": "freeform",
  "freeform_text": "...",
  "structured_note": null,
  "updated_at": "..."
}
```

- No working note returns null mode, empty freeform text, null structured note, and null updated timestamp.
- Response includes `mode` even when null so UI can render unlocked state.
- If `PATCH /working-note` sends a different mode while existing working-note content locks the transcript, return `409 business_rule_violation` with detail code `working_note_mode_locked`.
- User-facing message: "Clear the working note before switching mode."
- `DELETE /working-note` requires no request body confirmation. UI confirms before calling it. API deletes owner working-note content immediately and returns `204`.
- Transcript list/detail responses may include summary fields only: `working_note_mode` and `has_working_note`.
- Full working-note content comes from workspace payload or `GET /working-note`.
- Do not bloat generic transcript responses with working-note content unless the workspace specifically needs it.
- Generation requests do not send working-note content in the request payload.
- Generation forms must not render legacy `context_*` structured-context fields; saved Working note is the only structured source path.
- Client saves working note first; server loads saved working note from the DB during generation.
- This gives deterministic snapshots/redaction and avoids trusting unsaved client payload.
- `PATCH /working-note` rejects whitespace-only freeform text with `422` and instructs caller to clear/delete instead.
- Trim for validation. Preserve useful internal line formatting for valid text. Follow existing editor normalization for leading/trailing whitespace.
- Structured `PATCH /working-note` omits empty sections.
- If all structured sections are empty, reject with `422` and instruct caller to clear/delete instead.
- Empty structured saves must not lock mode.
- Add `working_note_updated_at` for UI saved state.
- Do not add/change transcript-wide `updated_at` only for this feature, to avoid changing transcript ordering semantics elsewhere.
- Do not add `working_note_updated_by_user_id` in MVP. Owner is the only editor; `working_note_updated_at` is enough.
- Add `TranscriptWorkingNoteMode` enum with `freeform` and `structured` values.
- Use enum in DB/API validation to prevent invalid mode values.
- Generated-document working-note snapshot mode reuses the same enum, nullable when no working note was included.
- Generated-document working-note snapshots are encrypted with owner DEK.
- Freeform snapshot uses encrypted text. Structured snapshot uses encrypted JSON.
- Do not expose generated-document working-note snapshots in normal `GeneratedDocumentDetail` for MVP.
- If exposed later, prefer a separate owner-only generation-inputs endpoint.

## Generation Contract

- Template generation receives transcript text and working note as separate labelled sources.
- Transcript remains factual patient-spoken anchor.
- Working note is clinician-authored context and carries stronger signal for assessment, phrasing, and plan.
- Selected template controls output shape.
- Working-note mode controls source shape.
- Dirty Working-note saves use the rendered editor mode, not the current template selector mode, so template switching cannot serialize visible content through the wrong shape.
- Structured working note may feed freeform output.
- Freeform working note may feed structured output.
- Structured output generation receives structured working note as labelled sectioned context; prompt/model decides how to use it.
- Application code does not hard-map working-note sections into generated output sections.
- Generated note must not invent facts absent from transcript, working note, or saved dictation.
- Template generation may proceed when at least one source has content: transcript text, working note, or saved dictation.
- Generation blocks when all sources are empty.
- Saved structured Working notes are represented only through generated-document Working-note snapshots, not generated-document structured context. Generation requests reject transient `structured_context`; callers must save Working note content first.
- Quick actions and follow-ups do not automatically include working note unless user explicitly supplies context.

## Redaction And Privacy

- Working note must pass through redaction before any LLM request.
- Redaction reuses the generation-time redaction boundary with one combined placeholder index across transcript, dictation, and working note.
- Do not create a separate user-visible redaction run for working note in MVP.
- If working-note redaction fails, generation fails closed.
- Never send unredacted working-note content to an LLM.
- Working-note content must not appear in logs, analytics, provider usage events, or error details.
- Logs may include IDs, mode, status, counts, durations, and error codes only.

## Generated Document Snapshots

- Each generated note stores an encrypted plaintext snapshot of the working-note input used for that generation.
- Store working-note mode snapshot.
- Populate only one matching source snapshot: freeform or structured.
- Structured generated-note snapshots store only sections used for that generation.
- Living structured working note keeps all profile sections.
- Redacted prompt auditing can rely on the generated document encrypted LLM request payload.
- First slice does not need normal UI exposure of snapshots.
- Snapshots may support future generation-input details, debugging, or provenance under owner-only access.

## Editing UX

- Working note uses conservative autosave/on-blur persistence.
- UI shows saving, saved, or error based on server response.
- Mode lock is final only after server confirms save.
- Generation first saves current editor content, then queues generation.
- Note generation submit uses one client-side in-flight enqueue guard shared by the normal Generate form and dictation Save & generate flow. The guard lasts until the save/enqueue request settles, not for a fixed timer.
- Generation blocks while working-note editor has unsaved or failed-save state.
- Working-note saves use the same note-editor dirty/save/queued/conflict machinery as generated-note edits; only the save endpoint/payload differs.
- Working-note PATCH may include `expected_updated_at`; stale values return `409 conflict` instead of overwriting newer content.
- Structured Working note virtual sections provide plaintext line content through `section.text`; the shared structured editor must parse that alongside generated-note section text fields.
- After generation, workspace may focus generated output, but working note must remain visible or easy to reopen.
- Unsaved working-note edits must be protected from accidental loss.
- Generated-note edits never feed back into working note automatically.
- No explicit copy-back flow from generated note to working note in MVP.
- Working-note UI may provide explicit copy working note action when user is viewing working note.
- Workspace UI renders Working note as a virtual note version in the existing note-builder editor.
- Selecting Working note uses the same freeform/sectioned line editor as generated notes, but saves through `/working-note`.
- The virtual note id uses `working:<transcript_id>` so focus preservation, switch guards, and autosave target checks stay target-based.
- Template selection changes are owned by the guarded action handler. Picker helpers dispatch `change` only; they must not sync template UI directly before dirty Working-note saves complete.
- Selecting a generated note version uses the same editor surface but saves generated-document edits only.
- After generation is queued/completed, UI auto-selects the newest generated note; Working note remains available in the note switcher.
- When reopening or refreshing a consultation with generated notes, the UI defaults to a generated note instead of Working note unless a focused/dirty/in-flight Working note edit must be protected.
- Default clinical copy/export actions use generated note output, not working note.
- Freeform working note should support smart phrases if existing editor infrastructure can be reused.
- Generated outputs do not need stale/out-of-date indicators when working note changes.
- Generated note history may show lightweight source metadata when easy, but it is not required for MVP.

## Limits

- Freeform working note supports up to 20,000 characters.
- Structured working note should reuse existing structured validation where present.
- If no existing structured limit applies, target 4,000 characters per section and 20,000 total.

## Lifecycle

- Working note follows transcript-root retention.
- Living working note is deleted with transcript root.
- Generated-document working-note snapshots are deleted with generated documents or transcript cascade.
- No separate retention clock exists for working notes.
- Owner may clear working-note content independently.
- Team leaders and system admins receive no new working-note visibility by default.

## Documentation Tasks

- Update user tutorial to explain working note during consultation and regeneration.
- Update API docs with working-note owner-only endpoints or fields.
- Update transcript/workspace docs with working-note lifecycle, redaction, and deletion semantics.
- Update testing docs with focused tests for ownership, redaction fail-closed, snapshots, clear-mode unlock, and generation source selection.

## Test Plan

- Backend/API tests first.
- Cover owner authorization, mode lock, clear/unlock, validation, and no admin/leader access expansion.
- Cover generation snapshot persistence and working-note redaction fail-closed behavior.
- Cover generation with working note only and empty transcript/dictation.
- Add browser JS tests for substantial UI logic, especially generation blocked after failed working-note save and mode switch blocked until clear.
