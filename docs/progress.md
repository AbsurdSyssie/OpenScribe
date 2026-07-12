# Progress

## 2026-07-12 Contextual Provider Wizard Errors

### Scope

- Replaced small STT/LLM wizard error text with prominent accessible alerts and safe provider-specific guidance.
- Preserved API error status, code, message, and explicit field name without retaining arbitrary nested details.
- Highlighted explicitly identified invalid controls and focused either that control or the alert.

### Checklist

- Target behavior: provider failures remain visible, actionable, accessible, and free of raw provider or secret detail.
- Affected schema/modules/endpoints: admin mockup template JavaScript/CSS only; no backend or schema change.
- Affected tests: focused admin provider wizard template regression.
- Architecture risks: server error messages remain the trusted user-safe summary; nested API details are discarded except allowlisted field targeting.
- Docs referenced/updated: `docs/testing.md`, `docs/progress.md`.
- Reuse decision: one shared alert renderer and guidance mapper serve both LLM and STT wizards.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/templates/admin_mockup.html`: alert markup/styles, structured error parsing, guidance, field state, and focus handling.
- `tests/test_admin_ui.py`: safe structured parsing, alert accessibility/style, guidance, and field-error regressions.
- `docs/testing.md`, `docs/progress.md`: test contract and implementation record.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "provider_redesign or provider_wizards_render_safe_contextual_errors or change_llm_connection"`: passed, 3 tests.
- `git diff --check`: passed.

### Architecture checkpoint summary

- Privacy boundaries: arbitrary nested provider responses and secret values are never rendered or retained on client errors.
- Ownership rules: unchanged; existing system-admin provider routes and team scope remain intact.
- Deletion semantics: unchanged; failed draft cleanup now uses same prominent safe alert.
- Provider rules: unchanged; UI guidance does not alter provider inspection, fallback, credential, or selection behavior.
- Structured-note contract: unchanged.
- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: no auth or authorization change.
- Lifecycle/deletion checkpoint: draft cleanup behavior unchanged except error presentation.
- Docs/tests checkpoint: focused regression and docs added.

## 2026-07-12 Transcript Retention Enforcement

### Scope

- Enforced fixed team retention expiry in transcript history, direct detail, and latest-session selection.
- Added bounded transcript-root cleanup and hourly Celery Beat scheduling.
- Preserved root cascades for working notes, versions, generated documents, redaction data, and ingestion jobs.

### Checklist

- Target behavior: expired notes become unavailable immediately and transcript roots are hard-deleted hourly.
- Affected schema/modules/endpoints: transcript service, transcript history/detail APIs, Celery task/config; no schema change.
- Affected tests: expiry boundary, root cascade, bounded/idempotent cleanup, API visibility, scheduler registration, existing Vault-failure deletion fixture.
- Architecture risks: retry-audio Vault deletion remains best-effort under existing deletion semantics; durable cleanup outbox remains open architecture work.
- Docs referenced/updated: `docs/DatabasePlan.md`, `docs/progress.md`.
- Reuse decision: reused transcript-root relationships and retry-source cleanup instead of child-specific note deletion.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: production must run Celery Beat as well as workers; existing transcript expiry timestamps are not recalculated from later team setting changes.

### Files changed

- `app/services/transcripts.py`: active-expiry guards and bounded root cleanup.
- `app/web/transcribe_workspace.py`, `app/routes/api_routes.py`: expired transcript visibility enforcement.
- `app/tasks.py`, `app/celery_app.py`: cleanup task and hourly schedule.
- `tests/test_retention.py`, `tests/test_api.py`: retention and deletion regressions.
- `docs/DatabasePlan.md`, `docs/progress.md`: operational and architecture behavior.

### Tests

- `.venv/bin/pytest -q tests/test_retention.py`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_retention.py tests/test_api.py -k "transcript_delete_is_owner_only or transcript_delete_still_succeeds or can_create_new_session or transcript_history"`: passed, 2 tests selected.
- Full `tests/test_api.py` regression reached 56% with 202 tests passing before exposing unrelated legacy fixtures whose expiry equals creation time; central write-path expiry enforcement was narrowed rather than rewriting unrelated fixtures.

### Architecture checkpoint summary

- Privacy boundaries: expired transcript-derived content is hidden immediately; no content is logged.
- Ownership rules: existing owner-only checks remain; expiry returns not-found only after ownership validation.
- Deletion semantics: cleanup deletes transcript roots and relies on existing cascades; no independent note lifecycle added.
- Provider rules: unchanged; retry-audio Vault cleanup follows existing best-effort deletion behavior.
- Structured-note contract: unchanged; working notes disappear with transcript root.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: owner access remains mandatory; history and direct detail hide expired owner content while cleanup removes its root.
- Lifecycle/deletion checkpoint: fixed expiry now triggers bounded hard deletion; later team setting changes do not extend existing content.
- Docs/tests checkpoint: retention docs and focused tests added.

## 2026-07-02 Legacy Loader, Route, And Usage Spacing Cleanup

### Scope

- Removed obsolete standalone `loading_animation.html` after confirming active note/follow-up loading markup and styles live in transcribe workspace assets.
- Removed obsolete `/transcribe-glm-2` alias and moved its coverage to `/transcribe`.
- Replaced empty 14-day usage charts with a compact no-activity message; real activity keeps the full charts.

### Checklist

- Target behavior: no unused standalone loader or alias route; empty usage dashboard avoids oversized blank chart cards.
- Affected schema/modules/endpoints: transcribe browser route, usage presentation data, admin template/CSS/tests/docs; no schema change.
- Affected tests: loader/route static assertions, normal transcribe route coverage, empty/active usage render coverage.
- Architecture risks: no transcript ownership, data, provider, deletion, or structured-note behavior changed.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: retain active loader in transcribe-specific CSS rather than preserving the unused standalone demo.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: `admin2.html` remains inline by explicit user decision.

### Files changed

- `loading_animation.html`: removed unused standalone demo.
- `app/routes/web_transcribe.py`: removes obsolete `/transcribe-glm-2` alias.
- `app/services/admin.py`, `app/web/presentation.py`: supply `usage_has_activity` for empty-dashboard rendering.
- `app/templates/admin.html`, `app/static/css/admin.css`: compact empty daily-activity state.
- `tests/test_admin_ui.py`, `tests/test_web_refactor.py`: migrate route coverage and add loader/usage regression tests.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: record cleanup.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "legacy_glm_transcribe_route_is_removed or generation_loading_replaces_plain_text_placeholders or user_transcribe_page_uses_workspace_template or user_transcribe_page_renders_workspace_values or user_transcribe_page_exposes_workspace_hooks_and_pane_controls or user_transcribe_page_uses_structured_template_sections or user_transcribe_page_prioritises_latest_note_and_emis_driven_generation or user_transcribe_page_shows_stt_config_label or user_transcribe_page_shows_idle_status_with_team_stt_selected or user_transcribe_page_allows_new_session_when_latest_has_transcript_text or user_transcribe_page_syncs_generation_controls_after_workspace_refresh or admin_page_usage_tab"`: passed, 13 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 13 tests.

### Architecture checkpoint summary

- Privacy boundaries: unchanged.
- Ownership rules: unchanged; `/transcribe` owner checks remain the sole supported workspace route.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no auth/session change.
- Lifecycle/deletion checkpoint: no record lifecycle path changed.
- Docs/tests checkpoint: docs and focused tests updated/passed.

## 2026-07-02 Admin CSS Extraction

### Scope

- Extracted `admin.html` inline style block into `app/static/css/admin.css`.
- Linked `tokens.css`, `components.css`, and `admin.css` from `admin.html`.
- Removed admin-local token root and direct font-family names in favour of shared font variables.
- Moderately harmonised admin surfaces with shared radii and shared feedback primitives while keeping dense admin layout CSS local.

### Checklist

- Target behavior: admin page has no inline CSS, uses shared tokens/components, and preserves all admin form actions, data hooks, and role-gated content.
- Affected schema/modules/endpoints: template/static CSS/tests/docs only; no schema, route, endpoint, provider, or deletion behavior changed.
- Affected tests: static refactor assertions and admin toast/static CSS assertions.
- Architecture risks: visual drift in admin management UI only; destructive/provider controls must remain clear and unchanged semantically.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: use `components.css` for tokens-adjacent primitives and feedback; keep admin shell, provider cards, usage charts, tables, and destructive form grouping in `admin.css`.
- Code complete: yes for admin extraction/harmonisation slice.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: `admin2.html` remains inline by prior special/excluded decision.

### Files changed

- `app/templates/admin.html`: links static stylesheets and removes inline style block.
- `app/static/css/admin.css`: admin page-specific shell, provider, directory, usage, table, modal, and responsive CSS.
- `tests/test_web_refactor.py`: adds admin static CSS/link/no-inline assertions.
- `tests/test_admin_ui.py`: points admin toast assertion at shared components/admin CSS split.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: record extraction status and decisions.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "home_and_template_editor_reuse_shared_visual_tokens or active_templates_route_flash_messages_through_top_right_toasts or root_route_shows_public_splash_without_auth or admin_page"`: passed, 25 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_cookie_csrf_security.py -k "home_and_admin_templates_do_not_use_inline_script_handlers or web_refactor"`: passed, 13 tests.

### Documentation

- Updated styling condensation plan current-state table and authenticated app audit notes.
- Added this progress entry.

### Risks / assumptions

- Assumes moderate admin visual harmonisation is acceptable: rounded panels/forms/cards, shared feedback, local dense layouts.
- Assumes `admin2.html` remains out of scope unless explicitly requested.

### Architecture checkpoint summary

- Privacy boundaries: unchanged.
- Ownership rules: unchanged.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no auth/session/route access change; admin page controls and hidden fields unchanged.
- Lifecycle/deletion checkpoint: destructive forms and confirmations unchanged.
- Docs/tests checkpoint: docs and focused tests updated/passed.

## 2026-07-02 Anti-Crawling Policy Hardening

### Scope

- Changed `/robots.txt` from selective route exclusions to deny-all policy.
- Added global `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex` defense-in-depth header.

### Checklist

- Target behavior: no OpenScribe route intended for crawler discovery or indexing.
- Affected modules/endpoints: shared response-header middleware and `/robots.txt`; no schema change.
- Affected tests: security-header and public-metadata regressions.
- Architecture risks: public splash becomes intentionally non-indexable; crawler controls remain non-security controls.
- Reuse decision: reused existing metadata route and shared middleware; no bot-name list or dependency.
- Code/tests/docs complete: yes.
- Open issue: production edge cache for `/robots.txt` must be purged after deployment.

### Files changed

- `app/routes/web_pages.py`: deny all compliant crawlers.
- `app/main.py`: emit global indexing-control header.
- `tests/test_cookie_csrf_security.py`: pin exact robots body and header value.
- `docs/security.md`, `docs/testing.md`, daily note, and this progress log: document policy and coverage.

### Tests

- Focused regression recorded red before implementation, then passed after implementation.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py`: passed, 30 tests.

### Risks / assumptions

- Assumes OpenScribe splash must not appear in search results.
- Existing indexed URLs may require separate search-engine removal workflow because blocked crawlers may not fetch new `noindex` headers.

### Architecture checkpoint summary

- Privacy boundaries: crawler discovery reduced; access controls unchanged and still authoritative.
- Ownership rules: unchanged.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: no route access or content visibility change.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: security/testing docs and regressions updated.

## 2026-07-02 Transcript Empty State

### Scope

- Replaced Transcript tab's plain empty copy with frozen orbit/waveform visual.
- Changed empty heading to `Start a recording to see your transcript`.
- Starts empty-state animation on local recording/capture activity and keeps it active through pre-transcript processing states.
- Hides orbit dot in frozen state; active orbit restores it at `1.45s` per cycle.
- Consolidated repeated orbit markup into shared Jinja macro.
- Added browser surface sync for empty, transcribing, and populated transcript states.

### Checklist

- Target behavior: empty transcript shows requested frozen visual/heading, then animates from recording start until text arrives.
- Affected schema/modules/endpoints: transcribe template, CSS, browser renderer, tests, docs only.
- Affected tests: shared-loader static regression and history empty-state expectation.
- Architecture risks: visual/status-sync only; no content access or persistence changes.
- Docs referenced/updated: `docs/testing.md`, `docs/progress.md`.
- Reuse decision: same orbit macro and waveform CSS serve generation-adjacent transcription and empty states.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: browser visual check remains optional.

### Files changed

- `app/templates/transcribe/_workspace.html`: shared orbit macro and frozen empty-state surface.
- `app/static/css/transcribe.css`: idle animation override.
- `app/static/js/transcribe/app.js`: three-way transcript surface sync.
- Asset include templates: cache-bust versions.
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: updated regressions.
- `docs/testing.md`, `docs/progress.md`: behavior and checkpoint record.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "generation_loading_replaces_plain_text_placeholders or splash_and_transcribe_styles_are_cacheable_static_assets"`: passed, 2 tests.
- `git diff --check`: passed.

### Documentation

- Added testing coverage note and this progress entry.

### Risks / assumptions

- Empty means transcript draft contains no non-whitespace text and status is not `transcribing`.
- Frozen visual retains ring and waveform but hides orbit dot; recording/listening/upload/finalize/queue states restore dot and run orbit at `1.45s` per cycle while draft remains empty.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript content added to new surface.
- Ownership rules: unchanged; existing owner-only workspace state drives visibility.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint changes.
- Auth/ownership checkpoint: no auth, query, or route changes.
- Lifecycle/deletion checkpoint: no record lifecycle changes.
- Docs/tests checkpoint: docs updated; JS syntax, static regressions, and diff validation pass.

## 2026-07-02 Transcription Loading State

### Scope

- Added orbit/waveform loading state to open Transcript tab while consultation status is `transcribing`.
- Reused note-generation loader structure, colors, sizing, orbit keyframe, and typography.
- Set transcription orbit duration to `1.45s`; note-generation orbit remains `2.2s`.
- Added client status sync so loader exits when backend status changes.

### Checklist

- Target behavior: show supplied transcription loader while open consultation is transcribing.
- Affected schema/modules/endpoints: transcribe template, CSS, browser status renderer, tests, docs; no schema or endpoint changes.
- Affected tests: static shared-loader regression and transcribing-session page render.
- Architecture risks: visual/status-sync only; no content, ownership, persistence, or provider path changes.
- Docs referenced/updated: `docs/testing.md`, `docs/progress.md`.
- Reuse decision: extend existing `.note-generation-loading` component with waveform center and duration modifier; no second loader component.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: browser visual check remains optional; existing transcribe page render test currently redirects its synthetic login back to `/login` before workspace assertions.

### Files changed

- `app/templates/transcribe/_workspace.html`: transcription loader markup and initial server visibility.
- `app/static/css/transcribe.css`: shared orbit duration variable plus waveform modifier.
- `app/static/js/transcribe/app.js`: status-driven loader/transcript visibility.
- `app/templates/transcribe/_head_assets.html`, `_shell_extras.html`: static asset cache busts.
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: loader, speed, asset, and server-render regressions.
- `docs/testing.md`, `docs/progress.md`: coverage and change record.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "generation_loading_replaces_plain_text_placeholders or splash_and_transcribe_styles_are_cacheable_static_assets"`: passed, 2 tests.
- Transcribing-session render regression attempted but blocked before workspace render: synthetic login redirects back to `/login`; same pre-existing failure also affects `test_user_transcribe_page_shows_workspace_shell`.

### Documentation

- Added testing coverage note and this daily progress entry.

### Risks / assumptions

- `1.45` interpreted as orbit duration in seconds (`1.45s`); waveform remains `1s`.
- Loader replaces Transcript tab body only for exact `transcribing` status. Live partial text in other statuses remains visible.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no new content rendered or exposed.
- Ownership rules: unchanged; existing owner-only workspace render remains source.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no auth or query change.
- Lifecycle/deletion checkpoint: no record lifecycle change.
- Docs/tests checkpoint: docs updated; static regressions and JS syntax pass. Existing login-fixture blocker noted for browser render test.

## 2026-07-02 Template Editor CSS Extraction

### Scope

- Extracted `template_editor.html` inline style block into `app/static/css/template-editor.css`.
- Kept editor shell, sidebar list, section rows, and translucent sticky action bar page-specific.
- Replaced editor button classes with shared `btn-primary` and `btn-secondary` component classes.
- Kept form actions, CSRF, hidden fields, data hooks, and template mode script unchanged.

### Checklist

- Target behavior: template editor has no inline CSS and uses shared rounded controls while preserving editor layout and sticky action bar behavior.
- Affected schema/modules/endpoints: template/static CSS/tests/docs only; no schema, route, or endpoint behavior changed.
- Affected tests: static refactor assertions for template editor CSS link, no inline style, shared button classes, and page CSS ownership.
- Architecture risks: visual drift only; form and structured-template semantics unchanged.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: reuse `components.css` for buttons/forms/flashes; keep editor-only layout in `template-editor.css`.
- Code complete: yes for template editor extraction.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: `admin2.html` remains excluded/special; `/transcribe-glm-2` route cleanup remains separate.

### Files changed

- `app/static/css/template-editor.css`: page-specific editor shell/sidebar/list/section/action-bar CSS.
- `app/templates/template_editor.html`: links static editor CSS, removes inline style block, uses shared button classes.
- `tests/test_web_refactor.py`: updates static assertions for extracted editor CSS and shared components.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: record extraction status and decisions.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 12 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "template_editor_page_uses_dedicated_full_page_layout or new_freeform_template_editor_hides_emis_sections_until_structured_mode or team_template_editor_page_keeps_team_scope_for_new_template or transcribe_template_editor_save_returns_to_transcribe or default_template_admin_routes_require_system_admin"`: passed, 4 tests.

### Documentation

- Updated styling condensation plan current-state table and template-editor decision note.
- Added this progress entry.

### Risks / assumptions

- Assumes moderate rounded harmonisation is acceptable per user choice.
- Assumes translucent sticky action bar should remain close to existing behavior per user choice.

### Architecture checkpoint summary

- Privacy boundaries: unchanged.
- Ownership rules: unchanged.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no auth/session/route access change; template forms unchanged except CSS classes.
- Lifecycle/deletion checkpoint: no data lifecycle paths touched.
- Docs/tests checkpoint: docs and focused tests updated/passed.

## 2026-07-01 Auth CSS Extraction

### Scope

- Added `app/static/css/auth.css` for login, request access, onboarding, MFA challenge, and password reset pages.
- Replaced duplicated inline auth/recovery style blocks with linked `tokens.css`, `components.css`, and `auth.css`.
- Kept auth page scripts/CSRF/forms/actions unchanged.
- Removed unused `app/templates/glm-3.html`; `/transcribe-glm-2` route remains because it currently renders normal `transcribe.html` and has existing route coverage.

### Checklist

- Target behavior: auth/recovery pages share one shell and existing shared controls while preserving form behavior and no-JS submissions.
- Affected schema/modules/endpoints: templates/static CSS/tests/docs only; no schema, route, or endpoint behavior changed.
- Affected tests: auth shell static assertions and toast/static CSS assertions.
- Architecture risks: visual drift only; auth flow semantics and CSRF remain unchanged.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: use `components.css` controls and `auth.css` layout/page-specific auth pieces.
- Code complete: yes for auth extraction.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: admin/template-editor inline page CSS remains; `/transcribe-glm-2` duplicate route cleanup remains separate from removed unused template.

### Files changed

- `app/static/css/auth.css`: shared auth/recovery layout and page-specific auth pieces.
- `app/static/css/components.css`: adds `.btn` and `.link-button` aliases for auth pages.
- `app/templates/login.html`, `request_access.html`, `onboarding.html`, `mfa_challenge.html`, `password_reset_request.html`, `password_reset_confirm.html`: link shared CSS and remove inline style blocks.
- `app/templates/glm-3.html`: removed unused prototype template.
- `tests/test_admin_ui.py`: updates auth CSS/static assertions.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: record extraction status and decisions.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "login_page_exposes_bootstrap_when_database_is_empty or request_access_page_submits_public_account_request or bootstrap_redirects_to_onboarding_and_requires_totp_setup or active_templates_route_flash_messages_through_top_right_toasts or auth_recovery_pages_use_current_shell_styling or invalid_browser_route_redirects_to_login_without_auth"`: passed, 6 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 12 tests.

### Documentation

- Updated styling condensation plan current-state table.
- Added this progress entry.

### Risks / assumptions

- Assumes small auth visual shifts are acceptable because auth pages now share one shell and component buttons/forms/toasts.
- Assumes deleting unused `glm-3.html` is safe because route grep shows no renderer uses that template.

### Architecture checkpoint summary

- Privacy boundaries: unchanged.
- Ownership rules: unchanged.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: auth forms/routes unchanged; only CSS links/body classes changed.
- Lifecycle/deletion checkpoint: no data lifecycle paths touched.
- Docs/tests checkpoint: docs and tests updated.

## 2026-07-01 Home CSS Extraction And Shared Components

### Scope

- Extracted the large `home.html` inline style block into static CSS.
- Added `components.css` for shared buttons, panels, forms, pills, tabs, modals, flashes, and toasts.
- Moved Home2 conditional styling into static `home2.css` and removed the old template partial.
- Linked shared components into splash, transcribe, home, and template editor.
- De-duplicated splash button CSS and transcribe toast CSS so those pages consume shared primitives.

### Checklist

- Target behavior: home uses existing/shared app styling where possible, with `home.css` reserved for home-only layout and state surfaces.
- Affected schema/modules/endpoints: templates/static CSS/tests/docs only; no backend endpoints or schemas.
- Affected tests: static asset/refactor tests, root splash render test, home/home2 render tests, toast/static CSS tests.
- Architecture risks: CSS cascade changes can cause visual drift; forms/actions/data hooks remain untouched.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: put reused primitives in `components.css` rather than copying them into `home.css`.
- Code complete: yes for home extraction/shared primitives slice.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: template editor/admin/auth still have inline page CSS; admin rounded shared-component pass remains pending.

### Files changed

- `app/static/css/components.css`: shared UI primitives.
- `app/static/css/home.css`: home-only layout and domain-specific UI.
- `app/static/css/home2.css`: extracted Home2 alternate layout.
- `app/templates/home.html`: links static CSS assets and removes inline style block.
- `app/templates/splashpage.html`: links `components.css`.
- `app/templates/transcribe/_head_assets.html`: links `components.css`.
- `app/templates/template_editor.html`: links `components.css`.
- `app/static/css/splash.css`: drops local `.button` primitive.
- `app/static/css/transcribe.css`: drops local toast primitive/status variants.
- `app/templates/_home2_admin2_style.html`: removed stale template style partial.
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: updated static/render assertions.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: updated plan and checkpoint.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 12 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "root_route_shows_public_splash_without_auth or home2_route_renders_admin2_styled_home_for_users_and_leaders or home_tab_navigation_updates_url_and_rejects_missing_panels or active_templates_route_flash_messages_through_top_right_toasts or home_overview_and_asset_cards_keep_white_fill_like_team_cards or non_admin_login_redirects_to_home_and_leader_sees_review_tools or home_restyled_preview_route_renders_for_signed_in_non_admin"`: passed, 7 tests.

### Documentation

- Updated styling condensation plan implementation status.
- Added this progress entry.

### Risks / assumptions

- Assumes shared button and toast visual language is acceptable across splash/home/transcribe per user decision.
- Assumes extracting Home2 static CSS is acceptable despite earlier default exclusion, because user explicitly chose this option.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content access path changed.
- Ownership rules: unchanged; no route authorization or response-scope change.
- Deletion semantics: unchanged; no destructive forms or deletion paths altered.
- Provider rules: unchanged; provider display/selection behavior untouched.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no auth/session changes.
- Lifecycle/deletion checkpoint: no records created, updated, or deleted.
- Docs/tests checkpoint: docs and tests updated.

## 2026-07-01 Home Template Editor Shared Tokens

### Scope

- Linked `tokens.css` into `home.html` and `template_editor.html`.
- Removed page-local token roots from both templates.
- Switched home/template editor font declarations to shared `--font-body` and `--font-display` variables.
- Extended shared tokens with app-page values already used by home/editor.

### Checklist

- Target behavior: home and template editor reuse shared visual tokens/fonts while route behavior, forms, CSRF fields, and page-specific layout stay unchanged.
- Affected schema/modules/endpoints: static CSS and templates only; no schema, module, route, or endpoint change.
- Affected tests: static/template refactor tests for shared token links and local-token removal.
- Architecture risks: visual drift only, accepted for this slice to move toward shared product palette.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: extended `tokens.css` instead of creating another app-page token root.
- Code complete: yes for first home/editor token slice.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: component extraction still pending; admin rounded shared-component pass remains next.

### Files changed

- `app/static/css/tokens.css`: adds app-page accent/shadow/radius/transition tokens.
- `app/templates/home.html`: links tokens and consumes shared font variables.
- `app/templates/template_editor.html`: links tokens and consumes shared font variables.
- `tests/test_web_refactor.py`: adds static assertions for shared app-page tokens.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: record audit and decisions.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 12 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "non_admin_login_redirects_to_home_and_leader_sees_review_tools or home_restyled_preview_route_renders_for_signed_in_non_admin or user_home_shows_team_stt_selection_when_configured"`: passed, 3 tests.
- First parallel admin-focused attempt collided with the shared OpenScribe test database while `tests/test_web_refactor.py` was running; rerun sequential passed.

### Documentation

- Updated styling condensation plan with home/admin/template-editor classification and borderline decisions.
- Added this progress entry.

### Risks / assumptions

- Assumes small visual shift on home/editor is acceptable because user chose shared tokens over exact local colour preservation.
- Assumes admin should be rounded/shared later, but not in this slice.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content access path changed.
- Ownership rules: unchanged; no route authorization or response-scope change.
- Deletion semantics: unchanged; no destructive forms or deletion paths altered.
- Provider rules: unchanged; provider display/selection behavior untouched.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no auth/session changes.
- Lifecycle/deletion checkpoint: no records created, updated, or deleted.
- Docs/tests checkpoint: docs and static tests updated.

## 2026-07-01 Splash Transcribe Token Harmonisation

### Scope

- Added `app/static/css/tokens.css` with shared splash/transcribe colour, font, radius, shadow, and compatibility alias tokens.
- Linked shared tokens before `splash.css` and `transcribe.css`.
- Removed duplicated `:root` token blocks from splash/transcribe CSS and routed body/display fonts through shared variables.
- Removed stale clinical-note empty-state CSS left behind after the empty guidance markup was removed.

### Checklist

- Target behavior: splash and transcribe share foundational visual tokens while keeping page-specific layout/control CSS intact.
- Affected schema/modules/endpoints: static CSS, splash/transcribe head includes, frontend/static tests; no schema or endpoint change.
- Affected tests: splash root render assertion, transcribe asset render assertion, static frontend CSS assertions.
- Architecture risks: token changes can create small visual drift; transcribe-specific layout and workflow selectors were not changed.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: used current splash/transcribe palette and aliases instead of inventing new styling values.
- Code complete: yes for token harmonisation.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: component-level harmonisation still pending; auth/home/admin still have inline duplicated tokens.

### Files changed

- `app/static/css/tokens.css`: shared visual tokens.
- `app/templates/splashpage.html`: links shared tokens before splash CSS.
- `app/templates/transcribe/_head_assets.html`: links shared tokens before transcribe CSS.
- `app/static/css/splash.css`: consumes shared font/token variables and drops local `:root` block.
- `app/static/css/transcribe.css`: consumes shared font/token variables, drops local `:root` block, and removes stale clinical-note empty-state CSS.
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: update static/link assertions for shared tokens.
- `docs/styling_condensation_plan.md`, `docs/progress.md`: record harmonisation status.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "splash or transcribe_page_includes_mobile_layout_assets or root_route_shows_public_splash_without_auth"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 11 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "root_route_shows_public_splash_without_auth or transcribe_page_includes_mobile_layout_assets or active_templates_route_flash_messages"`: passed, 3 tests.

### Documentation

- Updated styling condensation plan status.
- Added this progress entry.

### Risks / assumptions

- Assumes shared status tokens should use transcribe runtime values (`--success #38A169`, `--warning #D69E2E`) so product-state colors remain stable.
- Assumes splash can absorb those status token values because status colours are not central to its visible marketing layout.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content access path changed.
- Ownership rules: unchanged; `/transcribe` authorization and owner-only context unchanged.
- Deletion semantics: unchanged; no lifecycle or cascade path touched.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no route authorization or response-scope change.
- Lifecycle/deletion checkpoint: no records created, updated, or deleted.
- Docs/tests checkpoint: docs and focused static/render tests updated.

## 2026-07-01 Splash And Transcribe CSS Extraction

### Scope

- Extracted `splashpage.html` inline CSS into `app/static/css/splash.css`.
- Extracted `transcribe/_head_assets.html` inline CSS into `app/static/css/transcribe.css`.
- Kept selectors and CSS bodies unchanged for visual parity; no shared token/component de-duplication yet.

### Checklist

- Target behavior: splash and transcribe pages load cacheable static styles instead of large inline style blocks while keeping visual output unchanged.
- Affected schema/modules/endpoints: `splashpage.html`, `transcribe/_head_assets.html`, static CSS assets, render/static tests; no schema or endpoint change.
- Affected tests: splash root render assertion, transcribe asset render assertion, static frontend CSS assertions.
- Architecture risks: `/transcribe` is transcript-content UI; extraction preserved selectors and markup to avoid changing owner-only workspace behavior.
- Docs referenced/updated: `docs/styling_condensation_plan.md`, `docs/progress.md`.
- Reuse decision: moved existing CSS verbatim to static files instead of redesigning or merging controls prematurely.
- Code complete: yes for first extraction slice.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: shared token/component split remains pending; auth/home/admin inline styles still remain.

### Files changed

- `app/templates/splashpage.html`: replaced inline style block with static CSS link.
- `app/static/css/splash.css`: new extracted public landing page styles.
- `app/templates/transcribe/_head_assets.html`: replaced inline style block with static CSS link.
- `app/static/css/transcribe.css`: new extracted transcribe workspace styles.
- `tests/test_web_refactor.py`: points static CSS assertions at extracted files and adds extraction regression coverage.
- `tests/test_admin_ui.py`: checks new CSS links and reads transcribe style assertions from `transcribe.css`.
- `docs/styling_condensation_plan.md`: records implemented extraction status.
- `docs/progress.md`: records change and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "splash or transcribe_page_includes_mobile_layout_assets or root_route_shows_public_splash_without_auth or reorder_blocks_blank_note_lines or global_template_selector or active_templates_route_flash_messages"`: passed, 6 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 11 tests.

### Documentation

- Updated styling condensation plan implementation status.
- Added this progress entry.

### Risks / assumptions

- Assumes visual parity is best protected by verbatim extraction before any token/component merge.
- Assumes linked static CSS is acceptable under current CSP because existing pages already load static stylesheets.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content access path changed.
- Ownership rules: unchanged; `/transcribe` authorization and owner-only context unchanged.
- Deletion semantics: unchanged; no data lifecycle or cascade path touched.
- Provider rules: unchanged; provider display/runtime selection unchanged.
- Structured-note contract: unchanged; EMIS keys, JSON shape, and note editing behavior unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no route authorization or response-scope change.
- Lifecycle/deletion checkpoint: no records created, updated, or deleted.
- Docs/tests checkpoint: docs and focused static/render tests updated.

## 2026-07-01 Styling Condensation Plan

### Scope

- Added a planning document for condensing fragmented inline template styling.
- Prioritised `splashpage.html` as the public visual source of truth and `/transcribe` as a must-preserve workspace.
- Excluded `*2` templates and generated OWASP report HTML from application-style consolidation scope.

### Checklist

- Target behavior: document where inline styles live, what is page-unique, and what can move into shared CSS without changing route behavior.
- Affected schema/modules/endpoints: docs only; no schema, module, route, or endpoint change.
- Affected tests: none added because this is a planning-only documentation change.
- Architecture risks: future CSS refactor could accidentally alter `/transcribe` owner-only workspace layout; plan explicitly separates shared tokens/components from transcribe-specific workflow styling.
- Docs referenced/updated: `docs/UI_Translation.md`, `docs/frontend-roadmap.md`, `docs/transcribe_brief.md`, this progress note, and new `docs/styling_condensation_plan.md`.
- Reuse decision: plan reuses current splash and transcribe visual systems as extraction sources instead of inventing a new design system.
- Code complete: yes, documentation only.
- Tests added/updated: no, not applicable for planning document.
- Docs added/updated: yes.
- Open issues: implementation still needed; no CSS moved yet.

### Files changed

- `docs/styling_condensation_plan.md`: inventories inline styling and proposes phased central CSS extraction.
- `docs/progress.md`: records documentation-only planning work and architecture checkpoints.

### Tests

- Not run. Documentation-only change.

### Documentation

- Added `docs/styling_condensation_plan.md`.
- Added this progress entry.

### Risks / assumptions

- Assumes `splashpage.html` should drive public/shared styling and `/transcribe` should be extracted with visual parity before any component de-duplication.
- Assumes `admin2.html`, `_home2_admin2_style.html`, and generated ZAP report HTML are out of scope for this pass.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; plan only documents CSS extraction and warns against changing transcript-content handling.
- Ownership rules: unchanged; `/transcribe` remains owner-only and must not move auth decisions into frontend code.
- Deletion semantics: unchanged; plan requires destructive form targets/confirmation behavior to remain intact.
- Provider rules: unchanged; provider labels remain display-only.
- Structured-note contract: unchanged; EMIS keys, JSON shape, and structured-note editing behavior remain untouched.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no route authorization or response-scope change.
- Lifecycle/deletion checkpoint: no records created, updated, or deleted.
- Docs/tests checkpoint: docs updated; no tests needed for planning-only change.

## 2026-07-01 Transcribe Note Empty Guidance Removal

### Scope

- Removed the structured/freeform editable-note empty guidance row from `/transcribe`.
- Removed now-unused placeholder CSS, server template flags, and frontend empty-state sync hooks.

### Checklist

- Target behavior: the output note editor no longer shows "No note lines yet" or the start-recording guidance row.
- Affected schema/modules/endpoints: transcribe workspace template, transcribe workspace render context, and transcribe frontend JS/CSS only; no schema or endpoint change.
- Affected tests: transcribe page render assertions and static refactor coverage.
- Architecture risks: UI-only change; no content access, provider selection, ownership, or deletion path changed.
- Docs referenced/updated: this progress note.
- Reuse decision: removed obsolete empty-state code instead of adding hide-only conditionals.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/templates/transcribe/_workspace.html`: removed structured/freeform empty note guidance rows.
- `app/templates/transcribe/_head_assets.html`: removed unused note-editor empty-state CSS.
- `app/web/transcribe_workspace.py`: removed now-unused empty-state content flags.
- `app/static/js/transcribe/app.js`, `app/static/js/transcribe/structured.js`: removed empty-state DOM hooks and sync calls.
- `tests/test_admin_ui.py`, `tests/test_web_refactor.py`: updated regression assertions for removed guidance.
- `docs/progress.md`: records change and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py::test_user_transcribe_page_marks_structured_template_options_for_blank_note_editor tests/test_admin_ui.py::test_user_transcribe_page_hides_emis_context_for_freeform_template tests/test_admin_ui.py::test_user_transcribe_page_shows_transcript_and_followup_empty_states tests/test_web_refactor.py::test_note_editor_empty_state_guidance_removed tests/test_web_refactor.py::test_clinical_note_empty_state_uses_compact_spacing`: passed, 5 tests.

### Documentation

- Added this progress entry.

### Risks / assumptions

- Assumes the separate clinical-note flat placeholder "Add note lines here as the consultation unfolds." should remain; only the requested row containing "No note lines yet" and its body was removed.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content visibility path changed.
- Ownership rules: unchanged; only owner page rendering changed.
- Deletion semantics: unchanged; no data lifecycle or cascade path touched.
- Provider rules: unchanged.
- Structured-note contract: unchanged; EMIS/template JSON unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no route authorization or response-scope change.
- Lifecycle/deletion checkpoint: no records created, updated, or deleted.
- Docs/tests checkpoint: progress note and focused render/static tests updated.

## 2026-07-01 Follow-up Loading Animation Reuse

### Scope

- Reused the note generation loading animation for queued/processing follow-up generation.
- Refactored repeated loading markup into a Jinja macro and a shared frontend helper so note/follow-up loading states use one HTML shape.
- Bumped transcribe module cache tokens for the shared helper change.

### Checklist

- Target behavior: queued/processing follow-ups show the same orbit/star animation with follow-up-specific copy.
- Affected schema/modules/endpoints: transcribe workspace template and frontend render helpers only; no schema or endpoint change.
- Affected tests: static frontend and browser-render regression coverage.
- Architecture risks: render-only change; no content access, provider selection, or lifecycle semantics changed.
- Docs referenced/updated: `docs/transcribe_brief.md` and this note.
- Reuse decision: reused existing animation CSS, introduced one Jinja macro and one shared JS helper instead of duplicating follow-up markup.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: CSS class remains `note-generation-loading` for minimal churn even when rendering follow-ups.

### Files changed

- `app/templates/transcribe/_workspace.html`: adds shared `generation_loading` macro and uses it for note and follow-up queued/processing states.
- `app/static/js/transcribe/documents.js`: adds shared `generationLoadingHtml` helper.
- `app/static/js/transcribe/structured.js`, `app/static/js/transcribe/app.js`: use shared helper for note/follow-up dynamic renders.
- `app/templates/transcribe/_shell_extras.html`: bumps module cache token.
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: update loading and asset-token regressions.
- `docs/transcribe_brief.md`, `docs/progress.md`: document follow-up loading state.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py -k "generation_loading or workspace_refresh_burst"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "generate_followup or static_asset_version_bumped or transcribe_documents_show_hallucination_check_panel or user_transcribe_page_shows_workspace_shell"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 10 tests.

### Documentation

- Transcribe brief now records in-panel loading animation for follow-up generation too.

### Risks / assumptions

- Quick action queued state keeps its transcription-waiting copy, but uses the same animation shell.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content is logged or newly exposed.
- Ownership rules: unchanged.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no route authorization or response-scope change.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: docs and focused frontend/browser-render tests updated.

## 2026-07-01 Note Generation Loading Animation

### Scope

- Replaced plain queued/processing generated-note placeholder text with the loading animation from `loading_animation.html` inside the clinical note output area.
- Bumped transcribe module cache-bust tokens so refreshed pages cannot reuse stale generated-note renderers.
- Kept failed note messages and empty editable-note guidance unchanged.

### Checklist

- Target behavior: once a generated-note document is queued or processing, the clinical note pane shows the orbit/star loading state in the note text space.
- Affected schema/modules/endpoints: transcribe workspace template, transcribe styles, and generated-note client render path; no schema or endpoint change.
- Affected tests: static frontend regression coverage in `tests/test_web_refactor.py`.
- Architecture risks: render-only change; no content access, provider selection, or lifecycle semantics changed.
- Docs referenced/updated: `docs/transcribe_brief.md` and this note.
- Reuse decision: reused supplied animation structure/CSS, scoped class names to avoid broad page style changes.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none known.

### Files changed

- `app/templates/transcribe/_workspace.html`: renders loading animation for generated-note queued/processing states.
- `app/templates/transcribe/_head_assets.html`: adds scoped loading animation styles and keyframes.
- `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: bump module version tokens so browser fetches the updated renderer.
- `app/static/js/transcribe/structured.js`: uses same animation during dynamic workspace refreshes.
- `tests/test_web_refactor.py`: asserts animation replaces old plain text placeholders.
- `docs/transcribe_brief.md`, `docs/progress.md`: document behavior and change record.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py -k "note_generation_loading or clinical_note_empty_state or note_editor_empty_state"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "note_generation_loading or static_asset_version_bumped or transcribe_glm_2_page_exposes_workspace_hooks or user_transcribe_glm_2_page_exposes_workspace_hooks or blank_line_reorder"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 9 tests.

### Documentation

- Transcribe brief now records in-panel loading animation for queued/processing note generation.

### Risks / assumptions

- Animation is shown for generated-note queued and processing states, including queued notes still waiting on transcription, with status-specific helper text.
- Cache-bust tokens must change with render-path updates; otherwise fresh HTML can briefly show new UI before cached JS overwrites it.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content is logged or newly exposed.
- Ownership rules: unchanged; transcribe page remains owner-only through existing route protections.
- Deletion semantics: unchanged; no stored content, cascade, or retention behavior changed.
- Provider rules: unchanged; provider selection/fallback untouched.
- Structured-note contract: unchanged; EMIS section keys and generated-note content shape untouched.
- Schema checkpoint: no model, constraint, index, or migration change.
- Auth/ownership checkpoint: no authorization or response scope change.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: docs and focused frontend regression updated.

## 2026-07-01 Workspace Refresh Burst Gating

### Scope

- Stopped repeated browser workspace refresh bursts while the owner workspace SSE connection is healthy.
- Bumped the transcribe `app.js` cache token for the refresh-gating change so browsers do not reuse the prior ungated module.
- Kept refresh bursts as fallback when `EventSource` is unavailable or the stream errors.
- Skipped the immediate post-load workspace fetch when the SSE stream is present and expected to emit the initial workspace event.

### Checklist

- Target behavior: pending notes/transcripts update via SSE without browser-side repeated GET bursts that rebuild loading UI.
- Affected schema/modules/endpoints: transcribe frontend refresh scheduling only; no schema or endpoint change.
- Affected tests: static transcribe frontend regression coverage.
- Architecture risks: if a proxy silently stalls SSE without triggering `onerror`, UI can wait longer for updates; heartbeat/error fallback remains the mitigation.
- Docs referenced/updated: `docs/api.md`, `docs/transcript-capture.md`, and this note.
- Reuse decision: reused existing SSE connection state and fallback polling path; no new event bus yet.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: server SSE implementation still polls the read model once per second internally; true cross-process push needs an event bus or database notification path.

### Files changed

- `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: gate refresh burst scheduling/execution behind SSE fallback state, clear queued burst timers on stream open, skip initial fetch when stream owns hydration, and bump the module cache token.
- `tests/test_web_refactor.py`: asserts refresh bursts are fallback-only and cleared when SSE opens.
- `docs/api.md`, `docs/transcript-capture.md`, `docs/progress.md`: align realtime docs with fallback-only browser polling.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py -k "workspace_refresh_burst or note_generation_loading or transcribe_transcript_render_guard"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_web_refactor.py -k "workspace_refresh_burst or note_generation_loading or transcribe_transcript_render_guard" && .venv/bin/pytest -q tests/test_admin_ui.py -k "static_asset_version_bumped or transcribe_glm_2_page_exposes_workspace_hooks or user_transcribe_page_shows_workspace_shell"`: passed, 6 tests across two focused runs.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 10 tests.

### Documentation

- API/capture docs now say browser refresh bursts are suppressed while SSE is healthy and fallback only when disconnected/unavailable.

### Risks / assumptions

- Existing SSE stream emits an initial workspace payload, so skipping immediate fetch does not leave the page unhydrated when EventSource works.
- This reduces browser/network churn now; it does not yet remove server-side 1s polling inside the SSE stream.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; same owner-only workspace/SSE payload, no new transcript-derived exposure or logging.
- Ownership rules: unchanged; existing auth/session checks continue to gate workspace reads.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, migration, or constraint change.
- Auth/ownership checkpoint: no route authorization or response-scope change.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: realtime docs and static regression updated.

## 2026-07-01 Audit Input and Filter Bounds

### Scope

- Prevented overflowing relative audit lookbacks from causing Admin Audit 500 responses.
- Scoped all audit filter-option queries to the selected lookback and capped each option list using the same 1-to-250 event limit as recent rows.

### Checklist

- Target behavior: hostile numeric lookbacks clamp safely; filter generation cannot return unbounded historical IPs or metadata values.
- Affected schema/modules/endpoints: audit detection service and both Admin Audit views through shared presentation code; no schema or migration change.
- Affected tests: audit service and admin UI regression coverage.
- Architecture risks: cap may omit high-cardinality option values beyond the selected limit; direct URL filters remain supported.
- Docs referenced/updated: `docs/security.md`, `docs/testing.md`, OWASP audit response playbook, and this note; no applicable audit ADR exists.
- Reuse decision: reused existing 30-day lookback, 250-row cap, SQLAlchemy parameterized queries, and IP masking.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: filter options are capped, not paginated.

### Files changed

- `app/services/audit_detection.py`: pre-bounds relative durations and applies selected-window/limit predicates to all filter-option queries.
- `app/web/presentation.py`: passes parsed lookback and requested event limit into filter generation.
- `tests/test_audit_detection.py`: covers overflow-safe parsing plus windowed/capped option results.
- `tests/test_admin_ui.py`: verifies oversized lookback returns a successful Admin Audit page.
- Security/testing/OWASP docs: document query and input bounds.

### Tests

- Before fix: focused regressions reproduced `OverflowError`, route failure, and missing window/limit API.
- `.venv/bin/pytest -q tests/test_audit_detection.py tests/test_admin_ui.py -k "audit"`: passed, 12 tests (189 deselected).

### Documentation

- Security contract and response playbook now state 30-day lookback and 250-result bounds.
- Testing guide records overflow and bounded-option regressions.

### Risks / assumptions

- Options use lexical ordering before the cap for deterministic output. Values outside the cap can still be supplied as direct query filters.
- Public-IP masking still occurs after the bounded DB result, so an option list can contain fewer entries than its limit when internal addresses are omitted.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; only metadata-only audit fields are read, and internal-IP display masking remains intact.
- Ownership rules: unchanged; Audit tab remains system-admin-only.
- Deletion semantics: unchanged; audit retention remains separate from transcript-root retention.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no model, constraint, index, or migration change.
- Auth/ownership checkpoint: no route authorization or metadata scope expansion.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: service/UI regressions and security/testing/operations docs updated.

## 2026-06-30 Per-Session CSRF and Browser Harness Fix

### Scope

- Changed authenticated CSRF issuance from a fresh token on every safe page request to one deterministic, HMAC-signed token per login session.
- Fixed the Playwright live-server fixture to use one SQLAlchemy session per HTTP request and made the CSRF browser assertion compare request-time state before checking stability after redirect.

### Checklist

- Target behavior: authenticated CSRF token remains stable during normal navigation, unsafe browser API requests send that token, and session rotation invalidates it.
- Affected schema/modules/endpoints: `app/services/csrf.py` token generation/verification and Playwright test infrastructure; no schema or endpoint shape change.
- Affected tests: cookie/CSRF security tests, session-rotation API coverage, and Playwright CSRF/CSP browser tests.
- Architecture risks: changing CSRF derivation may reject a page holding a pre-deployment token until refresh; active auth sessions remain valid and the next safe page request issues the canonical token.
- Docs referenced/updated: `CONTEXT.md`, `docs/security.md`, `docs/auth.md`, `docs/testing.md`, `docs/progress.md`; no applicable CSRF ADR exists under `docs/adr/`.
- Reuse decision: reused existing CSRF HMAC secret, opaque session-token hash, constant-time comparison, origin validation, and cookie flags; no new storage or crypto library.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: Playwright remains an optional local dependency rather than a pinned project test dependency.

### Files changed

- `app/services/csrf.py`: derives one canonical opaque nonce per session and rejects non-canonical session nonces while retaining HMAC verification.
- `tests/test_cookie_csrf_security.py`: verifies same-session stability, cross-session separation, correct-session acceptance, and wrong-session rejection.
- `tests/test_csrf_browser.py`: uses request-scoped DB sessions, removes direct `conftest` import, verifies request-time token use, requires `201`, waits for redirect, and verifies same-session stability.
- `docs/security.md`, `docs/auth.md`, `docs/testing.md`: document per-session behavior and current unsafe API contract.
- `docs/progress.md`: records implementation checklist, evidence, and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "session_csrf_token_is_stable"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_csrf_browser.py::test_browser_transcribe_start_sends_csrf_header`: passed, 1 test with real Chromium.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py tests/test_api.py -k "csrf"`: passed, 36 tests with explicit test-mode cookie/HSTS settings.
- `.venv/bin/pytest -q tests/test_csrf_browser.py`: passed, 3 tests with real Chromium and explicit test-mode cookie/HSTS settings.

### Documentation

- Security and auth docs now state authenticated CSRF tokens persist for one session and rotate with the session.
- Testing docs now describe request-scoped browser DB sessions and the redirect stability check.

### Risks / assumptions

- Token remains non-auth-bearing, opaque, HMAC-signed, and bound to a high-entropy session token hash.
- Deployment changes the canonical token format. Already-open pages may need one refresh; no login/session revocation is required.
- Anonymous pre-login CSRF nonce behavior is unchanged.

### Architecture checkpoint summary

- Privacy boundaries: no transcript-derived content access or logging changed; tests use synthetic users and do not persist token values in docs.
- Ownership rules: no user/team/admin authorization behavior changed.
- Deletion semantics: no retention root, cascade, hard-delete, or session-revocation behavior changed.
- Provider rules: no provider selection, fallback, credential, or Vault-reference behavior changed; existing platform CSRF secret resolution remains intact.
- Structured-note contract: no structured profile, section key, validation, or generated-document behavior changed.
- Schema checkpoint: no model, constraint, migration, or database lifecycle change.
- Auth/ownership checkpoint: session binding, rotation invalidation, same-origin enforcement, HttpOnly session cookie, and constant-time HMAC checks remain enforced.
- Lifecycle/deletion checkpoint: unchanged.
- Docs/tests checkpoint: security, auth, testing, regression, and daily progress documentation updated; focused suites run.

## 2026-06-30 Regression Worklist Hardening

### Scope

- Worked through `regressions.md` P1/P2 items: permanent password rollout, audit User-Agent truncation, validation-response redaction, audit subject-hash secret handling, and CSP CSSOM browser coverage.

### Checklist

- Target behavior: permanent password UI/tests match 12-character complexity policy; audit strings fit storage limits including truncation marker; transient structured context is rejected with redacted validation payload; production audit subject hashes use configured/Vault secret material; strict CSP stays intact while CSSOM mutations remain covered.
- Affected schema/modules/endpoints: `app/services/security_audit.py`, `app/main.py`, auth/onboarding/login/reset templates, browser CSP tests, API/auth/audit tests; no schema migration or endpoint contract broadening.
- Affected tests: password/onboarding/MFA/browser UI tests, security audit tests, validation redaction test, CSP browser test.
- Architecture risks: low for password/test/doc changes; audit secret startup failure is intentional fail-closed production behavior; CSP browser test requires Playwright package/browser in environment.
- Docs referenced/updated: `regressions.md`, `docs/security.md`, `docs/auth.md`, `docs/dbtesting.md`, `docs/api.md`, `docs/working_note_implementation.md`, `docs/progress.md`.
- Reuse decision: reused existing `validate_password_strength`, validation handler redaction, CSRF/Vault platform secret source, and audit best-effort writer; no new auth or deletion flow.
- Code complete: yes for reproduced non-browser issues; CSP policy unchanged with regression test added.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: local venv lacks `playwright.sync_api`, so CSP browser regression skips here; run in browser-enabled CI/local env for live Chromium console evidence beyond static/full-suite skip coverage.

### Files changed

- `app/services/security_audit.py`: bounds truncation marker inside storage limit and resolves audit subject HMAC secret from explicit env/app/Vault sources with production fail-closed behavior.
- `app/main.py`: validates production audit subject-hash secret at startup with existing cookie/CSRF production checks.
- `app/templates/onboarding.html`, `app/templates/password_reset_confirm.html`, `app/templates/login.html`: permanent-password copy and `minlength` match backend policy while login/temp fields stay unchanged.
- `tests/constants.py`: shared policy-compliant permanent password for tests.
- `tests/test_api.py`, `tests/test_admin_ui.py`, `tests/test_auth_email.py`: password/onboarding/MFA tests use compliant value and preserve deliberate weak-password coverage; structured-context validation test asserts redacted public shape.
- `tests/test_security_audit.py`: covers audit string boundaries, long User-Agent persistence, secret precedence, Vault production fallback, fail-closed production behavior, and local fallback.
- `tests/test_csrf_browser.py`: adds real-browser CSP CSSOM mutation regression under `style-src-attr 'none'`.
- `app/static/js/transcribe/app.js`: restores forced draft rendering on transcript switch and routes PII-triggered highlight refresh through `renderDraft` so transcript DOM updates have one owner.
- `tests/test_admin_ui.py`: aligns static frontend guard assertions with single-owner draft render path.
- `docs/security.md`, `docs/dbtesting.md`, `docs/progress.md`: document final policy/evidence.

### Tests

- `.venv/bin/pytest -q tests/test_security_audit.py tests/test_api.py::test_generate_output_rejects_transient_structured_context_payload tests/test_api.py::test_temp_password_login_creates_onboarding_only_session_until_completion tests/test_admin_ui.py::test_completed_user_login_redirects_to_mfa_challenge_then_home`: passed, 31 tests.
- `.venv/bin/pytest -q tests/test_api.py tests/test_admin_ui.py tests/test_auth_email.py -k "password or onboarding or mfa or bootstrap or activation"`: passed, 31 tests.
- `.venv/bin/pytest -q tests/test_security_audit.py tests/test_cookie_csrf_security.py tests/test_auth_email.py -k "secret or subject_hash or login or audit or user_agent"`: passed, 37 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "generate_output_rejects_transient_structured_context_payload"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_csrf_browser.py`: skipped locally because `playwright.sync_api` is unavailable.
- `.venv/bin/pytest -q tests/test_admin_ui.py::test_transcribe_workspace_refresh_renders_updated_pii_entities tests/test_admin_ui.py::test_transcribe_frontend_uses_global_template_selector_for_generation_controls tests/test_admin_ui.py::test_generated_document_pii_no_reveal_mode_strips_cached_values tests/test_web_refactor.py::test_transcribe_transcript_render_guard_owns_transcript_dom_updates`: passed, 4 tests.
- `.venv/bin/pytest -q`: passed, 814 tests; 1 skipped; 18 warnings.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change; audit strings now fit existing column limits.
- Auth/ownership checkpoint: no ownership or role boundary change; permanent password creation keeps existing onboarding/activation/reset paths and temp/login contracts.
- Lifecycle/deletion checkpoint: no retention root, cascade, lock, or deletion behavior changed.
- Provider checkpoint: no team provider resolution or credential model changed; audit uses existing platform Vault secret only as secret material and never logs it.
- Structured-note contract: transient `structured_context` generation input remains rejected; response now asserts redacted validation contract and no persistence.
- Privacy boundaries: audit rows remain metadata-only; raw email subjects are HMACed, raw User-Agent is bounded, no transcript/note/prompt/provider response content exposed.

## 2026-06-29 Generation Audit Origin Metadata

### Scope

- Generation queue audit events now capture request IP, user agent, method, and route for API and browser generation starts.

### Checklist

- Target behavior: `generation_queued` audit rows include origin metadata like other request-triggered audit events.
- Affected schema/modules/endpoints: `app/services/templates.py`, `app/routes/api_routes.py`, `app/routes/web_transcribe.py`; no schema or endpoint contract change.
- Affected tests: API and browser generation route tests now assert audit IP/user-agent/route capture.
- Architecture risks: low; metadata-only audit enrichment, no transcript, prompt, or model response content exposed.
- Docs referenced/updated: `docs/progress.md`.
- Reuse decision: reused existing `record_security_event(request=...)` sanitizer and trusted-proxy handling; no new IP parsing code.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: async worker-side events still cannot know client IP unless the enqueue path records it first.

### Files changed

- `app/services/templates.py`: accepts optional `request` for template, follow-up, and quick-action queue audit events.
- `app/routes/api_routes.py`: passes request into API generation queue services.
- `app/routes/web_transcribe.py`: passes request into browser generation queue services.
- `tests/test_api.py`: verifies API `generation_queued` origin metadata.
- `tests/test_admin_ui.py`: verifies browser `generation_queued` origin metadata.
- `docs/progress.md`: records checklist, verification, and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py tests/test_admin_ui.py tests/test_audit_detection.py -k "team_and_personal_template_routes_enforce_scope_and_allow_generation or user_transcribe_page_can_generate_note_output_from_template or audit"`: passed, 12 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: existing full-session and owner checks unchanged.
- Lifecycle/deletion checkpoint: no retention, deletion, or cascade change.
- Provider checkpoint: no provider configuration, credential, or resolution change.
- Structured-note contract: unchanged.
- Privacy boundaries: only request metadata added to audit rows; safe details still exclude transcript, prompt, note, and provider response content.

## 2026-06-29 Audit UI Email And Team Display

### Scope

- Audit event rows in admin audit UI now show actor/target email addresses and team names instead of raw UUIDs when linked rows still exist.

### Checklist

- Target behavior: human-readable actor/target/team values in audit event tables while keeping UUID filters and service fields available.
- Affected schema/modules/endpoints: `app/services/audit_detection.py`, `app/templates/admin.html`, `app/templates/admin2.html`; no schema or endpoint change.
- Affected tests: audit listing service and admin audit UI rendering tests.
- Architecture risks: low; email is account metadata on system-admin audit surface, not transcript-derived content.
- Docs referenced/updated: `docs/progress.md`.
- Reuse decision: reused existing `SecurityAuditEvent.actor`, `SecurityAuditEvent.target`, and `SecurityAuditEvent.team` relationships with joined loading; no new lookup service or denormalized snapshot.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: deleted users/teams with nulled audit FKs cannot display historical email/name.

### Files changed

- `app/services/audit_detection.py`: joined actor/target users and teams, then added `actor_email`, `target_email`, and `team_name` display fields to listed audit events.
- `app/templates/admin.html`: legacy audit table prefers actor/target email and team name with UUID fallback.
- `app/templates/admin2.html`: audit table prefers actor/target emails and team name with UUID fallback.
- `tests/test_audit_detection.py`: verifies service returns actor/target emails and team name while retaining IDs.
- `tests/test_admin_ui.py`: verifies audit sections render email/team name and not raw UUIDs.
- `docs/progress.md`: records checklist, verification, and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_audit_detection.py tests/test_admin_ui.py -k "audit"`: passed, 10 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: audit UI remains admin-only; no ownership boundary change.
- Lifecycle/deletion checkpoint: user deletion/null FK behavior unchanged; no email snapshot added.
- Provider checkpoint: no provider configuration, credential, or resolution change.
- Structured-note contract: unchanged.
- Privacy boundaries: safe details allowlist unchanged; no transcript, prompt, note, or model-response content exposed.

## 2026-06-29 Home Tab Navigation Fix

### Scope

- Fixed `/home` section nav so tab clicks hide inactive panels, activate only existing panels, update the browser URL from each tab's `data-tab-url`, and keep browser back/forward in sync.

### Checklist

- Target behavior: Overview, Templates, Quick actions, Smart phrases, AI Services, Team, and Requests tabs all select their matching panel and preserve navigable URLs.
- Affected schema/modules/endpoints: `app/templates/home.html` only; no schema or endpoint change.
- Affected tests: static regression coverage in `tests/test_admin_ui.py`.
- Architecture risks: low; frontend navigation only, no content access or persistence logic changed.
- Docs referenced/updated: `docs/home_brief.md`, `docs/progress.md`.
- Reuse decision: reused existing tab buttons, `data-tab-target`, `data-tab-panel`, and `data-tab-url`; no new router or library.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: visual browser smoke recommended because focused test asserts the tab contract, not computed layout.

### Files changed

- `app/templates/home.html`: restores explicit `[hidden]` hiding for grid panels, validates tab target names against rendered panels, updates URL on click, and handles `popstate`.
- `tests/test_admin_ui.py`: adds regression coverage for the home tab navigation JS contract.
- `docs/progress.md`: records checklist, verification, and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "home_tab_navigation_updates_url_and_rejects_missing_panels or home2_route_renders_admin2_styled_home_for_users_and_leaders or home_restyled_preview_route_renders_for_signed_in_non_admin"`: passed, 3 tests.
- Browser MCP: logged in locally as `dev.user@example.com` and verified Overview, Templates, Quick actions, and Smart phrases each update URL, selected tab, active tab state, and sole visible panel.
- Browser MCP: logged in locally as `dev.leader@example.com` and verified all seven tabs, including AI Services, Team, and Requests, each update URL, selected tab, active tab state, and sole visible panel.
- Browser MCP root cause: found inactive panels visible because malformed HTML closed `[data-tab-shell]` after Overview, leaving other panels outside the tab shell. Also fixed explicit `[hidden]` CSS so author `display: grid` rules cannot override hidden panels.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: no auth, role, content visibility, or ownership change.
- Lifecycle/deletion checkpoint: no retention, deletion, or cascade change.
- Provider checkpoint: no provider configuration, credential, or resolution change.
- Structured-note contract: unchanged.
- Privacy boundaries: no transcript-derived content or confidential data touched.

## 2026-06-29 CSP Style Attribute Migration

### Scope

- Removed all 69 CSP-blocked `style` attributes from browser templates while preserving static layout and dynamic admin usage charts.
- Kept strict `style-src-attr 'none'` enforcement.

### Checklist

- Target behavior: templates render without inline style attributes under strict CSP.
- Affected schema/modules/endpoints: `admin.html`, `home.html`, `onboarding.html`, and `password_reset_request.html`; no schema or endpoint change.
- Affected tests: CSP/XSS static coverage plus focused home, onboarding, admin, and password-reset rendering.
- Architecture risks: prevent template values becoming arbitrary CSS; dynamic percentages are parsed, finite-checked, and clamped to `0..100` before direct CSSOM assignment.
- Docs referenced/updated: `docs/security.md`, `docs/progress.md`.
- Reuse decision: reused existing nonce-approved style/script blocks and existing template classes; no new library or style-injection mechanism.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: visual browser smoke remains recommended before deployment; no known code or test blocker.

### Files changed

- `app/templates/{admin,home,onboarding,password_reset_request}.html`: replace static style attributes with classes; move dynamic chart/meter percentages to escaped data attributes and a clamped CSSOM initializer.
- `tests/test_xss_coverage.py`: enforce zero template style attributes and verify strict CSP/dynamic-style contract.
- `docs/security.md`, `docs/progress.md`: document CSP-compatible styling rules and verification.

### Tests

- `.venv/bin/pytest -q tests/test_xss_coverage.py tests/test_cookie_csrf_security.py -k "csp or inline_style or dynamic_percentage or unsafe_rendering"`: passed, 9 tests.
- `.venv/bin/pytest -q tests/test_xss_coverage.py tests/test_cookie_csrf_security.py`: passed, 59 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py tests/test_auth_email.py -k "bootstrap_redirects_to_onboarding or user_home_shows_team_stt_selection_when_configured or admin_page_uses_flat_sidebar_workspace_layout or password_reset_request_disabled_tells_user_to_contact_admin"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_csrf_browser.py -k "home_styles_apply_under_strict_style_attribute_csp"`: browser module skipped because Playwright Python package is unavailable in current virtualenv; regression test added for browser-enabled CI.
- `rg -n 'style[[:space:]]*=' app/templates --glob '*.html'`: no matches.

### Documentation

- Added template and dynamic CSSOM rules to browser CSP guidance.

### Risks / assumptions

- Direct `element.style[property]` assignment is intentionally retained for validated runtime geometry; CSP blocks style attributes and `cssText`, not direct CSSOM property updates.
- Visual browser smoke remains advisable before deployment because focused server-render tests cannot compare computed layout.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: no auth, role, content visibility, or ownership change.
- Lifecycle/deletion checkpoint: no retention, deletion, or cascade change.
- Provider checkpoint: no provider configuration, credential, or resolution change.
- Structured-note contract: unchanged.
- Privacy boundaries: no transcript-derived content, secrets, or user-entered text added to CSS; only bounded aggregate percentages drive dynamic presentation.

## 2026-06-26 Merge Regression Audit Hardening

### Scope

- Validated `merge_regression.md` against OWASP intent docs and current code.
- Fixed confirmed security-regression risks in audit persistence, audit sanitisation bounds, audit detection query bounds, API CSRF handling, realtime/token access-denial auditing, CSP-compatible 429 HTML, and password schema metadata.

### Checklist

- Target behavior: audit writes do not commit/rollback caller work or break primary flows; audit metadata remains bounded and secret-free; audit detection queries are bounded; API CSRF requires header tokens; token-based full-context denials are audited.
- Affected schema/modules/endpoints: `app/services/security_audit.py`, `app/services/audit_detection.py`, `app/main.py`, `app/errors.py`, `app/schemas/auth.py`; no DB schema change.
- Affected tests: audit, audit detection, cookie/CSRF, API docs/rate-limit focused tests.
- Architecture risks: preserve transcript privacy, owner-only content access, deletion semantics, provider secret boundaries, and structured-note contract.
- Docs referenced/updated: OWASP README/context/findings/audit protocol/playbook, `docs/security.md`, `docs/progress.md`.
- Reuse decision: refined existing audit/CSRF/detection helpers; no new persistence layer, route, or custom crypto primitive beyond standard-library HMAC/IP parsing.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: several `merge_regression.md` items are broader backlog/accepted-risk items rather than immediate regressions, including forced legacy password rotation, SIEM/alerting, cleanup queues, and route-inventory/browser smoke expansion.

### Files changed

- `app/services/security_audit.py`: dedicated best-effort audit session, HMAC subject hashes, request-IP bounds/parsing, and audit detail/list/dict size caps.
- `app/services/audit_detection.py`: bounded lookback/summary reads and SQL-side category/outcome filtering for event listings.
- `app/main.py`: API CSRF header-only validation, env-gated forwarded origin trust, and token-helper access-denial audit coverage.
- `app/errors.py`: removed inline 429 CSS blocked by strict CSP.
- `app/schemas/auth.py`: aligned new-password schema minimums and documented permissive login password minimum.
- `tests/test_security_audit.py`, `tests/test_audit_detection.py`, `tests/test_cookie_csrf_security.py`: regression coverage for confirmed fixes.
- `docs/security.md`, `docs/progress.md`: records current security behavior and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_security_audit.py tests/test_audit_detection.py tests/test_cookie_csrf_security.py tests/test_api.py -k "api_docs or csrf or login_is_rate_limited"`: passed, 42 tests.
- `.venv/bin/pytest -q tests/test_security_audit.py tests/test_audit_detection.py tests/test_auth_email.py -k "audit or password_reset_request or security_audit_request_ip"`: passed, 35 tests.
- `.venv/bin/python -m py_compile app/errors.py app/main.py app/services/security_audit.py app/services/audit_detection.py app/schemas/auth.py`: passed.
- `git diff --check`: passed.

### Architecture checkpoint summary

- Schema checkpoint: no migration or schema change.
- Auth/ownership checkpoint: no route privileges expanded; token-based denials now add metadata-only audit rows.
- Lifecycle/deletion checkpoint: deletion semantics and audit retention separation unchanged.
- Provider checkpoint: provider credential storage, cleanup, and resolution unchanged.
- Structured-note contract: unchanged.
- Privacy boundaries: audit rows remain metadata-only; sensitive keys, long payloads, request bodies, transcript/note/prompt/provider response content, tokens, cookies, and secrets remain excluded or bounded.

## 2026-06-25 Provider Config Audit Enum Fix

### Scope

- Fixed STT and LLM provider config audit logging so post-refresh enum fields can be recorded whether SQLAlchemy returns enum objects or plain strings.

### Checklist

- Target behavior: provider config create/update/finalize/credential-replace endpoints do not 500 after successful DB commit when audit details include `setup_status` or `credential_status`.
- Affected schema/modules/endpoints: `app/services/stt.py`, `app/services/llm.py`; no schema or route contract change.
- Affected tests: provider config API tests covering STT, LLM, and deidentification provider paths.
- Architecture risks: provider audit detail serialization only; no transcript-derived content, ownership, deletion, encryption, or provider-resolution policy changed.
- Docs referenced/updated: `AGENTS.md`, `docs/progress.md`.
- Reuse decision: reused a tiny local enum/string normalization helper instead of adding new infrastructure.
- Code complete: yes.
- Tests added/updated: no new tests required; existing focused provider config tests caught and now cover regression.
- Docs added/updated: yes.
- Open issues: none known.

### Files changed

- `app/services/llm.py`: normalize provider setup status before audit details are recorded.
- `app/services/stt.py`: normalize provider setup and credential statuses before audit details are recorded.
- `docs/progress.md`: records checklist, tests, and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "llm_config or stt_config or deidentification_provider"`: passed, 25 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: system-admin/team-scoped provider management checks unchanged.
- Lifecycle/deletion checkpoint: no deletion, retention, or cascade behavior changed.
- Provider checkpoint: provider credential write/delete and provider fallback behavior unchanged; only audit serialization changed after commit/refresh.
- Structured-note contract: unchanged.
- Privacy boundaries: audit details remain metadata-only; no transcript, note, prompt, model response, or provider secret content logged.

## 2026-06-24 Pen Test Findings Retest

### Scope

- Retested F01-F11 against current code and live `openscribe.co.uk` where safe.
- Fixed confirmed live/code-level gaps for CSRF cookie `HttpOnly`, validation-response minimisation, permanent password complexity, `Retry-After` on 429 responses, and CSP `style-src-attr`.
- Confirmed `/docs` and `/openapi.json` are already production-gated to full system admins; live unauthenticated checks returned `401`.

### Checklist

- Target behavior: production docs not public; CSRF token cookie not JavaScript-readable; validation errors do not disclose schema internals; user-chosen permanent passwords have complexity; 429 includes `Retry-After`; CSP removes inline style attribute allowance where possible.
- Affected schema/modules/endpoints: auth schemas, browser CSRF middleware/bootstrap, error handlers, CSP header builder, browser password routes, auth email/bootstrap services; no DB schema change.
- Affected tests: cookie/CSRF, auth-email/password, error/rate-limit, API docs gate, security audit tests.
- Architecture risks: preserve browser CSRF without exposing auth-bearing cookies or transcript-derived content; keep public account-request flow but rate-limited and CSRF-protected for browser form use.
- Docs referenced/updated: `docs/security.md`, `docs/auth.md`, `docs/testing.md`, `docs/progress.md`.
- Reuse decision: reused existing CSRF signing/session binding and SlowAPI limiter; no captcha or custom crypto added.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: DNS/edge-only items remain operator tasks: SPF/DMARC and CAA records absent in live DNS; `www.openscribe.co.uk` returns Cloudflare `525`; `wasm-unsafe-eval` remains for ONNX Runtime/VAD unless VAD stack changes; `security.txt` contact disclosure accepted unless project wants alias-only contact.

### Files changed

- `app/main.py`, `app/templates/_csrf_script.html`, `app/static/js/csrf.js`: make CSRF cookie `HttpOnly`; source tokens from nonce-protected page state/hidden fields for browser requests.
- `app/errors.py`: minimise validation details and add `Retry-After` to 429 responses.
- `app/security_headers.py`: remove `style-src-attr 'unsafe-inline'`.
- `app/services/passwords.py`, `app/schemas/auth.py`, `app/routes/web_pages.py`, `app/services/auth_email.py`, `app/services/admin.py`: enforce stronger user-chosen permanent passwords.
- Tests in `tests/test_cookie_csrf_security.py`, `tests/test_auth_email.py`, `tests/test_errors.py`, `tests/test_security_audit.py`, `tests/test_admin_ui.py`: update/add regressions.
- Docs in `docs/security.md`, `docs/auth.md`, `docs/testing.md`, `docs/progress.md`: record security behavior and test coverage.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py tests/test_auth_email.py tests/test_errors.py tests/test_security_audit.py tests/test_api.py -k "api_docs or csrf_cookie or csp_header or password_reset_confirm or account_activation or user_chosen_passwords or validation_error_response or rate_limit_response or invalid_email_token_failure"`: passed, 18 tests.
- Live unauthenticated `curl` checks: `/openapi.json` and `/docs` returned `401`; `/login` set `openscribe_csrf` and `openscribe_csrf_anon` with `HttpOnly; Secure; SameSite=lax`; CSP includes `style-src-attr 'none'` and still includes `wasm-unsafe-eval`.
- Live DNS/metadata checks: no TXT SPF, no `_dmarc` TXT, no CAA returned; `/.well-known/security.txt` exposes `mailto:oscar@meddleapp.com`; `https://www.openscribe.co.uk/` returns Cloudflare `525`.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: full system-admin docs gate preserved; auth/session cookies stay `HttpOnly`; CSRF remains same-origin and session/anonymous-nonce bound.
- Lifecycle/deletion checkpoint: no deletion, retention, or transcript-root cascade behavior changed.
- Provider checkpoint: no provider credential or provider resolution behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no transcript/note/prompt/provider response/provider secret content logged or exposed; validation responses now disclose less request schema detail.

## 2026-06-24 Dev Reverse Proxy Client IP

### Scope

- Updated dev startup so FastAPI trusts forwarded proxy headers only from nginx/Nginx Proxy Manager at `192.168.1.234` by default.

### Checklist

- Target behavior: requests through local reverse proxy show the real client IP instead of the proxy host.
- Affected schema/modules/endpoints: `start-dev.sh` only; no schema, API route, auth, or content endpoint change.
- Affected tests: shell syntax check; no Python behavior changed.
- Architecture risks: avoid trusting spoofable forwarded headers from arbitrary clients.
- Docs referenced/updated: `docs/setup.md`, `.env.example`, `docs/progress.md`.
- Reuse decision: reused FastAPI/Uvicorn `--proxy-headers` and `--forwarded-allow-ips`; no custom IP parser added.
- Code complete: yes.
- Tests added/updated: no new test needed for shell flag wiring.
- Docs added/updated: yes.
- Open issues: ensure app port is reachable only from trusted LAN/proxy hosts in deployment firewall.

### Files changed

- `start-dev.sh`: adds `DEV_FORWARDED_ALLOW_IPS` default and passes proxy trust flags to `fastapi dev`.
- `.env.example`: documents dev trusted proxy IP knob.
- `docs/setup.md`: documents local reverse-proxy forwarded-IP behavior and safety warning.
- `docs/progress.md`: records checklist and checkpoints.

### Tests

- `bash -n start-dev.sh`: passed.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth or ownership policy changed.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no transcript, note, prompt, provider response, cookie, token, or secret content logged or exposed.

## 2026-06-14 Auth Session Scanner Triage

### Scope

- Accepted ZAP `Authentication Request Identified` for `/login` as expected public auth endpoint detection.
- Accepted ZAP `Session Management Response Identified` for `openscribe_csrf_anon`/`openscribe_csrf` as CSRF-cookie auto-detection, not auth-cookie exposure.
- Added `OWASP-2026-06-14-016` evidence and remediation-plan entry.

### Checklist

- Target behavior: scanner warnings are tied to expected login/CSRF behavior; auth-bearing cookies remain protected.
- Affected schema/modules/endpoints: docs/evidence only; no schema, route, cookie, or auth runtime change.
- Affected tests: existing cookie/session boundary tests and API CSRF tests.
- Architecture risks: avoid misclassifying CSRF controls as sessions; preserve clear auth-bearing cookie boundary.
- Docs referenced/updated: `docs/security.md`, `docs/testing.md`, OWASP passive recon/findings/retest/remediation/context, progress/daily note.
- Reuse decision: reused existing regression tests and latest ZAP retest output; no new scanner/tooling.
- Code complete: no runtime code change.
- Tests added/updated: no new tests needed; focused existing tests run.
- Docs added/updated: yes.
- Open issues: none for this scanner slice.

### Files changed

- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/session-scanner-triage-2026-06-14.txt`: compact scanner triage evidence.
- OWASP evidence docs: add `OWASP-2026-06-14-016` accepted finding, retest entry, and remediation-plan section.
- `docs/security.md`, `docs/testing.md`: document scanner interpretation and test coverage.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 Auth Session Scanner Triage.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "csrf_cookie_alone or session_cookie or trusted_device or anon_nonce"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "csrf"`: passed, 7 tests.
- Note: first parallel API pytest attempt hit shared DB guard; sequential rerun passed.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth or ownership policy changed; auth-bearing cookies remain `HttpOnly`.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no cookie values, tokens, account data, transcript/note/prompt/provider/audio content committed.

## 2026-06-14 Cache-Control Triage Fix

### Scope

- Classified ZAP cache-control warnings for public/auth/API/metadata/static routes.
- Added explicit no-store for `/`, public auth/account pages, and `/api/` responses.
- Added explicit short public caching for cookie-free metadata and static assets.

### Checklist

- Target behavior: pages that create CSRF cookies or may carry account/API context are not stored; public metadata/static assets are explicitly short-cacheable.
- Affected schema/modules/endpoints: `app/main.py` cache header middleware; `/`, `/api/`, auth pages, metadata routes, and `/static/`; no schema changes.
- Affected tests: `tests/test_cookie_csrf_security.py` cache/header regressions.
- Architecture risks: avoid caching transcript-derived or user-specific responses; metadata/static cache accepted only because routes are public and cookie-free.
- Docs referenced/updated: `docs/security.md`, `docs/testing.md`, OWASP passive recon/findings/retest/remediation/context, progress/daily note.
- Reuse decision: reused shared security-header middleware; no new dependency or per-route duplication.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none for cache triage; residual ZAP cache alerts are accepted design/heuristic alerts after production retest.

### Files changed

- `app/main.py`: adds cache policy helper for no-store and short public-cache route classes.
- `tests/test_cookie_csrf_security.py`: verifies no-store on `/`, auth pages, and `/api/`; verifies metadata/static cache headers and no CSRF cookies.
- `docs/security.md`, `docs/testing.md`: document cache policy and test coverage.
- OWASP evidence docs: add `OWASP-2026-06-14-015`, local retest evidence, and ready-for-production-retest state.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 Cache-Control Triage Fix.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "cache or metadata or static_assets or no_store"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py`: passed, 26 tests.
- `.venv/bin/python -m py_compile app/main.py`: passed.
- Production header sample: root/auth/API routes `no-store`; metadata/static routes cookie-free and public-cacheable.
- Production ZAP cache retest: `zap-baseline-retest-cache-2026-06-14.*`; residual cache alerts accepted as no-store/public-cache heuristics.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth policy or access scope changed; API responses are more conservative.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no transcript/note/prompt/provider/audio content exposed; public cache applies only to cookie-free metadata/static assets.

## 2026-06-14 CSRF Cookie HttpOnly Scanner Decision

### Scope

- Accepted the ZAP `Cookie No HttpOnly Flag` warning for `openscribe_csrf` as an intentional readable CSRF token design.
- Added regression tests proving auth-bearing cookies stay `HttpOnly` and CSRF cookie alone does not authenticate API access.

### Checklist

- Target behavior: `openscribe_csrf` may remain readable because browser JavaScript submits it as `X-CSRF-Token`; session/trusted-device cookies remain `HttpOnly`.
- Affected schema/modules/endpoints: tests and docs only; no schema, route, or cookie behavior change.
- Affected tests: `tests/test_cookie_csrf_security.py`; existing `tests/test_api.py` CSRF rejection tests referenced as evidence.
- Architecture risks: readable CSRF token increases importance of CSP/XSS controls, but it is not auth-bearing and is signed/session-bound.
- Docs referenced/updated: `docs/security.md`, `docs/testing.md`, OWASP passive recon/findings/retest/remediation/context, progress/daily note.
- Reuse decision: reused existing CSRF signing/verification and cookie helpers; no new mechanism.
- Code complete: no runtime code change.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: cache-control informational warnings and deeper authenticated role crawl remain separate slices.

### Files changed

- `tests/test_cookie_csrf_security.py`: adds cookie contract coverage for readable signed CSRF, `HttpOnly` nonce/session/trusted-device cookies, and CSRF-cookie-alone non-authentication.
- `docs/security.md`, `docs/testing.md`: document accepted cookie design and test coverage.
- OWASP evidence docs: add and accept `OWASP-2026-06-14-014` with test evidence.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 CSRF Cookie HttpOnly Scanner Decision.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "cookie or csrf_cookie or trusted_device"`: passed, 25 tests.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py`: passed, 25 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "csrf"`: passed, 7 tests.
- `.venv/bin/python` CSRF cookie decision docs sanity check: passed.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth policy or access scope changed; auth-bearing cookies remain `HttpOnly`.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no sensitive logging or evidence added.

## 2026-06-14 Public Metadata Routes Fix

### Scope

- Added explicit public metadata routes for `/robots.txt`, `/.well-known/security.txt`, and `/sitemap.xml`.
- Used `oscar@meddleapp.com` as the security contact.
- Suppressed CSRF cookie issuance on metadata and static GETs.

### Checklist

- Target behavior: public metadata paths no longer redirect to login and do not set unnecessary CSRF cookies.
- Affected schema/modules/endpoints: `app/routes/web_pages.py`, `app/main.py`; no schema or API contract changes.
- Affected tests: cookie/CSRF security public metadata tests.
- Architecture risks: public metadata only; no transcript-derived content, auth-bearing material, provider data, or secrets exposed.
- Docs referenced/updated: `docs/security.md`, `docs/testing.md`, OWASP passive recon/findings/remediation/context, progress/daily note.
- Reuse decision: used existing FastAPI response routes and CSRF middleware skip logic; no new dependency.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: deploy and retest production to close `OWASP-2026-06-14-013`.

### Files changed

- `app/routes/web_pages.py`: adds `robots.txt`, `security.txt`, and intentional `sitemap.xml` 404 routes.
- `app/main.py`: skips CSRF cookie issuance for metadata and static safe GETs.
- `tests/test_cookie_csrf_security.py`: covers metadata status/content/cookie behavior and static asset CSRF-cookie suppression.
- `docs/security.md`, `docs/testing.md`: document metadata behavior and test coverage.
- OWASP evidence docs: mark metadata finding remediated pending deploy/retest.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 Public Metadata Routes Fix.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "metadata or static_assets or hsts or security_headers or no_store"`: passed, 8 tests.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py`: passed, 21 tests.
- `.venv/bin/python -m py_compile app/main.py app/routes/web_pages.py`: passed.
- `.venv/bin/python` metadata docs sanity check: passed.
- Production metadata retest: metadata paths returned intended status/content and no cookies, but `Allow: /$` caused ZAP to crawl literal `/$`; changed robots rule to `Allow: /`.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "metadata or static_assets"`: passed, 2 tests after robots fix.
- Metadata final retest attempt: Cloudflare still served cached `/robots.txt` with `Allow: /$` (`cf-cache-status: HIT`), so ZAP still crawled literal `/$`. Need purge `/robots.txt` cache or wait TTL, then rerun.
- Metadata final retest after Cloudflare purge: `/robots.txt` returned `Allow: /`, ZAP no longer crawled literal `/$`, and `OWASP-2026-06-14-013` is resolved.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth policy or access scope changed.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: metadata contains only public crawl/contact data; no sensitive logging or evidence added.

## 2026-06-14 Security Header Hardening Fix

### Scope

- Added missing browser hardening headers for `Permissions-Policy` and COEP.
- Added public auth/account page `no-store` cache policy.
- Added `HSTS_SOURCE=app|proxy|proxy_static_fallback` so production behind Cloudflare/reverse proxy can avoid duplicate HSTS while covering static assets.

### Checklist

- Target behavior: deployed pages declare capability, embedder, and cache policies; exactly one infrastructure layer owns HSTS.
- Affected schema/modules/endpoints: `app/main.py` response middleware; public `/login`, `/forgot-password`, `/request-access`, `/reset-password`, `/activate-account`; no schema or API contract changes.
- Affected tests: cookie/CSRF security header tests.
- Architecture risks: COEP uses `credentialless` rather than stricter `require-corp` to reduce risk to public docs and same-origin browser assets; production must use the HSTS mode matching actual edge behavior.
- Docs referenced/updated: `docs/security.md`, `docs/testing.md`, OWASP findings/remediation/context, progress/daily note.
- Reuse decision: reused existing security-header middleware and tests; no new dependency or proxy-specific code.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: deploy, set production HSTS ownership as needed, and rerun ZAP baseline to close `OWASP-2026-06-14-010`/`012` evidence.

### Files changed

- `app/main.py`: adds hardening headers, public auth-page `no-store`, and configurable app/proxy HSTS ownership.
- `tests/test_cookie_csrf_security.py`: covers header presence, HSTS proxy delegation, and public auth-page no-store.
- `docs/security.md`, `docs/testing.md`: document header/cache/HSTS behavior and test coverage.
- OWASP evidence docs: mark header/HSTS findings remediated pending deploy/retest and update remediation plan/context.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 Security Header Hardening Fix.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "hsts or security_headers or no_store or csp"`: passed, 9 tests.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py`: passed, 19 tests.
- Documentation follow-up: `.env.example`, `README.md`, and `docs/setup.md` now explain `HSTS_SOURCE=app|proxy` and single-owner HSTS setup.
- Production ZAP header retest: `Permissions-Policy`/COEP checks passed and duplicate-HSTS warning disappeared; one static vendor JS path still lacks HSTS and needs follow-up.
- Static HSTS follow-up: added `HSTS_SOURCE=proxy_static_fallback` so production can keep Cloudflare-owned dynamic HSTS while app adds HSTS to `/static/` assets only.
- `.venv/bin/python` docs sanity check for `proxy_static_fallback`: passed.
- Cloudflare HSTS retest: after enabling Cloudflare HSTS for 6 months with include subdomains and preload off, dynamic and static samples both returned one HSTS header and ZAP reported `PASS: Strict-Transport-Security Header [10035]`.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth policy or access scope changed.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no sensitive logging or evidence added.

## 2026-06-14 OWASP Public Form CSRF Retest

### Scope

- Retested deployed public CSRF form fix on `https://openscribe.co.uk` with OWASP ZAP baseline.
- Marked `OWASP-2026-06-14-011` resolved after ZAP reported `PASS: Absence of Anti-CSRF Tokens [10202]` and captured `_csrf_token` fields on public forms.

### Checklist

- Target behavior: deployed public forms expose hidden `_csrf_token` in scanner-visible HTML/form nodes.
- Affected schema/modules/endpoints: documentation/evidence only in this step; production `/login`, `/forgot-password`, and `/request-access` retested.
- Affected tests: ZAP baseline retest evidence, no app test changes.
- Architecture risks: retest stayed anonymous/passive baseline; no authenticated crawl, active attack scan, transcript/note content, provider data, or secrets captured.
- Docs referenced/updated: OWASP findings, retest log, remediation plan, OWASP context, progress note.
- Reuse decision: reused existing ZAP baseline command/output folder rather than adding new tooling.
- Code complete: not applicable; docs/evidence only.
- Tests added/updated: ZAP retest evidence added.
- Docs added/updated: yes.
- Open issues: `OWASP-2026-06-14-010`, `OWASP-2026-06-14-012`, `OWASP-2026-06-14-013`, cache-control warnings, and CSRF cookie `HttpOnly` scanner warning remain separate work items.

### Files changed

- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-2026-06-14.*`: production ZAP retest outputs.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/09-findings-and-remediation.md`: marks `OWASP-2026-06-14-011` resolved.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/10-retest-log.md`: adds retest entry and evidence pointer.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/11-remediation-plan.md`: records R-002 deployed and ZAP-retested successfully.
- `docs/Compliance/OWASP/OWASP_Context.md`: carries forward resolved CSRF retest state.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 OWASP Public Form CSRF Retest.md`: record checklist and checkpoints.

### Tests

- `docker run --rm -t -v ".../07-tool-outputs/zap:/zap/wrk:rw" zaproxy/zap-stable zap-baseline.py -t "https://openscribe.co.uk" -m 1 -r zap-baseline-retest-2026-06-14.html -J zap-baseline-retest-2026-06-14.json -w zap-baseline-retest-2026-06-14.md -I`: passed for `Absence of Anti-CSRF Tokens [10202]`; 12 URLs observed, 0 fail alerts, 9 warning alert types, 58 pass rules.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no authenticated user or owner content accessed; public anonymous form evidence only.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed or provider endpoint tested.
- Structured-note contract: unchanged.
- Privacy boundaries: ZAP evidence contains public route/form metadata only; no cookies, tokens, transcript/note/prompt/provider/audio content committed.

## 2026-06-14 Public Form CSRF Rendering Fix

### Scope

- Fixed the OWASP/ZAP public-form CSRF evidence mismatch by rendering hidden `_csrf_token` fields server-side on public forms while keeping the existing JavaScript CSRF refresh/backstop.
- Marked public `/docs` and `/openapi.json` exposure as accepted by project owner because OpenScribe is open source, with future review caveat.

### Checklist

- Target behavior: public forms expose CSRF tokens in initial HTML so no-JS scanners and browsers see the same protected form contract.
- Affected schema/modules/endpoints: `app/main.py` CSRF cookie middleware state setup; public login/bootstrap, forgot-password, and request-access templates; no schema or endpoint contract change.
- Affected tests: cookie/CSRF security tests, API CSRF tests, auth browser reset tests, admin/UI CSRF-related tests.
- Architecture risks: CSRF token remains signed and validated by existing path; session/trusted-device cookies remain `HttpOnly`; public docs exposure accepted only because project is open source.
- Docs referenced/updated: `docs/security.md`, `docs/auth.md`, `docs/testing.md`, OWASP evidence/remediation files.
- Reuse decision: reused existing signed CSRF token generation/validation and `_csrf_script.html`; no new CSRF mechanism added.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: production deployment and ZAP retest still required; HSTS, `Permissions-Policy`/COEP, cache-control, and metadata-file remediations remain open.

### Files changed

- `app/main.py`: computes `request.state.csrf_token` before rendering and reuses it for the CSRF cookie.
- `app/templates/login.html`, `app/templates/password_reset_request.html`, `app/templates/request_access.html`: render hidden `_csrf_token` fields in public POST forms.
- `tests/test_cookie_csrf_security.py`: adds regression for server-rendered public-form CSRF fields matching the response cookie.
- `docs/security.md`, `docs/auth.md`, `docs/testing.md`: document server-rendered CSRF fields and test coverage.
- OWASP evidence files: update accepted/remediated statuses and remediation plan notes.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 Public Form CSRF Rendering Fix.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "csrf or csp or hsts"`: passed, 15 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "csrf"`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_auth_email.py -k "password_reset_browser"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "csrf or login or request_access"`: passed, 10 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no auth policy or role scope changed; public forms now expose the same CSRF token server-side that validation already required.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: no provider behavior changed.
- Structured-note contract: unchanged.
- Privacy boundaries: no sensitive logging or evidence added; password/account-request fields remain normal form submissions only.

## 2026-06-14 OWASP Remediation Planning

### Scope

- Added a remediation plan for public passive recon and server-fingerprinting findings from `openscribe.co.uk`.
- Linked current OWASP findings to concrete remediation sections with target behavior, implementation plan, tests, and acceptance criteria.

### Checklist

- Target behavior: each public passive/ZAP section has a safe, testable remediation path without changing app behavior in this step.
- Affected schema/modules/endpoints: documentation only under `docs/Compliance/OWASP`; no schema, endpoint, auth, provider, or storage code changed.
- Affected tests: docs/file sanity only.
- Architecture risks: public API docs and CSRF behavior require explicit owner/security decisions; plan avoids silently changing security model.
- Docs referenced/updated: OWASP context, passive recon, server fingerprinting, findings/remediation log.
- Reuse decision: reused existing findings and evidence, adding one plan doc rather than fragmenting remediation across files.
- Code complete: yes.
- Tests added/updated: not applicable; docs/evidence only.
- Docs added/updated: yes.
- Open issues: all remediation items remain open until code/config changes and retest evidence exist.

### Files changed

- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/11-remediation-plan.md`: remediation plan for API docs exposure, CSRF mismatch, HSTS, security headers, cache-control, metadata files, CSRF cookie scanner warning, and passive recon gaps.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/09-findings-and-remediation.md`: links findings to remediation plan sections.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/04-passive-recon.md`, `05-server-fingerprinting.md`: link to remediation plan.
- `docs/Compliance/OWASP/OWASP_Context.md`: records remediation plan location.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 OWASP Remediation Planning.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/python` docs sanity check: passed; remediation plan exists, finding links target known section slugs, and CSV files still parse.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no runtime access behavior changed; plan preserves owner-only content boundaries.
- Lifecycle/deletion checkpoint: no lifecycle behavior changed.
- Provider checkpoint: no provider behavior changed or external provider tested.
- Structured-note contract: unchanged.
- Privacy boundaries: plan contains no sensitive raw evidence and keeps retest evidence redaction rules explicit.

## 2026-06-14 OWASP Public Passive Recon

### Scope

- Captured public unauthenticated passive recon and server fingerprint evidence for `openscribe.co.uk`.
- Ran OWASP ZAP Docker baseline in passive/baseline mode and stored outputs under the OWASP evidence folder.
- Updated findings for public API docs exposure, public-form CSRF scanner mismatch, missing metadata files, header hardening, and HSTS duplicate-header triage.

### Checklist

- Target behavior: `04-passive-recon.md` and `05-server-fingerprinting.md` contain current public evidence for DNS, TLS, headers, metadata paths, docs exposure, and ZAP baseline results.
- Affected schema/modules/endpoints: documentation/evidence only under `docs/Compliance/OWASP`; no app code changed.
- Affected tests: evidence-file presence and CSV sanity only; no app runtime tests required.
- Architecture risks: avoid committing secrets/cookies/tokens/content; ZAP outputs contain public route/form names only and no authenticated session material.
- Docs referenced/updated: OWASP evidence pack, `OWASP_Context.md`, findings log, passive recon, server fingerprinting.
- Reuse decision: used Python stdlib capture and ZAP baseline instead of adding custom scanners or active attack tooling.
- Code complete: yes.
- Tests added/updated: not applicable; docs/evidence only.
- Docs added/updated: yes.
- Open issues: manual triage required for public `/docs` and `/openapi.json`, ZAP CSRF alert, duplicate HSTS alert, missing `Permissions-Policy`/COEP, and `security.txt`/robots/sitemap redirects.

### Files changed

- `docs/Compliance/OWASP/OWASP_Context.md`: records public capture phase, tools, and findings.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/04-passive-recon.md`: adds public passive findings and ZAP summary.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/05-server-fingerprinting.md`: adds DNS/TLS/header/server fingerprint evidence.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/`: stores redacted summary and ZAP outputs.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/09-findings-and-remediation.md`: adds new public evidence findings.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 OWASP Public Passive Recon.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/python` evidence sanity check: passed; required passive/fingerprint/ZAP files present, OWASP findings contain new IDs, route/role CSV files still parse.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no authenticated crawl or owner-content access occurred; public-only evidence captured.
- Lifecycle/deletion checkpoint: no lifecycle behavior changed.
- Provider checkpoint: no provider infrastructure tested; only public OpenScribe URL checked.
- Structured-note contract: unchanged.
- Privacy boundaries: committed summaries omit cookie values, CSP nonces, tokens, transcript/note/prompt content, provider responses, and audio.

## 2026-06-14 OWASP Scope Evidence Pack

### Scope

- Implemented the initial OWASP scope and evidence-pack structure inside `docs/Compliance/OWASP`.
- Seeded the dated `2026-06-14` evidence folder from repo-backed documentation, with gaps kept explicit until live test/crawl evidence exists.

### Checklist

- Target behavior: future OWASP work has a repeatable evidence structure, route inventory seed, role matrix, architecture map, recon/fingerprinting placeholders, findings log, retest log, Top 10 matrix, and carry-forward context.
- Affected schema/modules/endpoints: documentation only under `docs/Compliance/OWASP`; no schema, endpoint, auth, provider, or storage code changed.
- Affected tests: CSV structure/path sanity only; no app runtime tests required for docs-only evidence seed.
- Architecture risks: evidence must not contain secrets, auth material, patient content, transcript text, note text, prompts, provider responses with clinical content, or audio.
- Docs referenced/updated: `README.md`, `docs/api.md`, `docs/auth.md`, `docs/security.md`, `CONTEXT.md`, `docker-compose.yml`, OWASP pack docs.
- Reuse decision: reused existing documentation and security/test design instead of inventing a new assurance model.
- Code complete: yes.
- Tests added/updated: not applicable; docs/evidence only.
- Docs added/updated: yes.
- Open issues: live route crawl, role proxy evidence, SSRF canary tests, dependency/SBOM evidence, TLS/header evidence, persistent audit-event remediation, and AI safety test plan remain open evidence tasks.

### Files changed

- `docs/Compliance/OWASP/README.md`: adds context and dated evidence-pack links.
- `docs/Compliance/OWASP/00-scope-and-evidence-pack.md`: aligns evidence path with the OWASP directory and marks seeded first tasks.
- `docs/Compliance/OWASP/OWASP_Context.md`: carry-forward structure, rules, status, and next tasks for future agents.
- `docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/*`: initial dated evidence pack.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 OWASP Scope Evidence Pack.md`: record checklist and checkpoints.

### Tests

- `.venv/bin/python` CSV sanity check for `01-route-inventory.csv` and `02-role-access-matrix.csv`: passed; consistent column counts.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration changes.
- Auth/ownership checkpoint: no access behavior changed; evidence preserves owner-only transcript-derived content and metadata-only admin/leader boundaries.
- Lifecycle/deletion checkpoint: no deletion behavior changed; evidence records immediate transcript/user/Working-note deletion semantics.
- Provider checkpoint: no provider behavior changed; evidence records Vault-backed secrets, HTTPS/private-endpoint rules, and SSRF follow-up gaps.
- Structured-note contract: no structured-note behavior changed; evidence records EMIS section contract and AI safety follow-up needs.
- Privacy boundaries: no sensitive raw evidence committed; all files are repo-backed summaries/placeholders only.

## 2026-06-14 Redaction Fail-Closed Regression

### Scope

- Fixed full-suite regressions in transcribe PII refresh, LLM model display, and generated-document redaction fail-closed behavior.

### Checklist

- Target behavior: workspace refresh re-renders PII highlights/entities; user-selected LLM model displays without leaking team default model text into the transcribe page; redaction failures do not leave a provider request payload stored.
- Affected schema/modules/endpoints: `generated_documents` schema/model, generated-document queue/process service, transcribe workspace JS/template; no endpoint contract change.
- Affected tests: failing admin UI regressions, API redaction fail-closed regressions, migration expected-schema check.
- Architecture risks: `llm_request_payload_json_encrypted` is transcript-derived provider payload; it must remain empty until redacted prompt construction succeeds.
- Docs referenced/updated: `docs/progress.md` and daily note in `docs/progress/`.
- Reuse decision: reused existing generated-document snapshot flow and added a narrow metadata JSON column instead of overloading the encrypted provider payload field.
- Code complete: yes.
- Tests added/updated: migration expected-schema updated for the new column.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/models.py`, `alembic/versions/t9u0v1w2x3y4_add_generation_snapshot_to_generated_documents.py`: add nullable `generation_snapshot_json`.
- `app/services/templates.py`: store wait/options queue metadata in `generation_snapshot_json`; keep provider request payload empty until redacted request build succeeds.
- `app/static/js/transcribe/app.js`: refresh PII table and transcript highlights from workspace PII entities.
- `app/templates/transcribe/_workspace.html`: hide team-default model name from the user note-options selector while preserving default selection.
- `tests/test_migrations.py`: include new schema column.
- `docs/progress.md`, `docs/progress/Daily Note 14-6-26 Redaction Fail Closed Regression.md`: record work and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py tests/test_api.py -k "transcribe_workspace_refresh_renders_updated_pii_entities or user_transcribe_page_shows_resolved_user_llm_model or transcribe_frontend_uses_global_template_selector_for_generation_controls or generated_document_pii_no_reveal_mode_strips_cached_values or template_generation_fails_closed_when_working_note_redaction_fails or quick_action_generation_fails_closed_when_working_note_redaction_fails"`: passed, 6 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "expected_schema"`: passed, 1 test.

### Architecture checkpoint summary

- Privacy boundaries: preserved; failed redaction no longer leaves provider payload in the LLM request field.
- Ownership rules: unchanged; generated documents and transcript content remain owner-scoped.
- Deletion semantics: unchanged; generated-document metadata still follows transcript-root retention/cascade.
- Provider rules: unchanged; LLM resolution still uses user preference with team fallback.
- Structured-note contract: unchanged.

## 2026-06-02 Section Copy Selection Fix

### Scope

- Fixed single structured-section copy so it respects checked/unchecked UI line selections, matching whole-note copy behavior.

### Checklist

- Target behavior: copying one structured section copies only selected nonblank lines from that section.
- Affected schema/modules/endpoints: `app/static/js/transcribe/structured.js`; no schema or endpoint change.
- Affected tests: transcribe frontend regression coverage in `tests/test_admin_ui.py`.
- Architecture risks: no ownership, privacy, deletion, encryption, provider resolution, or structured-note JSON contract redesign.
- Docs referenced/updated: `docs/progress.md`.
- Reuse decision: reused existing `collectSelectedNoteLines({ mode: 'structured' })` whole-copy collector and filtered by section key.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/static/js/transcribe/structured.js`: section copy now delegates to selected-line collection used by whole-note copy.
- `tests/test_admin_ui.py`: asserts section copy path reuses selected-line collector.
- `docs/progress.md`: records scope, checklist, tests, docs, and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 1 test.
- `node --check app/static/js/transcribe/structured.js`: passed.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; copy operates only on already-visible owner UI text.
- Ownership rules: unchanged; no backend access path added.
- Deletion semantics: unchanged; no persistence or lifecycle behavior changed.
- Provider rules: unchanged.
- Structured-note contract: unchanged; section keys/labels stay same, copied text now follows existing UI selection contract.

## 2026-05-31 Generation Regression Fixes

### Scope

- Fixed optional structured hallucination-check Vault secret failures so they mark the checker `failed_provider` while saving the generated note as ready.
- Fixed dictation-only follow-up generation so saved post-consultation dictation counts as a valid source when transcript text is empty.
- Fixed the transcribe workspace follow-up availability gates so saved dictation enables the prompt and Generate controls.
- Documented current hallucination-check provider policy: checker selection is resolved at worker processing time, not queue time.

### Checklist

- Target behavior: checker-only provider/secret failures do not fail generated documents; follow-ups accept transcript, dictation, or Working-note sources.
- Affected schema/modules/endpoints: `app/services/templates.py`, `app/web/transcribe_workspace.py`, `app/static/js/transcribe/app.js`, generation worker/UI paths, generated-document metadata; no schema change.
- Affected tests: hallucination-check Vault failure regression, dictation-only follow-up backend regression, and dictation-only follow-up UI regression.
- Architecture risks: no ownership, privacy, deletion, encryption, provider resolution, or structured-note JSON contract redesign.
- Docs referenced/updated: `docs/api.md`, `docs/testing.md`, `docs/progress.md`.
- Reuse decision: reused existing checker failure metadata/audit event path and existing dictation source helper.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: queue-time hallucination-check snapshotting remains a future auditability decision requiring schema/API work.

### Files changed

- `app/services/templates.py`: catches checker Vault read failures as `failed_provider`, records safe debug/audit metadata, and lets dictation satisfy follow-up empty-source checks.
- `app/web/transcribe_workspace.py`, `app/static/js/transcribe/app.js`: allow saved dictation to enable follow-up controls on first render and after workspace sync.
- `tests/test_api.py`, `tests/test_admin_ui.py`: add regressions for checker secret-read failure, dictation-only follow-up generation, and dictation-only follow-up UI enablement.
- `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document source requirements, checker failure handling, runtime checker selection policy, and test coverage.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "structured_hallucination_check_vault_failure_does_not_fail_document or structured_hallucination_check_provider_failure_records_safe_debug or followup_generation_uses_saved_dictation_when_transcript_empty or followup_generation_uses_saved_working_note_when_transcript_empty"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_followups_enabled_for_saved_dictation or transcribe_quick_actions_enabled_for_saved_dictation or transcribe_create_button_enabled_for_saved_dictation or transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 4 tests.
- `node --check app/static/js/transcribe/app.js`: passed.

### Architecture checkpoint summary

- Schema checkpoint: no schema changes.
- Auth/ownership checkpoint: generation remains owner-only; no new content visibility.
- Lifecycle/deletion checkpoint: generated documents still inherit transcript retention and cascade from transcript root.
- Provider checkpoint: main generation provider snapshot unchanged; checker failure is isolated from document success; checker selection remains runtime-resolved by documented policy.
- Structured-note contract: unchanged; checker still receives redacted evidence and exact-substring JSON edits only.

## 2026-05-31 Admin2 Bedrock Provider URL Sync

### Scope

- Fixed admin2 LLM provider setup so changing provider presets updates default base URLs, and selecting Bedrock or changing Bedrock region derives `https://bedrock-mantle.<region>.api.aws/v1`.
- Applied the same provider-change and Bedrock region sync to the current `/admin` LLM provider form, hiding its editable Base URL while Bedrock is selected.
- Fixed server-side Bedrock preset defaults so an explicit Bedrock region overrides stale browser base URLs such as Ollama localhost during draft/create inspection.
- Hid the editable Base URL field while Bedrock is selected, show the derived Mantle endpoint beside the region selector, and moved admin2 Bedrock region options to the shared backend preset list.
- Matched provider-change behavior with the working admin form: changing provider now force-applies that provider's default URL, so Bedrock no longer waits for manual Base URL clearing.
- Preserved custom endpoint behavior: non-standard edited URLs remain admin-entered and still save as Custom OpenAI-compatible by backend rules.

### Checklist

- Target behavior: provider and region controls keep the submitted LLM base URL in sync during setup/edit; provider changes overwrite stale defaults; Bedrock shows a derived endpoint instead of an editable Base URL; backend corrects stale submitted base URL when Bedrock region is explicit.
- Affected schema/modules/endpoints: `app/templates/admin.html`, `app/templates/admin2.html`, `app/services/llm_presets.py`, admin LLM draft path; no schema change.
- Affected tests: admin UI template regression, admin2 stale localhost Bedrock draft regression, provider-default unit assertion.
- Architecture risks: no ownership, privacy, deletion, encryption, provider resolution, or structured-note contract redesign.
- Docs referenced/updated: `docs/llm-providers.md`, `docs/testing.md`, `docs/progress.md`.
- Reuse decision: reused existing provider/base-url/region data hooks from the admin form.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: broader LLM provider setup should become a stepper: Provider -> Credential/model discovery -> Default model.

### Files changed

- `app/templates/admin.html`, `app/templates/admin2.html`: add provider/base URL/Bedrock region sync and dynamic helper note, with provider changes force-applying defaults, Bedrock always overwriting URL from selected region, hidden editable Bedrock URL, derived endpoint label, and backend-rendered region options.
- `app/services/llm_presets.py`: makes explicit Bedrock region derive the standard Mantle URL even if stale base URL is submitted.
- `tests/test_admin_ui.py`, `tests/test_api.py`: cover admin2 sync hooks, stale localhost Bedrock draft correction, and provider-default derivation.
- `docs/llm-providers.md`, `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document expected Bedrock URL derivation and coverage.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "llm_provider_preset_catalog_and_inference"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_llm_provider_dropdown_syncs_base_url_and_note or admin2_llm_provider_dropdown_syncs_base_url_and_bedrock_region or admin2_bedrock_draft_ignores_stale_localhost_base_url or admin2_exposes_admin_lifecycle_and_provider_controls"`: passed, 4 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema changes.
- Auth/ownership checkpoint: system-admin provider page remains the only affected surface; no transcript-derived content access changes.
- Lifecycle/deletion checkpoint: no deletion or retention behavior changed.
- Provider checkpoint: runtime provider resolution unchanged; save-time defaults now treat explicit Bedrock region as source of truth before classification.
- Structured-note contract: unchanged.
- Privacy boundaries: no raw secrets, prompts, transcript text, or note text exposed/logged.

## 2026-05-30 Hallucination Checker Pipe Fix

### Scope

- Fixed OpenAI-compatible generation output extraction so checker/provider responses that return text as content-part dictionaries are treated as normal note text.
- Added regression coverage for this provider response shape, which previously surfaced as `llm_generation_failed` / `LLM generation returned no note text`.
- Reproduced the live Bedrock Mantle `openai.gpt-oss-120b` checker request with redacted inputs and confirmed the model can spend the 1600 completion-token cap on reasoning, returning `finish_reason=length` and no final note text.
- Added gpt-oss-specific checker request overrides: `reasoning_effort=low` and minimum `max_completion_tokens=4000`.

### Checklist

- Target behavior: hallucination checker reads provider text from string, text-part object, text-part dictionary, and string-list content shapes; gpt-oss checker calls leave enough final-answer budget for JSON.
- Affected schema/modules/endpoints: `app/services/templates.py` provider response extraction only; no schema or endpoint change.
- Affected tests: focused API/service regressions added for OpenAI-compatible content-part dictionaries and gpt-oss checker request overrides.
- Architecture risks: no ownership, privacy, deletion, provider selection, or structured-note contract redesign.
- Docs referenced/updated: `docs/hallucination-check-design.md`, `docs/testing.md`, `docs/progress.md`.
- Reuse decision: refined existing `_generate_freeform_output_openai` helper rather than adding a new provider path.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: no fallback policy changed; non-gpt-oss models that return empty final content still report provider failure.

### Files changed

- `app/services/templates.py`: extracts OpenAI-compatible message content from dict/object text parts as well as strings, and applies gpt-oss checker request overrides.
- `tests/test_api.py`: adds regression for content-part dictionary extraction, usage metadata preservation, and gpt-oss checker request shape.
- `docs/testing.md`, `docs/progress.md`: record the regression coverage and architecture checklist.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "hallucination_check or openai_generation_extracts_text_from_content_part_dicts or gpt_oss_hallucination_checker"`: passed, 6 tests.
- `python3 -m py_compile app/services/templates.py tests/test_api.py`: passed.
- Live sandbox check against the failing redacted Bedrock Mantle checker payload returned final JSON through the patched helper with `reasoning_effort=low` and `max_completion_tokens=4000`.

### Architecture checkpoint summary

- Schema checkpoint: no schema changes.
- Auth/ownership checkpoint: generated-document access and owner-only debug gates unchanged.
- Lifecycle/deletion checkpoint: no stored content lifecycle or cascade behavior changed.
- Provider checkpoint: selected checker provider/model still used directly; no fallback to generation LLM added.
- Structured-note contract: checker JSON/patch contract unchanged.
- Privacy boundaries: no prompt, transcript, checker response, note text, or secret logging added.

## 2026-05-30 Hallucination Check Design

### Scope

- Added design for structured-note hallucination check round using redacted source material, admin-only checker LLM selection, exact-substring JSON patches, one retry, and checked/unchecked metadata.
- Added dev-only debug UI guidance for first-pass note and checker edits, gated by env flag and owner-only visibility.
- Captured reuse-first implementation guidance to minimise churn and avoid new LLM/provider plumbing where existing services fit.

### Checklist

- Target behavior: checker reviews structured template notes against redacted transcript, Working note, and dictation sources, then saves checked or unchecked final note.
- Affected schema/modules/endpoints: future selection table, generated-document metadata, admin-only selection routes/UI, generation service, generated-document responses.
- Affected tests: future API, migration, admin UI, structured-output, redaction-boundary, provider-usage tests documented.
- Architecture risks: plaintext boundary, provider fallback, content-bearing logs/storage, exact-patch fragility documented.
- Development debug risk: first-pass note visibility allowed only behind explicit non-production env flag, owner-only generated-document access, encrypted at rest, and no logs.
- Docs referenced/updated: `docs/hallucination-check-design.md`, `docs/progress.md`.
- Reuse decision: reuse existing redaction, LLM config/provider runtime, encrypted generated-document storage, structured-note validation, and usage-event paths.
- Code complete: design only; no application code changed.
- Tests added/updated: not applicable for design-only change.
- Docs added/updated: yes.
- Open issues: implementation must verify exact insertion point before any reidentification.

### Files changed

- `docs/hallucination-check-design.md`: records resolved design decisions, patch contract, runtime flow, schema/API touch points, tests, and architecture checkpoints.
- `docs/progress.md`: records checklist and architecture checkpoint for the design change.

### Tests

- Not run. Documentation-only change.

### Architecture checkpoint summary

- Schema checkpoint: design adds team-scoped checker selection and non-content generated-document metadata only.
- Auth/ownership checkpoint: checker selection is admin-only; generated document access remains owner-only.
- Lifecycle/deletion checkpoint: no separate content-bearing checker artifact; metadata remains under generated document/transcript-root lifecycle.
- Provider checkpoint: design reuses existing team LLM configs and Vault-backed credentials; no silent fallback to active generation LLM.
- Structured-note contract: checker only edits existing title/sections by exact substring replacement; no new sections.
- Privacy boundaries: checker uses redacted sources only; no plaintext, raw checker response storage, or content logs.

## 2026-05-30 Hallucination Check MVP

### Scope

- Implemented admin-only hallucination checker selection from ready active team LLM configs.
- Added structured-template hallucination check pass using redacted transcript, Working note, and dictation evidence plus first-pass structured note.
- Added exact-substring checker JSON edits, one retry on invalid checker output, checked/unchecked buckets, usage events, and owner-only dev debug payload behind `HALLUCINATION_CHECK_DEBUG_UI=1`.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: checker response format may have high invalid rate with some models; exact-patch retry gives early data before considering full-note fallback.

### Files changed

- `app/models.py`, `alembic/versions/s8t9u0v1w2x3_add_hallucination_check.py`: add checker selection table, generated-document metadata, encrypted debug JSON, and status enum.
- `app/services/llm.py`, `app/schemas/llm.py`, `app/routes/api_routes.py`, `app/web/presentation.py`: add admin-only checker selection API and response helpers.
- `app/services/templates.py`: run checker before reidentification/final persistence, validate/apply exact patches, record usage, and store dev debug only when enabled.
- `app/schemas/templates.py`, `app/web/transcribe_workspace.py`, `app/static/js/transcribe/documents.js`, `app/static/js/transcribe/app.js`: expose check bucket and owner-only debug panel payload.
- `tests/test_api.py`, `tests/test_migrations.py`, `app/api_route_audit.py`, `docs/api.md`, `docs/testing.md`, `docs/progress.md`: add coverage and documentation.

### Tests

- `.venv/bin/pytest -q tests/test_api.py tests/test_migrations.py tests/test_api_route_audit.py -k "hallucination_check or structured_emis_template_generation_persists_sections or alembic_upgrade_head_creates_expected_schema or alembic_head_adds_onboarding_and_session_tables or api_route_audit"`: passed, 7 tests.
- `.venv/bin/python -m py_compile app/models.py app/schemas/llm.py app/schemas/templates.py app/schemas/__init__.py app/services/llm.py app/services/templates.py app/web/presentation.py app/web/transcribe_workspace.py app/routes/api_routes.py tests/test_api.py`: passed.
- `node --check app/static/js/transcribe/documents.js && node --check app/static/js/transcribe/app.js`: passed.

### Architecture checkpoint summary

- Schema checkpoint: new checker selection is team-scoped; generated-document check/debug metadata remains under generated-document/transcript-root deletion.
- Auth/ownership checkpoint: checker config routes are system-admin-only; generated-document debug content remains owner-only and env-gated.
- Lifecycle/deletion checkpoint: no separate content-bearing checker artifact; deletion cascades remain rooted in transcript/generated document.
- Provider checkpoint: checker reuses team LLM configs/Vault refs, has optional model override, and never silently falls back to generation LLM.
- Structured-note contract: checker only edits existing title/sections by exact unique substring, cannot create sections, and omits empty sections after edits.
- Privacy boundaries: checker prompt uses redacted sources only and excludes template instructions; raw checker response is not persisted or logged.

## 2026-05-30 Hallucination Checker Model Dropdown

### Scope

- Changed admin hallucination checker model override from free text to provider-model dropdown in both admin UIs.
- Reused saved LLM provider `available_models_json` and provider default metadata from existing default model selection pattern.

### Checklist

- Target behavior: system admin chooses checker provider, then chooses provider default or discovered available model from dropdown.
- Affected schema/modules/endpoints: admin templates only; existing selection route/service validation reused.
- Affected tests: admin UI checker configuration regression updated.
- Architecture risks: none new; admin-only checker control, provider validation, and no writing-assistant fallback preserved.
- Docs referenced/updated: `docs/progress.md`, `docs/testing.md`.
- Reuse decision: reused existing provider model metadata and client-side dropdown sync pattern rather than adding new API or model discovery path.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: providers without discovered model lists still show provider default option only.

### Architecture checkpoint summary

- Schema checkpoint: no schema change.
- Auth/ownership checkpoint: controls remain system-admin-only; no transcript-derived content access changed.
- Lifecycle/deletion checkpoint: no deletion behavior changed.
- Provider checkpoint: checker still uses selected ready team LLM config and optional override from that provider's available model list.
- Structured-note contract: no change to checker prompt, patch contract, or note validation.
- Privacy boundaries: UI change only; no plaintext transcript/note data exposed.

## 2026-05-30 Hallucination Check Visibility

### Scope

- Made hallucination check status/debug visible as an explicit generated-note panel instead of only a raw debug append near the LLM request slot.
- Added UI hint when a note has check status but no debug payload because debug capture was not enabled before generation.

### Checklist

- Target behavior: owner can see checked/unchecked status and, when captured, first-pass/checker debug in the note document UI.
- Affected schema/modules/endpoints: frontend document navigator only; generated-document API already returns status/debug.
- Affected tests: static admin/UI regression checks for visible checker panel and cache-busted import.
- Architecture risks: no new content exposure; debug payload still owner-only and env-gated by API.
- Docs referenced/updated: `docs/progress.md`, `docs/testing.md`.
- Reuse decision: reused existing document LLM/debug slot and generated-document response fields.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: checker still does not run for follow-ups/quick actions by design.

### Architecture checkpoint summary

- Schema checkpoint: no schema change.
- Auth/ownership checkpoint: API still returns debug only to document owner.
- Lifecycle/deletion checkpoint: no content lifecycle change.
- Provider checkpoint: no provider-selection behavior changed.
- Structured-note contract: no checker runtime/patch contract changed.
- Privacy boundaries: panel renders only API-provided owner-visible status/debug; no extra fetch or admin cross-owner content access.

## 2026-05-30 Hallucination Checker Provider Failure Debug

### Scope

- Added safe provider failure metadata to hallucination-check debug payload: failure message, provider error code, and provider HTTP status.
- Confirmed provider failure leaves generated note ready and marks only the checker as unchecked/failed provider.

### Checklist

- Target behavior: owner debug panel explains checker-provider failure without exposing transcript-derived content or secrets.
- Affected schema/modules/endpoints: no schema/API shape change; existing debug JSON carries extra safe keys.
- Affected tests: structured hallucination-check provider failure regression added.
- Architecture risks: debug remains env-gated and owner-only; metadata only, no raw prompt/response/provider secret.
- Docs referenced/updated: `docs/progress.md`, `docs/testing.md`.
- Reuse decision: reused existing `AppError.details` and encrypted debug payload storage.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: generic provider exceptions may still have no HTTP/provider code from upstream SDK.

### Architecture checkpoint summary

- Schema checkpoint: no schema change.
- Auth/ownership checkpoint: generated-document debug remains owner-only via existing response gate.
- Lifecycle/deletion checkpoint: debug remains encrypted on generated document and follows existing deletion lifecycle.
- Provider checkpoint: no fallback change; checker still uses selected provider/model only.
- Structured-note contract: no runtime checker contract change.
- Privacy boundaries: only safe provider metadata persisted; no transcript text, note text beyond existing owner-only debug, prompt, raw response, or secret logged.

## 2026-05-30 Pending Transcript New Consult Fix

### Scope

- Allowed owners to create a new consultation while the previous stopped consultation still has queued/transcribing ingestion work.
- Kept the duplicate blank-consult guard for latest sessions with no transcript content, working note, version, or ingestion job.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none known.

### Files changed

- `app/services/transcripts.py`: treats a latest session with ingestion jobs as meaningful enough to allow a new consultation, even while status reconciles to `transcribing`.
- `tests/test_api.py`: updates the new-consult guard regression to require creation during pending transcription and verify the original job remains queued.
- `docs/transcribe_brief.md`: documents that users can open another consultation while prior transcription is pending.
- `docs/transcript-capture.md`: records pending jobs staying attached to the original transcript root while users move on.
- `docs/progress.md`: records this checklist and architecture checkpoint.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "transcript_start_rejects_second_blank_but_allows_new_session_while_transcribing or transcript_input_mode_can_switch_only_for_blank_idle_owner_session"`: passed, 2 tests.
- `node --check app/static/js/transcribe/actions.js`: passed.

### Architecture checkpoint summary

- Schema checkpoint: no schema changes.
- Auth/ownership checkpoint: `start_transcript` still derives owner/team from the authenticated user; no cross-user transcript access added.
- Lifecycle/deletion checkpoint: transcript-root cascade unchanged; queued ingestion jobs remain children of the original transcript root.
- Provider checkpoint: STT/LLM provider resolution unchanged.
- Structured-note contract: unchanged.

## 2026-05-29 Generation Wait Review Fixes

### Scope

- Fixed queued generation review regressions for pending transcription status while preserving user-controlled generation queueing.
- Fixed non-waiting queued generation so worker keeps click-time transcript snapshot even if draft changes before Celery starts.
- Kept placeholder transcript snapshots for existing non-null generated-document FK, but stopped them marking pending transcripts ready.
- Stored a private generated-document queue-time wait flag in encrypted request payload so only pending-transcription jobs refresh snapshots.
- Kept active-recording generation blocked, but allow multiple queued follow-ups/notes/actions for the same transcript; rate limits remain the queue throttle.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none known.

### Files changed

- `app/services/templates.py`: adds `mark_transcript_ready` snapshot control, active-recording queue guard, and queue-time wait flag for worker refresh.
- `app/static/js/transcribe/app.js`: leaves generation controls usable while other generated documents are queued/processing; only the local enqueue request disables controls.
- `tests/test_api.py`: asserts pending generation keeps transcript `transcribing`, refreshes to final STT snapshot, non-waiting generation keeps queued snapshot after later draft edits, and multiple follow-ups can queue for one transcript.
- `docs/testing.md`: documents generation queue policy.
- `docs/progress.md`: records review-fix checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "generation_waits_for_pending_transcription_then_uses_fresh_snapshot or generation_without_pending_transcription_keeps_queued_snapshot or generation_allows_multiple_queued_followups_and_blocks_active_recording or generation_wait_timeout_fails_without_llm_call"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "alembic_upgrade_head_creates_expected_schema or alembic_head_adds_onboarding_and_session_tables"`: passed, 2 tests.
- `node --check app/static/js/transcribe/app.js`: passed.

### Architecture checkpoint summary

- Schema checkpoint: no generated-document uniqueness guard added; multiple queued/processing documents remain valid rows.
- Auth/ownership checkpoint: owner-only generation checks preserved; locked row is rechecked before queueing.
- Lifecycle/deletion checkpoint: transcript-root cascade unchanged; multiple queued children remain cascade-owned by the transcript root; pending generation still times out/fails closed.
- Provider checkpoint: LLM/STT provider resolution unchanged.
- Structured-note contract: EMIS JSON/generated-document structure unchanged.

## 2026-05-28 Dynamic Sidebar Delegation

### Scope

- Replaced stale sidebar link/checkbox snapshots with delegated sidebar listeners and query-at-use checkbox reads.
- Kept dynamic `recent_transcripts` rendering separate from action wiring so newly inserted consultations use existing delete and recording-switch guard behavior.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/static/js/transcribe/app.js`: sidebar state styling now queries current rows and delegates checkbox changes through `sessionList`.
- `app/static/js/transcribe/actions.js`: sidebar navigation delegates clicks through `sessionList`; bulk delete queries current checkboxes on submit.
- `tests/test_admin_ui.py`: static regression checks ensure stale `sessionLinks`/`selectionBoxes` action snapshots stay removed.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 1 test.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: no endpoint or ownership change; sidebar actions still call existing owner-filtered workspace and delete APIs.
- Lifecycle/deletion checkpoint: delete semantics preserved; fix only ensures current selected transcript roots reach existing delete path.
- Provider checkpoint: no STT, LLM, de-identification, credential, or Vault behavior changed.
- Structured-note contract: no generated document or EMIS JSON behavior changed.

## 2026-05-28 Boundary Modal Sidebar Refresh

### Scope

- Fixed the boundary-modal `New consult` recording path so the newly active consultation is rendered into Recent consultations without a full page reload.
- Reused the backend workspace `recent_transcripts` payload as the sidebar source of truth and kept existing sidebar rows updated/reordered.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/static/js/transcribe/app.js`: adds sidebar row creation/rebinding in `applyWorkspacePayload` for consultations present in `recent_transcripts` but absent from the DOM.
- `app/templates/transcribe/_sidebar.html`: adds `data-session-list` hook for safe sidebar updates.
- `tests/test_admin_ui.py`: adds static regression assertions for dynamic sidebar sync hooks.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 1 test.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: sidebar rendering consumes already owner-filtered workspace payload; no new endpoint or content access path.
- Lifecycle/deletion checkpoint: no transcript root, cascade, retention, or deletion behavior changed.
- Provider checkpoint: no STT, LLM, de-identification, credential, or Vault behavior changed.
- Structured-note contract: no generated document or EMIS JSON behavior changed.

## 2026-05-27 Public Splash Landing Page

### Scope

- Added the saved splash page as the public root landing page.
- Anonymous browser users now see marketing content at `/`; authenticated users are redirected with existing post-login routing.
- Kept first-pass links to implemented routes only: `/login`, `/request-access`, and same-page anchors.
- Replaced embedded splash SVG markup with the bundled Lucide icon pack.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/templates/splashpage.html`: new root landing page template with CSP nonce on inline styles and Lucide icons loaded from local static assets.
- `app/routes/web_pages.py`: adds `GET /` public splash route with authenticated redirects.
- `tests/test_admin_ui.py`: covers anonymous splash rendering, icon-pack wiring, and authenticated root redirects.
- `docs/auth.md`, `docs/progress.md`: documents root route behavior and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "root_route or invalid_browser_route_redirects"`: passed, 5 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: existing session resolution and `_post_login_redirect` are reused; no protected content exposed.
- Lifecycle/deletion checkpoint: no transcript root, generated document, retention, or deletion path changed.
- Provider checkpoint: no provider selection, credential, Vault, STT, LLM, or de-identification behavior changed.
- Structured-note contract: no structured-note generation or EMIS JSON contract changed.

## 2026-05-25 LLM Config Test Discovery Stub

### Scope

- Restored deterministic LLM config API coverage by stubbing live OpenAI-compatible model discovery in the legacy secret-redaction provisioning test.
- No application behavior changed; invalid LLM credential rejection remains covered by dedicated tests.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `tests/test_api.py`: stubs `_list_openai_compatible_chat_models` for the dummy OpenAI token used by `test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal`.
- `docs/progress.md`: records diagnosis, verification, and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q`: failed before fix with 1 failure, `test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal`, because dummy token triggered `llm_invalid_credential`.
- `.venv/bin/pytest -q tests/test_api.py::test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_api.py -k "llm_config or llm_configs or llm_save or llm_draft or llm_provider or llm_selection"`: passed, 16 tests.

### Architecture checkpoint summary

- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: system-admin route guard unchanged; test still exercises admin-only provisioning.
- Lifecycle/deletion checkpoint: no deletion, retention, or Vault cleanup behavior changed.
- Provider checkpoint: production provider validation unchanged; test now avoids real provider calls with existing monkeypatch pattern.
- Structured-note contract: no structured-note behavior changed.

## 2026-05-25 Working-note EMIS Section Preservation

### Scope

- Structured Working-note virtual documents now include the full EMIS section definition snapshot, so selected output templates cannot hide and autosave-drop existing Working-note sections.
- Transcribe static asset version bumped for the client fix.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/static/js/transcribe/documents.js`: adds EMIS section-definition snapshots to virtual Working-note documents.
- `app/static/js/transcribe/app.js`: passes bootstrapped EMIS section definitions into the document navigator and bumps the documents module import.
- `app/templates/transcribe/_shell_extras.html`: bumps the transcribe app asset version.
- `tests/test_document_navigator_js.py`, `tests/test_admin_ui.py`: cover full section snapshots and updated cache keys.
- `docs/working_note_implementation.md`, `docs/progress.md`: document section preservation.

### Tests

- `node --check app/static/js/transcribe/documents.js`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_document_navigator_js.py tests/test_admin_ui.py -k "working_note_to_editor_document_maps_virtual_target or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_page_bootstraps_saved_working_note or transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_document_navigator_js.py`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_transcribe_page_shows_workspace_shell or transcribe_reorder_blocks_blank_note_lines or transcribe_static_asset_version_bumped_for_pii_source_visibility"`: passed, 3 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content leaves owner-only editor state.
- Ownership rules preserved: no route or query scope changed.
- Deletion semantics preserved: no delete/retention behavior changed; fix prevents accidental overwrite data loss.
- Provider rules preserved: no provider resolution or credential behavior changed.
- Structured-note contract preserved: full EMIS section list remains the Working-note editor contract; output templates still control generated output shape only.

## 2026-05-25 Working-note Save Drain And Create Validation

### Scope

- Note, follow-up, and quick-action generation now wait for queued Working-note saves to drain before enqueueing.
- Transcript create/start now reject invalid or empty legacy `structured_context_json` Working-note payloads instead of silently treating them as absent.
- Follow-up contract clarified: saved Working note is included automatically after redaction, matching current product behavior.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/static/js/transcribe/app.js`: adds a save-drain helper for generation.
- `app/services/transcripts.py`: validates create/start structured context the same way as PATCH.
- `app/templates/transcribe/_shell_extras.html`: bumps the transcribe app asset version.
- `tests/test_api.py`, `tests/test_admin_ui.py`: cover create/start rejection and frontend save-drain wiring.
- `docs/api.md`, `docs/testing.md`, `docs/working_note_implementation.md`, `docs/progress.md`: document the current Working-note follow-up contract and validation.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "transcript_create_rejects_invalid_structured_context or followup_generation_uses_saved_working_note_when_transcript_empty"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_frontend_uses_global_template_selector_for_generation_controls or user_transcribe_page_shows_workspace_shell"`: passed, 3 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: provider prompts still receive only redacted Working-note snapshots.
- Ownership rules preserved: create/start still require full owner context; generation still loads owner-scoped transcript content.
- Deletion semantics preserved: no delete or retention behavior changed.
- Provider rules preserved: no provider resolution or credential path changed.
- Structured-note contract preserved: invalid/non-EMIS and empty structured payloads fail closed.

## 2026-05-25 Working-note Follow-up Contract

### Scope

- Follow-up generation now supports Working-note-only consultations.
- Follow-up jobs snapshot saved Working note, redact it, and include only redacted Working-note text in outbound LLM prompts.
- Empty legacy structured Working-note mode no longer locks the UI when normalized content is empty.
- Transcript list API now populates `working_note_mode` and `has_working_note` from decrypted/normalized content.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/services/templates.py`: follow-up queue/process path now uses saved Working-note snapshot and redaction helper.
- `app/services/transcripts.py`, `app/web/transcribe_workspace.py`, `app/routes/api_routes.py`: derive public Working-note summary from normalized content and use it in list/detail responses.
- `app/static/js/transcribe/actions.js`, `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: save dirty Working note before follow-up submit, enable follow-up from transcript or Working note, and bump cache keys.
- `tests/test_api.py`, `tests/test_admin_ui.py`: cover Working-note-only follow-up generation, empty legacy mode, list summaries, and frontend wiring.
- `docs/transcript-capture.md`, `docs/working_note_implementation.md`, `docs/testing.md`, `docs/progress.md`: document follow-up Working-note contract.

### Tests

- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "working_note_routes_enforce_owner_mode_lock_and_clear or empty_legacy_structured_working_note_does_not_lock_mode or followup_generation_uses_saved_working_note_when_transcript_empty or followup_generation_queues_and_processes_with_owner_scope"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls or transcribe_static_asset_version_bumped_for_pii_source_visibility or user_transcribe_page_shows_workspace_shell"`: passed, 3 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: follow-up prompts use redacted Working-note snapshot, not raw Working-note text.
- Ownership rules preserved: transcript and Working-note reads remain owner-only.
- Deletion semantics preserved: no delete path changed; empty legacy response normalization does not mutate content.
- Provider rules preserved: existing LLM and redaction provider resolution reused.
- Structured-note contract preserved: EMIS normalization remains source of truth for structured Working-note content.

## 2026-05-25 Working-note Review Race Fixes

### Scope

- Quick actions now save dirty Working-note edits before posting `/run-quick-action`.
- Legacy transcript `structured_context_json` PATCH now rejects invalid or empty structured payloads instead of clearing encrypted structured Working notes.
- Bumped transcribe static asset versions for the quick-action UI change.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/static/js/transcribe/actions.js`, `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: save dirty Working note before quick actions and bust cached assets.
- `app/services/transcripts.py`: reject invalid/empty legacy structured-context PATCH payloads before storage mutation.
- `tests/test_api.py`, `tests/test_admin_ui.py`: cover legacy PATCH non-clear behavior and static UI wiring.
- `docs/working_note_implementation.md`, `docs/testing.md`, `docs/progress.md`: document final contract and checks.

### Tests

- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "working_note_routes_enforce_owner_mode_lock_and_clear or transcript_patch_rejects_invalid_structured_context_without_clearing_working_note or quick_action_generation_uses_saved_working_note_when_transcript_empty"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_page_bootstraps_saved_working_note"`: passed, 3 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: quick actions still load saved owner-only Working note and provider prompts use existing redaction path.
- Ownership rules preserved: no route gate changes; owner-only transcript lookup still protects content reads/writes.
- Deletion semantics preserved: legacy PATCH cannot delete Working note; clear remains immediate only through `DELETE /working-note` with version check.
- Provider rules preserved: no provider resolution or credential changes.
- Structured-note contract preserved: invalid/non-EMIS and empty structured payloads fail closed.

## 2026-05-25 Working-note Quick-action Contract

### Scope

- Preserved quick-action contract: saved Working note is included automatically, but only after transient redaction.
- Counted Working-note-only consultations as non-empty for new-session lifecycle checks.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/services/transcripts.py`: includes saved Working note in meaningful-content checks.
- `tests/test_api.py`: verifies quick-action Working-note redaction and Working-note-only new-session lifecycle.
- `docs/transcript-capture.md`, `docs/testing.md`, `docs/working_note_implementation.md`, `docs/progress.md`: update contract and verification notes.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "working_note_only_session_allows_new_session or quick_action_generation_uses_saved_working_note"`: passed, 2 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: raw Working note stays owner-only and provider payload receives only redacted Working-note text.
- Ownership rules preserved: existing owner-only Working-note save/snapshot paths unchanged.
- Deletion semantics preserved: Working note remains under transcript-root retention/deletion; lifecycle check only treats saved content as non-empty.
- Provider rules preserved: existing LLM/de-identification provider resolution reused.
- Structured-note contract preserved: no EMIS output schema change.

## 2026-05-24 Working-note Review Fixes

### Scope

- Kept quick actions as full-context generation: saved Working note is expected to be redacted and included automatically.
- Added optimistic concurrency to Working-note clear, including stale clear rejection and stale save-after-clear rejection.
- Rejected unsupported EMIS Working-note section keys instead of dropping clinician text.
- Backfilled encrypted legacy `structured_context_json` values into structured Working-note mode locks.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/services/transcripts.py`: strict structured key validation and Working-note version conflict handling.
- `app/routes/api_routes.py`, `app/schemas/transcripts.py`: clear payload with `expected_updated_at`.
- `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: clear request sends current token and app cache key bumped.
- `alembic/versions/r7s8t9u0v1w2_add_working_notes.py`: encrypted legacy structured context backfill.
- `tests/test_api.py`, `tests/test_migrations.py`, `tests/test_admin_ui.py`: concurrency, validation, migration, and asset-version coverage.
- `docs/api.md`, `docs/working_note_implementation.md`, `docs/testing.md`, `docs/progress.md`: contract and verification notes.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "working_note or quick_action_generation_uses_saved_working_note"` with `COOKIE_SECURE_MODE=never`: passed, 6 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "working_note_migration_backfills_encrypted_structured_context"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_static_asset_version_bumped_for_pii_source_visibility or working_note"` with `COOKIE_SECURE_MODE=never`: passed, 4 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: quick actions intentionally use saved Working note only after redaction; no new content visibility or logging.
- Ownership rules preserved: owner transcript lookup still gates read/save/clear.
- Deletion semantics preserved: clear remains immediate deletion, now protected from stale tabs.
- Provider rules preserved: no provider resolution or credential flow changed.
- Structured-note contract preserved: unsupported EMIS section keys now fail closed.

## 2026-05-22 Pytest Warning Cleanup

### Scope

- Removed app-owned pytest warning noise from route audit cookie handling and OpenAPI validation.
- Pinned `requests` and compatible `chardet` versions to avoid dependency mismatch warnings.
- Added narrow pytest filters for upstream Starlette, Prance, and Prance-triggered OpenAPI validator deprecations.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: upstream deprecation filters should be revisited when FastAPI/Starlette/Prance/OpenAPI validator are upgraded.

### Files changed

- `app/api_route_audit.py`: sends audit cookies via `Cookie` header instead of deprecated per-request `cookies=`.
- `app/services/provider_inspection.py`: uses `openapi_spec_validator.validate` instead of deprecated `validate_spec`.
- `requirements.txt`: pins `requests==2.32.5` and `chardet==5.2.0`.
- `pytest.ini`: adds narrow filters for known upstream-only deprecations.
- `docs/progress.md`: records warning cleanup and verification.

### Tests

- `.venv/bin/pytest -q tests/test_api_route_audit.py tests/test_provider_inspection.py tests/test_api.py -k "route_audit or provider_inspection or inspect_stt_openapi or inspect_generic_stt_dynamic_field_names"`: passed, 11 tests selected, no warnings shown.
- `.venv/bin/pytest -q`: passed, 650 tests, 1 skipped, no warnings shown.

### Documentation

- Progress note added for warning cleanup and remaining upstream-filter assumption.

### Risks / assumptions

- Audit cookies are generated internally and contain URL-safe session/CSRF values, so direct `Cookie` header construction is safe for this negative-audit helper.
- Warning filters are scoped to known third-party modules/messages, not blanket deprecation suppression.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note content access or logging changed.
- Ownership rules preserved: route audit sends same session cookies through headers; endpoint auth behavior unchanged.
- Deletion semantics preserved: no lifecycle or cascade behavior changed.
- Provider rules preserved: provider inspection still validates OpenAPI documents and resolves refs; credential handling unchanged.
- Structured-note contract preserved: no structured output/schema changes.

## 2026-05-22 Follow-up UI Hook Regression

### Scope

- Restored follow-up custom prompt textarea hook used by frontend submission, enablement, recording, and clear flows.
- Rendered recent follow-up/quick-action document titles as main labels while keeping prompt/source detail visible.
- Refined hook regression tests to assert the shared textarea owns both runtime hooks instead of relying on one exact raw attribute string.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: full suite not rerun after focused follow-up regression checks.

### Files changed

- `app/templates/transcribe/_workspace.html`: restores `data-followup-prompt-input` on the shared follow-up context textarea and shows generated document titles plus prompt/source detail in the recent follow-up list.
- `app/static/js/transcribe/documents.js`: keeps hydrated follow-up history rendering aligned with the server-rendered list.
- `tests/test_admin_ui.py`: updates freeform follow-up UI assertion to check both runtime hooks on the same empty textarea.
- `tests/test_web_refactor.py`: replaces brittle exact hook-order check with a co-located textarea hook assertion.
- `docs/progress.md`: records this fix and verification.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_structured_note_content tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_freeform_note_content tests/test_admin_ui.py::test_user_transcribe_page_renders_generated_document_switchers tests/test_web_refactor.py::test_followup_redesign_preserves_required_hooks`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py::test_user_transcribe_page_truncates_document_switcher_labels tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_structured_note_content tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_freeform_note_content tests/test_admin_ui.py::test_user_transcribe_page_renders_generated_document_switchers tests/test_web_refactor.py::test_followup_redesign_preserves_required_hooks`: passed, 5 tests.
- `node --check app/static/js/transcribe/documents.js`: passed.

### Documentation

- Progress note added for follow-up UI hook regression.

### Risks / assumptions

- Existing single textarea remains intentional shared input for quick-action context and custom follow-up prompts.
- No product decision removed custom follow-up generation or saved generated-document switching.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content exposure; owner-only transcribe render remains unchanged.
- Ownership rules preserved: no generated-document lookup or transcript ownership filter changed.
- Deletion semantics preserved: no generated-document lifecycle, retention, or cascade behavior changed.
- Provider rules preserved: no provider resolution, credential, or LLM queueing behavior changed.
- Structured-note contract preserved: EMIS keys and structured output handling unchanged.

## 2026-05-22 Quick Action Source Guard

### Scope

- Critiqued `working_note_corrections.md`; kept quick-action UI/source guard alignment, rejected zero-source quick-action generation.
- Quick-action controls now enable when saved transcript text, Working note, or dictation exists, matching backend queue eligibility.
- Follow-up generation stays on its existing source path and does not implicitly consume Working note.
- Removed uncached live STT health probes from initial `/transcribe` HTML render to protect LCP TTFB; workspace API and explicit recheck still perform live health checks.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: direct browser QA still useful for selected quick-action primary button state after changing selection.

### Files changed

- `app/web/transcribe_workspace.py`: adds quick-action source availability to template context.
- `app/templates/transcribe/_workspace.html`: gates quick-action controls with quick-action eligibility instead of generated-note eligibility.
- `app/static/js/transcribe/app.js`, `app/static/js/transcribe/actions.js`: share source guard between note generation and quick actions, keep follow-up guard separate, avoid falling through to follow-up submit when only quick-action sources exist, and bump the actions module cache key.
- `app/services/stt.py`: adds cache-only STT health mode for paint-critical page render.
- `app/templates/transcribe/_shell_extras.html`: bumps app JS asset key.
- `tests/test_admin_ui.py`, `tests/test_api.py`: cover Working-note/dictation-only quick-action UI enablement and zero-source backend rejection.
- `docs/working_note_implementation.md`, `docs/api.md`, `working_note_corrections.md`, `docs/progress.md`: document critique and final contract.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "quick_actions_enabled_for_saved_working_note or quick_actions_enabled_for_saved_dictation or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_reorder_blocks_blank_note_lines or transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 5 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls or transcribe_static_asset_version_bumped_for_pii_source_visibility"`: passed, 2 tests after actions module cache-key bump.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_page_does_not_block_on_uncached_stt_health or transcribe_frontend_uses_global_template_selector_for_generation_controls or transcribe_static_asset_version_bumped_for_pii_source_visibility"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace_stt_health_plain_for_user_diagnostic_for_leader or transcribe_stt_health_recheck_bypasses_workspace_cache"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "quick_action_generation_uses_saved_working_note_when_transcript_empty or run_quick_action_rejects_empty_consultation_sources"`: passed, 2 tests after rerun. First parallel attempt exited because shared OpenScribe test database was already in use.

### Documentation

- Working-note implementation and API docs now state quick actions require at least one saved consultation source.

### Risks / assumptions

- Quick-action instructions alone remain insufficient clinical context by design.
- Server-rendered Generate button can be enabled before a quick action is selected when follow-up is unavailable but a quick-action source exists; JS shows a select-action warning instead of submitting an unsupported follow-up.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content exposure; UI only unlocks existing owner-only quick-action route.
- Ownership rules preserved: backend owner transcript check unchanged.
- Deletion semantics preserved: no lifecycle, retention, cascade, or clear behavior changed.
- Provider rules preserved: provider resolution/redaction path unchanged; zero-source requests still fail before LLM queueing.
- Structured-note contract preserved: EMIS keys and saved Working-note shape unchanged.

## 2026-05-21 Pytest Failure Cleanup

### Scope

- Fixed the listed pytest failures while trimming legacy hook expectations instead of keeping compatibility-only markup.
- Removed compatibility-only hooks restored during first pass: login-page transcribe form, `name="context_*"` editor aliases, `data-quick-action-kind`, and GLM-2 all-EMIS special-case rendering.
- Reworked brittle UI assertions to target current behavior: bootstrap form on login, template-defined structured sections, quick-pick icons, and provider adapter support without exact JS source-line pinning.
- Made LLM config save fall back to manual model metadata when live discovery rejects a key, while draft/inspect still reject invalid credentials.

### Checklist

- Code complete: yes
- Tests added/updated: focused regressions rerun; no new tests needed because failures were existing coverage.
- Docs added/updated: yes.
- Open issues: full suite not rerun after focused fix.

### Files changed

- `errors.md`: listed provided failures and verification runs.
- `app/templates/transcribe/_workspace.html`: keeps runtime hooks and user-facing follow-up copy; removes dead structured input names and quick-action kind metadata.
- `app/templates/transcribe/_shell_extras.html`: points follow-up prompt JS at existing quick-action context textarea hook.
- `app/web/transcribe_workspace.py`: uses one template-section selection path for `/transcribe` and `/transcribe-glm-2`.
- `app/services/llm.py`: treats save-time live discovery credential rejection as manual-required metadata instead of blocking config save.
- `tests/test_admin_ui.py`: removes legacy hook/source-line assertions and checks current behavior instead.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py::test_login_page_exposes_bootstrap_when_database_is_empty tests/test_admin_ui.py::test_user_transcribe_glm_2_page_uses_structured_template_sections tests/test_admin_ui.py::test_user_transcribe_page_truncates_document_switcher_labels tests/test_admin_ui.py::test_user_transcribe_page_shows_structured_emis_context_inputs tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_freeform_note_content tests/test_admin_ui.py::test_user_transcribe_page_shows_transcript_and_followup_empty_states tests/test_admin_ui.py::test_user_transcribe_page_reloads_persisted_structured_emis_context tests/test_admin_ui.py::test_user_transcribe_page_can_queue_followup_generation tests/test_admin_ui.py::test_user_transcribe_page_can_run_quick_action tests/test_admin_ui.py::test_admin_templates_sync_optional_provider_credential_actions tests/test_api.py::test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal`: passed, 11 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "llm_draft_invalid_key_creates_no_config_or_vault_secret or llm_provider_preset_saves_and_reclassifies_base_url_override or llm_save_validates_model_against_successful_live_discovery or llm_zero_model_discovery_requires_manual_model or llm_endpoint_change_with_failed_rediscovery_clears_stale_models"`: passed, 5 tests.

### Documentation

- Added `errors.md` failure log and this progress note.

### Risks / assumptions

- Save-time LLM config upsert now permits manual model save even if live discovery rejects supplied credential; draft/inspect remain the stricter validation path.
- Legacy hook assertions were removed where runtime code did not need the hook.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new transcript/note content exposure; only owner-rendered transcribe fields changed.
- Ownership rules preserved: no route ownership lookup changed.
- Deletion semantics preserved: no transcript/generated document lifecycle change.
- Provider rules preserved: credentials remain Vault-backed and unrevealed; save path records manual-required discovery metadata when live discovery cannot validate.
- Structured-note contract preserved: EMIS allowed section keys remain the only rendered structured sections; template-specific section rendering now uses one shared path across transcribe routes.

## 2026-05-21 Quick Action Working Note Context

### Scope

- Plumbed saved Working note into quick-action generation.
- Quick-action queue now snapshots Working note onto the generated document and allows empty transcript when saved Working note or dictation exists.
- Quick-action prompt building now includes labelled Working-note context after redaction.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: follow-up generation remains unchanged; Working note is not automatically included there.

### Files changed

- `app/services/templates.py`: shared redacted Working-note prompt helper, quick-action snapshots, prompt inclusion.
- `tests/test_api.py`: quick-action Working-note prompt/snapshot test and redaction fail-closed test.
- `docs/working_note_implementation.md`, `docs/api.md`, `docs/progress.md`: updated generation contract and progress note.

### Tests

- `.venv/bin/python -m py_compile app/services/templates.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "quick_action_generation_uses_saved_working_note or quick_action_generation_fails_closed_when_working_note_redaction_fails or template_generation_uses_saved_working_note_when_transcript_empty or template_generation_fails_closed_when_working_note_redaction_fails"`: passed, 4 tests.

### Documentation

- Documented that quick actions use saved transcript/dictation/Working-note sources plus quick-action instructions/context.
- Documented that follow-ups remain opt-in for Working-note context.

### Risks / assumptions

- Product scope interpreted as quick actions only; follow-ups intentionally unchanged.
- Working-note snapshot fields are reused for quick-action documents; no schema change needed.

### Architecture checkpoint summary

- Privacy boundaries preserved: no Working-note content added to request payloads or logs; server loads owner-scoped saved Working note.
- Ownership rules preserved: existing owner-only transcript checks still gate quick-action generation.
- Deletion semantics preserved: snapshots remain generated-document children under transcript-root cascade/retention.
- Provider rules preserved: provider resolution unchanged; Working note is redacted before outbound LLM calls.
- Structured-note contract preserved: structured Working note uses existing EMIS normalization and rendering.

## 2026-05-21 Working Note Baseline Helper Cleanup

### Scope

- Critiqued `working_note_corrections.md`; kept low-risk rename, note-save baseline helper extraction, and focused baseline regression tests.
- Rejected typed enqueue result/options-object refactor as extra churn without a current bug.
- Clarified duplicate generation behavior: first in-flight template request wins while template controls are locked.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: enqueue behavior still has static coverage only; deeper behavioral test should wait until enqueue is extracted from DOM-heavy app module.

### Files changed

- `app/static/js/transcribe/noteSaveState.js`: pure dirty-baseline capture/save helpers.
- `app/static/js/transcribe/app.js`: uses baseline helpers and clearer duplicate close-modal flag name.
- `app/templates/transcribe/_shell_extras.html`: frontend asset key bump.
- `tests/test_note_save_state_js.py`: focused Node baseline regression tests.
- `tests/test_admin_ui.py`: updated static assertions and asset key checks.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: documented critique and final behavior.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/noteSaveState.js`: passed.
- `.venv/bin/pytest -q tests/test_note_save_state_js.py tests/test_admin_ui.py -k "transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_frontend_uses_global_template_selector_for_generation_controls or user_transcribe_page_shows_workspace_shell or transcribe_reorder_blocks_blank_note_lines or note_save_state"`: passed, 6 tests.

### Documentation

- Updated Working-note implementation notes for baseline helper ownership and first-template-wins duplicate enqueue behavior.
- Rewrote correction critique with kept/rejected decisions.

### Risks / assumptions

- Template UI locking prevents normal user drift, not malicious request replay.
- Client duplicate enqueue guard remains not backend idempotency.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content exposure added; generation request body remains `template_id` only.
- Ownership rules preserved: no auth/API change; server still resolves owner-scoped sources.
- Deletion semantics preserved: no retention, cascade, clear, or hard-delete paths changed.
- Provider rules preserved: no provider selection, credentials, redaction provider, or LLM payload schema changed.
- Structured-note contract preserved: EMIS keys/validation and structured source shape unchanged.

## 2026-05-21 Working Note Final Hardening

### Scope

- Critiqued `working_note_corrections.md`; kept target-scoped save baseline, generation-busy template locking, duplicate modal-close merge, and unused parameter cleanup.
- Rejected broader enqueue splitting/state-object refactors as extra churn without enough debt reduction.
- Hardened generated-note optimistic-lock baseline selection and generation UI state consistency.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: static frontend regressions remain a compromise until enqueue logic is extracted into a small testable JS module.

### Files changed

- `app/static/js/transcribe/app.js`: target-scoped save baseline helper, generation-busy template/picker locks, duplicate close-modal intent merge, asset import key bump.
- `app/static/js/transcribe/actions.js`: removed unused `syncGenerationAvailability` parameter.
- `app/templates/transcribe/_shell_extras.html`: frontend asset key bump.
- `tests/test_admin_ui.py`: static regressions for baseline helper, UI locking, duplicate close intent, removed action wiring, and asset key.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: documented critique and final behavior.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_transcribe_page_shows_workspace_shell or transcribe_reorder_blocks_blank_note_lines or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 4 tests.

### Documentation

- Updated Working-note implementation notes for duplicate close intent, target-scoped optimistic-lock baselines, and generation-busy template locks.
- Rewrote correction critique with kept/modified/rejected decisions.

### Risks / assumptions

- Duplicate enqueue prevention remains client-side only; backend idempotency remains separate future hardening.
- Template UI locking prevents honest user drift, not malicious request replay.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content exposure added; generation request body remains `template_id` only.
- Ownership rules preserved: no auth/API change; owner-scoped server source resolution unchanged.
- Deletion semantics preserved: no retention, cascade, clear, or hard-delete paths changed.
- Provider rules preserved: no provider selection, credential, redaction provider, or LLM payload schema changed.
- Structured-note contract preserved: EMIS keys/validation and structured source shape unchanged.

## 2026-05-21 Working Note Enqueue Hardening

### Scope

- Critiqued `working_note_corrections.md`; kept race/duplicate-submit hardening, deleted one-call helper, rejected removing defensive final UI sync.
- Snapshotted transcript id before async Working-note save so generation POST cannot drift to a newly selected consultation.
- Duplicate generation submits now return the existing in-flight promise instead of silently returning `false`.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: direct behavioral JS test deferred until enqueue logic is extracted from large DOM module.

### Files changed

- `app/static/js/transcribe/app.js`: transcript-id snapshot, duplicate-submit promise reuse, inline post-success generation flow.
- `app/templates/transcribe/_shell_extras.html`: frontend asset key bump.
- `tests/test_admin_ui.py`: static regressions for enqueue snapshot/promise reuse/helper removal and asset key.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: updated final behavior and critique.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_transcribe_page_shows_workspace_shell or transcribe_reorder_blocks_blank_note_lines or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 4 tests.

### Documentation

- Updated Working-note implementation note with enqueue snapshot and duplicate-submit behavior.
- Rewrote correction critique with kept/modified/rejected decisions.

### Risks / assumptions

- Duplicate prevention remains client-side only; backend idempotency is still separate future hardening.
- Static frontend assertions cover this slice; a direct Node behavior test should follow only after generation enqueue is extracted into a small testable module.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content exposure added; generation body remains `template_id` only.
- Ownership rules preserved: no API/auth change; server still resolves owner-scoped transcript, Working note, and dictation sources.
- Deletion semantics preserved: no retention, cascade, clear, or hard-delete paths changed.
- Provider rules preserved: no provider selection, credentials, redaction provider, or LLM payload schema changed.
- Structured-note contract preserved: EMIS keys/validation and structured source shape unchanged.

## 2026-05-21 Working Note Queue Cleanup

### Scope

- Critiqued `working_note_corrections.md`; kept all suggested fixes, narrowed centralization to app-owned enqueue success handling.
- Fixed dirty Working-note first-save conflict baseline so `""` remains an intentional no-existing-note sentinel and serializes as `null`.
- Centralized template-generation post-success UI flow and removed dead `silent` option.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no new backend idempotency added; duplicate enqueue guard remains client-side.

### Files changed

- `app/static/js/transcribe/app.js`: explicit Working-note dirty timestamp sentinel, shared generation success helper, dictation modal close option, removed unused `silent` parameter.
- `app/static/js/transcribe/actions.js`: normal Generate delegates all enqueue success effects to `app.js`.
- `app/templates/transcribe/_shell_extras.html`: bumps frontend asset key.
- `tests/test_admin_ui.py`: updates static regression checks for sentinel handling, shared success helper, asset key, and removed dead callback/option.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: document critique and final behavior.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_transcribe_page_shows_workspace_shell or transcribe_frontend_uses_global_template_selector_for_generation_controls or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_reorder_blocks_blank_note_lines"`: passed, 4 tests.

### Documentation

- Updated Working-note implementation notes for explicit baseline sentinel and app-owned generation success flow.
- Rewrote correction critique with kept/modified decisions and architecture checkpoints.

### Risks / assumptions

- Multi-tab overwrite protection still depends on server `expected_updated_at` conflict handling; this change preserves correct client baseline.
- Generation duplicate prevention remains a UI guard, not server idempotency.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content exposure added; generation request body remains `template_id` only.
- Ownership rules preserved: no auth/API change; server still loads owner-scoped transcript, Working note, and dictation sources.
- Deletion semantics preserved: no retention, cascade, clear, or hard-delete paths changed.
- Provider rules preserved: no provider selection, credentials, redaction provider, or LLM payload schema changed.
- Structured-note contract preserved: EMIS keys/validation and structured source shape unchanged.

## 2026-05-21 Working Note In-Flight Generation Guard

### Scope

- Critiqued `working_note_corrections.md`; kept all suggested fixes, but removed timer/debounce entirely instead of layering it over in-flight state.
- Centralized template generation enqueue in `app.js` and shared it between normal Generate and dictation Save & generate.
- Removed dead structured-context autosave/no-op state from `structured.js` and app wiring.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: backend idempotency remains future hardening for cross-tab/malicious duplicate enqueue attempts.

### Files changed

- `app/static/js/transcribe/app.js`: adds shared `enqueueTemplateGeneration`, request-scoped busy state, and central generation request body.
- `app/static/js/transcribe/actions.js`: normal Generate now delegates enqueue to app helper.
- `app/static/js/transcribe/structured.js`: removes obsolete structured-context autosave state.
- `app/templates/transcribe/_shell_extras.html`: bumps frontend asset key.
- `tests/test_admin_ui.py`: updates static regression checks for shared helper, in-flight state, and removed dead paths.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: document critique and final behavior.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/structured.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_reorder_blocks_blank_note_lines or user_transcribe_glm_2_page_prioritises_latest_note_and_emis_driven_generation"`: passed, 4 tests.

### Documentation

- Updated Working-note implementation notes to describe request-lifetime guard shared by both generation entry points.
- Rewrote correction critique with kept/modified decisions and architecture checkpoints.

### Risks / assumptions

- Guard is still client-side. It prevents UI duplicate submits but is not a server idempotency guarantee.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content exposure added; request body remains `template_id` only.
- Ownership rules preserved: no API/auth change; server still resolves transcript owner and saved sources.
- Deletion semantics preserved: no lifecycle/cascade/retention path changed.
- Provider rules preserved: no provider selection, credential, redaction, or LLM request schema change.
- Structured-note contract preserved: EMIS keys/validation unchanged; removed only dead autosave plumbing.

## 2026-05-21 Working Note Generate Guard Cleanup

### Scope

- Critiqued `working_note_corrections.md` and kept the low-risk cleanup items: generated-note-only content no longer enables note generation, stale hidden `context_*` fields/sync plumbing were removed, and note Generate submissions are guarded for 3 seconds.
- Kept `collectStructuredContext()` and legacy generated-document structured-context reader because they still have compatibility/editor roles.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: behavioral JS runner tests would be better than current static substring assertions for future frontend refactors.

### Files changed

- `app/static/js/transcribe/app.js`: removes generated structured editor content from note generation availability and respects the 3 second Generate guard.
- `app/static/js/transcribe/actions.js`: adds the 3 second duplicate-submit guard for note generation.
- `app/static/js/transcribe/structured.js`, `app/templates/transcribe/_workspace.html`: remove dead structured-context hidden field sync.
- `app/templates/transcribe/_shell_extras.html`: bumps frontend asset cache key.
- `tests/test_admin_ui.py`: updates static/frontend regressions for hidden-field removal, source availability, asset key, and click guard.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: document critique decisions and final behavior.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/structured.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls or user_transcribe_glm_2_page_prioritises_latest_note_and_emis_driven_generation or transcribe_page_exposes_home_and_context_settings_controls or transcribe_reorder_blocks_blank_note_lines or transcribe_static_asset_version_bumped_for_pii_source_visibility"`: passed, 5 tests.

### Documentation

- Updated Working-note implementation notes to state generation forms do not render legacy `context_*` fields and note generation has a 3 second client-side duplicate-submit guard.
- Rewrote `working_note_corrections.md` with kept/modified/deferred decisions.

### Risks / assumptions

- Generate guard is client-side only; backend idempotency/rate limiting remains separate future hardening if needed.
- `collectStructuredContext()` name remains imperfect but was kept to avoid broader editor refactor.

### Architecture checkpoint summary

- Privacy boundaries preserved: generated-note output is no longer treated as generation input; no transcript-derived content exposure added.
- Ownership rules preserved: no route/auth change; generation still uses owner-scoped transcript APIs.
- Deletion semantics preserved: no lifecycle/cascade/retention behavior changed.
- Provider rules preserved: no provider selection, credential, redaction, or LLM request schema change beyond source gating.
- Structured-note contract preserved: EMIS section keys/validation unchanged; transient form fields removed in favor of saved Working-note source contract.

## 2026-05-20 Working Note Generation Contract Tightening

### Scope

- Critiqued `working_note_corrections.md` and kept the contract/concurrency fixes, while deferring broad legacy field renames.
- Tightened template generation so `POST /generate-output` accepts only `template_id`; saved transcript text, dictation, and Working note are the only generation sources.
- Fixed dirty note optimistic-save baseline so workspace refresh cannot silently advance the `expected_updated_at` used by an unsaved Working-note edit.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser timing QA still useful for multi-tab autosave conflict behavior.

### Files changed

- `app/schemas/templates.py`, `app/routes/api_routes.py`, `app/routes/web_transcribe.py`, `app/services/templates.py`, `app/main.py`: removed transient structured-context generation request path and dead serializer/form plumbing.
- `app/static/js/transcribe/app.js`: stores dirty edit baseline timestamp and updates it only after this tab's own save succeeds with newer edits still pending.
- `tests/test_api.py`, `tests/test_admin_ui.py`: added/updated generation-source and optimistic-baseline regressions.
- `docs/api.md`, `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: documented final contract and critique decisions.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k transcribe_frontend_uses_global_template_selector_for_generation_controls`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_api.py -k "working_note or structured_emis_generation or generate_output_rejects_transient_structured_context_payload or generated_note_context or redaction_boundary_for_static_and_structured_dynamic_inputs"`: passed, 8 tests.
- One parallel API pytest attempt exited because the shared OpenScribe test database was already in use; rerun passed alone.

### Documentation

- Updated API and Working-note docs to state generation rejects transient `structured_context` and uses saved sources only.
- Rewrote correction critique with kept/modified/deferred decisions.

### Risks / assumptions

- Existing DB field names (`structured_context_json`, `active_structured_context`) remain until a deliberate migration/compatibility cleanup.
- Old generated documents with `generated_documents.structured_context_json` can still be processed; new generation does not populate that field.

### Architecture checkpoint summary

- Privacy boundaries preserved: no generated-note content becomes generation input; Working note remains owner-only transcript-derived content.
- Ownership rules preserved: generation and Working-note save still require transcript owner.
- Deletion semantics preserved: no schema/lifecycle change; transcript cascade and Working-note clear behavior unchanged.
- Provider rules preserved: no provider selection/credential changes; saved Working note still redacts before LLM calls.
- Structured-note contract preserved: EMIS keys/validation unchanged; source contract is stricter.

## 2026-05-19 Working Note Correction Critique Follow-up

### Scope

- Reviewed `working_note_corrections.md` and kept all three items, with the empty-draft item narrowed to explicit status feedback instead of blocking never-saved blank drafts.
- Fixed dirty unlocked Working-note saves so template switching cannot make structured content serialize as freeform, or vice versa.
- Stopped saved structured Working notes from also populating generated-document structured context; legacy explicit `structured_context` payloads still use that path.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser timing QA still useful for template switch/autosave race confidence.

### Files changed

- `app/static/js/transcribe/app.js`: tracks dirty rendered note mode and shows explicit empty draft status.
- `app/services/templates.py`: separates Working-note snapshots from legacy generated-document structured context.
- `tests/test_api.py`, `tests/test_admin_ui.py`: cover no duplicate structured context and JS regression hooks.
- `docs/working_note_implementation.md`, `working_note_corrections.md`, `docs/progress.md`: document critique and finalized behavior.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js && node --check app/static/js/transcribe/documents.js && node --check app/static/js/transcribe/structured.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "structured_emis_generation_snapshots_working_note_without_structured_context_duplication or structured_emis_generation_filters_transcript_context_sections_removed_by_template"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_api.py -k working_note`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k transcribe_frontend_uses_global_template_selector_for_generation_controls`: passed, 1 test.
- One earlier parallel API pytest attempt exited because the shared OpenScribe test database was already in use; rerun passed alone.

### Documentation

- Updated Working-note implementation notes and correction critique.

### Risks / assumptions

- Legacy explicit `structured_context` is still supported for API/web compatibility; frontend generation does not send it.

### Architecture checkpoint summary

- Privacy boundaries preserved: Working-note content remains owner-only and is not newly exposed.
- Ownership rules preserved: generation still requires transcript owner; no route auth change.
- Deletion semantics preserved: Working-note clear/delete behavior and transcript cascade unchanged.
- Provider rules preserved: no provider selection, credential, or redaction boundary change.
- Structured-note contract preserved: EMIS validation and section keys unchanged; source channels are now less duplicative.

## 2026-05-19 Working Note Corrections

### Scope

- Removed the parallel Working note editor mode from the transcribe frontend.
- Recast Working note as a synthetic note target (`working:<transcript_id>`) rendered through the generated-note editor path.
- Kept Working note differences limited to target construction, save payload/endpoint, clear action, and mode-lock display.
- Added optimistic `expected_updated_at` conflict handling to `PATCH /working-note`.
- Fixed structured Working note re-rendering after switching to a generated note and back.
- Critiqued the remaining `working_note_corrections.md` items and kept all three fixes, with unchecked visible structured lines treated as persisted Working note text.
- Blocked generation when an active dirty Working note has been emptied, preventing stale saved Working note snapshots from feeding generation before the user explicitly clears.
- Tightened `PATCH /working-note` so timestamped notes reject omitted `expected_updated_at`; first-save behavior remains unchanged.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser QA still useful for exact focus restoration and autosave timing.

### Files changed

- `app/static/js/transcribe/app.js`, `documents.js`, `structured.js`, `actions.js`: target-based note editor flow, no `activeEditorSource`/`renderWorkingNote` split.
- `app/templates/transcribe/_workspace.html`: server-rendered Working note selector id now matches `working:<transcript_id>`.
- `app/schemas/transcripts.py`, `app/services/transcripts.py`: working-note optimistic conflict field and stricter missing-version check.
- `tests/test_api.py`, `tests/test_admin_ui.py`: conflict coverage and static regression expectations for virtual-note target reuse, dirty-empty generation blocking, and unchecked-line persistence.
- `docs/working_note_implementation.md`, `docs/progress.md`, `working_note_corrections.md`: updated implementation notes and correction critique.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `node --check app/static/js/transcribe/structured.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k working_note`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k working_note`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_api.py -k "working_note_routes_enforce_owner_mode_lock_and_clear"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls"`: passed, 1 test.
- `node --check app/static/js/transcribe/app.js`: passed after correction follow-up.
- Browser MCP on local `/transcribe`: reproduced saved structured Working note content disappearing after switching to a generated note and back, then verified it re-renders and PATCHes both existing and newly added lines.

### Documentation

- Updated working-note implementation notes with target-id and conflict contract.
- Added correction critique and follow-up status to `working_note_corrections.md` and this progress log.

### Risks / assumptions

- Existing generated-note editor behavior remains source of truth; Working note should not regain editor-specific dirty/focus/save branches.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content visibility; routes still owner-only.
- Ownership rules preserved: save/read/clear still use transcript owner lookup; timestamped saves now require the caller's version token.
- Deletion semantics preserved: clear remains `DELETE /working-note`; transcript cascade unchanged; dirty-empty generation now instructs explicit clear.
- Provider rules preserved: no provider resolution or credential change.
- Structured-note contract preserved: EMIS validation remains server-side; checkbox state remains UI selection state, not persistence state.

## 2026-05-17 Working Notes

### Scope

- Added owner-only working-note storage and API for transcript-scoped clinician-authored context.
- Added generation snapshots and prompt integration so note generation can use working notes without overwriting them.
- Added redaction fail-closed behavior for working-note content before LLM requests.
- Reworked workspace UI so Working note uses the existing note-builder editor as a virtual note version.
- Fixed a boot-time transcribe JS error from a stale `syncWorkingNoteModeUi` call, which had disabled tab switching, new-session interception, and Working note selection.
- Changed new-session form fallback from stale `/transcribe/sessions/start` to existing `/transcribe/sessions`.
- Fixed Working note refresh behavior by bootstrapping saved working-note content into the initial page payload and skipping unload saves when the working-note editor is not dirty.
- Fixed the in-progress Working note preserve path so workspace refresh uses the virtual `working_note` editor id, matching generated-note dirty-editor preservation.
- Fixed Working note autosave version tracking so an in-flight save cannot mark newer unsaved typing as saved, matching generated-note save-version behavior.
- Fixed Working note focus loss by preserving the active Working note editor on every workspace refresh while it remains selected.
- Updated Working note selection rules so generated notes become the default selection once any generated note exists, while focused/dirty/in-flight Working note edits remain protected.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: broader browser QA recommended for exact switch/autosave ergonomics across mobile and generated-output states.

### Files changed

- `app/models.py`, `alembic/versions/r7s8t9u0v1w2_add_working_notes.py`: add working-note mode/content/snapshot fields.
- `app/schemas/transcripts.py`, `app/services/transcripts.py`, `app/routes/api_routes.py`: add owner-only working-note read/save/clear contract.
- `app/services/templates.py`: snapshots working notes, redacts them, and labels them in generation prompts.
- `app/templates/transcribe/_workspace.html`, `app/templates/transcribe/_head_assets.html`, `app/templates/transcribe/_shell_extras.html`, `app/templates/glm-3.html`, `app/web/transcribe_workspace.py`, `app/static/js/transcribe/app.js`, `app/static/js/transcribe/actions.js`, `app/static/js/transcribe/documents.js`, `app/static/js/transcribe/structured.js`: render Working note in the existing note-builder editor, route saves by active source, clear through `/working-note`, bootstrap saved working notes on refresh, preserve focused/dirty/in-flight Working note edits across workspace refreshes, keep save-version guards for in-flight Working note saves, auto-select newest generated note after generation/default refresh, and keep new-session fallback valid if JS fails.
- `app/api_route_audit.py`, `tests/test_api.py`: cover route auth, mode lock, clear, generation snapshots, and fail-closed redaction.
- `CONTEXT.md`, `docs/working_note_implementation.md`, `docs/api.md`, `docs/transcript-capture.md`, `docs/progress.md`: document domain and API behavior.

### Tests

- `.venv/bin/python -m compileall app`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "working_note or team_and_personal_template_routes_enforce_scope_and_allow_generation"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "structured_context or structured_template or generation_uses_structured or working_note"`: passed, 5 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_migrations.py`: passed, 19 tests.
- `node --check app/static/js/transcribe/app.js && node --check app/static/js/transcribe/actions.js && node --check app/static/js/transcribe/documents.js && node --check app/static/js/transcribe/structured.js`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "workspace_shell or exposes_home_and_context_settings_controls or working_note"`: passed, 2 tests.
- Browser MCP on local `/transcribe`: reproduced stale `syncWorkingNoteModeUi` console error, then verified no console errors, new-session creation, tab switching, Working note selection, and `/working-note` save after fix.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "working_note or workspace_shell or transcribe_page_exposes_home_and_context_settings_controls or transcribe_reorder_blocks_blank_note_lines"`: passed, 4 tests.
- `node --check app/static/js/transcribe/app.js && node --check app/static/js/transcribe/documents.js && .venv/bin/pytest -q tests/test_admin_ui.py -k "working_note or transcribe_page_exposes_home_and_context_settings_controls or transcribe_reorder_blocks_blank_note_lines"`: passed, 3 tests.
- `node --check app/static/js/transcribe/app.js && node --check app/static/js/transcribe/documents.js && .venv/bin/pytest -q tests/test_admin_ui.py -k "working_note or transcribe_page_exposes_home_and_context_settings_controls or transcribe_reorder_blocks_blank_note_lines"`: passed after save-version guard update, 3 tests.

### Documentation

- Added temporary working-note implementation plan.
- Updated API and transcript-capture docs.
- Tutorials deferred until implementation UX is final.

### Risks / assumptions

- Existing `structured_context_json` remains supported for compatibility and is treated as structured working-note content.
- UI source separation now depends on active editor source routing; detailed mobile/browser QA remains useful.

### Architecture checkpoint summary

- Privacy boundaries preserved: working-note content is owner-only transcript-derived content and not exposed to leaders/admins.
- Ownership rules preserved: all working-note routes use owner transcript lookup.
- Deletion semantics preserved: living notes live under transcript root; generated snapshots cascade with generated documents/transcript.
- Provider rules preserved: no provider selection/credential changes; working-note content is redacted before LLM calls.
- Structured-note contract preserved: EMIS allowed keys remain enforced; structured output schema still comes from selected template.

## 2026-05-17 Follow Up Autosave Switch Guard

### Scope

- Fixed follow-up selection and workspace refresh so pending editable follow-up title/body changes are saved or preserved before another follow-up is rendered.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser race check still useful if broader UI QA is run.

### Files changed

- `app/static/js/transcribe/app.js`: tracks dirty follow-up document identity and preserves dirty render during refresh.
- `app/static/js/transcribe/documents.js`: saves pending follow-up edits before switching selected follow-up.
- `tests/test_web_refactor.py`: adds static regression hooks for the follow-up pending-save guard.
- `docs/transcribe_brief.md`, `docs/progress.md`: document the follow-up autosave guard.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py -k followup_redesign_preserves_required_hooks`: passed, 1 test.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- Mirrors existing note editor pending-save behavior; failed/conflicted save blocks selection change so unsaved text remains visible.

### Architecture checkpoint summary

- Privacy boundaries preserved: same owner-only generated-document PATCH endpoint.
- Ownership rules preserved: no access model changes.
- Deletion semantics preserved: no deletion or retention behavior changed.
- Provider rules preserved: no provider resolution or credential path changed.
- Structured-note contract preserved: no structured note schema/output changes.

## 2026-05-17 Follow Ups Editable Output

### Scope

- Made ready follow-up and quick-action outputs editable in the Follow Ups tab, including generated document title and body text.
- Extended the owner-only generated-document PATCH path to save freeform follow-up/quick-action title and body edits with encrypted body storage.
- Kept the LLM request hidden by default, moved its `Show request` / `Hide request` toggle into step 3, and fixed the request card CSS so `[hidden]` wins over the flex display rule.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser visual check recommended for textarea sizing and right-panel flow.

### Files changed

- `app/schemas/templates.py`: adds optional generated-document title to update payloads.
- `app/services/templates.py`: allows owner edits for ready freeform follow-up/quick-action generated documents.
- `app/templates/transcribe/_workspace.html`: renders editable follow-up title/body controls and update timestamp hook.
- `app/templates/transcribe/_head_assets.html`: styles editable title/body and lets hidden LLM request stop consuming layout space.
- `app/static/js/transcribe/app.js`: adds follow-up autosave, conflict handling, and editable rendering.
- `app/static/js/transcribe/actions.js`: copies follow-up text from textarea values.
- `app/static/js/transcribe/documents.js`: keeps editable title/body/history labels in sync and always renders LLM request closed.
- `tests/test_api.py`: verifies owner-only encrypted follow-up title/body edits.
- `tests/test_admin_ui.py`, `tests/test_web_refactor.py`: cover UI hooks and hidden LLM request behavior.
- `docs/transcribe_brief.md`: documents editable follow-up behavior.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "generated_document_update_saves_followup_title_and_body_for_owner"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_api.py -k "generated_document_update_saves_note_content_and_detects_revision_conflicts or generated_document_update_rejects_duplicate_structured_section_keys"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_transcribe_page_renders_generated_document_switchers"`: passed, 1 test.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- Title is treated as generated-document metadata, but edit permission remains owner-only because it belongs to transcript-derived output in this UI.
- Autosave uses the same optimistic `updated_at` conflict check as note editing; quick switching shortly after typing relies on browser focusout/debounce.

### Architecture checkpoint summary

- Privacy boundaries preserved: only owning user can patch generated follow-up body/title; body remains encrypted at rest.
- Ownership rules preserved: update service keeps `owner_user_id == actor.id` enforcement.
- Deletion semantics preserved: no delete, retention, or cascade behavior changed.
- Provider rules preserved: no provider resolution, request generation, or credential handling changed.
- Structured-note contract preserved: structured EMIS update path remains unchanged and was regression-tested.

## 2026-05-17 Follow Ups Two Pane Builder

### Scope

- Changed the Follow Ups tab from three columns to two panes: selected generated document output/history on the left, and context plus visible quick-action selection on the right.
- Moved the quick-action list into right-side step 2 so available actions stay visible beside the context and Generate controls.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser visual check recommended for exact responsive spacing.

### Files changed

- `app/templates/transcribe/_workspace.html`: reorders Follow Ups markup into output-left and controls-right panes, with quick actions inside step 2.
- `app/templates/transcribe/_head_assets.html`: switches Follow Ups layout to two columns and bounds the right-side quick-action list.
- `tests/test_web_refactor.py`: updates static layout assertions for the two-pane workflow and moved quick-action list.
- `docs/transcribe_brief.md`: documents the two-pane follow-up flow.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_transcribe_page_renders_generated_document_switchers or user_transcribe_page_enables_followups_from_structured_note_content"`: passed, 2 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- UI-only layout change; generated-document data, provider calls, copy/delete paths, and prompt construction are unchanged.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content surface beyond existing owner-only workspace data.
- Ownership rules preserved: selected follow-up and quick actions still come from existing scoped workspace context.
- Deletion semantics preserved: no lifecycle or cascade behavior changed.
- Provider rules preserved: no provider resolution or credential behavior changed.
- Structured-note contract preserved: no EMIS structured-note JSON behavior changed.

## 2026-05-17 Follow Ups Output Card Redesign

### Scope

- Redesigned the generated follow-up panel so the selected follow-up name/details live in the top header and the generated text is larger and more prominent inside the output card.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser visual check recommended for exact typography/spacing.

### Files changed

- `app/templates/transcribe/_workspace.html`: adds generated follow-up title/subtitle header and removes status/date metadata from the output text card.
- `app/templates/transcribe/_head_assets.html`: styles the output header, icon, title, subtitle, and larger body typography.
- `app/static/js/transcribe/app.js`: updates output title/subtitle during client-side follow-up rendering.
- `app/static/js/transcribe/documents.js`: keeps output header title/subtitle in sync when selecting follow-up recents.
- `tests/test_web_refactor.py`: covers output header hooks and prominent text styling.
- `docs/transcribe_brief.md`: documents generated follow-up panel hierarchy.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "quick_action_context or transcribe_workspace"`: passed, 12 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- UI-only hierarchy/typography change; generated follow-up content and copy/delete paths are unchanged.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content exposure.
- Ownership rules preserved: selected follow-up still comes from existing owner-scoped workspace/generated-document state.
- Deletion semantics preserved: no lifecycle change.
- Provider rules preserved: no provider resolution or credential handling change.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-17 Follow Ups Column Quick Action Generate

### Scope

- Restored the normal Generate button inside the optional quick-action card.
- Added the small circular no-context Generate button to the selected quick-action card in the left Quick Actions column.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser visual check recommended for exact card spacing.

### Files changed

- `app/templates/transcribe/_workspace.html`: wraps quick-action cards so the selected card can show a sibling circular Generate button; restores normal Step 2 Generate button.
- `app/templates/transcribe/_head_assets.html`: styles selected quick-action card shell and column Generate button.
- `app/static/js/transcribe/app.js`: tracks quick-action column Generate buttons and disables them with the rest of quick-action controls.
- `app/static/js/transcribe/actions.js`: shows the column Generate button for the selected card and runs selected quick action with blank context.
- `tests/test_web_refactor.py`: covers column Generate button hooks and restored Step 2 button behavior.
- `docs/transcribe_brief.md`: documents column Generate button behavior.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "quick_action_context or transcribe_workspace"`: passed, 12 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- The left-column circular Generate intentionally ignores context; main/Step 2 Generate still uses current context when selected quick action is active.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content path; existing owner-scoped quick-action endpoint reused.
- Ownership rules preserved: no user/team scope change.
- Deletion semantics preserved: no lifecycle change.
- Provider rules preserved: no provider resolution or credential handling change.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-17 Follow Ups Quick Action Generate And LLM Placement

### Scope

- Added a small highlighted circular Generate button inside the selected quick-action card to run that quick action without context.
- Removed the Prompt Preview step and replaced it with the LLM request card in the middle builder, leaving the right generated-follow-up panel focused on output and recents.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: visual browser check recommended for exact button spacing.

### Files changed

- `app/templates/transcribe/_workspace.html`: replaces Prompt Preview with LLM request slot and changes selected quick-action Generate to circular icon button.
- `app/templates/transcribe/_head_assets.html`: styles the circular quick-action Generate button.
- `app/static/js/transcribe/actions.js`: lets the selected quick-action button run the existing quick-action endpoint with blank context.
- `tests/test_web_refactor.py`: verifies prompt preview removal, middle LLM request placement, and blank-context quick-action trigger.
- `docs/transcribe_brief.md`: documents quick-action card generate and LLM request placement.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "quick_action_context or transcribe_workspace"`: passed, 12 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- The circular quick-action button intentionally ignores current context text; main Generate still uses context/free-text behavior.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content source; quick-action request still uses existing owner-scoped endpoint.
- Ownership rules preserved: no user/team scope change.
- Deletion semantics preserved: no lifecycle change.
- Provider rules preserved: no provider resolution or credential handling change.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-17 Follow Ups Selected Quick Action Visibility

### Scope

- Fixed selected quick-action state so the empty “No quick action selected” panel is hidden when a quick-action card is selected.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none.

### Files changed

- `app/templates/transcribe/_head_assets.html`: adds explicit hidden-state CSS for the empty selected quick-action panel.
- `tests/test_web_refactor.py`: verifies hidden-state CSS remains present.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 7 tests.

### Documentation

- Updated this progress record.

### Risks / assumptions

- CSS-only visibility fix; no endpoint, persistence, or prompt behavior changed.

### Architecture checkpoint summary

- Privacy boundaries preserved: no content access change.
- Ownership rules preserved: no owner/team scope change.
- Deletion semantics preserved: no lifecycle change.
- Provider rules preserved: no provider resolution change.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-17 Pentest Source Validation

### Scope

- Validated pentest auth/token/session/setup findings against source and focused tests.
- Fixed stale API route-audit coverage for newly added STT/LLM provider setup routes, STT health recheck, and quick-action context audio preview.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no runtime vulnerability confirmed in validated source paths; external authenticated stored-XSS testing still requires real authenticated test account/data flow.

### Files changed

- `app/api_route_audit.py`: adds missing API routes to authorization audit manifest with valid probe payloads.
- `pentest-findings-openscribe.md`: records source-validation results and focused test evidence.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_auth_email.py tests/test_auth_service.py`: passed, 30 tests.
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py`: passed, 14 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py`: passed, 2 tests.

### Documentation

- Updated pentest findings with source-validation outcomes and test evidence.
- Updated this progress record.

### Risks / assumptions

- Route-audit update verifies negative access control only; it does not exercise successful provider setup behavior.
- Stored XSS remains best validated with authenticated browser-level payload testing, although source review shows Jinja escaping and explicit transcript escaping on known render paths.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note/prompt/audio content access rules changed; quick-action context preview remains full-auth owner path.
- Ownership rules preserved: no owner/team scoping behavior changed; route audit now covers missing full/system-admin access tiers.
- Deletion semantics preserved: no persistence, cascade, retention, or hard-delete path changed.
- Provider rules preserved: STT/LLM credential setup routes remain system-admin-only; raw secrets only appear as synthetic test payload values in the audit manifest.
- Structured-note contract preserved: no generated-document JSON, EMIS section keys, or structured-output validation changed.

## 2026-05-14 Follow Ups LLM Request Wrapping

- Changed Follow Ups LLM request payload to wrap text and allow vertical scrolling only.
- Kept request source/rendering unchanged; CSS now uses `pre-wrap`, hides horizontal overflow, and breaks long tokens.
- Added focused static regression coverage.
- Architecture checkpoint: UI-only display fix; no transcript visibility, ownership, deletion, encryption-key, provider-resolution, or structured-note JSON contract changes.

## 2026-05-13 Follow Ups LLM Request Card

### Scope

- Replaced the Follow Ups LLM request `<details>` panel with the same scroll-card pattern used by the generated follow-up output.
- The LLM request toolbar button now toggles card visibility, and the card body owns the scroll area.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser visual check recommended to tune exact card height.

### Files changed

- `app/static/js/transcribe/documents.js`: renders LLM request as `followup-output-card-v2 followup-llm-request-card-v2` instead of `<details>`.
- `app/static/js/transcribe/actions.js`: toggles the LLM request card with `hidden`.
- `app/templates/transcribe/_head_assets.html`: bounds the LLM request card and makes its payload scroll.
- `tests/test_web_refactor.py`: verifies card renderer, toggle, and sizing hooks.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 5 tests.

### Documentation

- Updated this progress record.

### Risks / assumptions

- UI-only renderer change; payload source and content are unchanged.

### Architecture checkpoint summary

- Privacy boundaries preserved: same owner-scoped LLM request payload, no new exposure/logging.
- Ownership rules preserved: generated-document payload still comes through existing workspace/document paths.
- Deletion semantics preserved: no generated-document lifecycle changes.
- Provider rules preserved: no provider resolution or credential handling change.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-13 Follow Ups Builder Polish

### Scope

- Populates the step 2 optional quick-action box and prompt preview whenever a quick-action card is selected.
- Replaced the rough Generate/Clear buttons with dedicated Follow Ups action button styling.
- Constrained the LLM request panel so its payload scrolls inside the Follow Ups right panel instead of overflowing.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: visual polish still should be browser-reviewed against the screenshot.

### Files changed

- `app/templates/transcribe/_workspace.html`: adds selected quick-action eyebrow and new action button classes.
- `app/templates/transcribe/_head_assets.html`: styles selected quick-action state, action buttons, and bounded LLM request scroll area.
- `app/static/js/transcribe/app.js`: passes the selected quick-action panel to action wiring.
- `app/static/js/transcribe/actions.js`: toggles selected quick-action state and fills step 2/preview copy from the selected card.
- `app/static/js/transcribe/documents.js`: gives the rendered LLM request panel/payload stable classes for bounded scrolling.
- `tests/test_web_refactor.py`: covers selected action population hooks, button classes, and LLM request scroll hooks.
- `docs/transcribe_brief.md`: documents selected quick-action population behavior.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 5 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace or quick_action_context"`: passed, 12 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- UI-only change; selected quick-action context still reuses existing quick-action endpoint and free-text follow-up still reuses existing follow-up endpoint.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content reads, logs, or sharing paths added.
- Ownership rules preserved: existing owner-scoped workspace/generated-document APIs remain the only data source and mutation path.
- Deletion semantics preserved: selected follow-up delete behavior unchanged.
- Provider rules preserved: no LLM/STT/de-identification provider resolution change.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-13 Follow Ups LLM Request Bounds

### Scope

- Fixed the Follow Ups LLM request panel sizing so opening it gives the payload a real bounded scroll area instead of letting the panel extend below the right-side card.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser visual check still recommended for exact height comfort.

### Files changed

- `app/templates/transcribe/_head_assets.html`: makes the LLM request slot flex-bounded and renders the open details panel as fixed-summary plus scrollable payload grid.
- `tests/test_web_refactor.py`: covers bounded flex/grid sizing hooks for the LLM request panel.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/documents.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 5 tests.

### Documentation

- Updated this progress record.

### Risks / assumptions

- CSS-only containment change; no request payload content, storage, or API behavior changed.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new payload exposure; existing LLM request dev/user display remains same content in a bounded container.
- Ownership rules preserved: generated-document payload still comes from existing owner-scoped workspace response.
- Deletion semantics preserved: no lifecycle/cascade/retention path changed.
- Provider rules preserved: no provider resolution or credential handling changed.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-13 Follow Ups Screenshot Match

### Scope

- Updated the Follow Ups tab to match the supplied screenshot: quick-action cards on the left, a combined follow-up builder with numbered context/optional quick-action/preview steps in the middle, and selected follow-up output plus recents/LLM request on the right.
- The middle Generate button now submits a custom follow-up when no quick action is selected, or runs the selected quick action with the same context when one is selected.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: visual similarity is CSS/template based; no browser screenshot diff was run.

### Files changed

- `app/templates/transcribe/_workspace.html`: reshapes Follow Ups middle panel into the screenshot-style combined builder and keeps existing generation hooks.
- `app/templates/transcribe/_head_assets.html`: adds screenshot-style builder, preview, selected quick-action, and sidebar tip styles.
- `app/static/js/transcribe/app.js`: adds DOM refs and enables the combined Generate button when free-text follow-up generation is available.
- `app/static/js/transcribe/actions.js`: syncs selected quick action into the optional action/preview panels and routes Generate to the correct existing endpoint.
- `tests/test_web_refactor.py`: covers screenshot-specific builder hooks, right-panel recents, and combined textarea hook preservation.
- `docs/transcribe_brief.md`: documents combined builder behavior.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 5 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace or quick_action_context"`: passed, 12 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- Combined builder deliberately reuses existing generated-document endpoints; no new prompt schema or quick-action usage metadata was added.

### Architecture checkpoint summary

- Privacy boundaries preserved: transcript-derived context still sent only through existing owner-scoped follow-up/quick-action endpoints.
- Ownership rules preserved: workspace load, generated document selection, and delete still use existing owner checks.
- Deletion semantics preserved: no lifecycle/cascade/retention behavior changed.
- Provider rules preserved: existing LLM/STT resolution and credential boundaries unchanged.
- Structured-note contract preserved: no EMIS structured-note JSON, keys, validation, or editor behavior changed.

## 2026-05-13 Clinical Note Empty State Spacing

### Scope

- Reduced vertical padding, icon size, and preserved-template whitespace for the empty Clinical Note guidance so it takes less screen height before note lines exist.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none.

### Files changed

- `app/templates/transcribe/_workspace.html`: marks the empty Clinical Note output and its top empty state with compact modifiers.
- `app/templates/transcribe/_head_assets.html`: applies compact spacing only to that Clinical Note empty output/state.
- `tests/test_web_refactor.py`: verifies the compact empty-state hook and styles remain present.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 5 tests.

### Documentation

- Updated this progress record.

### Risks / assumptions

- Visual-only change; no product behavior or content flow changed.

### Architecture checkpoint summary

- Privacy boundaries preserved: CSS/template-only layout change; no transcript/note content exposure changes.
- Ownership rules preserved: owner-scoped workspace rendering unchanged.
- Deletion semantics preserved: no lifecycle or cascade paths changed.
- Provider rules preserved: no STT/LLM/de-identification provider logic changed.
- Structured-note contract preserved: no structured output keys, validation, or editor data model changed.

## 2026-05-13 Follow Ups Redesign

### Scope

- Redesigned Follow Ups tab into quick-action list, context/request, and selected-output panels with current-transcript recents.
- Follow-up recents now sit under the selected output; visual-only rating controls were removed.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no usage-count schema exists in this slice, so no Popular/Most used badges were added.

### Files changed

- `app/templates/transcribe/_workspace.html`: replaces Follow Ups body with v2 three-panel markup while preserving generation hooks.
- `app/templates/transcribe/_head_assets.html`: adds v2 panel/card/request/output responsive styles.
- `app/templates/transcribe/_shell_extras.html`: bumps frontend module cache key.
- `app/static/js/transcribe/app.js`: wires new DOM refs and selected output rendering.
- `app/static/js/transcribe/actions.js`: adds quick-action search, card selection sync, counters, clear/enter behavior, selected-output copy/delete, and recorder targeting for context/custom prompt.
- `app/static/js/transcribe/documents.js`: renders follow-up recents instead of stacked output cards.
- `app/web/transcribe_workspace.py`: orders quick actions/templates by default preference, favourites, then name without schema changes.
- `tests/test_web_refactor.py`: covers ordering and required Follow Ups hooks.
- `docs/transcribe_brief.md`: documents new Follow Ups flow and constraints.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `node --check app/static/js/transcribe/actions.js`: passed.
- `.venv/bin/python -m py_compile app/web/transcribe_workspace.py`: passed.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "transcribe_workspace or quick_action_context"`: passed, 12 tests.

### Documentation

- Updated transcribe brief and this progress record.

### Risks / assumptions

- No backend usage metadata was found, so most-used sorting and badges are intentionally omitted.
- Recorder still uses the existing quick-action context transcription preview endpoint, now targeting either context or custom prompt textareas.

### Architecture checkpoint summary

- Privacy boundaries preserved: frontend-only layout/interaction changes; no transcript/note/prompt content becomes shareable or visible to non-owners.
- Ownership rules preserved: existing owner-scoped workspace and generated-document endpoints remain the only data source/mutation path.
- Deletion semantics preserved: selected follow-up delete still calls existing generated-document hard-delete endpoint; no undo or cascade behavior changed.
- Provider rules preserved: LLM/STT provider resolution unchanged; no provider credentials or raw secrets exposed.
- Structured-note contract preserved: EMIS structured-note generation/editor paths unchanged.

## 2026-05-13 VAD Inactivity Behavior Tests

### Scope

- Added executable browser-logic regression coverage for VAD inactivity prompt dismiss, re-arm, stop, and page-lifecycle reset behavior.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no production code changed.

### Files changed

- `tests/test_vad_inactivity_js.py`: runs `media.js` in a Node VM with fake DOM/timers/VAD to verify inactivity lifecycle behavior.
- `docs/live_stt.md`: documents dismiss/re-arm/reset prompt semantics.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_vad_inactivity_js.py`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k transcribe_frontend_uses_global_template_selector_for_generation_controls`: passed, 1 test selected.
- `node --check app/static/js/transcribe/media.js`: passed.

### Documentation

- Updated live STT notes for inactivity prompt dismiss scope and reset behavior.

### Risks / assumptions

- Test harness simulates browser APIs and VAD callbacks; it does not replace full browser automation.

### Architecture checkpoint summary

- Privacy boundaries preserved: tests and docs cover local UI state only; no transcript, note, prompt, audio, or provider secret content exposed.
- Ownership rules preserved: no API ownership checks or user/team scoping changed.
- Deletion semantics preserved: no persistence, cascade, retention, or hard-delete path changed.
- Provider rules preserved: STT/LLM/de-identification provider resolution unchanged.
- Structured-note contract preserved: no generated-document JSON or EMIS section behavior changed.

## 2026-05-12 LLM Redaction Boundary

### Scope

- Fixed generation request construction so static template/quick-action asset text is not PHI-redacted, dynamic prompt inputs are redacted before provider send, and displayed `LLM request` is exactly the provider request body.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none known.

### Files changed

- `app/services/templates.py`: separates static prompt assets from dynamic prompt inputs, redacts structured context recursively, and stores/sends one provider request body.
- `tests/test_api.py`: updates request-payload expectations and adds redaction-boundary regressions.
- `docs/api.md`: documents static-vs-dynamic generation redaction behavior.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "redaction_boundary or redacts_transcript_and_reidentifies_output or redacts_dictation_before_provider_call or redacts_dictation_only_session_before_provider_call or followup_generation_queues_and_processes_with_owner_scope or quick_action_context or template_generation_queues"`: passed, 6 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "llm_request or generated_document or template_generation or followup_generation or quick_action"`: passed, 31 tests.
- `.venv/bin/pytest -q tests/test_pii_response_minimisation.py`: passed, 9 tests.
- `.venv/bin/python -m py_compile app/services/templates.py`: passed.

### Documentation

- Updated API generation behavior notes for static assets, dynamic inputs, and LLM request payload shape.

### Risks / assumptions

- Static asset prompts remain config, not transcript-derived content. If users intentionally put patient text into reusable templates/actions, it is treated as configuration by this boundary.

### Architecture checkpoint summary

- Privacy boundaries preserved: transcript, dictation, follow-up prompt, quick-action context, and structured context content are redacted before LLM send; no raw content logging added.
- Ownership rules preserved: generation still resolves owner-scoped transcript/generated-document records only.
- Deletion semantics preserved: no schema, cascade, retention, or hard-delete path changed.
- Provider rules preserved: LLM selection, Vault-backed secret lookup, and adapter dispatch unchanged; only request body construction changed.
- Structured-note contract preserved: EMIS section keys/order remain validated; structured context shape is preserved while string values are redacted.

## 2026-05-12 Status Pill Review Fixes

### Scope

- Fixed status pill review regressions: skipped STT health probes are neutral, and inactive-capture upload/stopping labels remain visible.

### Checklist

- Code complete: yes
- Tests added/updated: focused syntax/API checks; frontend-only aggregation behavior changed.
- Docs added/updated: yes
- Open issues: no browser automation in this slice.

### Files changed

- `app/static/js/transcribe/app.js`: treats skipped `unknown` STT health as neutral, keeps real warnings/unavailable states visible, and maps local `uploading`/`stopping` states before idle fallback.
- `docs/progress.md`: records review fix checklist and architecture checkpoints.
- `docs/progress/Daily Note 12-5-26 Status Pill Review Fixes.md`: local daily note added; directory is gitignored.

### Tests

- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "stt_health or transcribe_workspace"`: passed, 14 tests.

### Architecture checkpoint summary

- Privacy boundaries preserved: status pill shows operational state only, no transcript/note/prompt/audio content.
- Ownership rules preserved: frontend display logic only; workspace owner/provider resolution unchanged.
- Deletion semantics preserved: no persistence, cascade, retention, or hard-delete behavior changed.
- Provider rules preserved: intentionally skipped provider health is neutral; unavailable/warning health still surfaces.
- Structured-note contract preserved: no generated-document JSON or EMIS section behavior changed.

## 2026-05-12 Transcription Status Pill Health

### Scope

- Added the implementation plan and first slice for the transcription status pill: server-side STT health warnings, manual recheck, local live-capture status precedence, mic issue reporting, and multi-issue pill details.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no persistent health history or configurable health URL in this slice.

### Files changed

- `STATUS_PILL_PLAN.md`: captures the agreed hierarchy, behavior, scope, and test checklist.
- `app/services/stt.py`: adds selected STT health checks with 60-second in-memory cache and detail filtering.
- `app/web/transcribe_workspace.py`, `app/schemas/workspace.py`, `app/routes/api_routes.py`: expose workspace health and manual recheck.
- `app/static/js/transcribe/app.js`, `app/static/js/transcribe/media.js`, `app/static/js/transcribe/bootstrap.js`: aggregate pill states, show details, preserve live phases, and map mic errors.
- `app/templates/transcribe/_workspace.html`, `app/templates/transcribe/_head_assets.html`, `app/templates/transcribe/_shell_extras.html`: add pill detail styles, bootstrap health, and stop ready-state pulsing.
- `tests/test_api.py`: covers workspace STT health privacy/detail behavior and recheck cache bypass.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "stt_health or transcribe_workspace"`: passed, 14 tests.
- `.venv/bin/python -m py_compile app/services/stt.py app/web/transcribe_workspace.py app/routes/api_routes.py app/schemas/workspace.py`: passed.
- `node --check app/static/js/transcribe/app.js && node --check app/static/js/transcribe/media.js && node --check app/static/js/transcribe/bootstrap.js`: passed.

### Documentation

- Added root implementation guidance in `STATUS_PILL_PLAN.md` and this progress entry.

### Risks / assumptions

- STT health checks infer `${base_url}/health` only for generic/openai-compatible REST providers; provider-specific health remains future work.
- Health cache is process-local by design for MVP.
- Frontend JS behavior is syntax-checked; no browser automation was run in this slice.

### Architecture checkpoint summary

- Privacy boundaries preserved: health and pill details expose operational status only, not transcript/note/prompt/audio content.
- Ownership rules preserved: workspace/recheck resolve only the current user's team-selected STT provider; transcript content remains owner scoped.
- Deletion semantics preserved: no persisted health state or schema lifecycle added.
- Provider rules preserved: raw credentials stay Vault-backed and are used only server-side; leaders get safe diagnostics, normal users get plain messages.
- Structured-note contract preserved: no generated-document JSON or EMIS section behavior changed.

## 2026-05-12 Alembic Logger Preservation

### Scope

- Fixed Alembic migration logging setup so migration tests no longer disable existing `openscribe.*` application loggers before later API tests run.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none.

### Files changed

- `alembic/env.py`: keeps existing loggers enabled when loading Alembic logging config.
- `tests/test_migrations.py`: adds regression coverage for `openscribe.stt` logger state after migration upgrade.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_migrations.py tests/test_api.py -k "stt or migration or elevenlabs or deepgram"`: passed, 97 tests.

### Documentation

- Progress note added for logging test-order fix.

### Risks / assumptions

- Assumes existing application loggers must remain active across Alembic commands in test and runtime processes.

### Architecture checkpoint summary

- Privacy boundaries preserved: no content or credential logging added.
- Ownership rules preserved: no auth or team-scope behavior changed.
- Deletion semantics preserved: no lifecycle behavior changed.
- Provider rules preserved: STT provider runtime unchanged; diagnostic warning capture restored.
- Structured-note contract preserved: no generated document behavior changed.

## 2026-05-12 STT Secret Readiness Review Fix

### Scope

- Fixed STT credential readiness so provider presets that require API keys, including Deepgram, fail before selection/upload when no Vault secret is saved.
- Updated stale STT Vault test doubles to accept the new `secret_ref` keyword.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none.

### Files changed

- `app/services/stt.py`: centralizes saved-credential requirement around STT provider preset metadata.
- `tests/test_api.py`: updates Vault read fakes and adds Deepgram missing-secret regression coverage.
- `docs/progress.md`: records checklist and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "stt_selection_rejects_config_with_missing_saved_secret or deepgram_stt_selection_requires_saved_secret or audio_file_upload_fails_immediately_when_selected_stt_secret_is_missing or transcribe_with_team_stt_openai_compatible_rest_uses_vault_secret_and_response_path"`: passed, 4 tests.

### Documentation

- Progress note added for review fix.

### Risks / assumptions

- Custom REST/OpenAPI providers remain allowed without credentials because their preset marks `requires_api_key=False`.

### Architecture checkpoint summary

- Privacy boundaries preserved: no credential values logged or exposed.
- Ownership rules preserved: STT selection remains scoped to team leader/system-admin checks.
- Deletion semantics preserved: no lifecycle or cascade behavior changed.
- Provider rules strengthened: credential-required STT presets must have a saved Vault secret before use.
- Structured-note contract preserved: no generated document behavior changed.

## 2026-05-12 STT Rotated Secret Deletion

### Scope

- Fixed STT config deletion and team deletion so post-commit Vault cleanup deletes the stored `vault_secret_ref`, including rotated credential refs.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none.

### Files changed

- `app/services/stt.py`: captures the STT config's current Vault ref before deleting the row and passes it to cleanup after commit.
- `app/services/admin.py`: records each team STT config Vault ref during team hard-delete and passes it to post-commit cleanup.
- `tests/test_api.py`, `tests/test_admin_ui.py`: verify config and team deletion pass the saved STT secret ref.
- `docs/stt-config.md`, `docs/progress.md`: document current-ref cleanup.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "delete_provisioned_stt_config"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "delete_team_and_owned_records or team_delete_checks_system_admin_members_before_vault_cleanup or team_delete_defers_vault_cleanup_until_after_db_commit"`: passed, 3 tests.
- `python3 -m py_compile app/services/stt.py app/services/admin.py`: passed.

### Documentation

- Updated STT config API notes to say deletion removes the current Vault-backed secret reference after commit.

### Risks / assumptions

- Existing retry/compensation model is unchanged: Vault cleanup failure is logged after DB commit.

### Architecture checkpoint summary

- Privacy boundaries preserved: no credential values logged or exposed.
- Ownership rules preserved: system-admin delete scope unchanged.
- Deletion semantics improved: rotated provider credential refs are enumerated before DB deletion and cleaned after commit.
- Provider rules preserved: STT provisioning, selection, and credential resolution unchanged.
- Structured-note contract preserved: no generated document behavior changed.

## 2026-05-12 Long Upload STT Timeout

### Scope

- Fixed long whole-file uploads being marked failed before STT completed by replacing fixed 60 second STT HTTP timeouts with `STT_TRANSCRIPTION_TIMEOUT_SECONDS` defaulting to 4 hours.
- Raised ffmpeg normalization timeout from 60 seconds to `AUDIO_FFMPEG_TIMEOUT_SECONDS` defaulting to 30 minutes, matching longer accepted files.
- Kept timeout values configurable for deployments with shorter proxy/provider limits.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: providers or reverse proxies with their own shorter hard timeouts can still fail long synchronous transcription; deployment config must align with these app defaults.

### Files changed

- `app/services/stt.py`: adds env-backed STT transcription timeout and applies it to generic REST, ElevenLabs, Deepgram, and OpenAI SDK paths.
- `app/services/audio.py`: raises default ffmpeg normalization timeout.
- `tests/test_api.py`: asserts long-upload timeout defaults and verifies STT transports pass the long timeout.
- `docs/api.md`, `docs/setup.md`, `docs/transcript-capture.md`: document timeout knobs.
- `docs/progress.md`: records diagnosis, tests, and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "whole_file_upload_default_caps_match_four_hour_policy or elevenlabs_transcription_uses_xi_api_key_not_bearer or transcribe_with_team_stt_openai_compatible_rest_uses_vault_secret_and_response_path or deepgram_transcription_uses_query_params_and_raw_audio or audio_normalization_timeout_surfaces_app_error or processing_audio_file_job_fails_when_normalized_duration_exceeds_limit"`: passed, 6 tests.

### Documentation

- Documented `AUDIO_FFMPEG_TIMEOUT_SECONDS` and `STT_TRANSCRIPTION_TIMEOUT_SECONDS` defaults in API, setup, and transcript capture docs.

### Risks / assumptions

- Root cause was app-side synchronous request timeout, not provider rejection: accepted 45 minute audio can need more than 60 seconds for normalization/provider transcription.
- This keeps the current synchronous STT contract. Very long provider jobs would be better served by provider-native async/batch APIs if available.

### Architecture checkpoint summary

- Privacy boundaries preserved: no audio/transcript logging added; uploaded audio remains owner-only transcript-derived content.
- Ownership rules preserved: upload and processing still use existing owner/team scoped transcript and STT selection checks.
- Deletion semantics preserved: retry source storage/cleanup and transcript-root cascade behavior unchanged.
- Provider rules preserved: credential resolution and provider selection unchanged; only request timeout changes.
- Structured-note contract preserved: no generated document or structured JSON behavior changed.

## 2026-05-12 Four Hour Whole-File Upload Caps

### Scope

- Raised default whole-file upload duration cap from 30 minutes to 4 hours.
- Raised default raw whole-file upload size cap to 200 MB, matching the previous 25 MB per 30 minutes ratio.
- Raised default rolling whole-file owner budget to 200 MB / 4 hours so one max-size upload is not rejected by hourly budget defaults.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/audio.py`: updates default per-file upload size and duration caps.
- `app/services/transcripts.py`: updates default rolling whole-file owner budget.
- `tests/test_api.py`: adds regression coverage for four-hour default policy and keeps focused cap/budget coverage.
- `docs/api.md`, `docs/setup.md`, `docs/transcript-capture.md`: document new defaults.
- `docs/progress.md`: records implementation and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "whole_file_upload_default_caps_match_four_hour_policy or audio_file_upload_rejects_oversized_payload or audio_file_upload_enforces_hourly_upload_size_budget or audio_file_upload_enforces_hourly_duration_budget or processing_audio_file_job_fails_when_normalized_duration_exceeds_limit"`: passed, 5 tests.

### Documentation

- Updated API, setup, and transcript capture docs with 200 MB / 4 hour whole-file defaults and rolling budget defaults.

### Risks / assumptions

- 200 MB is calculated as commensurate with the previous 25 MB / 30 minute ratio.
- Longer queued PHI audio increases Vault/storage and STT processing burden, but does not change visibility or deletion rules.

### Architecture checkpoint summary

- Privacy boundaries preserved: uploaded audio remains owner-only transcript-derived content; no new content logging or admin visibility.
- Ownership rules preserved: upload routes still require authenticated owner transcript access and active team STT selection.
- Deletion semantics preserved: source audio lifecycle, retry source cleanup, and transcript-root cascade behavior unchanged.
- Provider rules preserved: STT provider resolution and credential snapshot behavior unchanged.
- Structured-note contract preserved: no structured-output behavior changed.

## 2026-05-12 Tutorial Document Areas

### Scope

- Added static tutorial markdown areas for user, team leader, admin, onboarding, and system-admin setup guidance.
- Ordered tutorials so team leaders inherit user workflow guidance without duplicate content.
- Expanded user and team leader tutorials into beginner-first, step-by-step guidance that explains core terms and first-use workflow.

### Checklist

- Code complete: yes
- Tests added/updated: not applicable; docs-only change
- Docs added/updated: yes
- Open issues: app routes/navigation for rendering tutorials in-product are not implemented in this slice

### Files changed

- `docs/tutorials/README.md`: tutorial index and content safety note.
- `docs/tutorials/user.md`: beginner clinician workflow, definitions, review/copy steps, privacy, deletion, and troubleshooting guidance.
- `docs/tutorials/team-leader.md`: beginner leader role explanation, team readiness checks, provider selection, user setup, template, quick-action, and escalation guidance.
- `docs/tutorials/admin.md`: system-admin daily admin workspace guidance and boundaries.
- `docs/tutorials/onboarding.md`: account setup, MFA, and recovery-code guidance.
- `docs/tutorials/system-admin-setup.md`: first bootstrap, provider provisioning, and pre-clinical setup guidance.
- `docs/progress.md`: records this docs slice.

### Tests

- Not run; markdown-only documentation update.

### Documentation

- Added `docs/tutorials/` tutorial set.

### Risks / assumptions

- Tutorials are currently repository documentation only. A later UI slice should expose them from Home, Transcribe, Admin, and onboarding surfaces.

### Architecture checkpoint summary

- Privacy boundaries preserved: tutorials contain no transcript-derived examples or patient content.
- Ownership rules preserved: docs state leaders/admins do not gain transcript or note visibility by role.
- Deletion semantics preserved: docs describe irreversible transcript-root and system-level user deletion without changing lifecycle behavior.
- Provider rules preserved: docs keep system-admin provisioning, leader selection, user preference, Vault-backed secret handling, and fallback boundaries.
- Structured-note contract preserved: user/admin docs list allowed EMIS section keys and do not expand the structured JSON contract.

## 2026-05-12 LLM Request Panel State

### Scope

- Implemented remaining `LLM_request_payload.md` UI fix: selected generated document `LLM request` details now stays open across same-document transcript workspace re-renders.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/static/js/transcribe/documents.js`: preserves same-document LLM request panel open state while rebuilding panel content.
- `tests/test_admin_ui.py`: adds static regression checks for panel state preservation wiring.
- `docs/progress.md`: records implementation and architecture checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "global_template_selector"`: verifies transcribe frontend bundle includes state-preserving LLM request panel logic.

### Documentation

- `docs/progress.md`: updated with change scope, tests, and checkpoint summary.

### Risks / assumptions

- Same-document re-render preserves open state. Switching documents resets closed, matching accepted simple fix in `LLM_request_payload.md`.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content source or API exposure; existing owner-scoped payload remains unchanged.
- Ownership rules preserved: UI uses already-authorized generated document data only.
- Deletion semantics preserved: no persistence/lifecycle changes.
- Provider rules preserved: no provider request or secret handling changes.
- Structured-note contract preserved: no structured output parsing or section behavior changes.

## 2026-05-12 LLM Request Payload Inspection

### Scope

- Implemented `LLM_request_payload.md`: newly generated notes, follow-ups, and quick actions now persist encrypted outbound LLM request snapshots and show them in collapsed `LLM request` panels on the transcript workspace.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: frontend rendering covered by implementation/manual inspection only; no JS test harness found.

### Files changed

- `app/models.py`, `alembic/versions/f5a6b7c9d1e2_add_llm_request_payload_to_generated_documents.py`: add nullable encrypted generated-document payload column.
- `app/services/templates.py`: builds one provider request body, stores grouped encrypted snapshot (`provider`, `generation`, `request`, `input`), and sends that same body to OpenAI-compatible/Bedrock/Ollama adapters.
- `app/schemas/templates.py`, `app/web/presentation.py`: expose decrypted `llm_request_payload_json` through generated-document detail responses.
- `app/templates/transcribe/_workspace.html`, `app/static/js/transcribe/app.js`, `app/static/js/transcribe/documents.js`: render selected note/follow-up `LLM request` details and update on document switch.
- `tests/test_api.py`: verifies encrypted storage, API serialization, exact provider request reuse, and template/follow-up/quick-action payload inputs.
- `tests/test_migrations.py`: verifies `generated_documents.llm_request_payload_json_encrypted` exists at Alembic head.
- `docs/llm-providers.md`, `docs/progress.md`: document behavior and progress.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "generated_document or llm_request or template or followup or quick_action"`: passed, 35 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "onboarding_and_session_tables"`: passed, 1 test.

### Documentation

- `docs/llm-providers.md`: documents encrypted request snapshots, API exposure boundary, and old-document null behavior.

### Risks / assumptions

- Snapshot intentionally contains redacted consultation source text and prompt content, so it remains encrypted with owner content DEK and available only via owner-generated-document access.

### Architecture checkpoint summary

- Privacy boundaries preserved: payload content is encrypted and returned only through existing owner-scoped generated-document API/UI.
- Ownership rules preserved: no team leader/system-admin content read path added.
- Deletion semantics preserved: payload lives on generated-document row and cascades with transcript-root/generated-document deletion.
- Provider rules preserved: provider/model metadata is stored, but Vault-backed bearer tokens and secret refs are never included.
- Structured-note contract preserved: structured EMIS JSON output validation unchanged; structured context is captured as request input only.

## 2026-05-11 STT Provider Error Diagnostics

### Scope

- Saved STT diagnostics now keep and render safe upstream provider error metadata, including provider HTTP status and provider error code.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/stt.py`: parses provider JSON error codes from HTTP failures and includes them in saved STT test results.
- `app/templates/admin.html`: renders provider error code/status in the admin STT test panel.
- `tests/test_api.py`, `tests/test_admin_ui.py`: cover provider error metadata extraction, propagation, and admin rendering.
- `docs/api.md`, `docs/stt-config.md`, `docs/progress.md`: document diagnostic metadata behavior.

### Tests

- `.venv/bin/python -m py_compile app/services/stt.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "safe_http_error_details_includes_provider_error_code_without_message or system_admin_stt_test_result_surfaces_provider_failure_without_secret_reveal"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "saved_stt_provider_error_details or saved_stt_test_and_render_result"`: passed, 2 tests.

### Documentation

- `docs/api.md`: documents safe `provider_error_code` on upstream STT failures.
- `docs/stt-config.md`: documents admin diagnostic provider metadata.

### Risks / assumptions

- Provider error messages are intentionally not propagated to logs/UI because they can include provider account/key labels or other nonessential detail.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note/provider secret content is exposed; only status codes and provider error codes are surfaced.
- Ownership rules preserved: diagnostic remains system-admin-only and team-scoped.
- Deletion semantics preserved: no lifecycle or cascade path changed.
- Provider rules preserved: raw credentials remain Vault-backed; provider failure metadata is safe operational metadata only.
- Structured-note contract preserved: no generated-document or EMIS behavior changed.

## 2026-05-11 ElevenLabs Dedicated STT Adapter

### Scope

- Implemented `STT_Wizard.md`: ElevenLabs now uses dedicated `elevenlabs_speech_to_text` adapter, built-in sync model list, hard-coded request/response contract, and migration backfill for existing ElevenLabs configs.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/models.py`, `alembic/versions/e4f5a6b7c9d1_add_elevenlabs_stt_adapter.py`: add adapter enum value and backfill ElevenLabs config rows/contract metadata.
- `app/services/stt_presets.py`, `app/services/stt.py`, `app/schemas/stt.py`: map ElevenLabs preset to dedicated adapter, hard-code supported models, validate model choices, and dispatch runtime through ElevenLabs contract.
- `app/templates/admin.html`: expose dedicated adapter in legacy admin form and keep known-contract fields hidden/defaulted.
- `tests/test_api.py`, `tests/test_migrations.py`: cover ElevenLabs adapter mapping/model rejection and enum migration value.
- `docs/stt-config.md`, `docs/progress.md`: document dedicated adapter behavior and progress.

### Tests

- `.venv/bin/python -m py_compile app/models.py app/schemas/stt.py app/services/stt.py app/services/stt_presets.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "elevenlabs"`: passed, 15 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "onboarding_and_session_tables"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_stt_deepgram_draft_pages_show_model_dropdown_without_key_field or admin2_exposes_admin_lifecycle_and_provider_controls"`: passed, 2 tests.

### Documentation

- `docs/stt-config.md`: documents `elevenlabs_speech_to_text`, built-in sync model contract, request/response shape, and validation points.

### Risks / assumptions

- `/v1/models` remains credential/catalog probe. Selectable ElevenLabs STT models intentionally stay fixed to `scribe_v2` and `scribe_v1` until realtime/non-sync adapters exist.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content access changed; STT request logging remains metadata-only.
- Ownership rules preserved: STT draft/finalize/save routes stay system-admin team-scoped; team selection gating unchanged.
- Deletion semantics preserved: no cascade/delete path changed; migration mutates provider metadata only.
- Provider rules preserved: raw ElevenLabs API key stays Vault-backed, never returned; runtime uses `xi-api-key` and dedicated provider contract.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-11 ElevenLabs STT Wizard Completion

### Scope

- Completed missing STT wizard behavior for ElevenLabs. Draft creation now validates `xi-api-key` via `/v1/models`, filters synchronous STT models, rejects invalid keys before DB/Vault persistence, normalizes provider-default language values, and runtime/admin tests use ElevenLabs `xi-api-key` multipart transcription instead of bearer auth.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/stt_normalization.py`, `app/schemas/stt.py`: add STT optional/language normalization and apply it to config/finalize/selection payloads.
- `app/services/stt_presets.py`: make ElevenLabs discoverable, remove static default model, and use `language_code`.
- `app/services/stt.py`: add ElevenLabs model/key validation helper, exact sync-STT filter, runtime `xi-api-key` transcription branch, and defensive language normalization.
- `app/routes/web_admin.py`, `app/routes/web_home_transcribe.py`: normalize STT language at form boundaries.
- `app/templates/admin.html`, `app/templates/admin2.html`, `app/templates/home.html`: render provider-default language as blank with clear placeholder.
- `tests/test_api.py`: add ElevenLabs discovery, invalid-key, draft, request-shape, default-language, generic-language, and admin saved-test parity coverage.
- `docs/stt-config.md`, `docs/progress.md`: document ElevenLabs wizard validation and progress.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "elevenlabs or default_language or generic_stt_transport_does_not_send_default_language or pending_stt_provider_cannot_be_selected_directly or system_admin_can_create_and_finalize_stt_provider_draft or saved_test_uses_elevenlabs"`: passed, 21 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_stt_deepgram_draft_pages_show_model_dropdown_without_key_field or admin2_exposes_admin_lifecycle_and_provider_controls"`: passed, 2 tests.

### Documentation

- `docs/stt-config.md`: documents ElevenLabs `/v1/models` validation, exact synchronous STT model filtering, runtime contract, language normalization, and secret handling.

### Risks / assumptions

- ElevenLabs model discovery is intentionally narrow: only `scribe_v2` and `scribe_v1` are selectable until realtime/WebSocket STT is supported.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript content path touched; raw API keys never returned/logged.
- Ownership rules preserved: system-admin-only team-scoped STT draft route unchanged.
- Deletion semantics preserved: invalid credentials fail before config row/Vault write; no deletion path changed.
- Provider rules preserved: ElevenLabs stays preset contract with Vault-backed key, live model discovery, `xi-api-key` auth, and provider-specific runtime transport.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-11 STT Wizard Model Dropdown

### Scope

- Implemented `STT_Wizard.md` UI fix: pending STT drafts now show saved discovered models as dropdown choices in both admin templates, and pending model-step forms do not expose credential fields.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/templates/admin.html`: use discovered `available_model_options` for STT model dropdowns and hide inspect/save credential form while a draft waits for model selection.
- `app/templates/admin2.html`: use saved `available_models_json` for STT draft/edit dropdowns and hide credential controls while a draft waits for model selection.
- `tests/test_admin_ui.py`: add Deepgram draft UI regression covering model dropdowns and credential-field hiding in both admin UIs.
- `docs/stt-config.md`, `docs/progress.md`: document wizard dropdown behavior and progress.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_stt_deepgram_draft_pages_show_model_dropdown_without_key_field or admin_page_can_inspect_team_stt_config_before_saving or admin2_exposes_admin_lifecycle_and_provider_controls"`: passed, 3 tests.

### Documentation

- `docs/stt-config.md`: documents saved discovered-model dropdowns and no credential-field render on pending draft pages.

### Risks / assumptions

- Existing `provider_model` browser field name remains because current web finalization route expects it.

### Architecture checkpoint summary

- Privacy boundaries preserved: raw provider secrets remain Vault-backed and are no longer re-rendered on pending model-step forms.
- Ownership rules preserved: system-admin-only STT provisioning routes/templates unchanged.
- Deletion semantics preserved: no deletion path changed.
- Provider rules preserved: saved discovered Deepgram models remain provider metadata; runtime/provider resolution unchanged.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-11 Deepgram STT Wizard Fix

### Scope

- Fixed Deepgram STT wizard discovery and runtime transport: live `/v1/models` discovery, invalid-key rejection before draft creation, and raw-audio `/v1/listen` transcription with query params.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/stt_presets.py`: make Deepgram discover models and stop seeding `nova-3` as static only option.
- `app/services/stt.py`: add Deepgram model discovery, inspection branch, preferred-model hint, raw-audio transport, and saved-config test propagation.
- `app/services/provider_inspection.py`: allow numeric list indexes in dotted JSON paths used by Deepgram response paths.
- `tests/test_api.py`, `tests/test_provider_inspection.py`: add Deepgram discovery, invalid-key, draft, raw-audio transport, call-site propagation, and dotted-index path tests.
- `docs/stt-config.md`, `docs/progress.md`: document Deepgram-specific discovery/transport.

### Tests

- `.venv/bin/pytest -q tests/test_api.py tests/test_provider_inspection.py -k "deepgram or stt_provider_draft or transcribe_with_team_stt_openai_compatible_rest_uses_vault_secret_and_response_path or transcribe_with_stt_snapshot_supports_old_and_new_snapshot_fields or extract_json_path_supports_dot_paths"`: passed, 9 tests.

### Documentation

- `docs/stt-config.md`: adds Deepgram discovery, credential, model, and raw-audio request behavior.

### Risks / assumptions

- Non-auth Deepgram discovery failures create a partial draft with manual model entry possible, matching existing wizard tolerance for degraded inspection.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content exposure; runtime logs include metadata only, not audio/key/text.
- Ownership rules preserved: discovery/drafts remain system-admin-only and team-scoped; users only consume active selections.
- Deletion semantics preserved: no deletion path changed; invalid credentials fail before config persistence.
- Provider rules preserved: raw API key stays Vault-backed; Deepgram uses Token auth and provider-specific transport.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 STT Provider Wizard

### Scope

- Added LLM-style STT provider presets, draft/finalize/replace-credential API and browser routes, setup status, and selection gating for incomplete providers.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none.

### Files changed

- `app/models.py`, `alembic/versions/d3e4f5a6b7c9_add_stt_provider_wizard_fields.py`: add STT provider preset/setup status and unique team label index.
- `app/services/stt_presets.py`, `app/services/stt.py`: add preset catalog, draft/finalize/replace services, selection gating, and branded auth headers.
- `app/schemas/stt.py`, `app/routes/api_routes.py`, `app/routes/web_admin.py`, `app/web/presentation.py`: expose wizard schemas/routes and response metadata.
- `app/templates/admin.html`, `app/templates/admin2.html`: show provider/setup states and draft/finalize controls.
- `tests/test_api.py`, `tests/test_migrations.py`: cover draft/finalize, pending selection block, and migration fields/index.
- `docs/stt-config.md`, `docs/progress.md`: document wizard layer and selection rules.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "stt_config or stt_selection or stt_provider or team_stt"`: passed, 30 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin2_exposes_admin_lifecycle_and_provider_controls or admin_page_can_save_team_stt_config_for_selected_team or admin2_stt_config_redirect_preserves_preview_route"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "onboarding_and_session_tables"`: passed, 1 test.
- `python -m py_compile ...`: passed for changed Python modules.

### Documentation

- `docs/stt-config.md`: documents provider presets and pending-vs-ready selection semantics.

### Risks / assumptions

- Deepgram and ElevenLabs first slice uses preset contracts/static default models, not live provider model discovery.

### Architecture checkpoint summary

- Privacy boundaries preserved: routes manage provider metadata/secrets only; no transcript-derived content returned.
- Ownership rules preserved: provisioning remains system-admin-only; selectors only see ready active team-scoped configs.
- Deletion semantics preserved: existing STT config deletion and Vault cleanup path unchanged.
- Provider rules preserved: raw credentials remain Vault-backed, never returned; branded presets map onto existing adapter/runtime model.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 LLM Provider Invalid Key and Label Uniqueness

### Scope

- Implemented remaining `LLM_Provider_Upgrade.md` items: invalid-key classification, no draft/secret on auth failure, draft label preservation, normalized per-team label uniqueness, and manual-model warning copy.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/llm.py`: preserves `llm_invalid_credential`, guards label uniqueness, preserves draft labels, catches DB uniqueness races.
- `app/schemas/llm.py`, `app/models.py`: adds draft label input and normalized unique label index metadata.
- `app/routes/web_admin.py`, `app/templates/admin.html`, `app/templates/admin2.html`: keeps bad keys on credential step and shows manual-save warning.
- `alembic/versions/c2d3e4f5a6b8_add_unique_llm_config_labels.py`: dedupes existing LLM config labels and adds unique index.
- `tests/test_api.py`, `tests/test_admin_ui.py`, `tests/test_migrations.py`: add regressions for bad keys, labels, warnings, and migration/index behavior.
- `docs/llm-providers.md`, `docs/progress.md`: document behavior.

### Tests

- Pending focused pytest in current session.

### Documentation

- `docs/llm-providers.md`: documents invalid-key handling, manual warning, and label uniqueness/preservation.

### Risks / assumptions

- Existing duplicate LLM labels are renamed deterministically with `copy N`, avoiding collisions with existing copy labels.

### Architecture checkpoint summary

- Privacy boundaries preserved: only provider metadata/labels touched; no transcript-derived content exposed.
- Ownership rules preserved: LLM provisioning remains system-admin-only and team-scoped.
- Deletion semantics preserved: no transcript/provider delete paths changed; invalid credentials avoid secret creation.
- Provider rules preserved: raw credentials remain Vault-backed and rejected keys are never persisted.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 LLM Provider Upgrade Review Patch

### Scope

- Applied verified follow-ups from `LLM_Provider_Upgrade.md`: zero-model live discovery now requires manual model entry, and team LLM selection responses expose selected provider preset/display name.
- Confirmed `/admin2` provider parity already had explicit regression assertions; no template change needed.
- Removed stale docs reference to deleted `API_Inspection_Upgrade.md`.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/llm.py`: normalize empty successful discovery to `manual_required` metadata across inspect/save paths.
- `app/schemas/llm.py`, `app/web/presentation.py`: add selected provider preset/display fields to `LlmSelectionDetail`.
- `tests/test_api.py`: add zero-model discovery regression and selection response provider-brand assertions.
- `docs/llm-providers.md`, `docs/provider-credential-combined-flow-plan.md`, `docs/progress.md`: document behavior and remove stale planning-doc reference.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin2_exposes_admin_lifecycle_and_provider_controls"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_api.py -k "llm_zero_model_discovery_requires_manual_model or leader_can_choose_and_clear_team_llm_selection or system_admin_can_inspect_bedrock_chat_models or llm_save_validates_model_against_successful_live_discovery"`: passed, 4 tests.
- `python3 -m py_compile app/services/llm.py app/schemas/llm.py app/web/presentation.py`: passed.

### Documentation

- `docs/llm-providers.md`: documents zero-model discovery fallback and selection provider-brand fields.
- `docs/provider-credential-combined-flow-plan.md`: replaces obsolete `API_Inspection_Upgrade.md` reference with current provider docs.

### Risks / assumptions

- Empty provider discovery is treated as manual fallback even if endpoint was reachable; this matches review expectation and avoids misleading provider-sourced metadata.

### Architecture checkpoint summary

- Privacy boundaries preserved: only provider model IDs/metadata touched; no transcript-derived content exposed.
- Ownership rules preserved: system-admin provisioning and team-scoped selection unchanged.
- Deletion semantics preserved: no transcript/provider deletion behavior changed.
- Provider rules preserved: Vault-backed secret handling unchanged; manual fallback is explicit admin-supplied model.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 LLM Provider Preset Hardening

### Scope

- Tightened LLM provider save behavior so live-discovered model lists are authoritative.
- Added metadata-aware Mistral and Together AI model discovery.
- Moved schema defaulting onto shared preset catalog helpers and added inspection timestamps.
- Clarified Bedrock HTTP gateway custom URL behavior in admin UI/docs.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/llm.py`: provider-specific model discovery, save-time model validation, inspection metadata timestamps.
- `app/services/llm_presets.py`, `app/llm_provider_defaults.py`, `app/schemas/llm.py`: single provider default source with schema shape validation only.
- `app/models.py`: documents `openai_chat` compatibility meaning.
- `app/templates/admin.html`, `app/templates/admin2.html`: explicit Bedrock/custom URL guidance.
- `tests/test_api.py`, `tests/test_admin_ui.py`: regressions for discovery, validation, metadata, schema default sharing, and UI copy.
- `docs/llm-providers.md`, `docs/progress.md`: provider behavior and change note.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "llm_provider_preset_catalog_and_inference or llm_schema_provider_defaults_use_shared_preset_catalog or llm_model_filtering_only_applies_openai_prefix_rules_to_openai or mistral_model_discovery_uses_chat_capability_metadata or together_model_discovery_uses_type_metadata or llm_provider_preset_saves_and_reclassifies_base_url_override or llm_save_validates_model_against_successful_live_discovery or llm_save_rejects_missing_model_when_discovery_fails or system_admin_saved_llm_inspection_uses_vault_key_and_updates_models or llm_manual_model_after_failed_discovery_is_selectable_and_metadata_is_service_owned or llm_endpoint_change_with_kept_secret_rediscover_models or llm_endpoint_change_with_failed_rediscovery_clears_stale_models or saved_llm_inspection_failure_persists_metadata_without_overwriting_models"`: passed, 13 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin2_exposes_admin_lifecycle_and_provider_controls"`: passed, 1 test.
- `python3 -m py_compile app/main.py app/web/presentation.py app/llm_provider_defaults.py app/schemas/llm.py app/services/llm_presets.py app/services/llm.py app/models.py`: passed.

### Documentation

- Updated LLM provider docs with `openai_chat` compatibility meaning, Bedrock URL reclassification, model validation, provider-specific filtering, and `inspected_at`.

### Risks / assumptions

- Assumes Together AI `chat`, `language`, and `code` records are acceptable for chat-completion selection.
- Assumes non-Mantle Bedrock gateway URLs should remain classified as Custom OpenAI-compatible for this branch.

### Architecture checkpoint summary

- Privacy boundaries preserved: only provider metadata and model IDs handled; no transcript-derived content exposed.
- Ownership rules preserved: LLM provisioning remains system-admin-only and team-scoped.
- Deletion semantics preserved: no transcript/provider deletion cascade changed.
- Provider rules preserved: Vault-backed bearer-token behavior unchanged; failed discovery requires manual model entry.
- Structured-note contract preserved: generated document and EMIS JSON behavior unchanged.

## 2026-05-10 LLM Provider Upgrade Fixes

### Scope

- Implemented the LLM provider upgrade blockers: manual failed-discovery models are selectable, provider endpoint edits no longer retain stale model lists, inspection metadata is service-owned, and saved inspection failures now persist metadata.
- Added Admin2 provider form parity for preset metadata, Bedrock region selection, and custom endpoint reclassification guidance.
- Documented `openai_chat` as the current OpenAI-compatible chat adapter semantics in `docs/llm-providers.md`.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: enum rename from `openai_chat` to `openai_compatible_chat` remains deliberate compatibility debt.

### Files changed

- `app/services/llm.py`: service-owned metadata, manual model persistence, endpoint-change rediscovery/stale clear, saved-inspection metadata persistence.
- `app/schemas/llm.py`: removed client-writable `inspection_metadata_json` from public upsert input.
- `app/templates/admin2.html`: added provider preset default metadata, Bedrock region selector, and custom endpoint note.
- `tests/test_api.py`: added regressions for manual model selection, metadata forgery rejection, endpoint rediscovery/stale clearing, and failed saved-inspection metadata.
- `tests/test_admin_ui.py`: added Admin2 provider preset parity assertions.
- `docs/llm-providers.md`, `docs/testing.md`, `docs/progress.md`: updated behavior and verification notes.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "llm_manual_model_after_failed_discovery_is_selectable_and_metadata_is_service_owned or llm_endpoint_change_with_kept_secret_rediscover_models or llm_endpoint_change_with_failed_rediscovery_clears_stale_models or saved_llm_inspection_failure_persists_metadata_without_overwriting_models or system_admin_saved_llm_inspection_uses_vault_key_and_updates_models or llm_provider_preset_saves_and_reclassifies_base_url_override or leader_can_choose_and_clear_team_llm_selection"`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin2_exposes_admin_lifecycle_and_provider_controls or admin2_preview_route_renders_for_system_admin"`: passed, 2 tests.

### Documentation

- `docs/llm-providers.md`: documents manual model selectability, stale model clearing/rediscovery, service-owned metadata, failed saved-inspection persistence, and adapter naming debt.
- `docs/testing.md`: expands LLM provider preset coverage summary.

### Risks / assumptions

- Existing Vault secret read during endpoint-change rediscovery fails closed if Vault read fails.
- Admin2 now has static preset metadata parity; full client-side provider/base URL sync remains lighter than `admin.html`.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content touched; inspection remains provider metadata/model discovery only.
- Ownership rules preserved: LLM provisioning remains system-admin-only; team/user selection validation remains team scoped.
- Deletion semantics preserved: no transcript/document/team/provider deletion paths changed; Vault cleanup order unchanged.
- Provider rules preserved: raw credentials remain Vault-backed, saved secret read is same-team/config scoped, manual models are explicit admin input, stale provider models are not carried across endpoint changes.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 LLM Provider Dropdown Base URL Sync

### Scope

- Fixed admin LLM provider dropdown behavior so switching from OpenAI to another branded preset replaces the known default base URL.
- Updated client note text to describe the selected provider instead of always saying OpenAI for OpenAI-compatible adapters.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/templates/admin.html`: sync known provider default URLs and provider-specific helper note on selection changes.
- `tests/test_admin_ui.py`: add static/client rendering regressions for preset URL metadata and note sync.
- `docs/progress.md`: records diagnosis and fix.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "branded_llm_provider_defaults or llm_provider_dropdown_syncs_base_url_and_note or admin_page_can_inspect_and_save_llm_provider_with_retyped_api_key or admin_page_can_inspect_and_save_bedrock_provider_with_retyped_api_key or admin_page_can_inspect_and_save_local_ollama_provider_without_api_key"`
- `python3 -m py_compile app/web/presentation.py`

### Architecture checkpoint summary

- Privacy boundaries preserved: UI metadata only.
- Ownership rules preserved: system-admin-only provider setup unchanged.
- Deletion semantics preserved: no deletion paths changed.
- Provider rules preserved: branded presets still map to existing adapters; custom edited endpoints still save as Custom OpenAI-compatible.
- Structured-note contract preserved: no generated-document behavior changed.

## 2026-05-10 STT Inspect Form Regression Fix

### Scope

- Fixed admin STT inspection rendering after LLM preset work accidentally added LLM-only form fields to `stt_form_defaults`.

### Checklist

- Code complete: yes
- Tests added/updated: existing focused admin STT inspection regression now passes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/web/presentation.py`: removed LLM provider preset fields from STT form defaults and kept them in LLM form defaults.
- `docs/progress.md`: records diagnosis and fix.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "inspect_team_stt_config_before_saving or optional_provider_defaults"`
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_page_can_inspect_and_save_llm_provider_with_retyped_api_key or admin_page_can_inspect_and_save_bedrock_provider_with_retyped_api_key or admin_page_can_inspect_and_save_local_ollama_provider_without_api_key or admin_templates_sync_optional_provider_credential_actions"`
- `python3 -m py_compile app/web/presentation.py`

### Architecture checkpoint summary

- Privacy boundaries preserved: form metadata only; no transcript-derived content access changed.
- Ownership rules preserved: system-admin STT inspection remains guarded.
- Deletion semantics preserved: no deletion paths changed.
- Provider rules preserved: STT and LLM form defaults remain separate.
- Structured-note contract preserved: no generated-document behavior changed.

## 2026-05-10 Branded LLM Provider Presets

### Scope

- Added system-admin LLM provider presets for OpenAI, OpenRouter, xAI, Groq, Mistral, DeepSeek, Together AI, Ollama, Bedrock HTTP gateway, and Custom OpenAI-compatible.
- Added `provider_preset` and `inspection_metadata_json` to LLM configs with migration backfill.
- Switched LLM discovery to live-only behavior with manual model fallback and OpenAI-only prefix filtering.
- Updated admin provider forms to show branded providers and Bedrock region selection while preserving Vault-backed secrets.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: internal protocol enum remains `openai_chat` for compatibility; preset layer carries OpenAI-compatible brand semantics.

### Files changed

- `app/models.py`, `alembic/versions/a0b1c2d3e4f6_add_llm_provider_presets.py`: add preset/metadata storage and backfill.
- `app/services/llm_presets.py`, `app/services/llm.py`: add preset catalog, inference, filtering, live discovery, manual fallback, reclassification, and metadata persistence.
- `app/schemas/llm.py`, `app/web/presentation.py`, `app/routes/web_admin.py`: extend API/form contracts.
- `app/templates/admin.html`, `app/templates/admin2.html`: expose branded presets and Bedrock region controls.
- `tests/test_api.py`, `tests/test_admin_ui.py`, `tests/test_migrations.py`: cover preset catalog, filtering, reclassification, UI save/inspect, and migration backfill.
- `docs/llm-providers.md`, `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document provider preset behavior and test coverage.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "llm_provider_preset or llm_model_filtering or provision_and_read_team_llm or provision_local_ollama or provision_bedrock or llm_inspection or missing_llm_secret"`
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "optional_provider_defaults or admin_templates_sync_optional_provider_credential_actions or admin_page_can_inspect_and_save_llm_provider_with_retyped_api_key or admin_page_can_inspect_and_save_bedrock_provider_with_retyped_api_key or admin_page_can_inspect_and_save_local_ollama_provider_without_api_key"`
- `.venv/bin/pytest -q tests/test_migrations.py -k "expected_schema or backfills_llm_provider_presets"`
- `python3 -m py_compile app/models.py app/schemas/llm.py app/services/llm.py app/services/llm_presets.py app/web/presentation.py app/routes/web_admin.py`

### Documentation

- Added `docs/llm-providers.md` and updated testing notes.

### Risks / assumptions

- No Anthropic/Gemini/Azure/native Bedrock adapter was introduced.
- Provider discovery still depends on provider compatibility with `/models` or Ollama `/api/tags`.

### Architecture checkpoint summary

- Privacy boundaries preserved: only provider metadata/model lists are exposed; no transcript-derived content access changed.
- Ownership rules preserved: provisioning remains system-admin-only; team/user selection stays scoped to active provisioned configs.
- Deletion semantics preserved: no transcript, document, team, or provider delete cascade semantics changed.
- Provider rules preserved: credentials stay Vault-backed, required-token providers fail closed without a saved/replacement token, Bedrock remains HTTP gateway.
- Structured-note contract preserved: generation JSON/EMIS validation paths unchanged.

## 2026-05-10 LLM Stale Secret Remove

### Scope

- Fixed explicit LLM `credential_action=remove` so a stale/missing Vault secret no longer blocks clearing the DB `vault_secret_ref` for optional-token adapters.
- Kept non-`vault_read_failed` read errors and Vault delete failures fail-closed.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: DB commit restoration remains best effort and only possible when the old Vault token was readable before delete.

### Files changed

- `app/services/llm.py`: tolerate `vault_read_failed` during restore-token snapshot before explicit removal, then continue idempotent Vault delete and DB clear.
- `tests/test_api.py`: add regression for stale LLM Vault refs clearing successfully.
- `docs/api.md`, `docs/security.md`, `docs/testing.md`, `docs/progress.md`: document stale-ref removal behavior.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "llm_secret_remove"`: passed, 4 tests.

### Documentation

- Updated API, security, testing, and progress docs for stale LLM Vault ref removal.

### Risks / assumptions

- Applies only to explicit LLM credential removal for optional-token adapters; OpenAI/Bedrock still cannot remove required saved credentials.
- If old token is unreadable and DB commit later fails, there is no token available for restoration; DB rollback preserves the existing reference.

### Architecture checkpoint summary

- Privacy boundaries preserved: no provider secrets are returned or logged; warning logs use IDs/error codes only.
- Ownership rules preserved: system-admin team-scoped LLM provisioning unchanged.
- Deletion semantics preserved: stale secret refs can be cleared, delete failure remains fail-closed, and DB commit restoration remains best effort when possible.
- Provider rules preserved: raw credentials remain Vault-backed and required-token adapters still require a saved or replacement token.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 LLM Credential Remove Fail-Closed

### Scope

- Changed explicit LLM `credential_action=remove` for optional-token providers so Vault delete failure aborts before the DB `vault_secret_ref` is cleared.
- Added best-effort Vault restoration if the DB commit fails after a successful explicit Vault delete.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: Vault restoration after DB commit failure is best effort and logs only sanitized IDs/error codes if restoration fails.

### Files changed

- `app/services/llm.py`: delete saved LLM Vault token before clearing the DB reference for explicit remove, with commit-failure restoration.
- `tests/test_api.py`: update LLM credential removal ordering tests and add fail-closed Vault delete coverage.
- `docs/api.md`, `docs/security.md`, `docs/testing.md`, `docs/progress.md`: document fail-closed LLM explicit removal behavior.

### Tests

- Focused LLM credential removal tests verify successful remove ordering, DB commit failure restoration, and Vault delete failure preserving the saved DB reference.

### Documentation

- Updated API, security, testing, and progress docs for LLM explicit credential removal ordering.

### Risks / assumptions

- Applies only to explicit LLM credential removal; full LLM config deletion keeps existing DB-first cleanup behavior.
- Requires reading the saved Vault token before delete so it can be restored if the DB commit fails.

### Architecture checkpoint summary

- Privacy boundaries preserved: no provider secrets are returned or logged.
- Ownership rules preserved: system-admin team-scoped LLM provisioning unchanged.
- Deletion semantics preserved: explicit credential remove fails closed on Vault delete failure and uses restoration compensation for DB commit failure.
- Provider rules preserved: OpenAI/Bedrock still require saved bearer credentials; optional-token local LLM providers may remove saved tokens.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 OpenAI-Compatible STT Token Validation

### Scope

- Fixed `openai_compatible_rest` STT replacement tokens so save-time validation performs a live bundled-sample transcription before writing the candidate token to Vault.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: focused tests still may show existing dependency/deprecation warnings if emitted.

### Files changed

- `app/services/stt.py`: route `openai_compatible_rest` replacement-token validation through the same saved-contract sample test used by `generic_rest`.
- `tests/test_api.py`: add regression for rejected OpenAI-compatible replacement token preserving existing config, team selection, and saved secret reference.
- `docs/api.md`, `docs/stt-config.md`, `docs/testing.md`, `docs/progress.md`: document live sample validation and regression coverage.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "openai_compatible_stt_bad_replacement_token_preserves_existing_config_and_selection or generic_stt_bad_replacement_token_preserves_existing_config_and_selection or generic_stt_save_with_token_tests_saved_contract_not_openapi_discovery"`: passed, 3 tests.

### Documentation

- Updated API, STT config, testing, and progress docs for OpenAI-compatible replacement-token live validation.

### Risks / assumptions

- `openai_compatible_rest` endpoints are expected to accept the known OpenAI-compatible multipart contract at save time; no schema/provider-resolution change.

### Architecture checkpoint summary

- Privacy boundaries preserved: no raw provider secrets are returned or logged.
- Ownership rules preserved: system-admin team-scoped STT provisioning unchanged.
- Deletion semantics preserved: invalid replacement rolls back DB changes and does not write candidate Vault secret.
- Provider rules preserved: Vault-backed credentials are only replaced after provider live sample acceptance.
- Structured-note contract preserved: no structured note changes.

## 2026-05-10 API Inspection Credential Hardening

### Scope

- Implemented remaining `API_Inspection_Upgrade.md` hardening: STT replacement credentials validate before Vault replacement, STT remove clears credential-derived state with explicit metadata, LLM remove clears DB references before Vault cleanup, and admin forms explain token non-retention.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: post-commit Vault outage can leave an orphaned LLM secret that is no longer referenced by DB state; cleanup failure is logged for follow-up. Focused tests still show existing dependency/deprecation warnings.

### Files changed

- `app/services/stt.py`: delay STT Vault writes until replacement credential validation succeeds and record credential removal metadata.
- `app/services/llm.py`: clear LLM DB secret references before deleting Vault secrets, including config deletion.
- `app/templates/admin.html`: add explicit STT/LLM credential handling copy.
- `tests/test_api.py`, `tests/test_admin_ui.py`: add/adjust credential lifecycle regressions and admin-template assertions.
- `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document credential lifecycle behavior.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "system_admin_can_explicitly_remove_saved_stt_secret or generic_stt_bad_replacement_token_preserves_existing_config_and_selection or system_admin_can_explicitly_remove_saved_ollama_secret or llm_secret_remove_deletes_vault_secret_after_db_commit or llm_secret_remove_keeps_vault_secret_when_db_commit_fails or llm_secret_remove_still_clears_db_ref_when_post_commit_vault_cleanup_fails or system_admin_cannot_keep_missing_secret_when_switching_llm_to_required_adapter"`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_templates_sync_optional_provider_credential_actions or admin_page_can_inspect_openai_stt_and_render_defaults"`: passed, 1 matching test.

### Documentation

- Updated API and testing docs for LLM post-commit Vault cleanup ordering, STT replacement validation ordering, STT credential removal metadata, and admin token non-retention copy.

### Risks / assumptions

- STT non-credential inspection failures still persist the replacement token and mark `partial`, matching prior partial-save behavior.
- Post-commit LLM Vault cleanup failures no longer break saved DB references; they can leave unreferenced Vault material until operational cleanup handles the logged event.

### Architecture checkpoint summary

- Privacy boundaries preserved: raw provider secrets are not returned or logged.
- Ownership rules preserved: system-admin team-scoped provider provisioning unchanged.
- Deletion semantics preserved: explicit credential removal clears DB secret references only after commit succeeds, then deletes Vault secrets with cleanup failure logged.
- Provider rules preserved: STT/LLM provider credential lifecycle remains Vault-backed and duplicate checks still run before Vault writes.
- Structured-note contract preserved: no structured note changes.

## 2026-05-10 LLM Required Secret Keep Guard

### Scope

- Fixed LLM config edits so `credential_action=keep` cannot switch a no-secret optional adapter into OpenAI/Bedrock without a saved Vault credential.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no architecture blocker; focused tests still show existing dependency/deprecation warnings if emitted.

### Files changed

- `app/services/llm.py`: add early saved-secret guard for OpenAI/Bedrock create/update paths.
- `tests/test_api.py`: add regression for no-secret Ollama to OpenAI keep edit rejection preserving existing config state.
- `docs/api.md`, `docs/progress.md`: document required-token `keep` semantics and checkpoint.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "missing_secret_when_switching_llm_to_required_adapter or system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal or system_admin_can_provision_local_ollama_without_secret or system_admin_can_explicitly_remove_saved_ollama_secret"`: passed, 4 tests.

### Documentation

- Updated API behavior docs for `credential_action=keep` on secret-required LLM adapters.

### Risks / assumptions

- Existing saved secret may be kept while switching adapter families; this preserves current behavior and only blocks missing-secret states.

### Architecture checkpoint summary

- Privacy boundaries preserved: raw provider secrets are not returned or logged.
- Ownership rules preserved: system-admin team-scoped LLM provisioning unchanged.
- Deletion semantics preserved: no deletion path changed.
- Provider rules preserved: OpenAI/Bedrock configs must retain Vault-backed credential references.
- Structured-note contract preserved: no structured note changes.

## 2026-05-10 STT Replacement Token Review Fix

### Scope

- Fixed failed `generic_rest` STT replacement-token verification so existing saved configs, active team selections, and prior Vault-backed credentials survive a 401/403 from the candidate token check.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no architecture blocker; focused tests still show existing dependency/deprecation warnings.

### Files changed

- `app/services/stt.py`: rollback existing-config mutations on credential rejection and restore the prior STT bearer token when replacement verification fails.
- `tests/test_api.py`: add regression coverage for bad generic REST replacement tokens preserving existing config state and selection.
- `docs/testing.md`, `docs/progress.md`: document the regression coverage and checkpoint.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "generic_stt_bad_replacement_token_preserves_existing_config_and_selection or stt_config_invalid_first_add_removes_db_row_before_vault_cleanup or generic_stt_save_with_token_tests_saved_contract_not_openapi_discovery"`: passed, 3 tests.

### Documentation

- Updated testing and progress docs for STT replacement-token credential lifecycle behavior.

### Risks / assumptions

- Existing STT replacement still writes the candidate token to the current Vault path before provider verification; the fix reads the old token first and restores it on provider credential rejection.

### Architecture checkpoint summary

- Privacy boundaries preserved: no raw provider secrets are returned or logged.
- Ownership rules preserved: system-admin team-scoped STT config access unchanged.
- Deletion semantics preserved: invalid first-add cleanup still deletes the new row only; failed replacement no longer deletes existing configs or dependent selections.
- Provider rules preserved: credentials remain Vault-backed and old credentials stay usable after a bad replacement token.
- Structured-note contract preserved: no structured note changes.

## 2026-05-09 API Inspection Upgrade Completion

### Scope

- Finished API inspection upgrade blockers: STT service imports cleanly, saved STT test calls use correct OpenAI/generic signatures, OpenAPI validation/dereferencing uses inspection libraries, malformed validator-detection failures become controlled `AppError`s, JSONPath extraction uses `jsonpath-ng`, configured STT segment mappings affect runtime and queued ingestion parsing, and browser save flows no longer accept hidden preserved provider tokens.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no architecture blocker; full suite still emits existing dependency/deprecation warnings.

### Files changed

- `app/services/provider_inspection.py`: validate OpenAPI docs, wrap validator detection failures, resolve local refs with Prance, and parse JSONPath via `jsonpath-ng`.
- `app/services/stt.py`: fix snapshot signature/calls, pass dynamic fields in saved tests/runtime, and apply configured segment mappings.
- `app/models.py`, `alembic/versions/20260509_003_add_stt_segment_snapshots_to_ingestion_jobs.py`, `app/services/transcripts.py`: persist and replay queued STT segment mapping snapshots.
- `app/routes/web_admin.py`, `app/templates/admin.html`: remove STT/LLM preserved-token hidden save path.
- `tests/test_api.py`, `tests/test_provider_inspection.py`: add regression coverage for inspection libraries, saved STT branches, snapshot compatibility, dynamic field names, and segment mapping.
- `docs/api.md`, `docs/stt-config.md`, `docs/testing.md`, `docs/progress.md`: document upgraded inspection/runtime/token behavior.

### Tests

- `.venv/bin/pytest -q`: passed, 542 tests passed, 1 skipped.
- `.venv/bin/pytest -q tests/test_provider_inspection.py`: passed, 7 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "expected_schema"`: passed, 1 test.

### Documentation

- Updated API, STT config, testing, and progress docs for library-backed inspection, JSONPath support, segment mapping, and token non-preservation.

### Risks / assumptions

- OpenAPI inspection now rejects invalid OpenAPI documents earlier; tests were updated to use valid fixtures.
- Prance is limited to internal refs for provider inspection to avoid arbitrary external reference fetches.

### Architecture checkpoint summary

- Privacy boundaries preserved: provider inspection does not inspect transcript-derived content and does not return raw secrets.
- Ownership rules preserved: provider configuration remains system-admin-only; transcript runtime access unchanged.
- Deletion semantics preserved: no deletion behavior changed.
- Provider rules preserved: saved secrets stay Vault-backed; standalone inspect tokens are one-request values; saved re-inspection uses Vault server-side.
- Structured-note contract preserved: no structured note changes.

## 2026-05-09 Provider Credential Combined Flow Implementation

### Scope

- Implemented STT save-and-inspect flow: one submitted bearer token is saved to Vault, inspected server-side, and never returned.
- Added credential status, safe duplicate fingerprinting, duplicate warning/confirmation, saved-provider re-inspection, invalid-selection clearing, and DB-before-Vault cleanup ordering for STT configs.
- Updated admin UI copy/actions for save-and-inspect, re-inspect, status display, and duplicate confirmation.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: LLM and de-identification remain on their existing provider-specific flows; this implementation applies the combined flow to current STT provider credential routes.

### Files changed

- `app/models.py`, `alembic/versions/20260509_002_add_stt_credential_inspection_status.py`: add STT credential status, fingerprint, and sanitized inspection metadata.
- `app/schemas/stt.py`, `app/web/presentation.py`: expose status/metadata and duplicate confirmation without exposing Vault refs or secrets.
- `app/services/stt.py`: add HMAC duplicate checks, save-and-inspect, saved re-inspection, invalid cleanup, and DB-before-Vault delete ordering.
- `app/routes/api_routes.py`, `app/routes/web_admin.py`, `app/api_route_audit.py`, `app/main.py`, `app/templates/admin.html`, `app/templates/admin2.html`: add re-inspect endpoint/action, status UI, and save-and-inspect copy.
- `tests/test_api.py`, `tests/test_migrations.py`: cover duplicate warning/override, invalid cleanup, partial status, saved re-inspection, selection clearing, and migration columns.
- `docs/api.md`, `docs/admin_brief.md`, `docs/stt-config.md`, `docs/security.md`, `docs/testing.md`, `docs/progress.md`: document behavior and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "stt_config"`: passed, 9 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "stt_config or stt or admin_restyled"`: passed, 20 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "stt_selection_purposes_per_team or new_stt_adapter_values or alembic_head_adds_onboarding_and_session_tables"`: passed, 3 tests.
- `.venv/bin/pytest -q`: passed, 534 tests passed, 1 skipped.

### Documentation

- Updated API, admin brief, STT config, security, testing, and progress docs for STT combined save-and-inspect semantics.

### Risks / assumptions

- Provider credential HMAC uses deployment secret material from `PROVIDER_CREDENTIAL_FINGERPRINT_SECRET`, `SECRET_KEY`, or `CSRF_SECRET`; local/dev fallback exists for tests/dev.
- `partial` means the credential was retained while metadata discovery/inspection did not fully succeed; provider-specific runtime may still fail later if endpoint contract is wrong.
- LLM/de-identification combined flow remains future work unless explicitly requested as a separate slice.

### Architecture checkpoint summary

- Privacy boundaries preserved: inspection uses provider metadata/known contracts only; no transcript/note/generated content enters admin inspection.
- Ownership rules preserved: STT credential provisioning/re-inspection/delete stay system-admin-only; team leaders still select policy only and never see raw credentials.
- Deletion semantics preserved: invalid first-add removes DB row before Vault cleanup; delete clears selections and commits DB removal before best-effort Vault cleanup.
- Provider rules preserved: raw credentials remain Vault-backed; Postgres stores only Vault reference, HMAC fingerprint, status, and sanitized metadata.
- Structured-note contract preserved: no EMIS or generated-document JSON behavior changed.

## 2026-05-09 Saved LLM Re-Inspection Uses Vault Key

### Scope

- Added saved LLM provider re-inspection endpoint/action that reads the stored Vault-backed key to discover models.
- Updated `/admin` and `/admin2` LLM provider controls to expose `Re-inspect models` / `Re-inspect saved credential` separately from setup-time model preview.
- Saved re-inspection refreshes sanitized available-model metadata and never returns the raw API key.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: no LLM credential status column added; this is model-refresh behavior only.

### Files changed

- `app/services/llm.py`: add `inspect_saved_llm_config` using `read_team_llm_bearer_token`.
- `app/routes/api_routes.py`, `app/routes/web_admin.py`, `app/main.py`, `app/api_route_audit.py`: add saved LLM inspect route wiring.
- `app/templates/admin.html`, `app/templates/admin2.html`: add saved credential re-inspect actions and clarify blank token keeps saved key.
- `tests/test_api.py`: verifies saved LLM inspection uses Vault key, refreshes models, and does not leak the key.
- `docs/api.md`, `docs/admin_brief.md`, `docs/testing.md`, `docs/progress.md`: document saved LLM model re-inspection.

### Tests

- `.venv/bin/pytest -q tests/test_api.py -k "saved_llm_inspection or llm_inspection or can_inspect_bedrock"`: passed, 3 tests.
- `.venv/bin/pytest -q tests/test_api_route_audit.py tests/test_admin_ui.py -k "api_route_audit or llm"`: passed, 9 tests.

### Documentation

- Updated API, admin brief, testing guide, and this progress entry.

### Risks / assumptions

- Re-inspection updates `available_models_json` and default model when fetched list no longer contains current default.
- Existing standalone inspect remains useful for first-time setup with an entered key; saved inspect is for existing providers only.

### Architecture checkpoint summary

- Privacy boundaries preserved: model discovery uses provider metadata only, no transcript/note/generated content.
- Ownership rules preserved: saved LLM re-inspection remains system-admin-only.
- Deletion semantics unchanged.
- Provider rules preserved: raw key stays in Vault, API/UI responses contain only sanitized model metadata.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-09 Provider Credential Combined Flow Plan

### Scope

- Documented admin-only combined provider credential create flow where admins enter API keys once, then server saves and validates/inspects in one pass.
- Captured duplicate warning, status semantics, re-inspection, active-selection clearing, Vault cleanup order, audit logging, and test/doc scope.

### Checklist

- Code complete: docs-only plan, no code changes.
- Tests added/updated: not applicable for docs-only plan.
- Docs added/updated: yes.
- Open issues: exact endpoint/schema names to resolve during implementation.

### Files changed

- `docs/provider-credential-combined-flow-plan.md`: adds implementation plan and architecture checkpoints for combined save+inspect flow.
- `docs/progress.md`: records planning checkpoint.

### Tests

- Not run; documentation-only change.

### Documentation

- Added provider credential combined flow plan.

### Risks / assumptions

- Assumes duplicate detection can use safe server-side fingerprint/HMAC without exposing or comparing raw secrets.
- Assumes existing providers should remain runtime-usable as `unknown` until manually re-inspected.

### Architecture checkpoint summary

- Privacy boundaries preserved: plan forbids transcript/note content in inspection and forbids raw secrets/raw provider bodies in responses/logs.
- Ownership rules preserved: provisioning remains system-admin only; team leaders do not gain credential visibility.
- Deletion semantics preserved: DB references/selections are removed or cleared before Vault cleanup, with retry/compensation for cleanup failure.
- Provider rules preserved: credentials remain Vault-backed; invalid first-add credentials are cleaned up; existing providers require explicit delete.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-09 Provider Inspection Upgrade

### Scope

- Added saved STT runtime contract fields for provider-specific model/language form names and optional segment mapping.
- Upgraded STT OpenAPI inspection to infer `model_id`/`lang`-style fields and runtime transcription to send saved field names only.
- Added machine-readable LLM discovery states and updated admin UI copy so inspect/discover tokens are not retained after responses.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/models.py`, `alembic/versions/20260509_001_add_stt_dynamic_contract_fields.py`: add STT config/job contract columns and backfills.
- `app/schemas/stt.py`, `app/schemas/llm.py`: expose STT field mappings and LLM discovery status fields.
- `app/services/provider_inspection.py`, `app/services/stt.py`, `app/services/llm.py`, `app/services/transcripts.py`: add shared inspection helpers, STT inference/runtime wiring, LLM status mapping, and queued-job snapshots.
- `app/routes/web_admin.py`, `app/web/presentation.py`, `app/templates/admin.html`: render dynamic STT fields, LLM discovery status, and require token re-entry after inspect.
- `tests/test_api.py`, `tests/test_admin_ui.py`, `tests/test_provider_inspection.py`, `tests/test_migrations.py`: cover inference, runtime field use, UI secret handling, LLM states, and schema columns.
- `docs/api.md`, `docs/stt-config.md`, `docs/admin_brief.md`, `docs/testing.md`, `requirements.txt`: document provider lifecycle, field mapping, discovery states, security rules, and dependencies.

### Tests

- Added tests for STT OpenAPI dynamic field inference, runtime dynamic form fields, JSONPath response extraction, LLM discovery status/manual-required state, admin UI token non-rendering/re-entry, and migration columns.
- `.venv/bin/python -m py_compile app/models.py app/schemas/stt.py app/schemas/llm.py app/services/provider_inspection.py app/services/stt.py app/services/llm.py app/services/transcripts.py app/routes/web_admin.py app/web/presentation.py tests/test_api.py tests/test_admin_ui.py tests/test_provider_inspection.py tests/test_migrations.py`: passed.
- `.venv/bin/pytest -q tests/test_provider_inspection.py`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "system_admin_can_inspect_stt_openapi_and_get_prefilled_fields or system_admin_can_inspect_generic_stt_dynamic_field_names or transcribe_with_team_stt_uses_saved_model_and_language_field_names or system_admin_can_inspect_bedrock_chat_models or system_admin_llm_inspection_exposes_manual_required_state"`: passed, 5 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "inspect_team_stt_config_before_saving or save_stt_config_after_inspect_with_retyped_token or inspect_and_save_llm_provider_with_retyped_api_key or inspect_and_save_bedrock_provider_with_retyped_api_key"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "alembic_head_adds_onboarding_and_session_tables"`: passed, 1 test.
- `.venv/bin/pytest -q`: passed, 529 tests passed, 1 skipped.

### Documentation

- Updated API, STT config, admin brief, testing guide, and this progress entry.

### Risks / assumptions

- `requirements.txt` now lists OpenAPI/JSONPath dependencies for install parity, but current helper implementation keeps local dereference/JSONPath support minimal for this slice.
- Runtime still ignores segment mapping beyond persistence/inspection; transcript text extraction behavior remains existing text/segment handling.

### Architecture checkpoint summary

- Privacy boundaries preserved: inspect/discover uses provider metadata and synthetic/sample-only tests; no transcript/note content exposed to admins.
- Ownership rules preserved: STT/LLM provisioning remains system-admin scoped; user/team selection scopes unchanged.
- Deletion semantics preserved: new columns are metadata on existing cascaded roots/jobs; no cascade weakened.
- Provider rules preserved: credentials remain Vault-backed on save; inspect tokens are not rendered or persisted; runtime uses saved provider contracts only.
- Structured-note contract preserved: no generated-document or EMIS JSON contract changed.

## 2026-05-09 Dictation-Only Redaction Guard

### Scope

- Fixed empty transcript redaction for dictation-only generation so strict remote de-identification providers are not called with an empty transcript snapshot.
- Kept dictation text in the transient redaction path before LLM generation.
- Fixed follow-on suite issues found during review: missing transcribe route template, recovery authorization ordering, and remembered-device logout persistence.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: full-suite rerun result noted below

### Files changed

- `app/services/redaction.py`: creates a successful empty redaction run locally when transcript snapshot text is empty.
- `app/routes/web_transcribe.py`: points the legacy Claude transcribe route at the active transcribe template.
- `app/routes/api_routes.py`, `app/routes/web_admin.py`, `app/routes/web_team_management.py`: check target-user manageability before mail-transport availability for recovery actions; keep trusted-device cookies across normal logout.
- `tests/test_admin_ui.py`, `tests/test_api.py`, `tests/test_auth_email.py`, `tests/test_migrations.py`: update regressions for current UI/provider copy, PII response shape, recovery authorization, and schema table list.
- `docs/progress.md`: records review-fix checkpoint.

### Tests

- Added regression coverage for dictation-only generation with empty transcript text and non-empty dictation.
- `.venv/bin/python -m py_compile app/services/redaction.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "dictation_only_session_before_provider_call or redacts_dictation_before_provider_call"`: passed, 2 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "...focused failing admin UI tests..."`: passed, 24 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "...focused failing API tests..." tests/test_auth_email.py::test_leader_cannot_recover_cross_team_user tests/test_migrations.py::test_alembic_upgrade_head_creates_expected_schema`: passed, 8 tests.
- `.venv/bin/python -m py_compile app/routes/api_routes.py app/routes/web_admin.py app/routes/web_team_management.py app/routes/web_transcribe.py app/services/redaction.py tests/test_admin_ui.py tests/test_api.py tests/test_auth_email.py tests/test_migrations.py`: passed.
- `.venv/bin/pytest -q`: passed, 522 tests passed, 1 skipped.

### Documentation

- Added progress entry here.

### Risks / assumptions

- Assumes empty transcript snapshots should still produce a reusable successful redaction run with zero entities.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript or note content visibility changed.
- Ownership rules preserved: redaction run remains tied to same owner, team, transcript, and transcript version.
- Deletion semantics preserved: new empty run remains transcript-derived and cascades through existing transcript-root relationships.
- Provider rules preserved: selected de-identification provider still handles non-empty transcript/dictation text; empty transcript text is not sent remotely.
- Structured-note contract preserved: generated-document JSON/EMIS behavior unchanged.

## 2026-05-08 Clinical NLP Chunking

### Scope

- Added bounded chunking for clinical NLP generic REST calls so long `/analyze` payloads no longer sit behind one slow provider request.
- Preserved returned span offsets by adding chunk offsets before encrypted clinical entity persistence.
- Defaulted local `/analyze` clinical calls to `sentence_detection=false` unless provider config explicitly overrides it.
- Recorded synthetic-only benchmark findings for the local OpenMed service.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: focused test run result noted below

### Files changed

- `app/services/clinical_nlp.py`: adds chunk splitting, offset correction, clinical-specific REST detection, and `/analyze` request defaults.
- `app/services/redaction.py`: lets generic REST detection accept caller-specific extra body defaults and failure labels.
- `tests/test_api.py`: adds chunking regression coverage and chunk offset/sentence-boundary unit coverage.
- `docs/api.md`: documents clinical NLP chunked runtime behavior.
- `docs/clinical-nlp-synthetic-benchmark.md`: records synthetic payload timing observations and selected technique.
- `docs/progress.md`: records this checkpoint.

### Tests

- Added focused tests for long clinical NLP chunking, `sentence_detection=false` defaulting, offset preservation, and chunk boundary behavior.
- `.venv/bin/python -m py_compile app/services/clinical_nlp.py app/services/redaction.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "clinical_detection or clinical_nlp or generic_rest_deidentification_spans_are_normalized_and_filtered"`: passed, 7 tests.
- Live synthetic `/analyze` check with `~11.9k` chars and `sentence_detection=false`: returned `200` in about `9.9s`.

### Documentation

- Updated API notes and added synthetic benchmark document.

### Risks / assumptions

- Assumes local OpenMedNER `/analyze` benefits from `sentence_detection=false`; provider config can override by explicitly setting `sentence_detection`.
- Chunking keeps each request bounded but total runtime can still grow with very long transcripts or a single-worker provider.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content logged or exposed; synthetic benchmark uses no real content.
- Ownership rules preserved: clinical values still come from owner-scoped transcript versions and store with owner DEK.
- Deletion semantics preserved: no table or cascade behavior changed.
- Provider rules preserved: active team clinical NLP provider selection remains unchanged; only request execution is chunked.
- Structured-note contract preserved: no generated-document JSON or EMIS section contract changed.

## 2026-05-07 Agent Pytest Instruction

### Scope

- Added explicit `AGENTS.md` guidance to run pytest through `.venv/bin/pytest`.
- Included full-suite and focused-test command examples.

### Checklist

- Code complete: yes
- Tests added/updated: not needed, docs-only agent instruction change
- Docs added/updated: yes
- Open issues: none

### Files changed

- `AGENTS.md`: documents virtualenv pytest command.
- `docs/progress.md`: records this docs-only change.

### Tests

- Not run; docs-only wording change.

### Documentation

- Updated `AGENTS.md` and this progress entry.

### Risks / assumptions

- Assumes project virtualenv remains at `.venv`.

### Architecture checkpoint summary

- Privacy boundaries preserved: no application behavior changed.
- Ownership rules preserved: no authz behavior changed.
- Deletion semantics preserved: no lifecycle behavior changed.
- Provider rules preserved: no provider behavior changed.
- Structured-note contract preserved: no structured output behavior changed.

## 2026-05-07 Live Chunk Lifecycle Recovery

### Scope

- Hardened current `live_chunked` capture against background/unload browser behavior.
- Backgrounding now pauses `MicVAD` even while listening, so capture flushes before browser timer throttling can stall chunking.
- Page unload now stops local mic state and sends a best-effort keepalive finalize request so uploaded chunks are reconciled instead of leaving a stale `recording` session.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: sudden tab/process kill can still lose an unuploaded in-memory speech segment; true continuous resilience needs ASR/provider streaming or a browser-resumable local chunk queue.

### Files changed

- `app/static/js/transcribe/media.js`: adds lifecycle-exit handling and broadens background flush behavior.
- `app/static/js/transcribe/app.js`: passes `keepalive` to live finalize and invokes media lifecycle cleanup on `pagehide`.
- `tests/test_api.py`: adds finalize regression for live sessions with no pending chunks.
- `tests/test_admin_ui.py`: locks frontend lifecycle wiring.
- `docs/live_stt.md`, `docs/transcript-capture.md`, `docs/progress.md`: document lifecycle behavior and residual limits.

### Tests

- Added API coverage that live finalize moves a no-pending `recording` transcript to `ready`.
- Added static frontend coverage for unload lifecycle finalize wiring.
- `node --check app/static/js/transcribe/media.js`: passed.
- `node --check app/static/js/transcribe/app.js`: passed.
- `.venv/bin/python -m py_compile tests/test_api.py tests/test_admin_ui.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "finalize_live_capture"`: passed, 4 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_frontend_uses_global_template_selector_for_generation_controls"`: failed after the new lifecycle assertions on an existing `manual-pii` string assertion in `app.js`.

### Documentation

- Updated live STT and transcript capture docs with background/unload recovery behavior.
- Added this progress entry.

### Risks / assumptions

- Browser `fetch(..., keepalive: true)` is best-effort; it cannot guarantee delivery after abrupt process death.
- Raw live audio still remains browser-local until chunk upload; this preserves privacy but means an unuploaded current segment can be lost on hard unload.

### Architecture checkpoint summary

- Privacy boundaries preserved: only owner browser calls existing owner-only finalize; no transcript content exposed to admins/leaders.
- Ownership rules preserved: no authz path changed; finalize still resolves transcript through owner check.
- Deletion semantics preserved: no transcript-root or child cascade behavior changed.
- Provider rules preserved: no STT provider resolution or secret handling changed.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-07 Recorded Upload Rollover Failure Handling

### Scope

- Fixed recorded-upload rollover so capture resumes only after the current part upload succeeds.
- If a rollover part upload fails, capture stops and no later audio is recorded after the failed part.
- Stop clicks during an in-flight rollover upload now wait for that upload instead of submitting the same segment twice.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser-held rollover parts are still not durable across refresh/tab close before upload succeeds

### Files changed

- `app/static/js/transcribe/media.js`: makes rollover upload return success/failure, waits before restart, stops on failed rollover upload, and guards stop-during-rollover duplicate upload.
- `tests/test_admin_ui.py`: extends static rollover regression coverage for wait-before-restart and failed-upload stop behavior.
- `docs/transcript-capture.md`, `docs/progress.md`: document failure handling.

### Tests

- Updated static UI coverage to verify rollover waits for upload result and stops instead of continuing after failed upload.

### Documentation

- Updated capture docs and this progress entry.

### Risks / assumptions

- Browser memory still holds in-flight parts only while the tab remains open.
- Non-409 upload failures stop capture rather than silently retrying forever.

### Architecture checkpoint summary

- Privacy boundaries preserved: audio still uploads only through existing authenticated transcript endpoints; no content logs added.
- Ownership rules preserved: rollover parts still use captured transcript ids and server-side owner checks.
- Deletion semantics preserved: no transcript root or cascade behavior changed.
- Provider rules preserved: existing team STT selection and whole-file processing path remain unchanged.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-06 Recorded Upload Rollover

### Scope

- Added recorded-upload microphone rollover before browser-captured WAV parts approach whole-file upload limits.
- Current recording part is queued to the existing owner-only whole-file transcription endpoint, then capture restarts for the same transcript.
- If backend still has a file job in progress, browser holds next part in memory and retries instead of bypassing server lifecycle rules.
- Fixed rollover stop/upload races so post-stop restart continuations abort and queued blobs keep their original transcript id.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: browser-held rollover parts are not durable across refresh/tab close before upload succeeds

### Files changed

- `app/static/js/transcribe/media.js`: adds batch rollover thresholds, forced VAD pause/restart, queued upload retry on active-job conflicts, post-await stop abort, and captured transcript id upload binding.
- `app/static/js/transcribe/app.js`: wires main consultation rollover config below whole-file server caps and lets dictation uploads use captured transcript ids.
- `tests/test_admin_ui.py`: adds static regression checks for rollover config, upload retry behavior, stop-race guard, and transcript id binding.
- `docs/transcript-capture.md`, `docs/api.md`, `docs/transcribe-playwright-checklist.md`, `docs/progress.md`: document rollover behavior and test checklist.

### Tests

- Added static UI coverage for rollover threshold config, forced split/restart, existing endpoint reuse, active-job retry messaging, post-stop restart abort, and queued upload transcript binding.

### Documentation

- Updated capture/API/checklist docs and this progress entry.

### Risks / assumptions

- Assumes normal worker ordering processes same-transcript whole-file jobs in acceptable order once backend active-job guard clears.
- Browser memory queue protects active capture and captured transcript routing, but refresh/tab close can lose a part that has not yet uploaded.

### Architecture checkpoint summary

- Privacy boundaries preserved: audio parts use existing owner-only upload path and captured transcript ids; no transcript text or note content logs added.
- Ownership rules preserved: server still resolves authenticated owner and transcript before queueing each part; client no longer retargets delayed blobs to a changed active consultation.
- Deletion semantics preserved: all jobs remain children of the same transcript root and existing cascade behavior applies.
- Provider rules preserved: each part uses existing team STT selection/resolution and credential checks.
- Structured-note contract preserved: no EMIS/generated-document JSON contract changed.

## 2026-05-06 Mobile Transcribe Layout

### Scope

- Added mobile presentation rules for `/transcribe`.
- Added mobile sidebar controller so recent consultations become an off-canvas drawer below `768px`.
- Kept change frontend-only.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: manual browser pass on 390px, 430px, and tablet widths still recommended.

### Files changed

- `app/templates/transcribe.html`: loads mobile CSS and JS assets.
- `app/static/css/transcribe-mobile.css`: responsive layout rules for mobile workspace, controls, tabs, toasts, and note rows.
- `app/static/js/transcribe/mobile.js`: off-canvas consultation drawer behavior and accessibility state.
- `tests/test_admin_ui.py`: verifies mobile assets are included on `/transcribe`.
- `docs/transcribe_brief.md`, `docs/progress.md`: document mobile layout behavior.

### Tests

- Added transcribe page smoke assertion for mobile CSS/JS assets and workspace endpoint marker.

### Documentation

- Transcribe brief now notes mobile off-canvas rail behavior.
- Progress note added.

### Risks / assumptions

- Assumes current desktop sidebar should become an off-canvas drawer below `768px`.
- Manual visual testing is still needed on real mobile/tablet devices.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content access path or logging changed.
- Ownership rules preserved: existing owner-only transcribe route and workspace APIs unchanged.
- Deletion semantics preserved: existing selected-session delete form and transcript-root cascade unchanged.
- Provider rules preserved: STT/LLM/de-identification selection and secret handling unchanged.
- Structured-note contract preserved: EMIS keys and generated-document JSON behavior unchanged.

## 2026-05-06 Home2 Spacing And Section Polish

### Scope

- Tightened `/home2` vertical spacing.
- Moved signed-in identity into the sidebar top area and removed the overview helper copy from `/home2`.
- Restyled section headers to follow `/admin2` section-head spacing.
- Moved Templates, Quick actions, and Smart phrases create buttons into their list/panel areas.

### Checklist

- Code complete: yes.
- Tests added/updated: no new tests for styling-only template polish.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/templates/home.html`: adjusts `/home2` sidebar identity and moves create actions into section bodies.
- `app/templates/_home2_admin2_style.html`: tightens Home2 layout, hides empty topbar, adds Admin2-style section headers and list rows.
- `docs/home_brief.md`, `docs/progress.md`: document Home2 chrome behavior.

### Tests

- Existing `/home2` access tests cover role gates; local run status recorded in final response.

### Documentation

- Home brief now documents sidebar identity and create actions inside content areas.

### Risks / assumptions

- Presentation-only change; normal `/home` layout remains gated by variant conditionals.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content fields or visibility paths changed.
- Ownership rules preserved: route and role gates unchanged.
- Deletion semantics preserved: no delete, retention, or cascade path changed.
- Provider rules preserved: provider labels already visible to current user are only repositioned.
- Structured-note contract preserved: no generated-document or EMIS behavior changed.

## 2026-05-06 Home2 Sidebar Navigation Polish

### Scope

- Moved `/home2` section tabs into the left sidebar.
- Docked the speech service, writing assistant, and template summary at the bottom-left of the sidebar.
- Adjusted `/home2` main content spacing to more closely match `/admin2`.

### Checklist

- Code complete: yes.
- Tests added/updated: no new tests for styling-only template restructure.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/templates/home.html`: renders `/home2` tabs and service summary inside sidebar while keeping normal `/home` layout unchanged.
- `app/templates/_home2_admin2_style.html`: makes sidebar nav vertical and docks compact service summary at sidebar bottom.
- `docs/home_brief.md`, `docs/progress.md`: document `/home2` sidebar behavior.

### Tests

- Existing `/home2` access tests cover route/role gates; local run status recorded in final response.

### Documentation

- Home brief now notes `/home2` sidebar tabs and bottom-docked service summary.

### Risks / assumptions

- This is presentation-only. Existing forms still post to existing `/home/...` handlers.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content fields or visibility paths changed.
- Ownership rules preserved: route and role gates unchanged.
- Deletion semantics preserved: no delete, retention, or cascade path changed.
- Provider rules preserved: only provider labels already visible to user/leader are repositioned.
- Structured-note contract preserved: no generated-document or EMIS behavior changed.

## 2026-05-06 Live Capture Throttle Hardening

### Scope

- Hardened browser live capture against route-level burst throttling and active-speech tab backgrounding.

### Checklist

- Code complete: yes.
- Tests added/updated: no new JS harness exists for `app/static/js/transcribe`; existing API tests could not run because local `pytest` is unavailable.
- Docs added/updated: yes.
- Open issues: browser background recording remains subject to browser/OS microphone and timer policies.

### Files changed

- `app/static/js/transcribe/media.js`: paces live chunk uploads, retries `429` with same sequence number, and flushes active speech when tab is hidden.
- `app/static/js/transcribe/app.js`: passes live pacing/retry timing constants into capture controllers.
- `docs/live_stt.md`, `docs/transcript-capture.md`: document live upload pacing, retry, and background flush behavior.
- `docs/progress.md`: records change.

### Tests

- JS syntax checks passed for `app/static/js/transcribe/media.js` and `app/static/js/transcribe/app.js`.
- Existing live chunk API rate-limit coverage remains the server guard for `1 request/second` enforcement, but could not run locally because `pytest` is not installed in this environment.
- Manual-code inspection covers frontend-only pacing because no JS test runner/package manifest exists in repo.

### Documentation

- Live STT and transcript capture docs now state client pacing, short `429` retry, and active-speech background flush.

### Risks / assumptions

- This reduces accidental burst `429` and foreground-to-background loss, but cannot guarantee continuous recording if browser/OS suspends microphone capture for hidden tabs.
- Retry is intentionally short and same-sequence only; no persisted per-chunk retry queue was added.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content visibility or logging added.
- Ownership rules preserved: owner-only upload route and sequence checks unchanged.
- Deletion semantics preserved: no transcript root, cascade, retention, or job deletion behavior changed.
- Provider rules preserved: STT provider resolution and Vault-backed credential handling unchanged.
- Structured-note contract preserved: no generated-document or EMIS behavior changed.

## 2026-05-06 Home2 Admin2-Styled Preview

- Added `/home2` as user/team-leader Home preview with Admin2-style dark shell and shared home capabilities.

### Scope

- `/home2` renders existing Home tabs/actions for normal users and team leaders.
- System admins remain redirected to `/admin`.
- Home return-view handling now supports `home2` so form redirects can return to `/home2`.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/routes/web_home_transcribe.py`: adds `/home2` route using same Home context and access rules.
- `app/web/presentation.py`: adds `home2` return-view and style variant support.
- `app/templates/home.html`: includes optional Home2 style partial and body marker.
- `app/templates/_home2_admin2_style.html`: adds Admin2-inspired dark styling for existing Home markup.
- `tests/test_admin_ui.py`: covers normal user, team leader, and system-admin `/home2` access.
- `docs/home_brief.md`: documents `/home2` as styling-only Home preview.
- `docs/progress.md`: records change.

### Tests

- `/home2` renders for normal users with personal Home tabs and `return_view=home2`.
- `/home2` renders for team leaders with AI services and team-management tabs.
- `/home2` redirects system admins to `/admin`.

### Documentation

- Home brief notes `/home2` uses same Home data/actions/role gates with Admin2 styling.

### Risks / assumptions

- `/home2` is a preview route; backend forms still post to existing `/home/...` handlers.
- CSS is shared by inclusion inside existing CSP-nonced Home style block, not as external static CSS.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content fields or visibility paths changed.
- Ownership rules preserved: route uses same `render_home` services and role-scoped lists as `/home`.
- Deletion semantics preserved: delete forms still use existing explicit `/home/.../delete` POST routes.
- Provider rules preserved: service selection display and updates still use existing provider selection services.
- Structured-note contract preserved: template editor/EMIS section handling unchanged.

## 2026-05-05 Admin2 Preview Route

- Follow-up parity pass: admin2 now exposes user lifecycle/recovery/delete forms, account-request approval fields, provider inspect/test/delete/create forms, provider selection clear actions, de-identification assignment controls, and default asset creation forms while keeping the dark inline-expanded design.
- Follow-up provider IA pass: admin2 now includes CSRF auto-fill script, separates Clinical NLP into its own provider registry page, and moves de-identification/clinical NLP team assignment controls into Workspace -> Teams.
- Follow-up teams/people polish: Teams table now starts fully collapsed and can remain collapsed, People renders team names instead of team IDs, and People row actions are collapsed into an Actions dropdown with Lucide delete icon.
- Follow-up request review polish: Account requests now render as cards with separated request metadata, approve form, and reject form instead of cramped inline table controls.
- Follow-up template editor polish: `/admin2` template forms now hide Structured EMIS section prompts whenever mode is switched to `freeform`.
- Follow-up theme polish: `/admin2` now has browser-persisted dark/light appearance mode using CSS variables and a Preferences toggle.
- Follow-up workspace sizing: `/admin2` usable area widened for table-heavy admin screens while retaining responsive page padding.
- Follow-up divider polish: `/admin2` setting row separators now stop before action/control columns, avoiding lines running under selects/buttons in light mode.
- Follow-up hierarchy polish: `/admin2` two-column section headings now align at the top, with stronger headings and more spacing around clear-action button rows.
- Follow-up dropdown polish: `/admin2` custom action dropdowns now use Notion-like 250px scrollable popovers, move to the front of the DOM while open, close on click-away/Escape/resize, and auto-close after 3 seconds without hover.
- Follow-up select polish: `/admin2` native `.select` controls are progressively enhanced into Notion-like custom listboxes that portal to `body`, scroll when long, close on click-away/Escape/resize/idle hover, and sync back to the real select for form submission.
- Follow-up usage polish: `/admin2` Usage now has local Overview, Teams, and Providers tabs; provider tab exposes only aggregate provider/model metadata.
- Follow-up people/usage controls: `/admin2` Usage > Teams now splits Active/Suspended; People table header icons sort by name/age/team/role/status; one auto-closing filter popover contains team/status selects.
- Follow-up tab preservation fix: `/admin2` Failures now loads aggregate failure rows, and default quick-action save/duplicate/delete returns to Quick actions instead of Templates.

### Scope

- Added `/admin2` as a system-admin-only preview route for `app/templates/admin2.html`.
- Wired `admin2` return-view handling so admin form redirects can stay on `/admin2`.
- Cleaned `admin2.html` by removing inline styles and fixing EMIS section label rendering.
- Filled missing admin functionality in `/admin2` using existing backend admin routes and CSRF/return-view handling.
- Fixed browser CSRF submissions by including the shared CSRF script that fills `_csrf_token` fields from the CSRF cookie.
- Split Clinical NLP from De-identification in the provider registry while keeping team assignment/selection in the Teams workflow.
- Updated Teams and People UX so no team details open by default, row collapse does not open another team, and user actions are tucked behind a dropdown.
- Reworked Account requests layout to avoid overlapping requester text and approval/rejection controls.
- Reworked admin2 template mode handling so freeform templates show only global prompt controls while structured mode keeps EMIS section prompts visible.
- Added `/admin2` light theme variables and a localStorage-backed Preferences toggle without server-side preference or schema changes.
- Increased `/admin2` content width caps from narrow Notion-style defaults to broader admin-console caps for better table/form fit.
- Adjusted `/admin2` setting separators so divider borders live on the descriptive text cell instead of the whole grid row.
- Removed unintended top margin from second sections inside two-column grids and tightened section-header hierarchy/action spacing.
- Restyled custom admin2 action dropdown menus while keeping the theme as a toggle; native provider `<select>` popups remain browser-native.
- Added custom combobox/listbox enhancement for admin2 select menus while preserving original select values for POST handlers and validation.
- Added local Usage sub-tabs for Teams and Providers so aggregate metadata views are easier to scan.
- Added client-side admin2 People table controls: column-header sort buttons plus one metadata filter popover with synced selects.
- Fixed `/admin2` Failures data loading and quick-action return-tab preservation.
- Kept `/admin` and `/admin-restyled` behavior unchanged.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/routes/web_admin.py`: adds `/admin2` route with admin auth, template selection, admin2-specific tabs, and quick-action return-tab support.
- `app/web/presentation.py`: supports `admin2` return route, optional preview tab set, and aggregate usage context for Failures.
- `app/templates/admin2.html`: removes CSP-hostile inline styles, renders structured EMIS section labels from context correctly, and sends quick-action return-tab fields.
- `tests/test_admin_ui.py`: covers `/admin2` render, redirect preservation, Failures rows, and quick-action tab preservation.
- `docs/admin_brief.md`: documents `/admin2` preview route.
- `docs/progress.md`: records change.

### Tests

- `/admin2?tab=llm` renders for system admins and carries `return_view=admin2`.
- `/admin2` render avoids inline `style` attributes so CSP remains compatible.
- `/admin2` render exposes lifecycle, recovery, account request, provider inspect/test/delete/create, de-identification assignment, and model-selection controls.
- `/admin2` render includes the CSRF helper script and exposes Clinical NLP as its own provider registry page.
- `/admin2` De-identification page no longer shows team assignment controls; Teams page now contains those assignment controls.
- `/admin2` Teams starts collapsed; People shows team names, Lucide actions menu, and no table-cell team IDs.
- `/admin2` Account requests use request-card layout with distinct approve/reject forms.
- `/admin2` Templates dynamically hides structured EMIS controls for freeform mode.
- `/admin2` Preferences exposes a dark/light theme toggle persisted as `openscribe_admin2_theme` in browser localStorage.
- `/admin2` uses wider content containers (`wide` 1440px, `inner` 1240px) with responsive horizontal padding.
- `/admin2` setting dividers no longer overrun into adjacent controls.
- `/admin2` section headings align across columns and action button rows have clearer spacing.
- `/admin2` custom action menus portal to `body` while open so they layer over tables/forms and remain scrollable when long.
- `/admin2` select controls now use front-layer custom dropdowns; underlying native selects remain in the form and receive synced values.
- `/admin2` Usage tabs split overview charts, team rollups, and provider/model rollups without adding new content access paths.
- `/admin2` People filters/sort operate on already-rendered account metadata only; no backend access model changed.
- STT config save with `return_view=admin2` redirects back to `/admin2`.
- `/admin2?tab=failures` renders aggregate failure rows.
- Default quick-action save from `/admin2` redirects back to `tab=quick-actions`.

### Documentation

- Admin brief now notes `/admin2` as preview route using same backend routes and protections.

### Risks / assumptions

- `admin2.html` remains preview UI. Backend actions still use existing admin forms/routes.

### Architecture checkpoint summary

- Privacy boundaries preserved: route exposes only admin metadata already rendered by admin context; no transcript or generated-note content added.
- Ownership rules preserved: route requires system-admin session and does not change transcript ownership checks.
- Deletion semantics preserved: destructive actions still submit existing explicit admin POST routes.
- Provider rules preserved: provider secrets remain Vault-backed references and are not rendered as plaintext.
- Structured-note contract preserved: default structured template fields still use existing EMIS section contract.

## 2026-05-05 Source Transcript PII Visibility Fix

- Follow-up UI cleanup: PII sidebar now shows Type, Value, and Count only. Placeholder, Source, and Reveal columns removed from source/generic sidebar table.
- Follow-up sidebar fix: generated-note selection no longer replaces source transcript PII rows with generated-document no-value summaries.
- Follow-up copy fix: Copy transcript now prefers raw transcript state so browser-only masking is not copied.

### Scope

- Restored owner source-transcript PII values in workspace/API bootstrap responses.
- Added browser-only Hide PII / Show PII masking for source transcript PII values.
- Kept generated-document PII values minimised by default and stopped note PII renders from blanking source highlights.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/web/transcribe_workspace.py`: source transcript workspace PII now includes owner-visible values.
- `app/schemas/workspace.py`: workspace schema reflects detailed source PII rows.
- `app/static/js/transcribe/app.js`: masks PII locally and highlights source PII/clinical entities from source cache.
- `app/static/js/transcribe/documents.js`: generated-document PII sidebar no longer overwrites source transcript highlights.
- `app/templates/transcribe/_workspace.html`: adds Hide PII control and initial value display.
- `app/templates/transcribe/_shell_extras.html`: bumps transcribe asset version.
- `tests/test_pii_response_minimisation.py` and `tests/test_admin_ui.py`: update PII visibility/minimisation regressions.
- `docs/progress.md`: records fix.

### Tests

- Owner workspace source transcript PII includes values by default.
- Generated-document PII remains value-minimised by default.
- Transcribe UI source includes hide/show control, source value display, clinical styling, and no generated-doc highlight overwrite.

### Documentation

- Progress note added for source transcript PII visibility fix.

### Risks / assumptions

- Reveal endpoint remains for existing guarded uses; source transcript owner view no longer depends on it.
- Plaintext PII is sent only through authenticated owner workspace/source transcript responses.

### Architecture checkpoint summary

- Privacy boundaries preserved: generated-document PII still omits values by default; owner source transcript is privileged.
- Ownership rules preserved: workspace selection and reveal routes still require transcript owner access.
- Deletion semantics preserved: no retention, cascade, or hard-delete behavior changed.
- Provider rules preserved: no provider URL validation or secret handling changed.
- Structured-note contract preserved: no generated document or EMIS JSON schema behavior changed.

## 2026-05-05 Transcribe Tailwind Runtime Class Coverage

### Scope

- Added transcribe JavaScript files to Tailwind content scanning so utility classes emitted by runtime renderers are compiled.
- Added regression coverage for runtime-only transcribe history classes in generated CSS.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `tailwind.transcribe.config.js`: scans `app/static/js/transcribe/**/*.js` in addition to transcribe templates.
- `app/static/css/transcribe-tailwind.css`: regenerated Tailwind bundle with runtime JS classes included.
- `tests/test_cookie_csrf_security.py`: asserts config and compiled CSS include runtime-only classes.
- `docs/progress.md`: records Tailwind scan fix.

### Tests

- Added source-level coverage that checks Tailwind scans transcribe JS and compiled CSS contains `bg-teal-pale/35`, `border-teal-muted/35`, and `hover:bg-parchment/50`.

### Documentation

- Progress note added for transcribe Tailwind runtime class coverage.

### Risks / assumptions

- Generated CSS remains committed from local Tailwind build output; future runtime JS class additions depend on this scan glob staying in place.

### Architecture checkpoint summary

- Privacy boundaries preserved: static CSS build coverage does not expose transcript or note content.
- Ownership rules preserved: no owner/team/auth scope logic changed.
- Deletion semantics preserved: no retention or cascade behavior changed.
- Provider rules preserved: no provider selection or secret handling changed.
- Structured-note contract preserved: no EMIS or generated-document schema contract changed.

## 2026-05-05 ONNX Runtime Threaded VAD Assets

### Scope

- Added missing vendored `onnxruntime-web` threaded module loaders required by browser `MicVAD` startup paths.
- Added regression coverage that asserts complete pinned ONNX runtime asset set exists under `/static/vendor`.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/static/vendor/onnxruntime-web/1.22.0/ort-wasm-simd-threaded.mjs`: vendored threaded ONNX loader.
- `app/static/vendor/onnxruntime-web/1.22.0/ort-wasm-simd-threaded.jsep.mjs`: vendored JSEP threaded ONNX loader.
- `tests/test_cookie_csrf_security.py`: asserts required ONNX runtime files exist locally.
- `docs/live_stt.md`, `security_remediation_plan.md`, `docs/progress.md`: document complete self-hosted ONNX asset contract.

### Tests

- Added source-level regression coverage for required self-hosted ONNX runtime JS/WASM/module files used by browser live VAD.

### Documentation

- Live STT and remediation docs now note that pinned ONNX runtime assets must include matching threaded `.mjs` loaders, not only `.wasm` binaries.

### Risks / assumptions

- Vendored browser assets remain version-pinned in repo; future ONNX runtime bumps must refresh both `.wasm` and matching `.mjs` files together.

### Architecture checkpoint summary

- Privacy boundaries preserved: static runtime asset completeness fix does not widen transcript or note access.
- Ownership rules preserved: no auth, owner, or team checks changed.
- Deletion semantics preserved: no transcript lifecycle or cascade logic changed.
- Provider rules preserved: no STT/LLM/de-identification selection or secret handling changed.
- Structured-note contract preserved: no generated-document or EMIS schema behavior changed.

## 2026-05-04 CSP And Local Runtime Assets

### Scope

- Added nonce-based CSP headers to HTML responses.
- Replaced public CDN runtime dependencies with self-hosted static browser assets.
- Updated inline script/style blocks to use CSP nonces.
- Fixed CSP regressions by moving destructive confirmations and auto-submit controls off inline handlers.
- Scoped `upgrade-insecure-requests` to HTTPS responses so localhost HTTP dev stays usable.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/security_headers.py`: builds per-response CSP nonce and policy string.
- `app/main.py`: injects CSP nonce and sends hardened response headers.
- `app/templates/*.html`, `app/templates/transcribe/*.html`: remove public CDN refs, add CSP nonces, and migrate blocked inline handlers to nonced listeners/data attributes.
- `app/static/js/transcribe/app.js`: points VAD and ONNX runtime to local vendor assets.
- `app/static/css/transcribe-tailwind.input.css`, `tailwind.transcribe.config.js`, generated static vendor/css assets: local browser runtime asset pipeline.
- `tests/test_cookie_csrf_security.py`: CSP and no-public-CDN regression coverage.
- `docs/security.md`, `docs/progress.md`: document CSP and local asset rules.

### Tests

- Added CSP header coverage, nonce rotation coverage, HTTPS-only upgrade coverage, public-CDN static scan coverage, local VAD asset path coverage, and inline-handler regression coverage.

### Documentation

- Security doc now records nonce-based CSP, same-origin browser runtime asset rule, and acceptance checks.

### Risks / assumptions

- Auth and home pages now rely on fallback local fonts because public Google Fonts loads were removed.
- Self-hosted vendor files are pinned by version in repo; checksum verification in CI is still future work.
- Remaining inline handler compatibility risk reduced by moving current `home` and `admin` destructive/auto-submit flows onto nonced script listeners.

### Architecture checkpoint summary

- Privacy boundaries preserved: CSP narrows browser execution sources and does not widen content access.
- Ownership rules preserved: no owner/team/auth scope logic changed.
- Deletion semantics preserved: no retention or cascade behavior changed.
- Provider rules preserved: no provider resolution or secret handling changed.
- Structured-note contract preserved: no EMIS or generated-document schema contract changed.

## 2026-05-04 Generated Document PII Value Sanitisation

### Scope

- Sanitised PII rows during no-reveal generated-document renders so cached workspace values cannot appear in the generated-document PII panel.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/static/js/transcribe/app.js`: strips `value` from displayed/current PII rows when `allowReveal:false`.
- `tests/test_admin_ui.py`: asserts no-reveal renders use sanitised display rows.
- `docs/progress.md`: records change.

### Tests

- Updated UI source regression coverage for no-reveal PII row sanitisation.

### Documentation

- Progress note added for generated-document PII value sanitisation.

### Risks / assumptions

- Source-level frontend regression test covers current JS architecture; no browser DOM test was added.

### Architecture checkpoint summary

- Privacy boundaries preserved: no-reveal generated-document PII renders do not expose raw original values from workspace cache.
- Ownership rules preserved: owner-only reveal endpoint unchanged.
- Deletion semantics preserved: no retention or cascade behavior changed.
- Provider rules preserved: no STT/LLM/de-identification behavior changed.
- Structured-note contract preserved: no EMIS keys or structured JSON contract changed.

## 2026-05-04 Generated Document PII Reveal Guard

### Scope

- Hid transcript PII reveal controls while selected generated-document PII summaries are displayed, avoiding replacement with active transcript PII values.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/static/js/transcribe/app.js`: adds render option to suppress PII reveal buttons.
- `app/static/js/transcribe/documents.js`: disables reveal for generated-document PII rows.
- `tests/test_admin_ui.py`: asserts generated-document PII renders with reveal disabled.
- `docs/progress.md`: records change.

### Tests

- Updated UI source regression coverage for reveal suppression on generated-document PII rows.

### Documentation

- Progress note added for PII reveal guard.

### Risks / assumptions

- No document-specific reveal endpoint added; generated-document PII remains minimised until backend/API contract is designed.

### Architecture checkpoint summary

- Privacy boundaries preserved: generated-document PII summaries no longer expose or trigger unrelated transcript PII reveal.
- Ownership rules preserved: backend owner-only reveal remains unchanged.
- Deletion semantics preserved: no retention or cascade behavior changed.
- Provider rules preserved: no provider selection or secret handling changed.
- Structured-note contract preserved: no EMIS keys or structured JSON contract changed.

## 2026-05-04 PII Response Minimisation

### Scope

- Renamed owner-facing plaintext response fields away from `_encrypted` names for transcript drafts and generated-document body text.
- Changed default workspace PII rows to summaries without original values.
- Changed default generated-document PII rows to summaries without original values.
- Added owner-only `POST /api/v1/transcripts/{transcript_id}/pii-entities/reveal` for explicit value reveal.
- Added no-store headers for sensitive transcript/workspace/generated-document API paths.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: broader `tests/test_admin_ui.py` still has pre-existing unrelated failures noted in final response.

### Files changed

- `app/schemas/transcripts.py`, `app/schemas/workspace.py`, `app/schemas/templates.py`, `app/schemas/__init__.py`: response contracts for plaintext names and PII summary/detail split.
- `app/web/transcribe_workspace.py`, `app/web/presentation.py`: build minimised workspace rows and plaintext response fields.
- `app/routes/api_routes.py`, `app/api_route_audit.py`: add reveal endpoint and audit manifest entry.
- `app/main.py`: no-store sensitive API headers.
- `app/static/js/transcribe/*.js`, `app/templates/transcribe/_workspace.html`, `app/templates/glm-3.html`: frontend consumes new response names and reveals PII explicitly.
- `tests/test_pii_response_minimisation.py`, `tests/test_api.py`: response minimisation and renamed-field coverage.
- `docs/api.md`, `docs/security.md`, `docs/testing.md`, `docs/progress.md`: document behavior and tests.

### Tests

- Added coverage for default workspace and generated-document PII summaries omitting `value`.
- Added owner reveal, non-owner `404`, CSRF rejection, no-store headers, and renamed transcript/generated-document response fields.

### Documentation

- API/security/testing docs now describe summary-vs-detail PII payloads, generated-document summary minimisation, reveal endpoint, no-store headers, and plaintext response names.

### Risks / assumptions

- Fresh-MFA reveal was not added because current session model does not track a recent MFA verification timestamp for this path.
- Generated-document section response fields still mirror DB encrypted field names; plan only covered top-level generated document body fields.

### Architecture checkpoint summary

- Privacy boundaries preserved: default workspace and generated-document responses no longer return original PII values; reveal stays owner-only.
- Ownership rules preserved: reveal uses full auth and owner check with non-owner `404`.
- Deletion semantics preserved: no retention, cascade, or hard-delete behavior changed.
- Provider rules preserved: no STT/LLM/de-identification selection or secret behavior changed.
- Structured-note contract preserved: no EMIS section keys or structured JSON output contract changed.

## 2026-05-04 Vault-Backed CSRF Secret Bootstrap

### Scope

- Added automatic stable CSRF secret bootstrap through Vault KV-v2 when explicit env secrets are absent.
- Kept `CSRF_SECRET`/`SECRET_KEY` as explicit override path.
- Documented default Vault ref and local/production behavior.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/services/csrf.py`: resolves CSRF secret from env or Vault and caches it per process.
- `app/services/vault.py`: reads or create-if-absent writes platform CSRF secret.
- `tests/test_cookie_csrf_security.py`: covers production Vault fallback and failure behavior.
- `.env.example`, `README.md`, `docs/setup.md`, `docs/security.md`, `docs/progress.md`: document automatic setup.

### Tests

- Added coverage that production accepts Vault-backed CSRF secret when env secret is missing.
- Updated failure test so startup still fails when no env secret and Vault is unavailable.

### Documentation

- README/setup/security docs now explain `CSRF_SECRET_VAULT_REF`, default Vault path, and local vs production behavior.

### Risks / assumptions

- Production runtime needs Vault read/write permission for default `secret:openscribe/platform/csrf` if no explicit `CSRF_SECRET` is supplied.
- Multi-instance startup assumes Vault KV-v2 CAS support, matching existing local Vault bootstrap.

### Architecture checkpoint summary

- Privacy boundaries preserved: only platform signing secret handling changed; no content access/logging added.
- Ownership rules preserved: no route authorization or owner/team checks changed.
- Deletion semantics preserved: no lifecycle/delete paths changed.
- Provider rules preserved: no provider credential semantics changed.
- Structured-note contract preserved: no generated-document behavior changed.

## 2026-05-04 Startup Security Env Docs

### Scope

- Added explicit README/setup guidance for cookie/CSRF startup guards.
- Added `CSRF_SECRET` to `.env.example` for local development.

### Checklist

- Code complete: docs/env example only.
- Tests added/updated: not needed for documentation-only clarification.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `.env.example`: adds CSRF secret override/reference variables.
- `README.md`, `docs/setup.md`, `docs/progress.md`: document local vs production settings.

### Tests

- Not run; documentation/env example only.

### Documentation

- README and setup docs now explain `APP_ENV`, `COOKIE_SECURE_MODE`, and `CSRF_SECRET` startup failures.

### Risks / assumptions

- Local/test can rely on the development-only fallback; production must use Vault auto-bootstrap or a strong explicit secret.

### Architecture checkpoint summary

- Privacy boundaries preserved: documentation-only change.
- Ownership rules preserved: no auth scope code changed.
- Deletion semantics preserved: no lifecycle code changed.
- Provider rules preserved: no provider behavior changed.
- Structured-note contract preserved: no generated-document behavior changed.

## 2026-05-04 Cookie And CSRF Hardening

### Scope

- Added production startup guards for secure cookies and CSRF secrets.
- Added HTTPS-only HSTS plus baseline response security headers.
- Replaced plain CSRF comparison with HMAC-signed session-bound and anonymous pre-login CSRF tokens.
- Required same-origin `Origin`/`Referer` for unsafe cookie-backed requests.
- Rotated CSRF on session creation/rotation and cleared CSRF/trusted-device cookies on logout.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/cookie_security.py`: production secure-cookie guard and shared environment helper.
- `app/services/csrf.py`: signed CSRF token creation and verification.
- `app/main.py`: startup guards, security headers, CSRF middleware/dependencies, session CSRF helpers.
- `app/routes/api_routes.py`, `app/routes/web_pages.py`: logout now clears trusted-device cookies via existing helpers.
- `app/api_route_audit.py`, `tests/conftest.py`: route-audit/test clients now send signed CSRF and same-origin headers.
- `tests/test_api.py`, `tests/test_cookie_csrf_security.py`: CSRF/security regression coverage.
- `docs/security.md`, `docs/testing.md`, `docs/progress.md`: document hardened behavior and tests.

### Tests

- Added production guard tests for secure cookies and CSRF secrets.
- Added HSTS/security-header tests for HTTPS vs HTTP.
- Added anonymous browser-login CSRF test.
- Updated API CSRF tests for signed tokens and same-origin headers.
- Added cross-origin rejection and stale session-bound token tests.

### Documentation

- Updated security and testing docs for production guards, headers, signed CSRF, anonymous pre-login CSRF, origin checks, and logout cleanup.

### Risks / assumptions

- `openscribe_csrf` remains JavaScript-readable by design so existing `csrfFetch` and form helpers keep working.
- Same-origin checks rely on trusted proxy headers (`X-Forwarded-Proto`/`X-Forwarded-Host`) matching deployment proxy configuration.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note/prompt content access or logging changed.
- Ownership rules preserved: route auth/owner/team/admin checks unchanged; CSRF only gates browser request authenticity.
- Deletion semantics preserved: no retention, cascade, or hard-delete path changed.
- Provider rules preserved: no STT/LLM/de-identification provider config, secret, or fallback behavior changed.
- Structured-note contract preserved: no EMIS/generated-document JSON behavior changed.

## 2026-05-04 Server-Owned Retention Policy

### Scope

- Removed client-controlled transcript retention from public create/start schemas.
- Made new transcript roots always snapshot `owner.team.default_retention_days`.
- Added explicit min/max validation for system-admin team retention policy creation.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: verification result recorded in final response.

### Files changed

- `app/schemas/transcripts.py`: remove public create/start retention input.
- `app/services/transcripts.py`: apply team retention policy server-side only.
- `app/services/admin.py`: validate team default retention bounds.
- `tests/test_api.py`: cover API/service retention behavior and team retention bounds.
- `docs/api.md`, `docs/security.md`, `docs/testing.md`, `docs/progress.md`: document server-owned retention behavior and coverage.

### Tests

- Added API coverage that transcript start/create ignore client retention override and return team default snapshot.
- Added service coverage that transcript start applies team default retention.
- Added patch coverage that transcript update cannot extend retention.
- Added system-admin team-create coverage that excessive retention returns `business_rule_violation`.

### Documentation

- Updated API, security, testing, and progress docs for server-owned retention policy.

### Risks / assumptions

- No DB max check added because `MAX_RETENTION_DAYS` is environment-configurable.
- Existing transcript retention snapshots are not migrated or altered.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content visibility or logging changed.
- Ownership rules preserved: transcript routes remain owner-only and team creation remains system-admin-only.
- Deletion semantics preserved: new retention snapshots are shorter/controlled by team policy; no cascade/delete path changed.
- Provider rules preserved: no STT/LLM/de-identification provider behavior changed.
- Structured-note contract preserved: no EMIS/generated-document behavior changed.

## 2026-05-04 Break-Glass Review Fixes

### Scope

- Added route-level throttling to break-glass recovery TOTP submission paths.
- Stopped trusting `X-Forwarded-For` for persisted security audit IPs unless explicitly enabled.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: local environment lacks `pytest`, so focused tests could not run here.

### Files changed

- `app/routes/api_routes.py`, `app/routes/web_team_management.py`, `app/routes/web_admin.py`: apply existing MFA limiter to break-glass routes.
- `app/services/security_audit.py`: gate forwarded audit IP trust behind `AUDIT_TRUST_X_FORWARDED_FOR`.
- `tests/test_auth_email.py`: cover break-glass throttling and forwarded-IP behavior.
- `docs/auth.md`, `docs/security.md`, `docs/progress.md`: document throttling and audit IP trust policy.

### Tests

- Added coverage for break-glass rate limiting after repeated invalid TOTP attempts.
- Added coverage that audit IP ignores `X-Forwarded-For` by default and honors it only when explicitly enabled.

### Documentation

- Updated auth brute-force controls and security audit IP guidance.

### Risks / assumptions

- `AUDIT_TRUST_X_FORWARDED_FOR=true` assumes upstream proxy strips client-supplied forwarded headers.
- Break-glass currently shares the existing MFA limiter bucket; if operators need stricter policy, add a dedicated break-glass limiter later.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content access or logging added.
- Ownership rules preserved: existing manager/admin scope checks unchanged.
- Deletion semantics preserved: no delete, retention, or cascade behavior changed.
- Provider rules preserved: no provider config, secret, or fallback behavior changed.
- Structured-note contract preserved: no EMIS/generated-document behavior changed.

## 2026-05-03 Regression Review Fixes

### Scope

- Documented the required local/test environment guard for `MAIL_TRANSPORT=stdout`.
- Updated `.env.example` with `APP_ENV=local` so local stdout mail remains valid.
- Updated stale transcribe asset-version assertions after the API CSRF cache-key bump.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: broader pre-existing suite failures still remain outside this targeted fix.

### Files changed

- `.env.example`: adds local app environment default.
- `README.md`, `docs/setup.md`, `docs/account_recovery_brief.md`: document stdout mail environment requirement.
- `tests/test_admin_ui.py`: updates stale transcribe module cache-key assertions.
- `docs/progress.md`: records this fix.

### Tests

- Targeted tests run after change; see final response for result.

### Documentation

- Updated local setup and recovery docs for stdout mail guard.

### Risks / assumptions

- `APP_ENV=local` in `.env.example` is development-only guidance; production deployments should set production-like environment values explicitly.

### Architecture checkpoint summary

- Privacy boundaries preserved: no content access or logging behavior changed.
- Ownership rules preserved: no route authorization behavior changed.
- Deletion semantics preserved: no lifecycle/delete code changed.
- Provider rules preserved: no STT/LLM/de-identification provider behavior changed.
- Structured-note contract preserved: no EMIS/generated-document contract changed.

## 2026-05-02 API CSRF Hardening

### Scope

- Required CSRF verification for unsafe cookie-backed `/api/v1` requests.
- Added shared frontend `csrfFetch` helper that sends `X-CSRF-Token` from `openscribe_csrf`.
- Updated transcribe and smart-phrase API mutations to use `csrfFetch`.
- Preserved unauthenticated public endpoint compatibility when no auth/trusted-device cookie exists.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none known.

### Files changed

- `app/main.py`: adds `/api/v1` CSRF dependency for unsafe requests carrying session/trusted-device cookies.
- `app/templates/_csrf_script.html`: installs a same-origin unsafe `/api/v1` fetch wrapper for legacy inline pages.
- `app/static/js/csrf.js`: adds cookie/header helper and same-origin `/api/v1`-scoped `csrfFetch`.
- `app/static/js/transcribe/actions.js`, `app/static/js/transcribe/app.js`, `app/static/js/transcribe/media.js`: unsafe API mutations now send CSRF header.
- `app/static/js/home/smart-phrases.js`, `app/templates/home.html`: smart-phrase mutations now use module-based `csrfFetch`.
- `app/templates/transcribe/_shell_extras.html`: bumps transcribe module cache key.
- `app/api_route_audit.py`: sends CSRF for cookie-backed unsafe audit probes so auth/role expectations remain isolated.
- `tests/conftest.py`, `tests/test_api.py`, `tests/test_admin_ui.py`, `tests/test_csrf_browser.py`: adds authenticated API, static, and optional browser CSRF regression coverage.
- `docs/security.md`, `docs/testing.md`, `docs/progress.md`: documents API CSRF behavior and browser test setup.

### Tests

- Added coverage for missing, matching, and mismatched CSRF on authenticated unsafe API requests.
- Added coverage that safe authenticated API requests do not require CSRF.
- Added coverage that public login remains callable without CSRF when unauthenticated.
- Added coverage that public unsafe endpoints require CSRF when an auth cookie exists.
- Added static coverage that shared `csrfFetch` handles `Request` inputs and does not attach CSRF outside same-origin `/api/v1`.
- Added optional Playwright browser coverage that opens `/transcribe`, clicks the rendered new-consultation control, and asserts `POST /api/v1/transcripts/start` carries `X-CSRF-Token`.

### Documentation

- Updated security/testing docs and this progress note.

### Risks / assumptions

- `openscribe_csrf` remains readable by JavaScript by design.
- `csrfFetch` does not set default `Content-Type`, preserving multipart `FormData` upload boundaries.
- `csrfFetch` and the legacy wrapper only attach CSRF to same-origin `/api/v1` unsafe requests.
- Anonymous unsafe requests without cookie-backed authority fall through to existing route auth/public handling.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note content visibility or logging changed.
- Ownership rules preserved: existing authenticated route dependencies still enforce owner/team/admin rules.
- Deletion semantics preserved: no cascade or retention behavior changed.
- Provider rules preserved: no provider resolution, credential, or fallback behavior changed.
- Structured-note contract preserved: no generated-document or EMIS JSON schema behavior changed.

## 2026-05-02 Celery Audio Payload Hardening

### Scope

- Removed raw audio from Celery transcript-ingestion task payloads; tasks now carry only `job_id`.
- Worker loads queued audio from `source_audio_vault_ref` and clears source audio after successful processing.
- Live chunks and whole-file retries now use the same Vault-backed queued-source path.
- Legacy queued `audio_b64` Celery messages remain accepted during rollout and are moved into Vault-backed source storage before processing.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: legacy `source_audio_blob` column remains for deployment safety; drop in later migration after confirming no deployed workers or failed jobs rely on it.

### Files changed

- `app/tasks.py`: remove audio/base64 from new transcript ingestion task payloads while accepting legacy queued payloads during rollout.
- `app/services/transcripts.py`: add queued-source reader, store live chunks in Vault-backed source storage, read worker audio by job ref, clear source on success after DB commit, and preserve legacy blob retry fallback.
- `app/routes/web_transcribe.py`, `app/routes/api_routes.py`: enqueue transcript ingestion by `job_id` only.
- `tests/conftest.py`, `tests/test_api.py`, `tests/test_admin_ui.py`: update stubs and regression coverage for job-id-only enqueue and Vault-backed source audio.
- `docs/testing.md`, `docs/transcript-capture.md`, `docs/progress.md`: document hardened payload behavior.

### Tests

- `python3 -m py_compile app/tasks.py app/services/transcripts.py app/routes/web_transcribe.py app/routes/api_routes.py tests/conftest.py tests/test_api.py tests/test_admin_ui.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py::test_enqueue_transcript_ingestion_job_does_not_send_audio tests/test_api.py::test_legacy_transcript_ingestion_task_payload_is_accepted tests/test_api.py::test_transcript_detail_includes_latest_ingestion_failure tests/test_api.py::test_live_audio_chunk_upload_queues_owner_job tests/test_api.py::test_processing_live_audio_chunk_jobs_applies_text_in_sequence tests/test_api.py::test_processing_transcript_ingestion_job_does_not_revive_midflight_failed_job tests/test_api.py::test_audio_file_upload_queues_job_for_whole_file_mode tests/test_api.py::test_retry_audio_file_route_requeues_failed_blob_for_owner tests/test_api.py::test_retry_audio_file_enqueue_failure_keeps_retry_source_available tests/test_api.py::test_processing_audio_file_job_appends_transcript_draft_and_marks_ready tests/test_api.py::test_processing_audio_file_job_keeps_vault_ref_when_cleanup_delete_fails tests/test_api.py::test_audio_file_job_uses_snapshotted_stt_selection_after_team_selection_changes tests/test_api.py::test_processing_audio_file_job_fails_when_normalized_duration_exceeds_limit tests/test_api.py::test_processing_audio_file_job_marks_failed_cleanly_when_stt_secret_is_missing`: passed, 14 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py::test_user_transcribe_page_shows_specific_ingestion_failure_message tests/test_admin_ui.py::test_user_can_retry_failed_file_transcription_from_browser`: passed, 2 tests.
- `git diff --check`: passed.

### Documentation

- Updated testing/transcript-capture docs and this progress note.

### Risks / assumptions

- Existing failed jobs with only legacy `source_audio_blob` remain retryable until the cleanup migration removes the fallback.
- `source_audio_blob` remains in schema for a safe rollout and later cleanup migration.

### Architecture checkpoint summary

- Privacy boundaries preserved: new Redis/Celery tasks no longer carry consultation audio; legacy queued messages are migrated into Vault-backed storage when processed.
- Ownership rules preserved: upload/retry still uses owner-scoped transcript services.
- Deletion semantics preserved: source refs are cleared after success and during transcript/user deletion; failed jobs keep source refs for retry.
- Provider rules preserved: STT snapshot and provider resolution behavior unchanged.
- Structured-note contract preserved: no EMIS/generated-document JSON behavior changed.

## 2026-05-01 Clinical NLP Status and Refresh

### Scope

- Clinical NLP now reruns for the same transcript version when the selected provider was edited after a previous successful clinical run, preventing stale zero-result runs from blocking updated endpoint config.
- The transcribe workspace now returns and renders clinical NLP status/count separately from redaction status.
- The review sidebar now shows whether clinical NLP has not run, failed, or completed with a zero/non-zero count.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: pytest still blocked locally by database connection failure.

### Files changed

- `app/services/clinical_nlp.py`: only reuses existing clinical runs when they are newer than the provider configuration.
- `app/web/transcribe_workspace.py`, `app/schemas/workspace.py`, `app/templates/transcribe/_workspace.html`, `app/templates/transcribe/_shell_extras.html`, `app/static/js/transcribe/app.js`: expose and render owner-scoped clinical NLP status.
- `tests/test_api.py`, `tests/test_admin_ui.py`: add stale-run rerun and clinical-status wiring coverage.
- `docs/api.md`, `docs/testing.md`, `docs/progress.md`: document clinical NLP status and rerun behavior.

### Tests

- Added service/API coverage for rerunning a stale zero-result clinical run after provider config update.
- Added workspace/API and static UI coverage for clinical NLP status payload and rendering hooks.

### Documentation

- Updated API/testing docs and this progress note.

### Risks / assumptions

- Existing historical clinical runs are preserved; the latest run is used for display.
- Provider `updated_at` is the freshness boundary for config changes.

### Architecture checkpoint summary

- Privacy boundaries preserved: only status/count metadata and owner-scoped clinical rows are exposed through the existing owner workspace.
- Ownership rules preserved: workspace and clinical entities remain scoped by transcript owner and team.
- Deletion semantics preserved: clinical runs remain transcript-derived children under existing cascades.
- Provider rules preserved: active team clinical NLP selection, assignment, redacted/unredacted policy, and HTTPS/local rules are unchanged.
- Structured-note contract preserved: no EMIS/generated-document JSON behavior changed.

## 2026-05-01 Copy Review Content Freshness

### Scope

- Revoked generated-note copy review when the rendered copyable note text changes after the user has reviewed it.
- Kept structured review invalidation section-scoped, so editing one section does not invalidate unrelated reviewed sections.
- Added scroll/resize bottom checks alongside `IntersectionObserver` so tall note panels can unlock once their bottom reaches the viewport.
- Restored the one-column transcript/PII review layout below 1180px even when the dictation panel is closed.
- Restored unrelated transcript/PII/sidebar/follow-up static regression assertions and removed root scratch patch notes from the worktree.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: browser automation not run; static regression covers fingerprint invalidation and tall-panel scroll wiring.

### Files changed

- `app/static/js/transcribe/structured.js`: tracks per-section/freeform copy-review fingerprints, revokes stale review after text changes, and checks viewport bottom reach on scroll/resize.
- `app/templates/transcribe/_head_assets.html`: adds a matching mobile override for the closed-dictation transcript review grid selector.
- `tests/test_admin_ui.py`: adds static regression coverage for content freshness, tall-panel observer settings, and the closed-dictation mobile grid override while preserving existing transcript/PII/sidebar/follow-up checks.
- `docs/testing.md`, `docs/transcribe_brief.md`, `docs/transcript-capture.md`, `docs/progress.md`: document updated copy-review behavior.

### Tests

- Added static UI regression coverage for copy-review content fingerprints, review invalidation copy, scroll listener wiring, non-full intersection threshold, and the closed-dictation mobile grid override.

### Documentation

- Updated testing and transcript-capture docs to state that changed copyable note text requires review again.

### Risks / assumptions

- Checkbox-only selection changes do not revoke review because the rendered note text has not changed.
- Whitespace-only placeholder rows are ignored for review freshness because they are not copyable note content.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript or generated-note content is logged or exposed beyond the existing owner workspace DOM.
- Ownership rules preserved: no owner/team access checks changed.
- Deletion semantics preserved: no deletion or cascade behavior changed.
- Provider rules preserved: no STT, LLM, or de-identification provider path changed.
- Structured-note contract preserved: EMIS section keys and generated-document JSON shape unchanged.

## 2026-05-01 Blank Note Line Reorder Guard

### Scope

- Prevented blank structured/freeform note lines in `/transcribe` from being moved by drag handle or `Alt+Arrow` keyboard reorder.
- Consumed blocked `Alt+Arrow` shortcuts on blank rows before returning so browser history navigation cannot steal focus from the workspace.
- Disabled and hid the reorder handle while a line has no non-whitespace text.
- Restored a blank row to its original position if a stale drag interaction starts before the handle state updates.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: browser automation not run; static regression covers JS guards, blocked shortcut consumption, handle state, and cache keys.

### Files changed

- `app/static/js/transcribe/reorder.js`: blocks blank-row keyboard moves, consumes blocked shortcuts, and restores blocked drag attempts.
- `app/static/js/transcribe/structured.js`: marks blank rows and disables their drag handles.
- `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: bump module cache keys.
- `app/templates/transcribe/_head_assets.html`: hides disabled blank-row drag handles.
- `tests/test_admin_ui.py`: adds static regression for blank-line reorder guard.
- `docs/testing.md`, `docs/transcribe_brief.md`, `docs/progress.md`: document expected behavior.

### Tests

- Added static UI regression coverage for blank-row reorder blocking, blocked shortcut consumption, and cache busting.

### Documentation

- Updated transcribe behavior notes, testing notes, and this progress log.

### Risks / assumptions

- Assumes blank placeholder rows should remain available for typing but should not be reorderable until they contain text.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note content access path changed.
- Ownership rules preserved: no owner/team checks changed.
- Deletion semantics preserved: no deletion or cascade path changed.
- Provider rules preserved: no STT/LLM/de-identification resolution changed.
- Structured-note contract preserved: EMIS keys and generated-document JSON/content contracts unchanged.

## 2026-05-01 Post-Consultation Dictation CTA

### Scope

- Added a global post-consultation dictation CTA beside the main consultation controls.
- Made the dictation panel collapsible from the right-side transcript workspace.
- CTA reveals the dictation panel and highlights/focuses the live dictation record button without starting recording.
- Kept live dictation visually primary and moved audio upload behind a secondary disclosure.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: no browser automation added; static template/JS regression coverage protects the expected selectors and no-auto-record behavior.

### Files changed

- `app/templates/transcribe/_workspace.html`: adds global dictation CTA, collapsible dictation panel affordance, and live-first copy.
- `app/templates/transcribe/_head_assets.html`: adds dictation CTA/panel/highlight styling.
- `app/static/js/transcribe/app.js`: adds CTA open/collapse behavior, record-button highlight, and dictation availability sync.
- `app/templates/transcribe/_shell_extras.html`: bumps transcribe app module cache key.
- `app/templates/transcribe/_shell_extras.html`: bumps transcribe app module cache key again after the initialization-order fix so browsers do not keep the broken module.
- `tests/test_admin_ui.py`: adds static regression tests for markup, live-first ordering, no-auto-record click behavior, and cache key.
- `docs/feature_todo.md`, `docs/transcript-capture.md`, `docs/progress.md`: documents the completed UI slice.

### Tests

- Added template/static JS checks for the global CTA, unavailable copy, collapsible panel, live-first ordering, and no automatic recording start.

### Documentation

- Updated feature checklist and transcript capture UX notes.

### Risks / assumptions

- Assumes existing workspace/session state is sufficient for panel open/close behavior and no new DB-backed preference is needed.
- Assumes upload should remain available but secondary to live microphone dictation.

### Architecture checkpoint summary

- Privacy boundaries preserved: no new content endpoint or admin/leader content view was added.
- Ownership rules preserved: existing owner-only dictation APIs remain the only dictation persistence path.
- Deletion semantics preserved: no transcript-root cascade or lifecycle behavior changed.
- Provider rules preserved: existing dictation STT selection/availability state is reused with no fallback change.
- Structured-note contract preserved: generated-note JSON and EMIS section behavior are unchanged.

## 2026-04-30 Editor Smart Phrases And Reordering

### Scope

- Added personal smart phrase CRUD/API plus default `CESRF` seed for normal team users.
- Added slash-trigger expansion to structured/freeform note lines and line reordering by drag handle or `Alt+Arrow` keys.
- Added home settings UI for creating, editing, duplicating, deleting, and searching personal smart phrases.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: local Sortable-compatible vendor shim is committed because package fetch is unavailable in this workspace.

### Files changed

- `app/models.py`, `alembic/versions/20260430_001_add_smart_phrases.py`: add smart phrase persistence.
- `app/schemas/smart_phrases.py`, `app/services/smart_phrases.py`, `app/routes/api_routes.py`: add owner-only smart phrase API.
- `app/api_route_audit.py`: cover smart phrase routes in auth audit manifest.
- `app/web/presentation.py`, `app/web/transcribe_workspace.py`, `app/schemas/workspace.py`: expose smart phrases to home/transcribe.
- `app/templates/home.html`, `app/static/js/home/smart-phrases.js`: add settings UI.
- `app/templates/transcribe/_head_assets.html`, `app/templates/transcribe/_shell_extras.html`, `app/static/js/transcribe/*.js`, `app/static/vendor/sortable/Sortable.min.js`: add expansion and reordering.
- `tests/test_smart_phrases_api.py`, `tests/test_migrations.py`: add API and schema coverage.
- `docs/api.md`, `docs/editor-smart-phrases.md`, `docs/progress.md`: document behavior and endpoints.

### Tests

- Added coverage for CRUD, ownership isolation, validation, usage counters, hard delete, default creation, and migration constraints.

### Documentation

- Added smart phrase feature doc and API endpoint list.

### Risks / assumptions

- Smart phrases are treated as personal configuration; users should not store transcript-derived content there unless deliberately making it their own reusable private text.

### Architecture checkpoint summary

- Privacy boundaries preserved: smart phrases are owner-only and not team-readable.
- Ownership rules preserved: all service queries filter by `owner_user_id`; admins are blocked.
- Deletion semantics preserved: phrase delete is hard delete; user delete cascades phrase rows.
- Provider rules preserved: no provider selection, credentials, or fallback behavior changed.
- Structured-note contract preserved: EMIS section keys/output JSON unchanged; reordering only changes editor line order.

## 2026-04-30 Built-In Team Asset Seeding

### Scope

- Added hard-coded starter team assets so setup creates a structured EMIS template plus follow-up and referral quick actions when missing.
- Kept previous default-asset library behavior: admin-created teams still receive active default assets, now with built-ins ensured first.
- Updated dev setup seed so the reusable dev team gets the built-in team assets even though the dev seed does not use the admin team-creation service.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: built-in content is intentionally minimal starter wording and may need clinical/editorial review.

### Files changed

- `app/services/default_assets.py`: added built-in starter asset definitions and idempotent default/team seed helpers.
- `app/services/admin.py`: ensures built-in defaults before seeding newly created teams.
- `scripts/seed_dev_accounts.py`: seeds built-in assets into the dev team after leader/user setup.
- `tests/test_admin_ui.py`: covers built-in default/team seeding and idempotency.
- `docs/setup.md`: documents dev-seeded starter assets.
- `docs/progress.md`: records this change.

### Tests

- Added focused regression coverage for admin-created team built-ins and direct team seed idempotency.

### Documentation

- Updated setup docs to note hard-coded dev team starter assets.

### Risks / assumptions

- Assumes starter assets are safe as non-content configuration and do not contain transcript-derived text.
- Assumes recreating missing built-ins during team setup is desired even if an operator removed them from the default library.

### Architecture checkpoint summary

- Privacy boundaries preserved: built-ins contain no transcript-derived content and do not affect generated documents.
- Ownership rules preserved: default library assets require system admin; seeded team assets are team-scoped with no owner user.
- Deletion semantics preserved: transcript-root/user deletion paths unchanged; deleted team assets can be recreated only by setup/team-seed paths.
- Provider rules preserved: no STT/LLM/de-identification provider config changed.
- Structured-note contract preserved: built-in structured template uses allowed EMIS section keys only and omits no schema fields.

## 2026-04-30 README Resend Setup Walkthrough

### Scope

- Added README walkthrough for configuring Resend transactional email for an OpenScribe instance.

### Checklist

- Code complete: docs-only change complete.
- Tests added/updated: not run; docs-only walkthrough change.
- Docs added/updated: yes.
- Open issues: production deployments still need Vault/deployment secret-store wiring for `RESEND_API_KEY_VAULT_REF` where applicable.

### Files changed

- `README.md`: added Resend setup, env example, smoke test, and troubleshooting notes.
- `docs/progress.md`: records this documentation change.

### Tests

- Not run; no executable code changed.

### Documentation

- Updated README with user-facing Resend setup instructions.

### Risks / assumptions

- Assumes operators can create a Resend API key and verify DNS outside OpenScribe.
- Assumes local plaintext `.env` key guidance remains limited to local development only.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content behavior or logs changed.
- Ownership rules preserved: no account/team ownership code changed.
- Deletion semantics preserved: no lifecycle/delete paths changed.
- Provider rules preserved: Resend remains instance-level mail infrastructure, not team STT/LLM/de-identification provider config.
- Structured-note contract preserved: no generated-note behavior changed.

## 2026-04-30 Team Member Menu Usability Fix

### Scope

- Fixed leader team-management member actions menu so it renders above the member list/background, closes on outside click/Escape, and auto-closes after 3.5 seconds when no longer hovered or focused.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/templates/home.html`: adjusted member-list/menu stacking and added menu lifecycle JavaScript.
- `tests/test_admin_ui.py`: added regression assertions for menu stacking and close behavior script.
- `docs/home_brief.md`: documented member actions menu behavior.
- `docs/progress.md`: records this fix.

### Tests

- Added focused HTML regression coverage for visible overflow, elevated open-menu z-index, outside-click close, and idle timeout constant.

### Documentation

- Updated home brief team member controls section.

### Risks / assumptions

- Assumes 3.5 seconds is acceptable for "a few seconds" and focus should keep the menu open for keyboard users.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content paths or logs changed.
- Ownership rules preserved: leader actions and same-team enforcement remain in existing routes/services.
- Deletion semantics preserved: delete form and confirmation text unchanged.
- Provider rules preserved: no provider selection/config behavior changed.
- Structured-note contract preserved: no generated-note behavior changed.

## 2026-04-29 Dev Startup Env Export Fix

### Scope

- Fixed `./start-dev.sh` so resolved dev defaults are exported before child Python port checks and FastAPI startup.

### Checklist

- Code complete: yes.
- Tests added/updated: shell syntax plus focused env-contract check.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `start-dev.sh`: exports `APP_HOST`, `APP_PORT`, dev flags, and resolved `APP_BIND_HOST` after applying defaults.
- `docs/setup.md`: documents derived default export behavior.
- `docs/progress.md`: records this fix.

### Tests

- `bash -n start-dev.sh`: passed.
- Focused Python env-contract check for `APP_BIND_HOST`/`APP_PORT`: passed.

### Documentation

- Updated setup docs for startup default export behavior.

### Risks / assumptions

- Assumes `.env` may omit optional defaults listed in setup docs.
- No service exposure policy changed; existing exposure checks still run before app startup.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content paths or logs changed.
- Ownership rules preserved: no auth, team, or owner scoping changed.
- Deletion semantics preserved: no lifecycle/delete paths changed.
- Provider rules preserved: Vault/provider startup remains unchanged; only exported env visibility changed.
- Structured-note contract preserved: no generated-note behavior changed.

## 2026-04-29 Resend Account Email First Slice

### Scope

- Planned Resend-backed transactional email for account activation/setup, password reset, and manager-assisted recovery.
- Kept email as instance-level platform infrastructure rather than team-scoped provider config.
- Updated account recovery direction from SMTP-first to Resend-first with provider-neutral mailer boundaries.
- Added first mail-service slice with disabled/stdout/resend config modes, stdout local delivery, and direct Resend Email API delivery.
- Kept no-Resend installs on the current manual temporary-password setup path.
- Added an operator test script so Resend credentials/domain can be verified before account activation/reset flows are wired.
- Added account setup links, self-service password reset, manager password/MFA recovery actions, and browser/API routes.

### Checklist

- Code complete: account activation, password reset, and manager recovery first pass complete.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: durable outbox/worker retry, Resend webhook delivery status, production UI/path for writing Resend API key into Vault.

### Files changed

- `.env.example`: added mail transport and Resend setup variables.
- `alembic/versions/c0d1e2f3a4b6_add_auth_email_tokens.py`: added hashed auth email token table.
- `app/models.py`: added auth email token purpose enum/model and user relationships.
- `app/services/auth_email.py`: added setup/reset token issuance, email sends, token confirmation, and manager recovery helpers.
- `app/services/mail.py`: added mail config loading/validation plus disabled, stdout, and Resend transports.
- `app/services/admin.py`: preserves auth-email token actor metadata during manager deletion.
- `app/services/vault.py`: added generic mail Resend API key read helper for Vault refs.
- `app/routes/api_routes.py`: added auth activation/reset and manager recovery API routes.
- `app/routes/web_pages.py`, `app/routes/web_team_management.py`, `app/routes/web_admin.py`: added browser recovery and manager actions.
- `app/templates/login.html`, `app/templates/password_reset_request.html`, `app/templates/password_reset_confirm.html`: added password reset/setup browser UI.
- `app/schemas/auth.py`, `app/schemas/__init__.py`: added request/response schemas.
- `app/api_route_audit.py`: added auth audit coverage for new routes and missing clinical NLP routes.
- `scripts/send_test_email.py`: added operator smoke test for the configured mail transport.
- `tests/test_mail_service.py`: added focused mail config, stdout delivery, and Resend adapter tests.
- `tests/test_auth_email.py`: added password reset, activation, and manager recovery authorization tests.
- `tests/test_migrations.py`: added auth email token table/schema assertions.
- `docs/api.md`, `docs/auth.md`, `docs/security.md`: documented activation/recovery API and security behavior.
- `docs/feature_todo.md`: added phased Resend transactional email plan with workflow checklist and architecture checkpoints.
- `docs/account_recovery_brief.md`: updated mail transport guidance, setup email model, and Resend send behavior.
- `docs/setup.md`: documented mail transport modes and setup variables.
- `docs/testing.md`: documented mail-service coverage.
- `docs/progress.md`: recorded this first slice.

### Tests

- `python3 -m py_compile app/models.py app/services/auth_email.py app/services/admin.py app/services/mail.py app/services/vault.py app/schemas/auth.py app/schemas/__init__.py app/main.py app/routes/api_routes.py app/routes/web_pages.py app/routes/web_team_management.py app/routes/web_admin.py app/api_route_audit.py tests/test_auth_email.py tests/test_mail_service.py tests/test_migrations.py alembic/versions/c0d1e2f3a4b6_add_auth_email_tokens.py scripts/send_test_email.py`: passed.
- `.venv/bin/pytest -q tests/test_mail_service.py tests/test_auth_email.py tests/test_api_route_audit.py`: passed, 15 tests.
- `.venv/bin/pytest -q tests/test_migrations.py -k "expected_schema"`: passed, 1 test.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "leader_can_suspend_reactivate_and_delete_team_user_from_home or admin_page_uses_flat_sidebar_workspace_layout"`: passed, 1 test.
- `git diff --check`: passed.

### Documentation

- Added Resend account email plan to feature todo.
- Updated account recovery brief so it no longer points at SMTP as the first transport.
- Documented `disabled`, `stdout`, and `resend` modes, with `disabled` preserving current manual setup.
- Documented `scripts/send_test_email.py --to you@example.com` for Resend smoke testing.
- Documented setup/reset browser/API flows and manager recovery actions.

### Risks / assumptions

- Assumes local-auth remains current recovery authority; Auth0 accounts should still use Auth0-owned recovery if added later.
- Assumes Resend API key follows Vault-backed secret handling in production.
- Resend webhook signature verification details need confirmation during implementation before any webhook endpoint ships.
- Email is sent synchronously for this first pass; durable outbox/worker retry remains the next hardening slice.

### Architecture checkpoint summary

- Privacy boundaries preserved: transactional emails may carry auth links only, never transcript or note content.
- Ownership rules preserved: activation/reset affects authentication state only and gives no manager content access.
- Deletion semantics preserved: auth tokens cascade on user delete; manager actor references are reassigned before user deletion.
- Provider rules preserved: Resend is platform mail infrastructure, not team STT/LLM/de-identification provider config; no team leader mail-secret UI added.
- Structured-note contract preserved: no EMIS or generated-document JSON behavior changed.

## 2026-04-29 Review Regression Fixes

### Scope

- Fixed clinical NLP generation when a successful redaction run already exists.
- Preserved duplicate detection for manual PII rows stored with legacy SHA-256 hashes.
- Kept admin provider save errors on the matching STT or LLM sub-tab.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/services/redaction.py`: run missing clinical entity detection on reused redaction runs.
- `app/services/transcripts.py`: add legacy manual PII hash lookup and upgrade matched rows to keyed digest.
- `app/routes/web_admin.py`: pass matching provider tab after STT and LLM save validation errors.
- `tests/test_api.py`: add clinical reuse and legacy manual PII duplicate regressions.
- `tests/test_admin_ui.py`: add provider-tab error regression.
- `docs/progress.md`: record this review fix.

### Tests

- Added API/service coverage for existing redaction reuse creating a clinical entity run.
- Added API coverage for legacy manual PII SHA-256 duplicate matching and hash upgrade.
- Added admin UI coverage for STT/LLM validation errors staying on matching provider tabs.

### Documentation

- Added progress entry here.

### Risks / assumptions

- Reusing a redaction run now commits if clinical detection creates or updates a clinical run, matching the already-committing fresh redaction path.
- Legacy manual PII compatibility is lookup-time migration only; no schema migration required.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content is logged or exposed; clinical detection still uses configured provider rules.
- Ownership rules preserved: manual PII lookup remains scoped to owning user's transcript and entity type.
- Deletion semantics preserved: no retention roots, hard-delete paths, or cascades changed.
- Provider rules preserved: clinical NLP still runs only when a valid assigned team selection exists; admin tabs only affect browser state.
- Structured-note contract preserved: no EMIS keys or generated-note JSON shape changed.

## 2026-04-29 Dev Startup Port Guard

### Scope

- Hardened `./start-dev.sh` when FastAPI port is already occupied.

### Checklist

- Code complete: yes.
- Tests added/updated: shell syntax validation.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `start-dev.sh`: stop more stale FastAPI process patterns, fail fast if `APP_PORT` is occupied, and open Brave on configured port.
- `docs/setup.md`, `docs/progress.md`: document port guard behavior.

### Tests

- `bash -n start-dev.sh`: passed.

### Architecture checkpoint summary

- Schema checkpoint: no schema changes.
- Auth/ownership checkpoint: no auth or ownership behavior changed.
- Lifecycle/deletion checkpoint: dev startup now fails before Celery/Brave when app port is unavailable; no content lifecycle changed.
- Provider/structured-note checkpoints: unchanged.

## 2026-04-28 Clinical NLP Workspace Refresh Fix

### Scope

- Fixed transcribe workspace refresh so newly returned PII/clinical NLP entities render into the right-side review table and transcript highlights after analyse runs.
- Bumped the transcribe app script cache key so browsers load the fixed module.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/static/js/transcribe/app.js`: re-render PII entities from each workspace payload after draft text refresh.
- `app/templates/transcribe/_shell_extras.html`: bump module cache key.
- `tests/test_admin_ui.py`: add regression guard for workspace refresh invoking PII render.
- `docs/testing.md`, `docs/progress.md`: document refresh coverage and daily note.

### Tests

- `python3 -m py_compile tests/test_admin_ui.py`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_workspace_refresh_renders_updated_pii_entities or user_transcribe_page_shows_clinical_entities_in_pii_area"`: passed, 2 tests.
- `git diff --check`: passed.

### Documentation

- Updated transcribe workspace regression notes and progress log.

### Risks / assumptions

- Static JS regression test protects the missing render call; no browser automation was added for this small fix.

### Architecture checkpoint summary

- Privacy boundaries preserved: only owner workspace consumes owner-scoped `active_transcript_pii_entities`; no admin/leader content access added.
- Ownership rules preserved: API/workspace ownership filtering unchanged.
- Deletion semantics preserved: no model or cascade behavior changed.
- Provider rules preserved: clinical NLP selection/resolution unchanged; this only renders returned entities.
- Structured-note contract preserved: no generated document or EMIS JSON behavior changed.

## 2026-04-28 Admin Full Layout Redesign

### Scope

- Updated `/admin` to use redesigned flat workspace sections across providers, defaults, directory, usage, and requests.
- Split provider management into STT, LLM, and de-identification subtabs with active-selection cards and provider metadata cards.
- Kept LLM inspection and de-identification/NLP ping responses on their originating provider subtab instead of resetting to STT.
- Preserved existing backend form routes, return-view routing, secret handling, and destructive action confirmations.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/templates/admin.html`: add redesigned section/card/subtab layout and provider subtab switching JS while keeping existing forms and honoring server-selected provider subtabs.
- `app/web/presentation.py`, `app/routes/web_admin.py`: carry active provider subtab context for LLM and de-identification inspect responses.
- `tests/test_admin_ui.py`: update admin layout assertions for provider subtabs, directory cards, and de-identification management.
- `docs/admin_brief.md`, `docs/testing.md`, `docs/progress.md`: document admin layout and regression coverage.

### Tests

- `python3 -m py_compile tests/test_admin_ui.py`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "admin_page_uses_flat_sidebar_workspace_layout or admin_restyled_preview_route_renders_for_system_admin or admin_providers_panel_renders_deidentification_management or admin_llm_selection_uses_visible_model_tiles_and_default_dropdown"`: passed, 4 tests.
- `python3 -m py_compile app/web/presentation.py app/routes/web_admin.py tests/test_admin_ui.py`: passed.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "deidentification_inspect_does_not_render_bearer_token or inspect_and_save_llm_provider_without_retyping_api_key or admin_restyled_preview_route_renders_for_system_admin"`: passed, 3 tests.

### Documentation

- Added admin layout notes and test coverage note.

### Risks / assumptions

- Provider forms remain inline rather than drawer-based so server-rendered inspect/edit flows and credential handling stay unchanged.

### Architecture checkpoint summary

- Privacy boundaries preserved: admin UI remains metadata-only and does not render transcript or generated note content.
- Ownership rules preserved: no user/team ownership filters or routes changed.
- Deletion semantics preserved: existing delete forms and confirmation prompts remain on same routes.
- Provider rules preserved: Vault-backed secret fields, provider selection, and fallback behavior unchanged.
- Structured-note contract preserved: no template JSON or generated-document behavior changed.

## 2026-04-28 Manual PII Digest and Nested Provider Secret Review Fix

### Scope

- Replaced plain SHA-256 manual PII dedupe hashes with owner-DEK-keyed HMAC digests so low-entropy PII cannot be dictionary-tested from DB contents alone.
- Made de-identification provider `extra_body_json` secret-key validation recursive so nested `token`/`api_key` style fields are rejected instead of persisted raw.

### Checklist

- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/services/content_crypto.py`: add owner-scoped keyed digest helper using the unwrapped owner DEK.
- `app/services/transcripts.py`: use keyed digest helper for manual PII `normalized_value_hash`.
- `app/schemas/deidentification.py`: recursively reject secret-bearing keys in arbitrary body JSON.
- `tests/test_api.py`: cover non-plain-SHA manual PII digests and nested de-identification body secrets.
- `docs/testing.md`, `docs/DatabasePlan.md`, `docs/progress.md`: document keyed digest and nested secret validation coverage.

### Tests

- `python3 -m py_compile app/services/content_crypto.py app/services/transcripts.py app/schemas/deidentification.py tests/test_api.py`: passed.
- `.venv/bin/pytest -q tests/test_api.py -k "owner_can_add_and_delete_manual_pii_entities or deidentification_provider_rejects_secret_headers"`: passed, 2 tests.

### Documentation

- Updated testing coverage, database plan note, and daily progress.

### Risks / assumptions

- Existing manual PII rows created with the old plain hash format will not dedupe against newly keyed digests until re-created; this avoids continuing offline-guessable hashes.

### Architecture checkpoint summary

- Privacy boundaries preserved: manual PII plaintext remains encrypted; dedupe metadata is now non-offline-guessable without owner key material.
- Ownership rules preserved: manual PII remains owner-only and keyed by owner DEK.
- Deletion semantics preserved: no cascade or retention changes; manual PII still lives under transcript root.
- Provider rules preserved: raw de-identification secrets must use Vault-backed bearer-token storage, including nested body fields.
- Structured-note contract preserved: no EMIS or generated-document JSON changes.

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

## 2026-04-30 Auth Onboarding Page Refresh

### Scope

- Refreshed onboarding and password reset browser pages to match the current OpenScribe auth design language.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice

### Files changed

- `app/templates/onboarding.html`: replaced old parchment/red styling with the current DM Sans/Fraunces auth shell, card panels, step rail, mobile layout, and current controls.
- `app/templates/password_reset_request.html`: updated forgot-password page with current auth shell, safer explanatory copy, and responsive actions.
- `app/templates/password_reset_confirm.html`: updated reset/setup confirmation page with current auth shell while preserving token form behavior.
- `tests/test_auth_email.py`: added browser assertions for reset page shell styling and preserved disabled-mail behavior.
- `tests/test_admin_ui.py`: updated onboarding copy assertion and added static shell-style coverage for onboarding/reset templates.
- `docs/testing.md`: noted reset browser shell coverage.
- `docs/progress.md`: added this progress entry.

### Tests

- `tests/test_auth_email.py`: password-reset browser pages render current shell styling and keep reset actions available.
- `tests/test_admin_ui.py`: onboarding flow still renders, TOTP setup remains available, and auth recovery templates keep current style markers.

### Documentation

- Updated `docs/testing.md`.
- Added progress entry here.

### Risks / assumptions

- This is a browser-template-only refresh; no auth state transitions, password reset token semantics, or onboarding endpoints changed.
- The reset confirm template is shared by account activation, so the refreshed shell applies to both password reset and activation setup links.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content access or display paths changed.
- Ownership rules preserved: forms still use existing authenticated onboarding and token-bound reset routes.
- Deletion semantics preserved: no deletion, retention, or cascade behavior changed.
- Provider rules preserved: no STT/LLM/de-identification provider selection or fallback behavior changed.
- Structured-note contract preserved: no EMIS sections or generated-document JSON contract changed.

## 2026-04-30 Password Reset Enumeration Guard

### Scope

- Closed a password-reset enumeration edge case where valid configured mail transport with broken send/config paths could return different API statuses for existing vs missing accounts.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none for this slice

### Files changed

- `app/services/auth_email.py`: validates reset mail config before lookup and returns the generic reset message when send fails after a matching active user is found.
- `tests/test_auth_email.py`: adds regression coverage for misconfigured mail and send failures returning non-enumerable responses.
- `docs/testing.md`: records the new auth-email coverage.
- `docs/progress.md`: adds this progress entry.

### Tests

- `tests/test_auth_email.py`: verifies existing and missing reset requests match during mail misconfiguration and send failure cases.

### Documentation

- Updated `docs/testing.md`.
- Added progress entry here.

### Risks / assumptions

- Send failures after token issuance can still leave an unused reset token, matching previous token issuance behavior, but response shape no longer reveals account presence.

### Architecture checkpoint summary

- Privacy boundaries preserved: reset responses no longer expose account existence through mail failure status differences.
- Ownership rules preserved: token-bound reset and existing user state transitions are unchanged.
- Deletion semantics preserved: auth tokens still use existing user cascade behavior.
- Provider rules preserved: mail remains platform infrastructure; STT/LLM/de-identification provider rules unchanged.
- Structured-note contract preserved: no generated-document behavior changed.

## 2026-04-30 MFA Reset Pending Password Guard

### Scope

- Fixed manager-triggered MFA-only reset so it no longer skips an existing `pending_password_change` onboarding state.
- Cleaned password-auth test/docs wording away from old hash specifics after the Argon2id-only reset.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: focused pytest run is blocked by local Postgres connectivity in this environment

### Files changed

- `app/services/auth_email.py`: preserves `pending_password_change` during MFA-only reset while still clearing MFA/recovery state and revoking trust.
- `tests/test_auth_email.py`: adds regression coverage for MFA-only reset against a user still forced to change password.
- `tests/test_auth_service.py`: updates non-Argon2id rejection/rotation fixtures to avoid old hash-specific data.
- `docs/auth.md`, `docs/security.md`, `docs/setup.md`, `docs/testing.md`: document generic non-Argon2id rotation and MFA-only reset password-change preservation.

### Tests

- Added `tests/test_auth_email.py::test_manager_reset_mfa_preserves_pending_password_change`.
- `python -m py_compile app/services/auth_email.py tests/test_auth_email.py tests/test_auth_service.py` passed.
- `pytest tests/test_auth_email.py tests/test_auth_service.py` could not start because test DB connection failed with `psycopg.OperationalError: connection is bad`.

### Documentation

- Updated auth/security/setup/testing docs.
- Added progress entry here.

### Risks / assumptions

- Assumes MFA-only reset should preserve the password-change gate and still clear prior TOTP/recovery-code state.
- No schema, migration, provider, encryption, or transcript-derived content changes.

### Architecture checkpoint summary

- Privacy boundaries preserved: recovery remains metadata/auth-only and no transcript-derived content paths changed.
- Ownership rules preserved: existing manager same-team/system-admin checks remain in caller path.
- Deletion semantics preserved: no transcript/user deletion or retention behavior changed; MFA/recovery rows still use existing reset cleanup.
- Provider rules preserved: no STT/LLM/de-identification provider selection or fallback behavior changed.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-04-30 Auth Confirm Hardening

### Scope

- Moved password hashing for reset/setup confirmations behind token validation so invalid public tokens cannot trigger Argon2id work.
- Restricted account setup links to users still pending first password setup.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: focused pytest result noted below

### Files changed

- `app/services/auth_email.py`: consumes/validates tokens before hashing new passwords and rejects activation for users not in first-password setup.
- `app/routes/api_routes.py`, `app/routes/web_pages.py`: pass raw new passwords to the service so validation happens before hashing.
- `tests/test_auth_email.py`: adds invalid-token no-hash coverage for API/browser reset confirm and completed-user activation guard coverage.
- `docs/auth.md`, `docs/security.md`, `docs/testing.md`: document confirm hardening and first-time-only setup links.

### Tests

- Added password reset invalid-token and activation-state regressions.
- `python -m py_compile app/services/auth_email.py app/routes/api_routes.py app/routes/web_pages.py tests/test_auth_email.py` passed.
- `pytest tests/test_auth_email.py -q` could not start because test DB connection failed with `psycopg.OperationalError: connection is bad`.

### Documentation

- Updated auth/security/testing docs.
- Added progress entry here.

### Risks / assumptions

- Account activation tokens that were issued before this guard for already-onboarded users are now rejected on use.
- Completed or already-onboarded users must use explicit recovery/reset flows instead of setup links.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content access or display paths changed.
- Ownership rules preserved: manager/system-admin issuance still uses existing manageable-user checks, with stricter activation eligibility.
- Deletion semantics preserved: no user/transcript deletion or cascade behavior changed.
- Provider rules preserved: no STT/LLM/de-identification provider behavior changed.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-04-30 Auth Review Fixes

### Scope

- Fixed activation browser page enum import so valid setup links do not 500.
- Updated invalid-token regression to use schema-valid token text so service token validation is exercised.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: focused pytest run result noted below

### Files changed

- `app/routes/web_pages.py`: imports `UserOnboardingState` used by account activation page guard.
- `tests/test_auth_email.py`: uses a nonexistent token that satisfies schema length validation.
- `docs/progress.md`: records review-fix checkpoint.

### Tests

- `.venv/bin/python -m py_compile app/routes/web_pages.py tests/test_auth_email.py`: passed.
- `.venv/bin/python -m pytest tests/test_auth_email.py::test_password_reset_confirm_rejects_invalid_token_before_hashing -q`: passed, 1 test.
- `.venv/bin/python -m pytest tests/test_auth_email.py::test_account_activation_sets_password_and_creates_onboarding_session tests/test_auth_email.py::test_account_activation_is_restricted_to_first_password_setup -q`: blocked by local Postgres connection with `psycopg.OperationalError: connection is bad`.

### Documentation

- Added progress entry here.

### Risks / assumptions

- Assumes existing auth docs already describe confirm hardening; no user-facing behavior changed beyond crash/test fix.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript-derived content paths changed.
- Ownership rules preserved: activation eligibility check unchanged.
- Deletion semantics preserved: no delete/cascade behavior changed.
- Provider rules preserved: no provider selection or fallback behavior changed.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 API Inspection Upgrade

### Scope

- Added explicit provider credential actions for STT/LLM save flows.
- Changed manual generic STT save verification to test the saved runtime contract with bundled synthetic audio instead of default OpenAPI discovery.
- Removed STT/LLM preserved-token form state and documented STT-only persisted credential status scope.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: full pytest stopped after user said focused suite is sufficient

### Files changed

- `app/schemas/stt.py`, `app/schemas/llm.py`: add request-level `credential_action`.
- `app/services/stt.py`: explicit keep/replace/remove handling and generic REST sample validation.
- `app/services/llm.py`: explicit keep/replace/remove handling for LLM secrets.
- `app/routes/web_admin.py`, `app/web/presentation.py`, `app/templates/admin.html`, `app/templates/admin2.html`: wire browser credential action and remove STT/LLM preserved-token state.
- `tests/test_api.py`: cover explicit secret removal and generic REST saved-contract validation.
- `docs/api.md`, `docs/stt-config.md`, `docs/admin_brief.md`, `docs/security.md`, `docs/testing.md`, `docs/progress.md`: update behavior docs.

### Tests

- Added API coverage for STT secret removal, LLM optional-token secret removal, and generic STT saved-contract validation.
- `.venv/bin/pytest -q tests/test_api.py -k "stt_config or llm_config or generic_stt_save or credential"`: passed, 16 tests.
- `.venv/bin/pytest -q tests/test_admin_ui.py -k "stt_config_before_saving or save_stt_config_after_inspect or save_no_auth_stt_config or inspect_and_save_llm_provider or inspect_and_save_bedrock or inspect_and_save_local_ollama or provider_save_errors"`: passed, 7 tests.
- `.venv/bin/pytest -q`: stopped after user interruption around 52%; no failures seen before stop.

### Documentation

- Documented credential actions, Vault cleanup order, generic STT validation, and LLM/STT status asymmetry.

### Risks / assumptions

- LLM persisted credential status remains intentionally out of scope for this slice; LLM still exposes inspection discovery status only.
- Generic STT save validation sends only bundled synthetic audio, not patient/transcript content.

### Architecture checkpoint summary

- Privacy boundaries preserved: no admin route reads transcript-derived content; provider validation uses metadata and bundled synthetic sample only.
- Ownership rules preserved: provider routes remain system-admin/team-scoped metadata routes.
- Deletion semantics preserved: credential removal clears DB reference before post-commit Vault cleanup.
- Provider rules preserved: STT runtime contract fields stay explicit; OpenAI/Bedrock LLM still require saved bearer credentials.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

## 2026-05-10 Optional Provider Credential Defaults

### Scope

- Fixed admin provider forms so optional-token STT adapters and Ollama LLM default blank saves to `credential_action=keep` instead of `replace`.
- Kept OpenAI Cloud STT, OpenAI Chat, and Bedrock defaults on token replacement for new required-token providers.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/web/presentation.py`, `app/routes/web_admin.py`: derive inspected form credential action from adapter requirements.
- `app/templates/admin.html`, `app/templates/admin2.html`: sync credential action when admins choose optional-token adapters.
- `tests/test_admin_ui.py`: cover optional-token form defaults and template sync.
- `docs/admin_brief.md`, `docs/stt-config.md`, `docs/testing.md`, `docs/progress.md`: document UI default behavior.

### Tests

- Added focused admin UI regressions for optional-token STT and Ollama forms using `keep` on blank token saves.

### Documentation

- Updated provider credential docs and testing notes.

### Risks / assumptions

- Assumes `credential_action=keep` on a new optional-token provider means no saved credential, matching existing service behavior.

### Architecture checkpoint summary

- Privacy boundaries preserved: only provider metadata form behavior changed; no transcript-derived content access.
- Ownership rules preserved: system-admin provider provisioning scope unchanged.
- Deletion semantics preserved: no deletion or cascade paths changed.
- Provider rules preserved: required-token providers still require credentials; optional local/self-hosted providers may save without credentials.
- Structured-note contract preserved: no generated-document or EMIS JSON behavior changed.

# 2026-05-13 Quick Action Context Audio Preview

### Scope

- Added transient quick-action context audio preview. Browser records short audio, backend transcribes through existing dictation STT path, UI appends returned text to quick-action context textarea.
- No new persisted dictation/content type added.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/services/dictations.py`: extracted reusable prompt-context STT helper using owner transcript lookup, upload caps, normalization, duration check, and post-consultation dictation STT purpose.
- `app/routes/api_routes.py`, `app/schemas/dictation.py`, `app/schemas/__init__.py`, `app/main.py`: added quick-action context preview endpoint/response.
- `app/templates/transcribe/_workspace.html`, `app/templates/transcribe/_shell_extras.html`, `app/static/js/transcribe/app.js`, `app/static/js/transcribe/actions.js`: added compact quick-action context recording toggle and textarea append flow.
- `tests/test_api.py`: added owner/non-owner/no-persistence/STT-purpose coverage and tightened quick-action context redaction assertion.
- `docs/api.md`, `docs/transcript-capture.md`: documented transient audio preview behavior.

### Tests

- Focused API tests cover owner access, non-owner rejection, no dictation rows/segments persisted, dictation-purpose STT call, and quick-action context redaction while static quick-action instructions stay unredacted.

### Documentation

- Updated API route list/redaction behavior and transcript-capture workflow notes.

### Risks / assumptions

- Uses existing `post_consultation_dictation` STT selection intentionally; no new enum/config/migration added.

### Architecture checkpoint summary

- Privacy boundaries preserved: STT preview text becomes user-visible quick-action context only; final LLM path still redacts dynamic context.
- Ownership rules preserved: endpoint requires full session and transcript owner.
- Deletion semantics preserved: preview creates no server-side transcript-derived child rows.
- Provider rules preserved: existing dictation STT selection and upload limits reused.
- Structured-note contract preserved: no EMIS/template JSON contract changes.

# 2026-05-14 Transcribe Note Empty-State Fix

- Fixed `/transcribe` editable-note empty guidance so "No note lines yet" hides when structured or freeform note rows already contain content.
- Server render now passes note-row content flags to hide the placeholder before JS runs.
- Generated-output refresh now calls the empty-state sync after structured/freeform rows render or hide.
- Added an explicit `.note-editor-empty-state[hidden]` rule because the component display rule was overriding Tailwind/browser hidden styling.
- Added focused static regression coverage and updated transcribe/testing notes.
- Architecture checkpoint: UI-only owner workspace fix; no transcript visibility, ownership, deletion, encryption-key, provider-resolution, or structured-note JSON contract changes.

## 2026-05-01 Transcribe Copy Review Marker Fix

### Scope

- Removed structured/freeform copy-review sentinel elements so no marker can render at note section bottoms.
- Kept copy-review gating by observing the real structured section cards and freeform panel bottoms.
- Updated keyboard row reordering to skip non-row siblings.

### Checklist

- Code complete: yes
- Tests added/updated: yes
- Docs added/updated: yes
- Open issues: none

### Files changed

- `app/static/js/transcribe/structured.js`: observes section/panel elements directly instead of rendering sentinel markers.
- `app/static/js/transcribe/reorder.js`: skips non-row siblings when moving rows by keyboard.
- `app/static/js/transcribe/app.js`, `app/templates/transcribe/_shell_extras.html`: bump module query strings for updated editor/reorder code and the outer app module.
- `tests/test_admin_ui.py`: adds static regressions for no sentinel markers and keyboard reorder sibling lookup.
- `docs/progress.md`: records this UI fix.

### Tests

- Added static UI regression coverage for no rendered sentinel markers and reorder sibling skipping.

### Documentation

- Added progress entry here.

### Risks / assumptions

- Assumes section/panel bottom visibility is the intended copy-review boundary.

### Architecture checkpoint summary

- Privacy boundaries preserved: no transcript/note content access or display permissions changed.
- Ownership rules preserved: no owner/team checks changed.
- Deletion semantics preserved: no deletion or cascade path changed.
- Provider rules preserved: no STT/LLM/de-identification resolution changed.
- Structured-note contract preserved: EMIS keys and generated-document JSON/content contracts unchanged.

# 2026-05-10 LLM Provider Saved Model Validation

- Implemented saved LLM config edit validation so non-empty saved provider model lists still constrain `model_name` when no fresh discovery runs.
- Added API regression coverage for unchanged endpoint + kept credential rejecting an unavailable model.
- Expanded `/admin2?tab=llm` parity assertions for branded provider options, Bedrock region selector, manual fallback copy, and override/reclassification copy.
- Docs updated in `docs/llm-providers.md`.
- Architecture checkpoint: no schema change; system-admin LLM provisioning boundary unchanged; no transcript content access; no deletion or encryption/key semantics changed; provider fallback preserved for manual-required/no-model-list state; structured-note contract unaffected.

# 2026-05-12 STT Credential Review Fixes

- Restored OpenAI-compatible STT save/replacement credential validation through the bundled sample transcription probe.
- Preserved OpenAI Cloud inspection fallback to built-in transcription models when SDK/model discovery raises an unexpected non-credential exception.
- Fixed ElevenLabs STT adapter enum downgrade by renaming the current enum before recreating the old enum.
- Added focused API and migration regressions for provider rejection, fallback, duplicate saves, URL scope, and enum downgrade.
- Architecture checkpoint: privacy boundaries unchanged; provider provisioning remains system-admin scoped; no ownership, deletion, encryption/key, or structured-note contract changes.

# 2026-05-12 STT Review Follow-up Fixes

- Added DB server defaults for `team_stt_configs.provider_preset` and `setup_status` so raw inserts after migration keep ORM-compatible defaults.
- Added Vault compensation for STT draft create when a secret is written but the DB commit fails.
- Snapshotted `stt_provider_preset` onto transcript ingestion jobs and replay queued jobs with that saved preset; old snapshots default to custom REST semantics unless an explicit preset exists.
- Added focused regressions for draft cleanup, queued preset replay, old snapshot fallback, and migration schema/default behavior.
- Architecture checkpoint: provider routing semantics preserved across queued jobs; secret cleanup follows DB rollback; no transcript visibility, ownership, deletion, encryption-key, or structured-note contract changes.

# 2026-05-12 STT Provider Preset Migration Review Fix

- Added tracked Alembic coverage for `transcript_ingestion_jobs.stt_provider_preset` so upgraded databases receive the queued-job provider snapshot column before ORM writes use it.
- Kept column nullable for existing queued jobs; replay logic still treats missing preset as custom REST fallback.
- Architecture checkpoint: provider snapshot persistence fixed; no transcript visibility, ownership, deletion, encryption-key, or structured-note contract changes.

# 2026-05-12 STT Draft Credential Vault Ordering Fix

- Changed STT credential replacement to write replacement secrets to a new Vault ref, then point the DB row at that ref on commit.
- Failed replacement commits now clean up only the new Vault secret and leave the previous saved credential untouched.
- Runtime STT credential reads now use the persisted Vault ref, preserving old deterministic refs and new replacement refs.
- Added focused API regression coverage for draft replacement commit failure preserving the old Vault secret.
- Architecture checkpoint: provider secret integrity improved; system-admin provisioning scope unchanged; no transcript visibility, ownership, deletion cascade, encryption-key, or structured-note contract changes.

# 2026-05-14 Follow-up Quick Action Selection UX

- Changed Follow Ups quick-action cards to select/fill the optional quick-action card without auto-running generation.
- Replaced the empty selected-action card's `Choose quick action` button with a left-arrow focus control pointing back to the quick-action list.
- Added Generate and Remove controls inside the selected quick-action card; bottom Generate remains and still runs the selected quick action when present, or context-only follow-up when no action is selected.
- Updated static web regression coverage and `docs/transcribe_brief.md`.
- Architecture checkpoint: privacy boundaries, ownership, deletion semantics, provider rules, and structured-note contract unchanged; existing owner-only generation endpoints remain the only execution path.

## 2026-06-07 Hallucination Checker Delete Cleanup

### Scope

- Fixed user deletion cleanup for `TeamHallucinationCheckSelection.selected_by_user_id` so deleting the selecting user reassigns attribution to the deleting actor.
- Added explicit hallucination-check selection cleanup in team hard-delete before team LLM configs are removed.

### Checklist

- Target behavior: admin/team user deletion no longer leaves hallucination-check selection FKs pointing at deleted users.
- Affected schema/modules/endpoints: `app/services/admin.py`; no schema or endpoint contract change.
- Affected tests: admin deletion regressions in `tests/test_admin_ui.py`.
- Architecture risks: deletion semantics/provider selection cleanup only; no content visibility, ownership, encryption, provider resolution, or structured-note contract redesign.
- Docs referenced/updated: `docs/progress.md`.
- Reuse decision: reused existing STT, LLM, de-identification, and clinical NLP selection reassignment pattern.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: yes.
- Open issues: none.

### Files changed

- `app/services/admin.py`: reassigns hallucination-check selection attribution during user deletion and deletes team checker selection during team hard-delete.
- `tests/test_admin_ui.py`: covers user deletion reassignment and team deletion cleanup.
- `docs/progress.md`: records scope, checklist, tests, docs, and checkpoints.

### Tests

- `.venv/bin/pytest -q tests/test_admin_ui.py -k "delete_team_and_owned_records or user_delete_reassigns_hallucination_check_selection"`: passed, 2 tests.

### Documentation

- Added this progress note; no API/user documentation needed because behavior is internal deletion cleanup.

### Risks / assumptions

- Assumes reassigning selection attribution to the deleting actor remains intended for admin-managed provider selection rows, matching existing provider cleanup.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; no transcript-derived content access added.
- Ownership rules: unchanged; selection attribution changes only to satisfy deletion cleanup.
- Deletion semantics: strengthened by covering the new hallucination-check provider-selection FK.
- Provider rules: unchanged; active checker provider/model selection remains intact unless the team itself is deleted.
- Structured-note contract: unchanged.

# 2026-05-19 Working Note Correction Plan Critique

- Reviewed `working_note_corrections.md` against current Working note UI/backend code and `docs/working_note_implementation.md`.
- Kept all three proposed regressions, but narrowed fixes to avoid stale saved Working-note generation, accidental mode switching, and over-broad availability flags.
- Deleted already-fixed earlier-review items from the active plan: omitted `expected_updated_at` conflict protection and unchecked structured-line persistence.
- Tests not run; docs-only planning update.
- Architecture checkpoint: privacy boundaries unchanged; owner-only Working-note content remains the only source for generation; deletion semantics preserved by requiring explicit Clear/DELETE for saved Working notes; provider rules unchanged; structured-note contract unchanged.

# 2026-05-19 Working Note Correction Implementation

- Fixed dirty-empty never-saved Working-note drafts so they clear local dirty state instead of trapping generation or note switching.
- Kept dirty-empty saved Working-note edits blocked until explicit Clear/DELETE, preventing stale saved content from silently feeding generation.
- Added guarded template-change handling so dirty Working notes save before template UI sync, failed saves revert the template selection, and locked Working notes skip destructive editor re-render.
- Added `active_template_generation_input_available` for server-rendered Create availability, including transcript text, structured/freeform Working note, and saved dictation while keeping follow-up/quick-action availability separate.
- Added focused admin UI regressions for saved Working note, saved dictation, generated-note-only Create state, and static JS guard wiring.
- Tests: `.venv/bin/pytest -q tests/test_admin_ui.py -k "working_note or create_button or transcribe_workspace_static"` passed, 4 tests.
- Tests: `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_static_asset_version_bumped_for_pii_source_visibility"` passed, 1 test.
- Tests: `.venv/bin/pytest -q tests/test_api.py -k "working_note or dictation_only_session_before_provider_call"` passed, 4 tests.
- Architecture checkpoint: privacy boundaries preserved; Working note/dictation remain owner-only generation inputs; deletion semantics preserved by using explicit DELETE only for saved content; provider resolution unchanged; structured-note contract unchanged.

# 2026-05-20 Working Note Template Guard Cleanup

- Critiqued `working_note_corrections.md`: handler wiring exists in `actions.js`; remaining debt was eager template UI sync in `app.js` and picker helpers.
- Removed eager template sync paths so `handleOutputTemplateChange()` remains the single guarded template-change policy.
- Confirmed dictation-only note generation path already allows empty transcript snapshots when saved dictation exists; kept existing UI/API coverage.
- Updated static asset version for changed transcribe JS.
- Tests: `node --check app/static/js/transcribe/app.js && node --check app/static/js/transcribe/documents.js` passed.
- Tests: `.venv/bin/pytest -q tests/test_admin_ui.py -k "working_note or create_button or transcribe_workspace_static"` passed, 4 tests.
- Tests: `.venv/bin/pytest -q tests/test_api.py -k "working_note or dictation_only_session_before_provider_call"` passed, 5 tests.
- Architecture checkpoint: privacy boundaries preserved; Working note and dictation remain owner-only generation inputs; deletion semantics unchanged; provider resolution unchanged; structured-note contract unchanged.

# 2026-05-26 Deepgram MIP Opt-Out Enforcement

- Added Deepgram STT query-param enforcement so `mip_opt_out=true` is part of provider defaults, direct saves, draft/finalize flows, reinspection, saved-config tests, and runtime transcription.
- Forced `api.deepgram.com` configs through the Deepgram preset even when submitted as custom REST, closing provider-preset bypass.
- Added focused API/runtime regressions for draft defaults, direct-save normalization, explicit false rejection, and old saved runtime params.
- Updated `docs/stt-config.md` and `docs/api.md`.
- Architecture checkpoint: privacy boundaries strengthened for provider processing; system-admin STT provisioning remains scoped by team; no ownership, deletion cascade, encryption-key, or structured-note contract changes.

# 2026-05-27 LLM Note Generation Options

- Added owner-scoped `note_generation_length` app preference and reused `llm_detail_level` for template-note detail.
- Added workspace `Note options` and Home writing-assistant controls for model, length, and detail; workspace saves length/detail through app preferences and model through the existing LLM preference route.
- Template note jobs snapshot saved length/detail at queue time, map length to provider token caps, and keep quick actions/follow-ups outside the new option path.
- Updated API, provider, and user docs.
- Tests: `.venv/bin/pytest -q tests/test_api.py -k "app_preferences or queued_note_options or template_generation_supports_ollama_adapter or generated_document_keeps_prompt_snapshot_after_quick_action_delete"` passed, 9 tests.
- Tests: `.venv/bin/pytest -q tests/test_admin_ui.py -k "user_home_can_save_llm_preference or user_transcribe_page_shows_workspace_shell or transcribe_static_asset_version_bumped_for_pii_source_visibility or transcribe_reorder_blocks_blank_note_lines"` passed, 4 tests.
- Tests: `.venv/bin/pytest -q tests/test_api.py -k "team_and_personal_template_routes_enforce_scope_and_allow_generation or structured_emis_template_generation_persists_sections or template_generation_supports_bedrock_adapter"` passed, 3 tests.
- Architecture checkpoint: privacy boundaries preserved; only owner app preferences are read. Ownership rules unchanged for transcript/generated-document access. Deletion semantics unchanged. Provider rules preserved by using existing LLM selection/credential paths and adapter-specific cap fields. Structured-note contract preserved; detail guidance does not expand EMIS keys or output schema.

# 2026-05-27 Note Option Save Ordering Fix

- Serialized workspace note option/model preference saves and made template-note Create wait for pending saves before queueing `/generate-output`.
- Added one retry for failed option/model saves; if retry still fails during Create, the workspace warns the user and queues generation with the last saved settings.
- Bumped the transcribe app asset version and added static UI regression checks for the pending-save/retry guard before the generation POST.
- Updated `docs/api.md` to record the retry and last-saved fallback behavior.
- Architecture checkpoint: privacy boundaries preserved; preference writes still use owner-scoped endpoints, generation remains owner-only, deletion semantics unchanged, provider resolution unchanged, and structured-note contract unchanged.

# 2026-05-27 Deepgram Host Adapter Guard

- Stopped runtime provider resolution from switching persisted STT rows to Deepgram solely because `base_url` is `https://api.deepgram.com`.
- Kept admin save/inspect protection by normalizing that host to Deepgram only for `generic_rest` and rejecting incompatible adapters.
- Added focused runtime regression coverage for persisted `openai_cloud` rows on the Deepgram host staying on the OpenAI adapter path.
- Updated `docs/stt-config.md`.
- Architecture checkpoint: privacy boundaries unchanged; provider resolution now preserves stored adapter semantics for existing rows while keeping new Deepgram admin writes on the known `generic_rest` contract; no ownership, deletion cascade, encryption-key, or structured-note contract changes.

# 2026-06-03 Transcript Render Guard

- Added a single guarded transcript render path in the transcribe workspace so no-op workspace refreshes no longer rebuild transcript DOM and clear text selection.
- Deferred real transcript/highlight redraws while user selection touches the transcript, keeping only the latest pending update; transcript switches and PII visibility changes force redraw immediately.
- Stopped PII table refreshes from bypassing the transcript render guard, and bumped the transcribe app asset version.
- Added focused static regression coverage for the guarded render path and cache-busting asset version.
- Tests: `node --check app/static/js/transcribe/app.js` passed.
- Tests: `.venv/bin/pytest -q tests/test_web_refactor.py -k "transcribe_transcript_render_guard"` passed, 1 test.
- Tests: `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_static_asset_version_bumped_for_pii_source_visibility or user_transcribe_page_shows_workspace_shell"` passed, 2 tests.
- Tests: `.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe_reorder_blocks_blank_note_lines"` passed, 1 test.
- Architecture checkpoint: privacy boundaries preserved by forcing redraw on active transcript changes and PII visibility changes; ownership rules unchanged; deletion semantics unchanged; provider rules unchanged; structured-note contract unchanged.

# 2026-06-29 CSRF UI Assertion Regression

### Scope

- Updated two rendered-page assertions to verify CSRF JavaScript receives its token from nonce-protected request state and does not declare the old cookie-reader constant.

### Checklist

- Code complete: yes; test-only assertion update.
- Tests added/updated: updated `tests/test_admin_ui.py`; focused regression passed.
- Docs added/updated: this daily progress note.
- Open issues: none.

### Files changed

- `tests/test_admin_ui.py`: replace stale cookie-source expectations with positive request-state token and negative cookie-reader checks.
- `docs/progress.md`: record scope, validation, and architecture checkpoints.

### Tests

- `COOKIE_SECURE_MODE=auto .venv/bin/pytest -q tests/test_admin_ui.py -k "template_editor_page_uses_dedicated_full_page_layout or admin2_preview_route_renders_for_system_admin"`: passed, 2 tests (after loading `.env`, `COOKIE_SECURE_MODE` was overridden to `auto` for HTTP TestClient compatibility).
- `.venv/bin/pytest -q tests/test_cookie_csrf_security.py -k "public_forms_render_server_side_csrf_tokens or csrf_cookie"`: passed, 4 tests.

### Documentation

- Existing `docs/security.md` contract remains accurate; no user-facing or API behavior changed.

### Risks / assumptions

- Assumes rendered `const CSRF_TOKEN = "..."` remains the intended nonce-protected request-state handoff. Runtime code is unchanged.

### Architecture checkpoint summary

- Privacy boundaries and ownership rules: unchanged; no content path changed.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: tests now reject the retired JavaScript-readable CSRF cookie source.
- Lifecycle/deletion checkpoint: no lifecycle or deletion path changed.
- Docs/tests checkpoint: assertions and daily note updated; focused UI and nearby CSRF security tests pass.

# 2026-06-29 Newest Capped Audit Detection

### Scope

- Changed capped audit detection reads to select newest events before applying the 10,000-event cap, preserving recent attacks and destructive actions during high-volume windows.

### Checklist

- Target behavior: newest events remain in audit detection summaries when selected window exceeds cap.
- Affected schema/modules/endpoints: `app/services/audit_detection.py`; no schema, migration, or endpoint change.
- Affected tests: `tests/test_audit_detection.py`.
- Architecture risks: metadata-only audit visibility; no transcript-derived content access.
- Reuse decision: refined existing bounded SQL query and existing summarizer; no new code path.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: `docs/security.md` and this note.
- Open issues: none.

### Files changed

- `app/services/audit_detection.py`: order summary candidates newest-first before limiting.
- `tests/test_audit_detection.py`: small-cap regression proves oldest exclusion and recent destructive-event retention.
- `docs/security.md`: documents newest-event cap behavior.
- `docs/progress.md`: records implementation checkpoints.

### Tests

- Before fix, focused regression failed because oldest event displaced newest event.
- `COOKIE_SECURE_MODE=auto .venv/bin/pytest -q tests/test_audit_detection.py`: passed, 6 tests (after loading `.env`, cookie mode was overridden for HTTP TestClient compatibility).

### Documentation

- Security operations contract now states how capped windows are sampled.

### Risks / assumptions

- Equal timestamps use descending UUID as deterministic tie-breaker; events sharing an exact timestamp have no finer persisted chronology.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; summaries still use metadata-only audit rows.
- Ownership/auth rules: unchanged; no read-access scope changed.
- Deletion semantics: unchanged; audit retention remains separate from transcript retention.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: system-admin/operations audit visibility unchanged.
- Lifecycle/deletion checkpoint: destructive-event detection improves; deletion behavior unchanged.
- Docs/tests checkpoint: regression and security documentation updated.

# 2026-06-29 Offline SSRF Redirect Canary

### Scope

- Replaced live `httpbin.org` access in SSRF redirect-policy coverage with `httpx.MockTransport` and asserted only the initial redirecting URL is requested.

### Checklist

- Target behavior: deterministic offline verification that default HTTPX clients do not follow redirects.
- Affected schema/modules/endpoints: test harness only; no schema, application module, or endpoint change.
- Affected tests: `tests/test_ssrf_canary.py`.
- Architecture risks: preserve meaningful SSRF redirect-policy coverage without external network dependency.
- Reuse decision: used HTTPX built-in mock transport; no local server or custom transport layer.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: `docs/testing.md` and this note.
- Open issues: none.

### Files changed

- `tests/test_ssrf_canary.py`: deterministic mocked redirect response and request-path assertion.
- `docs/testing.md`: records network-free SSRF canary behavior.
- `docs/progress.md`: records scope and checkpoints.

### Tests

- `COOKIE_SECURE_MODE=auto .venv/bin/pytest -q tests/test_ssrf_canary.py`: passed, 23 tests (after loading `.env`, cookie mode was overridden for HTTP TestClient compatibility).

### Documentation

- Testing guide now states public internet is not required for SSRF redirect coverage.

### Risks / assumptions

- Test covers HTTPX default client redirect behavior. Application provider calls must continue omitting `follow_redirects=True` for this guarantee to apply.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; synthetic host/path only.
- Ownership/auth rules: unchanged.
- Deletion semantics: unchanged.
- Provider rules: unchanged; runtime provider code untouched.
- Structured-note contract: unchanged.
- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: admin-only provider controls unchanged.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: deterministic test and testing docs updated.

# 2026-06-29 Bounded Audit Filter Options

### Scope

- Replaced unbounded application-side audit `details_json` loading with SQL-side distinct category/outcome extraction, defaulting, and ordering.

### Checklist

- Target behavior: Admin Audit filter options remain complete and sorted without loading every audit row's JSON into application memory.
- Affected schema/modules/endpoints: `app/services/audit_detection.py`; no schema, migration, or endpoint change.
- Affected tests: `tests/test_audit_detection.py` plus existing admin audit UI coverage.
- Architecture risks: preserve metadata-only system-admin filter behavior and missing-value defaults.
- Reuse decision: reused SQLAlchemy JSON text extraction, `coalesce`, `distinct`, and existing filter-option service.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: `docs/security.md` and this note.
- Open issues: none.

### Files changed

- `app/services/audit_detection.py`: query distinct JSON category/outcome values in SQL.
- `tests/test_audit_detection.py`: verify values/defaults and capture SQL to reject full-column JSON loading.
- `docs/security.md`: document SQL-side option extraction.
- `docs/progress.md`: record implementation checkpoints.

### Tests

- Before fix, regression captured one unbounded `SELECT security_audit_events.details_json`; after fix, focused test passed with two distinct JSON scalar queries.
- `COOKIE_SECURE_MODE=auto .venv/bin/pytest -q tests/test_audit_detection.py tests/test_admin_ui.py -k "audit_detection or admin2_audit_tab_shows_metadata_only_security_events"`: passed, 8 tests (after loading `.env`, cookie mode was overridden for HTTP TestClient compatibility).

### Documentation

- Security operations notes now describe bounded filter-option extraction.

### Risks / assumptions

- Audit category/outcome values are application-controlled metadata. SQL queries return all distinct values, whose cardinality is bounded by event vocabulary rather than row count.

### Architecture checkpoint summary

- Privacy boundaries: strengthened memory minimization; only distinct metadata values leave database.
- Ownership/auth rules: unchanged; Audit tab remains system-admin-only.
- Deletion semantics: unchanged; audit retention stays separate from transcript retention.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Schema checkpoint: no schema or migration change.
- Auth/ownership checkpoint: no scope expansion or content access.
- Lifecycle/deletion checkpoint: no lifecycle path changed.
- Docs/tests checkpoint: query-shape regression and security documentation updated.

# 2026-06-29 Exact Full-Window Audit Detection

### Scope

- Replaced capped in-memory audit summaries with exact SQL aggregation across the full selected window.
- Added a creation-time index for bounded audit-window queries.

### Checklist

- Target behavior: event/category/outcome counts and all detection signals include every event in the selected window, including windows above the former 10,000-event cap.
- Affected schema/modules/endpoints: `security_audit_events` index, `app/services/audit_detection.py`; existing Admin Audit pages consume the unchanged report shape.
- Affected tests: `tests/test_audit_detection.py`, `tests/test_migrations.py`, existing admin audit UI tests.
- Architecture risks: audit metadata query cost and migration write blocking; migration builds the index concurrently.
- Reuse decision: reused existing signal contract, action sets, SQLAlchemy JSON extraction, masking, and selected-window bound.
- Code complete: yes.
- Tests added/updated: yes.
- Docs added/updated: `docs/security.md`, OWASP audit plan, and this note.
- Open issues: exact per-event destructive/provider signals can produce large response sets if those specific event classes become unusually numerous.

### Files changed

- `app/services/audit_detection.py`: SQL-side exact counts, burst aggregation, and full-window destructive/provider event selection.
- `app/models.py`: declares creation-time audit index.
- `alembic/versions/v3w4x5y6z7a8_index_security_audit_events_created_at.py`: creates/drops index concurrently.
- `tests/test_audit_detection.py`: 10,001-event regression verifies exact totals and older destructive-event retention.
- `tests/test_migrations.py`: verifies index exists at migration head.
- `docs/security.md` and OWASP audit plan: document exact full-window behavior.

### Tests

- Before fix, previous capped implementation would report only newest 10,000 rows and omit the older destructive action.
- `COOKIE_SECURE_MODE=auto .venv/bin/pytest -q tests/test_audit_detection.py`: passed, 7 tests.
- `COOKIE_SECURE_MODE=auto .venv/bin/pytest -q tests/test_audit_detection.py tests/test_admin_ui.py -k "audit_detection or admin2_audit_tab_shows_metadata_only_security_events or admin_audit_tab"`: passed, 9 tests (189 deselected).
- `.venv/bin/pytest -q tests/test_migrations.py -k "upgrade_head_creates_expected_schema"`: passed, 1 test (20 deselected).

### Documentation

- Security operations contract now distinguishes exact full-window detection from separately limited recent-row display.

### Risks / assumptions

- Selected windows remain bounded to 30 days.
- Exact grouped queries trade additional database work for correct monitoring results; creation-time index limits scanned time range.
- Signal response cardinality remains event-based for destructive/admin and provider changes to preserve existing report semantics.

### Architecture checkpoint summary

- Privacy boundaries: unchanged; SQL reads metadata-only audit columns and allowlisted JSON keys, never transcript-derived content.
- Ownership rules: unchanged; Audit UI remains system-admin-only.
- Deletion semantics: unchanged; audit metadata retention remains separate from transcript-root deletion.
- Provider rules: unchanged; provider configuration is not modified and provider-change audit signals retain metadata-only fields.
- Structured-note contract: unchanged.
- Schema checkpoint: additive creation-time index only; no row or constraint semantics changed.
- Auth/ownership checkpoint: no access scope or endpoint changed.
- Lifecycle/deletion checkpoint: no deletion path changed; full-window destructive detection improves visibility.
- Docs/tests checkpoint: regression, migration assertion, security docs, OWASP plan, and daily note updated.
## 2026-07-12 Admin Sidebar Area Selector Polish

### Scope
- Removed shared horizontal-tab container chrome from the vertical `/admin` sidebar selector.
- Strengthened the selected admin area with the existing accent token and label weight.

### Checklist
- Code complete: admin-only CSS override; no route, schema, or navigation-label change.
- Tests updated: flat-sidebar regression now checks the selector override and active marker.
- Docs updated: `docs/admin_brief.md` records the flat selector direction.
- Open issues: browser visual verification pending; focused pytest is blocked by unavailable test PostgreSQL connection.

### Files changed
- `app/static/css/admin.css`: flattens the sidebar selector and adds its active marker.
- `app/templates/admin.html`: bumps the admin stylesheet cache key.
- `tests/test_admin_ui.py`: protects the admin-specific selector styling.
- `docs/admin_brief.md`, `docs/progress.md`: document the UI decision and daily progress.

### Architecture checkpoints
- Schema: unchanged.
- Auth/ownership: unchanged; existing system-admin rendering and tab behavior remain intact.
- Lifecycle/deletion: unchanged.

## 2026-07-12 STT/LLM Provider Revisions

### Scope
- Added inspected pending revisions for existing STT and LLM provider configs, with atomic promotion into stable active IDs.
- Kept pending rows out of normal lists, selection candidates, and runtime paths.

### Checklist
- Code complete: revision schema, service lifecycle, API schema, web draft forms, cancellation, promotion, and secret cleanup implemented.
- Tests updated: migration head verifies revision columns and partial unique indexes; focused provider draft regressions run.
- Docs updated: admin workspace lifecycle map and this daily note.
- Open issues: full migration suite requires configured test PostgreSQL; focused migration result recorded below.

### Files changed
- `alembic/versions/w4x5y6z7a8b9_add_provider_config_revisions.py`, `app/models.py`: self-links and partial uniqueness.
- `app/services/stt.py`, `app/services/llm.py`, `app/services/vault.py`, `app/services/templates.py`: staged lifecycle, runtime isolation, reference-aware Vault access.
- `app/schemas/stt.py`, `app/schemas/llm.py`, `app/routes/web_admin.py`, `app/main.py`: revision draft inputs and service-backed cancellation.
- `tests/test_migrations.py`: migration shape assertions.

### Architecture checkpoint summary
- Privacy boundaries: provider metadata and Vault references only; no transcript-derived content exposed.
- Ownership rules: revision target resolution requires same team and ready root.
- Deletion semantics: DB commit precedes staged/retired secret cleanup; active deletion includes pending revision secrets.
- Provider rules: active ID and selection FKs survive promotion; in-flight restrictions remain enforced against active root.
- Structured-note contract: unchanged.
- Schema checkpoint: nullable self-FKs plus root-label and one-pending-revision partial unique indexes.
- Auth/ownership checkpoint: existing system-admin draft authorization retained; service enforces team scope.
- Lifecycle/deletion checkpoint: revision promotion atomic; promoted secret reference is not cleaned with deleted revision row.
- Docs/tests checkpoint: function map, daily note, migration assertions, focused provider tests updated/run.
- Privacy/provider/structured-note contracts: unchanged; styling exposes no new data and changes no provider behavior.

## 2026-07-12 Usage Overview Panel Spacing

- Restored standard `18px` admin panel inset on `.usage-hero`; its previous late cascade override placed the Usage overview heading against the rounded border.
- Bumped the admin stylesheet cache key and extended the static admin UI regression.
- Updated `docs/usage_tab.md`; no schema, auth, ownership, deletion, provider, privacy, encryption, or structured-note behavior changed.
- Focused pytest remains blocked by the unavailable test PostgreSQL connection; `git diff --check` passes.

## 2026-07-12 Provider Setup Entry State

- Promoted the team selector to the first and primary Provider setup card when no team is selected.
- Moved entry-state context into that card; the full provider introduction remains after team selection.
- Reused existing panel, section header, form, and visual tokens. Provider selection inputs, routes, credentials, and policy behavior are unchanged.
- Added focused render coverage and updated `docs/admin_brief.md`.
- Follow-up: removed the stretched entry card after browser review. Unselected state is now a compact, unframed, top-aligned selector with a `520px` maximum width.
- Follow-up: fixed parent admin grid track stretching with `.admin-pane { align-content: start; }`, keeping sparse entry content directly below the header instead of halfway down the viewport.
- Final design decision: Team scope now uses identical panel markup and copy before and after team selection; only selector value and downstream provider configuration change.
# 2026-07-12 — Admin workspace redesign discovery

- Scope: inspected current `admin.html`, redesign input `admin_mockup.html`, admin routes/services, and existing admin docs; added `docs/admin_workspace_function_map.md` as preservation checklist before UI changes.
- Tests: not run; documentation-only change with no runtime behavior.
- Architecture: no schema or provider-resolution change; system-admin metadata-only privacy boundary, ownership rules, immediate deletion semantics, Vault secret handling, and EMIS structured-note contract recorded as redesign gates.
- Risk/open work: mockup remains mostly static and does not yet cover all current admin functions. Migration sequence and navigation model remain to be resolved through design grilling.
- Decision: new evolving workspace will own `/admin`; current functional workspace will move to temporary `/legacy-admin`. Existing mutation routes stay under `/admin/...`, with redirects required to preserve initiating workspace.
- Decision: internal-only development allows unfinished mock panels on `/admin`; production exposure waits for full redesign verification. Inert controls must not accidentally mutate state.
- Decision: retain mockup's team-first information architecture. First pass plugs mockup controls into extant routes/services one-by-one; workflow/backend redesign is out of scope until parity exists.
- Decision: keep full team list in sidebar as mocked; expected environment scale does not justify search/filter/pagination.
- Decision: retain `/admin2` unchanged as secondary developer reference. `/legacy-admin` is official migration fallback.
- Decision: selected team and team tab use canonical `team_id`/`team_tab` URL query state. Links work without JavaScript; refresh/history/bookmarks and POST redirects preserve valid state.
- Decision: `/admin` without `team_id` shows explicit team-selection empty state; no automatic or remembered team. Zero-team state offers team creation. Global areas remain available without team scope.
- Decision: team selection defaults to Overview and canonical `team_tab=overview`.
- Decision: team Overview is read-only summary/navigation; mutations remain in dedicated tabs.
- Decision: Provider policy owns all active runtime selections. STT, LLM, and new De-identification tab own provider provisioning/inspection/lifecycle. Configured and active states stay distinct.
- Decision: preserve current global De-identification provider model for now. Team tab lists assigned providers first, available global providers for attachment, and create-global-then-assign flow. “Remove from team” means detach; global edit/delete must disclose cross-team impact. Team-scoped provider model redesign deferred.
- Refinement: sidebar gets separate global De-ID providers management area for create/inspect/edit/delete. Team De-identification tab only views and attaches/detaches providers; team rows never globally delete. Provider policy selects active assigned provider.
- Decision: global De-ID registry also manages Clinical NLP providers, labelled by De-ID, Clinical NLP, or both capabilities. Team assignment and the two active runtime selections remain separate.
- Decision: team Security tab is read-only posture/navigation. User MFA/recovery/break-glass actions stay under Members; event inspection stays in global Audit.
- Decision: Danger zone contains existing team hard-delete only, with cleanup scope, blockers, and explicit confirmation. No new suspend/archive semantics.
- Proposed during grill: team Defaults should allow system-admin editing of default retention time. This needs a new backend mutation and targeted retention tests; effect on existing transcripts must respect fixed-expiry rule and remains to be confirmed.
- Resolution: keep fixed-expiry architecture. Team default retention may be edited, but only future transcript roots use the new value; existing expiry timestamps remain unchanged. New mutation needs authorization and retention regression tests when implemented.
- Decision: team Defaults includes retention edit plus read-only team asset summary. Team leaders manage team-owned assets; sidebar Global defaults manages platform defaults without overwriting existing team assets.
- Decision: Team Members creates normal users with selected team fixed and role chosen. Separate sidebar System admins area manages admin-only accounts; member form cannot promote to system admin.
- Decision: member rows use state-aware Actions menu. Destructive/break-glass actions get consequence-specific confirmation; email actions provide direct outcome feedback; server remains final guard.
- Decision: account requests remain global. Approval selects target team/role and links to resulting team member; rejection requires reason.
- Decision: Usage defaults to all-team aggregates; team and user drill-down use URL scope and remain metadata-only.
- Decision: Audit lookback/team/actor/action/outcome/resource filters are validated URL state. Sensitive values remain forbidden.
- Decision: production redesign uses Jinja/partials plus dedicated progressive-enhancement JS and minimal workspace CSS. Existing site tokens/components and CSRF behavior take priority; no parallel admin design system.
- Decision: implementation order is route swap; shell/navigation; Overview; Members; Provider policy; provider areas; Defaults/Security/Danger; global Requests/Usage/Audit; parity/release gate. Mockup expresses layout intent, but shared site components win on duplicated styling.
- Refinement: shared components do not automatically override mockup. Reuse clear matches; ask user at implementation time where reuse materially changes mockup appearance/interaction.
- Decision: forms use inline field errors plus form summary; outcomes use site toasts and refreshed panel state. Preserve non-secret input on failure, never credentials.
- Decision: provider wizards use server-side draft/finalize state with URL-addressable step/draft. Refresh restores non-secret state; secrets remain Vault-backed and absent from URL/HTML.
- Decision/new backend need: Cancel wizard explicitly deletes pending draft and safely cleans Vault reference after DB reference removal. Browser-abandoned drafts require later scheduled stale cleanup with metadata-only audit/logging.
- Release decision: `master` is user-facing. Incomplete redesign stays off `master`; full parity, affected tests, manual/accessibility/responsive comparison, and security review gate merge. `/legacy-admin` is development fallback only.
- Workflow decision: use one long-lived redesign branch, small slice commits, and regular synchronization with `master` to control drift before final merge.
- Test decision: add semantic browser E2E coverage for critical admin workflows; avoid pixel-perfect screenshots. Keep server/API tests authoritative for auth, deletion, provider, and secret invariants.
- IA decision: accepted sidebar is Home, Teams, Manage teams, Account requests, System admins, Global defaults, De-ID providers, Usage, Audit, Log out; accepted team tabs are Overview, Members, Provider policy, STT, LLM, De-identification, Defaults, Security, Danger zone. Open: admin-only `/home` currently redirects to `/admin`, so sidebar Home meaning needs resolution.
- Resolution: rename Home to Admin home; brand and link target neutral `/admin`. Show safe global summary counts plus team-selection prompt, with no automatic team.
- Decision: Manage teams provides metadata directory, create, and open-team actions. Hard-delete remains only in team Danger zone.
- Decision: team status remains read-only after creation; suspend/archive semantics are deferred pending explicit lifecycle design.
- Implementation checkpoint: `/admin` now renders `admin_mockup.html` with real system-admin identity, full team list, neutral Admin home, safe global counts, validated `team_id`/`team_tab`, dynamic team summary, agreed sidebar inventory, and POST logout. `/legacy-admin` renders unchanged functional `admin.html`; `/admin2` remains unchanged. Validated return views distinguish new workspace, legacy, admin2, and restyled routes.
- Tests: focused admin workspace/legacy/admin2 route tests passed (3 tests). Existing flat-layout regression now targets `/legacy-admin`; new regression covers neutral home and URL-scoped team navigation.
- Architecture checkpoints: no schema change; both pages remain system-admin-only and metadata-only; mutation services/deletion semantics/provider resolution/EMIS contract unchanged; invalid team IDs cannot select scope.
- Remaining: mock team panels still require control-by-control parity wiring; retention-default update, draft cancellation, CSS/JS extraction, global areas, broader test migration, and release gate are not complete.
- CSP styling fix: diagnosed mockup's un-nonced inline stylesheet/script plus six forbidden `style` attributes under `style-src-attr 'none'`. Added per-response CSP nonces and replaced style attributes with classes; retained strict CSP. scite.ai font violations are browser-extension injections, not app assets.
- Regression coverage now asserts new admin render contains nonced style/script, contains no `style` attributes, and retains strict `style-src-attr 'none'`. Template static probe and `git diff --check` pass. Focused DB-backed rerun reached assertion once after fix, then subsequent retry was blocked during test setup by intermittent PostgreSQL connection failure; rerun required when DB is stable.
- Functionality checkpoint: Members now renders real selected-team users and wires add, suspend/reactivate, activation, password/account recovery, MFA reset, and immediate delete through existing routes with CSRF, state-aware controls, confirmations, and `team_tab=members` returns. No member form can create/promote a system admin.
- Functionality checkpoint: team De-identification tab lists assigned and available global providers and wires attach/detach only; global deletion remains absent. Danger zone wires existing team hard-delete with full cleanup warning.
- Functionality checkpoint: added system-admin-only `POST /admin/teams/{team_id}/retention` and `update_team_default_retention`; bounded default changes apply to future transcript creation only and record safe audit metadata. Existing transcript expiry rows are not queried or updated.
- Tests: 4 focused redesign tests pass, covering CSP/shell navigation, member route wiring plus creation redirect, De-ID/Danger actions, and retention update.
- Functionality checkpoint: Provider policy now posts conversation/dictation STT, writing-assistant LLM/model visibility/default, hallucination checker, De-ID, and Clinical NLP selections through existing services. STT/LLM tabs render real config metadata and wire saved inspect/test/delete; add/edit currently enters fully functional legacy forms pending wizard design decision.
- Functionality checkpoint: global sidebar areas now render distinct functional views for team directory/create, account request approve/reject, system-admin creation/list, global defaults entry, global De-ID registry/delete, aggregate Usage, and filtered Audit. Security posture now derives member/provider metadata rather than mock counts.
- Open design decision: mock STT/LLM wizards are intentionally simple, while existing provider forms expose advanced generic REST/OpenAPI request/response mappings, credential actions, duplicate confirmation, model visibility, and adapter-specific controls. Need decide progressive Advanced step versus full expert form inside wizard.
- Resolution: keep simple preset wizard; custom providers reveal progressive Advanced fields preserving full expert contract. Provider cards must come only from backend-supported preset registries, never unsupported mock brands.
- Provider wizard checkpoint: STT/LLM add buttons now submit real server-side draft routes with backend preset keys, return to matching team tab, show pending finalization forms using discovered models, and expose explicit draft cancellation routes. Cancellation accepts pending drafts only and reuses existing delete/Vault cleanup services.
- Routing checkpoint: global Usage/Audit and other global sidebar views render globally even when `team_id` filters context; legacy `tab=providers&team_id=...` maps to Provider policy for migration compatibility.
- Verification: redesign-focused suite passes 7 tests. Full `tests/test_admin_ui.py` run: 172 passed, 29 failed. Most admin failures assert old `/admin` markup and need deliberate `/legacy-admin` or redesign expectation migration; unrelated Home/Transcribe failures also appeared and require baseline review rather than green-chasing.
- Provider edit decision: cosmetic label/availability edits may save directly. Endpoint, credential, adapter, region, model-discovery, and contract changes require a separately inspected pending revision; active provider remains unchanged and usable until atomic promotion. Cancelling removes only revision and staged Vault credential.
- Provider edit schema checkpoint: existing setup drafts have no lineage to an active config, so safe revision editing needs a dedicated migration and service/API slice. Redesign must retain legacy material-edit links until lineage, atomic promotion, auth/team scoping, post-commit secret cleanup, and tests exist. No schema shortcut added in presentation work.
## 2026-07-12 - Provider revision finalize safety

- LLM revision promotion now rejects when target config has queued or processing generated documents.
- STT and LLM API coverage verifies revisions remain hidden/pending until promotion, preserve active config IDs and selection foreign keys, apply provider/model fields, reject cross-team targets, and remove promoted revision rows.
- Provider credential writes/deletes remain Vault-reference operations; tests replace provider inspection and Vault calls with deterministic fakes.
- Migration head test name now explicitly includes provider config revisions for reliable focused selection.
- Verification rerun: provider revision API tests `3 passed`; revision migration test `1 passed`; redesign-focused admin suite `7 passed`; `git diff --check` passed. Shared test DB requires serial pytest runs, so one attempted parallel admin run exited by design and passed when rerun serially.
- Provider edit UI checkpoint: redesign now uses one populated add/edit wizard backed by inspected connection revisions. Review fixed add-provider listeners accidentally passing click events into edit-mode initializers; focused redesign/provider suite passes.
- LLM wizard field checkpoint: Region appears only for Bedrock; Base URL appears only for Ollama and Custom OpenAI-compatible. Edit mode no longer forces Base URL visible for managed providers.
- LLM wizard live visibility fix: author `.field { display: grid; }` overrode native `[hidden]`, so OpenAI still displayed Base URL and Region despite correct JS state. Added explicit `.wizard .field[hidden] { display: none; }` and regression coverage.
- Real inspection wizard review fixed optional JSON fields being sent as empty strings; credentials, revision IDs, OpenAPI paths, and regions now use `null`, matching API schemas and preventing add-mode `422` validation failures.
- LLM wizard finalize 422 fix: API path already identifies the draft, but route body incorrectly required a duplicate `config_id`. Added `LlmConfigFinalizeBody`, constructs service payload from path ID, and regression-tests finalize without body ID.
- LLM provider table layout: replaced obsolete 46px action-menu column with a 260px minimum action area, tightened metadata columns/gaps, and added wrapping flex layout for action buttons.
- STT provider table now mirrors LLM spacing: narrower provider-instance/usage columns, 260px minimum Actions area, 10px column gaps, and consistently spaced wrapping buttons.
- LLM wizard model filter fix: `.llm-model-option { display: flex; }` overrode native `hidden`, so filtered cards stayed visible. Added explicit `.llm-model-option[hidden] { display: none; }` regression rule.
- Restored operation feedback omitted by the redesign: STT tests render pass/fail, health, duration, model, and safe errors; saved LLM inspections render discovery status, provider, model count, warnings, and notes. Top-level operation messages now have visible status styling.
- Architecture checkpoints: no transcript-derived content visibility change; team ownership filtering remains enforced; no deletion/cascade or structured-note contract change; provider runtime targets remain stable until successful promotion.
# 2026-07-12: explicit provider edit actions

## Scope
Added narrow STT/LLM detail updates and revision-based connection-change actions in admin workspace.

## Checklist
- Code complete: root-ready, team-scoped detail services/routes; explicit UI actions; selected revision presentation.
- Tests added: cosmetic field isolation, secret/ref HTML absence, revision staging/redirect.
- Docs updated: function map, admin brief, testing guide, progress note.
- Open issues: pending-revision discovery remains intentionally selected-ID only; no broad runtime listing added.

## Files changed
`app/services/stt.py`, `app/services/llm.py`, `app/routes/web_admin.py`, `app/web/presentation.py`, provider schemas/template, focused tests, admin docs.

## Architecture checkpoints
- Privacy: no secret or Vault reference rendered; credential input blank on connection changes.
- Ownership: system-admin route plus service-level team scoping; root-ready target required.
- Deletion/lifecycle: revisions remain hidden from normal lists; discard deletes pending revision while active root stays unchanged.
- Provider rules: cosmetic edits do not mutate endpoint, model, or credentials; connection changes use existing inspect/finalize flow.
- Structured notes: unchanged.
# 2026-07-12: Unified provider edit wizard

- Scope: replaced split provider edit actions with one STT/LLM wizard edit action; populated supported non-secret settings and added blank-credential reuse.
- Checklist: code and focused regressions updated; docs updated; no schema change. Open issue: wizard remains server-backed two-stage flow, so inspected defaults finish in existing finalize card.
- Files: `app/templates/admin_mockup.html`, STT/LLM services, admin/API tests, admin UX docs.
- Tests: provider-row/modal regression plus STT/LLM shared-secret revision cancellation coverage; focused results recorded in change summary.
- Architecture checkpoints: system-admin and same-team root-ready checks unchanged; no transcript content path added; revision cancellation now checks remaining Vault references after commit; promotion retains identical refs; provider fallback and EMIS structured-note contract unchanged.
# 2026-07-12: Server-backed provider wizards

- Replaced admin workspace STT/LLM fabricated inspection data with existing authenticated draft/finalize APIs.
- Added response-driven safe provider, endpoint, status, warning/note, and model rendering; credentials are cleared after staging and never rendered.
- Added awaited draft deletion on cancel/backdrop/Escape, with modal retained on cleanup failure. Back locks after successful draft creation. Browser unload stale-draft cleanup remains open.
- Architecture: system-admin and team checks remain in API services; Vault references/raw provider responses stay outside DOM; no transcript content, ownership, deletion cascade, provider fallback, or structured-note contract changed.
- 2026-07-12 provider-policy redesign: replaced summary cards with functional six-row policy table using existing visual language and always-visible accessible selects. Added provider-driven STT/LLM/checker model synchronization, compact writing-assistant visible-model checkboxes, state-dependent clear actions, explicit Presidio fallback, and empty-provider links.
- Checklist: code complete; focused UI/POST coverage updated; admin brief, testing guide, and function map updated. No schema/API/service changes. Architecture checkpoints: metadata-only UI preserves transcript privacy; existing system-admin/team ownership guards remain authoritative; clear routes remove selections only and do not alter provider/deletion lifecycle; provider fallback/resolution and structured-note contracts remain unchanged.
