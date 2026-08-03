import json
from typing import Any
from urllib.parse import urljoin

import httpx
from jsonpath_ng import parse as parse_jsonpath
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from prance import ResolvingParser
from prance.util.resolver import RESOLVE_INTERNAL

from app.errors import AppError
from app.provider_url_security import require_safe_provider_url

OPENAPI_DOCUMENT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderResponseTooLargeError(ValueError):
    """Raised before an untrusted provider response exceeds its memory budget."""


def response_content_length_exceeds(response: httpx.Response, *, max_bytes: int) -> bool:
    raw_content_length = response.headers.get("content-length")
    if raw_content_length is None:
        return False
    try:
        return int(raw_content_length) > max_bytes
    except ValueError:
        return False


def read_limited_httpx_response(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read an HTTP response without retaining more than ``max_bytes`` in memory."""
    if response_content_length_exceeds(response, max_bytes=max_bytes):
        raise ProviderResponseTooLargeError("provider response exceeds configured limit")

    chunks: list[bytes] = []
    total_bytes = 0
    for chunk in response.iter_bytes():
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise ProviderResponseTooLargeError("provider response exceeds configured limit")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_openapi_document(
    *,
    base_url: str,
    candidate_paths: list[str],
    bearer_token: str | None,
    timeout_seconds: float = 10.0,
) -> tuple[dict[str, Any], str]:
    require_safe_provider_url(base_url)
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    for path in candidate_paths:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = urljoin(f"{base_url.rstrip('/')}/", normalized_path.lstrip("/"))
        try:
            with httpx.stream("GET", url, headers=headers, timeout=timeout_seconds) as response:
                response.raise_for_status()
                payload = json.loads(read_limited_httpx_response(response, max_bytes=OPENAPI_DOCUMENT_MAX_RESPONSE_BYTES))
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code in {401, 403}:
                raise AppError(401, "unauthorized", "OpenAPI document rejected the provided credentials") from exc
            continue
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("paths"), dict):
            _validate_openapi_document(payload)
            return payload, normalized_path
    raise AppError(422, "business_rule_violation", "No valid OpenAPI document was found at the candidate paths")


def _validate_openapi_document(document: dict[str, Any]) -> None:
    try:
        validate(document)
    except (OpenAPIValidationError, OpenAPISpecValidatorError) as exc:
        raise AppError(422, "business_rule_violation", "OpenAPI document failed schema validation") from exc


def dereference_openapi_document(document: dict[str, Any]) -> dict[str, Any]:
    _validate_openapi_document(document)
    try:
        parser = ResolvingParser(
            spec_string=json.dumps(document),
            lazy=True,
            strict=False,
            resolve_types=RESOLVE_INTERNAL,
        )
        parser.parse()
    except Exception as exc:
        raise AppError(422, "business_rule_violation", "OpenAPI document references could not be resolved") from exc
    resolved = parser.specification
    if not isinstance(resolved, dict) or not isinstance(resolved.get("paths"), dict):
        raise AppError(422, "business_rule_violation", "OpenAPI document did not look like OpenAPI JSON")
    return resolved


def operation_request_schema(document: dict[str, Any], operation: dict[str, Any], media_type_family: str) -> dict[str, Any] | None:
    request_body = operation.get("requestBody") or {}
    content = request_body.get("content") or {} if isinstance(request_body, dict) else {}
    for media_type, media in content.items():
        if media_type_family in media_type and isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    return None


def operation_response_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return None
    for status_code in ("200", "201", "202", "default"):
        response = responses.get(status_code)
        if not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        if not isinstance(content, dict):
            continue
        for media_type, media in content.items():
            if "json" in media_type and isinstance(media, dict) and isinstance(media.get("schema"), dict):
                return media["schema"]
    return None


def extract_json_path(payload: Any, path: str) -> Any:
    if path.startswith("$"):
        try:
            matches = parse_jsonpath(path).find(payload)
        except Exception as exc:
            raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path") from exc
        if not matches:
            raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path")
        values = [match.value for match in matches]
        return values[0] if len(values) == 1 else values
    current = payload
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path")
            current = current[index]
            continue
        if not isinstance(current, dict) or part not in current:
            raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path")
        current = current[part]
    return current


def display_default_from_schema_property(prop: dict[str, Any]) -> str | None:
    for key in ("default", "example"):
        value = prop.get(key)
        if value is not None:
            return str(value)
    enum_values = prop.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return str(enum_values[0])
    return None
