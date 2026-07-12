# Styling Condensation Plan

## Scope

This plan reviews application HTML templates and their inline styling, excluding pages whose filename ends in `2`, such as `admin2.html` and `_home2_admin2_style.html`. Generated security report HTML under `docs/Compliance/OWASP/.../zap/` is not application UI source and is out of scope.

Primary visual priorities:

- Preserve `/transcribe` appearance and workflow layout. This page contains transcript-derived content and must not be visually or behaviorally destabilised by broad styling moves.
- Treat `splashpage.html` as the preferred public-facing visual template. Its token palette, button treatment, brand header, card language, and soft clinical-paper aesthetic should drive shared style extraction.
- Consolidate splash and transcribe first, because they define the desired product feel. Other pages should adopt shared styles only after those two are stable.

## Current State

Most styling is embedded directly in Jinja templates. External CSS exists, but it is not yet the shared design system.

Implementation status:

- `splashpage.html` has had its inline CSS extracted to `app/static/css/splash.css` with no selector changes.
- `transcribe/_head_assets.html` has had its inline CSS extracted to `app/static/css/transcribe.css` with no selector changes.
- `app/static/css/tokens.css` now provides shared splash/transcribe color, font, radius, shadow, and alias tokens.
- `components.css` now provides shared buttons, panels, forms, pills, tabs, modals, flashes, and toasts used by splash, transcribe, home, and template editor.
- `home.html` now links `tokens.css`, `components.css`, `home.css`, and conditionally `home2.css`; its large inline style block has been removed.
- Auth/recovery pages now link `tokens.css`, `components.css`, and `auth.css`; duplicated inline auth/recovery style blocks have been removed.
- `template_editor.html` now links `tokens.css`, `components.css`, and `template-editor.css`; its inline style block has been removed and form/button controls use shared components.
- `admin.html` now links `tokens.css`, `components.css`, and `admin.css`; its inline style block has been removed and feedback primitives come from shared components.

| File | Current styling source | Notes |
| --- | --- | --- |
| `app/templates/splashpage.html` | Linked `tokens.css`, `components.css`, and `app/static/css/splash.css` | Strongest public visual system. Button primitive now comes from shared components. |
| `app/templates/transcribe/_head_assets.html` | Linked `tokens.css`, `components.css`, and `app/static/css/transcribe.css` | Largest style surface. Toast/flash primitive now comes from shared components; workflow CSS stays local. |
| `app/templates/transcribe.html` | Includes `_head_assets.html`, links `transcribe.css` and `transcribe-mobile.css` | Shell is already split into partials. Stylesheet extraction started; token/component dedupe remains. |
| `app/static/css/transcribe-mobile.css` | External CSS | Useful existing central file for responsive transcribe sidebar/header behavior. Keep and possibly fold into future `transcribe.css`. |
| `app/static/css/transcribe-tailwind.css` | External CSS, currently empty | Linked but not useful as central style today. Do not rely on it until build pipeline is fixed or replaced. |
| `app/static/css/transcribe-tailwind.input.css` | Tailwind input only | Build source only, not current shared design language. |
| `app/templates/home.html` | Linked `tokens.css`, `components.css`, `home.css`, and conditional `home2.css` | Big app-page style system. Shared primitives live in `components.css`; home-only layout stays in `home.css`. |
| `app/templates/admin.html` | Linked `tokens.css`, `components.css`, and `app/static/css/admin.css` | Admin shell/provider/directory/usage layouts stay page-specific. Shared tokens/components own base palette/fonts and feedback primitives. |
| `app/templates/login.html` | Linked `tokens.css`, `components.css`, `auth.css` | Auth landing layout now uses shared auth shell. |
| `app/templates/onboarding.html` | Linked `tokens.css`, `components.css`, `auth.css` | Shared auth shell plus onboarding stepper/recovery-code styles in `auth.css`. |
| `app/templates/mfa_challenge.html` | Linked `tokens.css`, `components.css`, `auth.css` | Shared auth shell plus MFA inline-check styling in `auth.css`. |
| `app/templates/request_access.html` | Linked `tokens.css`, `components.css`, `auth.css` | Shared auth shell plus textarea layout. |
| `app/templates/password_reset_request.html` | Linked `tokens.css`, `components.css`, `auth.css` | Shared auth shell. |
| `app/templates/password_reset_confirm.html` | Linked `tokens.css`, `components.css`, `auth.css` | Shared auth shell. |
| `app/templates/template_editor.html` | Linked `tokens.css`, `components.css`, and `app/static/css/template-editor.css` | Editor shell/list/action bar stay page-specific. Shared components own rounded buttons, forms, and flashes. |
| `app/templates/glm-3.html` | Removed | Unused prototype template and its legacy `/transcribe-glm-2` alias removed. |
| `loading_animation.html` | Removed | Standalone demo removed after confirming the active loader lives in transcribe workspace/CSS/JS. |

## Existing Overlap Between Splash And Transcribe

Splash and transcribe already share the same broad visual vocabulary, but it is duplicated rather than centralised.

| Shared concept | Splash source | Transcribe source | Consolidation decision |
| --- | --- | --- | --- |
| Core palette | `--bg`, `--parchment`, `--card`, `--ink`, `--slate`, `--muted`, `--border`, `--accent`, `--accent-soft`, `--accent-pale` | `--bg`, `--fg`, `--muted`, `--accent`, `--accent-soft`, `--card`, `--border`, status colors | Extract `tokens.css`. Prefer splash token names, add aliases for transcribe like `--fg: var(--ink)` to avoid visual churn. |
| Paper/noise background | `body::before` noise overlay | `.noise-bg` fixed overlay | Extract reusable `.noise-bg` or body helper. Keep transcribe overlay behavior unchanged. |
| Buttons | `.button`, `.button.secondary`, `.button.ghost` | Many bespoke buttons, `.btn-primary--compact`, dictation buttons, template picker buttons | Create base `.button` component from splash. Do not force transcribe-specific control buttons into it until visual parity screenshots pass. |
| Cards/panels | `.flow-card`, `.feature-card`, `.value-card`, `.cta-panel` | session items, workspace panels, modals, picker options, note cards | Extract shared surface tokens and generic `.surface-card`. Keep transcribe layout classes page-specific. |
| Pills/badges/chips | `.eyebrow`, `.chip`, `.badge` | `.status-pill-count`, `.template-picker-button__mode`, `.pii-type`, status pills | Centralise badge/pill primitives. Transcribe status pills can inherit color/radius variables but keep layout-specific classes. |
| Toasts/flash | Request/auth pages and transcribe have duplicate `.toast`, `.toast-container`, `.flash` variants | `.toast-container`, `.toast`, `.flash-banner` | Move to `components.css`. This is low-risk if class names and JS selectors stay stable. |
| Modals/dialogs | Splash has no functional modal, but card/dialog visual language exists | template picker modal, dictation modal, consult boundary modal | Extract modal shell variables and optional `.modal`, `.modal__backdrop`, `.modal__dialog`. Keep transcribe class names as wrappers or aliases. |
| Audio/recording visuals | splash demo `.waveform`, `.audio-card` | real dictation modal bars, recording states, status pulses | Share animation/color tokens only. Functional transcribe controls must remain page-specific. |
| Loading animation | transcribe `.note-generation-loading` | transcribe note/follow-up loading | Keep in `transcribe.css`; it is workspace-specific and used by active note/follow-up loading states. |

## Important Differences Between Splash And Transcribe

These differences matter and should not be flattened blindly.

| Difference | Why it matters | Alignment recommendation |
| --- | --- | --- |
| Splash is public marketing; transcribe is private workspace | Transcribe carries transcript-derived content and dense clinical actions. Marketing spacing and large typography would reduce usability. | Share tokens and components, not page layout. |
| Splash uses big Georgia display type | Strong brand feel on public page. Transcribe uses `DM Sans` plus utility classes for dense UI. | Keep splash display type for marketing headings. Use shared typography tokens, but let transcribe keep smaller product typography. |
| Splash scrolls vertically; transcribe is fixed-height workspace | Transcribe depends on `h-screen`, panes, scroll containers, and mobile sidebar behavior. | Do not move transcribe shell layout into global base. Put it in `transcribe.css`. |
| Splash cards are decorative; transcribe cards are functional | Transcribe cards hold editable note rows, selectors, modals, and polling states. | Share colors, borders, radii, shadows. Keep interaction styles local. |
| Splash motion is decorative; transcribe motion communicates state | Transcribe recording/loading/status animation affects user trust and workflow clarity. | Centralise animation tokens and keyframes only if names stay stable or tests update intentionally. |
| Splash buttons are CTA-heavy; transcribe buttons are compact controls | Large marketing CTA styles do not fit toolbar buttons. | Create button size variants: `.button`, `.button--secondary`, `.button--compact`, `.button--icon`. Migrate transcribe compact buttons later. |

## Proposed Central Styles

Create central CSS files under `app/static/css/`. Version query strings can follow current cache-bust pattern.

| File | Purpose | Initial source |
| --- | --- | --- |
| `tokens.css` | Color, radius, spacing, shadow, typography, z-index, status colors | Start from `splashpage.html`; add transcribe-safe aliases. |
| `base.css` | Reset, body defaults, headings, links, form defaults, focus rings, `[hidden]` | Start from splash and auth pages. Keep layout-light. |
| `components.css` | Buttons, panels/cards, badges/pills, flash, toasts, modals, empty states, loading animation | Start from splash plus transcribe toast/modal/loading pieces. |
| `splash.css` | Public landing layout: nav, hero, workflow demo, feature/value/CTA sections | Move remaining splash-only classes here. |
| `transcribe.css` | Product workspace layout and transcribe-only components | Move `_head_assets.html` inline CSS here in controlled sections. |
| `auth.css` | Login, request access, password reset, MFA, onboarding shell helpers | Extract from duplicated auth pages after base/components exist. |
| `app-pages.css` | Shared authenticated app shell, sidebars, tabs, panels for home/admin/template editor | Later phase after splash/transcribe/auth consolidation. |

Do not use `transcribe-tailwind.css` as the target unless the Tailwind build is restored. It is currently empty, so it is not a real central style.

## Phase 1: Consolidate Splash First

Target: `splashpage.html` still looks identical, but its large inline style becomes shared CSS plus `splash.css`.

Steps:

1. Add `tokens.css` from splash tokens.
2. Add `base.css` for reset, page background, typography defaults, links, focus behavior.
3. Add `components.css` for `.button`, `.eyebrow`, `.badge`, `.chip`, generic cards, status colors.
4. Add `splash.css` for `.site-header`, `.nav`, `.brand`, `.hero-grid`, `.workflow`, `.flow-card`, `.feature-grid`, `.value-grid`, `.cta-panel`, splash media queries.
5. Update `splashpage.html` to link CSS files and remove inline style.
6. Visual-check desktop and mobile splash page before touching transcribe.

What is unique to splash and should stay page-specific:

| Splash section | Unique reason | Central replacement possible |
| --- | --- | --- |
| `.site-header`, `.nav`, `.brand`, `.brand-mark`, `.brand-name` | Public marketing navigation and brand lockup. | Only tokens/buttons shared. Keep layout in `splash.css`. |
| `.hero`, `.hero-grid`, `.hero-copy`, `.hero-actions` | Marketing landing composition. | Share button and typography tokens only. |
| `.workflow-wrap`, `.workflow`, `.flow-card`, `.flow-node`, `.waveform`, `.progress` | Product-demo illustration, not real workflow UI. | Card surface and audio visual tokens may inform transcribe, but markup stays splash-only. |
| `.feature-band`, `.feature-grid`, `.feature-card`, `.value-grid`, `.value-card` | Marketing content layout. | Generic card component can cover border/radius/background; grid stays splash-only. |
| `.final-cta`, `.cta-panel`, `.cta-actions` | Marketing CTA. | Share panel/button styles; layout stays splash-only. |

## Phase 2: Consolidate Transcribe Without Visual Change

Target: `/transcribe` should render the same. This is extraction, not redesign.

Steps:

1. Link `tokens.css`, `base.css`, `components.css`, and `transcribe.css` from `transcribe/_head_assets.html`.
2. Move the full inline CSS from `transcribe/_head_assets.html` into `transcribe.css` with no selector changes first.
3. Keep `transcribe-mobile.css` linked as-is during first extraction.
4. Only after no visual regression, remove duplicate token definitions from `transcribe.css` and rely on `tokens.css` aliases.
5. Move shared `.toast`, `.flash-banner`, modal shell pieces, and loading animation into `components.css` only when class compatibility is preserved.
6. Keep page-specific workspace selectors in `transcribe.css`: session list, split panes, note editor, transcript review grid, dictation modal, PII sidebar, tour overlay, structured statement rows.

What is unique to transcribe and should remain page-specific:

| Transcribe section | Unique reason | Central replacement possible |
| --- | --- | --- |
| Fixed workspace height and overflow rules | Core app shell behavior. Affects pane scrolling and mobile sidebar. | No. Keep in `transcribe.css`. |
| `.session-item` active/selected states | Navigation state for transcript roots. | Token colors can centralise; selectors stay local. |
| `.structured-statement-list`, `.statement-row`, drag handles, checkboxes, editors | Editable structured note contract and drag behavior. | No broad replacement. Share focus colors only. |
| `.smart-phrase-menu` | Editor-specific autocomplete. | Modal/menu primitive may help later; keep local first. |
| `.main-tab`, `.main-panel` | Transcribe panel switching. | Could use shared tabs later, but not first pass. |
| `.template-picker-*` | Functional template chooser with modal/list states. | Modal shell can be shared. Picker classes stay local. |
| `.transcript-review-grid`, `.pii-sidebar`, `.pii-*` | Redaction/debug/transcript review workflow. | No. Keep local. Privacy-sensitive. |
| `.dictation-*`, `.record-*`, `.mic-*` | Recording controls and live state feedback. | Share tokens/keyframes cautiously. Keep layout local. |
| `.note-editor-toolbar`, `.note-generation-loading` | Note editing and generation state. | Loading animation can become shared component; toolbar stays local. |
| tour/overlay classes | Onboarding tour behavior depends on data hooks. | Keep local. |

Where transcribe can align with splash safely:

| Area | Safe alignment | Risk |
| --- | --- | --- |
| Palette | Use splash tokens and transcribe aliases. | Low if computed colors remain equivalent. |
| Radius and border tokens | Normalize card/control radii through variables. | Medium because dense controls may change subtly. Apply after screenshots. |
| Toasts and flashes | Use shared component CSS. | Low if class names remain stable. |
| Modals | Use shared backdrop/dialog variables. | Medium because modal z-index and scroll behavior matter. |
| Buttons | Align only generic `.button` and compact variants. | Medium/high if transcribe controls resize. Defer until after extraction. |
| Loading animation | Move to shared component. | Low/medium. Already reused in note/follow-up. Keep markup stable. |

## Phase 3: Auth Pages Use Shared Shell

The auth-style pages duplicate the same compact CSS. They are good second wave after splash/transcribe tokens exist.

| Page | Unique styling | Replace with central styles? |
| --- | --- | --- |
| `password_reset_request.html` | None beyond message variant and narrow panel shell. | Yes. Use `auth.css`, `components.css`, shared form/button/panel/message. |
| `password_reset_confirm.html` | Invalid-token error message and password form. | Yes. Same auth shell. |
| `request_access.html` | Textarea, success/error flash, toast script target. | Yes. Shared auth shell plus textarea/form controls/toasts. |
| `mfa_challenge.html` | Inline checkbox row, signed-in metadata, error-only toast. | Mostly. Keep `.inline-check` as auth-specific utility. |
| `onboarding.html` | Stepper, QR block, secret/recovery code list, sticky hero. | Partly. Shared auth shell, forms, buttons, flashes, toasts. Keep stepper/QR/recovery styles in `auth.css`. |
| `login.html` | Two-column login landing, feature list, setup/sign-in card, toast transform. | Partly. Shared auth shell/buttons/forms/toasts. Keep feature-list and setup/sign-in layout in `auth.css`. |

## Phase 4: Authenticated App Pages

These pages should come after shared tokens/components are stable. They are larger and more likely to have role-gated UI branches.

| Page | Unique styling | Replace with central styles? |
| --- | --- | --- |
| `home.html` | User dashboard panels, template/action/document cards, dropdowns/modals, tabbed panes, usage surfaces. | Partly. Use tokens, buttons, panels, badges, toasts, modal shell. Keep page layout and domain-specific cards local until audited. |
| `admin.html` | Admin sidebar, provider/team/user cards, tables, modals, destructive action forms. | Partly. Use tokens/forms/buttons/tables/modals. Keep admin shell and dense management layouts local. |
| `template_editor.html` | Editor sidebar, prompt textareas, section rows, sticky action bar. | Partly. Use app shell/sidebar/form/button tokens. Keep sticky action bar and editor-specific prompt styles local. |
| `glm-3.html` | Removed obsolete prototype. | Removed with legacy alias. |
| `loading_animation.html` | Removed obsolete standalone animation demo. | Active loader remains in transcribe workspace. |

### Home/Subpage Audit Notes

Confirmed authenticated-app slices now cover `home.html`, `template_editor.html`, and `admin.html`. `admin2.html` stays excluded by filename rule, and `glm-3.html` was removed as obsolete.

Safe to harmonise now:

- Token roots and font families: `home.html` and `template_editor.html` now consume `tokens.css` directly.
- Button families: `btn-primary`, `btn-secondary`, `btn-ghost`, `btn-danger`, and small variants overlap strongly with splash/app component language.
- Form fields: `input`, `select`, `textarea`, checkbox/radio focus styles overlap across home, admin, editor, and auth pages.
- Panels/cards: `.panel`, `.panel--interactive`, `.panel--info`, stat cards, asset cards, provider cards, and editor rows share border/background/radius/shadow language.
- Feedback: `.flash`, `.toast-container`, `.toast`, and status variants are good candidates for `components.css`.
- Tab navigation: `.tab-shell__nav` and `.tab-shell__tab` are duplicated between home and admin.
- Modals: home modal shell/dialog/header/close treatment can seed a shared modal component, with z-index/scroll behavior preserved.

Keep page-specific:

- Home dashboard composition, tab panel visibility, tour overlay, smart phrase drawer, service configuration rows, account-request review layout, and asset library metadata grids.
- Template editor shell, template list item states, section prompt rows, mode-specific form behavior, and sticky action bar remain page-specific in `template-editor.css`.
- Admin provider setup, team/user directory tables, usage/audit tables, destructive-account controls, provider inspection/test result blocks, and credential-related form grouping remain page-specific in `admin.css`.

Borderline decisions made:

- Use shared `tokens.css` for `home.html` and `template_editor.html` now, accepting small visual shift toward the shared product palette.
- Admin should eventually adopt rounded shared app components rather than keep square utilitarian panels, but in a separate pass.
- First implementation slice is `home.html` + `template_editor.html`; admin follows later.
- Template editor extraction should use moderate rounding: shared rounded buttons/forms/flashes, but keep sidebar/action-bar/list rows close to existing layout and keep the translucent sticky action bar.

## Migration Rules

- Do not change route paths, form actions, CSRF fields, authorization checks, owner-only transcript access, provider-selection behavior, or deletion behavior.
- Do not use CSS extraction to rename data hooks used by JavaScript tests or runtime code.
- Do not make global CSS responsible for transcribe pane layout, recording layout, or transcript review layout.
- Prefer additive central styles first, then remove inline duplication page by page.
- Keep screenshots or browser checks for splash and transcribe after each extraction step.
- Keep no-JS form submission behavior for auth/admin/home pages.
- Keep all transcript-derived content out of logs and browser storage. Styling work must not alter content handling.

## Testing Plan

For each extraction phase:

| Area | Check |
| --- | --- |
| Splash | Browser screenshot desktop and mobile. Verify nav anchors, login/request links, responsive workflow cards. |
| Transcribe | Focused render tests plus browser pass for session list, active transcript, note tabs, generation loading, template picker, dictation modal, mobile sidebar. |
| Auth pages | Render tests for form actions/CSRF fields and no-JS submissions. |
| Home/admin/template editor | Existing role-gated render tests, form target assertions, modal/dropdown smoke checks. |
| Static assets | Test CSS links exist and cache-bust strings are updated when required. |

Use focused pytest commands through project venv, for example:

```bash
.venv/bin/pytest -q tests/test_admin_ui.py -k "transcribe or splash or login"
.venv/bin/pytest -q tests/test_web_refactor.py
```

## Recommended First Pull Request

Smallest safe first slice:

1. Add `tokens.css`, `base.css`, `components.css`, and `splash.css`.
2. Convert only `splashpage.html` to linked CSS.
3. Keep selector names and markup stable.
4. Add or update render/static tests for CSS links.
5. Manually browser-check splash desktop and mobile.

Second slice:

1. Add `transcribe.css` by moving current `_head_assets.html` CSS unchanged.
2. Keep `transcribe-mobile.css` unchanged.
3. Link central tokens/base/components but do not de-duplicate yet.
4. Browser-check `/transcribe` before removing inline style.

Third slice:

1. Remove duplicate transcribe token definitions once visual parity is confirmed.
2. Move only obvious shared parts from `transcribe.css` to `components.css`: toasts, flashes, modal shell variables, loading animation.
3. Leave dense workflow classes local.

## Architecture Checkpoint

- Privacy boundaries: this plan is presentation-only. It does not broaden access to transcript-derived content.
- Ownership rules: `/transcribe` remains owner-only. No CSS or template consolidation should move authorization logic into frontend code.
- Deletion semantics: no deletion flow changes. Destructive form styling must preserve existing confirmation and POST targets.
- Provider rules: no provider selection or fallback logic changes. Provider labels remain display-only.
- Structured-note contract: no change to EMIS section keys, note JSON shape, or structured note editing behavior.
