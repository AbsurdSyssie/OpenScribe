"""Synthetic browser coverage for the on-screen transcription guide."""

from __future__ import annotations

import pytest

from tests.accessibility.audit import (
    STATE_GUIDE_EMPTY_DESKTOP,
    STATE_GUIDE_EMPTY_NARROW,
    STATE_GUIDE_POPULATED_DESKTOP,
    STATE_GUIDE_POPULATED_NARROW,
)
from tests.accessibility.conftest import login, scan
from tests.accessibility.test_editor import _create_synthetic_editor_content


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


GUIDE_STATES = (
    STATE_GUIDE_EMPTY_DESKTOP,
    STATE_GUIDE_EMPTY_NARROW,
    STATE_GUIDE_POPULATED_DESKTOP,
    STATE_GUIDE_POPULATED_NARROW,
)
GUIDE_STEP_COUNT = 10
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _assert_dialog_contract(dialog) -> None:
    assert dialog.evaluate("element => element.tagName.toLowerCase()") == "dialog"
    assert dialog.get_attribute("role") in {None, "dialog"}, "Native dialog must retain its dialog role"
    assert dialog.evaluate("element => element.matches(':modal')"), "Guide is not a native modal dialog"
    labelledby = dialog.get_attribute("aria-labelledby")
    describedby = dialog.get_attribute("aria-describedby")
    assert labelledby and dialog.locator(f"#{labelledby}").count() == 1
    assert describedby and dialog.locator(f"#{describedby}").count() == 1


def _assert_step(dialog, expected: int) -> None:
    assert dialog.is_visible(), "Guide closed before the current step could be reviewed"
    assert dialog.get_attribute("data-tour-step") == str(expected)
    progress = dialog.locator("[data-tour-progress]")
    assert progress.inner_text().strip() == f"Step {expected} of {GUIDE_STEP_COUNT}"
    announcement = dialog.locator("[data-tour-announcement]")
    assert announcement.get_attribute("role") == "status"
    assert announcement.get_attribute("aria-live") == "polite"
    assert announcement.get_attribute("aria-atomic") == "true"
    assert announcement.inner_text().strip().startswith(f"Step {expected} of {GUIDE_STEP_COUNT}:")
    assert dialog.get_attribute("data-tour-target-state") in {"matched", "fallback"}


def _press_button(dialog, selector: str) -> None:
    button = dialog.locator(selector)
    assert button.is_visible() and button.is_enabled()
    button.focus()
    button.press("Enter")


def _scan_without_violations(page, report, state, axe_source, blocked) -> int:
    result = scan(page, report, state, axe_source, blocked)
    return sum(len(rule.get("nodes", [])) for rule in result.get("violations", []))


def _assert_guide_reflows(page, dialog) -> None:
    assert dialog.evaluate(
        """element => {
          const card = element.querySelector('[data-tour-card]');
          if (!card) return false;
          const bounds = card.getBoundingClientRect();
          return bounds.left >= 0 && bounds.right <= window.innerWidth + 1
            && card.scrollWidth <= card.clientWidth + 1;
        }"""
    ), "Guide card overflows horizontally at the current CSS viewport width"


@pytest.mark.accessibility_states({state: "executed" for state in GUIDE_STATES})
def test_transcription_guide_keyboard_reflow_and_safe_states(
    db_session,
    audit_report,
    audit_browser,
    synthetic_audit_user,
    axe_source,
):
    page, blocked = audit_browser
    audit_report.record_keyboard(
        "browser_runtime",
        "passed",
        f"Playwright Chromium browser version: {page.context.browser.version}",
    )
    mutations: list[str] = []
    guide_is_open = False

    def observe_request(request) -> None:
        if guide_is_open and request.method.upper() in MUTATING_METHODS:
            mutations.append(request.method.upper())

    page.on("request", observe_request)
    page.set_viewport_size({"width": 1280, "height": 900})
    login(page, synthetic_audit_user, audit_report)
    response = page.goto("/transcribe", wait_until="domcontentloaded")
    assert response is not None and response.ok
    page.locator("[data-workspace-scribe-main]").wait_for(state="visible")
    assert page.locator('form[action="/login"]').count() == 0

    # Start with a presentational follow-up tab selected. Guide target steps may
    # switch tabs to expose anchors; dismissal must put this tab back.
    followups = page.locator('[data-tab-trigger="followups"]')
    if followups.count():
        followups.click()
        assert followups.get_attribute("aria-selected") == "true"
    opener = page.locator("[data-start-guide]")
    assert opener.is_visible() and opener.is_enabled()
    original_tab = page.locator('[data-tab-trigger][aria-selected="true"]').get_attribute("data-tab-trigger")
    dialog = page.locator("[data-tour-overlay]")

    # The guide remains manually available when local storage methods throw.
    page.evaluate("""() => {
      Object.defineProperty(Storage.prototype, 'getItem', { configurable: true, value() { throw new Error('storage unavailable'); } });
      Object.defineProperty(Storage.prototype, 'setItem', { configurable: true, value() { throw new Error('storage unavailable'); } });
    }""")
    opener.focus()
    opener.press("Enter")
    dialog.wait_for(state="visible")
    guide_is_open = True
    _assert_dialog_contract(dialog)
    assert dialog.locator("[data-tour-title]").inner_text().strip()
    assert dialog.locator("[data-tour-body]").inner_text().strip()
    _assert_step(dialog, 1)

    violations = _scan_without_violations(
        page, audit_report, STATE_GUIDE_EMPTY_DESKTOP, axe_source, blocked
    )
    assert page.locator("[data-tour-overlay]").evaluate(
        "dialog => dialog.contains(document.activeElement)"
    ), "Keyboard focus did not enter the guide dialog"
    for _ in range(10):
        page.keyboard.press("Tab")
        focus_state = dialog.evaluate("""element => ({
          inside: element.contains(document.activeElement),
          tag: document.activeElement?.tagName?.toLowerCase() || '',
          id: document.activeElement?.id || '',
          role: document.activeElement?.getAttribute('role') || '',
          modal: element.matches(':modal')
        })""")
        assert focus_state["inside"], f"Tab escaped the modal guide: {focus_state}"
    for _ in range(10):
        page.keyboard.press("Shift+Tab")
        focus_state = dialog.evaluate("""element => ({
          inside: element.contains(document.activeElement),
          tag: document.activeElement?.tagName?.toLowerCase() || '',
          id: document.activeElement?.id || '',
          role: document.activeElement?.getAttribute('role') || '',
          modal: element.matches(':modal')
        })""")
        assert focus_state["inside"], f"Shift+Tab escaped the modal guide: {focus_state}"

    _press_button(dialog, "[data-tour-next]")
    _assert_step(dialog, 2)
    assert dialog.locator("[data-tour-title]").evaluate(
        "element => document.activeElement === element"
    ), "Next did not move keyboard focus to the new step heading"
    page.keyboard.press("Shift+Tab")
    assert dialog.evaluate("element => element.contains(document.activeElement)"), "Shift+Tab escaped after the guide focused a non-tabbable step heading"
    _press_button(dialog, "[data-tour-back]")
    _assert_step(dialog, 1)
    assert dialog.locator("[data-tour-title]").evaluate(
        "element => document.activeElement === element"
    ), "Back did not move keyboard focus to the restored step heading"

    # Escape closes and restores both the opener focus and the original tab.
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    guide_is_open = False
    assert opener.evaluate("element => document.activeElement === element"), "Escape did not restore focus to the guide opener"
    if original_tab:
        assert page.locator(f'[data-tab-trigger="{original_tab}"]').get_attribute("aria-selected") == "true"

    # On the empty workspace, optional targets must use the visible fallback and
    # remain navigable instead of terminating the guide early.
    opener.focus()
    opener.press("Enter")
    dialog.wait_for(state="visible")
    guide_is_open = True
    _assert_step(dialog, 1)
    fallback_steps = 0
    for step in range(1, GUIDE_STEP_COUNT):
        if dialog.get_attribute("data-tour-target-state") == "fallback":
            fallback_steps += 1
        _press_button(dialog, "[data-tour-next]")
        _assert_step(dialog, step + 1)
    if dialog.get_attribute("data-tour-target-state") == "fallback":
        fallback_steps += 1
    assert fallback_steps > 0, "Empty workspace did not exercise missing-target fallback behavior"
    assert dialog.locator("[data-tour-next]").inner_text().strip() == "Finish"
    _press_button(dialog, "[data-tour-next]")
    dialog.wait_for(state="hidden")
    guide_is_open = False
    assert opener.evaluate("element => document.activeElement === element"), "Finish did not restore focus to the guide opener"
    if original_tab:
        assert page.locator(f'[data-tab-trigger="{original_tab}"]').get_attribute("aria-selected") == "true"

    # Probe the documented modal-blocking guard with a temporary native modal.
    # This does not simulate or start audio capture.
    page.evaluate("""() => {
      const blocker = document.createElement('dialog');
      blocker.id = 'synthetic-accessibility-blocking-dialog';
      document.body.append(blocker);
      blocker.showModal();
    }""")
    opener.evaluate("element => element.click()")
    assert not dialog.evaluate("element => element.open"), "Guide opened over another active modal"
    page.locator("[data-toast-container] .toast").last.wait_for(state="visible")
    audit_report.record_keyboard(
        "guide_blocked_by_other_modal",
        "passed",
        "An existing native modal prevented guide opening; no capture was started.",
    )
    page.evaluate("""() => {
      const blocker = document.querySelector('#synthetic-accessibility-blocking-dialog');
      blocker?.close();
      blocker?.remove();
    }""")

    # Narrow viewport probe checks dialog reflow and continued step operation.
    page.set_viewport_size({"width": 320, "height": 720})
    drawer_toggle = page.locator("[data-workspace-drawer-toggle]")
    assert drawer_toggle.is_visible()
    drawer_toggle.click()
    assert drawer_toggle.get_attribute("aria-expanded") == "true"
    opener = page.locator("[data-primary-sidebar] [data-start-guide]")
    assert opener.is_visible() and opener.evaluate("element => !element.closest('[inert]')")
    opener.press("Enter")
    dialog.wait_for(state="visible")
    guide_is_open = True
    _assert_step(dialog, 1)
    violations += _scan_without_violations(
        page, audit_report, STATE_GUIDE_EMPTY_NARROW, axe_source, blocked
    )
    _assert_guide_reflows(page, dialog)
    _press_button(dialog, "[data-tour-next]")
    _assert_step(dialog, 2)
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    guide_is_open = False
    assert opener.is_visible() and opener.evaluate("element => !element.closest('[inert]')"), "Escape hid or inerted the guide opener in the mobile drawer"
    assert drawer_toggle.get_attribute("aria-expanded") == "true", "Guide Escape also closed the navigation drawer"
    assert opener.evaluate("element => document.activeElement === element"), "Escape did not restore focus into the still-open mobile drawer"

    # A separate encrypted synthetic note exercises optional anchors that only
    # exist when a consultation and generated draft are present.
    transcript_id = _create_synthetic_editor_content(
        db_session,
        email=synthetic_audit_user["email"],
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"/workspace?transcript_id={transcript_id}&tab=output", wait_until="domcontentloaded")
    assert response is not None and response.ok
    page.locator("[data-workspace-scribe-main]").wait_for(state="visible")
    assert page.locator('form[action="/login"]').count() == 0
    assert page.locator("[data-freeform-note-input]").count() == 37
    note_before = page.locator("[data-freeform-note-input]").evaluate_all(
        "elements => elements.map(element => element.value)"
    )
    opener = page.locator("[data-start-guide]")
    original_tab = page.locator('[data-tab-trigger][aria-selected="true"]').get_attribute("data-tab-trigger")
    opener.focus()
    opener.press("Enter")
    dialog.wait_for(state="visible")
    guide_is_open = True
    _assert_dialog_contract(dialog)
    _assert_step(dialog, 1)
    violations += _scan_without_violations(
        page, audit_report, STATE_GUIDE_POPULATED_DESKTOP, axe_source, blocked
    )
    for step in range(1, GUIDE_STEP_COUNT):
        _assert_step(dialog, step)
        _press_button(dialog, "[data-tour-next]")
        _assert_step(dialog, step + 1)
    assert dialog.locator("[data-tour-next]").inner_text().strip() == "Finish"
    _press_button(dialog, "[data-tour-next]")
    dialog.wait_for(state="hidden")
    guide_is_open = False
    assert opener.evaluate("element => document.activeElement === element"), "Finish did not restore focus to the guide opener"
    if original_tab:
        assert page.locator(f'[data-tab-trigger="{original_tab}"]').get_attribute("aria-selected") == "true"
    assert page.locator("[data-freeform-note-input]").evaluate_all(
        "elements => elements.map(element => element.value)"
    ) == note_before, "Guide changed the synthetic note source"

    page.set_viewport_size({"width": 320, "height": 720})
    drawer_toggle = page.locator("[data-workspace-drawer-toggle]")
    drawer_toggle.click()
    assert drawer_toggle.get_attribute("aria-expanded") == "true"
    opener = page.locator("[data-primary-sidebar] [data-start-guide]")
    assert opener.is_visible() and opener.evaluate("element => !element.closest('[inert]')")
    opener.press("Enter")
    dialog.wait_for(state="visible")
    guide_is_open = True
    _assert_step(dialog, 1)
    violations += _scan_without_violations(
        page, audit_report, STATE_GUIDE_POPULATED_NARROW, axe_source, blocked
    )
    _assert_guide_reflows(page, dialog)
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    guide_is_open = False
    assert opener.is_visible() and opener.evaluate("element => !element.closest('[inert]')")
    assert drawer_toggle.get_attribute("aria-expanded") == "true"
    assert opener.evaluate("element => document.activeElement === element")

    assert not mutations, "Guide caused a source, consultation, provider, or other mutation request"
    assert not violations, f"axe found {violations} violation node(s) across the required guide states"
