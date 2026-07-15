import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    DeidentificationProvider,
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    LlmAdapterKind,
    ProviderSecretCleanupJob,
    ProviderSecretCleanupKind,
    Team,
    TeamLlmConfig,
    TeamSttConfig,
    utcnow,
)
from app.schemas.deidentification import DeidentificationProviderUpsert
from app.schemas.llm import LlmConfigDraftReplaceCredential, LlmConfigInspectResult
from app.schemas.stt import SttConfigDraftReplaceCredential, SttInspectResult
from app.services.admin import delete_team
from app.services.provider_secret_cleanup import (
    process_provider_secret_cleanup_jobs,
    queue_orphan_provider_secret_after_rollback,
    queue_provider_secret_cleanup,
)
from app.services.deidentification import delete_deidentification_provider, upsert_deidentification_provider
from app.services.llm import delete_llm_config, replace_llm_config_draft_credential
from app.services.stt import delete_stt_config, replace_stt_config_draft_credential


def _ref(provider_id):
    return f"secret:openscribe/deidentification/provider/{provider_id}"


def test_cleanup_worker_never_deletes_live_provider_reference(db_session, make_deidentification_provider, monkeypatch):
    provider = make_deidentification_provider(label="Live provider cleanup guard")
    provider.vault_secret_ref = _ref(provider.id)
    db_session.add(provider)
    queue_provider_secret_cleanup(
        db_session,
        kind=ProviderSecretCleanupKind.deidentification,
        secret_refs=[provider.vault_secret_ref],
    )
    db_session.commit()
    deleted_refs = []
    monkeypatch.setattr(
        "app.services.provider_secret_cleanup.delete_provider_secret_by_ref",
        lambda **kwargs: deleted_refs.append(kwargs["secret_ref"]),
    )

    assert process_provider_secret_cleanup_jobs(db_session, batch_size=10) == 0
    assert deleted_refs == []
    assert db_session.scalar(select(ProviderSecretCleanupJob)) is None


def test_cleanup_worker_retries_without_abandoning_job(db_session, monkeypatch):
    secret_ref = _ref("11111111-1111-1111-1111-111111111111")
    queue_provider_secret_cleanup(
        db_session,
        kind=ProviderSecretCleanupKind.deidentification,
        secret_refs=[secret_ref],
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.provider_secret_cleanup.delete_provider_secret_by_ref",
        lambda **kwargs: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    for _ in range(10):
        job = db_session.scalar(select(ProviderSecretCleanupJob))
        job.next_attempt_at = utcnow()
        db_session.commit()
        assert process_provider_secret_cleanup_jobs(db_session, batch_size=10) == 0

    job = db_session.scalar(select(ProviderSecretCleanupJob))
    assert job is not None
    assert job.attempt_count == 10
    assert job.next_attempt_at is not None
    assert job.last_error_code == "vault_unavailable"


def test_rollback_compensation_persists_orphan_cleanup_job(db_session):
    secret_ref = _ref("22222222-2222-2222-2222-222222222222")
    queue_orphan_provider_secret_after_rollback(
        db_session,
        kind=ProviderSecretCleanupKind.deidentification,
        secret_ref=secret_ref,
    )

    job = db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == secret_ref))
    assert job is not None
    assert job.kind is ProviderSecretCleanupKind.deidentification


def test_cleanup_job_is_deduplicated_by_exact_secret_ref(db_session):
    secret_ref = _ref("33333333-3333-3333-3333-333333333333")
    queue_provider_secret_cleanup(db_session, kind=ProviderSecretCleanupKind.deidentification, secret_refs=[secret_ref])
    queue_provider_secret_cleanup(db_session, kind=ProviderSecretCleanupKind.deidentification, secret_refs=[secret_ref])
    db_session.commit()

    assert db_session.scalars(select(ProviderSecretCleanupJob)).all()[0].secret_ref == secret_ref
    assert len(db_session.scalars(select(ProviderSecretCleanupJob)).all()) == 1


def test_cleanup_enqueue_conflict_rereads_existing_job_and_preserves_kind(db_session):
    secret_ref = _ref("44444444-4444-4444-4444-444444444444")
    existing = ProviderSecretCleanupJob(
        kind=ProviderSecretCleanupKind.deidentification,
        secret_ref=secret_ref,
        next_attempt_at=utcnow(),
    )
    db_session.add(existing)
    db_session.commit()

    job_ids = queue_provider_secret_cleanup(
        db_session,
        kind=ProviderSecretCleanupKind.deidentification,
        secret_refs=[secret_ref],
    )
    db_session.commit()

    assert job_ids == [existing.id]
    assert db_session.scalars(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == secret_ref)).all() == [existing]


def test_cleanup_enqueue_conflict_rejects_different_kind(db_session):
    secret_ref = _ref("55555555-5555-5555-5555-555555555555")
    db_session.add(
        ProviderSecretCleanupJob(
            kind=ProviderSecretCleanupKind.deidentification,
            secret_ref=secret_ref,
            next_attempt_at=utcnow(),
        )
    )
    db_session.commit()

    with pytest.raises(AppError, match="conflicting kind") as exc_info:
        queue_provider_secret_cleanup(db_session, kind=ProviderSecretCleanupKind.stt, secret_refs=[secret_ref])

    assert exc_info.value.code == "provider_secret_cleanup_kind_conflict"


def test_rollback_compensation_falls_back_to_validated_direct_deletion(db_session, monkeypatch):
    secret_ref = _ref("66666666-6666-6666-6666-666666666666")
    queued_attempts = []
    deleted = []

    def fail_enqueue(*args, **kwargs):
        queued_attempts.append(kwargs["secret_refs"])
        raise AppError(502, "database_unavailable", "Database unavailable")

    monkeypatch.setattr("app.services.provider_secret_cleanup.queue_provider_secret_cleanup", fail_enqueue)
    monkeypatch.setattr(
        "app.services.provider_secret_cleanup.delete_provider_secret_by_ref",
        lambda **kwargs: deleted.append((kwargs["kind"], kwargs["secret_ref"])),
    )

    queue_orphan_provider_secret_after_rollback(
        db_session,
        kind=ProviderSecretCleanupKind.deidentification,
        secret_ref=secret_ref,
    )

    assert len(queued_attempts) == 2
    assert deleted == [(ProviderSecretCleanupKind.deidentification, secret_ref)]


def test_rollback_compensation_raises_when_enqueue_and_direct_deletion_fail(db_session, monkeypatch):
    secret_ref = _ref("77777777-7777-7777-7777-777777777777")
    deleted = []

    monkeypatch.setattr(
        "app.services.provider_secret_cleanup.queue_provider_secret_cleanup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AppError(502, "database_unavailable", "Database unavailable")),
    )

    def fail_delete(**kwargs):
        deleted.append((kwargs["kind"], kwargs["secret_ref"]))
        raise AppError(502, "vault_unavailable", "Vault unavailable")

    monkeypatch.setattr("app.services.provider_secret_cleanup.delete_provider_secret_by_ref", fail_delete)

    with pytest.raises(AppError, match="could not be durably queued or deleted") as exc_info:
        queue_orphan_provider_secret_after_rollback(
            db_session,
            kind=ProviderSecretCleanupKind.deidentification,
            secret_ref=secret_ref,
        )

    assert exc_info.value.code == "provider_secret_cleanup_compensation_failed"
    assert deleted == [(ProviderSecretCleanupKind.deidentification, secret_ref)]


def test_deidentification_delete_queues_secret_before_provider_row_is_removed(
    db_session,
    make_deidentification_provider,
    make_user,
):
    admin = make_user(email="provider-cleanup-admin@example.com", is_system_admin=True)
    provider = make_deidentification_provider(actor=admin, label="Delete durable provider", has_secret=True)
    secret_ref = provider.vault_secret_ref

    delete_deidentification_provider(db_session, admin, provider_id=provider.id)

    assert db_session.get(DeidentificationProvider, provider.id) is None
    cleanup_job = db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == secret_ref))
    assert cleanup_job is not None
    assert cleanup_job.kind is ProviderSecretCleanupKind.deidentification


def test_stt_config_delete_queues_root_and_revision_refs_before_rows_are_removed(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="STT durable cleanup")
    admin = make_user(email="stt-cleanup-admin@example.com", is_system_admin=True)
    root = make_stt_config(team=team, actor=admin, label="STT root")
    revision = make_stt_config(team=team, actor=admin, label="STT revision")
    revision.revision_of_config_id = root.id
    db_session.add(revision)
    db_session.commit()
    root_id = root.id
    revision_id = revision.id
    root_ref = root.vault_secret_ref
    revision_ref = revision.vault_secret_ref

    queued_refs = []
    from app.services import stt as stt_service

    original_queue = stt_service.queue_provider_secret_cleanup

    def observe_queue(db, *, kind, secret_refs):
        assert db.get(TeamSttConfig, root.id) is not None
        assert db.get(TeamSttConfig, revision.id) is not None
        queued_refs.extend(secret_refs)
        return original_queue(db, kind=kind, secret_refs=secret_refs)

    monkeypatch.setattr(stt_service, "queue_provider_secret_cleanup", observe_queue)

    delete_stt_config(db_session, admin, config_id=root.id, team_id=team.id)

    assert queued_refs == [revision_ref, root_ref]
    assert db_session.get(TeamSttConfig, root_id) is None
    assert db_session.get(TeamSttConfig, revision_id) is None
    assert {
        job.secret_ref
        for job in db_session.scalars(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.kind == ProviderSecretCleanupKind.stt))
    } == set(queued_refs)


def test_llm_config_delete_queues_root_and_revision_refs_before_rows_are_removed(
    db_session,
    make_team,
    make_user,
    make_llm_config,
    monkeypatch,
):
    team = make_team(name="LLM durable cleanup")
    admin = make_user(email="llm-cleanup-admin@example.com", is_system_admin=True)
    root = make_llm_config(team=team, actor=admin, label="LLM root")
    revision = make_llm_config(team=team, actor=admin, label="LLM revision")
    revision.revision_of_config_id = root.id
    db_session.add(revision)
    db_session.commit()
    root_id = root.id
    revision_id = revision.id
    root_ref = root.vault_secret_ref
    revision_ref = revision.vault_secret_ref

    queued_refs = []
    from app.services import llm as llm_service

    original_queue = llm_service.queue_provider_secret_cleanup

    def observe_queue(db, *, kind, secret_refs):
        assert db.get(TeamLlmConfig, root.id) is not None
        assert db.get(TeamLlmConfig, revision.id) is not None
        queued_refs.extend(secret_refs)
        return original_queue(db, kind=kind, secret_refs=secret_refs)

    monkeypatch.setattr(llm_service, "queue_provider_secret_cleanup", observe_queue)

    delete_llm_config(db_session, admin, config_id=root.id, team_id=team.id)

    assert queued_refs == [revision_ref, root_ref]
    assert db_session.get(TeamLlmConfig, root_id) is None
    assert db_session.get(TeamLlmConfig, revision_id) is None
    assert {
        job.secret_ref
        for job in db_session.scalars(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.kind == ProviderSecretCleanupKind.llm))
    } == set(queued_refs)


def test_cleanup_worker_keeps_legacy_manually_shared_root_ref_when_only_revision_is_deleted(
    db_session,
    make_team,
    make_user,
    make_llm_config,
    monkeypatch,
):
    team = make_team(name="Shared ref cleanup guard")
    admin = make_user(email="shared-ref-admin@example.com", is_system_admin=True)
    root = make_llm_config(team=team, actor=admin, label="Shared root")
    revision = make_llm_config(team=team, actor=admin, label="Shared revision")
    revision.revision_of_config_id = root.id
    # New revision drafts own their refs. Preserve guard coverage for legacy or
    # manually-created rows that still share a root credential reference.
    revision.vault_secret_ref = root.vault_secret_ref
    db_session.add(revision)
    db_session.commit()
    deleted_refs = []
    monkeypatch.setattr(
        "app.services.provider_secret_cleanup.delete_provider_secret_by_ref",
        lambda **kwargs: deleted_refs.append(kwargs["secret_ref"]),
    )

    delete_llm_config(db_session, admin, config_id=revision.id, team_id=team.id)

    assert db_session.get(TeamLlmConfig, root.id) is not None
    assert process_provider_secret_cleanup_jobs(db_session, batch_size=10) == 0
    assert deleted_refs == []
    assert db_session.scalar(select(ProviderSecretCleanupJob)) is None


def test_llm_draft_credential_replacement_versions_ref_and_queues_old_ref(
    db_session,
    make_team,
    make_user,
    make_llm_config,
    monkeypatch,
):
    team = make_team(name="LLM credential replacement")
    admin = make_user(email="llm-replace-admin@example.com", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Replace LLM credential")
    old_secret_ref = config.vault_secret_ref
    new_secret_ref = f"{old_secret_ref}/version-two"
    inspection = LlmConfigInspectResult(
        provider_preset=config.provider_preset,
        provider_display_name="OpenAI",
        base_url=config.base_url,
        adapter_kind=LlmAdapterKind.openai_chat,
        model_name="gpt-4o-mini",
        available_models=["gpt-4o-mini"],
        discovery_status="fetched",
        default_model_source="provider",
        requires_bearer_token=True,
        supports_model_discovery=True,
    )
    monkeypatch.setattr("app.services.llm.inspect_llm_contract", lambda *args, **kwargs: inspection)
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda **kwargs: new_secret_ref)

    replaced, _ = replace_llm_config_draft_credential(
        db_session,
        admin,
        LlmConfigDraftReplaceCredential(team_id=team.id, config_id=config.id, bearer_token="replacement-token"),
    )

    assert replaced.vault_secret_ref == new_secret_ref
    assert replaced.vault_secret_ref != old_secret_ref
    cleanup_job = db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == old_secret_ref))
    assert cleanup_job is not None
    assert cleanup_job.kind is ProviderSecretCleanupKind.llm


def test_llm_replacement_queue_failure_compensates_only_new_vault_ref(
    db_session,
    make_team,
    make_user,
    make_llm_config,
    monkeypatch,
):
    team = make_team(name="LLM replacement queue failure")
    admin = make_user(email="llm-replace-queue-failure@example.com", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="LLM replacement queue failure")
    old_secret_ref = config.vault_secret_ref
    new_secret_ref = f"{old_secret_ref}/new"
    inspection = LlmConfigInspectResult(
        provider_preset=config.provider_preset,
        provider_display_name="OpenAI",
        base_url=config.base_url,
        adapter_kind=LlmAdapterKind.openai_chat,
        model_name="gpt-4o-mini",
        available_models=["gpt-4o-mini"],
        discovery_status="fetched",
        default_model_source="provider",
        requires_bearer_token=True,
        supports_model_discovery=True,
    )
    monkeypatch.setattr("app.services.llm.inspect_llm_contract", lambda *args, **kwargs: inspection)
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda **kwargs: new_secret_ref)
    monkeypatch.setattr(
        "app.services.llm.queue_provider_secret_cleanup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AppError(500, "queue_failed", "synthetic queue failure")),
    )

    with pytest.raises(AppError, match="synthetic queue failure"):
        replace_llm_config_draft_credential(
            db_session,
            admin,
            LlmConfigDraftReplaceCredential(team_id=team.id, config_id=config.id, bearer_token="replacement-token"),
        )

    assert db_session.get(TeamLlmConfig, config.id).vault_secret_ref == old_secret_ref
    assert db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == old_secret_ref)) is None
    assert db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == new_secret_ref)) is not None


def test_stt_replacement_queue_failure_compensates_only_new_vault_ref(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="STT replacement queue failure")
    admin = make_user(email="stt-replace-queue-failure@example.com", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="STT replacement queue failure")
    old_secret_ref = config.vault_secret_ref
    new_secret_ref = f"{old_secret_ref}/new"
    inspection = SttInspectResult(
        base_url=config.base_url,
        openapi_path=None,
        adapter_kind=config.adapter_kind,
        transcribe_path=config.transcribe_path,
        model_name=config.model_name,
        model_field_name=config.model_field_name,
        file_field_name=config.file_field_name,
        language=config.language,
        language_field_name=config.language_field_name,
        response_text_path=config.response_text_path,
        segments_path=config.segments_path,
        segment_text_field=config.segment_text_field,
        segment_start_field=config.segment_start_field,
        segment_end_field=config.segment_end_field,
        segment_speaker_field=config.segment_speaker_field,
        extra_form_fields_json=config.extra_form_fields_json,
        candidate_paths=[],
        operation_summary=None,
        available_models=[config.model_name],
        field_tips=[],
        notes=[],
    )
    monkeypatch.setattr("app.services.stt.inspect_stt_contract", lambda *args, **kwargs: inspection)
    monkeypatch.setattr("app.services.stt.write_team_stt_bearer_token", lambda **kwargs: new_secret_ref)
    monkeypatch.setattr(
        "app.services.stt.queue_provider_secret_cleanup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AppError(500, "queue_failed", "synthetic queue failure")),
    )

    with pytest.raises(AppError, match="synthetic queue failure"):
        replace_stt_config_draft_credential(
            db_session,
            admin,
            SttConfigDraftReplaceCredential(team_id=team.id, config_id=config.id, bearer_token="replacement-token"),
        )

    assert db_session.get(TeamSttConfig, config.id).vault_secret_ref == old_secret_ref
    assert db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == old_secret_ref)) is None
    assert db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == new_secret_ref)) is not None


def test_deidentification_replacement_queue_failure_compensates_only_new_vault_ref(
    db_session,
    make_user,
    make_deidentification_provider,
    monkeypatch,
):
    admin = make_user(email="deid-replace-queue-failure@example.com", is_system_admin=True)
    provider = make_deidentification_provider(
        actor=admin,
        label="Deid replacement queue failure",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.bearer,
        has_secret=True,
    )
    old_secret_ref = provider.vault_secret_ref
    new_secret_ref = f"{old_secret_ref}/new"
    monkeypatch.setattr("app.services.deidentification.write_deidentification_bearer_token", lambda **kwargs: new_secret_ref)
    monkeypatch.setattr(
        "app.services.deidentification.queue_provider_secret_cleanup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AppError(500, "queue_failed", "synthetic queue failure")),
    )

    with pytest.raises(AppError, match="synthetic queue failure"):
        upsert_deidentification_provider(
            db_session,
            admin,
            DeidentificationProviderUpsert(
                provider_id=provider.id,
                label=provider.label,
                adapter_kind=DeidentificationAdapterKind.generic_rest,
                base_url="https://deid.example.com",
                detect_path="/detect",
                auth_mode=DeidentificationAuthMode.bearer,
                bearer_token="replacement-token",
            ),
        )

    assert db_session.get(DeidentificationProvider, provider.id).vault_secret_ref == old_secret_ref
    assert db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == old_secret_ref)) is None
    assert db_session.scalar(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref == new_secret_ref)) is not None


def test_team_delete_queues_stt_and_llm_refs_without_direct_vault_deletion(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_llm_config,
    monkeypatch,
):
    admin = make_user(email="team-delete-cleanup-admin@example.com", is_system_admin=True)
    team = make_team(name="Team durable cleanup")
    member = make_user(email="team-delete-member@example.com", team=team)
    stt_config = make_stt_config(team=team, actor=admin, label="Team STT")
    llm_config = make_llm_config(team=team, actor=admin, label="Team LLM")
    stt_secret_ref = stt_config.vault_secret_ref
    llm_secret_ref = llm_config.vault_secret_ref
    direct_vault_deletes = []
    monkeypatch.setattr(
        "app.services.vault.delete_provider_secret_by_ref",
        lambda **kwargs: direct_vault_deletes.append(kwargs),
    )

    delete_team(db_session, admin, team_id=team.id)

    assert db_session.get(Team, team.id) is None
    assert db_session.get(TeamSttConfig, stt_config.id) is None
    assert db_session.get(TeamLlmConfig, llm_config.id) is None
    assert db_session.get(type(member), member.id) is None
    assert direct_vault_deletes == []
    cleanup_jobs = {
        (job.kind, job.secret_ref)
        for job in db_session.scalars(select(ProviderSecretCleanupJob))
    }
    assert cleanup_jobs == {
        (ProviderSecretCleanupKind.stt, stt_secret_ref),
        (ProviderSecretCleanupKind.llm, llm_secret_ref),
    }
