from __future__ import annotations

from typing import Any

from google.auth.credentials import Credentials

from app.errors import AppError
from app.models import LlmAuthMode, TeamLlmConfig
from app.services.llm_adapters.gemini_enterprise import service_account_credentials_from_info
from app.services.vault import read_team_llm_secret


def resolve_llm_runtime_credential(config: TeamLlmConfig) -> str | Credentials | None:
    """Resolve configured credential without exposing secret payloads."""
    if config.auth_mode is LlmAuthMode.none:
        return None
    if config.auth_mode is LlmAuthMode.google_adc:
        return None
    if not config.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "The LLM provider credential is not configured")

    secret = read_team_llm_secret(
        team_id=config.team_id,
        config_id=config.id,
        secret_ref=config.vault_secret_ref,
    )
    if config.auth_mode is LlmAuthMode.bearer:
        bearer_token = secret.get("bearer_token")
        if not isinstance(bearer_token, str) or not bearer_token:
            raise AppError(502, "vault_read_failed", "Vault secret read failed")
        return bearer_token
    if config.auth_mode is LlmAuthMode.google_service_account:
        if secret.get("secret_type") != "google_service_account_json":
            raise AppError(502, "vault_read_failed", "Vault secret read failed")
        credential_json = secret.get("credential_json")
        if not isinstance(credential_json, dict):
            raise AppError(502, "vault_read_failed", "Vault secret read failed")
        return service_account_credentials_from_info(credential_json)
    raise AppError(422, "business_rule_violation", "Unsupported LLM authentication mode")


def google_service_account_secret(credential_json: dict[str, Any]) -> dict[str, object]:
    return {
        "secret_type": "google_service_account_json",
        "credential_json": dict(credential_json),
    }
