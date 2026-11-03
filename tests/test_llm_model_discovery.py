from types import SimpleNamespace
from datetime import timedelta

import httpx
import pytest

from app.errors import AppError
from app.models import (
    GeneratedDocumentStatus,
    TeamHallucinationCheckSelection,
    TeamRole,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.schemas.llm import LlmConfigUpsert, LlmProviderModelPolicyUpsert
from app.services import llm
from app.services.content_crypto import ensure_user_dek
from app.services.llm_checker_selection import freeze_hallucination_check_selection, read_hallucination_check_selection


class _ModelDiscoveryResponse:
    headers = {}

    def __init__(self, *, status_code: int = 200, payload: bytes = b"{}"):
        self.status_code = status_code
        self._payload = payload
        self.iter_bytes_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://provider.example/models")
            raise httpx.HTTPStatusError("model discovery failed", request=request, response=self)

    def iter_bytes(self):
        self.iter_bytes_called = True
        return iter([self._payload])


@pytest.mark.parametrize(
    ("list_models", "expected_message"),
    [
        (llm._list_mistral_chat_models, "Could not load available Mistral chat models"),
        (llm._list_together_chat_models, "Could not load available Together AI chat models"),
    ],
)
def test_provider_specific_model_discovery_status_errors_are_preserved(monkeypatch, list_models, expected_message):
    monkeypatch.setattr(llm.httpx, "stream", lambda *args, **kwargs: _ModelDiscoveryResponse(status_code=502))

    with pytest.raises(AppError) as exc_info:
        list_models(api_key="test-key", base_url="https://provider.example/v1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "llm_inspection_failed"
    assert exc_info.value.message == expected_message
    assert exc_info.value.details == {"provider_status": 502, "provider_error_code": "provider_server_error"}


def test_model_discovery_rejects_invalid_credentials_with_provider_status(monkeypatch):
    monkeypatch.setattr(llm.httpx, "stream", lambda *args, **kwargs: _ModelDiscoveryResponse(status_code=403))

    with pytest.raises(AppError) as exc_info:
        llm._list_openai_compatible_models(api_key="test-key", base_url="https://provider.example/v1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "llm_invalid_credential"
    assert exc_info.value.message == "The API key was rejected by the provider."
    assert exc_info.value.details == {"provider_status": 403, "provider_error_code": "permission_denied"}


def test_model_discovery_status_diagnostic_does_not_read_or_expose_provider_body(monkeypatch):
    response = _ModelDiscoveryResponse(
        status_code=503,
        payload=b'{"message":"synthetic-key private-provider-response"}',
    )
    monkeypatch.setattr(llm.httpx, "stream", lambda *args, **kwargs: response)

    with pytest.raises(AppError) as exc_info:
        llm._list_openai_compatible_models(api_key="synthetic-key", base_url="https://provider.example/v1")

    assert exc_info.value.details == {"provider_status": 503, "provider_error_code": "provider_server_error"}
    assert response.iter_bytes_called is False
    assert "synthetic-key" not in str(exc_info.value.details)


def test_saved_model_inspection_refresh_preserves_saved_policy_without_provider_calls(
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    monkeypatch,
):
    team = make_team(name="Synthetic saved model inspection")
    admin = make_user(
        email="saved-model-inspection@example.com",
        password="password-1",
        is_system_admin=True,
    )
    config = make_llm_config(
        team=team,
        actor=admin,
        model_name="model-a",
        available_models_json=["model-a", "model-b", "model-c"],
    )
    selection = make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=["model-c", "model-b"],
        model_name_override=None,
    )
    llm.set_llm_provider_model_policy(
        db_session,
        admin,
        config_id=config.id,
        payload=LlmProviderModelPolicyUpsert(
            team_id=team.id,
            enabled_models_json=["model-c", "model-b"],
            default_model_name="model-c",
        ),
    )
    synthetic_inspection = SimpleNamespace(
        available_models=["model-a", "model-b", "model-new"],
        model_name="model-a",
        provider_preset=config.provider_preset,
        provider_display_name="Synthetic provider",
        discovery_status="fetched",
        model_catalog_checked=True,
        model_catalog_check_status="checked",
        model_catalog_check_error_code=None,
        model_catalog_check_http_status=None,
        default_model_source="provider",
        warnings=[],
        notes=[],
    )
    monkeypatch.setattr(llm, "inspect_llm_contract", lambda *args, **kwargs: synthetic_inspection)

    result = llm.inspect_saved_llm_config(db_session, admin, config_id=config.id, team_id=team.id)
    db_session.refresh(selection)

    assert result is synthetic_inspection
    assert selection.allowed_models_json == ["model-c", "model-b"]
    assert selection.model_name_override == "model-c"
    policy = llm.get_provider_model_policy(db_session, config=config)
    assert policy.enabled_models_json == ["model-c", "model-b"]
    assert policy.default_model_name == "model-c"
    assert config.available_models_json == ["model-a", "model-b", "model-new"]
    state = llm.provider_model_policy_state(db_session, config=config)
    assert state["enabled_models_json"] == ["model-c", "model-b"]
    assert state["default_model_name"] == "model-c"
    assert state["default_model_valid"] is False
    assert state["invalid_enabled_models_json"] == ["model-c"]


@pytest.mark.parametrize(
    ("check_status", "catalog", "expected_status"),
    [
        ("failed", ["model-a"], "failed"),
        ("not_attempted", ["model-a"], "passed"),
        ("checked", [], "passed"),
    ],
)
def test_saved_inspection_records_only_explicit_catalog_check_outcomes(
    db_session, make_team, make_user, make_llm_config, monkeypatch,
    check_status, catalog, expected_status,
):
    team = make_team(name=f"Synthetic {check_status} model check")
    admin = make_user(email=f"saved-check-{check_status}@example.com", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, model_name="model-a", available_models_json=["model-a"])
    config.inspection_metadata_json = {"connection_check": {"status": "passed", "checked_at": "prior"}}
    db_session.commit()
    synthetic_inspection = SimpleNamespace(
        available_models=catalog,
        model_name=None,
        provider_preset=config.provider_preset,
        provider_display_name="Synthetic provider",
        discovery_status="manual_required" if check_status != "checked" else "fetched",
        model_catalog_checked=check_status == "checked",
        model_catalog_check_status=check_status,
        model_catalog_check_error_code="timeout" if check_status == "failed" else None,
        model_catalog_check_http_status=504 if check_status == "failed" else None,
        default_model_source="none",
        warnings=[],
        notes=[],
    )
    monkeypatch.setattr(llm, "inspect_llm_contract", lambda *args, **kwargs: synthetic_inspection)

    llm.inspect_saved_llm_config(db_session, admin, config_id=config.id, team_id=team.id)

    db_session.refresh(config)
    check = config.inspection_metadata_json["connection_check"]
    assert check["status"] == expected_status
    if check_status == "failed":
        assert check["error_code"] == "timeout"
        assert check["http_status"] == 504
    if check_status == "checked":
        assert config.available_models_json == []
    else:
        assert config.available_models_json == ["model-a"]


@pytest.mark.parametrize("mutation", ["model", "availability"])
def test_legacy_queued_checker_work_blocks_config_mutation_but_frozen_work_does_not(
    db_session, make_team, make_user, make_llm_config, make_generated_document, mutation,
):
    team = make_team(name="Legacy checker upsert guard")
    admin = make_user(email="legacy-checker-admin@example.com", is_system_admin=True)
    owner = make_user(email="legacy-checker-owner@example.com", team=team, team_role=TeamRole.user)
    config = make_llm_config(
        team=team,
        actor=admin,
        has_secret=False,
        model_name="checker-model",
        available_models_json=["checker-model", "new-model"],
    )
    checker_selection = TeamHallucinationCheckSelection(
        team_id=team.id,
        llm_config_id=config.id,
        model_name_override="checker-model",
        selected_by_user_id=admin.id,
    )
    db_session.add(checker_selection)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Synthetic checker guard visit",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Synthetic source",
    )
    db_session.add(version)
    db_session.flush()
    ensure_user_dek(db_session, user=owner)
    legacy = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    legacy.document_mode = TemplateMode.structured
    legacy.status = GeneratedDocumentStatus.queued
    legacy.hallucination_check_selection_snapshot_encrypted = None
    legacy.hallucination_check_selection_config_id = None
    db_session.commit()

    with pytest.raises(AppError) as blocked:
        if mutation == "model":
            llm.upsert_llm_config(
                db_session,
                admin,
                LlmConfigUpsert(
                    config_id=config.id,
                    team_id=team.id,
                    label=config.label,
                    provider_preset="openai",
                    model_name="new-model",
                    bearer_token="synthetic-replacement-key",
                ),
            )
        else:
            llm.update_llm_config_details(
                db_session,
                admin,
                config_id=config.id,
                team_id=team.id,
                label=config.label,
                is_active=False,
            )
    assert blocked.value.status_code == 409
    assert blocked.value.details == {"resource": "legacy_checker_work_pending"}
    assert config.model_name == "checker-model"
    assert config.is_active is True

    legacy.status = GeneratedDocumentStatus.ready
    frozen = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    frozen.document_mode = TemplateMode.structured
    freeze_hallucination_check_selection(db_session, source=frozen, owner_user_id=owner.id)
    frozen.status = GeneratedDocumentStatus.queued
    db_session.commit()
    saved_policy = llm.set_llm_provider_model_policy(
        db_session,
        admin,
        config_id=config.id,
        payload=LlmProviderModelPolicyUpsert(
            team_id=team.id,
            enabled_models_json=["new-model"],
            default_model_name="new-model",
        ),
    )
    assert saved_policy["default_model_name"] == "new-model"
    frozen_selection = read_hallucination_check_selection(
        db_session, source=frozen, owner_user_id=owner.id,
    )
    assert frozen_selection.status == "configured"
    assert frozen_selection.model_name == "checker-model"
