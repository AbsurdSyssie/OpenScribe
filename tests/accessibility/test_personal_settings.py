"""Representative, synthetic-only account and personal library accessibility states."""

import pytest

from tests.accessibility.audit import (
    STATE_ACCOUNT_PERSONAL,
    STATE_ACCOUNT_EMAIL_DIALOG,
    STATE_PREFERENCES_PERSONAL,
    STATE_TEMPLATES_PERSONAL,
    STATE_TEMPLATE_IMPORT_DIALOG,
    STATE_QUICK_ACTIONS_PERSONAL,
    STATE_QUICK_ACTION_IMPORT_DIALOG,
    STATE_SMART_PHRASES_PERSONAL,
    STATE_SMART_PHRASE_VALIDATION,
)
from tests.accessibility.conftest import login, navigate, scan


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


@pytest.mark.accessibility_states(
    {
        STATE_ACCOUNT_PERSONAL: "executed",
        STATE_ACCOUNT_EMAIL_DIALOG: "executed",
        STATE_PREFERENCES_PERSONAL: "executed",
        STATE_TEMPLATES_PERSONAL: "executed",
        STATE_TEMPLATE_IMPORT_DIALOG: "executed",
        STATE_QUICK_ACTIONS_PERSONAL: "executed",
        STATE_QUICK_ACTION_IMPORT_DIALOG: "executed",
        STATE_SMART_PHRASES_PERSONAL: "executed",
        STATE_SMART_PHRASE_VALIDATION: "executed",
    }
)
def test_personal_settings_and_reusable_libraries_are_axe_checked(
    audit_report, audit_browser, synthetic_audit_user, axe_source
):
    page, blocked = audit_browser
    login(page, synthetic_audit_user, audit_report)
    results = []

    response = navigate(page, "/workspace/account", audit_report, STATE_ACCOUNT_PERSONAL)
    assert response.ok
    assert page.locator('[data-workspace-section="account"]').count() == 1
    assert page.locator('form[action="/login"]').count() == 0
    results.append(scan(page, audit_report, STATE_ACCOUNT_PERSONAL, axe_source, blocked))

    # Exercise a safe account dialog and cancel without submitting credentials or
    # changing authentication/security settings.
    email_opener = page.get_by_role("button", name="Change", exact=True).first
    email_opener.focus()
    page.keyboard.press("Enter")
    email_dialog = page.get_by_role("dialog", name="Change sign-in email")
    email_dialog.wait_for(state="visible")
    results.append(scan(page, audit_report, STATE_ACCOUNT_EMAIL_DIALOG, axe_source, blocked))
    page.keyboard.press("Escape")
    email_dialog.wait_for(state="hidden")

    response = navigate(page, "/workspace/preferences", audit_report, STATE_PREFERENCES_PERSONAL)
    assert response.ok
    assert page.locator('[data-workspace-section="preferences"]').count() == 1
    assert page.locator('form[action="/login"]').count() == 0
    results.append(scan(page, audit_report, STATE_PREFERENCES_PERSONAL, axe_source, blocked))

    response = navigate(page, "/workspace/library/templates?scope=personal", audit_report, STATE_TEMPLATES_PERSONAL)
    assert response.ok
    assert page.locator('[data-workspace-section="templates"]').count() == 1
    assert page.locator('form[action="/login"]').count() == 0
    assert page.get_by_role("group", name="Template import and export", exact=True).count() == 1
    results.append(scan(page, audit_report, STATE_TEMPLATES_PERSONAL, axe_source, blocked))
    template_import_opener = page.locator("[data-template-import-open]")
    template_import_opener.focus()
    page.keyboard.press("Enter")
    template_dialog = page.locator("[data-template-import-dialog]")
    template_dialog.wait_for(state="visible")
    dialog_result = scan(page, audit_report, STATE_TEMPLATE_IMPORT_DIALOG, axe_source, blocked)
    results.append(dialog_result)
    page.keyboard.press("Escape")
    template_dialog.wait_for(state="hidden")

    response = navigate(page, "/workspace/library/quick-actions?scope=personal", audit_report, STATE_QUICK_ACTIONS_PERSONAL)
    assert response.ok
    assert page.locator('[data-workspace-section="quick-actions"]').count() == 1
    assert page.locator('form[action="/login"]').count() == 0
    assert page.get_by_role("group", name="Quick action import and export", exact=True).count() == 1
    results.append(scan(page, audit_report, STATE_QUICK_ACTIONS_PERSONAL, axe_source, blocked))
    quick_import_opener = page.locator("[data-quick-action-import-open]")
    quick_import_opener.focus()
    page.keyboard.press("Enter")
    quick_action_dialog = page.locator("[data-quick-action-import-dialog]")
    quick_action_dialog.wait_for(state="visible")
    quick_action_result = scan(page, audit_report, STATE_QUICK_ACTION_IMPORT_DIALOG, axe_source, blocked)
    results.append(quick_action_result)
    page.keyboard.press("Escape")
    quick_action_dialog.wait_for(state="hidden")

    response = navigate(
        page,
        "/workspace/library/smart-phrases?smart_phrase_id=new",
        audit_report,
        STATE_SMART_PHRASES_PERSONAL,
    )
    assert response.ok
    assert page.locator('[data-workspace-section="smart-phrases"]').count() == 1
    assert page.locator('form[action="/login"]').count() == 0
    assert page.get_by_role("group", name="Smart phrase import and export", exact=True).count() == 1
    results.append(scan(page, audit_report, STATE_SMART_PHRASES_PERSONAL, axe_source, blocked))

    phrase_form = page.locator("[data-smart-phrase-form]")
    assert phrase_form.count() == 1
    page.get_by_role("button", name="Save phrase", exact=True).click()
    assert phrase_form.locator('input[name="trigger"]').evaluate("element => !element.validity.valid")
    validation_result = scan(page, audit_report, STATE_SMART_PHRASE_VALIDATION, axe_source, blocked)
    results.append(validation_result)
    audit_report.record_keyboard(
        "personal_settings_keyboard_cancel",
        "passed",
        "Keyboard activation opened the personal account email dialog and both library import dialogs; Escape closed each. No account credentials or library changes were submitted.",
    )
    violations = sum(len(result.get("violations", [])) for result in results)
    if violations:
        pytest.fail(
            f"axe found {violations} rule violation(s) across {len(results)} personal settings and library scans.",
            pytrace=False,
        )
