"""Bounded, privacy-safe adapter for Deepgram Listen V1 streaming."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
import deepgram.listen.v1.client as deepgram_listen_v1_client
from websockets.exceptions import ConnectionClosed
from deepgram import AsyncDeepgramClient
from deepgram.environment import DeepgramClientEnvironment
from deepgram.listen.v1.types.listen_v1error import ListenV1Error
from deepgram.listen.v1.types.listen_v1metadata import ListenV1Metadata
from deepgram.listen.v1.types.listen_v1results import ListenV1Results
from deepgram.listen.v1.types.listen_v1speech_started import ListenV1SpeechStarted
from deepgram.listen.v1.types.listen_v1utterance_end import ListenV1UtteranceEnd

from app.services.provider_errors import safe_provider_error_code
from app.services.stt_presets import DEEPGRAM_EU_BASE_URL, DEEPGRAM_GLOBAL_BASE_URL
from app.stt_normalization import normalize_stt_language


SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
MAX_PCM_FRAME_BYTES = SAMPLE_RATE_HZ * CHANNELS * 2  # one second of signed 16-bit PCM
MAX_RESULT_TEXT_CHARS = 64_000
MAX_MODEL_CHARS = 128
MAX_LANGUAGE_CHARS = 64
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 10.0
MAX_OPERATION_TIMEOUT_SECONDS = 60.0
MAX_CLEANUP_TIMEOUT_SECONDS = 2.0

# Deepgram's generated Listen V1 connector doesn't expose websockets' per-
# connection logger argument. Keep protocol frame logging disabled on an
# adapter-owned logger without muting the shared websockets/app loggers.
_DEEPGRAM_WEBSOCKET_LOGGER = logging.getLogger("openscribe.deepgram.websocket")
_DEEPGRAM_WEBSOCKET_LOGGER.disabled = True
_DEEPGRAM_WEBSOCKET_LOGGER.propagate = False


def _secure_deepgram_websocket_logger() -> None:
    """Reassert privacy after application logging configuration changes."""
    _DEEPGRAM_WEBSOCKET_LOGGER.disabled = True
    _DEEPGRAM_WEBSOCKET_LOGGER.propagate = False


def _install_scoped_sdk_websocket_logger() -> None:
    """Install the private protocol logger on this SDK connector once."""
    original_connect = deepgram_listen_v1_client.websockets_client_connect
    if getattr(original_connect, "_openscribe_private_websocket_logger", False):
        return

    def connect_with_private_logger(*args: Any, **kwargs: Any) -> Any:
        # websockets logs complete frames at DEBUG. Always override any caller
        # logger on this provider connector so audio/transcript frames cannot
        # reach the shared application logging pipeline.
        _secure_deepgram_websocket_logger()
        kwargs["logger"] = _DEEPGRAM_WEBSOCKET_LOGGER
        # The SDK connector doesn't expose these controls at its own layer;
        # websockets 15 legacy client accepts both per connection.
        kwargs["max_size"] = 256 * 1024
        kwargs["max_queue"] = 4
        return original_connect(*args, **kwargs)

    connect_with_private_logger._openscribe_private_websocket_logger = True  # type: ignore[attr-defined]
    connect_with_private_logger._openscribe_protocol_logger = _DEEPGRAM_WEBSOCKET_LOGGER  # type: ignore[attr-defined]
    deepgram_listen_v1_client.websockets_client_connect = connect_with_private_logger


_install_scoped_sdk_websocket_logger()

_ALLOWED_HOSTS = frozenset(
    {
        urlsplit(DEEPGRAM_EU_BASE_URL).hostname,
        urlsplit(DEEPGRAM_GLOBAL_BASE_URL).hostname,
    }
)
_BOOL_OPTIONS = frozenset(
    {
        "dictation",
        "diarize",
        "numerals",
        "profanity_filter",
        "punctuate",
        "smart_format",
        "vad_events",
    }
)
_INT_OPTIONS = frozenset({"endpointing", "utterance_end_ms"})
_FORCED_OPTIONS = frozenset(
    {
        "channels",
        "encoding",
        "interim_results",
        "language",
        "mip_opt_out",
        "model",
        "sample_rate",
    }
)
_ERROR_CATEGORIES = frozenset(
    {
        "authentication_error",
        "connection_error",
        "invalid_config",
        "invalid_response",
        "provider_error",
        "provider_server_error",
        "rate_limit_exceeded",
        "timeout",
    }
)
_CLOSE_CLASSES = {
    1000: "normal", 1001: "going_away", 1002: "protocol_error",
    1003: "unsupported_data", 1006: "abnormal", 1008: "policy_violation",
    1011: "internal_error", 1012: "service_restart", 1013: "try_again_later",
}
_LISTEN_V1_MODEL = re.compile(r"^(?:nova|enhanced|base)(?:-[a-z0-9]+)*$", re.IGNORECASE)


@dataclass(frozen=True)
class DeepgramStreamConfig:
    """Saved provider settings needed for one server-side Listen V1 stream."""

    base_url: str
    model_name: str
    language: str | None = None
    extra_query_params: Mapping[str, str | bool | int] | None = None
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class DeepgramResultEvent:
    text: str
    is_final: bool
    speech_final: bool
    from_finalize: bool
    start: float
    duration: float
    words: tuple["DeepgramWordEvent", ...] = ()


@dataclass(frozen=True)
class DeepgramWordEvent:
    text: str
    speaker_id: str | None
    start: float | None
    end: float | None


def group_deepgram_words(words: list[DeepgramWordEvent] | tuple[DeepgramWordEvent, ...]) -> list[dict[str, object]]:
    """Group ordered word metadata into display turns without assuming binary speakers."""
    max_turn_chars = 20_000
    grouped: list[dict[str, object]] = []
    for word in words:
        if not isinstance(word, DeepgramWordEvent) or not word.text.strip():
            continue
        speaker = word.speaker_id
        start_ms = round(word.start * 1000) if word.start is not None else None
        end_ms = round(word.end * 1000) if word.end is not None else None
        current = grouped[-1] if grouped and grouped[-1]["speaker_id"] == speaker else None
        next_text = word.text.strip()
        if current is None:
            grouped.append({"text": next_text, "speaker_id": speaker, "start_ms": start_ms, "end_ms": end_ms})
            continue
        prior_text = str(current["text"])
        separator = "" if next_text[:1] in ",.!?;:%)]}" else " "
        if len(prior_text) + len(separator) + len(next_text) > max_turn_chars:
            grouped.append({"text": next_text, "speaker_id": speaker, "start_ms": start_ms, "end_ms": end_ms})
            continue
        current["text"] = prior_text + separator + next_text
        if current["start_ms"] is None:
            current["start_ms"] = start_ms
        if end_ms is not None:
            current["end_ms"] = end_ms
    return grouped


def deepgram_event_words_or_fallback(event: DeepgramResultEvent) -> list[DeepgramWordEvent]:
    """Use speaker words only when they cover the result text; otherwise keep it unattributed."""
    if not event.text.strip():
        return []
    words = list(event.words)
    if not words:
        return [DeepgramWordEvent(event.text.strip(), None, event.start, event.start + event.duration)]
    # Ignore whitespace differences only. Removing punctuation can make medically
    # distinct strings compare equal (for example 12.5 and 125, or 120/80 and
    # 120 80), so uncertain metadata must fall back to unattributed source text.
    normalize = lambda value: "".join(char.casefold() for char in value if not char.isspace())
    transcript_fingerprint = normalize(event.text)
    words_fingerprint = normalize(" ".join(word.text for word in words))
    if not transcript_fingerprint or transcript_fingerprint != words_fingerprint:
        return [DeepgramWordEvent(event.text.strip(), None, event.start, event.start + event.duration)]
    return words


@dataclass(frozen=True)
class DeepgramMetadataEvent:
    duration: float
    request_id: str
    channels: int


DeepgramStreamEvent = DeepgramResultEvent | DeepgramMetadataEvent


class DeepgramStreamError(Exception):
    """A provider-stream failure with a controlled, content-free category."""

    def __init__(self, category: str, *, close_class: str = "none"):
        self.category = category if category in _ERROR_CATEGORIES else "provider_error"
        self.close_class = close_class if close_class in set(_CLOSE_CLASSES.values()) | {"unknown", "none"} else "unknown"
        super().__init__(self.category)


def _safe_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _parse_bool_option(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1"}:
            return True
        if lowered in {"false", "0"}:
            return False
    raise DeepgramStreamError("invalid_config")


def _stream_options(extra: Mapping[str, str | bool | int] | None) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if extra is not None and not isinstance(extra, Mapping):
        raise DeepgramStreamError("invalid_config")
    for raw_name, value in (extra or {}).items():
        if not isinstance(raw_name, str):
            raise DeepgramStreamError("invalid_config")
        name = raw_name.strip().lower()
        if name == "mip_opt_out":
            if _parse_bool_option(name, value) is not True:
                raise DeepgramStreamError("invalid_config")
            continue
        if name in _FORCED_OPTIONS:
            expected: object = {
                "channels": CHANNELS,
                "encoding": "linear16",
                "interim_results": True,
                "language": None,
                "model": None,
                "sample_rate": SAMPLE_RATE_HZ,
            }[name]
            if name in {"channels", "sample_rate"}:
                if isinstance(value, bool):
                    matches = False
                elif isinstance(value, int):
                    matches = value == expected
                elif isinstance(value, str) and value.strip().isdigit():
                    matches = int(value.strip()) == expected
                else:
                    matches = False
            elif name in {"interim_results"}:
                matches = _parse_bool_option(name, value) is True
            elif name == "encoding":
                matches = isinstance(value, str) and value.strip().lower() == expected
            else:
                # Model and language are carried by dedicated frozen config fields.
                matches = False
            if not matches:
                raise DeepgramStreamError("invalid_config")
            continue
        if name in _BOOL_OPTIONS:
            options[name] = _parse_bool_option(name, value)
            continue
        if name in _INT_OPTIONS:
            if name == "endpointing" and isinstance(value, str) and value.strip().lower() == "false":
                options[name] = False
                continue
            if isinstance(value, bool):
                raise DeepgramStreamError("invalid_config") from None
            if isinstance(value, int):
                parsed = value
            elif isinstance(value, str) and value.strip().isdigit():
                parsed = int(value.strip())
            else:
                raise DeepgramStreamError("invalid_config") from None
            lower_bound = 0 if name == "endpointing" else 100
            upper_bound = 10_000
            if isinstance(value, bool) or not lower_bound <= parsed <= upper_bound:
                raise DeepgramStreamError("invalid_config")
            options[name] = parsed
            continue
        raise DeepgramStreamError("invalid_config")
    return options


def _environment_for_base_url(base_url: str) -> DeepgramClientEnvironment:
    if not isinstance(base_url, str) or len(base_url) > 256:
        raise DeepgramStreamError("invalid_config")
    try:
        parsed = urlsplit(base_url.strip())
        hostname = (parsed.hostname or "").lower()
        valid = (
            parsed.scheme == "https"
            and hostname in _ALLOWED_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443}
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
        hostname = ""
    if not valid:
        raise DeepgramStreamError("invalid_config")
    ws_origin = f"wss://{hostname}"
    return DeepgramClientEnvironment(
        base=f"https://{hostname}",
        production=ws_origin,
        agent="wss://agent.deepgram.com",
        agent_rest="https://agent.deepgram.com",
    )


def _validate_stream_config(config: DeepgramStreamConfig):
    """Validate saved Listen V1 eligibility without credentials or network work."""
    if not isinstance(config, DeepgramStreamConfig):
        raise DeepgramStreamError("invalid_config")
    model = config.model_name.strip() if isinstance(config.model_name, str) else ""
    if not model or len(model) > MAX_MODEL_CHARS or _LISTEN_V1_MODEL.fullmatch(model) is None:
        raise DeepgramStreamError("invalid_config")
    try:
        language = normalize_stt_language(config.language)
    except Exception:
        raise DeepgramStreamError("invalid_config") from None
    if language is not None and len(language) > MAX_LANGUAGE_CHARS:
        raise DeepgramStreamError("invalid_config")
    options = _stream_options(config.extra_query_params)
    environment = _environment_for_base_url(config.base_url)
    connect_timeout = config.connect_timeout_seconds
    operation_timeout = config.operation_timeout_seconds
    if (
        not isinstance(connect_timeout, (int, float))
        or isinstance(connect_timeout, bool)
        or not math.isfinite(connect_timeout)
        or not 0.1 <= connect_timeout <= MAX_OPERATION_TIMEOUT_SECONDS
        or not isinstance(operation_timeout, (int, float))
        or isinstance(operation_timeout, bool)
        or not math.isfinite(operation_timeout)
        or not 0.1 <= operation_timeout <= MAX_OPERATION_TIMEOUT_SECONDS
    ):
        raise DeepgramStreamError("invalid_config")
    return model, language, options, environment, float(connect_timeout), float(operation_timeout)


def validate_stream_config(config: DeepgramStreamConfig) -> None:
    """Raise a typed, content-free error when saved config cannot use Listen V1.

    This is safe for UI capability and preflight checks: it performs no
    credential resolution, provider construction, network call, or logging.
    """
    _validate_stream_config(config)


def _safe_sdk_error(exc: BaseException) -> DeepgramStreamError:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return DeepgramStreamError("timeout")
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        safe_code = safe_provider_error_code(None, status_code=status_code)
        if safe_code == "authentication_error":
            return DeepgramStreamError("authentication_error")
        if safe_code == "rate_limit_exceeded":
            return DeepgramStreamError("rate_limit_exceeded")
        if safe_code == "provider_server_error":
            return DeepgramStreamError("provider_server_error")
        return DeepgramStreamError("provider_error")
    if isinstance(exc, ConnectionClosed):
        received = getattr(exc, "rcvd", None)
        code = getattr(received, "code", None)
        close_class = "abnormal" if received is None else _CLOSE_CLASSES.get(code, "unknown")
        return DeepgramStreamError("connection_error", close_class=close_class)
    if isinstance(exc, (OSError, ConnectionError)):
        return DeepgramStreamError("connection_error")
    return DeepgramStreamError("provider_error")


class DeepgramLiveStream:
    """Async context manager exposing bounded media/control and typed events.

    The surrounding route owns the durable delivery claim and accounting. This
    adapter neither retries nor reconnects, persists content, or logs provider
    messages/exceptions.
    """

    def __init__(self, config: DeepgramStreamConfig, *, api_key: str):
        self.config = config
        self._api_key = api_key
        self._connection_context: Any | None = None
        self._socket: Any | None = None
        self._client: AsyncDeepgramClient | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._has_media = False
        self._close_sent = False

    async def __aenter__(self) -> DeepgramLiveStream:
        if not isinstance(self._api_key, str) or not self._api_key.strip():
            raise DeepgramStreamError("invalid_config")
        model, language, options, environment, connect_timeout, operation_timeout = _validate_stream_config(self.config)

        _secure_deepgram_websocket_logger()
        self._http_client = httpx.AsyncClient(timeout=operation_timeout, follow_redirects=False)
        try:
            # The SDK's HTTP transport is not used for Listen V1 WebSockets, but
            # it creates one as part of client construction; own and close it.
            self._client = AsyncDeepgramClient(
                api_key=self._api_key,
                environment=environment,
                httpx_client=self._http_client,
                timeout=operation_timeout,
                max_retries=0,
                stream_reconnection_enabled=False,
                max_stream_reconnection_attempts=0,
                reconnect=False,
                redact_credentials_in_logs=True,
                telemetry_opt_out=True,
                logging={"silent": True},
            )
            self._connection_context = self._client.listen.v1.connect(
                model=model,
                language=language,
                encoding="linear16",
                sample_rate=SAMPLE_RATE_HZ,
                channels=CHANNELS,
                interim_results=True,
                mip_opt_out=True,
                **options,
            )
            async with asyncio.timeout(connect_timeout):
                self._socket = await self._connection_context.__aenter__()
        except asyncio.CancelledError:
            await self._close_connection_generator()
            await self._close_http_client()
            raise
        except Exception as exc:
            await self._close_connection_generator()
            await self._close_http_client()
            safe_error = _safe_sdk_error(exc)
            raise safe_error from None
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        cleanup_timeout = min(self.config.operation_timeout_seconds, MAX_CLEANUP_TIMEOUT_SECONDS)
        cleanup_deadline = asyncio.get_running_loop().time() + cleanup_timeout
        cleanup_timed_out = False
        try:
            if self._connection_context is not None and self._socket is not None:
                try:
                    # Reserve one quarter of the shared cleanup budget for the
                    # HTTP client, even if the SDK websocket context stalls.
                    context_timeout = cleanup_timeout * 0.75
                    async with asyncio.timeout(context_timeout):
                        await self._connection_context.__aexit__(exc_type, exc, traceback)
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    cleanup_timed_out = True
                except Exception as cleanup_exc:
                    if exc_type is None:
                        raise _safe_sdk_error(cleanup_exc) from None
        finally:
            await self._close_http_client(deadline=cleanup_deadline)
            self._socket = None
            self._connection_context = None
        if cleanup_timed_out and exc_type is None:
            raise DeepgramStreamError("timeout") from None
        return False

    async def _close_http_client(self, *, deadline: float | None = None) -> None:
        client, self._http_client = self._http_client, None
        if client is not None:
            try:
                remaining = MAX_CLEANUP_TIMEOUT_SECONDS
                if deadline is not None:
                    remaining = min(remaining, deadline - asyncio.get_running_loop().time())
                    if remaining <= 0:
                        return
                async with asyncio.timeout(remaining):
                    await client.aclose()
            except Exception:
                # Cleanup must not expose SDK/httpx exception content.
                pass

    async def _close_connection_generator(self) -> None:
        """Close a connect context canceled before its socket was yielded."""
        context = self._connection_context
        if context is None or self._socket is not None:
            return
        # `asynccontextmanager` exposes its owned async generator as `gen`; the
        # SDK has no public close operation for a context whose __aenter__ was
        # canceled during the handshake.
        generator = getattr(context, "gen", None)
        if generator is not None:
            try:
                await generator.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    def _require_socket(self) -> Any:
        if self._socket is None:
            raise DeepgramStreamError("connection_error")
        return self._socket

    async def _run_operation(self, operation: Any) -> Any:
        try:
            async with asyncio.timeout(self.config.operation_timeout_seconds):
                return await operation
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            safe_error = _safe_sdk_error(exc)
            raise safe_error from None

    async def send_media(self, pcm: bytes) -> None:
        socket = self._require_socket()
        if self._close_sent or not isinstance(pcm, bytes) or not pcm or len(pcm) > MAX_PCM_FRAME_BYTES or len(pcm) % 2:
            raise DeepgramStreamError("invalid_config")
        await self._run_operation(socket.send_media(pcm))
        self._has_media = True

    async def send_keep_alive(self) -> None:
        if not self._has_media or self._close_sent:
            raise DeepgramStreamError("invalid_config")
        socket = self._require_socket()
        await self._run_operation(socket.send_keep_alive())

    async def send_finalize(self) -> None:
        if self._close_sent:
            raise DeepgramStreamError("invalid_config")
        socket = self._require_socket()
        await self._run_operation(socket.send_finalize())

    async def send_close_stream(self) -> None:
        if self._close_sent:
            raise DeepgramStreamError("invalid_config")
        socket = self._require_socket()
        await self._run_operation(socket.send_close_stream())
        self._close_sent = True

    async def receive(self, *, timeout_seconds: float | None = None) -> DeepgramStreamEvent:
        socket = self._require_socket()
        timeout_value = timeout_seconds if timeout_seconds is not None else self.config.operation_timeout_seconds
        if not math.isfinite(timeout_value) or not 0.1 <= timeout_value <= MAX_OPERATION_TIMEOUT_SECONDS:
            raise DeepgramStreamError("invalid_config")
        while True:
            try:
                async with asyncio.timeout(timeout_value):
                    message = await socket.recv()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                safe_error = _safe_sdk_error(exc)
                raise safe_error from None
            if isinstance(message, ListenV1Results):
                return self._result_event(message)
            if isinstance(message, ListenV1Metadata):
                return self._metadata_event(message)
            if isinstance(message, ListenV1Error):
                safe_code = safe_provider_error_code(message.code)
                if safe_code == "authentication_error":
                    raise DeepgramStreamError("authentication_error")
                if safe_code == "rate_limit_exceeded":
                    raise DeepgramStreamError("rate_limit_exceeded")
                raise DeepgramStreamError("provider_error")
            if isinstance(message, (ListenV1SpeechStarted, ListenV1UtteranceEnd)):
                # The owning route currently needs transcript and accounting
                # events only; these protocol notices carry no durable content.
                continue
            raise DeepgramStreamError("invalid_response")

    @staticmethod
    def _result_event(message: ListenV1Results) -> DeepgramResultEvent:
        start = _safe_number(message.start)
        duration = _safe_number(message.duration)
        try:
            alternatives = message.channel.alternatives
            alternative = alternatives[0] if alternatives else None
            text = alternative.transcript if alternative else ""
            raw_words = getattr(alternative, "words", None) or []
        except (AttributeError, IndexError, TypeError):
            raise DeepgramStreamError("invalid_response") from None
        words: list[DeepgramWordEvent] = []
        for word in raw_words:
            if not isinstance(word, Mapping):
                word = getattr(word, "model_dump", lambda: {})()
            if not isinstance(word, Mapping):
                continue
            word_text = word.get("punctuated_word") or word.get("word")
            if not isinstance(word_text, str) or not word_text:
                continue
            raw_speaker = word.get("speaker")
            speaker_id = None if raw_speaker is None else str(raw_speaker)
            if speaker_id is not None and (len(speaker_id) > 64 or not speaker_id):
                speaker_id = None
            word_start = _safe_number(word.get("start"))
            word_end = _safe_number(word.get("end"))
            if word_start is not None and word_end is not None and word_end < word_start:
                word_start = word_end = None
            words.append(DeepgramWordEvent(word_text, speaker_id, word_start, word_end))
            if len(words) > 4096:
                words.clear()
                break
        if (
            start is None
            or duration is None
            or not isinstance(text, str)
            or len(text) > MAX_RESULT_TEXT_CHARS
            or not isinstance(message.is_final, (bool, type(None)))
            or not isinstance(message.speech_final, (bool, type(None)))
            or not isinstance(message.from_finalize, (bool, type(None)))
        ):
            raise DeepgramStreamError("invalid_response")
        return DeepgramResultEvent(
            text=text,
            is_final=bool(message.is_final),
            speech_final=bool(message.speech_final),
            from_finalize=bool(message.from_finalize),
            start=start,
            duration=duration,
            words=tuple(words),
        )

    @staticmethod
    def _metadata_event(message: ListenV1Metadata) -> DeepgramMetadataEvent:
        duration = _safe_number(message.duration)
        if (
            duration is None
            or message.channels != CHANNELS
            or not isinstance(message.request_id, str)
            or not message.request_id
            or len(message.request_id) > 128
        ):
            raise DeepgramStreamError("invalid_response")
        return DeepgramMetadataEvent(duration=duration, request_id=message.request_id, channels=CHANNELS)
