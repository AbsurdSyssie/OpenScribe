"""Gemini Enterprise adapter with content-safe error handling."""

from __future__ import annotations

import socket
import time
from collections.abc import Mapping
from typing import Any

import httpx
import requests
from google import genai
from google.auth import exceptions as google_auth_exceptions
from google.auth.credentials import Credentials
from google.genai import errors as genai_errors
from google.genai import types
from google.oauth2 import service_account

from app.errors import AppError
from app.services.llm_adapters.types import LlmGenerationRequest, LlmGenerationResult


_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_NON_TEXT_MODEL_MARKERS = ("embedding", "image", "tts", "live")
_GEMINI_MAX_OUTPUT_TOKENS = 30_000
_CAPACITY_HEADERS = {
    "auto": {},
    "shared": {"X-Vertex-AI-LLM-Request-Type": "shared"},
    "dedicated": {"X-Vertex-AI-LLM-Request-Type": "dedicated"},
}


def gemini_output_token_cap(model: str, nominal_cap: int) -> int:
    """Return fixed provider ceiling; saved length metadata is currently advisory."""
    del model, nominal_cap
    return _GEMINI_MAX_OUTPUT_TOKENS


def service_account_credentials_from_info(credential_info: Mapping[str, object]) -> Credentials:
    """Convert validated service-account JSON without echoing credential fields."""
    try:
        credentials = service_account.Credentials.from_service_account_info(
            dict(credential_info),
            scopes=[_CLOUD_PLATFORM_SCOPE],
        )
    except Exception as exc:
        raise AppError(
            401,
            "llm_invalid_credential",
            "The Google service-account credential could not be loaded.",
        ) from None
    return credentials


def build_gemini_client(
    *,
    project_id: str,
    location: str,
    credentials: Credentials | None = None,
    capacity_mode: str = "auto",
    api_version: str = "v1",
) -> genai.Client:
    """Build Gemini Enterprise client using explicit credentials or ADC."""
    if capacity_mode not in _CAPACITY_HEADERS:
        raise ValueError("Unsupported Gemini capacity mode")
    if api_version not in {"v1", "v1beta1"}:
        raise ValueError("Unsupported Gemini API version")
    headers = _CAPACITY_HEADERS[capacity_mode]
    http_options = types.HttpOptions(api_version=api_version, headers=headers or None)
    return genai.Client(
        enterprise=True,
        project=project_id,
        location=location,
        credentials=credentials,
        http_options=http_options,
    )


def discover_gemini_models(
    *,
    project_id: str,
    location: str,
    credentials: Credentials | None = None,
    capacity_mode: str = "auto",
) -> list[str]:
    model_names: set[str] = set()
    discovery_errors: list[Exception] = []
    for catalog_location in _gemini_catalog_locations(location):
        client = None
        try:
            client = build_gemini_client(
                project_id=project_id,
                location=catalog_location,
                credentials=credentials,
                capacity_mode=capacity_mode,
                # Publisher-model catalog listing is exposed on v1beta1.
                # Inference and token counting continue to use stable v1.
                api_version="v1beta1",
            )
            for model in client.models.list(config={"query_base": True}):
                name = getattr(model, "name", None)
                if not isinstance(name, str) or not name.strip():
                    continue
                normalized_name = name.strip()
                model_id = normalized_name.rsplit("/", 1)[-1].lower()
                # PublisherModel responses do not populate supported_actions
                # in google-genai. Keep Gemini text-generation model IDs only.
                if (
                    normalized_name.startswith("publishers/google/models/gemini-")
                    and not any(marker in model_id for marker in _NON_TEXT_MODEL_MARKERS)
                ):
                    model_names.add(normalized_name)
        except AppError:
            raise
        except Exception as exc:
            discovery_errors.append(exc)
        finally:
            _close_client(client)
    if model_names:
        return sorted(model_names)
    if discovery_errors:
        raise translate_gemini_error(discovery_errors[-1], operation="model_discovery") from None
    return []


def _gemini_catalog_locations(location: str) -> tuple[str, ...]:
    """Include jurisdictional catalog because regional catalogs can lag it."""
    normalized = location.strip().lower()
    if normalized.startswith("europe-"):
        return (normalized, "eu")
    if normalized.startswith(("us-", "northamerica-")):
        return (normalized, "us")
    return (normalized,)


def validate_gemini_model(
    *,
    project_id: str,
    location: str,
    model: str,
    credentials: Credentials | None = None,
    capacity_mode: str = "auto",
) -> int | None:
    """Validate model access without sending transcript-derived content."""
    client = None
    try:
        client = build_gemini_client(
            project_id=project_id,
            location=location,
            credentials=credentials,
            capacity_mode=capacity_mode,
        )
        response = client.models.count_tokens(
            model=model,
            contents="OpenScribe connection validation.",
        )
        value = getattr(response, "total_tokens", None)
        return value if isinstance(value, int) else None
    except AppError:
        raise
    except Exception as exc:
        raise translate_gemini_error(exc, operation="model_validation") from None
    finally:
        _close_client(client)


def generate_gemini_text(
    *,
    project_id: str,
    location: str,
    credentials: Credentials | None,
    request: LlmGenerationRequest,
    capacity_mode: str = "auto",
) -> LlmGenerationResult:
    client = None
    started = time.monotonic()
    try:
        client = build_gemini_client(
            project_id=project_id,
            location=location,
            credentials=credentials,
            capacity_mode=capacity_mode,
        )
        config = types.GenerateContentConfig(
            system_instruction=request.system_message,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            response_mime_type="application/json" if request.expect_json else None,
            response_schema=request.response_schema if request.expect_json else None,
            thinking_config=_gemini_thinking_config(request.model),
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=request.user_message)],
            )
        ]
        response = client.models.generate_content(
            model=request.model,
            contents=contents,
            config=config,
        )
        finish_reason = _finish_reason(response)
        if finish_reason == "MAX_TOKENS":
            raise AppError(
                502,
                "llm_generation_truncated",
                "Gemini exhausted its output-token limit before completing the response.",
                {"provider_finish_reason": finish_reason},
            )
        if finish_reason not in {None, "STOP"}:
            raise AppError(
                502,
                "llm_provider_bad_response",
                "Gemini stopped before completing the response.",
                {"provider_finish_reason": finish_reason},
            )
        text = _response_text(response)
        if not isinstance(text, str) or not text.strip():
            raise AppError(
                502,
                "llm_provider_bad_response",
                "Gemini returned an invalid generation response.",
            )
        usage = getattr(response, "usage_metadata", None)
        return LlmGenerationResult(
            text=text,
            input_tokens=_integer_attribute(usage, "prompt_token_count"),
            output_tokens=_integer_attribute(usage, "candidates_token_count"),
            total_tokens=_integer_attribute(usage, "total_token_count"),
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            provider_duration_ms=None,
            finish_reason=finish_reason,
        )
    except AppError:
        raise
    except Exception as exc:
        raise translate_gemini_error(exc, operation="generation") from None
    finally:
        _close_client(client)


def gemini_request_snapshot(request: LlmGenerationRequest) -> dict[str, object]:
    """Return non-credential request snapshot for encrypted persistence."""
    config: dict[str, object] = {
        "system_instruction": request.system_message,
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
    }
    if request.expect_json:
        config["response_mime_type"] = "application/json"
        if request.response_schema is not None:
            config["response_schema"] = request.response_schema
    return {
        "model": request.model,
        "contents": [
            {
                "role": "user",
                "parts": [{"text": request.user_message}],
            }
        ],
        "config": config,
    }


def _gemini_thinking_config(model: str) -> types.ThinkingConfig | None:
    """Keep hidden thinking from consuming note-output token budgets."""
    model_id = model.rsplit("/", 1)[-1].lower()
    if model_id.startswith(("gemini-3.1-pro", "gemini-3-pro")):
        return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
    if model_id.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
    if model_id.startswith(("gemini-2.5-flash", "gemini-2.5-flash-lite")):
        return types.ThinkingConfig(thinking_budget=0)
    if model_id.startswith("gemini-2.5-pro"):
        return types.ThinkingConfig(thinking_budget=128)
    return None


def translate_gemini_error(exc: Exception, *, operation: str = "generation") -> AppError:
    """Translate Google/transport exceptions to controlled, content-safe errors."""
    if isinstance(exc, (google_auth_exceptions.DefaultCredentialsError, google_auth_exceptions.RefreshError)):
        return AppError(401, "llm_invalid_credential", "Google credentials could not be loaded or refreshed.")
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, requests.Timeout)):
        return AppError(504, "llm_provider_timeout", "The Gemini request timed out.")
    if isinstance(
        exc,
        (
            socket.gaierror,
            ConnectionError,
            httpx.TransportError,
            requests.ConnectionError,
            google_auth_exceptions.TransportError,
        ),
    ):
        return AppError(502, "llm_provider_unreachable", "Gemini could not be reached.")

    status_code = _status_code(exc)
    reason_codes = _provider_reason_codes(exc)
    details = {"provider_http_status": status_code} if status_code is not None else None
    if status_code == 401:
        return AppError(401, "llm_invalid_credential", "Google credentials were rejected.", details)
    if "SERVICE_DISABLED" in reason_codes:
        return AppError(422, "llm_provider_api_disabled", "Required Google API is not enabled.", details)
    if status_code == 403:
        return AppError(403, "llm_permission_denied", "Google IAM denied this operation.", details)
    if status_code in {408, 504}:
        return AppError(504, "llm_provider_timeout", "The Gemini request timed out.", details)
    if status_code == 429:
        return AppError(429, "llm_provider_rate_limited", "Gemini capacity is temporarily unavailable.", details)
    if status_code == 404:
        if operation in {"model_validation", "generation"}:
            return AppError(422, "llm_model_unavailable", "The Gemini model is unavailable.", details)
        if operation == "model_discovery":
            # Managed publisher model listing is not exposed consistently by
            # the enterprise API. A 404 here does not prove location failure;
            # manual model validation via count_tokens is authoritative.
            return AppError(
                502,
                "llm_model_discovery_unavailable",
                "Gemini model discovery is unavailable for this endpoint.",
                details,
            )
        return AppError(422, "llm_location_unavailable", "The Gemini location is unavailable.", details)
    if status_code == 400 and operation in {"model_validation", "generation"}:
        return AppError(422, "llm_model_unavailable", "The Gemini model is unavailable.", details)
    if status_code == 400 and operation == "model_discovery":
        return AppError(422, "llm_location_unavailable", "The Gemini location is unavailable.", details)
    return AppError(502, "llm_generation_failed", "Gemini could not complete the request.", details)


def _close_client(client: object | None) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:
        # Cleanup failure must not mask provider result or expose SDK internals.
        pass


def _integer_attribute(value: object, name: str) -> int | None:
    attribute = getattr(value, name, None)
    return attribute if isinstance(attribute, int) else None


def _response_text(response: object) -> object:
    try:
        return getattr(response, "text", None)
    except Exception:
        raise AppError(
            502,
            "llm_provider_bad_response",
            "Gemini returned an invalid generation response.",
        ) from None


def _finish_reason(response: object) -> str | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    raw = getattr(reason, "value", reason)
    return str(raw) if raw is not None else None


def _status_code(exc: Exception) -> int | None:
    if isinstance(exc, genai_errors.APIError):
        value = getattr(exc, "code", None)
    else:
        value = getattr(exc, "status_code", None)
    return value if isinstance(value, int) else None


def _provider_reason_codes(exc: Exception) -> set[str]:
    """Read only structured reason identifiers; never retain provider messages."""
    response_json = getattr(exc, "response_json", None)
    if response_json is None and isinstance(exc, genai_errors.APIError):
        response_json = getattr(exc, "details", None)
    reasons: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            reason = value.get("reason")
            if isinstance(reason, str):
                reasons.add(reason.strip().upper())
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(response_json)
    return reasons
