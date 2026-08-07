from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.celery_app import celery_app
from app.models import (
    AttemptKind, AttemptOutcome, AttemptStatus, ProviderAttempt, QuotaResource,
    TaskDispatchKind, TaskDispatchOutbox, TaskDispatchState, Transcript,
    TranscriptIngestionJob, TranscriptIngestionJobKind, TranscriptIngestionJobStatus,
    TranscriptIngestionMode, TranscriptStatus, TranscriptVersion,
)
from app.services.admin import delete_team, delete_user
from app.services.quota_lifecycle import (
    PROVIDER_ATTEMPT_OUTCOME_UNKNOWN, QUOTA_RESERVATION_EXPIRED, TASK_DISPATCH_FAILED,
    cancel_pending_dispatches_for_sources, process_quota_lifecycle,
    terminalize_attempts_for_generated_document, terminalize_attempts_for_owner,
    terminalize_attempts_for_transcripts,
)
from app.services.quotas import mark_provider_attempt_submitted, reserve_provider_attempt
from app.services.task_outbox import add_pending_task_dispatch
from app.services.templates import delete_generated_document
from app.services.transcripts import delete_transcripts


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _transcript(db, owner):
    value = Transcript(owner_user_id=owner.id, team_id=owner.team_id, title="synthetic", ingestion_mode=TranscriptIngestionMode.whole_file,
                       status=TranscriptStatus.transcribing, retention_days_applied=30,
                       retention_expires_at=datetime.now(UTC) + timedelta(days=30))
    db.add(value)
    db.flush()
    return value


def _attempt(db, owner, *, transcript=None, job=None, document=None, audio=False, expired=True):
    value = reserve_provider_attempt(
        db, team_id=owner.team_id, owner_user_id=owner.id,
        resource=QuotaResource.audio_seconds if audio else QuotaResource.tokens,
        attempt_kind=AttemptKind.stt_conversation if audio else AttemptKind.llm_generation,
        correlation_id=uuid4(), attempt_number=1, reserved_units=3,
        authorized_at=NOW - timedelta(minutes=2), reservation_valid_until=NOW - timedelta(minutes=1) if expired else NOW + timedelta(minutes=1),
        transcript_id=transcript.id if transcript else None,
        transcript_ingestion_job_id=job.id if job else None,
        generated_document_id=document.id if document else None,
        measured_audio_seconds=Decimal("2.1") if audio else None,
    )
    return value


def test_expired_reservations_fail_queued_generation_and_ingestion(db_session, make_user, make_generated_document):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="x")
    db_session.add(version); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    document.status = document.status.queued
    job = TranscriptIngestionJob(transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
                                 job_kind=TranscriptIngestionJobKind.audio_file, source_filename="a.wav")
    db_session.add(job); db_session.flush()
    _attempt(db_session, owner, transcript=transcript, document=document)
    _attempt(db_session, owner, transcript=transcript, job=job, audio=True)
    add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=job.id)
    db_session.commit()

    assert process_quota_lifecycle(db_session, now=NOW) >= 4
    assert (document.status.value, document.error_code) == ("failed", QUOTA_RESERVATION_EXPIRED)
    assert (job.status.value, job.error_code, transcript.status.value) == ("failed", QUOTA_RESERVATION_EXPIRED, "failed")
    assert {item.state for item in db_session.scalars(select(TaskDispatchOutbox)).all()} == {TaskDispatchState.cancelled}


def test_deadline_unknown_settlement_and_direct_terminalizers(db_session, make_user):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    job = TranscriptIngestionJob(transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
                                 job_kind=TranscriptIngestionJobKind.audio_file, source_filename="unknown.wav")
    db_session.add(job); db_session.flush()
    token = _attempt(db_session, owner, transcript=transcript, job=job, expired=False)
    audio = _attempt(db_session, owner, transcript=transcript, job=job, audio=True, expired=False)
    for attempt in (token, audio):
        mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, now=NOW - timedelta(minutes=1), deadline_at=NOW)
    db_session.commit()

    process_quota_lifecycle(db_session, now=NOW)
    assert [(item.outcome, item.settled_units) for item in (token, audio)] == [(AttemptOutcome.unknown, 3), (AttemptOutcome.unknown, 3)]
    assert transcript.status is TranscriptStatus.failed

    live = _attempt(db_session, owner, transcript=transcript, expired=False)
    assert terminalize_attempts_for_transcripts(db_session, [transcript.id], NOW).cancelled == 1
    assert live.status is AttemptStatus.cancelled
    another = _attempt(db_session, owner, expired=False)
    assert terminalize_attempts_for_owner(db_session, owner.id, NOW).cancelled == 1
    assert another.status is AttemptStatus.cancelled


def test_failed_dispatch_cancels_reserved_source_attempt_and_published_is_unchanged(db_session, make_user, make_generated_document):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    job = TranscriptIngestionJob(transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
                                 job_kind=TranscriptIngestionJobKind.audio_file, source_filename="retry.wav",
                                 source_audio_vault_ref="vault:retry-audio",
                                 source_audio_expires_at=NOW + timedelta(hours=24))
    db_session.add(job); db_session.flush()
    attempt = _attempt(db_session, owner, job=job, audio=True, expired=False)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="x")
    db_session.add(version); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    document.status = document.status.queued
    document_attempt = _attempt(db_session, owner, document=document, expired=False)
    failed = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=job.id)
    failed_document = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    published = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    failed.state = TaskDispatchState.failed; failed.failed_at = NOW
    failed_document.state = TaskDispatchState.failed; failed_document.failed_at = NOW
    published.state = TaskDispatchState.published; published.published_at = NOW
    db_session.commit()

    process_quota_lifecycle(db_session, now=NOW)
    assert (attempt.status, job.status, job.error_code) == (AttemptStatus.cancelled, TranscriptIngestionJobStatus.failed, TASK_DISPATCH_FAILED)
    assert (document_attempt.status, document.status, document.error_code) == (AttemptStatus.cancelled, document.status.failed, TASK_DISPATCH_FAILED)
    assert job.source_audio_vault_ref == "vault:retry-audio"
    assert published.state is TaskDispatchState.published


def test_document_terminalizer_and_dispatch_locking_are_metadata_only(db_session, make_user, make_generated_document):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="x")
    db_session.add(version); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    attempt = _attempt(db_session, owner, document=document, expired=False)
    pending = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    db_session.commit()
    assert terminalize_attempts_for_generated_document(db_session, document.id, NOW).cancelled == 1
    assert cancel_pending_dispatches_for_sources(db_session, generated_document_ids=[document.id], now=NOW) == 1
    assert attempt.status is AttemptStatus.cancelled and pending.state is TaskDispatchState.cancelled
    assert attempt.generated_document_id == document.id


def test_generated_document_hard_delete_terminalizes_attempts_and_removes_failed_dispatch(
    db_session, make_user, make_generated_document
):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="metadata")
    db_session.add(version); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    reserved = _attempt(db_session, owner, transcript=transcript, document=document, expired=False)
    submitted = _attempt(db_session, owner, transcript=transcript, document=document, expired=False)
    mark_provider_attempt_submitted(
        db_session, attempt_id=submitted.id, now=NOW, deadline_at=NOW + timedelta(minutes=1)
    )
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    dispatch.state = TaskDispatchState.failed; dispatch.failed_at = NOW
    document_id = document.id
    db_session.commit()

    delete_generated_document(db_session, owner, generated_document_id=document_id)
    attempts = db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.id.in_((reserved.id, submitted.id)))).all()

    assert db_session.get(type(document), document_id) is None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == document_id)) is None
    assert {(item.id, item.status, item.outcome) for item in attempts} == {
        (reserved.id, AttemptStatus.cancelled, AttemptOutcome.cancelled),
        (submitted.id, AttemptStatus.settled, AttemptOutcome.unknown),
    }
    assert {(item.owner_user_id, item.team_id, item.generated_document_id) for item in attempts} == {
        (owner.id, owner.team_id, None)
    }


def test_manual_transcript_delete_terminalizes_active_llm_and_stt_attempts_and_removes_dispatches(
    db_session, make_user, make_generated_document
):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="metadata")
    job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="metadata.wav",
    )
    db_session.add_all((version, job)); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    attempts = [
        _attempt(db_session, owner, transcript=transcript, document=document, expired=False),
        _attempt(db_session, owner, transcript=transcript, document=document, expired=False),
        _attempt(db_session, owner, transcript=transcript, job=job, audio=True, expired=False),
        _attempt(db_session, owner, transcript=transcript, job=job, audio=True, expired=False),
    ]
    for attempt in (attempts[1], attempts[3]):
        mark_provider_attempt_submitted(
            db_session, attempt_id=attempt.id, now=NOW, deadline_at=NOW + timedelta(minutes=1)
        )
    document_dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    job_dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=job.id)
    job_dispatch.state = TaskDispatchState.failed; job_dispatch.failed_at = NOW
    transcript_id, document_id, job_id = transcript.id, document.id, job.id
    db_session.commit()

    assert delete_transcripts(db_session, owner, transcript_ids=[transcript_id]) == 1
    db_session.expire_all()
    persisted = db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.id.in_([item.id for item in attempts]))).all()

    assert db_session.get(Transcript, transcript_id) is None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id.in_((document_id, job_id)))) is None
    assert [item.status for item in persisted].count(AttemptStatus.cancelled) == 2
    assert [item.outcome for item in persisted].count(AttemptOutcome.unknown) == 2
    assert {(item.owner_user_id, item.team_id, item.transcript_id, item.generated_document_id, item.transcript_ingestion_job_id) for item in persisted} == {
        (owner.id, owner.team_id, None, None, None)
    }


def test_user_delete_preserves_terminalized_attempt_metadata_without_content_or_outbox(
    db_session, make_team, make_user, make_generated_document
):
    admin = make_user(email="quota-delete-admin@example.com", is_system_admin=True)
    team = make_team(name="Quota user deletion")
    owner = make_user(email="quota-delete-owner@example.com", team=team)
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="metadata")
    job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="metadata.wav",
    )
    db_session.add_all((version, job)); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    attempts = [
        _attempt(db_session, owner, transcript=transcript, document=document, expired=False),
        _attempt(db_session, owner, transcript=transcript, job=job, audio=True, expired=False),
    ]
    mark_provider_attempt_submitted(db_session, attempt_id=attempts[1].id, now=NOW, deadline_at=NOW + timedelta(minutes=1))
    add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=job.id)
    dispatch.state = TaskDispatchState.failed; dispatch.failed_at = NOW
    attempt_ids, source_ids, owner_id = [item.id for item in attempts], (document.id, job.id), owner.id
    db_session.commit()

    delete_user(db_session, admin, owner_id)
    persisted = db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.id.in_(attempt_ids))).all()

    assert db_session.get(Transcript, transcript.id) is None
    assert db_session.get(type(owner), owner_id) is None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id.in_(source_ids))) is None
    assert {(item.status, item.outcome, item.owner_user_id, item.team_id, item.transcript_id, item.generated_document_id, item.transcript_ingestion_job_id) for item in persisted} == {
        (AttemptStatus.cancelled, AttemptOutcome.cancelled, None, team.id, None, None, None),
        (AttemptStatus.settled, AttemptOutcome.unknown, None, team.id, None, None, None),
    }


def test_team_delete_cascades_attempts_and_removes_polymorphic_dispatches(
    db_session, make_team, make_user, make_generated_document
):
    admin = make_user(email="quota-team-delete-admin@example.com", is_system_admin=True)
    team = make_team(name="Quota team deletion")
    owner = make_user(email="quota-team-delete-owner@example.com", team=team)
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="metadata")
    job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="metadata.wav",
    )
    db_session.add_all((version, job)); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    _attempt(db_session, owner, transcript=transcript, document=document, expired=False)
    _attempt(db_session, owner, transcript=transcript, job=job, audio=True, expired=False)
    add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=job.id)
    dispatch.state = TaskDispatchState.failed; dispatch.failed_at = NOW
    source_ids = (document.id, job.id)
    db_session.commit()

    delete_team(db_session, admin, team_id=team.id)

    assert db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.team_id == team.id)) is None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id.in_(source_ids))) is None


def test_failed_outbox_scan_skips_old_terminal_and_missing_sources_before_queued_source(
    db_session, make_user, make_generated_document
):
    owner = make_user()
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="metadata")
    db_session.add(version); db_session.flush()
    terminal = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    terminal.status = terminal.status.failed
    queued = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    queued.status = queued.status.queued
    attempt = _attempt(db_session, owner, transcript=transcript, document=queued, expired=False)
    old_terminal = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=terminal.id)
    old_missing = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    candidate = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=queued.id)
    for dispatch in (old_terminal, old_missing, candidate):
        dispatch.state = TaskDispatchState.failed
    old_terminal.failed_at = NOW - timedelta(minutes=2)
    old_missing.failed_at = NOW - timedelta(minutes=1)
    candidate.failed_at = NOW
    db_session.commit()

    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", capture_statement)
    try:
        assert process_quota_lifecycle(db_session, batch_size=1, now=NOW) == 2
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture_statement)

    assert attempt.status is AttemptStatus.cancelled
    assert (queued.status, queued.error_code) == (queued.status.failed, TASK_DISPATCH_FAILED)
    # Source/owner locks precede later cancellation's outbox row lock.
    locked_statements = [statement for statement in statements if "FOR UPDATE" in statement]
    assert locked_statements
    assert "task_dispatch_outbox" not in locked_statements[0]


def test_worker_claims_bounded_rows_skip_locked_and_schedule_registered():
    statement = select(ProviderAttempt).with_for_update(skip_locked=True)
    assert "FOR UPDATE SKIP LOCKED" in str(statement.compile(dialect=postgresql.dialect()))
    import app.tasks  # noqa: F401
    assert "openscribe.process_quota_lifecycle" in celery_app.tasks
    assert celery_app.conf.beat_schedule["process-quota-lifecycle-every-10-seconds"] == {
        "task": "openscribe.process_quota_lifecycle", "schedule": 10.0, "options": {"expires": 10.0},
    }
