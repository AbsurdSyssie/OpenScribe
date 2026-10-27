# Admin sidebar switcher discussion

Implemented and verified under the user's explicit sidebar-update request. Runtime verification, root visual review and maintained-document reconciliation are complete.

## Confirmed

- Use the approved warm OpenScribe sidebar concept in `output/admin-sidebar-switcher-2026-10-26/sidebar-switcher-mockup.png`.
- Replace the expanded sidebar team list with a compact top team selector.
- Move administrator identity to a bottom account disclosure with the existing logout action.
- Keep the OpenScribe logo non-interactive.
- Remove Admin home and the separate team directory page from the intended navigation.
- Default administrator landing page: All teams Usage, with the proposed default reporting range of Last 30 days. The user explicitly selected All teams Usage; the range follows the existing default.
- Add Create team at the bottom of the team picker. Reuse existing modal infrastructure and the current Name, Status and Default retention fields. On success select the created team and open its Overview; on validation failure retain entered values and keep the dialog open. User explicitly requested modal-code reuse.
- Switching teams preserves the current supported section, including Members, provider/settings sections, and the Usage Team/Users/Quotas view. Preserve the reporting range in Usage; clear selected member and edit state. Fall back to Overview only when the current section has no counterpart for the new team. User explicitly selected keeping the current section.
- Keep global administration in a distinct sidebar group. Opening a global page retains the remembered team selection for return navigation; it must not apply that team as a filter or alter global operation scope. The user suggested dimming the picker on global pages. Exact inactive treatment remains a design detail, with a clear global-scope label required to avoid ambiguity. Returning to a team section restores the remembered team, subject to server validation.
- Remember team navigation context independently per browser tab. Managing Team A in one tab must not switch another tab away from Team B. User agreed to independent tab context.
- Protect unsaved admin form edits when navigating away, including switching teams or opening global administration. Offer Stay / Discard changes; never autosave as a side effect of navigation. User explicitly requested this guard. Reuse existing modal behavior where practical, preserve validation-error drafts and normal explicit form submission, and do not persist form values or secrets in navigation-context storage.
- Show suspended teams after active teams in the picker, with muted names and no separate suspended-group heading. Keep each suspended row's status identifiable through a small readable Suspended label rather than colour alone. Suspended teams remain selectable for administration and searchable.
- Place Usage outside Global administration. Its team selector remains enabled for both All teams aggregate reporting and selected-team reporting. Inactivate the selector only on genuinely global administration pages, retaining remembered team context for return navigation. User explicitly agreed to this distinction.

## Constraints

Preserve authorization, validated team scope, metadata-only reporting, quota/accounting behavior, POST/CSRF contracts and existing team management capabilities. Current login redirects to `/admin`; several mutations return to `tab=directory`, so implementation must reconcile those destinations rather than merely hide links. This file records intended changes, not current runtime behavior.

## Implementation details to reconcile with current code

- Keep explicit URL scope authoritative; validate any remembered team before using it. Never store edited form values, credentials or other sensitive content with navigation context.
- Respect explicit All teams navigation, browser history and refresh without silently replacing them with remembered scope.
- Retain the existing mobile Navigation disclosure, keeping team selection reachable and the bottom account controls accessible without overlapping content.
- Reconcile legacy `/admin`, directory links and mutation success/error returns with the agreed landing and creation flows. Preserve deep links to supported team sections.
- Verify global scope isolation, per-tab restoration, suspended-team ordering/search, keyboard/focus behavior, unsaved-edit cancellation, modal validation and creation, existing logout/POST contracts, and desktop/mobile rendering.

## Execution and acceptance

Luna owns application edits. A separate Luna owns focused route/browser tests, maintained documentation and synthetic screenshots, with exclusive test-database use. A third Luna independently reviews source and rendered behavior. Root owns integration decisions and the final requirement-by-requirement acceptance audit. Preserve all preceding uncommitted work; no deployment, staging or commit is requested.

Current-source reconciliation: login routes send administrators to `/admin`; that route resolves legacy default/home presentation, while create/delete and other mutation paths can still return to `tab=directory`. Those GET destinations and POST success/error renders must be reconciled, not just removed from sidebar markup. Creation currently lacks safe form-value restoration. Existing admin modal focus/inert helpers and provider-wizard draft cleanup must be reused or preserved.

The compact sidebar will provide Usage and a Team workspace/return link outside the global group, retaining the existing team-section tabs within team pages rather than duplicating that whole navigation hierarchy. Remembered context remains account-bound, per-tab navigation metadata only, checked against current server-rendered allowed teams before use. Global routes remain unscoped.

## Completion audit

| Requirement | Current evidence |
| --- | --- |
| Top searchable picker; active teams before muted suspended teams, no suspended heading | Server-rendered native disclosure in `admin_mockup.html`; picker/search/browser checks and `sidebar-picker-open-desktop.png`. |
| Bottom account identity and logout; noninteractive brand; no Home/directory UI | Template inspection, server-rendered fallback regression, desktop/mobile account captures. The desktop popup is explicitly checked as fixed, above its trigger and within the viewport. |
| All teams Usage landing and legacy destinations | Focused route tests cover default and retired Home/directory links; unscoped legacy Providers/Defaults also resolve to Usage. |
| Create team modal, existing fields, selected new-team Overview, error values retained | Existing modal focus/inert helper reused by new and existing modals; focused successful creation and validation tests, browser conflict/error capture, in-dialog error and safe values. Close/Discard remains in place; native GET/form fallbacks retained. |
| Switching team preserves supported section and Usage view/range, clears member/edit state | Picker URLs constructed from allowlisted current section/view/range, without member/editor parameters; browser tests cover Usage Users and team Defaults switches. Existing standalone quota and navigation checks pass. |
| Global sidebar separation, inactive remembered picker and per-tab restoration | Global routes strip team scope before rendering; browser checks prove global-page return links and independent tab contexts. Account-bound sessionStorage contains only registry-validated team ID, allowlisted section and range. Explicit All teams and logout clear context. |
| Unsaved edits: Stay/Discard, no autosave or stored form values | Browser tests preserve retention edits on Stay, discard only on confirmation, retain create validation drafts and verify storage schema. Quota error forms carry explicit dirty markers. Successful provider finalization bypasses the guard only after success; draft/credential lifecycle services are unchanged. |
| Desktop/mobile menu, focus and modal behavior | Full `tests/accessibility/test_admin.py` run: 14 passed. Targeted keyboard tests include mobile nested Escape, scroll reachability, member-menu bounds and account-popup geometry. Root inspected picker, global scope, create error, unsaved dialog and account captures. |
| Authorization, quota mutations and form contracts | Focused route/quota POST selection: 12 passed; native server-rendered fallback check also passed separately. All mutation action endpoints retained. Baseline comparison proves 52 other POST contracts unchanged; Create team changes (safe restored values/return context) and explicit Create/logout CSRF inputs are intentional. No quota/provider domain service, migration, configuration or API v1 changes in this task. |

Application work was delegated to Luna for UI and route/presentation slices, verification/documentation to another Luna, with independent source review and root integration/visual acceptance. Evidence is under `output/admin-sidebar-switcher-2026-10-26/`. Python/JavaScript syntax and diff checks pass. The final maintained-document validation checked 54 documents after the last sidebar wording correction. No test services were installed or restarted; sandbox-blocked PostgreSQL attempts were retried through the supported elevated path. No staging, commit or deployment was performed.

## Approved compact refinement

The user approved the balanced sizing option and requested the fix after reviewing the sidebar in use. This follow-up supersedes the explanatory global-picker text and separate Return link in the initial implementation.

- Remove the visible Team eyebrow, remembered-scope helper paragraph and global Return-to-team link. Keep an accessible name for the picker.
- Show only the inactive, muted remembered team name on global pages; the existing Usage link restores the validated team/view/range and activates the picker on return.
- Use a 240px desktop rail (approximately 204px inner picker), a 260px desktop popup, and preserve existing narrower/mobile layout rules.
- Truncate only the closed selector; show full team names with wrapping in the popup.
- Raise the desktop sidebar stacking context above sticky content so the popup cannot be painted behind charts or tabs, while keeping dialogs above it. Reset mobile stacking and retain in-flow menus.
- Preserve existing auth, scope validation, per-tab metadata storage, unsaved guards, forms, modal handling and keyboard behavior.

Implemented by Luna and verified by a separate Luna. Four focused browser cases passed across the refinement: picker, unsaved guard, team navigation and mobile navigation; the picker case passed again after the final status-row layout adjustment. It checks real hit-testing over sticky tabs, viewport bounds, full long-name wrapping, the closed-label ellipsis, and remembered team restoration through Usage. Suspended status sits below the name so it does not consume name width. Root reviewed final desktop/mobile captures and the simplified global state. Jinja/JavaScript syntax, diff checks and the 54-document validator pass. No routes, storage schema, quota/provider services, migration or configuration changes were required for this refinement.
