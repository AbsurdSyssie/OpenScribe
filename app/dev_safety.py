from __future__ import annotations

import ipaddress


LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}
WILDCARD_BIND_HOSTS = {"0.0.0.0", "::", "[::]"}


def is_local_bind_host(host: str | None) -> bool:
    if host is None:
        return False
    candidate = host.strip().strip("[]")
    if not candidate:
        return False
    if candidate in LOCAL_BIND_HOSTS:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def resolve_dev_bind_host(*, host: str | None, allow_remote: bool) -> str:
    candidate = (host or "").strip()
    if not candidate:
        return "127.0.0.1"
    if candidate in WILDCARD_BIND_HOSTS:
        return "0.0.0.0" if allow_remote else "127.0.0.1"
    if is_local_bind_host(candidate):
        return candidate
    if allow_remote:
        return candidate
    raise ValueError(
        "Refusing to bind the dev server to a non-local interface. "
        f"APP_HOST={candidate!r}. Set DEV_ALLOW_REMOTE_BIND=true only if you explicitly want LAN exposure."
    )


def ensure_safe_dev_bind(*, host: str | None, allow_remote: bool) -> None:
    resolve_dev_bind_host(host=host, allow_remote=allow_remote)


def parse_docker_compose_port_host(output: str) -> str | None:
    value = output.strip().splitlines()[0] if output.strip() else ""
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.startswith(":::"):
        return "::"
    if ":" not in value:
        return value
    host, _port = value.rsplit(":", 1)
    return host


def find_exposed_services(port_hosts: dict[str, str | None]) -> list[str]:
    exposed: list[str] = []
    for service_name, host in port_hosts.items():
        if host is None:
            continue
        if not is_local_bind_host(host):
            exposed.append(f"{service_name}={host}")
    return exposed
