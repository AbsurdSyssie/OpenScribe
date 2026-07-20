from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import LlmAdapterKind, LlmAuthMode
from app.schemas.llm import (
    LlmConfigDraftCreate,
    LlmConfigDraftReplaceCredential,
    LlmConfigUpsert,
    LlmInspectRequest,
)
from app.services import vault


PROJECT_ID = "clinical-platform-prod"
LOCATION = "europe-west2"
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "runtime@credential-home.iam.gserviceaccount.com",
    "private_key": "private-key-material",
    "private_key_id": "key-id",
    "token_uri": "https://oauth2.googleapis.com/token",
    "project_id": "credential-home",
}


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload


def gemini_fields(**overrides):
    fields = {
        "provider_preset": "gemini_enterprise",
        "google_project_id": PROJECT_ID,
        "google_location": LOCATION,
        "google_auth_method": "application_default",
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize(
    "schema,extra",
    [
        (LlmInspectRequest, {}),
        (LlmConfigDraftCreate, {"team_id": uuid4()}),
        (LlmConfigUpsert, {"label": "Gemini Enterprise"}),
    ],
)
def test_gemini_input_paths_derive_typed_non_secret_config(schema, extra):
    payload = schema(**extra, **gemini_fields(capacity_mode="dedicated", base_url="https://attacker.invalid"))

    if hasattr(payload, "adapter_kind"):
        assert payload.adapter_kind == LlmAdapterKind.gemini_enterprise
    assert payload.base_url == "https://europe-west2-aiplatform.googleapis.com"
    assert payload.provider_config_json == {
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "api_version": "v1",
        "capacity_mode": "dedicated",
    }
    if hasattr(payload, "auth_mode"):
        assert payload.auth_mode == LlmAuthMode.google_adc


def test_gemini_service_account_requires_expected_type_and_fields():
    valid = LlmInspectRequest(
        **gemini_fields(
            google_auth_method="service_account_json",
            google_service_account_json=SERVICE_ACCOUNT,
        )
    )

    assert "private-key-material" not in repr(valid)

    external_credential = {**SERVICE_ACCOUNT, "type": "external_account"}
    with pytest.raises(ValidationError, match="service-account JSON object") as exc_info:
        LlmInspectRequest(
            **gemini_fields(
                google_auth_method="service_account_json",
                google_service_account_json=external_credential,
            )
        )
    assert "private-key-material" not in str(exc_info.value)
    with pytest.raises(ValidationError, match="missing required fields"):
        LlmInspectRequest(
            **gemini_fields(
                google_auth_method="service_account_json",
                google_service_account_json={"type": "service_account"},
            )
        )


def test_gemini_rejects_bearer_and_non_gemini_rejects_google_fields():
    with pytest.raises(ValidationError, match="does not accept bearer tokens"):
        LlmInspectRequest(**gemini_fields(bearer_token="wrong-auth"))
    with pytest.raises(ValidationError, match="require the Gemini Enterprise provider preset"):
        LlmInspectRequest(provider_preset="openai", google_project_id=PROJECT_ID)


def test_gemini_service_account_replacement_hides_secret_from_repr():
    payload = LlmConfigDraftReplaceCredential(
        team_id=uuid4(),
        config_id=uuid4(),
        google_auth_method="service_account_json",
        google_service_account_json=SERVICE_ACCOUNT,
    )

    assert "private-key-material" not in repr(payload)


def test_generic_llm_vault_write_preserves_typed_payload(monkeypatch):
    team_id = uuid4()
    config_id = uuid4()
    writes = []
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "test"})
    monkeypatch.setattr(vault.httpx, "post", lambda *args, **kwargs: writes.append(kwargs["json"]) or FakeResponse())

    ref = vault.write_team_llm_secret(
        team_id=team_id,
        config_id=config_id,
        secret_payload={"secret_type": "google_service_account_json", "credential_json": SERVICE_ACCOUNT},
        secret_id=uuid4(),
    )

    assert ref.startswith(f"{vault.VAULT_KV_MOUNT}:openscribe/llm/team/{team_id}/config/{config_id}/")
    assert writes == [{"data": {"secret_type": "google_service_account_json", "credential_json": SERVICE_ACCOUNT}}]


def test_generic_llm_vault_read_returns_typed_payload(monkeypatch):
    stored = {"secret_type": "google_service_account_json", "credential_json": SERVICE_ACCOUNT}
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "test"})
    monkeypatch.setattr(vault.httpx, "get", lambda *args, **kwargs: FakeResponse({"data": {"data": stored}}))

    assert vault.read_team_llm_secret(team_id=uuid4(), config_id=uuid4()) == stored


@pytest.mark.parametrize(
    "stored_payload",
    [
        {"bearer_token": "legacy-token"},
        {"secret_type": "bearer_token", "bearer_token": "legacy-token"},
    ],
)
def test_bearer_vault_wrapper_reads_legacy_and_typed_payloads(monkeypatch, stored_payload):
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "test"})
    monkeypatch.setattr(
        vault.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"data": {"data": stored_payload}}),
    )

    assert vault.read_team_llm_bearer_token(team_id=uuid4(), config_id=uuid4()) == "legacy-token"


def test_bearer_vault_wrapper_writes_typed_envelope(monkeypatch):
    writes = []
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "test"})
    monkeypatch.setattr(vault.httpx, "post", lambda *args, **kwargs: writes.append(kwargs["json"]) or FakeResponse())

    vault.write_team_llm_bearer_token(team_id=uuid4(), config_id=uuid4(), bearer_token="secret-token")

    assert writes == [{"data": {"secret_type": "bearer_token", "bearer_token": "secret-token"}}]
