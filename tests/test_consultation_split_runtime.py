"""Focused at-most-once runtime tests for initial split analysis."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionStatus,
    ProviderAttempt,
    ProviderUsageEvent,
    TaskDispatchOutbox,
    TaskDispatchState,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    UserAppPreference,
    utcnow,
)
from app.services.consultation_split_queue import queue_or_reuse_split_analysis
from app.services.consultation_split_runtime import (
    CONSULTATION_SPLIT_MAX_RESPONSE_CHARS,
    process_consultation_split_analysis_execution,
)
from app.services.content_crypto import encrypt_json_for_owner
from app.services.quotas import mark_provider_attempt_submitted
from app.services.transcripts import set_freeform_working_note_text


def _queued_work(
    db,
    owner,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    preference = make_user_app_preference(
        user=owner, preferences_json={"split_consultations_into_separate_notes": True}
    )
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    transcript_id = transcript.id
    preference_id = preference.id
    set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic working note")
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    db.commit()
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)
    queued = queue_or_reuse_split_analysis(db, owner, transcript_id=transcript_id)
    assert queued.analysis is not None and queued.execution is not None
    execution_id = queued.execution.id
    dispatch = db.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == execution_id))
    assert dispatch is not None
    dispatch.state = TaskDispatchState.published
    dispatch.published_at = utcnow()
    db.commit()
    db.rollback()
    return transcript_id, execution_id, preference_id


def _rows(db, execution_id):
    execution = db.get(ConsultationSplitExecution, execution_id)
    assert execution is not None
    analysis = db.get(ConsultationSplitAnalysis, execution.analysis_id)
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution_id))
    dispatch = db.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == execution_id))
    assert analysis is not None and attempt is not None and dispatch is not None
    return analysis, execution, attempt, dispatch


def _provider_success(text, usage=None):
    return text, (usage if usage is not None else {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18})


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (json.dumps({"topics": []}), "not_required"),
        (
            json.dumps(
                {
                    "topics": [
                        {"title": "Primary", "is_primary": True, "disposition": "separate_note", "template_id": None},
                    ]
                }
            ),
            "not_required",
        ),
        (
            json.dumps(
                {
                    "topics": [
                        {"title": "Primary", "is_primary": True, "disposition": "separate_note", "template_id": None},
                        {"title": "Secondary", "is_primary": False, "disposition": "separate_note", "template_id": None},
                    ]
                }
            ),
            "ready",
        ),
    ],
)
def test_runtime_commits_valid_zero_single_or_multiple_topic_response_once(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch, response, expected,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    calls = []
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr(
        "app.services.consultation_split_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or _provider_success(response)),
    )

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == expected
    assert calls == [1]
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert analysis.status.value == expected
    assert analysis.proposal_encrypted and "Primary" not in analysis.proposal_encrypted
    assert execution.status is ConsultationSplitExecutionStatus.completed
    assert execution.recoverable_response_encrypted is None
    assert attempt.status is AttemptStatus.settled
    events = db_session.scalars(
        select(ProviderUsageEvent).where(ProviderUsageEvent.consultation_split_execution_id == execution_id)
    ).all()
    assert len(events) == 1 and events[0].total_tokens == 18
    db_session.rollback()
    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == expected
    assert calls == [1]


@pytest.mark.parametrize(
    "response",
    ["not json", "x" * (CONSULTATION_SPLIT_MAX_RESPONSE_CHARS * 8 + 1), json.dumps({"topics": [{"title": "Unknown", "is_primary": True, "disposition": "separate_note", "template_id": str(uuid4())}]})],
)
def test_runtime_rejects_invalid_or_unavailable_provider_output_once(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch, response,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    calls = []
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: (calls.append(1) or _provider_success(response)))

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "failed" and result.error_code == "consultation_split_analysis_invalid_output"
    assert calls == [1]
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.failed
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert attempt.status is AttemptStatus.settled
    assert len(db_session.scalars(select(ProviderUsageEvent).where(ProviderUsageEvent.consultation_split_execution_id == execution_id)).all()) == 1


@pytest.mark.parametrize("failure", [AppError(502, "provider_failed", "unsafe provider body", {"provider_error_code": "bad request", "provider_http_status": 400}), RuntimeError("unsafe provider body")])
def test_runtime_terminalizes_provider_errors_without_exception_text(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch, caplog, failure,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: (_ for _ in ()).throw(failure))

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "failed" and result.error_code == "consultation_split_provider_failed"
    assert "unsafe provider body" not in caplog.text
    _analysis, _execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert attempt.status is AttemptStatus.settled


def test_runtime_accepts_a_fast_delivery_while_outbox_is_still_pending(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    _analysis, _execution, _attempt, dispatch = _rows(db_session, execution_id)
    dispatch.state = TaskDispatchState.pending
    dispatch.published_at = None
    db_session.commit()
    calls = []
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr(
        "app.services.consultation_split_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or _provider_success(json.dumps({"topics": []}))),
    )

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "not_required"
    _analysis, _execution, attempt, dispatch = _rows(db_session, execution_id)
    assert attempt.status is AttemptStatus.settled and calls == [1]
    # The publisher may commit after this fast worker. Its late published
    # marker must be valid history, not a reason to repeat the provider call.
    dispatch.state = TaskDispatchState.published
    dispatch.published_at = utcnow()
    db_session.commit()
    db_session.rollback()
    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "not_required"
    assert calls == [1]


@pytest.mark.parametrize("dispatch_state", [TaskDispatchState.cancelled, TaskDispatchState.failed])
def test_runtime_never_invokes_cancelled_or_failed_dispatch(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch, dispatch_state,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    _analysis, _execution, _attempt, dispatch = _rows(db_session, execution_id)
    dispatch.state = dispatch_state
    dispatch.published_at = None
    if dispatch_state is TaskDispatchState.cancelled:
        dispatch.cancelled_at = utcnow()
    else:
        dispatch.failed_at = utcnow()
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: pytest.fail("provider invoked"))

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "noop"
    _analysis, _execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert attempt.status is AttemptStatus.reserved


def test_runtime_preference_off_cancels_queued_without_preparation_or_provider(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    preference = db_session.get(UserAppPreference, preference_id)
    assert preference is not None
    preference.preferences_json = {"split_consultations_into_separate_notes": False}
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: pytest.fail("credential resolved"))
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: pytest.fail("provider invoked"))

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "failed" and result.error_code == "consultation_split_preference_disabled"
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.failed
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert attempt.status is AttemptStatus.cancelled


def test_runtime_preference_turnoff_after_provider_return_settles_known_usage_and_suppresses_proposal(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    calls = []
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: (calls.append(1) or _provider_success(json.dumps({"topics": []}))))
    gate = iter([True, True, False])
    monkeypatch.setattr("app.services.consultation_split_runtime.consultation_splitting_enabled", lambda *_args: next(gate))

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "failed" and result.error_code == "consultation_split_preference_disabled"
    assert calls == [1]
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert analysis.proposal_encrypted is None
    assert execution.recoverable_response_encrypted is None
    assert attempt.status is AttemptStatus.settled and attempt.reported_total_tokens == 18


def test_runtime_recovers_persisted_response_without_a_second_provider_call(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, deadline_at=utcnow() + timedelta(minutes=10))
    analysis.status = ConsultationSplitAnalysisStatus.processing
    execution.status = ConsultationSplitExecutionStatus.processing
    execution.recoverable_response_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_executions", field="recoverable_response_encrypted",
        record_id=execution.id, plaintext={"text": json.dumps({"topics": []}), "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}},
    )
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: pytest.fail("second provider call"))

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "not_required"
    _analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert execution.recoverable_response_encrypted is None and attempt.status is AttemptStatus.settled


def test_runtime_preference_off_after_persisted_response_settles_known_usage_without_proposal(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, deadline_at=utcnow() + timedelta(minutes=10))
    analysis.status = ConsultationSplitAnalysisStatus.processing
    execution.status = ConsultationSplitExecutionStatus.processing
    execution.recoverable_response_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_executions", field="recoverable_response_encrypted",
        record_id=execution.id, plaintext={"text": json.dumps({"topics": []}), "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}},
    )
    preference = db_session.get(UserAppPreference, preference_id)
    assert preference is not None
    preference.preferences_json = {"split_consultations_into_separate_notes": False}
    db_session.commit()

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "failed" and result.error_code == "consultation_split_preference_disabled"
    analysis, _execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert analysis.proposal_encrypted is None
    assert attempt.status is AttemptStatus.settled and attempt.reported_total_tokens == 7


def test_runtime_processing_reserved_state_never_invokes_provider(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, _attempt, _dispatch = _rows(db_session, execution_id)
    analysis.status = ConsultationSplitAnalysisStatus.processing
    execution.status = ConsultationSplitExecutionStatus.processing
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: pytest.fail("provider invoked"))

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "in_flight"


def test_runtime_submitted_without_response_never_invokes_again(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, deadline_at=utcnow() + timedelta(minutes=10))
    analysis.status = ConsultationSplitAnalysisStatus.processing
    execution.status = ConsultationSplitExecutionStatus.processing
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: pytest.fail("second provider call"))

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "in_flight"


def test_runtime_response_or_finalization_persistence_failure_preserves_at_most_once_recovery(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    calls = []
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: (calls.append(1) or _provider_success(json.dumps({"topics": []}))))
    import app.services.consultation_split_runtime as runtime

    original_encrypt = runtime.encrypt_json_for_existing_owner
    def fail_finalization(*args, **kwargs):
        if kwargs.get("table") == "consultation_split_analyses":
            raise RuntimeError("storage unavailable")
        return original_encrypt(*args, **kwargs)
    monkeypatch.setattr(runtime, "encrypt_json_for_existing_owner", fail_finalization)

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "in_flight"
    _analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert execution.recoverable_response_encrypted is not None and attempt.status is AttemptStatus.submitted
    db_session.rollback()
    monkeypatch.setattr(runtime, "encrypt_json_for_existing_owner", original_encrypt)
    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "not_required"
    assert calls == [1]


def test_runtime_response_commit_failure_never_triggers_a_second_provider_call(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    calls = []
    fail_commit = {"value": False}
    real_commit = db_session.commit
    def commit():
        if fail_commit["value"]:
            raise RuntimeError("response commit failed")
        return real_commit()
    monkeypatch.setattr(db_session, "commit", commit)
    def reply(**_kwargs):
        calls.append(1)
        fail_commit["value"] = True
        return _provider_success(json.dumps({"topics": []}))
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", reply)

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "in_flight"
    fail_commit["value"] = False
    db_session.rollback()
    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "in_flight"
    assert calls == [1]


def test_runtime_source_change_after_provider_success_terminalizes_stale_with_known_usage(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    def alter_source_then_reply(**_kwargs):
        transcript = db_session.get(Transcript, transcript_id)
        assert transcript is not None
        set_freeform_working_note_text(db_session, transcript=transcript, plaintext="Changed after provider call")
        db_session.commit()
        return _provider_success(json.dumps({"topics": []}))
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", alter_source_then_reply)

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "stale" and result.error_code == "consultation_split_source_stale"
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.stale
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert attempt.status is AttemptStatus.settled and attempt.reported_total_tokens == 18


def test_runtime_root_expiry_after_provider_success_settles_and_does_not_leave_in_flight(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    def expire_then_reply(**_kwargs):
        transcript = db_session.get(Transcript, transcript_id)
        assert transcript is not None
        transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()
        return _provider_success(json.dumps({"topics": []}))
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", expire_then_reply)

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "failed" and result.error_code == "consultation_split_source_expired"
    _analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert execution.status is ConsultationSplitExecutionStatus.failed and attempt.status is AttemptStatus.settled


def test_runtime_normalizes_incoherent_usage_to_conservative_settlement(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-runtime-{uuid4()}@example.com")
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    from app.services.consultation_split_runtime import _normalized_usage

    # A single reported component is still coherent with a larger total. The
    # stricter sum rule applies only when both components are present.
    assert _normalized_usage({"input_tokens": 10, "total_tokens": 15})["total_tokens"] == 15
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", lambda **_kwargs: _provider_success(json.dumps({"topics": []}), {"input_tokens": 10, "output_tokens": 9, "total_tokens": 15, "duration_ms": True}))

    assert process_consultation_split_analysis_execution(db_session, execution_id=execution_id).outcome == "not_required"
    _analysis, execution, attempt, _dispatch = _rows(db_session, execution_id)
    assert execution.total_token_count is None
    assert attempt.status is AttemptStatus.settled and attempt.reported_total_tokens is None
    assert attempt.settled_units == attempt.reserved_units


def test_split_task_swallows_unexpected_exception_without_raw_text(monkeypatch, caplog):
    from app import tasks

    secret = "provider response must not be logged"
    monkeypatch.setattr("app.tasks.process_consultation_split_analysis_execution", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)))
    tasks.process_consultation_split_execution_task(execution_id=str(uuid4()))
    assert secret not in caplog.text
