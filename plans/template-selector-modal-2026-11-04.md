# Template selector modal

**Status:** Implemented; clinician verification pending
**Date:** 4 November 2026
**Purpose:** Intended behavior for the clinician’s main Scribe template picker. This plan does not describe or change current runtime behavior.

## Intended behavior

Upgrade the main Scribe template picker into a searchable modal while preserving the existing selection and save/revert behavior.

- Follow `DESIGN.md`’s “The Quiet Study”: warm paper-like surfaces, dark readable text, and restrained deep teal. Keep the modal calm and practical.
- Place a clear heading, close control, and labelled search field above a scrollable, single-column list of full-width button rows. Keep the heading and search fixed while results scroll.
- Use the selected “Compact rows” and “Warm header” treatment: a parchment title/search band, integrated search icon and clear control, compact continuous rows, and a scrollable result region. Keep the search label programmatic rather than visible.
- Use a brief opacity and small vertical-shift transition to connect the dialog with its overlay; immediately hide it when reduced motion is requested. Closing must restore focus and background interaction at once, even while the visual exit finishes.
- Each row shows the template title; an optional one-line, smaller description with nowrap ellipsis; the note mode (“Sectioned note” or “Free text note”); and “Personal” or “Team” metadata. Show selection with the pale-blue row highlight and an accessible pressed state, without a separate tick or “Selected” label. Keep the full title and description in the DOM and accessibility tree; search both in full. Do not add a description storage cap or rely on hover to reveal content.
- Provide sibling actions on each row to edit a template when the user is authorized, otherwise open it in view mode, and to generate with that template. Route edit/view through the existing library page and save pending editors before navigation. Route generation through the existing form and consultation generation path; await the canonical template-change save/revert guard, verify the requested template and consultation remain current, and respect the form's existing disabled state. Hide and block this per-row shortcut while the shared split-create workflow is enabled or its multiple-problems state is saving; the main Generate action continues to use the established split workflow.
- Filter locally as the clinician types, case-insensitively, after trimming outer query whitespace. Use deterministic contiguous matching of the normalized query against the normalized full title or description. Whitespace-only search shows all templates. Preserve the server’s `available_templates` order, including defaults and favourites; do not sort. Hidden rows must not be focusable. Typing never changes selection; retain the selected template ID when filtering it out.
- Clear the search when the modal reopens and provide an accessible clear-search action. Announce result counts and no-match state politely. Distinguish “no templates available” from “no matches”.
- Show only the existing active, authorized personal-owner/team template list. Do not change API, schema, migrations, provider behavior, ownership, prompts, or configuration exposure. Keep search text in memory only; do not persist it to browser storage or log it.
- Route selection through existing `chooseTemplateFromPicker` and the canonical hidden-select change handler. Preserve the dirty Working note save/revert guard, note-mode UI, and dictation synchronization. Clicking a row or activating it with Enter/Space selects the template and closes the modal. Enter in the search field must not select a result, generate a note, or submit a form. Native buttons provide the list interaction; no arrow-key listbox behavior is required.
- On open, focus search. Contain Tab and Shift+Tab within the modal and make the background inert. Escape, close, and backdrop close the modal and safely restore focus to its opener. If generation forcibly closes the modal while its opener is disabled, use a safe existing fallback focus target. Reuse established dialog/focus abstractions where available.

## Accessibility and responsive acceptance

Target WCAG 2.2 AA. Record verification evidence and remaining human checks; passing focused checks does not establish whole-product conformance.

- Provide accessible labels, names, descriptions, and current/selected state. Focus remains visible and is not obscured. Small text has at least 4.5:1 contrast; meaningful boundaries and focus indicators have at least 3:1 where applicable. Selection is distinguishable without color alone.
- Use project-standard 44px control targets. Verify reflow at 320px, 200% text resize and 400% zoom, text-spacing overrides, forced colors, and reduced motion. On mobile, the virtual keyboard must leave search and the results list usable.
- Verify with browser axe checks and manual keyboard, screen-reader, and zoom inspection.

## Scope

Likely targets are `app/templates/transcribe/_workspace.html`, `app/static/css/transcribe.css`, and `app/static/js/transcribe/app.js`; introduce a small module only if the existing structure justifies it. Do not redesign the dictation or split selectors. Exclude reference toggles, creator/type/sort filters, quota controls, template creation, and default-template actions.

During implementation, inspect `docs/testing.md`, `docs/transcribe_brief.md`, and `docs/workspace.md`; update the nearest maintained operational document if behavior changes. Update `DESIGN.md` only if the resulting behavior establishes a durable current design rule. The separately authorized Impeccable design-sidecar refresh is maintenance work and is not a prerequisite or part of this UI implementation.

## Implementation and verification phases

1. Reconcile this plan with the current template markup, selection flow, dialog/focus helpers, tests, and maintained documentation. Current source entry points include `app/templates/transcribe/_workspace.html`, `app/static/js/transcribe/app.js` (`chooseTemplateFromPicker` and picker event handling), `app/static/css/transcribe.css`, and `DESIGN.md`.
2. Implement the modal search, result states, accessibility behavior, and styling within existing abstractions. Keep filtering client-side and preserve the existing template list and selection contracts.
3. Add focused behavioral and browser accessibility regression coverage using synthetic data. Exercise empty descriptions, long titles/descriptions, duplicate names across scopes, currently selected result filtered away, no matches, one and many results, save-guard failure, and generation state.
4. Update the nearest maintained operational documentation. Run focused checks using the project virtual environment and the `APP_ENV=test`, `COOKIE_SECURE_MODE=auto`, and `HSTS_SOURCE=app` settings required by `docs/testing.md` and `docs/dbtesting.md`. Run any shared-infrastructure checks sequentially. Do not run these checks as part of this planning task.

## Current-source observations

The Scribe workspace already renders a `template-picker-modal` and button rows from `available_templates` in `app/templates/transcribe/_workspace.html`; the rows currently show title, note mode, and scope. `app/static/js/transcribe/app.js` contains picker UI synchronization, `chooseTemplateFromPicker`, and per-option click handlers. `app/static/css/transcribe.css` contains picker modal and row styles. `DESIGN.md` defines the warm-surface, restrained-teal “Quiet Study” visual direction. These are implementation references, not a claim that the intended search or accessibility behavior already exists.

## Implementation evidence and remaining verification

The modal search, descriptions, result announcements, selected state, focus containment/restoration, inert background, and reduced-motion-aware open/close transition are implemented in the Scribe workspace. Selection still dispatches `change` on the canonical hidden template select, preserving the existing Working-note save/revert and dictation/mode synchronization path. Row edit/view uses the library route and existing role-gated editor; direct generation awaits selection/save completion before the normal generation form. No API, schema, migration, provider, or persistence behavior changed.

The user tried the initial interface and confirmed search worked, then selected “Compact rows” and “Warm header” and requested row edit/view and direct-generate actions. They later reported that the direct-generation shortcut could enter the multiple-problems flow. It now hides the per-row Generate action while the shared split-create workflow is enabled or the multiple-problems state is saving; its handler also rechecks after the canonical selection/save wait. Focused synthetic behavior coverage verifies that the shortcut hides and restores as mode changes, split mode/pending state and a split-mode change during save never submit, while ordinary one-note generation still does. Browser accessibility audit and rendered verification remain pending: the configured Playwright browser was unavailable and no interactive browser surfaced to this subagent. The temporary server and fixture from the earlier preview were stopped and removed. Manual verification of keyboard use, 320px reflow, 200% text resize/400% zoom, text spacing, forced colors, reduced motion, virtual-keyboard usability, and screen-reader announcements remains pending. Automated checks and those manual checks do not alone establish whole-product WCAG 2.2 AA conformance.
