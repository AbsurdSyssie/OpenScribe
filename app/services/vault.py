import base64
import os
from pathlib import Path
from uuid import UUID

import httpx
import hvac
from hvac import exceptions as hvac_exceptions

from app.errors import AppError


VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "root")
VAULT_TOKEN_FILE = os.getenv("VAULT_TOKEN_FILE")
VAULT_KV_MOUNT = os.getenv("VAULT_KV_MOUNT", "secret")
VAULT_TRANSIT_MOUNT = os.getenv("VAULT_TRANSIT_MOUNT", "transit")
VAULT_USER_CONTENT_KEK_KEY_NAME = os.getenv("VAULT_USER_CONTENT_KEK_KEY_NAME", "openscribe-user-content-kek")
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


def team_stt_secret_path(team_id: UUID, config_id: UUID) -> str:
    return f"openscribe/stt/team/{team_id}/config/{config_id}"


def team_stt_secret_ref(team_id: UUID, config_id: UUID) -> str:
    return f"{VAULT_KV_MOUNT}:{team_stt_secret_path(team_id, config_id)}"


def write_team_stt_bearer_token(*, team_id: UUID, config_id: UUID, bearer_token: str) -> str:
    path = team_stt_secret_path(team_id, config_id)
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
    return team_stt_secret_ref(team_id, config_id)


def read_team_stt_bearer_token(*, team_id: UUID, config_id: UUID) -> str:
    path = team_stt_secret_path(team_id, config_id)
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


def delete_team_stt_bearer_token(*, team_id: UUID, config_id: UUID) -> None:
    path = team_stt_secret_path(team_id, config_id)
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


def team_llm_secret_path(team_id: UUID, config_id: UUID) -> str:
    return f"openscribe/llm/team/{team_id}/config/{config_id}"


def team_llm_secret_ref(team_id: UUID, config_id: UUID) -> str:
    return f"{VAULT_KV_MOUNT}:{team_llm_secret_path(team_id, config_id)}"


def write_team_llm_bearer_token(*, team_id: UUID, config_id: UUID, bearer_token: str) -> str:
    path = team_llm_secret_path(team_id, config_id)
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
    return team_llm_secret_ref(team_id, config_id)


def read_team_llm_bearer_token(*, team_id: UUID, config_id: UUID) -> str:
    path = team_llm_secret_path(team_id, config_id)
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
    bearer_token = (((payload.get("data") or {}).get("data") or {}).get("bearer_token"))
    if not bearer_token:
        raise AppError(502, "vault_read_failed", "Vault secret read failed")
    return str(bearer_token)


def delete_team_llm_bearer_token(*, team_id: UUID, config_id: UUID) -> None:
    path = team_llm_secret_path(team_id, config_id)
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
