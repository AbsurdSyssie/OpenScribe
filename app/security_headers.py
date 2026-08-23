import secrets


OIDC_FORM_ACTION_ORIGINS = (
    "https://accounts.google.com",
    "https://login.microsoftonline.com",
)


def new_csp_nonce() -> str:
    return secrets.token_urlsafe(24)


def content_security_policy(nonce: str, *, upgrade_insecure_requests: bool = False) -> str:
    directives = {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        # Chromium applies form-action to redirects after a form submission.
        # Account linking starts with a same-origin POST and then redirects to
        # the provider, so the built-in providers must be explicit here.
        "form-action": ["'self'", *OIDC_FORM_ACTION_ORIGINS],
        "script-src": ["'self'", f"'nonce-{nonce}'", "'wasm-unsafe-eval'"],
        "script-src-attr": ["'none'"],
        "style-src": ["'self'", f"'nonce-{nonce}'"],
        "style-src-attr": ["'none'"],
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
