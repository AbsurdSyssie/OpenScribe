import pytest

from app.cookie_security import enforce_production_cookie_security
from app.main import CSRF_COOKIE_NAME
from app.errors import AppError
from app.services import csrf as csrf_service
from app.services.csrf import CSRF_ANON_COOKIE_NAME, csrf_secret_configured_for_environment
from app.services.auth import SESSION_COOKIE_NAME


def test_production_requires_cookie_secure_always(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE_MODE", "auto")

    with pytest.raises(RuntimeError, match="COOKIE_SECURE_MODE=always"):
        enforce_production_cookie_security()


def test_production_requires_csrf_secret(monkeypatch):
    csrf_service._csrf_secret.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CSRF_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("CSRF_SECRET_VAULT_REF", raising=False)
    monkeypatch.setattr(
        csrf_service,
        "get_or_create_platform_csrf_secret",
        lambda: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    with pytest.raises(RuntimeError, match="CSRF_SECRET.*Vault"):
        csrf_secret_configured_for_environment()
    csrf_service._csrf_secret.cache_clear()


def test_production_uses_vault_csrf_secret_when_env_secret_missing(monkeypatch):
    csrf_service._csrf_secret.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CSRF_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(csrf_service, "get_or_create_platform_csrf_secret", lambda: "vault-backed-csrf-secret")

    csrf_secret_configured_for_environment()
    token = csrf_service.anonymous_csrf_token("anon-nonce")

    assert csrf_service.verify_csrf_token(submitted_token=token, raw_session_token=None, anon_nonce="anon-nonce")
    csrf_service._csrf_secret.cache_clear()


def test_hsts_added_for_https(raw_client):
    response = raw_client.get("/login", headers={"x-forwarded-proto": "https"})

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_hsts_not_added_for_http(raw_client):
    response = raw_client.get("/login")

    assert "Strict-Transport-Security" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_login_accepts_anonymous_csrf(raw_client, make_user):
    make_user(email="anonymous-csrf@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    get_response = raw_client.get("/login")
    csrf = get_response.cookies[CSRF_COOKIE_NAME]

    response = raw_client.post(
        "/login",
        data={"email": "anonymous-csrf@example.com", "password": "password-1", "_csrf_token": csrf},
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 200
    assert raw_client.cookies.get(SESSION_COOKIE_NAME)
    assert raw_client.cookies.get(CSRF_COOKIE_NAME)
    assert not raw_client.cookies.get(CSRF_ANON_COOKIE_NAME)
