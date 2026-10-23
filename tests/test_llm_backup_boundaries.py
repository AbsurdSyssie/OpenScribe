"""Synthetic worker-level failover proofs for quota, deadlines and ownership."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptKind, AttemptOutcome, AttemptStatus, GeneratedDocumentStatus,
    ProviderAttempt, ProviderSettlementBasis, TeamLlmBackupSelection, TeamRole,
    TemplateScope, Transcript, TranscriptIngestionMode, TranscriptStatus, utcnow,
)
from app.services import templates
from app.services.llm_backup_policy import tag_provider_failure
from app.services.templates import (
    generated_document_text, process_generated_document,
    queue_document_generation_from_template,
)
from app.services.transcripts import set_transcript_draft_text


VALID_NOTE = '{"title":"Synthetic visit","content":"Supported synthetic note."}'


def _usage(total):
    return {"input_tokens": 2, "output_tokens": total - 2, "total_tokens": total,
        "duration_ms": 6, "provider_duration_ms": 4}


@pytest.fixture
def backup_job(db_session, make_team, make_user, make_llm_config, make_llm_selection,
               make_template, monkeypatch):
    suffix = uuid4()
    team = make_team(name=f"Backup boundary clinic {suffix}")
    admin = make_user(email=f"boundary-admin-{suffix}@example.com", is_system_admin=True)
    owner = make_user(email=f"boundary-owner-{suffix}@example.com", team=team, team_role=TeamRole.user)
    primary = make_llm_config(team=team, actor=admin, model_name="primary-model",
        available_models_json=["primary-model"], has_secret=False)
    backup = make_llm_config(team=team, actor=admin, label="Synthetic backup",
        model_name="backup-model", available_models_json=["backup-model"], has_secret=False)
    make_llm_selection(config=primary, actor=admin, model_name_override="primary-model")
    db_session.add(TeamLlmBackupSelection(team_id=team.id, llm_config_id=backup.id,
        model_name="backup-model"))
    transcript = Transcript(owner_user_id=owner.id, team_id=team.id,
        title="Synthetic visit", ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready, retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30))
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript,
        plaintext="Synthetic consultation source without patient identifiers.")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner,
        name="Synthetic boundary template", prompt_text="Write a synthetic note.")
    db_session.commit()
    document = queue_document_generation_from_template(db_session, owner,
        transcript_id=transcript.id, template_id=template.id)
    monkeypatch.setattr(templates, "_resolve_generation_credential", lambda _config: None)
    return SimpleNamespace(document=document, transcript=transcript, owner=owner,
        primary=primary, backup=backup)


def _attempts(db, document_id):
    return list(db.scalars(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document_id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_generation,
    )))


@pytest.mark.parametrize("primary_result", ["success", "hard-quota", "unknown-quota"])
def test_primary_success_and_provider_quota_never_dispatch_backup(
    db_session, backup_job, monkeypatch, primary_result,
):
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        if primary_result != "success":
            details = {"provider_http_status": 429}
            if primary_result == "hard-quota":
                details.update(provider_error_code="quota_exceeded",
                    backup_failure_class="provider_quota_blocked")
            raise AppError(429, "llm_provider_rate_limited", "Synthetic quota response", details)
        return VALID_NOTE, _usage(12)

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    result = process_generated_document(db_session, document_id=backup_job.document.id)

    assert calls == [backup_job.primary.id]
    assert result.status is (GeneratedDocumentStatus.ready if primary_result == "success" else GeneratedDocumentStatus.failed)
    attempts = _attempts(db_session, result.id)
    assert len(attempts) == 1
    assert attempts[0].status is AttemptStatus.settled
    assert attempts[0].backup_of_attempt_id is None


@pytest.mark.parametrize("backup_valid", [True, False])
def test_invalid_outputs_account_each_response_and_never_replay_delivery(
    db_session, backup_job, monkeypatch, backup_valid,
):
    calls = []
    requests = []

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        requests.append(kwargs["request_body"]["messages"])
        if len(calls) == 1:
            return "not-json", _usage(5)
        return (VALID_NOTE if backup_valid else "also-not-json"), _usage(12)

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    result = process_generated_document(db_session, document_id=backup_job.document.id)

    assert calls == [backup_job.primary.id, backup_job.backup.id]
    assert requests[0] == requests[1]
    assert result.status is (GeneratedDocumentStatus.ready if backup_valid else GeneratedDocumentStatus.failed)
    attempts = _attempts(db_session, result.id)
    assert len(attempts) == 2
    primary = next(item for item in attempts if item.backup_of_attempt_id is None)
    child = next(item for item in attempts if item.backup_of_attempt_id is not None)
    assert child.backup_of_attempt_id == primary.id
    assert primary.status is child.status is AttemptStatus.settled
    assert primary.outcome is AttemptOutcome.failed
    assert primary.settled_units == 5
    assert child.settled_units == 12
    assert primary.settlement_basis is child.settlement_basis is ProviderSettlementBasis.reported
    assert child.outcome is (AttemptOutcome.succeeded if backup_valid else AttemptOutcome.failed)

    repeated = process_generated_document(db_session, document_id=result.id)
    assert repeated.status is result.status
    assert calls == [backup_job.primary.id, backup_job.backup.id]
    assert len(_attempts(db_session, result.id)) == 2


def test_backup_quota_exhaustion_keeps_unknown_primary_accounted_without_second_dispatch(
    db_session, backup_job, monkeypatch,
):
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        # A policy reduction after the primary dispatch must block backup;
        # already-submitted primary work still receives conservative charging.
        backup_job.owner.daily_token_limit = 1
        backup_job.owner.monthly_token_limit = 1
        db_session.flush()
        raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    result = process_generated_document(db_session, document_id=backup_job.document.id)

    assert result.status is GeneratedDocumentStatus.failed
    assert result.error_code == "quota_exceeded"
    assert calls == [backup_job.primary.id]
    attempts = _attempts(db_session, result.id)
    assert len(attempts) == 1
    assert attempts[0].status is AttemptStatus.settled
    assert attempts[0].outcome is AttemptOutcome.unknown
    assert attempts[0].settlement_basis is ProviderSettlementBasis.conservative_unknown
    assert attempts[0].settled_units == attempts[0].reserved_units


def test_timeout_past_primary_deadline_can_dispatch_one_backup_on_live_root(
    db_session, backup_job, monkeypatch,
):
    calls = []
    clock = [utcnow()]
    monkeypatch.setattr(templates, "utcnow", lambda: clock[0])

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        if len(calls) == 1:
            primary = _attempts(db_session, backup_job.document.id)[0]
            clock[0] = primary.deadline_at + timedelta(seconds=1)
            raise tag_provider_failure(AppError(504, "llm_provider_timeout", "Synthetic timeout"))
        return VALID_NOTE, _usage(12)

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    result = process_generated_document(db_session, document_id=backup_job.document.id)

    assert result.status is GeneratedDocumentStatus.ready
    assert result.model_used == "backup-model"
    assert calls == [backup_job.primary.id, backup_job.backup.id]
    attempts = _attempts(db_session, result.id)
    assert len(attempts) == 2
    primary = next(item for item in attempts if item.backup_of_attempt_id is None)
    child = next(item for item in attempts if item.backup_of_attempt_id is not None)
    assert primary.status is child.status is AttemptStatus.settled
    assert primary.outcome is AttemptOutcome.unknown
    assert primary.settled_units == primary.reserved_units
    assert child.authorized_at > primary.deadline_at
    assert child.outcome is AttemptOutcome.succeeded


@pytest.mark.parametrize("late_boundary", ["root-before-backup", "root-expired", "child-deadline"])
def test_late_backup_response_cannot_be_promoted_to_a_draft(
    db_session, backup_job, monkeypatch, late_boundary,
):
    calls = []
    clock = [utcnow()]
    monkeypatch.setattr(templates, "utcnow", lambda: clock[0])

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        if len(calls) == 1:
            if late_boundary == "root-before-backup":
                clock[0] = backup_job.transcript.retention_expires_at + timedelta(seconds=1)
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))
        child = next(item for item in _attempts(db_session, backup_job.document.id)
            if item.backup_of_attempt_id is not None)
        clock[0] = (backup_job.transcript.retention_expires_at if late_boundary == "root-expired"
            else child.deadline_at) + timedelta(seconds=1)
        return VALID_NOTE, _usage(12)

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    result = process_generated_document(db_session, document_id=backup_job.document.id)

    assert calls == ([backup_job.primary.id] if late_boundary == "root-before-backup"
        else [backup_job.primary.id, backup_job.backup.id])
    assert result.status is not GeneratedDocumentStatus.ready
    assert not generated_document_text(db_session, document=result, field="original_output_text_encrypted")
    assert len(_attempts(db_session, result.id)) == (1 if late_boundary == "root-before-backup" else 2)


@pytest.mark.parametrize("credential_failure", ["llm_invalid_credential", "vault_read_failed"])
def test_preclaim_credentials_fail_over_only_for_provider_failure_without_charging_primary(
    db_session, backup_job, monkeypatch, credential_failure,
):
    calls = []

    def credential(config):
        if config.id == backup_job.primary.id:
            raise AppError(502, credential_failure, "Synthetic credential failure")
        return None

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        return VALID_NOTE, _usage(12)

    monkeypatch.setattr(templates, "_resolve_generation_credential", credential)
    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    if credential_failure == "vault_read_failed":
        with pytest.raises(AppError) as failure:
            process_generated_document(db_session, document_id=backup_job.document.id)
        assert failure.value.code == credential_failure
        result = backup_job.document
        db_session.refresh(result)
        assert result.status is GeneratedDocumentStatus.failed
        assert calls == []
    else:
        result = process_generated_document(db_session, document_id=backup_job.document.id)
        assert result.status is GeneratedDocumentStatus.ready
        assert calls == [backup_job.backup.id]
    attempts = _attempts(db_session, result.id)
    assert len(attempts) == (1 if credential_failure == "vault_read_failed" else 2)
    primary = next(item for item in attempts if item.backup_of_attempt_id is None)
    assert primary.status is AttemptStatus.cancelled
    assert primary.submitted_at is None
    assert primary.settled_units is None
    if credential_failure == "llm_invalid_credential":
        child = next(item for item in attempts if item.backup_of_attempt_id is not None)
        assert child.status is AttemptStatus.settled
        assert child.outcome is AttemptOutcome.succeeded
        assert child.settled_units == 12
