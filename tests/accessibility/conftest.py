from __future__ import annotations

import socket
import threading
from urllib.parse import urlparse

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import TeamRole
from tests.accessibility.audit import (
    AuditReport,
    AuditSetupError,
    blocked_request_reason,
    missing_required_states,
    require_axe_bundle,
    same_origin,
    settings_from_environment,
    STATE_LOGIN_KEYBOARD,
    STATE_LOGIN_VALIDATION,
    STATE_ANONYMOUS_LOGIN,
)
from tests.accessibility.redirects import fetch_validated_redirect_chain

SYNTHETIC_AUDIT_STREAM_BODY = ": synthetic accessibility audit stream; no workspace updates\n\n"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--accessibility-audit", action="store_true", default=False, help="Run the isolated Playwright and axe-core accessibility audit.")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "accessibility_audit: opt-in isolated browser accessibility audit")
    config.addinivalue_line("markers", "accessibility_states(expected): required coverage statuses for this audit test")
    if config.getoption("--accessibility-audit") and getattr(config.option, "numprocesses", 0) not in (0, None):
        raise pytest.UsageError("The accessibility audit is serialized. Remove -n/--numprocesses.")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--accessibility-audit"):
        return
    skip = pytest.mark.skip(reason="accessibility audit is opt-in; pass --accessibility-audit")
    for item in items:
        if item.get_closest_marker("accessibility_audit"):
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    setattr(item, f"rep_{outcome.get_result().when}", outcome.get_result())


@pytest.fixture
def live_audit_server(db_session):
    """Use a disposable app/database fixture, never a caller's local service."""
    uvicorn = pytest.importorskip("uvicorn")
    browser_session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False, future=True)

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
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            pytest.fail("Isolated accessibility server failed to start", pytrace=False)
        thread.join(0.01)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.state.db_session_factory = original_session_factory
        app.dependency_overrides.clear()


@pytest.fixture
def synthetic_audit_user(make_team, make_user):
    """A newly created owner has no transcripts or other clinical content."""
    team = make_team(name="Accessibility Audit Team")
    user = make_user(
        email="accessibility-audit@example.test",
        password="synthetic-audit-password",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    return {"email": user.email, "password": "synthetic-audit-password"}


@pytest.fixture
def audit_settings(request, live_audit_server):
    if not request.config.getoption("--accessibility-audit"):
        pytest.skip("accessibility audit is opt-in")
    try:
        return settings_from_environment(base_url=live_audit_server)
    except AuditSetupError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture
def axe_source(request) -> str:
    if not request.config.getoption("--accessibility-audit"):
        pytest.skip("accessibility audit is opt-in")
    try:
        return require_axe_bundle().read_text(encoding="utf-8")
    except AuditSetupError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture
def audit_report(audit_settings, request):
    report = AuditReport(audit_settings, report_name=f"accessibility-audit-{request.node.name}")
    yield report
    report.write()
    expected_marker = request.node.get_closest_marker("accessibility_states")
    if not request.config.getoption("--accessibility-audit") or expected_marker is None:
        return
    expected = expected_marker.args[0] if expected_marker.args else {}
    mismatches = missing_required_states(report.coverage, expected)
    call_report = getattr(request.node, "rep_call", None)
    if mismatches and (call_report is None or call_report.passed or call_report.skipped):
        pytest.fail(
            "Required accessibility state was skipped, unexecuted, or had an unexpected status: "
            + ", ".join(mismatches),
            pytrace=False,
        )


@pytest.fixture
def audit_browser(audit_settings, audit_report):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.fail("Playwright is unavailable. Install the pinned requirements-dev.txt dependencies.", pytrace=False)
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:
        playwright.stop()
        pytest.fail(f"Playwright Chromium is unavailable: {exc}", pytrace=False)
    context = browser.new_context(base_url=audit_settings.base_url, bypass_csp=True, service_workers="block")
    blocked: list[str] = []
    closing = False

    def route_request(route):
        try:
            url = route.request.url
            if not same_origin(url, audit_settings.base_url):
                blocked.append(blocked_request_reason(url))
                route.abort("blockedbyclient")
                return
            # Fetch without following redirects. Resolve only same-origin redirects
            # here, then fulfil the final response. Letting Chromium receive a 30x
            # response would permit it to follow an external Location before a
            # route handler sees that transport request.
            if urlparse(url).path == "/api/v1/transcribe/workspace/stream":
                route.fulfill(status=200, content_type="text/event-stream", body=SYNTHETIC_AUDIT_STREAM_BODY)
                return
            # route.fetch() does not commit cookies until fulfill. The API
            # request client shares this context's cookie jar and lets each
            # validated same-origin hop observe Set-Cookie immediately.
            response, blocked_reason = fetch_validated_redirect_chain(
                context.request,
                url,
                audit_settings.base_url,
                route.request.method,
                route.request.headers,
                route.request.post_data_buffer,
            )
            if blocked_reason:
                blocked.append(blocked_reason)
                route.abort("blockedbyclient")
                return
            route.fulfill(response=response)
        except Exception:
            if not closing:
                raise

    context.route("**/*", route_request)
    page = context.new_page()
    try:
        yield page, blocked
    finally:
        closing = True
        try:
            context.close()
        finally:
            try:
                browser.close()
            finally:
                playwright.stop()
    if blocked:
        audit_report.external_requests.extend(blocked)
        pytest.fail("A non-audit browser request was blocked; the affected state is incomplete.", pytrace=False)


def _verify_authenticated_workspace(page, report) -> None:
    response = page.goto("/workspace", wait_until="domcontentloaded")
    if response is None or not response.ok:
        raise RuntimeError("workspace response was unsuccessful")
    page.locator("[data-workspace-shell]").wait_for(state="visible", timeout=5_000)
    if page.locator('form[action="/login"]').count():
        raise RuntimeError("workspace rendered a login form")


def verify_keyboard_login(page, report) -> None:
    try:
        _verify_authenticated_workspace(page, report)
    except Exception:
        report.record_coverage(STATE_LOGIN_KEYBOARD, "failed", "synthetic normal login did not open authenticated workspace")
        pytest.fail("Synthetic normal login session did not open the authenticated workspace.", pytrace=False)


def navigate(page, path: str, report, state: str):
    try:
        response = page.goto(path, wait_until="domcontentloaded")
        if response is None or not response.ok:
            raise RuntimeError("navigation response was unsuccessful")
        return response
    except Exception:
        report.record_coverage(state, "failed", "navigation failed")
        raise


def login(page, user, report) -> None:
    response = page.goto("/login", wait_until="domcontentloaded")
    if response is None or not response.ok:
        report.record_coverage(STATE_LOGIN_KEYBOARD, "failed", "synthetic login page did not load")
        pytest.fail("Isolated login page did not load.", pytrace=False)
    page.locator('form[action="/login"] input[name="email"]').fill(user["email"])
    page.locator('form[action="/login"] input[name="password"]').fill(user["password"])
    with page.expect_navigation(wait_until="domcontentloaded", timeout=5_000):
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/login"):
            page.get_by_role("button", name="Sign in", exact=True).click()
    verify_keyboard_login(page, report)


def scan(page, report, state: str, axe_source: str, blocked: list[str]) -> dict:
    try:
        if state not in {STATE_ANONYMOUS_LOGIN, STATE_LOGIN_VALIDATION}:
            page.locator("[data-workspace-shell], [data-workspace-scribe-main]").first.wait_for(
                state="visible", timeout=5_000
            )
            if page.locator('form[action="/login"]').count():
                raise RuntimeError("authenticated state rendered the login form")
        if not page.evaluate("() => Boolean(window.axe)"):
            page.add_script_tag(content=axe_source)
        audit = page.evaluate(
        """async (tags) => ({
          supportedRuleCounts: Object.fromEntries(tags.map((tag) => [tag, window.axe.getRules([tag]).length])),
          result: await window.axe.run(document, {runOnly: {type: 'tag', values: tags}}),
        })""",
        ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
        )
    except Exception:
        report.record_coverage(state, "failed", "axe scan execution failed")
        raise
    if not any(audit["supportedRuleCounts"].values()):
        report.record_coverage(state, "failed", "local axe bundle has no requested WCAG rules")
        pytest.fail("Local axe bundle has no requested WCAG rules.", pytrace=False)
    report.record_scan(state, audit["result"], external_requests=list(blocked), supported_rule_counts=audit["supportedRuleCounts"])
    if blocked:
        report.record_coverage(state, "incomplete", "non-audit request blocked")
        pytest.fail("A non-audit browser request was blocked; this state is incomplete.", pytrace=False)
    return audit["result"]
