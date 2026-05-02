# API CSRF Hardening

## Purpose

This note describes the implementation plan for tightening CSRF protection on cookie-authenticated JSON API routes.

The application already has an `openscribe_csrf` cookie, a `require_browser_csrf` dependency, and HTML form routes that use `BrowserCsrf`. The gap is that `/api/v1` state-changing JSON routes rely on cookie-backed authority but do not consistently require a CSRF token, and frontend `fetch()` calls do not currently appear to send `X-CSRF-Token`.

This slice is focused only on CSRF hardening. Do not mix in CSP, provider allowlisting, cookie restructuring, Celery audio, Vault, or UI redesign work.

## Target behaviour

```text
GET /api/v1/...               -> no CSRF required
HEAD /api/v1/...              -> no CSRF required
OPTIONS /api/v1/...           -> no CSRF required
POST/PATCH/PUT/DELETE /api/v1/... -> CSRF required when cookie-backed authority is present
Public unauthenticated APIs   -> remain callable without CSRF when no auth/trusted-device cookie is present
HTML forms                    -> unchanged; already use BrowserCsrf
```

## Existing project support

The existing CSRF model in `app/main.py` already provides:

```python
CSRF_COOKIE_NAME = "openscribe_csrf"

async def require_browser_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    submitted_token = csrf_header
    if submitted_token is None:
        form = await request.form()
        submitted_token = form.get("_csrf_token")
    if not cookie_token or not submitted_token or submitted_token != cookie_token:
        raise AppError(403, "forbidden", "CSRF verification failed")


BrowserCsrf = Annotated[None, Depends(require_browser_csrf)]
```

There is also middleware that issues `openscribe_csrf` on safe browser requests. This patch should reuse that existing mechanism.

## Files to change

```text
app/main.py
app/static/js/csrf.js
app/static/js/transcribe/actions.js
app/static/js/transcribe/app.js
app/static/js/transcribe/media.js
app/static/js/home/smart-phrases.js
tests/...
docs/progress/2026-05-02-api-csrf-hardening.md
```

## Required changes

### 1. Add API-level CSRF dependency in `app/main.py`

Use the existing `require_browser_csrf` rather than creating a second CSRF token system.

Move `api = APIRouter(prefix="/api/v1")` so it is created after the new dependency is defined.

```diff
diff --git a/app/main.py b/app/main.py
--- a/app/main.py
+++ b/app/main.py
@@
 app = FastAPI(title="OpenScribe MVP")
-api = APIRouter(prefix="/api/v1")
 CSRF_COOKIE_NAME = "openscribe_csrf"
+CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
+API_CSRF_PUBLIC_UNSAFE_EXEMPT_PATHS = {
+    "/api/v1/auth/login",
+    "/api/v1/auth/password-reset/request",
+    "/api/v1/auth/password-reset/confirm",
+    "/api/v1/auth/account-activation/confirm",
+    "/api/v1/account-requests",
+}
 LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}
 STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
```

Add this after `require_browser_csrf` and `BrowserCsrf`:

```diff
@@
 BrowserCsrf = Annotated[None, Depends(require_browser_csrf)]
+
+
+async def require_api_csrf(
+    request: Request,
+    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
+) -> None:
+    if request.method in CSRF_SAFE_METHODS:
+        return
+
+    has_cookie_backed_authority = bool(
+        request.cookies.get(SESSION_COOKIE_NAME)
+        or request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME)
+    )
+    if request.url.path in API_CSRF_PUBLIC_UNSAFE_EXEMPT_PATHS and not has_cookie_backed_authority:
+        return
+
+    await require_browser_csrf(request, csrf_header=csrf_header)
+
+
+api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_csrf)])
```

### Why this shape

This avoids editing every API route individually. It also avoids requiring CSRF on anonymous public endpoints unless an auth or trusted-device cookie is already present.

These remain callable without CSRF when unauthenticated:

```text
POST /api/v1/auth/login
POST /api/v1/auth/password-reset/request
POST /api/v1/auth/password-reset/confirm
POST /api/v1/auth/account-activation/confirm
POST /api/v1/account-requests
```

Once cookies are involved, unsafe API calls must carry the CSRF token.

## 2. Add frontend helper: `app/static/js/csrf.js`

Create a shared helper that adds `X-CSRF-Token` for unsafe requests only.

```js
const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export function readCookie(name) {
  const prefix = `${name}=`;
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) || '';
}

export function csrfToken() {
  return readCookie('openscribe_csrf');
}

export function csrfHeaders(existingHeaders = {}) {
  const headers = new Headers(existingHeaders || {});
  const token = csrfToken();
  if (token) {
    headers.set('X-CSRF-Token', token);
  }
  return headers;
}

export function csrfFetch(input, init = {}) {
  const method = String(init.method || 'GET').toUpperCase();
  if (!UNSAFE_METHODS.has(method)) {
    return fetch(input, init);
  }

  return fetch(input, {
    ...init,
    headers: csrfHeaders(init.headers),
  });
}
```

Important: this helper must not set `Content-Type` by default. FormData uploads need the browser to set the multipart boundary automatically.

## 3. Update frontend API callers

Replace unsafe `fetch()` calls with `csrfFetch()`.

Safe `GET` calls can remain plain `fetch`, but it is acceptable to use `csrfFetch` for all API calls because the helper only adds a header for unsafe methods.

### 3.1 `app/static/js/transcribe/actions.js`

Add import:

```diff
diff --git a/app/static/js/transcribe/actions.js b/app/static/js/transcribe/actions.js
--- a/app/static/js/transcribe/actions.js
+++ b/app/static/js/transcribe/actions.js
@@
+import { csrfFetch } from '../csrf.js';
+
 export function attachTranscribeActions({
```

Replace every unsafe API `fetch(...)` call with `csrfFetch(...)`.

Examples:

```diff
-const response = await fetch(`/api/v1/generated-documents/${generatedDocumentId}`, {
+const response = await csrfFetch(`/api/v1/generated-documents/${generatedDocumentId}`, {
   method: 'DELETE',
   credentials: 'include',
 });
```

```diff
-const response = await fetch('/api/v1/transcripts/start', {
+const response = await csrfFetch('/api/v1/transcripts/start', {
   method: 'POST',
   credentials: 'include',
   headers: { 'Content-Type': 'application/json' },
   body: JSON.stringify({ title: 'Untitled session', ingestion_mode: preferredMode }),
 });
```

```diff
-const response = await fetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
+const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
   method: 'POST',
   body: formData,
   credentials: 'include',
 });
```

Apply to every `POST`, `PATCH`, and `DELETE` API call in `actions.js`, including generated document deletion, transcript start, transcript patch, transcript deletion, audio upload, note generation, follow-up generation, and quick action execution.

### 3.2 `app/static/js/transcribe/app.js`

Add import:

```diff
@@
 import { attachTranscribeActions } from './actions.js?v=20260421-pii-refresh';
+import { csrfFetch } from '../csrf.js';
```

Replace unsafe calls.

Examples:

```diff
-const response = await fetch('/api/v1/app-preferences', {
+const response = await csrfFetch('/api/v1/app-preferences', {
   method: 'POST',
   credentials: 'include',
   headers: { 'Content-Type': 'application/json' },
   body: JSON.stringify(nextPreferences),
 });
```

```diff
-const response = await fetch(`/api/v1/transcripts/${transcriptId}/post-consultation-dictation`, {
+const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/post-consultation-dictation`, {
   method: 'PATCH',
   credentials: 'include',
   headers: { 'Content-Type': 'application/json' },
   keepalive,
   body: JSON.stringify({ combined_text: combinedText }),
 });
```

```diff
-const response = await fetch(`/api/v1/generated-documents/${saveRequest.generatedDocumentId}`, {
+const response = await csrfFetch(`/api/v1/generated-documents/${saveRequest.generatedDocumentId}`, {
   method: 'PATCH',
   credentials: 'include',
   headers: { 'Content-Type': 'application/json' },
   keepalive,
   body: JSON.stringify(saveRequest.payload),
 });
```

Apply to every `POST`, `PATCH`, and `DELETE` API call in `app.js`.

### 3.3 `app/static/js/transcribe/media.js`

Add import:

```diff
@@
+import { csrfFetch } from '../csrf.js';
+
 export function createAudioCaptureController({
```

Replace unsafe upload calls:

```diff
-const response = await fetch(`/api/v1/transcripts/${transcriptId}/audio-chunks`, {
+const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/audio-chunks`, {
   method: 'POST',
   body: formData,
   credentials: 'include',
 });
```

```diff
-const response = await fetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
+const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
   method: 'POST',
   body: formData,
   credentials: 'include',
 });
```

### 3.4 `app/static/js/home/smart-phrases.js`

Add import:

```diff
@@
+import { csrfFetch } from '../csrf.js';
```

Replace unsafe API calls:

```diff
-fetch(`/api/v1/smart-phrases/personal`, ...)
+csrfFetch(`/api/v1/smart-phrases/personal`, ...)
```

```diff
-fetch(`/api/v1/smart-phrases/personal/${id}`, ...)
+csrfFetch(`/api/v1/smart-phrases/personal/${id}`, ...)
```

```diff
-fetch(`/api/v1/smart-phrases/personal/${id}/used`, ...)
+csrfFetch(`/api/v1/smart-phrases/personal/${id}/used`, ...)
```

## Tests to add

Use existing auth/session fixtures where available. Fixture names below are placeholders and should be adapted to the existing test suite.

### 1. Unsafe authenticated API request without CSRF is rejected

```python
def test_authenticated_unsafe_api_requires_csrf(client, full_user_session_cookie):
    response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF probe", "ingestion_mode": "whole_file"},
        cookies={"openscribe_session": full_user_session_cookie},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF verification failed"
```

### 2. Unsafe authenticated API request with CSRF is accepted

```python
def test_authenticated_unsafe_api_accepts_matching_csrf(client, full_user_session_cookie):
    csrf = "test-csrf-token"

    response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF ok", "ingestion_mode": "whole_file"},
        cookies={
            "openscribe_session": full_user_session_cookie,
            "openscribe_csrf": csrf,
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code in {200, 201}
```

Adjust the expected status to match existing `POST /api/v1/transcripts/start` behaviour.

### 3. Mismatched CSRF is rejected

```python
def test_authenticated_unsafe_api_rejects_mismatched_csrf(client, full_user_session_cookie):
    response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF mismatch", "ingestion_mode": "whole_file"},
        cookies={
            "openscribe_session": full_user_session_cookie,
            "openscribe_csrf": "cookie-token",
        },
        headers={"X-CSRF-Token": "header-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF verification failed"
```

### 4. Safe API request does not require CSRF

```python
def test_authenticated_safe_api_does_not_require_csrf(client, full_user_session_cookie):
    response = client.get(
        "/api/v1/auth/me",
        cookies={"openscribe_session": full_user_session_cookie},
    )

    assert response.status_code == 200
```

### 5. Public unauthenticated login remains callable without CSRF

```python
def test_public_login_without_existing_auth_cookie_does_not_require_csrf(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong"},
    )

    assert response.status_code in {401, 422}
    assert response.json()["error"]["message"] != "CSRF verification failed"
```

### 6. Public endpoint with existing auth cookie requires CSRF

```python
def test_public_unsafe_endpoint_with_auth_cookie_requires_csrf(client, full_user_session_cookie):
    response = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Probe",
            "requested_email": "probe@example.com",
            "requested_team_name": "Probe Team",
            "request_details": "csrf probe",
        },
        cookies={"openscribe_session": full_user_session_cookie},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "CSRF verification failed"
```

## Regression areas to check

```text
- Login page still works without a pre-existing CSRF cookie.
- Password reset request still works without a pre-existing CSRF cookie.
- Password reset confirm still works from emailed link flow.
- Account activation confirm still works from emailed link flow.
- Public account request still works for anonymous users.
- Authenticated users cannot call unsafe public endpoints without CSRF.
- New consultation creation works from the transcribe UI.
- Audio file upload works; multipart boundary must not be broken.
- Live audio chunk upload works; multipart boundary must not be broken.
- Note autosave works with keepalive and X-CSRF-Token.
- Dictation autosave works with keepalive and X-CSRF-Token.
- Generated document delete works.
- Smart phrase create/update/delete/used works.
```

## Implementation checklist

```text
- Add require_api_csrf dependency to /api/v1 router.
- No-op CSRF on GET/HEAD/OPTIONS.
- Exempt public unsafe endpoints only when no session/trusted-device cookie exists.
- Add csrfFetch helper.
- Use csrfFetch for frontend POST/PATCH/DELETE calls.
- Keep FormData uploads working by not overriding Content-Type.
- Add tests for missing, matching, and mismatched CSRF.
- Add tests that safe methods still work.
- Add tests that anonymous public endpoints remain callable.
```

## Documentation entry

Create:

```text
docs/progress/2026-05-02-api-csrf-hardening.md
```

Suggested content:

```md
# API CSRF Hardening

## 1. Scope
- Required CSRF verification for unsafe cookie-backed `/api/v1` requests.
- Added a shared frontend `csrfFetch` helper.
- Updated transcribe/home API mutations to send `X-CSRF-Token` from `openscribe_csrf`.
- Preserved anonymous public endpoint compatibility.

## 2. Checklist
- [x] Code complete
- [x] Tests added/updated
- [x] Docs added/updated

## 3. Files changed
- `app/main.py`: `/api/v1` router now depends on `require_api_csrf`.
- `app/static/js/csrf.js`: added cookie/header helper and `csrfFetch`.
- `app/static/js/transcribe/actions.js`: unsafe API calls use `csrfFetch`.
- `app/static/js/transcribe/app.js`: unsafe API calls use `csrfFetch`.
- `app/static/js/transcribe/media.js`: upload calls use `csrfFetch`.
- `app/static/js/home/smart-phrases.js`: unsafe smart phrase calls use `csrfFetch`.
- `tests/...`: regression coverage for missing/matching/mismatched CSRF.

## 4. Tests
- Verified unsafe authenticated API calls without CSRF fail with 403.
- Verified unsafe authenticated API calls with matching CSRF pass.
- Verified mismatched CSRF fails with 403.
- Verified safe authenticated API calls do not require CSRF.
- Verified anonymous public endpoints remain callable without CSRF.
- Verified public unsafe endpoints require CSRF when auth cookies exist.

## 5. Risks / assumptions
- `openscribe_csrf` remains intentionally readable by JavaScript.
- FormData requests must not receive a forced `Content-Type` header.
- Public unsafe endpoint exemptions are path-specific and only apply when no cookie-backed authority is present.

## 6. Checkpoint summary
- Cookie-backed state mutation now requires a same-origin readable CSRF token.
- Login/reset/activation/account-request flows remain compatible for unauthenticated users.
- Existing HTML form CSRF behaviour is unchanged.
- Session/cookie structure is unchanged.
```

## Commit message

```text
Harden API CSRF protection
```

Longer commit message:

```text
Harden API CSRF protection

Require CSRF verification for unsafe cookie-backed /api/v1 requests. Add a
shared frontend csrfFetch helper that sends X-CSRF-Token from the existing
openscribe_csrf cookie, and update transcribe/home API mutations to use it.
Keep safe methods and unauthenticated public endpoints compatible.
```
