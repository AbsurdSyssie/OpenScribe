"""Disposable browser coverage for the consultation-splitting lifecycle.

The worker calls here are deliberate: the browser queues real outbox-backed
work, while this test process drives the normal runtime entry points instead
of requiring Celery, a broker, Vault, or a provider.
"""

import json
import socket
import threading
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker


playwright_sync = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.real_db_connections

from app.db import get_db
from app.main import app
from app.routes import api_routes
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitBatch,
    ConsultationSplitDraft,
    ConsultationSplitExecution,
    ConsultationSplitIntent,
    GeneratedDocument,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    TeamRole,
    User,
    UserAppPreference,
    utcnow,
)
from app.services.consultation_split_generation_runtime import (
    process_consultation_split_generation_execution,
)
from app.services.consultation_split_intent_progress import progress_consultation_split_intent
from app.services.consultation_split_runtime import (
    process_consultation_split_analysis_execution,
)
from app.services.consultation_splits import read_split_batch_json
from app.services.dictations import update_post_consultation_dictation
from app.services.transcripts import set_freeform_working_note_text, set_transcript_draft_text


@pytest.fixture
def live_split_server(db_session):
    """Serve the app against this test's disposable, committed database."""
    uvicorn = pytest.importorskip("uvicorn")
    browser_sessions = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False, future=True
    )

    def override_get_db():
        request_db = browser_sessions()
        try:
            yield request_db
        finally:
            request_db.close()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()

    app.dependency_overrides[get_db] = override_get_db
    original_session_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = browser_sessions
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            pytest.fail("Consultation-split browser server failed to start")
        thread.join(0.01)
    try:
        yield f"http://{host}:{port}", browser_sessions
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.state.db_session_factory = original_session_factory
        app.dependency_overrides.clear()


@pytest.fixture
def split_browser(live_split_server):
    base_url, sessions = live_split_server
    try:
        playwright = playwright_sync.sync_playwright().start()
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Playwright browser unavailable: {exc}")
    try:
        context = browser.new_context(base_url=base_url)
        # The production shell uses SSE when available.  Replace it only in
        # this browser before page load with a failed connection so production
        # code takes its normal HTTP fallback path; no stream or app setting is
        # changed.
        context.add_init_script("""
          window.EventSource = class {
            static OPEN = 1;
            constructor() { this.readyState = 2; queueMicrotask(() => this.onerror?.(new Event('error'))); }
            addEventListener() {}
            close() {}
          };
        """)
        yield context, sessions
    finally:
        browser.close()
        playwright.stop()


def _create_consultation(db, owner, *, title, source_only=None):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title=title,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    if source_only is None:
        set_transcript_draft_text(db, transcript=transcript, plaintext="Synthetic test transcript.")
    if source_only != "dictation":
        set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic test consultation.")
        transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    if source_only == "dictation":
        update_post_consultation_dictation(
            db,
            owner,
            transcript_id=transcript.id,
            combined_text="Synthetic saved dictation.",
        )
    db.commit()
    return transcript.id


def _execution_id(sessions, transcript_id, kind):
    with sessions() as db:
        return db.scalar(
            select(ConsultationSplitExecution.id)
            .where(
                ConsultationSplitExecution.transcript_id == transcript_id,
                ConsultationSplitExecution.kind == kind,
            )
            .order_by(ConsultationSplitExecution.created_at.desc())
        )


def _run_analysis(sessions, execution_id):
    with sessions() as db:
        result = process_consultation_split_analysis_execution(db, execution_id=execution_id)
        db.commit()
        return result


def _run_generation(sessions, execution_id):
    with sessions() as db:
        result = process_consultation_split_generation_execution(db, execution_id=execution_id)
        db.commit()
        return result


def _progress_intent(sessions, intent_id):
    with sessions() as db:
        result = progress_consultation_split_intent(db, intent_id=intent_id)
        db.commit()
        return result


def _generation_response(sessions, execution_id, *, omit_last=False):
    with sessions() as db:
        execution = db.get(ConsultationSplitExecution, execution_id)
        batch = db.get(ConsultationSplitBatch, execution.batch_id)
        owner = db.get(User, execution.owner_user_id)
        plan = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted")
        notes = []
        for topic in plan["topics"]:
            if topic["disposition"] != "separate_note":
                continue
            template = topic["template"]
            mode = template["mode"]
            content = (
                "Synthetic generated note."
                if mode == "freeform"
                else {
                    section["section_key"]: "Synthetic generated section."
                    for section in template["structured_sections"]["sections"]
                }
            )
            notes.append({"topic_uuid": topic["topic_uuid"], "mode": mode, "content": content})
    return json.dumps({"title": "Overall consultation", "notes": notes[:-1] if omit_last else notes})


def _login(page, email):
    page.goto("/login")
    page.locator('form[action="/login"] input[name="email"]').fill(email)
    page.locator('form[action="/login"] input[name="password"]').fill("password-1")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/workspace")
    notice = page.locator("[data-browser-storage-notice]")
    if notice.is_visible():
        notice.get_by_role("button", name="Dismiss").click()


def _start_review(page, transcript_id):
    page.goto(f"/transcribe?transcript_id={transcript_id}")
    with page.expect_response(lambda response: "/consultation-split-intents" in response.url) as response_info:
        page.get_by_role("button", name="Create", exact=True).click()
    assert response_info.value.status in {200, 202}


def _confirm_review(page):
    template_select = page.locator("[data-split-review-template]").first
    template_select.select_option(template_select.input_value())
    with page.expect_response(lambda response: "/consultation-split-draft" in response.url and response.request.method == "PUT") as response_info:
        page.get_by_role("button", name="Save split").click()
    assert response_info.value.status == 200
    with page.expect_response(lambda response: response.url.endswith("/consultation-split-draft/confirm")) as response_info:
        page.locator("[data-split-review-create]").click()
    assert response_info.value.status == 202


def _install_local_adapters(monkeypatch, *, analysis_response, generation_response):
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)
    monkeypatch.setattr("app.services.task_outbox.try_publish_task_dispatch_safely", lambda *_: None)
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "test-token")
    monkeypatch.setattr(
        "app.services.consultation_split_runtime.llm_runtime",
        SimpleNamespace(invoke_llm=lambda **_kwargs: (analysis_response, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_generation_runtime.resolve_generation_credential",
        lambda _config: "test-token",
    )
    monkeypatch.setattr(
        "app.services.consultation_split_generation_runtime.llm_runtime",
        SimpleNamespace(invoke_llm=lambda **_kwargs: (generation_response(), {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})),
    )


def _prepare_owner(db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template):
    team = make_team(name="Synthetic split browser clinic")
    owner = make_user(email="split-browser@example.com", password="password-1", team=team, mfa_required=False, mfa_enabled=False)
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": True}))
    config = make_llm_config(team=team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    template = make_template(owner=owner, actor=owner, name="Synthetic browser template")
    db_session.commit()
    return owner, template


def test_browser_team_leader_split_preference_persists_across_workspace_navigation(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    monkeypatch,
):
    """A leader's enabled split preference survives leaving and returning to Scribe."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, _ = split_browser
    team = make_team(name="Synthetic split preference leader clinic")
    leader = make_user(
        email="split-preference-leader@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
        mfa_required=False,
        mfa_enabled=False,
    )
    config = make_llm_config(
        team=team,
        actor=leader,
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(config=config, actor=leader, allowed_models_json=["gpt-4o-mini"])
    db_session.commit()

    page = context.new_page()
    try:
        _login(page, leader.email)
        page.goto("/workspace/preferences")
        split_toggle = page.get_by_role(
            "checkbox", name="Split a consultation into separate notes"
        )
        assert not split_toggle.is_checked()

        with page.expect_navigation(url="**/workspace/preferences"):
            page.locator('[data-consultation-splitting-preference-form] label').click()

        page.goto("/workspace")
        page.goto("/workspace/preferences")
        assert page.get_by_role(
            "checkbox", name="Split a consultation into separate notes"
        ).is_checked()
    finally:
        page.close()
        context.close()


def test_browser_split_happy_path_creates_bundled_drafts_and_ignores_a_switched_transcript(
    split_browser, db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    """Rendered JS intercepts Create, reviews, confirms, and renders two local drafts."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, template = _prepare_owner(db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template)
    first_id = _create_consultation(db_session, owner, title="Synthetic first consultation")
    second_id = _create_consultation(db_session, owner, title="Synthetic second consultation")
    analysis_response = json.dumps({"topics": [
        {"title": "Synthetic primary", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
        {"title": "Synthetic secondary", "is_primary": False, "disposition": "separate_note", "template_id": str(template.id)},
    ]})
    _install_local_adapters(
        monkeypatch, analysis_response=analysis_response,
        generation_response=lambda: _generation_response(sessions, _execution_id(sessions, first_id, "generation")),
    )
    monkeypatch.setattr("app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches", lambda *_args, **_kwargs: True)
    page = context.new_page()
    try:
        _login(page, owner.email)
        _start_review(page, first_id)
        analysis_id = _execution_id(sessions, first_id, "analysis")
        analysis_result = _run_analysis(sessions, analysis_id)
        assert analysis_result.outcome == "ready", analysis_result.error_code
        # Re-enter through the rendered Generate control after the queued
        # request has completed.  This is a normal idempotent replay and gives
        # the browser a fresh operation after the local worker transition.
        _start_review(page, first_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=8_000)
        _confirm_review(page)
        generation_id = _execution_id(sessions, first_id, "generation")
        assert _run_generation(sessions, generation_id).outcome == "ready"
        page.reload()
        latest_output = page.locator('[data-latest-generated-output][data-latest-generated-status="ready"]')
        latest_output.wait_for(state="attached", timeout=6_000)
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == first_id)) == 2
        # Let a second queued result finish after the rendered browser has
        # selected the first consultation. Its review must not bleed across.
        _start_review(page, second_id)
        second_analysis_id = _execution_id(sessions, second_id, "analysis")
        page.goto(f"/transcribe?transcript_id={first_id}")
        assert _run_analysis(sessions, second_analysis_id).outcome == "ready"
        page.wait_for_timeout(2_000)  # The browser's split fallback starts at 1.5 s.
        assert page.locator("[data-split-review-modal]").is_hidden()
    finally:
        page.close()
        context.close()


@pytest.mark.parametrize("split_enabled", [False, True], ids=["ordinary", "split-single-issue"])
def test_browser_create_waits_for_durable_acceptance_before_history_switch(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
    split_enabled,
):
    """Create reaches its server-owned durable state before consultation B opens."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true" if split_enabled else "false")
    context, sessions = split_browser
    owner, _template = _prepare_owner(
        db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template
    )
    first_id = _create_consultation(db_session, owner, title="Durable Create consultation A")
    second_id = _create_consultation(db_session, owner, title="History consultation B")
    if split_enabled:
        _install_local_adapters(
            monkeypatch,
            analysis_response=json.dumps({"topics": []}),
            generation_response=lambda: json.dumps({"title": "Synthetic", "content": "Synthetic note."}),
        )

    accepted = threading.Event()
    release_response = threading.Event()
    accepted_state = {}
    service_name = (
        "create_or_replay_consultation_split_intent"
        if split_enabled
        else "queue_document_generation_from_template_service"
    )
    original_service = getattr(api_routes, service_name)

    def hold_response_after_acceptance(*args, **kwargs):
        result = original_service(*args, **kwargs)
        if split_enabled:
            accepted_state["intent_id"] = result.intent.id
        else:
            accepted_state["document_id"] = result.id
        accepted.set()
        if not release_response.wait(timeout=8):
            raise RuntimeError("Synthetic Create response was not released")
        return result

    monkeypatch.setattr(api_routes, service_name, hold_response_after_acceptance)
    page = context.new_page()
    create_responses = []
    script_errors = []
    page.on("pageerror", lambda error: script_errors.append(str(error)))
    page.on("response", lambda response: create_responses.append(response.status)
            if "consultation-split-intents" in response.url else None)
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={first_id}")
        page.get_by_role("button", name="Create", exact=True).click()
        assert accepted.wait(timeout=5), "Create did not reach durable server acceptance"

        page.get_by_role("button", name="Recent consultations", exact=True).click()
        page.locator(f'[data-session-link][data-transcript-id="{second_id}"]').click()
        page.wait_for_timeout(150)
        assert f"transcript_id={first_id}" in page.url
        assert page.locator("[data-split-review-modal]").is_hidden()

        release_response.set()
        try:
            page.wait_for_url(f"**/*transcript_id={second_id}*", timeout=6_000)
        except playwright_sync.TimeoutError:
            pytest.fail(f"History switch did not finish; Create statuses: {create_responses}; script errors: {script_errors}")
        assert page.locator("[data-split-review-modal]").is_hidden()

        if split_enabled:
            analysis_id = _execution_id(sessions, first_id, "analysis")
            assert _run_analysis(sessions, analysis_id).outcome == "not_required"
            progress = _progress_intent(sessions, accepted_state["intent_id"])
            assert progress.outcome == "continued_as_one_note"

        with sessions() as db:
            first_documents = db.scalar(
                select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == first_id)
            )
            second_documents = db.scalar(
                select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == second_id)
            )
            assert first_documents == 1
            assert second_documents == 0
    finally:
        release_response.set()
        page.close()
        context.close()


def test_browser_ready_split_completed_while_switched_restores_passive_confirmable_review(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """A server-progressed review survives navigation without opening over consultation B."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, template = _prepare_owner(
        db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template
    )
    first_id = _create_consultation(db_session, owner, title="Durable split consultation A")
    second_id = _create_consultation(db_session, owner, title="Visible consultation B")
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": [
            {"title": "Synthetic primary", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
            {"title": "Synthetic secondary", "is_primary": False, "disposition": "separate_note", "template_id": str(template.id)},
        ]}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "notes": []}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        _start_review(page, first_id)
        with sessions() as db:
            intent_id = db.scalar(
                select(ConsultationSplitIntent.id)
                .where(ConsultationSplitIntent.transcript_id == first_id)
                .order_by(ConsultationSplitIntent.created_at.desc())
            )
        page.get_by_role("button", name="Recent consultations", exact=True).click()
        page.locator(f'[data-session-link][data-transcript-id="{second_id}"]').click()
        page.wait_for_url(f"**/*transcript_id={second_id}*", timeout=6_000)

        analysis_id = _execution_id(sessions, first_id, "analysis")
        assert _run_analysis(sessions, analysis_id).outcome == "ready"
        assert _progress_intent(sessions, intent_id).outcome == "review_ready"
        page.reload()
        assert page.locator("[data-split-review-modal]").is_hidden()

        with sessions() as db:
            draft = db.scalar(select(ConsultationSplitDraft).where(ConsultationSplitDraft.transcript_id == first_id))
            assert draft is not None
            assert db.scalar(
                select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == second_id)
            ) == 0

        page.goto(f"/transcribe?transcript_id={first_id}")
        trigger = page.locator("[data-split-review-trigger]")
        trigger.wait_for(state="visible", timeout=6_000)
        assert page.locator("[data-split-review-modal]").is_hidden()
        trigger.click()
        page.locator("[data-split-review-modal]").wait_for(state="visible")
        assert page.locator("[data-split-review-create]").is_enabled()
    finally:
        page.close()
        context.close()


@pytest.mark.parametrize("source_only", ["working_note", "dictation"])
def test_browser_split_source_only_materializes_drafts_with_batch_bound_empty_version(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
    source_only,
):
    """Rendered Generate supports a Working note or dictation as its only source."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, template = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    transcript_id = _create_consultation(
        db_session,
        owner,
        title=f"Synthetic {source_only}-only consultation",
        source_only=source_only,
    )
    analysis_response = json.dumps({"topics": [
        {"title": "Synthetic primary", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
        {"title": "Synthetic secondary", "is_primary": False, "disposition": "separate_note", "template_id": str(template.id)},
    ]})
    _install_local_adapters(
        monkeypatch,
        analysis_response=analysis_response,
        generation_response=lambda: _generation_response(
            sessions,
            _execution_id(sessions, transcript_id, "generation"),
        ),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches",
        lambda *_args, **_kwargs: True,
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        _start_review(page, transcript_id)
        analysis_id = _execution_id(sessions, transcript_id, "analysis")
        assert _run_analysis(sessions, analysis_id).outcome == "ready"

        # Re-enter through the rendered Generate control after the local
        # analysis worker transition, matching the normal idempotent browser
        # restoration path.
        _start_review(page, transcript_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=8_000)
        _confirm_review(page)
        generation_id = _execution_id(sessions, transcript_id, "generation")
        assert _run_generation(sessions, generation_id).outcome == "ready"
        page.reload()
        page.locator('[data-latest-generated-output][data-latest-generated-status="ready"]').wait_for(
            state="attached",
            timeout=6_000,
        )

        with sessions() as db:
            execution = db.get(ConsultationSplitExecution, generation_id)
            assert execution is not None and execution.batch_id is not None
            batch = db.get(ConsultationSplitBatch, execution.batch_id)
            assert batch is not None and batch.materialization_transcript_version_id is not None
            analysis = db.get(ConsultationSplitAnalysis, batch.analysis_id)
            assert analysis is not None
            assert analysis.transcript_version_id is None
            assert analysis.redaction_run_id is None
            documents = db.scalars(
                select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)
            ).all()
            assert len(documents) == 2
            assert {document.transcript_version_id for document in documents} == {
                batch.materialization_transcript_version_id
            }
    finally:
        page.close()
        context.close()


def test_browser_dictation_save_generate_closes_only_after_split_intent_acceptance(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """Dictation closes before a successful split review can open, but stays open on rejection."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, _ = split_browser
    owner, _ = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    accepted_id = _create_consultation(
        db_session, owner, title="Accepted dictation request", source_only="dictation"
    )
    rejected_id = _create_consultation(
        db_session, owner, title="Rejected dictation request", source_only="dictation"
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={accepted_id}")
        page.locator("[data-dictation-cta]").click()
        modal = page.locator("[data-dictation-modal]")
        modal.wait_for(state="visible")
        with page.expect_response(lambda response: "/consultation-split-intents" in response.url) as response_info:
            modal.locator("[data-dictation-save-generate]").click()
        assert response_info.value.status in {200, 202}
        modal.wait_for(state="hidden")

        page.goto(f"/transcribe?transcript_id={rejected_id}")
        page.route(
            "**/consultation-split-intents",
            lambda route: route.fulfill(
                status=400,
                content_type="application/json",
                body='{"detail":"Synthetic rejection."}',
            ),
        )
        page.locator("[data-dictation-cta]").click()
        modal = page.locator("[data-dictation-modal]")
        modal.wait_for(state="visible")
        with page.expect_response(lambda response: "/consultation-split-intents" in response.url) as response_info:
            modal.locator("[data-dictation-save-generate]").click()
        assert response_info.value.status == 400
        assert modal.is_visible()
    finally:
        page.close()
        context.close()


def test_browser_dictation_save_generate_continues_one_note_after_split_analysis(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """A dictation request queues its frozen one-note generation without another Create click."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, _ = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    transcript_id = _create_consultation(
        db_session, owner, title="One-note dictation request", source_only="dictation"
    )
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": []}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "content": "Synthetic note."}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        page.locator("[data-dictation-cta]").click()
        modal = page.locator("[data-dictation-modal]")
        modal.wait_for(state="visible")
        with page.expect_response(lambda response: "/consultation-split-intents" in response.url) as response_info:
            modal.locator("[data-dictation-save-generate]").click()
        assert response_info.value.status in {200, 202}
        modal.wait_for(state="hidden")

        with page.expect_response(
            lambda response: response.url.endswith("/continue-as-one-note")
        ) as response_info:
            analysis_id = _execution_id(sessions, transcript_id, "analysis")
            assert _run_analysis(sessions, analysis_id).outcome == "not_required"
        assert response_info.value.status in {200, 202}
        document_id = response_info.value.json()["document"]["id"]
        with sessions() as db:
            document = db.get(GeneratedDocument, UUID(document_id))
            assert document is not None
            assert document.transcript_id == transcript_id
            assert document.owner_user_id == owner.id
            assert document.status.value == "queued"
    finally:
        page.close()
        context.close()


def test_browser_multiple_problems_mark_persists_without_starting_generation(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """The toolbar records a clinician request but does not generate a note."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, _sessions = split_browser
    owner, _ = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == owner.id))
    preference.preferences_json = {"split_consultations_into_separate_notes": False}
    db_session.commit()
    transcript_id = _create_consultation(
        db_session, owner, title="Marked synthetic consultation", source_only="dictation"
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        toggle = page.locator("[data-multiple-problems-toggle]")
        toggle.wait_for(state="visible")
        assert toggle.get_attribute("aria-pressed") == "false"
        with page.expect_response(
            lambda response: response.url.endswith(f"/api/v1/transcripts/{transcript_id}")
            and response.request.method == "PATCH"
        ) as response_info:
            toggle.click()
        assert response_info.value.status == 200
        assert response_info.value.request.post_data_json == {"multiple_problems": True}
        assert toggle.get_attribute("aria-pressed") == "true"
        page.reload()
        toggle.wait_for(state="visible")
        assert toggle.get_attribute("aria-pressed") == "true"
        assert not page.locator("[data-split-review-modal]").is_visible()
    finally:
        page.close()
        context.close()


def test_browser_marked_single_problem_opens_review_without_generating(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """A persisted manual request keeps a one-problem result in clinician review."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, template = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == owner.id))
    preference.preferences_json = {"split_consultations_into_separate_notes": False}
    db_session.commit()
    transcript_id = _create_consultation(
        db_session, owner, title="Manual one-problem request", source_only="dictation"
    )
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": [
            {"title": "Synthetic issue", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
        ]}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "content": "Synthetic note."}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        with page.expect_response(
            lambda response: response.url.endswith(f"/api/v1/transcripts/{transcript_id}")
            and response.request.method == "PATCH"
        ):
            page.locator("[data-multiple-problems-toggle]").click()
        with page.expect_response(lambda response: "/consultation-split-intents" in response.url) as response_info:
            page.locator("[data-generate-output-form] button[type=submit]").click()
        assert response_info.value.status in {200, 202}
        analysis_id = _execution_id(sessions, transcript_id, "analysis")
        assert _run_analysis(sessions, analysis_id).outcome == "not_required"
        review = page.locator("[data-split-review-modal]")
        review.wait_for(state="visible", timeout=8_000)
        assert "One problem was detected" in review.locator("[data-split-review-status]").inner_text()
        assert review.locator("[data-split-review-add]").is_visible()
        assert review.get_by_role("button", name="Continue as one note", exact=True).is_visible()
        with sessions() as db:
            assert db.scalar(
                select(func.count()).select_from(GeneratedDocument).where(
                    GeneratedDocument.transcript_id == transcript_id
                )
            ) == 0
    finally:
        page.close()
        context.close()


def test_browser_dictation_save_generate_starts_ordinary_note_when_splitting_is_off(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """Dictation save-and-generate retains the ordinary template generation path."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")
    context, sessions = split_browser
    owner, _ = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    transcript_id = _create_consultation(
        db_session, owner, title="Ordinary dictation request", source_only="dictation"
    )
    monkeypatch.setattr("app.services.task_outbox.try_publish_task_dispatch_safely", lambda *_: None)
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        page.locator("[data-dictation-cta]").click()
        modal = page.locator("[data-dictation-modal]")
        modal.wait_for(state="visible")
        with page.expect_response(lambda response: response.url.endswith("/generate-output")) as response_info:
            modal.locator("[data-dictation-save-generate]").click()
        assert response_info.value.status == 202
        modal.wait_for(state="hidden")
        with sessions() as db:
            document = db.get(GeneratedDocument, UUID(response_info.value.json()["id"]))
            assert document is not None
            assert document.transcript_id == transcript_id
            assert document.owner_user_id == owner.id
            assert document.status.value == "queued"
    finally:
        page.close()
        context.close()


def test_browser_dictation_save_generate_opens_multi_problem_review_after_split_analysis(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """A dictation request reaches review when splitting finds multiple notes."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, template = _prepare_owner(
        db_session,
        make_team,
        make_user,
        make_llm_config,
        make_llm_selection,
        make_template,
    )
    transcript_id = _create_consultation(
        db_session, owner, title="Multi-note dictation request", source_only="dictation"
    )
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": [
            {"title": "Synthetic primary", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
            {"title": "Synthetic secondary", "is_primary": False, "disposition": "separate_note", "template_id": str(template.id)},
        ]}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "notes": []}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        page.locator("[data-dictation-cta]").click()
        modal = page.locator("[data-dictation-modal]")
        modal.wait_for(state="visible")
        with page.expect_response(lambda response: "/consultation-split-intents" in response.url) as response_info:
            modal.locator("[data-dictation-save-generate]").click()
        assert response_info.value.status in {200, 202}
        modal.wait_for(state="hidden")

        analysis_id = _execution_id(sessions, transcript_id, "analysis")
        assert _run_analysis(sessions, analysis_id).outcome == "ready"
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=8_000)
        _confirm_review(page)
        assert _execution_id(sessions, transcript_id, "generation") is not None
    finally:
        page.close()
        context.close()


def test_browser_all_merged_review_continues_as_one_note_immediately(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """Merging every detected secondary problem saves then consumes one frozen intent."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, template = _prepare_owner(
        db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template
    )
    transcript_id = _create_consultation(
        db_session, owner, title="All merged browser request", source_only="dictation"
    )
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": [
            {"title": "Synthetic primary", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
            {"title": "Synthetic secondary", "is_primary": False, "disposition": "separate_note", "template_id": str(template.id)},
        ]}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "content": "Synthetic note."}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        _start_review(page, transcript_id)
        assert _run_analysis(sessions, _execution_id(sessions, transcript_id, "analysis")).outcome == "ready"
        # Re-enter after the locally driven worker transition so this test
        # exercises the normal idempotent Create restoration path, just as the
        # existing split happy path does.
        _start_review(page, transcript_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=8_000)
        page.locator("[data-split-review-merge]").click()
        continue_button = page.locator("[data-split-review-modal]").get_by_role(
            "button", name="Continue as one note", exact=True
        )
        assert continue_button.is_enabled()
        with page.expect_response(
            lambda response: "/consultation-split-draft" in response.url
            and response.request.method == "PUT"
        ) as save_info, page.expect_response(
            lambda response: response.url.endswith("/continue-as-one-note")
        ) as response_info:
            continue_button.click()
        assert save_info.value.status == 200
        response = response_info.value
        assert response.status in {200, 202}
        page.locator("[data-split-review-modal]").wait_for(state="hidden")
        assert page.locator("[data-tab-trigger=output]").get_attribute("aria-selected") == "true"
        page.locator("[data-latest-generated-output]").wait_for(state="attached", timeout=6_000)
        with sessions() as db:
            document = db.get(GeneratedDocument, UUID(response.json()["document"]["id"]))
            assert document is not None
            assert document.transcript_id == transcript_id
            assert document.status.value == "queued"
    finally:
        page.close()
        context.close()


def test_browser_split_partial_recovery_exposes_and_keeps_only_available_drafts(
    split_browser, db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    """A partial local provider reply exposes the server-authorized Keep control."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    monkeypatch.setattr("app.services.consultation_split_recovery.queue_automatic_split_recovery", lambda *_args, **_kwargs: None)
    context, sessions = split_browser
    owner, template = _prepare_owner(db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template)
    transcript_id = _create_consultation(db_session, owner, title="Synthetic partial consultation")
    analysis_response = json.dumps({"topics": [
        {"title": "Synthetic primary", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
        {"title": "Synthetic missing", "is_primary": False, "disposition": "separate_note", "template_id": str(template.id)},
    ]})
    _install_local_adapters(
        monkeypatch, analysis_response=analysis_response,
        generation_response=lambda: _generation_response(sessions, _execution_id(sessions, transcript_id, "generation"), omit_last=True),
    )
    monkeypatch.setattr("app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches", lambda *_args, **_kwargs: True)
    page = context.new_page()
    try:
        _login(page, owner.email)
        _start_review(page, transcript_id)
        analysis_id = _execution_id(sessions, transcript_id, "analysis")
        analysis_result = _run_analysis(sessions, analysis_id)
        assert analysis_result.outcome == "ready", analysis_result.error_code
        _start_review(page, transcript_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=6_000)
        _confirm_review(page)
        generation_id = _execution_id(sessions, transcript_id, "generation")
        assert _run_generation(sessions, generation_id).outcome == "ready"
        page.reload()
        keep = page.get_by_role("button", name="Keep available notes")
        keep.wait_for(state="visible", timeout=6_000)
        with page.expect_response(lambda response: response.url.endswith("/keep-available-notes")) as response_info:
            keep.click()
        assert response_info.value.status == 200
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)) == 1
    finally:
        page.close()
        context.close()
