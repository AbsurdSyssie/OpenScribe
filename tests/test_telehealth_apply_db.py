"""Two-source transcript assembly: wait for both, preserve provenance, apply once."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services.telehealth_apply import apply_completed_telehealth_capture
from app.services.telehealth_apply import _mapped_turns
from app.services.telehealth_merge import TelehealthSourceTurn
from app.services.telehealth_audio import TelehealthSourceSpan


@pytest.mark.parametrize("silent_channels", [(), ("patient",), ("patient", "clinician")])
def test_capture_never_applies_before_both_sources_and_commits_once(
    db_session, make_team, make_user, monkeypatch,
    silent_channels,
):
    team = make_team(name="Telehealth Apply Team")
    owner = make_user(email="telehealth-apply@example.com", password="password-1", team=team)
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id,
        status=TranscriptStatus.transcribing,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.flush()
    session = TelehealthCaptureSession(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        timeline_sample_rate=48000, status="processing",
    )
    db_session.add(session)
    db_session.flush()

    parts = []
    for channel in ("patient", "clinician"):
        job = TranscriptIngestionJob(
            transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename=channel + ".wav",
            status=TranscriptIngestionJobStatus.completed,
            result_text_encrypted="synthetic encrypted result, never decrypted in this test",
        )
        db_session.add(job)
        db_session.flush()
        part = TelehealthCapturePart(
            id=uuid4(), session_id=session.id, transcript_id=transcript.id,
            owner_user_id=owner.id, team_id=team.id,
            channel=channel, sequence_no=0, idempotency_key=uuid4(),
            status="transcribed", ingestion_job_id=job.id,
            timeline_start_frame=0, timeline_end_frame=4800,
            timeline_sample_rate=48000,
            file_sample_rate=16000, file_samples=1600,
            timing_spans_json=[{
                "file_start_sample": 0, "file_end_sample": 1600,
                "timeline_start_frame": 0, "timeline_end_frame": 4800,
            }],
            source_audio_size_bytes=3244,
            source_audio_fingerprint="a" * 64,
            source_audio_duration_seconds=0.1,
        )
        db_session.add(part)
        parts.append(part)
    db_session.commit()

    # Simulate the patient job being still in flight: do not expose a
    # clinician-only transcript or mark the consultation ready.
    parts[0].status = "processing"
    db_session.commit()
    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is False
    db_session.refresh(session)
    assert session.status == "processing"
    assert transcript.status is TranscriptStatus.transcribing
    parts[0].status = "transcribed"
    db_session.commit()

    def fake_mapped(db, *, transcript, part, job):
        if part.channel in silent_channels:
            return ()
        if part.channel == "clinician":
            return (TelehealthSourceTurn(part.id, part.channel, 0, 0, "Synthetic clinician question", 100, 250),)
        return (TelehealthSourceTurn(part.id, part.channel, 0, 0, "Synthetic patient answer", 200, 450),)

    monkeypatch.setattr("app.services.telehealth_apply._mapped_turns", fake_mapped)
    recorded = []
    def fake_append(db, *, transcript, scope_id, turns, mark_ready):
        assert scope_id == session.id
        assert mark_ready is False
        recorded.append(turns)
        return [{"text": item["text"], "speaker_id": item["speaker_id"]} for item in turns]

    monkeypatch.setattr("app.services.structured_transcripts.append_transcript_turns", fake_append)
    versions = []
    def fake_version(db, *, transcript, owner_user_id, plaintext):
        versions.append(plaintext)
        return SimpleNamespace(id=uuid4(), transcript_id=transcript.id)

    monkeypatch.setattr("app.services.transcripts._create_transcript_version_from_text", fake_version)
    monkeypatch.setattr("app.services.structured_transcripts.store_turns_on_version", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.transcripts._trigger_waiting_generation_dispatches", lambda *args, **kwargs: None)

    if len(silent_channels) == 2:
        assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is False
        db_session.refresh(session)
        db_session.refresh(transcript)
        assert session.status == "failed"
        assert session.failure_code == "no_transcribed_speech"
        assert transcript.status is TranscriptStatus.failed
        assert versions == []
        return

    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    db_session.refresh(session)
    db_session.refresh(transcript)
    assert session.status == "completed"
    assert transcript.status is TranscriptStatus.ready
    assert len(versions) == 1
    if silent_channels == ("patient",):
        assert [item["text"] for item in recorded[0]] == ["Clinician: Synthetic clinician question"]
        assert [item["speaker_id"] for item in recorded[0]] == ["clinician"]
        assert [item["start_ms"] for item in recorded[0]] == [100]
        assert "Clinician: " in versions[0] and "Patient: " not in versions[0]
    else:
        assert [item["text"] for item in recorded[0]] == [
            "Clinician: Synthetic clinician question",
            "Patient: Synthetic patient answer",
        ]
        assert [item["speaker_id"] for item in recorded[0]] == ["clinician", "patient"]
        assert [item["start_ms"] for item in recorded[0]] == [100, 200]
        assert "Clinician: " in versions[0] and "Patient: " in versions[0]

    for job in db_session.scalars(select(TranscriptIngestionJob)):
        assert job.status is TranscriptIngestionJobStatus.applied
        assert job.applied_at is not None
    # Duplicate worker reconciliation never adds another transcript version.
    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    assert len(versions) == 1


@pytest.mark.parametrize("timing_case", ["outside_source", "rewound_source"])
def test_mapped_word_segments_fall_back_to_canonical_text_when_timing_is_unsafe(
    db_session, make_team, make_user, monkeypatch, timing_case,
):
    team = make_team(name=f"Telehealth Unsafe Timing {timing_case}")
    owner = make_user(email=f"telehealth-unsafe-{timing_case}@example.com", password="password-1", team=team)
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id,
        status=TranscriptStatus.transcribing,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.flush()
    session = TelehealthCaptureSession(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        timeline_sample_rate=48000, status="processing",
    )
    db_session.add(session)
    db_session.flush()
    job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        status=TranscriptIngestionJobStatus.completed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic.wav", result_text_encrypted="synthetic ciphertext",
        result_turns_encrypted="synthetic ciphertext",
    )
    db_session.add(job)
    db_session.flush()
    part = TelehealthCapturePart(
        id=uuid4(), session_id=session.id, transcript_id=transcript.id,
        owner_user_id=owner.id, team_id=team.id,
        channel="clinician", sequence_no=0, idempotency_key=uuid4(),
        status="transcribed", ingestion_job_id=job.id,
        timeline_start_frame=0, timeline_end_frame=96000,
        timeline_sample_rate=48000, file_sample_rate=16000, file_samples=32000,
        source_audio_size_bytes=3244, source_audio_fingerprint="a" * 64,
        source_audio_duration_seconds=2.0,
        timing_spans_json=[{
            "file_start_sample": 0, "file_end_sample": 32000,
            "timeline_start_frame": 0, "timeline_end_frame": 96000,
        }],
    )
    db_session.add(part)
    db_session.commit()
    if timing_case == "outside_source":
        stored_turns = [
            {"text": "Hello,", "start_ms": 0, "end_ms": 200},
            {"text": "how are you?", "start_ms": 200, "end_ms": 2100},
        ]
    else:
        stored_turns = [
            {"text": "Hello,", "start_ms": 1000, "end_ms": 1300},
            {"text": "how are you?", "start_ms": 500, "end_ms": 800},
        ]
    monkeypatch.setattr(
        "app.services.transcripts.job_result_text",
        lambda *_args, **_kwargs: "Hello, how are you?",
    )
    monkeypatch.setattr(
        "app.services.telehealth_apply.decrypt_json_for_owner",
        lambda *_args, **_kwargs: {"version": 1, "turns": stored_turns},
    )

    turns = _mapped_turns(db_session, transcript=transcript, part=part, job=job)

    assert len(turns) == 1
    assert turns[0].text == "Hello, how are you?"
    assert turns[0].start_ms is None and turns[0].end_ms is None
