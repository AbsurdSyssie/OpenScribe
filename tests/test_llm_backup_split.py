"""Focused contracts for cross-adapter split backup request rebuilding."""

from uuid import uuid4
import json
import pytest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    AttemptStatus, ConsultationSplitBatch, ConsultationSplitBatchTopic,
    ConsultationSplitExecution, ConsultationSplitExecutionKind, ProviderAttempt,
    ProviderUsageEvent, TeamHallucinationCheckSelection, TeamLlmBackupSelection,
    TeamRole,
)
from app.models import LlmAdapterKind
from app.services.llm_adapters.runtime import generation_request_snapshot
from app.services.llm_backup_policy import remap_generation_request, tag_provider_failure
from tests.test_consultation_split_runtime import _queued_work, _provider_success
from tests.test_consultation_split_generation_runtime import _queue as _queue_generation, _response
from app.services.consultation_split_runtime import process_consultation_split_analysis_execution
from app.services.consultation_split_generation_runtime import (
    _finalize as _finalize_split_generation,
    process_consultation_split_generation_execution,
)
from app.services.consultation_split_verification_runtime import process_consultation_split_verification_execution


def _backup_selection(db, owner, make_llm_config, model="backup-model"):
    config = make_llm_config(team=owner.team, actor=owner, has_secret=False,
        label="Synthetic backup", model_name=model, available_models_json=[model])
    db.add(TeamLlmBackupSelection(team_id=owner.team_id, llm_config_id=config.id, model_name=model))
    db.flush()
    return config


def _assert_two_settled_attempts(db, execution_id, provider_calls, *, expected_calls=2):
    attempts = list(db.scalars(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == execution_id).order_by(
            ProviderAttempt.backup_of_attempt_id.nullsfirst())))
    assert len(attempts) == 2
    assert attempts[0].status in {AttemptStatus.settled, AttemptStatus.cancelled}
    assert attempts[1].status is AttemptStatus.settled
    assert attempts[1].backup_of_attempt_id == attempts[0].id
    assert attempts[1].correlation_id == attempts[0].id
    execution = db.get(ConsultationSplitExecution, execution_id)
    assert execution.attempt_no == 1
    assert len(provider_calls) == expected_calls
    return attempts


def test_split_analysis_provider_failure_uses_one_frozen_backup(
    db_session, make_user, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-backup-{uuid4()}@example.com", team_role=TeamRole.user)
    _backup_selection(db_session, owner, make_llm_config)
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential",
        lambda config: "synthetic-token" if config.auth_mode.value == "bearer" else None)
    monkeypatch.setattr("app.services.consultation_split_runtime.resolve_generation_credential", lambda _config: None)
    monkeypatch.setattr("app.services.consultation_split_runtime.require_supported_execution_context", lambda: None)
    calls = []

    def invoke(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic provider failure"))
        return _provider_success(json.dumps({"topics": []}))

    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm", invoke)
    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "not_required"
    attempts = _assert_two_settled_attempts(db_session, execution_id, calls)
    assert attempts[0].reported_total_tokens is None  # provider raised before usage was returned
    assert attempts[1].reported_total_tokens == 18
    assert len(db_session.scalars(select(ProviderUsageEvent).where(
        ProviderUsageEvent.consultation_split_execution_id == execution_id)).all()) == 2
    db_session.rollback()
    process_consultation_split_analysis_execution(db_session, execution_id=execution_id)
    assert len(calls) == 2


def test_split_analysis_provider_specific_credential_rejection_preflights_backup(
    db_session, make_user, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"split-backup-analysis-credential-{uuid4()}@example.com", team_role=TeamRole.user)
    backup = _backup_selection(db_session, owner, make_llm_config)
    _transcript_id, execution_id, _preference_id = _queued_work(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)

    def resolve(config):
        if config.id == backup.id:
            return "synthetic-backup-token"
        raise AppError(401, "llm_invalid_credential", "Synthetic provider credential rejection")

    monkeypatch.setattr("app.services.consultation_split_pre_submit.resolve_generation_credential", resolve)
    monkeypatch.setattr("app.services.consultation_split_runtime.resolve_generation_credential", resolve)
    monkeypatch.setattr("app.services.consultation_split_runtime.require_supported_execution_context", lambda: None)
    calls = []
    monkeypatch.setattr("app.services.consultation_split_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or _provider_success(json.dumps({"topics": []}))))

    result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)

    assert result.outcome == "not_required"
    attempts = _assert_two_settled_attempts(db_session, execution_id, calls, expected_calls=1)
    assert attempts[0].status is AttemptStatus.cancelled
    assert attempts[1].reported_total_tokens == 18


def test_split_generation_invalid_output_uses_backup_and_keeps_business_attempt_number(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, caplog,
):
    owner = make_user(email=f"split-backup-generation-{uuid4()}@example.com", team_role=TeamRole.user)
    _backup_selection(db_session, owner, make_llm_config)
    _transcript, execution_id = _queue_generation(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    response = _response(db_session, execution_id)
    db_session.rollback()
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential",
        lambda config: "synthetic-primary-token" if config.auth_mode.value == "bearer" else None)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.require_supported_execution_context", lambda: None)
    calls = []
    duplicate_results = []

    def invoke(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "not-json", {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        with Session(bind=db_session.get_bind(), join_transaction_mode="create_savepoint") as duplicate_db:
            duplicate_results.append(process_consultation_split_generation_execution(
                duplicate_db, execution_id=execution_id).outcome)
            duplicate_results.append(_finalize_split_generation(
                duplicate_db, execution_id))
            duplicate_execution = duplicate_db.get(ConsultationSplitExecution, execution_id)
            duplicate_child = duplicate_db.scalar(select(ProviderAttempt).where(
                ProviderAttempt.consultation_split_execution_id == execution_id,
                ProviderAttempt.backup_of_attempt_id.is_not(None),
            ))
            assert duplicate_execution.status.value == "processing"
            assert duplicate_execution.recoverable_response_encrypted is None
            assert duplicate_child.status is AttemptStatus.submitted
        return response, {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11}

    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm", invoke)
    result = process_consultation_split_generation_execution(db_session, execution_id=execution_id)

    diagnostic_attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == execution_id).order_by(
            ProviderAttempt.backup_of_attempt_id.nullsfirst())))
    assert result.outcome == "ready", {
        "error_code": result.error_code,
        "provider_calls": len(calls),
        "attempts": [(attempt.status.value, attempt.provider_model,
                       str(attempt.backup_of_attempt_id) if attempt.backup_of_attempt_id else None)
                      for attempt in diagnostic_attempts],
        "finalization_errors": [
            (getattr(record, "error_type", None), getattr(record, "error_code", None))
            for record in caplog.records
            if getattr(record, "event", None) == "consultation_split_finalization_failed"
        ],
    }
    assert any(
        getattr(record, "event", None) == "consultation_split_generation_parse_failed"
        and getattr(record, "phase", None) == "parse"
        and getattr(record, "error_code", None) == "consultation_split_generation_invalid_output"
        for record in caplog.records
    )
    attempts = _assert_two_settled_attempts(db_session, execution_id, calls)
    assert attempts[0].reported_total_tokens == 5
    assert attempts[1].reported_total_tokens == 11
    assert duplicate_results[0] == "in_flight"
    assert duplicate_results[1].outcome == "in_flight"
    db_session.rollback()
    process_consultation_split_generation_execution(db_session, execution_id=execution_id)
    assert len(calls) == 2


def test_split_generation_provider_specific_credential_rejection_preflights_backup(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"split-backup-generation-credential-{uuid4()}@example.com", team_role=TeamRole.user)
    backup = _backup_selection(db_session, owner, make_llm_config)
    _transcript, execution_id = _queue_generation(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    response = _response(db_session, execution_id)
    db_session.rollback()

    def resolve(config):
        if config.id == backup.id:
            return "synthetic-backup-token"
        raise AppError(401, "llm_invalid_credential", "Synthetic provider credential rejection")

    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", resolve)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.require_supported_execution_context", lambda: None)
    calls = []
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or (response, {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11})))

    result = process_consultation_split_generation_execution(db_session, execution_id=execution_id)

    assert result.outcome == "ready"
    attempts = _assert_two_settled_attempts(db_session, execution_id, calls, expected_calls=1)
    assert attempts[0].status is AttemptStatus.cancelled
    assert attempts[1].reported_total_tokens == 11


@pytest.mark.parametrize("provider_outcome", ["response", "error"])
def test_split_generation_pre_submit_backup_discards_late_child_outcome(
    db_session, make_user, make_llm_config, make_llm_selection, make_template,
    monkeypatch, provider_outcome,
):
    owner = make_user(email=f"split-backup-late-{provider_outcome}-{uuid4()}@example.com",
        team_role=TeamRole.user)
    backup = _backup_selection(db_session, owner, make_llm_config)
    _transcript, execution_id = _queue_generation(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    response = _response(db_session, execution_id)
    db_session.rollback()

    def resolve(config):
        if config.id == backup.id:
            return "synthetic-backup-token"
        raise AppError(401, "llm_invalid_credential", "Synthetic provider credential rejection")

    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", resolve)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.require_supported_execution_context", lambda: None)
    backup_started = False
    original_binding_check = __import__(
        "app.services.consultation_split_generation_runtime",
        fromlist=["_has_valid_materialization_binding"],
    )._has_valid_materialization_binding

    def binding_check(db, work):
        return False if backup_started else original_binding_check(db, work)

    monkeypatch.setattr(
        "app.services.consultation_split_generation_runtime._has_valid_materialization_binding",
        binding_check,
    )

    def invoke(**_kwargs):
        nonlocal backup_started
        backup_started = True
        if provider_outcome == "error":
            raise tag_provider_failure(AppError(502, "llm_provider_unreachable", "Synthetic backup failure"))
        return response, {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11}

    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm", invoke)
    result = process_consultation_split_generation_execution(db_session, execution_id=execution_id)

    assert result.outcome == "in_flight"
    attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == execution_id).order_by(
            ProviderAttempt.backup_of_attempt_id.nullsfirst())))
    execution = db_session.get(ConsultationSplitExecution, execution_id)
    assert len(attempts) == 2
    assert attempts[0].status is AttemptStatus.cancelled
    assert attempts[1].status is AttemptStatus.submitted
    assert attempts[1].backup_of_attempt_id == attempts[0].id
    assert execution.status.value == "processing"
    assert execution.recoverable_response_encrypted is None


def test_split_verification_provider_failure_uses_backup_once(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"split-backup-verification-{uuid4()}@example.com", team_role=TeamRole.user)
    _backup_selection(db_session, owner, make_llm_config)
    checker = make_llm_config(team=owner.team, actor=owner, has_secret=False,
        label="Synthetic checker", model_name="checker-model", available_models_json=["checker-model"])
    db_session.add(TeamHallucinationCheckSelection(
        team_id=owner.team_id, llm_config_id=checker.id, selected_by_user_id=owner.id))
    db_session.flush()
    _transcript, generation_id = _queue_generation(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
        mixed=True, structured_only=True)
    generation_response = _response(db_session, generation_id)
    db_session.rollback()
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", lambda _config: None)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.require_supported_execution_context", lambda: None)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (generation_response, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}))
    assert process_consultation_split_generation_execution(db_session, execution_id=generation_id).outcome == "ready"
    generation = db_session.get(ConsultationSplitExecution, generation_id)
    batch = db_session.get(ConsultationSplitBatch, generation.batch_id)
    verification_id = db_session.scalar(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.verification))
    assert verification_id is not None
    topics = db_session.scalars(select(ConsultationSplitBatchTopic).where(
        ConsultationSplitBatchTopic.batch_id == batch.id,
        ConsultationSplitBatchTopic.disposition == "separate_note")).all()
    response = json.dumps({str(topic.topic_uuid): {"status": "unchanged"} for topic in topics})
    db_session.rollback()
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.resolve_generation_credential", lambda _config: None)
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.require_supported_execution_context", lambda: None)
    calls = []
    duplicate_deliveries = []

    def invoke(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "not-json", {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        with Session(bind=db_session.get_bind(), join_transaction_mode="create_savepoint") as duplicate_db:
            duplicate_deliveries.append(process_consultation_split_verification_execution(
                duplicate_db, execution_id=verification_id))
            duplicate_execution = duplicate_db.get(ConsultationSplitExecution, verification_id)
            duplicate_child = duplicate_db.scalar(select(ProviderAttempt).where(
                ProviderAttempt.consultation_split_execution_id == verification_id,
                ProviderAttempt.backup_of_attempt_id.is_not(None),
            ))
            assert duplicate_execution.status.value == "processing"
            assert duplicate_execution.recoverable_response_encrypted is None
            assert duplicate_child.status is AttemptStatus.submitted
        return response, {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}

    monkeypatch.setattr("app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm", invoke)
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)

    attempts = _assert_two_settled_attempts(db_session, verification_id, calls)
    assert attempts[0].reported_total_tokens == 5
    assert attempts[1].reported_total_tokens == 6
    assert duplicate_deliveries == [None]


def test_split_verification_provider_specific_credential_rejection_preflights_backup(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, caplog,
):
    owner = make_user(email=f"split-backup-verification-credential-{uuid4()}@example.com", team_role=TeamRole.user)
    backup = _backup_selection(db_session, owner, make_llm_config)
    checker = make_llm_config(team=owner.team, actor=owner,
        label="Synthetic checker", model_name="checker-model", available_models_json=["checker-model"])
    db_session.add(TeamHallucinationCheckSelection(
        team_id=owner.team_id, llm_config_id=checker.id, selected_by_user_id=owner.id))
    db_session.flush()
    _transcript, generation_id = _queue_generation(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
        mixed=True, structured_only=True)
    generation_response = _response(db_session, generation_id)
    db_session.rollback()
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", lambda _config: None)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.require_supported_execution_context", lambda: None)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (generation_response, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}))
    generation_result = process_consultation_split_generation_execution(db_session, execution_id=generation_id)
    assert generation_result.outcome == "ready", [
        (getattr(record, "error_type", None), getattr(record, "error_code", None))
        for record in caplog.records
        if getattr(record, "event", None) == "consultation_split_finalization_failed"
    ]
    generation = db_session.get(ConsultationSplitExecution, generation_id)
    batch = db_session.get(ConsultationSplitBatch, generation.batch_id)
    verification_id = db_session.scalar(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.verification))
    assert verification_id is not None
    topics = db_session.scalars(select(ConsultationSplitBatchTopic).where(
        ConsultationSplitBatchTopic.batch_id == batch.id,
        ConsultationSplitBatchTopic.disposition == "separate_note")).all()
    response = json.dumps({str(topic.topic_uuid): {"status": "unchanged"} for topic in topics})
    db_session.rollback()

    def resolve(config):
        if config.id == backup.id:
            return "synthetic-backup-token"
        raise AppError(401, "llm_invalid_credential", "Synthetic provider credential rejection")

    monkeypatch.setattr("app.services.consultation_split_verification_runtime.resolve_generation_credential", resolve)
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.require_supported_execution_context", lambda: None)
    calls = []
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or (response, {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6})))

    process_consultation_split_verification_execution(db_session, execution_id=verification_id)

    attempts = _assert_two_settled_attempts(db_session, verification_id, calls, expected_calls=1)
    assert attempts[0].status is AttemptStatus.cancelled
    assert attempts[1].reported_total_tokens == 6


def test_openai_split_snapshot_keeps_exact_schema_and_frozen_messages_for_gemini():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["synthetic-topic"],
        "properties": {"synthetic-topic": {"type": "string"}},
    }
    request = generation_request_snapshot(
        adapter_kind=LlmAdapterKind.openai_chat,
        model="primary-model",
        user_id=uuid4(),
        system_message="Return only the required JSON.",
        user_message="Synthetic redacted split source.",
        output_token_cap=1024,
    )

    rebuilt = remap_generation_request(
        request,
        source_adapter_kind=LlmAdapterKind.openai_chat,
        target_adapter_kind=LlmAdapterKind.gemini_enterprise,
        target_model="backup-model",
        user_id=uuid4(),
        output_token_cap=2048,
        response_json_schema=schema,
    )

    assert rebuilt["model"] == "backup-model"
    assert rebuilt["contents"][0]["parts"][0]["text"] == "Synthetic redacted split source."
    assert rebuilt["config"]["system_instruction"] == "Return only the required JSON."
    assert rebuilt["config"]["response_json_schema"] == schema
    # Gemini uses its established fixed provider ceiling, which is why quota
    # reservation must inspect the rebuilt request rather than the primary cap.
    assert rebuilt["config"]["max_output_tokens"] == 30_000


def test_gemini_split_snapshot_keeps_frozen_messages_for_openai():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["synthetic-topic"],
        "properties": {"synthetic-topic": {"type": "string"}},
    }
    request = generation_request_snapshot(
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        model="primary-model",
        user_id=uuid4(),
        system_message="Return only the required JSON.",
        user_message="Synthetic redacted split source.",
        output_token_cap=1024,
        response_json_schema=schema,
    )

    rebuilt = remap_generation_request(
        request,
        source_adapter_kind=LlmAdapterKind.gemini_enterprise,
        target_adapter_kind=LlmAdapterKind.openai_chat,
        target_model="backup-model",
        user_id=uuid4(),
        output_token_cap=2048,
        response_json_schema=schema,
    )

    assert rebuilt["model"] == "backup-model"
    assert rebuilt["messages"] == [
        {"role": "system", "content": "Return only the required JSON."},
        {"role": "user", "content": "Synthetic redacted split source."},
    ]
    assert rebuilt["max_completion_tokens"] == 2048
