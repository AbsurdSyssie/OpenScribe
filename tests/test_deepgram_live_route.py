"""Synthetic route and relay regressions for the Deepgram live boundary."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    ProviderAttempt,
    ProviderSettlementBasis,
    SessionStatus,
    TeamRole,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    UserOnboardingState,
    UserSession,
    utcnow,
)
from app.routes import deepgram_live as live
from app.services.auth import SESSION_COOKIE_NAME
from app.services.csrf import session_csrf_token
from app.services.transcript_streaming import (
    StreamBatchAuthorization,
    StreamSessionStarted,
    StreamWindowCommitted,
)


def _started():
    return StreamSessionStarted(
        session_id=uuid4(), connection_fence=1, fence_token=uuid4(),
        lease_expires_at=utcnow() + timedelta(seconds=30), window_no=0,
        next_source_sample_offset=0,
    )


async def _async_noop_owner_call(*_args, **_kwargs):
    return None


class _FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _FakeProvider:
    def __init__(self, events=(), order=None):
        self.events = asyncio.Queue()
        for event in events:
            self.events.put_nowait(event)
        self.order = order if order is not None else []
        self.media: list[bytes] = []
        self.controls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.order.append("provider_exit")

    async def receive(self, *, timeout_seconds=None):
        if timeout_seconds is None:
            return await self.events.get()
        return await asyncio.wait_for(self.events.get(), timeout_seconds)

    async def send_media(self, payload):
        self.order.append("send_media")
        self.media.append(payload)

    async def send_keep_alive(self):
        self.order.append("keep_alive")
        self.controls.append("keep_alive")

    async def send_finalize(self):
        self.order.append("finalize")
        self.controls.append("finalize")

    async def send_close_stream(self):
        self.order.append("close_stream")
        self.controls.append("close_stream")


def _result(*, start=0.0, duration=1.0, text="synthetic finalized speech", final=True):
    from app.services.deepgram_streaming import DeepgramResultEvent

    return DeepgramResultEvent(
        text=text, is_final=final, speech_final=final, from_finalize=True,
        start=start, duration=duration,
    )


def _metadata():
    from app.services.deepgram_streaming import DeepgramMetadataEvent

    return DeepgramMetadataEvent(duration=1.0, request_id="synthetic-request", channels=1)


def test_live_result_is_delivered_while_recording_without_waiting_for_stop(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        provider = _FakeProvider([_result(duration=0.5)])
        relay.provider = provider
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        relay.media_samples = 16_000
        relay.provider_task = asyncio.create_task(provider.receive(timeout_seconds=1))

        event = await relay.provider_event()

        assert event.text == "synthetic finalized speech"
        assert socket.sent == [{"type": "interim", "text": "synthetic finalized speech"}]
        assert not relay.completed
        await relay.close()

    asyncio.run(scenario())


def test_authorization_commits_before_provider_media_and_quota_failure_sends_nothing(monkeypatch):
    async def scenario():
        from app.errors import AppError

        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        order: list[str] = []
        provider = _FakeProvider(order=order)

        class ProviderContext:
            async def __aenter__(self):
                return provider

            async def __aexit__(self, *_exc):
                await provider.__aexit__(*_exc)

        monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", lambda *_a, **_k: ProviderContext())
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)

        async def reject_quota(name, **_kwargs):
            if name == "authorize_stream_batch":
                order.append("authorize")
                raise AppError(429, "quota_exceeded", "Synthetic quota limit")
            raise AssertionError(name)

        relay.service = reject_quota
        frame = live.FRAME_HEADER.pack(1, 0, 2) + b"\x01\x00\x02\x00"

        with pytest.raises(AppError) as raised:
            await relay.audio_frame(frame, live._Preflight(config=object(), snapshot={}, api_key="synthetic"))

        assert getattr(raised.value, "code", None) == "quota_exceeded"
        assert order == ["authorize"]
        assert provider.media == []
        await relay.close()

    asyncio.run(scenario())


def test_short_pause_checkpoint_finalizes_and_reuses_socket_before_ack(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        order: list[str] = []
        provider = _FakeProvider([_result(duration=1.0, text=""), _metadata()], order=order)
        relay.provider = provider
        relay.provider_context = provider
        relay.audio.extend(b"\x00\x00" * 16_000)
        relay.media_samples = 16_000

        async def service(name, **kwargs):
            order.append(name)
            assert name == "commit_stream_window"
            assert kwargs["forwarded_sample_count"] == 16_000
            return StreamWindowCommitted(0, 16_000, 16_000)

        relay.service = service
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        relay._receive_provider()

        await relay.checkpoint()

        assert order.index("finalize") < order.index("commit_stream_window")
        assert socket.sent[-1] == {"type": "window_committed", "window_no": 0, "next_sample_offset": 16_000}
        assert relay.audio == bytearray()
        assert relay.provider is provider
        assert "close_stream" not in provider.controls
        await relay.close()

    asyncio.run(scenario())


def test_silent_window_closes_and_commits_empty_text_without_transcript_span(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        class SilentProvider(_FakeProvider):
            async def send_close_stream(self):
                await super().send_close_stream()
                self.events.put_nowait(_metadata())

        provider = SilentProvider()
        relay.provider = provider
        relay.provider_context = provider
        relay.audio.extend(b"\x00\x00" * 32)
        relay.media_samples = 16
        relay.source_end = 16
        committed = {}

        async def service(name, **kwargs):
            assert name == "commit_stream_window"
            committed.update(kwargs)
            return StreamWindowCommitted(0, 16, 16)

        relay.service = service
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        relay._receive_provider()

        await relay.checkpoint()

        assert committed["finalized_text"] == ""
        assert committed["final_segment_count"] == 0
        assert socket.sent[-1]["type"] == "window_committed"
        await relay.close()

    asyncio.run(scenario())


def test_stop_with_idle_provider_closes_once_without_phantom_commit(monkeypatch):
    async def scenario():
        relay = live._Relay(_FakeSocket(), uuid4(), _started())
        exits = []
        commits = []

        class ProviderContext:
            async def __aexit__(self, *_exc):
                exits.append("closed")

        provider_context = ProviderContext()

        class IdleProvider(_FakeProvider):
            async def send_close_stream(self):
                await super().send_close_stream()
                self.events.put_nowait(_metadata())

        provider = IdleProvider()
        relay.provider = provider
        relay.provider_context = provider_context
        relay.provider_task = asyncio.create_task(provider.receive(timeout_seconds=60))

        async def service(name, **_kwargs):
            if name == "commit_stream_window":
                commits.append(name)
                raise AssertionError("idle Stop must not commit a window")
            return None

        relay.service = service
        await relay.checkpoint(closing=True)

        assert provider.controls == ["close_stream"]
        assert exits == ["closed"]
        assert commits == []
        assert relay.provider is None
        await relay.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("is_final,duration", [(False, 2 / live.SAMPLE_RATE), (True, 0.0)])
def test_nonfinal_or_zero_length_finalize_marker_uses_close_drain_fallback(monkeypatch, is_final, duration):
    from app.services.deepgram_streaming import DeepgramResultEvent

    async def scenario():
        relay = live._Relay(_FakeSocket(), uuid4(), _started())
        provider = _FakeProvider([DeepgramResultEvent(
            text="synthetic marker", is_final=is_final, speech_final=False,
            from_finalize=True, start=0.0, duration=duration,
        )])

        async def close_with_metadata():
            await _FakeProvider.send_close_stream(provider)
            provider.events.put_nowait(_metadata())

        provider.send_close_stream = close_with_metadata
        relay.provider = provider
        relay.provider_context = provider
        relay.audio.extend(b"\x00\x00" * 2)
        relay.media_samples = 2
        relay.source_end = 2
        committed = {}

        async def service(name, **kwargs):
            assert name == "commit_stream_window"
            committed.update(kwargs)
            return StreamWindowCommitted(0, 2, 2)

        relay.service = service
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        monkeypatch.setattr(live, "CONTROL_WRITE_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(live, "FINALIZE_ACK_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(live, "CLOSE_DRAIN_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(live, "PROVIDER_KEEPALIVE_INTERVAL_SECONDS", 0.003)
        relay._receive_provider()

        await relay.checkpoint()

        assert provider.controls[-1] == "close_stream"
        assert committed["finalized_text"] == ""
        assert committed["final_segment_count"] == 0
        assert relay.provider is None
        await relay.close()

    asyncio.run(scenario())


def test_checkpoint_reuses_connection_and_keeps_cumulative_provider_clock(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        providers = []

        def make_provider(*_args, **_kwargs):
            provider = _FakeProvider()

            async def finalize_with_delayed_markers():
                await _FakeProvider.send_finalize(provider)
                provider.events.put_nowait(_result(
                    start=0, duration=2 / live.SAMPLE_RATE, text="stale finalize marker", final=True,
                ))
                provider.events.put_nowait(_result(
                    start=2 / live.SAMPLE_RATE, duration=2 / live.SAMPLE_RATE,
                    text="second window", final=True,
                ))

            provider.send_finalize = finalize_with_delayed_markers
            providers.append(provider)
            return provider

        class ProviderContext:
            def __init__(self, provider):
                self.provider = provider

            async def __aenter__(self):
                return self.provider

            async def __aexit__(self, *_exc):
                await self.provider.__aexit__(*_exc)

        monkeypatch.setattr(
            "app.services.deepgram_streaming.DeepgramLiveStream",
            lambda *args, **kwargs: ProviderContext(make_provider(*args, **kwargs)),
        )
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        total = 0
        committed_text = []

        async def service(name, **kwargs):
            nonlocal total
            if name == "authorize_stream_batch":
                first = kwargs["first_sample_offset"]
                count = kwargs["sample_count"]
                start = total
                total += count
                return StreamBatchAuthorization(
                    uuid4(), uuid4(), True, relay.window_no, start, total,
                    first + count, total, total - relay.committed_samples,
                )
            if name == "record_stream_batch_sent":
                return None
            if name == "commit_stream_window":
                committed_text.append(kwargs["finalized_text"])
                return StreamWindowCommitted(relay.window_no, kwargs["source_end_sample_offset"], kwargs["forwarded_sample_count"])
            raise AssertionError(name)

        relay.service = service
        preflight = live._Preflight(config=object(), snapshot={}, api_key="synthetic")
        first_pcm = b"\x10\x00\x11\x00"
        second_pcm = b"\x20\x00\x21\x00"

        await relay.audio_frame(live.FRAME_HEADER.pack(1, 0, 2) + first_pcm, preflight)
        providers[0].events.put_nowait(_result(duration=2 / live.SAMPLE_RATE, text="first window", final=True))
        first_event = await relay.provider_event()
        assert first_event.start == 0
        await relay.checkpoint()
        await relay.audio_frame(live.FRAME_HEADER.pack(1, 2, 2) + second_pcm, preflight)
        await relay.checkpoint()

        assert len(providers) == 1
        assert providers[0].media == [first_pcm, second_pcm]
        assert providers[0].controls == ["finalize"]
        assert committed_text == ["first window", "second window"]
        await relay.close()

    asyncio.run(scenario())


def test_unconfirmed_finalize_drains_fallback_socket_and_reopens_without_replay(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        providers = []
        exits = []

        class ProviderContext:
            def __init__(self, provider):
                self.provider = provider

            async def __aenter__(self):
                return self.provider

            async def __aexit__(self, *_exc):
                exits.append(self.provider)

        def provider_factory(*_args, **_kwargs):
            provider = _FakeProvider()
            index = len(providers)
            if index == 1:
                async def finalize_with_current_marker():
                    await _FakeProvider.send_finalize(provider)
                    provider.events.put_nowait(_result(
                        start=0, duration=2 / live.SAMPLE_RATE, text="current marker",
                    ))

                provider.send_finalize = finalize_with_current_marker

            async def close_with_metadata():
                await _FakeProvider.send_close_stream(provider)
                provider.events.put_nowait(_metadata())

            provider.send_close_stream = close_with_metadata
            providers.append(provider)
            return ProviderContext(provider)

        monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", provider_factory)
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        monkeypatch.setattr(live, "CONTROL_WRITE_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(live, "FINALIZE_ACK_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(live, "CLOSE_DRAIN_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(live, "PROVIDER_KEEPALIVE_INTERVAL_SECONDS", 0.003)

        total = 0
        commits = []

        async def service(name, **kwargs):
            nonlocal total
            if name == "authorize_stream_batch":
                first = kwargs["first_sample_offset"]
                count = kwargs["sample_count"]
                start = total
                total += count
                return StreamBatchAuthorization(
                    uuid4(), uuid4(), True, relay.window_no, start, total,
                    first + count, total, total - relay.committed_samples,
                )
            if name == "record_stream_batch_sent":
                return None
            if name == "commit_stream_window":
                commits.append(kwargs["finalized_text"])
                return StreamWindowCommitted(
                    relay.window_no, kwargs["source_end_sample_offset"], kwargs["forwarded_sample_count"],
                )
            raise AssertionError(name)

        relay.service = service
        preflight = live._Preflight(config=object(), snapshot={}, api_key="synthetic")
        first_pcm, second_pcm = b"\x10\x00\x11\x00", b"\x20\x00\x21\x00"

        await relay.audio_frame(live.FRAME_HEADER.pack(1, 0, 2) + first_pcm, preflight)
        await relay.checkpoint()
        assert relay.provider is None
        assert providers[0].controls[0] == "finalize"
        assert "keep_alive" in providers[0].controls
        assert providers[0].controls[-1] == "close_stream"
        assert exits == [providers[0]]

        await relay.audio_frame(live.FRAME_HEADER.pack(1, 2, 2) + second_pcm, preflight)
        await relay.checkpoint()

        assert len(providers) == 2
        assert providers[0].media == [first_pcm]
        assert providers[1].media == [second_pcm]
        assert providers[1].controls == ["finalize"]
        assert relay.connection_base_samples == 2
        assert commits == ["", "current marker"]
        await relay.close()

    asyncio.run(scenario())


def test_pause_and_stop_keep_socket_and_commit_before_receipt(monkeypatch):
    async def scenario():
        order: list[str] = []
        providers = []

        class ProviderContext:
            def __init__(self, provider):
                self.provider = provider

            async def __aenter__(self):
                return self.provider

            async def __aexit__(self, *_exc):
                await self.provider.__aexit__(*_exc)

        def provider_factory(*_args, **_kwargs):
            provider = _FakeProvider([_result(duration=0.000125)], order=order)

            async def close_with_metadata():
                await _FakeProvider.send_close_stream(provider)
                provider.events.put_nowait(_metadata())

            provider.send_close_stream = close_with_metadata
            providers.append(provider)
            return ProviderContext(provider)

        monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", provider_factory)
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)

        class BrowserSocket(_FakeSocket):
            def __init__(self, messages):
                super().__init__()
                self.messages = asyncio.Queue()
                for message in messages:
                    self.messages.put_nowait(message)

            async def receive(self):
                return await self.messages.get()

            async def send_json(self, payload):
                if payload["type"] == "window_committed":
                    order.append("ack")
                elif payload["type"] == "finalized":
                    order.append("final_ack")
                await super().send_json(payload)

        pcm1, pcm2 = b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"
        frame1 = live.FRAME_HEADER.pack(1, 0, 2) + pcm1
        frame2 = live.FRAME_HEADER.pack(1, 2, 2) + pcm2
        socket = BrowserSocket([
            {"type": "websocket.receive", "bytes": frame1, "text": None},
            {"type": "websocket.receive", "bytes": None, "text": '{"type":"paused"}'},
            {"type": "websocket.receive", "bytes": frame2, "text": None},
            {"type": "websocket.receive", "bytes": None, "text": '{"type":"stop"}'},
        ])
        relay = live._Relay(socket, uuid4(), _started())
        total = 0
        commits = []

        async def service(name, **kwargs):
            nonlocal total
            if name == "authorize_stream_batch":
                start = total
                total += kwargs["sample_count"]
                order.append("authorize")
                return StreamBatchAuthorization(
                    uuid4(), uuid4(), True, relay.window_no, start, total,
                    kwargs["first_sample_offset"] + kwargs["sample_count"], total, total - relay.committed_samples,
                )
            if name == "record_stream_batch_sent":
                order.append("record_sent")
                return None
            if name == "commit_stream_window":
                order.append("commit")
                commits.append(kwargs["finalized_text"])
                return StreamWindowCommitted(relay.window_no, kwargs["source_end_sample_offset"], kwargs["forwarded_sample_count"])
            if name == "finish_transcript_stream":
                order.append("finish")
                return SimpleNamespace(status="completed")
            raise AssertionError(name)

        relay.service = service
        await relay.run(live._Preflight(config=object(), snapshot={}, api_key="synthetic"))

        assert len(providers) == 1
        assert providers[0].media == [pcm1, pcm2]
        assert providers[0].controls == ["close_stream"]
        assert len(commits) == 2
        assert order.count("finish") == 1
        assert order.count("ack") == 2
        first_commit = order.index("commit")
        first_ack = order.index("ack")
        close_index = order.index("close_stream")
        second_commit = order.index("commit", first_commit + 1)
        second_ack = order.index("ack", first_ack + 1)
        assert first_commit < first_ack < close_index < second_commit < second_ack
        assert second_ack < order.index("finish") < order.index("final_ack")
        assert socket.sent[-1] == {"type": "finalized"}
        assert relay.completed
        await relay.close()

    asyncio.run(scenario())


def test_malformed_audio_frames_fail_closed_with_controlled_error():
    from app.errors import AppError

    for data in (b"", b"not pcm", live.FRAME_HEADER.pack(1, 0, 0), live.FRAME_HEADER.pack(9, 0, 1) + b"\x00\x00"):
        with pytest.raises(AppError) as raised:
            live.parse_audio_frame(data)
        assert raised.value.code == "stream_frame_invalid"


def _route_test_app(db_session):
    from app.main import app

    route_path = "/api/v1/transcripts/{transcript_id}/deepgram-stream"
    if not any(getattr(route, "path", None) == route_path for route in app.routes):
        app.include_router(live.router)
    bind = db_session.get_bind()
    app.state.db_session_factory = sessionmaker(
        bind=bind, autoflush=False, autocommit=False, future=True,
        join_transaction_mode="create_savepoint",
    )
    return app


def _new_transcript(db_session, owner, title="Synthetic live session"):
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id, title=title,
        ingestion_mode=TranscriptIngestionMode.live_chunked, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    return transcript


def _ws_cookies(client):
    return {SESSION_COOKIE_NAME: client.cookies.get(SESSION_COOKIE_NAME)}


@pytest.mark.parametrize("dictation", [False, True])
def test_websocket_rejects_wrong_origin_before_auth_or_provider_work(client, db_session, dictation):
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{uuid4()}/{"post-consultation-dictation/" if dictation else ""}deepgram-stream",
                headers={"Origin": "https://attacker.example"},
            ):
                pass
    assert raised.value.code == 1008


@pytest.mark.parametrize("dictation", [False, True])
def test_websocket_rejects_anonymous_even_with_valid_origin(db_session, make_team, make_user, dictation):
    team = make_team(name="Deepgram WS anonymous team")
    owner = make_user(email="deepgram-ws-anonymous@example.com", team=team)
    transcript = _new_transcript(db_session, owner)
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/{"post-consultation-dictation/" if dictation else ""}deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


def test_route_logs_only_fixed_outcome_when_session_service_raises_sensitive_error(monkeypatch, capsys):
    from app.main import app

    route_path = "/api/v1/transcripts/{transcript_id}/deepgram-stream"
    if not any(getattr(route, "path", None) == route_path for route in app.routes):
        app.include_router(live.router)

    def fail_with_sensitive_text(*_args, **_kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_SENTINEL")

    monkeypatch.setattr(live, "_owner_call", fail_with_sensitive_text)
    with TestClient(app) as ws_client:
        with pytest.raises(WebSocketDisconnect):
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{uuid4()}/deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass

    output = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in output
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert any(record.get("event") == "phase" and record.get("phase") == "owner_check" for record in records)


@pytest.mark.parametrize(
    ("failure_kind", "public_code", "persisted_code", "provider_category"),
    [
        ("provider", "stream_failed", "stream_provider_failed", "provider_error"),
        ("provider_closed", "stream_failed", "stream_provider_failed", "none"),
        ("protocol", "stream_result_invalid", "stream_protocol_invalid", "none"),
        ("finalization", "stream_finalization_failed", "stream_finalization_timeout", "none"),
        ("session_expired", "stream_session_expired", "stream_session_expired", "none"),
        ("disconnect", None, "stream_transport_failed", "none"),
        ("unknown", "stream_failed", "stream_failed", "none"),
    ],
)
def test_stream_failure_diagnostics_are_safe_visible_and_bounded(
    monkeypatch, capsys, tmp_path, failure_kind, public_code, persisted_code, provider_category,
):
    diagnostic_path = tmp_path / "logs" / "deepgram-live.jsonl"
    live.deepgram_diagnostics.configure_file_sink(str(diagnostic_path))
    from app.errors import AppError
    from app.services.deepgram_streaming import DeepgramStreamError

    class SyntheticSocket(_FakeSocket):
        def __init__(self):
            super().__init__()
            self.url = SimpleNamespace(scheme="http")
            self.headers = {"host": "testserver", "origin": "http://testserver"}
            self.cookies = {SESSION_COOKIE_NAME: "synthetic-session"}
            self.messages = [
                {"text": '{"type":"start","csrf_token":"synthetic-csrf"}'},
                {"type": "websocket.receive", "bytes": live.FRAME_HEADER.pack(1, 0, 2) + bytes((1, 0, 2, 0))},
            ]
            self.closed = []

        async def accept(self):
            return None

        async def receive(self):
            if self.messages:
                return self.messages.pop(0)
            await asyncio.Event().wait()

        async def close(self, code=1000):
            self.closed.append(code)

    socket = SyntheticSocket()
    started = _started()
    preflight = live._Preflight(config=SimpleNamespace(), snapshot={}, api_key="synthetic-key")
    owner_calls = 0

    async def fake_owner_call(_socket, _transcript_id, _operation):
        nonlocal owner_calls
        owner_calls += 1
        if owner_calls == 2:
            return preflight
        if owner_calls == 3:
            return started
        if owner_calls >= 6:
            return {}
        return None

    class ProviderContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_media(self, _pcm):
            return None

        async def receive(self, *, timeout_seconds=None):
            if failure_kind == "provider":
                raise DeepgramStreamError("provider_error")
            if failure_kind == "provider_closed":
                raise AppError(502, "stream_provider_closed", "Synthetic provider close")
            if failure_kind == "protocol":
                raise AppError(502, "stream_result_invalid", "Synthetic invalid interval")
            if failure_kind == "finalization":
                raise AppError(502, "stream_finalization_failed", "Synthetic finalization failure")
            if failure_kind == "session_expired":
                raise AppError(409, "stream_session_expired", "Synthetic session expiry")
            if failure_kind == "disconnect":
                raise WebSocketDisconnect(1001)
            raise RuntimeError("SYNTHETIC_PRIVATE_SENTINEL")

    failure_call = {}

    async def fake_service(self, name, **kwargs):
        if name == "authorize_stream_batch":
            return StreamBatchAuthorization(
                uuid4(), uuid4(), True, self.window_no, 0, 2, 2, 2, 2,
            )
        if name == "fail_transcript_stream":
            failure_call.update(kwargs)
            return SimpleNamespace(
                recovery_available=False,
                next_source_sample_offset=2,
                status=SimpleNamespace(value="failed"),
            )
        return None

    monkeypatch.setattr(live, "_same_origin", lambda _socket: True)
    monkeypatch.setattr(live, "_owner_call", fake_owner_call)
    monkeypatch.setattr(live, "verify_csrf_token", lambda **_kwargs: True)
    monkeypatch.setattr(live._Relay, "service", fake_service)
    monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", lambda *_a, **_k: ProviderContext())
    asyncio.run(live.deepgram_live(socket, uuid4()))

    error_messages = [message for message in socket.sent if message.get("type") == "error"]
    if public_code is None:
        assert not error_messages
    else:
        assert error_messages and error_messages[0]["code"] == public_code
    assert failure_call["error_code"] == persisted_code
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    failure_records = [record for record in records if record.get("event") == "recording_failed"]
    assert failure_records
    assert failure_records[0]["failure"] == persisted_code
    assert failure_records[0]["provider_category"] == provider_category
    assert failure_records[0]["diagnostic_id"]
    file_records = [json.loads(line) for line in diagnostic_path.read_text().splitlines()]
    file_failure_records = [record for record in file_records if record.get("event") == "recording_failed"]
    assert file_failure_records == failure_records
    if failure_kind == "unknown":
        assert "SYNTHETIC_PRIVATE_SENTINEL" not in output
    live.deepgram_diagnostics.reset_for_tests()


def test_checkpoint_wait_expiry_uses_safe_finalization_error(monkeypatch):
    from app.errors import AppError

    class ClosingProvider:
        def __init__(self):
            self.events = asyncio.Queue()

        async def receive(self, *, timeout_seconds=None):
            return await asyncio.wait_for(self.events.get(), timeout_seconds)

        async def send_finalize(self):
            return None

        async def send_keep_alive(self):
            return None

        async def send_close_stream(self):
            return None

    relay = live._Relay(_FakeSocket(), uuid4(), _started())
    relay.provider = ClosingProvider()
    relay.audio.extend(b"\x00\x00")
    relay.media_samples = 1
    monkeypatch.setattr(live, "CONTROL_WRITE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(live, "FINALIZE_ACK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(live, "CLOSE_DRAIN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(live, "PROVIDER_KEEPALIVE_INTERVAL_SECONDS", 0.005)

    async def scenario():
        relay.provider_task = asyncio.create_task(relay.provider.receive(timeout_seconds=60))
        with pytest.raises(AppError) as raised:
            await relay.checkpoint()
        assert raised.value.code == "stream_finalization_failed"
        await relay.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("dictation", [False, True])
def test_websocket_rejects_cross_owner_transcript(client, db_session, make_team, make_user, dictation):
    team = make_team(name="Deepgram WS owner team")
    owner = make_user(email="deepgram-ws-owner@example.com", password="password-1", team=team)
    other = make_user(email="deepgram-ws-other@example.com", password="password-2", team=team)
    transcript = _new_transcript(db_session, owner)
    assert client.post("/api/v1/auth/login", json={"email": other.email, "password": "password-2"}).status_code == 200
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/{"post-consultation-dictation/" if dictation else ""}deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


@pytest.mark.parametrize("dictation", [False, True])
def test_websocket_rejects_partial_onboarding_session(client, db_session, make_team, make_user, dictation):
    team = make_team(name="Deepgram WS onboarding team")
    owner = make_user(
        email="deepgram-ws-onboarding@example.com", password="password-1", team=team,
        onboarding_state=UserOnboardingState.pending_password_change, must_change_password=True,
    )
    transcript = _new_transcript(db_session, owner)
    response = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})
    assert response.status_code == 200
    assert response.json()["auth_level"] == "onboarding"
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/{"post-consultation-dictation/" if dictation else ""}deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


def test_websocket_full_session_requires_session_bound_csrf_before_start(monkeypatch, client, db_session, make_team, make_user):
    team = make_team(name="Deepgram WS CSRF team")
    owner = make_user(email="deepgram-ws-csrf@example.com", password="password-1", team=team)
    transcript = _new_transcript(db_session, owner)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    raw_session = client.cookies.get(SESSION_COOKIE_NAME)
    csrf = session_csrf_token(raw_session)

    from app.services import transcript_streaming

    monkeypatch.setattr(live, "_preflight", lambda *_args: live._Preflight(config=None, snapshot={}, api_key="synthetic"))
    monkeypatch.setattr(transcript_streaming, "start_transcript_stream", lambda *_args, **_kwargs: _started())
    monkeypatch.setattr(transcript_streaming, "finish_transcript_stream", lambda *_args, **_kwargs: SimpleNamespace(status="completed"))
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with ws_client.websocket_connect(
            f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(json.dumps({"type": "start", "csrf_token": csrf}))
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_text('{"type":"stop"}')
            assert websocket.receive_json() == {"type": "finalized"}


@pytest.mark.parametrize("dictation", [False, True])
def test_websocket_invalid_csrf_does_not_start_stream(monkeypatch, client, db_session, make_team, make_user, dictation):
    team = make_team(name="Deepgram WS invalid CSRF team")
    owner = make_user(email="deepgram-ws-invalid-csrf@example.com", password="password-1", team=team)
    transcript = _new_transcript(db_session, owner)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    started = []

    def should_not_start(*_args, **_kwargs):
        started.append(True)
        raise AssertionError("stream start must be blocked by CSRF")

    monkeypatch.setattr(live, "_preflight", should_not_start)
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with ws_client.websocket_connect(
            f"/api/v1/transcripts/{transcript.id}/{"post-consultation-dictation/" if dictation else ""}deepgram-stream",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_text('{"type":"start","csrf_token":"invalid-synthetic-token"}')
            response = websocket.receive_json()
            assert response["type"] == "error"
    assert started == []


def _login_full_ws(client, *, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    assert response.json()["auth_level"] == "full"
    return client.cookies.get(SESSION_COOKIE_NAME)


def test_websocket_rejects_pending_mfa_session(client, db_session, make_team, make_user, make_totp_method):
    team = make_team(name="Deepgram WS pending MFA team")
    user = make_user(
        email="deepgram-ws-pending-mfa@example.com", password="password-1", team=team,
        mfa_required=True, mfa_enabled=True,
    )
    make_totp_method(user=user)
    transcript = _new_transcript(db_session, user)
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": "password-1"})
    assert response.status_code == 200
    assert response.json()["auth_level"] == "pending_mfa"

    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


def test_websocket_rejects_expired_transcript_root(client, db_session, make_team, make_user):
    team = make_team(name="Deepgram WS expired team")
    owner = make_user(
        email="deepgram-ws-expired@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    transcript = _new_transcript(db_session, owner)
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})

    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


@pytest.mark.parametrize("identity", ["admin", "leader"])
def test_websocket_rejects_admin_or_team_leader_cross_owner_content(
    identity, client, db_session, make_team, make_user
):
    team = make_team(name=f"Deepgram WS {identity} cross-owner team")
    owner = make_user(
        email=f"deepgram-ws-content-owner-{identity}@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    transcript = _new_transcript(db_session, owner)
    if identity == "admin":
        actor = make_user(
            email="deepgram-ws-admin@example.com", password="password-2", is_system_admin=True,
            mfa_required=False, mfa_enabled=False,
        )
    else:
        actor = make_user(
            email="deepgram-ws-leader@example.com", password="password-2", team=team,
            team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False,
        )
    client.post("/api/v1/auth/login", json={"email": actor.email, "password": "password-2"})

    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


def test_session_revocation_before_new_audio_blocks_provider_dispatch(monkeypatch, client, db_session, make_team, make_user):
    team = make_team(name="Deepgram WS revoke before audio team")
    owner = make_user(
        email="deepgram-ws-revoke-audio@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    transcript = _new_transcript(db_session, owner)
    raw_session = _login_full_ws(client, email=owner.email, password="password-1")
    from app.services.auth import session_token_hash

    session = db_session.query(UserSession).filter_by(session_token_hash=session_token_hash(raw_session)).one()
    app = _route_test_app(db_session)
    provider_instances = []

    class ProviderContext:
        def __init__(self):
            self.media = []
            self.events = asyncio.Queue()
            provider_instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def send_media(self, payload):
            self.media.append(payload)

        async def send_keep_alive(self):
            return None

        async def send_finalize(self):
            return None

        async def send_close_stream(self):
            from app.services.deepgram_streaming import DeepgramMetadataEvent
            self.events.put_nowait(DeepgramMetadataEvent(duration=0.0, request_id="synthetic", channels=1))

        async def receive(self, *, timeout_seconds=None):
            if timeout_seconds is None:
                return await self.events.get()
            return await asyncio.wait_for(self.events.get(), timeout_seconds)

    monkeypatch.setattr(live, "_preflight", lambda *_args: live._Preflight(config=object(), snapshot={
        "config_id": str(uuid4()), "provider_preset": "deepgram", "adapter_kind": "generic_rest",
        "base_url": "https://api.deepgram.com", "model_name": "nova-3", "mip_opt_out": True,
        "extra_query_params": {"mip_opt_out": "true"},
    }, api_key="synthetic"))
    monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", lambda *_args, **_kwargs: ProviderContext())

    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with ws_client.websocket_connect(
            f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(json.dumps({"type": "start", "csrf_token": session_csrf_token(raw_session)}))
            assert websocket.receive_json()["type"] == "ready"
            session.status = SessionStatus.revoked
            session.revoked_at = utcnow()
            db_session.commit()
            websocket.send_bytes(live.FRAME_HEADER.pack(1, 0, 2) + b"\x01\x00\x02\x00")
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert response["code"] == "unauthorized"

    # The SDK context may be opened before the per-batch reauthorization, but
    # no user audio may cross the provider boundary after revocation.
    assert len(provider_instances) == 1
    assert provider_instances[0].media == []


def test_session_revocation_before_provider_result_blocks_text_delivery(monkeypatch, client, db_session, make_team, make_user):
    from app.services.deepgram_streaming import DeepgramResultEvent
    from app.services.auth import session_token_hash

    team = make_team(name="Deepgram WS revoke before result team")
    owner = make_user(
        email="deepgram-ws-revoke-result@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    transcript = _new_transcript(db_session, owner)
    raw_session = _login_full_ws(client, email=owner.email, password="password-1")
    session = db_session.query(UserSession).filter_by(session_token_hash=session_token_hash(raw_session)).one()
    app = _route_test_app(db_session)
    provider_instances = []

    class ProviderContext:
        def __init__(self):
            self.media = []
            self.events = asyncio.Queue()
            provider_instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def send_media(self, payload):
            self.media.append(payload)

        async def send_keep_alive(self):
            return None

        async def send_finalize(self):
            return None

        async def send_close_stream(self):
            from app.services.deepgram_streaming import DeepgramMetadataEvent
            self.events.put_nowait(DeepgramMetadataEvent(duration=0.000125, request_id="synthetic", channels=1))

        async def receive(self, *, timeout_seconds=None):
            if timeout_seconds is None:
                return await self.events.get()
            return await asyncio.wait_for(self.events.get(), timeout_seconds)

    monkeypatch.setattr(live, "_preflight", lambda *_args: live._Preflight(config=object(), snapshot={
        "config_id": str(uuid4()), "provider_preset": "deepgram", "adapter_kind": "generic_rest",
        "base_url": "https://api.deepgram.com", "model_name": "nova-3", "mip_opt_out": True,
        "extra_query_params": {"mip_opt_out": "true"},
    }, api_key="synthetic"))
    monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", lambda *_args, **_kwargs: ProviderContext())

    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with ws_client.websocket_connect(
            f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(json.dumps({"type": "start", "csrf_token": session_csrf_token(raw_session)}))
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(live.FRAME_HEADER.pack(1, 0, 2) + b"\x01\x00\x02\x00")
            assert websocket.receive_json()["type"] == "received"
            assert len(provider_instances) == 1
            assert provider_instances[0].media == [b"\x01\x00\x02\x00"]
            session.status = SessionStatus.revoked
            session.revoked_at = utcnow()
            db_session.commit()
            provider_instances[0].events.put_nowait(DeepgramResultEvent(
                text="synthetic private result", is_final=True, speech_final=True,
                from_finalize=False, start=0.0, duration=0.000125,
            ))
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert response["code"] == "unauthorized"
            assert "text" not in response


@pytest.mark.parametrize("dictation", [False, True])
def test_live_websocket_real_stream_lifecycle_saves_encrypted_text_and_settles_audio(monkeypatch, client, db_session, make_team, make_user, dictation):
    from sqlalchemy import select
    from app.services.deepgram_streaming import DeepgramMetadataEvent, DeepgramResultEvent, DeepgramStreamConfig
    from app.services.transcripts import transcript_draft_text

    team = make_team(name="Deepgram WS real lifecycle team")
    owner = make_user(
        email="deepgram-ws-real-lifecycle@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    transcript = _new_transcript(db_session, owner)
    if dictation:
        transcript.ingestion_mode = TranscriptIngestionMode.whole_file
        db_session.commit()
    original_status = transcript.status
    purposes = []
    raw_session = _login_full_ws(client, email=owner.email, password="password-1")
    provider_instances = []
    provider_order = []
    pcm = b"\x01\x00\x02\x00"

    class ProviderContext:
        def __init__(self, *_args, **_kwargs):
            self.media = []
            self.events = asyncio.Queue()
            self.events.put_nowait(DeepgramResultEvent(
                text="synthetic finalized phrase", is_final=True, speech_final=True,
                from_finalize=False, start=0.0, duration=2 / 16000,
            ))
            provider_instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def send_media(self, payload):
            self.media.append(payload)

        async def send_keep_alive(self):
            return None

        async def send_finalize(self):
            return None

        async def send_close_stream(self):
            provider_order.append("close_stream")
            self.events.put_nowait(DeepgramMetadataEvent(duration=2 / 16000, request_id="synthetic", channels=1))

        async def receive(self, *, timeout_seconds=None):
            if timeout_seconds is None:
                event = await self.events.get()
            else:
                event = await asyncio.wait_for(self.events.get(), timeout_seconds)
            if isinstance(event, DeepgramMetadataEvent):
                provider_order.append("metadata")
            return event

    snapshot = {
        "config_id": str(uuid4()), "provider_preset": "deepgram", "adapter_kind": "generic_rest",
        "base_url": "https://api.deepgram.com", "model_name": "nova-3", "language": "en",
        "mip_opt_out": True, "extra_query_params": {"mip_opt_out": "true"},
    }
    def fake_preflight(*args):
        purposes.append(args[-1])
        return live._Preflight(
        config=DeepgramStreamConfig(
            base_url=snapshot["base_url"], model_name=snapshot["model_name"],
            language=snapshot["language"], extra_query_params=snapshot["extra_query_params"],
        ), snapshot=snapshot, api_key="synthetic-server-only-key",
    )
    monkeypatch.setattr(live, "_preflight", fake_preflight)
    monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", ProviderContext)
    from app.services import transcript_streaming
    commit_window = transcript_streaming.commit_stream_window
    finish_stream = transcript_streaming.finish_transcript_stream

    def record_commit(*args, **kwargs):
        provider_order.append("commit")
        return commit_window(*args, **kwargs)

    def record_finish(*args, **kwargs):
        provider_order.append("finish")
        return finish_stream(*args, **kwargs)

    monkeypatch.setattr(transcript_streaming, "commit_stream_window", record_commit)
    monkeypatch.setattr(transcript_streaming, "finish_transcript_stream", record_finish)

    vault_audio_writes = []
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda *_args, **_kwargs: vault_audio_writes.append(True),
    )
    app = _route_test_app(db_session)
    messages = []
    with TestClient(app) as ws_client:
        ws_client.cookies.update(_ws_cookies(client))
        with ws_client.websocket_connect(
            f"/api/v1/transcripts/{transcript.id}/" + ("post-consultation-dictation/" if dictation else "") + "deepgram-stream",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(json.dumps({"type": "start", "csrf_token": session_csrf_token(raw_session)}))
            ready = websocket.receive_json()
            assert ready["type"] == "ready"
            websocket.send_bytes(live.FRAME_HEADER.pack(1, 0, 2) + pcm)
            while True:
                message = websocket.receive_json()
                messages.append(message)
                if message["type"] == ("dictation_preview" if dictation else "interim"):
                    break
                assert message["type"] == "received"
            if dictation:
                assert message == {"type": "dictation_preview", "window_no": 0, "confirmed_text": "synthetic finalized phrase", "interim_text": ""}
            else:
                assert message == {"type": "interim", "text": "synthetic finalized phrase"}
            websocket.send_text('{"type":"stop"}')
            while True:
                message = websocket.receive_json()
                messages.append(message)
                if message["type"] == "finalized":
                    break
                assert message["type"] == "window_committed"

    # Resolve the durable session by transcript and compare it with the ready ID.
    from app.models import TranscriptStreamSession
    stream_session = db_session.scalar(select(TranscriptStreamSession).where(
        TranscriptStreamSession.transcript_id == transcript.id,
    ))
    assert stream_session is not None
    assert ready["stream_id"] == str(stream_session.id)
    db_session.refresh(transcript)
    if dictation:
        assert purposes == [live.SttSelectionPurpose.post_consultation_dictation]
        assert transcript.status == original_status
        assert not transcript.current_draft_text_encrypted
        assert not transcript_draft_text(db_session, transcript=transcript)
        assert next(m for m in messages if m["type"] == "window_committed")["text"] == "synthetic finalized phrase"
    else:
        assert transcript.current_draft_text_encrypted
        assert "synthetic finalized phrase" not in transcript.current_draft_text_encrypted
        assert transcript_draft_text(db_session, transcript=transcript) == "synthetic finalized phrase"
    attempt = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == stream_session.id,
        ProviderAttempt.attempt_kind == AttemptKind.stt_deepgram_live_stream,
    ))
    assert attempt is not None
    assert attempt.status is AttemptStatus.settled
    assert attempt.outcome is AttemptOutcome.succeeded
    assert attempt.settlement_basis is ProviderSettlementBasis.stream_measured
    assert attempt.stream_received_samples == 2
    assert attempt.stream_authorized_samples == 2
    assert attempt.reserved_units == 1
    assert attempt.settled_units == 1
    assert attempt.measured_audio_seconds is None
    assert provider_instances[0].media == [pcm]
    assert provider_order.index("close_stream") < provider_order.index("metadata") < provider_order.index("commit") < provider_order.index("finish")
    assert [message["type"] for message in messages] == ["received", "dictation_preview" if dictation else "interim", "window_committed", "finalized"]
    assert vault_audio_writes == []


def test_dictation_relay_distinguishes_final_and_provisional(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started(), dictation=True)
        provider = _FakeProvider([_result(duration=0.5, text="confirmed"), _result(start=0.5, duration=0.5, text="provisional", final=False)])
        relay.provider = provider
        monkeypatch.setattr(live, "_owner_call", _async_noop_owner_call)
        relay.media_samples = 16000
        relay.provider_task = asyncio.create_task(provider.receive(timeout_seconds=1))
        await relay.provider_event()
        await relay.provider_event()
        assert socket.sent == [
            {"type": "dictation_preview", "window_no": 0, "confirmed_text": "confirmed", "interim_text": ""},
            {"type": "dictation_preview", "window_no": 0, "confirmed_text": "confirmed", "interim_text": "provisional"},
        ]
        await relay.close()
    asyncio.run(scenario())
