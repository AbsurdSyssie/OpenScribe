"""Synthetic route and relay regressions for the Deepgram live boundary."""

from __future__ import annotations

import asyncio
import json
import logging
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


def test_short_pause_checkpoint_drains_then_commits_before_ack(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        order: list[str] = []
        provider = _FakeProvider([_result(duration=1.0), _metadata()], order=order)
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

        assert order.index("close_stream") < order.index("commit_stream_window")
        assert socket.sent[-1] == {"type": "window_committed", "window_no": 0, "next_sample_offset": 16_000}
        assert relay.audio == bytearray()
        assert relay.provider is None
        await relay.close()

    asyncio.run(scenario())


def test_silent_window_closes_and_commits_empty_text_without_transcript_span(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        provider = _FakeProvider([_metadata()])
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


def test_checkpoint_rotates_connection_without_replaying_committed_pcm(monkeypatch):
    async def scenario():
        socket = _FakeSocket()
        relay = live._Relay(socket, uuid4(), _started())
        providers = []

        def make_provider(*_args, **_kwargs):
            provider = _FakeProvider([_metadata()])
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
                return StreamWindowCommitted(relay.window_no, kwargs["source_end_sample_offset"], kwargs["forwarded_sample_count"])
            raise AssertionError(name)

        relay.service = service
        preflight = live._Preflight(config=object(), snapshot={}, api_key="synthetic")
        first_pcm = b"\x10\x00\x11\x00"
        second_pcm = b"\x20\x00\x21\x00"

        await relay.audio_frame(live.FRAME_HEADER.pack(1, 0, 2) + first_pcm, preflight)
        await relay.checkpoint()
        await relay.audio_frame(live.FRAME_HEADER.pack(1, 2, 2) + second_pcm, preflight)

        assert len(providers) == 2
        assert providers[0].media == [first_pcm]
        assert providers[1].media == [second_pcm]
        assert first_pcm not in providers[1].media
        assert second_pcm not in providers[0].media
        await relay.close()

    asyncio.run(scenario())


def test_pause_and_stop_drain_windows_rotate_provider_and_commit_before_receipt(monkeypatch):
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
            provider = _FakeProvider([_result(duration=0.000125), _metadata()], order=order)
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

        assert len(providers) == 2
        assert providers[0].media == [pcm1]
        assert providers[1].media == [pcm2]
        assert all(provider.controls == ["close_stream"] for provider in providers)
        assert len(commits) == 2
        assert order.count("finish") == 1
        assert order.count("ack") == 2
        assert order.index("close_stream") < order.index("commit") < order.index("ack")
        second_drain = order.index("close_stream", order.index("ack") + 1)
        second_commit = order.index("commit", order.index("ack") + 1)
        second_ack = order.index("ack", order.index("ack") + 1)
        assert second_drain < second_commit < second_ack < order.index("finish") < order.index("final_ack")
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


def test_websocket_rejects_wrong_origin_before_auth_or_provider_work(client, db_session):
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{uuid4()}/deepgram-stream",
                headers={"Origin": "https://attacker.example"},
            ):
                pass
    assert raised.value.code == 1008


def test_websocket_rejects_anonymous_even_with_valid_origin(db_session, make_team, make_user):
    team = make_team(name="Deepgram WS anonymous team")
    owner = make_user(email="deepgram-ws-anonymous@example.com", team=team)
    transcript = _new_transcript(db_session, owner)
    app = _route_test_app(db_session)
    with TestClient(app) as ws_client:
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect(
                f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


def test_route_logs_only_fixed_outcome_when_session_service_raises_sensitive_error(monkeypatch, caplog):
    from app.main import app

    route_path = "/api/v1/transcripts/{transcript_id}/deepgram-stream"
    if not any(getattr(route, "path", None) == route_path for route in app.routes):
        app.include_router(live.router)
    caplog.set_level(logging.INFO, logger=live.logger.name)

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

    assert "SYNTHETIC_PRIVATE_SENTINEL" not in caplog.text
    assert "deepgram_stream_closed" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_websocket_rejects_cross_owner_transcript(client, db_session, make_team, make_user):
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
                f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
                headers={"Origin": "http://testserver"},
            ):
                pass
    assert raised.value.code == 1008


def test_websocket_rejects_partial_onboarding_session(client, db_session, make_team, make_user):
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
                f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
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


def test_websocket_invalid_csrf_does_not_start_stream(monkeypatch, client, db_session, make_team, make_user):
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
            f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
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


def test_live_websocket_real_stream_lifecycle_saves_encrypted_text_and_settles_audio(monkeypatch, client, db_session, make_team, make_user):
    from sqlalchemy import select
    from app.services.deepgram_streaming import DeepgramMetadataEvent, DeepgramResultEvent, DeepgramStreamConfig
    from app.services.transcripts import transcript_draft_text

    team = make_team(name="Deepgram WS real lifecycle team")
    owner = make_user(
        email="deepgram-ws-real-lifecycle@example.com", password="password-1", team=team,
        mfa_required=False, mfa_enabled=False,
    )
    transcript = _new_transcript(db_session, owner)
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
    monkeypatch.setattr(live, "_preflight", lambda *_args: live._Preflight(
        config=DeepgramStreamConfig(
            base_url=snapshot["base_url"], model_name=snapshot["model_name"],
            language=snapshot["language"], extra_query_params=snapshot["extra_query_params"],
        ), snapshot=snapshot, api_key="synthetic-server-only-key",
    ))
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
            f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(json.dumps({"type": "start", "csrf_token": session_csrf_token(raw_session)}))
            ready = websocket.receive_json()
            assert ready["type"] == "ready"
            websocket.send_bytes(live.FRAME_HEADER.pack(1, 0, 2) + pcm)
            while True:
                message = websocket.receive_json()
                messages.append(message)
                if message["type"] == "interim":
                    break
                assert message["type"] == "received"
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
    assert [message["type"] for message in messages] == ["received", "interim", "window_committed", "finalized"]
    assert vault_audio_writes == []
