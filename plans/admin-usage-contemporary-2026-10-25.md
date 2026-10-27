# Contemporary admin Usage trial

## Intended outcome

The user approved the generated minimalist workspace concept and asked to try it while keeping the sidebar largely unchanged. Apply the new visual direction to Usage first, preserving the current navigation shell and unrelated admin screens. This is an active implementation plan, not a statement of current behavior.

The concept is saved as `output/admin-usage-contemporary-2026-10-25/concept.png`. Its layout and typography guide this trial; its illustrative data, calendar-month selector and active-member metric are not executable requirements.

## Scope

- Contemporary sans-serif headings, compact filters, slim summaries, flat chart sections and readable tables for global and selected-team Usage.
- Preserve all five real KPIs, four chart series and accessible data tables, reporting ranges, comparison windows, UTC labels, search filters and operational breakdowns.
- Within selected-team Usage, Team shows team reporting; Users shows the existing user usage breakdown through an allowlisted `usage_view=users` query value. The reporting-range form preserves the selected view and team.
- Quotas remains the existing selected-team route and validated lazy-loaded panel. Add matching page-level navigation without relocating quota forms or changing mutations.
- Global Usage remains cross-team reporting, without unscoped user or quota navigation.
- Preserve sidebar structure, mobile navigation, quota editor, retention, defaults and other admin designs.

## Boundaries and ownership

Presentation only; no schema, quota accounting, provider behavior, authentication, authorization, content-access or persistence changes. Preserve POST contracts, CSRF, metadata-only reporting and current team/member validation. Use synthetic fixtures and screenshots only.

Luna owns template implementation; a separate Luna owns focused verification, screenshots and documentation and is the sole test-database user. Another Luna independently reviews source and actual renders. Root owns scope, integration and final screenshot acceptance.

## Acceptance

Check global Usage, selected-team Team/Users and the existing Quotas destination at desktop, tablet and mobile widths. Verify reporting filters preserve view and scope, links work without JavaScript, chart keyboard controls and full data remain available, and page-level overflow is absent. Run focused admin tests, the admin accessibility module, chart checks where appropriate, maintained-document validation and form-contract comparison. Keep existing uncommitted changes; do not stage or commit.

## Status

Implemented by Luna and independently reviewed against populated desktop and mobile renders. The existing sidebar remains intact. Team/Users/Quotas links use the existing team scope and quota route, with an allowlisted Users view. The chart changes only adjust visual label density; all data, comparisons and accessible controls remain available. Usage reuses content-height mobile layout to avoid a forced blank region below short user tables.

Verification complete: the full admin accessibility module passed (10 tests), including the populated responsive Usage regression; four existing admin UI checks and one chart JavaScript test also passed. All 54 POST form contracts remain intact; Jinja parsing, JavaScript syntax, maintained-document validation (54 files) and diff whitespace checks passed. Screenshots and the synthetic mobile gap measurement are saved alongside the concept under `output/admin-usage-contemporary-2026-10-25/`.

Documentation updated: DESIGN.md, docs/usage_tab.md and docs/testing.md. No migrations, configuration, backend services or accounting changes were required.
