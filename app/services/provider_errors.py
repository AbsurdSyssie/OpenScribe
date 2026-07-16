"""Content-safe provider error classification for durable metadata."""

from __future__ import annotations


_SAFE_PROVIDER_CODES = frozenset(
    {
        "authentication_error",
        "bad_request",
        "connection_error",
        "invalid_api_key",
        "invalid_request",
        "invalid_request_error",
        "model_not_found",
        "not_found",
        "permission_denied",
        "quota_exceeded",
        "rate_limit_exceeded",
        "timeout",
    }
)


def safe_provider_error_code(raw_code: object, *, status_code: int | None = None) -> str:
    """Return only controlled values; provider strings may echo private data."""
    if isinstance(raw_code, str):
        normalized = raw_code.strip().lower()
        if normalized in _SAFE_PROVIDER_CODES:
            return normalized
    status_codes = {
        400: "bad_request",
        401: "authentication_error",
        403: "permission_denied",
        404: "not_found",
        408: "timeout",
        409: "provider_conflict",
        422: "invalid_request",
        429: "rate_limit_exceeded",
    }
    if status_code in status_codes:
        return status_codes[status_code]
    if status_code is not None and 500 <= status_code <= 599:
        return "provider_server_error"
    return "http_status_error"
