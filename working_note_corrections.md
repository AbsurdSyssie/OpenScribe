Verdict

Ready after two data-safety fixes.

Kept / fixed

1. Legacy encrypted structured notes may not be backfilled by SQL migration.

Decision: do not add app-level migration/decryption churn. Runtime now infers existing structured Working-note content even when `working_note_mode` is null, returns it as `structured`, and blocks mode switching until clear.

2. Clearing a Working note bypassed optimistic concurrency.

Decision: fixed. `DELETE /working-note` now accepts `expected_updated_at` and applies same conflict guard as save when saved content has a timestamp. Frontend sends current Working-note timestamp. Legacy no-timestamp notes can still be cleared because no lock token exists.

Modified / rejected

Transient structured-context generation path from master stays removed. Saved Working note is intended generation source; generated documents use Working-note snapshot fields, not generated-document `structured_context_json` duplication.

Positive findings kept

- DB additions mostly nullable and isolated.
- Working-note validation is strict: EMIS profile, known section keys, per-section and total size limits.
- Generation snapshots Working notes at queue time.
- Redaction applies before Working note enters LLM prompt.
- Frontend autosave uses baseline tracking and 409 handling; clear now uses same concurrency token.
