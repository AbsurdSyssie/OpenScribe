# Accessibility evidence summary: 16 October 2026

Assessment date: 16 October 2026\
Status: Post-remediation automated follow-up recorded; human review remains open. No criterion-wide pass or conformance claim is made.\
Related records: [human testing guide](human-testing-guide.md), [WCAG criterion checklist](wcag-2.2-manual-checklist-2026-10-15.md), [15 October interactive findings and post-fix run](interactive-browser-assessment-2026-10-15.md), and [15 October evidence manifest](evidence-manifest-2026-10-15.md).

## Scope and preservation

This record documents follow-up contrast investigation and additional browser states requested after the 15 October audit. The 14 October baseline and all 15 October browser findings, report directories, and hashes remain unchanged. New reports belong in a dated directory under `test-results/accessibility/`; the evidence owner must inspect reports before preserving or transferring them.

The current candidate must use the isolated synthetic browser/pytest fixture documented in [testing.md](../testing.md#opt-in-local-accessibility-audit). Do not use production, a developer service on port 8080, real user content, provider credentials, live provider calls, or real microphone capture. Do not place screenshot, page dump, credentials, identifiers, clinical text, or raw network data in this repository.

## Automated result history

The 15 October post-interactive full audit completed with 22 tests passed, 3 upstream deprecation warnings, 11 axe scans, zero violation groups, zero blocked requests, 2 passed states, and 9 `needs_manual_review` states. Each incomplete result was a `color-contrast` check; the reports did not supply rendered foreground/background pairs or ratios for those targets. An incomplete check is not a contrast failure and is not a pass. See exact report hashes and tool versions in the [15 October manifest](evidence-manifest-2026-10-15.md).

The 15 October targeted interactive remediation test verified specific focus-companion and named-group assertions. The interactive record remains a pre-remediation manual snapshot plus separate automated post-fix evidence. It does not contain human screen-reader, visual-image, actual browser zoom, or native microphone prompt testing.

The first 16 October expanded run completed with 23 tests passed and zero axe violation groups. Its incomplete checks included `aria-prohibited-attr` on library utility wrappers. The UI change exposed those utility wrappers as named groups, and a focused assertion passed. The final full run below was performed against that change and adds an explicit unchecked-editor-row scan. The first run remains an interim result; the final run is the current automated evidence.

The final 16 October run completed with 23 tests passed in 67.77 seconds and 3 upstream deprecation warnings. Across 21 scans, it recorded zero axe violation groups, zero blocked requests, 18 `needs_manual_review` states, and 3 passed states. The reports contain 15 incomplete `color-contrast` rule groups (40 target nodes), 5 `aria-valid-attr-value` groups (10 nodes), and 2 `link-in-text-block` groups (2 nodes). Incomplete checks are not failures or passes. The 5 ARIA groups concern popup `aria-controls` references that resolve in source templates but axe cannot validate at runtime when combined with `aria-haspopup`; manual verification remains open. The 2 link checks remain incomplete because contrast could not be determined. The `aria-prohibited-attr` flags from the interim library run no longer appear after the utility-group semantics change.

The drag-handle glyph received a rendered contrast measurement with ancestor opacity composited: checked row hover 11.40:1, pointer over handle 7.53:1, keyboard focus 7.91:1; unchecked row hover 4.67:1, pointer over handle 3.60:1, keyboard focus 3.60:1. All exceed the 3:1 non-text contrast threshold. The unchecked editor-row text also produced no axe violation. The computed foreground/background values were transient and were not retained; other incomplete contrast targets have no verified ratios. These measurements support only the sampled glyph states, not complete 1.4.11 or 1.4.3 conformance.

## Contrast investigation queue

The first queue below summarizes targets from the nine incomplete checks in the 15 October post-fix run. The second lists targets added by the 16 October expanded interim run. These are inspection targets, not confirmed failures. Some use gradients, transparency, overlays, or inherited opacity; obtain actual displayed color pairs after compositing and measure the settled state before judging them. Do not infer ratios from source declarations alone.

| State | Target categories | Investigation status | Evidence needed |
| --- | --- | --- | --- |
| Anonymous login and login validation | Hero eyebrow, heading, intro text, emphasized feature text, and validation toast | Pending rendered-state measurement | Foreground/background pair and ratio for each visually distinct state; indicate normal/large text classification and settled toast animation state. |
| Narrow Scribe, including 320 CSS px | Recording hero timer text and one redacted inline span target | Pending rendered-state measurement | Displayed pair and ratio at narrow widths, plus confirmation of state and viewport. Do not treat the redacted node target as a known label or issue. |
| Dictation dialog | Textarea state | Pending rendered-state measurement | Actual text and control boundary pairs against the dialog surface and any backdrop effects. |
| Editor copy/review and post-edit | Statement-row drag handles in the affected rows | Pending rendered-state measurement | Icon/focus/target state colors against their actual row surface, including focus. |
| Synthetic PII reveal and mask states | Compact-dictation section kicker, title/body, visibility control, and generic/success toast messages | Pending rendered-state measurement | Text/control pair values in both reveal states, including success and transient toast appearance. Do not copy rendered content into evidence. |

The expanded interim run added these categories:

| State | Target categories | Investigation status | Evidence needed |
| --- | --- | --- | --- |
| Account email dialog | Dialog header text | Pending rendered-state measurement | Settled foreground/background pair and ratio, including dialog surface compositing. |
| Template and Quick Actions import dialogs | Dialog copy, file input, and one import-dialog link | Pending rendered-state measurement | Text and control boundary pairs for each state; link distinguishability is also an `link-in-text-block` review target. |
| Smart Phrase library and invalid form | Textarea/control surface | Pending rendered-state measurement | Text and control boundary pairs in normal and validation states. |
| Editor/copy-review states | Unchecked statement-row drag handle, including post-edit rows | Pending rendered-state measurement | Icon and focus contrast against the row background at its actual opacity. |
| Other expanded workflow states | Login text and toast; narrow recording timer and redacted span; dialog textarea; compact-dictation labels/body/toggle and toast messages | Pending rendered-state measurement | Preserve each state, viewport, overlay and settled animation details; do not infer ratios from redacted targets. |

For text, compare against WCAG 2.2 SC 1.4.3 thresholds: 4.5:1 for normal text and 3:1 for large text. For applicable non-text control boundaries, state indicators, and meaningful graphics, evaluate 1.4.11 at 3:1 against adjacent color. Apply criterion exceptions only where their normative conditions hold. See the [W3C WCAG 2.2 Recommendation](https://www.w3.org/TR/WCAG22/#contrast-minimum) and [1.4.11 Non-text Contrast](https://www.w3.org/TR/WCAG22/#non-text-contrast).

## Expanded browser-state coverage

The final run extended coverage beyond the 15 October states. Keep each finding scoped to its state and tool evidence. Browser accessibility-tree output is not screen-reader output; no automated result substitutes for the human tasks in the [testing guide](human-testing-guide.md).

| Coverage area | Observed result | Disposition / next step |
| --- | --- | --- |
| Existing login, workspace, Scribe, dialog, editor/copy, PII and narrow viewport states | Final run has 21 scans across 9 reports, 0 violations, 18 manual-review states, and 3 passed states | 18 states remain manual review due to incomplete axe checks |
| Personal Account, email dialog, Preferences, Templates, template import dialog, Quick Actions, Quick Actions import dialog, Smart Phrases, invalid Smart Phrase form, and unchecked editor row | 10 added scans; account and personal-library dialogs were opened and cancelled with keyboard | Axe checks do not establish screen-reader operation or full keyboard conformance |
| Incomplete color contrast | 15 rule groups/40 nodes in final run; no ratio in reports for these targets | Human measurement remains open. Separately measured drag-handle glyph states are all above 3:1; other incomplete targets remain indeterminate. |
| ARIA and link distinction | 5 `aria-valid-attr-value` groups/10 nodes and 2 `link-in-text-block` groups/2 nodes. No `aria-prohibited-attr` incompletes remain in the final run. | Human browser/screen-reader review remains open. Source ID references resolving in templates do not establish runtime exposure. |
| Human screen-reader/browser/OS checks | Not performed | Remain open; assign a reviewer and supported test matrix |
| Native microphone allow/deny/dismiss UI | Not performed; no real microphone capture | Remain open pending a safe fake-device test setup and platform owner |
| Actual browser zoom at 200% and 400% / 320 CSS px reflow | Not performed in interactive check | Remain open for human testing with actual browser zoom |

## Final run command and environment

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-16-accessibility-complete .venv/bin/pytest -q tests/accessibility --accessibility-audit
```

The sandbox blocked the configured local PostgreSQL test connection before collection; the same command passed through the supported elevated retry. Environment: Python 3.12, pytest 9.0.3, Playwright Python 1.61.0, axe-core 4.10.3, and uvicorn 0.41.0. Chromium's exact version was not verified for this run. Three warnings came from upstream Starlette/httpx TestClient, websockets legacy, and uvicorn websockets protocol deprecations. The negative synthetic-login guard intentionally recorded the expected rejected login as a failed state while the test passed and left authenticated workspace unexecuted. The normal keyboard-login report passed.

The nine reports were reviewed for sensitive synthetic strings, UUIDs, screenshots, page HTML, and clinical content; none were present. All reports had empty blocked-request arrays. Hashes and byte sizes identify the reviewed files:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `accessibility-audit-test_dialog_keyboard_and_notifications.json` | 65,339 | `44452a5c976fa9e95fd8039b2ed67c1923ac049ce9d39c15a15b5183d7c266bf` |
| `accessibility-audit-test_isolated_synthetic_pages_have_no_axe_wcag_violations.json` | 213,780 | `32542e52c458814c3bbfbc7ca80bb79ba5a845e78996b40821c723c62f91d718` |
| `accessibility-audit-test_keyboard_can_reach_and_submit_normal_login.json` | 3,724 | `8615c0f5369c9692fd63c1d8c5d681a792c7bbb92099547cc01bb944f0cb225a` |
| `accessibility-audit-test_keyboard_reaches_workspace_tabs_without_generation.json` | 3,744 | `22b3c477295a183eea29ed61cf36c24a508b0c524f83dd2d6baf6cdd60399336` |
| `accessibility-audit-test_login_validation_error_is_axe_checked.json` | 46,795 | `a130bd0531d172d3f0789754ead88ae42e9e4b3a8fe87f1daaf9651f26d512dc` |
| `accessibility-audit-test_narrow_reflow_and_keyboard_focus_are_visible.json` | 56,548 | `ab5ca264627135fa3784c6a38c331948c119df829421f3587db5346d4a23703f` |
| `accessibility-audit-test_personal_settings_and_reusable_libraries_are_axe_checked.json` | 468,033 | `bf00b018f4a0e7d96370958f47a8827a5980227837373f2d7541463f4abd52ac` |
| `accessibility-audit-test_rejected_synthetic_login_does_not_mark_authenticated_state_passed.json` | 3,586 | `7d939ddc781719fcbcadd98ad7f1407881ecc935a71f8e764e9df9fc7806f09f` |
| `accessibility-audit-test_synthetic_editor_copy_review_state_is_accessible_and_keyboard_gated.json` | 324,578 | `aeb26600f610c396f45b6af2fa023ee23ff869f75cdb28a82a1f1cf2e519d0c8` |

## Human work that remains

The [human testing guide](human-testing-guide.md) contains step-by-step test IDs, setup, expected observations, evidence fields, severity and retest rules, and cleanup. At minimum, a human reviewer still needs to test complete keyboard operation and focus visibility/obscuration; screen-reader names, roles, states and announcements; actual color pairs in normal and high-contrast presentation; real browser zoom/reflow and text spacing; microphone permission UI using an approved fake input; password manager, paste, and provisioned MFA behavior; drag alternatives and target sizes; and exported-document scope/accessibility. Record each criterion as Pass, Fail, Not applicable with an approved reason, or Not tested with the limitation. The current evidence does not establish WCAG conformance or legal applicability.
