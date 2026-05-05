import secrets


def new_csp_nonce() -> str:
    return secrets.token_urlsafe(24)


def content_security_policy(nonce: str, *, upgrade_insecure_requests: bool = False) -> str:
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
    }

    if upgrade_insecure_requests:
        directives["upgrade-insecure-requests"] = []

    return "; ".join(
        name if not values else f"{name} {' '.join(values)}"
        for name, values in directives.items()
    )
