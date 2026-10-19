# Interactive browser accessibility assessment addendum

Assessment date: 15 October 2026\
Status: Pre-remediation interactive spot checks. One keyboard-focus occurrence failed 2.4.7; a scoped accessibility-tree naming concern was observed for 4.1.2. No WCAG criterion is claimed as passed.\
Related records: [manual checklist](wcag-2.2-manual-checklist-2026-10-15.md) and [evidence manifest](evidence-manifest-2026-10-15.md).

## Method and limits

The computer-use browser surface and Chrome DevTools connection were unavailable. The reviewer therefore used the interactive Playwright browser tool against the isolated synthetic localhost session provisioned for this assessment. Browser identity reported `HeadlessChrome/155.0.0.0`; viewport, DOM, computed-style, and accessibility-tree measurements came from the live page. No screenshots were captured or persisted, so this pass did not include image-based visual inspection. The browser backend did not provide a working browser-zoom control; the attempted shortcut did not change zoom metrics. This differs from the planned computer-use assessment and does not substitute for visual review or testing with a supported screen reader and operating system.

The reviewer signed in through the normal form using the pre-provisioned synthetic account. Only the session's synthetic encrypted note was present. No provider was configured or invoked, no microphone capture was started, and no external links were followed. The Playwright browser did not provide request interception, so the harness's exact-origin and redirect guards were not reproduced; navigation was limited to the observed `127.0.0.1` origin. Credentials, transcript text, screenshots, HTML, and raw request/response data are intentionally omitted. No code or application behavior was changed during this pass.

## Observations

| Area / criterion | Interactive evidence | Disposition and follow-up |
| --- | --- | --- |
| Login labels and keyboard submission (3.3.2, 2.1.1) | The Email and Password fields were exposed with those names. Enter submitted the normal synthetic login form and reached the workspace. | Partial workflow only; all criteria remain **Not tested**. Review error recovery, password manager/paste behavior, and supported assistive technology. |
| Workspace keyboard order and tabs (2.1.1, 2.4.3) | Tab moved through the visible sidebar and workspace controls in sequence. ArrowRight moved the selected consultation tab from Clinical Note through Follow Ups to Transcript. | No complete operation or focus-order conclusion. Continue through every state and action. |
| Dictation dialog cancellation and focus return (2.1.2, 2.4.3) | Enter on Add dictation opened the named “Post-consultation dictation” dialog with focus in its textbox. Escape closed the dialog and returned focus to Add dictation. Tab from Cancel moved to Save dictation while focus remained inside the open dialog. | Observed cancellation and focus return; this did not prove every focus-trap boundary or all close paths. Screen-reader announcements were not tested. |
| Recording mode select focus (2.4.7, 4.1.2) | On desktop, keyboard Tab reached the enabled, named Recording mode select immediately after the visible Change recording mode button. While focused, its box was 1×1 CSS px, clipped with `rect(0, 0, 0, 0)`, and had no outline or shadow. The visible replacement button was not focused and had no focus-within state. | **Confirmed keyboard focus visibility issue:** the focused select is visually clipped and its visible equivalent has no focus indicator. Route to the accessibility remediation backlog. Do not infer a screen-reader result from this check. |
| Editor focus styling (2.4.7) | At 320 CSS px, Tab reached Copy selected note lines and the synthetic note-row drag handle, checkbox, and textarea. The drag handle's focused state changed from transparent background/muted icon to an 8%-accent background/accent icon; its outline remained `none`. The focused copy button also had no outline. | Computed styles show a focus-state change on the drag handle, but no screenshot was captured to judge perceptibility. Treat overall focus visibility as **Not tested**, with image-based visual review required. |
| Note-version switcher semantics (4.1.2) | The live `.document-switcher` is a focusable generic `div` with `aria-label="Note versions"` and no role. The browser accessibility snapshot exposed it as an unnamed generic. | **Potential accessible-name/role issue confirmed in the browser tree:** the author-provided name is not exposed there. Determine the intended interaction semantics and correct role/name in a separately reviewed fix; native assistive-technology behavior remains untested. |
| 320 CSS px reflow (1.4.10) | At a 320×900 CSS-pixel viewport, document and body widths were 320 px with no horizontal overflow. | Limited to this workspace state and viewport. The 400% zoom requirement and other pages/states remain **Not tested**. |
| Text-spacing override (1.4.12) | Temporarily applied WCAG text-spacing values in the live page (1.5 line height, 0.12em letter spacing, 0.16em word spacing, and 2em paragraph spacing). At 320 CSS px, document/body width remained 320 px without horizontal overflow. The page was reloaded afterward, removing the temporary style. | Geometry-only check; visual readability and loss of content/function were not established. Criterion remains **Not tested**. |
| 200% browser zoom (1.4.4) | Attempted the browser keyboard zoom shortcut. The browser backend did not change `innerWidth`, `devicePixelRatio`, or `visualViewport.scale`; all remained at their original values. | **Not tested.** Do not treat viewport resizing, CSS scaling, or this failed shortcut as browser zoom. Repeat in a supported interactive browser. |
| Contrast and screen-reader behavior (1.4.3, 1.4.11, 4.1.3) | No image-based visual inspection or supported screen-reader/OS session was performed. DOM/style inspection alone was insufficient to judge fully composited color pairs or announcements. | Remain **Not tested**; complete the manual checklist with visual review and supported assistive technology. |

## Conclusion

This is a pre-remediation evidence snapshot. The clipped keyboard-focused Recording mode select is a reproducible focus-visibility defect and is recorded as a scoped 2.4.7 failure in the checklist. The note-version switcher appears as an unnamed generic in the browser accessibility snapshot; record this as a scoped 4.1.2 concern while leaving the criterion-wide outcome untested pending semantic and assistive-technology review. Other observations are partial evidence only. Do not overwrite these findings after the authorized fixes; append separate post-remediation verification.


## Post-remediation verification

This section records targeted verification after the fixes. The pre-remediation observations and table above are preserved unchanged as dated findings.

The isolated Playwright accessibility test was rerun after adding a visible focus companion for the clipped Recording mode select and exposing the note-version selector as a named group:

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-15-post-interactive-remediation .venv/bin/pytest -q --accessibility-audit tests/accessibility/test_editor.py
```

Result: 1 test passed with 3 upstream deprecation warnings. The test verifies that keyboard focus can still reach the native Recording mode select while its visible companion shows a 2 px outline with 2 px offset, at least 24 CSS px target size, at least 3:1 effective indicator contrast after ancestor opacity, and no clipping by overflow ancestors. It also asserts the note-version selector is exposed as a named `group` and remains keyboard focusable. Four axe scans recorded zero violation groups and zero blocked external requests. Each scan remained `needs_manual_review` because axe marked color-contrast checks incomplete; this does not establish screen-reader behavior or full criterion conformance.

The focused workspace contract/layout/visual regression tests were then run sequentially with the same test settings; 39 passed with 1 upstream deprecation warning. The test report hash and byte size are listed in the evidence manifest. The report does not embed the browser-tool versions used for this targeted run; those versions remain unverified in this record.

This targeted result verifies specific browser/DOM behavior after the fixes. It does not establish complete 2.4.7 or 4.1.2 conformance. Image-based visual review, supported screen-reader testing, and checks of all other controls remain open.

### Full automated rerun after the interactive findings fixes

The complete opt-in accessibility suite was rerun after the two targeted repairs:

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-15-post-interactive-full .venv/bin/pytest -q --accessibility-audit tests/accessibility
```

Result: 22 passed with 3 upstream deprecation warnings. The run used Playwright 1.61.0, Google Chrome for Testing 149.0.7827.55, and axe-core 4.10.3. Eight reports contain 11 axe scans: zero violation groups, zero blocked requests, two `passed` scan states, and nine `needs_manual_review` states. The remaining incomplete axe rule in those nine scans was `color-contrast`. These are automated results for the recorded states, not criterion-level conformance. The focused workspace regression command also passed 39 tests with one upstream warning.

The post-fix full-run report hashes and byte sizes are in the [evidence manifest](evidence-manifest-2026-10-15.md). The focused target report is retained separately there as well. The targeted browser assertions support only the recorded focus companion and named-group behaviors; 2.4.7 and 4.1.2 remain untested at criterion-wide scope. No image-based review, supported screen-reader session, native microphone prompt assessment, or actual browser zoom assessment was performed.
