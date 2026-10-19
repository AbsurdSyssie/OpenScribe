"""HTTPX hooks that cap provider response reads before SDK parsing."""

from __future__ import annotations

from collections.abc import Iterator
import httpx


DEFAULT_PROVIDER_RESPONSE_MAX_BYTES = 8 * 1024 * 1024


class ProviderResponseTooLarge(httpx.TransportError):
    """Raised as soon as a provider response exceeds its byte ceiling."""


class ProviderResponseEncodingUnsupported(httpx.TransportError):
    """Raised when HTTP content encoding could expand beyond the byte bound."""


class _BoundedStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, max_bytes: int) -> None:
        self._stream = stream
        self._max_bytes = max_bytes
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        total = 0
        for chunk in self._stream:
            total += len(chunk)
            if total > self._max_bytes:
                self.close()
                raise ProviderResponseTooLarge("provider response exceeded configured byte ceiling")
            yield chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stream.close()


def bounded_request_hook(request: httpx.Request) -> None:
    # Avoid compressed content where possible. The response hook still rejects
    # encoded bodies because providers may ignore this request preference.
    request.headers["accept-encoding"] = "identity"


def bounded_response_hook(
    response: httpx.Response,
    *,
    max_bytes: int = DEFAULT_PROVIDER_RESPONSE_MAX_BYTES,
) -> None:
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"", "identity"}:
        response.close()
        raise ProviderResponseEncodingUnsupported("encoded provider response is not accepted")
    length = response.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > max_bytes:
                response.close()
                raise ProviderResponseTooLarge("provider response exceeded configured byte ceiling")
        except ValueError:
            pass
    response.stream = _BoundedStream(response.stream, max_bytes)


def bounded_event_hooks(*, max_bytes: int = DEFAULT_PROVIDER_RESPONSE_MAX_BYTES) -> dict[str, list[object]]:
    """Return hooks that retain the HTTPX client's normal proxy/TLS behavior."""
    def check_response(response: httpx.Response) -> None:
        bounded_response_hook(response, max_bytes=max_bytes)

    return {"request": [bounded_request_hook], "response": [check_response]}


def is_provider_response_too_large(exc: BaseException) -> bool:
    return _has_cause(exc, ProviderResponseTooLarge)


def is_provider_response_encoding_unsupported(exc: BaseException) -> bool:
    return _has_cause(exc, ProviderResponseEncodingUnsupported)


def _has_cause(exc: BaseException, expected: type[BaseException]) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, expected):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
