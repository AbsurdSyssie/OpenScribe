## Next slice: cookie + CSRF hardening

This should be the next implementation slice. It is security-critical, self-contained, and builds directly on the recovery work.

Current state:

* Session cookies are `HttpOnly`, `SameSite=Lax`, but `Secure` is decided dynamically through `should_set_secure_cookie(...)`. 
* CSRF currently uses a JS-readable `openscribe_csrf` cookie and compares it directly to `X-CSRF-Token` or form `_csrf_token`. 
* `csrfFetch` reads the CSRF cookie and adds the header for unsafe same-origin `/api/v1` requests. 

## Goal

Make cookie-authenticated requests safer against:

* production proxy misconfiguration
* accidental non-secure cookies
* cross-origin POST/DELETE/PATCH abuse
* session-independent CSRF token reuse
* login/session rotation leaving stale CSRF tokens valid

---

# Agent brief

## 1. Add production secure-cookie startup guard

### File

```text id="f6whwt"
app/cookie_security.py
```

Add helpers:

```python id="kzpnn7"
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

    mode = cookie_secure_mode()
    if mode != COOKIE_SECURE_ALWAYS:
        raise RuntimeError(
            "COOKIE_SECURE_MODE=always is required in production"
        )
```

### File

```text id="b8c8jh"
app/main.py
```

Update import:

```python id="pklp06"
from .cookie_security import should_set_secure_cookie, enforce_production_cookie_security
```

Call immediately after app creation or before:

```python id="t8ofy2"
enforce_production_cookie_security()
app = FastAPI(title="OpenScribe MVP")
```

If tests expect `APP_ENV` unset, ensure test env sets `APP_ENV=test`, or make test fixtures set `COOKIE_SECURE_MODE=always`.

---

## 2. Add HSTS middleware

### File

```text id="qz1shk"
app/main.py
```

Add helper:

```python id="e4k3d8"
def _request_is_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"
```

Add middleware after app creation:

```python id="6gwd8f"
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)

    if _request_is_https(request):
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    return response
```

Do **not** emit HSTS on HTTP/local development responses.

---

## 3. Replace plain CSRF with session-bound CSRF

### Current behaviour to replace

Current `require_browser_csrf` does:

```python id="u9q7kw"
cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
submitted_token = csrf_header or form["_csrf_token"]
submitted_token == cookie_token
```

That token is not visibly tied to the session.

### New design

Use an HMAC-signed CSRF token bound to the session token hash.

Token shape:

```text id="8kfevv"
nonce.signature
```

Where:

```text id="a2muqz"
signature = HMAC(CSRF_SECRET, session_token_hash + "." + nonce)
```

For pre-login pages that need browser forms, support an anonymous CSRF token bound to an anonymous browser nonce cookie.

Use two cookies:

```text id="fn7rdx"
openscribe_csrf
openscribe_csrf_anon
```

* `openscribe_csrf`: JS-readable signed CSRF token.
* `openscribe_csrf_anon`: HttpOnly random nonce used only when there is no session cookie.

Why anonymous support matters: login, request-access, activation, and reset-password forms may need CSRF before the user has a session.

---

# 4. Create CSRF service module

### New file

```text id="4mp15s"
app/services/csrf.py
```

```python id="szk7d3"
from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256

from app.services.auth import session_token_hash


CSRF_COOKIE_NAME = "openscribe_csrf"
CSRF_ANON_COOKIE_NAME = "openscribe_csrf_anon"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _csrf_secret() -> str:
    value = os.getenv("CSRF_SECRET") or os.getenv("SECRET_KEY")
    if not value:
        # Local/test fallback only. Production startup should reject this.
        value = os.getenv("APP_ENV", "").lower() in {"local", "dev", "development", "test", "testing"} and "dev-only-csrf-secret"
    if not value:
        raise RuntimeError("CSRF_SECRET or SECRET_KEY is required")
    return str(value)


def csrf_secret_configured_for_environment() -> None:
    environment = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
        or "production"
    ).strip().lower()

    if environment in {"production", "prod"} and not (os.getenv("CSRF_SECRET") or os.getenv("SECRET_KEY")):
        raise RuntimeError("CSRF_SECRET or SECRET_KEY is required in production")


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


def session_csrf_token(raw_session_token: str) -> str:
    nonce = secrets.token_urlsafe(24)
    return _encode(f"session:{session_token_hash(raw_session_token)}", nonce)


def anonymous_csrf_token(anon_nonce: str) -> str:
    return _encode(f"anon:{anon_nonce}", anon_nonce)


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
        subject = f"session:{session_token_hash(raw_session_token)}"
    elif anon_nonce:
        subject = f"anon:{anon_nonce}"
        if nonce != anon_nonce:
            return False
    else:
        return False

    expected = _sign(subject, nonce)
    return hmac.compare_digest(signature, expected)
```

Important: this design rotates CSRF naturally whenever a new session token is issued. A token created for the previous session hash will fail after session rotation.

---

## 5. Validate Origin/Referer for unsafe requests

### Add helper in `app/main.py` or `app/services/csrf.py`

```python id="a5i13n"
from urllib.parse import urlsplit


def _origin_allowed(request: Request) -> bool:
    if request.method in CSRF_SAFE_METHODS:
        return True

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    expected_scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
    expected_host = request.headers.get("x-forwarded-host") or request.headers.get("host")

    if not expected_host:
        return False

    expected_origin = f"{expected_scheme}://{expected_host}"

    if origin:
        return origin == expected_origin

    if referer:
        parsed = urlsplit(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}"
        return referer_origin == expected_origin

    return False
```

Then in unsafe CSRF validation, reject when origin/referrer is missing or cross-origin.

Return:

```python id="c6e5ff"
raise AppError(403, "forbidden", "Cross-origin request rejected")
```

Exception: allow test clients if the repo already has `testserver` handling. For tests, set `Origin: http://testserver`.

---

# 6. Update CSRF middleware

### File

```text id="fj6fcy"
app/main.py
```

Replace `CSRF_COOKIE_NAME`, `CSRF_SAFE_METHODS` constants with imports from `app.services.csrf`.

Update imports:

```python id="1z3quk"
from .services.csrf import (
    CSRF_ANON_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_SAFE_METHODS,
    anonymous_csrf_token,
    csrf_secret_configured_for_environment,
    session_csrf_token,
    verify_csrf_token,
)
```

Call startup guard:

```python id="74j77j"
csrf_secret_configured_for_environment()
```

### Replace `ensure_csrf_cookie`

```python id="sb77q1"
@app.middleware("http")
async def ensure_csrf_cookie(request: Request, call_next):
    response = await call_next(request)

    if request.method not in {"GET", "HEAD"}:
        return response

    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )

    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_session_token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=session_csrf_token(raw_session_token),
            httponly=False,
            secure=secure_cookie,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(CSRF_ANON_COOKIE_NAME, path="/")
        return response

    anon_nonce = request.cookies.get(CSRF_ANON_COOKIE_NAME) or secrets.token_urlsafe(24)
    response.set_cookie(
        key=CSRF_ANON_COOKIE_NAME,
        value=anon_nonce,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=anonymous_csrf_token(anon_nonce),
        httponly=False,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    return response
```

This refreshes CSRF on every GET/HEAD. That is acceptable and simple. Existing JS reads the latest cookie.

---

## 7. Update CSRF verification dependencies

### Replace `require_browser_csrf`

```python id="s9kr1u"
async def require_browser_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    if request.method in CSRF_SAFE_METHODS:
        return

    if not _origin_allowed(request):
        raise AppError(403, "forbidden", "Cross-origin request rejected")

    submitted_token = csrf_header
    if submitted_token is None:
        form = await request.form()
        submitted_token = form.get("_csrf_token")

    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    anon_nonce = request.cookies.get(CSRF_ANON_COOKIE_NAME)

    if not submitted_token or not verify_csrf_token(
        submitted_token=str(submitted_token),
        raw_session_token=raw_session_token,
        anon_nonce=anon_nonce,
    ):
        raise AppError(403, "forbidden", "CSRF verification failed")
```

### Keep `require_api_csrf`, but make it use the stronger browser CSRF

```python id="hiiuj6"
async def require_api_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    if request.method in CSRF_SAFE_METHODS:
        return

    has_cookie_backed_authority = bool(
        request.cookies.get(SESSION_COOKIE_NAME)
        or request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME)
    )
    if not has_cookie_backed_authority:
        return

    await require_browser_csrf(request, csrf_header=csrf_header)
```

This keeps Bearer/API-style non-cookie calls possible if they exist later.

---

## 8. Rotate CSRF on login/session rotation

Because the token is bound to the session token hash, rotation happens automatically after the next GET. For JSON login responses, set the new CSRF immediately so SPA/fetch flows do not need a full page reload.

### Add helper

```python id="y2qgtz"
def _set_csrf_cookie_for_session(request: Request, response: Response, token: str) -> None:
    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=session_csrf_token(token),
        httponly=False,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(CSRF_ANON_COOKIE_NAME, path="/")
```

Call this wherever a session cookie is set:

```python id="hur5rw"
_set_session_cookie(request, response, token)
_set_csrf_cookie_for_session(request, response, token)
```

Search for all uses of:

```text id="hc08pz"
_set_session_cookie(
rotate_session(
create_session(
```

Apply after login, MFA completion, activation, password reset flows if they create/rotate sessions.

On logout, clear CSRF too:

```python id="4fd7lx"
def _clear_csrf_cookie(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_ANON_COOKIE_NAME, path="/")
```

Call this in logout.

---

# 9. Frontend impact

`app/static/js/csrf.js` can mostly stay as-is because it reads `openscribe_csrf` and sets `X-CSRF-Token`. 

No major JS change required.

Check templates that set `_csrf_token`; they should still use the `csrf_token` variable from the cookie/template context. If templates currently read cookie at render time, fine. If context stores the raw cookie token, it will now be signed.

---

# 10. Tests

Add or update tests in a dedicated file:

```text id="xer9c5"
tests/test_cookie_csrf_security.py
```

## Test 1: production requires secure cookies

```python id="j4v6s3"
def test_production_requires_cookie_secure_always(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE_MODE", "auto")

    with pytest.raises(RuntimeError, match="COOKIE_SECURE_MODE=always"):
        enforce_production_cookie_security()
```

## Test 2: production requires CSRF secret

```python id="7qpdu3"
def test_production_requires_csrf_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CSRF_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="CSRF_SECRET"):
        csrf_secret_configured_for_environment()
```

## Test 3: HSTS only on HTTPS

```python id="1fojp8"
def test_hsts_added_for_https(client):
    response = client.get("/login", headers={"x-forwarded-proto": "https"})
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_hsts_not_added_for_http(client):
    response = client.get("/login")
    assert "Strict-Transport-Security" not in response.headers
```

## Test 4: unsafe API rejects missing CSRF

```python id="h780ar"
def test_unsafe_cookie_api_rejects_missing_csrf(authenticated_client):
    response = authenticated_client.post(
        "/api/v1/app-preferences",
        json={},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF verification failed"
```

## Test 5: unsafe API rejects cross-origin

```python id="kf9vf0"
def test_unsafe_cookie_api_rejects_cross_origin(authenticated_client, csrf_token):
    response = authenticated_client.post(
        "/api/v1/app-preferences",
        json={},
        headers={
            "Origin": "https://evil.example",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Cross-origin request rejected"
```

## Test 6: session-bound CSRF cannot be reused after login rotation

```python id="1k6jgp"
def test_csrf_bound_to_session_token(client, user):
    # 1. GET login page, capture anonymous CSRF.
    # 2. Login, capture session-bound CSRF.
    # 3. Rotate session via MFA/login flow or manual rotate_session helper.
    # 4. Attempt unsafe API call with old CSRF and new session cookie.
    # 5. Assert 403.
```

## Test 7: anonymous CSRF works for login

```python id="cpzhbo"
def test_login_accepts_anonymous_csrf(client, user):
    get_response = client.get("/login")
    csrf = get_response.cookies["openscribe_csrf"]

    response = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "correct-password"},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf,
        },
    )

    assert response.status_code in {200, 303}
```

Adjust endpoint names to match actual login API/browser tests.

---

# Acceptance criteria

The slice is complete when:

* Production startup fails unless `COOKIE_SECURE_MODE=always`.
* Production startup fails without `CSRF_SECRET` or `SECRET_KEY`.
* HTTPS responses include HSTS.
* Unsafe cookie-authenticated requests require:

  * same-origin `Origin` or `Referer`
  * valid signed CSRF token
  * token bound to current session token hash, or anonymous nonce for pre-login forms
* Login/session rotation invalidates old session-bound CSRF tokens.
* Logout clears session, trusted-device where appropriate, and CSRF cookies.
* Existing frontend `csrfFetch` still works.
* Browser forms still work.
* Tests cover missing CSRF, cross-origin rejection, valid CSRF, session rotation, secure-cookie production guard, and HSTS.

---

## Why this slice now

Account recovery and retention closed two direct PII/control risks. This slice hardens the browser/session layer so future XSS/CSRF/cookie misconfiguration issues have a smaller blast radius.
