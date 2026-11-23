"""Renew only the configured runtime identity; never print Vault responses."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vault import vault_client


def renew_runtime_token() -> int:
    try:
        response = vault_client().auth.token.renew_self()
        ttl = int(response["auth"]["lease_duration"])
        if ttl <= 0 or not response["auth"].get("renewable"):
            raise ValueError("Nonrenewable runtime identity")
        return max(1, min(3600, ttl // 3))
    except Exception:
        raise SystemExit("Vault runtime identity renewal failed") from None


if __name__ == "__main__":
    while True:
        time.sleep(renew_runtime_token())
