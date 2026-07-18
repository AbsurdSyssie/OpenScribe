# 2026-07-18 Daily Progress Note

## Dev-server stall during transcriber iteration

### 1. Scope

- Reproduced listener accepting connections while all HTTP requests timed out with zero response bytes.
- Moved synchronous owner-scoped workspace resolution from async SSE event loop into `asyncio.to_thread`.
- Limited FastAPI dev reload watcher to `app/`; prototype templates/static, tests, and docs no longer restart Python.

### 2. Checklist

- [x] Deterministic blocking regression failed before fix and passed after fix.
- [x] Existing stream owner-content and request-session isolation tests pass.
- [x] Dev reload-scope regression added.
- [x] Setup and daily docs updated.
- [x] No schema, migration, content-access, deletion, provider-resolution, encryption, retention, or structured-note change.
- [ ] Whole-repository suite not run.

### 3. Tests

- SSE focused tests: `3 passed`.
- Reload-scope test: `1 passed`.
- Python compile, shell syntax, and scoped diff checks passed.
- Live server recovered through reloader; `/login` returned `200` in `0.011s`.

### 4. Risks / assumptions

- One final `start-dev.sh` restart is required to apply new reload-directory argument to current parent process.
- Each SSE connection still resolves once per second, but blocking DB/service work now runs outside event loop.

### 5. Architecture checkpoint summary

- **Privacy/ownership:** each worker-thread resolution creates its own DB session and re-resolves authenticated owner exactly as before.
- **Deletion/provider/structured notes:** unchanged.
- **Lifecycle:** DB session remains scoped to one SSE poll and closes before payload serialization.

---

## Consultation metadata and delete control cleanup

- Removed whole-file/live ingestion-mode labels from server and live-rendered consultation cards; retained `HH:MM` and status pills.
- Replaced sidebar Delete text with accessible Lucide trash icon at right side of pop-out Recent consultations header.
- Disabled icon uses muted grey; enabled/hover states reuse shared `--error` red and existing destructive background values.
- Existing selection and immediate bulk-delete endpoint hooks remain unchanged.
- Architecture checkpoint: presentation-only change; ownership, privacy, deletion semantics, encryption, provider, retention, and structured-note contracts unchanged.

---

## Consultation history pagination after column transition

- Fixed prototype history stopping at its initial 12-row workspace page after the zero-width column transition.
- Live browser reproduction found copied `app.js` resolving `../csrf.js` under prototype static mount; redirect-to-login HTML broke the module graph. Prototype now imports canonical `/static/js/csrf.js` explicitly.
- Same correction applied to copied `actions.js` and `media.js`; live browser module graph then loaded without console errors.
- Live DOM geometry check confirmed independent scrolling (`1066px` client height, `1920px` synthetic content height, `scrollTop` advanced to `500`) without writing test consultations to DB.
- Retained IntersectionObserver and added passive near-bottom scroll fallback plus an open-time check.
- Prevented SSE first-page refreshes from resetting an already-advanced history cursor.
- Focused route/static regression and existing owner-scoped cursor API regression cover frontend wiring and backend pagination.
- Architecture checkpoint: pagination remains owner-scoped and metadata-only; no privacy, deletion, encryption, provider, retention, or structured-note contract change.

---

## Consultation-column scrolling and transition

- Confirmed consultation list independently scrolls within lower workspace row.
- Replaced generic `hidden` state with `is-collapsed`, allowing existing width/opacity transition to run.
- Added `aria-hidden` synchronization and `prefers-reduced-motion` fallback.
- Focused regression covers scroll container, collapsed state, transition, accessibility state, and reduced-motion rule.
- Architecture checkpoint: UI-only change; privacy, ownership, deletion, encryption, provider, retention, and structured-note contracts unchanged.

---

## Transcriber broad-toolbar consultation layout

### 1. Scope

- Reordered only `/transcriber_col_changes` prototype: primary sidebar stays full height; one note toolbar now sits above lower Recent consultations rail plus note workspace.
- Kept one template picker, copy controls, Create action, note options menu, and existing workspace hooks.

### 2. Checklist

- [x] Target behavior, copied prototype templates/CSS, focused UI test, docs, and mobile/layout hook constraints reviewed before coding.
- [x] Schema checkpoint: no schema or migration change.
- [x] Auth/ownership checkpoint: route and canonical owner-scoped resolver unchanged.
- [x] Lifecycle/deletion checkpoint: existing delete endpoints and transcript-root behavior unchanged.
- [x] Docs/tests checkpoint: focused rendered layout regression and docs updated.
- [ ] Whole-repository suite not run.

### 3. Files changed

- `transcriber_changes/workspace/templates/transcribe/transcribe.html`, `_workspace.html`: prototype-only DOM reorder and semantic layout hooks.
- `transcriber_changes/workspace/static/css/transcribe.css`: lower rail/workspace layout plus mobile rail positioning.
- `tests/test_admin_ui.py`: asserts toolbar-before-body order, rail-before-workspace order, and singleton note controls.
- `docs/transcribe_brief.md`: broad-toolbar prototype contract.

### 4. Tests

- Focused prototype route regression verifies authenticated rendering, DOM layout order, retained hooks, singleton controls, and prototype stylesheet rules.

### 5. Documentation

- Updated transcribe brief and this daily record.

### 6. Risks / assumptions

- Toolbar remains visible while Follow Ups or Transcript tab is selected because it now spans shared lower workspace; controls retain existing handlers but are note-specific.
- Open issue / escalate to Sol: confirm whether non-output tabs should hide shared note toolbar after visual review; hiding it needs tab-state wiring, not duplicate markup.

### 7. Architecture checkpoint summary

- **Privacy/ownership:** existing authenticated owner-scoped route and content endpoints reused.
- **Deletion:** existing immediate consultation/note deletion flows unchanged.
- **Provider/structured note:** provider resolution and EMIS/output contracts unchanged.

---

## Transcriber consultation-column prototype route

### 1. Scope

- Added authenticated `/transcriber_col_changes` preview route backed by copied workspace files under `transcriber_changes/workspace`.
- Added left/right Recent consultations toggle and adjacent consultation column.
- Grouped consultations by date and morning/afternoon with Lucide sunrise/sun icons; row time is 24-hour `HH:MM`.

### 2. Checklist

- [x] Code complete.
- [x] Focused auth, render, grouping, time, and static-asset regression added.
- [x] Transcribe brief and daily progress note updated.
- [x] No schema, migration, content-access, deletion, encryption, provider-resolution, or structured-note change.
- [ ] Whole-repository suite not run.

### 3. Files changed

- `app/routes/web_transcribe.py`, `app/web/transcribe_workspace.py`, `app/main.py`: authenticated prototype route, alternate template renderer, static mount, and no-store policy.
- `transcriber_changes/workspace/templates/transcribe/`, `transcriber_changes/workspace/static/css/transcribe.css`: copied prototype UI and consultation column.
- `tests/test_admin_ui.py`: route access and rendered behavior regression.
- `docs/transcribe_brief.md`: prototype contract.

### 4. Tests

- Focused test verifies anonymous redirect, owner render, no-store response, toggle/panel hooks, grouped date/time-of-day markup, `HH:MM`, and copied stylesheet delivery.

### 5. Documentation

- Documented prototype route as non-canonical and owner-only.

### 6. Risks / assumptions

- Existing action endpoints still use canonical `/transcribe/...` paths; completed mutations may return to `/transcribe` during prototype evaluation.
- Prototype now serves its copied `app.js`; live SSE additions rebuild bold date and Morning/Afternoon dividers while retaining 24-hour `HH:MM` row times.

### 7. Architecture checkpoint summary

- **Privacy:** route is authenticated, owner-scoped, and no-store; no new content payload.
- **Ownership:** existing transcribe resolver and owner filtering reused.
- **Deletion:** canonical immediate bulk-delete endpoint and cascade semantics unchanged.
- **Provider rules:** existing team/user provider resolution unchanged.
- **Structured-note contract:** editor and EMIS output contract unchanged.

---

## 1. Scope

Made quota exhaustion actionable in owner-facing API and browser UI. Users now see that their quota is used up and should contact their administrator instead of a generic service-unavailable message.

## 2. Checklist

- [x] Code complete.
- [x] Focused API, browser UI, and error-boundary tests added or updated.
- [x] Active quota API, security, capture, testing, admin, and usage docs updated.
- [x] No schema or migration change.
- [x] No auth, ownership, deletion, provider-resolution, or structured-note change.
- [ ] Whole-repository suite not run.

## 3. Files changed

- `app/errors.py`: shared safe public quota message and sanitized public error mapping.
- `app/services/quotas.py`: browser-form quota failures use same safe message.
- `tests/test_errors.py`, `tests/test_api.py`, `tests/test_admin_ui.py`: boundary, API, and rendered browser regression coverage.
- Active quota contract docs: API, testing, DB testing, security, transcript capture, live STT, admin, and usage references.

## 4. Tests

- Focused regression: public `quota_disabled` and `quota_exceeded`, synchronous STT rejection, and browser note-generation rejection.
- Verified no internal limit, usage, remaining allowance, period, or reset metadata reaches owner-facing response/UI.

## 5. Documentation

Replaced stale `usage_unavailable` contract with safe public `quota_exceeded` behavior. Clarified that normal users receive failure guidance but no quota dashboard or policy metadata.

## 6. Risks / assumptions

- Both zero allowance and exhausted finite allowance intentionally use same public message. Internal codes and HTTP statuses remain distinct for service/admin diagnostics.
- “Administrator” means system administrator because quota policy remains system-admin-only.
- Clients continue treating quota failure as terminal and retry only route-level `rate_limited` responses.

## 7. Architecture checkpoint summary

- **Privacy:** only failure category and action guidance exposed; exact limits, consumption, remaining allowance, window, and reset time remain private admin metadata.
- **Ownership:** existing authenticated owner checks unchanged.
- **Deletion:** no persistence or lifecycle behavior changed.
- **Provider rules:** reservation and provider selection unchanged; rejected requests still make no provider call.
- **Structured-note contract:** EMIS structure and generated-content handling unchanged.

---

# MFA Challenge Guidance

## 1. Scope

- Made post-password authenticator-code flow easier to understand.
- Added small phone-and-timer SVG illustration.
- Added plain-language six-digit code and refresh guidance.
- Improved code input hints without changing MFA verification behavior.
- Placed verify and sign-out actions on one row while keeping logout independent from code validation.
- Fixed stale MFA stylesheet caching and increased spacing above the action row after browser screenshot review.
- Added six visual code slots, subtle digit-fill motion, and strict numeric-only normalisation.
- Kept refresh guidance static; omitted browser countdown because authenticator-device clocks may differ.

## 2. Checklist

- Complete: code and responsive styling.
- Complete: focused UI regression coverage.
- Complete: auth documentation.
- Remaining: none.

## 3. Files changed

- `app/templates/mfa_challenge.html`: visual, clearer instructions, accessible code input.
- `app/static/css/auth.css`: scoped MFA illustration and input styling.
- `tests/test_admin_ui.py`: MFA guidance and input-contract assertions.
- `docs/auth.md`: browser challenge UX contract.
- `docs/progress/2026/07/18 Daily Progress Note.md`: change record.

## 4. Tests

- Added focused static assertions for illustration, refresh guidance, numeric pattern, autofill, help association, and focus behavior.
- Updated browser-flow copy assertions.
- Added pending-MFA logout regression proving no code is required to sign out.
- Verified: `3 passed, 230 deselected`.
- Added cache-key and action-spacing regression coverage after reproducing the stacked-button layout.
- Verified natural-width buttons share one row at the reported 767px viewport in headless Chromium.
- Added regression assertions for six slots, numeric typing/paste guards, no live countdown, and reduced-motion fallback.
- Headless Chromium verified letter rejection, mixed-paste sanitisation, leading-zero preservation, slot updates, and 767px layout.

## 5. Documentation

- Updated `docs/auth.md`.
- Appended this entry to today’s daily progress note.

## 6. Risks / assumptions

- Assumes standard TOTP period remains about 30 seconds.
- Illustration is decorative and hidden from assistive technology; nearby text carries full meaning.
- Single input retained instead of hand-rolled segmented fields for better paste, autofill, and screen-reader behavior.
- Verify and logout remain separate POST forms; no nested forms or JavaScript validation bypass.
- Root cause of stacked screenshot: HTML changed while browser reused the earlier MFA stylesheet URL.
- JavaScript enhances one real text input; without JavaScript, native six-digit pattern validation and numeric keyboard hints remain.

## 7. Architecture checkpoint summary

- Privacy boundaries: no content or confidential fields added.
- Ownership rules: unchanged; existing pending-MFA user context remains required.
- Deletion semantics: unchanged.
- Provider rules: unchanged.
- Structured-note contract: unchanged.
- Auth security: same endpoint, CSRF handling, rate limit, session rotation, and TOTP verification path.

---

# Authenticated Home OpenScribe Branding

## 1. Scope

- Replaced normal-user home `Clinical workspace` eyebrow with splash-page OpenScribe feather logo and wordmark.
- Moved lockup styling into shared components so splash and authenticated home use same brand treatment.

## 2. Checklist

- [x] Code complete.
- [x] Focused home UI regression updated.
- [x] Daily progress documentation updated.
- [x] No schema, auth, ownership, deletion, provider, encryption, or structured-note change.
- [ ] Whole-repository suite not run.
- Open issue / escalate to Sol: none.

## 3. Files changed

- `app/templates/home.html`: authenticated home brand lockup.
- `app/templates/splashpage.html`: shared component cache key.
- `app/static/css/components.css`: shared OpenScribe lockup styles.
- `app/static/css/splash.css`: removed duplicated lockup styles.
- `app/static/css/home.css`: home lockup spacing.
- `tests/test_admin_ui.py`: rendered brand and shared CSS assertions.

## 4. Tests

- Focused home rendering regression verifies feather icon, OpenScribe wordmark, and removal of `Clinical workspace` text.

## 5. Documentation

- Appended this change to today's daily progress note.

## 6. Risks / assumptions

- Home brand link returns to current home route through `home_page_route`, preserving `/home`, `/home-restyled`, and `/home2` variants.
- Shared CSS cache key changed for splash and home so browsers receive lockup styles immediately.

## 7. Architecture checkpoint summary

- **Privacy:** no transcript-derived content or new data exposure.
- **Ownership:** unchanged; existing authenticated home gates remain authoritative.
- **Deletion:** unchanged.
- **Provider rules:** unchanged.
- **Structured-note contract:** unchanged.

---

# Transcribe Note Selector Delete Placement

## 1. Scope

- Moved selected-note trash control to right side of note-selector row.
- Kept selector and delete behavior unchanged.

## 2. Checklist

- [x] Code complete.
- [x] Focused rendered UI regression added.
- [x] Daily progress documentation updated.
- [x] No schema, auth, ownership, encryption, provider, or structured-note contract change.
- [ ] Whole-repository suite not run.
- Open issue: final focused pytest rerun blocked before collection by PostgreSQL connection failure; earlier hover/date focused run passed.

## 3. Files changed

- `app/templates/transcribe/_workspace.html`: wraps note selector and trash control in right-aligned flex row.
- `tests/test_admin_ui.py`: verifies selector precedes right-side delete control in rendered markup.
- `docs/progress/2026/07/18 Daily Progress Note.md`: change record.

## 4. Tests

- Focused transcribe document-switcher rendering regression covers row placement and existing delete control.

## 5. Documentation

- Appended this change to today's daily progress note.

## 6. Risks / assumptions

- Existing confirmation, DELETE request, and owner checks remain in place behind hover controls.
- Flex row uses existing utility classes; no new responsive breakpoint behavior added.

## 7. Architecture checkpoint summary

- **Privacy:** no content visibility change.
- **Ownership:** existing owner-only note deletion unchanged.
- **Deletion:** permanent deletion semantics unchanged; only control placement moved.
- **Provider rules:** unchanged.
- **Structured-note contract:** unchanged.

---

# Transcribe Note Pill Date Formatting

## 1. Scope

- Changed generated-note pill timestamps to compact `YY-MM-DD HH:MM` 24-hour format.
- Applied same formatting to initial server-rendered pills and live workspace refreshes.

## 2. Checklist

- [x] Code complete.
- [x] Static and rendered UI regressions added.
- [x] Daily progress documentation updated.
- [x] No schema, auth, ownership, deletion, encryption, provider, or structured-note contract change.
- [ ] Whole-repository suite not run; focused DB tests blocked by shared pytest lock.
- Open issue / escalate to Sol: none.

## 3. Files changed

- `app/templates/transcribe/_workspace.html`: formats initial note-pill timestamps.
- `app/static/js/transcribe/documents.js`: formats refreshed note-pill timestamps.
- `app/static/js/transcribe/app.js`, `app/static/js/transcribe/structured.js`: cache-bust updated documents module.
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: timestamp-format and rendered-output regressions.
- `docs/progress/2026/07/18 Daily Progress Note.md`: change record.

## 4. Tests

- `node --check app/static/js/transcribe/documents.js`: passed.
- Focused pytest attempts blocked before collection by another active run holding `/tmp/openscribe_pytest.lock`.

## 5. Documentation

- Appended this change to today's daily progress note.

## 6. Risks / assumptions

- `HH:HH` interpreted as conventional `HH:MM` (hours:minutes).
- UTC used in both server and browser formatter for deterministic output.

## 7. Architecture checkpoint summary

- **Privacy:** no content visibility change.
- **Ownership:** existing owner-only note access unchanged.
- **Deletion:** unchanged.
- **Provider rules:** unchanged.
- **Structured-note contract:** unchanged.

---

# Transcribe Note Hover Delete And Cache Fix

## 1. Scope

- Added small red trash affordance on each note pill, visible on hover/focus.
- Hover delete selects hovered note, then invokes existing canonical delete flow.
- Bumped app/CSS/JS cache keys so stale renderer cannot overwrite compact timestamps with raw datetimes.

## 2. Checklist

- [x] Code complete.
- [x] Focused rendered/static UI regressions added.
- [x] Daily progress documentation updated.
- [x] No schema, auth, ownership, encryption, provider, or structured-note contract change.
- [ ] Whole-repository suite not run.
- Open issue / escalate to Sol: none.

## 3. Files changed

- `app/templates/transcribe/_workspace.html`: static note hover delete controls; standalone delete control removed.
- `app/static/js/transcribe/documents.js`: dynamic note hover controls and timestamp formatter.
- `app/static/js/transcribe/actions.js`: hover controls route through existing selection/delete behavior.
- `app/static/css/transcribe.css`: red hover/focus affordance styling.
- Transcribe cache-bust URLs, tests, and this note updated.

## 4. Tests

- Earlier focused UI tests: `3 passed, 255 deselected` before final standalone-control removal.
- Final rerun blocked during `conftest` setup by `psycopg.OperationalError` connecting to test PostgreSQL.
- Node syntax checks passed for modified transcribe modules.

## 5. Documentation

- Appended this change to today's daily progress note.

## 6. Risks / assumptions

- Hover icon deletes hovered note after selecting it; existing confirmation, CSRF, endpoint, and owner authorization remain source of truth.
- Keyboard focus also reveals icon for accessibility.
- Root cause of timestamp regression: unchanged app.js cache URL loaded old renderer after initial server formatting.

## 7. Architecture checkpoint summary

- **Privacy:** no content visibility change.
- **Ownership:** existing owner-only note access/deletion unchanged.
- **Deletion:** permanent deletion semantics unchanged; UI affordance only.
- **Provider rules:** unchanged.
- **Structured-note contract:** unchanged.

---

# User And Leader Settings Workspace

## 1. Scope

- Added direct `/settings` workspace for normal users and team leaders while leaving `/home` unchanged.
- Matched canonical warm `/admin` visual language with a dedicated, ownership-scoped template and responsive sidebar shell.
- Added personal Preferences, Templates, Quick actions, and Smart phrases sections; leaders also receive AI services, Team members, and Account requests.
- Reused existing form handlers, service authorization, template editor, CSRF handling, and validated return navigation.
- Omitted leader hard user deletion by explicit design decision.

## 2. Checklist

- [x] Code complete.
- [x] Focused auth, role, navigation, preference, and editor-return tests added.
- [x] Active home/settings, testing, README, and daily progress docs updated.
- [x] No schema or migration change.
- [x] No privacy, ownership, encryption, provider-resolution, retention, or structured-note contract change.
- [ ] Whole-repository suite not run.
- Open issue / escalate to Sol: current `/home` still exposes a leader hard-delete control from pre-existing behavior; `/settings` does not copy it. Resolving `/home` requires separate architecture cleanup.

## 3. Files changed

- `app/routes/web_home_transcribe.py`: authenticated `/settings` route using existing home presentation context.
- `app/web/presentation.py`: closed settings return-view, route, template, and role-tab resolution.
- `app/main.py`: personalized settings responses use `Cache-Control: no-store`.
- `app/templates/settings.html`, `app/templates/settings/*.html`: dedicated settings shell and role-scoped sections.
- `app/static/css/settings.css`: canonical admin-inspired responsive settings visual system.
- `tests/test_admin_ui.py`: settings auth, role, deletion-boundary, modal, redirect, preference, and editor-return coverage.
- `README.md`, `docs/home_brief.md`, `docs/testing.md`: route and behavior documentation.

## 4. Tests

- Focused settings route suite verifies normal-user/leader rendering, system-admin redirect, no-store response, invalid-tab fallback, quick-action modal, closed redirect helpers, LLM preference return, and template-editor return.
- `.venv/bin/pytest -q` with seven named settings tests plus two inline-handler security tests: `9 passed`.
- `.venv/bin/pytest -q tests/test_web_refactor.py`: `15 passed`.
- Focused Home compatibility command: `9 passed` after replacing two stale pre-brand-heading assertions with current lockup assertions; `/home` implementation was not changed for settings.
- `.venv/bin/python -m compileall -q app`: passed.
- Scoped `git diff --check`: passed. Repository-wide check still reports unrelated trailing whitespace in concurrent `app/templates/login.html` work.
- Broader `-k settings_` command also selected one pre-existing transcribe test whose current sidebar markup no longer contains its expected Home link; this concurrent change is outside settings and remains unresolved separately.

## 5. Documentation

- Documented `/settings` as a direct-only configuration workspace distinct from consultation-oriented `/home`.
- Documented role sections, existing backend gate reuse, closed return navigation, and leader deletion omission.

## 6. Risks / assumptions

- Canonical `/admin` is `admin_mockup.html`; settings copies visual language only and never loads privileged admin context, all-team metadata, provider credentials, usage, or audit data.
- Existing mutation endpoints retain `/home/...` paths; `return_view=settings` controls safe post-action return without duplicating business logic.
- `/settings` is not linked from `/home` or transcribe yet, per direct-URL-only decision.

## 7. Architecture checkpoint summary

- **Privacy:** settings renders only current-user and same-team configuration already available through `render_home`; no transcript-derived content or raw provider secrets.
- **Ownership:** personal assets remain owner-scoped; leader assets, members, requests, and provider policy retain existing same-team service checks.
- **Deletion:** personal template/action deletion remains existing owner action; leader member hard deletion is absent from settings; transcript-root cascades and retention unchanged.
- **Provider rules:** leaders select only admin-provisioned team providers; credential visibility and fallback behavior unchanged.
- **Structured-note contract:** existing template editor and EMIS section contract reused unchanged.

---

# Settings Primary Button Hover Contrast

## 1. Scope

- Fixed primary link-buttons losing visible text on hover in `/settings`.
- Reused transcriber Create button colors through existing shared tokens: teal deep for rest state and teal muted for hover, with white text retained.

## 2. Checklist

- [x] Code complete.
- [x] Focused shared-style regression added.
- [x] Settings component cache key updated.
- [x] Daily progress documentation updated.
- [x] No schema, auth, ownership, deletion, provider, or structured-note change.
- [ ] Whole-repository suite not run.
- Open issue / escalate to Sol: none.

## 3. Files changed

- `app/static/css/components.css`: primary hover/focus explicitly retains white text.
- `app/templates/settings.html`: cache-busts shared component CSS.
- `tests/test_admin_ui.py`, `tests/test_web_refactor.py`: asset and transcriber-token parity coverage.
- `docs/progress/2026/07/18 Daily Progress Note.md`: change record.

## 4. Tests

- Focused regression verifies shared accent colors exactly match transcriber `teal.deep` and `teal.muted`, and primary hover retains white text.
- Focused settings render + hover regression: `2 passed`.
- Full `tests/test_web_refactor.py` attempt reached `16 passed, 1 failed`; remaining failure is unrelated concurrent transcriber `app.js` cache-key drift during this change.
- Scoped `git diff --check`: passed.

## 5. Documentation

- Appended this fix to today's daily progress note.

## 6. Risks / assumptions

- Shared rule improves every primary anchor/button using component classes; secondary, ghost, danger, and icon controls remain unchanged.
- Explicit white hover color prevents global `a:hover` from overriding primary anchor contrast.

## 7. Architecture checkpoint summary

- Privacy, ownership, deletion, provider resolution, encryption, retention, and structured-note contracts unchanged; CSS-only behavior fix.

---

# Transcribe Sidebar OpenScribe Branding

## 1. Scope

- Reused splash/home feather lockup in transcribe sidebar.
- Made the sidebar brand lockup the retained route back to `/home` after removing the separate Home control.
- Updated the navigation regression to check the retained route and accessible label instead of removed visible `Home` copy.
- Reused page-scoped override to increase sidebar wordmark slightly, from `1.125rem` (`18px`) to `1.25rem` (`20px`).

## 2. Checklist

- [x] Code complete.
- [x] Focused UI regression added.
- [x] Daily progress documentation updated.
- [x] No schema, auth, ownership, deletion, encryption, provider, or structured-note change.
- [ ] Whole-repository suite not run.
- Open issue: broader `tests/test_web_refactor.py` rerun hit a test-harness database connection failure during `conftest` import; focused regressions passed first.

## 3. Files changed

- `app/templates/transcribe/_sidebar.html`: identifies shared brand lockup for compact sidebar sizing.
- `app/templates/transcribe/_head_assets.html`: cache-busts updated shared and page CSS.
- `app/static/css/transcribe.css`: sets compact sidebar wordmark to `1.25rem` (`20px`).
- `tests/test_web_refactor.py`, `tests/test_admin_ui.py`: brand and asset-version regressions.
- `docs/progress/2026/07/18 Daily Progress Note.md`: change record.

## 4. Tests

- Static regression verifies shared feather markup and exact `1.25rem` compact wordmark size.
- Render regression verifies updated transcribe stylesheet asset key.
- `.venv/bin/pytest -q tests/test_web_refactor.py tests/test_admin_ui.py -k "splash_and_transcribe_styles_are_cacheable_static_assets or transcribe_sidebar_reuses_brand_lockup_at_compact_wordmark_size or transcribe_page_includes_mobile_layout_assets"`: latest `20px` check passed, `3 passed, 251 deselected`.
- Later full `tests/test_web_refactor.py` rerun did not collect because PostgreSQL connection failed during shared `conftest` setup.

## 5. Documentation

- Appended this change to today's daily progress note.

## 6. Risks / assumptions

- Existing uncommitted sidebar cleanup remains intact; repaired its missing footer closing tag while touching same partial.
- Shared mark keeps splash/home dimensions; sidebar wordmark remains smaller than splash/home `2rem` (`32px`) wordmark.

## 7. Architecture checkpoint summary

- **Privacy:** no transcript-derived content or metadata exposure change.
- **Ownership:** existing owner-only transcribe access unchanged.
- **Deletion:** no lifecycle behavior changed.
- **Provider rules:** selection and credential handling unchanged.
- **Structured-note contract:** unchanged.

---

# Normal User Home Status Bar

## 1. Scope

- Hid home service status cards from normal users.
- Leaders retain status cards on standard home and `home2`; normal users retain existing preference controls.

## 2. Checklist

- [x] Code complete.
- [x] Role-based rendering regression added.
- [x] Daily progress documentation updated.
- [x] No schema or migration change.
- [x] No ownership, deletion, encryption, provider-resolution, or structured-note change.
- [ ] Whole-repository suite not run.
- Open issue / escalate to Sol: none.

## 3. Files changed

- `app/templates/home.html`: manager-only guards around both service status bars.
- `tests/test_admin_ui.py`: normal-user and leader visibility coverage across home routes.
- `docs/progress/2026/07/18 Daily Progress Note.md`: change record.

## 4. Tests

- Focused browser rendering regression verifies normal users do not receive `stat-grid`, while leaders do on `/home` and `/home2`.

## 5. Documentation

- Appended this change to today's daily progress note.

## 6. Risks / assumptions

- `is_manager` remains existing role gate for leader/system-admin management visibility; no new authorization logic added.
- Lower user preference controls remain unchanged because request targets top status bar only.

## 7. Architecture checkpoint summary

- **Privacy:** reduces provider metadata shown to normal users; no transcript-derived content exposed.
- **Ownership:** existing authenticated ownership gates unchanged.
- **Deletion:** unchanged.
- **Provider rules:** provider selection behavior unchanged; only summary visibility changed.
- **Structured-note contract:** unchanged.
