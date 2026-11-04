"""Owner-bound staging for two-channel telehealth recording.

Only *stages* measured PCM16 WAV source parts; this module never authorizes
provider work or appends clinical text. A later post-stop finalization service
must dispatch independent STT jobs and atomically transfer Vault source-ref
ownership from a staged part to its job.

Do not publish these methods through a public route until that processing,
retry and warned-incomplete generation pathway is implemented.
"""
from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import asdict
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    User,
    utcnow,
)
from app.services.audio import LIVE_CHUNK_MAX_UPLOAD_BYTES, WHOLE_FILE_MAX_DURATION_SECONDS
from app.services.content_crypto import keyed_digest_for_owner
from app.services.stt import (
    ensure_stt_config_credential_ready,
    resolve_selected_team_stt,
)
from app.services.telehealth_audio import (
    TelehealthAudioPart,
    TelehealthSourceSpan,
    validate_next_telehealth_part,
    validate_telehealth_audio_part,
)
from app.services.transcripts import (
    INGESTION_SOURCE_AUDIO_MAX_AGE,
    WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS,
    WHOLE_FILE_HOURLY_UPLOAD_BYTES,
    assert_no_unresolved_stream_session,
    queue_orphan_transcript_audio_after_rollback,
)
from app.services.vault import write_transcript_ingestion_source_audio


def _owner_transcript_locked(db: Session, owner: User, *, transcript_id: UUID) -> Transcript:
    """Preserve OpenScribe's owner -> transcript row-lock hierarchy."""
    if owner.is_system_admin or owner.team_id is None:
        raise AppError(403, "forbidden", "Only team members can record consultations.")
    locked_owner = db.scalar(
        select(User).where(User.id == owner.id).with_for_update()
    )
    if locked_owner is None or locked_owner.team_id != owner.team_id:
        raise AppError(403, "forbidden", "Consultation owner is unavailable.")
    transcript = db.scalar(
        select(Transcript)
        .where(Transcript.id == transcript_id)
        .with_for_update()
    )
    if transcript is None:
        raise AppError(404, "not_found", "Consultation not found.")
    if transcript.owner_user_id != owner.id or transcript.team_id != owner.team_id:
        raise AppError(403, "forbidden", "Consultation access is restricted to its owner.")
    from app.services.transcripts import transcript_is_expired
    if transcript_is_expired(transcript):
        raise AppError(404, "not_found", "Consultation not found.")
    return transcript


def _require_recorder_ready(db: Session, transcript: Transcript) -> None:
    if transcript.ingestion_mode is not TranscriptIngestionMode.whole_file:
        raise AppError(409, "business_rule_violation", "Telehealth requires recorded capture mode.")
    assert_no_unresolved_stream_session(db, transcript_id=transcript.id)
    outstanding = db.scalar(select(TranscriptIngestionJob.id).where(
        TranscriptIngestionJob.transcript_id == transcript.id,
        TranscriptIngestionJob.status.in_(
            (TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing)
        ),
    ).limit(1))
    if outstanding is not None:
        raise AppError(409, "conflict", "Resolve the current transcription before recording.")


def begin_telehealth_capture(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    timeline_sample_rate: int,
) -> TelehealthCaptureSession:
    """Start a server-owned capture run, with a single immutable audio clock."""
    if type(timeline_sample_rate) is not int or not 8000 <= timeline_sample_rate <= 192000:
        raise AppError(422, "validation_error", "Invalid capture sample rate.")
    transcript = _owner_transcript_locked(db, owner, transcript_id=transcript_id)
    _require_recorder_ready(db, transcript)
    already_active = db.scalar(select(TelehealthCaptureSession.id).where(
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.status.in_(("active", "stopping")),
    ).limit(1))
    if already_active is not None:
        raise AppError(409, "conflict", "A telehealth recording is already active.")
    _, config, _, _ = resolve_selected_team_stt(db, team_id=transcript.team_id)
    ensure_stt_config_credential_ready(team_id=transcript.team_id, config=config)

    session = TelehealthCaptureSession(
        id=uuid4(),
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=transcript.team_id,
        stt_config_id=config.id,
        status="active",
        timeline_sample_rate=timeline_sample_rate,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _inspect_canonical_pcm16_wav(audio_bytes: bytes) -> tuple[int, int, float]:
    """Accept only exact 44-byte-header mono PCM16 WAVs (no hidden trailing data).

    Client worklets emit this format after resampling. The server measures its
    actual sample rate/count and never trusts the claimed uploaded duration.
    """
    if not audio_bytes or len(audio_bytes) > LIVE_CHUNK_MAX_UPLOAD_BYTES:
        raise AppError(413, "payload_too_large", "Telehealth audio part exceeds upload size limits.")
    if (
        len(audio_bytes) < 46
        or audio_bytes[:4] != b"RIFF"
        or audio_bytes[8:12] != b"WAVE"
        or int.from_bytes(audio_bytes[4:8], "little") != len(audio_bytes) - 8
    ):
        raise AppError(422, "validation_error", "Invalid telehealth WAV format.")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getcomptype() != "NONE"
                or wav_file.getframerate() <= 0
            ):
                raise AppError(422, "validation_error", "Telehealth audio must be mono PCM16.")
            sample_rate = wav_file.getframerate()
            sample_count = wav_file.getnframes()
            if (
                sample_count <= 0
                or len(audio_bytes) != 44 + sample_count * 2
                or len(wav_file.readframes(sample_count)) != sample_count * 2
            ):
                raise AppError(422, "validation_error", "Telehealth WAV sample count is invalid.")
    except (wave.Error, EOFError) as exc:
        raise AppError(422, "validation_error", "Invalid telehealth WAV format.") from exc
    return sample_rate, sample_count, sample_count / sample_rate


def _part_mapping(row: TelehealthCapturePart) -> TelehealthAudioPart:
    return TelehealthAudioPart(
        session_id=row.session_id,
        channel=row.channel,
        sequence_no=row.sequence_no,
        idempotency_key=row.idempotency_key,
        timeline_sample_rate=row.timeline_sample_rate,
        file_sample_rate=row.file_sample_rate,
        file_samples=row.file_samples,
        spans=tuple(TelehealthSourceSpan(**span) for span in row.timing_spans_json),
        timeline_start_frame=row.timeline_start_frame,
        timeline_end_frame=row.timeline_end_frame,
    )


def stage_telehealth_audio_part(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
    channel: str,
    sequence_no: int,
    idempotency_key: UUID,
    spans: tuple[TelehealthSourceSpan, ...] | list[TelehealthSourceSpan],
    audio_bytes: bytes,
) -> TelehealthCapturePart:
    """Stage one bounded, independently timed audio part under owner/root locks.

    The return is an accepted *staging* acknowledgement, NOT an STT result.
    The source stays recoverable until successful ingestion transfer or bounded
    expiry; we never claim the consultation is complete on this path.
    """
    # Validate actual audio bytes *before* writing to Vault or modifying rows.
    rate, samples, measured_duration = _inspect_canonical_pcm16_wav(audio_bytes)
    transcript = _owner_transcript_locked(db, owner, transcript_id=transcript_id)
    session = db.scalar(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.id == capture_session_id,
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
    ).with_for_update())
    if session is None:
        raise AppError(404, "not_found", "Telehealth capture not found.")
    if session.timeline_sample_rate not in range(8_000, 192_001):
        raise AppError(409, "conflict", "Capture audio clock is invalid.")

    try:
        mapped = validate_telehealth_audio_part(
            session_id=session.id,
            channel=channel,
            sequence_no=sequence_no,
            idempotency_key=idempotency_key,
            timeline_sample_rate=session.timeline_sample_rate,
            file_sample_rate=rate,
            file_samples=samples,
            spans=spans,
            measured_audio_seconds=measured_duration,
        )
    except ValueError as exc:
        raise AppError(422, "validation_error", "Invalid telehealth audio timeline.") from exc

    # A user-keyed digest prevents metadata-identical replacement under a
    # replayed idempotency key, without storing a raw audio hash.
    fingerprint = keyed_digest_for_owner(
        db,
        owner_user_id=owner.id,
        purpose="telehealth_source_audio",
        value=hashlib.sha256(audio_bytes).hexdigest(),
    )
    existing = db.scalar(select(TelehealthCapturePart).where(
        TelehealthCapturePart.session_id == session.id,
        TelehealthCapturePart.idempotency_key == idempotency_key,
    ).with_for_update())
    if existing is not None:
        if (
            existing.owner_user_id == owner.id
            and existing.source_audio_fingerprint == fingerprint
            and _part_mapping(existing) == mapped
            and existing.status != 'expired'
            and (
                existing.ingestion_job_id is not None
                or (
                    existing.source_audio_vault_ref is not None
                    and existing.source_audio_expires_at is not None
                    and existing.source_audio_expires_at > utcnow()
                )
            )
        ):
            return existing
        raise AppError(409, "idempotency_conflict", "Telehealth audio request conflicts with an earlier part.")

    # A lost acceptance response remains replayable after Stop/Interruption,
    # but genuinely NEW parts are accepted only while capture is active.
    if session.status not in ('active', 'stopping'):
        raise AppError(409, 'conflict', 'Telehealth capture is not accepting new audio.')

    previous = db.scalar(select(TelehealthCapturePart).where(
        TelehealthCapturePart.session_id == session.id,
        TelehealthCapturePart.channel == channel,
    ).order_by(TelehealthCapturePart.sequence_no.desc()).limit(1).with_for_update())
    if previous is not None:
        previous_metadata = _part_mapping(previous)
        expected_sequence = previous.sequence_no + 1
    else:
        previous_metadata = None
        expected_sequence = 0
    try:
        validate_next_telehealth_part(
            previous_metadata, mapped,
            expected_sequence_no=expected_sequence,
        )
    except ValueError as exc:
        raise AppError(409, "conflict", "Telehealth audio part is out of order.") from exc

    # Additional limits for storage *before* provider dispatch, which will
    # enforce actual combined STT quota later.
    staged_seconds = float(db.scalar(select(func.coalesce(
        func.sum(TelehealthCapturePart.source_audio_duration_seconds), 0
    )).where(TelehealthCapturePart.session_id == session.id)) or 0)
    if staged_seconds + measured_duration > WHOLE_FILE_MAX_DURATION_SECONDS * 2:
        raise AppError(413, "payload_too_large", "Telehealth source audio duration limit reached.")

    # Bound Vault staging independently of later provider/ASR quota charging.
    # The owner lock serializes concurrent requests across both sources and
    # capture sessions, so two channels cannot exceed these limits together.
    hourly_since = utcnow() - timedelta(hours=1)
    used_bytes, used_duration = db.execute(select(
        func.coalesce(func.sum(TelehealthCapturePart.source_audio_size_bytes), 0),
        func.coalesce(func.sum(TelehealthCapturePart.source_audio_duration_seconds), 0),
    ).where(
        TelehealthCapturePart.owner_user_id == owner.id,
        TelehealthCapturePart.created_at >= hourly_since,
    )).one()
    if WHOLE_FILE_HOURLY_UPLOAD_BYTES > 0 and used_bytes + len(audio_bytes) > WHOLE_FILE_HOURLY_UPLOAD_BYTES:
        raise AppError(429, "rate_limited", "Telehealth hourly staged-audio size limit exceeded.")
    if (
        WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS > 0
        and float(used_duration) + measured_duration > WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS
    ):
        raise AppError(429, "rate_limited", "Telehealth hourly staged-audio duration limit exceeded.")

    part_id = uuid4()
    vault_ref = write_transcript_ingestion_source_audio(job_id=part_id, audio_bytes=audio_bytes)
    part = TelehealthCapturePart(
        id=part_id,
        session_id=session.id,
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=transcript.team_id,
        channel=mapped.channel,
        sequence_no=mapped.sequence_no,
        idempotency_key=mapped.idempotency_key,
        status="staged",
        timeline_start_frame=mapped.timeline_start_frame,
        timeline_end_frame=mapped.timeline_end_frame,
        timeline_sample_rate=mapped.timeline_sample_rate,
        file_sample_rate=mapped.file_sample_rate,
        file_samples=mapped.file_samples,
        timing_spans_json=[asdict(item) for item in mapped.spans],
        source_audio_size_bytes=len(audio_bytes),
        source_audio_fingerprint=fingerprint,
        source_audio_duration_seconds=measured_duration,
        source_audio_vault_ref=vault_ref,
        source_audio_expires_at=min(
            utcnow() + INGESTION_SOURCE_AUDIO_MAX_AGE, transcript.retention_expires_at,
        ),
    )
    db.add(part)
    try:
        db.commit()
    except Exception:
        db.rollback()
        queue_orphan_transcript_audio_after_rollback(db, secret_ref=vault_ref)
        raise
    db.refresh(part)
    return part


# Source interruption reason codes are fixed transport/availability metadata:
# never accept arbitrary error/exception text as persisted diagnostic content.
_TELEHEALTH_FAILURE_CODES = frozenset((
    "source_ended", "permission_revoked", "audio_clock_interrupted",
    "audio_backpressure", "audio_processor_failed", "upload_failed",
    "browser_capture_failed",
))


def _owner_capture_session_locked(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
) -> tuple[Transcript, TelehealthCaptureSession]:
    transcript = _owner_transcript_locked(db, owner, transcript_id=transcript_id)
    session = db.scalar(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.id == capture_session_id,
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
        TelehealthCaptureSession.team_id == transcript.team_id,
    ).with_for_update())
    if session is None:
        raise AppError(404, "not_found", "Telehealth capture not found.")
    return transcript, session


def request_telehealth_capture_stop(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
    interrupted_channel: str | None = None,
    failure_code: str | None = None,
) -> TelehealthCaptureSession:
    """Stop physical capture while allowing in-flight bounded audio uploads.

    After a source interruption, record only a safe reason code and channel.
    A 'stopping' run can continue accepting new *audio parts* until all buffered
    PCM has been safely staged. It is not sealed merely because a track ended.
    """
    if interrupted_channel is not None and failure_code is None:
        raise AppError(422, "validation_error", "Capture interruption metadata is incomplete.")
    if interrupted_channel is not None and interrupted_channel not in ("clinician", "patient"):
        raise AppError(422, "validation_error", "Capture interruption channel is invalid.")
    if failure_code is not None and failure_code not in _TELEHEALTH_FAILURE_CODES:
        raise AppError(422, "validation_error", "Capture interruption reason is invalid.")
    _, session = _owner_capture_session_locked(
        db, owner, transcript_id=transcript_id, capture_session_id=capture_session_id,
    )
    if session.status not in ("active", "stopping"):
        # A repeated exact stop acknowledgement after sealing is safe.
        if session.status in ("ready", "interrupted"):
            if session.failure_channel == interrupted_channel and session.failure_code == failure_code:
                return session
        raise AppError(409, "conflict", "Telehealth capture cannot stop in this state.")
    if session.status == "stopping":
        if session.failure_code:
            # A later clean stop cannot erase an existing interruption.
            if failure_code is None:
                return session
            if session.failure_channel != interrupted_channel or session.failure_code != failure_code:
                raise AppError(409, "idempotency_conflict", "Capture interruption details conflict.")
        elif failure_code:
            # A final audio upload/flush error can be discovered after a
            # nominal Stop was requested. Escalate the stopping run to
            # interrupted; do not mark it falsely complete.
            session.failure_channel = interrupted_channel
            session.failure_code = failure_code
            db.add(session)
            db.commit()
    else:
        session.status = "stopping"
        session.recording_stopped_at = utcnow()
        session.failure_channel = interrupted_channel
        session.failure_code = failure_code
        db.add(session)
        db.commit()
    db.refresh(session)
    return session


def seal_telehealth_capture(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
    expected_clinician_parts: int,
    expected_patient_parts: int,
) -> TelehealthCaptureSession:
    """Seal after all locally buffered source parts were durably accepted.

    The client may retry this operation after an ambiguous HTTP response; no
    new data can be staged after sealing except exact idempotency replays.
    PART COUNTS alone do not prove every utterance was captured; separate
    source-drop gaps and VAD confidence evidence must remain visible.
    """
    expected = {
        "clinician": expected_clinician_parts,
        "patient": expected_patient_parts,
    }
    if any(type(count) is not int or count < 0 or count > 100_000 for count in expected.values()):
        raise AppError(422, "validation_error", "Capture source-part counts are invalid.")
    _, session = _owner_capture_session_locked(
        db, owner, transcript_id=transcript_id, capture_session_id=capture_session_id,
    )
    if session.status not in ("stopping", "ready", "interrupted"):
        raise AppError(409, "conflict", "Stop audio capture before sealing its parts.")
    counts = dict(db.execute(select(
        TelehealthCapturePart.channel, func.count(TelehealthCapturePart.id),
    ).where(
        TelehealthCapturePart.session_id == session.id,
        TelehealthCapturePart.owner_user_id == owner.id,
    ).group_by(TelehealthCapturePart.channel)).all())
    if any(int(counts.get(channel, 0)) != count for channel, count in expected.items()):
        raise AppError(409, "conflict", "Some recording parts have not been safely uploaded.")
    if session.status == "stopping":
        session.status = "interrupted" if session.failure_code else "ready"
        session.updated_at = utcnow()
        db.add(session)
        db.commit()
    db.refresh(session)
    return session
