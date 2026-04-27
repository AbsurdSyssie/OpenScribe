# Progress

## 2026-04-27 De-identification Inspect Token Handling

### Scope

- Stopped the admin de-identification inspection flow from rendering newly entered bearer tokens back into the browser as hidden form fields.
- De-identification bearer tokens are now one-request values for Inspect; saving a new bearer-auth provider after inspection requires re-entry so Vault storage happens only on Save.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/routes/web_admin.py`: discard de-identification inspect bearer tokens after the request and ignore client-provided preserved tokens when saving.
- `app/templates/admin.html`: remove the de-identification hidden preserved-token field and clarify one-request/save behavior.
- `tests/test_admin_ui.py`: add regression coverage for no token echo and required re-entry on save.
- `docs/testing.md`, `docs/progress.md`: document the browser UI secret-handling coverage and this fix.

### Tests

- `python3 -m py_compile app/routes/web_admin.py tests/test_admin_ui.py`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "deidentification_inspect_does_not_render_bearer_token"`: passed, 1 test.

### Documentation

- Updated testing coverage notes and daily progress.

### Risks / assumptions

- Admins must re-enter a bearer token when saving a new bearer-auth de-identification provider after Inspect. Existing saved providers can still inspect with their Vault-backed token.

### Architecture checkpoint summary

- Privacy boundaries preserved: provider secrets are no longer exposed in rendered HTML; synthetic ping still sends only sample text.
- Ownership rules preserved: no transcript-derived access paths changed; route remains system-admin only.
- Deletion semantics preserved: no retention, cascade, or Vault cleanup behavior changed.
- Provider rules preserved: raw de-identification secrets remain Vault-backed on save, with built-in fallback unchanged.
- Structured-note contract preserved: no EMIS or generated-document behavior changed.

## 2026-04-27 De-identification OpenAPI Array Response Path

### Scope

- Preserved the empty response entity path inferred from top-level array OpenAPI response schemas so generic REST providers returning an entity list directly can be parsed.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/services/deidentification.py`: keep the empty inferred path instead of falling back to `entities`.
- `tests/test_api.py`: add OpenAPI/docs inspection regression coverage for top-level array responses.
- `docs/testing.md`, `docs/progress.md`: document supported inspection coverage and this fix.

### Tests

- `python3 -m py_compile app/services/deidentification.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "openapi_docs_preserve_top_level_array_response_path or inspect_deidentification_openapi_docs"`: passed, 2 tests.

### Documentation

- Updated testing coverage notes and daily progress.

### Risks / assumptions

- Empty path remains the existing parser contract for "use the whole payload"; no provider resolution, ownership, deletion, or structured-note behavior changed.

### Architecture checkpoint summary

- Privacy boundaries preserved: synthetic admin ping still sends only configured sample text, not transcript/note content.
- Ownership rules preserved: system-admin inspection flow only; no transcript-derived records touched.
- Deletion semantics preserved: no persistence or cascade behavior changed.
- Provider rules preserved: generic REST OpenAPI inference now supports another valid response shape while retaining fallback behavior.
- Structured-note contract preserved: no EMIS or generated-document output behavior changed.

## 2026-04-27 Value-Based De-identification Parsing

### Scope

- Added generic REST parsing fallback for PII provider responses that return detected text plus label instead of numeric start/end offsets.
- Shared parser between runtime redaction and admin synthetic ping so successful admin parsing matches transcription behavior.
- Added common entity value fields (`text`, `value`, `entity_text`, `entity`, `match`, etc.) and case-insensitive source-text lookup.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: repeated identical values are matched sequentially where possible; ambiguous duplicate occurrences may still require offset-capable provider output for perfect alignment.

### Files changed

- `app/services/redaction.py`: add shared provider payload parser with value-based span derivation.
- `app/services/deidentification.py`: use shared parser for admin synthetic pings.
- `tests/test_api.py`: cover value-only entity parsing and existing offset parsing.
- `docs/admin_brief.md`, `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document setup workflow, parser behavior, and coverage.

### Tests

- `python3 -m py_compile app/services/redaction.py app/services/deidentification.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "generic_rest_deidentification"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "inspect_deidentification or prunes_forbidden"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider"`: passed, 8 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "deidentification"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py -k "manifest"`: passed, 1 test.

### Documentation

- Updated admin setup guidance, API notes, testing notes, and this progress entry.

### Risks / assumptions

- Value-only parsing depends on matching provider-returned text back into the submitted source text. Exact offsets remain more reliable for duplicates and transformed text.

### Architecture checkpoint summary

- Privacy boundaries preserved: parsing occurs only on owner runtime text or synthetic admin sample text; no new content readers/logging.
- Ownership rules preserved: runtime redaction remains owner/transcript scoped through existing paths.
- Deletion semantics preserved: no new persisted rows or cascade changes.
- Provider rules preserved: selected generic REST provider contract remains explicit and team-scoped.
- Structured-note contract preserved: no EMIS keys or structured JSON behavior changed.

## 2026-04-27 Admin De-identification Team Selection

### Scope

- Added admin provider-tab controls to select an assigned de-identification provider for the current team and clear back to built-in fallback.
- Displayed selected provider endpoint in the admin selection summary so stale runtime paths like `/detect` are visible before testing transcription redaction.
- Added regression coverage that runtime redaction receives the selected provider's saved detect path.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: if an old provider remains selected, admin must click `Use for team` on the newly saved/assigned provider or clear selection.

### Files changed

- `app/templates/admin.html`: add `Use for team`, `Clear selection`, and selected endpoint display for de-identification providers.
- `app/routes/web_admin.py`: add admin set/clear de-identification selection routes.
- `tests/test_admin_ui.py`: cover admin assign/select/clear flow.
- `tests/test_api.py`: assert runtime redaction receives the selected provider's non-default detect path.
- `docs/testing.md`, `docs/progress.md`: document coverage and workflow.

### Tests

- `python3 -m py_compile app/routes/web_admin.py tests/test_admin_ui.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "deidentification"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "redaction_run_uses_selected_team_deidentification_provider"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_api.py -k "inspect_deidentification"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider"`: passed, 8 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py -k "manifest"`: passed, 1 test.

### Documentation

- Updated `docs/testing.md` and this progress entry.

### Risks / assumptions

- Provider ping/save and team selection remain distinct steps; runtime uses the selected provider row, not the last successful ping.

### Architecture checkpoint summary

- Privacy boundaries preserved: admin controls only provider metadata/selection and expose no transcript-derived content.
- Ownership rules preserved: no owner content access changed; team selection remains team-scoped metadata.
- Deletion semantics preserved: clearing selection removes only the selection row and falls back to built-in provider.
- Provider rules preserved: only assigned, active providers can be selected; fallback behavior remains unchanged.
- Structured-note contract preserved: no EMIS keys or structured output behavior changed.

## 2026-04-27 De-identification OpenAPI Inspection

### Scope

- Extended system-admin de-identification inspection so `/docs`, `/redoc`, and OpenAPI JSON paths load API metadata instead of being treated as detect endpoints.
- Split OpenAPI/docs path from selected detect endpoint so admins can discover candidate endpoints, choose one, then infer and ping that endpoint's request/response contract.
- Inferred generic REST detect path, request text/language fields, extra body defaults, response entity array path, span fields, score field, and model/version path from OpenAPI.
- Preserved typed extra body defaults from OpenAPI so provider pings send booleans/numbers as JSON booleans/numbers rather than strings.
- Ping now shows raw provider JSON response for the synthetic admin test only, helping diagnose providers that return redacted output/mapping instead of entity spans.
- Synthetic ping now omits common language values accidentally entered as field names, and retries without body fields that FastAPI reports as `extra_forbidden`.
- Updated admin provider form after inspection so inferred values can be pinged or saved without retyping.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: OpenAPI inference is heuristic; unusual provider schemas may still need manual field edits before ping/save.

### Files changed

- `app/schemas/deidentification.py`, `app/schemas/__init__.py`: add docs path plus inferred contract fields and field tips to inspect result.
- `app/services/deidentification.py`: load OpenAPI JSON from docs paths, infer chosen generic REST de-identification contract, send synthetic ping, and return raw test response.
- `app/routes/web_admin.py`: pass docs path separately and update form values with inferred contract fields after inspection.
- `app/templates/admin.html`: show docs path, candidate endpoint choices, inferred operation, contract fields, request field tips, and raw synthetic ping response.
- `tests/test_api.py`: add OpenAPI/docs inspection coverage and 422 extra-field pruning coverage.
- `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document docs/OpenAPI inspection behavior.

### Tests

- `python3 -m py_compile app/schemas/deidentification.py app/schemas/__init__.py app/services/deidentification.py app/services/redaction.py app/routes/api_routes.py app/routes/web_admin.py app/web/presentation.py app/api_route_audit.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "inspect_deidentification or prunes_forbidden"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider"`: passed, 8 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "deidentification"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py -k "manifest"`: passed, 1 test.
- `.venv/bin/python - <<'PY' ... templates.env.get_template('admin.html') ... PY`: passed.
- `git diff --check`: passed.

### Documentation

- Updated `docs/api.md`, `docs/testing.md`, and this progress entry.

### Risks / assumptions

- `/docs` is assumed to be FastAPI-style docs with `/openapi.json` at the same prefix; direct OpenAPI JSON paths are also supported.
- Inference selects the highest scoring JSON POST operation using de-id/PII/entity keywords; admins can still manually adjust fields before ping/save.
- If provider returns 422, inspect now returns raw synthetic response so admins can adjust selected endpoint/body fields without exposing transcript-derived content.
- Raw provider response in admin is safe only because inspect sends synthetic sample text; runtime redaction remains non-debug and does not reveal transcript-derived provider responses.

### Architecture checkpoint summary

- Privacy boundaries preserved: docs inspection fetches API metadata only and sends no transcript/note content.
- Ownership rules preserved: inspection remains system-admin-only provider configuration.
- Deletion semantics preserved: no persisted transcript-derived records and no cascade changes.
- Provider rules preserved: generic REST validation, bearer auth, HTTPS/local URL rules, and Vault-backed saved secret behavior remain intact.
- Structured-note contract preserved: no EMIS keys or structured JSON behavior changed.

## 2026-04-27 De-identification Provider Ping

### Scope

- Added system-admin de-identification provider inspection/ping for generic REST providers before save.
- Ping sends only admin-supplied synthetic sample text, parses configured response paths/fields, applies entity type mapping, and returns visible spans in API/UI.
- Added browser admin `Ping provider` flow next to save so a new provider can be tested and then configured.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: ping uses current configured contract fields; it does not auto-discover provider schemas.

### Files changed

- `app/schemas/deidentification.py`, `app/schemas/__init__.py`: add inspect request/result contracts.
- `app/services/deidentification.py`, `app/services/redaction.py`: add safe provider ping path with bearer override support and existing-secret reuse.
- `app/routes/api_routes.py`, `app/routes/web_admin.py`, `app/main.py`: expose API and browser inspect handlers.
- `app/templates/admin.html`, `app/web/presentation.py`: show ping control, sample text, parsed entities, and notes.
- `app/api_route_audit.py`: cover new system-admin inspect endpoint.
- `tests/test_api.py`: add de-identification provider inspect coverage.
- `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document endpoint and coverage.

### Tests

- `python3 -m py_compile app/schemas/deidentification.py app/schemas/__init__.py app/services/deidentification.py app/services/redaction.py app/routes/api_routes.py app/routes/web_admin.py app/web/presentation.py app/api_route_audit.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider"`: passed, 8 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py -k "manifest"`: passed, 1 test.
- `.venv/bin/python - <<'PY' ... templates.env.get_template('admin.html') ... PY`: passed.
- `git diff --check`: passed.

### Documentation

- Updated `docs/api.md`, `docs/testing.md`, and this progress entry.

### Risks / assumptions

- Inspection proves connectivity and configured parsing against sample text only; successful ping does not guarantee all real transcript formats are supported.
- Hidden preserved bearer token follows existing admin inspect/save pattern so admins can ping then save without retyping, but secrets are still not returned by JSON API and saved credentials remain Vault-backed.

### Architecture checkpoint summary

- Privacy boundaries preserved: ping sends synthetic sample text only; no transcript/note content involved.
- Ownership rules preserved: route is system-admin-only provider configuration, not owner content access.
- Deletion semantics preserved: no new persisted transcript-derived records and no cascade changes.
- Provider rules preserved: generic REST validation, bearer handling, HTTPS/local URL rules, and Vault-backed saved secrets remain intact.
- Structured-note contract preserved: no EMIS keys or structured JSON behavior changed.

## 2026-04-24 Live Capture Finalize Redaction

### Scope

- Added an owner-only live-capture finalize API that moves live transcripts out of `recording`, applies completed chunks, and triggers proactive redaction when the final draft is ready.
- Deferred preview redaction while live chunks are still queued/processing so the PII review state is not created from an incomplete transcript.
- Wired the browser live stop path to call finalize after the last VAD segment is flushed, then refresh the workspace so detected PII can appear before generation.
- Exposed owner-only redaction preview status in the workspace so an empty PII table can distinguish not-run, succeeded-with-none, and failed checks.
- Applied owner-entered manual PII as an extra outbound redaction layer for transcript, dictation, prompt, quick-action, follow-up, and structured-context text before LLM calls.
- Made owner-entered manual PII matching whitespace-tolerant so repeated spaces, tabs, or newlines in transcript text do not bypass outbound redaction.
- Stopped the workspace PII table from falling back to older successful detected PII when the newest redaction run for the transcript failed.
- Updated the API route audit manifest for new redaction-review routes and previously missing de-identification/app-preference/generated-document routes.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none known for the finalize lifecycle; live redaction still depends on the configured de-identification provider being available.

### Files changed

- `app/services/transcripts.py`: add live finalize service and ready-state preview redaction after completed chunks apply.
- `app/services/templates.py`: merge owner manual PII into outbound redacted generation text and PHI index before provider calls, with whitespace-tolerant matching.
- `app/routes/api_routes.py`, `app/main.py`: expose `POST /api/v1/transcripts/{id}/finalize-live-capture`.
- `app/static/js/transcribe/media.js`, `app/static/js/transcribe/app.js`: call finalize when live recording stops and refresh workspace state.
- `app/web/transcribe_workspace.py`, `app/schemas/workspace.py`, `app/templates/transcribe/_workspace.html`, `app/templates/transcribe/_head_assets.html`, `app/templates/transcribe/_shell_extras.html`: surface redaction preview status in owner workspace payload and UI.
- `app/api_route_audit.py`: cover new and missing API route specs in the auth audit manifest.
- `tests/test_api.py`: add finalize auth, pending-chunk, non-live, redaction preview, manual-PII outbound redaction including whitespace variants, and stale detected-PII coverage.
- `docs/api.md`, `docs/testing.md`, `docs/transcript-capture.md`, `docs/progress.md`: document lifecycle, workspace fields, and coverage.

### Tests

- `python3 -m py_compile app/services/transcripts.py app/services/templates.py app/routes/api_routes.py app/main.py app/web/transcribe_workspace.py app/schemas/workspace.py app/api_route_audit.py tests/test_api.py`: passed.
- `python3 -m py_compile app/services/templates.py app/web/transcribe_workspace.py tests/test_api.py`: passed.
- `node --check app/static/js/transcribe/media.js`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/python - <<'PY' ... missing_route_specs() ... PY`: passed, returned `[]`.
- `git diff --check`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "manual_pii_before_provider or transcribe_workspace_endpoint_returns_owner_pii_entities or process_generated_document_redacts_transcript"'`: blocked by local test DB bootstrap (`psycopg.OperationalError` while connecting in `tests/conftest.py`).
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "finalize_live_capture or transcript_routes_require_full_auth_and_preserve_owner_only_access or team_and_personal_template_routes_enforce_scope_and_allow_generation or processing_audio_file_job_appends_transcript_draft_and_marks_ready or transcribe_workspace_endpoint_returns_owner_pii_entities"'`: blocked by local test DB bootstrap (`psycopg.OperationalError` while connecting in `tests/conftest.py`).
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api_route_audit.py -k "manifest"'`: blocked by local test DB bootstrap (`psycopg.OperationalError` while connecting in `tests/conftest.py`).

### Documentation

- Updated transcript capture and testing notes plus this progress entry.

### Risks / assumptions

- The client finalize call is best-effort after local VAD stop; backend generation still has lazy redaction fallback if preview redaction is absent.
- Finalize returns `transcribing` and creates no redaction run while chunks remain pending.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content readers; finalize is owner-only and logs no transcript content.
- Ownership rules preserved: route uses owner transcript lookup and system-admin transcript ownership remains blocked.
- Deletion semantics preserved: preview runs remain version-linked transcript-derived children under transcript-root cascade.
- Provider rules preserved: redaction still resolves de-identification through the existing provider fallback path.
- Structured-note contract preserved: no EMIS keys or structured JSON behavior changed.

## 2026-04-24 Proactive Redaction Preview

### Scope

- Added proactive redaction after owner transcript commits and completed whole-file ingestion so detected PII can appear before LLM generation.
- Updated generation snapshotting to reuse an existing latest transcript version when its text matches the current draft, preserving the preview redaction run as the generation provenance.
- Kept generated-document lazy redaction as a fallback when no successful preview run exists.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: live chunked capture still avoids per-chunk version/redaction churn; a dedicated stop/finalize endpoint should trigger the same preview path when added.

### Files changed

- `app/services/transcripts.py`: create stable transcript versions and attempt preview redaction on commit and whole-file ingestion completion.
- `app/services/templates.py`: reuse matching transcript versions during generation instead of duplicating a just-reviewed snapshot.
- `tests/test_api.py`: cover proactive redaction on commit/whole-file ingestion and generation reuse of the preview run.
- `docs/testing.md`, `docs/progress.md`: document the new expected redaction timing and coverage.

### Tests

- `python3 -m py_compile app/services/transcripts.py app/services/templates.py`: passed.
- `python3 -m py_compile tests/test_api.py`: passed.
- `git diff --check`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "team_and_personal_template_routes_enforce_scope_and_allow_generation or transcript_routes_require_full_auth_and_preserve_owner_only_access or processing_audio_file_job_appends_transcript_draft_and_marks_ready"'`: blocked by local test DB bootstrap (`psycopg.OperationalError` while connecting in `tests/conftest.py`).

### Documentation

- Updated testing notes and this progress entry.

### Risks / assumptions

- Preview redaction failure is logged with IDs/error code only and does not block transcript persistence; generation still retries and fails closed if redaction remains unavailable.
- Live chunked preview redaction is intentionally deferred until there is a stable server-side finalize point.

### Architecture checkpoint summary

- Privacy boundaries preserved: redaction artifacts remain transcript-derived owner content and no content-bearing logs were added.
- Ownership rules preserved: redaction runs/entities are still scoped to the transcript owner and team.
- Deletion semantics preserved: artifacts remain attached to transcript versions under the transcript-root cascade.
- Provider rules preserved: de-identification provider resolution/fallback still goes through the existing redaction service.
- Structured-note contract preserved: no EMIS keys or structured JSON validation changed.

## 2026-04-23 Persisted Manual PII

### Scope

- Added owner-created manual PII rows under the transcript root.
- Manual PII values are encrypted with the owner content DEK, returned only through owner workspace/API paths, and merged into the transcript PII sidebar/highlights.
- Added owner-only create/delete API routes and browser add/delete controls.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: exact-string transcript highlighting still does not catch punctuation/spacing variants.

### Files changed

- `app/models.py`, `alembic/versions/1a2b3c4d5e6f_add_transcript_manual_pii_entities.py`: add transcript-root manual PII table with owner/team scope and cascade.
- `app/services/transcripts.py`: add owner-only manual PII create/delete/encrypted value helpers.
- `app/routes/api_routes.py`, `app/main.py`, `app/schemas/transcripts.py`, `app/schemas/__init__.py`: expose manual PII API contracts.
- `app/web/transcribe_workspace.py`: merge detected and manual PII into owner workspace payload.
- `app/static/js/transcribe/app.js`, `app/static/js/transcribe/bootstrap.js`, `app/static/js/transcribe/documents.js`, `app/templates/transcribe/_workspace.html`, `app/templates/transcribe/_shell_extras.html`, `app/templates/transcribe/_head_assets.html`: persist add/delete UI and keep highlights/table refreshed.
- `tests/test_api.py`, `tests/test_migrations.py`: add API, auth, encryption, cascade, and schema coverage.
- `docs/api.md`, `docs/testing.md`, `docs/transcript-capture.md`, `docs/progress.md`: document behavior and coverage.

### Tests

- `python3 -m py_compile app/models.py app/services/transcripts.py app/web/transcribe_workspace.py app/main.py app/routes/api_routes.py app/schemas/transcripts.py app/schemas/__init__.py`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/bootstrap.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "pii_sidebar or transcribe_frontend_uses_global_template_selector"'`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "manual_pii or transcribe_workspace_endpoint_returns_owner_pii_entities"'`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_migrations.py -k "expected_schema"'`: passed.
- `git diff --check`: passed.

### Documentation

- Updated API behavior, transcript capture notes, testing notes, and progress log.

### Risks / assumptions

- Duplicate manual PII is collapsed by transcript, type, and normalized value hash.
- Manual PII is review/highlight metadata only; it does not alter provider redaction runs or generated-document redaction mappings.

### Architecture checkpoint summary

- Privacy boundaries preserved: manual PII is transcript-derived owner content and never exposed to leaders/admins.
- Ownership rules preserved: create/delete/read require owning user and owner workspace resolution.
- Deletion semantics preserved: manual PII cascades from transcript root and row deletion is immediate.
- Provider rules preserved: no STT/LLM/de-identification provider behavior changed.
- Structured-note contract preserved: no EMIS section keys or structured JSON shape changed.

## 2026-04-22 PII Highlighting and Manual Review Entries

### Scope

- Highlight selected-note PII matches inside the transcript text area.
- Added UI-only manual PII entry controls in the PII sidebar.
- Manual entries join the sidebar table and transcript highlights for the current browser session only.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: persistence of manual PII intentionally deferred.

### Files changed

- `app/templates/transcribe/_workspace.html`: add manual PII add controls.
- `app/templates/transcribe/_head_assets.html`: style manual controls and transcript highlights.
- `app/static/js/transcribe/app.js`: highlight selected-note/manual PII and manage UI-only manual entries.
- `tests/test_admin_ui.py`: cover wiring for highlight/manual controls.
- `docs/testing.md`, `docs/transcript-capture.md`, `docs/progress.md`: document behavior and coverage.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `git diff --check`: passed.
- `python3 -m py_compile app/web/presentation.py app/web/transcribe_workspace.py app/schemas/templates.py app/schemas/transcripts.py app/schemas/workspace.py`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "pii_sidebar or transcribe_frontend_uses_global_template_selector"'`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_endpoint_returns_owner_pii_entities"'`: passed.

### Documentation

- Updated testing, transcript-capture, and progress docs.

### Risks / assumptions

- Highlighting uses exact string matching. Case-insensitive matches are highlighted, but punctuation/spacing variants may not match.
- Manual PII is not persisted and resets on transcript switch or page reload.

### Architecture checkpoint summary

- Privacy boundaries preserved: PII values remain owner-visible transcript-derived content in the owner workspace only.
- Ownership rules preserved: no new endpoint or shared visibility added.
- Deletion semantics preserved: no persisted manual PII, no retention/deletion path change.
- Provider rules preserved: no provider resolution or credential behavior changed.
- Structured-note contract preserved: no EMIS section keys or structured JSON shape changed.

## 2026-04-21 Note-Switch PII Refresh

### Scope

- Added note-level `pii_entities` to generated-document workspace payloads.
- Updated note selection rendering so the PII sidebar refreshes from the newly selected note without a page reload.
- Fixed note-switch redaction debug rendering so the selected document object no longer shadows `document.createElement`.
- Versioned the transcribe module script and imports so browsers fetch the updated note-switch/PII modules after deploy or restart.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/schemas/templates.py`, `app/schemas/__init__.py`: add generated-document PII row payload.
- `app/web/presentation.py`: include selected document redaction entities in generated-document responses.
- `app/static/js/transcribe/documents.js`, `app/static/js/transcribe/app.js`: refresh PII sidebar during note selection.
- `app/static/js/transcribe/actions.js`: route note-history clicks through the same note-selection path.
- `app/templates/transcribe/_shell_extras.html`: version the transcribe module entrypoint.
- `tests/test_admin_ui.py`, `tests/test_api.py`: cover dynamic wiring and note payload rows.
- `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document behavior and coverage.

### Tests

- `python3 -m py_compile app/schemas/templates.py app/web/presentation.py`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `git diff --check`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "pii_sidebar or transcribe_frontend_uses_global_template_selector"'`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_endpoint_returns_owner_pii_entities"'`: passed.

### Documentation

- Updated API, testing, and progress docs.

### Risks / assumptions

- PII rows remain transcript-derived content. They are present only in owner-scoped generated-document payloads.

### Architecture checkpoint summary

- Privacy boundaries preserved: PII values are still owner-only and derived from existing redaction entities.
- Ownership rules preserved: generated documents remain owner-scoped by existing workspace query.
- Deletion semantics preserved: no deletion or retention path changed.
- Provider rules preserved: no provider resolution or credential behavior changed.
- Structured-note contract preserved: no EMIS section keys or structured JSON shape changed.

## 2026-04-21 Transcript PII Sidebar

### Scope

- Added owner-only detected PII rows to the transcribe workspace read model.
- Added a bounded right-side PII table in the Transcript tab, beside the transcript text and below the control/title bars.
- Kept post-consultation dictation in the same bounded history content area, with transcript text reflowing in the remaining grid width.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/schemas/transcripts.py`, `app/schemas/workspace.py`, `app/schemas/__init__.py`: add owner-visible PII row schema and workspace field.
- `app/web/transcribe_workspace.py`: read latest successful redaction entities for the active owner transcript.
- `app/templates/transcribe/_workspace.html`: render the PII table in the transcript history layout.
- `app/templates/transcribe/_head_assets.html`: add responsive three-column transcript/PII/dictation grid styling.
- `app/static/js/transcribe/app.js`: refresh PII table from workspace API/SSE payloads.
- `tests/test_admin_ui.py`, `tests/test_api.py`: cover SSR UI and API ownership behavior.
- `docs/api.md`, `docs/testing.md`, `docs/transcript-capture.md`, `docs/progress.md`: document behavior and coverage.

### Tests

- `python3 -m py_compile app/schemas/transcripts.py app/schemas/workspace.py app/web/transcribe_workspace.py`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `git diff --check`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "pii_sidebar or transcribe_frontend_uses_global_template_selector"'`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_endpoint_returns_owner_pii_entities"'`: passed.

### Documentation

- Updated API, testing, transcript-capture, and progress docs.

### Risks / assumptions

- PII table shows original detected values, so it is transcript-derived content. It remains owner-only and is not exposed in admin/team-leader views.
- The panel uses the latest successful transcript redaction run. It does not trigger redaction by itself.

### Architecture checkpoint summary

- Privacy boundaries preserved: detected PII values are returned only through the owner workspace for the active transcript.
- Ownership rules preserved: workspace transcript resolution remains owner-scoped; non-owners get no active transcript and no PII rows.
- Deletion semantics preserved: no deletion, retention, or cascade paths changed.
- Provider rules preserved: no provider selection or credential behavior changed.
- Structured-note contract preserved: no EMIS section keys or structured JSON shape changed.

## 2026-04-21 Transcript Content Flag Review Fix

### Scope

- Fixed the transcribe workspace `has_transcript_content` flag so blank committed transcript versions do not trigger the "has transcript text" delete confirmation.
- Kept the existing owner-only workspace response shape and immediate transcript-root deletion behavior.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/web/transcribe_workspace.py`: derive transcript content from decrypted draft text or decrypted committed version text with non-whitespace content.
- `tests/test_admin_ui.py`: cover sidebar delete-confirmation flags for empty draft, meaningful draft, meaningful version, and blank version sessions.
- `tests/test_api.py`: cover workspace API serialization for a blank committed transcript version.
- `docs/testing.md`, `docs/progress.md`: document the regression coverage and architecture checkpoint.

### Tests

- `python3 -m py_compile app/web/transcribe_workspace.py app/schemas/transcripts.py`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "marks_non_empty_sessions_for_delete_confirmation"'`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_endpoint_ignores_blank_transcript_versions_for_content_flag or transcribe_workspace_endpoint_returns_owner_workspace_state"'`: passed.
- `git diff --check`: passed.

### Documentation

- Updated testing and progress docs.

### Risks / assumptions

- The flag means meaningful transcript text, not the existence of a transcript-version row. Empty structured-generation snapshots are intentionally treated as no transcript text.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript text is exposed in the session rail or response beyond existing owner-only fields.
- Ownership rules preserved: only owner-scoped workspace transcripts are inspected.
- Deletion semantics preserved: browser confirmation remains advisory; authorized transcript-root deletion still hard-deletes immediately.
- Provider rules preserved: STT, LLM, and de-identification resolution are unchanged.
- Structured-note contract preserved: no EMIS section keys or structured output shape changed.

## 2026-04-20 Generated Note Copy Review Gate

### Scope

- Added a client-side generated-note review gate in the transcribe workspace.
- Structured generated-note section copy now unlocks only after the owner has scrolled far enough for that section bottom to be visible.
- Freeform generated-note copy now unlocks only after the generated note bottom is visible.
- Hidden output panes no longer satisfy the review gate during initial workspace render.
- Review sentinels are observed only after the editable note layout has stabilized.
- A render-readiness guard prevents sentinels from unlocking copy during setup.
- Review-required state now also uses the rendered generated draft id, not only the hidden latest-output dataset.
- Viewing a later structured section bottom marks earlier structured sections reviewed too, so reaching the note bottom unlocks copy-all behavior.
- Copy controls remain clickable while blocked and use a data marker plus toast feedback instead of silent disabled buttons.
- Manual pre-generation note entry remains unrestricted.
- Blocked copy attempts now surface as a toast instead of an inline alert.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: targeted pytest could not start because the test database connection failed during `tests/conftest.py` import.

### Files changed

- `app/templates/transcribe/_workspace.html`: expose the copy review status hook in the note toolbar.
- `app/static/js/transcribe/structured.js`: track generated-note review state, add bottom sentinels, derive review-required state from the rendered draft, ignore hidden review targets, delay observation until after layout, guard setup-time observation, and expose copy-blocker checks.
- `app/static/js/transcribe/actions.js`: block generated-note copy actions when the relevant content has not been viewed.
- `app/static/js/transcribe/app.js`: pass the copy button into the structured editor controller.
- `tests/test_admin_ui.py`: assert the frontend review-gate hooks and blockers remain wired.
- `docs/testing.md`, `docs/transcribe_brief.md`, `docs/transcript-capture.md`, `docs/progress.md`: document behavior and coverage.

### Tests

- `node --check app/static/js/transcribe/structured.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `git diff --check`: passed.
- Browser check on `/transcribe?transcript_id=4a842a80-f10a-4047-9a9f-d28dc9d1c6a4`: blocked section copy and Copy Selected before review with toast feedback and no clipboard write; after scrolling the note body to the bottom, sections unlocked and Copy Selected wrote to the clipboard.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector or exposes_workspace_hooks_and_pane_controls"'`: not run to completion; database connection failed while loading `tests/conftest.py`.

### Documentation

- Updated testing, transcribe brief, transcript-capture, and progress docs.

### Risks / assumptions

- This is a browser-side data-validation control. It discourages blind copy/paste in the UI, but it is not a server-side content export policy.
- A section is treated as reviewed once the section-bottom sentinel becomes visible in the viewport.
- Hidden panes and zero-geometry review targets are not treated as reviewed.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content readers, logs, or admin/leader views were added.
- Ownership rules preserved: all generated-note content still comes from the existing owner-only transcribe workspace payload.
- Deletion semantics preserved: no deletion or retention paths changed.
- Provider rules preserved: STT, LLM, and de-identification provider resolution are unchanged.
- Structured-note contract preserved: EMIS section keys, template validation, and generated-note JSON handling are unchanged.

## 2026-04-20 Non-Empty Session Delete Confirmation

### Scope

- Added an owner-only `has_transcript_content` read-model flag for transcript list items.
- Wired the transcribe session rail to require browser confirmation when deleting any selected session with non-empty transcript content.
- Kept deletion on the existing owner-scoped immediate transcript-root delete API.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none for this slice.

### Files changed

- `app/schemas/transcripts.py`: add the derived `has_transcript_content` list/detail field.
- `app/web/transcribe_workspace.py`: populate the field from decrypted owner draft text or committed transcript-version existence.
- `app/templates/transcribe/_sidebar.html`: expose the boolean as a checkbox data attribute.
- `app/static/js/transcribe/app.js`: refresh the checkbox data attribute from workspace payloads.
- `app/static/js/transcribe/actions.js`: prompt before deleting selected non-empty sessions.
- `tests/test_admin_ui.py`: cover sidebar flags and client confirmation wiring.
- `tests/test_api.py`: cover workspace API content-flag serialization.
- `docs/api.md`, `docs/testing.md`, `docs/transcript-capture.md`, `docs/progress.md`: document the behavior and coverage.

### Tests

- `python3 -m py_compile app/schemas/transcripts.py app/web/transcribe_workspace.py`
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "bulk_delete_selected_sessions or marks_non_empty_sessions_for_delete_confirmation or transcribe_frontend_uses_global_template_selector"'`
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_endpoint_returns_owner_workspace_state"'`
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_endpoint_reuses_unwrapped_owner_dek_for_multiple_fields or transcribe_workspace_endpoint_uses_row_kek_metadata_for_dek_unwrap"'`

### Documentation

- Updated API, testing, transcript-capture, and progress docs.

### Risks / assumptions

- Confirmation is client-side, matching existing browser delete confirmations; direct API deletion remains immediate after an authorized request.
- Non-empty means decrypted draft text has non-whitespace content or at least one committed transcript version exists.

### Architecture checkpoint summary

- Privacy boundaries preserved: inactive session transcript text is not rendered; the rail receives only a boolean owned-content flag.
- Ownership rules preserved: workspace and delete routes remain owner-scoped.
- Deletion semantics preserved: confirmed deletion still hard-deletes the transcript root and cascades derived children immediately.
- Provider rules preserved: provider resolution and credentials are unchanged.
- Structured-note contract preserved: structured-note JSON handling is unchanged.

## 2026-04-19 Delete Confirmation Hardening

### Scope

- Added a selected-note permanent-delete control in the transcribe workspace with an explicit browser confirmation before calling the owner-only generated-document delete API.
- Verified existing user-account and team delete forms already expose permanent-delete confirmation prompts.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none for this slice.

### Files changed

- `app/templates/transcribe/_workspace.html`: add the selected-note delete button next to note-version controls.
- `app/static/js/transcribe/app.js`: wire the note delete button and active generated-note id into transcribe actions.
- `app/static/js/transcribe/actions.js`: add confirmed selected-note deletion using the existing generated-document delete API.
- `tests/test_admin_ui.py`: assert note, user-account, and team confirmation surfaces are rendered/wired.
- `docs/testing.md`: note regression coverage for confirmed note deletion.
- `docs/transcript-capture.md`: document the confirmed selected-note delete control.
- `docs/progress.md`: add this progress note.

### Tests

- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "delete_team_user or generated_document_switchers or lists_teams_users_and_account_requests or transcribe_frontend_uses_global_template_selector"'`

### Documentation

- Updated `docs/testing.md`, `docs/transcript-capture.md`, and this progress note.

### Risks / assumptions

- Confirmation remains a browser-side control, matching the existing account/team delete pattern.
- Direct API deletion remains protected by existing owner/admin authorization and still performs immediate hard delete after a valid request.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript or note content is exposed to admins/leaders or logs.
- Ownership rules preserved: note deletion still uses the generated-document service, which restricts access to the owning user.
- Deletion semantics preserved: deletion remains immediate and permanent once confirmed; no soft-delete or undo layer was introduced.
- Provider rules preserved: provider selection and fallback are unchanged.
- Structured-note contract preserved: note JSON shape and allowed EMIS section behavior are unchanged.

## 2026-04-19 Hazard Log Discussion Table

### Scope

- Converted `docs/Compliance/Hazard Log/hazard_discussion.txt` into a Markdown OpenScribe hazard log table.
- Preserved the example hazard-log structure with hazard assessment fields, cause rows, design/training/business-process controls, evidence fields, and initial risk fields.
- Added the supplied risk matrix and populated initial consequence, likelihood, and risk scores as working assessments.

### Checklist

- Code complete: not applicable; documentation-only change.
- Tests added/updated: not applicable; validated by file inspection and Markdown table column checks.
- Docs added/updated: yes.
- Open issues: risk ratings need clinical safety review; implementation status for several proposed controls remains `TBC`; residual risk columns were not added because the supplied example structure only includes initial risk fields.

### Files changed

- `docs/Compliance/Hazard Log/openscribe_hazard_log.md`: hazard log table produced from the discussion transcript, including the supplied risk matrix and initial working risk assessments.
- `docs/progress.md`: progress note for this documentation slice.

### Tests

- Checked the generated Markdown table shape with a pipe-count pass: risk matrix rows have the expected 6 columns and hazard rows have the expected 16 data columns.
- Checked that placeholder risk text from the first draft was removed.

### Documentation

- Added `docs/Compliance/Hazard Log/openscribe_hazard_log.md`.
- Updated this progress note.

### Risks / assumptions

- Initial ratings use the provided matrix and are working assessments, not final clinical safety sign-off.
- Controls are marked existing, proposed, or requiring confirmation where the transcript did not prove implementation status.
- Residual risk was left out because the current example table does not define residual-risk columns.
- Team-leader full user deletion remains an architecture checkpoint because the discussion describes it, while the project rules state leaders should lock/deactivate users rather than fully delete them.

### Architecture checkpoint summary

- Privacy boundaries preserved: the generated table does not include patient-identifiable examples, transcript text, note text, prompts, provider secrets, or tokens.
- Ownership rules preserved: the table continues to treat transcript-derived content as owner-only and flags patient-identifying display policy as needing review.
- Deletion semantics preserved: deletion hazards are documented as immediate/permanent and include confirmation and governance controls rather than weakening deletion semantics.
- Provider rules preserved: provider hazards are framed around controlled provisioning, local validation, and model suitability rather than exposing provider secrets or changing provider resolution.
- Structured-note contract preserved: generated-note and EPR-transfer hazards do not alter the EMIS structured-output contract.

## 2026-04-19 Hazard Log Reference

### Scope

- Added a hazard-log reference note for converting future walkthrough/discussion transcripts into OpenScribe hazard-log rows.
- Captured the target table structure from the compliance example, including cause rows, control categories, evidence fields, and initial risk fields.
- Documented OpenScribe-specific privacy, ownership, deletion, encryption, provider, and structured-note boundaries to keep hazard logging aligned with the product architecture.

### Checklist

- Code complete: not applicable; documentation-only change.
- Tests added/updated: not applicable; validated by markdown/file inspection.
- Docs added/updated: yes.
- Open issues: future hazard batches still need clinical discussion content and risk ratings.

### Files changed

- `docs/Compliance/Hazard Log/hazard_log_reference.md`: reference guide for parsing hazard-log discussion transcripts into the agreed table structure.
- `docs/progress.md`: progress note for this documentation slice.

### Tests

- Lightweight file inspection only; no application code or executable documentation checks were changed.

### Documentation

- Added `docs/Compliance/Hazard Log/hazard_log_reference.md`.
- Updated this progress note.

### Risks / assumptions

- The reference guide avoids inventing clinical risk ratings or controls. Missing risk, evidence, or control details should remain `TBC`, `Gap`, or `Planned` until confirmed during the hazard discussion.

### Architecture checkpoint summary

- Privacy boundaries preserved: the guide explicitly forbids patient-identifiable data, transcript text, note text, prompts, model responses, provider secrets, and token material in the hazard log.
- Ownership rules preserved: future controls must keep transcript-derived content owner-only and must not treat admin or team-leader metadata authority as content access.
- Deletion semantics preserved: future hazard entries must align with immediate deletion and transcript-root cascade rules.
- Provider rules preserved: provider credentials remain Vault references, and provider-selection changes are called out as architecture-sensitive.
- Structured-note contract preserved: the EMIS allowed section keys and strict structured-output contract are recorded as boundaries.

## 2026-04-18 Live Recording Status Stabilization

### Scope

- Moved live recording feedback into the existing status pill with compact labels such as `listening`, `speech detected`, `sending chunk`, `stopping`, and `uploading`.
- Removed the visible mic-status line beneath the recording controls and the visible blocked-new-session warning from the sidebar layout.
- Blocked session-rail switching and new consultation creation while recording is active, with toast feedback instead of layout-changing inline text.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no backend route changes in this slice; existing owner/API guards remain the server-side safety net.

### Files changed

- `app/templates/transcribe/_workspace.html`: mark the status pill for JS styling, stabilize its width, and make mic-status screen-reader-only.
- `app/templates/transcribe/_sidebar.html`: remove the inline blocked-new-session warning and keep the new-session trigger visually stable.
- `app/static/js/transcribe/app.js`: map live recording progress into status-pill labels and stop toggling sidebar warning text.
- `app/static/js/transcribe/actions.js`: block session switches/new consultations during active recording with warning toasts.
- `tests/test_admin_ui.py`: add regression coverage for hidden mic-status text, stable sidebar markup, and recording switch guards.
- `docs/testing.md`, `docs/transcribe_brief.md`, and `docs/transcript-capture.md`: document toast-based blocked actions and status-pill recording feedback.

### Tests

- `tests/test_admin_ui.py` coverage updated for live-recording UI markup, sidebar warning removal, and frontend recording guards.

### Documentation

- Updated `docs/testing.md`, `docs/transcribe_brief.md`, `docs/transcript-capture.md`, and this progress note.

### Risks / assumptions

- This blocks client-side session switching while the browser/live recording state is active or the active transcript status is still `recording`; backend ownership and lifecycle checks still govern actual API access.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript content is exposed to new actors or routes.
- Ownership rules preserved: session switching still uses owner-scoped workspace APIs, and blocked switches stop navigation before a new workspace fetch.
- Deletion semantics preserved: no transcript deletion, retention, or cascade behavior changed.
- Provider rules preserved: STT/LLM provider configuration and fallback behavior are untouched.
- Structured-note contract preserved: structured note JSON, section keys, and generated-document persistence are unchanged.

## 2026-04-18 Structured Section Copy Buttons

### Scope

- Added per-section copy buttons to structured note section headers on the transcribe workspace.
- Wired copied output to include the section label followed by all non-empty lines in that section.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice.

### Files changed

- `app/templates/transcribe/_workspace.html`: render section copy buttons beside server-rendered structured section headers.
- `app/templates/transcribe/_head_assets.html`: style structured section headers so titles stay left and copy buttons sit on the right edge.
- `app/static/js/transcribe/structured.js`: render matching copy buttons for refreshed/dynamic structured sections and expose section-line collection.
- `app/static/js/transcribe/actions.js`: handle section copy clicks with the existing clipboard/toast/status pattern.
- `app/static/js/transcribe/app.js`: pass the structured panel into action wiring for delegated section copy handling.
- `tests/test_admin_ui.py`: assert rendered section copy controls and frontend wiring remain present.
- `docs/testing.md` and `docs/transcribe_brief.md`: document section-level structured note copy behavior.

### Tests

- `tests/test_admin_ui.py` coverage updated for structured section copy controls in server-rendered HTML and JS wiring.

### Documentation

- Updated `docs/testing.md`, `docs/transcribe_brief.md`, and this progress note.

### Risks / assumptions

- Section copy copies all non-empty lines in that section, independent of checkbox selection, so it does not disturb existing selected-line copy behavior.

### Architecture checkpoint summary

- Privacy boundaries preserved: copy reads only content already rendered in the owner-facing workspace and uses browser clipboard APIs.
- Ownership rules preserved: no new route or backend content fetch was added.
- Deletion semantics preserved: no transcript or generated-document lifecycle behavior changed.
- Provider rules preserved: provider configuration, selection, and fallback logic are untouched.
- Structured-note contract preserved: EMIS section keys, JSON output shape, persistence, and validation are unchanged.

## 2026-04-18 Freeform Template Section Guardrails

### Scope

- Hid EMIS section prompt fields in the template editor unless the selected template mode is `structured`.
- Stopped browser form handlers from constructing template section config for `freeform` saves before persistence.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice.

### Files changed

- `app/templates/template_editor.html`: hide/show EMIS section prompts from the mode selector and keep structured prompts available when needed.
- `app/main.py`: add a mode-aware template-config form helper so freeform submissions never build section config.
- `app/routes/web_home_transcribe.py`: use the mode-aware helper for personal/team template saves.
- `app/routes/web_admin.py`: use the mode-aware helper for default template saves.
- `tests/test_admin_ui.py`: add regression coverage for hidden freeform section UI and freeform saves persisting `config_json=None`.
- `docs/testing.md`: record the new template-editor regression coverage.

### Tests

- `tests/test_admin_ui.py` coverage added for freeform template editor section visibility and freeform form-post persistence.

### Documentation

- Updated `docs/testing.md` and this progress note.

### Risks / assumptions

- Existing API/service callers could still submit ignored `config_json` for freeform mode, but persistence continues to drop it via service-layer serialization; this slice specifically tightens the browser/editor path.

### Architecture checkpoint summary

- Privacy boundaries preserved: this only changes template configuration UI and request shaping, with no transcript-derived content exposure.
- Ownership rules preserved: personal, team, and default template saves stay inside the same existing role-scoped routes and services.
- Deletion semantics preserved: no transcript, document, or template deletion behavior changed.
- Provider rules preserved: provider selection, credentials, and fallback logic are untouched.
- Structured-note contract preserved: structured templates still use the same EMIS section keys and config shape; freeform templates now avoid section config earlier in the request path.

## 2026-04-17 Split LLM Token Counts

### Scope

- Normalized generation usage handling so OpenScribe tracks input tokens separately from output tokens before persisting document and usage-event metadata.
- Kept existing ownership, generation, and provider behavior unchanged while continuing to derive total tokens from the same run metadata.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: historical rows remain as previously stored; this slice only affects new generation runs.

### Files changed

- `app/services/templates.py`: normalize provider usage into `input_tokens` and `output_tokens`, then map those explicitly into generated-document and provider-usage persistence.
- `tests/test_api.py`: update generation-service mocks and add API regression coverage for split token counts in generated-document responses.
- `docs/api.md`: clarify that generation metadata stores input/output/total token counts.
- `docs/transcript-capture.md`: document split token persistence in generation telemetry.

### Tests

- `tests/test_api.py` coverage updated for OpenAI/Ollama generation usage normalization and generated-document token fields.

### Documentation

- Updated `docs/api.md`, `docs/transcript-capture.md`, and this progress note.

### Risks / assumptions

- This keeps the existing provider-usage-event column names (`prompt_tokens` and `completion_tokens`) for schema compatibility while normalizing runtime handling to input/output semantics.

### Architecture checkpoint summary

- Privacy boundaries preserved: only metadata counts/durations changed; no transcript, note, prompt, or provider-secret exposure was added.
- Ownership rules preserved: token metadata remains attached to the same owner-scoped generated documents and usage events.
- Deletion semantics preserved: transcript-root and generated-document cleanup paths are unchanged.
- Provider rules preserved: provider selection, credentials, and fallback logic are unchanged; only usage-field normalization changed.
- Structured-note contract preserved: no template/output JSON shape or EMIS section behavior changed.

## 2026-04-17 Admin Flat Workspace Layout

### Scope

- Moved the system-admin page away from card/tab chrome into a flat sidebar workspace layout aligned with the template editor direction.
- Kept existing admin routes, forms, provider flows, account lifecycle actions, default assets, requests, and usage behavior unchanged.
- Moved team/user creation controls into the Directory area so sidebar areas map to the work they contain.
- Updated LLM team policy controls so visible user models render as multi-column selectable tiles, and team default model is chosen from a dropdown populated by enabled models.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: provider setup and team policy still share one Providers pane; a later slice can split them without changing backend authority.

### Files changed

- `app/templates/admin.html`: add admin sidebar shell, flat pane styling, sidebar area navigation, and directory-local create controls.
- `app/templates/admin.html`: render LLM visible models as on/off tiles and sync default model dropdown from enabled models.
- `tests/test_admin_ui.py`: add regression coverage for the flat sidebar admin layout and LLM visible-model/default-dropdown controls.
- `docs/admin_brief.md`: record the current visual direction for `/admin`.
- `docs/testing.md`: note admin flat layout coverage.

### Tests

- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "admin_page_uses_flat_sidebar_workspace_layout or admin_restyled_preview_route_renders_for_system_admin or admin_page_can_save_team_stt_config_for_selected_team or admin_page_can_manage_default_assets or admin_page_lists_teams_users_and_account_requests or admin_page_usage_tab_shows_team_and_user_telemetry"'`
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "admin_page_"`
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "admin_llm_selection_uses_visible_model_tiles_and_default_dropdown or admin_page_uses_flat_sidebar_workspace_layout"'`
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "admin_page_ or admin_llm_selection_uses_visible_model_tiles_and_default_dropdown"'`

### Documentation

- Updated `docs/admin_brief.md`, `docs/testing.md`, and this progress note.

### Risks / assumptions

- This is intentionally a layout-only change. Provider setup and team policy remain in the same backend tab for redirect compatibility.
- The existing `/admin-restyled` alias continues to render the same live admin template.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript, note, prompt, model response, or redaction-source content is exposed.
- Ownership rules preserved: `/admin` remains system-admin-only and does not add transcript-derived object access.
- Deletion semantics preserved: destructive user/team/default/provider actions keep their existing POST forms and explicit confirmations.
- Provider rules preserved: provider secrets remain write-only and Vault-backed; provider provisioning and selection forms still post to the same routes.
- Structured-note contract preserved: default template editing still uses the existing template editor and EMIS section rules.

## 2026-04-15

### Scope

- Updated current `/transcribe` workspace partials and JS-rendered follow-up cards to use Lucide icons instead of inline SVG markup.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice

### Files changed

- `app/templates/transcribe/_head_assets.html`: load Lucide runtime pinned to `1.8.0`.
- `app/templates/transcribe/_sidebar.html`: swap sidebar icons to Lucide.
- `app/templates/transcribe/_workspace.html`: swap workspace, note, and follow-up icons to Lucide.
- `app/templates/transcribe/_shell_extras.html`: add shared Lucide refresh hook for dynamic DOM updates.
- `app/static/js/transcribe/app.js`: refresh dynamic record-toggle icon via Lucide and re-query replaced icon node after each Lucide render.
- `app/static/js/transcribe/documents.js`: refresh Lucide icons after follow-up history rerenders.
- `tests/test_admin_ui.py`: update UI/source assertions for Lucide-based markup.

### Tests

- `tests/test_admin_ui.py` assertions cover pinned Lucide runtime include, transcribe template placeholders, and JS refresh hooks for dynamic icons.

### Documentation

- Added daily progress note in `docs/progress.md`.

### Risks / assumptions

- Pinned Lucide CDN include to `1.8.0`, current latest npm dist-tag at time of change.

### Architecture checkpoint summary

- Privacy boundaries unchanged: icon-only UI markup updates, no transcript content handling changed.
- Ownership rules unchanged: no auth, query, or access-path changes.
- Deletion semantics unchanged: delete controls preserved, only icon markup changed.
- Provider rules unchanged: no STT/LLM selection or fallback logic changed.
- Structured-note contract unchanged: no section keys, JSON shape, or note persistence behavior changed.

## 2026-04-15 Default Asset Seeding

### Scope

- Added system-admin managed default templates and default quick actions, plus automatic seeding of active defaults into each newly created team.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice

### Files changed

- `app/models.py`: add default template and default quick action blueprint tables.
- `alembic/versions/0a1b2c3d4e6f_add_default_asset_blueprints.py`: create default asset tables.
- `app/schemas/templates.py`: add default asset upsert schemas.
- `app/schemas/__init__.py`: export default asset schemas.
- `app/services/default_assets.py`: add default asset CRUD and team-seeding logic.
- `app/services/admin.py`: seed active defaults during team creation in one transaction.
- `app/web/presentation.py`: render default asset state in admin views and default template editor.
- `app/routes/web_admin.py`: add system-admin browser routes for default assets and default template editor.
- `app/routes/api_routes.py`: pass acting system admin into team creation service.
- `app/templates/admin.html`: add Defaults admin tab and quick-action management UI.
- `app/templates/template_editor.html`: reuse template editor UI for default templates.
- `tests/conftest.py`: add default asset factories.
- `tests/test_admin_ui.py`: add admin default asset CRUD and team-seeding coverage.
- `tests/test_migrations.py`: assert default asset tables and indexes exist at head.
- `docs/testing.md`: note default asset UI and team seeding coverage.

### Tests

- `./.venv/bin/pytest tests/test_admin_ui.py -k 'admin_page_'`
- `./.venv/bin/pytest tests/test_admin_ui.py -k 'team_template_editor_page_keeps_team_scope_for_new_template or leader_home_can_create_team_template or user_transcribe_page_shows_workspace_shell'`
- `./.venv/bin/pytest tests/test_migrations.py -k 'upgrade_head_creates_expected_schema or head_adds_onboarding_and_session_tables'`

### Documentation

- Updated `docs/testing.md` regression coverage notes.
- Added progress entry here.

### Risks / assumptions

- Default asset edits only affect future teams. Existing seeded team copies remain independent.
- Seeded assets are created with acting system-admin user as `created_by_user_id`.

### Architecture checkpoint summary

- Privacy boundaries preserved: defaults are non-transcript config only.
- Ownership rules preserved: seeded copies become team-owned assets; defaults stay admin-only config.
- Deletion semantics preserved: deleting default assets does not mutate existing team copies.
- Provider rules preserved: no STT/LLM resolution changes.
- Structured-note contract preserved: default templates reuse existing EMIS validation and section rules.

## 2026-04-16 Team Default Import

### Scope

- Added a one-off import path to copy team-scoped templates and quick actions into the admin-managed default asset library, then used it to bootstrap defaults from an existing team.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice

### Files changed

- `app/services/default_assets.py`: add team-to-default import helper with editor-equivalent name normalization, skip-existing idempotency, and freeform quick-action normalization.
- `scripts/import_team_defaults.py`: add one-off operator script for importing a source team's assets into defaults, requiring explicit source team input.
- `tests/test_admin_ui.py`: add regression coverage for normalized-name import behavior, idempotency, and quick-action mode normalization.
- `docs/testing.md`: note import regression coverage.

### Tests

- `./.venv/bin/pytest tests/test_admin_ui.py -k 'import_team_assets_to_defaults or admin_team_creation_seeds_active_default_assets'`

### Documentation

- Updated `docs/testing.md` coverage notes.
- Added progress entry here.

### Follow-up fixes

- Re-ran statement textarea autosize after note panels become visible so long lines wrap on first render instead of waiting for an edit event.
- Made the transcript pane in the history tab independently scrollable inside the split workspace.

### Risks / assumptions

- Import copies the latest team asset version only for assets that do not already exist in defaults.
- Import normalizes source asset names with the same trim rules as the editor, then skips existing default asset names so reruns are idempotent.
- Import preserves each source asset's `is_active` flag, and imported quick actions are normalized to freeform to match downstream generation rules.

### Architecture checkpoint summary

- Privacy boundaries preserved: imported assets remain config-only and contain no transcript-derived content.
- Ownership rules preserved: source assets stay team-owned, imported defaults stay admin-managed, seeded outputs still become team-owned copies.
- Deletion semantics preserved: importing defaults does not alter or link back to the source team assets.
- Provider rules preserved: no provider-selection or fallback behavior changed.
- Structured-note contract preserved: structured template mode and JSON config are copied without changing allowed section rules.

## 2026-04-16 Team Hard Delete

### Scope

- Added system-admin team deletion from the admin screen with explicit cleanup of team users, team-owned config/assets, team-linked account requests, transcript-derived rows, and team usage metadata.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice

### Files changed

- `app/services/admin.py`: add explicit team hard-delete service and shared user-row delete helper.
- `app/routes/web_admin.py`: add admin browser route for deleting teams.
- `app/templates/admin.html`: add team delete action in the admin directory table.
- `app/main.py`: export team delete service alias for route use.
- `tests/test_admin_ui.py`: add regression coverage for team hard delete across users, providers, assets, and metadata rows.
- `docs/testing.md`: note team delete regression coverage.

### Tests

- `./.venv/bin/pytest tests/test_admin_ui.py -k 'admin_page_can_delete_team_and_owned_records or admin_team_creation_seeds_active_default_assets'`

### Documentation

- Updated `docs/testing.md` coverage notes.
- Added progress entry here.

### Risks / assumptions

- Team hard delete removes team usage metadata rows instead of retaining them with null foreign keys.
- Team hard delete rejects any unexpected team-linked system-admin user rows rather than deleting them silently.

### Architecture checkpoint summary

- Privacy boundaries preserved: team delete removes transcript-derived content instead of widening access.
- Ownership rules preserved: only system admins can hard-delete teams; all team users and team-owned assets are removed.
- Deletion semantics preserved: confirmed immediate hard delete with no grace period or orphaned team-owned rows.
- Provider rules preserved: team-scoped STT/LLM/de-identification rows and credential refs are explicitly cleaned up.
- Structured-note contract preserved: no structured output shape or template semantics changed outside deletion cleanup.

## 2026-04-20 Review Follow-Up Hardening

### Scope

- Fixed review findings around metadata deletion, de-identification provider validation, external redaction span handling, and home tab initialization while keeping team-selectable de-identification with built-in global fallback.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice.

### Files changed

- `app/schemas/deidentification.py`: reject secret-bearing extra headers so provider secrets stay in Vault-backed bearer-token storage.
- `app/services/deidentification.py`: require a bearer token when enabling bearer auth without an existing saved secret; retain built-in provider fallback behavior.
- `app/services/redaction.py`: normalize and policy-filter generic REST de-identification spans before redaction.
- `app/services/admin.py`: reassign metadata FK references before user deletion and preflight system-admin team membership before Vault cleanup during team deletion.
- `app/routes/web_admin.py` and `app/routes/web_home_transcribe.py`: keep template-mode parsing inside handled validation paths.
- `app/templates/home.html`: initialize tabs after nav relocation.
- `tests/test_api.py` and `tests/test_admin_ui.py`: add regression coverage for the above.
- `docs/testing.md`: document the added coverage.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider_rejects_secret_headers or deidentification_runtime_falls_back_to_builtin or generic_rest_deidentification_spans or system_admin_delete_reassigns_metadata"`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "home_tab_script_finds_relocated_tab_nav or admin_team_delete_checks_system_admin_members_before_vault_cleanup"`: passed.

### Documentation

- Updated `docs/testing.md`.
- Added progress entry here.

### Risks / assumptions

- Team-selected de-identification is intentional; runtime resolution falls back to the built-in global provider when the selected provider is inactive, missing, or no longer assigned.
- Secret-bearing arbitrary HTTP headers are blocked for de-identification providers; callers should use the dedicated `bearer_token` field so secrets remain in Vault.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript or generated-note access changes; external de-identification receives text only at the existing lazy redaction point.
- Ownership rules preserved: user hard-delete still removes owned transcript-derived content and only reassigns non-content metadata attribution.
- Deletion semantics preserved: team delete still hard-deletes immediately, with system-admin blockers checked before side-effecting Vault cleanup.
- Provider rules preserved: STT/LLM provider behavior unchanged; de-identification keeps built-in global fallback when selected team provider is invalid.
- Structured-note contract preserved: no EMIS section keys or structured JSON output shape changed.

## 2026-04-20 De-identification and Cleanup Rule Alignment

### Scope

- Updated agent architecture instructions for team-scoped de-identification provider selection with built-in legacy/native Presidio fallback.
- Documented HTTPS-only remote de-identification transfer, with localhost/LAN/private/link-local exceptions.
- Hardened team deletion so STT/LLM Vault secret cleanup runs only after DB cleanup commits.
- Made built-in de-identification provider seeding transaction-safe by removing the internal commit from read/selection paths.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: team deletion uses post-commit best-effort provider secret cleanup; a durable retry/outbox would be stronger if Vault is unavailable after DB commit.

### Files changed

- `AGENTS.md` and `agents.md`: align provider and deletion rules with the team-scoped de-identification decision.
- `app/services/deidentification.py`: keep built-in provider creation inside the caller transaction.
- `app/services/admin.py`: defer team STT/LLM Vault cleanup until after successful DB commit and rollback on cleanup errors before commit.
- `tests/test_api.py`: cover built-in provider helper transaction boundaries.
- `tests/test_admin_ui.py`: cover deferred Vault cleanup during failed team delete.
- `docs/testing.md`: document the new regression coverage.
- `docs/progress.md`: add this progress entry.

### Tests

- `.venv/bin/pytest -q tests/test_api.py tests/test_admin_ui.py -k "ensure_builtin_deidentification_provider_does_not_commit_caller_transaction or admin_team_delete_defers_vault_cleanup_until_after_db_commit or admin_team_delete_checks_system_admin_members_before_vault_cleanup"`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider_rejects_secret_headers or deidentification_runtime_falls_back_to_builtin or generic_rest_deidentification_spans or system_admin_can_provision_assign_and_leader_select_deidentification_provider or leader_cannot_select_unassigned_deidentification_provider or redaction_run_uses_selected_team_deidentification_provider"`: passed.

### Documentation

- Updated `AGENTS.md`, `agents.md`, and `docs/testing.md`.
- Added progress entry here.

### Risks / assumptions

- Team-selected de-identification is intentional architecture: leaders select assigned providers for their team; runtime falls back to built-in Presidio when selection is absent or invalid.
- Remote de-identification providers can receive transcript-derived text during lazy redaction, so HTTPS/non-local validation and admin provisioning remain mandatory.
- Provider secret cleanup after team DB deletion is best-effort logging if Vault delete fails post-commit; no DB row remains pointing at a missing secret.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content readers; external de-identification remains limited to the lazy redaction path and configured providers.
- Ownership rules preserved: de-identification selection remains team-scoped; transcript-derived content access remains owner-only.
- Deletion semantics preserved: team hard delete still removes team content immediately after explicit confirmation, and DB rollback no longer leaves provider rows pointing to deleted Vault secrets.
- Provider rules preserved: STT/LLM rules unchanged; de-identification is team-selectable with built-in fallback and HTTPS-only remote transfer outside local networks.
- Structured-note contract preserved: no EMIS section keys or structured JSON output shape changed.

## 2026-04-20 De-identification Vault Lifecycle Review Fixes

### Scope

- Rejected secret-bearing keys in de-identification REST body config, matching the existing header secret rule.
- Moved de-identification bearer-token replacement to a new Vault secret ref so DB rollback cannot leave old provider metadata pointing at a new secret.
- Deferred old/deleted provider secret cleanup until after the DB commit removes or replaces the DB reference.
- Fixed `.gitignore` trailing whitespace.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: post-commit Vault cleanup remains best-effort logged; a durable retry queue would be stronger for long Vault outages.

### Files changed

- `app/schemas/deidentification.py`: reject secret-like keys in `extra_body_json`.
- `app/services/vault.py`: support de-identification secret refs with per-write secret ids and ref-based read/delete.
- `app/services/deidentification.py`: commit DB references before deleting old provider secrets, and clean pending replacement secrets on DB commit failure.
- `tests/conftest.py`: update Vault stubs for ref-based de-identification secret helpers.
- `tests/test_api.py`: add focused de-identification provider secret lifecycle regressions.
- `docs/testing.md`: document the new coverage.
- `docs/progress.md`: add this progress entry.
- `.gitignore`: remove trailing whitespace.

### Tests

- `git diff --check`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification_provider_rejects_secret_headers or deidentification_provider_upsert_cleans_pending_secret or deidentification_provider_delete_defers_vault_cleanup"`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "deidentification"`: passed.

### Documentation

- Updated `docs/testing.md`.
- Added progress entry here.

### Risks / assumptions

- De-identification token replacement writes the new token before DB commit, but to a new ref; failed DB commits trigger best-effort deletion of only that pending ref.
- If old secret cleanup fails after a successful DB commit, no DB row points to the old secret, and cleanup is logged for operators.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript or generated-note access changes.
- Ownership rules preserved: de-identification provider provisioning remains system-admin only; team selection rules unchanged.
- Deletion semantics preserved: provider DB references are removed/replaced before corresponding Vault secret deletion.
- Provider rules preserved: raw de-identification secrets remain Vault-backed; arbitrary persisted headers/body fields cannot carry obvious secrets.
- Structured-note contract preserved: no EMIS section keys or structured JSON output shape changed.

## 2026-04-24 Transcribe Header Toolbar

### Scope

- Moved the transcribe note-selection actions (`Clear`, `Select all`, `Copy selected`) from the note body into the note header action row beside `Create`, while keeping the existing sidebar and panel hierarchy intact.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none noted for this UI-only slice.

### Files changed

- `app/templates/transcribe/_workspace.html`: relocate the note selection controls into the note header action row and add a header-specific hook for coverage.
- `app/templates/transcribe/_head_assets.html`: update the header action row and toolbar layout so the controls sit beside `Create` and remain responsive.
- `tests/test_admin_ui.py`: assert the transcribe page renders the header toolbar hook.
- `docs/transcribe-playwright-checklist.md`: note the expected header-row placement in manual/browser checks.
- `docs/progress.md`: add this progress entry.

### Tests

- `tests/test_admin_ui.py`: updated the existing transcribe freeform editor render check to confirm the footer toolbar is present.

### Documentation

- Updated `docs/transcribe-playwright-checklist.md`.
- Added progress entry here.

### Risks / assumptions

- This change preserves the existing `data-*` selectors so the current transcribe JS continues to find and control the same buttons after relocation.
- The note header action row now wraps on narrower widths so the copy controls do not compress or reorder the surrounding sidebar/pane layout.

### Architecture checkpoint summary

- Privacy boundaries preserved: the change is limited to client-side layout and does not alter transcript or generated-note visibility.
- Ownership rules preserved: no content access paths or ownership checks changed.
- Deletion semantics preserved: no deletion flows, retention roots, or cascade behavior changed.
- Provider rules preserved: provider selection and fallback logic are untouched.
- Structured-note contract preserved: structured/freeform note data shape and copy-selection behavior remain unchanged.

## 2026-04-23 De-identification Web UI Slice

### Scope

- Added the missing browser UI for de-identification provider management so system admins can provision and assign providers in `/admin`, and team leaders can choose the active team de-identification provider in `/home`.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: targeted pytest coverage is blocked in this environment until the test Postgres instance is available.

### Files changed

- `app/web/presentation.py`: add de-identification render context, form defaults, and JSON map parsing helper.
- `app/routes/web_admin.py`: add admin browser handlers for de-identification provider upsert/delete and team assignment removal.
- `app/routes/web_home_transcribe.py`: add leader browser handlers for de-identification selection and clear.
- `app/templates/admin.html`: add provider provisioning, assignment, and team-selection visibility for de-identification.
- `app/templates/home.html`: add a de-identification card to AI services for leader selection.
- `tests/test_admin_ui.py`: add focused admin/home UI coverage for de-identification management.
- `docs/testing.md`: record the new UI coverage expectations.

### Tests

- `.venv/bin/python -m py_compile app/web/presentation.py app/routes/web_admin.py app/routes/web_home_transcribe.py tests/test_admin_ui.py`: passed.
- `bash -lc 'export $(grep -v ^# .env | xargs); .venv/bin/pytest -q tests/test_admin_ui.py -k "deidentification or ai_service"'`: blocked by local test DB bootstrap (`psycopg.OperationalError` while connecting in `tests/conftest.py`).

### Documentation

- Updated `docs/testing.md`.
- Added progress entry here.

### Risks / assumptions

- The admin provider form intentionally supports the existing configurable `generic_rest` adapter only; the built-in native Presidio provider remains read-only and always available as fallback.
- Provider provisioning remains visible only within the selected-team admin workflow even though the provider rows themselves are global admin-managed records.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript or generated-note access changed; the UI only manages provider metadata and selections.
- Ownership rules preserved: transcript-derived content remains owner-only; leader access is limited to team-scoped provider selection metadata.
- Deletion semantics preserved: assignment removal and selection clear immediately fall back to the built-in provider without changing transcript-root cascade behavior.
- Provider rules preserved: system admins provision and assign providers, leaders select from assigned options, and clearing/invalid selection still falls back to built-in Presidio.
- Structured-note contract preserved: no EMIS section keys or structured JSON output shape changed.
