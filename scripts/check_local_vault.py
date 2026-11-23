"""Cheap local Vault readiness check using only the scoped runtime identity."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def check_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    token_path = Path(os.getenv("LOCAL_VAULT_RUNTIME_TOKEN_FILE", str(root / ".local" / "vault-runtime" / "token")))
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Runtime identity unavailable")
    address = urlsplit(os.getenv("VAULT_ADDR", "http://127.0.0.1:8200"))
    if (address.scheme not in {"http", "https"} or not address.hostname
            or address.username is not None or address.password is not None
            or address.query or address.fragment):
        raise ValueError("Invalid Vault address")
    connection_type = http.client.HTTPSConnection if address.scheme == "https" else http.client.HTTPConnection
    # Direct connections neither consult proxy environment variables nor follow
    # redirects, so the runtime credential stays at the configured Vault address.
    connection = connection_type(address.hostname, address.port, timeout=2)
    try:
        connection.request("GET", f"{address.path.rstrip('/')}/v1/auth/token/lookup-self",
                           headers={"X-Vault-Token": token})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Runtime identity unavailable")
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("Runtime identity unavailable")
        data = json.loads(body)["data"]
        if set(data.get("policies", [])) != {"openscribe-runtime"} or int(data.get("ttl", 0)) <= 0:
            raise ValueError("Runtime identity unavailable")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        check_runtime()
    except Exception:
        raise SystemExit("Vault runtime identity unavailable") from None
