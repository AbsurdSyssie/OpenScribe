"""Two-source STT dispatch: atomic Vault handoff, quota call and idempotency."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services.telehealth_dispatch import queue_telehealth_transcription


def test_sealed_two_channel_capture_queues_independent_metered_jobs(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Dispatch Team")
    owner = make_user(
        email="telehealth-dispatch@example.com", password="password-1", team=team,
    )
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id,
        status=TranscriptStatus.ready,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.flush()
    config_id = uuid4()
    session = TelehealthCaptureSession(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=team.id,
        stt_config_id=config_id,
        timeline_sample_rate=48000,
        status="ready",
    )
    db_session.add(session)
    db_session.flush()
    expected_refs = []
    for channel in ("clinician", "patient"):
        part_id = uuid4()
        audio_ref = f"secret:openscribe/transcript-ingestion/{part_id}/source-audio"
        expected_refs.append(audio_ref)
        db_session.add(TelehealthCapturePart(
            id=part_id,
            session_id=session.id, transcript_id=transcript.id,
            owner_user_id=owner.id, team_id=team.id,
            channel=channel, sequence_no=0, idempotency_key=uuid4(),
            status="staged", timeline_start_frame=0,
            timeline_end_frame=4800, timeline_sample_rate=48000,
            file_sample_rate=16000, file_samples=1600,
            timing_spans_json=[{
                "file_start_sample": 0, "file_end_sample": 1600,
                "timeline_start_frame": 0, "timeline_end_frame": 4800,
            }],
            source_audio_size_bytes=3244,
            source_audio_fingerprint="a" * 64,
            source_audio_duration_seconds=0.1,
            source_audio_vault_ref=audio_ref,
            source_audio_expires_at=utcnow() + timedelta(hours=1),
        ))
    db_session.commit()

    config = SimpleNamespace(
        id=config_id, provider_preset="custom_rest_openapi",
        adapter_kind=SimpleNamespace(value="generic_rest"),
        base_url="https://stt.example.test", transcribe_path="/v1/transcribe",
        model_field_name="model", model_name="test-model",
        language_field_name="language", language="en",
        file_field_name="file", response_text_path="text",
        segments_path="segments", segment_text_field="text",
        segment_start_field="start", segment_end_field="end",
        extra_form_fields_json={},
        diarization_field_name="diarize",
        diarization_on_value="true",
        diarization_off_value="false",
    )
    monkeypatch.setattr(
        "app.services.telehealth_dispatch.resolve_selected_team_stt",
        lambda db, *, team_id: (None, config, "test-model", "en"),
    )
    monkeypatch.setattr(
        "app.services.telehealth_dispatch.ensure_stt_config_credential_ready",
        lambda **kwargs: None,
    )
    quota_reserved = []
    def stage_attempt(db, *, job, transcript, measured_duration_seconds):
        quota_reserved.append((job.id, measured_duration_seconds))
    monkeypatch.setattr(
        "app.services.telehealth_dispatch._queue_ingestion_attempt_and_dispatch",
        stage_attempt,
    )
    monkeypatch.setattr(
        "app.services.telehealth_dispatch.try_publish_task_dispatch_safely",
        lambda _: None,
    )

    session, jobs = queue_telehealth_transcription(
        db_session, owner,
        transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert session.status == "processing"
    assert len(jobs) == 2
    assert len(quota_reserved) == 2
    assert [duration for _id, duration in quota_reserved] == [0.1, 0.1]
    assert {job.source_audio_vault_ref for job in jobs} == set(expected_refs)
    assert all(job.stt_diarisation_enabled is False for job in jobs)
    assert all(job.job_kind.value == "audio_file" for job in jobs)
    assert all(job.status is TranscriptIngestionJobStatus.queued for job in jobs)
    assert transcript.status is TranscriptStatus.transcribing

    parts = list(db_session.scalars(select(TelehealthCapturePart)))
    assert {part.ingestion_job_id for part in parts} == {job.id for job in jobs}
    assert all(part.source_audio_vault_ref is None for part in parts)
    assert all(part.status == "processing" for part in parts)
    assert all(part.source_audio_expires_at is not None for part in parts)

    # Replaying an ambiguous "start transcription" response never double
    # reserves quota, moves audio refs or queues new jobs.
    another_session, duplicate_jobs = queue_telehealth_transcription(
        db_session, owner,
        transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert another_session.id == session.id
    assert {job.id for job in duplicate_jobs} == {job.id for job in jobs}
    assert len(quota_reserved) == 2
    assert len(list(db_session.scalars(select(TranscriptIngestionJob)))) == 2
