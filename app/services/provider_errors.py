"""Content-safe provider error classification for durable metadata."""

from __future__ import annotations

from http import HTTPStatus


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


_PROVIDER_ERROR_REASONS = {
    "invalid_json": "the provider returned invalid JSON",
    "invalid_response": "the provider returned an unreadable response",
    "response_too_large": "the provider response exceeded the allowed size",
    "unsupported_content_encoding": "the provider used an unsupported response encoding",
    "authentication_error": "the provider rejected the credentials",
    "bad_request": "the provider rejected the request",
    "connection_error": "OpenScribe could not establish a provider connection",
    "invalid_api_key": "the provider rejected the API key",
    "invalid_request": "the provider rejected the request",
    "invalid_request_error": "the provider rejected the request",
    "model_not_found": "the provider could not find the requested model",
    "not_found": "the provider could not find the requested resource",
    "permission_denied": "the provider denied access",
    "provider_server_error": "the provider reported a server error",
    "quota_exceeded": "the provider quota is exhausted",
    "rate_limit_exceeded": "the provider rate limit was reached",
    "timeout": "the provider request timed out",
}


def safe_provider_error_diagnostic(
    *, status_code: object = None, provider_error_code: object = None
) -> str | None:
    """Build user-facing diagnostics from HTTP status and allowlisted codes only."""
    status: int | None = None
    if type(status_code) is int and 400 <= status_code <= 599:
        status = status_code

    raw_code = provider_error_code.strip().lower() if isinstance(provider_error_code, str) else ""
    diagnostic_codes = {"invalid_json", "invalid_response", "response_too_large", "unsupported_content_encoding"}
    code = raw_code if raw_code in diagnostic_codes else safe_provider_error_code(raw_code, status_code=status)
    reason = _PROVIDER_ERROR_REASONS.get(code)
    if status is not None:
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = "HTTP error"
        suffix = f" ({code}: {reason})" if reason else ""
        return f"Provider returned HTTP {status} {phrase}{suffix}."

    if raw_code not in _SAFE_PROVIDER_CODES and raw_code not in diagnostic_codes or reason is None:
        return None
    if code == "connection_error":
        return f"No upstream HTTP response was received; {reason} ({code})."
    return f"{reason[0].upper()}{reason[1:]} ({code})."
