from __future__ import annotations

import os
import hashlib
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import SecurityAuditEvent, User


SENSITIVE_AUDIT_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "csrf",
    "mfa_code",
    "password",
    "provider_response",
    "prompt",
    "raw_token",
    "recovery_code",
    "response_text",
    "session",
    "temporary_password",
    "token",
    "totp",
    "secret",
    "transcript_text",
}

MAX_AUDIT_STRING_LENGTH = 1024


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(sensitive in normalized for sensitive in SENSITIVE_AUDIT_KEYS)


def _safe_string(value: str) -> str:
    clean = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(clean) > MAX_AUDIT_STRING_LENGTH:
        return clean[:MAX_AUDIT_STRING_LENGTH] + "...[truncated]"
    return clean


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, nested_value in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            clean[_safe_string(key_text)] = _safe_value(nested_value)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_string(str(value))


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    clean: dict[str, Any] = {}
    for key, value in details.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        clean[_safe_string(key_text)] = _safe_value(value)
    return clean


def request_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    cloudflare_ip = request.headers.get("cf-connecting-ip")
    if cloudflare_ip and os.getenv("AUDIT_TRUST_CLOUDFLARE", "false").lower() in {"1", "true", "yes"}:
        return _safe_string(cloudflare_ip.strip())
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for and os.getenv("AUDIT_TRUST_X_FORWARDED_FOR", "false").lower() in {"1", "true", "yes"}:
        return _safe_string(forwarded_for.split(",", 1)[0].strip())
    return _safe_string(request.client.host) if request.client else None


def audit_subject_hash(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def record_security_event(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    target: User | None = None,
    team_id: UUID | None = None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = _safe_details(details)
    if request is not None:
        safe_details.setdefault("method", request.method)
        safe_details.setdefault("route", request.url.path)
    event = SecurityAuditEvent(
        action=action,
        actor_user_id=actor.id if actor else None,
        target_user_id=target.id if target else None,
        team_id=team_id or (target.team_id if target else None),
        request_ip=request_ip(request),
        user_agent=_safe_string(request.headers.get("user-agent", "")) if request else None,
        details_json=safe_details,
    )
    db.add(event)
    db.commit()
