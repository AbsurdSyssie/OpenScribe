"""Two-source transcript assembly: wait for both, preserve provenance, apply once."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

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
from app.services.telehealth_merge import TelehealthSourceTurn


def test_capture_never_applies_before_both_sources_and_commits_once(
    db_session, make_team, make_user, monkeypatch,
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
    monkeypatch.setattr("app.services.structured_transcripts.store_turns_on_version", lambda **kwargs: None)
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.transcripts._trigger_waiting_generation_dispatches", lambda **kwargs: None)

    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    db_session.refresh(session)
    db_session.refresh(transcript)
    assert session.status == "completed"
    assert transcript.status is TranscriptStatus.ready
    assert len(versions) == 1
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
