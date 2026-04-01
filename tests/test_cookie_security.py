from app.cookie_security import (
    COOKIE_SECURE_MODE_ENV,
    should_set_secure_cookie,
)


def test_secure_cookie_disabled_for_local_http_by_default(monkeypatch):
    monkeypatch.delenv(COOKIE_SECURE_MODE_ENV, raising=False)
    assert should_set_secure_cookie(request_url="http://127.0.0.1:8080/login") is False


def test_secure_cookie_enabled_for_public_https_by_default(monkeypatch):
    monkeypatch.delenv(COOKIE_SECURE_MODE_ENV, raising=False)
    assert should_set_secure_cookie(request_url="https://medscribe.duckdns.org/login") is True


def test_secure_cookie_disabled_for_local_https_by_default(monkeypatch):
    monkeypatch.delenv(COOKIE_SECURE_MODE_ENV, raising=False)
    assert should_set_secure_cookie(request_url="https://localhost/login") is False


def test_secure_cookie_honors_forwarded_proto(monkeypatch):
    monkeypatch.delenv(COOKIE_SECURE_MODE_ENV, raising=False)
    assert should_set_secure_cookie(
        request_url="http://medscribe.duckdns.org/login",
        forwarded_proto="https",
    ) is True


def test_secure_cookie_mode_always_overrides_request(monkeypatch):
    monkeypatch.setenv(COOKIE_SECURE_MODE_ENV, "always")
    assert should_set_secure_cookie(request_url="http://127.0.0.1:8080/login") is True


def test_secure_cookie_mode_never_overrides_request(monkeypatch):
    monkeypatch.setenv(COOKIE_SECURE_MODE_ENV, "never")
    assert should_set_secure_cookie(request_url="https://medscribe.duckdns.org/login") is False
