import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

playwright_sync = pytest.importorskip("playwright.sync_api")
expect = playwright_sync.expect

pytestmark = pytest.mark.real_db_connections

from app.db import get_db
from app.main import app
from app.models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    TeamRole,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)


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
        page.wait_for_url("**/workspace")

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
        page.wait_for_url("**/workspace?transcript_id=*")
        csrf_cookie_after = next(
            (cookie["value"] for cookie in context.cookies() if cookie["name"] == "openscribe_csrf"),
            "",
        )
        if csrf_cookie_after != csrf_cookie_before:
            pytest.fail("Authenticated navigation rotated the per-session CSRF token")
    finally:
        browser.close()
        playwright.stop()


def test_followup_context_accepts_typed_input_when_generation_is_available(
    client,
    live_server,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    """The steering field must remain a usable text control, not just look enabled."""
    team = make_team(name="Browser Follow-up Context Clinic")
    admin = make_user(
        email="browser-followup-context-admin@example.com",
        password="password-1",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    leader = make_user(
        email="browser-followup-context-leader@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.leader,
        mfa_required=False,
        mfa_enabled=False,
    )
    member = make_user(
        email="browser-followup-context-member@example.com",
        password="password-3",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Browser Follow-up LLM",
        model_name="gpt-4o-mini",
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")

    client.post(
        "/login",
        data={"email": member.email, "password": "password-3"},
        follow_redirects=False,
    )
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Browser follow-up context",
            "ingestion_mode": "whole_file",
            "current_draft_text_encrypted": "",
        },
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    working_note_response = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Synthetic working note."},
    )
    assert working_note_response.status_code == 200

    try:
        playwright = playwright_sync.sync_playwright().start()
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Playwright browser unavailable: {exc}")

    try:
        context = browser.new_context(base_url=live_server)
        page = context.new_page()
        page.goto("/login")
        page.locator('form[action="/login"] input[name="email"]').fill(member.email)
        page.locator('form[action="/login"] input[name="password"]').fill("password-3")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/workspace")

        page.goto(f"/transcribe?transcript_id={transcript_id}")
        page.get_by_role("tab", name="Follow Ups").click()
        steering = page.locator("[data-quick-action-context-input]")

        assert steering.is_enabled()
        steering.click()
        assert steering.evaluate("element => document.activeElement === element")
        page.keyboard.type("Use a concise tone.")
        assert steering.input_value() == "Use a concise tone."
    finally:
        browser.close()
        playwright.stop()


def test_followup_history_menu_paints_above_the_workspace(
    live_server,
    db_session,
    make_team,
    make_user,
):
    """The active Follow Ups menu must not be hidden behind the output pane."""
    team = make_team(name="Browser Follow-up History Menu Clinic")
    member = make_user(
        email="browser-followup-history-menu@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Browser follow-up history menu",
        current_draft_text_encrypted="Synthetic transcript.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(transcript_version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=member.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            generator_type=GeneratedDocumentGeneratorType.followup,
            source_template_name="Follow-up",
            status=GeneratedDocumentStatus.ready,
            title="Custom follow-up",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="Synthetic follow-up.",
            edited_output_text_encrypted="Synthetic follow-up.",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()

    try:
        playwright = playwright_sync.sync_playwright().start()
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Playwright browser unavailable: {exc}")

    try:
        context = browser.new_context(base_url=live_server, viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto("/login")
        page.locator('form[action="/login"] input[name="email"]').fill(member.email)
        page.locator('form[action="/login"] input[name="password"]').fill("password-1")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/workspace")

        page.goto(f"/transcribe?transcript_id={transcript.id}&tab=followups")
        page.get_by_role("tab", name="Follow Ups").click()

        menu_trigger = page.locator("[data-followup-history-menu] > summary")
        menu_trigger.click()
        menu = page.locator('[data-followup-history-menu][open] > [role="menu"]')
        expect(menu).to_be_visible()

        # Checking the viewport alone misses stacking-context bugs: a hidden menu
        # can retain a valid layout box. Hit testing its centre proves it paints
        # above the output pane and receives pointer interaction.
        menu_box = menu.bounding_box()
        assert menu_box is not None
        assert menu_box["width"] > 0
        assert menu_box["height"] > 0
        assert menu_box["x"] >= 0
        assert menu_box["y"] >= 0
        assert menu_box["x"] + menu_box["width"] <= page.viewport_size["width"]
        assert menu_box["y"] + menu_box["height"] <= page.viewport_size["height"]
        assert menu.evaluate(
            """
            (element) => {
              const rect = element.getBoundingClientRect();
              const topmost = document.elementFromPoint(
                rect.left + (rect.width / 2),
                rect.top + (rect.height / 2),
              );
              return topmost === element || element.contains(topmost);
            }
            """
        )

        # Retain the menu's keyboard and screen-reader semantics as part of the
        # same regression contract.
        expect(menu).to_have_attribute("role", "menu")
        expect(menu.locator('[data-followup-copy]')).to_have_attribute("role", "menuitem")
        expect(menu.locator('[data-followup-delete]')).to_have_attribute("role", "menuitem")
        page.keyboard.press("Escape")
        expect(menu_trigger).to_be_focused()
        expect(menu).to_be_hidden()
    finally:
        browser.close()
        playwright.stop()


def test_workspace_styles_apply_under_strict_style_attribute_csp(live_server, make_team, make_user):
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
        page.wait_for_url("**/workspace")

        response = page.goto("/workspace")
        assert response is not None
        assert "style-src-attr 'none'" in response.headers["content-security-policy"]
        assert page.locator("[data-workspace-scribe-main]").evaluate(
            "element => getComputedStyle(element).display"
        ) == "flex"
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
        page.wait_for_url("**/workspace")
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
