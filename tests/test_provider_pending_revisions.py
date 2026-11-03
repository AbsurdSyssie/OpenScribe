from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.models import TeamLlmConfig, TeamSttConfig
from app.schemas import LlmConfigDraftCreate, SttConfigDraftCreate
from app.schemas.llm import LlmConfigInspectResult
from app.schemas.stt import SttInspectResult
from app.services import llm, stt


def _llm_inspection():
    return LlmConfigInspectResult(
        provider_preset="openai",
        provider_display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        adapter_kind="openai_chat",
        model_name=None,
        available_models=["model-a"],
        discovery_status="fetched",
        model_catalog_checked=True,
        model_catalog_check_status="checked",
        default_model_source="provider",
        requires_bearer_token=True,
        supports_model_discovery=True,
    )


def _stt_inspection():
    return SttInspectResult(
        base_url="https://api.openai.com",
        openapi_path=None,
        adapter_kind="openai_cloud",
        transcribe_path="/v1/audio/transcriptions",
        model_name=None,
        model_field_name="model",
        file_field_name="file",
        language=None,
        language_field_name="language",
        response_text_path="text",
        extra_form_fields_json={},
        candidate_paths=[],
        operation_summary=None,
        available_models=["whisper-1"],
        field_tips=[],
        notes=[],
    )


@pytest.fixture(params=["llm", "stt"])
def provider_case(request, db_session, make_team, make_user, make_llm_config, make_stt_config, monkeypatch):
    kind = request.param
    team = make_team(name=f"Pending {kind}")
    actor = make_user(email=f"pending-{kind}@example.com", password="password-1", is_system_admin=True)
    if kind == "llm":
        root = make_llm_config(team=team, actor=actor, label="Saved LLM", provider_preset="openai", available_models_json=["model-a"])
        inspect = _llm_inspection()
        monkeypatch.setattr(llm, "inspect_llm_contract", lambda *args, **kwargs: inspect)
        writes = []
        monkeypatch.setattr(llm, "write_team_llm_bearer_token", lambda *, team_id, config_id, bearer_token, secret_id=None: writes.append((config_id, bearer_token)) or f"secret:llm/{config_id}")
        service = llm.create_llm_config_draft
        payload_factory = lambda revision_id: LlmConfigDraftCreate(team_id=team.id, revision_of_config_id=revision_id, provider_preset="openai", bearer_token="synthetic-key")
        model = TeamLlmConfig
    else:
        root = make_stt_config(team=team, actor=actor, label="Saved STT")
        inspect = _stt_inspection()
        monkeypatch.setattr(stt, "inspect_stt_contract", lambda *args, **kwargs: inspect)
        writes = []
        monkeypatch.setattr(stt, "write_team_stt_bearer_token", lambda *, team_id, config_id, bearer_token, secret_id=None: writes.append((config_id, bearer_token)) or f"secret:stt/{config_id}")
        service = stt.create_stt_config_draft
        payload_factory = lambda revision_id: SttConfigDraftCreate(team_id=team.id, revision_of_config_id=revision_id, provider_preset="openai", bearer_token="synthetic-key")
        model = TeamSttConfig
    return SimpleNamespace(kind=kind, team=team, actor=actor, root=root, inspect=inspect, writes=writes, service=service, payload_factory=payload_factory, model=model)


def test_duplicate_pending_revision_is_safe_conflict_before_inspection_or_vault(provider_case, db_session, monkeypatch):
    case = provider_case
    first, _ = case.service(db_session, case.actor, case.payload_factory(case.root.id))
    db_session.refresh(case.root)
    original_root = (case.root.id, case.root.vault_secret_ref, case.root.label, case.root.setup_status)
    original_revision = (first.id, first.revision_of_config_id, first.vault_secret_ref, first.setup_status)
    original_secret_refs = [row.vault_secret_ref for row in db_session.scalars(select(case.model).where(case.model.team_id == case.team.id)).all()]
    inspection_calls = 0

    def unexpected_inspection(*args, **kwargs):
        nonlocal inspection_calls
        inspection_calls += 1
        raise AssertionError("duplicate revision must be rejected before provider inspection")

    service_module = llm if case.kind == "llm" else stt
    monkeypatch.setattr(service_module, "inspect_llm_contract" if case.kind == "llm" else "inspect_stt_contract", unexpected_inspection)
    with pytest.raises(AppError) as caught:
        case.service(db_session, case.actor, case.payload_factory(case.root.id))

    error = caught.value
    assert error.status_code == 409
    assert error.code == "conflict"
    assert error.details == {
        "resource": f"{case.kind}_config_revision",
        "revision_of_config_id": str(case.root.id),
        "pending_revision_id": str(first.id),
    }
    assert inspection_calls == 0
    assert len(case.writes) == 1
    db_session.refresh(case.root)
    db_session.refresh(first)
    assert (case.root.id, case.root.vault_secret_ref, case.root.label, case.root.setup_status) == original_root
    assert (first.id, first.revision_of_config_id, first.vault_secret_ref, first.setup_status) == original_revision
    assert [row.vault_secret_ref for row in db_session.scalars(select(case.model).where(case.model.team_id == case.team.id)).all()] == original_secret_refs


def test_cross_team_revision_target_returns_not_found_without_revision_metadata(provider_case, db_session, make_team, make_llm_config, make_stt_config):
    case = provider_case
    other_team = make_team(name=f"Other {case.kind}")
    if case.kind == "llm":
        other_root = make_llm_config(team=other_team, actor=case.actor, label="Other LLM", provider_preset="openai", available_models_json=["model-a"])
    else:
        other_root = make_stt_config(team=other_team, actor=case.actor, label="Other STT")
    with pytest.raises(AppError) as caught:
        case.service(db_session, case.actor, case.payload_factory(other_root.id))
    assert caught.value.status_code == 404
    assert caught.value.code == "not_found"
    assert "pending_revision_id" not in (caught.value.details or {})
    assert case.writes == []


@pytest.mark.parametrize("winner_survives", [True, False])
def test_named_pending_revision_constraint_race_maps_to_conflict(provider_case, db_session, monkeypatch, winner_survives):
    case = provider_case
    winner, _ = case.service(db_session, case.actor, case.payload_factory(case.root.id))
    inspections = 0
    service_module = llm if case.kind == "llm" else stt
    inspect_name = "inspect_llm_contract" if case.kind == "llm" else "inspect_stt_contract"

    def count_inspection(*args, **kwargs):
        nonlocal inspections
        inspections += 1
        return case.inspect

    monkeypatch.setattr(service_module, inspect_name, count_inspection)
    winner_id = winner.id
    lookups = 0
    original_pending_lookup = getattr(service_module, f"_pending_{case.kind}_revision")

    def pending_lookup(db, *, team_id, target_id):
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        result = original_pending_lookup(db, team_id=team_id, target_id=target_id)
        return result if winner_survives else None

    monkeypatch.setattr(service_module, f"_pending_{case.kind}_revision", pending_lookup)
    with pytest.raises(AppError) as caught:
        case.service(db_session, case.actor, case.payload_factory(case.root.id))
    assert caught.value.status_code == 409
    assert caught.value.code == "conflict"
    expected_details = {
        "resource": f"{case.kind}_config_revision",
        "revision_of_config_id": str(case.root.id),
    }
    if winner_survives:
        expected_details["pending_revision_id"] = str(winner_id)
    assert caught.value.details == expected_details
    assert inspections == 1
    assert lookups == 2
    assert len(case.writes) == 1
    assert db_session.get(case.model, winner.id) is not None
    assert len(db_session.scalars(select(case.model).where(case.model.revision_of_config_id == case.root.id)).all()) == 1


def test_unrelated_integrity_error_is_not_rewritten(provider_case, db_session, monkeypatch):
    case = provider_case
    service_module = llm if case.kind == "llm" else stt

    class Orig:
        diag = SimpleNamespace(constraint_name="unrelated_constraint")

    @contextmanager
    def fail_unrelated(db, *, kind):
        try:
            yield lambda ref: None
            raise IntegrityError("insert", {}, Orig())
        except IntegrityError:
            db.rollback()
            raise

    monkeypatch.setattr(service_module, "provider_credential_write_transaction", fail_unrelated)
    with pytest.raises(IntegrityError):
        case.service(db_session, case.actor, case.payload_factory(None))
