# Working note save recovery

Status: implemented and verified on 3 November 2026, following authorisation in this chat. The intended design is retained here; maintained docs and executable contracts describe current behaviour. Final verification passed 144 focused tests, the 170-case API authorization audit, migration round trips, operational-document validation, JavaScript syntax checks, and diff whitespace checks.

## Outcome and current behaviour

Make Working note saves recover from a committed write whose response was lost, without choosing clinical content by wall-clock time or overwriting another editor. Implement first; run tests at the end, as requested.

Current code uses `expected_updated_at`, the existing root/source-writer lock, a serialized browser save queue, and a local edit counter. The prior editor-preservation fix is already present in the working tree and must remain. Clears reset the timestamp to null, so it is unsuitable as a permanent revision. The browser currently forgets the exact submitted request on transport failure. GET/workspace projections use `WorkingNoteDetail`; save and clear are dedicated owner routes. The legacy structured-context writer also changes the Working note.

## Protocol and persistence

1. Add non-negative `working_note_revision` (BIGINT, default 0) and nullable `working_note_last_operation_id` (UUID) to the transcript root. Backfill existing timestamped notes to revision 1. Root-created structured notes start at 1; empty roots start at 0. Every accepted save, clear, or legacy structured-context write increases revision under the existing source-writer lock. Clear never resets revision. Legacy writes invalidate the last-operation marker.
2. Add optional `expected_revision` and `operation_id` to Working note PATCH/DELETE requests; require them together. A revision-aware request must match the current revision, except for an exact replay of the latest accepted operation. Continue accepting the existing timestamp-only contract for older clients. Do not change mode/profile/section/content validation.
3. Remember only the latest operation ID on the root, not an unbounded history or content digest. An exact retry with its frozen original base revision is acknowledged without a second write only while its resulting revision is still current and its normalized operation content matches the current owner-encrypted note. Reusing that latest ID with different content, operation type, or base conflicts. A superseded original request conflicts; it cannot overwrite a newer save or recreate a cleared note. The monotonic base revision prevents an old frozen request from executing twice even after its marker is superseded.
4. Include `revision` in owner WorkingNoteDetail GET/PATCH/workspace responses, including empty notes. Keep timestamps for display/compatibility. Preserve DELETE 204 and return its acknowledged revision in `X-Working-Note-Revision`.
5. Keep permission/expiry checks before any replay handling. Mutation, revision, and operation marker commit atomically. Compare normalized payloads through existing content decryption/normalization; do not store plaintext, hashes, fingerprints, or request bodies in metadata/logs. No new content retention or crypto primitive.

## Browser behaviour

1. Preserve the rendered revision alongside the rendered text/mode, including revision 0. Capture the dirty revision on the first edit; polling must not rebase dirty content. Reuse the existing local edit counter and serialized queue.
2. Freeze one operation ID, endpoint, base revision, serialized payload, and submitted local edit number per outstanding operation. On uncertain transport/5xx/response-decoding failure retain that attempt in page memory. Retry its exact body and ID before submitting later edits. Never attach new text to an old ID.
3. A successful acknowledgement advances the rendered and dirty baseline revision, but marks the editor clean only if the acknowledged local edit number is still current. Drain/schedule later edits with a fresh ID and the acknowledged base. Validate acknowledgement revision equals the frozen base plus one; do not adopt an unrelated newer response as the baseline of retained local text.
4. Use bounded retry (one immediate replay for uncertain outcomes, then retain the attempt for an explicit/normal later flush). Do not loop on 409/auth/validation failures or silently replace a draft. Failures continue blocking generation/navigation. Keep conflict text and the existing unsaved-change protections.
5. Clear uses the same operation identity discipline. Drain pending saves before an explicit clear, capture the intended target/context, and reset text/revision only after its own successful acknowledgement. A failed clear retains its exact identity for a safe retry without unexpectedly clearing later remote changes. Guard against late results after consultation/target changes. Do not persist clinical drafts or pending payloads in browser storage.

## Delegation and ownership

- Backend agent: model/migration/schema/service/route/audit-manifest changes and focused new backend/migration tests. Own these files; avoid editing unrelated existing test changes. No test execution until implementation is integrated.
- Frontend agent: Working note revision projection and save/clear attempt lifecycle, app integration, module asset versions, focused browser/Node regression tests. Own frontend/template files and new frontend tests; coordinate schema names with the backend protocol above. No test execution until integration.
- Primary agent: architecture decisions, integration review, maintain this plan, then delegate sequential verification and an independent review. Documentation agent owns maintained docs (`api.md`, `workspace.md`, `transcript-capture.md`, `DatabasePlan.md`, `CONTEXT.md`, and the compatibility clarification in `working_note_implementation.md`). Preserve all unrelated working-tree changes.

## Acceptance and end-stage verification

- A save commits and loses its response; exact replay acknowledges one revision increment and preserves later typed edits. Cover both freeform and structured normalization.
- Two independent writers from one base cannot both commit. A newer remote save/clear makes old pending requests conflict without modifying remote state. An empty root edited before another save-and-clear cannot recreate content using revision 0.
- Duplicate clear does not increment twice; a superseded clear cannot delete new content. Same operation ID with altered payload/base/type is rejected while current.
- Revision/marker changes are atomic and respect owner, admin, expiry, mode lock, encryption, root deletion/retention, and legacy structured writer boundaries.
- Frontend tests use real state/attempt helpers and actual app save integration where practical: lost response, failed JSON decode, bounded retries, edits during save, delayed poll, real conflict, target switches, and generation/navigation drains. No real providers/patient content.
- Run project `.venv` pytest with `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app`, focused new suites and existing Working note, navigator, autosave, generation-barrier/source-lock, and migration checks as affected. Run pytest sequentially with the API auth audit; follow docs/testing.md and docs/dbtesting.md, including infra-lock and sandbox escalation rules. No installs/restarts/isolation bypass.
- Update API authorization manifest for changed request contract and run the documented audit after pytest. Run operational-doc validation, JS syntax checks, and diff whitespace checks at the end. Fix failures by identifying implementation/test/fixture/environment cause; never weaken guards.
- Update maintained API/workspace/transcript-capture/persistence docs and CONTEXT.md as necessary; close the recorded lost-ack issue only after verified. Report migration requirement and any remaining limitation precisely.

## Explicit limits

Working note only. Generated-note and follow-up saves keep their existing contracts. No last-write-wins, automatic clinical merge, comparison UI, cross-owner access, provider changes, browser persistent drafts, or general idempotency framework. Latest-operation acknowledgement is intentionally bounded: once superseded, an original retry is a real conflict and receives no historical content replay.
