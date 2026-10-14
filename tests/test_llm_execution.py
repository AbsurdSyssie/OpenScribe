from __future__ import annotations

import signal
import threading
import time

import pytest

from app.errors import AppError
from app.services import llm_execution as execution
from app.services.provider_transport_telemetry import validate_transport_event


def test_temporary_429_retries_four_calls_and_records_before_sleep(monkeypatch):
    events, sleeps, calls = [], [], []
    monkeypatch.setattr(execution.random, "uniform", lambda lower, upper: lower)
    monkeypatch.setattr(execution.time, "sleep", lambda seconds: sleeps.append((seconds, len(events))))
    def call(budget):
        calls.append(budget.deadline)
        if len(calls) < 4:
            raise AppError(429, "llm_provider_rate_limited", "capacity", {"provider_http_status": 429, "temporary_capacity": True})
        return "synthetic"
    with execution.execution_policy(budget=execution.ExecutionBudget.seconds(90), observer=lambda event: events.append(event) or True):
        assert execution.bounded_request(call) == "synthetic"
    assert len(calls) == 4 and len(set(calls)) == 1
    assert sleeps == [(1, 1), (2, 2), (4, 3)]
    assert [event["code"] for event in events] == ["rate_limited"] * 3 + ["success"]
    for event in events:
        assert validate_transport_event(event) == event


@pytest.mark.parametrize("status,temporary,retry_after", [(429, False, None), (503, True, None), (429, True, 31), (429, True, 900)])
def test_no_replay_for_hard_quota_ambiguous_or_excess_retry_after(monkeypatch, status, temporary, retry_after):
    calls, events = [], []
    monkeypatch.setattr(execution.time, "sleep", lambda seconds: pytest.fail("must not wait"))
    def call(budget):
        calls.append(True)
        raise AppError(502, "llm_generation_failed", "synthetic", {"provider_http_status": status, "temporary_capacity": temporary, "retry_after_seconds": retry_after})
    with execution.execution_policy(budget=execution.ExecutionBudget.seconds(90), observer=lambda event: events.append(event) or True), pytest.raises(AppError):
        execution.bounded_request(call)
    assert len(calls) == len(events) == 1
    assert events[0]["retry_wait_ms"] == 0


def test_wall_timeout_closes_call_and_restores_signal_before_observation():
    original = signal.getsignal(signal.SIGALRM)
    events = []
    def observe(event):
        assert signal.getsignal(signal.SIGALRM) == original
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
        events.append(event)
        return False  # timeout deadline is already expired; preserve timeout
    with execution.execution_policy(budget=execution.ExecutionBudget.seconds(.02), observer=observe), pytest.raises(AppError) as caught:
        execution.bounded_request(lambda budget: time.sleep(.2))
    assert caught.value.code == "llm_provider_timeout"
    assert events[0]["code"] == "timeout"
    assert signal.getsignal(signal.SIGALRM) == original


def test_active_prior_timer_is_preserved_and_fails_closed():
    original = signal.getsignal(signal.SIGALRM)
    signal.setitimer(signal.ITIMER_REAL, 10, 1)
    try:
        with pytest.raises(AppError) as caught:
            execution.bounded_request(lambda budget: pytest.fail("must not dispatch"))
        assert caught.value.code == "llm_execution_context_unsupported"
        delay, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 9 < delay <= 10 and interval == 1
        assert signal.getsignal(signal.SIGALRM) == original
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def test_thread_context_never_dispatches():
    codes = []
    def run():
        try:
            execution.bounded_request(lambda budget: pytest.fail("must not dispatch"))
        except AppError as exc:
            codes.append(exc.code)
    thread = threading.Thread(target=run)
    thread.start(); thread.join()
    assert codes == ["llm_execution_context_unsupported"]


def test_lost_claim_stops_before_first_dispatch():
    def observer(event):
        return True
    observer.is_active = lambda: False
    with execution.execution_policy(budget=execution.ExecutionBudget.seconds(90), observer=observer), pytest.raises(AppError) as caught:
        execution.bounded_request(lambda budget: pytest.fail("must not dispatch"))
    assert caught.value.code == "llm_attempt_inactive"


def _submitted_draft(db, make_user, make_generated_document):
    from datetime import datetime, timedelta, timezone
    from app.models import (Transcript, TranscriptVersion, TranscriptIngestionMode, TranscriptStatus,
        TemplateMode, GeneratedDocumentStatus, GeneratedDocumentSection, AttemptKind, QuotaResource)
    from app.services.quotas import reserve_provider_attempt, mark_provider_attempt_submitted
    now = datetime.now(timezone.utc)
    owner = make_user()
    root = Transcript(owner_user_id=owner.id, team_id=owner.team_id, title="synthetic",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=now + timedelta(days=30))
    db.add(root); db.flush()
    version = TranscriptVersion(transcript_id=root.id, version_no=1, text_encrypted="synthetic ciphertext")
    db.add(version); db.flush()
    document = make_generated_document(owner=owner, transcript=root, transcript_version=version)
    document.document_mode = TemplateMode.structured
    document.status = GeneratedDocumentStatus.processing
    document.validated_draft_at = now
    db.add(GeneratedDocumentSection(generated_document_id=document.id, section_key="summary",
        section_label="Summary", section_order=0, original_text_encrypted="synthetic ciphertext",
        edited_text_encrypted="synthetic ciphertext", is_edited=False))
    attempt = reserve_provider_attempt(db, team_id=owner.team_id, owner_user_id=owner.id,
        resource=QuotaResource.tokens, attempt_kind=AttemptKind.llm_generation,
        correlation_id=document.id, attempt_number=1, reserved_units=3,
        authorized_at=now, reservation_valid_until=now + timedelta(minutes=5),
        transcript_id=root.id, generated_document_id=document.id)
    mark_provider_attempt_submitted(db, attempt_id=attempt.id, now=now, deadline_at=now + timedelta(seconds=150))
    db.commit()
    return root, document, attempt, now


@pytest.mark.parametrize("root_expired", [False, True])
def test_expiry_promotes_only_validated_live_checkpoint(db_session, make_user, make_generated_document, root_expired):
    from datetime import timedelta
    from app.models import AttemptOutcome, GeneratedDocumentStatus, HallucinationCheckStatus
    from app.services.quota_lifecycle import process_quota_lifecycle
    from app.services.templates import _lock_live_generation_result
    root, document, attempt, now = _submitted_draft(db_session, make_user, make_generated_document)
    if root_expired:
        root.retention_expires_at = now - timedelta(seconds=1)
    attempt.deadline_at = now - timedelta(seconds=1)
    db_session.commit()
    assert process_quota_lifecycle(db_session, now=now) >= 2
    assert document.status is (GeneratedDocumentStatus.failed if root_expired else GeneratedDocumentStatus.ready)
    assert attempt.outcome is AttemptOutcome.unknown and attempt.settled_units == 3
    if not root_expired:
        assert document.hallucination_check_status is HallucinationCheckStatus.not_applicable
    assert not _lock_live_generation_result(db_session, document=document)
    assert process_quota_lifecycle(db_session, now=now) == 0


def test_transport_timeout_persists_after_expiry_and_stops_next_call(db_session, make_user, make_generated_document):
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from app.services.provider_transport_telemetry import transport_observer
    root, document, attempt, now = _submitted_draft(db_session, make_user, make_generated_document)
    attempt.deadline_at = now - timedelta(seconds=1)
    db_session.commit()
    observer = transport_observer(attempt.id, session_factory=lambda: Session(bind=db_session.get_bind(), join_transaction_mode="create_savepoint"))
    event = {"occurred_at": now.isoformat(), "request_ordinal": 1, "duration_ms": 90000,
        "code": "timeout", "http_status": None, "retry_wait_ms": 0}
    assert observer(event) is False
    db_session.refresh(attempt)
    assert attempt.transport_events_json == [event]
    assert observer.is_active() is False


@pytest.mark.parametrize("status", ["not_applicable", "skipped_not_configured"])
def test_checkpoint_without_selected_checker_does_not_report_checker_failure(db_session, make_user, make_generated_document, status):
    from datetime import timedelta
    from app.models import GeneratedDocumentStatus, HallucinationCheckStatus
    from app.services.quota_lifecycle import process_quota_lifecycle
    root, document, attempt, now = _submitted_draft(db_session, make_user, make_generated_document)
    document.hallucination_check_status = HallucinationCheckStatus(status)
    attempt.deadline_at = now - timedelta(seconds=1)
    db_session.commit()
    process_quota_lifecycle(db_session, now=now)
    assert document.status is GeneratedDocumentStatus.ready
    assert document.hallucination_check_status is HallucinationCheckStatus(status)


@pytest.mark.parametrize("mutation", [{"code": 123}, {"prompt": "synthetic"}, {"duration_ms": 0.5}, {"http_status": "429"}])
def test_postgresql_rejects_unsafe_transport_metadata(db_session, make_user, make_generated_document, mutation):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError
    from app.models import ProviderAttempt
    root, document, attempt, now = _submitted_draft(db_session, make_user, make_generated_document)
    event = {"occurred_at": now.isoformat(), "request_ordinal": 1, "duration_ms": 10,
        "code": "success", "http_status": None, "retry_wait_ms": 0, **mutation}
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(update(ProviderAttempt).where(ProviderAttempt.id == attempt.id).values(transport_events_json=[event]))


def test_migration_constraint_is_frozen_and_matches_current_metadata():
    import runpy
    from pathlib import Path
    from app.provider_transport_schema import TRANSPORT_EVENTS_CONSTRAINT
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/b9c0d1e2f3a6_add_provider_transport_events.py"))
    assert migration["TRANSPORT_EVENTS_CONSTRAINT"] == TRANSPORT_EVENTS_CONSTRAINT


def test_additive_migration_upgrade_and_downgrade_on_postgresql_temp_tables(db_session):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    path = Path(__file__).parents[1] / "alembic/versions/b9c0d1e2f3a6_add_provider_transport_events.py"
    spec = importlib.util.spec_from_file_location("transport_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = db_session.connection()
    # pg_temp shadows public names only on this isolated fixture connection.
    connection.execute(text("CREATE TEMP TABLE generated_documents (id uuid)"))
    connection.execute(text("CREATE TEMP TABLE provider_attempts (id uuid)"))
    try:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM pg_attribute WHERE attrelid = 'pg_temp.provider_attempts'::regclass AND attname = 'transport_events_json' AND NOT attisdropped")) == 1
            connection.execute(text("INSERT INTO pg_temp.provider_attempts (id) VALUES (:id)"), {"id": "00000000-0000-0000-0000-000000000001"})
            assert connection.scalar(text("SELECT transport_events_json FROM pg_temp.provider_attempts")) == []
            migration.downgrade()
            assert connection.scalar(text("SELECT count(*) FROM pg_attribute WHERE attrelid = 'pg_temp.provider_attempts'::regclass AND attname = 'transport_events_json' AND NOT attisdropped")) == 0
    finally:
        connection.execute(text("DROP TABLE pg_temp.provider_attempts"))
        connection.execute(text("DROP TABLE pg_temp.generated_documents"))


def test_checker_lifecycle_expiry_wins_over_late_response_and_duplicate_delivery(
    db_session, make_user, make_generated_document, make_llm_config, monkeypatch,
):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.models import (Team, TeamHallucinationCheckSelection, ProviderAttempt, AttemptKind,
        AttemptOutcome, GeneratedDocumentStatus, HallucinationCheckStatus)
    from app.services import templates
    from app.services.quota_lifecycle import process_quota_lifecycle
    root, document, main, now = _submitted_draft(db_session, make_user, make_generated_document)
    config = make_llm_config(team=db_session.get(Team, root.team_id), has_secret=False)
    db_session.add(TeamHallucinationCheckSelection(team_id=root.team_id, llm_config_id=config.id,
        model_name_override=config.model_name, selected_by_user_id=config.created_by_user_id))
    db_session.commit()
    calls = []
    def late_checker(**kwargs):
        calls.append(True)
        checker = db_session.scalar(select(ProviderAttempt).where(
            ProviderAttempt.generated_document_id == document.id,
            ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check))
        current = datetime.now(timezone.utc)
        checker.deadline_at = current - timedelta(seconds=1)
        db_session.commit()
        assert process_quota_lifecycle(db_session, now=current) == 2
        return '{"status":"unchanged","edits":[]}', {"total_tokens": 5}
    monkeypatch.setattr(templates, "_invoke_frozen_llm", late_checker)
    monkeypatch.setenv("HALLUCINATION_CHECK_DEBUG_UI", "0")
    result = templates._run_hallucination_check(db_session, document=document, transcript_text="synthetic",
        working_note_text="", dictation_text="", title="Synthetic", sections=[{
            "section_key": "summary", "section_label": "Summary", "section_order": 0, "text": "synthetic"}],
        note_generation_options={"note_generation_length": "standard", "llm_detail_level": "standard"})
    db_session.refresh(document)
    assert document.status is GeneratedDocumentStatus.ready
    assert document.hallucination_check_status is HallucinationCheckStatus.failed_provider
    checker = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check))
    assert checker.outcome is AttemptOutcome.unknown
    assert result[2] is HallucinationCheckStatus.failed_provider
    assert templates.process_generated_document(db_session, document_id=document.id).status is GeneratedDocumentStatus.ready
    assert calls == [True]
