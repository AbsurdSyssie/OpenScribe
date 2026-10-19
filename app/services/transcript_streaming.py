"""Durable metadata, quota and transcript lifecycle for Deepgram live streams.

Audio and provider messages are accepted only as short-lived call arguments. This
module never stores successful PCM, interim text, or raw provider errors.
"""

from __future__ import annotations

import base64
import hmac
import io
import os
import wave
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Mapping
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptStatus,
    QuotaResource,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptStreamDelivery,
    TranscriptStreamDeliveryStatus,
    TranscriptStreamSession,
    TranscriptStreamStatus,
    TranscriptStreamWindow,
    TranscriptStreamWindowStatus,
    User,
    ProviderAttempt,
    utcnow,
)
from app.services.content_crypto import encrypt_text_for_owner, keyed_digest_for_owner
from app.services.quotas import (
    increase_stream_provider_attempt_reservation,
    mark_provider_attempt_submitted,
    reserve_provider_attempt,
    settle_provider_attempt_stream_audio,
    settle_provider_attempt_stream_unknown,
)


PCM_SAMPLE_RATE = 16_000
MAX_STREAM_BATCH_SAMPLES = 16_000
MAX_STREAM_WINDOW_SAMPLES = 30 * PCM_SAMPLE_RATE
STREAM_LEASE_SECONDS = 15
STREAM_LEASE_GRACE_SECONDS = 30
MAX_RECOVERY_AUDIO_BYTES = MAX_STREAM_WINDOW_SAMPLES * 2 + 44
from app.services.stt import STT_TRANSCRIPTION_TIMEOUT_SECONDS

STREAM_ATTEMPT_DEADLINE_SECONDS = int(STT_TRANSCRIPTION_TIMEOUT_SECONDS) + 300
LIVE_STT_HOURLY_DURATION_LIMIT_SECONDS = float(os.getenv("LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", "3600"))
FAILED_AUDIO_MAX_AGE = timedelta(hours=24)
_SELECTION_KEYS = frozenset({
    "config_id", "provider_preset", "adapter_kind", "base_url", "model_name", "language",
    "mip_opt_out", "extra_query_params",
})
_SAFE_STREAM_FAILURE_CODES = frozenset({
    "stream_transport_failed", "stream_provider_failed", "stream_finalization_timeout",
    "stream_quota_exceeded", "stream_session_expired", "stream_protocol_invalid", "stream_failed",
})


@dataclass(frozen=True, slots=True)
class StreamSessionStarted:
    session_id: UUID
    connection_fence: int
    fence_token: UUID
    lease_expires_at: datetime
    window_no: int
    next_source_sample_offset: int


@dataclass(frozen=True, slots=True)
class StreamHeartbeat:
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class StreamBatchAuthorization:
    delivery_id: UUID | None
    provider_attempt_id: UUID | None
    dispatchable: bool
    window_no: int
    provider_sample_start: int
    provider_sample_end: int
    next_source_sample_offset: int
    authorized_sample_count: int
    window_forwarded_sample_count: int


@dataclass(frozen=True, slots=True)
class StreamWindowCommitted:
    window_no: int
    next_source_sample_offset: int
    forwarded_sample_count: int
    already_committed: bool = False


@dataclass(frozen=True, slots=True)
class StreamFinished:
    status: TranscriptStreamStatus
    next_source_sample_offset: int


@dataclass(frozen=True, slots=True)
class StreamFailed:
    status: TranscriptStreamStatus
    recovery_available: bool
    next_source_sample_offset: int


def _now(value: datetime | None) -> datetime:
    value = value or utcnow()
    if value.tzinfo is None:
        raise ValueError("stream timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _recovery_wav_matches_window(audio_bytes: bytes, *, sample_count: int) -> bool:
    """Accept only the exact normalized PCM WAV corresponding to a failed interval."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            return (
                wav_file.getframerate() == PCM_SAMPLE_RATE
                and wav_file.getnchannels() == 1
                and wav_file.getsampwidth() == 2
                and wav_file.getnframes() == sample_count
                and len(audio_bytes) <= MAX_RECOVERY_AUDIO_BYTES
            )
    except (wave.Error, EOFError):
        return False


def _recovery_wav_matches_deliveries(
    db: Session,
    *,
    owner_user_id: UUID,
    session_id: UUID,
    window: TranscriptStreamWindow,
    audio_bytes: bytes,
) -> bool:
    """Require recovery audio to match the exact persisted forwarded batches."""
    if not _recovery_wav_matches_window(audio_bytes, sample_count=window.forwarded_sample_count):
        return False
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            pcm = wav_file.readframes(wav_file.getnframes())
    except (wave.Error, EOFError):
        return False
    deliveries = db.scalars(
        select(TranscriptStreamDelivery).where(
            TranscriptStreamDelivery.session_id == session_id,
            TranscriptStreamDelivery.window_no == window.window_no,
        ).order_by(TranscriptStreamDelivery.source_sample_offset)
    ).all()
    if not deliveries or sum(item.sample_count for item in deliveries) != window.forwarded_sample_count:
        return False
    cursor = 0
    for delivery in deliveries:
        byte_count = delivery.sample_count * 2
        batch = pcm[cursor:cursor + byte_count]
        if len(batch) != byte_count:
            return False
        digest = keyed_digest_for_owner(
            db,
            owner_user_id=owner_user_id,
            purpose="transcript_stream_pcm_batch",
            value=base64.b64encode(batch).decode("ascii"),
        )
        if not hmac.compare_digest(digest, delivery.pcm_digest):
            return False
        cursor += byte_count
    return cursor == len(pcm)


def _selection_snapshot(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) - _SELECTION_KEYS:
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    snapshot = dict(value)
    if (
        snapshot.get("provider_preset") != "deepgram"
        or snapshot.get("adapter_kind") != "generic_rest"
        or snapshot.get("mip_opt_out") is not True
        or not isinstance(snapshot.get("base_url"), str)
        or not isinstance(snapshot.get("model_name"), str)
    ):
        raise AppError(409, "stream_selection_unsupported", "The selected transcription provider does not support live streaming")
    if "language" in snapshot and snapshot["language"] is not None and not isinstance(snapshot["language"], str):
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    try:
        config_id = UUID(str(snapshot.get("config_id")))
    except (TypeError, ValueError) as exc:
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid") from exc
    if len(str(snapshot["base_url"])) > 2048 or len(str(snapshot["model_name"])) > 255:
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    extra = snapshot.get("extra_query_params", {})
    if not isinstance(extra, Mapping) or len(extra) > 24:
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    from app.services.deepgram_streaming import DeepgramStreamError, _stream_options
    try:
        _stream_options(extra)
    except DeepgramStreamError as exc:
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid") from exc
    if any(not isinstance(key, str) or len(key) > 64 for key in extra):
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    if any(not isinstance(item, (str, bool, int)) or len(str(item)) > 256 for item in extra.values()):
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    snapshot["extra_query_params"] = dict(sorted(extra.items()))
    snapshot["config_id"] = str(config_id)
    if snapshot.get("language") is not None and len(str(snapshot["language"])) > 32:
        raise AppError(422, "stream_selection_invalid", "Live transcription selection is invalid")
    return snapshot


def _lock_owner_transcript(
    db: Session, *, owner_user_id: UUID, transcript_id: UUID, now: datetime
) -> tuple[User, Transcript]:
    owner = db.scalar(select(User).where(User.id == owner_user_id).execution_options(populate_existing=True).with_for_update())
    if owner is None or owner.is_system_admin or owner.team_id is None:
        raise AppError(404, "not_found", "Transcript not found")
    transcript = db.scalar(
        select(Transcript).where(
            Transcript.id == transcript_id,
            Transcript.owner_user_id == owner.id,
        ).execution_options(populate_existing=True).with_for_update()
    )
    if transcript is None or transcript.team_id != owner.team_id or transcript.retention_expires_at <= now:
        raise AppError(404, "not_found", "Transcript not found")
    return owner, transcript


def _lock_session(
    db: Session,
    *,
    session_id: UUID,
    transcript_id: UUID,
    owner_user_id: UUID,
    connection_fence: int | None = None,
    fence_token: UUID | None = None,
) -> TranscriptStreamSession:
    session = db.scalar(
        select(TranscriptStreamSession).where(
            TranscriptStreamSession.id == session_id,
            TranscriptStreamSession.transcript_id == transcript_id,
            TranscriptStreamSession.owner_user_id == owner_user_id,
        ).with_for_update()
    )
    if session is None:
        raise AppError(404, "not_found", "Stream session not found")
    if connection_fence is not None and (
        session.connection_fence != connection_fence or session.fence_token != fence_token
    ):
        raise AppError(409, "stream_session_fenced", "Stream connection is no longer current")
    return session


def _live_session_blocker(db: Session, *, transcript_id: UUID) -> TranscriptStreamSession | None:
    return db.scalar(
        select(TranscriptStreamSession).where(
            TranscriptStreamSession.transcript_id == transcript_id,
            TranscriptStreamSession.status.in_(
                [TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing,
                 TranscriptStreamStatus.failed, TranscriptStreamStatus.recovering]
            ),
        ).order_by(TranscriptStreamSession.created_at.desc()).limit(1)
    )


def start_transcript_stream(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    selection_snapshot: Mapping[str, object],
    lease_seconds: int = STREAM_LEASE_SECONDS,
    now: datetime | None = None,
) -> StreamSessionStarted:
    now = _now(now)
    if not 1 <= lease_seconds <= 60:
        raise ValueError("stream lease must be between 1 and 60 seconds")
    snapshot = _selection_snapshot(selection_snapshot)
    locked_owner, transcript = _lock_owner_transcript(db, owner_user_id=owner.id, transcript_id=transcript_id, now=now)
    if transcript.ingestion_mode is not TranscriptIngestionMode.live_chunked:
        raise AppError(409, "stream_transcript_mode_unsupported", "Transcript is not configured for live capture")
    if transcript.status not in {TranscriptStatus.recording, TranscriptStatus.ready, TranscriptStatus.transcribing}:
        raise AppError(409, "stream_transcript_unavailable", "Transcript is not available for live capture")
    pending_job = db.scalar(
        select(TranscriptIngestionJob.id).where(
            TranscriptIngestionJob.transcript_id == transcript.id,
            TranscriptIngestionJob.status.in_([TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing]),
        ).limit(1)
    )
    if pending_job is not None or _live_session_blocker(db, transcript_id=transcript.id) is not None:
        raise AppError(409, "stream_transcript_busy", "Transcript has unfinished transcription work")
    session = TranscriptStreamSession(
        id=uuid4(), transcript_id=transcript.id, owner_user_id=locked_owner.id, team_id=transcript.team_id,
        selection_snapshot_json=snapshot, status=TranscriptStreamStatus.active,
        connection_fence=1, fence_token=uuid4(), lease_expires_at=now + timedelta(seconds=lease_seconds),
        started_at=now, created_at=now, updated_at=now,
    )
    transcript.status = TranscriptStatus.transcribing
    db.add(session)
    db.add(transcript)
    db.commit()
    return StreamSessionStarted(session.id, session.connection_fence, session.fence_token,
                                session.lease_expires_at, session.window_no, session.next_source_sample_offset)


def heartbeat_transcript_stream(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    connection_fence: int,
    fence_token: UUID,
    lease_seconds: int = STREAM_LEASE_SECONDS,
    now: datetime | None = None,
) -> StreamHeartbeat:
    now = _now(now)
    if not 1 <= lease_seconds <= 60:
        raise ValueError("stream lease must be between 1 and 60 seconds")
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id,
                            connection_fence=connection_fence, fence_token=fence_token)
    if session.status not in {TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing} or session.lease_expires_at <= now:
        raise AppError(409, "stream_session_expired", "Stream session is no longer active")
    session.lease_expires_at = now + timedelta(seconds=lease_seconds)
    session.updated_at = now
    db.commit()
    return StreamHeartbeat(session.lease_expires_at)


def _hourly_stream_samples(db: Session, *, owner_user_id: UUID, since: datetime) -> int:
    stream_samples = db.scalar(
        select(func.coalesce(func.sum(TranscriptStreamDelivery.sample_count), 0))
        .select_from(TranscriptStreamDelivery)
        .join(TranscriptStreamSession, TranscriptStreamSession.id == TranscriptStreamDelivery.session_id)
        .where(TranscriptStreamSession.owner_user_id == owner_user_id,
               TranscriptStreamDelivery.created_at >= since)
    ) or 0
    live_chunk_seconds = db.scalar(
        select(func.coalesce(func.sum(TranscriptIngestionJob.declared_duration_seconds), 0.0))
        .where(TranscriptIngestionJob.owner_user_id == owner_user_id,
               TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk,
               TranscriptIngestionJob.transcript_stream_window_id.is_(None),
               TranscriptIngestionJob.created_at >= since)
    ) or 0
    return int(stream_samples) + ceil(float(live_chunk_seconds) * PCM_SAMPLE_RATE)


def _stream_attempt_bucket_changed(attempt: ProviderAttempt, now: datetime) -> bool:
    authorized = _now(attempt.authorized_at)
    return authorized.date() != now.date() or (authorized.year, authorized.month) != (now.year, now.month)


def _session_provider_attempts(db: Session, *, session_id: UUID, lock: bool = True) -> list[ProviderAttempt]:
    query = select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == session_id,
        ProviderAttempt.attempt_kind == AttemptKind.stt_deepgram_live_stream,
    ).order_by(ProviderAttempt.attempt_number)
    if lock:
        query = query.with_for_update()
    return list(db.scalars(query))


def _settle_session_attempts(db: Session, *, session_id: UUID, outcome: str, now: datetime) -> None:
    for attempt in _session_provider_attempts(db, session_id=session_id):
        if attempt.status is not AttemptStatus.submitted or not attempt.stream_authorized_samples:
            continue
        if outcome == "succeeded":
            settle_provider_attempt_stream_audio(
                db, attempt_id=attempt.id, authorized_samples=attempt.stream_authorized_samples, now=now,
            )
        else:
            settle_provider_attempt_stream_unknown(db, attempt_id=attempt.id, now=now)


def enforce_live_stt_hourly_audio_limit(
    db: Session, *, owner_user_id: UUID, requested_sample_count: int,
    limit_seconds: float | None = None, now: datetime | None = None
) -> None:
    """Shared capture-hourly cap; caller holds the owner row lock."""
    effective_limit = LIVE_STT_HOURLY_DURATION_LIMIT_SECONDS if limit_seconds is None else max(0.0, float(limit_seconds))
    if requested_sample_count <= 0 or effective_limit <= 0:
        return
    now = _now(now)
    used_samples = _hourly_stream_samples(db, owner_user_id=owner_user_id, since=now - timedelta(hours=1))
    max_samples = int(effective_limit * PCM_SAMPLE_RATE)
    if used_samples + requested_sample_count > max_samples:
        raise AppError(
            429, "rate_limited", "Live transcription hourly audio limit exceeded",
            {"window": "1 hour", "max_seconds": effective_limit,
             "used_seconds": round(used_samples / PCM_SAMPLE_RATE, 2),
             "requested_seconds": round(requested_sample_count / PCM_SAMPLE_RATE, 3)},
        )


def authorize_stream_batch(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    connection_fence: int,
    fence_token: UUID,
    first_sample_offset: int,
    sample_count: int,
    pcm_bytes: bytes,
    now: datetime | None = None,
) -> StreamBatchAuthorization:
    """Commit the quota and non-replayable dispatch claim before returning."""
    now = _now(now)
    if first_sample_offset < 0 or not 1 <= sample_count <= MAX_STREAM_BATCH_SAMPLES or len(pcm_bytes) != sample_count * 2:
        raise AppError(422, "stream_audio_frame_invalid", "Audio frame is invalid")
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id,
                            connection_fence=connection_fence, fence_token=fence_token)
    if session.status is not TranscriptStreamStatus.active or session.lease_expires_at <= now:
        raise AppError(409, "stream_session_expired", "Stream session is no longer active")
    pcm_b64 = base64.b64encode(pcm_bytes).decode("ascii")
    pcm_digest = keyed_digest_for_owner(db, owner_user_id=owner_user_id,
                                        purpose="transcript_stream_pcm_batch", value=pcm_b64)
    existing = db.scalar(select(TranscriptStreamDelivery).where(
        TranscriptStreamDelivery.session_id == session.id,
        TranscriptStreamDelivery.source_sample_offset == first_sample_offset,
    ))
    if existing is not None:
        if existing.sample_count != sample_count or existing.pcm_digest != pcm_digest:
            raise AppError(409, "stream_audio_interval_conflict", "Audio interval conflicts with an existing stream claim")
        attempt_id = existing.provider_attempt_id
        result = StreamBatchAuthorization(
            existing.id, attempt_id, False, existing.window_no,
            existing.provider_sample_start, existing.provider_sample_end,
            session.next_source_sample_offset, session.authorized_sample_count,
            session.window_forwarded_sample_count,
        )
        db.commit()
        return result
    end_offset = first_sample_offset + sample_count
    if first_sample_offset < session.next_source_sample_offset:
        raise AppError(409, "stream_audio_interval_overlap", "Audio interval overlaps an existing stream claim")
    if session.window_forwarded_sample_count + sample_count > MAX_STREAM_WINDOW_SAMPLES:
        raise AppError(409, "stream_window_checkpoint_required", "Finalize the current audio window before sending more audio")
    enforce_live_stt_hourly_audio_limit(db, owner_user_id=owner_user_id, requested_sample_count=sample_count, now=now)

    previous_authorized = session.authorized_sample_count
    next_authorized = previous_authorized + sample_count
    next_received = session.received_sample_count + sample_count
    attempt = None
    if session.active_provider_attempt_id is not None:
        attempt = db.scalar(select(ProviderAttempt).where(
            ProviderAttempt.id == session.active_provider_attempt_id,
        ).with_for_update())
        if attempt is None:
            raise AppError(409, "stream_attempt_missing", "Stream accounting state conflicts")
    if attempt is not None and attempt.status is not AttemptStatus.submitted:
        raise AppError(409, "stream_attempt_unavailable", "Stream quota attempt is no longer active")
    if attempt is not None and _stream_attempt_bucket_changed(attempt, now):
        # Each reservation remains in the day/month in which its first sample
        # was authorized. Start a fresh attempt before forwarding across a UTC
        # quota boundary so a long stream cannot carry old headroom forward.
        if session.window_forwarded_sample_count:
            raise AppError(
                409, "stream_quota_checkpoint_required",
                "Finalize the current audio window before continuing in a new quota period",
            )
        attempt = None
    if attempt is None:
        attempt_number = (db.scalar(select(func.max(ProviderAttempt.attempt_number)).where(
            ProviderAttempt.correlation_id == session.id,
            ProviderAttempt.attempt_kind == AttemptKind.stt_deepgram_live_stream,
        )) or 0) + 1
        reserve_until = now + timedelta(seconds=60)
        attempt = reserve_provider_attempt(
            db, team_id=transcript.team_id, owner_user_id=owner_user_id,
            resource=QuotaResource.audio_seconds, attempt_kind=AttemptKind.stt_deepgram_live_stream,
            correlation_id=session.id, attempt_number=attempt_number,
            reserved_units=ceil(sample_count / PCM_SAMPLE_RATE),
            reservation_valid_until=reserve_until, authorized_at=now,
            transcript_id=transcript.id, transcript_stream_session_id=session.id,
            provider_adapter="deepgram_websocket", provider_model=str(session.selection_snapshot_json["model_name"]),
            stream_received_samples=sample_count, stream_authorized_samples=sample_count,
        )
        if attempt.status is AttemptStatus.reserved:
            mark_provider_attempt_submitted(
                db, attempt_id=attempt.id,
                deadline_at=now + timedelta(seconds=STREAM_ATTEMPT_DEADLINE_SECONDS), now=now,
            )
    else:
        attempt_received = int(attempt.stream_received_samples or 0) + sample_count
        attempt_authorized = int(attempt.stream_authorized_samples or 0) + sample_count
        increase_stream_provider_attempt_reservation(
            db, attempt_id=attempt.id, received_samples=attempt_received,
            authorized_samples=attempt_authorized, now=now,
        )
    if attempt is None:
        raise AppError(409, "stream_attempt_missing", "Stream accounting state conflicts")

    current_window = db.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.window_no == session.window_no,
    ).with_for_update())
    if current_window is None:
        current_window = TranscriptStreamWindow(
            id=uuid4(), session_id=session.id, window_no=session.window_no,
            source_start_sample_offset=first_sample_offset,
            source_end_sample_offset=end_offset,
            forwarded_sample_count=sample_count,
            status=TranscriptStreamWindowStatus.active,
            created_at=now,
        )
        db.add(current_window)
    else:
        if current_window.status is not TranscriptStreamWindowStatus.active:
            raise AppError(409, "stream_window_not_active", "Stream window is not active")
        current_window.source_end_sample_offset = end_offset
        current_window.forwarded_sample_count += sample_count

    delivery = TranscriptStreamDelivery(
        id=uuid4(), session_id=session.id, window_no=session.window_no,
        source_sample_offset=first_sample_offset, sample_count=sample_count,
        pcm_digest=pcm_digest,
        provider_sample_start=previous_authorized, provider_sample_end=next_authorized,
        provider_attempt_id=attempt.id, status=TranscriptStreamDeliveryStatus.authorized,
        created_at=now,
    )
    db.add(delivery)
    session.next_source_sample_offset = end_offset
    session.received_sample_count = next_received
    session.authorized_sample_count = next_authorized
    session.window_forwarded_sample_count += sample_count
    session.active_provider_attempt_id = attempt.id
    session.lease_expires_at = max(session.lease_expires_at, now + timedelta(seconds=STREAM_LEASE_GRACE_SECONDS))
    session.updated_at = now
    db.add(session)
    db.commit()
    return StreamBatchAuthorization(
        delivery.id, attempt.id, True, session.window_no,
        previous_authorized, next_authorized,
        session.next_source_sample_offset, session.authorized_sample_count,
        session.window_forwarded_sample_count,
    )


def record_stream_batch_sent(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    connection_fence: int,
    fence_token: UUID,
    delivery_id: UUID,
    now: datetime | None = None,
) -> None:
    now = _now(now)
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id,
                            connection_fence=connection_fence, fence_token=fence_token)
    if session.status not in {TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing}:
        raise AppError(409, "stream_session_not_active", "Stream session is no longer active")
    delivery = db.scalar(select(TranscriptStreamDelivery).where(
        TranscriptStreamDelivery.id == delivery_id,
        TranscriptStreamDelivery.session_id == session.id,
    ).with_for_update())
    if delivery is None:
        raise AppError(404, "not_found", "Stream delivery claim not found")
    if delivery.status is TranscriptStreamDeliveryStatus.sent:
        db.commit()
        return
    if delivery.status is not TranscriptStreamDeliveryStatus.authorized:
        raise AppError(409, "stream_delivery_not_dispatchable", "Stream delivery claim is no longer dispatchable")
    delivery.status = TranscriptStreamDeliveryStatus.sent
    delivery.sent_at = now
    session.lease_expires_at = max(session.lease_expires_at, now + timedelta(seconds=STREAM_LEASE_GRACE_SECONDS))
    session.updated_at = now
    db.commit()


def commit_stream_window(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    connection_fence: int,
    fence_token: UUID,
    window_no: int,
    source_end_sample_offset: int,
    forwarded_sample_count: int,
    finalized_text: str,
    final_segment_count: int,
    now: datetime | None = None,
) -> StreamWindowCommitted:
    now = _now(now)
    if not isinstance(finalized_text, str) or len(finalized_text) > 200_000 or final_segment_count < 0:
        raise AppError(422, "stream_final_text_invalid", "Final transcription is invalid")
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id,
                            connection_fence=connection_fence, fence_token=fence_token)
    if session.status not in {TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing}:
        raise AppError(409, "stream_session_not_active", "Stream session is no longer active")
    window = db.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.window_no == window_no,
    ).with_for_update())
    if window is None:
        if forwarded_sample_count == 0 and source_end_sample_offset == session.next_source_sample_offset and not finalized_text.strip() and final_segment_count == 0:
            return StreamWindowCommitted(window_no, source_end_sample_offset, 0, True)
        raise AppError(409, "stream_window_missing", "Stream checkpoint is unavailable")
    if window.status is TranscriptStreamWindowStatus.committed:
        digest = keyed_digest_for_owner(db, owner_user_id=owner_user_id,
                                        purpose="transcript_stream_final_text", value=finalized_text)
        if window.text_digest == digest and window.source_end_sample_offset == source_end_sample_offset and window.forwarded_sample_count == forwarded_sample_count:
            db.commit()
            return StreamWindowCommitted(window_no, source_end_sample_offset, forwarded_sample_count, True)
        raise AppError(409, "stream_window_conflict", "Stream checkpoint conflicts with committed content")
    if (
        window.status is not TranscriptStreamWindowStatus.active
        or window_no != session.window_no
        or forwarded_sample_count != session.window_forwarded_sample_count
        or forwarded_sample_count != window.forwarded_sample_count
        or forwarded_sample_count > MAX_STREAM_WINDOW_SAMPLES
        or source_end_sample_offset != session.next_source_sample_offset
        or source_end_sample_offset != window.source_end_sample_offset
    ):
        raise AppError(409, "stream_window_checkpoint_mismatch", "Stream checkpoint does not match authorized audio")
    text_digest = keyed_digest_for_owner(db, owner_user_id=owner_user_id,
                                         purpose="transcript_stream_final_text", value=finalized_text)
    # A drained provider window is the durable accounting boundary. Settle its
    # submitted samples before admitting more audio so quota windows follow the
    # existing per-clip ceiling semantics.
    _settle_session_attempts(db, session_id=session.id, outcome="succeeded", now=now)
    from app.services.transcripts import _append_chunk_text, set_transcript_draft_text, transcript_draft_text
    if finalized_text.strip():
        updated_text = _append_chunk_text(transcript_draft_text(db, transcript=transcript), finalized_text)
        set_transcript_draft_text(db, transcript=transcript, plaintext=updated_text)
    window.status = TranscriptStreamWindowStatus.committed
    window.final_segment_count = final_segment_count
    window.text_digest = text_digest
    window.committed_at = now
    session.window_no += 1
    session.window_forwarded_sample_count = 0
    session.active_provider_attempt_id = None
    session.updated_at = now
    db.add(window)
    db.add(session)
    db.add(transcript)
    db.commit()
    return StreamWindowCommitted(window_no, source_end_sample_offset, forwarded_sample_count, False)


def _resolve_transcript_after_stream_change(db: Session, *, transcript: Transcript, now: datetime) -> None:
    # Some request sessions disable autoflush; resolve against transitioned state.
    db.flush()
    pending_ingestion = db.scalar(select(TranscriptIngestionJob.id).where(
        TranscriptIngestionJob.transcript_id == transcript.id,
        TranscriptIngestionJob.status.in_([TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing]),
    ).limit(1))
    blocker = _live_session_blocker(db, transcript_id=transcript.id)
    if blocker is not None:
        transcript.status = TranscriptStatus.failed if blocker.status is TranscriptStreamStatus.failed else TranscriptStatus.transcribing
    elif pending_ingestion is not None:
        transcript.status = TranscriptStatus.transcribing
    else:
        transcript.status = TranscriptStatus.ready
    db.add(transcript)


def finish_transcript_stream(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    connection_fence: int,
    fence_token: UUID,
    now: datetime | None = None,
) -> StreamFinished:
    now = _now(now)
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id,
                            connection_fence=connection_fence, fence_token=fence_token)
    if session.status is TranscriptStreamStatus.completed:
        db.commit()
        return StreamFinished(session.status, session.next_source_sample_offset)
    if session.status not in {TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing}:
        raise AppError(409, "stream_session_not_finalizable", "Stream session cannot be finalized")
    if session.window_forwarded_sample_count:
        raise AppError(409, "stream_window_checkpoint_required", "Finalize and commit the current audio window first")
    unfinished = db.scalar(select(TranscriptStreamWindow.id).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.status.in_([TranscriptStreamWindowStatus.active, TranscriptStreamWindowStatus.failed,
                                           TranscriptStreamWindowStatus.recovering]),
    ).limit(1))
    if unfinished is not None:
        raise AppError(409, "stream_window_checkpoint_required", "Resolve the current audio window first")
    _settle_session_attempts(db, session_id=session.id, outcome="succeeded", now=now)
    session.status = TranscriptStreamStatus.completed
    session.completed_at = now
    session.lease_expires_at = now
    session.updated_at = now
    _resolve_transcript_after_stream_change(db, transcript=transcript, now=now)
    db.add(session)
    db.commit()
    if transcript.status is TranscriptStatus.ready:
        from app.services.transcripts import _preview_redact_current_draft_if_ready
        _preview_redact_current_draft_if_ready(db, transcript=transcript)
    return StreamFinished(session.status, session.next_source_sample_offset)


def fail_transcript_stream(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    connection_fence: int,
    fence_token: UUID,
    error_code: str,
    recovery_audio_bytes: bytes | None = None,
    recovery_filename: str = "stream-recovery.wav",
    now: datetime | None = None,
) -> StreamFailed:
    now = _now(now)
    safe_code = error_code if error_code in _SAFE_STREAM_FAILURE_CODES else "stream_failed"
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id,
                            connection_fence=connection_fence, fence_token=fence_token)
    if session.status in {TranscriptStreamStatus.completed, TranscriptStreamStatus.discarded}:
        db.commit()
        return StreamFailed(session.status, False, session.next_source_sample_offset)
    current_window = db.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.window_no == session.window_no,
    ).with_for_update())
    if session.status in {TranscriptStreamStatus.failed, TranscriptStreamStatus.recovering}:
        # Failure is terminal for this connection attempt. A delayed relay
        # callback must not erase the unresolved-gap barrier or replace its
        # original retention deadline.
        recovery_available = bool(
            current_window is not None
            and current_window.recovery_audio_vault_ref
            and current_window.recovery_audio_expires_at is not None
            and current_window.recovery_audio_expires_at > now
        )
        db.commit()
        return StreamFailed(session.status, recovery_available, session.next_source_sample_offset)
    recovery_available = False
    newly_written_recovery_ref = None
    unresolved_window = (
        current_window is not None
        and current_window.status in {
            TranscriptStreamWindowStatus.active,
            TranscriptStreamWindowStatus.failed,
            TranscriptStreamWindowStatus.recovering,
        }
        and current_window.forwarded_sample_count > 0
    )
    if not unresolved_window:
        # Previously committed windows have durable text; a closing transport
        # error after their commit must not create a fictitious recovery gap.
        session.status = TranscriptStreamStatus.completed if session.authorized_sample_count else TranscriptStreamStatus.discarded
        session.completed_at = now
        if current_window is not None and current_window.forwarded_sample_count == 0:
            current_window.status = TranscriptStreamWindowStatus.discarded
        _settle_session_attempts(db, session_id=session.id, outcome="unknown", now=now)
    else:
        session.status = TranscriptStreamStatus.failed
        session.failure_code = safe_code
        if current_window is not None and current_window.status is TranscriptStreamWindowStatus.active:
            current_window.status = TranscriptStreamWindowStatus.failed
            if (recovery_audio_bytes
                    and _recovery_wav_matches_deliveries(
                        db,
                        owner_user_id=owner_user_id,
                        session_id=session.id,
                        window=current_window,
                        audio_bytes=recovery_audio_bytes,
                    )):
                expires_at = min(now + FAILED_AUDIO_MAX_AGE, transcript.retention_expires_at)
                if expires_at > now:
                    from app.services.vault import write_transcript_ingestion_source_audio
                    try:
                        ref = write_transcript_ingestion_source_audio(job_id=current_window.id, audio_bytes=recovery_audio_bytes)
                    except AppError:
                        ref = None
                    if ref:
                        newly_written_recovery_ref = ref
                        current_window.recovery_audio_vault_ref = ref
                        current_window.recovery_audio_size_bytes = len(recovery_audio_bytes)
                        current_window.recovery_audio_stored_at = now
                        current_window.recovery_audio_expires_at = expires_at
                        recovery_available = True
        _settle_session_attempts(db, session_id=session.id, outcome="unknown", now=now)
    session.failure_code = safe_code if session.status is TranscriptStreamStatus.failed else None
    session.lease_expires_at = now
    session.updated_at = now
    if current_window is not None:
        db.add(current_window)
    _resolve_transcript_after_stream_change(db, transcript=transcript, now=now)
    db.add(session)
    try:
        db.commit()
    except Exception:
        db.rollback()
        if newly_written_recovery_ref:
            # A Vault write precedes the relational commit. On a definite
            # rollback, durably enqueue orphan cleanup; if commit outcome was
            # ambiguous, first check whether the window still owns the ref.
            try:
                ref_is_live = db.scalar(select(TranscriptStreamWindow.id).where(
                    TranscriptStreamWindow.recovery_audio_vault_ref == newly_written_recovery_ref,
                ).limit(1)) is not None
            except Exception:
                ref_is_live = True
            if not ref_is_live:
                from app.services.transcripts import queue_orphan_transcript_audio_after_rollback
                queue_orphan_transcript_audio_after_rollback(db, secret_ref=newly_written_recovery_ref)
        raise
    return StreamFailed(session.status, recovery_available, session.next_source_sample_offset)


def discard_transcript_stream_gap(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    window_no: int | None = None,
    now: datetime | None = None,
) -> StreamFinished:
    now = _now(now)
    _owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner_user_id)
    if session.status not in {TranscriptStreamStatus.failed, TranscriptStreamStatus.recovering}:
        raise AppError(409, "stream_gap_not_discardable", "There is no unresolved stream gap to discard")
    windows = db.scalars(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.status.in_([TranscriptStreamWindowStatus.failed, TranscriptStreamWindowStatus.recovering]),
        *([TranscriptStreamWindow.window_no == window_no] if window_no is not None else []),
    ).order_by(TranscriptStreamWindow.window_no).with_for_update()).all()
    if not windows:
        raise AppError(404, "stream_gap_not_found", "Unresolved stream interval not found")
    window_ids = [window.id for window in windows]
    if db.scalar(select(TranscriptIngestionJob.id).where(
        TranscriptIngestionJob.transcript_stream_window_id.in_(window_ids),
        TranscriptIngestionJob.status.in_([TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing]),
    ).limit(1)) is not None:
        raise AppError(409, "stream_recovery_in_progress", "Stream recovery is still in progress")
    from app.services.transcripts import queue_transcript_audio_cleanup
    refs = [window.recovery_audio_vault_ref for window in windows if window.recovery_audio_vault_ref]
    if refs:
        queue_transcript_audio_cleanup(db, secret_refs=refs)
    for window in windows:
        window.recovery_audio_vault_ref = None
        window.recovery_audio_size_bytes = None
        window.recovery_audio_expires_at = None
        window.status = TranscriptStreamWindowStatus.discarded
        db.add(window)
    db.flush()
    remaining = db.scalar(select(TranscriptStreamWindow.id).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.status.in_([TranscriptStreamWindowStatus.failed, TranscriptStreamWindowStatus.recovering]),
    ).limit(1))
    if remaining is None:
        session.status = TranscriptStreamStatus.discarded
        session.completed_at = now
    session.lease_expires_at = now
    session.updated_at = now
    _resolve_transcript_after_stream_change(db, transcript=transcript, now=now)
    db.add(session)
    db.commit()
    return StreamFinished(session.status, session.next_source_sample_offset)


def queue_transcript_stream_recovery(
    db: Session,
    owner_user_id: UUID,
    *,
    transcript_id: UUID,
    session_id: UUID,
    window_no: int,
    audio_bytes: bytes | None = None,
    filename: str = "stream-recovery.wav",
    now: datetime | None = None,
) -> UUID:
    """Queue explicit owner-requested retry through the ordinary ingestion outbox."""
    now = _now(now)
    owner, transcript = _lock_owner_transcript(db, owner_user_id=owner_user_id, transcript_id=transcript_id, now=now)
    session = _lock_session(db, session_id=session_id, transcript_id=transcript.id, owner_user_id=owner.id)
    window = db.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == session.id,
        TranscriptStreamWindow.window_no == window_no,
    ).with_for_update())
    if window is None or window.status not in {TranscriptStreamWindowStatus.failed, TranscriptStreamWindowStatus.recovering}:
        raise AppError(409, "stream_recovery_unavailable", "Stream recovery is no longer available")
    existing_job = db.scalar(select(TranscriptIngestionJob).where(
        TranscriptIngestionJob.transcript_stream_window_id == window.id,
    ).with_for_update())
    if existing_job is not None:
        if existing_job.status in {TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing,
                                   TranscriptIngestionJobStatus.completed, TranscriptIngestionJobStatus.applied}:
            existing_job_id = existing_job.id
            db.commit()
            return existing_job_id
        raise AppError(409, "stream_recovery_already_attempted", "This stream interval has already been retried")
    if session.status not in {TranscriptStreamStatus.failed, TranscriptStreamStatus.recovering}:
        raise AppError(409, "stream_recovery_unavailable", "Stream recovery is no longer available")
    recovery_deadline = min(
        window.recovery_audio_expires_at or (window.created_at + FAILED_AUDIO_MAX_AGE),
        transcript.retention_expires_at,
    )
    if recovery_deadline <= now:
        raise AppError(410, "stream_recovery_expired", "Stored recovery audio has expired")
    if audio_bytes is None:
        if not window.recovery_audio_vault_ref:
            raise AppError(409, "stream_recovery_audio_missing", "Recovery audio is unavailable")
        from app.services.vault import read_transcript_ingestion_source_audio
        audio_bytes = read_transcript_ingestion_source_audio(secret_ref=window.recovery_audio_vault_ref)
    if not isinstance(audio_bytes, bytes) or not audio_bytes or len(audio_bytes) > MAX_RECOVERY_AUDIO_BYTES:
        raise AppError(422, "stream_recovery_audio_invalid", "Recovery audio is invalid")
    if not _recovery_wav_matches_deliveries(
        db,
        owner_user_id=owner_user_id,
        session_id=session.id,
        window=window,
        audio_bytes=audio_bytes,
    ):
        raise AppError(422, "stream_recovery_audio_mismatch", "Recovery audio does not match the failed interval")
    if (not isinstance(filename, str) or len(filename) > 128 or "/" in filename or "\\" in filename
            or not filename.lower().endswith((".wav", ".webm", ".ogg", ".mp3", ".m4a", ".mp4"))):
        filename = "stream-recovery.wav"
    from app.services.transcripts import queue_audio_chunk_ingestion
    _transcript, job = queue_audio_chunk_ingestion(
        db, owner, transcript_id=transcript.id, filename=filename,
        source_audio_bytes=audio_bytes,
        chunk_sequence_no=transcript.next_live_chunk_sequence_no_applied,
        declared_duration_seconds=None,
        request_idempotency_key=f"stream-{session.id.hex}-{window_no}",
        _stream_recovery_window_id=window.id,
    )
    return job.id


def reconcile_expired_stream_sessions(
    db: Session, *, batch_size: int = 100, now: datetime | None = None,
) -> int:
    """Turn abandoned leases into explicit barriers without inventing audio recovery."""
    now = _now(now)
    if batch_size <= 0:
        return 0
    candidate_ids = list(db.scalars(
        select(TranscriptStreamSession.id).where(
            TranscriptStreamSession.status.in_((TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing)),
            TranscriptStreamSession.lease_expires_at <= now,
            TranscriptStreamSession.transcript_id.in_(
                select(Transcript.id).where(Transcript.retention_expires_at > now)
            ),
        ).order_by(TranscriptStreamSession.lease_expires_at, TranscriptStreamSession.id).limit(batch_size)
    ))
    changed = 0
    for session_id in candidate_ids:
        identity = db.get(TranscriptStreamSession, session_id)
        if identity is None:
            continue
        locked_owner, transcript = _lock_owner_transcript(
            db, owner_user_id=identity.owner_user_id, transcript_id=identity.transcript_id, now=now,
        )
        session = db.scalar(select(TranscriptStreamSession).where(
            TranscriptStreamSession.id == session_id,
            TranscriptStreamSession.owner_user_id == locked_owner.id,
            TranscriptStreamSession.transcript_id == transcript.id,
        ).with_for_update())
        if (session is None or session.status not in {TranscriptStreamStatus.active, TranscriptStreamStatus.finalizing}
                or session.lease_expires_at > now):
            continue
        window = db.scalar(select(TranscriptStreamWindow).where(
            TranscriptStreamWindow.session_id == session.id,
            TranscriptStreamWindow.window_no == session.window_no,
            TranscriptStreamWindow.status == TranscriptStreamWindowStatus.active,
        ).with_for_update())
        unresolved_audio_window = (
            window is not None
            and window.forwarded_sample_count > 0
            and window.status is TranscriptStreamWindowStatus.active
        )
        if unresolved_audio_window:
            session.status = TranscriptStreamStatus.failed
            session.failure_code = "stream_session_expired"
            window.status = TranscriptStreamWindowStatus.failed
            db.add(window)
            _settle_session_attempts(db, session_id=session.id, outcome="unknown", now=now)
        elif session.authorized_sample_count > 0:
            # All sent audio already has a durable finalized-text checkpoint.
            # An expired browser lease after that boundary must not invent a
            # failed interval with no recoverable window.
            session.status = TranscriptStreamStatus.completed
            session.failure_code = None
            session.completed_at = now
            if window is not None:
                window.status = TranscriptStreamWindowStatus.discarded
                db.add(window)
            _settle_session_attempts(db, session_id=session.id, outcome="succeeded", now=now)
        else:
            session.status = TranscriptStreamStatus.discarded
            session.failure_code = None
            session.completed_at = now
            if window is not None:
                window.status = TranscriptStreamWindowStatus.discarded
                db.add(window)
        session.lease_expires_at = now
        session.updated_at = now
        _resolve_transcript_after_stream_change(db, transcript=transcript, now=now)
        db.add(session)
        changed += 1
    db.flush()
    return changed


def expire_stream_recovery_audio(
    db: Session, *, batch_size: int = 100, now: datetime | None = None,
) -> int:
    """Expire failed stream recovery refs at the earlier of root or 24-hour deadline."""
    now = _now(now)
    if batch_size <= 0:
        return 0
    candidate_ids = list(db.scalars(
        select(TranscriptStreamWindow.id)
        .join(TranscriptStreamSession, TranscriptStreamSession.id == TranscriptStreamWindow.session_id)
        .join(Transcript, Transcript.id == TranscriptStreamSession.transcript_id)
        .where(TranscriptStreamWindow.recovery_audio_vault_ref.is_not(None),
               TranscriptStreamWindow.recovery_audio_expires_at <= now,
               Transcript.retention_expires_at > now)
        .order_by(TranscriptStreamWindow.recovery_audio_expires_at, TranscriptStreamWindow.id)
        .limit(batch_size)
    ))
    expired = 0
    from app.services.transcripts import queue_transcript_audio_cleanup
    for window_id in candidate_ids:
        identity = db.get(TranscriptStreamWindow, window_id)
        if identity is None:
            continue
        session_identity = db.get(TranscriptStreamSession, identity.session_id)
        if session_identity is None:
            continue
        owner, transcript = _lock_owner_transcript(
            db, owner_user_id=session_identity.owner_user_id,
            transcript_id=session_identity.transcript_id, now=now,
        )
        session = _lock_session(
            db, session_id=session_identity.id, transcript_id=transcript.id, owner_user_id=owner.id,
        )
        window = db.scalar(select(TranscriptStreamWindow).where(
            TranscriptStreamWindow.id == window_id,
            TranscriptStreamWindow.session_id == session.id,
        ).with_for_update())
        if (window is None or not window.recovery_audio_vault_ref
                or window.recovery_audio_expires_at is None or window.recovery_audio_expires_at > now):
            continue
        job_exists = db.scalar(select(TranscriptIngestionJob.id).where(
            TranscriptIngestionJob.transcript_stream_window_id == window.id,
            TranscriptIngestionJob.status.in_((TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing)),
        ).limit(1))
        if job_exists is not None:
            continue
        queue_transcript_audio_cleanup(db, secret_refs=[window.recovery_audio_vault_ref])
        window.recovery_audio_vault_ref = None
        window.recovery_audio_size_bytes = None
        window.recovery_audio_expires_at = None
        db.add(window)
        expired += 1
    db.flush()
    return expired
