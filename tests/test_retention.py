from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from pathlib import Path

import pytest

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app import tasks as celery_tasks
from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    GeneratedDocument,
    ProviderAttempt,
    QuotaResource,
    TaskDispatchKind,
    TaskDispatchOutbox,
    TaskDispatchState,
    Transcript,
    TranscriptAudioCleanupJob,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services.quotas import mark_provider_attempt_submitted, reserve_provider_attempt
from app.services.task_outbox import add_pending_task_dispatch
from app.services.transcripts import (
    delete_expired_transcripts,
    process_transcript_ingestion_job,
    process_transcript_audio_cleanup_jobs,
    queue_orphan_transcript_audio_after_rollback,
    queue_transcript_audio_cleanup,
)


def _make_transcript(db_session, *, owner, title: str, expires_at):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title=title,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=expires_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.refresh(transcript)
    return transcript


def test_retention_cleanup_deletes_expired_roots_and_derived_documents(
    db_session,
    make_team,
    make_user,
    make_generated_document,
):
    team = make_team(name="Retention Team")
    owner = make_user(email="retention-owner@example.com", password="password-1", team=team)
    now = utcnow()
    expired = _make_transcript(db_session, owner=owner, title="Expired", expires_at=now)
    active = _make_transcript(db_session, owner=owner, title="Active", expires_at=now + timedelta(days=1))
    version = TranscriptVersion(transcript_id=expired.id, version_no=1, text_encrypted="encrypted")
    db_session.add(version)
    db_session.commit()
    db_session.refresh(version)
    document = make_generated_document(owner=owner, transcript=expired, transcript_version=version)
    job = TranscriptIngestionJob(
        transcript_id=expired.id,
        owner_user_id=owner.id,
        team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="metadata.wav",
    )
    db_session.add(job)
    db_session.flush()
    attempts = [
        reserve_provider_attempt(
            db_session, team_id=team.id, owner_user_id=owner.id, resource=QuotaResource.tokens,
            attempt_kind=AttemptKind.llm_generation, correlation_id=uuid4(), attempt_number=1,
            reserved_units=3, authorized_at=now, reservation_valid_until=now + timedelta(minutes=2),
            transcript_id=expired.id, generated_document_id=document.id,
        ),
        reserve_provider_attempt(
            db_session, team_id=team.id, owner_user_id=owner.id, resource=QuotaResource.tokens,
            attempt_kind=AttemptKind.llm_generation, correlation_id=uuid4(), attempt_number=1,
            reserved_units=3, authorized_at=now, reservation_valid_until=now + timedelta(minutes=2),
            transcript_id=expired.id, generated_document_id=document.id,
        ),
        reserve_provider_attempt(
            db_session, team_id=team.id, owner_user_id=owner.id, resource=QuotaResource.audio_seconds,
            attempt_kind=AttemptKind.stt_conversation, correlation_id=uuid4(), attempt_number=1,
            reserved_units=3, authorized_at=now, reservation_valid_until=now + timedelta(minutes=2),
            transcript_id=expired.id, transcript_ingestion_job_id=job.id, measured_audio_seconds=Decimal("2.1"),
        ),
        reserve_provider_attempt(
            db_session, team_id=team.id, owner_user_id=owner.id, resource=QuotaResource.audio_seconds,
            attempt_kind=AttemptKind.stt_conversation, correlation_id=uuid4(), attempt_number=1,
            reserved_units=3, authorized_at=now, reservation_valid_until=now + timedelta(minutes=2),
            transcript_id=expired.id, transcript_ingestion_job_id=job.id, measured_audio_seconds=Decimal("2.1"),
        ),
    ]
    for attempt in (attempts[1], attempts[3]):
        mark_provider_attempt_submitted(
            db_session, attempt_id=attempt.id, now=now, deadline_at=now + timedelta(minutes=1)
        )
    add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=document.id)
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=job.id)
    dispatch.state = TaskDispatchState.failed
    dispatch.failed_at = now
    attempt_ids, source_ids = [attempt.id for attempt in attempts], (document.id, job.id)
    db_session.commit()

    deleted = delete_expired_transcripts(db_session, now=now, batch_size=10)
    db_session.expire_all()
    persisted_attempts = db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.id.in_(attempt_ids))).all()

    assert deleted == 1
    assert db_session.get(Transcript, expired.id) is None
    assert db_session.get(TranscriptVersion, version.id) is None
    assert db_session.get(GeneratedDocument, document.id) is None
    assert db_session.get(Transcript, active.id) is not None
    assert db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id.in_(source_ids))) is None
    assert [item.status for item in persisted_attempts].count(AttemptStatus.cancelled) == 2
    assert [item.outcome for item in persisted_attempts].count(AttemptOutcome.unknown) == 2
    assert {(item.owner_user_id, item.team_id, item.transcript_id, item.generated_document_id, item.transcript_ingestion_job_id) for item in persisted_attempts} == {
        (owner.id, team.id, None, None, None)
    }


def test_retention_cleanup_is_bounded_and_idempotent(db_session, make_team, make_user):
    team = make_team(name="Bounded Retention Team")
    owner = make_user(email="bounded-retention@example.com", password="password-1", team=team)
    now = utcnow()
    first = _make_transcript(db_session, owner=owner, title="First", expires_at=now - timedelta(days=2))
    second = _make_transcript(db_session, owner=owner, title="Second", expires_at=now - timedelta(days=1))

    assert delete_expired_transcripts(db_session, now=now, batch_size=1) == 1
    assert db_session.get(Transcript, first.id) is None
    assert db_session.get(Transcript, second.id) is not None
    assert delete_expired_transcripts(db_session, now=now, batch_size=1) == 1
    assert delete_expired_transcripts(db_session, now=now, batch_size=1) == 0


def test_retention_cleanup_task_processes_one_bounded_batch_and_returns(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Task Bounded Retention Team")
    owner = make_user(email="task-bounded-retention@example.com", password="password-1", team=team)
    now = utcnow()
    for index in range(3):
        _make_transcript(
            db_session,
            owner=owner,
            title=f"Expired {index}",
            expires_at=now - timedelta(days=index + 1),
        )
    monkeypatch.setattr(celery_tasks, "SessionLocal", lambda: db_session)

    assert celery_tasks.delete_expired_transcripts_task(batch_size=2) == 2
    assert db_session.scalar(select(func.count()).select_from(Transcript)) == 1


def test_retention_cleanup_preserves_failed_vault_deletion_for_retry(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Retention Vault Retry Team")
    owner = make_user(email="retention-vault-retry@example.com", password="password-1", team=team)
    now = utcnow()
    expired = _make_transcript(db_session, owner=owner, title="Expired audio", expires_at=now)
    secret_ref = "secret:openscribe/transcript-ingestion/retention-retry/source-audio"
    db_session.add(
        TranscriptIngestionJob(
            transcript_id=expired.id,
            owner_user_id=owner.id,
            team_id=team.id,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="recording.wav",
            source_audio_vault_ref=secret_ref,
            source_audio_expires_at=now + timedelta(hours=24),
            status=TranscriptIngestionJobStatus.failed,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    assert delete_expired_transcripts(db_session, now=now, batch_size=10) == 1
    assert db_session.get(Transcript, expired.id) is None
    cleanup_job = db_session.scalar(select(TranscriptAudioCleanupJob))
    assert cleanup_job is not None
    assert cleanup_job.secret_ref == secret_ref
    assert cleanup_job.attempt_count == 1


def test_ingestion_worker_deletes_already_expired_root_and_queues_retry_audio_cleanup(
    db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Expired ingestion worker team")
    owner = make_user(email="expired-ingestion-worker@example.com", password="password-1", team=team)
    expired = _make_transcript(
        db_session,
        owner=owner,
        title="Expired before worker",
        expires_at=utcnow() - timedelta(seconds=1),
    )
    secret_ref = "secret:openscribe/transcript-ingestion/66666666-6666-6666-6666-666666666666/source-audio"
    job = TranscriptIngestionJob(
        transcript_id=expired.id,
        owner_user_id=owner.id,
        team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.wav",
        source_audio_vault_ref=secret_ref,
        source_audio_expires_at=utcnow() + timedelta(hours=24),
        status=TranscriptIngestionJobStatus.queued,
    )
    db_session.add(job)
    db_session.commit()
    provider_called = False

    def fail_if_provider_called(*args, **kwargs):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("expired transcript must not reach STT provider")

    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fail_if_provider_called)
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    assert process_transcript_ingestion_job(db_session, job_id=job.id) is None
    assert provider_called is False
    assert db_session.get(Transcript, expired.id) is None
    assert db_session.get(TranscriptIngestionJob, job.id) is None
    cleanup_job = db_session.scalar(select(TranscriptAudioCleanupJob).where(TranscriptAudioCleanupJob.secret_ref == secret_ref))
    assert cleanup_job is not None
    assert cleanup_job.attempt_count == 1


@pytest.mark.real_db_connections
@pytest.mark.parametrize("failure_kind", ["app_error", "unexpected"])
def test_ingestion_failure_deletes_root_expired_during_failure_path(
    db_session, make_team, make_user, monkeypatch, failure_kind
):
    team = make_team(name=f"Expiry failure {failure_kind} team")
    owner = make_user(email=f"expiry-failure-{failure_kind}@example.com", password="password-1", team=team)
    transcript = _make_transcript(
        db_session,
        owner=owner,
        title="Expires while ingesting",
        expires_at=utcnow() + timedelta(days=1),
    )
    secret_ref = f"secret:openscribe/transcript-ingestion/{failure_kind}/source-audio"
    job = TranscriptIngestionJob(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.wav",
        source_audio_vault_ref=secret_ref,
        source_audio_expires_at=utcnow() + timedelta(hours=24),
        status=TranscriptIngestionJobStatus.queued,
    )
    db_session.add(job)
    db_session.commit()
    transcript_id = transcript.id
    job_id = job.id

    def expire_then_fail(*args, **kwargs):
        with Session(bind=db_session.get_bind(), future=True) as concurrent_db:
            expiring = concurrent_db.get(Transcript, transcript_id)
            assert expiring is not None
            expiring.retention_expires_at = utcnow() - timedelta(seconds=1)
            concurrent_db.commit()
        if failure_kind == "app_error":
            raise AppError(502, "vault_read_failed", "Audio source unavailable")
        raise RuntimeError("audio worker failure")

    monkeypatch.setattr("app.services.transcripts._read_queued_source_audio", expire_then_fail)
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    assert process_transcript_ingestion_job(db_session, job_id=job_id) is None

    db_session.expire_all()
    assert db_session.get(Transcript, transcript_id) is None
    assert db_session.get(TranscriptIngestionJob, job_id) is None
    cleanup_job = db_session.scalar(select(TranscriptAudioCleanupJob).where(TranscriptAudioCleanupJob.secret_ref == secret_ref))
    assert cleanup_job is not None
    assert cleanup_job.attempt_count == 1


def test_audio_rollback_compensation_persists_cleanup_job(db_session):
    secret_ref = "secret:openscribe/transcript-ingestion/22222222-2222-2222-2222-222222222222/source-audio"

    queue_orphan_transcript_audio_after_rollback(db_session, secret_ref=secret_ref)

    cleanup_job = db_session.scalar(select(TranscriptAudioCleanupJob).where(TranscriptAudioCleanupJob.secret_ref == secret_ref))
    assert cleanup_job is not None


def test_audio_cleanup_worker_never_deletes_live_ingestion_retry_source(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Live audio cleanup guard")
    owner = make_user(email="live-audio-cleanup-owner@example.com", password="password-1", team=team)
    transcript = _make_transcript(
        db_session,
        owner=owner,
        title="Retry source remains live",
        expires_at=utcnow() + timedelta(days=1),
    )
    secret_ref = "secret:openscribe/transcript-ingestion/55555555-5555-5555-5555-555555555555/source-audio"
    ingestion_job = TranscriptIngestionJob(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.wav",
        source_audio_vault_ref=secret_ref,
        source_audio_expires_at=utcnow() + timedelta(hours=24),
        status=TranscriptIngestionJobStatus.failed,
    )
    db_session.add(ingestion_job)
    queue_transcript_audio_cleanup(db_session, secret_refs=[secret_ref])
    db_session.commit()
    deleted_refs: list[str] = []
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted_refs.append(secret_ref),
    )

    assert process_transcript_audio_cleanup_jobs(db_session, batch_size=10) == 0
    assert deleted_refs == []
    assert db_session.get(TranscriptIngestionJob, ingestion_job.id).source_audio_vault_ref == secret_ref
    assert db_session.scalar(select(TranscriptAudioCleanupJob).where(TranscriptAudioCleanupJob.secret_ref == secret_ref)) is None


def test_audio_rollback_compensation_falls_back_to_validated_direct_deletion(db_session, monkeypatch):
    secret_ref = "secret:openscribe/transcript-ingestion/33333333-3333-3333-3333-333333333333/source-audio"
    queued_attempts = []
    deleted_refs = []

    def fail_enqueue(*args, **kwargs):
        queued_attempts.append(kwargs["secret_refs"])
        raise AppError(502, "database_unavailable", "Database unavailable")

    monkeypatch.setattr("app.services.transcripts.queue_transcript_audio_cleanup", fail_enqueue)
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted_refs.append(secret_ref),
    )

    queue_orphan_transcript_audio_after_rollback(db_session, secret_ref=secret_ref)

    assert len(queued_attempts) == 2
    assert deleted_refs == [secret_ref]


def test_audio_rollback_compensation_fails_when_enqueue_and_direct_deletion_fail(db_session, monkeypatch):
    secret_ref = "secret:openscribe/transcript-ingestion/44444444-4444-4444-4444-444444444444/source-audio"

    monkeypatch.setattr(
        "app.services.transcripts.queue_transcript_audio_cleanup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AppError(502, "database_unavailable", "Database unavailable")),
    )
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda **kwargs: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault unavailable")),
    )

    with pytest.raises(AppError, match="could not be durably queued or deleted") as exc_info:
        queue_orphan_transcript_audio_after_rollback(db_session, secret_ref=secret_ref)

    assert exc_info.value.code == "transcript_audio_cleanup_compensation_failed"


def test_expired_transcript_is_hidden_from_history_and_detail(client, db_session, make_team, make_user):
    team = make_team(name="Visibility Retention Team")
    owner = make_user(
        email="retention-visibility@example.com",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    expired = _make_transcript(db_session, owner=owner, title="Expired", expires_at=utcnow() - timedelta(seconds=1))
    active = _make_transcript(db_session, owner=owner, title="Active", expires_at=utcnow() + timedelta(days=1))

    login = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})
    assert login.status_code == 200

    history = client.get("/api/v1/transcripts")
    assert history.status_code == 200
    assert [item["id"] for item in history.json()["items"]] == [str(active.id)]

    detail = client.get(f"/api/v1/transcripts/{expired.id}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "not_found"


def test_expired_transcript_is_rejected_by_workspace_and_owner_content_routes(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Expired Content Gate Team")
    owner = make_user(
        email="expired-content-gate@example.com",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    expired = _make_transcript(
        db_session,
        owner=owner,
        title="Confidential expired session",
        expires_at=utcnow() - timedelta(seconds=1),
    )
    expired.current_draft_text_encrypted = "confidential transcript body"
    db_session.add(expired)
    db_session.commit()

    login = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})
    assert login.status_code == 200

    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={expired.id}")
    assert workspace.status_code == 200
    assert workspace.json()["active_transcript"] is None
    assert workspace.json()["recent_transcripts"] == []
    assert workspace.json()["generated_documents"] == []
    assert "confidential transcript body" not in workspace.text

    requests = [
        client.patch(f"/api/v1/transcripts/{expired.id}", json={"title": "Changed"}),
        client.get(f"/api/v1/transcripts/{expired.id}/working-note"),
        client.get(f"/api/v1/transcripts/{expired.id}/post-consultation-dictation"),
        client.get(f"/api/v1/transcripts/{expired.id}/generated-documents"),
        client.post(f"/api/v1/transcripts/{expired.id}/pii-entities/reveal"),
        client.delete(f"/api/v1/transcripts/{expired.id}"),
    ]
    for response in requests:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert db_session.get(Transcript, expired.id) is not None


def test_retention_cleanup_task_has_timely_beat_schedule():
    schedule = celery_app.conf.beat_schedule["delete-expired-transcripts-every-10-seconds"]

    assert schedule["task"] == "openscribe.delete_expired_transcripts"
    assert schedule["schedule"] == 10.0
    assert schedule["options"] == {"expires": 10.0}

    audio_cleanup_schedule = celery_app.conf.beat_schedule["retry-transcript-audio-cleanup-every-10-seconds"]
    assert audio_cleanup_schedule["task"] == "openscribe.process_transcript_audio_cleanup_jobs"
    assert audio_cleanup_schedule["schedule"] == 10.0
    assert audio_cleanup_schedule["options"] == {"expires": 10.0}

    provider_cleanup_schedule = celery_app.conf.beat_schedule["retry-provider-secret-cleanup-every-10-seconds"]
    assert provider_cleanup_schedule["task"] == "openscribe.process_provider_secret_cleanup_jobs"
    assert provider_cleanup_schedule["schedule"] == 10.0
    assert provider_cleanup_schedule["options"] == {"expires": 10.0}

    assert celery_tasks.delete_expired_transcripts_task.name in celery_app.tasks
    assert celery_tasks.process_transcript_audio_cleanup_jobs_task.name in celery_app.tasks
    assert celery_tasks.process_provider_secret_cleanup_jobs_task.name in celery_app.tasks


def test_dev_runtime_starts_and_stops_retention_scheduler():
    script = Path("start-dev.sh").read_text()

    assert "celery -A app.celery_app:celery_app beat" in script
    assert 'CELERY_BEAT_PID=""' in script
    assert 'kill "${CELERY_BEAT_PID}"' in script
