"""Owner-scoped telehealth staging and durable Vault lifecycle tests.

Uses synthetic PCM and mocked Vault writes; never contacts STT providers.
"""
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest
from sqlalchemy import func, select

from app.errors import AppError
from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptAudioCleanupJob,
    ProviderAttempt,
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    QuotaResource,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services.telehealth_audio import TelehealthSourceSpan
from app.services.telehealth_capture import (
    begin_telehealth_capture,
    request_telehealth_capture_stop,
    seal_telehealth_capture,
    stage_telehealth_audio_part,
)
from app.services.quotas import mark_provider_attempt_submitted, reserve_provider_attempt
from app.services.transcripts import (
    delete_transcripts,
    expire_ingestion_source_audio,
    process_transcript_ingestion_job,
    process_transcript_audio_cleanup_jobs,
    queue_transcript_audio_cleanup,
    reconcile_transcript_status,
    retry_failed_audio_ingestion,
)


def _transcript(db, owner, *, expires_at=None):
    row = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic telehealth consultation",
        status=TranscriptStatus.ready,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=expires_at or utcnow() + timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row


def _audio(*, first=123, count=1600, sample_rate=16000):
    stream = BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(first.to_bytes(2, "little", signed=True) * count)
    return stream.getvalue()


def _session(db, owner, transcript):
    row = TelehealthCaptureSession(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        timeline_sample_rate=48000,
        status="active",
    )
    db.add(row)
    db.commit()
    return row


def _part(db, owner, session, transcript, *, deadline, channel="patient", sequence_no=0):
    row = TelehealthCapturePart(
        id=uuid4(),
        session_id=session.id,
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        channel=channel,
        sequence_no=sequence_no,
        idempotency_key=uuid4(),
        status="staged",
        timeline_start_frame=0,
        timeline_end_frame=4800,
        timeline_sample_rate=48000,
        file_sample_rate=16000,
        file_samples=1600,
        timing_spans_json=[{
            "file_start_sample": 0, "file_end_sample": 1600,
            "timeline_start_frame": 0, "timeline_end_frame": 4800,
        }],
        source_audio_size_bytes=3244,
        source_audio_fingerprint="a" * 64,
        source_audio_duration_seconds=0.1,
        source_audio_vault_ref=None,  # Assigned below from the part UUID.
        source_audio_expires_at=deadline,
    )
    row.source_audio_vault_ref = f"secret:openscribe/transcript-ingestion/{row.id}/source-audio"
    db.add(row)
    db.commit()
    return row


def test_owner_bound_telehealth_session_and_idempotent_staging(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Synthetic Telehealth Staging Team")
    owner = make_user(email="telehealth-owner@example.com", password="password-1", team=team)
    another = make_user(email="telehealth-other@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    config_id = uuid4()
    monkeypatch.setattr(
        "app.services.telehealth_capture.resolve_selected_team_stt",
        lambda db, *, team_id: (None, SimpleNamespace(id=config_id), "fake-model", "en"),
    )
    monkeypatch.setattr(
        "app.services.telehealth_capture.ensure_stt_config_credential_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.telehealth_capture.keyed_digest_for_owner",
        lambda db, *, owner_user_id, purpose, value: sha256(
            (str(owner_user_id) + purpose + value).encode()
        ).hexdigest(),
    )
    vault_writes = []
    monkeypatch.setattr(
        "app.services.telehealth_capture.write_transcript_ingestion_source_audio",
        lambda *, job_id, audio_bytes: (
            vault_writes.append(job_id)
            or f"secret:openscribe/transcript-ingestion/{job_id}/source-audio"
        ),
    )
    session = begin_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, timeline_sample_rate=48000,
    )
    assert session.stt_config_id == config_id
    with pytest.raises(AppError):
        begin_telehealth_capture(
            db_session, owner, transcript_id=transcript.id, timeline_sample_rate=48000,
        )
    db_session.rollback()
    with pytest.raises(AppError):
        begin_telehealth_capture(
            db_session, another, transcript_id=transcript.id, timeline_sample_rate=48000,
        )
    db_session.rollback()

    first = _audio()
    key = uuid4()
    args = dict(
        transcript_id=transcript.id, capture_session_id=session.id,
        channel="patient", sequence_no=0, idempotency_key=key,
        spans=[TelehealthSourceSpan(0, 1600, 0, 4800)],
        audio_bytes=first,
    )
    row = stage_telehealth_audio_part(db_session, owner, **args)
    assert row.status == "staged"
    assert row.source_audio_vault_ref.endswith(f"{row.id}/source-audio")
    assert len(vault_writes) == 1
    assert stage_telehealth_audio_part(db_session, owner, **args).id == row.id
    assert len(vault_writes) == 1
    assert db_session.scalar(select(func.count()).select_from(TelehealthCapturePart)) == 1

    with pytest.raises(AppError) as error:
        stage_telehealth_audio_part(db_session, owner, **{**args, "audio_bytes": _audio(first=999)})
    assert error.value.code == "idempotency_conflict"
    db_session.rollback()

    session.status = "interrupted"
    db_session.add(session)
    db_session.commit()
    assert stage_telehealth_audio_part(db_session, owner, **args).id == row.id
    with pytest.raises(AppError) as error:
        stage_telehealth_audio_part(db_session, owner, **{
            **args, "idempotency_key": uuid4(), "sequence_no": 1,
            "spans": [TelehealthSourceSpan(0, 1600, 4800, 9600)],
        })
    assert error.value.code == "conflict"
    db_session.rollback()
    with pytest.raises(AppError):
        stage_telehealth_audio_part(db_session, another, **args)
    db_session.rollback()


def test_telehealth_staging_expiry_and_root_deletion_enqueue_vault_cleanup(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Vault Retention Team")
    owner = make_user(email="telehealth-retention@example.com", password="password-1", team=team)
    now = utcnow()
    transcript = _transcript(db_session, owner, expires_at=now + timedelta(days=1))
    session = _session(db_session, owner, transcript)
    part = _part(db_session, owner, session, transcript, deadline=now - timedelta(seconds=1))
    expected_ref = part.source_audio_vault_ref
    deleted = []
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted.append(secret_ref),
    )

    assert expire_ingestion_source_audio(db_session, now=now) == 1
    db_session.refresh(part)
    db_session.refresh(session)
    assert part.status == "expired"
    assert part.source_audio_vault_ref is None
    assert session.status == "failed"
    assert session.failure_channel == "patient"
    assert session.failure_code == "source_audio_expired"
    assert deleted == [expected_ref]
    assert expire_ingestion_source_audio(db_session, now=now) == 0

    # A second consultation still holding staged audio must enqueue cleanup
    # in the same transaction as deleting its transcript root.
    another = _transcript(db_session, owner, expires_at=now + timedelta(days=1))
    another_session = _session(db_session, owner, another)
    kept = _part(
        db_session, owner, another_session, another, deadline=now + timedelta(hours=1),
    )
    held_ref = kept.source_audio_vault_ref
    kept_id = kept.id
    assert delete_transcripts(db_session, owner, transcript_ids=[another.id]) == 1
    assert held_ref in deleted
    # SQLAlchemy may expire a deleted mapped object after the root commit.
    assert db_session.get(TelehealthCapturePart, kept_id) is None


@pytest.mark.parametrize("job_status", [TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing])
@pytest.mark.parametrize("original_failure", [None, "source_ended"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_expired_handed_off_telehealth_source_fails_linked_capture(
    db_session, make_team, make_user, monkeypatch,
    job_status, original_failure, cleanup_fails,
):
    team = make_team(name="Telehealth Handed-off Expiry")
    owner = make_user(email="telehealth-handoff-expiry@example.com", password="password-1", team=team)
    now = utcnow()
    transcript = _transcript(db_session, owner, expires_at=now + timedelta(days=1))
    session = _session(db_session, owner, transcript)
    session.failure_channel = "patient" if original_failure else None
    session.failure_code = original_failure
    session.incomplete_acknowledged_at = now - timedelta(minutes=1) if original_failure else None
    job = TranscriptIngestionJob(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=job_status,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-patient.wav",
        source_audio_vault_ref=f"secret:openscribe/transcript-ingestion/{uuid4()}/source-audio",
        source_audio_size_bytes=3244,
        source_audio_duration_seconds=0.1,
        source_audio_expires_at=now - timedelta(seconds=1),
    )
    db_session.add(job)
    db_session.flush()
    part = _part(db_session, owner, session, transcript, deadline=job.source_audio_expires_at)
    part.status = "processing"
    part.ingestion_job_id = job.id
    part.source_audio_vault_ref = None
    part.source_audio_expires_at = job.source_audio_expires_at
    expected_ref = job.source_audio_vault_ref
    db_session.add(part)
    session.status = "processing"
    db_session.add(session)
    db_session.commit()
    attempt = reserve_provider_attempt(
        db_session,
        team_id=owner.team_id,
        owner_user_id=owner.id,
        resource=QuotaResource.audio_seconds,
        attempt_kind=AttemptKind.stt_conversation,
        correlation_id=job.id,
        attempt_number=1,
        reserved_units=1,
        reservation_valid_until=now + timedelta(minutes=10),
        transcript_id=transcript.id,
        transcript_ingestion_job_id=job.id,
        measured_audio_seconds=0.1,
    )
    if job_status is TranscriptIngestionJobStatus.processing:
        mark_provider_attempt_submitted(
            db_session, attempt_id=attempt.id, deadline_at=now + timedelta(minutes=5), now=now,
        )
    db_session.commit()
    deleted = []
    def delete_source(*, secret_ref):
        if cleanup_fails:
            raise AppError(502, "vault_unavailable", "Synthetic cleanup failure")
        deleted.append(secret_ref)

    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        delete_source,
    )

    assert expire_ingestion_source_audio(db_session, now=now) == 1
    db_session.refresh(job)
    db_session.refresh(part)
    db_session.refresh(session)
    db_session.refresh(transcript)
    assert job.status is TranscriptIngestionJobStatus.failed
    assert job.error_code == "ingestion_source_expired"
    assert job.source_audio_vault_ref is None
    assert part.status == "failed"
    assert part.source_audio_vault_ref is None
    assert session.status == "failed"
    assert session.failure_channel == "patient"
    assert session.failure_code == (original_failure or "source_audio_expired")
    assert bool(session.incomplete_acknowledged_at) == bool(original_failure)
    assert transcript.status is TranscriptStatus.failed
    if cleanup_fails:
        cleanup = db_session.scalar(select(TranscriptAudioCleanupJob).where(
            TranscriptAudioCleanupJob.secret_ref == expected_ref,
        ))
        assert cleanup is not None and cleanup.attempt_count == 1
        assert deleted == []
    else:
        assert deleted == [expected_ref]
    db_session.refresh(attempt)
    if job_status is TranscriptIngestionJobStatus.processing:
        assert attempt.status is AttemptStatus.settled
        assert attempt.outcome is AttemptOutcome.unknown
    else:
        assert attempt.status is AttemptStatus.cancelled
    with pytest.raises(AppError) as error:
        retry_failed_audio_ingestion(
            db_session, owner, transcript_id=transcript.id, job_id=job.id,
        )
    assert error.value.code == "ingestion_retry_expired"


@pytest.mark.parametrize("sibling_status", [
    TranscriptIngestionJobStatus.queued,
    TranscriptIngestionJobStatus.completed,
])
def test_failed_telehealth_capture_dominates_sibling_job_reconciliation(
    db_session, make_team, make_user, sibling_status,
):
    team = make_team(name=f"Telehealth Expired Sibling {sibling_status.value}")
    owner = make_user(email=f"telehealth-sibling-{sibling_status.value}@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session = _session(db_session, owner, transcript)
    session.status = "failed"
    session.failure_channel = "patient"
    session.failure_code = "source_audio_expired"
    expired_job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptIngestionJobStatus.failed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-expired.wav",
        error_code="ingestion_source_expired",
    )
    sibling_job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        status=sibling_status,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-sibling.wav",
        result_text_encrypted="synthetic encrypted result" if sibling_status is TranscriptIngestionJobStatus.completed else None,
    )
    db_session.add_all([expired_job, sibling_job])
    db_session.flush()
    expired_part = _part(db_session, owner, session, transcript, deadline=utcnow())
    expired_part.status = "failed"
    expired_part.ingestion_job_id = expired_job.id
    expired_part.source_audio_vault_ref = None
    sibling_part = _part(
        db_session, owner, session, transcript, deadline=utcnow(),
        channel="clinician", sequence_no=0,
    )
    sibling_part.status = "transcribed" if sibling_status is TranscriptIngestionJobStatus.completed else "processing"
    sibling_part.ingestion_job_id = sibling_job.id
    sibling_part.source_audio_vault_ref = None
    db_session.add_all([expired_part, sibling_part, session])
    db_session.commit()

    reconcile_transcript_status(db_session, transcript=transcript)

    db_session.refresh(transcript)
    assert transcript.status is TranscriptStatus.failed


def test_provider_result_returning_after_telehealth_source_expiry_is_fenced(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Late Result Fence")
    owner = make_user(email="telehealth-late-result@example.com", password="password-1", team=team)
    now = utcnow()
    transcript = _transcript(db_session, owner)
    session = _session(db_session, owner, transcript)
    deadline = now + timedelta(minutes=1)
    job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptIngestionJobStatus.queued,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-patient.wav",
        source_audio_vault_ref=f"secret:openscribe/transcript-ingestion/{uuid4()}/source-audio",
        source_audio_size_bytes=3244, source_audio_duration_seconds=0.1,
        source_audio_expires_at=deadline,
    )
    db_session.add(job)
    db_session.flush()
    part = _part(db_session, owner, session, transcript, deadline=deadline)
    part.status = "processing"
    part.ingestion_job_id = job.id
    part.source_audio_vault_ref = None
    part.source_audio_expires_at = deadline
    db_session.add(part)
    sibling_deadline = now + timedelta(minutes=5)
    sibling_job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptIngestionJobStatus.queued,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-clinician.wav",
        source_audio_vault_ref=f"secret:openscribe/transcript-ingestion/{uuid4()}/source-audio",
        source_audio_size_bytes=3244, source_audio_duration_seconds=0.1,
        source_audio_expires_at=sibling_deadline,
    )
    db_session.add(sibling_job)
    db_session.flush()
    sibling_part = _part(
        db_session, owner, session, transcript, deadline=sibling_deadline,
        channel="clinician", sequence_no=0,
    )
    sibling_part.status = "processing"
    sibling_part.ingestion_job_id = sibling_job.id
    sibling_part.source_audio_vault_ref = None
    sibling_part.source_audio_expires_at = sibling_deadline
    db_session.add(sibling_part)
    session.status = "processing"
    transcript.status = TranscriptStatus.transcribing
    db_session.add_all([part, session, transcript])
    attempt = reserve_provider_attempt(
        db_session, team_id=owner.team_id, owner_user_id=owner.id,
        resource=QuotaResource.audio_seconds, attempt_kind=AttemptKind.stt_conversation,
        correlation_id=job.id, attempt_number=1, reserved_units=1,
        reservation_valid_until=now + timedelta(minutes=10), transcript_id=transcript.id,
        transcript_ingestion_job_id=job.id, measured_audio_seconds=0.1,
    )
    sibling_attempt = reserve_provider_attempt(
        db_session, team_id=owner.team_id, owner_user_id=owner.id,
        resource=QuotaResource.audio_seconds, attempt_kind=AttemptKind.stt_conversation,
        correlation_id=sibling_job.id, attempt_number=1, reserved_units=1,
        reservation_valid_until=now + timedelta(minutes=10), transcript_id=transcript.id,
        transcript_ingestion_job_id=sibling_job.id, measured_audio_seconds=0.1,
    )
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts._read_queued_source_audio", lambda *args, **kwargs: b"synthetic audio")
    monkeypatch.setattr(
        "app.services.transcripts.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: SimpleNamespace(data=b"synthetic normalized audio", filename="synthetic.wav", content_type="audio/wav"),
    )
    monkeypatch.setattr("app.services.transcripts.enforce_whole_file_duration_limit", lambda **kwargs: None)
    monkeypatch.setattr("app.services.transcripts.resolve_stt_snapshot_bearer_token", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.transcripts.delete_transcript_ingestion_source_audio", lambda **kwargs: None)

    provider_calls = 0
    def provider_returns_after_expiry(db, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        assert kwargs["telehealth_timing"] is True
        if provider_calls == 1:
            expire_ingestion_source_audio(db, now=deadline + timedelta(seconds=1))
            return "Synthetic late result must not persist"
        return "Synthetic sibling result stays unapplied"

    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", provider_returns_after_expiry)

    processed = process_transcript_ingestion_job(db_session, job_id=job.id)

    assert processed is not None and processed.status is TranscriptIngestionJobStatus.failed
    sibling_processed = process_transcript_ingestion_job(db_session, job_id=sibling_job.id)
    assert sibling_processed is not None and sibling_processed.status is TranscriptIngestionJobStatus.completed
    job = db_session.get(TranscriptIngestionJob, job.id)
    part = db_session.get(TelehealthCapturePart, part.id)
    sibling_job = db_session.get(TranscriptIngestionJob, sibling_job.id)
    sibling_part = db_session.get(TelehealthCapturePart, sibling_part.id)
    session = db_session.get(TelehealthCaptureSession, session.id)
    transcript = db_session.get(Transcript, transcript.id)
    attempt = db_session.get(ProviderAttempt, attempt.id)
    sibling_attempt = db_session.get(ProviderAttempt, sibling_attempt.id)
    assert job.result_text_encrypted is None
    assert part.status == "failed"
    assert sibling_part.status == "transcribed"
    assert sibling_job.result_text_encrypted is not None
    assert session.status == "failed"
    assert transcript.status is TranscriptStatus.failed
    assert attempt.status is AttemptStatus.settled
    assert attempt.outcome is AttemptOutcome.unknown
    assert sibling_attempt.status is AttemptStatus.settled
    assert sibling_attempt.outcome is AttemptOutcome.succeeded


def test_staged_audio_prevents_cleanup_when_source_reference_is_still_live(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Cleanup Guard")
    owner = make_user(email="telehealth-guard@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session = _session(db_session, owner, transcript)
    part = _part(
        db_session, owner, session, transcript,
        deadline=utcnow() + timedelta(hours=1),
    )
    ref = part.source_audio_vault_ref
    queue_transcript_audio_cleanup(db_session, secret_refs=[ref])
    db_session.commit()
    deleted = []
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted.append(secret_ref),
    )

    assert process_transcript_audio_cleanup_jobs(db_session) == 0
    assert deleted == []
    assert db_session.scalar(select(TranscriptAudioCleanupJob.id)) is None
    db_session.refresh(part)
    assert part.source_audio_vault_ref == ref



def test_capture_stop_salvages_pending_audio_then_seals_interrupted_run(
    db_session, make_team, make_user,
):
    team = make_team(name="Telehealth Capture Lifecycle Team")
    owner = make_user(email="telehealth-stop@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session = _session(db_session, owner, transcript)
    session_id = session.id

    stopped = request_telehealth_capture_stop(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session_id,
        interrupted_channel="patient",
        failure_code="source_ended",
    )
    assert stopped.status == "stopping"
    assert stopped.failure_channel == "patient"
    assert stopped.recording_stopped_at is not None

    with pytest.raises(AppError) as error:
        seal_telehealth_capture(
            db_session, owner,
            transcript_id=transcript.id,
            capture_session_id=session_id,
            expected_clinician_parts=0,
            expected_patient_parts=1,
        )
    assert error.value.message == "Some recording parts have not been safely uploaded."
    db_session.rollback()

    # The original recording may have buffered source data when its tab
    # disconnected. Its parts can still finish staging while stopping.
    _part(
        db_session, owner, session, transcript,
        deadline=utcnow() + timedelta(hours=1),
    )
    sealed = seal_telehealth_capture(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session_id,
        expected_clinician_parts=0,
        expected_patient_parts=1,
    )
    assert sealed.status == "interrupted"
    assert sealed.failure_channel == "patient"
    assert sealed.failure_code == "source_ended"
    assert seal_telehealth_capture(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session_id,
        expected_clinician_parts=0,
        expected_patient_parts=1,
    ).status == "interrupted"

    assert request_telehealth_capture_stop(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session_id,
        interrupted_channel="patient",
        failure_code="source_ended",
    ).status == "interrupted"

    with pytest.raises(AppError):
        request_telehealth_capture_stop(
            db_session, owner,
            transcript_id=transcript.id,
            capture_session_id=session_id,
            interrupted_channel="clinician",
            failure_code="source_ended",
        )
    db_session.rollback()


def test_telehealth_clock_failure_can_escalate_after_clean_stop(
    db_session, make_team, make_user,
):
    team = make_team(name="Telehealth Late Capture Failure Team")
    owner = make_user(email="telehealth-clock@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session = _session(db_session, owner, transcript)
    stopped = request_telehealth_capture_stop(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session.id,
    )
    assert stopped.status == "stopping"
    interrupted = request_telehealth_capture_stop(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session.id,
        failure_code="audio_clock_interrupted",
    )
    assert interrupted.status == "stopping"
    assert interrupted.failure_channel is None
    assert interrupted.failure_code == "audio_clock_interrupted"
    # An older clean-stop replay cannot downgrade the interrupted run.
    assert request_telehealth_capture_stop(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session.id,
    ).failure_code == "audio_clock_interrupted"
    assert seal_telehealth_capture(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session.id,
        expected_clinician_parts=0,
        expected_patient_parts=0,
    ).status == "interrupted"


def test_clean_telehealth_capture_can_seal_with_no_detected_patient_speech(
    db_session, make_team, make_user,
):
    team = make_team(name="Telehealth Silent Patient Team")
    owner = make_user(email="telehealth-silent@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session = _session(db_session, owner, transcript)
    assert request_telehealth_capture_stop(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session.id,
    ).status == "stopping"
    # VAD may retain zero patient speech; that is not proof the source failed.
    assert seal_telehealth_capture(
        db_session, owner,
        transcript_id=transcript.id,
        capture_session_id=session.id,
        expected_clinician_parts=0,
        expected_patient_parts=0,
    ).status == "ready"
