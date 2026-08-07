from __future__ import annotations

import os
import hashlib
import hmac
import ipaddress
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from app.cookie_security import app_environment
from app.models import SecurityAuditEvent, User


logger = logging.getLogger("openscribe.audit")


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
MAX_AUDIT_IP_LENGTH = 255
MAX_AUDIT_DICT_KEYS = 50
MAX_AUDIT_LIST_ITEMS = 50
MAX_AUDIT_DETAILS_JSON_LENGTH = 8192
AUDIT_TRUNCATION_MARKER = "...[truncated]"
LOCAL_AUDIT_SECRET_ENVIRONMENTS = {"local", "dev", "development", "test", "testing"}


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(sensitive in normalized for sensitive in SENSITIVE_AUDIT_KEYS)


def _bounded_truncated_string(value: str, *, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= len(AUDIT_TRUNCATION_MARKER):
        return AUDIT_TRUNCATION_MARKER[:max_length]
    return value[: max_length - len(AUDIT_TRUNCATION_MARKER)] + AUDIT_TRUNCATION_MARKER


def _safe_string(value: str, *, max_length: int = MAX_AUDIT_STRING_LENGTH) -> str:
    clean = value.replace("\r", "\\r").replace("\n", "\\n")
    return _bounded_truncated_string(clean, max_length=max_length)


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for index, (key, nested_value) in enumerate(value.items()):
            if index >= MAX_AUDIT_DICT_KEYS:
                clean["[truncated_keys]"] = len(value) - MAX_AUDIT_DICT_KEYS
                break
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            clean[_safe_string(key_text)] = _safe_value(nested_value)
        return clean
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        clean_list = [_safe_value(item) for item in values[:MAX_AUDIT_LIST_ITEMS]]
        if len(values) > MAX_AUDIT_LIST_ITEMS:
            clean_list.append({"[truncated_items]": len(values) - MAX_AUDIT_LIST_ITEMS})
        return clean_list
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_string(str(value))


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    clean: dict[str, Any] = {}
    for index, (key, value) in enumerate(details.items()):
        if index >= MAX_AUDIT_DICT_KEYS:
            clean["[truncated_keys]"] = len(details) - MAX_AUDIT_DICT_KEYS
            break
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        clean[_safe_string(key_text)] = _safe_value(value)
    if len(json.dumps(clean, default=str)) > MAX_AUDIT_DETAILS_JSON_LENGTH:
        clean = {"[truncated_details]": True, "original_key_count": len(clean)}
    return clean


def _safe_ip(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()[:MAX_AUDIT_IP_LENGTH]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return _safe_string(text, max_length=MAX_AUDIT_IP_LENGTH)


def request_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    cloudflare_ip = request.headers.get("cf-connecting-ip")
    if cloudflare_ip and os.getenv("AUDIT_TRUST_CLOUDFLARE", "false").lower() in {"1", "true", "yes"}:
        return _safe_ip(cloudflare_ip)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for and os.getenv("AUDIT_TRUST_X_FORWARDED_FOR", "false").lower() in {"1", "true", "yes"}:
        return _safe_ip(forwarded_for.split(",", 1)[0])
    return _safe_ip(request.client.host) if request.client else None


def _subject_hash_secret() -> bytes:
    material = os.getenv("AUDIT_SUBJECT_HASH_SECRET") or os.getenv("SECRET_KEY") or os.getenv("CSRF_SECRET")
    if material:
        return material.encode("utf-8")

    environment = app_environment()
    if environment in {"production", "prod"} or os.getenv("CSRF_SECRET_VAULT_REF"):
        try:
            from app.services.vault import get_or_create_platform_csrf_secret

            material = get_or_create_platform_csrf_secret()
        except Exception as exc:
            raise RuntimeError(
                "AUDIT_SUBJECT_HASH_SECRET, SECRET_KEY, CSRF_SECRET, or Vault-backed CSRF secret is required for audit subject hashing"
            ) from exc
    elif environment in LOCAL_AUDIT_SECRET_ENVIRONMENTS:
        material = "openscribe-dev-audit-subject-hash"
    else:
        raise RuntimeError("AUDIT_SUBJECT_HASH_SECRET, SECRET_KEY, or CSRF_SECRET is required for audit subject hashing")
    return material.encode("utf-8")


def audit_subject_hash_secret_configured_for_environment() -> None:
    if app_environment() in {"production", "prod"}:
        _subject_hash_secret()


def audit_subject_hash(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return "hmac-sha256:" + hmac.new(_subject_hash_secret(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


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
    AuditSession = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    try:
        with AuditSession() as audit_db:
            add_security_event(
                audit_db,
                action=action,
                actor=actor,
                target=target,
                team_id=team_id,
                request=request,
                details=details,
            )
            audit_db.commit()
    except Exception:
        logger.exception("security_audit_write_failed", extra={"action": action})


def add_security_event(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    target: User | None = None,
    team_id: UUID | None = None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> SecurityAuditEvent:
    """Add a sanitized audit row to the caller's transaction without committing it."""
    safe_details = _safe_details(details)
    if request is not None:
        safe_details.setdefault("method", request.method)
        safe_details.setdefault("route", request.url.path)
    event_payload = {
        "action": action,
        "actor_user_id": actor.id if actor else None,
        "target_user_id": target.id if target else None,
        "team_id": team_id or (target.team_id if target else None),
        "request_ip": request_ip(request),
        "user_agent": _safe_string(request.headers.get("user-agent", "")) if request else None,
        "details_json": safe_details,
    }
    event = SecurityAuditEvent(**event_payload)
    db.add(event)
    return event
