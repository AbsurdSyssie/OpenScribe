# Global admin form refinement — 25 October 2026

## Status

Initial implementation and density follow-up verified; awaiting visual approval. The user requested design review, generated concept images, delegated Luna implementation, screenshot review, then a final request for approval.

Luna completed the scoped template changes, tests and documentation; a separate Luna reviewed the source and renders. Root inspected final desktop, tablet and mobile captures. The admin accessibility module passed (8 tests), three existing focused admin UI tests passed, and the final populated/empty-state regression checks passed (2 tests). All 54 POST form contracts are preserved. Maintained-document validation checked 54 files and `git diff --check` passed. No backend, schema, migration or configuration changes were made in this pass.

Final captures are in `output/admin-global-forms-2026-10-25/`. This pass also corrected the filtered available-provider empty state. No unresolved issues were identified within the scoped screens.

## Scope and direction

### Density follow-up

The user rejected the amount of empty space in the first implementation. Rebalance the global pages around visible record lists and native inline creation disclosures. Stack the Global Defaults sections instead of pairing a short Templates list with a permanently open Quick Action editor. Keep edit forms open when editing. Remove redundant viewport-height constraints on these global pages, particularly when the navigation stacks on mobile; preserve the desktop sticky sidebar, footer content, 44px controls and existing form contracts. Verify closed and open states with keyboard interaction and fresh desktop/mobile screenshots before requesting visual approval again.

Implemented by Luna and independently reviewed. Fresh captures and measurements are in `output/admin-global-density-2026-10-25/`. The targeted populated/empty-state checks passed (2 tests), including keyboard disclosure operation, automatic edit opening, responsive layouts and a 32px mobile content-to-footer gap. Desktop retains the viewport-height navigation rail; short lists do not gain artificial filler. All 54 form contracts remain intact.

Final full admin accessibility run passed (9 tests). Maintained-document validation checked 54 files; `git diff --check` passed. DESIGN.md and docs/testing.md describe the new layouts and coverage. No backend, configuration or migration changes were required.

- Manage teams: padded create form with visible labels, readable status/retention controls, compact Create action, quiet team list and Open links.
- System administrators: grouped identity fields, usable generated-password controls, compact Create action, readable account list.
- Global defaults: clear template and Quick Action panels, counts and empty states, labelled Quick Action editor, retained item actions.
- Global De-ID providers: readable provider list and editor; group connection, five visible request/response mapping fields and existing capability checkboxes. Apply the same list treatment to team De-identification where appropriate.

Preserve the existing Quiet Study palette, typography and approved quota, retention and navigation designs. Use 44px controls, labels above fields, restrained borders and 11–12px panel corners. Panels use their natural height; actions use content-width buttons. Desktop uses bounded form/list columns and mobile stacks them.

Generated concept boards and prompt records are in `output/admin-form-design-2026-10-25/`. They illustrate the layout direction; stretched cards, wide buttons or incidental header differences in generated imagery are not implementation requirements. Executable contracts and the existing design system take precedence.

## Boundaries

Presentation only. Preserve every POST endpoint, field name/value, required/min/max constraint, hidden return parameter, checkbox state and destructive confirmation. Preserve CSRF/nonces, generated-password hooks, admin-only role/MFA defaults, built-in provider immutability and the blank/write-only credential boundary. Keep the exact local/private endpoint restriction for unredacted Clinical NLP. Do not change authentication, authorisation, content ownership, provider selection/execution, Vault handling, quota accounting, retention, schema or migrations.

## Ownership and acceptance

Luna owns implementation; a separate Luna owns focused tests, synthetic browser captures and maintained documentation; another Luna reviews actual renders and source independently. Root owns scope and final acceptance. Only the test owner uses the shared test infrastructure.

Verify the four target screens at 320, 390, 768 and 1440px with populated and relevant empty states. Check labels, field fit, focus, password controls, all list actions, blank secret fields, checkbox semantics and built-in restrictions. Compare POST contracts with the pre-pass template. Run focused application checks, the admin browser/accessibility module and maintained-document validation. Preserve earlier uncommitted work and do not commit or stage.

Ask the user for final approval only after the implemented screenshots and independent review are satisfactory.
