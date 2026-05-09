from typing import Any
from urllib.parse import urljoin

import httpx

from app.errors import AppError


def fetch_openapi_document(
    *,
    base_url: str,
    candidate_paths: list[str],
    bearer_token: str | None,
    timeout_seconds: float = 10.0,
) -> tuple[dict[str, Any], str]:
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    for path in candidate_paths:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = urljoin(f"{base_url.rstrip('/')}/", normalized_path.lstrip("/"))
        try:
            response = httpx.get(url, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code in {401, 403}:
                raise AppError(401, "unauthorized", "OpenAPI document rejected the provided credentials") from exc
            continue
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("paths"), dict):
            return payload, normalized_path
    raise AppError(422, "business_rule_violation", "No valid OpenAPI document was found at the candidate paths")


def _resolve_openapi_pointer(document: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise AppError(422, "business_rule_violation", "Only local OpenAPI references are supported")
    current: Any = document
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise AppError(422, "business_rule_violation", "OpenAPI document contains an invalid local reference")
        current = current[part]
    if not isinstance(current, dict):
        raise AppError(422, "business_rule_violation", "OpenAPI reference did not resolve to an object")
    return current


def dereference_openapi_document(document: dict[str, Any]) -> dict[str, Any]:
    def dereference(value: Any) -> Any:
        if isinstance(value, dict):
            if "$ref" in value:
                return dereference(_resolve_openapi_pointer(document, str(value["$ref"])))
            return {key: dereference(item) for key, item in value.items()}
        if isinstance(value, list):
            return [dereference(item) for item in value]
        return value

    resolved = dereference(document)
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
    if path.startswith("$."):
        return _extract_jsonpath(payload, path)
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path")
        current = current[part]
    return current


def _extract_jsonpath(payload: Any, path: str) -> Any:
    current = payload
    expression = path[2:]
    for raw_part in expression.split("."):
        part = raw_part
        while "[" in part:
            key, rest = part.split("[", 1)
            if key:
                if not isinstance(current, dict) or key not in current:
                    raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path")
                current = current[key]
            index_text, part = rest.split("]", 1)
            if not isinstance(current, list):
                raise AppError(502, "provider_response_invalid", "Provider response did not contain the configured JSON path")
            current = current[int(index_text)]
            if part.startswith("."):
                part = part[1:]
        if part:
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
