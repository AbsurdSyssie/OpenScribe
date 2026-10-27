"""Owner-only live audio relay. Audio and provider text never enter diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import struct
import io
import logging
import wave
from time import monotonic
from dataclasses import dataclass, field
from uuid import UUID, uuid4

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
from app.services import deepgram_diagnostics


router = APIRouter()
logger = logging.getLogger("openscribe.deepgram_live")
deepgram_diagnostics.configure_file_sink(os.getenv("DEEPGRAM_LIVE_LOG_PATH"))
SAMPLE_RATE = 16000
WINDOW_SAMPLES = 30 * SAMPLE_RATE
CONTROL_WRITE_TIMEOUT_SECONDS = 1.0
FINALIZE_ACK_TIMEOUT_SECONDS = 2.0
CLOSE_DRAIN_TIMEOUT_SECONDS = 2.0
PROVIDER_KEEPALIVE_INTERVAL_SECONDS = 1.0
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


def _preflight(db, owner, root, purpose=SttSelectionPurpose.conversation) -> _Preflight:
    from app.models import SttAdapterKind
    from app.services.deepgram_streaming import DeepgramStreamConfig, validate_stream_config
    from app.services.stt_presets import is_deepgram_stt_base_url
    from app.services.preferences import diarization_preference_enabled

    _, config, model, language = resolve_selected_team_stt(db, team_id=owner.team_id, purpose=purpose)
    if config.adapter_kind is not SttAdapterKind.generic_rest or not is_deepgram_stt_base_url(config.base_url):
        raise AppError(422, "stream_provider_ineligible", "The selected provider does not support this stream")
    effective_diarization = bool(
        purpose == SttSelectionPurpose.conversation
        and getattr(config, "diarization_live_enabled", False)
        and diarization_preference_enabled(db, owner)
    )
    from app.services.stt import _deepgram_query_fields
    frozen_options = _deepgram_query_fields(config.extra_form_fields_json or {}, enabled=effective_diarization)
    frozen = DeepgramStreamConfig(
        base_url=config.base_url, model_name=model, language=language,
        extra_query_params=frozen_options,
    )
    validate_stream_config(frozen)
    snapshot = {
        "config_id": str(config.id), "provider_preset": config.provider_preset,
        "adapter_kind": config.adapter_kind.value, "base_url": config.base_url,
        "model_name": model, "language": language,
        "extra_query_params": frozen_options, "mip_opt_out": True,
        "diarization_live_enabled": bool(getattr(config, "diarization_live_enabled", False)),
        "stt_diarisation_enabled": effective_diarization,
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
    def __init__(self, socket: WebSocket, transcript_id: UUID, started, *, dictation: bool = False):
        self.socket = socket
        self.dictation = dictation
        self.transcript_id = transcript_id
        self.started = started
        self.provider = None
        self.provider_context = None
        self.provider_task = None
        self.browser_task = None
        self.window_no = started.window_no
        self.media_samples = 0
        self.committed_samples = 0
        self.connection_base_samples = 0
        self.finalize_pending_window: tuple[int, int, int] | None = None
        self.source_end = started.next_source_sample_offset
        self.audio = bytearray()
        self.finals: dict[tuple[int, int], str] = {}
        self.final_events: dict[tuple[int, int], object] = {}
        self.scope_id = None
        self.diarization_enabled = False
        self.last_media_at = monotonic()
        self.last_heartbeat_at = monotonic()
        self.completed = False
        self.active_phase = "session_start"
        self.last_completed_stage = "session_started"
        self.last_failure_code = None
        self._first_dispatch_logged = False
        self._content_key_cache = {}
        self._content_key_cache_started = monotonic()
        self.diagnostic_id = uuid4().hex
        self._window_diagnostics = {
            "media_batches": 0, "media_samples": 0, "provider_results": 0,
            "interim_results": 0, "final_results": 0, "metadata_events": 0,
            "keepalives": 0, "reconnects": 0,
        }

    def diagnostic(self, event: str, **fields) -> None:
        deepgram_diagnostics.emit(event, diagnostic_id=self.diagnostic_id, **fields)

    def _take_window_diagnostics(self) -> dict[str, int]:
        result = self._window_diagnostics
        self._window_diagnostics = {key: 0 for key in result}
        return result

    async def _open_provider(self, preflight: _Preflight) -> None:
        from app.services.deepgram_streaming import DeepgramLiveStream

        self.provider_context = DeepgramLiveStream(preflight.config, api_key=preflight.api_key)
        connect_started = monotonic()
        self.active_phase = "connect"
        self.diagnostic("provider_connect", phase="connect", outcome="started")
        try:
            self.provider = await self.provider_context.__aenter__()
        except Exception:
            self.last_failure_code = "stream_provider_failed"
            self.diagnostic(
                "provider_connect", phase="connect", outcome="failed",
                duration_ms=min(3_600_000, round((monotonic() - connect_started) * 1000)),
            )
            raise
        self.diagnostic(
            "provider_connect", phase="connect", outcome="success",
            duration_ms=min(3_600_000, round((monotonic() - connect_started) * 1000)),
        )
        self._first_dispatch_logged = False
        self.last_completed_stage = "provider_connected"
        self.scope_id = uuid4()
        self.diarization_enabled = bool(preflight.snapshot.get("stt_diarisation_enabled"))
        self.connection_base_samples = self.media_samples
        self._receive_provider()

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
        previous_phase = self.active_phase
        self.active_phase = "browser_ack"
        try:
            await _send(self.socket, self.transcript_id, payload)
        except Exception:
            self.last_failure_code = "browser_ack_failed"
            raise
        self.active_phase = previous_phase

    def _receive_provider(self):
        self.provider_task = asyncio.create_task(self.provider.receive(timeout_seconds=60))

    async def provider_event(self, *, deliver_interim: bool = True):
        from app.services.deepgram_streaming import DeepgramMetadataEvent, DeepgramStreamError

        task, self.provider_task = self.provider_task, None
        self.active_phase = "provider_receive"
        try:
            event = await task
        except DeepgramStreamError as exc:
            if exc.category == "timeout":
                self._receive_provider()
                return None
            raise
        if isinstance(event, DeepgramMetadataEvent):
            self._window_diagnostics["metadata_events"] += 1
            return event
        self._window_diagnostics["provider_results"] += 1
        result_kind = "final_results" if event.is_final else "interim_results"
        self._window_diagnostics[result_kind] += 1
        self._receive_provider()
        first = self.connection_base_samples + round(event.start * SAMPLE_RATE)
        last = self.connection_base_samples + round((event.start + event.duration) * SAMPLE_RATE)
        if first < 0 or last < first or last > self.media_samples + 1:
            raise AppError(502, "stream_result_invalid", "Invalid transcription interval")
        if last <= self.committed_samples:
            return event
        if first < self.committed_samples:
            raise AppError(502, "stream_result_invalid", "Overlapping transcription interval")
        if last == first:
            # A zero-length result has no window association. It cannot
            # contribute text or acknowledge a pending Finalize.
            return event
        if event.is_final:
            identity = (first, last)
            existing_event = self.final_events.get(identity)
            if identity in self.finals and (
                self.finals[identity] != event.text
                or (existing_event is not None and getattr(existing_event, "words", ()) != event.words)
            ):
                raise AppError(502, "stream_result_invalid", "Conflicting finalized transcription")
            if len(self.finals) >= 2048 and identity not in self.finals:
                raise AppError(502, "stream_result_invalid", "Too many transcription segments")
            for (previous_first, previous_last), _ in self.finals.items():
                if identity != (previous_first, previous_last) and first < previous_last and last > previous_first:
                    raise AppError(502, "stream_result_invalid", "Overlapping finalized transcription")
            self.finals[identity] = event.text
            self.final_events[identity] = event
        provisional = " ".join(text for _, text in sorted(self.finals.items()) if text)
        if not event.is_final and event.text:
            provisional = f"{provisional} {event.text}".strip()
        if len(provisional) > 64000:
            raise AppError(502, "stream_result_invalid", "Transcription checkpoint exceeds its limit")
        if deliver_interim:
            if self.dictation:
                await self.send({"type": "dictation_preview", "window_no": self.window_no,
                                 "confirmed_text": " ".join(text for _, text in sorted(self.finals.items()) if text),
                                 "interim_text": "" if event.is_final else event.text})
            else:
                await self.send({"type": "interim", "text": provisional})
        return event

    def _window_has_final_coverage(self, start: int, end: int) -> bool:
        cursor = start
        for first, last in sorted(self.finals):
            if last <= cursor:
                continue
            if first > cursor:
                return False
            cursor = max(cursor, last)
            if cursor >= end:
                return True
        return cursor >= end

    async def _wait_provider_event(self, *, deadline: float, keep_alive: bool):
        from app.services.deepgram_streaming import DeepgramMetadataEvent

        next_keep_alive = monotonic() + PROVIDER_KEEPALIVE_INTERVAL_SECONDS
        while True:
            if self.provider_task is None:
                raise AppError(502, "stream_finalization_failed", "Transcription did not finalize")
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            wake_at = min(deadline, next_keep_alive) if keep_alive else deadline
            done, _ = await asyncio.wait({self.provider_task}, timeout=max(0, wake_at - monotonic()))
            if self.provider_task in done:
                # The eventual encrypted checkpoint delivers the complete
                # final text. Avoid a second owner recheck/send inside the
                # already bounded provider acknowledgement wait.
                event = await self.provider_event(deliver_interim=False)
                if isinstance(event, DeepgramMetadataEvent):
                    return event
                return event
            if not keep_alive:
                return None
            if self.provider is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(
                        self.provider.send_keep_alive(),
                        timeout=min(CONTROL_WRITE_TIMEOUT_SECONDS, remaining),
                    )
                    self._window_diagnostics["keepalives"] += 1
                except asyncio.TimeoutError:
                    return None
            next_keep_alive = monotonic() + PROVIDER_KEEPALIVE_INTERVAL_SECONDS

    async def _drain_and_close_provider(self):
        from app.services.deepgram_streaming import DeepgramMetadataEvent

        if self.provider is None:
            return
        drain_started = monotonic()
        self.active_phase = "drain"
        self.diagnostic("provider_drain", phase="drain", outcome="started", window_no=self.window_no)
        try:
            await asyncio.wait_for(self.provider.send_close_stream(), timeout=CONTROL_WRITE_TIMEOUT_SECONDS)
            deadline = monotonic() + CLOSE_DRAIN_TIMEOUT_SECONDS
            while True:
                event = await self._wait_provider_event(deadline=deadline, keep_alive=False)
                if event is None:
                    raise AppError(502, "stream_finalization_failed", "Transcription did not finalize")
                if isinstance(event, DeepgramMetadataEvent):
                    break
            context, self.provider_context = self.provider_context, None
            self.provider = None
            self.connection_base_samples = self.media_samples
            self.finalize_pending_window = None
            if context is not None:
                await context.__aexit__(None, None, None)
            self.last_completed_stage = "provider_drained"
        except Exception:
            self.last_failure_code = "stream_finalization_timeout"
            self.diagnostic(
                "provider_drain", phase="drain", outcome="failed", failure="stream_finalization_timeout",
                window_no=self.window_no,
                duration_ms=min(3_600_000, round((monotonic() - drain_started) * 1000)),
            )
            raise
        self.diagnostic(
            "provider_drain", phase="drain", outcome="success", window_no=self.window_no,
            duration_ms=min(3_600_000, round((monotonic() - drain_started) * 1000)),
        )

    async def _commit_window(self):
        if not self.audio:
            return
        text = " ".join(value.strip() for _, value in sorted(self.finals.items()) if value.strip())
        structured_turns = None
        if self.diarization_enabled:
            from app.services.deepgram_streaming import deepgram_event_words_or_fallback, group_deepgram_words
            word_events = [
                word
                for _identity, event in sorted(self.final_events.items())
                for word in deepgram_event_words_or_fallback(event)
            ]
            structured_turns = group_deepgram_words(word_events)
        commit_started = monotonic()
        self.active_phase = "commit"
        try:
            committed = await self.service(
            "commit_stream_window", window_no=self.window_no,
            source_end_sample_offset=self.source_end,
            forwarded_sample_count=self.media_samples - self.committed_samples,
            finalized_text=text, final_segment_count=len(self.finals),
            finalized_turns=structured_turns, scope_id=self.scope_id,
            )
        except Exception:
            self.last_failure_code = "stream_commit_failed"
            self.diagnostic(
                "window_commit", phase="commit", outcome="failed", failure="stream_commit_failed",
                window_no=self.window_no,
                duration_ms=min(3_600_000, round((monotonic() - commit_started) * 1000)),
            )
            raise
        self._content_key_cache.clear()
        self._content_key_cache_started = monotonic()
        self.last_completed_stage = "window_committed"
        self.diagnostic(
            "window_commit", phase="commit", outcome="success", window_no=self.window_no,
            duration_ms=min(3_600_000, round((monotonic() - commit_started) * 1000)),
            final_results=len(self.finals),
        )
        ack_started = monotonic()
        self.active_phase = "browser_ack"
        try:
            payload = {"type": "window_committed", "window_no": committed.window_no,
                       "next_sample_offset": committed.next_source_sample_offset}
            if self.dictation:
                payload["text"] = text
            await self.send(payload)
        except Exception:
            self.last_failure_code = "browser_ack_failed"
            self.diagnostic(
                "browser_ack", phase="browser_ack", outcome="failed", failure="browser_ack_failed",
                window_no=self.window_no,
                duration_ms=min(3_600_000, round((monotonic() - ack_started) * 1000)),
            )
            raise
        self.diagnostic(
            "browser_ack", phase="browser_ack", outcome="success", window_no=self.window_no,
            duration_ms=min(3_600_000, round((monotonic() - ack_started) * 1000)),
        )
        self.last_completed_stage = "browser_acknowledged"
        self.active_phase = "checkpoint"
        self.diagnostic(
            "checkpoint", phase="checkpoint", outcome="success", window_no=self.window_no,
            **self._take_window_diagnostics(),
        )
        self.committed_samples = self.media_samples
        self.window_no += 1
        self.audio.clear()
        self.finals.clear()
        self.final_events.clear()

    async def checkpoint(self, *, closing: bool = False):
        from app.services.deepgram_streaming import DeepgramMetadataEvent

        if self.provider is None:
            return
        if closing:
            await self._drain_and_close_provider()
            await self._commit_window()
            return
        if not self.audio:
            return

        checkpoint_started = monotonic()
        self.active_phase = "checkpoint"
        self.diagnostic(
            "checkpoint", phase="checkpoint", outcome="started", window_no=self.window_no,
            media_samples=self.media_samples - self.committed_samples,
        )

        window_start, window_end, window_no = self.committed_samples, self.media_samples, self.window_no
        if self._window_has_final_coverage(window_start, window_end):
            await self._commit_window()
            return

        # Once Finalize is sent, only its own positive-duration final marker can release
        # this socket for more audio. Interval coverage arriving later does not
        # pay that acknowledgement debt.
        self.finalize_pending_window = (window_no, window_start, window_end)
        try:
            finalize_started = monotonic()
            self.active_phase = "finalize"
            self.diagnostic("checkpoint", phase="finalize", outcome="started", window_no=window_no)
            await asyncio.wait_for(self.provider.send_finalize(), timeout=CONTROL_WRITE_TIMEOUT_SECONDS)
            deadline = monotonic() + FINALIZE_ACK_TIMEOUT_SECONDS
            acknowledged = False
            while monotonic() < deadline:
                event = await self._wait_provider_event(deadline=deadline, keep_alive=True)
                if event is None or isinstance(event, DeepgramMetadataEvent):
                    break
                first = self.connection_base_samples + round(event.start * SAMPLE_RATE)
                last = self.connection_base_samples + round((event.start + event.duration) * SAMPLE_RATE)
                if (
                    event.is_final and event.from_finalize
                    and last > first and first >= window_start and last <= window_end
                    and self.finalize_pending_window == (window_no, window_start, window_end)
                ):
                    acknowledged = True
                    self.finalize_pending_window = None
                    break
            if acknowledged:
                self.last_completed_stage = "finalized"
                self.active_phase = "checkpoint"
                self.diagnostic(
                    "checkpoint", phase="finalize", outcome="success", window_no=window_no,
                    duration_ms=min(3_600_000, round((monotonic() - finalize_started) * 1000)),
                )
                await self._commit_window()
                return
        except asyncio.TimeoutError:
            pass

        # An unproven Finalize is exceptional. Drain and close the old socket;
        # durable recovery retains the exact unresolved window if that fails.
        self.diagnostic(
            "checkpoint", phase="finalize", outcome="timeout", window_no=window_no,
            duration_ms=min(3_600_000, round((monotonic() - checkpoint_started) * 1000)),
            exceptional_fallback=True,
        )
        await self._drain_and_close_provider()
        self._window_diagnostics["reconnects"] += 1
        await self._commit_window()

    async def audio_frame(self, data: bytes, preflight: _Preflight):
        first, count, pcm = parse_audio_frame(data)
        if len(self.audio) + len(pcm) > WINDOW_SAMPLES * 2:
            raise AppError(422, "stream_frame_invalid", "Audio crossed its checkpoint boundary")
        if self.provider is None:
            # Do not open a billable/timeout-prone provider connection during
            # initial local listening. No artificial silence is sent.
            await self._open_provider(preflight)
        self.active_phase = "authorize"
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
            if self.provider is None:
                await self._open_provider(preflight)
            authorized = await self.service(
                "authorize_stream_batch", first_sample_offset=first, sample_count=count, pcm_bytes=pcm,
            )
        if not authorized.dispatchable:
            await self.send({"type": "received", "next_sample_offset": first + count})
            return
        self.audio.extend(pcm)
        self.media_samples = authorized.provider_sample_end
        self.source_end = authorized.next_source_sample_offset
        dispatch_started = monotonic()
        self.active_phase = "media_send"
        try:
            await self.provider.send_media(pcm)
        except Exception:
            self.last_failure_code = "stream_provider_failed"
            self.diagnostic(
                "media_dispatch", phase="media_send", outcome="failed", failure="stream_provider_failed",
                duration_ms=min(3_600_000, round((monotonic() - dispatch_started) * 1000)),
            )
            raise
        self._window_diagnostics["media_batches"] += 1
        self._window_diagnostics["media_samples"] += count
        if not self._first_dispatch_logged:
            self.diagnostic(
                "media_dispatch", phase="media_send", outcome="success",
                duration_ms=min(3_600_000, round((monotonic() - dispatch_started) * 1000)),
            )
            self._first_dispatch_logged = True
        self.last_media_at = monotonic()
        self.active_phase = "record_sent"
        try:
            await self.service("record_stream_batch_sent", delivery_id=authorized.delivery_id)
        except Exception:
            self.last_failure_code = "stream_accounting_failed"
            raise
        self.last_completed_stage = "media_dispatched"
        self.active_phase = "authorize"
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
                self.active_phase = "browser_receive"
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
                        self.active_phase = "stop"
                        await self.checkpoint(closing=True)
                        self.active_phase = "stop"
                        await self.service("finish_transcript_stream")
                        self.completed = True
                        self.last_completed_stage = "finalized"
                        self.diagnostic(
                            "recording_finished", phase="stop", outcome="completed",
                            window_no=self.window_no, **self._take_window_diagnostics(),
                        )
                        await self.send({"type": "finalized"})
                        return
                self.browser_task = asyncio.create_task(self.socket.receive())
            if monotonic() - self.last_heartbeat_at >= 4:
                self.active_phase = "heartbeat"
                try:
                    await self.service("heartbeat_transcript_stream", lease_seconds=30)
                except Exception:
                    self.last_failure_code = "stream_lease_failed"
                    raise
                self.last_heartbeat_at = monotonic()
                self.active_phase = "browser_receive"
                if self.provider is not None and monotonic() - self.last_media_at >= 4:
                    try:
                        self.active_phase = "keepalive"
                        await self.provider.send_keep_alive()
                        self._window_diagnostics["keepalives"] += 1
                        self.active_phase = "browser_receive"
                    except Exception:
                        self.last_failure_code = "stream_provider_failed"
                        self.diagnostic(
                            "checkpoint", phase="checkpoint", outcome="failed",
                            failure="stream_provider_failed", window_no=self.window_no,
                            **self._take_window_diagnostics(),
                        )
                        raise
        raise AppError(409, "stream_time_limit", "Recording reached its time limit")

    async def close(self):
        self.active_phase = "close"
        self._content_key_cache.clear()
        for task in (self.browser_task, self.provider_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self.provider_context is not None:
            try:
                await self.provider_context.__aexit__(None, None, None)
            except Exception:
                self.diagnostic(
                    "provider_close", phase="close", outcome="failed", failure="provider_close_failed",
                )
                raise
            else:
                self.diagnostic("provider_close", phase="close", outcome="success")
            finally:
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
    await _deepgram_live(socket, transcript_id, dictation=False)


@router.websocket("/api/v1/transcripts/{transcript_id}/post-consultation-dictation/deepgram-stream")
async def deepgram_dictation_live(socket: WebSocket, transcript_id: UUID):
    await _deepgram_live(socket, transcript_id, dictation=True)


async def _deepgram_live(socket: WebSocket, transcript_id: UUID, *, dictation: bool):
    relay = None
    accepted = False
    phase = "origin_check"
    phase_started = monotonic()
    try:
        if not _same_origin(socket):
            deepgram_diagnostics.emit("phase", phase="origin_check", outcome="failed")
            await socket.close(code=1008)
            return
        deepgram_diagnostics.emit(
            "phase", phase="origin_check", outcome="success",
            duration_ms=min(3_600_000, round((monotonic() - phase_started) * 1000)),
        )
        phase = "owner_check"
        phase_started = monotonic()
        await _owner_call(socket, transcript_id, lambda db, owner, root: None)
        deepgram_diagnostics.emit(
            "phase", phase="owner_check", outcome="success",
            duration_ms=min(3_600_000, round((monotonic() - phase_started) * 1000)),
        )
        await socket.accept()
        accepted = True
        phase = "handshake"
        phase_started = monotonic()
        message = _control(await asyncio.wait_for(socket.receive(), timeout=5))
        csrf = message.get("csrf_token")
        if (
            set(message) != {"type", "csrf_token"} or message.get("type") != "start"
            or not isinstance(csrf, str) or not verify_csrf_token(
                submitted_token=csrf, raw_session_token=socket.cookies.get(SESSION_COOKIE_NAME), anon_nonce=None,
            )
        ):
            raise AppError(403, "stream_csrf_invalid", "Stream authentication failed")
        deepgram_diagnostics.emit(
            "phase", phase="handshake", outcome="success",
            duration_ms=min(3_600_000, round((monotonic() - phase_started) * 1000)),
        )
        phase = "preflight"
        phase_started = monotonic()
        purpose = SttSelectionPurpose.post_consultation_dictation if dictation else SttSelectionPurpose.conversation
        preflight = await _owner_call(socket, transcript_id, lambda db, owner, root: _preflight(db, owner, root, purpose))
        deepgram_diagnostics.emit(
            "phase", phase="preflight", outcome="success",
            duration_ms=min(3_600_000, round((monotonic() - phase_started) * 1000)),
        )
        from app.services.transcript_streaming import start_transcript_stream

        phase = "session_start"
        phase_started = monotonic()
        started = await _owner_call(
            socket, transcript_id,
            lambda db, owner, root: start_transcript_stream(
                db, owner, transcript_id=transcript_id, selection_snapshot=preflight.snapshot, purpose=purpose.value, lease_seconds=30,
            ),
        )
        relay = _Relay(socket, transcript_id, started, dictation=dictation)
        relay.diagnostic(
            "recording_started", phase="recording", outcome="started", window_no=relay.window_no,
            duration_ms=min(3_600_000, round((monotonic() - phase_started) * 1000)),
        )
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
        from app.services.deepgram_streaming import DeepgramStreamError

        code = "stream_failed"
        failure_code = "stream_failed"
        provider_category = None
        close_class = "none"
        if isinstance(exc, DeepgramStreamError):
            # DeepgramStreamError.category is a closed, content-free enum.
            provider_category = exc.category
            close_class = exc.close_class
            failure_code = "stream_provider_failed"
        elif isinstance(exc, WebSocketDisconnect):
            failure_code = "stream_transport_failed"
        elif isinstance(exc, AppError) and exc.code == "stream_provider_closed":
            # A provider Metadata event before CloseStream is an upstream close.
            failure_code = "stream_provider_failed"
        if isinstance(exc, AppError):
            if exc.code in {"quota_exceeded", "quota_disabled"}:
                code = "quota_exceeded"
                failure_code = "stream_quota_exceeded"
            elif exc.code == "stream_session_expired":
                code = exc.code
                failure_code = "stream_session_expired"
            elif exc.code in allowed_codes:
                code = exc.code
                if exc.code in {"stream_result_invalid", "stream_frame_invalid", "stream_message_invalid"}:
                    failure_code = "stream_protocol_invalid"
                elif exc.code == "stream_finalization_failed":
                    failure_code = "stream_finalization_timeout"
        recovery_info = {}
        failure_stage = relay.active_phase if relay is not None else phase
        last_completed_stage = relay.last_completed_stage if relay is not None else "none"
        if relay is not None and not relay.completed:
            try:
                failed = await relay.service(
                    "fail_transcript_stream", error_code=failure_code,
                    recovery_audio_bytes=None if dictation else relay.recovery_wav(),
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
                if relay is not None:
                    relay.diagnostic(
                        "recovery", phase="recovery", outcome="pending",
                        failure="reconciliation_pending", window_no=relay.window_no,
                    )
                else:
                    deepgram_diagnostics.emit(
                        "recovery", phase="recovery", outcome="pending",
                        failure="reconciliation_pending",
                    )
            else:
                relay.diagnostic(
                    "recovery", phase="recovery",
                    outcome="available" if recovery_info.get("recovery_available") else "unavailable",
                    window_no=relay.window_no,
                    recovery_available=bool(recovery_info.get("recovery_available")),
                    unresolved=bool(recovery_info.get("unresolved")),
                )
        if accepted and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
            with contextlib.suppress(Exception):
                await socket.send_json({"type": "error", "code": code, **recovery_info})
        if relay is not None:
            if relay.last_failure_code is not None:
                failure_code = relay.last_failure_code
            relay.diagnostic(
                "recording_failed", phase=failure_stage, outcome="failed", failure=failure_code,
                provider_category=provider_category or "none", close_class=close_class,
                window_no=relay.window_no, last_completed_stage=last_completed_stage,
                **relay._take_window_diagnostics(),
            )
        else:
            failure_safe = failure_code if failure_code in {
                "stream_failed", "stream_provider_failed", "stream_transport_failed",
                "stream_protocol_invalid", "stream_session_expired", "stream_quota_exceeded",
                "stream_finalization_timeout", "stream_commit_failed", "stream_accounting_failed",
                "stream_lease_failed", "reconciliation_pending", "browser_ack_failed", "provider_close_failed",
            } else "stream_failed"
            deepgram_diagnostics.emit(
                "phase", phase=phase, outcome="failed", failure=failure_safe,
                provider_category=provider_category or "none", close_class=close_class,
                duration_ms=min(3_600_000, round((monotonic() - phase_started) * 1000)),
            )
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
        TranscriptStreamSession.purpose == "conversation",
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
