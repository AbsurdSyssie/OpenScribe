from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.celery_app import celery_app
from app.errors import AppError
from app.models import (
    GeneratedDocument,
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
from app.services.transcripts import delete_expired_transcripts


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

    deleted = delete_expired_transcripts(db_session, now=now, batch_size=10)

    assert deleted == 1
    assert db_session.get(Transcript, expired.id) is None
    assert db_session.get(TranscriptVersion, version.id) is None
    assert db_session.get(GeneratedDocument, document.id) is None
    assert db_session.get(Transcript, active.id) is not None


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


def test_dev_runtime_starts_and_stops_retention_scheduler():
    script = Path("start-dev.sh").read_text()

    assert "celery -A app.celery_app:celery_app beat" in script
    assert 'CELERY_BEAT_PID=""' in script
    assert 'kill "${CELERY_BEAT_PID}"' in script
