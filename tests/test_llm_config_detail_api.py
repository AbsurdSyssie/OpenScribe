from app.schemas import LlmConfigDraftCreate
from app.schemas import SttConfigDraftCreate
from app.schemas.llm import LlmConfigInspectResult
from app.schemas.stt import SttInspectResult
from app.services import llm, stt


def test_system_admin_can_read_pending_llm_revision_metadata_without_secret(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="LLM review metadata")
    admin = make_user(email="llm-review-admin@example.com", password="password-1", is_system_admin=True)
    root = make_llm_config(team=team, actor=admin, label="Saved config", available_models_json=["base-model"])
    monkeypatch.setattr(llm, "inspect_llm_contract", lambda *args, **kwargs: LlmConfigInspectResult(
        provider_preset="openai", provider_display_name="OpenAI", base_url="https://api.openai.com/v1",
        adapter_kind="openai_chat", model_name="review-model", available_models=["review-model"],
        discovery_status="fetched", model_catalog_checked=True, model_catalog_check_status="checked",
        default_model_source="provider", requires_bearer_token=True, supports_model_discovery=True,
    ))
    monkeypatch.setattr(llm, "write_team_llm_bearer_token", lambda **kwargs: "vault://synthetic/unrestricted-secret-reference")
    revision, _ = llm.create_llm_config_draft(
        db_session,
        admin,
        LlmConfigDraftCreate(team_id=team.id, revision_of_config_id=root.id, provider_preset="openai", bearer_token="synthetic-credential"),
    )
    assert client.post("/api/v1/auth/login", json={"email": admin.email, "password": "password-1"}).status_code == 200

    response = client.get(f"/api/v1/llm-configs/{revision.id}", params={"team_id": str(team.id)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(revision.id)
    assert payload["team_id"] == str(team.id)
    assert payload["revision_of_config_id"] == str(root.id)
    assert payload["base_url"] == "https://api.openai.com/v1"
    assert payload["setup_status"] == "pending_model_selection"
    assert payload["available_models_json"] == ["review-model"]
    assert payload["has_secret"] is True
    serialized = response.text
    assert "synthetic-credential" not in serialized
    assert "unrestricted-secret-reference" not in serialized


def test_llm_config_detail_hides_cross_team_and_unknown_configs(client, make_team, make_user, make_llm_config):
    admin = make_user(email="llm-detail-admin@example.com", password="password-1", is_system_admin=True)
    first_team = make_team(name="LLM detail one")
    second_team = make_team(name="LLM detail two")
    config = make_llm_config(team=first_team, actor=admin)
    assert client.post("/api/v1/auth/login", json={"email": admin.email, "password": "password-1"}).status_code == 200

    cross_team = client.get(f"/api/v1/llm-configs/{config.id}", params={"team_id": str(second_team.id)})
    missing = client.get("/api/v1/llm-configs/00000000-0000-0000-0000-000000000001", params={"team_id": str(first_team.id)})

    assert cross_team.status_code == 404
    assert missing.status_code == 404


def test_llm_config_detail_requires_system_admin(client, make_team, make_user, make_llm_config):
    team = make_team(name="LLM detail auth")
    clinician = make_user(email="llm-detail-clinician@example.com", password="password-1", team=team)
    config = make_llm_config(team=team)
    assert client.post("/api/v1/auth/login", json={"email": clinician.email, "password": "password-1"}).status_code == 200

    response = client.get(f"/api/v1/llm-configs/{config.id}", params={"team_id": str(team.id)})

    assert response.status_code == 403


def test_existing_stt_config_detail_includes_revision_linkage(client, db_session, make_team, make_user, make_stt_config, monkeypatch):
    team = make_team(name="STT revision metadata")
    admin = make_user(email="stt-review-admin@example.com", password="password-1", is_system_admin=True)
    root = make_stt_config(team=team, actor=admin, label="Saved STT")
    monkeypatch.setattr(stt, "inspect_stt_contract", lambda *args, **kwargs: SttInspectResult(
        base_url="https://api.openai.com", openapi_path=None, adapter_kind="openai_cloud",
        transcribe_path="/v1/audio/transcriptions", model_name="whisper-1", model_field_name="model",
        file_field_name="file", language=None, language_field_name="language", response_text_path="text",
        extra_form_fields_json={}, candidate_paths=[], operation_summary=None, available_models=["whisper-1"],
        field_tips=[], notes=[],
    ))
    monkeypatch.setattr(stt, "write_team_stt_bearer_token", lambda **kwargs: "vault://synthetic/stt-secret-reference")
    revision, _ = stt.create_stt_config_draft(
        db_session,
        admin,
        SttConfigDraftCreate(team_id=team.id, revision_of_config_id=root.id, provider_preset="openai", bearer_token="synthetic-stt-credential"),
    )
    assert client.post("/api/v1/auth/login", json={"email": admin.email, "password": "password-1"}).status_code == 200

    response = client.get(f"/api/v1/stt-configs/{revision.id}", params={"team_id": str(team.id)})

    assert response.status_code == 200
    assert response.json()["revision_of_config_id"] == str(root.id)
    assert response.json()["has_secret"] is True
    assert "synthetic-stt-credential" not in response.text
    assert "stt-secret-reference" not in response.text
