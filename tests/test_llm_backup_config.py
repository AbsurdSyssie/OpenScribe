import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (GeneratedDocumentStatus, TeamLlmBackupSelection, TeamLlmConfig,
    TeamLlmSelection, TeamRole, Transcript, TranscriptIngestionMode, TranscriptStatus,
    TranscriptVersion, User, utcnow)
from app.schemas import (LlmBackupSelectionUpsert, LlmConfigDraftReplaceCredential,
    LlmConfigFinalize, LlmConfigInspectResult, LlmConfigUpsert, LlmSelectionUpsert)
from app.services.content_crypto import ensure_user_dek
from app.services.llm_backup import freeze_backup_selection
from app.services import llm as llm_service
from app.services.llm import (
    _reconcile_llm_config_model_selections,
    set_team_llm_backup_selection,
    set_team_llm_selection,
    snapshot_team_llm_backup,
    delete_llm_config,
)


def test_admin_can_set_backup_and_primary_leader_change_does_not_replace_it(
    db_session, make_team, make_user, make_llm_config
):
    team = make_team(name="Backup Selection Clinic")
    admin = make_user(email="backup-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(
        email="backup-leader@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.leader,
    )
    primary = make_llm_config(
        team=team, actor=admin, label="Primary", available_models_json=["primary-a"]
    )
    backup = make_llm_config(
        team=team,
        actor=admin,
        label="Backup",
        model_name="backup-a",
        available_models_json=["backup-a", "backup-b"],
        has_secret=False,
    )

    saved = set_team_llm_backup_selection(
        db_session,
        admin,
        LlmBackupSelectionUpsert(team_id=team.id, llm_config_id=backup.id, model_name="backup-b"),
    )
    assert saved is not None
    assert saved.llm_config_id == backup.id
    assert saved.model_name == "backup-b"

    set_team_llm_selection(
        db_session,
        leader,
        LlmSelectionUpsert(llm_config_id=primary.id, model_name_override="primary-a"),
    )
    db_session.expire_all()
    stored_backup = db_session.get(TeamLlmBackupSelection, team.id)
    assert stored_backup is not None
    assert stored_backup.llm_config_id == backup.id
    assert stored_backup.model_name == "backup-b"

    snapshot = snapshot_team_llm_backup(db_session, team_id=team.id)
    assert snapshot is not None
    assert snapshot["llm_config_id"] == str(backup.id)
    assert snapshot["model"] == "backup-b"
    assert "vault_secret_ref" not in snapshot
    assert "secret:" not in repr(snapshot)


def test_backup_selection_is_system_admin_only_and_team_scoped(
    db_session, make_team, make_user, make_llm_config
):
    team = make_team(name="Backup Access Clinic")
    other_team = make_team(name="Other Backup Clinic")
    admin = make_user(email="backup-access-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(
        email="backup-access-leader@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.leader,
    )
    config = make_llm_config(team=team, actor=admin, available_models_json=["model-a"])
    foreign_config = make_llm_config(team=other_team, actor=admin, label="Foreign", available_models_json=["model-b"])

    with pytest.raises(AppError) as denied:
        set_team_llm_backup_selection(
            db_session,
            leader,
            LlmBackupSelectionUpsert(team_id=team.id, llm_config_id=config.id, model_name="model-a"),
        )
    assert denied.value.status_code == 403

    with pytest.raises(AppError) as wrong_team:
        set_team_llm_backup_selection(
            db_session,
            admin,
            LlmBackupSelectionUpsert(team_id=team.id, llm_config_id=foreign_config.id, model_name="model-b"),
        )
    assert wrong_team.value.status_code == 404

    with pytest.raises(AppError) as wrong_model:
        set_team_llm_backup_selection(
            db_session,
            admin,
            LlmBackupSelectionUpsert(team_id=team.id, llm_config_id=config.id, model_name="missing-model"),
        )
    assert wrong_model.value.status_code == 422


def test_backup_model_reconciles_to_new_provider_default_and_clear_removes_selection(
    db_session, make_team, make_user, make_llm_config
):
    team = make_team(name="Backup Reconcile Clinic")
    admin = make_user(email="backup-reconcile-admin@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        model_name="model-a",
        available_models_json=["model-a", "model-b"],
        has_secret=False,
    )
    set_team_llm_backup_selection(
        db_session,
        admin,
        LlmBackupSelectionUpsert(team_id=team.id, llm_config_id=config.id, model_name="model-b"),
    )

    config.available_models_json = ["model-c"]
    config.model_name = "model-c"
    _reconcile_llm_config_model_selections(db_session, config=config)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(TeamLlmBackupSelection, team.id).model_name == "model-c"
    assert snapshot_team_llm_backup(db_session, team_id=team.id)["model"] == "model-c"

    cleared = set_team_llm_backup_selection(
        db_session,
        admin,
        LlmBackupSelectionUpsert(team_id=team.id),
    )
    assert cleared is None
    assert db_session.get(TeamLlmBackupSelection, team.id) is None
    assert snapshot_team_llm_backup(db_session, team_id=team.id) is None


def test_deleting_backup_provider_clears_only_the_backup_selection(
    db_session, make_team, make_user, make_llm_config
):
    team = make_team(name="Backup Deletion Clinic")
    admin = make_user(email="backup-delete-admin@example.com", password="password-1", is_system_admin=True)
    primary = make_llm_config(
        team=team, actor=admin, label="Primary", available_models_json=["primary-model"], has_secret=False
    )
    backup = make_llm_config(
        team=team, actor=admin, label="Backup", available_models_json=["backup-model"], has_secret=False
    )
    primary_selection = TeamLlmSelection(
        team_id=team.id,
        llm_config_id=primary.id,
        allowed_models_json=["primary-model"],
        model_name_override="primary-model",
        selected_by_user_id=admin.id,
    )
    db_session.add(primary_selection)
    db_session.commit()
    set_team_llm_backup_selection(
        db_session,
        admin,
        LlmBackupSelectionUpsert(team_id=team.id, llm_config_id=backup.id, model_name="backup-model"),
    )

    delete_llm_config(db_session, admin, config_id=backup.id, team_id=team.id)
    db_session.expire_all()
    assert db_session.get(TeamLlmConfig, primary.id) is not None
    assert db_session.get(TeamLlmConfig, backup.id) is None
    assert db_session.get(TeamLlmSelection, primary_selection.id) is not None
    assert db_session.get(TeamLlmBackupSelection, team.id) is None


def test_admin_provider_policy_page_exposes_separate_optional_backup_controls(
    client, make_team, make_user, make_llm_config
):
    team = make_team(name="Backup UI Clinic")
    admin = make_user(email="backup-ui-admin@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Visible Backup",
        model_name="backup-ui-model",
        available_models_json=["backup-ui-model"],
        has_secret=False,
    )
    login = client.post(
        "/login",
        data={"email": admin.email, "password": "password-1"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    page = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")
    assert page.status_code == 200
    assert 'data-policy-row="writing-assistant-backup"' in page.text
    assert 'action="/admin/llm-backup-selection"' in page.text
    assert "No backup provider" in page.text
    assert config.label in page.text
    assert 'data-backup-model-select' in page.text
    csrf = re.search(r'name="_csrf_token" value="([^"]+)"', page.text).group(1)
    saved = client.post(
        "/admin/llm-backup-selection",
        data={
            "_csrf_token": csrf,
            "team_id": str(team.id),
            "backup_llm_config_id": str(config.id),
            "backup_model_name": "backup-ui-model",
            "return_view": "workspace",
            "return_tab": "provider-policy",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    refreshed = client.get(saved.headers["location"])
    assert refreshed.status_code == 200
    assert f'value="{config.id}" data-models=' in refreshed.text
    assert 'data-selected-value="backup-ui-model"' in refreshed.text


def _backup_reference_document(db, owner, make_generated_document):
    root = Transcript(id=uuid4(), owner_user_id=owner.id, team_id=owner.team_id,
        title="Synthetic backup config test", ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready, retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30))
    db.add(root)
    db.flush()
    version = TranscriptVersion(transcript_id=root.id, version_no=1, text_encrypted="Synthetic")
    db.add(version)
    db.flush()
    ensure_user_dek(db, user=owner)
    return make_generated_document(owner=owner, transcript=root, transcript_version=version)


def test_empty_provider_catalog_clears_backup_selection(
    db_session, make_user, make_llm_config,
):
    admin = make_user(email="empty-catalog-admin@example.com", is_system_admin=True)
    config = make_llm_config(actor=admin, available_models_json=["model-a"], model_name="model-a")
    set_team_llm_backup_selection(db_session, admin, LlmBackupSelectionUpsert(
        team_id=config.team_id, llm_config_id=config.id, model_name="model-a"))
    config.available_models_json = []
    _reconcile_llm_config_model_selections(db_session, config=config)
    db_session.commit()
    assert db_session.get(TeamLlmBackupSelection, config.team_id) is None


@pytest.mark.parametrize("entry_point", ["upsert", "draft"])
@pytest.mark.parametrize("metadata", ["missing-model", "corrupt", "retains-model"])
def test_credential_correction_preserves_exact_queued_backup_model(
    db_session, make_user, make_llm_config, make_generated_document, monkeypatch,
    entry_point, metadata,
):
    owner = make_user()
    admin = make_user(email="correction-admin@example.com", is_system_admin=True)
    config = make_llm_config(team=owner.team, actor=admin, model_name="model-a",
        available_models_json=["model-a", "backup-b", "unused-c"])
    set_team_llm_backup_selection(db_session, admin, LlmBackupSelectionUpsert(
        team_id=owner.team_id, llm_config_id=config.id, model_name="backup-b"))
    document = _backup_reference_document(db_session, owner, make_generated_document)
    freeze_backup_selection(db_session, source=document, owner_user_id=owner.id, team_id=owner.team_id)
    document.status = GeneratedDocumentStatus.queued
    if metadata == "corrupt":
        document.backup_provider_snapshot_encrypted = "{}"
    db_session.commit()
    original_secret = config.vault_secret_ref
    catalog = ["model-a"] if metadata == "missing-model" else ["model-a", "backup-b"]
    inspection = LlmConfigInspectResult(provider_preset=config.provider_preset,
        provider_display_name="Synthetic", base_url=config.base_url, adapter_kind=config.adapter_kind,
        model_name="model-a", available_models=catalog, discovery_status="fetched",
        default_model_source="provider", requires_bearer_token=True, supports_model_discovery=True)
    monkeypatch.setattr(llm_service, "inspect_llm_contract", lambda *args, **kwargs: inspection)
    monkeypatch.setattr(llm_service, "_list_openai_compatible_chat_models", lambda **kwargs: catalog)
    secret_writes = []
    original_write = llm_service.write_team_llm_bearer_token

    def write_credential(**kwargs):
        secret_writes.append(True)
        return original_write(**kwargs)

    monkeypatch.setattr(llm_service, "write_team_llm_bearer_token", write_credential)

    def correct():
        if entry_point == "draft":
            return llm_service.replace_llm_config_draft_credential(db_session, admin,
                LlmConfigDraftReplaceCredential(team_id=owner.team_id, config_id=config.id,
                    bearer_token="synthetic-replacement"))
        return llm_service.upsert_llm_config(db_session, admin, LlmConfigUpsert(
            team_id=owner.team_id, config_id=config.id, label=config.label,
            provider_preset=config.provider_preset, base_url=config.base_url,
            model_name="model-a", credential_action="replace", bearer_token="synthetic-replacement"))

    if metadata == "retains-model":
        correct()
        assert secret_writes == [True]
        assert config.vault_secret_ref != original_secret
        assert config.available_models_json == ["model-a", "backup-b"]
    else:
        with pytest.raises(AppError) as blocked:
            correct()
        assert blocked.value.status_code == 409
        assert secret_writes == []
        assert config.vault_secret_ref == original_secret
        assert config.available_models_json == ["model-a", "backup-b", "unused-c"]


@pytest.mark.real_db_connections
@pytest.mark.parametrize("entry_point", ["upsert", "finalize"])
def test_config_edit_waits_for_queued_backup_before_checking_references(
    db_session, make_user, make_llm_config, make_generated_document, monkeypatch, entry_point,
):
    owner = make_user()
    admin = make_user(email="serialization-admin@example.com", is_system_admin=True)
    config = make_llm_config(team=owner.team, actor=admin, model_name="model-a",
        available_models_json=["model-a", "backup-b"])
    set_team_llm_backup_selection(db_session, admin, LlmBackupSelectionUpsert(
        team_id=owner.team_id, llm_config_id=config.id, model_name="backup-b"))
    document = _backup_reference_document(db_session, owner, make_generated_document)
    config_id, admin_id, team_id, label = config.id, admin.id, owner.team_id, config.label
    # Queue transaction holds the same config lock as freezing; the document
    # does not become an in-flight backup reference until after the edit starts.
    db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config_id).with_for_update())
    entered_guard = Event()
    worker_started = Event()
    original_guard = llm_service._llm_config_has_in_flight_jobs

    def guard(db, **kwargs):
        entered_guard.set()
        return original_guard(db, **kwargs)

    monkeypatch.setattr(llm_service, "_llm_config_has_in_flight_jobs", guard)

    def edit():
        with Session(bind=db_session.get_bind(), future=True) as worker:
            actor = worker.get(User, admin_id)
            worker_started.set()
            try:
                if entry_point == "finalize":
                    llm_service.finalize_llm_config_draft(worker, actor, LlmConfigFinalize(
                        team_id=team_id, config_id=config_id, label=label + " edited",
                        model_name="model-a"))
                else:
                    llm_service.upsert_llm_config(worker, actor, LlmConfigUpsert(
                        team_id=team_id, config_id=config_id, label=label + " edited",
                        model_name="model-a"))
            except AppError as error:
                return error.status_code
            return 200

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(edit)
        try:
            assert worker_started.wait(timeout=5)
            assert not entered_guard.wait(timeout=1)
            freeze_backup_selection(db_session, source=document, owner_user_id=owner.id, team_id=team_id)
            document.status = GeneratedDocumentStatus.queued
            db_session.commit()
        finally:
            # Always release the lock if an assertion fails, so the executor
            # can finish instead of hanging during test cleanup.
            db_session.rollback()
        assert pending.result(timeout=10) == 409
    db_session.refresh(config)
    assert config.label == label
