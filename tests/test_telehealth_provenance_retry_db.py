"""ASR retry must not erase an earlier clinically incomplete capture warning."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.models import (
    TelehealthCapturePart, TelehealthCaptureSession, Transcript,
    TranscriptIngestionJob, TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus, TranscriptIngestionMode, TranscriptStatus,
    utcnow,
)
from app.services.transcripts import _mark_job_failed, retry_failed_audio_ingestion


def _pending_source(db, owner, *, failure_code=None):
    root = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptStatus.transcribing,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db.add(root)
    db.flush()
    capture = TelehealthCaptureSession(
        transcript_id=root.id, owner_user_id=owner.id, team_id=owner.team_id,
        status="processing", timeline_sample_rate=48000,
        failure_code=failure_code,
        failure_channel="patient" if failure_code else None,
        incomplete_acknowledged_at=utcnow() if failure_code else None,
    )
    db.add(capture)
    db.flush()
    job = TranscriptIngestionJob(
        transcript_id=root.id, owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptIngestionJobStatus.processing,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-patient.wav",
        source_audio_vault_ref=f"secret:openscribe/transcript-ingestion/{uuid4()}/source-audio",
        source_audio_size_bytes=3244,
        source_audio_duration_seconds=0.1,
        source_audio_expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(job)
    db.flush()
    part = TelehealthCapturePart(
        session_id=capture.id, transcript_id=root.id,
        owner_user_id=owner.id, team_id=owner.team_id,
        channel="patient", sequence_no=0, idempotency_key=uuid4(),
        status="processing", ingestion_job_id=job.id,
        timeline_start_frame=0, timeline_end_frame=4800,
        timeline_sample_rate=48000, file_sample_rate=16000,
        file_samples=1600,
        timing_spans_json=[{
            "file_start_sample": 0, "file_end_sample": 1600,
            "timeline_start_frame": 0, "timeline_end_frame": 4800,
        }],
        source_audio_size_bytes=3244,
        source_audio_fingerprint="a" * 64,
        source_audio_duration_seconds=0.1,
    )
    db.add(part)
    db.commit()
    return root, capture, part, job


@pytest.mark.parametrize("original_interruption", ["source_ended", None])
def test_failed_asr_and_retry_preserve_original_interruption(
    db_session, make_team, make_user, monkeypatch, original_interruption,
):
    team = make_team(name="Telehealth ASR Retry Provenance")
    owner = make_user(
        email=f"telehealth-provenance-{original_interruption or 'clean'}@example.com",
        password="password-1", team=team,
    )
    root, capture, part, job = _pending_source(
        db_session, owner, failure_code=original_interruption,
    )
    _mark_job_failed(db_session, root, job, code="stt_timeout", message="Synthetic STT timeout")
    db_session.refresh(capture)
    db_session.refresh(part)
    assert capture.status == "failed"
    assert part.status == "failed"
    assert capture.failure_code == (original_interruption or "transcription_failed")
    assert capture.failure_channel == "patient"

    reservations = []
    monkeypatch.setattr(
        "app.services.transcripts._queue_ingestion_attempt_and_dispatch",
        lambda db, *, job, transcript, measured_duration_seconds: reservations.append(
            (job.id, transcript.id, measured_duration_seconds)
        ),
    )
    root, retry_job = retry_failed_audio_ingestion(
        db_session, owner, transcript_id=root.id, job_id=job.id,
    )
    db_session.refresh(capture)
    db_session.refresh(part)
    assert retry_job.id == job.id
    assert retry_job.status is TranscriptIngestionJobStatus.queued
    assert root.status is TranscriptStatus.transcribing
    assert capture.status == "processing"
    assert part.status == "processing"
    assert capture.failure_code == original_interruption
    assert capture.failure_channel == ("patient" if original_interruption else None)
    assert bool(capture.incomplete_acknowledged_at) == bool(original_interruption)
    assert len(reservations) == 1
    assert reservations[0][0] == job.id
