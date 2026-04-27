from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import hvac

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.errors import AppError
from app.services.vault import VAULT_ADDR, VAULT_KV_MOUNT, ensure_user_content_transit_ready, ensure_vault_kv_ready


LOCAL_VAULT_DIR = ROOT_DIR / ".local" / "vault"
ROOT_TOKEN_FILE = LOCAL_VAULT_DIR / "root-token"
UNSEAL_KEY_FILE = LOCAL_VAULT_DIR / "unseal-key"
WAIT_TIMEOUT_SECONDS = float(os.getenv("LOCAL_VAULT_WAIT_TIMEOUT_SECONDS", "90"))
WAIT_RETRY_INTERVAL_SECONDS = float(os.getenv("LOCAL_VAULT_WAIT_RETRY_INTERVAL_SECONDS", "1"))


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value.strip()}\n", encoding="utf-8")
    path.chmod(0o600)


def _read_secret(path: Path, *, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(f"Local Vault is initialized but {label} is missing at {path}") from exc
    if not value:
        raise SystemExit(f"Local Vault is initialized but {label} is empty at {path}")
    return value


def _wait_for_vault() -> None:
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    health_url = f"{VAULT_ADDR.rstrip('/')}/v1/sys/health"
    while time.time() < deadline:
        try:
            response = httpx.get(health_url, timeout=2.0)
            if response.status_code in {200, 429, 472, 473, 501, 503}:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(WAIT_RETRY_INTERVAL_SECONDS)
    raise SystemExit(f"Vault did not become ready in time: {last_error}")


def _retry_vault_call(label: str, fn):
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            time.sleep(WAIT_RETRY_INTERVAL_SECONDS)
    raise SystemExit(f"Vault {label} failed after waiting {WAIT_TIMEOUT_SECONDS:.0f}s: {last_error}")


def bootstrap_local_vault() -> None:
    _wait_for_vault()
    client = hvac.Client(url=VAULT_ADDR)

    initialized = _retry_vault_call("initialization check", client.sys.is_initialized)

    if not initialized:
        try:
            response = client.sys.initialize(secret_shares=1, secret_threshold=1)
        except Exception as exc:
            raise SystemExit(f"Vault init failed: {exc}") from exc
        root_token = str(response["root_token"])
        unseal_key = str((response.get("keys_base64") or response["keys"])[0])
        _write_secret(ROOT_TOKEN_FILE, root_token)
        _write_secret(UNSEAL_KEY_FILE, unseal_key)
    else:
        root_token = _read_secret(ROOT_TOKEN_FILE, label="root token")
        unseal_key = _read_secret(UNSEAL_KEY_FILE, label="unseal key")

    if _retry_vault_call("seal status check", client.sys.is_sealed):
        try:
            client.sys.submit_unseal_key(unseal_key)
        except Exception as exc:
            raise SystemExit(f"Vault unseal failed: {exc}") from exc

    os.environ["VAULT_TOKEN_FILE"] = str(ROOT_TOKEN_FILE)
    os.environ.pop("VAULT_TOKEN", None)

    try:
        ensure_vault_kv_ready()
        ensure_user_content_transit_ready()
    except AppError as exc:
        raise SystemExit(f"Vault bootstrap failed: {exc.code}: {exc.message}") from exc


if __name__ == "__main__":
    bootstrap_local_vault()
