import re

import pytest

from app.models import LegalDocumentKind
from tests.test_legal_content import _publish


playwright_sync = pytest.importorskip("playwright.sync_api")

from tests.test_csrf_browser import live_server


pytestmark = pytest.mark.real_db_connections


def test_scribe_terms_decline_reopen_and_accept_on_mobile(live_server, db_session, make_team, make_user):
    admin = make_user(email="browser-terms-admin@example.com", is_system_admin=True)
    team = make_team(name="Browser Terms Clinic")
    user = make_user(
        email="browser-terms-user@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    _publish(db_session, actor=admin, kind=LegalDocumentKind.terms)

    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except playwright_sync.Error as exc:
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            page = browser.new_page(base_url=live_server, viewport={"width": 390, "height": 800})
            page.goto("/login")
            page.locator('form[action="/login"] input[name="email"]').fill(user.email)
            page.locator('form[action="/login"] input[name="password"]').fill("password-1")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url("**/workspace")

            dialog = page.get_by_role("dialog", name=re.compile("terms", re.I))
            playwright_sync.expect(dialog).to_be_visible()
            playwright_sync.expect(dialog.get_by_text("Synthetic legal notice text.")).to_be_visible()
            assert dialog.evaluate("element => element.contains(document.activeElement)")

            dialog.get_by_text(re.compile(r"^I don['’]t accept$")).click()
            page.wait_for_url("**/workspace/preferences")
            playwright_sync.expect(dialog).to_have_count(0)

            page.get_by_role("button", name="Open workspace navigation").click()
            page.get_by_role("link", name="Back to Scribe", exact=True).click()
            playwright_sync.expect(dialog).to_be_visible()
            dialog.get_by_role("button", name=re.compile("accept these terms", re.I)).click()
            playwright_sync.expect(dialog).to_have_count(0)
            playwright_sync.expect(page.locator("[data-workspace-scribe-main]")).to_have_count(1)
            page.reload()
            playwright_sync.expect(dialog).to_have_count(0)
        finally:
            browser.close()


def test_admin_can_download_evidence_and_cancel_account_deletion(live_server, make_team, make_user):
    admin = make_user(
        email="deletion-terms-admin@example.com", is_system_admin=True,
        mfa_required=False, mfa_enabled=False,
    )
    team = make_team(name="Deletion Terms Clinic")
    member = make_user(email="deletion-terms-member@example.com", full_name="Synthetic Member", team=team)

    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except playwright_sync.Error as exc:
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            page = browser.new_page(base_url=live_server)
            page.goto("/login")
            page.locator('form[action="/login"] input[name="email"]').fill(admin.email)
            page.locator('form[action="/login"] input[name="password"]').fill("password-1")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url(re.compile(r".*/admin(?:/usage\?.*)?$"))
            page.goto(f"/admin?team_id={team.id}&team_tab=members")

            page.get_by_role("button", name="Actions for Synthetic Member").click()
            delete = page.locator(f'form[action="/admin/users/{member.id}/delete"] button')
            delete.click()

            dialog = page.get_by_role("dialog", name="Delete account")
            playwright_sync.expect(dialog).to_be_visible()
            with page.expect_download() as downloaded:
                dialog.get_by_role("link", name="Download acceptance history (CSV)").click()
            assert downloaded.value.suggested_filename.endswith(".csv")
            page.keyboard.press("Escape")
            playwright_sync.expect(dialog).not_to_be_visible()
            playwright_sync.expect(page.get_by_role("button", name="Actions for Synthetic Member")).to_be_visible()
        finally:
            browser.close()
