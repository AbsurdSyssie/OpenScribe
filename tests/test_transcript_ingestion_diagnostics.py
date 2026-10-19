from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptOutcome,
    AttemptStatus,
    ProviderSettlementBasis,
    ProviderAttempt,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
)
from app.schemas.transcripts import TranscriptStart
from app.services.transcripts import (
    process_transcript_ingestion_job,
    queue_audio_chunk_ingestion,
    start_transcript,
)


def _queued_chunk(db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name=f"Ingestion diagnostics {uuid4()}")
    owner = make_user(email=f"ingestion-diagnostics-{uuid4()}@example.com", team=team)
    admin = make_user(email=f"ingestion-diagnostics-admin-{uuid4()}@example.com", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    transcript = start_transcript(
        db_session,
        owner,
        TranscriptStart(title="Synthetic", ingestion_mode=TranscriptIngestionMode.live_chunked),
    )
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **_kwargs: 2.0)
    monkeypatch.setattr("app.services.transcripts.write_transcript_ingestion_source_audio", lambda **_kwargs: "secret:synthetic-audio")
    _, job = queue_audio_chunk_ingestion(
        db_session,
        owner,
        transcript_id=transcript.id,
        filename="synthetic.webm",
        source_audio_bytes=b"synthetic-audio",
        chunk_sequence_no=1,
        declared_duration_seconds=2,
    )
    monkeypatch.setattr("app.services.transcripts._read_queued_source_audio", lambda *_args, **_kwargs: b"synthetic-audio")
    monkeypatch.setattr(
        "app.services.transcripts.normalize_audio_to_wav_16k_mono",
        lambda **_kwargs: SimpleNamespace(data=b"synthetic-wav", filename="synthetic.wav", content_type="audio/wav"),
    )
    return job


def test_preclaim_unknown_credential_error_logs_only_allowlisted_metadata(
    db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch, caplog,
):
    job = _queued_chunk(db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch)
    monkeypatch.setattr(
        "app.services.transcripts.resolve_stt_snapshot_bearer_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AppError(502, "upstream_secret_patient_payload", "synthetic-private-error-message")
        ),
    )

    with caplog.at_level("WARNING", logger="openscribe.transcript_ingestion"):
        processed = process_transcript_ingestion_job(db_session, job_id=job.id)

    records = [record for record in caplog.records if record.name == "openscribe.transcript_ingestion"]
    assert processed is not None and processed.status is TranscriptIngestionJobStatus.failed
    assert records
    assert all(record.job_id == str(job.id) for record in records)
    assert all(record.stage == "credential_resolution" for record in records)
    assert {record.error_code for record in records} == {"ingestion_failed"}
    assert {record.action for record in records} == {"failure_observed", "terminalized"}
    assert all(isinstance(datetime.fromisoformat(record.occurred_at), datetime) for record in records)
    assert all(record.exc_info is None for record in records)
    assert "upstream_secret_patient_payload" not in caplog.text
    assert "synthetic-private-error-message" not in caplog.text
    assert all("transcript_ingestion_failure" in record.getMessage() for record in records)
    assert all(str(job.id) in record.getMessage() for record in records)
    assert all("credential_resolution" in record.getMessage() for record in records)
    attempt = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id))
    assert attempt is not None and attempt.status is AttemptStatus.cancelled


def test_transient_stt_failure_schedules_one_retry_and_reports_only_after_commit(
    db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch, caplog,
):
    job = _queued_chunk(db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch)
    monkeypatch.setattr("app.services.transcripts.resolve_stt_snapshot_bearer_token", lambda *_args, **_kwargs: "synthetic-token")
    calls = []

    def fail_stt(*_args, **_kwargs):
        calls.append("provider")
        raise AppError(502, "stt_request_failed", "synthetic-provider-failure", {"provider_status_code": 503})

    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fail_stt)
    with caplog.at_level("WARNING", logger="openscribe.transcript_ingestion"):
        processed = process_transcript_ingestion_job(db_session, job_id=job.id)

    records = [record for record in caplog.records if record.name == "openscribe.transcript_ingestion"]
    assert processed is not None and processed.status is TranscriptIngestionJobStatus.queued
    assert {record.action for record in records} == {"failure_observed", "retry_scheduled"}
    assert all(record.stage == "stt_dispatch" for record in records)
    assert all(record.error_code == "stt_request_failed" for record in records)
    assert all(record.exc_info is None for record in records)
    assert "synthetic-provider-failure" not in caplog.text
    assert calls == ["provider"]
    attempts = list(db_session.scalars(
        select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id).order_by(ProviderAttempt.attempt_number)
    ))
    assert len(attempts) == 2
    assert attempts[0].status is AttemptStatus.settled
    assert attempts[0].outcome is AttemptOutcome.unknown
    assert attempts[0].settlement_basis is ProviderSettlementBasis.measured
    assert attempts[0].settled_units == 2
    assert attempts[1].status is AttemptStatus.reserved
