"""Post-stop queueing of owner-staged Telehealth source parts.

This module transfers Vault *ownership* atomically from each staged source
part to an independently metered TranscriptIngestionJob. Every session belongs
to one consultation; two channels retain independent original-clock ranges.
It does not apply any result text. The ingestion worker must defer telehealth
result application until both sources have terminal, transcribed results.

Call only after the capture has been stopped, all buffered parts have been
staged, and seal_telehealth_capture has committed an exact-count seal.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptStatus,
    User,
    utcnow,
)
from app.services.stt import ensure_stt_config_credential_ready, resolve_selected_team_stt
from app.services.task_outbox import try_publish_task_dispatch_safely
from app.services.telehealth_capture import _owner_capture_session_locked
from app.services.transcripts import _queue_ingestion_attempt_and_dispatch


def queue_telehealth_transcription(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
) -> tuple[TelehealthCaptureSession, tuple[TranscriptIngestionJob, ...]]:
    """Atomically queue all sealed parts, including both channels, once.

    If quota reservation fails partway through, the *entire transaction*
    rolls back; staged Vault references remain in their original part rows,
    recoverable through the configured source-audio expiry deadline.
    """
    transcript, session = _owner_capture_session_locked(
        db,
        owner,
        transcript_id=transcript_id,
        capture_session_id=capture_session_id,
    )
    if session.status in ("processing", "completed"):
        jobs = tuple(db.scalars(select(TranscriptIngestionJob).join(
            TelehealthCapturePart,
            TelehealthCapturePart.ingestion_job_id == TranscriptIngestionJob.id,
        ).where(
            TelehealthCapturePart.session_id == session.id,
            TelehealthCapturePart.owner_user_id == owner.id,
        ).order_by(TelehealthCapturePart.channel, TelehealthCapturePart.sequence_no)))
        if jobs:
            return session, jobs
        raise AppError(409, "conflict", "Telehealth capture has no submitted audio.")
    incomplete = bool(session.failure_code)
    if session.status not in ("ready", "interrupted"):
        raise AppError(409, "conflict", "Seal the capture before transcription.")
    if incomplete and (
        session.status != "interrupted" or session.incomplete_acknowledged_at is None
    ):
        raise AppError(
            409, "conflict",
            "Acknowledge the incomplete telehealth recording before transcription.",
        )
    parts = list(db.scalars(
        select(TelehealthCapturePart)
        .where(
            TelehealthCapturePart.session_id == session.id,
            TelehealthCapturePart.owner_user_id == owner.id,
            TelehealthCapturePart.transcript_id == transcript.id,
        )
        .order_by(TelehealthCapturePart.channel.asc(), TelehealthCapturePart.sequence_no.asc())
        .with_for_update()
    ))
    if not parts or (
        not incomplete and {part.channel for part in parts} != {"clinician", "patient"}
    ):
        raise AppError(
            409, "conflict",
            "Both sources must have recoverable audio, unless an interruption was acknowledged.",
        )
    if any(
        part.status != "staged"
        or not part.source_audio_vault_ref
        or not part.source_audio_expires_at
        or part.ingestion_job_id is not None
        for part in parts
    ):
        raise AppError(409, "conflict", "Resolve missing or expired source audio first.")
    now = utcnow()
    if any(part.source_audio_expires_at <= now for part in parts):
        raise AppError(409, "ingestion_source_expired", "A source audio part has expired.")
    if transcript.status is TranscriptStatus.failed:
        # Existing failed work needs explicit recovery before accepting
        # additional provider charges in the same consultation.
        raise AppError(409, "conflict", "Resolve the failed transcription first.")

    _, config, model_name, language = resolve_selected_team_stt(
        db, team_id=transcript.team_id,
    )
    if config.id != session.stt_config_id:
        raise AppError(
            409, "conflict",
            "The speech service changed since recording. Select the original service or reconfigure safely.",
        )
    ensure_stt_config_credential_ready(team_id=transcript.team_id, config=config)

    jobs: list[TranscriptIngestionJob] = []
    for part in parts:
        job = TranscriptIngestionJob(
            id=uuid4(),
            transcript_id=transcript.id,
            owner_user_id=owner.id,
            team_id=transcript.team_id,
            job_kind=TranscriptIngestionJobKind.audio_file,
            chunk_sequence_no=None,  # Unique sequence belongs to TelehealthCapturePart.
            source_filename=f"telehealth-{part.channel}-{part.sequence_no}.wav",
            request_idempotency_key=f"telehealth:{part.id}",  # <=64 chars; transcript-unique
            source_audio_blob=None,
            source_audio_vault_ref=part.source_audio_vault_ref,
            source_audio_size_bytes=part.source_audio_size_bytes,
            source_audio_duration_seconds=part.source_audio_duration_seconds,
            source_audio_expires_at=part.source_audio_expires_at,
            stt_config_id=config.id,
            stt_provider_preset=config.provider_preset,
            stt_adapter_kind=config.adapter_kind.value,
            stt_base_url=config.base_url,
            stt_transcribe_path=config.transcribe_path,
            stt_model_name=model_name,
            stt_model_field_name=config.model_field_name or "model",
            stt_language=language,
            stt_language_field_name=config.language_field_name or "language",
            stt_file_field_name=config.file_field_name,
            stt_response_text_path=config.response_text_path,
            stt_segments_path=config.segments_path,
            stt_segment_text_field=config.segment_text_field,
            stt_segment_start_field=config.segment_start_field,
            stt_segment_end_field=config.segment_end_field,
            # Telehealth source roles supersede provider diarisation in v1.
            stt_segment_speaker_field=None,
            stt_extra_form_fields_json=dict(config.extra_form_fields_json or {}),
            stt_diarisation_enabled=False,
            stt_diarization_batch_enabled=False,
            stt_diarization_field_name=getattr(config, "diarization_field_name", None),
            stt_diarization_on_value=getattr(config, "diarization_on_value", "true"),
            stt_diarization_off_value=getattr(config, "diarization_off_value", "false"),
            status=TranscriptIngestionJobStatus.queued,
        )
        part.ingestion_job_id = job.id
        part.status = "processing"
        part.source_audio_vault_ref = None  # Shared ref now belongs to job.
        part.updated_at = now
        db.add(job)
        db.add(part)
        db.flush()
        _queue_ingestion_attempt_and_dispatch(
            db,
            job=job,
            transcript=transcript,
            measured_duration_seconds=part.source_audio_duration_seconds,
        )
        jobs.append(job)

    session.status = "processing"
    session.updated_at = now
    transcript.status = TranscriptStatus.transcribing
    db.add(session)
    db.add(transcript)
    # All jobs, provider quota reservations, outbox dispatch intents and
    # source-ref ownership changes commit as ONE transaction.
    db.commit()
    for job in jobs:
        if job.celery_task_id:
            try_publish_task_dispatch_safely(job.celery_task_id)
    return session, tuple(jobs)
