from __future__ import annotations

import hmac
import os
import secrets
from functools import lru_cache
from hashlib import sha256

from app.cookie_security import app_environment
from app.errors import AppError
from app.services.auth import session_token_hash
from app.services.vault import get_or_create_platform_csrf_secret


CSRF_COOKIE_NAME = "openscribe_csrf"
CSRF_ANON_COOKIE_NAME = "openscribe_csrf_anon"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@lru_cache(maxsize=1)
def _csrf_secret() -> str:
    value = os.getenv("CSRF_SECRET") or os.getenv("SECRET_KEY")
    if value:
        return str(value)

    environment = app_environment()
    if environment in {"production", "prod"} or os.getenv("CSRF_SECRET_VAULT_REF"):
        try:
            return get_or_create_platform_csrf_secret()
        except AppError as exc:
            raise RuntimeError(
                "CSRF_SECRET or SECRET_KEY is required, or Vault must be available for CSRF secret bootstrap"
            ) from exc

    if environment in {"local", "dev", "development", "test", "testing"}:
        value = "dev-only-csrf-secret"
    if not value:
        raise RuntimeError("CSRF_SECRET or SECRET_KEY is required")
    return str(value)


def csrf_secret_configured_for_environment() -> None:
    if app_environment() in {"production", "prod"}:
        _csrf_secret()


def _sign(subject: str, nonce: str) -> str:
    message = f"{subject}.{nonce}".encode("utf-8")
    return hmac.new(_csrf_secret().encode("utf-8"), message, sha256).hexdigest()


def _encode(subject: str, nonce: str) -> str:
    return f"{nonce}.{_sign(subject, nonce)}"


def _decode(token: str) -> tuple[str, str] | None:
    parts = token.split(".", 1)
    if len(parts) != 2:
        return None
    nonce, signature = parts
    if not nonce or not signature:
        return None
    return nonce, signature


def _session_nonce(session_hash: str) -> str:
    message = f"session-csrf-nonce:{session_hash}".encode("utf-8")
    return hmac.new(_csrf_secret().encode("utf-8"), message, sha256).hexdigest()


def session_csrf_token(raw_session_token: str) -> str:
    session_hash = session_token_hash(raw_session_token)
    return _encode(f"session:{session_hash}", _session_nonce(session_hash))


def anonymous_csrf_token(anon_nonce: str) -> str:
    return _encode(f"anon:{anon_nonce}", anon_nonce)


def new_anonymous_nonce() -> str:
    return secrets.token_urlsafe(24)


def verify_csrf_token(
    *,
    submitted_token: str,
    raw_session_token: str | None,
    anon_nonce: str | None,
) -> bool:
    decoded = _decode(submitted_token)
    if decoded is None:
        return False

    nonce, signature = decoded
    if raw_session_token:
        session_hash = session_token_hash(raw_session_token)
        subject = f"session:{session_hash}"
        if not hmac.compare_digest(nonce, _session_nonce(session_hash)):
            return False
    elif anon_nonce:
        subject = f"anon:{anon_nonce}"
        if nonce != anon_nonce:
            return False
    else:
        return False

    expected = _sign(subject, nonce)
    return hmac.compare_digest(signature, expected)
