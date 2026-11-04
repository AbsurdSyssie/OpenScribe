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
from app.services.transcripts import (
    delete_transcripts,
    expire_ingestion_source_audio,
    process_transcript_audio_cleanup_jobs,
    queue_transcript_audio_cleanup,
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


def _part(db, owner, session, transcript, *, deadline):
    row = TelehealthCapturePart(
        id=uuid4(),
        session_id=session.id,
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        channel="patient",
        sequence_no=0,
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
    assert delete_transcripts(db_session, owner, transcript_ids=[another.id]) == 1
    assert held_ref in deleted
    assert db_session.get(TelehealthCapturePart, kept.id) is None


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

    with pytest.raises(AppError, match="not been safely uploaded"):
        seal_telehealth_capture(
            db_session, owner,
            transcript_id=transcript.id,
            capture_session_id=session_id,
            expected_clinician_parts=0,
            expected_patient_parts=1,
        )
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
