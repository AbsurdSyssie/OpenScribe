"""Two-source transcript assembly: wait for both, preserve provenance, apply once."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.services.content_crypto import decrypt_text_for_owner

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
from app.services.telehealth_apply import (
    apply_completed_telehealth_capture,
    reconcile_processing_telehealth_captures,
)
from app.services.telehealth_apply import _mapped_turns
from app.services.telehealth_merge import TelehealthSourceTurn, merge_telehealth_source_turns
from app.services.telehealth_audio import TelehealthSourceSpan


@pytest.mark.parametrize("failure_mode", ["raise", "return_false"])
def test_reconciler_skips_pending_batch_and_rotates_failed_ready_candidate(
    db_session, make_team, make_user, monkeypatch, failure_mode,
):
    team = make_team(name="Telehealth Fair Reconciliation")
    owner = make_user(email="telehealth-fair-reconcile@example.com", password="password-1", team=team)
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id,
        status=TranscriptStatus.transcribing,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.flush()
    old = utcnow() - timedelta(days=2)
    for index in range(51):
        db_session.add(TelehealthCaptureSession(
            transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
            timeline_sample_rate=48000, status="processing",
            updated_at=old + timedelta(seconds=index),
        ))

    def ready_session(offset):
        session = TelehealthCaptureSession(
            transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
            timeline_sample_rate=48000, status="processing",
            failure_code="source_ended", incomplete_acknowledged_at=utcnow(),
            updated_at=old + timedelta(seconds=100 + offset),
        )
        db_session.add(session)
        db_session.flush()
        job = TranscriptIngestionJob(
            transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="synthetic.wav", status=TranscriptIngestionJobStatus.completed,
            result_text_encrypted="synthetic ciphertext",
        )
        db_session.add(job)
        db_session.flush()
        db_session.add(TelehealthCapturePart(
            id=uuid4(), session_id=session.id, transcript_id=transcript.id,
            owner_user_id=owner.id, team_id=team.id, channel="clinician",
            sequence_no=0, idempotency_key=uuid4(), status="transcribed",
            ingestion_job_id=job.id, timeline_start_frame=0, timeline_end_frame=4800,
            timeline_sample_rate=48000, file_sample_rate=16000, file_samples=1600,
            timing_spans_json=[{
                "file_start_sample": 0, "file_end_sample": 1600,
                "timeline_start_frame": 0, "timeline_end_frame": 4800,
            }], source_audio_size_bytes=3244,
            source_audio_fingerprint="b" * 64, source_audio_duration_seconds=0.1,
        ))
        return session

    failing = ready_session(0)
    later = ready_session(1)
    db_session.commit()
    failures_remaining = {failing.id: 1}
    calls = []

    def fake_apply(db, *, capture_session_id):
        calls.append(capture_session_id)
        if capture_session_id == failing.id and failures_remaining[failing.id]:
            failures_remaining[failing.id] -= 1
            if failure_mode == "raise":
                raise RuntimeError("synthetic transient apply failure")
            return False
        row = db.get(TelehealthCaptureSession, capture_session_id)
        row.status = "completed"
        db.commit()
        return True

    monkeypatch.setattr("app.services.telehealth_apply.apply_completed_telehealth_capture", fake_apply)
    # The earlier 51 pending sessions do not occupy the bounded batch.
    assert reconcile_processing_telehealth_captures(db_session, batch_size=1) == 0
    assert calls == [failing.id]
    db_session.refresh(later)
    db_session.refresh(failing)
    assert failing.status == "processing"
    assert failing.updated_at > old + timedelta(seconds=100)

    # The rotated failure cannot monopolize a one-row batch; the next ready
    # capture applies before the first candidate is retried.
    assert reconcile_processing_telehealth_captures(db_session, batch_size=1) == 1
    db_session.refresh(later)
    db_session.refresh(failing)
    assert later.status == "completed"
    assert failing.status == "processing"

    assert reconcile_processing_telehealth_captures(db_session, batch_size=1) == 1
    db_session.refresh(failing)
    assert failing.status == "completed"
    assert calls == [failing.id, later.id, failing.id]


def test_real_mapping_and_merge_keep_interleaving_with_small_tail_overrun(monkeypatch):
    owner_id = uuid4()
    transcript = SimpleNamespace(owner_user_id=owner_id)
    patient_job = SimpleNamespace(id=uuid4(), result_turns_encrypted="synthetic")
    clinician_job = SimpleNamespace(id=uuid4(), result_turns_encrypted="synthetic")

    def part(channel):
        return SimpleNamespace(
            id=uuid4(), session_id=uuid4(), channel=channel, sequence_no=0,
            idempotency_key=uuid4(), timeline_sample_rate=48_000,
            file_sample_rate=16_000, file_samples=160_000,
            timeline_start_frame=0, timeline_end_frame=480_000,
            timing_spans_json=[{
                "file_start_sample": 0, "file_end_sample": 160_000,
                "timeline_start_frame": 0, "timeline_end_frame": 480_000,
            }],
        )

    patient = part("patient")
    clinician = part("clinician")
    texts = {patient_job.id: "Patient response", clinician_job.id: "Clinician interruption"}
    words = {
        patient_job.id: [
            {"text": "Patient", "start_ms": 9_500, "end_ms": 9_800},
            {"text": "response", "start_ms": 9_800, "end_ms": 11_600},
        ],
        clinician_job.id: [
            {"text": "Clinician", "start_ms": 9_700, "end_ms": 9_900},
            {"text": "interruption", "start_ms": 9_900, "end_ms": 10_000},
        ],
    }
    monkeypatch.setattr("app.services.transcripts.job_result_text", lambda _db, *, job, **_kw: texts[job.id])
    monkeypatch.setattr(
        "app.services.telehealth_apply.decrypt_json_for_owner",
        lambda _db, *, record_id, **_kw: {"version": 1, "turns": words[record_id]},
    )

    mapped = [
        *_mapped_turns(None, transcript=transcript, part=patient, job=patient_job),
        *_mapped_turns(None, transcript=transcript, part=clinician, job=clinician_job),
    ]
    result = merge_telehealth_source_turns(mapped)
    assert result.mode == "timed"
    assert [(turn.speaker_id, turn.start_ms, turn.end_ms, turn.text) for turn in result.turns] == [
        ("patient", 9_500, 10_000, "Patient response"),
        ("clinician", 9_700, 10_000, "Clinician interruption"),
    ]


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
    real_merge = __import__(
        "app.services.structured_transcripts", fromlist=["merge_transcript_turns_preserving_order"]
    ).merge_transcript_turns_preserving_order
    def capture_merge(db, *, transcript, scope_id, turns):
        assert scope_id == session.id
        recorded.append(turns)
        return real_merge(db, transcript=transcript, scope_id=scope_id, turns=turns)
    monkeypatch.setattr(
        "app.services.structured_transcripts.merge_transcript_turns_preserving_order", capture_merge,
    )
    versions = []
    real_create_version = __import__(
        "app.services.transcripts", fromlist=["_create_transcript_version_from_text"]
    )._create_transcript_version_from_text
    def capture_version(db, *, transcript, owner_user_id, plaintext):
        version = real_create_version(
            db, transcript=transcript, owner_user_id=owner_user_id, plaintext=plaintext,
        )
        versions.append(version)
        return version
    monkeypatch.setattr(
        "app.services.transcripts._create_transcript_version_from_text", capture_version,
    )
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
        assert [item["text"] for item in recorded[0]] == ["Synthetic clinician question"]
        assert [item["speaker_id"] for item in recorded[0]] == ["clinician"]
        assert [item["start_ms"] for item in recorded[0]] == [100]
        assert decrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
            record_id=versions[0].id, stored_value=versions[0].text_encrypted,
        ) == "Synthetic clinician question"
        assert recorded[0][0]["speaker_role"] == "clinician"
    else:
        assert [item["text"] for item in recorded[0]] == [
            "Synthetic clinician question",
            "Synthetic patient answer",
        ]
        assert [item["speaker_id"] for item in recorded[0]] == ["clinician", "patient"]
        assert [item["start_ms"] for item in recorded[0]] == [100, 200]
        assert decrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
            record_id=versions[0].id, stored_value=versions[0].text_encrypted,
        ) == "Synthetic clinician question\nSynthetic patient answer"
        assert [item["speaker_role"] for item in recorded[0]] == ["clinician", "patient"]

    for job in db_session.scalars(select(TranscriptIngestionJob)):
        assert job.status is TranscriptIngestionJobStatus.applied
        assert job.applied_at is not None
    # Duplicate worker reconciliation never adds another transcript version.
    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    assert len(versions) == 1


@pytest.mark.parametrize("timing_case", ["outside_source", "rewound_source", "small_tail_overrun"])
def test_mapped_word_segments_fall_back_unless_timing_is_safe_or_tail_clippable(
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
    duration_seconds = 10 if timing_case == "small_tail_overrun" else 2
    file_samples = duration_seconds * 16_000
    part = TelehealthCapturePart(
        id=uuid4(), session_id=session.id, transcript_id=transcript.id,
        owner_user_id=owner.id, team_id=team.id,
        channel="clinician", sequence_no=0, idempotency_key=uuid4(),
        status="transcribed", ingestion_job_id=job.id,
        timeline_start_frame=0, timeline_end_frame=duration_seconds * 48_000,
        timeline_sample_rate=48000, file_sample_rate=16000, file_samples=file_samples,
        source_audio_size_bytes=3244, source_audio_fingerprint="a" * 64,
        source_audio_duration_seconds=float(duration_seconds),
        timing_spans_json=[{
            "file_start_sample": 0, "file_end_sample": file_samples,
            "timeline_start_frame": 0, "timeline_end_frame": duration_seconds * 48_000,
        }],
    )
    db_session.add(part)
    db_session.commit()
    if timing_case == "outside_source":
        stored_turns = [
            {"text": "Hello,", "start_ms": 0, "end_ms": 200},
            {"text": "how are you?", "start_ms": 2100, "end_ms": 2200},
        ]
    elif timing_case == "small_tail_overrun":
        stored_turns = [
            {"text": "Hello,", "start_ms": 0, "end_ms": 200},
            {"text": "how are you?", "start_ms": 200, "end_ms": 11_600},
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

    if timing_case == "small_tail_overrun":
        assert [turn.text for turn in turns] == ["Hello,", "how are you?"]
        assert [(turn.start_ms, turn.end_ms) for turn in turns] == [(0, 200), (200, 10_000)]
    else:
        assert len(turns) == 1
        assert turns[0].text == "Hello, how are you?"
        assert turns[0].start_ms is None and turns[0].end_ms is None
