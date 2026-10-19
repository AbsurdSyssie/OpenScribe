# Scribe Guide verification: 16 October 2026

Assessment date: 16 October 2026\
Status: Automated synthetic-browser verification recorded. Human assistive-technology and actual browser-zoom review remains open. This record is specific to the on-screen Scribe Guide and is separate from the WCAG suite snapshot in [the 16 October WCAG evidence summary](evidence-summary-2026-10-16.md).

## Implemented behavior in scope

The Scribe sidebar's **Guide** button opens a native modal containing ten static workflow steps. It does not autostart or read, write, or remove the legacy `openscribe:tour:transcribe:<role>` local-storage value. Existing legacy values are left untouched. The guide provides Back, Next/Finish, Close, Escape, a step announcement, target highlighting with a visible fallback, and restores the opening tab and focus on close. It refuses to open while a visible dialog or a capture/dictation busy state is present. The synthetic capture-busy branch could not be established safely in the test harness; that guard was source-reviewed, not browser-exercised.

The guide only explains the workflow. It does not submit forms, invoke providers, start capture, or mutate consultation/source/draft content. Its ten topics are: start a consultation; return to recent work; choose an audio mode; review the transcript; keep Working note source separate; use post-consultation dictation deliberately; create a draft; review every section; prepare follow-ups carefully; and copy only verified text.

## Automated verification

The full opt-in accessibility suite ran against synthetic data with this command:

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app OPENSCRIBE_ACCESSIBILITY_REPORT_DIR=test-results/accessibility/2026-10-16-guide-verified .venv/bin/pytest -q tests/accessibility --accessibility-audit
```

The configured local PostgreSQL test connection required the supported elevated retry after sandbox access was denied. The full suite completed with 24 passed in 71.11 seconds and 3 upstream deprecation warnings. Across 25 scans in 10 reports, axe recorded zero violation groups/nodes, 21 `needs_manual_review` scans, 4 passed scans, and zero blocked external requests. The incomplete groups were `color-contrast` (18 groups/46 nodes), `aria-valid-attr-value` (5 groups/10 nodes), and `link-in-text-block` (2 groups/2 nodes). Incomplete checks are neither passes nor confirmed failures.

The guide report covers four synthetic states: empty and populated consultation states at desktop and a 320 CSS-pixel narrow viewport. It records four scans, zero violations, seven incomplete contrast nodes, and zero blocked external requests. Three scans have `needs_manual_review` status due to incomplete axe contrast checks; the populated narrow scan passed. The test also exercised manual opening, dialog keyboard containment and focus return, Escape/Finish restoration, target fallback, a test-only existing-modal guard, absence of local-storage use, and no mutating request while the guide was open. It does not establish a human screen-reader experience or actual browser zoom behavior.

Environment versions: Python 3.12, pytest 9.0.3, Playwright Python 1.61.0, Chromium 149.0.7827.55, axe-core 4.10.3. The separate focused workspace regression command also completed with 39 passed and one upstream Starlette/httpx deprecation warning:

```text
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q tests/test_workspace_frontend_contract.py tests/test_workspace_scribe_layout_contract.py tests/test_workspace_sidebar_visual_contract.py
```

The reviewed guide report is stored locally at `test-results/accessibility/2026-10-16-guide-verified/accessibility-audit-test_transcription_guide_keyboard_reflow_and_safe_states.json` (169,917 bytes; SHA-256 `5a34824837c0a52a55a7755cc708c2f085db7a02ec9110a8aa4003bfe84356a1`). It contains sanitized audit metadata, no screenshot/HTML/content, and no blocked-request entries. It is local test output, not a transferred legal or compliance evidence package.

## Human review still required

Use [H-11 in the human testing guide](human-testing-guide.md#h-11-on-screen-scribe-guide) for keyboard and screen-reader speech checks, actual browser zoom at 200% and 400%/320 CSS-pixel reflow, all-four-value text spacing, dialog/fallback behavior under real assistive technology, and a safe capture-busy test if a fake lifecycle fixture is available. No human screen-reader, native microphone, visual-image inspection, or actual browser zoom test is claimed by this record. Do not treat this guide-specific run as WCAG conformance evidence for the wider product.
