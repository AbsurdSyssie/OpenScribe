import pytest

from tests.accessibility.conftest import login
from tests.accessibility.conftest import verify_keyboard_login
from tests.accessibility.audit import STATE_AUTH_WORKSPACE, STATE_LOGIN_KEYBOARD, STATE_SCRIBE_TABS_KEYBOARD


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


def _tab_to(page, *, role: str, name: str, limit: int = 30) -> bool:
    page.locator("body").focus()
    for _ in range(limit):
        page.keyboard.press("Tab")
        active = page.evaluate(
            """() => ({
                role: document.activeElement?.getAttribute('role') || document.activeElement?.tagName?.toLowerCase(),
                name: document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent?.trim() || '',
            })"""
        )
        if active["role"] == role and active["name"] == name:
            return True
    return False


@pytest.mark.accessibility_states({STATE_LOGIN_KEYBOARD: "executed"})
def test_keyboard_can_reach_and_submit_normal_login(audit_report, audit_browser, synthetic_audit_user):
    try:
        page, _blocked = audit_browser
        page.goto("/login", wait_until="domcontentloaded")
        assert _tab_to(page, role="input", name=""), "Email input was not reachable with Tab"
        email = page.locator('form[action="/login"] input[name="email"]')
        assert email.evaluate("element => document.activeElement === element")
        page.keyboard.press("Tab")
        password = page.locator('form[action="/login"] input[name="password"]')
        assert password.evaluate("element => document.activeElement === element"), "Password input did not follow email in Tab order"
        password.fill(synthetic_audit_user["password"])
        email.fill(synthetic_audit_user["email"])
        # The exact-origin route guard follows the redirect and fulfills its
        # final response at the original POST URL. Wait for the POST navigation
        # to commit before the canonical authenticated GET check below.
        with page.expect_navigation(wait_until="domcontentloaded", timeout=5_000):
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/login")):
                page.keyboard.press("Enter")
        verify_keyboard_login(page, audit_report)
    except Exception:
        audit_report.record_coverage(STATE_LOGIN_KEYBOARD, "failed", "keyboard login assertion failed")
        audit_report.record_keyboard("normal_login", "failed", "keyboard login assertion failed")
        raise
    audit_report.record_coverage(STATE_LOGIN_KEYBOARD, "passed", "keyboard normal login completed")
    audit_report.record_keyboard("normal_login", "passed", "Keyboard focus reached password control and Enter submitted normal sign-in.")


@pytest.mark.accessibility_states({STATE_SCRIBE_TABS_KEYBOARD: "executed"})
def test_keyboard_reaches_workspace_tabs_without_generation(audit_report, audit_browser, synthetic_audit_user):
    try:
        page, _blocked = audit_browser
        login(page, synthetic_audit_user, audit_report)
        page.goto("/transcribe", wait_until="domcontentloaded")
        assert _tab_to(page, role="tab", name="Clinical Note"), "Clinical Note tab was not reachable with Tab"
        page.keyboard.press("ArrowRight")
        follow_ups = page.get_by_role("tab", name="Follow Ups")
        assert follow_ups.evaluate("element => document.activeElement === element"), "ArrowRight did not move the roving tab focus"
        page.keyboard.press("Enter")
        assert follow_ups.get_attribute("aria-selected") == "true"
    except Exception:
        audit_report.record_coverage(STATE_SCRIBE_TABS_KEYBOARD, "failed", "keyboard tab assertion failed")
        audit_report.record_keyboard("scribe_tabs", "failed", "keyboard tab assertion failed")
        raise
    audit_report.record_coverage(STATE_SCRIBE_TABS_KEYBOARD, "passed", "keyboard tab activation completed")
    audit_report.record_keyboard("scribe_tabs", "passed", "Keyboard focus reached the Follow Ups tab and Enter activated it. No provider action was used.")


@pytest.mark.accessibility_states({STATE_LOGIN_KEYBOARD: "failed", STATE_AUTH_WORKSPACE: "unexecuted"})
def test_rejected_synthetic_login_does_not_mark_authenticated_state_passed(audit_report, audit_browser, synthetic_audit_user):
    page, _blocked = audit_browser
    rejected_user = {**synthetic_audit_user, "password": "invalid-synthetic-password"}
    with pytest.raises(pytest.fail.Exception):
        login(page, rejected_user, audit_report)
    coverage = {item["state"]: item["status"] for item in audit_report.coverage}
    assert coverage[STATE_LOGIN_KEYBOARD] == "failed"
    assert coverage[STATE_AUTH_WORKSPACE] == "unexecuted"
