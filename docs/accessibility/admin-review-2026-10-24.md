# Admin accessibility review — 24 October 2026

## Scope and method

Target: WCAG 2.2 Level A and AA, canonical `/admin` global screens and selected-team tabs, including the quota editor. This is a dated assessment of working-tree source based on commit `4603b1ec929fded6eb222a6e21878eca8a9fdfdb`, including uncommitted quota/UI changes. It is not a conformance certification.

The review combines source inspection, calculated CSS contrast, and an isolated Chromium/axe review with synthetic accounts and metadata. No real user content or provider calls are needed. Source review was delegated separately for forms/interaction and styling; browser evidence was collected separately and reviewed together.

## Findings

Priorities reflect practical impact, not a WCAG-defined severity scale. Source locations refer to the assessed working tree.

### A01 — Mobile hides global navigation (high)

At `max-width: 820px`, `.sidebar .nav-group` is hidden without an equivalent mobile navigation control. Global destinations and Log out disappear. A responsive layout must preserve access to functions, rather than merely fit the remaining content.

Browser confirmation: at 320 CSS pixels the navigation groups are `display: none`; no replacement navigation control was visible.

- Criteria: 1.4.10 Reflow; investigate 1.4.4 Resize Text with actual browser zoom separately.
- Source: `app/templates/admin_mockup.html:1933`, navigation at line 2003.
- Fix: provide an accessible mobile navigation disclosure/drawer retaining global links and session actions. Verify keyboard operation, focus return and reflow.

### A02 — Provider dialogs do not manage focus (high)

STT and LLM wizard open handlers unhide an `aria-modal` overlay without moving focus into it or containing keyboard navigation. Keyboard users can remain on or reach background controls covered by the overlay.

Browser confirmation: focus was outside both dialogs immediately after opening. The runtime spot check did not exhaustively cycle focus or measure every fully obscured control, so 2.4.11 remains conditional rather than a separately confirmed failure.

- Criteria: 2.4.3 Focus Order; 2.4.11 Focus Not Obscured where the focused background component is completely covered.
- Source: `app/templates/admin_mockup.html:5479` and `:5761`; Escape handlers at `:5617` and `:5957`.
- Fix: move focus to an appropriate dialog control/title, make background content inert, contain Tab/Shift+Tab, and restore focus to the actual opening control on every close path. Reuse an established accessible dialog abstraction if available.

### A03 — LLM dialog and fields lack programmatic labels (high)

The LLM wizard dialog has no accessible name despite an existing `llm-wizard-title` heading. Several fields have visible sibling labels without `for`/`id` associations: Provider name, Base URL, Region, API key, Google configuration fields, and final model configuration. A visual label alone does not establish the input's accessible name.

The missing dialog name was confirmed in the browser. Step-two field associations were reviewed in source; that step was not exercised in this browser run.

- Criteria: 1.3.1 Info and Relationships; 4.1.2 Name, Role, Value.
- Source: `app/templates/admin_mockup.html:4759`, `:4914`, `:4947`, `:5031`.
- Fix: name the dialog with `aria-labelledby="llm-wizard-title"`; associate all fields with their visible labels, including the manual-model input.

### A04 — Shared text and focus contrast are insufficient (high)

Declared CSS pairs fail normal-size text contrast: muted `#718096` is approximately 3.79:1 on `#faf8f5` and 4.02:1 on white; success text is approximately 3.99:1 on its declared badge background; warning text approximately 3.29:1. These affect metadata, helper text and status labels. The shared focus shadow replaces native outlines with a pale ring approximately 1.15:1 against white and 1.09:1 against the page background.

Browser confirmation: all 23 scanned states had an axe `color-contrast` violation. Examples included muted text at approximately 3.78:1 on the page, 4.01:1 on white, and 3.56:1 on table-heading backgrounds; the Active badge measured 3.99:1. Source calculations retain slightly different rounding from axe. Focus-ring ratios are source calculations, not an axe focus finding.

- Criteria: 1.4.3 Contrast (Minimum); 1.4.11 Non-text Contrast for the authored focus indicator. Whether a weak ring is perceptible under 2.4.7 also needs interaction assessment.
- Source: `app/templates/admin_mockup.html:9`, `:24`, `:371`, `:410`; chart text repeats muted colors in `app/static/js/admin-usage-charts.js:46`.
- Fix: darken semantic text tokens and use a strong, consistent focus indicator. Recheck all actual status/background combinations, focus states and forced-colors mode. Small font size alone is not classified as a failure.

### A05 — Selection and disclosure state are visual only (medium)

Provider option buttons change only a CSS selected class. Member action buttons toggle a hidden menu without exposing expansion state.

Browser confirmation: selected provider buttons had no pressed/selected state; an open member menu's trigger had no `aria-expanded` attribute.

- Criterion: 4.1.2 Name, Role, Value.
- Source: `app/templates/admin_mockup.html:4554`, `:5562`, `:3872`, `:5300`.
- Fix: use native radio groups for mutually exclusive provider choices, or expose the appropriate state consistently. Set `aria-expanded` on member disclosure buttons and associate each with its controlled content.

### A06 — Reporting range changes navigate without warning (medium)

The usage reporting-range select automatically submits its GET form on `change`. The Apply button is available only without JavaScript, and the label does not forewarn users of navigation. A keyboard user exploring choices can trigger a context change unexpectedly.

Browser confirmation: ArrowDown changed the URL to `range=90d` through a GET navigation.

- Criterion: 3.2.2 On Input.
- Source: `app/static/js/admin-usage-charts.js:4`; form at `app/templates/admin_mockup.html:2480`.
- Fix: expose an Apply button with JavaScript enabled, or give advance instructions describing the automatic navigation. Prefer explicit submission for predictable keyboard use.

### A07 — Usage charts omit accessible data and keyboard interaction (high)

The populated usage chart's generated accessible label described only the first ten points in each series. No equivalent full daily-data table was found. The SVG had no tab stops, and the legend/zoom interactions were not reachable in the keyboard spot check. Aggregate cards and provider totals do not supply the omitted per-period data.

- Criteria: 1.1.1 Non-text Content; 2.1.1 Keyboard for chart functionality without an equivalent keyboard operation.
- Source: `app/static/js/admin-usage-charts.js:35`, `:41`, `:72`; chart containers at `app/templates/admin_mockup.html:2528` and `:4227`.
- Fix: provide a complete accessible table of the same dates/current/previous values; provide keyboard-operable equivalents for retained legend/zoom functionality, or simplify the chart to a noninteractive view with equivalent accessible data.

### A08 — Wide admin grids expand the whole page (medium)

At a 320 CSS-pixel viewport, document scroll widths measured 1002px for Members, 1118px for Provider policy and 1038px for Team usage. This expands the page around the data rather than confining necessary horizontal scrolling to a two-dimensional table.

- Criterion: 1.4.10 Reflow. Two-dimensional data tables have an exception; the measurements do not mean every wide table is itself a failure. The concern is whole-page expansion and affected surrounding navigation/forms.
- Source: `app/templates/admin_mockup.html:992` and the corresponding policy/usage grid containers.
- Fix: contain necessary table scrolling within labelled, keyboard-accessible regions; ensure surrounding headings, navigation and forms reflow. Consider a stacked member layout where practical.

## Additional source concerns

- **Status announcements:** `app/templates/admin_mockup.html:5974` copies server `role="status"` flashes into plain toast elements and hides the original. The resulting toast lacks live-region semantics. Retain a persistent, correctly implemented status/error region and verify actual announcements with supported screen readers (4.1.3). Source inspection alone does not establish what was spoken. Four-second expiry is not automatically classified as a timing failure.
- **Text spacing:** Overview, quota editing and Defaults fit 320px before overrides but expanded to 381px under the text-spacing stress check. The cause and any content/function loss were not isolated, so this is a follow-up under 1.4.12 rather than a proven failure based only on scroll width.
- **Overlay notices:** the existing legal-setup warning has previously covered quota content. Verify actual computed styles and complete focus occlusion before assigning 2.4.11. Keep required legal information visible when redesigning its presentation.

## Verification record

The temporary audit harness completed: **1 passed, 3 warnings**. This means evidence collection completed; it does **not** mean accessibility checks passed. Every scanned state had a contrast violation. The local pinned axe version was 4.10.3, with WCAG 2.0/2.1/2.2 A/AA tags. The report bounds diagnostic nodes to ten per rule; a listed count is not a complete inventory of affected elements.

Command (temporary harness removed after execution):

```sh
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app \
OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/admin-review-2026-10-24 \
.venv/bin/pytest -q --accessibility-audit tests/accessibility/test_admin_review_tmp.py
```

Scanned states: team Overview, Members, quota overview, quota editor, Defaults, Provider policy, STT, LLM, De-identification, Usage, Security and Danger zone; global Providers, Directory, System administrators, Defaults, De-ID providers, Audit, Legal and Usage; invalid quota form; open STT and LLM wizards. Account requests, complete wizard branches, team-leader-specific pages and exhaustive legal editing were not covered.

Manual browser probes covered dialog entry focus/name/state, member-menu expansion state, populated chart accessibility and keyboard reachability, range-select navigation, and six representative pages at 320px with text-spacing overrides. Overview, quota editing and Defaults fit 320px without spacing overrides. Target boxes were sampled, but spacing exceptions were not exhaustively resolved; no target-size failure is claimed from size alone.

Evidence:

- [Sanitized browser summary](../../output/admin-accessibility-review-2026-10-24/admin-review-summary.json).
- [Synthetic screenshots](../../output/admin-accessibility-review-2026-10-24/), including mobile stress states. Some captures retain text-spacing overrides and legal/storage notices; they are not all baseline-layout screenshots.
- Raw bounded axe report: ignored `test-results/accessibility/admin-review-2026-10-24/accessibility-audit-test_admin_wcag22aa_review.json`.

Temporary test/HTML/browser scripts were removed and the test database released. No product code, provider configuration or quota behavior changed during the assessment. Operational-document validation checked 54 maintained files; `git diff --check` passed.

## Remaining human checks

This review does not establish screen-reader speech output, actual 200% browser zoom, complete forced-colors behavior, all target-spacing exceptions, every provider branch/error path, MFA/authentication, or all legal-editor interactions. These require the maintained [human testing guide](human-testing-guide.md). Do not treat an empty axe result on a page as a conformance claim.

## References

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [Contrast Minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
- [Focus Not Obscured Minimum](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html)
- [Name, Role, Value](https://www.w3.org/WAI/WCAG22/Understanding/name-role-value.html)
- [On Input](https://www.w3.org/WAI/WCAG22/Understanding/on-input.html)
