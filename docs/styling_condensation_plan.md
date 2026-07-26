# Frontend Styling Architecture

## Status

The initial CSS extraction/consolidation is implemented. This document records the current styling boundary and remaining safe cleanup work. The canonical authenticated Scribe surface is `/workspace`; `/transcribe` is a compatibility redirect and must not be treated as the primary route in styling plans.

Generated security-report HTML under `docs/Compliance/` is evidence output, not application UI source. Preview/prototype templates whose routes are not canonical must not drive the shared design system.

## Current shared layers

Application CSS is served from the same origin and must remain compatible with the enforced CSP.

### `tokens.css`

Shared design variables for:

- core palette and status colors;
- typography aliases;
- radii;
- shadows;
- spacing/z-index where centralized.

Page-specific CSS can use compatibility aliases where removing old variable names would create unnecessary churn.

### `components.css`

Shared low-risk primitives such as:

- buttons and size/state variants;
- cards/panels;
- form controls;
- pills/badges;
- tabs;
- modal/dialog shells;
- flashes/toasts;
- empty/loading states where truly reusable.

A component belongs here only when semantics and layout are consistent across pages. Do not force dense clinical-workspace controls into a marketing component merely because both are buttons/cards.

### Page/area-specific styles

- `splash.css`: public marketing layout/visuals;
- `transcribe.css` and responsive workspace CSS: Scribe/consultation controls, panes, rail, editors, recording, dictation, PII, generation, tours;
- `workspace.css` or current workspace-shell assets: permanent user navigation/settings/library/team layout;
- `admin.css`: admin shell, provider/directory/usage/audit/danger layouts;
- `auth.css`: login/request/reset/onboarding/MFA;
- `home.css`: transitional `/home` compatibility surface;
- `template-editor.css` and other focused assets where still used.

Page-specific workflow selectors should remain local when moving them would risk behavior, accessibility, scroll ownership, or privacy-sensitive rendering.

## Implemented extraction

The main large inline style blocks were moved into linked same-origin stylesheets for the splash, Scribe assets, Home, auth/recovery pages, Template editor, and Admin workspace. Shared tokens/components now provide base palette, buttons, forms, panels, pills/tabs, modal, feedback, and related primitives where compatible.

Templates should not use inline `style="..."` attributes. Dynamic visual values should be represented through escaped/clamped data attributes and applied by nonce-approved JavaScript through direct CSS properties.

Run:

```bash
rg '\bstyle\s*=' app/templates
pytest -q tests/test_xss_coverage.py tests/test_cookie_csrf_security.py
```

## Visual boundaries

### Splash versus authenticated product

The splash page can use larger display typography, decorative motion, wider marketing spacing, and illustrative cards.

Authenticated Scribe/Admin/Library/Account pages require denser accessible controls, predictable layout, internal scroll ownership, and state-signaling motion. Share tokens/primitives, not entire page composition.

### Scribe workspace

Scribe styling remains high-risk because it controls:

- bounded viewport and pane scrolling;
- consultation rail;
- Working-note/generated-note editors;
- structured statement rows and drag/copy controls;
- recording/VAD/dictation state;
- template picker;
- PII/redaction review;
- loading/polling/status state;
- mobile consultation/navigation overlays.

Do not do broad selector renames/reparenting without focused UI/browser regression coverage. Privacy-sensitive content should not be duplicated into hidden DOM solely for styling convenience.

### Permanent workspace

The shared workspace shell owns sidebar/header/mobile drawer and general settings/library/team page layout. Scribe has intentional exceptions for internal scrolling and edge-to-edge Library rails.

Avoid applying generic form/layout selectors to Scribe based only on element type; use area/component classes so transcriber title/editor/recording controls keep their dedicated behavior.

### Admin

Admin shares tokens/components but keeps tab/directory/provider/usage/audit/table/danger-zone layout local. Charts use the vendored ECharts runtime; style changes must not introduce CDN dependencies.

### Auth and public forms

Auth/request/reset/onboarding/MFA pages share a compact auth shell. Keep password/TOTP/reset/setup values out of DOM diagnostics/snapshots and preserve server-rendered validation/fallback behavior.

## CSP and dependency rules

- Production runtime CSS, fonts, scripts, WASM, and models are same-origin.
- No public CDN runtime dependencies.
- No inline event attributes.
- No inline style attributes.
- Nonce-approved scripts can set specific safe properties; avoid `cssText`/style-string construction.
- Keep external stylesheet links versioned/cacheable through the current asset pattern.

## Safe consolidation sequence

For each area:

1. Capture current desktop/tablet/mobile behavior with synthetic data.
2. Move declarations without selector/markup changes first.
3. Verify computed appearance and interaction.
4. Replace duplicate literals with tokens.
5. Replace duplicate low-risk components while preserving JS/data/ARIA hooks.
6. Remove old selectors only after repository search and browser tests show no use.
7. Update CSP/XSS and responsive coverage.

Do not combine extraction, redesign, route migration, and behavior refactoring in one change.

## Remaining cleanup

Potential focused work:

- identify genuinely shared workspace/admin/settings layout primitives without flattening area-specific scroll behavior;
- remove unused/empty build artifacts such as Tailwind outputs only after verifying no template references;
- reduce duplicate status/button/modal declarations that remain byte-for-byte compatible;
- consolidate responsive breakpoints/tokens where current behavior matches;
- document/automate visual regression snapshots for canonical `/workspace`, `/admin`, auth, Library, and splash routes;
- retire `/home` CSS only after the route migration is complete;
- review preview/prototype route assets and remove them with their routes when no longer needed.

## Verification matrix

At minimum review:

- splash desktop/mobile;
- login/request/reset/onboarding/MFA;
- `/workspace` Scribe with empty and populated consultations;
- recording/live/dictation/modal states;
- Working note freeform/structured and generated-note editing;
- Account/Preferences/Library/leader Team pages;
- `/admin` global/selected-team/provider/member/quota/usage/audit/danger states;
- keyboard focus, dialogs, collapsed/off-canvas navigation;
- high-content/long-label/error/empty/loading cases.

Use synthetic content only. Do not capture real transcript/note/PII data in visual evidence.
