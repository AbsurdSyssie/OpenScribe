"""Local-only unseal supervisor, isolated from application containers."""
from __future__ import annotations

import os
import sys
import time

import hvac

from bootstrap_local_vault import (
    RUNTIME_TOKEN_FILE, UNSEAL_KEY_FILE, VAULT_ADDR, _read_secret, bootstrap_local_vault,
)


def check_runtime() -> None:
    client = hvac.Client(url=VAULT_ADDR, token=_read_secret(RUNTIME_TOKEN_FILE, label="runtime token"))
    data = client.auth.token.lookup_self()["data"]
    if set(data.get("policies", [])) != {"openscribe-runtime"} or int(data.get("ttl", 0)) <= 0:
        raise ValueError("Runtime identity unavailable")


if __name__ == "__main__":
    if "--check" in sys.argv:
        try:
            check_runtime()
        except Exception:
            raise SystemExit("Vault runtime identity unavailable") from None
    else:
        bootstrap_local_vault()
        os.chown(RUNTIME_TOKEN_FILE, 10001, 10001)
        # Steady state never provisions policy or reads the root token. Vault restarts
        # only require unseal; lease renewal belongs to the application supervisor.
        while True:
            try:
                client = hvac.Client(url=VAULT_ADDR)
                if client.sys.is_sealed():
                    client.sys.submit_unseal_key(_read_secret(UNSEAL_KEY_FILE, label="unseal key"))
            except Exception:
                print("Local Vault unseal check unavailable", file=sys.stderr)
            time.sleep(5)
