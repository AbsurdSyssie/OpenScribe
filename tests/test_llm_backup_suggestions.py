from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptKind, AttemptOutcome, AttemptStatus, LlmAuthMode, ProviderAttempt, TeamLlmBackupSelection,
    TemplateScope, TemplateSuggestionStatus, Transcript, TranscriptIngestionMode,
    TranscriptStatus, UserAppPreference, transcript_expiry, utcnow,
)
from app.services import template_suggestions
from app.services.content_crypto import decrypt_json_for_owner
from app.services.llm_backup_policy import tag_provider_failure
from app.services.template_suggestions import process_template_suggestion, queue_template_suggestion
from app.services.transcripts import set_transcript_draft_text


def test_template_suggestion_uses_one_frozen_backup_request(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    user = make_user(email=f"backup-suggestion-{uuid4()}@example.com")
    user.app_preferences = UserAppPreference(user_id=user.id, preferences_json={"template_suggestions_enabled": True})
    admin = make_user(email=f"backup-suggestion-admin-{uuid4()}@example.com", is_system_admin=True)
    primary = make_llm_config(team=user.team, actor=admin)
    make_llm_selection(config=primary, actor=admin)
    transcript = Transcript(
        owner_user_id=user.id, team_id=user.team_id, title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.live_chunked, status=TranscriptStatus.recording,
        retention_days_applied=30, retention_expires_at=transcript_expiry(30),
    )
    db_session.add(transcript); db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="x" * 1300)
    candidates = [
        make_template(scope=TemplateScope.user, owner=user, actor=user, name=f"Synthetic {index}")
        for index in range(2)
    ]
    db_session.commit()
    backup = make_llm_config(
        team=user.team, actor=admin, has_secret=False, label="Suggestion backup",
        model_name="backup-model", available_models_json=["backup-model"],
    )
    db_session.add(TeamLlmBackupSelection(
        team_id=user.team_id, llm_config_id=backup.id, model_name="backup-model",
    ))
    db_session.commit()
    monkeypatch.setattr(template_suggestions, "try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, user, transcript_id=transcript.id)
    primary_config_id, primary_model = job.llm_config_id, job.model_used
    monkeypatch.setattr(
        template_suggestions, "redact_transient_text",
        lambda *_args, **_kwargs: {"redacted_text": "synthetic redacted source", "phi_index": []},
    )
    monkeypatch.setattr(
        template_suggestions, "_apply_manual_pii_redaction",
        lambda _db, **kwargs: (kwargs["transcript_text"], "", []),
    )
    monkeypatch.setattr(template_suggestions, "_resolve_generation_credential", lambda *_: None)
    calls = []

    def invoke(**kwargs):
        calls.append((kwargs["config"].id, kwargs["request_body"]["model"]))
        if len(calls) == 1:
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "synthetic provider failure"))
        return (
            '{"template_id":"%s","confidence":"high","reason":"Synthetic match"}' % candidates[0].id,
            {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
        )

    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", invoke)

    result = process_template_suggestion(db_session, job_id=job.id)

    assert result.status is TemplateSuggestionStatus.completed
    assert result.model_used == "backup-model"
    assert calls == [(primary_config_id, primary_model), (backup.id, "backup-model")]
    primary = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.correlation_id == job.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_template_suggestion,
        ProviderAttempt.attempt_number == 1,
    ))
    assert primary is not None
    child = db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.backup_of_attempt_id == primary.id))
    assert primary.status is AttemptStatus.settled
    assert child.status is AttemptStatus.settled
    assert child.backup_of_attempt_id == primary.id
    payload = decrypt_json_for_owner(
        db_session, owner_user_id=user.id, table=job.__tablename__,
        field="llm_request_payload_json_encrypted", record_id=job.id,
        stored_value=job.llm_request_payload_json_encrypted,
    )
    assert payload["model"] == "backup-model"
    assert "synthetic redacted source" in payload["messages"][1]["content"]


@pytest.fixture
def backup_suggestion_job(db_session, make_user, make_template, make_llm_config,
                          make_llm_selection, monkeypatch):
    suffix = uuid4()
    owner = make_user(email=f"suggestion-boundary-owner-{suffix}@example.com")
    owner.app_preferences = UserAppPreference(user_id=owner.id,
        preferences_json={"template_suggestions_enabled": True})
    admin = make_user(email=f"suggestion-boundary-admin-{suffix}@example.com", is_system_admin=True)
    primary = make_llm_config(team=owner.team, actor=admin)
    make_llm_selection(config=primary, actor=admin)
    backup = make_llm_config(team=owner.team, actor=admin, has_secret=False,
        label="Suggestion boundary backup", model_name="backup-model", available_models_json=["backup-model"])
    db_session.add(TeamLlmBackupSelection(team_id=owner.team_id,
        llm_config_id=backup.id, model_name="backup-model"))
    root = Transcript(owner_user_id=owner.id, team_id=owner.team_id,
        title="Synthetic suggestion source", ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording, retention_days_applied=30,
        retention_expires_at=transcript_expiry(30))
    db_session.add(root)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=root, plaintext="x" * 1300)
    candidates = [make_template(scope=TemplateScope.user, owner=owner, actor=owner,
        name=f"Synthetic boundary candidate {index}") for index in range(2)]
    db_session.commit()
    monkeypatch.setattr(template_suggestions, "try_publish_task_dispatch_safely", lambda *_: None)
    job = queue_template_suggestion(db_session, owner, transcript_id=root.id)
    redactions = []

    def redact(*_args, **_kwargs):
        redactions.append(True)
        return {"redacted_text": "synthetic redacted source", "phi_index": []}

    monkeypatch.setattr(template_suggestions, "redact_transient_text", redact)
    monkeypatch.setattr(template_suggestions, "_apply_manual_pii_redaction",
        lambda _db, **kwargs: (kwargs["transcript_text"], "", []))
    monkeypatch.setattr(template_suggestions, "_resolve_generation_credential", lambda *_: None)
    return SimpleNamespace(job=job, owner=owner, primary=primary, backup=backup,
        root=root, redactions=redactions,
        response='{"template_id":"%s","confidence":"high","reason":"Synthetic match"}' % candidates[0].id)


def _suggestion_attempts(db, job):
    primary = db.scalar(select(ProviderAttempt).where(ProviderAttempt.correlation_id == job.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_template_suggestion,
        ProviderAttempt.attempt_number == 1))
    child = db.scalar(select(ProviderAttempt).where(ProviderAttempt.backup_of_attempt_id == primary.id))
    return primary, child


@pytest.mark.parametrize("credential_failure", ["llm_invalid_credential", "missing-reference", "vault_read_failed", "backup-invalid"])
def test_suggestion_preclaim_fallback_cancels_primary_without_charging_it(
    db_session, backup_suggestion_job, monkeypatch, credential_failure,
):
    state = backup_suggestion_job
    calls = []
    if credential_failure == "missing-reference":
        state.primary.auth_mode = LlmAuthMode.bearer
        state.primary.vault_secret_ref = ""
        db_session.commit()

    def credential(config):
        if config.id == state.primary.id:
            code = {
                "vault_read_failed": "vault_read_failed",
                "missing-reference": "business_rule_violation",
            }.get(credential_failure, "llm_invalid_credential")
            raise AppError(502, code, "Synthetic credential failure")
        if credential_failure == "backup-invalid":
            raise AppError(401, "llm_invalid_credential", "Synthetic backup credential failure")
        return None

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        return state.response, {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}

    monkeypatch.setattr(template_suggestions, "_resolve_generation_credential", credential)
    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", invoke)
    result = process_template_suggestion(db_session, job_id=state.job.id)
    primary, child = _suggestion_attempts(db_session, state.job)

    assert primary.status is AttemptStatus.cancelled
    assert primary.submitted_at is None
    assert primary.settled_units is None
    assert state.redactions == [True]
    if credential_failure in {"llm_invalid_credential", "missing-reference"}:
        assert result.status is TemplateSuggestionStatus.completed
        assert result.model_used == "backup-model"
        assert calls == [state.backup.id]
        assert child.status is AttemptStatus.settled
        assert child.outcome is AttemptOutcome.succeeded
        assert child.settled_units == 7
    else:
        assert result.status is TemplateSuggestionStatus.failed
        assert child is None
        assert calls == []
    process_template_suggestion(db_session, job_id=state.job.id)
    assert state.redactions == [True]
    assert len(calls) == (1 if credential_failure in {"llm_invalid_credential", "missing-reference"} else 0)


@pytest.mark.parametrize("failure_mode", ["invalid-primary", "invalid-backup", "hard-quota", "unknown-quota", "backup-quota"])
def test_suggestion_output_and_quota_boundaries(
    db_session, backup_suggestion_job, monkeypatch, failure_mode,
):
    state = backup_suggestion_job
    calls = []
    requests = []
    usage = {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        requests.append(kwargs["request_body"]["messages"])
        if len(calls) == 1:
            if failure_mode in {"hard-quota", "unknown-quota"}:
                details = {"provider_http_status": 429}
                if failure_mode == "hard-quota":
                    details["backup_failure_class"] = "provider_quota_blocked"
                raise AppError(429, "llm_provider_rate_limited", "Synthetic provider quota", details)
            if failure_mode == "backup-quota":
                state.owner.daily_token_limit = state.owner.monthly_token_limit = 1
                db_session.flush()
                raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))
            return "invalid-json", usage
        return ("invalid-backup-json" if failure_mode == "invalid-backup" else state.response), usage

    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", invoke)
    result = process_template_suggestion(db_session, job_id=state.job.id)
    primary, child = _suggestion_attempts(db_session, state.job)

    assert state.redactions == [True]
    assert primary.status is AttemptStatus.settled
    if failure_mode in {"hard-quota", "unknown-quota", "backup-quota"}:
        assert result.status is TemplateSuggestionStatus.failed
        assert calls == [state.primary.id]
        assert child is None
        assert primary.outcome is AttemptOutcome.unknown
        assert primary.settled_units == primary.reserved_units
        if failure_mode == "backup-quota":
            assert result.error_code == "quota_exceeded"
    else:
        assert calls == [state.primary.id, state.backup.id]
        assert requests[0] == requests[1]
        assert primary.outcome is AttemptOutcome.failed
        assert primary.settled_units == 7
        assert child.status is AttemptStatus.settled
        assert child.settled_units == 7
        assert child.outcome is (AttemptOutcome.failed if failure_mode == "invalid-backup" else AttemptOutcome.succeeded)
        assert result.status is (TemplateSuggestionStatus.failed if failure_mode == "invalid-backup" else TemplateSuggestionStatus.completed)


@pytest.mark.parametrize("late_boundary", ["root-expired", "child-deadline"])
def test_suggestion_late_child_cannot_persist_result_or_restart_delivery(
    db_session, backup_suggestion_job, monkeypatch, late_boundary,
):
    state = backup_suggestion_job
    calls = []
    clock = [utcnow()]
    monkeypatch.setattr(template_suggestions, "utcnow", lambda: clock[0])

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        if len(calls) == 1:
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))
        if late_boundary == "root-expired":
            state.root.retention_expires_at = utcnow() - timedelta(seconds=1)
            db_session.flush()
        else:
            _primary, child = _suggestion_attempts(db_session, state.job)
            clock[0] = child.deadline_at + timedelta(seconds=1)
        return state.response, {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}

    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", invoke)
    result = process_template_suggestion(db_session, job_id=state.job.id)

    assert result.status is not TemplateSuggestionStatus.completed
    assert result.suggestion_result_encrypted is None
    assert calls == [state.primary.id, state.backup.id]
    process_template_suggestion(db_session, job_id=state.job.id)
    assert calls == [state.primary.id, state.backup.id]
    assert state.redactions == [True]


def test_suggestion_request_encryption_failure_cancels_unsubmitted_primary(
    db_session, backup_suggestion_job, monkeypatch,
):
    state = backup_suggestion_job
    calls = []

    def encrypt(*_args, **_kwargs):
        raise AppError(500, "content_crypto_missing_key", "Synthetic content key failure")

    monkeypatch.setattr(template_suggestions, "encrypt_json_for_owner", encrypt)
    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", lambda **kwargs: calls.append(True))
    result = process_template_suggestion(db_session, job_id=state.job.id)
    primary, child = _suggestion_attempts(db_session, state.job)

    assert result.status is TemplateSuggestionStatus.failed
    assert primary.status is AttemptStatus.cancelled
    assert primary.submitted_at is None
    assert primary.settled_units is None
    assert child is None
    assert calls == []


def test_suggestion_backup_request_encryption_failure_never_submits_child(
    db_session, backup_suggestion_job, monkeypatch,
):
    state = backup_suggestion_job
    calls = []
    encrypt = template_suggestions.encrypt_json_for_owner

    def encrypt_request(*args, **kwargs):
        if kwargs["plaintext"].get("model") == "backup-model":
            raise AppError(500, "content_crypto_missing_key", "Synthetic content key failure")
        return encrypt(*args, **kwargs)

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))

    monkeypatch.setattr(template_suggestions, "encrypt_json_for_owner", encrypt_request)
    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", invoke)
    result = process_template_suggestion(db_session, job_id=state.job.id)
    primary, child = _suggestion_attempts(db_session, state.job)

    assert result.status is TemplateSuggestionStatus.failed
    assert primary.status is AttemptStatus.settled
    assert primary.outcome is AttemptOutcome.unknown
    assert child is None
    assert calls == [state.primary.id]


def test_suggestion_backup_result_encryption_failure_fails_without_third_call(
    db_session, backup_suggestion_job, monkeypatch,
):
    state = backup_suggestion_job
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs["config"].id)
        if len(calls) == 1:
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))
        return state.response, {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}

    def fail_result(*_args, **_kwargs):
        raise AppError(500, "content_crypto_missing_key", "Synthetic content key failure")

    monkeypatch.setattr(template_suggestions, "_invoke_frozen_llm", invoke)
    monkeypatch.setattr(template_suggestions, "_set_result", fail_result)
    result = process_template_suggestion(db_session, job_id=state.job.id)
    primary, child = _suggestion_attempts(db_session, state.job)

    assert result.status is TemplateSuggestionStatus.failed
    assert primary.status is AttemptStatus.settled
    assert child.status is AttemptStatus.settled
    assert child.outcome is AttemptOutcome.failed
    assert child.settled_units == 7
    assert result.suggestion_result_encrypted is None
    process_template_suggestion(db_session, job_id=state.job.id)
    assert calls == [state.primary.id, state.backup.id]
