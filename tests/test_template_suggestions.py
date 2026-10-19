from datetime import timedelta
import logging
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptStatus, LlmAdapterKind, ProviderAttempt, TaskDispatchOutbox, TemplateSuggestionJob,
    TemplateSuggestionStatus, Transcript, TranscriptIngestionMode, TranscriptStatus, UserAppPreference,
    transcript_expiry, utcnow,
)
from app.services.template_suggestions import (
    _extract_json, _messages, get_template_suggestion, process_template_suggestion, queue_template_suggestion,
)
from app.services.quotas import estimate_token_reservation
from app.services.llm import _llm_config_has_in_flight_jobs
from app.services.task_outbox import publish_task_dispatch
from app.services.transcripts import delete_transcripts, set_transcript_draft_text


@pytest.mark.parametrize(
    "payload",
    [
        '{"template_id":"first","template_id":"second","confidence":"high","reason":"Synthetic"}',
        '{"template_id":"first","confidence":"high","reason":{"text":"one","text":"two"}}',
    ],
)
def test_template_suggestion_json_rejects_duplicate_keys_at_every_depth(payload):
    with pytest.raises(ValueError):
        _extract_json(payload)


def test_template_suggestion_json_accepts_unambiguous_object():
    assert _extract_json('{"template_id":"synthetic","confidence":"high","reason":"Synthetic"}') == {
        "template_id": "synthetic",
        "confidence": "high",
        "reason": "Synthetic",
    }


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


def _enabled_user(make_user, **kwargs):
    user = make_user(**kwargs)
    user.app_preferences = UserAppPreference(
        user_id=user.id,
        preferences_json={"template_suggestions_enabled": True},
    )
    return user


def _configured_user(make_user, make_llm_config, make_llm_selection):
    user = _enabled_user(make_user, email=f"suggest-{uuid4()}@example.com")
    config = make_llm_config(team=user.team)
    make_llm_selection(config=config)
    return user


def _templates(make_template, user, count=2):
    return [make_template(owner=user, actor=user, name=f"Template {index}") for index in range(count)]


def test_below_threshold_does_not_claim_or_resolve_provider(db_session, make_user, monkeypatch):
    user = _enabled_user(make_user, email="suggest-short@example.com")
    transcript = _transcript(db_session, user, "x" * 1199)
    monkeypatch.setattr("app.services.template_suggestions.resolve_user_llm", lambda *_: pytest.fail("provider resolved"))

    assert queue_template_suggestion(db_session, user, transcript_id=transcript.id) is None
    assert db_session.scalar(select(TemplateSuggestionJob)) is None
    assert db_session.scalar(select(ProviderAttempt)) is None
    assert db_session.scalar(select(TaskDispatchOutbox)) is None


def test_disabled_template_suggestions_do_not_create_job_attempt_or_outbox(
    client, db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    user = make_user(email=f"suggest-disabled-{uuid4()}@example.com")
    config = make_llm_config(team=user.team)
    make_llm_selection(config=config)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    make_user_app_preference(user=user, preferences_json={"template_suggestions_enabled": False})
    monkeypatch.setattr("app.services.template_suggestions.resolve_user_llm", lambda *_: pytest.fail("provider resolved"))

    login = client.post("/api/v1/auth/login", json={"email": user.email, "password": "password-1"})
    assert login.status_code == 200
    response = client.post(f"/api/v1/transcripts/{transcript.id}/template-suggestion")

    assert response.status_code == 200
    assert response.json() == {"status": "not_eligible", "suggestion": None}
    assert db_session.scalar(select(TemplateSuggestionJob)) is None
    assert db_session.scalar(select(ProviderAttempt)) is None
    assert db_session.scalar(select(TaskDispatchOutbox)) is None


def test_enabled_template_suggestions_queue_job_and_outbox(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)

    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)

    assert job is not None
    assert job.status is TemplateSuggestionStatus.queued
    assert db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id)) is not None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id)) is not None


def test_unsupported_worker_cancels_suggestion_before_submitted_or_provider_call(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    provider_calls = []
    monkeypatch.setattr(
        "app.services.template_suggestions.require_supported_execution_context",
        lambda: (_ for _ in ()).throw(AppError(503, "llm_execution_context_unsupported", "synthetic")),
    )
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **_kwargs: provider_calls.append(True),
    )

    failed = process_template_suggestion(db_session, job_id=job.id)

    db_session.refresh(attempt)
    assert failed.status is TemplateSuggestionStatus.failed
    assert failed.error_code == "llm_execution_context_unsupported"
    assert attempt.status is AttemptStatus.cancelled
    assert provider_calls == []


@pytest.mark.parametrize(
    ("adapter_kind", "base_url", "expected_cap"),
    [
        (LlmAdapterKind.openai_chat, "https://api.openai.com/v1", 220),
        (LlmAdapterKind.gemini_enterprise, "https://us-central1-aiplatform.googleapis.com/v1", 30_000),
    ],
)
def test_template_suggestion_reserves_actual_adapter_output_cap(
    db_session, make_user, make_template, make_llm_config, monkeypatch, adapter_kind, base_url, expected_cap,
):
    user = _enabled_user(make_user, email=f"suggest-cap-{uuid4()}@example.com")
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    config = make_llm_config(
        team=user.team,
        adapter_kind=adapter_kind,
        base_url=base_url,
        model_name="gemini-2.5-flash" if adapter_kind is LlmAdapterKind.gemini_enterprise else "gpt-4o-mini",
    )
    monkeypatch.setattr(
        "app.services.template_suggestions.resolve_user_llm",
        lambda *_args, **_kwargs: (None, config, config.model_name, None),
    )
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)

    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)

    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    assert attempt is not None
    messages = _messages(excerpt="x" * 1300, candidates=job.candidates_snapshot_json, selected_template=None)
    assert attempt.reserved_units == estimate_token_reservation(messages, max_completion_tokens=expected_cap)


def test_missing_template_suggestion_preference_defaults_to_enabled(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = make_user(email=f"suggest-default-on-{uuid4()}@example.com")
    config = make_llm_config(team=user.team)
    make_llm_selection(config=config)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)

    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)

    assert job is not None
    assert job.status is TemplateSuggestionStatus.queued


def test_disabling_preference_cancels_queued_job_before_redaction(
    client, db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    monkeypatch.setattr(
        "app.services.template_suggestions.redact_transient_text",
        lambda *_args, **_kwargs: pytest.fail("redaction must not run after opt-out"),
    )

    login = client.post("/api/v1/auth/login", json={"email": user.email, "password": "password-1"})
    assert login.status_code == 200
    saved = client.post("/api/v1/app-preferences", json={"template_suggestions_enabled": False})

    assert saved.status_code == 200
    assert saved.json()["template_suggestions_enabled"] is False
    db_session.refresh(job)
    db_session.refresh(attempt)
    assert job.status is TemplateSuggestionStatus.failed
    assert job.error_code == "template_suggestion_preference_disabled"
    assert attempt.status is AttemptStatus.cancelled
    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.failed


def test_too_few_candidates_is_durable_completed_null_without_dispatch(
    db_session, make_user, make_template, monkeypatch,
):
    user = _enabled_user(make_user, email="suggest-one@example.com")
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
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", provider)

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


def test_dispatch_expands_reservation_for_larger_final_redacted_request(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    candidates = _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    initial_reservation = attempt.reserved_units
    monkeypatch.setattr(
        "app.services.template_suggestions.redact_transient_text",
        lambda *_args, **_kwargs: {"redacted_text": "z" * 6000, "phi_index": []},
    )
    monkeypatch.setattr(
        "app.services.template_suggestions._apply_manual_pii_redaction",
        lambda _db, **kwargs: (kwargs["transcript_text"], "", []),
    )
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "synthetic-token")
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **_kwargs: (
            '{"template_id":"%s","confidence":"high","reason":"Synthetic match"}' % candidates[0].id,
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        ),
    )

    processed = process_template_suggestion(db_session, job_id=job.id)

    db_session.refresh(attempt)
    final_messages = _messages(excerpt="z" * 6000, candidates=job.candidates_snapshot_json, selected_template=None)
    assert processed.status is TemplateSuggestionStatus.completed
    assert attempt.reserved_units > initial_reservation
    assert attempt.reserved_units == estimate_token_reservation(final_messages, max_completion_tokens=220)


def test_dispatch_reservation_quota_failure_cancels_without_provider_call(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    user.daily_token_limit = attempt.reserved_units + 1
    user.monthly_token_limit = attempt.reserved_units + 1
    db_session.add(user)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.template_suggestions.redact_transient_text",
        lambda *_args, **_kwargs: {"redacted_text": "z" * 6000, "phi_index": []},
    )
    monkeypatch.setattr(
        "app.services.template_suggestions._apply_manual_pii_redaction",
        lambda _db, **kwargs: (kwargs["transcript_text"], "", []),
    )
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "synthetic-token")
    provider_calls = []
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **_kwargs: provider_calls.append(True),
    )

    failed = process_template_suggestion(db_session, job_id=job.id)

    db_session.refresh(attempt)
    assert failed.status is TemplateSuggestionStatus.failed
    assert failed.error_code == "template_suggestion_preparation_failed"
    assert attempt.status is AttemptStatus.cancelled
    assert provider_calls == []


def test_reservation_failure_does_not_fail_or_cancel_concurrent_submitted_winner(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    monkeypatch.setattr(
        "app.services.template_suggestions.redact_transient_text",
        lambda *_args, **_kwargs: {"redacted_text": "z" * 6000, "phi_index": []},
    )
    monkeypatch.setattr(
        "app.services.template_suggestions._apply_manual_pii_redaction",
        lambda _db, **kwargs: (kwargs["transcript_text"], "", []),
    )
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "synthetic-token")
    monkeypatch.setattr(
        "app.services.template_suggestions.increase_provider_attempt_reservation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppError(429, "quota_exceeded", "Synthetic quota limit")),
    )
    provider_calls = []
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **_kwargs: provider_calls.append(True),
    )

    real_rollback = db_session.rollback
    winner_started = False

    def rollback_then_progress_winner():
        nonlocal winner_started
        real_rollback()
        if winner_started:
            return
        winner_started = True
        winning_job = db_session.get(TemplateSuggestionJob, job.id)
        winning_job.status = TemplateSuggestionStatus.processing
        winning_job.started_at = utcnow()
        from app.services.quotas import mark_provider_attempt_submitted

        now = utcnow()
        mark_provider_attempt_submitted(
            db_session,
            attempt_id=attempt.id,
            deadline_at=now + timedelta(seconds=90),
            now=now,
        )
        db_session.add(winning_job)
        db_session.commit()

    monkeypatch.setattr(db_session, "rollback", rollback_then_progress_winner)

    returned = process_template_suggestion(db_session, job_id=job.id)

    db_session.refresh(job)
    db_session.refresh(attempt)
    assert winner_started
    assert returned.status is TemplateSuggestionStatus.processing
    assert job.status is TemplateSuggestionStatus.processing
    assert attempt.status is AttemptStatus.submitted
    assert provider_calls == []


@pytest.mark.parametrize(
    "output",
    [
        '{"template_id":null,"confidence":"low","reason":"Unclear"}',
        '{"template_id":null,"confidence":"high","reason":"Current template is sufficient"}',
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
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", lambda **_: (output, {"total_tokens": 2}))
    processed = process_template_suggestion(db_session, job_id=job.id)
    _, result = get_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert result is None
    assert processed.status is (TemplateSuggestionStatus.completed if ('"low"' in output or '"template_id":null' in output) else TemplateSuggestionStatus.failed)


def test_selected_template_is_snapshotted_and_sent_as_current_context(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    candidates = _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(
        db_session, user, transcript_id=transcript.id, selected_template_id=candidates[0].id,
    )
    assert job.selected_template_snapshot_json == {
        "id": str(candidates[0].id), "name": candidates[0].name, "description": candidates[0].description,
    }
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", lambda *_args, **_kwargs: ("safe", "", []))
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "token")
    seen = []
    def provider(**kwargs):
        seen.append(kwargs["request_body"]["messages"][1]["content"])
        return '{"template_id":null,"confidence":"high","reason":"Already appropriate"}', {"total_tokens": 2}
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", provider)
    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.completed
    assert '"current_template":{"id":"%s"' % candidates[0].id in seen[0]


def test_provider_returning_current_template_is_suppressed(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = _configured_user(make_user, make_llm_config, make_llm_selection)
    transcript = _transcript(db_session, user, "x" * 1300)
    candidates = _templates(make_template, user)
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(
        db_session, user, transcript_id=transcript.id, selected_template_id=candidates[0].id,
    )
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", lambda *_args, **_kwargs: ("safe", "", []))
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "token")
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **_: (
            '{"template_id":"%s","confidence":"high","reason":"Best fit"}' % candidates[0].id,
            {"total_tokens": 2},
        ),
    )

    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.completed
    _, result = get_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert result is None


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
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", lambda **_: (_ for _ in ()).throw(RuntimeError("sensitive provider detail")))
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
            name=f"Sensitive Template Name {index}",
            description="Sensitive template description",
        )
        for index in range(2)
    ]
    monkeypatch.setattr("app.services.template_suggestions.try_publish_task_dispatch_safely", lambda *_: None)
    monkeypatch.setattr("app.services.template_suggestions.redact_transient_text", lambda *_args, **_kwargs: {"redacted_text": "safe", "phi_index": []})
    monkeypatch.setattr("app.services.template_suggestions._apply_manual_pii_redaction", lambda *_args, **_kwargs: ("safe", "", []))
    monkeypatch.setattr("app.services.template_suggestions._resolve_generation_credential", lambda *_: "credential-should-not-appear")
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **_: (
            '{"template_id":"%s","confidence":"high","reason":"Sensitive provider explanation"}' % candidates[0].id,
            {"total_tokens": 12},
        ),
    )
    caplog.set_level(logging.INFO, logger="openscribe.template_suggestion")

    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    assert process_template_suggestion(db_session, job_id=job.id).status is TemplateSuggestionStatus.completed
    get_template_suggestion(db_session, user, transcript_id=transcript.id)

    events = [record.event for record in caplog.records if record.name == "openscribe.template_suggestion"]
    assert {
        "template_suggestion_queue_started",
        "template_suggestion_queued",
        "template_suggestion_worker_started",
        "template_suggestion_prepared",
        "template_suggestion_provider_submitted",
        "template_suggestion_completed",
        "template_suggestion_status_read",
    } <= set(events)
    lifecycle_records = [record for record in caplog.records if record.name == "openscribe.template_suggestion"]
    assert all(f"event={record.event}" in record.getMessage() for record in lifecycle_records)
    job_record = next(record for record in lifecycle_records if record.event == "template_suggestion_completed")
    assert f"template_suggestion_job_id={job.id}" in job_record.getMessage()
    assert f"transcript_id={transcript.id}" in job_record.getMessage()
    assert "status=completed" in job_record.getMessage()
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
