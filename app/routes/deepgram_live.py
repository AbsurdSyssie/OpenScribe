"""Owner-only live audio relay. Audio and provider text never enter diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import io
import wave
from time import monotonic
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db import get_db
from app.errors import AppError
from app.models import SttSelectionPurpose, TranscriptStreamSession, TranscriptStreamWindow
from app.services.auth import SESSION_COOKIE_NAME
from app.services.csrf import verify_csrf_token
from app.services.stt import resolve_selected_team_stt, resolve_stt_snapshot_bearer_token
from app.services.transcripts import get_active_owner_transcript
from app.web.transcribe_workspace import open_realtime_workspace_db_session, resolve_realtime_workspace_user


router = APIRouter()
logger = logging.getLogger("openscribe.deepgram_live")
SAMPLE_RATE = 16000
WINDOW_SAMPLES = 30 * SAMPLE_RATE
MAX_CONTROL_BYTES = 2048
FRAME_HEADER = struct.Struct("<BQI")


def parse_audio_frame(data: bytes) -> tuple[int, int, bytes]:
    if not FRAME_HEADER.size < len(data) <= FRAME_HEADER.size + 2 * SAMPLE_RATE:
        raise AppError(422, "stream_frame_invalid", "Invalid audio frame")
    version, offset, count = FRAME_HEADER.unpack_from(data)
    if version != 1 or not 0 < count <= SAMPLE_RATE or len(data) != FRAME_HEADER.size + count * 2:
        raise AppError(422, "stream_frame_invalid", "Invalid audio frame")
    # JavaScript cannot represent arbitrary uint64 integers exactly.
    if offset + count > 2**53 - 1:
        raise AppError(422, "stream_frame_invalid", "Invalid audio frame")
    return offset, count, data[FRAME_HEADER.size:]


def _control(message: dict) -> dict:
    text = message.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CONTROL_BYTES:
        raise AppError(422, "stream_message_invalid", "Invalid stream message")
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        raise AppError(422, "stream_message_invalid", "Invalid stream message") from None
    if not isinstance(value, dict):
        raise AppError(422, "stream_message_invalid", "Invalid stream message")
    return value


def _same_origin(socket: WebSocket) -> bool:
    scheme = "https" if socket.url.scheme == "wss" else "http"
    host = socket.headers.get("host")
    if os.getenv("TRUST_FORWARDED_ORIGIN_HEADERS", "false").lower() in {"true", "1", "yes"}:
        scheme = socket.headers.get("x-forwarded-proto", scheme).split(",", 1)[0].strip()
        host = socket.headers.get("x-forwarded-host") or host
    return bool(host and scheme in {"http", "https"} and socket.headers.get("origin") == f"{scheme}://{host}")


def _with_owner(socket: WebSocket, transcript_id: UUID, operation):
    from app.main import _enforce_localhost_only_dev_account

    with open_realtime_workspace_db_session(socket) as db:
        owner = resolve_realtime_workspace_user(db, raw_session_token=socket.cookies.get(SESSION_COOKIE_NAME))
        if owner is None:
            raise AppError(401, "unauthorized", "Authentication required")
        _enforce_localhost_only_dev_account(socket, owner)
        root = get_active_owner_transcript(db, owner, transcript_id=transcript_id)
        if owner.is_system_admin or owner.team_id is None or root.team_id != owner.team_id:
            raise AppError(403, "forbidden", "Transcript access denied")
        return operation(db, owner, root)


async def _owner_call(socket: WebSocket, transcript_id: UUID, operation):
    return await asyncio.to_thread(_with_owner, socket, transcript_id, operation)


async def _send(socket: WebSocket, transcript_id: UUID, payload: dict) -> None:
    # Revocation and retention are rechecked before delivering any clinical text.
    await _owner_call(socket, transcript_id, lambda db, owner, root: None)
    await asyncio.wait_for(socket.send_json(payload), timeout=5)


@dataclass(frozen=True)
class _Preflight:
    config: object
    snapshot: dict
    api_key: str = field(repr=False)


def _preflight(db, owner, root) -> _Preflight:
    from app.models import SttAdapterKind
    from app.services.deepgram_streaming import DeepgramStreamConfig, validate_stream_config
    from app.services.stt_presets import is_deepgram_stt_base_url

    _, config, model, language = resolve_selected_team_stt(db, team_id=owner.team_id, purpose=SttSelectionPurpose.conversation)
    if config.adapter_kind is not SttAdapterKind.generic_rest or not is_deepgram_stt_base_url(config.base_url):
        raise AppError(422, "stream_provider_ineligible", "The selected provider does not support this stream")
    frozen = DeepgramStreamConfig(
        base_url=config.base_url, model_name=model, language=language,
        extra_query_params=dict(config.extra_form_fields_json or {}),
    )
    validate_stream_config(frozen)
    snapshot = {
        "config_id": str(config.id), "provider_preset": config.provider_preset,
        "adapter_kind": config.adapter_kind.value, "base_url": config.base_url,
        "model_name": model, "language": language,
        "extra_query_params": dict(config.extra_form_fields_json or {}), "mip_opt_out": True,
    }
    api_key = resolve_stt_snapshot_bearer_token(
        db, team_id=owner.team_id, stt_config_id=config.id, adapter_kind=config.adapter_kind.value,
        base_url=config.base_url, transcribe_path=config.transcribe_path,
        file_field_name=config.file_field_name, response_text_path=config.response_text_path,
        provider_preset=config.provider_preset,
    )
    if not api_key:
        raise AppError(409, "stt_config_secret_missing", "The selected STT credential is unavailable")
    return _Preflight(frozen, snapshot, api_key)


class _Relay:
    def __init__(self, socket: WebSocket, transcript_id: UUID, started):
        self.socket = socket
        self.transcript_id = transcript_id
        self.started = started
        self.provider = None
        self.provider_context = None
        self.provider_task = None
        self.browser_task = None
        self.window_no = started.window_no
        self.media_samples = 0
        self.committed_samples = 0
        self.source_end = started.next_source_sample_offset
        self.audio = bytearray()
        self.finals: dict[tuple[int, int], str] = {}
        self.last_media_at = monotonic()
        self.last_heartbeat_at = monotonic()
        self.completed = False
        self._content_key_cache = {}
        self._content_key_cache_started = monotonic()

    async def service(self, name: str, **kwargs):
        from app.services import transcript_streaming as lifecycle
        from app.services.content_crypto import DEK_CACHE_SESSION_KEY

        if monotonic() - self._content_key_cache_started >= 30:
            self._content_key_cache.clear()
            self._content_key_cache_started = monotonic()

        def operation(db, owner, root):
            # Reuse the established owning-user crypto cache across short DB
            # transactions for at most one window/30 wall-clock seconds. The
            # owner/session/root checks run before this callback, every time.
            db.info[DEK_CACHE_SESSION_KEY] = self._content_key_cache
            return getattr(lifecycle, name)(
                db, owner.id, transcript_id=self.transcript_id, session_id=self.started.session_id,
                connection_fence=self.started.connection_fence, fence_token=self.started.fence_token, **kwargs,
            )

        return await _owner_call(self.socket, self.transcript_id, operation)

    async def send(self, payload: dict):
        await _send(self.socket, self.transcript_id, payload)

    def _receive_provider(self):
        self.provider_task = asyncio.create_task(self.provider.receive(timeout_seconds=60))

    async def provider_event(self):
        from app.services.deepgram_streaming import DeepgramMetadataEvent, DeepgramStreamError

        task, self.provider_task = self.provider_task, None
        try:
            event = await task
        except DeepgramStreamError as exc:
            if exc.category == "timeout":
                self._receive_provider()
                return None
            raise
        if isinstance(event, DeepgramMetadataEvent):
            return event
        self._receive_provider()
        first = self.committed_samples + round(event.start * SAMPLE_RATE)
        last = self.committed_samples + round((event.start + event.duration) * SAMPLE_RATE)
        if first < 0 or last < first or last > self.media_samples + 1:
            raise AppError(502, "stream_result_invalid", "Invalid transcription interval")
        if last <= self.committed_samples:
            return event
        if first < self.committed_samples:
            raise AppError(502, "stream_result_invalid", "Overlapping transcription interval")
        if event.is_final:
            identity = (first, last)
            if identity in self.finals and self.finals[identity] != event.text:
                raise AppError(502, "stream_result_invalid", "Conflicting finalized transcription")
            if len(self.finals) >= 2048 and identity not in self.finals:
                raise AppError(502, "stream_result_invalid", "Too many transcription segments")
            for (previous_first, previous_last), _ in self.finals.items():
                if identity != (previous_first, previous_last) and first < previous_last and last > previous_first:
                    raise AppError(502, "stream_result_invalid", "Overlapping finalized transcription")
            self.finals[identity] = event.text
        provisional = " ".join(text for _, text in sorted(self.finals.items()) if text)
        if not event.is_final and event.text:
            provisional = f"{provisional} {event.text}".strip()
        if len(provisional) > 64000:
            raise AppError(502, "stream_result_invalid", "Transcription checkpoint exceeds its limit")
        await self.send({"type": "interim", "text": provisional})
        return event

    async def checkpoint(self, *, closing: bool = False):
        from app.services.deepgram_streaming import DeepgramMetadataEvent

        if self.provider is None:
            return
        await self.provider.send_close_stream()
        deadline = monotonic() + 8
        # CloseStream guarantees a final drain followed by summary Metadata.
        # Finalize has no unconditional acknowledgement, and silent samples
        # need not produce a textual Results interval.
        while True:
            if self.provider_task is None:
                raise AppError(502, "stream_finalization_failed", "Transcription did not finalize")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AppError(502, "stream_finalization_failed", "Transcription did not finalize")
            event = await asyncio.wait_for(self.provider_event(), timeout=remaining)
            if isinstance(event, DeepgramMetadataEvent):
                break
        await self.provider_context.__aexit__(None, None, None)
        self.provider = None
        self.provider_context = None
        if not self.audio:
            return
        text = " ".join(value.strip() for _, value in sorted(self.finals.items()) if value.strip())
        committed = await self.service(
            "commit_stream_window", window_no=self.window_no,
            source_end_sample_offset=self.source_end,
            forwarded_sample_count=self.media_samples - self.committed_samples,
            finalized_text=text, final_segment_count=len(self.finals),
        )
        self._content_key_cache.clear()
        self._content_key_cache_started = monotonic()
        await self.send({
            "type": "window_committed", "window_no": committed.window_no,
            "next_sample_offset": committed.next_source_sample_offset,
        })
        self.committed_samples = self.media_samples
        self.window_no += 1
        self.audio.clear()
        self.finals.clear()

    async def audio_frame(self, data: bytes, preflight: _Preflight):
        from app.services.deepgram_streaming import DeepgramLiveStream

        first, count, pcm = parse_audio_frame(data)
        if len(self.audio) + len(pcm) > WINDOW_SAMPLES * 2:
            raise AppError(422, "stream_frame_invalid", "Audio crossed its checkpoint boundary")
        if self.provider is None:
            # Do not open a billable/timeout-prone provider connection during
            # initial local listening. No artificial silence is sent.
            self.provider_context = DeepgramLiveStream(preflight.config, api_key=preflight.api_key)
            self.provider = await self.provider_context.__aenter__()
            self._receive_provider()
        try:
            authorized = await self.service(
                "authorize_stream_batch", first_sample_offset=first, sample_count=count, pcm_bytes=pcm,
            )
        except AppError as exc:
            if exc.code != "stream_quota_checkpoint_required":
                raise
            # Complete and settle the prior quota day's actual work before
            # authorizing any media in the new day. The pending-work policy
            # itself remains unchanged; uncertain audio is never replayed.
            await self.checkpoint()
            self.provider_context = DeepgramLiveStream(preflight.config, api_key=preflight.api_key)
            self.provider = await self.provider_context.__aenter__()
            self._receive_provider()
            authorized = await self.service(
                "authorize_stream_batch", first_sample_offset=first, sample_count=count, pcm_bytes=pcm,
            )
        if not authorized.dispatchable:
            await self.send({"type": "received", "next_sample_offset": first + count})
            return
        self.audio.extend(pcm)
        self.media_samples = authorized.provider_sample_end
        self.source_end = authorized.next_source_sample_offset
        await self.provider.send_media(pcm)
        self.last_media_at = monotonic()
        await self.service("record_stream_batch_sent", delivery_id=authorized.delivery_id)
        if len(self.audio) == WINDOW_SAMPLES * 2:
            await self.checkpoint()
        await self.send({"type": "received", "next_sample_offset": first + count})

    async def run(self, preflight: _Preflight):
        await self.send({
            "type": "ready", "stream_id": str(self.started.session_id),
            "sample_rate": SAMPLE_RATE, "encoding": "pcm_s16le",
        })
        started_at = monotonic()
        self.browser_task = asyncio.create_task(self.socket.receive())
        while monotonic() - started_at < 4 * 60 * 60:
            pending = {self.browser_task}
            if self.provider_task is not None:
                pending.add(self.provider_task)
            done, _ = await asyncio.wait(pending, timeout=4, return_when=asyncio.FIRST_COMPLETED)
            if self.provider_task is not None and self.provider_task in done:
                from app.services.deepgram_streaming import DeepgramMetadataEvent

                event = await self.provider_event()
                if isinstance(event, DeepgramMetadataEvent):
                    raise AppError(502, "stream_provider_closed", "Transcription connection closed")
            if self.browser_task in done:
                message = self.browser_task.result()
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))
                if message.get("bytes") is not None:
                    await self.audio_frame(message["bytes"], preflight)
                else:
                    control = _control(message)
                    if set(control) != {"type"} or control["type"] not in {"paused", "resumed", "stop"}:
                        raise AppError(422, "stream_message_invalid", "Invalid stream control")
                    if control["type"] == "paused":
                        await self.checkpoint()
                    elif control["type"] == "stop":
                        await self.checkpoint(closing=True)
                        await self.service("finish_transcript_stream")
                        self.completed = True
                        await self.send({"type": "finalized"})
                        return
                self.browser_task = asyncio.create_task(self.socket.receive())
            if monotonic() - self.last_heartbeat_at >= 4:
                await self.service("heartbeat_transcript_stream", lease_seconds=30)
                self.last_heartbeat_at = monotonic()
                if self.provider is not None and monotonic() - self.last_media_at >= 4:
                    await self.provider.send_keep_alive()
        raise AppError(409, "stream_time_limit", "Recording reached its time limit")

    async def close(self):
        self._content_key_cache.clear()
        for task in (self.browser_task, self.provider_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self.provider_context is not None:
            await self.provider_context.__aexit__(None, None, None)
            self.provider_context = None
            self.provider = None

    def recovery_wav(self) -> bytes | None:
        if not self.audio:
            return None
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(bytes(self.audio))
        return output.getvalue()


@router.websocket("/api/v1/transcripts/{transcript_id}/deepgram-stream")
async def deepgram_live(socket: WebSocket, transcript_id: UUID):
    relay = None
    accepted = False
    try:
        if not _same_origin(socket):
            await socket.close(code=1008)
            return
        await _owner_call(socket, transcript_id, lambda db, owner, root: None)
        await socket.accept()
        accepted = True
        message = _control(await asyncio.wait_for(socket.receive(), timeout=5))
        csrf = message.get("csrf_token")
        if (
            set(message) != {"type", "csrf_token"} or message.get("type") != "start"
            or not isinstance(csrf, str) or not verify_csrf_token(
                submitted_token=csrf, raw_session_token=socket.cookies.get(SESSION_COOKIE_NAME), anon_nonce=None,
            )
        ):
            raise AppError(403, "stream_csrf_invalid", "Stream authentication failed")
        preflight = await _owner_call(socket, transcript_id, _preflight)
        from app.services.transcript_streaming import start_transcript_stream

        started = await _owner_call(
            socket, transcript_id,
            lambda db, owner, root: start_transcript_stream(
                db, owner, transcript_id=transcript_id, selection_snapshot=preflight.snapshot, lease_seconds=30,
            ),
        )
        relay = _Relay(socket, transcript_id, started)
        await relay.run(preflight)
    except (Exception, asyncio.CancelledError) as exc:
        # Never propagate raw SDK/DB exceptions through ASGI tracebacks: their
        # exception strings can include provider bodies or encrypted SQL binds.
        allowed_codes = {
            "unauthorized", "forbidden", "not_found", "stream_csrf_invalid",
            "stt_config_secret_missing", "stream_provider_ineligible", "stream_transcript_busy",
            "stream_frame_invalid", "stream_message_invalid", "stream_session_expired",
            "stream_result_invalid", "stream_finalization_failed", "stream_time_limit",
        }
        code = "stream_failed"
        if isinstance(exc, AppError):
            if exc.code in {"quota_exceeded", "quota_disabled"}:
                code = "quota_exceeded"
            elif exc.code in allowed_codes:
                code = exc.code
        recovery_info = {}
        if relay is not None and not relay.completed:
            try:
                failed = await relay.service(
                    "fail_transcript_stream", error_code="stream_quota_exceeded" if code == "quota_exceeded" else code,
                    recovery_audio_bytes=relay.recovery_wav(),
                )
                recovery_info = {
                    "stream_id": str(relay.started.session_id), "window_no": relay.window_no,
                    "recovery_available": failed.recovery_available,
                    "next_sample_offset": failed.next_source_sample_offset,
                    "unresolved": failed.status.value == "failed",
                }
                def gap_bounds(db, owner, root):
                    window = db.scalar(select(TranscriptStreamWindow).where(
                        TranscriptStreamWindow.session_id == relay.started.session_id,
                        TranscriptStreamWindow.window_no == relay.window_no,
                    ))
                    return {} if window is None else {
                        "source_start_sample_offset": window.source_start_sample_offset,
                        "source_end_sample_offset": window.source_end_sample_offset,
                        "forwarded_sample_count": window.forwarded_sample_count,
                    }
                recovery_info.update(await _owner_call(socket, transcript_id, gap_bounds))
            except Exception:
                logger.warning("deepgram_stream_reconciliation_pending")
        if accepted and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
            with contextlib.suppress(Exception):
                await socket.send_json({"type": "error", "code": code, **recovery_info})
        logger.info("deepgram_stream_closed", extra={"stream_outcome": code})
        if isinstance(exc, asyncio.CancelledError):
            raise
    finally:
        if relay is not None:
            with contextlib.suppress(Exception):
                await relay.close()
            relay.audio.clear()
            relay.finals.clear()
        with contextlib.suppress(Exception):
            await socket.close(code=1000 if relay is not None and relay.completed else 1008)


# Ordinary HTTP recovery requests use the same full-session, CSRF, upload bounds
# and durable prerecorded dispatch as other owner ingestion routes.
from app.main import AuthenticatedContext, api, require_full_context, LIVE_CHUNK_UPLOAD_RATE_LIMIT
from app.services.audio import read_live_chunk_upload


def _recovery_session(db: Session, owner, transcript_id: UUID, session_id: UUID):
    get_active_owner_transcript(db, owner, transcript_id=transcript_id)
    session = db.scalar(select(TranscriptStreamSession).where(
        TranscriptStreamSession.id == session_id,
        TranscriptStreamSession.transcript_id == transcript_id,
        TranscriptStreamSession.owner_user_id == owner.id,
        TranscriptStreamSession.team_id == owner.team_id,
    ))
    if session is None or owner.is_system_admin:
        raise AppError(404, "not_found", "Stream session not found")
    return session


@api.post("/transcripts/{transcript_id}/deepgram-stream/{session_id}/retry", status_code=202)
@LIVE_CHUNK_UPLOAD_RATE_LIMIT
def retry_deepgram_stream(
    request: Request, transcript_id: UUID, session_id: UUID,
    audio: UploadFile | None = File(default=None),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    from app.services.transcript_streaming import queue_transcript_stream_recovery

    session = _recovery_session(db, context.user, transcript_id, session_id)
    audio_bytes = read_live_chunk_upload(upload=audio) if audio is not None else None
    job_id = queue_transcript_stream_recovery(
        db, context.user.id, transcript_id=transcript_id, session_id=session_id,
        window_no=session.window_no, audio_bytes=audio_bytes,
        filename="stream-recovery.wav",
    )
    return {"job_id": str(job_id)}


@api.post("/transcripts/{transcript_id}/deepgram-stream/{session_id}/discard")
def discard_deepgram_stream(
    transcript_id: UUID, session_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    from app.services.transcript_streaming import discard_transcript_stream_gap

    _recovery_session(db, context.user, transcript_id, session_id)
    discard_transcript_stream_gap(db, context.user.id, transcript_id=transcript_id, session_id=session_id)
    return {"status": "discarded"}
