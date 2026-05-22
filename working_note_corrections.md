Verdict

Not ready to merge as-is.
working_note is mechanically clean against master — 18 commits ahead, 0 behind — but I would hold the merge for two data-safety fixes.

Blockers
1. Legacy structured notes may not be safely backfilled

The migration backfills working_note_mode = 'structured' only when structured_context_json can be queried as plaintext JSON with a sections object.

But current JSON content is normally encrypted via encrypt_json_for_owner, which serializes JSON and stores an encrypted text envelope; decrypt_json_for_owner handles both encrypted strings and raw dict/list values.

Risk: existing encrypted structured_context_json rows may keep working_note_mode = NULL and working_note_updated_at = NULL. Runtime fallback may still display them, but save_working_note() only enforces the “clear before switching mode” rule when transcript.working_note_mode is not None, so a legacy structured note could potentially be overwritten as freeform without the intended guard.

Fix: either perform an app-level backfill that decrypts/normalizes existing structured notes, or make save_working_note() infer/lock existing structured notes even when working_note_mode is null.

2. Clearing a working note bypasses optimistic concurrency

PATCH /transcripts/{id}/working-note uses WorkingNoteUpdate and the service checks expected_updated_at. DELETE /transcripts/{id}/working-note takes no payload and directly calls clear_working_note_service.

The clear service wipes mode, freeform text, structured context, and timestamp without checking expected_updated_at.

Risk: a stale tab/client can delete a newer working note.

Fix: add a clear payload/query parameter with expected_updated_at, or route clearing through the same concurrency guard used for saves.

Needs product/behavior confirmation
Structured context path changed from master

On master, queue_document_generation_from_template() accepted transient structured_context, fell back to transcript structured_context_json, serialized it, saved it onto generated_documents.structured_context_json, and used it as “existing section context” for structured generation.

On working_note, generation snapshots saved working-note content onto separate generated-document working-note snapshot fields instead. The branch docs/commit notes say transient structured_context is intentionally rejected and saved Working note is now the source.

That is probably intentional, but it is a meaningful behavior change. Confirm before merge that losing the old transient structured-context path is desired.

Positive findings
DB additions are mostly nullable and isolated: transcript working-note fields plus generated-document snapshot fields.
Working-note save validation is reasonably strict: EMIS profile only, known section keys, per-section and total size limits.
Generation snapshots working notes at queue time, which is the right pattern for reproducible async generation.
Redaction is applied to working-note context before it is inserted into LLM prompts.
Frontend autosave has baseline tracking and 409 handling for saves.