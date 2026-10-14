"""Closed, content-free presentation mapping for generation failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID


_TEMPORARY_PROVIDER_CODES = frozenset({"connection_error", "timeout", "rate_limit_exceeded"})
_CONFIG_PROVIDER_CODES = frozenset(
    {"authentication_error", "invalid_api_key", "model_not_found", "permission_denied", "quota_exceeded"}
)
_TEMPORARY_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_CONFIG_APP_CODES = frozenset(
    {
        "hallucination_check_invalid_config",
        "llm_config_invalid",
        "llm_execution_context_unsupported",
        "llm_credential_unavailable",
        "llm_model_unavailable",
        "consultation_split_credential_unavailable",
        "consultation_split_llm_model_unavailable",
        "consultation_split_provider_config_invalid",
        "quota_exceeded",
        "verification_credential_unavailable",
    }
)
_TEMPORARY_APP_CODES = frozenset(
    {
        "llm_provider_timeout",
        "llm_provider_unreachable",
        "llm_rate_limited",
        "transcript_wait_timeout",
    }
)
_VALIDATION_APP_CODES = frozenset(
    {
        "llm_generation_invalid_json",
        "consultation_split_analysis_invalid_output",
        "consultation_split_generation_invalid_output",
        "verification_invalid_output",
    }
)
_SAFE_APP_CODES = frozenset(
    {
        *_CONFIG_APP_CODES,
        *_TEMPORARY_APP_CODES,
        *_VALIDATION_APP_CODES,
        "generation_enqueue_failed",
        "generation_snapshot_invalid",
        "llm_generation_invalid_json",
        "llm_generation_preparation_failed",
        "hallucination_check_failed",
        "hallucination_check_invalid_response",
        "consultation_split_analysis_invalid_output",
        "consultation_split_generation_invalid_output",
        "consultation_split_materialization_binding_invalid",
        "consultation_split_reservation_expired",
        "consultation_split_retention_binding_invalid",
        "consultation_split_source_expired",
        "consultation_split_source_stale",
        "verification_provider_failed",
        "verification_credential_unavailable",
        "verification_invalid_output",
    }
)
_SAFE_PROVIDER_CODES = frozenset(
    {
        *_TEMPORARY_PROVIDER_CODES,
        *_CONFIG_PROVIDER_CODES,
        "bad_request",
        "http_status_error",
        "invalid_request",
        "invalid_request_error",
        "not_found",
        "provider_conflict",
        "provider_server_error",
        "quota_exceeded",
        "rate_limit_exceeded",
    }
)


def generation_failure_category(
    *,
    error_code: object = None,
    provider_error_code: object = None,
    provider_http_status: object = None,
) -> str:
    """Classify known failures without returning provider/server wording."""
    if (isinstance(provider_error_code, str) and provider_error_code == "quota_exceeded") or (
        isinstance(error_code, str) and error_code == "quota_exceeded"
    ):
        return "configuration"
    if isinstance(provider_http_status, int) and not isinstance(provider_http_status, bool) and provider_http_status in {401, 403}:
        return "configuration"
    if isinstance(provider_error_code, str) and provider_error_code in _CONFIG_PROVIDER_CODES:
        return "configuration"
    if isinstance(error_code, str) and error_code in _CONFIG_APP_CODES:
        return "configuration"
    if isinstance(error_code, str) and error_code in _VALIDATION_APP_CODES:
        return "input_or_validation"
    if (
        isinstance(provider_http_status, int)
        and not isinstance(provider_http_status, bool)
        and provider_http_status in _TEMPORARY_HTTP_STATUSES
    ) or (
        isinstance(provider_error_code, str) and provider_error_code in _TEMPORARY_PROVIDER_CODES
    ) or (isinstance(error_code, str) and error_code in _TEMPORARY_APP_CODES):
        return "temporary"
    return "unknown"


def generation_failure_presentation(
    *,
    reference: UUID,
    occurred_at: datetime | None,
    time_basis: str = "failed",
    error_code: object = None,
    provider_error_code: object = None,
    provider_http_status: object = None,
    include_technical_details: bool = False,
) -> dict[str, object]:
    """Build a minimal response projection; technical codes are role-gated."""
    technical_details = None
    if include_technical_details:
        technical_details = {
            "application_code": error_code if isinstance(error_code, str) and error_code in _SAFE_APP_CODES else None,
            "provider_code": (
                provider_error_code
                if isinstance(provider_error_code, str) and provider_error_code in _SAFE_PROVIDER_CODES
                else None
            ),
            "provider_http_status": (
                provider_http_status
                if isinstance(provider_http_status, int)
                and not isinstance(provider_http_status, bool)
                and 100 <= provider_http_status <= 599
                else None
            ),
        }
    return {
        "category": generation_failure_category(
            error_code=error_code,
            provider_error_code=provider_error_code,
            provider_http_status=provider_http_status,
        ),
        "support_reference": reference,
        "occurred_at": occurred_at,
        "time_basis": "created" if time_basis == "created" else "failed",
        "technical_details": technical_details,
    }


def active_retry_presentation(attempt: object, *, reference: UUID) -> dict[str, object] | None:
    """Project only a confirmed in-flight capacity retry for a team leader."""
    if getattr(getattr(attempt, "status", None), "value", None) != "submitted":
        return None
    events = getattr(attempt, "transport_events_json", None)
    if not isinstance(events, list) or not events:
        return None
    event = events[-1]
    if not isinstance(event, dict):
        return None
    code = event.get("code")
    wait_ms = event.get("retry_wait_ms")
    http_status = event.get("http_status")
    occurred_at_raw = event.get("occurred_at")
    if code != "rate_limited":
        return None
    if not isinstance(http_status, int) or isinstance(http_status, bool) or http_status != 429:
        return None
    if not isinstance(wait_ms, int) or isinstance(wait_ms, bool) or wait_ms <= 0:
        return None
    if not isinstance(occurred_at_raw, str):
        return None
    try:
        occurred_at = datetime.fromisoformat(occurred_at_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A persisted rejection is not evidence that the worker is still sleeping.
    # Do not keep presenting the retry once the bounded backoff has elapsed.
    if occurred_at.tzinfo is None or not (
        occurred_at <= datetime.now(UTC) < occurred_at + timedelta(milliseconds=wait_ms)
    ):
        return None
    return {
        "category": "temporary",
        "support_reference": reference,
        "occurred_at": occurred_at,
        "time_basis": "failed",
        "technical_details": {
            "application_code": None,
            "provider_code": "rate_limit_exceeded",
            "provider_http_status": 429,
        },
    }


def failed_transport_details(attempt: object | None) -> tuple[str | None, int | None]:
    """Return only controlled terminal provider metadata from a settled failure."""
    if attempt is None or getattr(getattr(attempt, "status", None), "value", None) != "settled":
        return None, None
    if getattr(getattr(attempt, "outcome", None), "value", None) not in {"failed", "unknown"}:
        return None, None
    events = getattr(attempt, "transport_events_json", None)
    if not isinstance(events, list) or not events or not isinstance(events[-1], dict):
        return None, None
    event = events[-1]
    code = event.get("code")
    status = event.get("http_status")
    if not isinstance(code, str) or code not in {"rate_limited", "timeout", "connection_error", "provider_error"}:
        return None, None
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        status = None
    provider_code = {
        "rate_limited": "rate_limit_exceeded",
        "timeout": "timeout",
        "connection_error": "connection_error",
    }.get(code)
    if provider_code is None:
        provider_code = {
            400: "bad_request", 401: "authentication_error", 403: "permission_denied",
            404: "not_found", 408: "timeout", 409: "provider_conflict", 422: "invalid_request",
            429: "rate_limit_exceeded",
        }.get(status)
        if provider_code is None and status is not None and 500 <= status <= 599:
            provider_code = "provider_server_error"
        elif provider_code is None and status is not None:
            provider_code = "http_status_error"
    return provider_code, status


def generated_document_status_projection(db: object, document: object, actor: object | None) -> dict[str, object]:
    """Build generated-note status metadata after the caller's owner authorization."""
    from sqlalchemy import select

    from app.models import (
        AttemptKind,
        AttemptOutcome,
        AttemptStatus,
        GeneratedDocumentStatus,
        HallucinationCheckStatus,
        ProviderAttempt,
        TeamRole,
    )

    owns = actor is not None and getattr(actor, "id", None) == getattr(document, "owner_user_id", None)
    leader = bool(
        owns
        and not getattr(actor, "is_system_admin", False)
        and getattr(actor, "team_role", None) is TeamRole.leader
        and getattr(actor, "team_id", None) is not None
        and getattr(actor, "team_id", None) == getattr(document, "team_id", None)
    )
    result: dict[str, object] = {
        "active_retry_presentation": None,
        "checker_unavailable": False,
        "checker_failure_presentation": None,
    }
    if leader:
        attempt_predicates = (
            ProviderAttempt.generated_document_id == document.id,
            ProviderAttempt.owner_user_id == document.owner_user_id,
            ProviderAttempt.team_id == document.team_id,
            ProviderAttempt.transcript_id == document.transcript_id,
        )
        attempt = db.scalar(
            select(ProviderAttempt).where(
                *attempt_predicates,
                ProviderAttempt.attempt_kind == AttemptKind.llm_generation,
                ProviderAttempt.status == AttemptStatus.submitted,
            ).order_by(ProviderAttempt.attempt_number.desc()).limit(1)
        )
        retry = active_retry_presentation(attempt, reference=document.id) if attempt is not None else None
        if retry is None:
            attempt = db.scalar(
                select(ProviderAttempt).where(
                    *attempt_predicates,
                    ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
                    ProviderAttempt.status == AttemptStatus.submitted,
                ).order_by(ProviderAttempt.attempt_number.desc()).limit(1)
            )
            retry = active_retry_presentation(attempt, reference=document.id) if attempt is not None else None
        result["active_retry_presentation"] = retry
    if getattr(document, "status", None) is GeneratedDocumentStatus.failed:
        result["failure_presentation"] = generation_failure_presentation(
            reference=document.id,
            occurred_at=getattr(document, "completed_at", None) or document.created_at,
            time_basis="failed" if getattr(document, "completed_at", None) else "created",
            error_code=getattr(document, "error_code", None),
            provider_error_code=getattr(document, "provider_error_code", None),
            provider_http_status=getattr(document, "provider_http_status", None),
            include_technical_details=leader,
        )
    checker_failed = getattr(document, "hallucination_check_status", None) in {
        HallucinationCheckStatus.failed_provider,
        HallucinationCheckStatus.failed_invalid_response,
    }
    result["checker_unavailable"] = checker_failed
    if checker_failed:
        checker_attempt = None
        if leader:
            checker_attempt = db.scalar(
                select(ProviderAttempt).where(
                    ProviderAttempt.generated_document_id == document.id,
                    ProviderAttempt.owner_user_id == document.owner_user_id,
                    ProviderAttempt.team_id == document.team_id,
                    ProviderAttempt.transcript_id == document.transcript_id,
                    ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
                    ProviderAttempt.status == AttemptStatus.settled,
                    ProviderAttempt.outcome.in_((AttemptOutcome.failed, AttemptOutcome.unknown)),
                ).order_by(ProviderAttempt.attempt_number.desc()).limit(1)
            )
        transport_code, transport_status = failed_transport_details(checker_attempt)
        invalid = getattr(document, "hallucination_check_status", None) is HallucinationCheckStatus.failed_invalid_response
        result["checker_failure_presentation"] = generation_failure_presentation(
            reference=document.id,
            occurred_at=(getattr(checker_attempt, "settled_at", None) if checker_attempt is not None else None)
            or getattr(document, "hallucination_check_completed_at", None)
            or document.created_at,
            time_basis="failed" if (getattr(checker_attempt, "settled_at", None) if checker_attempt is not None else None)
            or getattr(document, "hallucination_check_completed_at", None) else "created",
            error_code=(getattr(checker_attempt, "error_code", None) if checker_attempt is not None else None)
            or ("hallucination_check_invalid_response" if invalid else "hallucination_check_failed"),
            provider_error_code=(getattr(checker_attempt, "provider_error_code", None) if checker_attempt is not None else None)
            or transport_code,
            provider_http_status=(getattr(checker_attempt, "provider_http_status", None) if checker_attempt is not None else None)
            or transport_status,
            include_technical_details=leader,
        )
    return result
