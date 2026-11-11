from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
import pytest

from app.errors import AppError
from app.models import (
    AttemptKind, AttemptOutcome, AttemptStatus, GeneratedDocumentStatus, ProviderAttempt,
    ProviderUsageEvent, TeamHallucinationCheckSelection, TeamLlmBackupSelection,
    TeamRole, TemplateMode, TemplateScope, Transcript,
    TranscriptIngestionMode, TranscriptStatus, utcnow,
)
from app.services import templates
from app.services.llm_backup_policy import tag_provider_failure
from app.services.llm_backup import read_backup_provider
from app.services.admin import admin_usage_overview
from app.services.templates import (
    process_generated_document, queue_document_generation_from_template,
)
from app.services.transcripts import set_transcript_draft_text


@pytest.mark.parametrize("preflight,structured,scenario", [(False, False, "success"), (False, True, "success"), (True, False, "success"), (False, False, "quota"), (False, False, "expired"), (False, False, "timeout")])
def test_backup_invalid_json_repaired_once_with_separate_claim(
    db_session, make_team, make_user, make_llm_config, make_llm_selection,
    make_template, monkeypatch, preflight, structured, scenario,
):
    team = make_team(name=f"Synthetic repair {uuid4()}")
    admin = make_user(email=f"repair-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email=f"repair-owner-{uuid4()}@example.com", password="password-2", team=team, team_role=TeamRole.user)
    primary = make_llm_config(team=team, actor=admin, has_secret=False, model_name="primary-model", available_models_json=["primary-model"])
    backup = make_llm_config(team=team, actor=admin, has_secret=False, label="Backup", model_name="backup-model", available_models_json=["backup-model"])
    make_llm_selection(config=primary, actor=admin, model_name_override="primary-model")
    db_session.add(TeamLlmBackupSelection(team_id=team.id, llm_config_id=backup.id, model_name="backup-model"))
    transcript = Transcript(owner_user_id=owner.id, team_id=team.id, title="Synthetic",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30))
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic private source marker.")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner,
        name="Synthetic", prompt_text="Write a note.", **({"mode": TemplateMode.structured,
        "config_json": {"profile": "emis", "sections": [{"section_key": "problem", "instruction": "Summarise.", "section_order": 0}]}} if structured else {}))
    db_session.commit()
    document = queue_document_generation_from_template(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    calls = []
    malformed = '{"title":"Synthetic","content":'
    backup_credential = object()

    def resolve(config):
        if preflight and config.id == primary.id:
            raise AppError(401, "llm_invalid_credential", "Synthetic credential failure")
        return backup_credential if config.id == backup.id else None

    def invoke(**kwargs):
        calls.append(kwargs)
        if kwargs["config"].id == primary.id:
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic failure"))
        assert kwargs["credential"] is backup_credential
        if sum(c["config"].id == backup.id for c in calls) == 1:
            if scenario == "quota":
                owner.daily_token_limit = 1
                owner.monthly_token_limit = 1
                db_session.commit()
            if scenario == "expired":
                transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
                db_session.commit()
            return malformed, {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
        body = kwargs["request_body"]
        serialized = str(body)
        assert malformed in serialized
        assert "Expected JSON schema" in serialized
        assert "Synthetic private source marker" not in serialized
        attempts_now = list(db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.generated_document_id == document.id)))
        backup_now = next(a for a in attempts_now if a.backup_of_attempt_id is not None)
        repair_now = next(a for a in attempts_now if a.repair_of_attempt_id is not None)
        from app.services.llm_backup import provider_result_is_current
        assert not provider_result_is_current(db_session, primary_id=backup_now.backup_of_attempt_id, submitted_attempt_id=backup_now.id)
        assert provider_result_is_current(db_session, primary_id=backup_now.backup_of_attempt_id, submitted_attempt_id=repair_now.id)
        if scenario == "timeout":
            raise AppError(504, "llm_provider_timeout", "Synthetic timeout")
        content = '{"problem":"Synthetic note."}' if structured else '"Synthetic note."'
        return '{"title":"Synthetic","content":' + content + '}', {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9}

    monkeypatch.setattr(templates, "_resolve_generation_credential", resolve)
    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    result = process_generated_document(db_session, document_id=document.id)
    if scenario in {"quota", "expired"}:
        assert sum(c["config"].id == backup.id for c in calls) == 1
        assert not db_session.scalar(select(ProviderAttempt.id).where(ProviderAttempt.generated_document_id == document.id, ProviderAttempt.repair_of_attempt_id.is_not(None)))
        if scenario == "quota":
            assert result.error_code in {"quota_exceeded", "quota_disabled"}
        return
    if scenario == "timeout":
        repair = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.generated_document_id == document.id, ProviderAttempt.repair_of_attempt_id.is_not(None)))
        assert result.status is GeneratedDocumentStatus.failed
        assert repair.settled_units == 0
        return
    assert result.status is GeneratedDocumentStatus.ready
    assert result.model_used == "backup-model"
    assert sum(c["config"].id == backup.id for c in calls) == 2
    attempts = list(db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.generated_document_id == document.id)))
    backup_attempt = next(a for a in attempts if a.backup_of_attempt_id is not None)
    repair = next(a for a in attempts if a.repair_of_attempt_id is not None)
    assert repair.repair_of_attempt_id == backup_attempt.id
    assert (backup_attempt.settled_units, repair.settled_units) == (7, 9)
    assert repair.provider_model == backup_attempt.provider_model == "backup-model"
    if structured:
        from app.services.quota_lifecycle import _validated_main_checkpoint
        assert _validated_main_checkpoint(db_session, repair, utcnow())[0]
        assert not _validated_main_checkpoint(db_session, backup_attempt, utcnow())[0]
    assert templates.generated_document_llm_request_payload(db_session, document=result) == calls[-1]["request_body"]
    usage = admin_usage_overview(
        db_session, team_id=team.id, monitor_reference=str(document.id),
    )
    generation_rows = [row for row in usage["usage_recent_operational_rows"] if row.stage == "Generation"]
    assert len(generation_rows) == 1
    assert generation_rows[0].outcome == "Recovered after retry"
    assert malformed not in str(result.llm_request_payload_json_encrypted)
    assert process_generated_document(db_session, document_id=document.id).status is result.status
    assert sum(c["config"].id == backup.id for c in calls) == 2
    repair_id = repair.id
    db_session.delete(result)
    db_session.flush()
    db_session.expire_all()
    retained = db_session.get(ProviderAttempt, repair_id)
    assert retained is not None and retained.generated_document_id is None


@pytest.mark.parametrize("failure_mode", [
    "provider_error", "invalid_output", "structured_invalid_output_retry", "invalid_retry_quota",
    "invalid_output_then_backup", "invalid_output_backup_invalid",
    "backup_invalid", "backup_quota", "hard_quota", "primary_success",
    "expired_timeout", "backup_preflight_context_failure",
])
def test_generation_uses_one_frozen_backup_and_accounts_for_winning_provider(
    db_session, make_team, make_user, make_llm_config, make_llm_selection,
    make_template, monkeypatch, failure_mode,
):
    team = make_team(name=f"Backup generation {uuid4()}")
    admin = make_user(email=f"backup-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email=f"backup-owner-{uuid4()}@example.com", password="password-2", team=team, team_role=TeamRole.user)
    primary = make_llm_config(
        team=team, actor=admin, has_secret=False, model_name="primary-model",
        available_models_json=["primary-model"],
    )
    make_llm_selection(config=primary, actor=admin, model_name_override="primary-model")
    backup = make_llm_config(
        team=team, actor=admin, has_secret=False, label="Backup", model_name="backup-model",
        available_models_json=["backup-model"],
    )
    if failure_mode not in {"invalid_output", "structured_invalid_output_retry"}:
        db_session.add(TeamLlmBackupSelection(
            team_id=team.id, llm_config_id=backup.id, model_name="backup-model",
        ))
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="Synthetic visit",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic consultation source.")
    template = make_template(
        scope=TemplateScope.user, owner=owner, actor=owner,
        name="Synthetic template", prompt_text="Write a synthetic note.",
        **({
            "mode": TemplateMode.structured,
            "config_json": {"profile": "emis", "sections": [
                {"section_key": "problem", "instruction": "Summarise the problem.", "section_order": 0},
            ]},
        } if failure_mode == "structured_invalid_output_retry" else {}),
    )
    db_session.commit()
    document = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id,
    )
    assert (read_backup_provider(db_session, source=document) is None) == (failure_mode in {"invalid_output", "structured_invalid_output_retry"})
    primary_attempt_before = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.correlation_id == document.id, ProviderAttempt.attempt_number == 1,
        ProviderAttempt.attempt_kind == AttemptKind.llm_generation,
    ))
    calls = []
    request_bodies = []

    def invoke(**kwargs):
        calls.append((kwargs["config"].id, kwargs["adapter_kind"], kwargs["request_body"]["model"]))
        request_bodies.append(kwargs["request_body"])
        if len(calls) == 1:
            if failure_mode == "invalid_retry_quota":
                owner.daily_token_limit = primary_attempt_before.reserved_units
                owner.monthly_token_limit = primary_attempt_before.reserved_units
                db_session.add(owner)
                db_session.commit()
                return "not-json", {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
            if failure_mode == "backup_quota":
                # Primary dispatch has already expanded its reservation. Let
                # that attempt through, then leave no quota for a second call.
                owner.daily_token_limit = primary_attempt_before.reserved_units
                owner.monthly_token_limit = primary_attempt_before.reserved_units
                db_session.add(owner)
                db_session.commit()
                raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "synthetic provider failure"))
            if failure_mode == "provider_error":
                raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "synthetic provider failure"))
            if failure_mode == "expired_timeout":
                primary_attempt_before.deadline_at = utcnow() - timedelta(seconds=1)
                db_session.commit()
                raise tag_provider_failure(AppError(504, "llm_provider_timeout", "synthetic timeout"))
            if failure_mode == "hard_quota":
                raise AppError(429, "llm_provider_rate_limited", "synthetic quota", {
                    "provider_http_status": 429, "provider_error_code": "quota_exceeded",
                    "backup_failure_class": "provider_quota_blocked",
                })
            if failure_mode == "primary_success":
                return '{"title":"Synthetic visit","content":"Supported synthetic note."}', {
                    "input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
                    "duration_ms": 10, "provider_duration_ms": 8,
                }
            if failure_mode in {"backup_invalid", "invalid_output_then_backup", "invalid_output_backup_invalid"}:
                if failure_mode != "backup_invalid":
                    return "not-json", {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
                raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "synthetic provider failure"))
            if failure_mode in {"invalid_output", "structured_invalid_output_retry", "invalid_output_then_backup", "invalid_output_backup_invalid"}:
                return "not-json", {
                    "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
                    "duration_ms": 6, "provider_duration_ms": 4,
                }
            return "not-json", {
                "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
                "duration_ms": 6, "provider_duration_ms": 4,
            }
        if failure_mode in {"invalid_output_then_backup", "invalid_output_backup_invalid"} and len(calls) == 2:
            return "not-json", {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
        if failure_mode in {"backup_invalid", "invalid_output_backup_invalid"} and kwargs["config"].id == backup.id:
            return "not-json", {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        content = (
            '{"title":"Synthetic visit","content":{"problem":"Supported synthetic note."}}'
            if failure_mode == "structured_invalid_output_retry"
            else '{"title":"Synthetic visit","content":"Supported synthetic note."}'
        )
        return content, {
            "input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
            "duration_ms": 10, "provider_duration_ms": 8,
        }

    def resolve_credential(current_config):
        if failure_mode == "backup_preflight_context_failure" and current_config.id == primary.id:
            raise AppError(401, "llm_invalid_credential", "Synthetic missing primary credential")
        return None

    monkeypatch.setattr(templates, "_resolve_generation_credential", resolve_credential)
    if failure_mode == "backup_preflight_context_failure":
        def unsupported_context():
            raise AppError(500, "llm_execution_context_unsupported", "Synthetic worker limitation")

        monkeypatch.setattr(templates, "require_supported_execution_context", unsupported_context)
    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)

    if failure_mode == "backup_preflight_context_failure":
        with pytest.raises(AppError) as unsupported:
            process_generated_document(db_session, document_id=document.id)
        assert unsupported.value.code == "llm_execution_context_unsupported"
        result = db_session.get(type(document), document.id)
        assert result is not None
    else:
        result = process_generated_document(db_session, document_id=document.id)
    first_call_count = len(calls)
    assert process_generated_document(db_session, document_id=document.id).status is result.status
    assert len(calls) == first_call_count

    if failure_mode in {"backup_quota", "backup_invalid", "backup_preflight_context_failure", "invalid_output_backup_invalid", "invalid_retry_quota"}:
        assert result.status is GeneratedDocumentStatus.failed
        assert len(calls) == (4 if failure_mode == "invalid_output_backup_invalid" else 3 if failure_mode == "backup_invalid" else 0 if failure_mode == "backup_preflight_context_failure" else 1)
        if failure_mode == "invalid_retry_quota":
            assert result.error_code in {"quota_exceeded", "quota_disabled"}
        if failure_mode == "backup_quota":
            assert calls == [(primary.id, primary.adapter_kind, "primary-model")]
        elif failure_mode == "backup_invalid":
            assert calls == [
                (primary.id, primary.adapter_kind, "primary-model"),
                (backup.id, backup.adapter_kind, "backup-model"),
                (backup.id, backup.adapter_kind, "backup-model"),
            ]
        elif failure_mode == "invalid_output_backup_invalid":
            assert [call[0] for call in calls] == [primary.id, primary.id, backup.id, backup.id]
    elif failure_mode == "primary_success":
        assert result.status is GeneratedDocumentStatus.ready
        assert result.model_used == "primary-model"
        assert calls == [(primary.id, primary.adapter_kind, "primary-model")]
    elif failure_mode in {"invalid_output", "structured_invalid_output_retry", "invalid_output_then_backup"}:
        assert result.status is GeneratedDocumentStatus.ready, result.error_code
        assert result.model_used == ("backup-model" if failure_mode == "invalid_output_then_backup" else "primary-model")
        assert [call[0] for call in calls] == (
            [primary.id, primary.id, backup.id] if failure_mode == "invalid_output_then_backup"
            else [primary.id, primary.id]
        )
        if failure_mode == "structured_invalid_output_retry":
            assert document.validated_draft_at is not None
            assert [section.section_key for section in document.sections] == ["problem"]
    else:
        assert result.status is GeneratedDocumentStatus.ready, result.error_code
        assert result.model_used == "backup-model"
        assert result.llm_adapter_kind == backup.adapter_kind.value
        assert result.llm_base_url == backup.base_url
        assert calls == [
            (primary.id, primary.adapter_kind, "primary-model"),
            (backup.id, backup.adapter_kind, "backup-model"),
        ]
    assert result.llm_config_id == primary.id
    attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_generation,
    ).order_by(ProviderAttempt.backup_of_attempt_id.nullsfirst())))
    expected_attempt_count = {
        "provider_error": 2, "invalid_output": 2, "structured_invalid_output_retry": 2,
        "invalid_output_then_backup": 3,
        "invalid_output_backup_invalid": 4, "backup_invalid": 3, "expired_timeout": 2,
        "hard_quota": 2,
    }.get(failure_mode, 1)
    assert len(attempts) == expected_attempt_count
    primary_attempt = next(item for item in attempts if item.correlation_id == document.id and item.attempt_number == 1)
    if failure_mode == "backup_preflight_context_failure":
        assert primary_attempt.status is AttemptStatus.cancelled
        assert not any(item.backup_of_attempt_id is not None for item in attempts)
        return
    assert primary_attempt.status is AttemptStatus.settled
    if failure_mode in {"provider_error", "backup_invalid", "expired_timeout", "hard_quota", "invalid_output_then_backup", "invalid_output_backup_invalid"}:
        backup_attempt = next(item for item in attempts if item.backup_of_attempt_id is not None)
        assert backup_attempt.status is AttemptStatus.settled
        expected_backup_parent = (
            next(item for item in attempts if item.backup_of_attempt_id is None and item.attempt_number == 2)
            if failure_mode.startswith("invalid_output") else primary_attempt
        )
        assert backup_attempt.backup_of_attempt_id == expected_backup_parent.id
        if failure_mode == "backup_invalid":
            assert backup_attempt.settled_units == 5
    if failure_mode.startswith("invalid_output"):
        retry_attempt = next(item for item in attempts if item.backup_of_attempt_id is None and item.attempt_number == 2)
        assert primary_attempt.settled_units == 5
        assert retry_attempt.status is AttemptStatus.settled
    events = list(db_session.scalars(select(ProviderUsageEvent).where(
        ProviderUsageEvent.generated_document_id == document.id,
    )))
    if failure_mode in {"provider_error", "backup_invalid", "expired_timeout", "hard_quota", "invalid_output_then_backup", "invalid_output_backup_invalid"}:
        assert any(item.event_type.value == "failed" and item.llm_config_id == primary.id for item in events)
        if failure_mode in {"backup_invalid", "invalid_output_backup_invalid"}:
            backup_failures = [item for item in events if item.event_type.value == "failed" and item.llm_config_id == backup.id]
            assert backup_failures and all(item.model_name == "backup-model" for item in backup_failures)
            assert any(item.total_tokens == 5 for item in backup_failures)
        else:
            assert any(item.event_type.value == "completed" and item.llm_config_id == backup.id for item in events)
    if failure_mode == "invalid_output":
        assert request_bodies[0] == request_bodies[1]


@pytest.mark.parametrize("failure_mode", ["invalid_output", "invalid_credential", "invalid_backup", "invalid_checker"])
def test_structured_checker_uses_only_one_frozen_backup(
    db_session, make_team, make_user, make_llm_config, make_llm_selection,
    make_template, monkeypatch, failure_mode,
):
    team = make_team(name=f"Checker backup {uuid4()}")
    admin = make_user(email=f"checker-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email=f"checker-owner-{uuid4()}@example.com", password="password-2", team=team, team_role=TeamRole.user)
    primary = make_llm_config(
        team=team, actor=admin, has_secret=False, model_name="primary-model",
        available_models_json=["primary-model"],
    )
    make_llm_selection(config=primary, actor=admin, model_name_override="primary-model")
    checker = make_llm_config(team=team, actor=admin, label="Checker", has_secret=False, available_models_json=["checker-model"])
    backup = make_llm_config(team=team, actor=admin, label="Backup", has_secret=False, model_name="backup-model", available_models_json=["backup-model"])
    db_session.add_all([
        TeamLlmBackupSelection(team_id=team.id, llm_config_id=backup.id, model_name="backup-model"),
        TeamHallucinationCheckSelection(
            team_id=team.id, llm_config_id=checker.id, model_name_override="checker-model",
            selected_by_user_id=admin.id,
        ),
    ])
    if failure_mode == "invalid_checker":
        checker.model_policy.enabled_models_json = []
        checker.model_policy.default_model_name = None
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="Synthetic visit",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic consultation source.")
    template = make_template(
        scope=TemplateScope.user, owner=owner, actor=owner, name="Structured synthetic template",
        prompt_text="Use only synthetic evidence.", mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [{
            "section_key": "problem", "instruction": "Summarise the problem.", "section_order": 0,
        }]},
    )
    db_session.commit()
    document = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id,
    )
    # Simulate a post-acceptance admin edit without invoking the mutation
    # guard: execution must consume the checker model already frozen on this
    # document, even when the live role and allowance now disagree.
    checker_selection = db_session.scalar(select(TeamHallucinationCheckSelection).where(
        TeamHallucinationCheckSelection.team_id == team.id,
    ))
    checker_selection.llm_config_id = backup.id
    checker_selection.model_name_override = "backup-model"
    checker.model_policy.enabled_models_json = []
    checker.model_policy.default_model_name = None
    db_session.commit()
    calls = []
    checker_invalid = '{"status":"unchanged","status":"corrected","edits":[]}'
    corrected = '{"status":"corrected","edits":[{"section_key":"problem","original":"Supported original note.","replacement":"Supported corrected note."}]}'

    def invoke(**kwargs):
        config = kwargs["config"]
        model = kwargs["request_body"]["model"]
        calls.append((config.id, model))
        if config.id == primary.id:
            return '{"title":"Synthetic visit","content":{"problem":"Supported original note."}}', {
                "input_tokens": 4, "output_tokens": 5, "total_tokens": 9,
            }
        if config.id == checker.id:
            return checker_invalid, {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}
        return (checker_invalid if failure_mode == "invalid_backup" else corrected), {
            "input_tokens": 3, "output_tokens": 3, "total_tokens": 6,
        }

    def credential(config):
        if failure_mode == "invalid_credential" and config.id == checker.id:
            raise AppError(401, "llm_invalid_credential", "Synthetic provider credential rejection")
        return None

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    monkeypatch.setattr(templates, "_resolve_generation_credential", credential)
    result = process_generated_document(db_session, document_id=document.id)
    assert result.status is GeneratedDocumentStatus.ready, result.error_code
    if failure_mode == "invalid_checker":
        assert result.hallucination_check_status.value == "skipped_config_invalid"
        assert calls == [(primary.id, "primary-model")]
        assert not db_session.scalars(select(ProviderAttempt).where(
            ProviderAttempt.generated_document_id == document.id,
            ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
        )).all()
        return
    checker_attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
    )))
    assert len(checker_attempts) == 2
    parent = next(item for item in checker_attempts if item.backup_of_attempt_id is None)
    child = next(item for item in checker_attempts if item.backup_of_attempt_id == parent.id)
    assert child.provider_model == "backup-model"
    if failure_mode == "invalid_backup":
        assert child.outcome is AttemptOutcome.failed
        assert len(calls) == 3
    else:
        assert child.outcome is AttemptOutcome.succeeded
        assert len(calls) == (2 if failure_mode == "invalid_credential" else 3)
