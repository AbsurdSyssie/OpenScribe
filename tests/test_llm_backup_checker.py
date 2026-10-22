from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptKind, AttemptOutcome, AttemptStatus, GeneratedDocumentStatus,
    HallucinationCheckStatus, ProviderAttempt, TeamHallucinationCheckSelection,
    TeamLlmBackupSelection, TeamRole, TemplateMode, TemplateScope, Transcript,
    TranscriptIngestionMode, TranscriptStatus, utcnow,
)
from app.services import templates
from app.services.llm_backup_policy import tag_provider_failure
from app.services.templates import process_generated_document, queue_document_generation_from_template
from app.services.transcripts import set_transcript_draft_text


@pytest.mark.parametrize("backup_response, expected_status", [
    ('{"status":"unchanged","edits":[]}', HallucinationCheckStatus.checked_unchanged),
    ('{"status":"unchanged","status":"corrected","edits":[]}', HallucinationCheckStatus.failed_invalid_response),
])
@pytest.mark.parametrize("expire_primary_checker", [False, True])
def test_checker_backup_is_single_attempt_and_records_actual_provider(
    db_session, make_team, make_user, make_llm_config, make_llm_selection,
    make_template, monkeypatch, backup_response, expected_status, expire_primary_checker,
):
    team = make_team(name=f"Checker failover {uuid4()}")
    admin = make_user(email=f"checker-failover-admin-{uuid4()}@example.com", password="synthetic-password", is_system_admin=True)
    owner = make_user(email=f"checker-failover-owner-{uuid4()}@example.com", password="synthetic-password", team=team, team_role=TeamRole.user)
    primary = make_llm_config(team=team, actor=admin, has_secret=False, available_models_json=["primary-model"])
    make_llm_selection(config=primary, actor=admin, model_name_override="primary-model")
    checker = make_llm_config(team=team, actor=admin, label="Checker", has_secret=False,
        available_models_json=["checker-model"])
    backup = make_llm_config(team=team, actor=admin, label="Checker backup", has_secret=False,
        model_name="backup-model", available_models_json=["backup-model"])
    db_session.add_all([
        TeamLlmBackupSelection(team_id=team.id, llm_config_id=backup.id, model_name="backup-model"),
        TeamHallucinationCheckSelection(
            team_id=team.id, llm_config_id=checker.id, model_name_override="checker-model",
            selected_by_user_id=admin.id,
        ),
    ])
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="Synthetic visit",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic consultation source.")
    template = make_template(
        scope=TemplateScope.user, owner=owner, actor=owner, name="Synthetic checker template",
        prompt_text="Use only synthetic evidence.", mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [{
            "section_key": "problem", "instruction": "Summarise the problem.", "section_order": 0,
        }]},
    )
    db_session.commit()
    document = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id,
    )
    calls = []

    def invoke(**kwargs):
        config = kwargs["config"]
        calls.append((config.id, kwargs["request_body"]["model"]))
        if config.id == primary.id:
            return '{"title":"Synthetic visit","content":{"problem":"Supported synthetic note."}}', {
                "input_tokens": 4, "output_tokens": 5, "total_tokens": 9,
            }
        if config.id == checker.id:
            if expire_primary_checker:
                primary_attempt = db_session.scalar(select(ProviderAttempt).where(
                    ProviderAttempt.generated_document_id == document.id,
                    ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
                    ProviderAttempt.backup_of_attempt_id.is_(None),
                ))
                assert primary_attempt is not None
                # Simulate the checker call reaching its own 30-second timeout
                # while the parent generation still has its 150-second budget.
                primary_attempt.deadline_at = utcnow() - timedelta(seconds=1)
                db_session.commit()
                raise tag_provider_failure(AppError(504, "llm_provider_timeout", "Synthetic checker timeout"))
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic checker outage"))
        return backup_response, {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}

    monkeypatch.setattr(templates, "_invoke_frozen_llm", invoke)
    monkeypatch.setattr(templates, "_resolve_generation_credential", lambda _config: None)
    result = process_generated_document(db_session, document_id=document.id)
    assert result.status is GeneratedDocumentStatus.ready
    assert result.hallucination_check_status is expected_status
    assert calls == [
        (primary.id, "primary-model"),
        (checker.id, "checker-model"),
        (backup.id, "backup-model"),
    ]

    attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
    )))
    assert len(attempts) == 2
    parent = next(item for item in attempts if item.backup_of_attempt_id is None)
    child = next(item for item in attempts if item.backup_of_attempt_id == parent.id)
    assert parent.status is AttemptStatus.settled
    assert parent.outcome is AttemptOutcome.unknown
    assert child.status is AttemptStatus.settled
    assert child.provider_model == "backup-model"
    if expected_status is HallucinationCheckStatus.failed_invalid_response:
        assert child.outcome is AttemptOutcome.failed
        assert result.hallucination_check_llm_config_id == backup.id
        assert result.hallucination_check_model_name == "backup-model"
    else:
        assert child.outcome is AttemptOutcome.succeeded
        assert result.hallucination_check_llm_config_id == backup.id
        assert result.hallucination_check_model_name == "backup-model"
