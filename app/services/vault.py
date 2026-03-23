import os
from uuid import UUID

import httpx

from app.errors import AppError


VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "root")
VAULT_KV_MOUNT = os.getenv("VAULT_KV_MOUNT", "secret")


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
            headers={"X-Vault-Token": VAULT_TOKEN},
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
            headers={"X-Vault-Token": VAULT_TOKEN},
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
            headers={"X-Vault-Token": VAULT_TOKEN},
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
            headers={"X-Vault-Token": VAULT_TOKEN},
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
            headers={"X-Vault-Token": VAULT_TOKEN},
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
            headers={"X-Vault-Token": VAULT_TOKEN},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AppError(502, "vault_unavailable", "Vault is unavailable") from exc
    if response.status_code in {200, 204, 404}:
        return
    raise AppError(502, "vault_delete_failed", "Vault secret delete failed")
