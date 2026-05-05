import re
from pathlib import Path

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


def test_csp_header_added(raw_client):
    response = raw_client.get("/login")

    csp = response.headers["Content-Security-Policy"]

    assert "default-src 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "upgrade-insecure-requests" not in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert re.search(r"'nonce-[A-Za-z0-9_-]+'", csp)


def test_csp_nonce_changes_per_response(raw_client):
    first = raw_client.get("/login").headers["Content-Security-Policy"]
    second = raw_client.get("/login").headers["Content-Security-Policy"]

    assert first != second


def test_https_csp_upgrades_insecure_requests(raw_client):
    response = raw_client.get("/login", headers={"x-forwarded-proto": "https"})

    assert "upgrade-insecure-requests" in response.headers["Content-Security-Policy"]


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


def test_app_templates_and_js_do_not_use_public_cdns():
    forbidden = [
        "cdn.tailwindcss.com",
        "unpkg.com",
        "cdn.jsdelivr.net",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
    ]

    for path in Path("app/templates").rglob("*.html"):
        content = path.read_text()
        for host in forbidden:
            assert host not in content, f"{host} found in {path}"

    for path in Path("app/static/js").rglob("*.js"):
        content = path.read_text()
        for host in forbidden:
            assert host not in content, f"{host} found in {path}"


def test_transcribe_js_uses_local_vad_assets():
    js = Path("app/static/js/transcribe/app.js").read_text()

    assert "cdn.jsdelivr.net" not in js
    assert "/static/vendor/vad-web/" in js
    assert "/static/vendor/onnxruntime-web/" in js


def test_vendored_onnxruntime_assets_include_threaded_modules():
    asset_dir = Path("app/static/vendor/onnxruntime-web/1.22.0")

    assert (asset_dir / "ort.wasm.min.js").is_file()
    assert (asset_dir / "ort-wasm-simd-threaded.wasm").is_file()
    assert (asset_dir / "ort-wasm-simd-threaded.jsep.wasm").is_file()
    assert (asset_dir / "ort-wasm-simd-threaded.mjs").is_file()
    assert (asset_dir / "ort-wasm-simd-threaded.jsep.mjs").is_file()


def test_transcribe_tailwind_build_includes_runtime_js_classes():
    config = Path("tailwind.transcribe.config.js").read_text()
    css = Path("app/static/css/transcribe-tailwind.css").read_text()

    assert "./app/static/js/transcribe/**/*.js" in config
    assert ".bg-teal-pale\\/35" in css
    assert ".border-teal-muted\\/35" in css
    assert ".hover\\:bg-parchment\\/50:hover" in css


def test_home_and_admin_templates_do_not_use_inline_script_handlers():
    for path in [Path("app/templates/home.html"), Path("app/templates/admin.html")]:
        content = path.read_text()
        assert "onsubmit=" not in content, f"inline submit handler left in {path}"
        assert "onchange=" not in content, f"inline change handler left in {path}"
        assert "data-confirm-submit=" in content or "data-auto-submit" in content
