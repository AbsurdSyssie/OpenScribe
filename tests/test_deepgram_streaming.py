from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from deepgram.listen.v1.types.listen_v1error import ListenV1Error
from deepgram.listen.v1.types.listen_v1metadata import ListenV1Metadata
from deepgram.listen.v1.types.listen_v1results import ListenV1Results
from deepgram.listen.v1.types.listen_v1speech_started import ListenV1SpeechStarted

from app.services import deepgram_streaming as streaming


class _FakeSocket:
    def __init__(self, incoming=()):
        self.sent = []
        self.incoming = list(incoming)

    async def send_media(self, message):
        self.sent.append(("media", message))

    async def send_keep_alive(self):
        self.sent.append(("keepalive", None))

    async def send_finalize(self):
        self.sent.append(("finalize", None))

    async def send_close_stream(self):
        self.sent.append(("close", None))

    async def recv(self):
        if not self.incoming:
            raise OSError("synthetic provider transport detail")
        return self.incoming.pop(0)


class _FakeContext:
    def __init__(self, socket):
        self.socket = socket
        self.exited = False

    async def __aenter__(self):
        connect = streaming.deepgram_listen_v1_client.websockets_client_connect
        assert getattr(connect, "_openscribe_private_websocket_logger", False)
        self.protocol_logger = connect._openscribe_protocol_logger
        return self.socket

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True
        return False


class _FakeClient:
    def __init__(self, *, socket, **kwargs):
        self.constructor_kwargs = kwargs
        self.socket = socket
        self.connect_kwargs = None
        self.context = _FakeContext(socket)
        self.listen = SimpleNamespace(v1=self)

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        return self.context


def _results(*, text="synthetic transcript", final=True, speech_final=False, from_finalize=False, start=0.0, duration=0.5):
    return ListenV1Results(
        channel_index=[0],
        duration=duration,
        start=start,
        channel={"alternatives": [{"transcript": text, "confidence": 1.0, "words": []}]},
        metadata={
            "request_id": "synthetic-request",
            "model_uuid": "synthetic-model-uuid",
            "model_info": {"name": "nova-3", "version": "synthetic", "arch": "nova-3"},
        },
        is_final=final,
        speech_final=speech_final,
        from_finalize=from_finalize,
    )


def _fake_sdk(monkeypatch, *, socket, context=None):
    clients = []

    def create_client(**kwargs):
        client = _FakeClient(socket=socket, **kwargs)
        if context is not None:
            client.context = context
        clients.append(client)
        return client

    monkeypatch.setattr(streaming, "AsyncDeepgramClient", create_client)
    return clients


def test_connect_uses_saved_eu_host_for_v1_and_forced_privacy_audio_options(monkeypatch):
    async def exercise():
        socket = _FakeSocket()
        clients = _fake_sdk(monkeypatch, socket=socket)
        config = streaming.DeepgramStreamConfig(
            base_url="https://api.eu.deepgram.com/",
            model_name="nova-3",
            language="en-GB",
            extra_query_params={"smart_format": "true", "mip_opt_out": "true", "endpointing": "300"},
        )
        async with streaming.DeepgramLiveStream(config, api_key="synthetic-key"):
            client = clients[0]
            assert client.constructor_kwargs["reconnect"] is False
            assert client.constructor_kwargs["stream_reconnection_enabled"] is False
            assert client.constructor_kwargs["max_retries"] == 0
            assert client.constructor_kwargs["logging"] == {"silent": True}
            assert client.constructor_kwargs["redact_credentials_in_logs"] is True
            environment = client.constructor_kwargs["environment"]
            assert environment.base == "https://api.eu.deepgram.com"
            assert environment.production == "wss://api.eu.deepgram.com"
            assert client.connect_kwargs == {
                "model": "nova-3",
                "language": "en-GB",
                "encoding": "linear16",
                "sample_rate": 16000,
                "channels": 1,
                "interim_results": True,
                "mip_opt_out": True,
                "smart_format": True,
                "endpointing": 300,
            }

    asyncio.run(exercise())


@pytest.mark.parametrize("model", ["nova-2", "nova-3-medical", "enhanced-phonecall", "base-general"])
def test_public_config_validation_accepts_listen_v1_streaming_models_without_provider_work(model):
    config = streaming.DeepgramStreamConfig(
        base_url="https://api.eu.deepgram.com",
        model_name=model,
        language="en-GB",
        extra_query_params={"smart_format": "true", "mip_opt_out": "true"},
    )

    assert streaming.validate_stream_config(config) is None


@pytest.mark.parametrize(
    "model,options",
    [
        ("flux-general-en", {"mip_opt_out": "true"}),  # Flux requires Listen V2.
        ("whisper-large", {"mip_opt_out": "true"}),  # Whisper Cloud is prerecorded only.
        ("nova-3", {"mip_opt_out": "true", "callback": "https://example.invalid"}),
        ("nova-3", {"mip_opt_out": "false"}),
    ],
)
def test_public_config_validation_rejects_unsupported_stream_model_or_saved_option(model, options):
    config = streaming.DeepgramStreamConfig(
        base_url="https://api.eu.deepgram.com", model_name=model,
        extra_query_params=options,
    )

    with pytest.raises(streaming.DeepgramStreamError) as raised:
        streaming.validate_stream_config(config)
    assert raised.value.category == "invalid_config"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.eu.deepgram.com/v1/listen",
        "https://api.eu.deepgram.com.evil.example",
        "http://api.eu.deepgram.com",
        "https://user:secret@api.eu.deepgram.com",
        "https://api.eu.deepgram.com:444",
        "https://api.eu.deepgram.com?region=global",
    ],
)
def test_base_url_is_limited_to_saved_deepgram_regions(base_url):
    with pytest.raises(streaming.DeepgramStreamError) as raised:
        streaming._environment_for_base_url(base_url)
    assert raised.value.category == "invalid_config"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    "options",
    [
        {"mip_opt_out": "false"},
        {"model": "different-model"},
        {"encoding": "wav"},
        {"sample_rate": "8000"},
        {"channels": "2"},
        {"interim_results": "false"},
        {"callback": "https://untrusted.example"},
        {"endpointing": "not-a-number"},
        {"utterance_end_ms": "50000"},
    ],
)
def test_unsupported_or_conflicting_saved_options_fail_closed(options):
    with pytest.raises(streaming.DeepgramStreamError) as raised:
        streaming._stream_options(options)
    assert raised.value.category == "invalid_config"


def test_global_host_is_preserved_without_fallback():
    environment = streaming._environment_for_base_url("https://api.deepgram.com")
    assert environment.base == "https://api.deepgram.com"
    assert environment.production == "wss://api.deepgram.com"


def test_word_metadata_must_match_source_case_but_may_vary_in_whitespace():
    source = "Synthetic reading 5 mS."
    words = (
        streaming.DeepgramWordEvent("Synthetic", "0", 0.0, 0.2),
        streaming.DeepgramWordEvent("reading", "0", 0.2, 0.4),
        streaming.DeepgramWordEvent("5", "0", 0.4, 0.5),
        streaming.DeepgramWordEvent("mS.", "0", 0.5, 0.7),
    )
    whitespace_variant = streaming.DeepgramResultEvent(
        text="Synthetic   reading 5 mS.", is_final=True, speech_final=True,
        from_finalize=False, start=0.0, duration=1.0, words=words,
    )
    case_variant = streaming.DeepgramResultEvent(
        text=source, is_final=True, speech_final=True,
        from_finalize=False, start=0.0, duration=1.0,
        words=(*words[:-1], streaming.DeepgramWordEvent("ms.", "0", 0.5, 0.7)),
    )

    accepted = streaming.deepgram_event_words_or_fallback(whitespace_variant)
    fallback = streaming.deepgram_event_words_or_fallback(case_variant)

    assert accepted[-1].speaker_id == "0"
    assert [word.text for word in fallback] == [source]
    assert fallback[0].speaker_id is None


def test_sdk_connector_wrapper_forces_bounded_response_buffers_and_preserves_other_options():
    connect = streaming.deepgram_listen_v1_client.websockets_client_connect
    external_logger = logging.getLogger("synthetic.external.websocket")

    async def build_connection():
        return connect(
            "wss://api.eu.deepgram.com/v1/listen",
            max_size=1024,
            max_queue=1,
            logger=external_logger,
            open_timeout=3,
        )

    connection = asyncio.run(build_connection())

    # websockets 15's legacy Connect does not expose these protocol controls
    # on the returned Connect object; they are held by its protocol factory.
    protocol_factory = connection._create_connection.args[0]
    protocol_options = protocol_factory.keywords
    assert protocol_options["max_size"] == 256 * 1024
    assert protocol_options["max_queue"] == 4
    assert protocol_options["logger"] is streaming._DEEPGRAM_WEBSOCKET_LOGGER
    assert connection.open_timeout == 3


def test_send_bounds_pcm_and_exposes_control_messages(monkeypatch):
    async def exercise():
        socket = _FakeSocket()
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            with pytest.raises(streaming.DeepgramStreamError):
                await client.send_keep_alive()
            await client.send_media(b"\x00\x00" * 320)
            await client.send_keep_alive()
            await client.send_keep_alive()
            await client.send_finalize()
            await client.send_media(b"\x01\x00")
            await client.send_close_stream()
            with pytest.raises(streaming.DeepgramStreamError):
                await client.send_media(b"\x01\x00")
        assert socket.sent == [
            ("media", b"\x00\x00" * 320),
            ("keepalive", None),
            ("keepalive", None),
            ("finalize", None),
            ("media", b"\x01\x00"),
            ("close", None),
        ]

    asyncio.run(exercise())


@pytest.mark.parametrize("pcm", [b"", b"\x00", b"x" * (streaming.MAX_PCM_FRAME_BYTES + 2)])
def test_invalid_pcm_frames_are_rejected_before_sdk_send(monkeypatch, pcm):
    async def exercise():
        socket = _FakeSocket()
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            with pytest.raises(streaming.DeepgramStreamError) as raised:
                await client.send_media(pcm)
        assert raised.value.category == "invalid_config"
        assert socket.sent == []

    asyncio.run(exercise())


def test_receive_returns_typed_results_and_metadata_and_skips_control_events(monkeypatch):
    async def exercise():
        result = _results(final=True, speech_final=True, from_finalize=True, start=4.0, duration=0.5)
        metadata = ListenV1Metadata.model_construct(duration=4.5, request_id="request-uuid", channels=1)
        socket = _FakeSocket([ListenV1SpeechStarted.model_construct(), result, metadata])
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            transcript = await client.receive()
            usage = await client.receive()
        assert transcript == streaming.DeepgramResultEvent(
            text="synthetic transcript",
            is_final=True,
            speech_final=True,
            from_finalize=True,
            start=4.0,
            duration=0.5,
        )
        assert usage == streaming.DeepgramMetadataEvent(duration=4.5, request_id="request-uuid", channels=1)

    asyncio.run(exercise())


def test_provider_error_and_transport_failure_hide_provider_text_and_traceback(monkeypatch, caplog):
    async def exercise():
        sentinel = "SENSITIVE_PROVIDER_SENTINEL"
        error = ListenV1Error.model_construct(code=sentinel, description=sentinel, message=sentinel)
        socket = _FakeSocket([error])
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            with pytest.raises(streaming.DeepgramStreamError) as raised:
                await client.receive()
        assert raised.value.category == "provider_error"
        assert sentinel not in str(raised.value)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None or raised.value.__suppress_context__

        socket = _FakeSocket()
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            with pytest.raises(streaming.DeepgramStreamError) as raised:
                await client.receive()
        assert raised.value.category == "connection_error"
        assert sentinel not in caplog.text

    caplog.set_level(logging.DEBUG)
    asyncio.run(exercise())


def test_sdk_protocol_frame_logging_uses_private_disabled_logger(monkeypatch, caplog):
    async def exercise():
        socket = _FakeSocket()
        clients = _fake_sdk(monkeypatch, socket=socket)
        original_connect = streaming.deepgram_listen_v1_client.websockets_client_connect
        external_logger = logging.getLogger("websockets.client")
        original_disabled = external_logger.disabled
        original_level = external_logger.level
        protocol_logger = streaming._DEEPGRAM_WEBSOCKET_LOGGER
        original_protocol_disabled = protocol_logger.disabled
        original_protocol_propagate = protocol_logger.propagate
        original_protocol_level = protocol_logger.level
        protocol_logger.disabled = False
        protocol_logger.propagate = True
        try:
            async with streaming.DeepgramLiveStream(
                streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
                api_key="synthetic-key",
            ):
                protocol_logger = clients[0].context.protocol_logger
                sentinel = "SYNTHETIC_TRANSCRIPT_FRAME_SENTINEL"
                protocol_logger.setLevel(logging.DEBUG)
                protocol_logger.debug("> %s", sentinel)
                assert protocol_logger.disabled is True
                assert protocol_logger.propagate is False
                assert external_logger.disabled is original_disabled
            assert streaming.deepgram_listen_v1_client.websockets_client_connect is original_connect
            assert sentinel not in caplog.text
            assert external_logger.disabled is original_disabled
            assert external_logger.level == original_level
        finally:
            protocol_logger.disabled = original_protocol_disabled
            protocol_logger.propagate = original_protocol_propagate
            protocol_logger.setLevel(original_protocol_level)

    caplog.set_level(logging.DEBUG)
    asyncio.run(exercise())


@pytest.mark.parametrize("message", [_results(text="x" * (streaming.MAX_RESULT_TEXT_CHARS + 1)), _results(start=float("nan")), _results(duration=-1)])
def test_malformed_results_are_rejected(message):
    with pytest.raises(streaming.DeepgramStreamError) as raised:
        streaming.DeepgramLiveStream._result_event(message)
    assert raised.value.category == "invalid_response"


def test_cancellation_is_not_reclassified_as_provider_error(monkeypatch):
    async def exercise():
        socket = _FakeSocket()
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            async def cancelled_send(_message):
                raise asyncio.CancelledError()

            socket.send_media = cancelled_send
            with pytest.raises(asyncio.CancelledError):
                await client.send_media(b"\x00\x00")

    asyncio.run(exercise())


def test_connection_and_receive_timeouts_are_controlled(monkeypatch):
    async def exercise():
        socket = _FakeSocket()
        class _StalledContext:
            async def __aenter__(self):
                await asyncio.Event().wait()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        _fake_sdk(monkeypatch, socket=socket, context=_StalledContext())
        config = streaming.DeepgramStreamConfig(
            "https://api.eu.deepgram.com",
            "nova-3",
            connect_timeout_seconds=0.1,
        )
        with pytest.raises(streaming.DeepgramStreamError) as raised:
            async with streaming.DeepgramLiveStream(config, api_key="synthetic-key"):
                pass
        assert raised.value.category == "timeout"

        socket = _FakeSocket()
        _fake_sdk(monkeypatch, socket=socket)
        async with streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig("https://api.eu.deepgram.com", "nova-3"),
            api_key="synthetic-key",
        ) as client:
            async def stalled_recv():
                await asyncio.Event().wait()

            socket.recv = stalled_recv
            with pytest.raises(streaming.DeepgramStreamError) as raised:
                await client.receive(timeout_seconds=0.1)
        assert raised.value.category == "timeout"

    asyncio.run(exercise())


def test_context_cleanup_is_bounded_and_clears_owned_connection_references(monkeypatch):
    async def exercise():
        socket = _FakeSocket()

        class StalledExit(_FakeContext):
            async def __aexit__(self, exc_type, exc, traceback):
                await asyncio.Event().wait()

        clients = _fake_sdk(monkeypatch, socket=socket, context=StalledExit(socket))
        monkeypatch.setattr(streaming, "MAX_CLEANUP_TIMEOUT_SECONDS", 0.05)
        client = streaming.DeepgramLiveStream(
            streaming.DeepgramStreamConfig(
                "https://api.eu.deepgram.com", "nova-3", operation_timeout_seconds=0.1,
            ),
            api_key="synthetic-key",
        )
        await client.__aenter__()

        with pytest.raises(streaming.DeepgramStreamError) as raised:
            await client.__aexit__(None, None, None)

        assert raised.value.category == "timeout"
        assert client._socket is None
        assert client._connection_context is None
        assert client._http_client is None
        assert clients[0].context.exited is False

    asyncio.run(exercise())
