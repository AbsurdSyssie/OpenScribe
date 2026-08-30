from datetime import timedelta
import logging
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptStatus, ProviderAttempt, TaskDispatchOutbox, TemplateSuggestionJob,
    TemplateSuggestionStatus, Transcript, TranscriptIngestionMode, TranscriptStatus,
    transcript_expiry,
)
from app.services.template_suggestions import (
    get_template_suggestion, process_template_suggestion, queue_template_suggestion,
)
from app.services.llm import _llm_config_has_in_flight_jobs
from app.services.task_outbox import publish_task_dispatch
from app.services.transcripts import delete_transcripts, set_transcript_draft_text


def _transcript(db, owner, text):
    item = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id, title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.live_chunked, status=TranscriptStatus.recording,
        retention_days_applied=30, retention_expires_at=transcript_expiry(30),
    )
    db.add(item)
    db.flush()
    set_transcript_draft_text(db, transcript=item, plaintext=text)
    db.commit()
    db.refresh(item)
    return item


def _configured_user(make_user, make_llm_config, make_llm_selection):
    user = make_user(email=f"suggest-{uuid4()}@example.com")
    config = make_llm_config(team=user.team)
    make_llm_selection(config=config)
    return user


def _templates(make_template, user, count=2):
    return [make_template(owner=user, actor=user, name=f"Template {index}") for index in range(count)]


def test_below_threshold_does_not_claim_or_resolve_provider(db_session, make_user, monkeypatch):
    user = make_user(email="suggest-short@example.com")
    transcript = _transcript(db_session, user, "x" * 1199)
    monkeypatch.setattr("app.services.template_suggestions.resolve_user_llm", lambda *_: pytest.fail("provider resolved"))

    assert queue_template_suggestion(db_session, user, transcript_id=transcript.id) is None
    assert db_session.scalar(select(TemplateSuggestionJob)) is None
    assert db_session.scalar(select(ProviderAttempt)) is None
    assert db_session.scalar(select(TaskDispatchOutbox)) is None


def test_too_few_candidates_is_durable_completed_null_without_dispatch(
    db_session, make_user, make_template, monkeypatch,
):
    user = make_user(email="suggest-one@example.com")
    transcript = _transcript(db_session, user, "x" * 1200)
    make_template(owner=user, actor=user, name="Only template")
    monkeypatch.setattr("app.services.template_suggestions.resolve_user_llm", lambda *_: pytest.fail("provider resolved"))

    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert job.status is TemplateSuggestionStatus.completed
    assert get_template_suggestion(db_session, user, transcript_id=transcript.id) == (job, None)
    assert queue_template_suggestion(db_session, user, transcript_id=transcript.id).id == job.id
    assert db_session.scalar(select(ProviderAttempt)) is None
    assert db_session.scalar(select(TaskDispatchOutbox)) is None


def test_template_suggestion_api_returns_terminal_and_queued_states(
    client, db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    first = make_template(owner=user, actor=user, name="First template")
    login = client.post("/api/v1/auth/login", json={"email": user.email, "password": "password-1"})
    assert login.status_code == 200

    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    only_one = client.post(f"/api/v1/transcripts/{transcript.id}/template-suggestion")
    assert only_one.status_code == 200
    assert only_one.json() == {"status": "completed", "suggestion": None}

    second_transcript = _transcript(db_session, user, "y" * 1300)
    make_template(owner=user, actor=user, name="Second template")
    queued = client.post(f"/api/v1/transcripts/{second_transcript.id}/template-suggestion")
    assert queued.status_code == 202
    assert queued.json() == {"status": "queued", "suggestion": None}
    status_response = client.get(f"/api/v1/transcripts/{second_transcript.id}/template-suggestion")
    assert status_response.status_code == 200
    assert status_response.json() == {"status": "queued", "suggestion": None}
    assert first.id is not None


def test_queue_is_atomic_and_credential_failure_is_pre_dispatch(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id))
    assert attempt.status is AttemptStatus.reserved
    assert dispatch is not None
    assert _llm_config_has_in_flight_jobs(db_session, config_id=job.llm_config_id) is True

    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: (_ for _ in ()).throw(RuntimeError("secret unavailable")))
    failed = process_template_suggestion(db_session, job_id=job.id)
    db_session.refresh(attempt)
    assert failed.status is TemplateSuggestionStatus.failed
    assert failed.error_code == "template_suggestion_preparation_failed"
    assert attempt.status is AttemptStatus.cancelled


def test_valid_result_uses_redacted_text_dispatches_once_and_reads_current_name(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    raw = "Patient Alice Secret " + ("clinical text " * 100)
    transcript = _transcript(db_session, user, raw)
    candidates = _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "Alice Secret safe", "phi_index": []})
    seen = []
    def manual(*_args, **kwargs):
        assert kwargs["transcript_text"] == "Alice Secret safe"
        return "[PHI-1] safe", "", [{"index": 1}]
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", manual)
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "token")
    def provider(**kwargs):
        body = kwargs["request_body"]
        user_message = body["messages"][1]["content"]
        seen.append(user_message)
        assert "Alice" not in user_message and "Secret" not in user_message
        return '{"template_id":"%s","confidence":"high","reason":"Best fit"}' % candidates[0].id, {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    monkeypatch.setattr("app.services.template_suggestions._generate_freeform_output_openai", provider)

    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.completed
    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.completed
    candidates[0].name = "Renamed authoritative template"
    db_session.commit()
    _, result = get_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert result == {"template_id": str(candidates[0].id), "confidence": "high", "template_name": "Renamed authoritative template"}
    assert len(seen) == 1

    stranger = make_user(email="suggest-stranger@example.com", team=user.team)
    with pytest.raises(AppError) as denied:
        get_template_suggestion(db_session, stranger, transcript_id=transcript.id)
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "output",
    [
        '{"template_id":null,"confidence":"low","reason":"Unclear"}',
        '{"template_id":"00000000-0000-0000-0000-000000000001","confidence":"medium","reason":"Invented"}',
        "not json",
    ],
)
def test_low_or_invalid_provider_output_never_exposes_a_suggestion(
    output, db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", lambda *_args, **_kwargs: ("safe", "", []))
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "token")
    monkeypatch.setattr("app.services.template_suggestions._generate_freeform_output_openai", lambda **_: (output, {"total_tokens": 2}))
    processed = process_template_suggestion(db_session, job_id=job.id)
    _, result = get_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert result is None
    assert processed.status is (TemplateSuggestionStatus.completed if '"low"' in output else TemplateSuggestionStatus.failed)


def test_provider_failure_is_generic_and_suppressed(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", lambda *_args, **_kwargs: ("safe", "", []))
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "token")
    monkeypatch.setattr("app.services.template_suggestions._generate_freeform_output_openai", lambda **_: (_ for _ in ()).throw(RuntimeError("sensitive provider detail")))
    failed = process_template_suggestion(db_session, job_id=job.id)
    assert failed.status is TemplateSuggestionStatus.failed
    assert failed.error_code == "template_suggestion_failed"
    assert "sensitive" not in failed.error_code


def test_suggestion_lifecycle_logs_transitions_without_content(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch, caplog,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    raw_transcript = "Patient Alice Sensitive Transcript " + ("clinical detail " * 100)
    transcript = _transcript(db_session, user, raw_transcript)
    candidates = [
        make_template(
            owner=user,
            actor=user,
            name="Sensitive Template Name",
            description="Sensitive template description",
        )
        for _ in range(2)
    ]
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", lambda *_args, **_kwargs: ("safe", "", []))
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "credential-should-not-appear")
    monkeypatch.setattr(
        "app.services.template_suggestions._generate_freeform_output_openai",
        lambda **_: (
            '{"template_id":"%s","confidence":"high","reason":"Sensitive provider explanation"}' % candidates[0].id,
            {"total_tokens": 12},
        ),
    )
    caplog.set_level(logging.INFO, logger="openscribe.template_suggestion")

    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.completed
    get_template_suggestion(db_session, user, transcript_id=transcript.id)

    events = [record.msg for record in caplog.records if record.name == "openscribe.template_suggestion"]
    assert {
        "template_suggestion_queue_started",
        "template_suggestion_queued",
        "template_suggestion_worker_started",
        "template_suggestion_prepared",
        "template_suggestion_provider_submitted",
        "template_suggestion_completed",
        "template_suggestion_status_read",
    } <= set(events)
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    for sensitive_value in (
        "Alice Sensitive Transcript",
        "Sensitive Template Name",
        "Sensitive template description",
        "Sensitive provider explanation",
        "credential-should-not-appear",
    ):
        assert sensitive_value not in logged


def test_suggestion_outbox_publish_logs_durable_metadata_only(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch, caplog,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "Private transcript content " + ("x" * 1300))
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id))

    class Publisher:
        def publish(self, item):
            assert item.task_id == dispatch.task_id

    caplog.set_level(logging.INFO, logger="openscribe.task_outbox")
    assert publish_task_dispatch(db_session, task_id=dispatch.task_id, publisher=Publisher()) is True
    records = [record for record in caplog.records if record.name == "openscribe.task_outbox"]
    assert [record.msg for record in records] == [
        "template_suggestion_dispatch_publish_started",
        "template_suggestion_dispatch_published",
    ]
    assert all(record.template_suggestion_job_id == str(job.id) for record in records)
    assert "Private transcript content" not in "\n".join(f"{record.getMessage()} {record.__dict__}" for record in records)


def test_transcript_deletion_removes_job_and_polymorphic_dispatch(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id)) is not None

    assert delete_transcripts(db_session, user, transcript_ids=[transcript.id]) == 1
    assert db_session.get(TemplateSuggestionJob, job.id) is None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id)) is None
