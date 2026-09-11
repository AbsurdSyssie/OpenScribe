"""Focused queue-only tests for the initial consultation-split boundary."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionStatus,
    ProviderAttempt,
    TaskDispatchOutbox,
    TaskDispatchState,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    utcnow,
)
from app.services.consultation_split_queue import queue_or_reuse_split_analysis
from app.services.consultation_splits import create_split_analysis, read_split_execution_json
from app.services.content_crypto import encrypt_json_for_owner
from app.services.quotas import mark_provider_attempt_submitted
from app.services.transcripts import set_freeform_working_note_text


def _enabled_source(db, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    make_user_app_preference(user=owner, preferences_json={"split_consultations_into_separate_notes": True})
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini", "override-model"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=config.available_models_json)
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
    return transcript, config


def test_queue_gate_short_circuits_before_source_or_provider_work(db_session, make_user, monkeypatch):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    monkeypatch.setattr(
        "app.services.consultation_split_queue.prepare_source_bound_consultation_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("source preparation ran"),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_queue.resolve_user_llm",
        lambda *_args, **_kwargs: pytest.fail("provider selection ran"),
    )

    result = queue_or_reuse_split_analysis(db_session, owner, transcript_id=uuid4())

    assert result.outcome == "disabled"
    assert db_session.scalar(select(ConsultationSplitAnalysis)) is None


def test_queue_creates_encrypted_atomic_work_and_reuses_queued_row(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    published = []
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", published.append)
    monkeypatch.setattr(
        "app.services.llm.read_active_team_llm_bearer_token",
        lambda *_args, **_kwargs: pytest.fail("queue must not resolve provider credentials"),
    )
    monkeypatch.setattr(
        "app.services.llm_adapters.runtime.invoke_llm",
        lambda *_args, **_kwargs: pytest.fail("queue must not invoke a provider"),
    )

    first = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert first.outcome == "queued"
    assert first.analysis is not None and first.execution is not None
    assert first.execution.llm_config_id == config.id
    assert first.execution.request_payload_encrypted and "Synthetic working note" not in first.execution.request_payload_encrypted
    assert first.analysis.source_snapshot_encrypted and "Synthetic working note" not in first.analysis.source_snapshot_encrypted
    request = read_split_execution_json(
        db_session, owner, execution=first.execution, field="request_payload_encrypted"
    )
    snapshot = read_split_execution_json(
        db_session, owner, execution=first.execution, field="provider_snapshot_encrypted"
    )
    assert request and request["model"] == config.model_name
    assert snapshot and snapshot["model"] == config.model_name
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == first.execution.id))
    assert dispatch is not None
    assert published == [dispatch.task_id]

    second = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert second.outcome == "queued"
    assert second.execution is not None and second.execution.id == first.execution.id
    assert len(db_session.scalars(select(ConsultationSplitExecution)).all()) == 1


def test_queue_reuses_terminal_rows_and_fails_closed_for_passive_queue(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    first = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert first.analysis is not None
    first.analysis.proposal_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_analyses",
        field="proposal_encrypted",
        record_id=first.analysis.id,
        plaintext={"topics": []},
    )
    first.analysis.status = ConsultationSplitAnalysisStatus.ready
    db_session.commit()
    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "ready"

    first.analysis.status = ConsultationSplitAnalysisStatus.failed
    db_session.commit()
    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "failed"

    # A new current source with a passive queued analysis gets no speculative
    # reservation or outbox repair in this slice.
    set_freeform_working_note_text(db_session, transcript=transcript, plaintext="Changed synthetic note")
    db_session.flush()
    from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis

    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    state = prepared.source_state
    create_split_analysis(
        db_session, owner, transcript_id=transcript.id, source_fingerprint=state.source_fingerprint,
        source_snapshot=prepared.source_snapshot, candidate_template_snapshot=prepared.candidate_template_snapshot,
    )
    db_session.commit()
    passive = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert passive.outcome == "incomplete"
    assert passive.analysis is not None
    assert db_session.scalars(
        select(ConsultationSplitExecution).where(ConsultationSplitExecution.analysis_id == passive.analysis.id)
    ).all() == []


@pytest.mark.parametrize("status", [ConsultationSplitAnalysisStatus.ready, ConsultationSplitAnalysisStatus.not_required])
def test_queue_terminal_cache_requires_encrypted_proposal(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch, status,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    first = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert first.analysis is not None
    first.analysis.status = status
    db_session.commit()

    result = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert result.outcome == "incomplete"

    first.analysis.proposal_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_analyses",
        field="proposal_encrypted",
        record_id=first.analysis.id,
        plaintext={"topics": []},
    )
    db_session.commit()
    result = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert result.outcome == status.value


def test_queue_reuse_strictly_matches_queued_and_processing_execution_states(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)
    first = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert first.analysis is not None and first.execution is not None
    assert first.created_new_work is True and first.queued_new_work is True
    first_dispatch = db_session.scalar(
        select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == first.execution.id)
    )
    assert first_dispatch is not None and first_dispatch.state is TaskDispatchState.pending

    reused = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert reused.outcome == "queued"
    assert reused.created_new_work is False and reused.queued_new_work is False

    attempt = db_session.scalar(
        select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == first.execution.id)
    )
    assert attempt is not None
    first.analysis.status = ConsultationSplitAnalysisStatus.processing
    first.execution.status = ConsultationSplitExecutionStatus.processing
    mark_provider_attempt_submitted(
        db_session,
        attempt_id=attempt.id,
        deadline_at=utcnow() + timedelta(minutes=10),
    )
    db_session.commit()
    processing = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert processing.outcome == "processing"
    assert processing.created_new_work is False

    first.execution.status = ConsultationSplitExecutionStatus.queued
    db_session.commit()
    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "incomplete"

    first.execution.status = ConsultationSplitExecutionStatus.processing
    db_session.commit()
    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "processing"



def test_queue_reuse_rejects_processing_with_reserved_attempt(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)
    queued = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert queued.analysis is not None and queued.execution is not None
    queued.analysis.status = ConsultationSplitAnalysisStatus.processing
    queued.execution.status = ConsultationSplitExecutionStatus.processing
    db_session.commit()

    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "incomplete"


def test_queue_reuse_rejects_expired_reservation_and_processing_deadline(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)
    first = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert first.analysis is not None and first.execution is not None
    attempt = db_session.scalar(
        select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == first.execution.id)
    )
    assert attempt is not None

    attempt.authorized_at = utcnow() - timedelta(minutes=10)
    attempt.reservation_valid_until = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "incomplete"

    attempt.reservation_valid_until = utcnow() + timedelta(minutes=10)
    first.analysis.status = ConsultationSplitAnalysisStatus.processing
    first.execution.status = ConsultationSplitExecutionStatus.processing
    mark_provider_attempt_submitted(
        db_session, attempt_id=attempt.id, deadline_at=utcnow() + timedelta(minutes=10)
    )
    attempt.deadline_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id).outcome == "incomplete"


def test_queue_rolls_back_new_analysis_when_queue_step_fails(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr(
        "app.services.consultation_split_queue.queue_split_execution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppError(429, "quota_exceeded", "Quota exhausted")),
    )

    with pytest.raises(AppError):
        queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert db_session.scalars(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.transcript_id == transcript.id)).all() == []
    assert db_session.scalars(select(ConsultationSplitExecution).where(ConsultationSplitExecution.transcript_id == transcript.id)).all() == []
    assert db_session.scalars(select(TaskDispatchOutbox)).all() == []


def test_queue_rolls_back_on_actual_quota_disabled(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    owner.daily_token_limit = owner.monthly_token_limit = 0
    db_session.commit()

    with pytest.raises(AppError) as quota:
        queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert quota.value.code == "quota_disabled"
    assert db_session.scalar(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.transcript_id == transcript.id)) is None
    assert db_session.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.transcript_id == transcript.id)) is None
    assert db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.transcript_id == transcript.id)) is None
    assert db_session.scalar(select(TaskDispatchOutbox)) is None


def test_queue_fast_path_failure_leaves_pending_outbox(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_args: None)

    result = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == result.execution.id))
    assert dispatch is not None
    assert dispatch.state.value == "pending"


def test_queue_rethrows_unexpected_integrity_error(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    unexpected = IntegrityError(
        "insert",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="unexpected_constraint")),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_queue.create_split_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(unexpected),
    )

    with pytest.raises(IntegrityError) as raised:
        queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert raised.value is unexpected


def test_queue_expected_analysis_unique_race_reuses_without_second_reservation(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    first = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert first.analysis is not None and first.execution is not None
    execution_count = len(db_session.scalars(select(ConsultationSplitExecution)).all())
    attempt_count = len(db_session.scalars(select(ProviderAttempt)).all())
    dispatch_count = len(db_session.scalars(select(TaskDispatchOutbox)).all())

    expected = IntegrityError(
        "insert",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_consultation_split_analyses_owner_source")),
    )
    cache_calls = 0

    def simulated_cache(*_args, **_kwargs):
        nonlocal cache_calls
        cache_calls += 1
        return None if cache_calls == 1 else first.analysis

    monkeypatch.setattr("app.services.consultation_split_queue.find_cached_prepared_consultation_split_analysis", simulated_cache)
    monkeypatch.setattr(
        "app.services.consultation_split_queue.create_split_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(expected),
    )

    result = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert result.outcome == "queued"
    assert result.created_new_work is False
    assert result.execution is not None and result.execution.id == first.execution.id
    assert len(db_session.scalars(select(ConsultationSplitExecution)).all()) == execution_count
    assert len(db_session.scalars(select(ProviderAttempt)).all()) == attempt_count
    assert len(db_session.scalars(select(TaskDispatchOutbox)).all()) == dispatch_count


def test_queue_uses_user_resolved_model_not_config_default(
    db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, make_user_llm_preference, monkeypatch,
):
    owner = make_user(email=f"queue-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    make_user_llm_preference(user=owner, preferred_model_name="override-model")
    monkeypatch.setattr("app.services.consultation_split_queue.try_publish_task_dispatch_safely", lambda *_: None)

    result = queue_or_reuse_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert result.execution is not None
    assert result.execution.provider_model == "override-model"
    request = read_split_execution_json(db_session, owner, execution=result.execution, field="request_payload_encrypted")
    assert request and request["model"] == "override-model"
