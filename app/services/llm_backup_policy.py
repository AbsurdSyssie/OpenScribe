"""Safe, provider-neutral policy helpers for a single LLM backup attempt.

This module is deliberately independent of persistence and credentials. Callers
must establish their own lifecycle/quota boundary before invoking a provider.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from uuid import UUID

from app.errors import AppError
from app.models import LlmAdapterKind


BACKUP_FAILURE_DETAIL = "backup_failure_class"
PROVIDER_FAILURE = "provider_failure"
OUTPUT_VALIDATION_FAILURE = "output_validation_failure"
TEMPORARY_CAPACITY_EXHAUSTED = "temporary_capacity_exhausted"
PROVIDER_QUOTA_BLOCKED = "provider_quota_blocked"
_ELIGIBLE_CLASSES = frozenset({PROVIDER_FAILURE, OUTPUT_VALIDATION_FAILURE, TEMPORARY_CAPACITY_EXHAUSTED})
_PROVIDER_FAILURE_CODES = frozenset({
    "llm_invalid_credential", "llm_provider_timeout", "llm_provider_unreachable",
    "llm_provider_rate_limited", "llm_generation_failed", "llm_generation_truncated",
    "llm_provider_bad_response", "llm_model_unavailable", "llm_permission_denied",
    "llm_provider_api_disabled", "llm_provider_bad_request", "llm_location_unavailable",
})
_OUTPUT_VALIDATION_CODES = frozenset({
    "llm_generation_invalid_json", "llm_generation_truncated", "llm_provider_bad_response",
    "hallucination_check_invalid_response", "consultation_split_analysis_invalid",
    "consultation_split_generation_invalid", "consultation_split_generation_invalid_output",
    "consultation_split_verification_invalid",
    "template_suggestion_invalid_response",
})


def tag_provider_failure(error: AppError) -> AppError:
    """Mark a normalized provider error without copying provider response text."""
    details = dict(error.details or {})
    if details.get(BACKUP_FAILURE_DETAIL) in _ELIGIBLE_CLASSES | {PROVIDER_QUOTA_BLOCKED}:
        return error
    status = details.get("provider_http_status")
    if status == 429:
        details[BACKUP_FAILURE_DETAIL] = (
            TEMPORARY_CAPACITY_EXHAUSTED
            if details.get("temporary_capacity") is True
            else PROVIDER_QUOTA_BLOCKED
        )
    else:
        details[BACKUP_FAILURE_DETAIL] = PROVIDER_FAILURE
    error.details = details
    return error


def tag_output_validation_failure(error: AppError) -> AppError:
    """Mark an explicit provider-output validation error for the caller."""
    details = dict(error.details or {})
    details[BACKUP_FAILURE_DETAIL] = OUTPUT_VALIDATION_FAILURE
    error.details = details
    return error


def can_failover(error: AppError) -> bool:
    """Return true only for explicitly classified provider/output failures."""
    failure_class = (error.details or {}).get(BACKUP_FAILURE_DETAIL)
    if error.code in {
        "quota_disabled", "quota_exceeded", "llm_attempt_inactive",
        "llm_execution_context_unsupported", "llm_request_invalid",
        "llm_provider_snapshot_invalid", "llm_provider_snapshot_config_mismatch",
        "llm_provider_endpoint_blocked",
    }:
        return False
    if (error.details or {}).get("provider_http_status") == 429:
        return failure_class == TEMPORARY_CAPACITY_EXHAUSTED and error.code in _PROVIDER_FAILURE_CODES
    if failure_class == OUTPUT_VALIDATION_FAILURE:
        return error.code in _OUTPUT_VALIDATION_CODES
    if failure_class == PROVIDER_FAILURE:
        return error.code in _PROVIDER_FAILURE_CODES
    return failure_class == TEMPORARY_CAPACITY_EXHAUSTED and error.code in _PROVIDER_FAILURE_CODES


def backup_is_distinct(
    *, primary_config_id: UUID | str | None, primary_model: str | None, backup_provider: object,
) -> bool:
    """Reject an identical provider/model pair while allowing a model switch."""
    backup_config_id = getattr(backup_provider, "llm_config_id", None)
    backup_model = getattr(backup_provider, "model", None)
    if isinstance(backup_provider, Mapping):
        backup_config_id = backup_provider.get("llm_config_id", backup_config_id)
        backup_model = backup_provider.get("model", backup_model)
    return not (
        primary_config_id is not None
        and str(primary_config_id) == str(backup_config_id)
        and isinstance(primary_model, str)
        and isinstance(backup_model, str)
        and primary_model.strip() == backup_model.strip()
    )


def remap_generation_request(
    request_body: Mapping[str, object],
    *,
    source_adapter_kind: LlmAdapterKind,
    target_adapter_kind: LlmAdapterKind,
    target_model: str,
    user_id: UUID,
    output_token_cap: int | None = None,
    response_json_schema: dict[str, object] | None = None,
) -> dict[str, object]:
    """Rebuild a target adapter envelope from the same frozen messages.

    Only the adapter envelope and model change. The source request must be a
    recognized canonical snapshot; malformed snapshots fail closed.
    """
    from app.services.llm_adapters.runtime import (
        gemini_request_from_snapshot,
        generation_request_snapshot,
    )

    model = request_body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")

    system_message: str
    user_message: str
    temperature: float
    parsed_output_token_cap: int | None

    if source_adapter_kind is LlmAdapterKind.gemini_enterprise:
        request = gemini_request_from_snapshot(request_body)
        system_message = request.system_message
        user_message = request.user_message
        temperature = request.temperature
        parsed_output_token_cap = request.max_output_tokens
        if response_json_schema is None:
            response_json_schema = request.response_json_schema
    else:
        messages = request_body.get("messages")
        if not isinstance(messages, list) or len(messages) != 2:
            raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
        system, user = messages
        if not isinstance(system, Mapping) or not isinstance(system.get("content"), str):
            raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
        if not isinstance(user, Mapping) or not isinstance(user.get("content"), str):
            raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
        if system.get("role") != "system" or user.get("role") != "user":
            raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
        system_message = system["content"]
        user_message = user["content"]
        raw_temperature: object
        if source_adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
            raw_temperature = request_body.get("temperature", 0.2)
            cap = request_body.get("max_completion_tokens")
            parsed_output_token_cap = cap if isinstance(cap, int) and not isinstance(cap, bool) else None
        elif source_adapter_kind is LlmAdapterKind.ollama_chat:
            options = request_body.get("options")
            raw_temperature = options.get("temperature", 0.2) if isinstance(options, Mapping) else 0.2
            cap = options.get("num_predict") if isinstance(options, Mapping) else None
            parsed_output_token_cap = cap if isinstance(cap, int) and not isinstance(cap, bool) else None
        else:
            raise AppError(422, "business_rule_violation", "Unsupported LLM adapter")
        if not isinstance(raw_temperature, (int, float)) or isinstance(raw_temperature, bool):
            raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
        temperature = float(raw_temperature)

    if not math.isfinite(temperature):
        raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
    if not isinstance(target_model, str) or not target_model.strip():
        raise AppError(500, "llm_request_invalid", "Stored LLM provider snapshot is invalid")

    resolved_cap = output_token_cap if output_token_cap is not None else parsed_output_token_cap
    if resolved_cap is not None and (type(resolved_cap) is not int or resolved_cap <= 0):
        raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")

    rebuilt = generation_request_snapshot(
        adapter_kind=target_adapter_kind,
        model=target_model,
        user_id=user_id,
        system_message=system_message,
        user_message=user_message,
        output_token_cap=resolved_cap,
        response_json_schema=response_json_schema,
        temperature=temperature,
    )

    return rebuilt


def prepare_backup_request(
    provider: object,
    primary_request: Mapping[str, object],
    user_id: UUID,
    *,
    output_token_cap: int | None = None,
    response_json_schema: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the backup adapter request from an immutable provider snapshot."""
    adapter_value = getattr(provider, "adapter_kind", None)
    model_value = getattr(provider, "model", None)
    if isinstance(provider, Mapping):
        adapter_value = provider.get("adapter_kind", adapter_value)
        model_value = provider.get("model", model_value)
    try:
        target_adapter = LlmAdapterKind(adapter_value)
    except (TypeError, ValueError):
        raise AppError(500, "llm_request_invalid", "Stored LLM provider snapshot is invalid") from None
    if not isinstance(model_value, str) or not model_value.strip():
        raise AppError(500, "llm_request_invalid", "Stored LLM provider snapshot is invalid")
    if isinstance(primary_request.get("contents"), list) and isinstance(primary_request.get("config"), Mapping):
        source_adapter = LlmAdapterKind.gemini_enterprise
    elif isinstance(primary_request.get("messages"), list) and (
        "options" in primary_request or primary_request.get("stream") is True
    ):
        source_adapter = LlmAdapterKind.ollama_chat
    elif isinstance(primary_request.get("messages"), list):
        # OpenAI-compatible and Bedrock snapshots share the same canonical
        # request shape, so either source label parses identically here.
        source_adapter = LlmAdapterKind.openai_chat
    else:
        raise AppError(500, "llm_request_invalid", "Stored LLM request is invalid")
    return remap_generation_request(
        primary_request,
        source_adapter_kind=source_adapter,
        target_adapter_kind=target_adapter,
        target_model=model_value,
        user_id=user_id,
        output_token_cap=output_token_cap,
        response_json_schema=response_json_schema,
    )
