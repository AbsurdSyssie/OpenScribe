"""Provider-neutral generation request and transport primitives.

This module deliberately has no database or quota/work lifecycle dependencies.
Callers must resolve credentials and complete their own claim/submission/settlement
flow before invoking a provider.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.errors import AppError
from app.models import LlmAdapterKind
from app.provider_url_security import require_safe_provider_url
from app.services.llm_adapters.gemini_enterprise import (
    gemini_output_token_cap,
    gemini_request_snapshot,
    generate_gemini_text,
)
from app.services.llm_adapters.types import LlmGenerationRequest, LlmProviderSnapshot
from app.services.provider_errors import safe_provider_error_code
from app.services.provider_inspection import response_content_length_exceeds

if TYPE_CHECKING:
    from app.models import TeamLlmConfig


DEFAULT_OUTPUT_TOKEN_CAP = 1600
OLLAMA_STREAM_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
OLLAMA_STREAM_MAX_FRAGMENTS = 10_000
OLLAMA_STREAM_RAW_CHUNK_BYTES = 64 * 1024
_PROVIDER_CONFIG_ALLOWLIST = frozenset({"project_id", "location", "api_version", "capacity_mode"})
_PROVIDER_SNAPSHOT_KEYS = frozenset(
    {"llm_config_id", "provider_preset", "adapter_kind", "base_url", "model", "auth_mode", "provider_config"}
)
_SECRET_KEY_NAMES = frozenset(
    {"credential", "credentials", "bearer_token", "api_key", "vault_secret_ref", "secret_ref", "authorization", "password", "token", "secret"}
)


class GenerationUsage(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None
    provider_duration_ms: int | None


def provider_output_token_cap(*, adapter_kind: LlmAdapterKind, model: str, nominal_cap: int) -> int:
    if adapter_kind is LlmAdapterKind.gemini_enterprise:
        return gemini_output_token_cap(model, nominal_cap)
    return nominal_cap


def generation_request_snapshot(
    *,
    adapter_kind: LlmAdapterKind,
    model: str,
    user_id: UUID,
    system_message: str,
    user_message: str,
    output_token_cap: int | None = None,
    response_json_schema: dict[str, object] | None = None,
    temperature: float = 0.2,
) -> dict[str, object]:
    """Build the existing adapter-specific, non-credential request shape."""
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message},
    ]
    if adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
        return {
            "model": model,
            "temperature": temperature,
            "max_completion_tokens": output_token_cap or DEFAULT_OUTPUT_TOKEN_CAP,
            "user": str(user_id),
            "messages": messages,
        }
    if adapter_kind is LlmAdapterKind.ollama_chat:
        request_body: dict[str, object] = {"model": model, "stream": True, "messages": messages}
        if output_token_cap is not None:
            request_body["options"] = {"num_predict": output_token_cap}
        return request_body
    if adapter_kind is LlmAdapterKind.gemini_enterprise:
        nominal_cap = output_token_cap or DEFAULT_OUTPUT_TOKEN_CAP
        return gemini_request_snapshot(
            LlmGenerationRequest(
                model=model,
                system_message=system_message,
                user_message=user_message,
                temperature=temperature,
                max_output_tokens=provider_output_token_cap(
                    adapter_kind=adapter_kind, model=model, nominal_cap=nominal_cap
                ),
                expect_json=response_json_schema is not None,
                response_json_schema=response_json_schema,
            )
        )
    raise AppError(422, "business_rule_violation", "Unsupported LLM adapter", {"adapter_kind": adapter_kind.value})


def request_output_token_cap(request_body: Mapping[str, object]) -> int | None:
    direct = request_body.get("max_completion_tokens")
    if isinstance(direct, int):
        return direct
    options = request_body.get("options")
    ollama = options.get("num_predict") if isinstance(options, Mapping) else None
    if isinstance(ollama, int):
        return ollama
    config = request_body.get("config")
    nested = config.get("max_output_tokens") if isinstance(config, Mapping) else None
    return nested if isinstance(nested, int) else None


def gemini_request_from_snapshot(request_body: Mapping[str, object]) -> LlmGenerationRequest:
    config = request_body.get("config")
    contents = request_body.get("contents")
    if not isinstance(config, Mapping) or not isinstance(contents, list) or not contents:
        raise AppError(500, "llm_request_invalid", "Stored Gemini request is invalid")
    first_content = contents[0]
    parts = first_content.get("parts") if isinstance(first_content, Mapping) else None
    first_part = parts[0] if isinstance(parts, list) and parts else None
    user_message = first_part.get("text") if isinstance(first_part, Mapping) else None
    model = request_body.get("model")
    system_message = config.get("system_instruction")
    max_output_tokens = config.get("max_output_tokens")
    temperature = config.get("temperature")
    response_json_schema = config.get("response_json_schema")
    if response_json_schema is None:
        response_json_schema = config.get("response_schema")
    if not isinstance(model, str) or not isinstance(system_message, str) or not isinstance(user_message, str):
        raise AppError(500, "llm_request_invalid", "Stored Gemini request is invalid")
    if not isinstance(max_output_tokens, int) or not isinstance(temperature, (int, float)):
        raise AppError(500, "llm_request_invalid", "Stored Gemini request is invalid")
    if response_json_schema is not None and not isinstance(response_json_schema, dict):
        raise AppError(500, "llm_request_invalid", "Stored Gemini response schema is invalid")
    return LlmGenerationRequest(
        model=model,
        system_message=system_message,
        user_message=user_message,
        temperature=float(temperature),
        max_output_tokens=max_output_tokens,
        expect_json=config.get("response_mime_type") == "application/json",
        response_json_schema=response_json_schema,
    )


def build_provider_snapshot(
    *, config: TeamLlmConfig, model: str | None = None, base_url: str | None = None
) -> LlmProviderSnapshot:
    """Create an allowlisted provider snapshot without reading credentials."""
    resolved_base_url = (base_url if base_url is not None else config.base_url).strip()
    require_safe_provider_url(resolved_base_url)
    resolved_model = model if model is not None else config.model_name
    if not isinstance(resolved_model, str) or not resolved_model.strip():
        raise AppError(422, "business_rule_violation", "The LLM provider model is not configured")
    raw_provider_config = config.provider_config_json if isinstance(config.provider_config_json, dict) else {}
    provider_config = {
        key: value
        for key, value in raw_provider_config.items()
        if key in _PROVIDER_CONFIG_ALLOWLIST and isinstance(value, str)
    }
    return LlmProviderSnapshot(
        llm_config_id=str(config.id),
        provider_preset=str(getattr(config.provider_preset, "value", config.provider_preset)),
        adapter_kind=str(getattr(config.adapter_kind, "value", config.adapter_kind)),
        base_url=resolved_base_url,
        model=resolved_model.strip(),
        auth_mode=str(getattr(config.auth_mode, "value", config.auth_mode)),
        provider_config=provider_config,
    )


def validate_provider_snapshot_shape(snapshot: object) -> dict[str, object]:
    """Validate the exact non-secret JSON shape stored for provider work."""
    if not isinstance(snapshot, dict) or set(snapshot) != _PROVIDER_SNAPSHOT_KEYS:
        raise AppError(422, "llm_provider_snapshot_invalid", "The LLM provider snapshot is invalid")
    if any(not isinstance(snapshot[key], str) or not snapshot[key].strip() for key in _PROVIDER_SNAPSHOT_KEYS - {"provider_config"}):
        raise AppError(422, "llm_provider_snapshot_invalid", "The LLM provider snapshot is invalid")
    provider_config = snapshot["provider_config"]
    if not isinstance(provider_config, dict) or any(
        not isinstance(key, str)
        or key not in _PROVIDER_CONFIG_ALLOWLIST
        or not isinstance(value, str)
        or not value.strip()
        for key, value in provider_config.items()
    ):
        raise AppError(422, "llm_provider_snapshot_invalid", "The LLM provider snapshot is invalid")
    if snapshot_contains_secret_key(snapshot):
        raise AppError(422, "llm_provider_snapshot_invalid", "The LLM provider snapshot contains secret material")
    require_safe_provider_url(snapshot["base_url"])
    return snapshot


def snapshot_contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            (
                isinstance(key, str)
                and (key.casefold() in _SECRET_KEY_NAMES or key.casefold().endswith(("_token", "_secret", "_key")))
            )
            or snapshot_contains_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(snapshot_contains_secret_key(item) for item in value)
    return False


def validate_provider_snapshot_for_config(
    snapshot: object, *, config: TeamLlmConfig, expected_model: str | None = None
) -> dict[str, object]:
    """Require a stored snapshot to match its locked config exactly."""
    validated = validate_provider_snapshot_shape(snapshot)
    canonical = build_provider_snapshot(config=config, model=expected_model).to_dict()
    if validated != canonical:
        raise AppError(
            422,
            "llm_provider_snapshot_config_mismatch",
            "The LLM provider snapshot does not match its config",
        )
    return validated


def invoke_llm(
    *,
    snapshot: LlmProviderSnapshot,
    credential: object | None,
    request_body: dict[str, object],
) -> tuple[str, GenerationUsage]:
    """Perform one bounded provider request from a stable non-secret snapshot."""
    try:
        adapter_kind = LlmAdapterKind(snapshot.adapter_kind)
    except ValueError:
        raise AppError(
            422,
            "business_rule_violation",
            "Unsupported LLM adapter",
            {"adapter_kind": snapshot.adapter_kind},
        ) from None
    require_safe_provider_url(snapshot.base_url)
    if adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
        return invoke_openai_compatible(
            api_key=credential if isinstance(credential, str) else "",
            base_url=snapshot.base_url,
            request_body=request_body,
        )
    if adapter_kind is LlmAdapterKind.ollama_chat:
        return invoke_ollama(
            base_url=snapshot.base_url,
            bearer_token=credential if isinstance(credential, str) else None,
            request_body=request_body,
        )
    if adapter_kind is LlmAdapterKind.gemini_enterprise:
        return invoke_gemini(
            provider_config=snapshot.provider_config, credential=credential, request_body=request_body
        )
    raise AppError(422, "business_rule_violation", "Unsupported LLM adapter", {"adapter_kind": adapter_kind.value})


def invoke_openai_compatible(
    *,
    api_key: str,
    base_url: str,
    request_body: dict[str, object],
    client_factory: Callable[..., object] = OpenAI,
    timeout_error_cls: type[Exception] = APITimeoutError,
    connection_error_cls: type[Exception] = APIConnectionError,
    status_error_cls: type[Exception] = APIStatusError,
) -> tuple[str, GenerationUsage]:
    require_safe_provider_url(base_url)
    started = time.perf_counter()
    try:
        client = client_factory(api_key=api_key, base_url=base_url, max_retries=0)
        completion = client.chat.completions.create(**request_body)
    except Exception as exc:  # pragma: no cover - provider transport is isolated by unit tests
        raise translate_openai_generation_error(
            exc,
            timeout_error_cls=timeout_error_cls,
            connection_error_cls=connection_error_cls,
            status_error_cls=status_error_cls,
        ) from exc
    message = completion.choices[0].message if completion.choices else None
    generated_text = openai_message_content_text(getattr(message, "content", None) if message is not None else None)
    if not generated_text:
        raise AppError(502, "llm_generation_failed", "LLM generation returned no note text")
    usage = getattr(completion, "usage", None)
    return generated_text, generation_usage(
        input_tokens=getattr(usage, "prompt_tokens", None) if usage is not None else None,
        output_tokens=getattr(usage, "completion_tokens", None) if usage is not None else None,
        total_tokens=getattr(usage, "total_tokens", None) if usage is not None else None,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def openai_message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
        if part_type not in {None, "text", "output_text"}:
            continue
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(text, dict) and isinstance(text.get("value"), str):
            parts.append(text["value"])
    return "".join(parts).strip()


def invoke_ollama(
    *,
    base_url: str,
    bearer_token: str | None,
    request_body: dict[str, object],
    max_response_bytes: int = OLLAMA_STREAM_MAX_RESPONSE_BYTES,
    max_fragments: int = OLLAMA_STREAM_MAX_FRAGMENTS,
    raw_chunk_bytes: int = OLLAMA_STREAM_RAW_CHUNK_BYTES,
    stream_factory: Callable[..., object] = httpx.stream,
    http_error_cls: type[Exception] = httpx.HTTPError,
    timeout_exception_cls: type[Exception] = httpx.TimeoutException,
    connect_error_cls: type[Exception] = httpx.ConnectError,
    http_status_error_cls: type[Exception] = httpx.HTTPStatusError,
) -> tuple[str, GenerationUsage]:
    require_safe_provider_url(base_url)
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    started = time.perf_counter()
    generated_parts: list[str] = []
    final_payload: dict[str, object] | None = None
    response_bytes = 0
    response_fragments = 0

    def consume_frame(raw_frame: bytes) -> bool:
        nonlocal final_payload, response_fragments
        response_fragments += 1
        if response_fragments > max_fragments:
            raise AppError(502, "llm_provider_bad_response", "The LLM provider response exceeded the permitted size", {"provider_error_code": "response_too_large"})
        if not raw_frame.strip():
            return False
        payload = json.loads(raw_frame)
        if not isinstance(payload, dict):
            raise AppError(502, "llm_provider_bad_response", "The LLM provider returned an unreadable response", {"provider_error_code": "invalid_json"})
        message = payload.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content:
            generated_parts.append(content)
        if payload.get("done") is True:
            final_payload = payload
            return True
        return False

    try:
        with stream_factory(
            "POST", f"{base_url.rstrip('/')}/api/chat", headers=headers, json=request_body,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=60.0),
        ) as response:
            response.raise_for_status()
            if response_content_length_exceeds(response, max_bytes=max_response_bytes):
                raise AppError(502, "llm_provider_bad_response", "The LLM provider response exceeded the permitted size", {"provider_error_code": "response_too_large"})
            pending_frame = bytearray()
            stream_complete = False
            for raw_chunk in response.iter_raw(chunk_size=raw_chunk_bytes):
                if not isinstance(raw_chunk, bytes):
                    raise AppError(502, "llm_provider_bad_response", "The LLM provider returned an unreadable response", {"provider_error_code": "invalid_json"})
                response_bytes += len(raw_chunk)
                if response_bytes > max_response_bytes:
                    raise AppError(502, "llm_provider_bad_response", "The LLM provider response exceeded the permitted size", {"provider_error_code": "response_too_large"})
                pending_frame.extend(raw_chunk)
                while b"\n" in pending_frame:
                    index = pending_frame.index(b"\n")
                    raw_frame = bytes(pending_frame[:index])
                    del pending_frame[: index + 1]
                    if consume_frame(raw_frame):
                        stream_complete = True
                        break
                if stream_complete:
                    break
            if not stream_complete and pending_frame:
                consume_frame(bytes(pending_frame))
    except (http_error_cls, ValueError) as exc:  # pragma: no cover
        if isinstance(exc, ValueError):
            raise AppError(502, "llm_provider_bad_response", "The LLM provider returned an unreadable response", {"provider_error_code": "invalid_json"}) from exc
        raise translate_ollama_generation_error(
            exc,
            timeout_exception_cls=timeout_exception_cls,
            connect_error_cls=connect_error_cls,
            http_status_error_cls=http_status_error_cls,
        ) from exc
    payload = final_payload or {}
    generated_text = "".join(generated_parts).strip()
    if not generated_text:
        raise AppError(502, "llm_generation_failed", "LLM generation returned no note text")
    duration = payload.get("total_duration")
    return generated_text, generation_usage(
        input_tokens=payload.get("prompt_eval_count"), output_tokens=payload.get("eval_count"),
        duration_ms=int((time.perf_counter() - started) * 1000),
        provider_duration_ms=int(duration / 1_000_000) if isinstance(duration, int) else None,
    )


def invoke_gemini(*, provider_config: Mapping[str, object], credential: object | None, request_body: dict[str, object]) -> tuple[str, GenerationUsage]:
    result = generate_gemini_text(
        project_id=str(provider_config.get("project_id") or ""),
        location=str(provider_config.get("location") or ""),
        credentials=credential,
        capacity_mode=str(provider_config.get("capacity_mode") or "auto"),
        request=gemini_request_from_snapshot(request_body),
    )
    return result.text, generation_usage(
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, total_tokens=result.total_tokens,
        duration_ms=result.duration_ms, provider_duration_ms=result.provider_duration_ms,
    )


def _safe_provider_http_error_message(*, status_code: int | None, provider_error_code: str | None = None) -> str:
    if status_code == 400:
        return "The LLM provider rejected the generation request"
    if status_code in {401, 403}:
        return "The LLM provider rejected the configured credentials"
    if status_code == 404:
        if provider_error_code == "model_not_found" or (provider_error_code and "not found" in provider_error_code.lower()):
            return "The selected model is not available on the LLM provider"
        return "The requested LLM provider resource was not found"
    if status_code == 408:
        return "The LLM provider timed out"
    if status_code == 429:
        return "The LLM provider is rate limiting requests"
    if status_code is not None and 500 <= status_code <= 599:
        return "The LLM provider is temporarily unavailable"
    return "LLM generation failed"


def translate_openai_generation_error(
    exc: Exception,
    *,
    timeout_error_cls: type[Exception] = APITimeoutError,
    connection_error_cls: type[Exception] = APIConnectionError,
    status_error_cls: type[Exception] = APIStatusError,
) -> AppError:
    if isinstance(exc, timeout_error_cls):
        return AppError(504, "llm_provider_timeout", "The LLM provider timed out", {"provider_error_code": "timeout"})
    if isinstance(exc, connection_error_cls):
        return AppError(502, "llm_provider_unreachable", "Could not reach the LLM provider", {"provider_error_code": "connection_error"})
    if isinstance(exc, status_error_cls):
        status_code = getattr(exc, "status_code", None)
        provider_error_code = None
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str) and code.strip():
                provider_error_code = safe_provider_error_code(code, status_code=status_code)
        return AppError(502, "llm_generation_failed", _safe_provider_http_error_message(status_code=status_code, provider_error_code=provider_error_code), {"provider_http_status": status_code, "provider_error_code": provider_error_code})
    return AppError(502, "llm_generation_failed", "LLM generation failed")


def translate_ollama_generation_error(
    exc: Exception,
    *,
    timeout_exception_cls: type[Exception] = httpx.TimeoutException,
    connect_error_cls: type[Exception] = httpx.ConnectError,
    http_status_error_cls: type[Exception] = httpx.HTTPStatusError,
) -> AppError:
    if isinstance(exc, timeout_exception_cls):
        return AppError(504, "llm_provider_timeout", "The LLM provider timed out", {"provider_error_code": "timeout"})
    if isinstance(exc, connect_error_cls):
        return AppError(502, "llm_provider_unreachable", "Could not reach the LLM provider", {"provider_error_code": "connection_error"})
    if isinstance(exc, http_status_error_cls):
        status_code = exc.response.status_code if exc.response is not None else None
        provider_error_code = None
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error_value = payload.get("error")
            if isinstance(error_value, str) and error_value.strip():
                normalized = error_value.strip().lower()
                provider_error_code = safe_provider_error_code(
                    "model_not_found" if status_code == 404 and "model" in normalized and "not found" in normalized else error_value,
                    status_code=status_code,
                )
        return AppError(502, "llm_generation_failed", _safe_provider_http_error_message(status_code=status_code, provider_error_code=provider_error_code), {"provider_http_status": status_code, "provider_error_code": provider_error_code})
    return AppError(502, "llm_generation_failed", "LLM generation failed")


def generation_usage(*, input_tokens: object = None, output_tokens: object = None, total_tokens: object = None, duration_ms: object = None, provider_duration_ms: object = None) -> GenerationUsage:
    normalized_input = input_tokens if isinstance(input_tokens, int) else None
    normalized_output = output_tokens if isinstance(output_tokens, int) else None
    normalized_total = total_tokens if isinstance(total_tokens, int) else None
    if normalized_total is None and normalized_input is not None and normalized_output is not None:
        normalized_total = normalized_input + normalized_output
    return {"input_tokens": normalized_input, "output_tokens": normalized_output, "total_tokens": normalized_total, "duration_ms": duration_ms if isinstance(duration_ms, int) else None, "provider_duration_ms": provider_duration_ms if isinstance(provider_duration_ms, int) else None}
