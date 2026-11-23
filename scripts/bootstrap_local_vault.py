from __future__ import annotations

import os
import re
import json
import tempfile
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


LOCAL_VAULT_DIR = Path(os.getenv("LOCAL_VAULT_BOOTSTRAP_DIR", str(ROOT_DIR / ".local" / "vault")))
RUNTIME_TOKEN_FILE = Path(os.getenv("LOCAL_VAULT_RUNTIME_TOKEN_FILE", str(ROOT_DIR / ".local" / "vault-runtime" / "token")))
ROOT_TOKEN_FILE = LOCAL_VAULT_DIR / "root-token"
UNSEAL_KEY_FILE = LOCAL_VAULT_DIR / "unseal-key"
WAIT_TIMEOUT_SECONDS = float(os.getenv("LOCAL_VAULT_WAIT_TIMEOUT_SECONDS", "90"))
WAIT_RETRY_INTERVAL_SECONDS = float(os.getenv("LOCAL_VAULT_WAIT_RETRY_INTERVAL_SECONDS", "1"))


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        temporary = Path(handle.name)
        try:
            handle.write(f"{value.strip()}\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _read_secret(path: Path, *, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(
            f"Local Vault is initialized but {label} is missing at {path}. "
            "Restore the bootstrap files into the Vault bootstrap location without "
            "printing their contents; see the Vault troubleshooting section in "
            "docs/docker.md."
        ) from None
    if not value:
        raise SystemExit(
            f"Local Vault is initialized but {label} is empty at {path}. "
            "Restore the bootstrap files into the Vault bootstrap location without "
            "printing their contents; see the Vault troubleshooting section in "
            "docs/docker.md."
        )
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
    raise SystemExit("Vault did not become ready in time")


def _retry_vault_call(label: str, fn):
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            time.sleep(WAIT_RETRY_INTERVAL_SECONDS)
    raise SystemExit(f"Vault {label} failed after waiting {WAIT_TIMEOUT_SECONDS:.0f}s")


def bootstrap_local_vault() -> None:
    # Validate policy inputs before privileged operations.
    runtime_policy()
    _wait_for_vault()
    client = hvac.Client(url=VAULT_ADDR)

    initialized = _retry_vault_call("initialization check", client.sys.is_initialized)

    if not initialized:
        if any(path.exists() for path in (ROOT_TOKEN_FILE, UNSEAL_KEY_FILE, RUNTIME_TOKEN_FILE)):
            raise SystemExit("Vault is uninitialized but recovery material exists; restore matching Vault storage")
        try:
            response = client.sys.initialize(secret_shares=1, secret_threshold=1)
        except Exception as exc:
            raise SystemExit("Vault init failed") from None
        root_token = str(response["root_token"])
        unseal_key = str((response.get("keys_base64") or response["keys"])[0])
        _write_secret(ROOT_TOKEN_FILE, root_token)
        _write_secret(UNSEAL_KEY_FILE, unseal_key)
    else:
        root_token = None
        unseal_key = _read_secret(UNSEAL_KEY_FILE, label="unseal key")

    if _retry_vault_call("seal status check", client.sys.is_sealed):
        try:
            client.sys.submit_unseal_key(unseal_key)
        except Exception as exc:
            raise SystemExit("Vault unseal failed") from None

    # A healthy scoped lease supports ordinary restarts after root retirement.
    if RUNTIME_TOKEN_FILE.exists() and os.getenv("LOCAL_VAULT_FORCE_PROVISION") != "true":
        runtime = hvac.Client(url=VAULT_ADDR, token=_read_secret(RUNTIME_TOKEN_FILE, label="runtime token"))
        try:
            data = runtime.auth.token.lookup_self()["data"]
            if set(data.get("policies", [])) == {"openscribe-runtime"} and data.get("period") == 86400:
                runtime.auth.token.renew_self()
                return
        except (hvac.exceptions.InvalidRequest, hvac.exceptions.Forbidden):
            pass
    root_token = root_token or _read_secret(ROOT_TOKEN_FILE, label="root token")

    from app.services import vault
    vault.VAULT_TOKEN_FILE = str(ROOT_TOKEN_FILE)
    os.environ["VAULT_TOKEN_FILE"] = str(ROOT_TOKEN_FILE)
    os.environ.pop("VAULT_TOKEN", None)

    try:
        ensure_vault_kv_ready()
        ensure_user_content_transit_ready()
    except AppError as exc:
        raise SystemExit(f"Vault bootstrap failed: {exc.code}: {exc.message}") from None

    client.token = root_token
    try:
        policy = runtime_policy()
        client.sys.create_or_update_policy(name="openscribe-runtime", policy=policy)
        # Preserve a healthy token on repeat bootstrap; renewal does not require root.
        token = None
        if RUNTIME_TOKEN_FILE.exists():
            candidate = _read_secret(RUNTIME_TOKEN_FILE, label="runtime token")
            runtime = hvac.Client(url=VAULT_ADDR, token=candidate)
            try:
                data = runtime.auth.token.lookup_self()["data"]
                if set(data.get("policies", [])) == {"openscribe-runtime"} and data.get("period") == 86400:
                    runtime.auth.token.renew_self()
                    token = candidate
            except hvac.exceptions.InvalidRequest:
                pass
            except hvac.exceptions.Forbidden:
                pass
        if token is None:
            response = client.auth.token.create(
                policies=["openscribe-runtime"], no_default_policy=True,
                no_parent=True, period="24h", renewable=True,
                display_name="openscribe-runtime",
            )
            _write_secret(RUNTIME_TOKEN_FILE, response["auth"]["client_token"])
    except Exception as exc:
        raise SystemExit("Vault runtime identity provisioning failed") from None


def runtime_policy() -> str:
    from app.services.vault import VAULT_TRANSIT_MOUNT, VAULT_USER_CONTENT_KEK_KEY_NAME
    kv = VAULT_KV_MOUNT.strip("/")
    transit = VAULT_TRANSIT_MOUNT.strip("/")
    key = VAULT_USER_CONTENT_KEK_KEY_NAME
    for value in (kv, transit):
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value):
            raise ValueError("Invalid Vault mount")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError("Invalid Vault Transit key name")
    # JSON HCL syntax avoids configuration becoming policy syntax.
    paths = {
        **{f"{kv}/data/openscribe/{prefix}/*": {"capabilities": ["create", "read", "update"]}
           for prefix in ("stt/team", "llm/team", "deidentification/provider", "transcript-ingestion")},
        **{f"{kv}/metadata/openscribe/{prefix}/*": {"capabilities": ["delete"]}
           for prefix in ("stt/team", "llm/team", "deidentification/provider", "transcript-ingestion")},
        **{f"{kv}/data/openscribe/platform/{name}": {"capabilities": ["create", "read", "update"]}
           for name in ("csrf", "oidc-subject-hash")},
        f"{kv}/data/openscribe/oidc/*": {"capabilities": ["read"]},
        **{f"{transit}/{operation}/{key}": {"capabilities": ["update"]}
           for operation in ("decrypt", "rewrap")},
        f"{transit}/datakey/plaintext/{key}": {"capabilities": ["update"]},
        "auth/token/lookup-self": {"capabilities": ["read"]},
        "auth/token/renew-self": {"capabilities": ["update"]},
    }
    # Operator-selected legacy refs remain usable without granting a namespace wildcard.
    for variable, capabilities in (
        ("RESEND_API_KEY_VAULT_REF", ["read"]),
        ("CSRF_SECRET_VAULT_REF", ["create", "read", "update"]),
        ("OIDC_SUBJECT_HASH_SECRET_VAULT_REF", ["create", "read", "update"]),
    ):
        reference = os.getenv(variable, "").strip()
        if reference:
            mount, separator, path = reference.partition(":")
            if not separator or mount != kv or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path):
                raise ValueError("Unsupported runtime Vault reference")
            if variable == "OIDC_SUBJECT_HASH_SECRET_VAULT_REF" and path != "openscribe/platform/oidc-subject-hash":
                raise ValueError("Invalid OIDC subject-hash Vault reference")
            paths[f"{kv}/data/{path}"] = {"capabilities": capabilities}
    return json.dumps({"path": paths})


if __name__ == "__main__":
    bootstrap_local_vault()
