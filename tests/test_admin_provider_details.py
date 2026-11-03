from __future__ import annotations

import re
from uuid import UUID

import pytest

from app.models import TeamLlmConfig, TeamSttConfig


PROVIDER_TYPES = ("stt", "llm")


def _config_factory(provider_type: str, make_stt_config, make_llm_config):
    return make_stt_config if provider_type == "stt" else make_llm_config


def _config_model(provider_type: str):
    return TeamSttConfig if provider_type == "stt" else TeamLlmConfig


def _details_path(provider_type: str, config_id: UUID) -> str:
    return f"/admin/{provider_type}-configs/{config_id}/details"


def _form_data(provider_type: str, team_id: UUID, *, label: str, is_active: bool) -> dict[str, str]:
    return {
        "team_id": str(team_id),
        "label": label,
        "is_active": "true" if is_active else "false",
        "return_view": "workspace",
        "return_tab": provider_type,
    }


@pytest.mark.parametrize("provider_type", PROVIDER_TYPES)
@pytest.mark.parametrize("accept", [None, "application/json"])
def test_admin_provider_details_save_updates_only_metadata_without_provider_or_vault_calls(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_llm_config,
    monkeypatch,
    provider_type,
    accept,
):
    team = make_team(name=f"Details save {provider_type}")
    admin = make_user(
        email=f"details-save-{provider_type}@example.com",
        password="password-1",
        is_system_admin=True,
    )
    factory = _config_factory(provider_type, make_stt_config, make_llm_config)
    config = factory(
        team=team,
        actor=admin,
        label=f"Original {provider_type.upper()}",
        model_name="synthetic-model",
    )
    original_secret_ref = config.vault_secret_ref
    original_base_url = config.base_url
    original_model_name = config.model_name

    def unexpected_call(*args, **kwargs):
        pytest.fail("Metadata-only provider edit must not inspect, draft, replace, or access Vault credentials")

    import app.routes.web_admin as web_admin

    for name in (
        f"create_{provider_type}_config_draft_service",
        f"inspect_{provider_type}_contract_service",
        f"replace_{provider_type}_config_draft_credential_service",
    ):
        monkeypatch.setattr(web_admin, name, unexpected_call)

    service_module = "app.services.stt" if provider_type == "stt" else "app.services.llm"
    secret_reader = "read_team_stt_bearer_token" if provider_type == "stt" else "read_team_llm_bearer_token"
    secret_writer = "write_team_stt_bearer_token" if provider_type == "stt" else "write_team_llm_bearer_token"
    monkeypatch.setattr(f"{service_module}.{secret_reader}", unexpected_call)
    monkeypatch.setattr(f"{service_module}.{secret_writer}", unexpected_call)

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    response = client.post(
        _details_path(provider_type, config.id),
        data=_form_data(provider_type, team.id, label="  Renamed synthetic provider  ", is_active=False),
        headers={"Accept": accept} if accept else None,
        follow_redirects=False,
    )

    if accept == "application/json":
        assert response.status_code == 200
        assert response.json() == {
            "config": {"id": str(config.id), "label": "Renamed synthetic provider", "is_active": False}
        }
    else:
        assert response.status_code == 303
        assert response.headers["location"].endswith(f"provider_notice={provider_type}_details_saved")
    db_session.expire_all()
    saved = db_session.get(_config_model(provider_type), config.id)
    assert saved is not None
    assert saved.label == "Renamed synthetic provider"
    assert saved.is_active is False
    assert saved.base_url == original_base_url
    assert saved.model_name == original_model_name
    assert saved.vault_secret_ref == original_secret_ref


@pytest.mark.parametrize("provider_type", PROVIDER_TYPES)
@pytest.mark.parametrize("accept", [None, "application/json"])
def test_admin_provider_details_duplicate_label_preserves_submitted_edit_context(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_llm_config,
    provider_type,
    accept,
):
    team = make_team(name=f"Duplicate details {provider_type}")
    admin = make_user(
        email=f"duplicate-details-{provider_type}@example.com",
        password="password-1",
        is_system_admin=True,
    )
    factory = _config_factory(provider_type, make_stt_config, make_llm_config)
    existing = factory(team=team, actor=admin, label="Existing provider")
    edited = factory(team=team, actor=admin, label="Edited provider", is_active=True)
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    response = client.post(
        _details_path(provider_type, edited.id),
        data=_form_data(provider_type, team.id, label=" Existing provider ", is_active=False),
        headers={"Accept": accept} if accept else None,
        follow_redirects=False,
    )

    assert response.status_code == 409
    if accept == "application/json":
        assert response.json() == {
            "error": {
                "code": "conflict",
                "message": f"An {provider_type.upper()} provider with this name already exists for this team.",
                "details": {"field": "label"},
            }
        }
    else:
        assert 'id="provider-details-modal"' in response.text
        assert 'data-error-kind="' + provider_type + '"' in response.text
        assert f'data-error-config-id="{edited.id}"' in response.text
        assert 'data-error-label=" Existing provider "' in response.text
        assert 'data-error-active="false"' in response.text
        assert f'action="{_details_path(provider_type, edited.id)}"' in response.text
        label_input = re.search(
            r'<input\b(?=[^>]*\bname="label")(?=[^>]*\bvalue=" Existing provider ")[^>]*>',
            response.text,
        )
        assert label_input is not None
        assert "with this name already exists for this team" in response.text
        modal_tag = re.search(r'<div\b(?=[^>]*\bid="provider-details-modal")([^>]*)>', response.text)
        assert modal_tag is not None
        assert " hidden" not in modal_tag.group(1)
    db_session.expire_all()
    saved = db_session.get(_config_model(provider_type), edited.id)
    assert saved is not None
    assert saved.label == "Edited provider"
    assert saved.is_active is True
    assert db_session.get(_config_model(provider_type), existing.id) is not None


@pytest.mark.parametrize("provider_type", PROVIDER_TYPES)
def test_admin_provider_details_rejects_non_admin_and_cross_team_config(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_llm_config,
    provider_type,
):
    team = make_team(name=f"Restricted details {provider_type}")
    other_team = make_team(name=f"Other details {provider_type}")
    admin = make_user(
        email=f"restricted-admin-{provider_type}@example.com",
        password="password-1",
        is_system_admin=True,
    )
    member = make_user(
        email=f"restricted-member-{provider_type}@example.com",
        password="password-1",
        team=team,
        mfa_required=False,
    )
    factory = _config_factory(provider_type, make_stt_config, make_llm_config)
    config = factory(team=team, actor=admin, label="Restricted provider")

    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)
    forbidden = client.post(
        _details_path(provider_type, config.id),
        data=_form_data(provider_type, team.id, label="Unauthorized rename", is_active=False),
        follow_redirects=False,
    )
    assert forbidden.status_code == 403

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    wrong_team = client.post(
        _details_path(provider_type, config.id),
        data=_form_data(provider_type, other_team.id, label="Cross-team rename", is_active=False),
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert wrong_team.status_code == 404
    assert wrong_team.json()["error"]["code"] == "not_found"

    db_session.expire_all()
    saved = db_session.get(_config_model(provider_type), config.id)
    assert saved is not None
    assert saved.team_id == team.id
    assert saved.label == "Restricted provider"
    assert saved.is_active is True
