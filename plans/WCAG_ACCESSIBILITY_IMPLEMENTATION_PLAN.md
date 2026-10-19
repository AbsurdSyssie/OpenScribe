# WCAG accessibility implementation plan

Status: implementation complete; manual assessment and external evidence handoff remain open, 15 October 2026. This plan records intended work, not current conformance or approval.

## Outcome

Resolve the confirmed automated accessibility findings, extend the isolated audit to representative user-interface states, and assess applicable WCAG 2.2 Level A and AA criteria through documented manual review. Preserve authentication, privacy, security, ownership, and clinical-safety behaviour.

## Historical automated baseline (14 October 2026)

The opt-in harness in `tests/accessibility/` runs Chromium and locally pinned axe-core 4.10.3 against a disposable localhost app server and synthetic user. It uses normal form login, disables service workers, blocks requests outside the exact ephemeral origin, and marks the workspace event stream response synthetic. It does not invoke providers or create clinical content. Its baseline report, `test-results/accessibility/accessibility-audit-test_isolated_synthetic_pages_have_no_axe_wcag_violations.json`, was generated 14 October 2026. It is ignored generated evidence, not a maintained source or stable release record.

That report records three unique violation groups across four scanned states: eight rule entries and 23 element occurrences. It records three `color-contrast` entries with 18 nodes (serious), three `aria-prohibited-attr` entries with three nodes (serious), and two `select-name` entries with two nodes (critical). The authenticated workspace and empty Scribe scans repeat findings across states; they are not distinct underlying defects.

- Login: muted labels and footer text fall below 4.5:1 for normal text. One reported ratio is 4.01:1.
- Workspace and empty Scribe: sidebar identity and navigation text fall below 4.5:1 on `#f5f1eb`; one reported ratio is 3.56:1. Tab and button text is also reported at 4.01:1 on white.
- Authenticated workspace states: `.toast-container` places `aria-label` on a `div`, where that attribute is prohibited for its current semantics.
- A select without an accessible name is reported in both the authenticated workspace and empty Scribe states. The rendered recording-mode select is server-rendered in `app/templates/transcribe/_workspace.html` as `select[data-recording-mode-select]`; confirm its actual name in the rendered DOM and cover any other selects separately. Do not infer a selector from a redacted report.

At the time of the 14 October baseline, the harness covered anonymous login, authenticated workspace, empty Scribe at normal and narrow viewports, normal keyboard login, and workspace tab keyboard navigation. It did not scan consultation editor or copy-control states. Those results did not establish full keyboard conformance.

## Boundaries

- Change only the minimum relevant application components, styles, and accessibility tests. Preserve existing user-facing intent and established abstractions.
- Do not suppress axe rules globally, add selectors to hide genuine failures, or use arbitrary ARIA roles solely to make a scan pass.
- Do not change production authentication, MFA, encryption, owner access, or clinical review controls to simplify tests.
- Use isolated synthetic fixtures only. Do not target a developer service on port 8080, reset a development database, use private patient records, use provider credentials, or make live provider calls.
- Never put credentials, cookies, tokens, user identifiers, patient data, transcript text, page HTML, or screenshots in reports.
- Keep baseline reports as dated evidence. Do not overwrite historical evidence or describe generated tests as certification.

## Phase 1: correct confirmed findings

1. Inspect the login styles/templates, workspace sidebar and tab styles, toast-container consumers, and the affected rendered select control or controls. Confirm the actual elements and user-facing purpose before selecting fixes.
2. Adjust only affected text/background pairs to meet at least 4.5:1 for normal text and 3:1 for qualifying large text. Check applicable non-text control boundaries and focus indicators. Do not treat disabled or placeholder styling as exempt without checking actual applicability and use.
3. Give each affected select a programmatic accessible name that remains understandable when options or surrounding layout change. Prefer visible labels where the interface permits them.
4. Remove the prohibited label attribute from the toast container or replace its semantics with a valid, purpose-appropriate live region. Select announcement behaviour based on the actual notification lifecycle; do not announce an empty container or every update without review.
5. Add focused regression assertions for the identified controls, valid notification semantics, and measured contrast. Keep the existing page audit as an additional regression check.

Acceptance: the three confirmed issue groups are resolved in the affected states; no new serious or critical axe findings appear; controls remain usable and understandable; and application behaviour remains unchanged beyond accessibility improvements.

## Phase 2: expand representative automated coverage

Extend the existing opt-in isolated harness rather than creating another server or authentication shortcut. Add states only where fixtures can safely and faithfully reach them:

- Scribe editor with synthetic content, including relevant edit, reveal/hide, add/remove, and copy controls.
- Copy or export gates with existing review visibility checks; verify keyboard interaction cannot bypass existing checks or scroll/confirmation gates. Do not treat these checks as evidence of clinical approval or add clinical-approval assertions.
- Validation errors, notification updates, and confirmation dialogs, including safe cancellation paths.
- Relevant user roles only when existing test fixtures support them; do not grant content access through administrative roles.

Mock provider-dependent responses at the test boundary if a representative state requires them. Do not call a real provider. Mark mocked states and limitations in the report. Keep synthetic markers and current external-request/redirect guards. A rejected or unauthenticated response must fail the state; a login page must never count as an authenticated scan. Ensure selected audit tests fail when a required state is skipped or unexecuted.

Acceptance: reports identify every intended state and accurately distinguish passed, failed, incomplete, and manual-review states. Diagnostics remain bounded and redacted. Existing keyboard checks still pass, with added checks for focus visibility, dialog focus handling, and operation without a pointer where applicable.

## Phase 3: manual WCAG 2.2 review

Create a criterion-level checklist for applicable WCAG 2.2 A and AA requirements. Record each criterion as pass, fail, not applicable with reason, or not tested. Axe output is evidence for selected machine-testable checks, not proof of overall conformance.

Manually assess at least:

- Complete keyboard operation, logical focus order, visible focus, focus not obscured, and no keyboard traps.
- Browser zoom to 200%, reflow at 320 CSS pixels, text spacing, orientation where relevant, and content without loss or horizontal scrolling.
- Labels, instructions, error identification and recovery, status announcements, accessible names/roles/values, and consistent navigation.
- Authentication accessibility, including paste and password-manager support where applicable. Do not weaken MFA.
- Screen-reader operation of key login, workspace, Scribe editing, review, and copy workflows using available supported assistive technology. Record exact technology/version and limitations.
- Native browser microphone prompts and other browser/operating-system UI that axe cannot inspect. Assess separately and state limitations.
- Downloaded documents or exported content if included in the product accessibility scope. Inspect document accessibility and provide an accessible alternative where required.

Review supported themes and browsers only. Do not claim dark-mode support unless the application implements it. Do not infer legal applicability or public-sector obligations from this technical checklist; record the organisation's scope decision separately.

Acceptance: every applicable criterion has evidence, an owner, and a disposition. Any failure or untested high-risk workflow has a remediation or explicit follow-up. No percentage score substitutes for criterion-level results.

## Phase 4: evidence and maintained records

After implementation and review, provide sanitized, dated test reports and the manual checklist to the legal documentation repository's accessibility assessment. Keep application implementation and legal evidence changes separate. Record tested application version or source revision, test commands, browser/axe versions, state coverage, limitations, and report hashes or other stable identifiers in the assessment pack manifest. Review each artifact for sensitive information before copying it.

Use the assessment to inform the applicable DTAC evidence and any accessibility statement. State only verified scope, status, limitations, and remediation commitments. Do not invent approval, completion dates, conformance, or an accessibility-statement publication date. Preserve existing originals and historical submissions.

## Verification for implementation

Run the focused harness first, followed by relevant UI/unit tests and maintained-document validation if maintained operational documentation changes. The opt-in audit is run from the repository root with the project virtual environment and required test settings:

```bash
cd tests/accessibility && npm ci --ignore-scripts && cd ../..
source .venv/bin/activate
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app pytest -q --accessibility-audit tests/accessibility
```

`npm ci` installs the test-only pinned axe bundle. Playwright and Chromium must already be available as specified by `requirements-dev.txt` and the repository testing instructions. Do not bypass a failed setup check or isolation guard. Run database-backed pytest checks sequentially as required by `AGENTS.md`. Run `python .github/scripts/check-operational-docs.py` only if maintained operational documents change. Finish with `git diff --check` and review the complete diff.

A passing automated run supports only the states and axe rules it actually exercised. It does not establish WCAG conformance or replace Phase 3.

## Out of scope

This plan does not author application changes, alter production security or clinical workflows, decide legal applicability, certify WCAG conformance, or approve/publish an accessibility statement. It does not alter unrelated policy or documentation.

## 15 October implementation and verification record

The confirmed baseline contrast, select-name, and toast-semantics findings were addressed. The server-rendered recording-mode select has an accessible name; selected workspace/Scribe colors and editor control sizing were corrected; the toast container no longer carries a prohibited name and actual notices use status/alert semantics. Regression assertions cover these focused controls and measured text pairs.

The isolated harness now scans anonymous login, authenticated workspace, empty Scribe, editor/copy-review, edited editor, revealed/masked synthetic PII, dialog/notification, rejected-login, and responsive states. Keyboard checks cover login, tab activation, editor line add/remove and reviewed copy behavior, and dictation-dialog cancellation/focus return. It reuses the isolated synthetic database/browser harness and does not call providers. The final command and sanitized report hashes are in [the evidence manifest](../docs/accessibility/evidence-manifest-2026-10-15.md).

Verification on the working tree completed before and after the interactive findings fixes. The post-fix full audit completed with 22 accessibility tests passing (3 deprecation warnings), 11 axe scans with zero violation groups, and focused workspace contract/layout/visual tests passing (39 passed, 1 deprecation warning). Nine scans were marked `needs_manual_review` because color-contrast checks were incomplete; two scan states were passed. The report also includes a deliberately rejected login guard whose expected negative state is recorded as failed while its test passes. The focused interactive-remediation test and report are separately recorded in the [interactive assessment](../docs/accessibility/interactive-browser-assessment-2026-10-15.md) and [evidence manifest](../docs/accessibility/evidence-manifest-2026-10-15.md). These automated results do not demonstrate WCAG conformance.

The criterion-level checklist is prepared. The pre-remediation interactive browser pass recorded a scoped failure for the clipped keyboard-focused Recording mode select under 2.4.7 and a scoped accessibility-tree concern under 4.1.2; a targeted post-remediation test verified the focus companion and named group behavior. Neither criterion is claimed as passed across the product; remaining criteria are not tested. Screen-reader/browser/OS testing, native microphone permission UI, 200%/400% zoom, full focus order and obscuration, complete keyboard operation, text spacing, paste/password-manager/MFA review, exported document accessibility, and organisation scope/legal decisions remain open. The legal evidence repository destination was not specified; no report has been transferred. Phase 3 manual assessment and Phase 4 external handoff are therefore incomplete.
