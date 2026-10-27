# Admin accessibility remediation — 25 October 2026

## Scope

This record follows the [24 October admin review](admin-review-2026-10-24.md). It covers the canonical system-administrator workspace, shared admin styles, dialogs, quota detail and usage reporting. It describes the working tree based on commit `4603b1ec929fded6eb222a6e21878eca8a9fdfdb`, including the earlier quota changes. The older review remains a record of the interface before remediation.

## Changes

| Review finding | Remediation |
| --- | --- |
| A01: mobile navigation | A named navigation disclosure retains global destinations, teams and Log out. Escape closes it and returns focus to its button. |
| A02: dialog focus | Member, STT and LLM dialogs use shared focus entry, background inertness, Tab containment and opener restoration. Provider-step navigation moves focus to the new step; failed draft cancellation retains the open dialog. |
| A03: LLM names and labels | The dialog references its heading. Visible configuration fields, model search and manual model input have associated names. |
| A04: contrast | Muted/status text, chart labels, focus indicators and control boundaries use stronger contrast. Decorative borders remain light; forced-colors focus styling is provided. |
| A05: widget state | Provider choices expose pressed state. Member-action disclosures expose names, controlled content and expansion state, including dismissal. |
| A06: range navigation | Reporting ranges require the explicit Apply button instead of submitting on selection. |
| A07: charts | Each scope has one server-rendered table containing every bucket, all four metrics and corresponding prior buckets when comparison is available. Each chart has named native range/series controls synchronized with its visual controls. Chart descriptions refer to the complete data rather than truncating at ten points. |
| A08: reflow | Wide grids have named, focusable scroll containers. Surrounding navigation/forms reflow; quota allowance/history tables use the same pattern. |

Required operator legal warnings and the browser-storage notice remain visible in page flow rather than covering controls. Server error/status messages retain their semantics; dynamic announcements use persistent live regions. Global provider-form selects and checkbox-label rows have larger hit areas.

The control-boundary color `#81776b` has calculated contrast of approximately 4.39:1 on white, 4.14:1 on the page background and 3.90:1 on parchment. The calculation is separate from automated text-contrast checks.

## Boundaries

Production changes are presentation-only. Authorization, owning-user content boundaries, provider credentials/selection/submission, quota accounting, operation identifiers, CSRF protection and form field names remain in their established services and routes. No migration or deployment configuration is required for these accessibility changes. The new reporting table contains the same permitted aggregate metadata as the charts.

## Regression harness

`tests/accessibility/test_admin.py` adds persistent isolated browser regressions, using synthetic system-administrator/member/provider metadata and the local pinned axe bundle. The tests assert requested route context, rather than treating a fallback page as coverage. Axe violations fail the test; completion of evidence collection is not treated as a pass when violations exist.

The audit's test-only redirect handling was corrected after a reproducible login-cookie propagation failure. Each destination remains validated against the exact ephemeral origin before fetching it. The browser-context request client commits cookies before the next validated hop, retains CSRF headers and follows the current redirect method/body semantics. It does not relax production authentication or permit unvalidated browser redirects. Local synthetic HTTP regressions cover cookie-setting redirects and blocked external destinations.

## Verification

Focused application checks passed:

- `tests/test_admin_ui.py -k 'quota or wizard or workspace or usage'`: **88 passed**, 221 deselected.
- `tests/test_admin_usage_charts_js.py`: **1 passed**. This synthetic Node test verifies native/chart state synchronization and the description configuration.

The accessibility batch passed **22 tests**:

```sh
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app \
OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/admin-review-2026-10-25 \
OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR=test-results/accessibility/admin-review-2026-10-25 \
.venv/bin/pytest -q --accessibility-audit \
  tests/accessibility/test_admin.py \
  tests/accessibility/test_network_guard.py \
  tests/accessibility/test_harness.py \
  tests/accessibility/test_pages.py::test_isolated_synthetic_pages_have_no_axe_wcag_violations \
  tests/accessibility/test_pages.py::test_login_validation_error_is_axe_checked
```

The local pinned axe 4.10.3 reports contain **24 admin scans across 23 unique states**, with no violations, plus **five existing login/workspace scans**, also with no violations. Route checks cover eight global and eleven selected-team views; additional states cover quota editing/invalid drafts, both provider dialogs and reporting controls. Passing tests also cover keyboard dialog entry/Tab containment/Escape return/background inertness, navigation and Log out access, quota disclosure scrolling, chart controls/full data, explicit Apply, 320px reflow and text-spacing overrides.

The test-only redirect regression verifies cookie-setting POST → 303 GET → 307 GET with CSRF headers preserved, and external 307/308 destinations blocked before the receiver. The final batch emitted three third-party deprecation warnings.

Generated evidence is in ignored `test-results/accessibility/admin-review-2026-10-25/`. It contains separately named JSON reports and synthetic screenshots (`quotas-editor-desktop.png`, `quotas-editor-mobile.png`, `quota-panel-mobile.png`, `usage-global-mobile.png`). Some captures include text-spacing stress overrides. Reports include bounded axe `incomplete` diagnostics; no-violation scans do not resolve all manual checks. The original 24 October evidence was not overwritten.

After the final source pass, three additional focused UI tests passed:
`test_admin_page_renders_branded_llm_provider_defaults`,
`test_admin_providers_panel_renders_deidentification_management`, and
`test_active_templates_route_flash_messages_through_top_right_toasts`.
They emitted one third-party deprecation warning.

The latest-source route-only audit was then rerun: **1 passed**, covering all
eight global and eleven team route states, with **zero violations** in the
refreshed route report. Each route retains one `color-contrast` incomplete
rule for branding text over the sidebar gradient (19 rule instances); those
checks are not counted as automated passes. The invalid global-section ARIA
references and notice-link incomplete findings are gone after correcting the
section name and underlining notice links. Other state reports remain from the
preceding full batch; their bounded incomplete diagnostics remain visible.

Separate source calculations for the branding gradient give approximately
14.52–15.39:1 for `--ink` and 8.01–8.49:1 for `--accent`, using its declared
composited endpoint backgrounds. These calculations support the chosen colors;
they do not change axe's incomplete status or constitute screen-reader testing.

Jinja parsing, JavaScript syntax/interaction checks, operational-document
validation (54 maintained files), and `git diff --check` passed. No `/api/v1`
route changed in this remediation, so the API route audit was not rerun.

## Remaining human assessment

Automated checks and browser keyboard/reflow tests do not certify WCAG conformance. Supported screen-reader speech output, actual browser zoom, operating-system accessibility combinations and exhaustive provider/authentication/error branches still require the [human testing guide](human-testing-guide.md). The synthetic administrator does not exercise MFA or live providers.

## Implementation ownership

Delegated work covered the main template and focused UI regressions, chart controls/data-table implementation, and isolated accessibility/browser regression infrastructure. Integration review checked focus cleanup, menu state, full reporting data and preservation of security/provider/quota boundaries.

Changed presentation files: `app/templates/admin_mockup.html`,
`app/templates/admin_quota_panel.html`,
`app/templates/_admin_usage_chart_data.html`, and
`app/static/js/admin-usage-charts.js`. Regression changes are in
`tests/test_admin_ui.py`, `tests/test_admin_usage_charts_js.py`, and
`tests/accessibility/{test_admin.py,conftest.py,redirects.py,test_network_guard.py}`.
Maintained documentation updates cover the admin function map and testing
guide; `docs/README.md` indexes this record, and `DISCOVERED_ISSUES.md` records
the remediation disposition.
