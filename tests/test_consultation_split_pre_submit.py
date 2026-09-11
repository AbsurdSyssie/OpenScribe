"""Focused tests for the claim-free split-analysis pre-submit boundary."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptOutcome,
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionStatus,
    PostConsultationDictation,
    ProviderAttempt,
    ProviderUsageEvent,
    TaskDispatchOutbox,
    TaskDispatchState,
    TeamLlmConfig,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    utcnow,
)
from app.services.consultation_split_pre_submit import prepare_queued_split_analysis_for_submission
from app.services.consultation_split_queue import queue_or_reuse_split_analysis
from app.services.consultation_splits import read_split_execution_json
from app.services.content_crypto import encrypt_json_for_owner, encrypt_text_for_owner
from app.services.transcripts import create_manual_pii_entity, set_freeform_working_note_text


def _queued_work(
    db,
    owner,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    *,
    make_template=None,
):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    make_user_app_preference(
        user=owner,
        preferences_json={"split_consultations_into_separate_notes": True},
    )
    config = make_llm_config(
        team=owner.team,
        actor=owner,
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    template = make_template(owner=owner, actor=owner) if make_template is not None else None
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
    set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic working note")
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    db.commit()
    transcript_id = transcript.id
    config_id = config.id
    template_id = template.id if template is not None else None
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)
    queued = queue_or_reuse_split_analysis(db, owner, transcript_id=transcript_id)
    assert queued.analysis is not None and queued.execution is not None
    analysis_id = queued.analysis.id
    execution_id = queued.execution.id
    db.rollback()
    template = db.get(type(template), template_id) if template is not None else None
    db.rollback()
    return transcript_id, config_id, analysis_id, execution_id, template


def _rows(db, *, execution_id):
    execution = db.get(ConsultationSplitExecution, execution_id)
    assert execution is not None
    analysis = db.get(ConsultationSplitAnalysis, execution.analysis_id)
    attempt = db.scalar(
        select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution_id)
    )
    dispatch = db.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == execution_id))
    assert analysis is not None and attempt is not None and dispatch is not None
    return analysis, execution, attempt, dispatch


def test_pre_submit_success_returns_only_memory_values_and_keeps_final_locks_active(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    caplog,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    opaque_secret = "opaque-provider-secret-never-persisted"
    def resolve_credential_without_db_transaction(_config):
        assert not db_session.in_transaction()
        return opaque_secret

    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential",
        resolve_credential_without_db_transaction,
    )
    monkeypatch.setattr(
        "app.services.llm.resolve_user_llm",
        lambda *_args, **_kwargs: pytest.fail("queued work must not be retargeted"),
    )
    monkeypatch.setattr(
        "app.services.llm_adapters.runtime.invoke_llm",
        lambda *_args, **_kwargs: pytest.fail("pre-submit must not call a provider"),
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "prepared" and result.prepared is not None
    assert result.prepared.execution_id == execution_id
    assert result.prepared.credential == opaque_secret
    assert result.prepared.request_body["model"] == result.prepared.provider_snapshot.model
    assert db_session.in_transaction()
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.queued
    assert execution.status is ConsultationSplitExecutionStatus.queued
    assert attempt.status is AttemptStatus.reserved
    assert attempt.submitted_at is None and attempt.settled_at is None
    assert opaque_secret not in execution.request_payload_encrypted
    assert opaque_secret not in execution.provider_snapshot_encrypted
    assert opaque_secret not in caplog.text
    db_session.rollback()


@pytest.mark.parametrize("dispatch_state", [TaskDispatchState.pending, TaskDispatchState.published])
def test_pre_submit_accepts_viable_pending_and_published_dispatch(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    dispatch_state,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    _analysis, _execution, _attempt, dispatch = _rows(db_session, execution_id=execution_id)
    if dispatch_state is TaskDispatchState.published:
        dispatch.state = TaskDispatchState.published
        dispatch.published_at = utcnow()
        db_session.commit()
    else:
        db_session.rollback()
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "prepared"
    db_session.rollback()


@pytest.mark.parametrize("change", ["working_note", "dictation", "transcript", "manual_pii", "candidate"])
def test_pre_submit_marks_every_current_source_change_stale_without_usage(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    make_template,
    monkeypatch,
    change,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    transcript_id, _config_id, _analysis_id, execution_id, template = _queued_work(
        db_session,
        owner,
        make_llm_config,
        make_llm_selection,
        make_user_app_preference,
        monkeypatch,
        make_template=make_template,
    )
    transcript = db_session.get(Transcript, transcript_id)
    assert transcript is not None
    if change == "working_note":
        set_freeform_working_note_text(db_session, transcript=transcript, plaintext="Changed note")
        transcript.working_note_updated_at = utcnow() + timedelta(seconds=1)
    elif change == "dictation":
        dictation = PostConsultationDictation(
            id=uuid4(),
            transcript_id=transcript.id,
            owner_user_id=owner.id,
            team_id=owner.team_id,
            combined_edited_text_encrypted="",
            is_combined_text_user_edited=True,
        )
        dictation.combined_edited_text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="post_consultation_dictations",
            field="combined_edited_text_encrypted",
            record_id=dictation.id,
            plaintext="Changed dictation",
        )
        db_session.add(dictation)
    elif change == "transcript":
        transcript.current_draft_text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            plaintext="New transcript draft",
        )
    elif change == "manual_pii":
        create_manual_pii_entity(
            db_session, owner, transcript_id=transcript.id, entity_type="PERSON", value="Synthetic person"
        )
    else:
        assert template is not None
        template.description = "Changed candidate description"
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "stale"
    analysis, execution, attempt, dispatch = _rows(db_session, execution_id=execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.stale
    assert execution.status is ConsultationSplitExecutionStatus.cancelled
    assert attempt.status is AttemptStatus.cancelled
    assert attempt.submitted_at is None and attempt.settled_at is None
    assert dispatch.state is TaskDispatchState.cancelled
    assert db_session.scalars(
        select(ProviderUsageEvent).where(ProviderUsageEvent.consultation_split_execution_id == execution_id)
    ).all() == []


@pytest.mark.parametrize("mismatch", ["config", "snapshot", "request"])
def test_pre_submit_fails_bound_config_snapshot_or_request_mismatch(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    mismatch,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, _attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    if mismatch == "config":
        config = db_session.get(TeamLlmConfig, config_id)
        assert config is not None
        config.base_url = "https://changed.example.test/v1"
    elif mismatch == "snapshot":
        snapshot = read_split_execution_json(
            db_session, owner, execution=execution, field="provider_snapshot_encrypted"
        )
        assert snapshot is not None
        snapshot["model"] = "changed-model"
        execution.provider_snapshot_encrypted = encrypt_json_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="consultation_split_executions",
            field="provider_snapshot_encrypted",
            record_id=execution.id,
            plaintext=snapshot,
        )
    else:
        request = read_split_execution_json(
            db_session, owner, execution=execution, field="request_payload_encrypted"
        )
        assert request is not None
        request["temperature"] = 0.9
        execution.request_payload_encrypted = encrypt_json_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="consultation_split_executions",
            field="request_payload_encrypted",
            record_id=execution.id,
            plaintext=request,
        )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "failed"
    assert result.error_code == "consultation_split_provider_binding_invalid"
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.failed
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert attempt.status is AttemptStatus.cancelled


def test_pre_submit_credential_failure_is_safe_and_keeps_published_dispatch_history(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    caplog,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    _analysis, _execution, _attempt, dispatch = _rows(db_session, execution_id=execution_id)
    dispatch.state = TaskDispatchState.published
    dispatch.published_at = utcnow()
    db_session.commit()
    secret = "credential-value-must-not-be-logged"

    def fail_credential(_config):
        raise AppError(502, "vault_read_failed", secret)

    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", fail_credential)

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "failed"
    assert result.error_code == "consultation_split_credential_unavailable"
    _analysis, _execution, attempt, dispatch = _rows(db_session, execution_id=execution_id)
    assert attempt.status is AttemptStatus.cancelled
    assert attempt.submitted_at is None and attempt.settled_at is None
    assert dispatch.state is TaskDispatchState.published
    assert secret not in caplog.text


@pytest.mark.parametrize("expired", ["root", "reservation"])
def test_pre_submit_cancels_expired_root_or_reservation(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    expired,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    _analysis, _execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    if expired == "root":
        transcript = db_session.get(Transcript, transcript_id)
        assert transcript is not None
        transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    else:
        future_now = attempt.reservation_valid_until + timedelta(seconds=1)
    db_session.commit()
    if expired == "reservation":
        monkeypatch.setattr("app.services.consultation_split_pre_submit.utcnow", lambda: future_now)
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "failed"
    assert result.error_code == f"consultation_split_{'source' if expired == 'root' else 'reservation'}_expired"
    _analysis, execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    assert execution.status is ConsultationSplitExecutionStatus.cancelled
    assert attempt.status is AttemptStatus.cancelled


@pytest.mark.parametrize("winner", ["execution", "analysis", "attempt", "dispatch"])
def test_pre_submit_lost_worker_states_are_content_safe_noops(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    winner,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, attempt, dispatch = _rows(db_session, execution_id=execution_id)
    if winner == "execution":
        execution.status = ConsultationSplitExecutionStatus.processing
    elif winner == "analysis":
        analysis.status = ConsultationSplitAnalysisStatus.processing
    elif winner == "attempt":
        attempt.status = AttemptStatus.cancelled
        attempt.outcome = AttemptOutcome.cancelled
        attempt.cancelled_at = utcnow()
    else:
        dispatch.state = TaskDispatchState.cancelled
        dispatch.cancelled_at = utcnow()
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "noop"
    _analysis, current_execution, current_attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    assert current_execution.error_code is None
    if winner != "attempt":
        assert current_attempt.status is AttemptStatus.reserved


def test_pre_submit_rejects_a_session_with_an_active_transaction(
    db_session,
):
    db_session.execute(select(Transcript.id).limit(1))
    with pytest.raises(AppError) as exc:
        prepare_queued_split_analysis_for_submission(db_session, execution_id=uuid4())
    assert exc.value.code == "consultation_split_pre_submit_transaction_active"
    db_session.rollback()


def test_pre_submit_rechecks_deadline_after_current_source_proof(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    _analysis, _execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    before_deadline = attempt.reservation_valid_until - timedelta(seconds=1)
    after_deadline = attempt.reservation_valid_until + timedelta(seconds=1)
    db_session.rollback()
    clock = iter([before_deadline, after_deadline, after_deadline])
    monkeypatch.setattr("app.services.consultation_split_pre_submit.utcnow", lambda: next(clock))
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.current_consultation_split_analysis_source_matches",
        lambda *_args, **_kwargs: True,
    )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "failed"
    assert result.error_code == "consultation_split_reservation_expired"
    _analysis, execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    assert execution.status is ConsultationSplitExecutionStatus.cancelled
    assert attempt.status is AttemptStatus.cancelled
    assert attempt.submitted_at is None


@pytest.mark.parametrize("corrupt_field", ["source", "request", "invalid_utf8_request"])
def test_pre_submit_normalizes_corrupt_encrypted_content_to_safe_binding_failure(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
    corrupt_field,
):
    owner = make_user(email=f"split-submit-{uuid4()}@example.com")
    _transcript_id, _config_id, _analysis_id, execution_id, _template = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    analysis, execution, _attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    malformed = '{"alg":"AES-256-GCM","ct":"not-base64","dkv":1,"n":"bad","v":1}'
    if corrupt_field == "source":
        analysis.source_snapshot_encrypted = malformed
    elif corrupt_field == "request":
        execution.request_payload_encrypted = malformed
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential", lambda _config: "token"
    )
    if corrupt_field == "invalid_utf8_request":
        original_reader = __import__(
            "app.services.consultation_split_pre_submit", fromlist=["read_split_execution_json"]
        ).read_split_execution_json

        def invalid_utf8_reader(*args, **kwargs):
            if kwargs.get("field") == "request_payload_encrypted":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            return original_reader(*args, **kwargs)

        monkeypatch.setattr(
            "app.services.consultation_split_pre_submit.read_split_execution_json",
            invalid_utf8_reader,
        )

    result = prepare_queued_split_analysis_for_submission(db_session, execution_id=execution_id)

    assert result.outcome == "failed"
    assert result.error_code == "consultation_split_provider_binding_invalid"
    analysis, execution, attempt, _dispatch = _rows(db_session, execution_id=execution_id)
    assert analysis.status is ConsultationSplitAnalysisStatus.failed
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert attempt.status is AttemptStatus.cancelled
