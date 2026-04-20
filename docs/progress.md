# Progress

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
