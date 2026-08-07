from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event as sqlalchemy_event, select

from app import tasks as celery_tasks
from app.celery_app import celery_app
from app.errors import AppError
from app.models import (
    SecurityAuditEvent,
    SecurityAuditEventHold,
    SecurityAuditHoldReason,
    Transcript,
    TranscriptAudioCleanupJob,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services.audit_retention import (
    expire_security_audit_events,
    place_security_audit_hold,
    release_security_audit_hold,
    renew_security_audit_hold,
    subtract_calendar_months,
)
from app.services.admin import delete_user
from app.services.transcripts import (
    expire_ingestion_source_audio,
    process_transcript_ingestion_job,
    retry_audio_file_ingestion,
)
from app.web.transcribe_workspace import resolve_transcribe_workspace


def _transcript(db_session, *, owner, status=TranscriptStatus.failed):
    row = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="DSPT retention test",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=status,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _source_job(db_session, *, transcript, status, deadline, source_ref="secret:openscribe/transcript-ingestion/11111111-1111-1111-1111-111111111111/source-audio"):
    row = TranscriptIngestionJob(
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic.wav",
        source_audio_vault_ref=source_ref,
        source_audio_size_bytes=12,
        source_audio_expires_at=deadline,
        status=status,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_source_audio_expiry_is_at_original_deadline_and_is_idempotent(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="DSPT source deadline")
    owner = make_user(email="dspt-source@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner=owner)
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    job = _source_job(
        db_session,
        transcript=transcript,
        status=TranscriptIngestionJobStatus.failed,
        deadline=now - timedelta(seconds=1),
    )
    deleted = []
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted.append(secret_ref),
    )

    assert expire_ingestion_source_audio(db_session, now=now) == 1
    db_session.refresh(job)
    assert job.source_audio_vault_ref is None
    assert job.source_audio_expired_at == now
    assert deleted == ["secret:openscribe/transcript-ingestion/11111111-1111-1111-1111-111111111111/source-audio"]
    assert expire_ingestion_source_audio(db_session, now=now) == 0


@pytest.mark.parametrize("status", [TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing])
def test_expiry_terminalises_queued_or_processing_source(db_session, make_team, make_user, status):
    team = make_team(name=f"DSPT source {status.value}")
    owner = make_user(email=f"dspt-{status.value}@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner=owner, status=TranscriptStatus.transcribing)
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    job = _source_job(db_session, transcript=transcript, status=status, deadline=now)

    assert expire_ingestion_source_audio(db_session, now=now) == 1
    db_session.refresh(job)
    assert job.status is TranscriptIngestionJobStatus.failed
    assert job.error_code == "ingestion_source_expired"
    assert job.source_audio_vault_ref is None


def test_source_audio_cleanup_retries_after_vault_failure(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="DSPT Vault retry")
    owner = make_user(email="dspt-vault@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner=owner)
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    job = _source_job(db_session, transcript=transcript, status=TranscriptIngestionJobStatus.failed, deadline=now)
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda **_: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "synthetic")),
    )

    assert expire_ingestion_source_audio(db_session, now=now) == 1
    db_session.refresh(job)
    cleanup = db_session.scalar(select(TranscriptAudioCleanupJob))
    assert cleanup is not None and cleanup.attempt_count == 1
    monkeypatch.setattr("app.services.transcripts.delete_transcript_ingestion_source_audio", lambda **_: None)
    from app.services.transcripts import process_transcript_audio_cleanup_jobs

    assert process_transcript_audio_cleanup_jobs(db_session, now=cleanup.next_attempt_at + timedelta(seconds=1)) == 1
    assert db_session.scalar(select(TranscriptAudioCleanupJob)) is None


def test_worker_enforces_expired_source_before_vault_read_or_provider_dispatch(
    db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="DSPT worker deadline")
    owner = make_user(email="dspt-worker-deadline@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner=owner, status=TranscriptStatus.transcribing)
    job = _source_job(
        db_session,
        transcript=transcript,
        status=TranscriptIngestionJobStatus.queued,
        deadline=utcnow() - timedelta(seconds=1),
    )
    calls = []
    monkeypatch.setattr(
        "app.services.transcripts.read_transcript_ingestion_source_audio",
        lambda **_: calls.append("vault-read"),
    )
    monkeypatch.setattr(
        "app.services.transcripts.transcribe_with_stt_snapshot",
        lambda *_, **__: calls.append("provider-dispatch"),
    )
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda **_: None,
    )

    result = process_transcript_ingestion_job(db_session, job_id=job.id)

    db_session.refresh(job)
    assert result is not None and result.status is TranscriptIngestionJobStatus.failed
    assert job.error_code == "ingestion_source_expired"
    assert job.source_audio_vault_ref is None
    assert job.source_audio_expired_at is not None
    assert calls == []


def test_retry_transfers_original_source_deadline(db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="DSPT retry deadline")
    owner = make_user(email="dspt-retry@example.com", password="password-1", team=team)
    admin = make_user(email="dspt-retry-admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    transcript = _transcript(db_session, owner=owner)
    deadline = utcnow() + timedelta(hours=3)
    previous = _source_job(db_session, transcript=transcript, status=TranscriptIngestionJobStatus.failed, deadline=deadline)
    previous.stt_config_id = config.id
    previous.stt_adapter_kind = config.adapter_kind.value
    previous.stt_base_url = config.base_url
    previous.stt_transcribe_path = config.transcribe_path
    previous.stt_model_name = config.model_name
    previous.stt_file_field_name = config.file_field_name
    previous.stt_response_text_path = config.response_text_path
    previous.stt_extra_form_fields_json = config.extra_form_fields_json
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts.read_transcript_ingestion_source_audio", lambda **_: b"synthetic")
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **_: 1.0)

    _, retried, _, _ = retry_audio_file_ingestion(db_session, owner, transcript_id=transcript.id)
    assert retried.source_audio_expires_at == deadline


def test_expired_retry_is_reported_by_api_and_workspace(client, db_session, make_team, make_user):
    team = make_team(name="DSPT expired UI")
    owner = make_user(
        email="dspt-ui@example.com",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    transcript = _transcript(db_session, owner=owner)
    _source_job(
        db_session,
        transcript=transcript,
        status=TranscriptIngestionJobStatus.failed,
        deadline=utcnow() - timedelta(seconds=1),
    )
    login = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})
    assert login.status_code == 200
    session_token = login.cookies.get("openscribe_session")
    assert session_token
    headers = {"Cookie": f"openscribe_session={session_token}"}

    detail = client.get(f"/api/v1/transcripts/{transcript.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["latest_ingestion_retry_expired"] is True
    assert detail.json()["latest_ingestion_retry_available"] is False
    workspace = resolve_transcribe_workspace(
        db_session,
        current_user=owner,
        transcript_id=str(transcript.id),
        live_stt_health_check=False,
    )
    assert workspace["active_transcript_retry_expired"] is True


def test_calendar_six_month_boundary_handles_end_of_month_and_leap_year():
    assert subtract_calendar_months(datetime(2026, 8, 31, tzinfo=timezone.utc), 6) == datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert subtract_calendar_months(datetime(2024, 8, 31, tzinfo=timezone.utc), 6) == datetime(2024, 2, 29, tzinfo=timezone.utc)
    assert subtract_calendar_months(datetime(2024, 2, 29, tzinfo=timezone.utc), 6) == datetime(2023, 8, 29, tzinfo=timezone.utc)


def test_audit_expiry_batches_and_respects_active_released_and_expired_holds(db_session, make_user):
    admin = make_user(email="dspt-audit-admin@example.com", password="password-1", is_system_admin=True)
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    cutoff = subtract_calendar_months(now, 6)
    active = SecurityAuditEvent(action="active", created_at=cutoff)
    released = SecurityAuditEvent(action="released", created_at=cutoff)
    expired = SecurityAuditEvent(action="expired", created_at=cutoff)
    recent = SecurityAuditEvent(action="recent", created_at=cutoff + timedelta(seconds=1))
    db_session.add_all([active, released, expired, recent])
    db_session.commit()
    active_hold = place_security_audit_hold(
        db_session, actor=admin, event_id=active.id, reason=SecurityAuditHoldReason.incident,
        reference="INC-1", review_at=now + timedelta(days=1), expires_at=now + timedelta(days=2), now=now,
    )
    released_hold = place_security_audit_hold(
        db_session, actor=admin, event_id=released.id, reason=SecurityAuditHoldReason.dispute,
        reference="DSPT-1", review_at=now + timedelta(days=1), expires_at=now + timedelta(days=2), now=now,
    )
    release_security_audit_hold(db_session, actor=admin, hold_id=released_hold.id, now=now)
    expired_hold = SecurityAuditEventHold(
        security_audit_event_id=expired.id, reason=SecurityAuditHoldReason.legal_hold, owner_user_id=admin.id,
        created_at=now - timedelta(days=2), approved_at=now - timedelta(days=2),
        review_at=now - timedelta(days=1), expires_at=now - timedelta(days=1),
    )
    db_session.add(expired_hold)
    db_session.commit()

    assert expire_security_audit_events(db_session, now=now, batch_size=2) == 2
    assert db_session.get(SecurityAuditEvent, active.id) is not None
    assert db_session.get(SecurityAuditEvent, released.id) is None
    assert db_session.get(SecurityAuditEvent, expired.id) is None
    assert db_session.get(SecurityAuditEvent, recent.id) is not None
    assert db_session.get(SecurityAuditEventHold, active_hold.id) is not None
    assert expire_security_audit_events(db_session, now=now, batch_size=2) == 0


def test_audit_expiry_rolls_back_on_failure_and_retries_safely(db_session):
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    due = SecurityAuditEvent(
        action="synthetic_retryable_expiry",
        created_at=subtract_calendar_months(now, 6) - timedelta(seconds=1),
    )
    db_session.add(due)
    db_session.commit()

    def fail_commit_once(_session):
        raise RuntimeError("synthetic commit failure")

    sqlalchemy_event.listen(db_session, "before_commit", fail_commit_once)
    try:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            expire_security_audit_events(db_session, now=now)
    finally:
        sqlalchemy_event.remove(db_session, "before_commit", fail_commit_once)
        db_session.rollback()

    assert db_session.get(SecurityAuditEvent, due.id) is not None
    assert expire_security_audit_events(db_session, now=now) == 1
    assert db_session.get(SecurityAuditEvent, due.id) is None


def test_audit_hold_authority_window_renewal_and_release(db_session, make_user):
    admin = make_user(email="dspt-hold-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="dspt-hold-member@example.com", password="password-1")
    event = SecurityAuditEvent(action="hold-test")
    db_session.add(event)
    db_session.commit()
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    with pytest.raises(AppError, match="System-admin"):
        place_security_audit_hold(
            db_session, actor=member, event_id=event.id, reason=SecurityAuditHoldReason.incident,
            reference=None, review_at=now + timedelta(days=1), expires_at=now + timedelta(days=2), now=now,
        )
    with pytest.raises(AppError, match="cannot exceed 90 days"):
        place_security_audit_hold(
            db_session, actor=admin, event_id=event.id, reason=SecurityAuditHoldReason.incident,
            reference=None, review_at=now + timedelta(days=1), expires_at=now + timedelta(days=91), now=now,
        )
    hold = place_security_audit_hold(
        db_session, actor=admin, event_id=event.id, reason=SecurityAuditHoldReason.incident,
        reference="INC-2", review_at=now + timedelta(days=1), expires_at=now + timedelta(days=2), now=now,
    )
    renewed = renew_security_audit_hold(
        db_session, actor=admin, hold_id=hold.id, reason=SecurityAuditHoldReason.legal_duty,
        reference="LEGAL-2", review_at=now + timedelta(days=3), expires_at=now + timedelta(days=4), now=now + timedelta(days=1),
    )
    assert renewed.renewal_count == 1
    released = release_security_audit_hold(db_session, actor=admin, hold_id=hold.id, now=now + timedelta(days=1, hours=1))
    assert released.released_by_user_id == admin.id


def test_dspt_retention_workers_have_timely_control_queue_schedules():
    expected = {
        "expire-ingestion-source-audio-every-10-seconds": "openscribe.expire_ingestion_source_audio",
        "expire-security-audit-events-every-10-seconds": "openscribe.expire_security_audit_events",
        "expire-legal-document-versions-every-10-seconds": "openscribe.expire_legal_document_versions",
    }
    for schedule_name, task_name in expected.items():
        schedule = celery_app.conf.beat_schedule[schedule_name]
        assert schedule == {"task": task_name, "schedule": 10.0, "options": {"expires": 10.0}}
        assert celery_app.conf.task_routes[task_name] == {"queue": "control"}
    assert celery_tasks.expire_ingestion_source_audio_task.name in celery_app.tasks
    assert celery_tasks.expire_security_audit_events_task.name in celery_app.tasks
    assert celery_tasks.expire_legal_document_versions_task.name in celery_app.tasks


def test_system_admin_can_place_renew_and_release_audit_hold_from_browser(
    raw_client, db_session, make_user
):
    admin = make_user(
        email="dspt-audit-browser-admin@example.com",
        password="password-1",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    event = SecurityAuditEvent(action="synthetic_browser_hold")
    db_session.add(event)
    db_session.commit()

    assert raw_client.post(
        "/api/v1/auth/login", json={"email": admin.email, "password": "password-1"}
    ).status_code == 200
    page = raw_client.get("/admin?tab=audit")
    assert page.status_code == 200
    csrf = raw_client.cookies["openscribe_csrf"]
    assert page.text.count(f'name="_csrf_token" value="{csrf}"') >= 1
    headers = {"Origin": "http://testserver"}
    now = utcnow()
    missing_csrf = raw_client.post(
        f"/admin/audit/events/{event.id}/holds",
        data={
            "reason": "incident",
            "reference": "INC-MISSING-CSRF",
            "review_at": (now + timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(),
        },
        headers=headers,
        follow_redirects=False,
    )
    assert missing_csrf.status_code == 403
    placed = raw_client.post(
        f"/admin/audit/events/{event.id}/holds",
        data={
            "reason": "incident",
            "reference": "INC-BROWSER-1",
            "review_at": (now + timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(),
            "_csrf_token": csrf,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert placed.status_code == 303
    hold = db_session.scalar(
        select(SecurityAuditEventHold).where(
            SecurityAuditEventHold.security_audit_event_id == event.id,
            SecurityAuditEventHold.released_at.is_(None),
        )
    )
    assert hold is not None
    held_page = raw_client.get("/admin?tab=audit")
    assert held_page.status_code == 200
    assert (
        f'action="/admin/audit/holds/{hold.id}/renew"' in held_page.text
        and f'action="/admin/audit/holds/{hold.id}/release"' in held_page.text
    )
    assert held_page.text.count(f'name="_csrf_token" value="{csrf}"') >= 2

    renewed = raw_client.post(
        f"/admin/audit/holds/{hold.id}/renew",
        data={
            "reason": "legal_duty",
            "reference": "LEGAL-BROWSER-2",
            "review_at": (now + timedelta(days=2)).isoformat(),
            "expires_at": (now + timedelta(days=3)).isoformat(),
            "_csrf_token": csrf,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert renewed.status_code == 303
    db_session.refresh(hold)
    assert hold.renewal_count == 1

    released = raw_client.post(
        f"/admin/audit/holds/{hold.id}/release",
        data={"_csrf_token": csrf},
        headers=headers,
        follow_redirects=False,
    )
    assert released.status_code == 303
    db_session.refresh(hold)
    assert hold.released_at is not None


def test_user_deletion_is_blocked_while_system_admin_owns_active_audit_hold(
    db_session, make_user
):
    actor = make_user(
        email="dspt-audit-delete-actor@example.com",
        password="password-1",
        is_system_admin=True,
    )
    owner = make_user(
        email="dspt-audit-delete-owner@example.com",
        password="password-1",
        is_system_admin=True,
    )
    event = SecurityAuditEvent(action="synthetic_owner_delete_hold")
    db_session.add(event)
    db_session.commit()
    now = utcnow()
    place_security_audit_hold(
        db_session,
        actor=owner,
        event_id=event.id,
        reason=SecurityAuditHoldReason.legal_duty,
        reference="LEGAL-OWNER-1",
        review_at=now + timedelta(days=1),
        expires_at=now + timedelta(days=2),
        now=now,
    )

    with pytest.raises(AppError, match="active audit holds"):
        delete_user(db_session, actor, owner.id)

    assert db_session.get(type(owner), owner.id) is not None
