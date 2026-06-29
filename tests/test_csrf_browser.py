import socket
import threading

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from app.db import get_db
from app.main import app
from app.models import TeamRole
from tests.conftest import TestingSessionLocal


@pytest.fixture
def live_server(db_session):
    uvicorn = pytest.importorskip("uvicorn")

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()

    app.dependency_overrides[get_db] = override_get_db
    original_session_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = TestingSessionLocal

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            pytest.fail("Browser test server failed to start")
        thread.join(0.01)

    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.state.db_session_factory = original_session_factory
        app.dependency_overrides.clear()


def test_browser_transcribe_start_sends_csrf_header(live_server, make_team, make_user):
    team = make_team(name="Browser CSRF Clinic")
    make_user(
        email="browser-csrf@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )

    try:
        playwright = playwright_sync.sync_playwright().start()
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Playwright browser unavailable: {exc}")

    try:
        context = browser.new_context(base_url=live_server)
        page = context.new_page()
        page.goto("/login")
        page.locator('form[action="/login"] input[name="email"]').fill("browser-csrf@example.com")
        page.locator('form[action="/login"] input[name="password"]').fill("password-1")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/home")

        page.goto("/transcribe")
        with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/v1/transcripts/start")) as request_info:
            page.locator("[data-new-session-button]").click()
        request = request_info.value
        response = request.response()
        csrf_cookie = next((cookie["value"] for cookie in context.cookies() if cookie["name"] == "openscribe_csrf"), "")

        assert csrf_cookie
        assert request.headers.get("x-csrf-token") == csrf_cookie
        assert response is not None
        assert response.status != 403
    finally:
        browser.close()
        playwright.stop()


def test_home_styles_apply_under_strict_style_attribute_csp(live_server, make_team, make_user):
    team = make_team(name="Browser CSP Clinic")
    make_user(
        email="browser-csp@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )

    try:
        playwright = playwright_sync.sync_playwright().start()
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Playwright browser unavailable: {exc}")

    try:
        context = browser.new_context(base_url=live_server)
        page = context.new_page()
        csp_errors = []
        page.on(
            "console",
            lambda message: csp_errors.append(message.text)
            if "content security policy" in message.text.lower()
            else None,
        )

        page.goto("/login")
        page.locator('form[action="/login"] input[name="email"]').fill("browser-csp@example.com")
        page.locator('form[action="/login"] input[name="password"]').fill("password-1")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/home")

        response = page.goto("/home")
        assert response is not None
        assert "style-src-attr 'none'" in response.headers["content-security-policy"]
        assert page.locator(".overview-copy").first.evaluate("element => getComputedStyle(element).marginTop") == "8px"
        assert page.locator(".overview-primary-action").evaluate("element => getComputedStyle(element).marginTop") == "18px"
        assert csp_errors == []
    finally:
        browser.close()
        playwright.stop()
