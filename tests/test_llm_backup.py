"""Synthetic checks for immutable choices and durable one-shot backup accounting."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (AttemptKind, AttemptOutcome, AttemptStatus, ProviderAttempt,
    GeneratedDocumentStatus, HallucinationCheckStatus, QuotaResource, TeamLlmBackupSelection, TeamRole, Transcript, TranscriptIngestionMode,
    TranscriptStatus, TranscriptVersion, utcnow)
from app.services.content_crypto import ensure_user_dek, is_encrypted_envelope
from app.services.llm_adapters.runtime import build_provider_snapshot
from app.services.llm_backup import (copy_backup_selection, effective_provider_attempt,
    freeze_backup_selection, provider_result_is_current, read_backup_provider,
    reserve_backup_attempt)
from app.services.quota_lifecycle import terminalize_attempts_for_transcripts
from app.services.quotas import (mark_provider_attempt_submitted, reserve_provider_attempt,
    settle_provider_attempt_tokens, settle_provider_attempt_unknown_tokens)


def _document(db, owner, make_generated_document):
    root = Transcript(id=uuid4(), owner_user_id=owner.id, team_id=owner.team_id,
        title="Synthetic", ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready, retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30))
    db.add(root); db.flush()
    version = TranscriptVersion(transcript_id=root.id, version_no=1, text_encrypted="synthetic")
    db.add(version); db.flush()
    ensure_user_dek(db, user=owner)
    return make_generated_document(owner=owner, transcript=root, transcript_version=version)


def _primary(db, owner, document, *, kind=AttemptKind.llm_generation, number=1):
    now = utcnow()
    return reserve_provider_attempt(db, team_id=owner.team_id, owner_user_id=owner.id,
        resource=QuotaResource.tokens, attempt_kind=kind,
        correlation_id=document.id, attempt_number=number, reserved_units=10,
        authorized_at=now, reservation_valid_until=now + timedelta(minutes=5),
        transcript_id=document.transcript_id, generated_document_id=document.id,
        provider_adapter="openai_chat", provider_model="primary")


def _settle(db, attempt):
    now = utcnow()
    mark_provider_attempt_submitted(db, attempt_id=attempt.id, now=now,
        deadline_at=now + timedelta(seconds=90))
    settle_provider_attempt_unknown_tokens(db, attempt_id=attempt.id, now=now)


def test_queued_backup_is_immutable_owner_encrypted_and_copy_rebinds_aad(
    db_session, make_user, make_llm_config, make_generated_document,
):
    owner = make_user()
    config = make_llm_config(team=owner.team, available_models_json=["backup-a", "backup-b"])
    selection = TeamLlmBackupSelection(team_id=owner.team_id, llm_config_id=config.id, model_name="backup-a")
    db_session.add(selection); db_session.commit()
    first = _document(db_session, owner, make_generated_document)
    second = _document(db_session, owner, make_generated_document)
    freeze_backup_selection(db_session, source=first, owner_user_id=owner.id, team_id=owner.team_id)
    selection.model_name = "backup-b"; db_session.flush()
    assert read_backup_provider(db_session, source=first).model == "backup-a"
    copy_backup_selection(db_session, source=first, target=second)
    assert is_encrypted_envelope(first.backup_provider_snapshot_encrypted)
    assert second.backup_provider_snapshot_encrypted != first.backup_provider_snapshot_encrypted
    assert read_backup_provider(db_session, source=second).model == "backup-a"
    second.backup_provider_snapshot_encrypted = first.backup_provider_snapshot_encrypted
    with pytest.raises(AppError, match="decrypt"):
        read_backup_provider(db_session, source=second)


def test_inactive_optional_backup_does_not_prevent_primary_queue(
    db_session, make_user, make_llm_config, make_generated_document,
):
    owner = make_user()
    config = make_llm_config(team=owner.team, available_models_json=["backup"], is_active=False)
    db_session.add(TeamLlmBackupSelection(team_id=owner.team_id, llm_config_id=config.id, model_name="backup"))
    db_session.commit()
    document = _document(db_session, owner, make_generated_document)
    freeze_backup_selection(db_session, source=document, owner_user_id=owner.id, team_id=owner.team_id)
    assert read_backup_provider(db_session, source=document) is None


def test_backup_child_does_not_collide_with_checker_and_fences_primary_results(
    db_session, make_user, make_llm_config, make_generated_document,
):
    owner = make_user()
    config = make_llm_config(team=owner.team)
    document = _document(db_session, owner, make_generated_document)
    primary = _primary(db_session, owner, document)
    checker = _primary(db_session, owner, document, kind=AttemptKind.llm_hallucination_check, number=2)
    _settle(db_session, primary)
    provider = build_provider_snapshot(config=config, model="backup")
    child = reserve_backup_attempt(db_session, primary=primary, provider=provider, reserved_units=20)
    assert child.backup_of_attempt_id == primary.id
    assert child.correlation_id == primary.id and child.attempt_number == 1
    assert child.generated_document_id == primary.generated_document_id
    assert child.transcript_id == primary.transcript_id
    assert checker.correlation_id == primary.correlation_id
    assert reserve_backup_attempt(db_session, primary=primary, provider=provider, reserved_units=20).id == child.id
    assert effective_provider_attempt(db_session, primary=primary).id == child.id
    now = utcnow()
    mark_provider_attempt_submitted(db_session, attempt_id=child.id, now=now,
        deadline_at=now + timedelta(seconds=90))
    assert not provider_result_is_current(db_session, primary_id=primary.id, submitted_attempt_id=primary.id)
    assert provider_result_is_current(db_session, primary_id=primary.id, submitted_attempt_id=child.id)
    assert not provider_result_is_current(db_session, primary_id=primary.id, submitted_attempt_id=child.id,
        now=now + timedelta(seconds=91))
    assert terminalize_attempts_for_transcripts(db_session, [document.transcript_id], now).settled_unknown == 1
    assert child.status is AttemptStatus.settled and child.outcome is AttemptOutcome.unknown
    assert not provider_result_is_current(db_session, primary_id=primary.id, submitted_attempt_id=child.id)


def test_backup_cannot_chain_or_use_other_team_config(
    db_session, make_user, make_team, make_llm_config, make_generated_document,
):
    owner = make_user()
    config = make_llm_config(team=owner.team)
    other_config = make_llm_config(team=make_team(name="Other backup team"))
    document = _document(db_session, owner, make_generated_document)
    primary = _primary(db_session, owner, document)
    _settle(db_session, primary)
    with pytest.raises(AppError) as error:
        reserve_backup_attempt(db_session, primary=primary,
            provider=build_provider_snapshot(config=other_config), reserved_units=20)
    assert error.value.code == "llm_backup_scope_mismatch"
    child = reserve_backup_attempt(db_session, primary=primary,
        provider=build_provider_snapshot(config=config), reserved_units=20)
    with pytest.raises(AppError) as error:
        reserve_backup_attempt(db_session, primary=child,
            provider=build_provider_snapshot(config=config), reserved_units=20)
    assert error.value.code == "provider_attempt_backup_identity_invalid"


def test_backup_quota_failure_preserves_primary_conservative_settlement(
    db_session, make_user, make_llm_config, make_generated_document,
):
    owner = make_user()
    config = make_llm_config(team=owner.team)
    document = _document(db_session, owner, make_generated_document)
    owner.daily_token_limit = owner.monthly_token_limit = 10
    primary = _primary(db_session, owner, document)
    _settle(db_session, primary)
    with pytest.raises(AppError) as error:
        with db_session.begin_nested():
            reserve_backup_attempt(db_session, primary=primary,
                provider=build_provider_snapshot(config=config), reserved_units=1)
    assert error.value.code == "quota_exceeded"
    assert primary.status is AttemptStatus.settled and primary.settled_units == 10
    assert db_session.scalar(select(ProviderAttempt.id).where(ProviderAttempt.backup_of_attempt_id == primary.id)) is None


def test_backup_rejects_expired_root_and_plaintext_snapshot(
    db_session, make_user, make_llm_config, make_generated_document,
):
    owner = make_user()
    config = make_llm_config(team=owner.team)
    document = _document(db_session, owner, make_generated_document)
    primary = _primary(db_session, owner, document)
    _settle(db_session, primary)
    document.backup_llm_config_id = config.id
    document.backup_provider_snapshot_encrypted = "{}"
    with pytest.raises(AppError) as error:
        read_backup_provider(db_session, source=document)
    assert error.value.code == "llm_backup_snapshot_invalid"
    document.transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(AppError) as error:
        reserve_backup_attempt(db_session, primary=primary,
            provider=build_provider_snapshot(config=config), reserved_units=1)
    assert error.value.code == "llm_backup_source_inactive"


def test_checker_failure_presentation_uses_final_backup_not_parent_number(
    db_session, make_user, make_llm_config, make_generated_document,
):
    from app.services.generation_presentation import generated_document_status_projection

    owner = make_user(team_role=TeamRole.leader)
    config = make_llm_config(team=owner.team)
    document = _document(db_session, owner, make_generated_document)
    primary = _primary(db_session, owner, document, kind=AttemptKind.llm_hallucination_check, number=2)
    _settle(db_session, primary)
    primary.error_code = "llm_provider_timeout"
    child = reserve_backup_attempt(db_session, primary=primary,
        provider=build_provider_snapshot(config=config), reserved_units=10)
    now = utcnow()
    mark_provider_attempt_submitted(db_session, attempt_id=child.id, now=now,
        deadline_at=now + timedelta(seconds=30))
    settle_provider_attempt_tokens(db_session, attempt_id=child.id,
        reported_total_tokens=1, outcome=AttemptOutcome.failed)
    child.error_code = "hallucination_check_invalid_response"
    document.status = GeneratedDocumentStatus.ready
    document.hallucination_check_status = HallucinationCheckStatus.failed_invalid_response
    db_session.flush()

    failure = generated_document_status_projection(db_session, document, owner)["checker_failure_presentation"]
    assert failure["category"] == "input_or_validation"
    assert failure["technical_details"]["application_code"] == "hallucination_check_invalid_response"
