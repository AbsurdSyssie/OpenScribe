import pytest

from tests.accessibility.conftest import login, navigate, scan
from tests.accessibility.audit import (
    STATE_ANONYMOUS_LOGIN,
    STATE_AUTH_SCRIBE_EMPTY,
    STATE_AUTH_SCRIBE_NARROW,
    STATE_AUTH_WORKSPACE,
    STATE_CLINICAL_MANUAL,
    STATE_DIALOG_NOTIFICATIONS,
    STATE_LOGIN_VALIDATION,
    STATE_RESPONSIVE_KEYBOARD,
)


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


def _contrast_ratios(page, selectors: list[str]) -> list[float]:
    return page.evaluate(
        """(selectors) => {
          const channel = (value) => {
            const number = Number(value) / 255;
            return number <= 0.04045 ? number / 12.92 : ((number + 0.055) / 1.055) ** 2.4;
          };
          const parseColor = (value) => {
            const match = value.match(/rgba?\\(([^)]+)\\)/);
            if (!match) return null;
            const parts = match[1].split(',').map((part) => Number.parseFloat(part.trim()));
            return { rgb: parts.slice(0, 3), alpha: parts.length > 3 ? parts[3] : 1 };
          };
          const composite = (front, back) => front.rgb.map((value, index) => value * front.alpha + back[index] * (1 - front.alpha));
          const luminance = (rgb) => 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
          const ratios = [];
          for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
              if (!element.getClientRects().length) continue;
              let background = [255, 255, 255];
              const ancestors = [];
              for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) ancestors.push(ancestor);
              for (const ancestor of ancestors.reverse()) {
                const parsed = parseColor(getComputedStyle(ancestor).backgroundColor);
                if (parsed && parsed.alpha > 0) {
                  background = composite(parsed, background);
                }
              }
              const foreground = parseColor(getComputedStyle(element).color);
              if (!foreground) continue;
              const foregroundRgb = foreground.alpha < 1 ? composite(foreground, background) : foreground.rgb;
              const values = [luminance(foregroundRgb), luminance(background)].sort((a, b) => b - a);
              ratios.push((values[0] + 0.05) / (values[1] + 0.05));
            }
          }
          return ratios;
        }""",
        selectors,
    )


def _assert_controls_named(page) -> None:
    unnamed_count = page.locator("select").evaluate_all(
        "elements => elements.filter(element => !(element.labels?.length || element.getAttribute('aria-label')?.trim() || element.getAttribute('aria-labelledby')?.trim())).length"
    )
    assert unnamed_count == 0, "A rendered select control has no programmatic accessible name"


def _assert_text_contrast(page, selectors: list[str]) -> None:
    ratios = _contrast_ratios(page, selectors)
    assert ratios, "Expected visible text contrast targets were not rendered"
    assert min(ratios) >= 4.5, "Visible normal text is below the WCAG AA contrast threshold"


@pytest.mark.accessibility_states(
    {
        STATE_ANONYMOUS_LOGIN: "executed",
        STATE_AUTH_WORKSPACE: "executed",
        STATE_AUTH_SCRIBE_EMPTY: "executed",
        STATE_AUTH_SCRIBE_NARROW: "executed",
        STATE_CLINICAL_MANUAL: "needs_manual_review",
    }
)
def test_isolated_synthetic_pages_have_no_axe_wcag_violations(audit_report, audit_browser, synthetic_audit_user, axe_source):
    """Scan an empty, newly created synthetic owner through the real app UI."""
    page, blocked = audit_browser
    navigate(page, "/login", audit_report, STATE_ANONYMOUS_LOGIN)
    _assert_text_contrast(page, [".auth-page label", ".auth-page footer *"])
    results = [scan(page, audit_report, STATE_ANONYMOUS_LOGIN, axe_source, blocked)]

    login(page, synthetic_audit_user, audit_report)
    navigate(page, "/workspace", audit_report, STATE_AUTH_WORKSPACE)
    _assert_controls_named(page)
    _assert_text_contrast(
        page,
        [".workspace-sidebar__identity > small", ".tab-shell__tab", ".workspace-sidebar a"],
    )
    results.append(scan(page, audit_report, STATE_AUTH_WORKSPACE, axe_source, blocked))

    navigate(page, "/transcribe", audit_report, STATE_AUTH_SCRIBE_EMPTY)
    _assert_controls_named(page)
    results.append(scan(page, audit_report, STATE_AUTH_SCRIBE_EMPTY, axe_source, blocked))

    page.set_viewport_size({"width": 390, "height": 844})
    try:
        page.reload(wait_until="domcontentloaded")
    except Exception:
        audit_report.record_coverage(STATE_AUTH_SCRIBE_NARROW, "failed", "navigation failed")
        raise
    results.append(scan(page, audit_report, STATE_AUTH_SCRIBE_NARROW, axe_source, blocked))
    audit_report.record_coverage(
        STATE_CLINICAL_MANUAL,
        "needs_manual_review",
        "Other consultation workflows, clinician review, assistive technology, and full keyboard conformance require manual assessment.",
    )
    violation_count = sum(len(result.get("violations", [])) for result in results)
    if violation_count:
        pytest.fail(f"axe found {violation_count} accessibility rule violation(s) across {len(results)} scanned states.", pytrace=False)


@pytest.mark.accessibility_states({STATE_DIALOG_NOTIFICATIONS: "executed"})
def test_dialog_keyboard_and_notifications(
    audit_report, audit_browser, synthetic_audit_user, axe_source
):
    page, blocked = audit_browser
    login(page, synthetic_audit_user, audit_report)
    response = page.goto("/transcribe", wait_until="domcontentloaded")
    assert response is not None and response.ok
    page.locator("[data-workspace-scribe-main]").wait_for(state="visible")
    assert page.locator('form[action="/login"]').count() == 0, "Authenticated Scribe state rendered the login form"

    # The empty container itself has no prohibited naming attribute. A synthetic
    # notification exercises the actual announcer without persisting user data.
    container = page.locator("[data-toast-container]")
    assert container.count() == 1
    assert container.get_attribute("aria-label") is None
    page.evaluate("window.showToast('Synthetic accessibility notification', 'info', 0)")
    notice = page.locator("[data-toast-container] .toast").last
    assert notice.get_attribute("role") == "status"
    assert notice.get_attribute("aria-live") == "polite"
    assert notice.get_attribute("aria-atomic") == "true"

    # Keyboard open and safe cancel must place focus in the dialog and return it
    # to the opener. Opening creates only an empty synthetic consultation root.
    storage_notice = page.locator("[data-browser-storage-notice]")
    if storage_notice.is_visible():
        storage_notice.get_by_role("button", name="Dismiss").click()
    opener = page.locator("[data-dictation-cta]")
    page.locator("body").focus()
    for _ in range(80):
        page.keyboard.press("Tab")
        if opener.evaluate("element => document.activeElement === element"):
            break
    else:
        pytest.fail("Keyboard focus did not reach the dictation dialog trigger", pytrace=False)
    with page.expect_response(lambda item: item.request.method == "POST" and "/transcripts/start" in item.url):
        page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Post-consultation dictation")
    dialog.wait_for(state="visible")
    assert dialog.locator("textarea").evaluate("element => document.activeElement === element")
    dialog_scan = scan(page, audit_report, STATE_DIALOG_NOTIFICATIONS, axe_source, blocked)
    assert not dialog_scan.get("violations"), "axe found accessibility violations in the open dialog state"
    cancel = dialog.get_by_role("button", name="Cancel", exact=True)
    for _ in range(20):
        if cancel.evaluate("element => document.activeElement === element"):
            break
        page.keyboard.press("Tab")
    else:
        pytest.fail("Keyboard focus did not reach the dialog cancellation control", pytrace=False)
    page.keyboard.press("Enter")
    dialog.wait_for(state="hidden")
    page.wait_for_function("element => document.activeElement === element", arg=opener.element_handle())

    _assert_text_contrast(page, [".workspace-sidebar__identity > small", ".tab-shell__tab"])
    audit_report.record_keyboard(
        "dictation_dialog",
        "passed",
        "Keyboard opened the empty dictation dialog, focus entered its text field, and Cancel returned focus to the opener.",
    )


@pytest.mark.accessibility_states({STATE_RESPONSIVE_KEYBOARD: "executed"})
def test_narrow_reflow_and_keyboard_focus_are_visible(
    audit_report, audit_browser, synthetic_audit_user, axe_source
):
    page, blocked = audit_browser
    login(page, synthetic_audit_user, audit_report)
    response = page.goto("/transcribe", wait_until="domcontentloaded")
    assert response is not None and response.ok
    page.locator("[data-workspace-scribe-main]").wait_for(state="visible")
    assert page.locator('form[action="/login"]').count() == 0, "Authenticated Scribe state rendered the login form"
    page.set_viewport_size({"width": 320, "height": 720})
    page.wait_for_timeout(150)
    dimensions = page.evaluate(
        "() => ({client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})"
    )
    assert dimensions["scroll"] <= dimensions["client"], "Scribe content overflows the 320 CSS pixel viewport"
    cta = page.locator("[data-dictation-cta]")
    page.locator("body").focus()
    for _ in range(80):
        page.keyboard.press("Tab")
        if cta.evaluate("element => document.activeElement === element"):
            break
    else:
        pytest.fail("Keyboard focus did not reach the dictation control", pytrace=False)
    visible_focus = cta.evaluate(
        """element => {
          const style = getComputedStyle(element);
          const hasOutline = style.outlineStyle !== 'none' && style.outlineWidth !== '0px';
          return element.matches(':focus-visible') && (hasOutline || style.boxShadow !== 'none');
        }"""
    )
    assert visible_focus, "Keyboard focus styling is absent on the reachable dictation control"
    result = scan(page, audit_report, STATE_RESPONSIVE_KEYBOARD, axe_source, blocked)
    assert not result.get("violations"), "axe found accessibility violations at the 320 CSS pixel viewport"
    audit_report.record_keyboard(
        "responsive_focus",
        "passed",
        "At 320 CSS pixels the page did not overflow horizontally and the reachable dictation control had focus styling; 200% zoom and assistive technology were not tested.",
    )


@pytest.mark.accessibility_states({STATE_LOGIN_VALIDATION: "executed"})
def test_login_validation_error_is_axe_checked(audit_report, audit_browser, synthetic_audit_user, axe_source):
    page, blocked = audit_browser
    response = page.goto("/login", wait_until="domcontentloaded")
    assert response is not None and response.ok
    page.locator('form[action="/login"] input[name="email"]').fill(synthetic_audit_user["email"])
    page.locator('form[action="/login"] input[name="password"]').fill("invalid-synthetic-password")
    with page.expect_response(lambda item: item.request.method == "POST" and item.url.endswith("/login")):
        page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_function(
        "() => { const toast = document.querySelector('[data-toast-container] .toast'); return toast && getComputedStyle(toast).opacity === '1'; }",
        timeout=3_000,
    )
    assert page.locator('form[action="/login"]').count() == 1
    assert page.locator("[data-workspace-shell], [data-workspace-scribe-main]").count() == 0
    result = scan(page, audit_report, STATE_LOGIN_VALIDATION, axe_source, blocked)
    assert not result.get("violations"), "axe found accessibility violations in the rejected-login state"
    toasts = page.locator("[data-toast-container] .toast")
    assert toasts.count() == 1, "Rejected login did not create an error notification"
    toast = toasts.last
    assert toast.get_attribute("role") == "alert"
    assert toast.get_attribute("aria-live") == "assertive"
    assert toast.get_attribute("aria-atomic") == "true"
