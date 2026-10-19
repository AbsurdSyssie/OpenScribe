# OpenScribe WCAG 2.2 human testing guide

Guide date: 16 October 2026\
Status: Instructions for a coordinator and human accessibility reviewer. This guide is not a test result, legal scope decision, or conformance claim.\
Related records: [criterion checklist](wcag-2.2-manual-checklist-2026-10-15.md), [15 October interactive findings](interactive-browser-assessment-2026-10-15.md), [15 October evidence manifest](evidence-manifest-2026-10-15.md), [16 October evidence summary](evidence-summary-2026-10-16.md), and [18 October Help verification](help-verification-2026-10-18.md). Procedure updated: 18 October 2026; this update records no new human test results.

## Purpose and limits

Use these steps to gather evidence the automated Playwright/axe suite cannot establish. A human reviewer must use the actual browser and operating system, and a supported assistive technology where a task calls for one. A browser accessibility tree, DOM inspection, axe result, computed style, or automated screenshot comparison can guide a review; none of these is a screen reader speaking to a user.

WCAG 2.2 is testable using both automated checks and human evaluation. The normative success criteria define requirements; W3C Understanding and Techniques documents explain them but do not replace the normative text. The W3C contrasts are 4.5:1 for normal text and 3:1 for large text under 1.4.3, and 3:1 for applicable component boundaries, states, and graphics under 1.4.11. Reflow at 400% zoom is evaluated at a width equivalent to 320 CSS pixels for vertically scrolling content under 1.4.10. See the [WCAG 2.2 Recommendation](https://www.w3.org/TR/WCAG22/), particularly [1.4.3](https://www.w3.org/TR/WCAG22/#contrast-minimum), [1.4.4](https://www.w3.org/TR/WCAG22/#resize-text), [1.4.10](https://www.w3.org/TR/WCAG22/#reflow), [1.4.11](https://www.w3.org/TR/WCAG22/#non-text-contrast), [1.4.12](https://www.w3.org/TR/WCAG22/#text-spacing), [2.4.7](https://www.w3.org/TR/WCAG22/#focus-visible), [2.4.11](https://www.w3.org/TR/WCAG22/#focus-not-obscured-minimum), [2.5.7](https://www.w3.org/TR/WCAG22/#dragging-movements), [3.3.8](https://www.w3.org/TR/WCAG22/#accessible-authentication-minimum), [4.1.2](https://www.w3.org/TR/WCAG22/#name-role-value), and the [W3C evaluation overview](https://www.w3.org/WAI/test-evaluate/).

## Coordinator: prepare the test environment

1. Select a disposable, isolated environment that runs the current candidate build. Use the established test fixture or approved test deployment workflow. Do not use production, a developer service on port 8080, a personal account, or a database containing real user data.
2. Arrange synthetic accounts and data through the maintained fixture/deployment path. Include only fictional user names, notes, dates, and generated drafts. Provide distinct accounts only where an existing role-based test requires them. Do not enter patient-like real information.
3. Keep provider integrations disabled and ensure no provider credentials are present. The reviewer must not submit generation or transcription work. Do not enable real microphone capture or save audio. If native microphone permission UI needs testing, provide a documented fake audio source or another approved isolated browser fixture. If the environment cannot provide that safely, mark those steps Not tested.
4. Confirm access to a disposable browser profile and the exact intended application origin. Record the build identifier or source revision, environment name, date, browser, operating system, display scale, viewport, and zoom before testing. Never include session IDs, cookies, passwords, MFA seeds, tokens, credentials, or private deployment paths in evidence.
5. Agree the supported browser and assistive-technology matrix before the session. Choose combinations the product owner intends to support; do not imply support or certification from this guide. Record product/version, OS/version, browser/version, AT/version, speech/output settings, and any relevant extensions. Use at least one keyboard-only run without AT and a separate run for each agreed screen-reader/browser combination.
6. Confirm where evidence may be stored. Use only the organization’s authorized restricted assessment location. Do not put recordings, screenshots, page dumps, browser profiles, accessibility-tree dumps, or source content in this repository. A screenshot may be stored only if it shows synthetic data, has been reviewed for secrets and identifying details, and the evidence owner approved that destination. Prefer a short written observation and measurements.
7. Prepare an evidence sheet using the template below. Give each test a named human reviewer and a second reviewer for failures when available. Stop immediately if real data, a real microphone, provider activity, or an unexpected external origin appears.

### Route and state matrix

Use the normal application's login entry and redirects; do not create routes around authentication. The paths below follow the maintained workspace reference. The coordinator must confirm that each page exists in the candidate and provide a safe synthetic state before the reviewer starts.

| Start page/state | Maintained route or entry | Synthetic prerequisite and useful controls |
| --- | --- | --- |
| Signed-out login | `/login` at the coordinator-supplied base URL | Synthetic account and known-invalid synthetic credential for recovery checks. If external SSO is enabled, assess it separately only when included in the approved scope and safe test setup. |
| Scribe workspace | `/workspace` | Synthetic owner with an empty workspace for navigation/dialog checks and a separate synthetic editor note for editor/copy checks. Current UI labels include Clinical Note, Follow Ups, Transcript, Add dictation, Change recording mode, Recording mode, Note versions, and Copy selected note lines. Confirm labels against the candidate. |
| Help | `/workspace/help` | Synthetic owner. Check topic links, keyboard focus, sidebar navigation, and narrow-screen reflow. Help explains workflows; reading it must not start capture or provider work. |
| Account | `/workspace/account` | Synthetic owner account only. Do not edit real profile or identity data. |
| Preferences | `/workspace/preferences` | Synthetic owner; record current values before a reversible change and restore them afterward. |
| Template library | `/workspace/library/templates?scope=personal` | Synthetic personal library item and an open import dialog if present. Do not import real content. |
| Quick Actions library | `/workspace/library/quick-actions?scope=personal` | Synthetic personal item and open import dialog if present. Do not store patient or transcript content. |
| Smart Phrase editor | `/workspace/library/smart-phrases?scope=personal` | Synthetic personal phrase data and invalid required-field state. Do not copy real phrases or personal data. |
| Native microphone prompt | Scribe recording control in `/workspace` | Disposable profile and coordinator-provided fake audio device, no provider credentials, and a verified no-persistence stop/cleanup boundary. If Allow could start capture or submit media, do not choose it unless the fixture safely intercepts that behavior; mark that branch Not tested. |
| Exported document | Normal synthetic export workflow, if in scope | Synthetic-only draft and document reader/AT combination agreed by the owner. No real clinical content. |

The test fixture does not establish that a route or state is safe in another deployment. Before each session, verify the candidate build, account, state setup, and cleanup path.

## Tools and interpretation

- Use browser keyboard controls and the browser’s built-in zoom. Record browser zoom separately from viewport resizing, operating-system magnification, and CSS transforms.
- Use browser contrast measurement tooling only as a measurement aid. Check the actual displayed pair, including backgrounds, overlays, opacity, gradients, focus rings, borders, text state, and the normal presentation expected by users. Recalculate or verify through a trusted WCAG contrast tool; record foreground/background values and the sampled state, not a screenshot with content.
- Use the browser accessibility tree to identify exposed names, roles, and states as a diagnostic. Confirm these observations with the agreed screen reader. The tree is not a spoken announcement and does not prove what a screen reader user hears.
- Use the operating system’s high-contrast/forced-colors mode if that mode is part of the chosen support matrix. Record exact OS and browser settings. Do not treat a contrast theme or forced-colors result as a substitute for measuring WCAG contrast in the expected presentation.
- Use approved evidence capture only. Do not export full DOM, accessibility snapshots, network logs, browser profiles, storage, or recordings. Record concise findings with a page/workflow label, control label where safe, state, measured value, and result.

## Human test tasks

Run each task through all applicable states in the agreed browser matrix. For each row, set one disposition: **Pass**, **Fail**, **Not applicable** with a reason approved by the coordinator, or **Not tested** with the blocker. Do not infer a pass from a partial action or a different criterion.

### H-01: Keyboard-only end-to-end operation

**Criteria:** 2.1.1 Keyboard, 2.1.2 No Keyboard Trap, 2.4.3 Focus Order, 2.4.7 Focus Visible, 2.4.11 Focus Not Obscured (Minimum), 3.2.1 On Focus, 3.2.2 On Input.

**Setup:** Begin signed out at the normal login entry and use only keyboard controls through login. Start each separate page from a fresh reload. If another page needs a fresh login, sign out and repeat from login; do not use a pointer to skip a workflow step. Use Tab, Shift+Tab, Enter, Space, arrow keys where the widget calls for them, Escape, and ordinary character input.

**Actions:**

1. Traverse login, validation error, recovery links, and successful login. Confirm focus order follows the visible and meaningful order; identify each focus target visually.
2. Traverse the workspace navigation, Help topic links, and Scribe tabs. Activate each tab using its documented key behavior; verify the selected panel changes and focus remains predictable.
3. In Scribe, traverse the recording-mode control and its visible companion, editor fields, note-version control, row checkbox, row move controls, add/remove actions, and copy/review controls. Use keyboard alternatives for any drag action.
4. Open the dictation dialog. Tab and Shift+Tab through its controls, try Escape and Cancel, and check that focus returns to the invoking control. Reopen it and test each non-destructive path. Do not record audio.
5. Traverse the consultation rail and any open overlays, banners, toasts, and menus. Confirm sticky or fixed content does not cover the entire focused component and the focus indicator remains perceivable.
6. Repeat a complete copy/review workflow only up to its safe synthetic boundary. Confirm the review gate cannot be bypassed by keyboard and do not submit provider or clinical work.

Explicitly check Tab into the native Recording mode select and its visible Change recording mode companion, then the named Note versions group, synthetic editor row controls, and Copy selected note lines. Earlier spot checks implicated these controls; targeted post-fix automation did not establish criterion-wide conformance.

**Expected:** Every function in scope has a keyboard route; no trap occurs; focus order is meaningful; the visible focus indicator remains perceptible and the focused control is not entirely hidden by author-created content. Record pointer-only steps, unexpected context changes, lost focus, focus obscuration, and inability to return.

### H-02: Screen-reader workflows and announcements

**Criteria:** 1.1.1 Non-text Content, 1.3.1 Info and Relationships, 1.3.2 Meaningful Sequence, 2.4.2 Page Titled, 2.4.6 Headings and Labels, 2.5.3 Label in Name, 3.3.1 Error Identification, 3.3.2 Labels or Instructions, 4.1.2 Name, Role, Value, 4.1.3 Status Messages.

**Setup:** Use one coordinator-approved screen reader and browser combination at a time. Start with default speech settings; record any changes. Keep keyboard navigation available. Do not use DOM names or accessibility-tree output as a substitute for announcements.

**Navigation quick start:** Follow the reviewer's version-specific screen-reader manual and settings. Current NVDA documentation describes Browse Mode for web reading and `NVDA+Space` to switch Browse/Focus modes; Tab may switch modes automatically when it reaches a control. On macOS VoiceOver, `VO` commonly means Control+Option; `VO+Shift+Down Arrow` interacts with a group and `VO+Shift+Up Arrow` stops interacting. These are orientation examples, not a required support matrix or expected spoken wording. Record exact versions and consult the [NVDA User Guide](https://download.nvaccess.org/releases/stable/documentation/en/userGuide.html) and [Apple VoiceOver interaction commands](https://support.apple.com/guide/voiceover/interaction-commands-cpvokys07/10/mac/27).

**Actions:**

1. Sign in and navigate by headings, landmarks, links, and form fields. Confirm page title, labels, instructions, required state, and control names make sense when read out of visual context.
2. Submit an intentionally invalid synthetic login. Listen for an error announcement and identify whether the affected field and next step are clear. Follow the existing authentication contract when recovering: preserve useful non-secret context where intended, while allowing passwords, OTPs, or other secrets to clear by design.
3. Navigate the workspace tabs, Recording mode control, note-version group, editor, row checkbox and movement controls. Verify each control exposes a useful name, role, state, and value, and that visible text labels are included in the spoken name where appropriate.
4. Open and dismiss the dictation dialog with keyboard. Confirm dialog name and boundaries, text-field label, buttons, focus return, and no unexpected speech or focus loss.
5. Trigger a safe synthetic status update or validation message available in the test fixture. Record whether it is announced once, at the correct time, without moving focus or repeating stale content. Do not trigger a provider action.
6. Inspect any non-text controls or icons encountered. Determine whether the spoken alternative conveys their purpose; distinguish decorative icons from controls.
7. Use the Account and Preferences routes, then personal-scope Templates and Quick Actions pages, including an import dialog, and the Smart Phrase editor with a synthetic invalid required field. Confirm each utility group, button, dialog, form control, and error has a useful name, role, state, and value. Confirm a control that opens a dialog exposes a relationship to the actual dialog when active; a source ID or browser tree alone is not proof that the running control is properly connected.
8. In import dialogs, inspect links and adjacent text. Determine whether link purpose remains clear and visually distinguishable without relying on color alone. A scanner's `link-in-text-block` incomplete status is a prompt to inspect, not a confirmed failure.

**Expected:** Speech identifies the same function/state that a sighted keyboard user perceives, relationships remain understandable, and status/error messages announce useful information without requiring focus movement. Record exact spoken output only when it contains no sensitive or user content; otherwise paraphrase the issue.

Sanitized example: `H-02; /workspace synthetic editor; Tab to Note versions; screen reader reported “group, Note versions”; Pass for this one observation; no note text captured.` Replace it with the reviewer's actual observation; do not copy it as a result.

### H-03: Contrast and visible state inventory

**Criteria:** 1.4.3 Contrast (Minimum), 1.4.11 Non-text Contrast, 2.4.7 Focus Visible.

**Setup:** Use the standard expected visual presentation at default zoom. Capture color values with approved tools and note each state. W3C thresholds: normal text 4.5:1; large text 3:1; applicable control boundaries, state indicators, and meaningful graphics 3:1 against adjacent color(s). Large text means at least 18 pt regular or 14 pt bold under WCAG definitions. Consider criterion exceptions only when their conditions actually apply.

**Actions:**

1. Measure normal and large text against its actual adjacent background on Login, validation, workspace navigation, selected/unselected tabs, Scribe, editor, dialog, notifications, and any coordinator-selected forms. Include primary, secondary, muted, disabled, placeholder, hover, active, invalid, and selected states that are visible and in scope. In the synthetic editor, measure unchecked row text at the new 0.7 row-opacity setting and the drag handle/icon, plus focused and selected states; do not infer a pass from source values until the rendered background is confirmed.
2. Measure meaningful non-text boundaries and indicators for text inputs, selects, checkboxes, buttons, tabs, icons, selected state, and focus outlines. Include the Recording mode visible focus companion and editor controls.
3. Inspect layered surfaces: toast over page content, modal backdrop/dialog, translucent controls, and any overlapping panel. Measure the composited presentation as users see it; an uncomposited CSS color calculation alone is insufficient when opacity or overlays change the result.
4. Switch to the agreed OS high-contrast/forced-colors configuration. Repeat H-01’s focus and control recognition checks; record unsupported styling or missing state cues. Restore the original OS setting afterward.

**Current queue from the 15 October axe incompletes:** These rendered targets have not been proven to fail contrast. Measure the actual composited colors and record the exact state: Login hero eyebrow, heading, intro and emphasized feature text; Login validation toast; narrow/320 CSS px recording hero timer; a redacted inline target in the narrow state; dictation dialog textarea; affected statement-row drag handles in editor/copy and post-edit states; revealed/hidden compact-dictation kicker, title, body and visibility control; generic and success toast messages. The automated report did not provide ratios. Gradients and opacity affect several surfaces, so do not calculate from uncomposited source colors. See the [16 October evidence summary](evidence-summary-2026-10-16.md) for a state-by-state queue and final investigation results when available.

The 16 October expanded personal-library scans add incomplete reviews for popup-button `aria-controls` relationships and help-button attributes in Account, Templates, Quick Actions, and Smart Phrases, as well as link text in the Template and Quick Actions import dialogs. These are axe incomplete checks, not observed violations. Verify each control in the running page and agreed screen reader: determine the exposed name/role/state, confirm each active popup relationship reaches the correct rendered dialog, and check that adjacent links remain distinguishable without color alone. The code review found referenced IDs in source templates, but that fact alone does not prove runtime behavior. Recheck after the utility-group role adjustment is present in the candidate build.

The final 16 October run removed the utility-group `aria-prohibited-attr` incompletes, but retained 5 `aria-valid-attr-value` rule groups and 2 `link-in-text-block` groups as incomplete manual-review signals. It also retained 15 `color-contrast` rule groups (40 target nodes); the tool did not calculate ratios for those nodes. For the statement drag-handle glyph only, the rendered browser check measured checked row hover/own-hover/focus at 11.40:1/7.53:1/7.91:1 and unchecked row hover/own-hover/focus at 4.67:1/3.60:1/3.60:1 after ancestor opacity compositing. These exceed 3:1 for the non-text glyph in those sampled states. The computed colors were not retained; do not extend these measurements to text or other states. The [16 October evidence summary](evidence-summary-2026-10-16.md) contains the reviewed run record and hashes.

**Expected:** Each applicable displayed pair meets the relevant threshold in its expected presentation, and every meaningful control/state remains distinguishable. Record actual ratios and colors, component/state labels, tool and version, and any exception rationale. A failed or incomplete automated contrast check remains open until a human verifies the relevant rendered pairs.

### H-04: 200% browser zoom and 400% reflow

**Criteria:** 1.4.4 Resize Text, 1.4.10 Reflow, 1.4.11 Non-text Contrast, 2.4.11 Focus Not Obscured (Minimum).

**Setup:** Use browser zoom, not OS magnification or CSS scaling. Record the browser’s displayed zoom and viewport CSS dimensions. Test portrait/landscape only if part of the intended device support matrix. WCAG 1.4.4 requires text to resize to 200% without loss of content or function. WCAG 1.4.10 assesses vertically scrolling content at a width equivalent to 320 CSS pixels; horizontal-scrolling content uses a height equivalent to 256 CSS pixels, subject to the criterion’s two-dimensional-layout exception.

**Actions:**

1. At 100%, record the normal browser window and relevant page/workflow. Increase browser zoom to 200%. Verify text enlargement and complete access to labels, controls, status, dialogs, editor content, and navigation. Try all interactive tasks in H-01 that apply.
2. Increase browser zoom to 400%. For example, start with a browser content viewport near 1280 CSS pixels wide at 100%, then zoom to 400%; the effective layout viewport should be near 320 CSS pixels. Do not also set the window to 320 CSS pixels before zooming, because that would test a much narrower width. Record actual browser zoom and resulting CSS viewport. Test Login, validation, workspace navigation, Scribe, editor, dialog, and copy/review flow. Check for lost content/function, horizontal scrolling in two dimensions, overlap, clipping, and focus hidden by sticky panels.
3. For any truly two-dimensional content, document why both dimensions are essential and test its alternative navigation. Do not use the exception for ordinary text or form layouts.
4. Restore zoom and viewport after recording results.

**Expected:** The complete workflow remains usable at 200%; at 400%/320 CSS px ordinary content reflows without loss and without two-dimensional scrolling, except where WCAG permits a documented two-dimensional layout. Focus remains visible and not entirely obscured. A resized window or `innerWidth` measurement without actual browser zoom does not complete this task. No horizontal overflow alone does not prove text, instructions, controls, or function remain available.

### H-05: Text-spacing override

**Criteria:** 1.4.12 Text Spacing.

**Setup:** Apply user style overrides without changing application source. WCAG specifies line height at least 1.5 times font size, paragraph spacing at least 2 times font size, letter spacing at least 0.12 times font size, and word spacing at least 0.16 times font size. Apply all four together.

**Actions:**

1. Apply the overrides in browser/user style tooling or a coordinator-provided test stylesheet.
2. Visit Login, error state, workspace, Scribe editor, dialog, and notification states. Operate the controls and complete keyboard workflows that apply.
3. Check clipped or overlapping text, hidden labels, truncated controls, missing content, loss of function, or required horizontal/vertical two-dimensional scrolling.
4. Remove the override by reloading/restoring the profile and confirm the original page presentation returns.

**Expected:** No content or function is lost when all specified spacing values apply. A measured page width or absence of horizontal overflow alone does not establish a pass.

### H-06: Focus appearance under overlays and system display settings

**Criteria:** 1.4.11 Non-text Contrast, 2.4.7 Focus Visible, 2.4.11 Focus Not Obscured (Minimum).

**Setup:** Use the agreed browser and OS with default presentation, then the agreed high-contrast/forced-colors mode. This task complements H-03 and H-01; it does not test AAA 2.4.13.

**Actions:**

1. Tab through every interactive control while notification banners, dialog backdrops, expanded navigation, and sticky/fixed content are present.
2. Record where the focused component or its indicator intersects an overlay. Determine whether the control itself is entirely hidden and whether the indicator remains distinguishable against all adjacent colors.
3. Repeat with forced colors/high contrast enabled. Confirm focus, checked, selected, invalid, and disabled states remain distinguishable and controls remain operable.

**Expected:** Focused controls are at least partially visible and focus indicators are visible; applicable non-text contrast remains sufficient. Do not merge 2.4.7 focus-indicator visibility and 2.4.11 focused-component obscuration into one disposition.

### H-07: Microphone permission prompt, denial, and recovery

**Criteria:** 1.3.3 Sensory Characteristics, 2.1.1 Keyboard, 2.1.2 No Keyboard Trap, 2.4.3 Focus Order, 3.3.1 Error Identification, 3.3.2 Labels or Instructions, 4.1.2 Name, Role, Value, 4.1.3 Status Messages.

**Setup:** Only run where the coordinator has arranged an isolated browser profile, fake audio input, no provider credentials, no external provider route, and an explicit no-real-audio/no-upload test boundary. Browser flags or Playwright grants that auto-allow or auto-deny access bypass the native prompt and cannot count as prompt evidence. Do not grant access to a real microphone. If the app automatically starts capture after Allow, proceed only when the fake input is confirmed and the coordinator has verified capture cannot persist or leave the environment. Otherwise do not choose Allow; mark that branch Not tested and leave native prompt coverage to the authorized platform owner.

**Actions:**

1. Begin the product action that requests microphone permission, but do not start or save a recording. Use the keyboard to reach and respond to the native browser/OS prompt.
2. Record whether browser/OS UI identifies the requesting origin and offers clear allow, deny, and dismiss choices. Record exact browser and OS versions; the native prompt is not part of the webpage accessibility tree.
3. Choose deny. Confirm OpenScribe explains the problem and recovery path through visible text and the agreed screen reader; confirm keyboard focus returns to a meaningful control.
4. Reset permission in the disposable profile. Repeat, choosing dismiss/cancel. Confirm the page remains usable and does not imply permission was granted.
5. Only if the approved fake-device setup and verified application lifecycle make Allow safe, grant temporary access and follow the coordinator's documented stop point. Do not assume permission can be granted without capture starting. Confirm the app communicates permission state, stop capture before any save/upload boundary, revoke permission, and close the profile. If the stop point cannot be verified first, mark Allow Not tested.

**Expected:** The person can understand and control permission choice, denial and dismissal do not trap focus or falsely report success, and recovery works without real capture or provider submission. This check does not establish full browser/OS platform accessibility across other versions.

### H-08: Authentication, paste, password manager, MFA, and error recovery

**Criteria:** 1.3.5 Identify Input Purpose, 2.1.1 Keyboard, 3.3.1 Error Identification, 3.3.2 Labels or Instructions, 3.3.3 Error Suggestion, 3.3.8 Accessible Authentication (Minimum), 4.1.3 Status Messages.

**Setup:** Use only the synthetic account and the approved disposable password-manager profile. The coordinator must confirm which MFA methods are enabled in the candidate environment. Do not weaken MFA or change production authentication policy to simplify this review.

**Actions:**

1. Navigate login and recovery entirely by keyboard and screen reader. Check labels, input-purpose metadata where applicable, required cues, and error identification.
2. Paste synthetic credentials into the fields. Use the approved password manager’s fill workflow. Confirm the application accepts the supported method without requiring transcription or memorization of credentials when the criterion’s exception does not apply.
3. Submit an invalid synthetic password; verify the error identifies the problem without exposing whether an unrelated account exists. Recover by correcting the field and resubmitting. Do not require the browser or application to retain a password or OTP after rejection; record whether non-secret context follows the established authentication behavior.
4. If MFA is provisioned, complete the test MFA method using synthetic credentials. Assess keyboard, screen reader, paste/autofill, timeout, and recovery behavior without disabling or bypassing the control. If MFA is absent from this test deployment, record that limitation; do not infer MFA behavior.
5. Test logout and session-expiry messaging only through the coordinator’s safe test mechanism. Confirm reauthentication retains the needed context where intended and does not expose secrets.

**Expected:** Authentication does not impose a prohibited cognitive function test without an allowed alternative, and established security controls remain intact. Paste/password-manager behavior and MFA findings are recorded separately. Do not write credentials or OTPs into notes, screenshots, logs, or evidence.

### H-09: Dragging alternatives and target size

**Criteria:** 2.5.7 Dragging Movements, 2.5.8 Target Size (Minimum), 2.1.1 Keyboard.

**Setup:** Use only the synthetic Scribe editor. Identify row movement, resize, reorder, or other drag actions currently rendered. Do not alter or save source content.

**Actions:**

1. Identify every operation that requires dragging. For each, try an alternative single-pointer method such as a move control/menu, and a keyboard-only method.
2. Confirm the alternative achieves the same result without dragging unless dragging is essential. Record any constraints requiring precise movement.
3. Measure rendered pointer target dimensions and spacing in CSS pixels for controls used in these flows, then inspect exceptions against the normative 2.5.8 text. Test targets at normal and narrow/reflowed layouts.

**Expected:** Dragging functionality has an equivalent single-pointer alternative unless the criterion’s exception applies. Target-size results are recorded by control and state; automated absence of an axe finding alone does not close the criterion.

### H-10: Exported content and scope decision

**Criteria:** Criteria applicable to exported/downloaded documents and complete processes, including 1.1.1, 1.3.1, 1.3.2, 1.4.3, 2.4.2, and the relevant WCAG conformance requirements.

**Setup:** The product owner and legal/accessibility coordinator decide whether generated downloads, print views, or third-party content fall within the assessment scope. Use synthetic material only. Do not produce or retain a clinical document that resembles a real patient record.

**Actions:**

1. Record the scope decision, accountable owner, document formats, and applicable full user processes before testing.
2. If in scope, export synthetic content using the normal product workflow. Inspect structure, reading order, language, title, headings, tables, links, contrast, and keyboard/screen-reader use in the target document reader. Record exact reader and AT versions.
3. If the owner has not decided scope or no safe document is available, mark Not tested. Do not mark Not applicable without a reason approved by the responsible organization.

**Expected:** The organization records an explicit scope decision and evidence for each included format. A web-page scan does not establish an exported document’s accessibility.

### H-11: On-screen Scribe Guide

**Criteria:** 1.4.4 Resize Text, 1.4.10 Reflow, 1.4.12 Text Spacing, 2.1.1 Keyboard, 2.1.2 No Keyboard Trap, 2.4.3 Focus Order, 2.4.7 Focus Visible, 2.4.11 Focus Not Obscured, 4.1.2 Name, Role, Value, and 4.1.3 Status Messages.

**Setup:** Use a synthetic owner at `/workspace` in a disposable browser profile, with no active capture and no other open dialog. Record the browser, OS, assistive technology (if used), viewport, zoom, and display scale. The automated guide check covers four synthetic desktop/narrow states, but it does not establish actual screen-reader speech or real browser zoom; see the separate [guide verification record](scribe-guide-verification-2026-10-16.md).

**Actions:**

1. Reach **Guide** in the Scribe sidebar with the keyboard and open it. Confirm the dialog is named and described, focus enters the guide, background controls cannot be operated, and the current step/progress is announced by the agreed screen reader.
2. Use Tab and Shift+Tab through the dialog. Confirm focus remains contained and visible, and controls are announced with usable names and states. Check that each **Next** and **Back** action announces the new title, body, and step count without duplicate or stale announcements.
3. Complete the steps with Next/Finish. Repeat and close with **Close**, Escape, and (where appropriate) backdrop dismissal. Confirm each path restores the original workspace tab and keyboard focus to the opener. Verify the guide is not shown after reload unless explicitly opened.
4. Test both an empty consultation and a populated synthetic consultation. When a step target is absent or hidden, confirm the guide continues with a sensible fallback rather than skipping, closing, or highlighting unrelated content.
5. At 200% browser zoom, inspect the guide card, text, controls, focus, and highlighted target. At 400% browser zoom with a 1280 CSS-pixel starting viewport (equivalent to about 320 CSS pixels), confirm the guide remains readable and operable without two-dimensional scrolling or clipped controls. Do not combine zoom with a separately reduced viewport. Apply all four WCAG text-spacing overrides and repeat.
6. Repeat with every agreed screen-reader/browser combination. Record the exact spoken title/body/progress and control names, keyboard sequence, unexpected focus movement, and any announcement timing. An accessibility-tree snapshot alone is not speech evidence.
7. Open the guide while another dialog is open and while a synthetic capture-busy state can be safely established. Confirm it explains why it cannot open and does not alter capture/source/draft state. If no safe capture-busy fixture exists, mark that branch Not tested; do not start a real microphone.

**Expected:** The guide is a manually opened aid; its dialog, step changes, dismissal, focus return, fallback, and reflow work in the selected human browser/AT matrix. The automated four-state check and source review do not close this criterion scope. Do not claim no-autostart persistence across unrelated profiles; verify the behavior in the disposable profile and record it.

## Record outcomes and retest

Use this block for every test ID and every browser/AT pair. Keep each outcome atomic; do not average failures and passes across browsers or workflows.

```text
Test ID:
Criterion(s):
Candidate build/revision:
Environment identifier (non-secret):
Date/time and reviewer:
Browser/version and OS/version:
Assistive technology/version, if used:
Viewport CSS dimensions / browser zoom / display scale:
Workflow and synthetic state:
Steps completed:
Observed result (concise, no user content):
Measured values and tool/version, if relevant:
Disposition: Pass | Fail | Not applicable | Not tested
Evidence reference in approved restricted location:
Failure severity: Critical workflow blocker | Major barrier | Moderate barrier | Minor issue | Informational
Severity rationale and affected user/task:
Immediate safe workaround, if any:
Follow-up owner role/name and target date:
Retest build/revision, date, browser/AT versions, steps, result:
Cleanup completed:
```

Use severity to prioritize user impact, not as a WCAG conformance level. A failure that blocks login, navigation, editing, review, or safe exit is a workflow blocker. Record evidence in a restricted assessment location; remove or avoid content-bearing captures. For retest, reproduce the same browser, state, and exact steps, then check adjacent workflows for regressions. Keep the original failure record and append the retest result. Do not convert a fix or automated assertion into a criterion-wide pass until the reviewer completes the relevant criterion scope.

## Cleanup and rollback

1. Stop any in-progress workflow before it records, uploads, generates, or dispatches content. If accidental real data or audio enters the environment, stop and follow the organization's incident and deletion process; do not copy it into evidence.
2. Log out of the synthetic account, close the disposable browser profile, clear its site data and permission grants, and remove temporary local overrides.
3. Ask the environment owner to reset or destroy only the disposable test data using the established cleanup path. Do not run manual SQL, clear a shared database, delete another user's data, or change app retention settings.
4. Return OS accessibility and display settings, browser zoom, high-contrast mode, and audio input to their recorded original values.
5. Review evidence for content, identifiers, secrets, URLs, profile data, and accidental recordings. Store only approved sanitized evidence. Record cleanup completion and any unresolved residue in the test sheet.

## Current handoff queue

The [16 October evidence summary](evidence-summary-2026-10-16.md) records the automated contrast investigation and expanded browser states. The separate [Scribe Guide verification record](scribe-guide-verification-2026-10-16.md) covers the later on-screen guide automation. Remaining human tasks in the [criterion checklist](wcag-2.2-manual-checklist-2026-10-15.md) include full contrast and ARIA/link review; screen-reader verification of the note-version group, guide, and status/error announcements; complete keyboard/focus-obscuration coverage; actual 200% browser zoom and 400%/320 CSS-pixel reflow; all-four-value text spacing; microphone permission allow/deny/dismiss; password-manager, paste, and provisioned MFA; drag alternatives/target size; and the product owner's exported-document scope decision. Keep each criterion Not tested until a human provides evidence.
