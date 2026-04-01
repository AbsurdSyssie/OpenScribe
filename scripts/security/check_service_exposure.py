#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.dev_safety import find_exposed_services, parse_docker_compose_port_host


SERVICE_PORTS: tuple[tuple[str, int], ...] = (
    ("postgres", 5432),
    ("redis", 6379),
    ("vault", 8200),
)


def docker_compose_port(service: str, port: int) -> str | None:
    result = subprocess.run(
        ["docker", "compose", "port", service, str(port)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip().lower()
        if "no port" in stderr or "not found" in stderr:
            return None
        raise RuntimeError(f"Could not inspect docker port for {service}:{port}: {result.stderr.strip()}")
    return parse_docker_compose_port_host(result.stdout)


def collect_port_hosts() -> dict[str, str | None]:
    return {f"{service}:{port}": docker_compose_port(service, port) for service, port in SERVICE_PORTS}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether Docker-published dev services are exposed beyond localhost.")
    parser.add_argument("--allow-remote", action="store_true", help="Allow non-local service exposure without failing.")
    args = parser.parse_args()

    allow_remote = args.allow_remote or os.getenv("DEV_ALLOW_REMOTE_SERVICE_EXPOSURE", "false").strip().lower() == "true"
    port_hosts = collect_port_hosts()
    exposed = find_exposed_services(port_hosts)

    for service_name, host in port_hosts.items():
        print(f"[exposure-check] {service_name} -> {host or 'not-published'}")

    if exposed and not allow_remote:
        print("[exposure-check] ERROR: non-local service bindings detected:", file=sys.stderr)
        for item in exposed:
            print(f"[exposure-check]   {item}", file=sys.stderr)
        print(
            "[exposure-check] Refusing to continue. "
            "Set DEV_ALLOW_REMOTE_SERVICE_EXPOSURE=true only if you explicitly want off-box access.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
