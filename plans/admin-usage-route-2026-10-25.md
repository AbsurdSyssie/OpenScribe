# Standalone admin Usage route

## User request

Move Usage to its own route reached through the sidebar. Remove the selected-team workspace title, status, metadata and Overview/Members/Quotas/Usage/Providers/Settings tab strip from this surface. Reuse the existing reporting and quota content rather than rebuilding it.

## Implementation contract

- Canonical GET `/admin/usage`, with allowlisted `view=team|users|quotas` and optional validated `team_id`.
- Retain full-session system-administrator authorization and metadata-only reporting.
- Reuse `render_admin`, extracted existing Usage sections and existing quota partials through an explicit standalone presentation mode. Keep the sidebar structure; update its Usage destination.
- Keep local Team/Users/Quotas navigation. Quotas also uses the standalone shell, including save redirects and validation-error renders; do not send the user back to unwanted workspace chrome.
- Preserve reporting ranges, UTC semantics, monitor filters, charts, data tables and scope. A compact scope control uses existing authorized team metadata and clears selected member/edit state when changing team.
- Quota details remain lazy-loaded only for the selected team's eligible member set. Quota views must not load reporting telemetry.
- Preserve all quota POST endpoints, fields, CSRF, idempotency and domain service calls. Only central presentation/redirect helpers change destinations; error renders retain drafts, selected member and form state.
- Legacy Usage and quota query URLs redirect after existing authorization and validation to the new route, preserving bounded navigation state.
- Malformed or missing teams follow existing safe global fallback semantics, with no user/quota data. Invalid view defaults to Team. A cross-team or ineligible quota member remains rejected inside the valid selected-team scope.

## Ownership and verification

Luna owns route, presentation and template implementation. A separate Luna owns focused tests, documentation and screenshots and exclusively uses the test database. Another Luna independently reviews source and rendered results. Root owns routing decisions and acceptance.

Verify canonical/compatibility routes, full-session authorization, malformed scope and member isolation, quota success and error returns, lazy loading, range/search state, sidebar entry, absence of workspace chrome, and keyboard/no-JavaScript operation. Inspect global/team/user/quota views on desktop and mobile. Run focused route/quota checks, the complete admin accessibility module, form-contract comparison and maintained-document validation. No API v1, migration, configuration or domain behavior changes are intended.

## Status

### Approved compact-table follow-up

The user approved `output/admin-usage-summary-concepts-2026-10-25/d-compact-table.png` and explicitly requested implementation. Replace the top summary strip with a shared compact semantic table: Total tokens, Audio hours, Input tokens, Output tokens, Generations. Use the existing selected-range summary and its authoritative provider total, not parsed display strings or inferred totals. Preserve reporting/accounting semantics and avoid additional database queries.

Remove redundant page team scope controls and the divider above the Consumption and health chart heading. Sidebar scope changes within standalone Usage retain the current view and range while clearing member/editor state; provide an All teams scope there. Keep other admin sidebar links unchanged. Reporting-range controls, quota member selection, chart accessibility, scope authorization and quota mutations remain intact.

This follow-up is implemented and verified. Luna implemented the shared table and sidebar navigation; another Luna ran three focused UI checks and the ten admin accessibility cases (nine passed in the full run, the corrected Usage case passed on targeted rerun). Independent source and render review is clear. Root inspected the final 1440px and 390px captures. The mobile table scrolls within its labelled, focusable region. All 54 POST contracts remain unchanged; maintained-document validation checked 54 files and diff whitespace checks passed. Evidence is under `output/admin-usage-compact-table-2026-10-25/`. No migrations, quota-policy changes or new reporting queries were required.

### Approved inline range-menu follow-up

The user selected B in `output/admin-usage-range-concepts-2026-10-25/b-navigation-menu-v2.png` and explicitly requested implementation. Move the reporting range into the tab row as a quiet text/chevron disclosure using the existing admin dropdown styling. Four ordinary GET links apply the chosen range immediately and preserve the validated team and Team/Users view. Remove the old select and Apply form. Keep the menu usable without JavaScript; optional Escape/outside-click handling must preserve focus and guard absent controls.

Global reporting gets an equivalent range bar without unscoped Users/Quotas tabs. Quotas does not gain a reporting filter because its accounting windows are fixed; its navigation continues carrying range state. Preserve the compact table, charts, monitor filters, sidebar scope and existing reporting semantics. Luna owns templates and scoped interaction code, a separate Luna owns focused tests, documentation and screenshots, and another Luna reviews source and actual renders. This follow-up is complete. Luna implemented the shared menu and interaction handling; another Luna updated tests, documentation and browser evidence. Independent review and root desktop/mobile visual review passed. The full admin accessibility module passed all 10 tests, followed by a passing focused range-menu rerun after a mobile-only CSS adjustment. That adjustment makes the mobile menu flow into the page so its options remain reachable on short screens. All 54 POST form contracts remain unchanged; maintained-document validation checked 54 files and diff whitespace checks passed. Evidence is under `output/admin-usage-range-menu-2026-10-25/`. No domain, route, schema or accounting changes were required.

Implemented by Luna and reviewed independently. The canonical route, compatibility redirects, shared team Usage partial, standalone quota rendering and PRG/error returns are in place. Sidebar structure and quota domain services remain unchanged. Root inspected actual desktop and mobile captures showing no selected-team workspace chrome.

Verification complete. Focused route/quota checks passed (13 tests), including limits, allowances, resets, revocations, scope and authorization. The final full admin accessibility/browser module passed (10 tests), including the populated standalone check at 320–1440px. Selector width and optional-control event bindings were corrected; chart bounds settle within their containers and the standalone check reports no page JavaScript errors. All 54 POST form contracts are preserved. Maintained-document validation checked 54 files and diff whitespace checks passed.

Evidence: `output/admin-usage-route-2026-10-25/`. No migrations, configuration, API v1 or accounting changes were made. Preserve prior uncommitted work; no staging or commit requested.
