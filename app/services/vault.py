import base64
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import hvac
import httpx
from hvac import exceptions as hvac_exceptions

from app.errors import AppError


VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "root")
VAULT_TOKEN_FILE = os.getenv("VAULT_TOKEN_FILE")
VAULT_KV_MOUNT = os.getenv("VAULT_KV_MOUNT", "secret")
VAULT_TRANSIT_MOUNT = os.getenv("VAULT_TRANSIT_MOUNT", "transit")
VAULT_USER_CONTENT_KEK_KEY_NAME = os.getenv("VAULT_USER_CONTENT_KEK_KEY_NAME", "openscribe-user-content-kek")
DEFAULT_CSRF_SECRET_REF = f"{VAULT_KV_MOUNT}:openscribe/platform/csrf"
DEFAULT_OIDC_SUBJECT_HASH_SECRET_REF = (
    f"{VAULT_KV_MOUNT}:openscribe/platform/oidc-subject-hash"
)
DEFAULT_LOCAL_VAULT_TOKEN_FILE = Path(__file__).resolve().parents[2] / ".local" / "vault" / "root-token"


def _default_local_vault_token_file() -> Path | None:
    if not DEFAULT_LOCAL_VAULT_TOKEN_FILE.exists():
        return None
    if VAULT_ADDR.rstrip("/") != "http://127.0.0.1:8200":
        return None
    if VAULT_TOKEN_FILE:
        return None
    if VAULT_TOKEN and VAULT_TOKEN != "root":
        return None
    return DEFAULT_LOCAL_VAULT_TOKEN_FILE


def _resolve_vault_token() -> str | None:
    token_file = Path(VAULT_TOKEN_FILE) if VAULT_TOKEN_FILE else _default_local_vault_token_file()
    if token_file:
        try:
            with open(token_file, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError as exc:
            raise AppError(502, "vault_unavailable", "Vault token file is unavailable") from exc
        return token or None
    return VAULT_TOKEN or None


def _vault_headers() -> dict[str, str]:
    token = _resolve_vault_token()
    if not token:
        raise AppError(502, "vault_unavailable", "Vault token is not configured")
    return {"X-Vault-Token": token}


def vault_client() -> hvac.Client:
    try:
        client = hvac.Client(url=VAULT_ADDR, token=_resolve_vault_token())
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    return client


def ensure_user_content_transit_ready() -> None:
    client = vault_client()
    mount_path = f"{VAULT_TRANSIT_MOUNT.strip('/')}/"
    try:
        mounts_response = client.sys.list_mounted_secrets_engines()
    except Exception as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    mounts = (mounts_response or {}).get("data") or {}
    if mount_path not in mounts:
        try:
            client.sys.enable_secrets_engine(
                backend_type="transit",
                path=VAULT_TRANSIT_MOUNT.strip("/"),
            )
        except hvac_exceptions.InvalidRequest:
            pass
        except Exception as exc:
            raise AppError(502, "vault_bootstrap_failed", "Vault Transit mount could not be created") from exc
    try:
        client.secrets.transit.read_key(
            name=VAULT_USER_CONTENT_KEK_KEY_NAME,
            mount_point=VAULT_TRANSIT_MOUNT,
        )
    except hvac_exceptions.InvalidPath:
        try:
            client.secrets.transit.create_key(
                name=VAULT_USER_CONTENT_KEK_KEY_NAME,
                mount_point=VAULT_TRANSIT_MOUNT,
            )
        except hvac_exceptions.InvalidRequest:
            pass
        except Exception as exc:
            raise AppError(502, "vault_bootstrap_failed", "Vault KEK key could not be created") from exc
    except Exception as exc:
        raise AppError(502, "vault_bootstrap_failed", "Vault KEK key could not be checked") from exc


def ensure_vault_kv_ready() -> None:
    client = vault_client()
    mount_path = f"{VAULT_KV_MOUNT.strip('/')}/"
    try:
        mounts_response = client.sys.list_mounted_secrets_engines()
    except Exception as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    mounts = (mounts_response or {}).get("data") or {}
    existing = mounts.get(mount_path)
    if existing is None:
        try:
            client.sys.enable_secrets_engine(
                backend_type="kv",
                path=VAULT_KV_MOUNT.strip("/"),
                options={"version": "2"},
            )
        except hvac_exceptions.InvalidRequest:
            pass
        except Exception as exc:
            raise AppError(502, "vault_bootstrap_failed", "Vault KV mount could not be created") from exc
        return
    options = (existing.get("options") or {}) if isinstance(existing, dict) else {}
    if str(options.get("version") or "1") != "2":
        raise AppError(502, "vault_bootstrap_failed", "Vault KV mount must use version 2")


def _transit_key_version_from_ciphertext(ciphertext: str) -> int:
    try:
        _, version_fragment, _ = ciphertext.split(":", 2)
    except ValueError as exc:
        raise AppError(502, "vault_read_failed", "Vault returned an invalid wrapped key") from exc
    if not version_fragment.startswith("v"):
        raise AppError(502, "vault_read_failed", "Vault returned an invalid wrapped key")
    try:
        return int(version_fragment[1:])
    except ValueError as exc:
        raise AppError(502, "vault_read_failed", "Vault returned an invalid wrapped key") from exc


def generate_user_content_data_key() -> tuple[bytes, str, int]:
    client = vault_client()
    try:
        response = client.secrets.transit.generate_data_key(
            name=VAULT_USER_CONTENT_KEK_KEY_NAME,
            key_type="plaintext",
            mount_point=VAULT_TRANSIT_MOUNT,
        )
    except Exception as exc:
        raise AppError(502, "vault_write_failed", "Vault data key generation failed") from exc
    data = response.get("data") or {}
    plaintext_b64 = data.get("plaintext")
    wrapped_dek = data.get("ciphertext")
    if not plaintext_b64 or not wrapped_dek:
        raise AppError(502, "vault_write_failed", "Vault data key generation failed")
    try:
        plaintext = base64.b64decode(str(plaintext_b64), validate=True)
    except ValueError as exc:
        raise AppError(502, "vault_read_failed", "Vault returned an invalid plaintext data key") from exc
    key_version = int(data.get("key_version") or _transit_key_version_from_ciphertext(str(wrapped_dek)))
    return plaintext, str(wrapped_dek), key_version


def unwrap_user_content_data_key(
    *,
    wrapped_dek: str,
    mount_point: str | None = None,
    key_name: str | None = None,
) -> bytes:
    client = vault_client()
    try:
        response = client.secrets.transit.decrypt_data(
            name=key_name or VAULT_USER_CONTENT_KEK_KEY_NAME,
            ciphertext=wrapped_dek,
            mount_point=mount_point or VAULT_TRANSIT_MOUNT,
        )
    except Exception as exc:
        raise AppError(502, "vault_read_failed", "Vault data key unwrap failed") from exc
    data = response.get("data") or {}
    plaintext_b64 = data.get("plaintext")
    if not plaintext_b64:
        raise AppError(502, "vault_read_failed", "Vault data key unwrap failed")
    try:
        return base64.b64decode(str(plaintext_b64), validate=True)
    except ValueError as exc:
        raise AppError(502, "vault_read_failed", "Vault returned an invalid plaintext data key") from exc


def rewrap_user_content_data_key(
    *,
    wrapped_dek: str,
    mount_point: str | None = None,
    key_name: str | None = None,
) -> tuple[str, int]:
    client = vault_client()
    try:
        response = client.secrets.transit.rewrap_data(
            name=key_name or VAULT_USER_CONTENT_KEK_KEY_NAME,
            ciphertext=wrapped_dek,
            mount_point=mount_point or VAULT_TRANSIT_MOUNT,
        )
    except Exception as exc:
        raise AppError(502, "vault_write_failed", "Vault data key rewrap failed") from exc
    data = response.get("data") or {}
    rewrapped = data.get("ciphertext")
    if not rewrapped:
        raise AppError(502, "vault_write_failed", "Vault data key rewrap failed")
    return str(rewrapped), int(data.get("key_version") or _transit_key_version_from_ciphertext(str(rewrapped)))


def team_stt_secret_path(team_id: UUID, config_id: UUID, *, secret_id: UUID | None = None) -> str:
    if secret_id is None:
        return f"openscribe/stt/team/{team_id}/config/{config_id}"
    return f"openscribe/stt/team/{team_id}/config/{config_id}/{secret_id}"


def team_stt_secret_ref(team_id: UUID, config_id: UUID, *, secret_id: UUID | None = None) -> str:
    return f"{VAULT_KV_MOUNT}:{team_stt_secret_path(team_id, config_id, secret_id=secret_id)}"


def _team_stt_path_from_ref(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> str:
    if not secret_ref:
        return team_stt_secret_path(team_id, config_id)
    prefix = f"{VAULT_KV_MOUNT}:"
    if not secret_ref.startswith(prefix):
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    path = secret_ref[len(prefix):].strip()
    expected_prefix = team_stt_secret_path(team_id, config_id)
    if not path or (path != expected_prefix and not path.startswith(f"{expected_prefix}/")):
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    return path


def write_team_stt_bearer_token(*, team_id: UUID, config_id: UUID, bearer_token: str, secret_id: UUID | None = None) -> str:
    path = team_stt_secret_path(team_id, config_id, secret_id=secret_id)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.post(
            url,
            headers=_vault_headers(),
            json={"data": {"bearer_token": bearer_token}},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code >= 400:
        raise AppError(502, "vault_write_failed", "Vault secret write failed")
    return team_stt_secret_ref(team_id, config_id, secret_id=secret_id)


def read_team_stt_bearer_token(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> str:
    path = _team_stt_path_from_ref(team_id=team_id, config_id=config_id, secret_ref=secret_ref)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.get(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code == 404:
        raise AppError(
            502,
            "vault_read_failed",
            "STT provider credential is missing for the queued transcription config",
            {"team_id": str(team_id), "config_id": str(config_id)},
        )
    if response.status_code >= 400:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    payload = response.json()
    bearer_token = (((payload.get("data") or {}).get("data") or {}).get("bearer_token"))
    if not bearer_token:
        raise AppError(
            502,
            "vault_read_failed",
            "STT provider credential is missing for the queued transcription config",
            {"team_id": str(team_id), "config_id": str(config_id)},
        )
    return str(bearer_token)


def delete_team_stt_bearer_token(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> None:
    path = _team_stt_path_from_ref(team_id=team_id, config_id=config_id, secret_ref=secret_ref)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/metadata/{path}"
    try:
        response = httpx.delete(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code in {200, 204, 404}:
        return
    raise AppError(502, "vault_delete_failed", "Vault secret delete failed")


def team_llm_secret_path(team_id: UUID, config_id: UUID, *, secret_id: UUID | None = None) -> str:
    path = f"openscribe/llm/team/{team_id}/config/{config_id}"
    return f"{path}/{secret_id}" if secret_id is not None else path


def team_llm_secret_ref(team_id: UUID, config_id: UUID, *, secret_id: UUID | None = None) -> str:
    return f"{VAULT_KV_MOUNT}:{team_llm_secret_path(team_id, config_id, secret_id=secret_id)}"


def write_team_llm_secret(
    *,
    team_id: UUID,
    config_id: UUID,
    secret_payload: dict[str, object],
    secret_id: UUID | None = None,
    secret_ref: str | None = None,
) -> str:
    if not isinstance(secret_payload, dict) or not secret_payload:
        raise AppError(500, "vault_secret_invalid", "Vault secret payload is invalid")
    if secret_ref is not None and secret_id is not None:
        raise AppError(500, "vault_reference_invalid", "Vault secret reference is invalid")
    path = (
        _team_llm_path_from_ref(team_id=team_id, config_id=config_id, secret_ref=secret_ref)
        if secret_ref is not None
        else team_llm_secret_path(team_id, config_id, secret_id=secret_id)
    )
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.post(
            url,
            headers=_vault_headers(),
            json={"data": secret_payload},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code >= 400:
        raise AppError(502, "vault_write_failed", "Vault secret write failed")
    return f"{VAULT_KV_MOUNT}:{path}"


def write_team_llm_bearer_token(
    *,
    team_id: UUID,
    config_id: UUID,
    bearer_token: str,
    secret_id: UUID | None = None,
    secret_ref: str | None = None,
) -> str:
    return write_team_llm_secret(
        team_id=team_id,
        config_id=config_id,
        secret_payload={"secret_type": "bearer_token", "bearer_token": bearer_token},
        secret_id=secret_id,
        secret_ref=secret_ref,
    )


def _team_llm_path_from_ref(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> str:
    if not secret_ref:
        return team_llm_secret_path(team_id, config_id)
    mount_prefix = f"{VAULT_KV_MOUNT}:"
    if not secret_ref.startswith(mount_prefix):
        raise AppError(500, "vault_reference_invalid", "Vault secret reference is invalid")
    path = secret_ref[len(mount_prefix):].strip()
    expected_prefix = f"openscribe/llm/team/{team_id}/config/"
    if not path or not path.startswith(expected_prefix):
        raise AppError(500, "vault_reference_invalid", "Vault secret reference is invalid")
    return path


def read_team_llm_secret(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> dict[str, object]:
    path = _team_llm_path_from_ref(team_id=team_id, config_id=config_id, secret_ref=secret_ref)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.get(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code == 404:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    if response.status_code >= 400:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    payload = response.json()
    secret_payload = ((payload.get("data") or {}).get("data") or {})
    if not isinstance(secret_payload, dict) or not secret_payload:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    return secret_payload


def read_team_llm_bearer_token(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> str:
    secret_payload = read_team_llm_secret(team_id=team_id, config_id=config_id, secret_ref=secret_ref)
    secret_type = secret_payload.get("secret_type")
    bearer_token = secret_payload.get("bearer_token")
    if secret_type not in {None, "bearer_token"}:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    if not bearer_token:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    return str(bearer_token)


def delete_team_llm_bearer_token(*, team_id: UUID, config_id: UUID, secret_ref: str | None = None) -> None:
    path = _team_llm_path_from_ref(team_id=team_id, config_id=config_id, secret_ref=secret_ref)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/metadata/{path}"
    try:
        response = httpx.delete(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code in {200, 204, 404}:
        return
    raise AppError(502, "vault_delete_failed", "Vault secret delete failed")


def delete_provider_secret_by_ref(*, kind, secret_ref: str) -> None:
    """Delete one validated provider ref without rebuilding a path from deleted DB rows."""
    from app.models import ProviderSecretCleanupKind

    if not isinstance(kind, ProviderSecretCleanupKind):
        raise AppError(500, "vault_reference_invalid", "Vault secret reference is invalid")
    prefix = f"{VAULT_KV_MOUNT}:"
    if not secret_ref.startswith(prefix):
        raise AppError(500, "vault_reference_invalid", "Vault secret reference is invalid")
    path = secret_ref[len(prefix):].strip()
    uuid_pattern = r"[0-9a-fA-F-]{36}"
    patterns = {
        ProviderSecretCleanupKind.stt: rf"openscribe/stt/team/{uuid_pattern}/config/{uuid_pattern}(?:/{uuid_pattern})?",
        ProviderSecretCleanupKind.llm: rf"openscribe/llm/team/{uuid_pattern}/config/{uuid_pattern}(?:/{uuid_pattern})?",
        ProviderSecretCleanupKind.deidentification: rf"openscribe/deidentification/provider/{uuid_pattern}(?:/{uuid_pattern})?",
    }
    if not re.fullmatch(patterns[kind], path):
        raise AppError(500, "vault_reference_invalid", "Vault secret reference is invalid")
    try:
        response = httpx.delete(
            f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/metadata/{path}",
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code not in {200, 204, 404}:
        raise AppError(502, "vault_delete_failed", "Vault secret delete failed")


def deidentification_secret_path(provider_id: UUID, *, secret_id: UUID | None = None) -> str:
    if secret_id is None:
        return f"openscribe/deidentification/provider/{provider_id}"
    return f"openscribe/deidentification/provider/{provider_id}/{secret_id}"


def deidentification_secret_ref(provider_id: UUID, *, secret_id: UUID | None = None) -> str:
    return f"{VAULT_KV_MOUNT}:{deidentification_secret_path(provider_id, secret_id=secret_id)}"


def _deidentification_path_from_ref(*, provider_id: UUID, secret_ref: str | None = None) -> str:
    if not secret_ref:
        return deidentification_secret_path(provider_id)
    prefix = f"{VAULT_KV_MOUNT}:"
    if not secret_ref.startswith(prefix):
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    path = secret_ref[len(prefix):].strip()
    if not path:
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    return path


def write_deidentification_bearer_token(*, provider_id: UUID, bearer_token: str, secret_id: UUID | None = None) -> str:
    path = deidentification_secret_path(provider_id, secret_id=secret_id)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.post(
            url,
            headers=_vault_headers(),
            json={"data": {"bearer_token": bearer_token}},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code >= 400:
        raise AppError(502, "vault_write_failed", "Vault secret write failed")
    return deidentification_secret_ref(provider_id, secret_id=secret_id)


def read_deidentification_bearer_token(*, provider_id: UUID, secret_ref: str | None = None) -> str:
    path = _deidentification_path_from_ref(provider_id=provider_id, secret_ref=secret_ref)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.get(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code >= 400:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    payload = response.json()
    bearer_token = (((payload.get("data") or {}).get("data") or {}).get("bearer_token"))
    if not bearer_token:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    return str(bearer_token)


def delete_deidentification_bearer_token(*, provider_id: UUID, secret_ref: str | None = None) -> None:
    path = _deidentification_path_from_ref(provider_id=provider_id, secret_ref=secret_ref)
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/metadata/{path}"
    try:
        response = httpx.delete(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code in {200, 204, 404}:
        return
    raise AppError(502, "vault_delete_failed", "Vault secret delete failed")


def read_mail_resend_api_key(*, secret_ref: str) -> str:
    prefix = f"{VAULT_KV_MOUNT}:"
    if not secret_ref.startswith(prefix):
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    path = secret_ref[len(prefix):].strip()
    if not path:
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    url = f"{VAULT_ADDR.rstrip('/')}/v1/{VAULT_KV_MOUNT}/data/{path}"
    try:
        response = httpx.get(
            url,
            headers=_vault_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code >= 400:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    payload = response.json()
    data = ((payload.get("data") or {}).get("data") or {})
    api_key = data.get("api_key") or data.get("resend_api_key")
    if not api_key:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    return str(api_key)


def _read_platform_secret_value(*, secret_ref: str, field: str) -> str | None:
    mount, path = _split_secret_ref(secret_ref)
    url = _kv_url_for_path(mount=mount, path=path)
    try:
        response = httpx.get(url, headers=_vault_headers(), timeout=10.0)
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(502, "vault_read_failed", "Vault secret read failed") from exc
    if not isinstance(payload, dict):
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    envelope = payload.get("data")
    if not isinstance(envelope, dict):
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    value = data.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    return value


def _get_or_create_platform_secret(
    *,
    secret_ref: str,
    field: str,
    generated: str,
    failure_message: str,
) -> str:
    existing = _read_platform_secret_value(secret_ref=secret_ref, field=field)
    if existing:
        return existing

    mount, path = _split_secret_ref(secret_ref)
    url = _kv_url_for_path(mount=mount, path=path)
    try:
        response = httpx.post(
            url,
            headers=_vault_headers(),
            json={"options": {"cas": 0}, "data": {field: generated}},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code < 400:
        return generated

    # Another instance may have created it first.
    existing = _read_platform_secret_value(secret_ref=secret_ref, field=field)
    if existing:
        return existing
    raise AppError(502, "vault_write_failed", failure_message)


def get_or_create_platform_csrf_secret(*, secret_ref: str | None = None) -> str:
    resolved_ref = secret_ref or os.getenv("CSRF_SECRET_VAULT_REF") or DEFAULT_CSRF_SECRET_REF
    return _get_or_create_platform_secret(
        secret_ref=resolved_ref,
        field="csrf_secret",
        generated=secrets.token_urlsafe(48),
        failure_message="Vault CSRF secret write failed",
    )


@lru_cache(maxsize=8)
def get_or_create_platform_oidc_subject_hash_secret(
    *,
    secret_ref: str | None = None,
) -> str:
    resolved_ref = (
        secret_ref
        or os.getenv("OIDC_SUBJECT_HASH_SECRET_VAULT_REF")
        or DEFAULT_OIDC_SUBJECT_HASH_SECRET_REF
    )
    mount, path = _split_secret_ref(resolved_ref)
    if (
        mount != VAULT_KV_MOUNT.strip("/")
        or path != "openscribe/platform/oidc-subject-hash"
    ):
        raise AppError(
            500,
            "vault_ref_invalid",
            "OIDC subject-hash Vault reference is invalid",
        )
    return _get_or_create_platform_secret(
        secret_ref=resolved_ref,
        field="subject_hash_secret",
        generated=secrets.token_urlsafe(48),
        failure_message="Vault OIDC subject-hash secret write failed",
    )


@lru_cache(maxsize=16)
def read_oidc_client_secret(*, secret_ref: str, provider_key: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", provider_key):
        raise AppError(500, "vault_ref_invalid", "OIDC provider key is invalid")
    mount, path = _split_secret_ref(secret_ref)
    if (
        mount != VAULT_KV_MOUNT.strip("/")
        or path != f"openscribe/oidc/{provider_key}"
    ):
        raise AppError(
            500,
            "vault_ref_invalid",
            "OIDC provider Vault reference is invalid",
        )
    value = _read_platform_secret_value(
        secret_ref=secret_ref,
        field="client_secret",
    )
    if not value:
        raise AppError(
            502,
            "vault_read_failed",
            "OIDC provider credential is missing",
        )
    return value


def transcript_ingestion_source_audio_path(job_id: UUID) -> str:
    return f"openscribe/transcript-ingestion/{job_id}/source-audio"


def transcript_ingestion_source_audio_ref(job_id: UUID) -> str:
    return f"{VAULT_KV_MOUNT}:{transcript_ingestion_source_audio_path(job_id)}"


def _kv_url_for_path(*, mount: str, path: str, endpoint: str = "data") -> str:
    return f"{VAULT_ADDR.rstrip('/')}/v1/{mount}/{endpoint}/{path}"


def _split_secret_ref(secret_ref: str) -> tuple[str, str]:
    mount, separator, path = secret_ref.partition(":")
    if not separator or not mount or not path:
        raise AppError(500, "vault_ref_invalid", "Vault secret reference is invalid")
    return mount, path


def write_transcript_ingestion_source_audio(*, job_id: UUID, audio_bytes: bytes) -> str:
    path = transcript_ingestion_source_audio_path(job_id)
    url = _kv_url_for_path(mount=VAULT_KV_MOUNT, path=path)
    encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
    try:
        response = httpx.post(
            url,
            headers=_vault_headers(),
            json={"data": {"audio_b64": encoded_audio}},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code >= 400:
        raise AppError(502, "vault_write_failed", "Vault secret write failed")
    return transcript_ingestion_source_audio_ref(job_id)


def read_transcript_ingestion_source_audio(*, secret_ref: str) -> bytes:
    mount, path = _split_secret_ref(secret_ref)
    url = _kv_url_for_path(mount=mount, path=path)
    try:
        response = httpx.get(
            url,
            headers=_vault_headers(),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code == 404:
        raise AppError(502, "vault_read_failed", "Stored retry audio is missing")
    if response.status_code >= 400:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    payload = response.json()
    encoded_audio = (((payload.get("data") or {}).get("data") or {}).get("audio_b64"))
    if not encoded_audio:
        raise AppError(502, "vault_read_failed", "Stored retry audio is missing")
    try:
        return base64.b64decode(str(encoded_audio), validate=True)
    except ValueError as exc:
        raise AppError(502, "vault_read_failed", "Stored retry audio is invalid") from exc


def delete_transcript_ingestion_source_audio(*, secret_ref: str) -> None:
    mount, path = _split_secret_ref(secret_ref)
    path_parts = path.split("/")
    if mount != VAULT_KV_MOUNT or len(path_parts) != 4 or path_parts[:2] != ["openscribe", "transcript-ingestion"] or path_parts[3] != "source-audio":
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    try:
        job_id = UUID(path_parts[2])
    except ValueError as exc:
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid") from exc
    if path != transcript_ingestion_source_audio_path(job_id):
        raise AppError(502, "vault_secret_ref_invalid", "Vault secret reference is invalid")
    url = _kv_url_for_path(mount=mount, path=path, endpoint="metadata")
    try:
        response = httpx.delete(
            url,
            headers=_vault_headers(),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code in {200, 204, 404}:
        return
    raise AppError(502, "vault_delete_failed", "Vault secret delete failed")
