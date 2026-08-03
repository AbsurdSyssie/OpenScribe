from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from app.errors import AppError


BLOCKED_PROVIDER_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
}
BLOCKED_PROVIDER_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def provider_host_is_local(host: str) -> bool:
    """Classify supported local-provider hosts while denying metadata targets."""

    normalized_host = host.strip().rstrip(".").lower()
    if normalized_host in BLOCKED_PROVIDER_HOSTNAMES:
        raise ValueError("Provider base URL must not target a cloud metadata service")
    if normalized_host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return False
    comparable_address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
    if comparable_address in BLOCKED_PROVIDER_IPS or comparable_address.is_link_local or comparable_address.is_unspecified:
        raise ValueError("Provider base URL must not target a link-local, unspecified, or cloud metadata address")
    return comparable_address.is_loopback or comparable_address.is_private


def require_safe_provider_url(url: str) -> None:
    """Recheck persisted provider URLs immediately before outbound use."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise AppError(422, "provider_endpoint_blocked", "Provider endpoint must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise AppError(422, "provider_endpoint_blocked", "Provider endpoint must not contain credentials")
    host = parsed.hostname
    if not host:
        raise AppError(422, "provider_endpoint_blocked", "Provider endpoint does not contain a valid host")
    try:
        is_localish = provider_host_is_local(host)
    except ValueError as exc:
        raise AppError(422, "provider_endpoint_blocked", "Provider endpoint is blocked by network safety policy") from exc
    if parsed.scheme != "https" and not is_localish:
        raise AppError(422, "provider_endpoint_blocked", "Remote provider endpoints must use HTTPS")
