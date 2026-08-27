import re
import secrets
from ipaddress import ip_address
from urllib.parse import urlsplit


OIDC_FORM_ACTION_ORIGINS = (
    "https://accounts.google.com",
    "https://login.microsoftonline.com",
)
DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def new_csp_nonce() -> str:
    return secrets.token_urlsafe(24)


def oidc_form_action_origin(authorization_url: str) -> str:
    try:
        parsed = urlsplit(authorization_url)
    except ValueError:
        raise ValueError("OIDC authorization URL has no safe CSP origin") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("OIDC authorization URL has no safe CSP origin")
    hostname = parsed.hostname.lower()
    if not hostname.isascii() or len(hostname) > 253:
        raise ValueError("OIDC authorization URL has no safe CSP origin")
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if not labels or any(DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels):
            raise ValueError("OIDC authorization URL has no safe CSP origin") from None
        rendered_host = hostname
    else:
        rendered_host = f"[{hostname}]" if parsed_ip.version == 6 else hostname
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("OIDC authorization URL has no safe CSP origin") from None
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{rendered_host}{port_suffix}"


def content_security_policy(
    nonce: str,
    *,
    upgrade_insecure_requests: bool = False,
    oidc_form_action_origins: tuple[str, ...] = (),
) -> str:
    form_action_origins = tuple(
        dict.fromkeys((*OIDC_FORM_ACTION_ORIGINS, *oidc_form_action_origins))
    )
    directives = {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        # Chromium applies form-action to redirects after a form submission.
        # Account linking starts with a same-origin POST and then redirects to
        # the provider, so the built-in providers must be explicit here.
        "form-action": ["'self'", *form_action_origins],
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
