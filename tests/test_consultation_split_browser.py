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
    ConsultationProblemPlan,
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
    if source_only in {None, "transcript", "transcript_dictation"}:
        set_transcript_draft_text(db, transcript=transcript, plaintext="Synthetic test transcript.")
    if source_only not in {"dictation", "transcript", "transcript_dictation"}:
        set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic test consultation.")
        transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    if source_only in {"dictation", "transcript_dictation"}:
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


def _progress_latest_intent(sessions, transcript_id):
    with sessions() as db:
        intent_id = db.scalar(
            select(ConsultationSplitIntent.id)
            .where(ConsultationSplitIntent.transcript_id == transcript_id)
            .order_by(ConsultationSplitIntent.created_at.desc())
        )
    assert intent_id is not None
    return _progress_intent(sessions, intent_id)


def _generation_response(sessions, execution_id, *, omit_last=False):
    with sessions() as db:
        execution = db.get(ConsultationSplitExecution, execution_id)
        batch = db.get(ConsultationSplitBatch, execution.batch_id)
        owner = db.get(User, execution.owner_user_id)
        plan = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted")
        notes = []
        planned_notes = plan.get("notes", plan.get("topics", []))
        for topic in planned_notes:
            if topic.get("disposition", "separate_note") != "separate_note":
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
    tile_templates = page.locator("[data-tile-template]")
    assert tile_templates.count() > 0
    assert all(value for value in tile_templates.evaluate_all("items => items.map(item => item.value)"))
    with page.expect_response(
        lambda response: response.url.endswith("/consultation-note-batches")
        and response.request.method == "POST"
    ) as response_info:
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
        assert _progress_latest_intent(sessions, first_id).outcome == "review_ready"
        # Re-enter through the rendered Generate control after the queued
        # request has completed.  This is a normal idempotent replay and gives
        # the browser a fresh operation after the local worker transition.
        _start_review(page, first_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=8_000)
        with sessions() as db:
            problem_plan = db.scalar(
                select(ConsultationProblemPlan).where(ConsultationProblemPlan.transcript_id == first_id)
            )
            assert problem_plan is not None
            from app.services.content_crypto import decrypt_json_for_owner
            stored_plan = decrypt_json_for_owner(
                db, owner_user_id=owner.id, table="consultation_problem_plans",
                field="plan_encrypted", record_id=problem_plan.id, stored_value=problem_plan.plan_encrypted,
            )
            assert len(stored_plan["tiles"]) == 2
        assert page.locator("[data-tile-template]").count() == 2
        _confirm_review(page)
        generation_id = _execution_id(sessions, first_id, "generation")
        assert _run_generation(sessions, generation_id).outcome == "ready"
        page.reload()
        latest_output = page.locator('[data-latest-generated-output][data-latest-generated-status="ready"]')
        latest_output.wait_for(state="attached", timeout=6_000)
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == first_id)) == 2
            documents = db.scalars(
                select(GeneratedDocument)
                .where(GeneratedDocument.transcript_id == first_id)
                .order_by(GeneratedDocument.created_at)
            ).all()
            document_ids = [str(document.id) for document in documents]
            db.delete(documents[0])
            db.commit()
        page.reload()
        page.locator(f'[data-note-document-select][data-document-id="{document_ids[1]}"]').wait_for(
            state="attached", timeout=6_000,
        )
        playwright_sync.expect(page.locator('[data-document-id^="split-placeholder:"]')).to_have_count(0)
        playwright_sync.expect(page.locator('[data-note-selector] [data-note-document-select]')).to_have_count(2)
        page.reload()
        playwright_sync.expect(page.locator('[data-document-id^="split-placeholder:"]')).to_have_count(0)
        playwright_sync.expect(page.locator('[data-note-selector] [data-note-document-select]')).to_have_count(2)
        with sessions() as db:
            survivor = db.get(GeneratedDocument, UUID(document_ids[1]))
            assert survivor is not None
            db.delete(survivor)
            db.commit()
        page.reload()
        page.locator('[data-note-selector] [data-note-document-select]').first.wait_for(
            state="attached", timeout=6_000,
        )
        playwright_sync.expect(page.locator('[data-document-id^="split-placeholder:"]')).to_have_count(0)
        playwright_sync.expect(page.locator('[data-note-selector] [data-note-document-select]')).to_have_count(1)
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


def test_browser_review_problems_is_passive_until_explicit_manual_suggestion(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """A preference-off owner can inspect, then explicitly request one-problem review."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    team = make_team(name="Synthetic review-problems browser clinic")
    owner = make_user(
        email="review-problems-browser@example.com",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    config = make_llm_config(team=team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    template = make_template(owner=owner, actor=owner, name="Synthetic review-problems template")
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={}))
    db_session.commit()
    transcript_id = _create_consultation(
        db_session, owner, title="Review problems consultation", source_only="transcript_dictation",
    )
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": [{
            "title": "Synthetic single problem",
            "is_primary": True,
            "disposition": "separate_note",
            "template_id": str(template.id),
        }]}),
        generation_response=lambda: json.dumps({"title": "Unexpected", "content": "Unexpected"}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        review = page.get_by_role("button", name="Review problems", exact=True)
        review.wait_for(state="visible", timeout=6_000)
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(ConsultationSplitIntent).where(ConsultationSplitIntent.transcript_id == transcript_id)) == 0
            assert db.scalar(select(func.count()).select_from(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.transcript_id == transcript_id)) == 0
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)) == 0

        review.click()
        page.locator("[data-split-review-modal]").wait_for(state="visible")
        suggest_button = page.get_by_role("button", name="Auto suggest", exact=True)
        playwright_sync.expect(suggest_button).to_be_enabled(timeout=6_000)
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(ConsultationSplitIntent).where(ConsultationSplitIntent.transcript_id == transcript_id)) == 0
            assert db.scalar(select(func.count()).select_from(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.transcript_id == transcript_id)) == 0
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)) == 0

        page.once("dialog", lambda dialog: dialog.accept())
        with page.expect_response(lambda response: "/consultation-problem-suggestions" in response.url) as response_info:
            page.get_by_role("button", name="Auto suggest", exact=True).click()
        assert response_info.value.status in {200, 202}
        payload = response_info.value.request.post_data_json
        assert payload["expected_revision"] == 0
        assert payload["selected_template_id"] == str(template.id)
        assert payload["retry_failed_analysis"] is False
        assert response_info.value.request.headers.get("content-type", "").startswith("application/json")
        analysis_id = _execution_id(sessions, transcript_id, "analysis")
        assert _run_analysis(sessions, analysis_id).outcome == "not_required"
        with sessions() as db:
            intent_id = db.scalar(
                select(ConsultationSplitIntent.id)
                .where(ConsultationSplitIntent.transcript_id == transcript_id)
                .order_by(ConsultationSplitIntent.created_at.desc())
            )
        assert _progress_intent(sessions, intent_id).outcome == "review_ready"
        page.reload()
        page.get_by_role("button", name="Review problems", exact=True).click()
        page.locator("[data-split-review-modal]").wait_for(state="visible")
        assert page.locator(".split-review-tile").count() == 1
        assert page.locator("[data-problem-title]").count() == 1
        playwright_sync.expect(page.locator("[data-split-review-create]")).to_be_enabled(timeout=6_000)
        with sessions() as db:
            plan = db.scalar(
                select(ConsultationProblemPlan).where(ConsultationProblemPlan.transcript_id == transcript_id)
            )
            assert plan is not None
            assert db.scalar(select(func.count()).select_from(ConsultationSplitBatch).where(ConsultationSplitBatch.transcript_id == transcript_id)) == 0
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)) == 0
    finally:
        page.close()
        context.close()


def test_browser_living_problem_plan_groups_problems_and_preserves_promoted_tile_identity(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """Manual planning edits persist independently and promote the existing tile."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, _template = _prepare_owner(
        db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template,
    )
    transcript_id = _create_consultation(db_session, owner, title="Synthetic living plan consultation")
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": []}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "content": "Synthetic note."}),
    )
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        page.get_by_role("button", name="Review problems", exact=True).click()
        modal = page.locator("[data-split-review-modal]")
        modal.wait_for(state="visible")
        primary = page.locator(".split-review-tile.is-primary")
        primary.wait_for(state="visible")
        assert primary.get_attribute("data-tile-uuid")

        # Add two manually named problems, then explicitly route one to a new
        # output note using the keyboard/touch-friendly Move to control.
        add_problem = page.locator('[data-tile-action="add-problem"]').first
        add_problem.click()
        first = page.locator("[data-problem-title]").last
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            first.fill("Synthetic knee pain")
        assert save_response.value.status == 200
        page.locator('[data-tile-action="add-problem"]').first.click()
        second = page.locator("[data-problem-title]").last
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            second.fill("Synthetic blood pressure")
        assert save_response.value.status == 200
        second_key = second.get_attribute("data-problem-key")
        assert second_key
        move = page.locator(f'[data-problem-move][data-problem-key="{second_key}"]')
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            move.select_option("__new_note__")
        assert save_response.value.status == 200
        secondary = page.locator(".split-review-tile").nth(1)
        secondary.wait_for(state="visible")
        assert secondary.locator("[data-problem-title]").input_value() == "Synthetic blood pressure"

        # Escape cancels an active native drag without creating a note or
        # changing the visible plan. A deliberate drag to Add note creates the
        # tile only after the drop is accepted.
        handle = secondary.locator("[data-problem-drag-handle]")
        handle_box = handle.bounding_box()
        page.mouse.move(handle_box["x"] + 8, handle_box["y"] + 8)
        page.mouse.down()
        page.mouse.move(handle_box["x"] + 48, handle_box["y"] + 8, steps=4)
        page.keyboard.press("Escape")
        page.mouse.up()
        assert page.locator(".split-review-tile").count() == 2
        assert page.locator("[data-split-review-add]").inner_text() == "+ Add note"

        handle = page.locator(f'[data-problem-drag-handle][data-problem-key="{second_key}"]')
        handle_box = handle.bounding_box()
        add_note = page.locator("[data-split-review-add]")
        add_note_box = add_note.bounding_box()
        page.mouse.move(handle_box["x"] + 8, handle_box["y"] + 8)
        page.mouse.down()
        target_x = add_note_box["x"] + add_note_box["width"] / 2
        target_y = add_note_box["y"] + add_note_box["height"] - 2
        page.mouse.move(target_x, target_y, steps=10)
        # Chromium can coalesce the final native dragover when a drag ends at
        # the target. A second in-target move lets the registered dragover
        # handlers observe the deliberate drop intent before the dwell.
        page.mouse.move(target_x, target_y, steps=1)
        page.wait_for_timeout(250)
        assert add_note.inner_text() == "Drop here to create a note"
        assert page.locator(".split-review-tile").count() == 2
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            page.mouse.up()
        assert save_response.value.status == 200
        playwright_sync.expect(page.locator(".split-review-tile")).to_have_count(2, timeout=6_000)
        assert page.locator(".split-review-tile").nth(1).locator("[data-problem-title]").input_value() == "Synthetic blood pressure"

        secondary = page.locator(".split-review-tile").nth(1)
        secondary_id = secondary.get_attribute("data-tile-uuid")
        assert secondary_id
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            secondary.get_by_role("button", name="Make Note 2 primary").click()
        assert save_response.value.status == 200
        promoted = page.locator(".split-review-tile.is-primary")
        assert promoted.get_attribute("data-tile-uuid") == secondary_id
        assert page.locator(".split-review-tile").first.get_attribute("data-tile-uuid") == secondary_id
        assert page.locator(".split-review-tile").count() == 2

        with sessions() as db:
            plan = db.scalar(
                select(ConsultationProblemPlan).where(ConsultationProblemPlan.transcript_id == transcript_id)
            )
            assert plan is not None
            assert plan.manual_override is True
            from app.services.content_crypto import decrypt_json_for_owner
            persisted_plan = decrypt_json_for_owner(
                db, owner_user_id=owner.id, table="consultation_problem_plans",
                field="plan_encrypted", record_id=plan.id, stored_value=plan.plan_encrypted,
            )
            persisted_titles = [problem["title"] for problem in persisted_plan["problems"]]
            assert {"Synthetic knee pain", "Synthetic blood pressure"} <= set(persisted_titles)
            assert plan.revision >= 3

        assert "saved" not in modal.locator("[data-split-review-status]").inner_text().lower()

        for width in (320, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(100)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert modal.locator(".split-review-modal__dialog").evaluate(
                "el => el.scrollWidth <= el.clientWidth"
            )
            assert page.locator(".split-review-tile").first.evaluate("""el => {
              const tile = el.getBoundingClientRect();
              const dialog = el.closest('.split-review-modal__dialog').getBoundingClientRect();
              return tile.left >= dialog.left && tile.right <= dialog.right;
            }""")
            assert page.locator(".split-review-tile").evaluate_all(
                "tiles => tiles.every(el => el.scrollHeight <= el.clientHeight + 1)"
            )
    finally:
        page.close()
        context.close()


def test_browser_living_problem_actions_autosave_and_enforce_note_limit(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """Ignore, Restore, Delete, autosave, and note limits remain visible contracts."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    context, sessions = split_browser
    owner, _template = _prepare_owner(
        db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template,
    )
    transcript_id = _create_consultation(db_session, owner, title="Synthetic problem actions consultation")
    _install_local_adapters(
        monkeypatch,
        analysis_response=json.dumps({"topics": []}),
        generation_response=lambda: json.dumps({"title": "Synthetic", "content": "Synthetic note."}),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        page.goto(f"/transcribe?transcript_id={transcript_id}")
        page.get_by_role("button", name="Review problems", exact=True).click()
        modal = page.locator("[data-split-review-modal]")
        modal.wait_for(state="visible")
        page.locator('[data-tile-action="add-problem"]').first.click()
        title = page.locator("[data-problem-title]").last
        row = title.locator("xpath=ancestor::div[contains(@class, 'split-review-problem')]")
        ignore = row.locator('[data-problem-action="ignore"]')
        assert ignore.is_disabled()

        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            title.fill("Synthetic sleep concerns")
        assert save_response.value.status == 200
        assert not ignore.is_disabled()
        with sessions() as db:
            saved_plan = db.scalar(
                select(ConsultationProblemPlan).where(ConsultationProblemPlan.transcript_id == transcript_id)
            )
            assert saved_plan is not None
            from app.services.content_crypto import decrypt_json_for_owner
            saved_payload = decrypt_json_for_owner(
                db, owner_user_id=owner.id, table="consultation_problem_plans",
                field="plan_encrypted", record_id=saved_plan.id, stored_value=saved_plan.plan_encrypted,
            )
            assert saved_payload["problems"][0]["title"] == "Synthetic sleep concerns"
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            ignore.click()
        assert save_response.value.status == 200

        ignored_button = modal.locator("[data-split-review-ignored]")
        ignored_button.wait_for(state="visible")
        assert "1" in ignored_button.inner_text()
        ignored_button.click()
        popover = modal.locator("[data-split-review-ignored-popover]")
        popover.get_by_text("Synthetic sleep concerns", exact=True).wait_for(state="visible")
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            popover.get_by_role("button", name="Restore", exact=True).click()
        assert save_response.value.status == 200
        assert modal.locator("[data-problem-title]").count() == 1

        row = modal.locator("[data-problem-title]").locator("xpath=ancestor::div[contains(@class, 'split-review-problem')]")
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_response:
            row.locator('[data-problem-action="delete"]').click()
        assert save_response.value.status == 200
        assert modal.locator("[data-problem-title]").count() == 0
        assert modal.locator(".split-review-tile.is-primary").count() == 1
        with sessions() as db:
            plan = db.scalar(
                select(ConsultationProblemPlan).where(ConsultationProblemPlan.transcript_id == transcript_id)
            )
            assert plan is not None
            assert plan.manual_override is True
            persisted_plan = decrypt_json_for_owner(
                db, owner_user_id=owner.id, table="consultation_problem_plans",
                field="plan_encrypted", record_id=plan.id, stored_value=plan.plan_encrypted,
            )
            assert persisted_plan["problems"] == []

        add_note = modal.locator("[data-split-review-add]")
        for _ in range(5):
            assert add_note.is_enabled()
            add_note.click()
        assert modal.locator(".split-review-tile").count() == 6
        assert add_note.is_disabled()
        assert modal.locator("[data-split-review-status]").inner_text().strip() == ""
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
            plan = db.scalar(select(ConsultationProblemPlan).where(ConsultationProblemPlan.transcript_id == first_id))
            assert plan is not None
            assert db.scalar(
                select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == second_id)
            ) == 0

        page.goto(f"/transcribe?transcript_id={first_id}")
        trigger = page.locator("[data-split-review-trigger]")
        trigger.wait_for(state="visible", timeout=6_000)
        assert page.locator("[data-split-review-modal]").is_hidden()
        trigger.click()
        page.locator("[data-split-review-modal]").wait_for(state="visible")
        create_notes = page.locator("[data-split-review-create]")
        create_notes.wait_for(state="visible", timeout=6_000)
        assert page.locator(".split-review-tile").count() == 2
        playwright_sync.expect(create_notes).to_be_enabled(timeout=6_000)
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
        assert _progress_latest_intent(sessions, transcript_id).outcome == "review_ready"

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
            assert batch.problem_plan_id is not None
            plan = db.get(ConsultationProblemPlan, batch.problem_plan_id)
            assert plan is not None
            frozen = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted")
            assert frozen["version"] == 2
            assert frozen["source_lineage"]["problem_plan_id"] == str(plan.id)
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
        assert _progress_latest_intent(sessions, transcript_id).outcome == "review_ready"
        review = page.locator("[data-split-review-modal]")
        review.wait_for(state="visible", timeout=8_000)
        assert review.locator("[data-split-review-status]").inner_text().strip() == ""
        assert review.locator("[data-problem-title]").count() == 1
        assert review.locator(".split-review-tile").count() == 1
        assert review.locator("[data-split-review-create]").is_enabled()
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


def test_browser_all_merged_review_creates_one_note_from_saved_plan(
    split_browser,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
):
    """Moving every problem into Primary creates one note from the saved plan."""
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
        generation_response=lambda: _generation_response(
            sessions, _execution_id(sessions, transcript_id, "generation"),
        ),
    )
    page = context.new_page()
    try:
        _login(page, owner.email)
        _start_review(page, transcript_id)
        assert _run_analysis(sessions, _execution_id(sessions, transcript_id, "analysis")).outcome == "ready"
        assert _progress_latest_intent(sessions, transcript_id).outcome == "review_ready"
        _start_review(page, transcript_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=8_000)
        secondary_title = page.locator('[data-problem-title][aria-label="Problem title: Synthetic secondary"]')
        secondary_row = secondary_title.locator("xpath=ancestor::div[contains(@class, 'split-review-problem')]")
        move = secondary_row.locator("[data-problem-move]")
        with page.expect_response(lambda response: response.url.endswith("/consultation-problem-plan") and response.request.method == "PUT") as save_info:
            move.select_option(label="Primary note")
        assert save_info.value.status == 200
        assert page.locator(".split-review-tile").count() == 1
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)) == 0
        _confirm_review(page)
        generation_id = _execution_id(sessions, transcript_id, "generation")
        assert generation_id is not None
        assert _run_generation(sessions, generation_id).outcome == "ready"
        page.reload()
        page.locator('[data-latest-generated-output][data-latest-generated-status="ready"]').wait_for(state="attached", timeout=6_000)
        with sessions() as db:
            documents = db.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)).all()
            assert len(documents) == 1
            assert documents[0].transcript_id == transcript_id
            execution = db.get(ConsultationSplitExecution, generation_id)
            batch = db.get(ConsultationSplitBatch, execution.batch_id)
            frozen = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted")
            assert len(frozen["notes"]) == 1
            assert frozen["notes"][0]["template"]["template_id"] == str(template.id)
    finally:
        page.close()
        context.close()


def test_browser_split_partial_recovery_exposes_and_keeps_only_available_drafts(
    split_browser, db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    """A partial local provider reply exposes the server-authorized Keep control."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
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
        assert _progress_latest_intent(sessions, transcript_id).outcome == "review_ready"
        _start_review(page, transcript_id)
        page.locator("[data-split-review-modal]").wait_for(state="visible", timeout=6_000)
        _confirm_review(page)
        generation_id = _execution_id(sessions, transcript_id, "generation")
        assert _run_generation(sessions, generation_id).outcome == "ready"
        recovery_generation_id = _execution_id(sessions, transcript_id, "generation")
        assert recovery_generation_id is not None and recovery_generation_id != generation_id
        assert _run_generation(sessions, recovery_generation_id).outcome == "failed"
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
