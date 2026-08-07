import re
from pathlib import Path
from uuid import uuid4

import pyotp
import pytest

from app.cookie_security import enforce_production_cookie_security
from app.main import CSRF_COOKIE_NAME
from app.errors import AppError
from app.models import MfaMethodType, UserMfaMethod, utcnow
from app.services import csrf as csrf_service
from app.services.csrf import CSRF_ANON_COOKIE_NAME, csrf_secret_configured_for_environment
from app.services.auth import SESSION_COOKIE_NAME, TRUSTED_DEVICE_COOKIE_NAME


def _set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _cookie_header(response, cookie_name: str) -> str:
    matches = [header for header in _set_cookie_headers(response) if header.startswith(f"{cookie_name}=")]
    assert len(matches) == 1
    return matches[0]


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


def test_session_csrf_token_is_stable_for_session_and_bound_to_session():
    first_token = csrf_service.session_csrf_token("first-session-token")

    assert csrf_service.session_csrf_token("first-session-token") == first_token
    assert csrf_service.session_csrf_token("second-session-token") != first_token
    assert csrf_service.verify_csrf_token(
        submitted_token=first_token,
        raw_session_token="first-session-token",
        anon_nonce=None,
    )
    assert not csrf_service.verify_csrf_token(
        submitted_token=first_token,
        raw_session_token="second-session-token",
        anon_nonce=None,
    )


def test_hsts_added_for_https(raw_client):
    response = raw_client.get("/login", headers={"x-forwarded-proto": "https"})

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_hsts_can_be_delegated_to_proxy(raw_client, monkeypatch):
    monkeypatch.setenv("HSTS_SOURCE", "proxy")

    response = raw_client.get("/login", headers={"x-forwarded-proto": "https"})

    assert "Strict-Transport-Security" not in response.headers


def test_hsts_proxy_static_fallback_only_adds_static_hsts(raw_client, monkeypatch):
    monkeypatch.setenv("HSTS_SOURCE", "proxy_static_fallback")

    dynamic_response = raw_client.get("/login", headers={"x-forwarded-proto": "https"})
    static_response = raw_client.get(
        "/static/vendor/lucide/1.8.0/lucide.min.js",
        headers={"x-forwarded-proto": "https"},
    )

    assert "Strict-Transport-Security" not in dynamic_response.headers
    assert static_response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_browser_security_headers_added(raw_client):
    response = raw_client.get("/login")

    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Embedder-Policy"] == "credentialless"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), geolocation=(), payment=(), usb=(), fullscreen=(self), microphone=(self)"
    )
    assert response.headers["X-Robots-Tag"] == (
        "noindex, nofollow, noarchive, nosnippet, noimageindex"
    )


def test_x_robots_tag_added_to_all_response_types(raw_client):
    for path in ["/login", "/api/", "/robots.txt"]:
        response = raw_client.get(path)

        assert response.headers["X-Robots-Tag"] == (
            "noindex, nofollow, noarchive, nosnippet, noimageindex"
        )


def test_public_auth_pages_are_no_store(raw_client):
    for path in ["/", "/login", "/forgot-password", "/request-access"]:
        response = raw_client.get(path)

        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"


def test_api_responses_are_no_store(raw_client):
    response = raw_client.get("/api/")
    slashless_response = raw_client.get("/api")

    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
    assert slashless_response.headers["Cache-Control"] == "no-store"
    assert slashless_response.headers["Pragma"] == "no-cache"
    assert slashless_response.headers["Expires"] == "0"


def test_csrf_cookie_is_httponly_signed_and_paired_with_httponly_anon_nonce(raw_client):
    response = raw_client.get("/login")

    csrf_cookie = _cookie_header(response, CSRF_COOKIE_NAME)
    anon_cookie = _cookie_header(response, CSRF_ANON_COOKIE_NAME)
    csrf_value = response.cookies[CSRF_COOKIE_NAME]
    anon_nonce = response.cookies[CSRF_ANON_COOKIE_NAME]

    assert "HttpOnly" in csrf_cookie
    assert "SameSite=lax" in csrf_cookie
    assert "HttpOnly" in anon_cookie
    assert "SameSite=lax" in anon_cookie
    assert csrf_service.verify_csrf_token(
        submitted_token=csrf_value,
        raw_session_token=None,
        anon_nonce=anon_nonce,
    )


def test_csrf_cookie_alone_does_not_authenticate_api(raw_client):
    raw_client.get("/login")

    response = raw_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert SESSION_COOKIE_NAME not in raw_client.cookies


def test_session_cookie_and_csrf_cookie_are_httponly(raw_client, make_user):
    make_user(email="cookie-session@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = raw_client.post("/api/v1/auth/login", json={"email": "cookie-session@example.com", "password": "password-1"})

    session_cookie = _cookie_header(response, SESSION_COOKIE_NAME)
    csrf_cookie = _cookie_header(response, CSRF_COOKIE_NAME)
    csrf_value = response.cookies[CSRF_COOKIE_NAME]
    session_value = response.cookies[SESSION_COOKIE_NAME]

    assert response.status_code == 200
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "HttpOnly" in csrf_cookie
    assert csrf_service.verify_csrf_token(
        submitted_token=csrf_value,
        raw_session_token=session_value,
        anon_nonce=None,
    )


def test_trusted_device_cookie_is_httponly(raw_client, db_session, make_user, make_totp_method):
    user = make_user(email="cookie-trusted@example.com", password="password-1", mfa_required=True, mfa_enabled=True)
    _, secret = make_totp_method(user=user, verified_at=utcnow())

    login_response = raw_client.post("/api/v1/auth/login", json={"email": "cookie-trusted@example.com", "password": "password-1"})
    csrf = raw_client.cookies.get(CSRF_COOKIE_NAME)
    mfa_response = raw_client.post(
        "/api/v1/auth/mfa/totp",
        json={"code": pyotp.TOTP(secret).now(), "remember_device": True},
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )

    trusted_cookie = _cookie_header(mfa_response, TRUSTED_DEVICE_COOKIE_NAME)

    assert login_response.status_code == 200
    assert login_response.json()["auth_level"] == "pending_mfa"
    assert mfa_response.status_code == 200
    assert "HttpOnly" in trusted_cookie
    assert "SameSite=lax" in trusted_cookie


def test_public_metadata_routes_are_explicit_and_cookie_free(raw_client):
    robots = raw_client.get("/robots.txt")
    security_txt = raw_client.get("/.well-known/security.txt")
    sitemap = raw_client.get("/sitemap.xml")

    assert robots.status_code == 200
    assert robots.headers["content-type"].startswith("text/plain")
    assert robots.text == "User-agent: *\nDisallow: /\n"
    assert "set-cookie" not in robots.headers
    assert robots.headers["Cache-Control"] == "public, max-age=3600"

    assert security_txt.status_code == 404
    assert security_txt.headers["content-type"].startswith("text/plain")
    assert security_txt.text == "Security contact not configured.\n"
    assert "meddleapp.com" not in security_txt.text
    assert "openscribe.co.uk" not in security_txt.text
    assert "set-cookie" not in security_txt.headers
    assert security_txt.headers["Cache-Control"] == "public, max-age=3600"

    assert sitemap.status_code == 404
    assert sitemap.headers["content-type"].startswith("text/plain")
    assert "Sitemap not published." in sitemap.text
    assert "set-cookie" not in sitemap.headers
    assert sitemap.headers["Cache-Control"] == "public, max-age=3600"


def test_static_assets_do_not_issue_csrf_cookie(raw_client):
    response = raw_client.get("/static/vendor/lucide/1.8.0/lucide.min.js")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert response.headers["Cache-Control"] == "public, max-age=3600"


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
    assert "style-src-attr 'none'" in csp
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


def test_api_csrf_requires_header_and_ignores_form_fallback(raw_client, make_user):
    make_user(email="api-csrf-header@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert raw_client.post("/api/v1/auth/login", json={"email": "api-csrf-header@example.com", "password": "password-1"}).status_code == 200
    csrf = raw_client.cookies[CSRF_COOKIE_NAME]

    response = raw_client.post(
        "/api/v1/transcripts/start",
        data={"title": "CSRF form fallback", "ingestion_mode": "whole_file", "_csrf_token": csrf},
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF verification failed"


def test_settings_account_change_rejects_missing_csrf(raw_client, db_session, make_user):
    user = make_user(
        email="account-csrf@example.com",
        full_name="Original Name",
        password="Password123",
        mfa_required=False,
        mfa_enabled=False,
    )
    assert raw_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Password123"},
    ).status_code == 200

    response = raw_client.post("/settings/account/name", data={"full_name": "Changed Without CSRF"})

    assert response.status_code == 403
    db_session.refresh(user)
    assert user.full_name == "Original Name"


def test_csrf_origin_ignores_forwarded_headers_without_trust(raw_client, make_user, monkeypatch):
    monkeypatch.delenv("TRUST_FORWARDED_ORIGIN_HEADERS", raising=False)
    make_user(email="api-csrf-forwarded@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert raw_client.post("/api/v1/auth/login", json={"email": "api-csrf-forwarded@example.com", "password": "password-1"}).status_code == 200
    csrf = raw_client.cookies[CSRF_COOKIE_NAME]

    response = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "Forwarded origin", "ingestion_mode": "whole_file"},
        headers={
            "Origin": "http://forwarded.example",
            "Host": "testserver",
            "X-Forwarded-Host": "forwarded.example",
            "X-Forwarded-Proto": "http",
            "X-CSRF-Token": csrf,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Cross-origin request rejected"


def _hidden_csrf_token(html: str) -> str:
    match = re.search(r'<input type="hidden" name="_csrf_token" value="([^"]+)">', html)
    assert match is not None
    return match.group(1)


def test_public_forms_render_hidden_csrf_tokens(raw_client, make_user, monkeypatch):
    monkeypatch.setenv("MAIL_TRANSPORT", "stdout")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "no-reply@example.com")
    monkeypatch.setenv("MAIL_FROM_NAME", "OpenScribe")
    make_user(email="public-csrf-form@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    login_page = raw_client.get("/login")
    assert login_page.status_code == 200
    assert _hidden_csrf_token(login_page.text) == login_page.cookies[CSRF_COOKIE_NAME]
    assert '<form method="post" action="/login">' in login_page.text

    reset_page = raw_client.get("/forgot-password")
    assert reset_page.status_code == 200
    assert _hidden_csrf_token(reset_page.text) == reset_page.cookies[CSRF_COOKIE_NAME]
    assert '<form method="post" action="/forgot-password">' in reset_page.text

    request_page = raw_client.get("/request-access")
    assert request_page.status_code == 200
    assert _hidden_csrf_token(request_page.text) == request_page.cookies[CSRF_COOKIE_NAME]
    assert '<form method="post" action="/request-access">' in request_page.text


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
    for path in [
        Path("app/templates/home.html"),
        Path("app/templates/settings.html"),
        Path("app/templates/admin_mockup.html"),
    ]:
        content = path.read_text()
        assert "onsubmit=" not in content, f"inline submit handler left in {path}"
        assert "onchange=" not in content, f"inline change handler left in {path}"
        assert "data-confirm-submit" in content or "data-auto-submit" in content


def test_settings_partials_do_not_use_inline_script_handlers():
    for path in Path("app/templates/settings").glob("*.html"):
        content = path.read_text()
        assert "onsubmit=" not in content, f"inline submit handler left in {path}"
        assert "onchange=" not in content, f"inline change handler left in {path}"


@pytest.mark.parametrize("path_suffix", [
    "quotas/limits",
    "quota-grants",
    "quota-resets",
    "quota-grants/{grant_id}/revoke",
])
def test_quota_browser_mutations_require_csrf_and_same_origin(raw_client, make_user, path_suffix):
    admin = make_user(email="quota-csrf-admin@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    assert raw_client.post("/api/v1/auth/login", json={"email": admin.email, "password": "password-1"}).status_code == 200
    csrf = raw_client.cookies[CSRF_COOKIE_NAME]
    user_id, grant_id = uuid4(), uuid4()
    path = f"/admin/users/{user_id}/{path_suffix.format(grant_id=grant_id)}"

    assert raw_client.post(path, headers={"Origin": "http://testserver"}).status_code == 403
    assert raw_client.post(path, data={"_csrf_token": csrf}, headers={"Origin": "http://evil.example"}).status_code == 403
