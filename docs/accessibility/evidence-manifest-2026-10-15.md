# Accessibility evidence handoff manifest

Assessment record date: 15 October 2026\
Assessment scope: OpenScribe web application, subject to the organisation's scope decision.\
Status: Local evidence pack prepared for review. It is not a conformance claim, approval, accessibility statement, or legal applicability decision.

## Handoff destination

The legal documentation repository and its intake location were not specified or available in this workspace. No files have been copied or submitted outside this repository. This manifest is a sanitized local handoff record; the authorised evidence owner must confirm the destination and transfer process before handoff.

## Evidence inventory

| Artifact | Status | Identifier | Review notes |
| --- | --- | --- | --- |
| [WCAG 2.2 A/AA manual checklist](wcag-2.2-manual-checklist-2026-10-15.md) | Prepared; one scoped pre-remediation 2.4.7 failure and one 4.1.2 concern recorded; overall criterion review remains incomplete | `wcag-2.2-manual-checklist-2026-10-15.md` | Contains one specific 2.4.7 failure with targeted fix evidence, a scoped 4.1.2 accessibility-tree concern with targeted semantics test evidence, and untested follow-up for the remaining criteria. Screen-reader and native microphone UI testing were not performed. |
| [Interactive browser assessment addendum](interactive-browser-assessment-2026-10-15.md) | Pre-remediation observations and separate post-fix verification recorded | `interactive-browser-assessment-2026-10-15.md` | The original browser evidence records a scoped 2.4.7 failure and 4.1.2 AX-tree concern; a later targeted test verifies the focus companion and named group behavior. No screenshots were captured or persisted; no HTML, credentials, or clinical content is included. |
| Initial remediation-overlap run | Reviewed; failed; superseded by the final run | `test-results/accessibility/2026-10-15-initial-rerun-during-remediation/` | This run overlapped CSS remediation and is not a pristine baseline. The isolated page audit recorded 4 violation entries across its 4 scanned states; one synthetic keyboard login and one tab-keyboard test passed, and the rejected-login guard behaved as expected. It cannot establish final behavior. Artifact hashes appear below. |
| Automated accessibility reports before the interactive findings fixes | Reviewed; 22 tests passed | `test-results/accessibility/2026-10-15-accessibility-final/` | Eight sanitized reports are listed below. The pytest process reported 3 deprecation warnings. 11 axe scans recorded zero violation groups and zero blocked external requests; two scans were `passed`, and nine were `needs_manual_review` because of axe incomplete checks. Generated reports are ignored local artifacts; preserve them only through the evidence owner's approved transfer process. |
| Targeted post-interactive-remediation verification | Reviewed; 1 accessibility test and 39 focused workspace regressions passed | `test-results/accessibility/2026-10-15-post-interactive-remediation/` | The targeted report contains 4 axe scans, zero violation groups, zero blocked requests, and 4 `needs_manual_review` statuses for incomplete color-contrast rules. Exact test command and report identifier are below. Browser version for this targeted run was not recorded in the report and is not stated here. |
| Full post-interactive-remediation audit | Reviewed; 22 accessibility tests passed | `test-results/accessibility/2026-10-15-post-interactive-full/` | Eight reports contain 11 axe scans with zero violation groups, zero blocked requests, 2 passed states, and 9 `needs_manual_review` states because `color-contrast` checks were incomplete. Browser versions: Playwright 1.61.0, Chrome for Testing 149.0.7827.55; axe-core 4.10.3. Report identifiers and hashes are below. |
| Historical baseline summary | Preserved as dated plan context | Plan: `plans/WCAG_ACCESSIBILITY_IMPLEMENTATION_PLAN.md` | The plan records the 14 October 2026 baseline as 3 unique violation groups, 8 rule entries, and 23 element occurrences across four scans. The generated report is point-in-time evidence and must not be overwritten or represented as current results. |
| Source revision | Base revision identified; working tree dirty during assessment | `9f71ad396b725c68dfbbd6fd2dcfb01cf4d4c414` | This is the repository HEAD known at review time; accessibility changes were uncommitted. Do not represent this SHA alone as containing the changes or as the tested final source revision. |
| Test environment | Recorded | Python 3.12.3; pytest 9.0.3; Playwright 1.61.0; Chromium 149.0.7827.55; axe-core 4.10.3 | Final command and test settings are recorded below. PostgreSQL sandbox restriction required the supported elevated retry. No credentials or secrets are recorded. |

## 15 October automated run before interactive findings fixes

Command:

```text
OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-15-accessibility-final APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q --accessibility-audit tests/accessibility
```

The sandbox attempt was restricted at the configured local PostgreSQL service; the supported elevated retry passed. Environment versions: pytest 9.0.3, Playwright 1.61.0, Chromium 149.0.7827.55, and axe-core 4.10.3. The run completed with 22 passed, 3 deprecation warnings, and zero pytest failures. Across 11 axe scans there were zero violation groups and zero blocked external requests. The authenticated workspace and empty Scribe scans were `passed`; anonymous login, narrow Scribe, dialog/notification, login validation, 320-pixel responsive, and four editor/PII scans were `needs_manual_review` because axe reported incomplete checks. The report separately marks other consultation workflows for manual review. The report marks axe incomplete checks as `needs_manual_review`; they are not passes. The intentionally rejected synthetic login test records its expected negative login state as failed while the test itself passes and keeps the authenticated workspace state unexecuted.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `accessibility-audit-test_dialog_keyboard_and_notifications.json` | 64,351 | `b6c74a4af0cbf7a9a07bdbc72255fb8c955f4a268ba22c54dd276919712e9495` |
| `accessibility-audit-test_isolated_synthetic_pages_have_no_axe_wcag_violations.json` | 212,504 | `4057aa13d81de39b348bc00155e5b778f790a29f20dfca900d3039916fc7ca05` |
| `accessibility-audit-test_keyboard_can_reach_and_submit_normal_login.json` | 2,448 | `087e7636f79c861e78ef7ee6ebb2e60acfbecc2bb18cd4acd1b2ade67b6ce37b` |
| `accessibility-audit-test_keyboard_reaches_workspace_tabs_without_generation.json` | 2,468 | `cbfe33f87f4d4228a387523f7e8ec64cc8eae2cf768ea1f70a48ce70c1634e3d` |
| `accessibility-audit-test_login_validation_error_is_axe_checked.json` | 45,519 | `97918066dcba8afdcfb67135a673d10cfb85206f97bf39e58a2f13a0c62dc725` |
| `accessibility-audit-test_narrow_reflow_and_keyboard_focus_are_visible.json` | 55,272 | `392990ff854ebb43e4c685fb64309181d8770d7b46146c16cec54333495ce3c3` |
| `accessibility-audit-test_rejected_synthetic_login_does_not_mark_authenticated_state_passed.json` | 2,310 | `5480a6d33e38c95911500d9fe4a7c97fe775802035edd78d51eaf5500c63fee7` |
| `accessibility-audit-test_synthetic_editor_copy_review_state_is_accessible_and_keyboard_gated.json` | 258,420 | `c7f6cda3429757a03c2f9fc1e9c6ec1f740740b2bce6f6486511dc5ff830b1e0` |

A separate focused workspace contract/layout/visual regression run completed with 39 passed and 1 Starlette deprecation warning using `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q tests/test_workspace_frontend_contract.py tests/test_workspace_scribe_layout_contract.py tests/test_workspace_sidebar_visual_contract.py`. The human checklist remains uncompleted. Automated results support only the states and checks exercised; they do not establish WCAG criterion conformance.



## Post-interactive-remediation targeted verification

The focused accessibility test and related workspace checks were run sequentially through the supported local test-service path:

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-15-post-interactive-remediation .venv/bin/pytest -q --accessibility-audit tests/accessibility/test_editor.py
```

Result: 1 passed, 3 deprecation warnings. The report contains four axe scans with zero violation groups and zero blocked requests. All four remain `needs_manual_review` due to incomplete color-contrast checks. The keyboard editor/copy-review assertions passed. The same test checks the Recording mode focus companion and named note-version group. The report does not contain browser version metadata; this manifest leaves it unspecified.

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q tests/test_workspace_frontend_contract.py tests/test_workspace_scribe_layout_contract.py tests/test_workspace_sidebar_visual_contract.py
```

Result: 39 passed, 1 deprecation warning. These targeted results do not replace a full audit or manual assistive-technology review.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `accessibility-audit-test_synthetic_editor_copy_review_state_is_accessible_and_keyboard_gated.json` | 258,373 | `6edd4bf2715217b44b3d383634e2bdedc9b27e03c0af0dbb610d2a01ee4287fe` |

## Post-interactive-remediation full audit

After the targeted checks, the complete suite was rerun:

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-15-post-interactive-full .venv/bin/pytest -q --accessibility-audit tests/accessibility
```

Result: 22 passed, 3 upstream deprecation warnings. Environment: Python 3.12.3, pytest 9.0.3, Playwright 1.61.0, Google Chrome for Testing 149.0.7827.55, axe-core 4.10.3. The eight reports below contain 11 scans, zero violation groups, zero blocked requests, 2 passed statuses, and 9 `needs_manual_review` statuses due solely to incomplete `color-contrast` checks. This full automated rerun does not close the manual checklist or establish criterion conformance.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `accessibility-audit-test_dialog_keyboard_and_notifications.json` | 64,063 | `e74bdb7ef6cc2af8ebe4f14dbbd7c69c8bfcb4c472e0680ebc5a71f4bf71398b` |
| `accessibility-audit-test_isolated_synthetic_pages_have_no_axe_wcag_violations.json` | 212,504 | `746da0ade540a800ae4154ab2a49b4e8cb15dc2443b57cd3cfcdbd8ac82a3aea` |
| `accessibility-audit-test_keyboard_can_reach_and_submit_normal_login.json` | 2,448 | `5d32da6b8182f31041057d2493017650990dfa45e4c330b4fb90e1145a33f155` |
| `accessibility-audit-test_keyboard_reaches_workspace_tabs_without_generation.json` | 2,468 | `ff8fe1e71d5c2f50fba14fde83300d4e4973b8f294dd2a5e14ee51eaf4a98b8b` |
| `accessibility-audit-test_login_validation_error_is_axe_checked.json` | 45,519 | `af1801ff64bd63ea499664d19c4a4751563f371bf206dc82f99dad87bb5004bc` |
| `accessibility-audit-test_narrow_reflow_and_keyboard_focus_are_visible.json` | 55,272 | `5642352c506b4f6c1d20a727580eef18a9f74bbff8d1a9cb35c3e6014025eac1` |
| `accessibility-audit-test_rejected_synthetic_login_does_not_mark_authenticated_state_passed.json` | 2,310 | `1028858ebc187f1f4a71b73ec4189b6a253f0205a3d8b479cca78061b8d053c1` |
| `accessibility-audit-test_synthetic_editor_copy_review_state_is_accessible_and_keyboard_gated.json` | 257,774 | `cd352e090ed65cfaccfde8e233ec39a7329a6f5decaf4cc87168e5827bfa0607` |

The related workspace contract/layout/visual tests passed 39 with 1 upstream deprecation warning. This run verifies the focused browser and DOM assertions after the two repairs; it does not replace visual inspection, screen-reader testing, native browser/OS testing, or criterion-level review.

## Reviewed initial-run identifiers

The 15 October initial run used the command below. Sandbox execution first failed while connecting to the configured PostgreSQL test service; the same command completed on the supported elevated retry. The run used Playwright 1.61.0, Chromium 149.0.7827.55, axe-core 4.10.3, and pytest 9.0.3. The reports were inspected as sanitized JSON from the maintained `AuditReport` writer: fields are limited to bounded/redacted rule identifiers, impact, help text/URLs, tags, selector targets, redacted failure summaries, keyboard state, test coverage, and environment metadata. No DOM HTML or screenshots are present.

```text
OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-15-initial-rerun-during-remediation APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q --accessibility-audit tests/accessibility
```

| File | SHA-256 |
| --- | --- |
| `accessibility-audit-test_isolated_synthetic_pages_have_no_axe_wcag_violations.json` | `da70b2ddc047cb11b5437b4152a841bb9b44d7ddfef8e622a577e9aec049f477` |
| `accessibility-audit-test_keyboard_can_reach_and_submit_normal_login.json` | `e4ca8ba2df75a7782e98a7453bfbe79d8c25a4f44740763059d682b422fa7768` |
| `accessibility-audit-test_keyboard_reaches_workspace_tabs_without_generation.json` | `a7fce495e4e8645c4d85131191e7e275036795d983bb503c43574808801c417f` |
| `accessibility-audit-test_rejected_synthetic_login_does_not_mark_authenticated_state_passed.json` | `4ded211f0326f9f8625d82fcb17a72c7e0253ff1bdfc6e1d1b72a23c53a61954` |

This intermediate run remains separately identified; it must not replace the 14 October historical record or the final run.

## Artifact review and transfer controls

Before any external transfer, inspect each candidate report for transcript-derived content, patient data, user identifiers, credentials, cookies, tokens, full URLs, page HTML, screenshots, and sensitive request/response bodies. Exclude any artifact that contains such material and investigate its generation path. Preserve the dated 14 October baseline unchanged. Store destination copy hashes and transfer date only after transfer actually occurs.

## Limitations and outstanding work

- The checklist records one pre-remediation 2.4.7 failure and one scoped pre-remediation 4.1.2 concern; targeted checks verified specific fixes but neither criterion is claimed as passed. The post-remediation full audit had zero axe violation groups across 11 scans, but nine scans retained incomplete color-contrast checks and overall manual coverage remains limited.
- Supported screen-reader/browser/operating-system combinations and native browser microphone permission prompts were not available for this review; test them manually and record exact versions.
- Complete keyboard operation, focus order/obscuration, 200% zoom, text spacing, authentication paste/password-manager support, full error recovery, status announcements with a screen reader, and exported-document accessibility still need criterion-specific evidence where applicable. The 320 CSS pixel overflow check and selected visible-focus check are limited automated spot checks.
- Confirm whether exported documents and any embedded/third-party content are in scope.
- Assign named reviewers and dates to the follow-up owner roles in the checklist.
- Determine legal and public-sector applicability separately. No certification, compliance approval, or accessibility-statement publication is implied by this pack.
