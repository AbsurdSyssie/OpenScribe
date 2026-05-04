from __future__ import annotations

import os
from ipaddress import ip_address
from urllib.parse import urlsplit


COOKIE_SECURE_MODE_ENV = "COOKIE_SECURE_MODE"
COOKIE_SECURE_AUTO = "auto"
COOKIE_SECURE_ALWAYS = "always"
COOKIE_SECURE_NEVER = "never"
COOKIE_SECURE_MODES = {COOKIE_SECURE_AUTO, COOKIE_SECURE_ALWAYS, COOKIE_SECURE_NEVER}
LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}


def app_environment() -> str:
    return (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
        or "production"
    ).strip().lower()


def enforce_production_cookie_security() -> None:
    environment = app_environment()
    if environment not in {"production", "prod"}:
        return

    if cookie_secure_mode() != COOKIE_SECURE_ALWAYS:
        raise RuntimeError("COOKIE_SECURE_MODE=always is required in production")


def _is_local_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    candidate = hostname.strip().strip("[]").lower()
    if candidate in LOCALHOST_NAMES:
        return True
    try:
        return ip_address(candidate).is_loopback
    except ValueError:
        return False


def cookie_secure_mode() -> str:
    value = os.getenv(COOKIE_SECURE_MODE_ENV, COOKIE_SECURE_AUTO).strip().lower()
    if value not in COOKIE_SECURE_MODES:
        return COOKIE_SECURE_AUTO
    return value


def should_set_secure_cookie(*, request_url: str, forwarded_proto: str | None = None) -> bool:
    mode = cookie_secure_mode()
    if mode == COOKIE_SECURE_ALWAYS:
        return True
    if mode == COOKIE_SECURE_NEVER:
        return False

    effective_scheme = (forwarded_proto or "").split(",", 1)[0].strip().lower()
    parsed = urlsplit(request_url)
    if not effective_scheme:
        effective_scheme = parsed.scheme.lower()
    if effective_scheme != "https":
        return False
    return not _is_local_host(parsed.hostname)
