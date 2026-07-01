import socket
import threading

import pytest
from sqlalchemy.orm import sessionmaker

playwright_sync = pytest.importorskip("playwright.sync_api")

from app.db import get_db
from app.main import app
from app.models import TeamRole


@pytest.fixture
def live_server(db_session):
    uvicorn = pytest.importorskip("uvicorn")
    browser_session_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        future=True,
    )

    def override_get_db():
        request_db = browser_session_factory()
        try:
            yield request_db
        finally:
            request_db.close()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()

    app.dependency_overrides[get_db] = override_get_db
    original_session_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = browser_session_factory

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
        csrf_cookie_before = next(
            (cookie["value"] for cookie in context.cookies() if cookie["name"] == "openscribe_csrf"),
            "",
        )
        with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/v1/transcripts/start")) as request_info:
            page.locator("[data-new-session-button]").click()
        request = request_info.value
        response = request.response()

        assert csrf_cookie_before
        if request.headers.get("x-csrf-token") != csrf_cookie_before:
            pytest.fail("Browser sent a CSRF token different from the pre-request session token")
        assert response is not None
        assert response.status == 201
        page.wait_for_url("**/transcribe?transcript_id=*")
        csrf_cookie_after = next(
            (cookie["value"] for cookie in context.cookies() if cookie["name"] == "openscribe_csrf"),
            "",
        )
        if csrf_cookie_after != csrf_cookie_before:
            pytest.fail("Authenticated navigation rotated the per-session CSRF token")
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


def test_dynamic_cssom_mutations_work_under_strict_style_attribute_csp(live_server, make_team, make_user):
    team = make_team(name="Browser Dynamic CSP Clinic")
    make_user(
        email="browser-dynamic-csp@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    make_user(
        email="browser-dynamic-csp-admin@example.com",
        password="password-2",
        is_system_admin=True,
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
        page.locator('form[action="/login"] input[name="email"]').fill("browser-dynamic-csp@example.com")
        page.locator('form[action="/login"] input[name="password"]').fill("password-1")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/home")
        transcribe_response = page.goto("/transcribe")

        assert transcribe_response is not None
        assert "style-src-attr 'none'" in transcribe_response.headers["content-security-policy"]
        transcribe_styles = page.evaluate(
            """
            () => {
              const shell = document.querySelector('[data-workspace-endpoint]');
              const menu = document.createElement('div');
              const textarea = document.querySelector('[data-structured-line-input], [data-freeform-note-input]') || document.createElement('textarea');
              const visualizer = document.querySelector('[data-mic-visualizer]');
              const bar = document.createElement('span');
              const highlight = document.querySelector('[data-tour-highlight]');
              if (!textarea.isConnected) document.body.appendChild(textarea);
              menu.className = 'smart-phrase-menu';
              document.body.appendChild(menu);

              shell.style.setProperty('--split-ratio', '61');
              document.body.style.cursor = 'col-resize';
              document.body.style.userSelect = 'none';
              menu.style.left = '23px';
              menu.style.top = '31px';
              menu.style.width = '260px';
              textarea.style.height = 'auto';
              textarea.style.height = `${Math.max(textarea.scrollHeight, 22)}px`;
              bar.className = 'mic-visualizer__bar';
              bar.style.setProperty('--level', '0.72');
              visualizer.appendChild(bar);
              highlight.style.top = '11px';
              highlight.style.left = '13px';
              highlight.style.width = '150px';
              highlight.style.height = '44px';

              return {
                splitRatio: getComputedStyle(shell).getPropertyValue('--split-ratio').trim(),
                bodyCursor: getComputedStyle(document.body).cursor,
                bodyUserSelect: getComputedStyle(document.body).userSelect,
                menuLeft: getComputedStyle(menu).left,
                menuTop: getComputedStyle(menu).top,
                menuWidth: getComputedStyle(menu).width,
                textareaHeight: getComputedStyle(textarea).height,
                visualizerLevel: getComputedStyle(bar).getPropertyValue('--level').trim(),
                highlightTop: getComputedStyle(highlight).top,
                highlightLeft: getComputedStyle(highlight).left,
                highlightWidth: getComputedStyle(highlight).width,
                highlightHeight: getComputedStyle(highlight).height,
              };
            }
            """
        )

        assert transcribe_styles["splitRatio"] == "61"
        assert transcribe_styles["bodyCursor"] == "col-resize"
        assert transcribe_styles["bodyUserSelect"] == "none"
        assert transcribe_styles["menuLeft"] == "23px"
        assert transcribe_styles["menuTop"] == "31px"
        assert transcribe_styles["menuWidth"] == "260px"
        assert transcribe_styles["textareaHeight"].endswith("px")
        assert transcribe_styles["visualizerLevel"] == "0.72"
        assert transcribe_styles["highlightTop"] == "11px"
        assert transcribe_styles["highlightLeft"] == "13px"
        assert transcribe_styles["highlightWidth"] == "150px"
        assert transcribe_styles["highlightHeight"] == "44px"

        context.clear_cookies()
        page.goto("/login")
        page.locator('form[action="/login"] input[name="email"]').fill("browser-dynamic-csp-admin@example.com")
        page.locator('form[action="/login"] input[name="password"]').fill("password-2")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/admin")
        admin_response = page.goto("/admin")
        assert admin_response is not None
        assert "style-src-attr 'none'" in admin_response.headers["content-security-policy"]
        admin_chart_width = page.evaluate(
            """
            () => {
              const bar = document.querySelector('[data-style-width-pct]') || document.createElement('div');
              if (!bar.isConnected) document.body.appendChild(bar);
              bar.style.width = '37%';
              return getComputedStyle(bar).width;
            }
            """
        )

        assert admin_chart_width != "0px"
        assert csp_errors == []
    finally:
        browser.close()
        playwright.stop()
