"""Unsubmitted Telehealth audio discard and shared note-generation barrier.

Uses synthetic source metadata. No real patient audio or provider calls.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    TelehealthCapturePart, TelehealthCaptureSession, Transcript,
    TranscriptIngestionJob, TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus, TranscriptIngestionMode, TranscriptStatus, utcnow,
)
from app.services.telehealth_capture import (
    acknowledge_incomplete_telehealth_capture, discard_unsubmitted_telehealth_capture,
)
from app.services.telehealth_api import unresolved_telehealth_capture_detail
from app.services.structured_transcripts import append_transcript_turns, load_current_turns
from app.services.transcripts import (
    _resolved_transcript_status, assert_no_unresolved_stream_session,
)


def _fixture(db, owner, *, status="interrupted", with_job=False):
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptStatus.ready, ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=1),
    )
    db.add(transcript)
    db.flush()
    capture = TelehealthCaptureSession(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        status=status, timeline_sample_rate=48000,
        failure_channel="patient" if status == "interrupted" else None,
        failure_code="source_ended" if status == "interrupted" else None,
    )
    db.add(capture)
    db.flush()
    job = None
    if with_job:
        job = TranscriptIngestionJob(
            transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="synthetic-telehealth.wav",
            status=TranscriptIngestionJobStatus.queued,
        )
        db.add(job)
        db.flush()
    audio_ref = f"secret:openscribe/transcript-ingestion/{uuid4()}/source-audio"
    part = TelehealthCapturePart(
        session_id=capture.id, transcript_id=transcript.id,
        owner_user_id=owner.id, team_id=owner.team_id,
        channel="patient", sequence_no=0, idempotency_key=uuid4(),
        status="processing" if with_job else "staged",
        timeline_start_frame=0, timeline_end_frame=4800,
        timeline_sample_rate=48000, file_sample_rate=16000,
        file_samples=1600,
        timing_spans_json=[{
            "file_start_sample": 0, "file_end_sample": 1600,
            "timeline_start_frame": 0, "timeline_end_frame": 4800,
        }],
        source_audio_size_bytes=3244, source_audio_fingerprint="a" * 64,
        source_audio_duration_seconds=0.1,
        source_audio_vault_ref=None if with_job else audio_ref,
        source_audio_expires_at=utcnow() + timedelta(hours=1),
        ingestion_job_id=job.id if job else None,
    )
    db.add(part)
    db.commit()
    return transcript, capture, part, audio_ref


def test_discard_unsubmitted_interruption_clears_barrier_and_queues_vault_delete(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Discard Team")
    owner = make_user(
        email="telehealth-discard-owner@example.com", password="password-1", team=team,
    )
    stranger = make_user(
        email="telehealth-discard-stranger@example.com", password="password-1", team=team,
    )
    transcript, capture, part, audio_ref = _fixture(db_session, owner)
    restored = unresolved_telehealth_capture_detail(
        db_session, owner, transcript_id=transcript.id,
    )
    assert restored is not None
    assert restored.capture_session_id == capture.id
    assert restored.parts[0].id == part.id
    assert "source_audio_vault_ref" not in restored.model_dump()
    assert _resolved_transcript_status(db_session, transcript=transcript) is TranscriptStatus.failed
    with pytest.raises(AppError) as error:
        assert_no_unresolved_stream_session(db_session, transcript_id=transcript.id)
    assert error.value.code == "telehealth_capture_unresolved"
    db_session.rollback()
    with pytest.raises(AppError) as error:
        discard_unsubmitted_telehealth_capture(
            db_session, stranger,
            transcript_id=transcript.id, capture_session_id=capture.id,
        )
    assert error.value.code == "forbidden"
    db_session.rollback()

    deleted = []
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted.append(secret_ref),
    )
    discarded = discard_unsubmitted_telehealth_capture(
        db_session, owner,
        transcript_id=transcript.id, capture_session_id=capture.id,
    )
    assert discarded.status == "discarded"
    assert discarded.recording_stopped_at is not None
    db_session.refresh(part)
    assert part.status == "expired"
    assert part.source_audio_vault_ref is None
    assert deleted == [audio_ref]
    assert _resolved_transcript_status(db_session, transcript=transcript) is TranscriptStatus.ready
    assert_no_unresolved_stream_session(db_session, transcript_id=transcript.id)
    assert unresolved_telehealth_capture_detail(
        db_session, owner, transcript_id=transcript.id,
    ) is None

    # A repeated response after an ambiguous HTTP result is idempotent.
    assert discard_unsubmitted_telehealth_capture(
        db_session, owner,
        transcript_id=transcript.id, capture_session_id=capture.id,
    ).status == "discarded"
    assert deleted == [audio_ref]


def test_discard_refuses_any_stt_submitted_source_even_after_failure(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Discard STT Protection")
    owner = make_user(
        email="telehealth-discard-job@example.com", password="password-1", team=team,
    )
    transcript, capture, part, _ = _fixture(
        db_session, owner, status="failed", with_job=True,
    )
    with pytest.raises(AppError) as error:
        discard_unsubmitted_telehealth_capture(
            db_session, owner,
            transcript_id=transcript.id, capture_session_id=capture.id,
        )
    assert error.value.code == "conflict"
    db_session.rollback()
    db_session.refresh(part)
    db_session.refresh(capture)
    assert part.ingestion_job_id is not None
    assert capture.status == "failed"


def test_discard_acknowledged_interruption_removes_and_does_not_restore_warning(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Discard Warning")
    owner = make_user(
        email="telehealth-discard-warning@example.com", password="password-1", team=team,
    )
    transcript, capture, _, _ = _fixture(db_session, owner)
    acknowledge_incomplete_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=capture.id,
    )
    # Reproduce an existing current warning created before discard.
    before = append_transcript_turns(
        db_session, transcript=transcript, scope_id=uuid4(),
        turns=[{"text": "Synthetic prior consultation."}],
    )
    assert any(turn["source_warning"] for turn in before)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: None,
    )
    discarded = discard_unsubmitted_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=capture.id,
    )
    assert discarded.incomplete_acknowledged_at is not None
    assert discarded.failure_code == "source_ended"

    for text in ("Synthetic new source.", "Synthetic later source."):
        turns = append_transcript_turns(
            db_session, transcript=transcript, scope_id=uuid4(), turns=[{"text": text}],
        )
        db_session.commit()
        assert not any(turn["source_warning"] for turn in turns)
        assert not any(turn["source_warning"] for turn in load_current_turns(db_session, transcript=transcript))
