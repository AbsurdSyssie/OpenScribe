from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.real_db_connections, pytest.mark.accessibility_audit]


@pytest.fixture
def provider_feedback_page(live_audit_server):
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


@pytest.mark.parametrize(
    ("kind", "tab", "button_id", "next_id", "cancel_id", "key_id"),
    [
        ("stt", "stt", "add-stt-provider-button", "stt-wizard-next", "cancel-stt-wizard", "stt-api-key"),
        ("llm", "llm", "add-llm-provider-button", "llm-wizard-next", "cancel-llm-wizard", "llm-api-key"),
    ],
)
def test_provider_inspection_failure_is_visible_and_recoverable(
    provider_feedback_page,
    make_team,
    make_user,
    kind,
    tab,
    button_id,
    next_id,
    cancel_id,
    key_id,
):
    team = make_team(name=f"Provider feedback {kind.upper()}")
    admin = make_user(
        email=f"provider-feedback-{kind}@example.test",
        password="synthetic-provider-feedback-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    page = provider_feedback_page

    def block_unmocked_provider_requests(route):
        if re.search(r"/api/v1/(?:stt|llm)-configs/", route.request.url):
            route.abort()
        else:
            route.continue_()

    # This context-level backstop remains active across the save redirect. All
    # provider requests in this test are fulfilled by the synthetic JS stub.
    page.context.route("**/api/v1/**", block_unmocked_provider_requests)
    page.goto("/login", wait_until="domcontentloaded")
    page.locator('input[name="email"]').fill(admin.email)
    page.locator('input[name="password"]').fill("synthetic-provider-feedback-password")
    page.locator('button[type="submit"]').click()
    page.wait_for_url(re.compile(r".*/admin(?:/.*|\?.*)?$"))
    page.goto(f"/admin?team_id={team.id}&team_tab={tab}", wait_until="domcontentloaded")

    page.locator(f"#{button_id}").click()
    wizard = page.locator(f"#{kind}-wizard")
    next_button = page.locator(f"#{next_id}")
    next_button.click()  # Provider choice -> connection details.
    page.locator(f"#{key_id}").fill("synthetic-key-for-browser-test")

    page.evaluate(
        """() => {
          window.__providerFetchCalls = [];
          window.OpenScribeCSRF.fetch = (url, options) => new Promise((resolve) => {
            window.__providerFetchCalls.push({ url, options });
            window.__resolveProviderFetch = (status, payload) => resolve(status === 204
              ? new Response(null, { status })
              : new Response(JSON.stringify(payload), {
                  status, headers: { "Content-Type": "application/json" },
                }));
          });
        }"""
    )
    next_button.click()
    page.locator(f"#{kind}-wizard .wizard-body").wait_for()
    page.wait_for_function("document.querySelector('.wizard-body[aria-busy=true]') !== null")
    assert page.locator(f"#{kind}-wizard-status").is_visible()
    assert page.locator(f"#{next_id}").is_disabled()
    assert page.locator(f"#{cancel_id}").is_disabled()
    assert page.locator(f"#{key_id}").is_disabled()
    assert next_button.inner_text() in {"Checking connection…", "Finding models…"}

    # Even a synthetic second click cannot dispatch a duplicate request.
    page.evaluate(
        f"document.getElementById('{next_id}').dispatchEvent(new MouseEvent('click', {{ bubbles: true }}))"
    )
    assert page.evaluate("window.__providerFetchCalls.length") == 1

    page.evaluate(
        """() => window.__resolveProviderFetch(502, {
          error: {
            code: "llm_inspection_failed",
            message: "Could not load available provider models.",
            details: {
              provider_status: 503,
              provider_error_code: "provider_server_error",
              provider_message: "synthetic-key-for-browser-test private-response-content",
            },
          },
        })"""
    )
    alert = wizard.get_by_role("alert")
    alert.wait_for(state="visible")
    assert "Could not load available provider models." in alert.inner_text()
    assert "Provider returned HTTP 503 Service Unavailable" in alert.inner_text()
    assert "provider_server_error" in alert.inner_text()
    assert "synthetic-key-for-browser-test" not in alert.inner_text()
    assert "private-response-content" not in alert.inner_text()
    assert page.locator(f"#{key_id}").input_value() == "synthetic-key-for-browser-test"
    assert page.locator(f"#{kind}-wizard .wizard-body").get_attribute("aria-busy") == "false"
    assert page.locator(f"#{next_id}").is_enabled()
    assert page.locator(f"#{cancel_id}").is_enabled()

    # Retry clears stale feedback and preserves the editable connection values.
    next_button.click()
    page.wait_for_function("window.__providerFetchCalls.length === 2")
    assert alert.is_hidden()
    assert page.locator(f"#{key_id}").input_value() == "synthetic-key-for-browser-test"
    inspect_result = {
        "config": {
            "id": f"synthetic-{kind}-draft",
            "label": f"Synthetic {kind.upper()}",
            "provider_display_name": f"Synthetic {kind.upper()}",
            "base_url": "https://provider.example.test",
            "transcribe_path": "/v1/transcribe",
            "setup_status": "pending_model_selection",
            "setup_status_label": "Setup incomplete",
        },
        "available_models": [],
        "available_model_options": [],
        "warnings": [],
        "notes": [],
    }
    if kind == "stt":
        inspect_result["available_models"] = ["synthetic-stt-model"]
        inspect_result["available_model_options"] = [{"id": "synthetic-stt-model"}]
    else:
        inspect_result["discovery_status"] = "manual_required"
    page.evaluate(
        "([result]) => window.__resolveProviderFetch(200, result)",
        [inspect_result],
    )
    page.wait_for_function(
        f"document.querySelector('#{kind}-wizard .wizard-body').getAttribute('aria-busy') === 'false'"
    )
    assert next_button.inner_text() == "Continue"
    assert next_button.is_enabled()
    assert page.locator(f"#{kind}-wizard-status").is_visible()
    assert "Provider check complete." in page.locator(f"#{kind}-wizard-status").inner_text()

    next_button.click()  # Inspection/models -> Defaults.
    if kind == "llm":
        manual_model = page.locator("#llm-manual-model")
        default_model = page.locator("#llm-default-model")
        assert manual_model.is_visible() and manual_model.is_enabled()
        assert default_model.is_disabled()
        manual_model.fill("synthetic-manual-model")
    else:
        model_select = page.locator("#stt-default-model")
        model_select.select_option("model:synthetic-stt-model")

    assert next_button.inner_text() == "Save provider"
    next_button.click()
    page.wait_for_function("window.__providerFetchCalls.length === 3")
    assert next_button.is_disabled()
    assert "Saving provider…" == next_button.inner_text()
    page.evaluate(
        """() => window.__resolveProviderFetch(409, {
          error: { code: "conflict", message: "Synthetic provider conflict." },
        })"""
    )
    alert.wait_for(state="visible")
    assert "Synthetic provider conflict." in alert.inner_text()
    assert next_button.inner_text() == "Save provider"
    assert next_button.is_enabled()
    assert page.locator(f"#{cancel_id}").is_enabled()
    if kind == "llm":
        assert page.locator("#llm-manual-model").is_enabled()
        assert page.locator("#llm-default-model").is_disabled()

    next_button.click()
    page.wait_for_function("window.__providerFetchCalls.length === 4")
    body = page.evaluate("JSON.parse(window.__providerFetchCalls[3].options.body)")
    if kind == "llm":
        assert body["model_name"] == "synthetic-manual-model"
    page.evaluate("() => window.__resolveProviderFetch(200, {})")
    page.wait_for_url(re.compile(rf".*/admin\?team_id={team.id}&team_tab={tab}(?:&.*)?$"))
    notice = page.locator("#provider-save-notice")
    notice.wait_for(state="visible")
    assert notice.get_attribute("role") == "status"
    expected_notice = (
        "Connection saved. Choose the models your team can use."
        if kind == "llm"
        else "STT provider settings saved."
    )
    assert expected_notice in notice.inner_text()
    assert "provider_notice" not in page.url

    # A failed draft deletion leaves the same draft in the open dialog for retry.
    page.evaluate(
        """() => {
          window.__providerFetchCalls = [];
          window.OpenScribeCSRF.fetch = (url, options) => new Promise((resolve) => {
            window.__providerFetchCalls.push({ url, options });
            window.__resolveProviderFetch = (status, payload) => resolve(status === 204
              ? new Response(null, { status })
              : new Response(JSON.stringify(payload), {
                  status, headers: { "Content-Type": "application/json" },
                }));
          });
        }"""
    )
    page.locator(f"#{button_id}").click()
    page.locator(f"#{next_id}").click()
    page.locator(f"#{key_id}").fill("synthetic-key-for-browser-test")
    page.locator(f"#{next_id}").click()
    page.wait_for_function("window.__providerFetchCalls.length === 1")
    page.evaluate(
        "([result]) => window.__resolveProviderFetch(200, result)",
        [inspect_result],
    )
    page.locator(f"#{kind}-wizard .wizard-body").wait_for()
    page.locator(f"#{cancel_id}").click()
    page.wait_for_function("window.__providerFetchCalls.length === 2")
    page.evaluate(
        """() => window.__resolveProviderFetch(503, {
          error: { code: "provider_unavailable", message: "Synthetic discard unavailable." },
        })"""
    )
    alert.wait_for(state="visible")
    assert wizard.is_visible()
    assert page.locator(f"#{kind}-draft-id").input_value() == f"synthetic-{kind}-draft"
    assert page.locator(f"#{cancel_id}").is_enabled()
    page.locator(f"#{cancel_id}").click()
    page.wait_for_function("window.__providerFetchCalls.length === 3")
    page.evaluate("() => window.__resolveProviderFetch(204, null)")
    wizard.wait_for(state="hidden")
