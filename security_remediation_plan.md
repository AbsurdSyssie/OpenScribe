## Fix: enforce CSP and stop loading runtime JS from public CDNs

The repo currently sets HSTS, `nosniff`, referrer policy, and no-store for sensitive paths, but no CSP.  The transcribe page also loads Tailwind, Lucide, ONNX Runtime, and VAD from public CDNs.  The JS config then points VAD/ONNX runtime paths back to jsDelivr.  That conflicts with the project rule requiring strict CSP because XSS can abuse authenticated endpoints from the victim session. 

Use this fix.

---

# 1. Add CSP builder

Create `app/security_headers.py`:

```python
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class CspConfig:
    report_only: bool = False


def new_csp_nonce() -> str:
    return secrets.token_urlsafe(24)


def content_security_policy(nonce: str) -> str:
    # wasm-unsafe-eval is needed by onnxruntime-web WASM.
    # Keep this narrow: script source is still only self + this response nonce.
    directives = {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "script-src": ["'self'", f"'nonce-{nonce}'", "'wasm-unsafe-eval'"],
        "script-src-attr": ["'none'"],
        "style-src": ["'self'", f"'nonce-{nonce}'"],
        "style-src-attr": ["'unsafe-inline'"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "data:"],
        "connect-src": ["'self'"],
        "media-src": ["'self'", "blob:"],
        "worker-src": ["'self'", "blob:"],
        "manifest-src": ["'self'"],
        "upgrade-insecure-requests": [],
    }

    return "; ".join(
        name if not values else f"{name} {' '.join(values)}"
        for name, values in directives.items()
    )
```

`style-src-attr 'unsafe-inline'` is a temporary compatibility allowance for existing inline style attributes. Do **not** allow `script-src 'unsafe-inline'`.

---

# 2. Wire CSP into `app/main.py`

Add imports:

```python
from .security_headers import content_security_policy, new_csp_nonce
```

Replace the current `add_security_headers` middleware with:

```python
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    request.state.csp_nonce = new_csp_nonce()
    response = await call_next(request)

    if _request_is_https(request):
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", content_security_policy(request.state.csp_nonce))

    if request.url.path.startswith(SENSITIVE_NO_STORE_PATH_PREFIXES):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    return response
```

---

# 3. Add nonces to inline scripts and styles

Every inline `<script>` and `<style>` must receive:

```html
nonce="{{ request.state.csp_nonce }}"
```

At minimum, update `app/templates/_csrf_script.html` because it is an inline script: 

```html
<script nonce="{{ request.state.csp_nonce }}">
  ...
</script>
```

Update `app/templates/transcribe/_head_assets.html`:

```html
<script nonce="{{ request.state.csp_nonce }}">
  ...
</script>

<style nonce="{{ request.state.csp_nonce }}">
  ...
</style>
```

Also update every template returned by this search set:

* `app/templates/login.html`
* `app/templates/home.html`
* `app/templates/admin.html`
* `app/templates/onboarding.html`
* `app/templates/mfa_challenge.html`
* `app/templates/request_access.html`
* `app/templates/password_reset_request.html`
* `app/templates/password_reset_confirm.html`
* `app/templates/template_editor.html`
* `app/templates/transcribe/_shell_extras.html`

---

# 4. Replace CDN runtime JS with local static files

Change the top of `app/templates/transcribe/_head_assets.html` from CDN scripts to local paths:

```html
<link rel="stylesheet" href="/static/css/transcribe-tailwind.css">
<script src="/static/vendor/lucide/1.8.0/lucide.min.js" defer></script>
<script src="/static/vendor/onnxruntime-web/1.22.0/ort.wasm.min.js" defer></script>
<script src="/static/vendor/vad-web/0.0.29/bundle.min.js" defer></script>
```

Remove these external lines entirely:

```html
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lucide@1.8.0"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/..." rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/ort.wasm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.29/dist/bundle.min.js"></script>
```

Then update `app/static/js/transcribe/app.js`:

```js
const liveVadBundleVersion = '0.0.29';
const liveVadModel = 'v5';
const liveVadAssetBasePath = `/static/vendor/vad-web/${liveVadBundleVersion}/`;
const liveVadOnnxBasePath = '/static/vendor/onnxruntime-web/1.22.0/';
```

---

# 5. Vendor the browser assets into the repo or build artifact

Expected layout:

```text
app/static/vendor/
  lucide/1.8.0/lucide.min.js
  onnxruntime-web/1.22.0/ort.wasm.min.js
  onnxruntime-web/1.22.0/ort-wasm-simd-threaded.wasm
  onnxruntime-web/1.22.0/ort-wasm-simd-threaded.mjs
  onnxruntime-web/1.22.0/ort-wasm-simd-threaded.jsep.wasm
  onnxruntime-web/1.22.0/ort-wasm-simd-threaded.jsep.mjs
  vad-web/0.0.29/bundle.min.js
  vad-web/0.0.29/silero_vad_v5.onnx
app/static/css/transcribe-tailwind.css
```

Recommended build approach:

```bash
npm install --save-dev tailwindcss@3.4.17
npx tailwindcss \
  -i app/static/css/transcribe-tailwind.input.css \
  -o app/static/css/transcribe-tailwind.css \
  --minify
```

For VAD/ONNX/Lucide, either commit the pinned static files or download them in CI from exact versioned package URLs and verify SHA-256 checksums before copying into `app/static/vendor`.

Do **not** keep runtime browser dependencies on `cdn.tailwindcss.com`, `unpkg.com`, `cdn.jsdelivr.net`, `fonts.googleapis.com`, or `fonts.gstatic.com`.

---

# 6. Add tests

Extend `tests/test_cookie_csrf_security.py`:

```python
import re


def test_csp_header_added(raw_client):
    response = raw_client.get("/login")

    csp = response.headers["Content-Security-Policy"]

    assert "default-src 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert re.search(r"'nonce-[A-Za-z0-9_-]+'", csp)


def test_csp_nonce_changes_per_response(raw_client):
    first = raw_client.get("/login").headers["Content-Security-Policy"]
    second = raw_client.get("/login").headers["Content-Security-Policy"]

    assert first != second


def test_transcribe_head_assets_do_not_use_public_cdns():
    html = Path("app/templates/transcribe/_head_assets.html").read_text()

    forbidden = [
        "cdn.tailwindcss.com",
        "unpkg.com",
        "cdn.jsdelivr.net",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
    ]

    for host in forbidden:
        assert host not in html
```

Add this import:

```python
from pathlib import Path
```

Also add a simple static check for JS paths:

```python
def test_transcribe_js_uses_local_vad_assets():
    js = Path("app/static/js/transcribe/app.js").read_text()

    assert "cdn.jsdelivr.net" not in js
    assert "/static/vendor/vad-web/" in js
    assert "/static/vendor/onnxruntime-web/" in js
```

Tests/docs are mandatory for this project’s change workflow. 

---

# 7. Update docs

Add to `docs/security.md`:

```md
## Browser CSP and local runtime assets

OpenScribe enforces a response-specific nonce-based Content Security Policy.

Browser pages must not load runtime JavaScript, WASM, ONNX models, CSS, or fonts from public CDNs in production. Runtime browser assets are served from `/static/vendor` or compiled into `/static/css`.

Current CSP goals:

- script execution only from `'self'` and response nonces
- no `script-src 'unsafe-inline'`
- no third-party `script-src`, `style-src`, `font-src`, or `connect-src`
- `frame-ancestors 'none'`
- `object-src 'none'`
- same-origin API/WebSocket/EventSource connections only
- WASM allowed only through the narrow ONNX Runtime requirement
```

---

## PR acceptance checklist

Before merging:

```bash
pytest tests/test_cookie_csrf_security.py
grep -R "cdn.jsdelivr.net\|cdn.tailwindcss.com\|unpkg.com\|fonts.googleapis.com\|fonts.gstatic.com" app/templates app/static/js
```

Expected grep result: no matches.

This fix does not change ownership, auth, deletion, provider resolution, or transcript-content visibility. It only narrows browser execution sources and removes runtime public-CDN dependency.
