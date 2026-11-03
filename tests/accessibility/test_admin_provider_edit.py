from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import pytest

from app.models import (
    LlmAdapterKind,
    SttAdapterKind,
    SttProviderPreset,
    SttSelectionPurpose,
    TeamHallucinationCheckSelection,
    TeamLlmBackupSelection,
    TeamLlmSelection,
    TeamSttSelection,
)


pytestmark = [pytest.mark.real_db_connections, pytest.mark.accessibility_audit]


@pytest.fixture
def provider_edit_page(live_audit_server):
    playwright_module = pytest.importorskip("playwright.sync_api")
    playwright = playwright_module.sync_playwright().start()
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:  # pragma: no cover - depends on local browser installation
        playwright.stop()
        pytest.skip(f"Playwright Chromium is unavailable: {exc}")
    context = browser.new_context(base_url=live_audit_server, bypass_csp=True, service_workers="block")
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()
        browser.close()
        playwright.stop()


def _login_and_open_admin(page, *, base_url: str, email: str, password: str, team_id: str, tab: str) -> None:
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.locator('input[name="email"]').fill(email)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"]').click()
    page.wait_for_url(re.compile(r".*/admin(?:/.*|\?.*)?$"))
    page.goto(f"/admin?team_id={team_id}&team_tab={tab}", wait_until="domcontentloaded")


def _fence_provider_requests(page):
    """Allow selection APIs but abort provider config/inspection traffic."""
    seen: list[tuple[str, str]] = []
    allowed = {
        ("GET", "/api/v1/llm-selection"),
        ("GET", "/api/v1/stt-selection"),
        ("POST", "/api/v1/stt-selection"),
    }

    def guard(route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.path.startswith("/api/v1/"):
            seen.append((request.method, parsed.path))
            if (request.method, parsed.path) not in allowed and not (parsed.path.startswith("/api/v1/llm-configs/") and parsed.path.endswith("/model-policy")):
                route.abort()
                return
        route.continue_()

    page.context.route("**/api/v1/**", guard)
    return seen


def _provider_button(page, kind: str, config_id):
    return page.locator(f'[data-edit-{kind}-provider][data-config-id="{config_id}"]')


def _model_policy_detail(config, team_id: str) -> dict[str, object]:
    policy = config.model_policy
    return {
        "team_id": team_id,
        "llm_config_id": str(config.id),
        "provider_available_models_json": list(config.available_models_json or []),
        "enabled_models_json": list(policy.enabled_models_json or []),
        "default_model_name": policy.default_model_name,
        "policy_valid": True,
        "invalid_enabled_models_json": [],
        "default_model_valid": True,
        "role_warnings": [],
    }


def test_provider_row_actions_menu_is_accessible_and_keeps_existing_actions(
    provider_edit_page, live_audit_server, db_session, make_team, make_user,
    make_stt_config, make_llm_config,
):
    team = make_team(name="Provider row actions")
    admin = make_user(email="provider-row-actions@example.test", password="synthetic-provider-actions-password",
                      is_system_admin=True, mfa_required=False, mfa_enabled=False)
    stt = make_stt_config(team=team, actor=admin, label="Synthetic STT row", base_url="https://stt.example.test")
    llm = make_llm_config(team=team, actor=admin, label="Synthetic LLM row", base_url="https://llm.example.test",
                          available_models_json=["synthetic-model"])
    db_session.commit()

    page = provider_edit_page
    blocked = []
    def block_api(route):
        if urlparse(route.request.url).path.startswith("/api/v1/"):
            blocked.append(route.request.url)
            route.abort()
        else:
            route.continue_()
    page.context.route("**/api/v1/**", block_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email,
                          password="synthetic-provider-actions-password", team_id=str(team.id), tab="stt")
    blocked.clear()

    stt_edit = _provider_button(page, "stt", stt.id)
    stt_table = page.locator(".stt-provider-table")
    assert stt_table.locator(":scope > .stt-provider-row:not(.head)").count() == 1
    assert stt_table.locator(":scope > .stt-provider-row:not(.head)").first.locator(":scope > *").count() == 5
    assert stt_edit.get_attribute("aria-label") == "Edit Synthetic STT row"
    assert stt_edit.locator("svg").count() == 1
    stt_menu_button = page.get_by_role("button", name="More actions for Synthetic STT row")
    stt_menu = page.locator(f"#stt-actions-{stt.id}")
    assert stt_menu.is_hidden()
    stt_menu_button.click()
    assert stt_menu.is_visible()
    assert stt_menu.get_by_role("button", name="Test connection").is_visible()
    assert stt_menu.get_by_role("button", name="Refresh provider details").is_visible()
    assert stt_menu.locator("form").nth(0).get_attribute("action") == f"/admin/stt-configs/{stt.id}/test"
    assert stt_menu.locator('input[name="team_id"]').first.input_value() == str(team.id)
    assert stt_menu.locator('input[name="return_view"]').first.input_value() == "workspace"
    assert stt_menu.locator('input[name="return_tab"]').first.input_value() == "stt"
    assert stt_menu.get_by_role("button", name="Test connection").evaluate("el => document.activeElement === el")
    page.keyboard.press("Escape")
    assert stt_menu.is_hidden()
    assert stt_menu_button.get_attribute("aria-expanded") == "false"
    assert stt_menu_button.evaluate("el => document.activeElement === el")

    llm_response = page.goto(f"/admin?team_id={team.id}&team_tab=llm", wait_until="domcontentloaded")
    assert llm_response is not None and llm_response.status == 200
    blocked.clear()
    llm_table = page.locator(".llm-provider-table")
    assert llm_table.locator(":scope > .llm-provider-row:not(.head)").count() == 1
    assert llm_table.locator(":scope > .llm-provider-row:not(.head)").first.locator(":scope > *").count() == 6
    llm_menu_button = page.get_by_role("button", name="More actions for Synthetic LLM row")
    llm_menu = page.locator(f"#llm-actions-{llm.id}")
    llm_menu_button.click()
    assert llm_menu.is_visible()
    assert llm_menu.get_by_role("button", name="Refresh models").is_visible()
    assert llm_menu.locator("form").nth(0).get_attribute("action") == f"/admin/llm-configs/{llm.id}/inspect"
    assert llm_menu.locator('input[name="return_tab"]').first.input_value() == "llm"
    assert llm_menu.locator("[data-confirm-submit]").count() == 1
    assert "Delete this LLM provider" in llm_menu.locator("[data-confirm-submit]").get_attribute("data-confirm-submit")
    delete_requests = []
    page.on("request", lambda request: delete_requests.append(request.url) if request.method == "POST" and f"/admin/llm-configs/{llm.id}/delete" in request.url else None)
    page.evaluate("window.confirm = () => false")
    llm_menu.get_by_role("button", name="Delete").click()
    page.wait_for_function("el => document.activeElement === el", arg=llm_menu_button.element_handle())
    assert llm_menu_button.evaluate("el => document.activeElement === el")
    assert not delete_requests, "Cancelling provider deletion submitted the form"

    # A second provider menu replaces the current one; menus remain viewport-clamped.
    second = make_llm_config(team=team, actor=admin, label="Synthetic LLM second", base_url="https://llm-two.example.test",
                             available_models_json=["synthetic-model"])
    db_session.commit()
    page.reload(wait_until="domcontentloaded")
    blocked.clear()
    llm_table = page.locator(".llm-provider-table")
    assert llm_table.locator(":scope > .llm-provider-row:not(.head)").count() == 2
    assert llm_table.locator(":scope > .llm-provider-row:not(.head)").nth(1).locator(":scope > *").count() == 6
    first_button = page.get_by_role("button", name="More actions for Synthetic LLM row")
    second_button = page.get_by_role("button", name="More actions for Synthetic LLM second")
    first_button.click()
    second_button.focus()
    page.keyboard.press("Enter")
    assert page.locator(f"#llm-actions-{llm.id}").is_hidden()
    second_menu = page.locator(f"#llm-actions-{second.id}")
    assert second_menu.is_visible()
    bounds = second_menu.evaluate("el => { const r = el.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom]; }")
    assert bounds[0] >= 0 and bounds[1] <= page.evaluate("innerWidth")
    assert bounds[2] >= 0 and bounds[3] <= page.evaluate("innerHeight")
    page.mouse.click(8, 8)
    assert second_menu.is_hidden()
    assert not blocked, f"Opening provider action menus made API requests: {blocked}"

    page.set_viewport_size({"width": 1440, "height": 900})
    first_button.click()
    page.screenshot(path="/tmp/openscribe-provider-row-actions-llm-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    mobile_button = page.get_by_role("button", name="More actions for Synthetic LLM second")
    mobile_button.scroll_into_view_if_needed()
    mobile_button.click()
    page.screenshot(path="/tmp/openscribe-provider-row-actions-llm-mobile.png")
    page.goto(f"/admin?team_id={team.id}&team_tab=stt", wait_until="domcontentloaded")
    page.set_viewport_size({"width": 1440, "height": 900})
    stt_desktop_button = page.get_by_role("button", name="More actions for Synthetic STT row")
    stt_desktop_button.click()
    page.screenshot(path="/tmp/openscribe-provider-row-actions-stt-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    stt_mobile_button = page.get_by_role("button", name="More actions for Synthetic STT row")
    stt_mobile_button.scroll_into_view_if_needed()
    stt_mobile_button.click()
    page.screenshot(path="/tmp/openscribe-provider-row-actions-stt-mobile.png")

    page.get_by_role("button", name="Edit Synthetic STT row").click()
    assert page.locator("#provider-details-modal").is_visible()


def test_saved_llm_model_policy_is_editable_without_connection_check_and_keeps_cross_tab_changes(
    provider_edit_page,
    live_audit_server,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="LLM model editor accessibility")
    admin = make_user(
        email="llm-model-editor@example.test",
        password="synthetic-editor-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    models = ["synthetic-long-model-id-for-responsive-policy-layout-1", "synthetic-long-model-id-for-responsive-policy-layout-2"]
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Synthetic selected writer",
        adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai",
        model_name=models[1],
        available_models_json=models,
        base_url="https://api.openai.com/v1",
        has_secret=True,
    )
    duplicate = make_llm_config(
        team=team,
        actor=admin,
        label="Existing synthetic provider name",
        adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai",
        model_name="synthetic-other-model",
        available_models_json=["synthetic-other-model"],
        base_url="https://other.synthetic.example/v1",
        has_secret=False,
    )
    target = make_llm_config(
        team=team,
        actor=admin,
        label="Synthetic inactive provider",
        adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai",
        model_name=models[1],
        available_models_json=models,
        base_url="https://inactive.synthetic.example/v1",
        has_secret=True,
        is_active=False,
    )
    config.inspection_metadata_json = {
        "connection_check": {
            "status": "failed",
            "checked_at": "2026-11-01T12:00:00+00:00",
            "error_code": "authentication_error",
            "http_status": 401,
        }
    }
    selection = make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=models,
        model_name_override=models[1],
    )
    db_session.add(TeamLlmBackupSelection(team_id=team.id, llm_config_id=target.id, model_name=models[1]))
    db_session.add(TeamHallucinationCheckSelection(
        team_id=team.id,
        llm_config_id=config.id,
        model_name_override="synthetic-checker-model-no-longer-listed",
        selected_by_user_id=admin.id,
    ))
    db_session.commit()

    page = provider_edit_page
    api_calls = _fence_provider_requests(page)
    _login_and_open_admin(
        page,
        base_url=live_audit_server,
        email=admin.email,
        password="synthetic-editor-password",
        team_id=str(team.id),
        tab="llm",
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"/admin?team_id={team.id}&team_tab=provider-policy", wait_until="domcontentloaded")
    policy_table_text = page.locator("[data-provider-policy-table]").inner_text()
    assert "Backup: provider unavailable" in policy_table_text
    assert "Checker: assigned model unavailable" in policy_table_text
    page.screenshot(path="/tmp/openscribe-provider-policy-role-warnings-desktop.png", full_page=True)
    page.goto(f"/admin?team_id={team.id}&team_tab=llm", wait_until="domcontentloaded")
    page.screenshot(path="/tmp/openscribe-llm-provider-list-health-desktop.png", full_page=True)
    status_stack = page.locator(".llm-provider-row:not(.head) [data-llm-availability]").first
    assert status_stack.locator(":scope > .configured").count() == 1
    assert status_stack.locator(":scope > .provider-check-status").count() == 1
    assert status_stack.locator(":scope > .provider-role-warning").count() == 1
    opener = _provider_button(page, "llm", target.id)
    opener.click()

    dialog = page.locator("#provider-details-modal")
    assert dialog.is_visible()
    models_tab = page.locator("#provider-details-tab-models")
    connection_tab = page.locator("#provider-details-tab-connection")
    assert models_tab.get_attribute("aria-selected") == "true"
    assert page.locator("#provider-details-panel-models").is_visible()
    assert page.locator("#provider-details-panel-connection").is_hidden()
    page.wait_for_function(
        "([selector, value]) => document.querySelector(selector)?.value === value",
        arg=["#provider-details-llm-default", models[1]],
    )
    assert page.locator("#provider-details-llm-default").input_value() == models[1]
    checkboxes = page.locator('#provider-details-llm-allowed input[type="checkbox"]')
    assert [checkbox.input_value() for checkbox in checkboxes.all()] == models
    assert all(checkbox.is_checked() for checkbox in checkboxes.all())
    assert page.locator("#provider-details-save-models").is_visible()
    assert not any(path.endswith(("/inspect", "/drafts")) for _, path in api_calls)
    dialog.screenshot(path="/tmp/openscribe-provider-model-editor-desktop.png")

    # A short mobile viewport keeps the dialog and footer reachable without
    # horizontal page overflow. Scroll only if the rendered content needs it.
    page.set_viewport_size({"width": 390, "height": 600})
    modal_panel = page.locator("#provider-details-modal > section")
    mobile_bounds = modal_panel.evaluate(
        """element => {
          const rect = element.getBoundingClientRect();
          return {
            left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
            scrollHeight: element.scrollHeight, clientHeight: element.clientHeight
          };
        }"""
    )
    assert mobile_bounds["left"] >= 0 and mobile_bounds["right"] <= 390, mobile_bounds
    assert mobile_bounds["scrollWidth"] <= mobile_bounds["clientWidth"] == 390, mobile_bounds
    modal_footer = modal_panel.locator("footer")
    modal_footer.scroll_into_view_if_needed()
    assert modal_footer.is_visible()
    footer_bounds = modal_footer.bounding_box()
    assert footer_bounds is not None and footer_bounds["y"] + footer_bounds["height"] <= 600, (mobile_bounds, footer_bounds)
    page.locator("#provider-details-llm-allowed tr[data-model-id]").first.scroll_into_view_if_needed()
    dialog.screenshot(path="/tmp/openscribe-provider-model-editor-mobile.png")

    # Editing the connection tab only changes metadata; the saved credential is
    # described without exposing its Vault reference or secret value.
    page.set_viewport_size({"width": 1280, "height": 900})
    models_tab.press("ArrowRight")
    assert connection_tab.get_attribute("aria-selected") == "true"
    assert page.locator("#provider-details-panel-connection").is_visible()
    dialog.screenshot(path="/tmp/openscribe-provider-connection-editor-desktop.png")
    page.set_viewport_size({"width": 390, "height": 600})
    dialog.screenshot(path="/tmp/openscribe-provider-connection-editor-mobile.png")
    page.set_viewport_size({"width": 1280, "height": 900})
    assert page.locator("#provider-details-label").input_value() == target.label
    assert page.locator("#provider-details-base-url").input_value() == target.base_url
    connection_text = page.locator("#provider-details-panel-connection").inner_text()
    assert "Key saved — paste to replace" in connection_text
    assert "secret:openscribe" not in dialog.inner_text()
    page.locator("#provider-details-label").fill("Synthetic renamed writer")

    # Saving this inactive provider's empty policy does not switch the writing
    # pointer; an empty policy explicitly means no models are enabled.
    connection_tab.press("ArrowLeft")
    assert models_tab.get_attribute("aria-selected") == "true"
    for checkbox in checkboxes.all():
        checkbox.uncheck()
    with page.expect_response(
        lambda response: response.request.method == "PUT" and urlparse(response.url).path == f"/api/v1/llm-configs/{target.id}/model-policy"
    ) as save_response:
        page.locator("#provider-details-save-models").click()
    assert save_response.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Models saved')")
    payload = json.loads(save_response.value.request.post_data or "{}")
    assert payload == {"team_id": str(team.id), "enabled_models_json": [], "default_model_name": None}
    status = page.locator("#provider-details-status")
    assert status.is_visible()
    assert status.inner_text() == "Models saved. Changes apply to new generations."
    db_session.expire_all()
    saved_selection = db_session.get(TeamLlmSelection, selection.id)
    assert saved_selection is not None
    assert saved_selection.llm_config_id == config.id
    assert target.model_policy.enabled_models_json == []
    assert target.model_policy.default_model_name is None
    page.set_viewport_size({"width": 1440, "height": 900})
    dialog.screenshot(path="/tmp/openscribe-provider-model-policy-inactive-saved-desktop.png")
    page.set_viewport_size({"width": 390, "height": 600})
    page.locator("#provider-details-llm-allowed tr[data-model-id]").first.scroll_into_view_if_needed()
    dialog.screenshot(path="/tmp/openscribe-provider-model-policy-empty-long-id-mobile.png")

    # Saving Models must not mark the Connection tab clean. Closing keeps the
    # unsaved rename behind the established Stay/Discard guard.
    page.locator("#provider-details-cancel").click()
    unsaved = page.locator("#admin-unsaved-modal")
    assert unsaved.is_visible()
    unsaved.locator("[data-unsaved-stay]").click()
    assert dialog.is_visible()
    assert page.locator("#provider-details-label").input_value() == "Synthetic renamed writer"

    # A server validation error remains attached to its field and can be
    # corrected in place; a later metadata-only save does not inspect a
    # provider or create a revision.
    connection_tab.click()
    label = page.locator("#provider-details-label")
    label.fill(duplicate.label)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == f"/admin/llm-configs/{target.id}/details"
    ) as duplicate_response:
        page.locator("#provider-details-save-connection").click()
    assert duplicate_response.value.status == 409, duplicate_response.value.text()
    assert "application/json" in duplicate_response.value.request.headers.get("accept", "")
    error = page.locator("#provider-details-error")
    error.wait_for(state="visible")
    assert error.is_visible()
    assert label.input_value() == duplicate.label
    assert label.get_attribute("aria-invalid") == "true"
    assert label.get_attribute("aria-describedby") == "provider-details-error"
    assert label.evaluate("element => document.activeElement === element")
    label.fill("Synthetic corrected writer name")
    assert error.is_hidden()
    assert label.get_attribute("aria-invalid") is None
    assert label.get_attribute("aria-describedby") is None
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == f"/admin/llm-configs/{target.id}/details"
    ) as metadata_response:
        page.locator("#provider-details-save-connection").click()
    assert metadata_response.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent === 'Provider settings saved.'")
    assert page.locator("#provider-details-status").inner_text() == "Provider settings saved."
    assert page.locator("#provider-details-label").input_value() == "Synthetic corrected writer name"
    assert not any(path.endswith(("/inspect", "/drafts")) for _, path in api_calls)

    # A session redirect can return an HTML login page to fetch. It must not
    # leak a JSON parser/TypeError into the editor or clear the entered value.
    page.route(
        f"**/admin/llm-configs/{target.id}/details",
        lambda route: route.fulfill(status=200, content_type="text/html", body="<html><title>Sign in</title></html>"),
    )
    label.fill("Synthetic pending session writer")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == f"/admin/llm-configs/{target.id}/details"
    ):
        page.locator("#provider-details-save-connection").click()
    error.wait_for(state="visible")
    assert error.is_visible()
    assert "unexpected token" not in error.inner_text().lower()
    assert "cannot read properties" not in error.inner_text().lower()
    assert label.input_value() == "Synthetic pending session writer"


def test_stt_saved_model_and_language_choices_remain_per_purpose(
    provider_edit_page,
    live_audit_server,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="STT model editor accessibility")
    admin = make_user(
        email="stt-model-editor@example.test",
        password="synthetic-editor-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    models = ["synthetic-stt-1", "synthetic-stt-2"]
    config = make_stt_config(
        team=team,
        actor=admin,
        label="Synthetic speech provider",
        adapter_kind=SttAdapterKind.openai_cloud,
        base_url="https://api.openai.com/v1",
        model_name=models[1],
        available_models_json=models,
        has_secret=True,
    )
    config.provider_preset = SttProviderPreset.openai.value
    config.transcribe_path = "/audio/transcriptions"
    db_session.commit()
    conversation = make_stt_selection(
        config=config,
        actor=admin,
        purpose=SttSelectionPurpose.conversation,
        model_name_override=models[1],
        language_override="en-GB",
    )
    dictation = make_stt_selection(
        config=config,
        actor=admin,
        purpose=SttSelectionPurpose.post_consultation_dictation,
        model_name_override=models[0],
        language_override="fr-FR",
    )

    page = provider_edit_page
    api_calls = _fence_provider_requests(page)
    _login_and_open_admin(
        page,
        base_url=live_audit_server,
        email=admin.email,
        password="synthetic-editor-password",
        team_id=str(team.id),
        tab="stt",
    )
    _provider_button(page, "stt", config.id).click()

    dialog = page.locator("#provider-details-modal")
    assert dialog.is_visible()
    conversation_model = page.get_by_label("Consultation transcription model")
    conversation_language = page.get_by_label("Consultation transcription language")
    dictation_model = page.get_by_label("Post-consultation dictation model")
    dictation_language = page.get_by_label("Post-consultation dictation language")
    assert conversation_model.input_value() == models[1]
    assert conversation_language.input_value() == "en-GB"
    assert dictation_model.input_value() == models[0]
    assert dictation_language.input_value() == "fr-FR"

    # The long dictation action should fit the fixed-width desktop editor with
    # the two selection fields, without pushing outside the dialog.
    page.set_viewport_size({"width": 760, "height": 900})
    modal_panel = page.locator("#provider-details-modal > section")
    modal_bounds = modal_panel.bounding_box()
    assert modal_bounds is not None and modal_bounds["width"] <= 720, modal_bounds
    dictation_row = page.locator("#provider-details-stt-selections .provider-details-stt-row").nth(1)
    row_bounds = dictation_row.bounding_box()
    row_children = [child.bounding_box() for child in dictation_row.locator(":scope > *").all()]
    assert row_bounds is not None and all(child is not None for child in row_children)
    assert all(child["x"] >= row_bounds["x"] and child["x"] + child["width"] <= row_bounds["x"] + row_bounds["width"] for child in row_children)
    assert row_children[-1]["x"] + row_children[-1]["width"] <= modal_bounds["x"] + modal_bounds["width"]
    page.screenshot(path="/tmp/openscribe-provider-stt-editor-620px.png", full_page=True)

    # Edit both purposes before saving either one. Saving Conversation must
    # retain the unsaved Dictation draft.
    conversation_model.select_option(models[0])
    conversation_language.fill("en-US")
    dictation_model.select_option(models[1])
    dictation_language.fill("de-DE")
    with page.expect_response(
        lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/stt-selection"
    ) as conversation_response:
        page.get_by_role("button", name="Save consultation transcription").click()
    assert conversation_response.value.ok
    conversation_payload = json.loads(conversation_response.value.request.post_data or "{}")
    assert conversation_payload == {
        "team_id": str(team.id),
        "purpose": "conversation",
        "stt_config_id": str(config.id),
        "model_name_override": models[0],
        "language_override": "en-US",
    }
    assert dictation_model.input_value() == models[1]
    assert dictation_language.input_value() == "de-DE"
    with page.expect_response(
        lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/stt-selection"
    ) as dictation_response:
        page.get_by_role("button", name="Save post-consultation dictation").click()
    assert dictation_response.value.ok
    dictation_payload = json.loads(dictation_response.value.request.post_data or "{}")
    assert dictation_payload == {
        "team_id": str(team.id),
        "purpose": "post_consultation_dictation",
        "stt_config_id": str(config.id),
        "model_name_override": models[1],
        "language_override": "de-DE",
    }

    db_session.expire_all()
    saved_conversation = db_session.get(TeamSttSelection, conversation.id)
    saved_dictation = db_session.get(TeamSttSelection, dictation.id)
    assert saved_conversation.model_name_override == models[0]
    assert saved_conversation.language_override == "en-US"
    assert saved_dictation.model_name_override == models[1]
    assert saved_dictation.language_override == "de-DE"
    assert not any(path.startswith("/api/v1/stt-configs/") for _, path in api_calls)


def test_saved_policy_read_failure_stays_locked_after_close_and_reopen(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="LLM stale policy recovery")
    admin = make_user(
        email="llm-stale-policy@example.test",
        password="synthetic-editor-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    model = "synthetic-existing-model"
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Synthetic stale policy writer",
        adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai",
        model_name=model,
        available_models_json=[model],
        base_url="https://api.openai.com/v1",
        has_secret=True,
    )
    make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=[model],
        model_name_override=model,
    )

    page = provider_edit_page
    calls = {"policy_get": 0, "inspection": 0}

    def synthetic_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        if parsed.path == f"/api/v1/llm-configs/{config.id}/model-policy" and request.method == "GET":
            calls["policy_get"] += 1
            route.fulfill(status=503, json={"error": {"code": "unavailable", "message": "Saved model policy is temporarily unavailable."}})
            return
        if parsed.path == f"/api/v1/llm-configs/{config.id}/inspect" and request.method == "POST":
            calls["inspection"] += 1
            route.fulfill(
                status=200,
                json={
                    "id": str(config.id),
                    "available_models": [model, "synthetic-refreshed-model"],
                    "model_catalog_check_status": "checked",
                    "model_name": model,
                    "base_url": config.base_url,
                    "credential_status": "valid",
                    "warnings": [],
                    "notes": [],
                },
            )
            return
        if parsed.path == "/api/v1/llm-configs" and request.method == "GET":
            route.continue_()
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_api)
    _login_and_open_admin(
        page,
        base_url=live_audit_server,
        email=admin.email,
        password="synthetic-editor-password",
        team_id=str(team.id),
        tab="llm",
    )
    opener = _provider_button(page, "llm", config.id)
    opener.click()
    assert page.locator("#provider-details-modal").is_visible()
    assert calls["policy_get"] == 1
    assert calls["inspection"] == 0
    error = page.locator("#provider-details-error")
    error.wait_for(state="visible")
    assert "Saved model policy is temporarily unavailable" in error.inner_text()
    assert page.locator("#provider-details-save-models").is_disabled()
    assert page.locator("#provider-details-refresh").is_disabled()
    assert calls["policy_get"] == 1
    assert calls["inspection"] == 0

    page.locator("#provider-details-cancel").click()
    assert page.locator("#provider-details-modal").is_hidden()
    opener.click()
    assert page.locator("#provider-details-modal").is_visible()
    page.wait_for_function("document.querySelector('#provider-details-policy-loading').hidden")
    assert "Saved model policy is temporarily unavailable" in page.locator("#provider-details-error").inner_text()
    assert page.locator("#provider-details-save-models").is_disabled()
    assert page.locator("#provider-details-refresh").is_disabled()
    assert calls["policy_get"] == 2
    assert calls["inspection"] == 0, "Opening the editor must not silently retry provider inspection"


def test_llm_connection_test_is_non_finalizing_and_save_reuses_the_checked_draft(
    provider_edit_page,
    live_audit_server,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="LLM connection editor accessibility")
    admin = make_user(email="llm-connection-editor@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    model = "synthetic-current-model"
    config = make_llm_config(team=team, actor=admin, label="Synthetic connection writer", adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai", model_name=model, available_models_json=[model], base_url="https://api.openai.com/v1", has_secret=True)
    make_llm_selection(config=config, actor=admin, allowed_models_json=[model], model_name_override=model)
    page = provider_edit_page
    draft_id = "synthetic-editor-draft"
    discovered_model = "synthetic-replacement-model"
    events: list[tuple[str, str, dict[str, object] | None]] = []
    calls = {"draft": 0, "finalize": 0}

    def synthetic_provider_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        events.append((request.method, parsed.path, body))
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.fulfill(status=200, json={"team_id": str(team.id), "llm_config_id": str(config.id), "allowed_models_json": [model], "model_name_override": model})
            return
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            calls["draft"] += 1
            if calls["draft"] == 1:
                route.fulfill(status=502, json={"error": {"code": "llm_inspection_failed", "message": "Synthetic check failed safely.", "details": {
                    "provider_status": 503, "provider_error_code": "provider_server_error",
                    "provider_message": "LEAK_PROVIDER_BODY_SENTINEL", "response_body": "LEAK_PROVIDER_BODY_SENTINEL"}}})
                return
            route.fulfill(status=200, json={
                "config": {"id": draft_id, "label": "Synthetic connection writer", "base_url": "https://synthetic.example/v1", "setup_status": "pending_model_selection"},
                "discovery_status": "models_discovered", "available_models": [model, discovered_model],
                "available_model_options": [{"id": model, "label": model, "source": "provider"}, {"id": discovered_model, "label": discovered_model, "source": "provider"}], "warnings": [], "notes": [],
            })
            return
        if parsed.path == f"/api/v1/llm-configs/{draft_id}/finalize" and request.method == "POST":
            calls["finalize"] += 1
            route.fulfill(status=200, json={"id": str(config.id), "label": "Synthetic connection writer", "provider_preset": "openai",
                "base_url": "https://synthetic.example/v1", "model_name": discovered_model, "available_models_json": [discovered_model],
                "is_active": True, "auth_mode": "bearer", "has_secret": True, "google_project_id": None,
                "google_location": None, "google_auth_method": None, "capacity_mode": "auto"})
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    opener = _provider_button(page, "llm", config.id)
    opener.click()
    dialog = page.locator("#provider-details-modal")
    assert dialog.is_visible()
    page.locator("#provider-details-tab-connection").click()
    endpoint = page.locator("#provider-details-base-url")
    token = page.locator("#provider-details-token")
    endpoint.fill("https://synthetic.example/v1")
    token.fill("synthetic-editor-token")
    dialog.screenshot(path="/tmp/openscribe-provider-connection-auth-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    dialog.screenshot(path="/tmp/openscribe-provider-connection-auth-mobile.png")
    page.set_viewport_size({"width": 1280, "height": 900})
    test_connection = page.locator("#provider-details-test-connection")
    save_connection = page.locator("#provider-details-save-connection")
    assert test_connection.is_visible() and save_connection.is_visible()

    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts"):
        test_connection.click()
    error = page.locator("#provider-details-error")
    error.wait_for(state="visible")
    assert "Synthetic check failed safely." in error.inner_text()
    assert "Provider returned HTTP 503 Service Unavailable" in error.inner_text()
    assert "provider_server_error" in error.inner_text()
    assert "LEAK_PROVIDER_BODY_SENTINEL" not in dialog.inner_text()
    assert endpoint.input_value() == "https://synthetic.example/v1" and token.input_value() == "synthetic-editor-token"
    assert page.locator("#llm-wizard").is_hidden()

    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as test_response:
        test_connection.click()
    assert test_response.value.ok
    payload = json.loads(test_response.value.request.post_data or "{}")
    assert payload["bearer_token"] == "synthetic-editor-token"
    assert payload["base_url"] == "https://synthetic.example/v1"
    assert token.input_value() == "synthetic-editor-token"
    assert "synthetic-editor-token" not in opener.evaluate("element => [...element.attributes].map(attribute => attribute.value).join(' ')")
    assert "synthetic-editor-token" not in page.evaluate("() => [...Object.values(localStorage), ...Object.values(sessionStorage)].join(' ')")
    assert page.locator("#provider-details-status").inner_text() == "Connection test passed. Changes have not been saved."
    assert page.locator("#provider-details-tab-connection").get_attribute("aria-selected") == "true"
    assert calls == {"draft": 2, "finalize": 0}, "Test connection must not finalize or change the live provider"
    assert not any(path.endswith("/inspect") or path == "/api/v1/llm-selection" for _, path, _ in events)

    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == f"/api/v1/llm-configs/{draft_id}/finalize") as save_response:
        save_connection.click()
    assert save_response.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent === 'Connection saved.'")
    assert calls == {"draft": 2, "finalize": 1}, "Save must reuse the checked draft without a second test"
    finalize_body = json.loads(save_response.value.request.post_data or "{}")
    assert finalize_body["model_name"] == model and "bearer_token" not in finalize_body
    assert "Connection saved." in page.locator("#provider-details-status").inner_text()
    assert "LEAK_PROVIDER_BODY_SENTINEL" not in dialog.inner_text()
    db_session.expire_all()
    persisted = db_session.get(type(config), config.id)
    assert persisted is not None and persisted.base_url == "https://api.openai.com/v1"


def test_saved_bedrock_editor_uses_add_region_options_and_derives_endpoint(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Bedrock saved connection editor")
    admin = make_user(email="bedrock-editor@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    model = "synthetic-bedrock-model"
    saved_region = "eu-north-1"
    saved_url = f"https://bedrock-mantle.{saved_region}.api.aws/v1"
    config = make_llm_config(team=team, actor=admin, label="Synthetic Bedrock gateway", adapter_kind=LlmAdapterKind.bedrock_chat,
        provider_preset="bedrock_http_gateway", base_url=saved_url, model_name=model, available_models_json=[model], has_secret=True)
    make_llm_selection(config=config, actor=admin, allowed_models_json=[model], model_name_override=model)
    page = provider_edit_page
    draft_id = "synthetic-bedrock-editor-draft"
    calls = {"draft": 0, "finalize": 0}

    def synthetic_provider_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.fulfill(status=200, json={"team_id": str(team.id), "llm_config_id": str(config.id), "allowed_models_json": [model], "model_name_override": model})
            return
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            calls["draft"] += 1
            assert body["provider_preset"] == "bedrock_http_gateway"
            assert body["bedrock_region"] == "us-west-2"
            assert body["base_url"] == "https://bedrock-mantle.us-west-2.api.aws/v1"
            route.fulfill(status=200, json={
                "config": {"id": draft_id, "label": "Synthetic Bedrock gateway", "base_url": body["base_url"], "setup_status": "pending_model_selection"},
                "discovery_status": "models_discovered", "available_models": [model],
                "available_model_options": [{"id": model, "label": model, "source": "provider"}], "warnings": [], "notes": [],
            })
            return
        if parsed.path == f"/api/v1/llm-configs/{draft_id}/finalize" and request.method == "POST":
            calls["finalize"] += 1
            route.fulfill(status=200, json={"id": str(config.id), "label": "Synthetic Bedrock gateway", "provider_preset": "bedrock_http_gateway",
                "base_url": "https://bedrock-mantle.us-west-2.api.aws/v1", "model_name": model, "available_models_json": [model],
                "is_active": True, "auth_mode": "bearer", "has_secret": True, "google_project_id": None,
                "google_location": None, "google_auth_method": None, "capacity_mode": "auto"})
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    opener = _provider_button(page, "llm", config.id)
    opener.click()
    dialog = page.locator("#provider-details-modal")
    page.locator("#provider-details-tab-connection").click()
    endpoint = page.locator("#provider-details-base-url")
    region = page.locator("#provider-details-region")
    assert endpoint.get_attribute("readonly") is not None
    assert endpoint.input_value() == saved_url
    assert region.input_value() == saved_region
    assert region.locator("option").all_text_contents() == ["eu-west-2", "eu-west-1", "eu-central-1", "us-east-1", "us-west-2", "ap-southeast-1", "ap-southeast-2", "eu-north-1"]
    assert calls == {"draft": 0, "finalize": 0}, "Opening the saved editor must not inspect or test the provider"

    region.select_option("us-west-2")
    assert endpoint.input_value() == "https://bedrock-mantle.us-west-2.api.aws/v1"
    assert calls == {"draft": 0, "finalize": 0}, "Changing a region must not call a provider"
    page.set_viewport_size({"width": 1280, "height": 900})
    dialog.screenshot(path="/tmp/openscribe-bedrock-connection-editor-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    dialog.screenshot(path="/tmp/openscribe-bedrock-connection-editor-mobile.png")
    page.set_viewport_size({"width": 1280, "height": 900})

    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as test_response:
        page.locator("#provider-details-test-connection").click()
    assert test_response.value.ok
    payload = json.loads(test_response.value.request.post_data or "{}")
    assert payload["bedrock_region"] == "us-west-2"
    assert payload["base_url"] == "https://bedrock-mantle.us-west-2.api.aws/v1"
    assert calls == {"draft": 1, "finalize": 0}
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == f"/api/v1/llm-configs/{draft_id}/finalize") as save_response:
        page.locator("#provider-details-save-connection").click()
    assert save_response.value.ok
    assert calls == {"draft": 1, "finalize": 1}, "Save must reuse the checked Bedrock draft"
    page.locator("#provider-details-cancel").click()
    assert dialog.is_hidden()
    opener.click()
    assert page.locator("#provider-details-region").input_value() == "us-west-2", "Reopening must reflect the saved region"


def test_missing_saved_model_and_finalize_retries_keep_the_tested_draft(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
):
    team = make_team(name="LLM replacement model recovery")
    admin = make_user(email="llm-replacement@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, label="Synthetic old-model writer", adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai", model_name="synthetic-old-model", available_models_json=["synthetic-old-model"], base_url="https://api.openai.com/v1", has_secret=True)
    page = provider_edit_page
    events: list[tuple[str, str, dict[str, object] | None]] = []
    finalize_count = 0
    draft_id = "synthetic-model-recovery-draft"
    replacement = "synthetic-new-model"

    def synthetic_provider_api(route):
        nonlocal finalize_count
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        events.append((request.method, parsed.path, body))
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            route.fulfill(status=200, json={"config": {"id": draft_id, "label": "Synthetic old-model writer", "base_url": "https://synthetic.example/v1"},
                "discovery_status": "models_discovered", "available_models": [replacement],
                "available_model_options": [{"id": replacement, "label": replacement, "source": "provider"}], "warnings": [], "notes": []})
            return
        if parsed.path == f"/api/v1/llm-configs/{draft_id}/finalize" and request.method == "POST":
            finalize_count += 1
            if finalize_count == 1:
                route.fulfill(status=503, json={"error": {"code": "provider_unavailable", "message": "Synthetic finalize failed safely."}})
            elif finalize_count == 2:
                route.fulfill(status=409, json={"error": {"code": "business_rule_violation", "message": "A provider with this name already exists.", "details": {"field": "label"}}})
            else:
                route.fulfill(status=200, json={"id": str(config.id), "label": "Synthetic corrected writer", "provider_preset": "openai",
                    "base_url": "https://synthetic.example/v1", "model_name": replacement, "available_models_json": [replacement],
                    "is_active": True, "auth_mode": "bearer", "has_secret": True, "google_project_id": None,
                    "google_location": None, "google_auth_method": None, "capacity_mode": "auto"})
            return
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.fulfill(status=200, json={"team_id": str(team.id), "llm_config_id": str(config.id), "allowed_models_json": [replacement], "model_name_override": replacement})
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-tab-connection").click()
    page.locator("#provider-details-base-url").fill("https://synthetic.example/v1")
    page.locator("#provider-details-token").fill("synthetic-replacement-key")
    page.locator("#provider-details-test-connection").click()
    page.wait_for_function("document.querySelector('#provider-details-draft-id')?.value === 'synthetic-model-recovery-draft'")
    assert page.locator("#provider-details-tab-models").get_attribute("aria-selected") == "true"
    assert "Choose a model before saving" in page.locator("#provider-details-status").inner_text()

    # Finalization is blocked until a missing saved default is replaced.
    page.locator("#provider-details-save-connection").click()
    error = page.locator("#provider-details-error")
    error.wait_for(state="visible")
    assert "Enter or choose a model" in error.inner_text()
    assert finalize_count == 0
    model = page.locator("#provider-details-revision-model")
    model.select_option(replacement)
    page.locator("#provider-details-save-connection").click()
    error.wait_for(state="visible")
    assert "Synthetic finalize failed safely." in error.inner_text()
    assert "synthetic-replacement-key" not in page.locator("#provider-details-modal").inner_text()
    assert model.input_value() == replacement
    assert len([event for event in events if event[1] == "/api/v1/llm-configs/drafts"]) == 1

    page.locator("#provider-details-save-connection").click()
    error.wait_for(state="visible")
    assert "already exists" in error.inner_text()
    assert page.locator("#provider-details-tab-connection").get_attribute("aria-selected") == "true"
    label = page.locator("#provider-details-label")
    assert label.evaluate("element => document.activeElement === element")
    label.fill("Synthetic corrected writer")
    page.locator("#provider-details-save-connection").click()
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent === 'Connection saved.'")
    assert finalize_count == 3
    assert len([event for event in events if event[1] == "/api/v1/llm-configs/drafts"]) == 1
    finalize_bodies = [body for method, path, body in events if method == "POST" and path.endswith(f"/{draft_id}/finalize")]
    assert [body["model_name"] for body in finalize_bodies] == [replacement, replacement, replacement]
    assert finalize_bodies[-1]["label"] == "Synthetic corrected writer"


def test_manual_required_empty_catalog_is_inconclusive_and_keeps_saved_model_usable(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
):
    team = make_team(name="LLM inconclusive inspection")
    admin = make_user(email="llm-inconclusive@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, label="Synthetic known-model writer", adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai", model_name="synthetic-saved-model", available_models_json=["synthetic-saved-model"],
        base_url="https://api.openai.com/v1", has_secret=True)
    page = provider_edit_page
    events: list[tuple[str, str, dict[str, object] | None]] = []
    draft_id = "synthetic-inconclusive-draft"

    def synthetic_provider_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        events.append((request.method, parsed.path, body))
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            route.fulfill(status=200, json={"config": {"id": draft_id, "label": config.label, "base_url": "https://synthetic.example/v1"},
                "discovery_status": "manual_required", "available_models": [], "available_model_options": [],
                "warnings": ["Synthetic catalog probe was unavailable."], "notes": ["The saved model remains available for review."]})
            return
        if parsed.path == f"/api/v1/llm-configs/{draft_id}/finalize" and request.method == "POST":
            route.fulfill(status=200, json={"id": str(config.id), "label": config.label, "provider_preset": "openai",
                "base_url": "https://synthetic.example/v1", "model_name": "synthetic-saved-model", "available_models_json": ["synthetic-saved-model"],
                "is_active": True, "auth_mode": "bearer", "has_secret": True, "google_project_id": None,
                "google_location": None, "google_auth_method": None, "capacity_mode": "auto"})
            return
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.fulfill(status=200, json={"team_id": str(team.id), "llm_config_id": str(config.id),
                "allowed_models_json": ["synthetic-saved-model"], "model_name_override": "synthetic-saved-model"})
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-tab-connection").click()
    page.locator("#provider-details-base-url").fill("https://synthetic.example/v1")
    page.locator("#provider-details-token").fill("synthetic-inconclusive-key")
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as test_response:
        page.locator("#provider-details-test-connection").click()
    assert test_response.value.ok
    status = page.locator("#provider-details-status").inner_text()
    assert status == "Connection test inconclusive. Synthetic catalog probe was unavailable. The saved model remains available for review. Changes have not been saved."
    assert "passed" not in status.lower()
    assert page.locator("#provider-details-tab-connection").get_attribute("aria-selected") == "true"
    assert page.locator("#provider-details-revision-manual-model").input_value() == "synthetic-saved-model"
    assert not any(path.endswith("/finalize") for _, path, _ in events)

    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == f"/api/v1/llm-configs/{draft_id}/finalize") as save_response:
        page.locator("#provider-details-save-connection").click()
    assert save_response.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent === 'Connection saved.'")
    finalize_body = next(body for method, path, body in events if method == "POST" and path.endswith(f"/{draft_id}/finalize"))
    assert finalize_body["model_name"] == "synthetic-saved-model"
    assert len([event for event in events if event[1] == "/api/v1/llm-configs/drafts"]) == 1


def test_changed_llm_tested_connection_is_discarded_only_on_explicit_retest(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
):
    team = make_team(name="LLM connection retest accessibility")
    admin = make_user(email="llm-retest@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, label="Synthetic retest provider", adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai", model_name="synthetic-model", available_models_json=["synthetic-model"], base_url="https://api.openai.com/v1", has_secret=True)
    page = provider_edit_page
    draft_ids = ["synthetic-first-check", "synthetic-second-check", "synthetic-third-check", "synthetic-reopened-check"]
    events: list[tuple[str, str, dict[str, object] | None]] = []
    drafts = 0

    def synthetic_provider_api(route):
        nonlocal drafts
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        events.append((request.method, parsed.path, body))
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            drafts += 1
            draft_id = draft_ids[drafts - 1]
            route.fulfill(status=200, json={"config": {"id": draft_id, "label": "Synthetic retest provider", "base_url": body["base_url"]},
                "discovery_status": "models_discovered", "available_models": ["synthetic-model"],
                "available_model_options": [{"id": "synthetic-model", "label": "synthetic-model", "source": "provider"}], "warnings": [], "notes": []})
            return
        if request.method == "DELETE" and parsed.path.startswith("/api/v1/llm-configs/"):
            route.fulfill(status=204, body="")
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-tab-connection").click()
    endpoint = page.locator("#provider-details-base-url")
    token = page.locator("#provider-details-token")
    endpoint.fill("https://first.synthetic.example/v1")
    token.fill("first-synthetic-key")
    page.locator("#provider-details-test-connection").click()
    page.wait_for_function("document.querySelector('#provider-details-draft-id')?.value === 'synthetic-first-check'")
    assert drafts == 1

    # An explicit second Test always refreshes the checked snapshot, even when
    # inputs match. Typing afterward makes no request; the next Test discards
    # that second draft and inspects the new endpoint and key.
    with page.expect_response(lambda response: response.request.method == "DELETE" and urlparse(response.url).path == f"/api/v1/llm-configs/{draft_ids[0]}"):
        page.locator("#provider-details-test-connection").click()
    page.wait_for_function("document.querySelector('#provider-details-draft-id')?.value === 'synthetic-second-check'")
    assert drafts == 2
    existing_events = len(events)
    endpoint.fill("https://second.synthetic.example/v1")
    token.fill("second-synthetic-key")
    assert len(events) == existing_events, "Typing must not send a request or discard the existing draft"
    with page.expect_response(lambda response: response.request.method == "DELETE" and urlparse(response.url).path == f"/api/v1/llm-configs/{draft_ids[1]}"):
        page.locator("#provider-details-test-connection").click()
    page.wait_for_function("document.querySelector('#provider-details-draft-id')?.value === 'synthetic-third-check'")
    assert drafts == 3
    third_payload = next(body for method, path, body in events if method == "POST" and path == "/api/v1/llm-configs/drafts" and body and body["base_url"] == "https://second.synthetic.example/v1")
    assert third_payload["bearer_token"] == "second-synthetic-key"
    assert not any(path.endswith("/finalize") or path.endswith("/inspect") for _, path, _ in events)

    # Cancel durably removes an unfinalized tested revision and leaves the
    # editor usable when opened again.
    page.locator("#provider-details-cancel").click()
    unsaved = page.locator("#admin-unsaved-modal")
    assert unsaved.is_visible()
    with page.expect_response(lambda response: response.request.method == "DELETE" and urlparse(response.url).path == f"/api/v1/llm-configs/{draft_ids[2]}"):
        unsaved.locator("[data-unsaved-discard]").click()
    page.locator("#provider-details-modal").wait_for(state="hidden")
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-modal").wait_for(state="visible")
    page.locator("#provider-details-tab-connection").click()
    assert page.locator("#provider-details-test-connection").is_enabled()
    assert page.locator("#provider-details-save-connection").is_enabled()
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts"):
        page.locator("#provider-details-test-connection").click()
    page.wait_for_function("document.querySelector('#provider-details-draft-id')?.value === 'synthetic-reopened-check'")
    assert drafts == 4


def test_optional_saved_bearer_requires_replacement_or_explicit_removal(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
):
    team = make_team(name="Optional saved bearer accessibility")
    admin = make_user(email="optional-key@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, label="Synthetic optional provider", adapter_kind=LlmAdapterKind.ollama_chat,
        provider_preset="ollama", model_name="synthetic-model", available_models_json=["synthetic-model"], base_url="http://localhost:11434/v1", has_secret=True)
    page = provider_edit_page
    events: list[tuple[str, str, dict[str, object] | None]] = []

    def synthetic_provider_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        events.append((request.method, parsed.path, body))
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            route.fulfill(status=200, json={"config": {"id": "synthetic-noauth-draft", "label": "Synthetic optional provider", "base_url": "http://localhost:11434/v1"},
                "discovery_status": "models_discovered", "available_models": ["synthetic-model"],
                "available_model_options": [{"id": "synthetic-model", "label": "synthetic-model", "source": "provider"}], "warnings": [], "notes": []})
            return
        if request.method == "DELETE" and parsed.path == "/api/v1/llm-configs/synthetic-noauth-draft":
            route.fulfill(status=204, body="")
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-tab-connection").click()
    token = page.locator("#provider-details-token")
    assert token.get_attribute("type") == "password" and token.input_value() == ""
    panel_text = page.locator("#provider-details-panel-connection").inner_text()
    assert "Key saved — paste to replace" in panel_text
    assert page.locator('input[name="provider-details-auth-choice"]').count() == 0
    remove = page.locator("[data-provider-remove-saved-key]")
    assert remove.is_visible()

    page.locator("#provider-details-test-connection").click()
    error = page.locator("#provider-details-error")
    error.wait_for(state="visible")
    assert "Remove saved key" in error.inner_text()
    assert not any(method == "POST" and path == "/api/v1/llm-configs/drafts" for method, path, _ in events)

    remove.click()
    assert remove.get_attribute("aria-pressed") == "true"
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as test_response:
        page.locator("#provider-details-test-connection").click()
    assert test_response.value.ok
    payload = json.loads(test_response.value.request.post_data or "{}")
    assert payload["bearer_token"] is None
    assert "auth_mode" not in payload
    assert len([event for event in events if event[1] == "/api/v1/llm-configs/drafts"]) == 1

    token.fill("synthetic-optional-key-replacement")
    assert remove.get_attribute("aria-pressed") == "false", "Pasting a replacement must cancel key removal"
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as override_response:
        page.locator("#provider-details-test-connection").click()
    assert override_response.value.ok
    assert any(method == "DELETE" and path == "/api/v1/llm-configs/synthetic-noauth-draft" for method, path, _ in events)
    payloads = [body for method, path, body in events if method == "POST" and path == "/api/v1/llm-configs/drafts"]
    assert payloads[1]["bearer_token"] == "synthetic-optional-key-replacement"

def test_valid_saved_default_auto_finalizes_connection_revision_without_model_walkthrough(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="LLM valid-default connection accessibility")
    admin = make_user(
        email="llm-valid-default-editor@example.test",
        password="synthetic-editor-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    saved_model = "synthetic-saved-default"
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Synthetic stable writer",
        adapter_kind=LlmAdapterKind.openai_chat,
        provider_preset="openai",
        model_name=saved_model,
        available_models_json=[saved_model, "synthetic-extra-model"],
        base_url="https://api.openai.com/v1",
        has_secret=True,
    )
    make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=[saved_model],
        model_name_override=saved_model,
    )

    page = provider_edit_page
    events: list[tuple[str, str, dict[str, object] | None]] = []
    draft_id = "synthetic-auto-finalize-draft"

    def synthetic_provider_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        body = json.loads(request.post_data or "{}") if request.post_data else None
        events.append((request.method, parsed.path, body))
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.continue_()
            return
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            route.fulfill(
                status=200,
                json={
                    "config": {"id": draft_id, "label": "Synthetic revision", "base_url": "https://synthetic.example/v1"},
                    "discovery_status": "models_discovered",
                    "available_models": [saved_model, "synthetic-extra-model"],
                    "available_model_options": [
                        {"id": saved_model, "label": saved_model, "source": "provider"},
                        {"id": "synthetic-extra-model", "label": "synthetic-extra-model", "source": "provider"},
                    ],
                    "warnings": [],
                    "notes": [],
                },
            )
            return
        if parsed.path == f"/api/v1/llm-configs/{draft_id}/finalize" and request.method == "POST":
            route.fulfill(
                status=200,
                json={
                    "id": str(config.id),
                    "label": "Synthetic stable writer",
                    "provider_preset": "openai",
                    "base_url": "https://synthetic.example/v1",
                    "model_name": saved_model,
                    "available_models_json": [saved_model, "synthetic-extra-model"],
                    "is_active": True,
                    "auth_mode": "bearer",
                    "has_secret": True,
                    "google_project_id": None,
                    "google_location": None,
                    "google_auth_method": None,
                    "capacity_mode": "auto",
                },
            )
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(
        page,
        base_url=live_audit_server,
        email=admin.email,
        password="synthetic-editor-password",
        team_id=str(team.id),
        tab="llm",
    )
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-tab-connection").click()
    page.locator("#provider-details-base-url").fill("https://synthetic.example/v1")
    page.locator("#provider-details-save-connection").click()

    status = page.locator("#provider-details-status")
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent === 'Connection saved.'")
    assert status.is_visible()
    assert status.inner_text() == "Connection saved."
    assert page.locator("#provider-details-save-connection").text_content() == "Save connection"
    assert page.locator("#provider-details-work").get_attribute("aria-busy") == "false"
    assert page.locator("#provider-details-save-connection").is_enabled()
    assert page.locator("#provider-details-revision-review").is_hidden()
    assert page.locator("#llm-wizard").is_hidden()
    draft_calls = [entry for entry in events if entry[1] == "/api/v1/llm-configs/drafts"]
    finalize_calls = [entry for entry in events if entry[1].endswith(f"/llm-configs/{draft_id}/finalize")]
    assert len(draft_calls) == 1
    assert len(finalize_calls) == 1
    assert finalize_calls[0][2]["model_name"] == saved_model
    assert not any(path.endswith("/inspect") for _, path, _ in events)


def test_optional_provider_without_saved_key_uses_blank_noauth_connection(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
):
    team = make_team(name="Optional no-key accessibility")
    admin = make_user(email="optional-no-key@example.test", password="synthetic-editor-password", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, label="Synthetic no-key provider", adapter_kind=LlmAdapterKind.ollama_chat,
        provider_preset="ollama", model_name="synthetic-model", available_models_json=["synthetic-model"],
        base_url="http://localhost:11434/v1", has_secret=False)
    page = provider_edit_page
    draft_payloads: list[dict[str, object]] = []

    def synthetic_provider_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            draft_payloads.append(body)
            route.fulfill(status=200, json={"config": {"id": "synthetic-no-key-draft", "label": "Synthetic no-key provider", "base_url": config.base_url},
                "discovery_status": "models_discovered", "available_models": ["synthetic-model"],
                "available_model_options": [{"id": "synthetic-model", "label": "synthetic-model", "source": "provider"}], "warnings": [], "notes": []})
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_provider_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email, password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    _provider_button(page, "llm", config.id).click()
    page.locator("#provider-details-tab-connection").click()
    token = page.locator("#provider-details-token")
    assert token.input_value() == ""
    assert "Key saved" not in page.locator("#provider-details-panel-connection").inner_text()
    assert page.locator("[data-provider-remove-saved-key]").count() == 0
    assert page.locator('input[name="provider-details-auth-choice"]').count() == 0
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as test_response:
        page.locator("#provider-details-test-connection").click()
    assert test_response.value.ok
    assert len(draft_payloads) == 1 and draft_payloads[0]["bearer_token"] is None
    assert "auth_mode" not in draft_payloads[0]


def test_successful_model_actions_restore_labels_and_release_busy_state(
    provider_edit_page,
    live_audit_server,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="DeepSeek model action feedback")
    admin = make_user(
        email="deepseek-model-feedback@example.test",
        password="synthetic-editor-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    models = ["deepseek-chat", "deepseek-reasoner"]
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Synthetic DeepSeek provider",
        provider_preset="deepseek",
        adapter_kind=LlmAdapterKind.openai_chat,
        model_name=models[0],
        available_models_json=models,
        base_url="https://api.deepseek.com",
        has_secret=True,
    )
    make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=models,
        model_name_override=models[0],
    )

    page = provider_edit_page
    console_errors: list[str] = []
    page.on("pageerror", lambda error: console_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    calls: list[tuple[str, str]] = []
    fail_next_inspection = {"value": False}
    saved_check_failed = {"value": False}
    inspection_failure = {"code": "authentication_error", "http_status": 401, "credential_status": "invalid"}

    def synthetic_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        calls.append((request.method, parsed.path))
        if parsed.path == f"/api/v1/llm-configs/{config.id}/model-policy" and request.method == "GET":
            route.fulfill(status=200, json=_model_policy_detail(config, str(team.id)))
            return
        if parsed.path == f"/api/v1/llm-configs/{config.id}/model-policy" and request.method == "PUT":
            body = json.loads(request.post_data or "{}")
            policy = _model_policy_detail(config, str(team.id))
            policy["enabled_models_json"] = body["enabled_models_json"]
            policy["default_model_name"] = body["default_model_name"]
            route.fulfill(status=200, json=policy)
            return
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.fulfill(status=200, json={
                "team_id": str(team.id),
                "llm_config_id": str(config.id),
                "allowed_models_json": models,
                "model_name_override": models[0],
            })
            return
        if parsed.path == "/api/v1/llm-selection" and request.method == "POST":
            route.fulfill(status=200, json={"ok": True})
            return
        if parsed.path == f"/api/v1/llm-configs/{config.id}/inspect" and request.method == "POST":
            if fail_next_inspection["value"]:
                fail_next_inspection["value"] = False
                saved_check_failed["value"] = True
                route.fulfill(status=200, json={
                    "id": str(config.id), "available_models": models,
                    "model_catalog_checked": False,
                    "model_catalog_check_status": "failed",
                    "model_catalog_check_error_code": inspection_failure["code"],
                    "model_catalog_check_http_status": inspection_failure["http_status"],
                    "discovery_status": "manual_required",
                    "credential_status": inspection_failure["credential_status"],
                    "warnings": [], "notes": [],
                })
                return
            route.fulfill(status=200, json={
                "id": str(config.id),
                "available_models_json": models,
                "model_name": models[0],
                "base_url": config.base_url,
                "credential_status": "valid",
                "model_catalog_check_status": "checked",
                "warnings": [],
                "notes": [],
            })
            return
        if parsed.path == f"/api/v1/llm-configs/{config.id}" and request.method == "GET":
            route.fulfill(status=200, json={
                "id": str(config.id), "is_active": True, "available_models_json": models,
                "model_name": models[0], "base_url": config.base_url,
                "last_connection_check_status": "failed",
                "last_connection_check_at": "2026-11-01T12:00:00Z",
                "last_connection_check_error_code": inspection_failure["code"],
                "last_connection_check_http_status": inspection_failure["http_status"],
            })
            return
        if parsed.path == "/api/v1/llm-configs" and request.method == "GET":
            route.fulfill(status=200, json=[{
                "id": str(config.id), "is_active": True, "available_models_json": models,
                "model_name": models[0], "base_url": config.base_url,
                "last_connection_check_status": "failed" if saved_check_failed["value"] else None,
                "last_connection_check_at": "2026-11-01T12:00:00Z" if saved_check_failed["value"] else None,
                "last_connection_check_error_code": inspection_failure["code"] if saved_check_failed["value"] else None,
                "last_connection_check_http_status": inspection_failure["http_status"] if saved_check_failed["value"] else None,
            }])
            return
        route.abort()

    page.context.route("**/api/v1/**", synthetic_api)
    _login_and_open_admin(
        page,
        base_url=live_audit_server,
        email=admin.email,
        password="synthetic-editor-password",
        team_id=str(team.id),
        tab="llm",
    )
    _provider_button(page, "llm", config.id).click()
    dialog = page.locator("#provider-details-modal")
    assert dialog.is_visible()
    save = page.locator("#provider-details-save-models")
    refresh = page.locator("#provider-details-refresh")
    work = page.locator("#provider-details-work")
    states: list[dict[str, object]] = []

    page.wait_for_function("!document.querySelector('#provider-details-save-models')?.disabled")
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{config.id}/model-policy"
    ) as first_save:
        save.click()
    assert first_save.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Models saved')")
    states.append({
        "step": "first save",
        "save": save.text_content(),
        "refresh": refresh.locator("span").text_content(),
        "busy": work.get_attribute("aria-busy"),
        "save_enabled": save.is_enabled(),
        "refresh_enabled": refresh.is_enabled(),
    })

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{config.id}/inspect"
    ) as refresh_response:
        refresh.click()
    assert refresh_response.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Refresh complete')")
    states.append({
        "step": "refresh",
        "save": save.text_content(),
        "refresh": refresh.locator("span").text_content(),
        "busy": work.get_attribute("aria-busy"),
        "save_enabled": save.is_enabled(),
        "refresh_enabled": refresh.is_enabled(),
    })

    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{config.id}/model-policy"
    ) as second_save:
        save.click()
    assert second_save.value.ok
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Models saved')")
    states.append({
        "step": "second save",
        "save": save.text_content(),
        "refresh": refresh.locator("span").text_content(),
        "busy": work.get_attribute("aria-busy"),
        "save_enabled": save.is_enabled(),
        "refresh_enabled": refresh.is_enabled(),
        "status": page.locator("#provider-details-status").inner_text(),
        "console_errors": list(console_errors),
    })
    dialog.screenshot(path="/tmp/openscribe-provider-model-action-feedback-success-after.png")

    fail_next_inspection["value"] = True
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{config.id}/inspect"
    ) as failed_refresh:
        refresh.click()
    assert failed_refresh.value.status == 200
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Could not refresh models')")
    assert "saved credential was rejected" in page.locator("#provider-details-status").inner_text()
    row = _provider_button(page, "llm", config.id)
    row_health = row.evaluate("button => button.closest('.llm-provider-row')?.querySelector('[data-check-status]')?.dataset.checkStatus")
    assert row_health == "failed", row.evaluate("button => button.closest('.llm-provider-row')?.outerHTML || button.outerHTML")
    assert "Saved credential rejected" in row.evaluate("button => button.closest('.llm-provider-row')?.innerText || ''")
    states.append({
        "step": "failed refresh",
        "save": save.text_content(),
        "refresh": refresh.locator("span").text_content(),
        "busy": work.get_attribute("aria-busy"),
        "save_enabled": save.is_enabled(),
        "refresh_enabled": refresh.is_enabled(),
    })
    dialog.screenshot(path="/tmp/openscribe-provider-model-action-feedback-after.png")
    inspection_failure.update(code="permission_denied", http_status=403, credential_status="valid")
    fail_next_inspection["value"] = True
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{config.id}/inspect"
    ) as denied_refresh:
        page.locator("#provider-details-refresh").click()
    assert denied_refresh.value.status == 200
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Could not refresh models')")
    denial_copy = page.locator("#provider-details-status").inner_text().lower()
    assert "provider denied access" in denial_copy
    assert "credential was rejected" not in denial_copy
    denied_row_text = row.evaluate("button => button.closest('.llm-provider-row')?.innerText || ''")
    assert "Provider denied access" in denied_row_text
    assert "Saved credential rejected" not in denied_row_text
    page.locator("#provider-details-cancel").click()

    expected_states = [
        {"step": "first save", "save": "Save models", "refresh": "Refresh models", "busy": "false", "save_enabled": True, "refresh_enabled": True},
        {"step": "refresh", "save": "Save models", "refresh": "Refresh models", "busy": "false", "save_enabled": True, "refresh_enabled": True},
        {"step": "second save", "save": "Save models", "refresh": "Refresh models", "busy": "false", "save_enabled": True, "refresh_enabled": True,
         "status": "Models saved. Changes apply to new generations.", "console_errors": []},
        {"step": "failed refresh", "save": "Save models", "refresh": "Refresh models", "busy": "false", "save_enabled": True, "refresh_enabled": True},
    ]
    assert states == expected_states, states
    assert calls.count(("PUT", f"/api/v1/llm-configs/{config.id}/model-policy")) == 2
    assert calls.count(("POST", f"/api/v1/llm-configs/{config.id}/inspect")) == 3


def test_saved_provider_pending_revision_conflict_has_explicit_safe_recovery(
    provider_edit_page, live_audit_server, make_team, make_user, make_llm_config,
):
    team = make_team(name="Pending revision recovery")
    admin = make_user(email="pending-revision@example.test", password="synthetic-editor-password",
                      is_system_admin=True, mfa_required=False, mfa_enabled=False)
    model = "synthetic-model"
    pending_id = "22222222-2222-4222-8222-222222222222"
    config = make_llm_config(team=team, actor=admin, label="Synthetic writer", model_name=model,
        available_models_json=[model], base_url="https://api.synthetic.example/v1", has_secret=True)
    root_id = str(config.id)
    events = []
    valid_conflict = {"error": {"code": "conflict", "message": "An unfinished connection edit exists.", "details": {
        "resource": "llm_config_revision", "revision_of_config_id": root_id, "pending_revision_id": pending_id}}}
    draft_responses = [
        (409, valid_conflict),
        (409, valid_conflict),
    ]
    delete_statuses = [204, 503, 404]
    detail_state = {"status": 200, "body": {"id": pending_id, "team_id": str(team.id),
        "revision_of_config_id": root_id, "label": "Synthetic earlier writer", "provider_preset": "openai",
        "base_url": "https://earlier.synthetic.example/v1", "auth_mode": "bearer", "has_secret": True,
        "available_models_json": ["synthetic-model", "synthetic-model-two"], "model_name": "synthetic-model",
        "setup_status": "pending_model_selection"}}

    def synthetic_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/api/v1/"):
            route.continue_()
            return
        events.append((request.method, parsed.path))
        if parsed.path == "/api/v1/llm-selection" and request.method == "GET":
            route.fulfill(status=200, json={"team_id": str(team.id), "llm_config_id": root_id,
                "allowed_models_json": [model], "model_name_override": model})
            return
        if parsed.path == "/api/v1/llm-configs/drafts" and request.method == "POST":
            status, body = draft_responses.pop(0)
            route.fulfill(status=status, json=body)
            return
        if parsed.path == f"/api/v1/llm-configs/{pending_id}" and request.method == "GET":
            route.fulfill(status=detail_state["status"], json=detail_state["body"])
            return
        if parsed.path == f"/api/v1/llm-configs/{pending_id}" and request.method == "DELETE":
            route.fulfill(status=delete_statuses.pop(0))
            return
        route.abort()

    page = provider_edit_page
    page.context.route("**/api/v1/**", synthetic_api)
    _login_and_open_admin(page, base_url=live_audit_server, email=admin.email,
        password="synthetic-editor-password", team_id=str(team.id), tab="llm")
    opener = _provider_button(page, "llm", config.id)
    opener.click()
    page.locator("#provider-details-tab-connection").click()
    endpoint = page.locator("#provider-details-base-url")
    token = page.locator("#provider-details-token")
    endpoint.fill("https://new.synthetic.example/v1")
    token.fill("synthetic-new-key")
    page.locator("#provider-details-test-connection").click()
    recovery = page.locator("#provider-details-conflict-recovery")
    recovery.wait_for(state="visible")
    review = recovery.get_by_role("button", name="Review unfinished changes")
    assert "saved connection is still in use" in recovery.inner_text()
    assert "Verify the endpoint" not in page.locator("#provider-details-error").inner_text()
    assert token.input_value() == "synthetic-new-key"
    assert not any(method == "DELETE" for method, _ in events)
    prior_location = page.url
    events_before_review = len(events)
    with page.expect_response(lambda response: response.request.method == "GET"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{pending_id}") as reviewed:
        review.click()
    assert reviewed.value.ok
    assert page.url == prior_location
    assert page.locator("#provider-details-modal").is_visible()
    assert page.locator("#admin-unsaved-modal").is_hidden()
    assert endpoint.input_value() == "https://new.synthetic.example/v1"
    assert token.input_value() == "synthetic-new-key"
    summary = page.locator("#provider-details-conflict-summary")
    assert summary.is_visible()
    assert "Synthetic earlier writer" in summary.inner_text()
    assert "https://earlier.synthetic.example/v1" in summary.inner_text()
    assert "Authentication" in summary.inner_text() and "Configured" in summary.inner_text()
    assert "2 available · default synthetic-model" in summary.inner_text()
    assert "synthetic-new-key" not in summary.inner_text()
    assert not any(method in {"POST", "DELETE"} for method, _ in events[events_before_review:])
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("#provider-details-modal").screenshot(path="/tmp/openscribe-provider-pending-review-inline-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#provider-details-modal").screenshot(path="/tmp/openscribe-provider-pending-review-inline-mobile.png")
    page.set_viewport_size({"width": 1280, "height": 900})
    recovery.get_by_role("button", name="Hide earlier changes").click()
    assert summary.is_hidden()
    assert endpoint.evaluate("el => document.activeElement === el")

    # A mismatched response and a read failure never expose the returned metadata
    # or change the current editor draft; each can be retried in place.
    detail_state["body"] = {**detail_state["body"], "team_id": "33333333-3333-4333-8333-333333333333"}
    with page.expect_response(lambda response: response.request.method == "GET"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{pending_id}"):
        review.click()
    assert "could not be loaded" in page.locator("#provider-details-conflict-summary-status").inner_text()
    assert "Synthetic earlier writer" not in summary.inner_text()
    assert endpoint.input_value() == "https://new.synthetic.example/v1"
    detail_state["status"] = 503
    with page.expect_response(lambda response: response.request.method == "GET"
        and urlparse(response.url).path == f"/api/v1/llm-configs/{pending_id}"):
        review.click()
    assert "could not be loaded" in page.locator("#provider-details-conflict-summary-status").inner_text()
    assert endpoint.input_value() == "https://new.synthetic.example/v1"
    detail_state["status"] = 200
    detail_state["body"] = {**detail_state["body"], "team_id": str(team.id)}
    page.locator("#provider-details-cancel").click()
    unsaved = page.locator("#admin-unsaved-modal")
    assert unsaved.is_visible(), "Cancel must still use the established unsaved-changes guard"
    unsaved.locator("[data-unsaved-discard]").click()
    assert page.locator("#provider-details-modal").is_hidden()
    assert not any(method == "DELETE" for method, _ in events), "Closing must leave the conflicting draft untouched"

    opener.click()
    assert page.locator("#provider-details-conflict-recovery").is_hidden(), "A new modal must not inherit old conflict state"
    page.locator("#provider-details-tab-connection").click()
    page.locator("#provider-details-base-url").fill("https://new.synthetic.example/v1")
    page.locator("#provider-details-token").fill("synthetic-new-key")
    page.locator("#provider-details-test-connection").click()
    page.locator("#provider-details-conflict-recovery").wait_for(state="visible")
    page.locator("#provider-details-conflict-discard").click()
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('Previous changes discarded')")
    assert ("DELETE", f"/api/v1/llm-configs/{pending_id}") in events
    assert ("DELETE", f"/api/v1/llm-configs/{root_id}") not in events
    assert token.input_value() == "synthetic-new-key"
    assert page.locator("#provider-details-conflict-recovery").is_hidden()
    assert page.locator("#provider-details-test-connection").evaluate("el => document.activeElement === el")
    assert "Test the connection or save" in page.locator("#provider-details-status").inner_text()

    malformed_details = [
        {"resource": "stt_config_revision", "revision_of_config_id": root_id, "pending_revision_id": pending_id},
        {"resource": "llm_config_revision", "revision_of_config_id": pending_id, "pending_revision_id": root_id},
        {"resource": "llm_config_revision", "revision_of_config_id": root_id, "pending_revision_id": "vault/path"},
        {"field": "label"},
    ]
    for details in malformed_details:
        draft_responses.append((409, {"error": {"code": "conflict", "message": "Conflict.", "details": details}}))
        page.locator("#provider-details-test-connection").click()
        page.locator("#provider-details-error").wait_for(state="visible")
        assert page.locator("#provider-details-conflict-recovery").is_hidden()

    draft_responses.append((409, valid_conflict))
    page.locator("#provider-details-test-connection").click()
    page.locator("#provider-details-conflict-recovery").wait_for(state="visible")
    draft_requests_before_discard = sum(method == "POST" and path == "/api/v1/llm-configs/drafts" for method, path in events)
    page.locator("#provider-details-conflict-discard").click()
    page.locator("#provider-details-error").wait_for(state="visible")
    assert page.locator("#provider-details-conflict-recovery").is_visible()
    assert page.locator("#provider-details-conflict-discard").is_enabled()
    assert endpoint.input_value() == "https://new.synthetic.example/v1"
    assert token.input_value() == "synthetic-new-key"
    assert sum(method == "POST" and path == "/api/v1/llm-configs/drafts" for method, path in events) == draft_requests_before_discard

    page.locator("#provider-details-conflict-discard").click()
    page.wait_for_function("document.querySelector('#provider-details-status')?.textContent.includes('already gone')")
    assert page.locator("#provider-details-conflict-recovery").is_hidden()
    assert page.locator("#provider-details-test-connection").evaluate("el => document.activeElement === el")
    assert endpoint.input_value() == "https://new.synthetic.example/v1"
    assert token.input_value() == "synthetic-new-key"
    assert sum(method == "POST" and path == "/api/v1/llm-configs/drafts" for method, path in events) == draft_requests_before_discard

    draft_responses.append((200, {"config": {"id": pending_id, "label": "Synthetic writer",
        "base_url": "https://new.synthetic.example/v1", "setup_status": "pending_model_selection"},
        "discovery_status": "fetched", "available_models": [model],
        "available_model_options": [{"id": model, "label": model, "source": "provider"}], "warnings": [], "notes": []}))
    with page.expect_response(lambda response: response.request.method == "POST"
        and urlparse(response.url).path == "/api/v1/llm-configs/drafts") as explicit_test:
        page.locator("#provider-details-test-connection").click()
    assert explicit_test.value.ok
    assert page.locator("#provider-details-status").inner_text() == "Connection test passed. Changes have not been saved."
    submitted = json.loads(explicit_test.value.request.post_data or "{}")
    assert submitted["base_url"] == "https://new.synthetic.example/v1"
    assert submitted["bearer_token"] == "synthetic-new-key"
