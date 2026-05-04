from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import SecurityAuditEvent, User


SENSITIVE_AUDIT_KEYS = {
    "password",
    "temporary_password",
    "token",
    "mfa_code",
    "totp",
    "secret",
    "authorization",
}


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    clean: dict[str, Any] = {}
    for key, value in details.items():
        normalized = key.lower()
        if any(sensitive in normalized for sensitive in SENSITIVE_AUDIT_KEYS):
            continue
        clean[key] = value
    return clean


def request_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for and os.getenv("AUDIT_TRUST_X_FORWARDED_FOR", "false").lower() in {"1", "true", "yes"}:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


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
    event = SecurityAuditEvent(
        action=action,
        actor_user_id=actor.id if actor else None,
        target_user_id=target.id if target else None,
        team_id=team_id or (target.team_id if target else None),
        request_ip=request_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        details_json=_safe_details(details),
    )
    db.add(event)
    db.commit()
