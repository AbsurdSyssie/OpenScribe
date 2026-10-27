# Admin refinement — 25 October 2026

## Status and scope

User-authorised implementation of the five priorities in the 25 October Impeccable admin critique. This plan describes intended work, not current behaviour. Preserve the existing OpenScribe identity and approved quota editor.

Completed on 25 October 2026. Luna implemented the presentation changes, verified them with two focused application tests and seven admin browser/accessibility tests, and independently reviewed refreshed synthetic desktop/mobile captures. The root review confirmed all 54 template POST actions and named-field contracts remained unchanged. Current interface behaviour is documented in the maintained admin references and `DESIGN.md`.

## Intended outcome

1. Group team quota defaults by tokens/audio and daily/monthly period, with readable labels, units and consistent fields. Preserve all finite-limit values, validation and new-member snapshot semantics.
2. Place the existing retention editor under Defaults. Overview shows its current value and a link to the setting.
3. Keep Overview, Members, Quotas and Usage prominent; group provider destinations and settings in accessible native disclosures. Preserve every existing destination URL and selected-team scope.
4. Keep dense desktop member/provider-policy layouts, but keep identity, status and actions together in labelled mobile rows without horizontal panning.
5. Compact incomplete legal-setup information into a persistent warning summary, count and setup link, with all individual warnings available through disclosure.

## Boundaries

- Presentation only: no authentication, authorisation, content access, provider execution, quota accounting, retention calculation, schema or migration changes.
- Preserve POST endpoints, field names, constraints, CSRF handling, confirmation text and state validation.
- Preserve recent keyboard focus, dialog, chart/table and forced-colours accessibility work.
- Keep global administration and selected-team configuration distinct.
- Preserve existing uncommitted work; do not stage or commit.

## Ownership

- Luna implementation: admin template and any necessary admin-only presentation assets.
- Luna verification: focused UI/browser tests, synthetic screenshots and closest maintained documentation.
- Independent Luna review: Impeccable design, interaction and accessibility review; fixes return to the implementation owner.
- Root: scope, integration, acceptance and final review. Test infrastructure has one verification owner.

## Acceptance

- Defaults is readable at 320, 390, 768 and 1440 CSS pixels; resource/period/unit associations remain clear.
- Retention has one visible editor, under Defaults, with unchanged server semantics.
- All eleven team destinations remain reachable with keyboard and without JavaScript; selected nested destinations remain identifiable.
- Navigation disclosures dismiss predictably; member action menus remain visible and operable.
- Member/provider-policy rows retain all metadata and controls without mobile horizontal panning. Other wide reporting tables retain their labelled scroll regions.
- Legal warnings retain a visible summary and direct setup link; every warning remains available.
- Focused application and browser checks pass, actual desktop/mobile captures are reviewed, and maintained docs match the final interface.

## Verification

Follow `docs/testing.md`, `docs/dbtesting.md` and `tests/accessibility/AGENTS.md`. Use only synthetic data. Run database-backed checks sequentially with the project virtual environment and required test environment flags. Retry the same focused command through the supported elevated path if sandbox restrictions plausibly prevent service access. No production service changes or weakened checks.

## User follow-up

Replace the wide retention save button and unused horizontal space with a compact setting row: explanatory text alongside a grouped days field and content-width save button. On mobile, place the team destinations inside a burger disclosure with the current page visible when closed. This supersedes the initial visible mobile tab groups; desktop grouping and all existing routes remain intact. Recheck keyboard access, focus, mobile reflow and actual rendered screenshots before completion.

Follow-up completed: seven admin browser/accessibility tests passed, including all eleven team destinations, mobile keyboard/resize behaviour and retention-row layout. Independent visual review cleared the refreshed 320, 390, 768 and 1440px captures. POST form contracts remain unchanged.
