"""Direct, durable-runtime tests for confirmed bundled note generation."""

import json
from datetime import timedelta
from threading import Barrier, Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import (
    AttemptOutcome,
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitBatchTopic,
    ConsultationSplitBatchStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionStatus,
    ConsultationSplitIntent,
    ConsultationSplitTopicDisposition,
    ConsultationSplitTopicOutcome,
    ConsultationSplitTopicOutcomeStatus,
    ConsultationSplitVerificationStatus,
    GeneratedDocument,
    GeneratedDocumentSection,
    PromptTemplateVersion,
    ProviderAttempt,
    ProviderUsageEvent,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    TemplateMode,
    TeamLlmSelection,
    TeamHallucinationCheckSelection,
    TeamLlmConfig,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
    UserAppPreference,
    utcnow,
)
from app.schemas.consultation_split import ConsultationSplitDraftConfirmRequest
from app.services.consultation_split_confirmation import confirm_split_draft
from app.services.consultation_split_drafts import create_split_draft, create_split_draft_topic
from app.services.consultation_split_generation_runtime import process_consultation_split_generation_execution
from app.services.consultation_split_verification_runtime import process_consultation_split_verification_execution
from app.services.consultation_split_partial import keep_available_split_notes
from app.services.consultation_split_recovery import (
    queue_automatic_split_recovery,
    retry_missing_split_notes,
)
from app.services.consultation_split_api import read_workspace_split_batch
from app.services.consultation_splits import (
    create_split_analysis,
    read_split_execution_json,
    read_split_topic_outcome_output,
)
from app.services.content_crypto import (
    decrypt_json_for_owner,
    decrypt_text_for_owner,
    encrypt_json_for_owner,
    encrypt_text_for_owner,
)
from app.services.consultation_splits import read_split_topic_title
from app.services.quota_lifecycle import (
    PROVIDER_ATTEMPT_OUTCOME_UNKNOWN,
    QUOTA_RESERVATION_EXPIRED,
    TASK_DISPATCH_FAILED,
    process_quota_lifecycle,
)
from app.services.quotas import mark_provider_attempt_submitted
from app.services.transcripts import (
    delete_expired_transcripts,
    delete_transcripts,
    set_freeform_working_note_text,
)
from app.services.dictations import update_post_consultation_dictation


def _queue(db, owner, make_llm_config, make_llm_selection, make_template, monkeypatch, *, mixed=True, included=False, structured_only=False, source_only=None):
    """Build a real confirmed batch; only the provider boundary is faked."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    db.add(UserAppPreference(user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": True}))
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id, title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript); db.flush()
    if source_only == "working_note":
        set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic working note")
        transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    elif source_only == "dictation":
        update_post_consultation_dictation(
            db,
            owner,
            transcript_id=transcript.id,
            combined_text="Synthetic saved dictation",
        )
    else:
        set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic working note")
        transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    freeform = make_template(owner=owner, actor=owner, name="Freeform", mode=TemplateMode.freeform)
    structured = make_template(
        owner=owner, actor=owner, name="Structured", mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [
            {"section_key": "problem", "instruction": "Problem", "section_order": 0},
            {"section_key": "tasks", "instruction": "Tasks", "section_order": 1},
        ]},
    )
    free_version = db.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == freeform.id))
    structured_version = db.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == structured.id))
    source_text = "" if source_only else "[PHI_1] synthetic"
    analysis = create_split_analysis(
        db, owner, transcript_id=transcript.id, source_fingerprint="d" * 64,
        source_snapshot={"source_state": {}, "sources": {
            "transcript": source_text,
            "working_note": {"mode": "freeform", "value": "Synthetic working note"} if source_only != "dictation" else {"mode": None, "value": None},
            "dictation": "Synthetic saved dictation" if source_only == "dictation" else "",
        }, "clinical_nlp_hints": [], "phi_index": []},
        candidate_template_snapshot={"templates": []}, provider_snapshot={}, proposal={},
    )
    if source_only is None:
        version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="")
        db.add(version); db.flush()
        version.text_encrypted = encrypt_text_for_owner(db, owner_user_id=owner.id, table="transcript_versions",
            field="text_encrypted", record_id=version.id, plaintext="Synthetic transcript") or ""
        analysis.transcript_version_id = version.id
    analysis.status = ConsultationSplitAnalysisStatus.ready
    intent = ConsultationSplitIntent(owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        analysis_id=analysis.id, client_idempotency_key=uuid4(), retention_expires_at=transcript.retention_expires_at,
        generation_snapshot_encrypted="")
    db.add(intent); db.flush()
    from app.services.content_crypto import encrypt_json_for_owner
    selected_template, selected_version = (structured, structured_version) if structured_only else (freeform, free_version)
    selected_config = selected_version.config_json if structured_only else None
    selected_sections = {
        "profile": "emis", "sections": [
            {"section_key": "problem", "section_label": "Problem", "section_order": 0},
            {"section_key": "tasks", "section_label": "Tasks", "section_order": 1},
        ],
    } if structured_only else None
    intent.generation_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=owner.id,
        table="consultation_split_intents", field="generation_snapshot_encrypted", record_id=intent.id,
        plaintext={"selected_template": {"template_id": str(selected_template.id), "template_version_id": str(selected_version.id),
            "template_version_no": 1, "name": selected_template.name, "description": selected_template.description, "mode": selected_version.mode.value,
            "prompt_text": "Synthetic prompt", "config": selected_config, "structured_sections": selected_sections},
            "generation_configuration": {"note_generation_length": "normal", "llm_detail_level": "balanced"}})
    draft = create_split_draft(db, owner, analysis=analysis)
    primary_template, primary_version = (structured, structured_version) if structured_only else (freeform, free_version)
    create_split_draft_topic(db, owner, draft=draft, title="Synthetic primary", topic_order=0, is_primary=True,
        disposition=ConsultationSplitTopicDisposition.separate_note, template_id=primary_template.id, template_version_id=primary_version.id)
    if mixed:
        create_split_draft_topic(db, owner, draft=draft, title="Synthetic structured", topic_order=1, is_primary=False,
            disposition=ConsultationSplitTopicDisposition.separate_note, template_id=structured.id, template_version_id=structured_version.id)
    if included:
        create_split_draft_topic(db, owner, draft=draft, title="Synthetic included", topic_order=2 if mixed else 1, is_primary=False,
            disposition=ConsultationSplitTopicDisposition.include_in_primary, template_id=None, template_version_id=None)
    db.commit()
    monkeypatch.setattr("app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches", lambda *_a, **_k: True)
    monkeypatch.setattr("app.services.task_outbox.try_publish_task_dispatch_safely", lambda *_a, **_k: None)
    confirmed = confirm_split_draft(db, owner, transcript_id=transcript.id,
        payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=draft.updated_at))
    execution = db.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.batch_id == confirmed.batch_id))
    assert execution is not None
    db.commit(); db.rollback()
    return transcript, execution.id


def _rows(db, execution_id):
    execution = db.get(ConsultationSplitExecution, execution_id)
    batch = db.get(ConsultationSplitBatch, execution.batch_id)
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution_id))
    outcomes = db.scalars(select(ConsultationSplitTopicOutcome).where(ConsultationSplitTopicOutcome.transcript_id == execution.transcript_id)).all()
    assert execution and batch and attempt
    return execution, batch, attempt, outcomes


def _response(db, execution_id, *, malformed=False):
    _execution, batch, _attempt, _outcomes = _rows(db, execution_id)
    from app.services.consultation_splits import read_split_batch_json
    owner = db.get(__import__("app.models", fromlist=["User"]).User, batch.owner_user_id)
    plan = read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted")
    notes = []
    for topic in plan["topics"]:
        if topic["disposition"] != "separate_note":
            continue
        content = {"problem": "Synthetic problem", "tasks": "Synthetic task"} if topic["template"]["mode"] == "structured" else "Synthetic freeform"
        notes.append({"topic_uuid": topic["topic_uuid"], "mode": topic["template"]["mode"], "content": content})
    if malformed:
        notes[0]["mode"] = "structured"
    return json.dumps({"notes": notes})


def _provider(monkeypatch, response, calls):
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or (response, {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18})))


def _run(db, execution_id):
    # Worker entrypoints deliberately require an idle session.
    db.rollback()
    return process_consultation_split_generation_execution(db, execution_id=execution_id)


def _queue_selected_verification(
    db, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    """Build one queued verifier with validated originals and no documents yet."""
    transcript, generation_id = _queue(
        db, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, structured_only=True,
    )
    config = db.scalar(
        select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id)
    ).config
    db.add(TeamHallucinationCheckSelection(
        team_id=owner.team_id, llm_config_id=config.id, selected_by_user_id=owner.id,
    ))
    db.commit()
    _provider(monkeypatch, _response(db, generation_id), [])
    assert _run(db, generation_id).outcome == "ready"
    verification = db.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == db.get(ConsultationSplitExecution, generation_id).batch_id,
        ConsultationSplitExecution.kind == "verification",
    ))
    assert verification is not None
    db.commit()
    return transcript, verification.id


def _verification_response(db, execution_id):
    execution, _batch, _attempt, _outcomes = _rows(db, execution_id)
    return json.dumps({
        str(topic.topic_uuid): {"status": "unchanged"}
        for topic in db.scalars(select(ConsultationSplitBatchTopic).where(
            ConsultationSplitBatchTopic.batch_id == execution.batch_id,
            ConsultationSplitBatchTopic.disposition == ConsultationSplitTopicDisposition.separate_note,
        ))
    })


def _disable_split_gate(db, owner, monkeypatch, gate):
    if gate == "deployment":
        monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")
        return
    preference = db.scalar(select(UserAppPreference).where(
        UserAppPreference.user_id == owner.id,
    ))
    assert preference is not None
    preference.preferences_json = {"split_consultations_into_separate_notes": False}
    db.commit()


def _generation_terminal_snapshot(batch, execution, attempt, dispatch, outcomes):
    """Record terminal metadata that a second lifecycle pass must not change."""
    return (
        (batch.status, batch.error_code, batch.completed_at),
        (execution.status, execution.error_code, execution.completed_at),
        (
            attempt.status,
            attempt.outcome,
            attempt.cancelled_at,
            attempt.settled_at,
            attempt.settlement_basis,
            attempt.settled_units,
        ),
        (dispatch.state, dispatch.cancelled_at, dispatch.failed_at),
        tuple(sorted(
            (outcome.id, outcome.status, outcome.error_code, outcome.output_encrypted, outcome.updated_at)
            for outcome in outcomes
        )),
    )


def test_generation_submits_before_one_provider_call_and_commits_exact_mixed_documents(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []
    _provider(monkeypatch, _response(db_session, execution_id), calls)
    observed = []
    runtime = __import__("app.services.consultation_split_generation_runtime", fromlist=["_submit"])
    original = runtime.llm_runtime.invoke_llm
    def invoke(**kwargs):
        execution, batch, attempt, _outcomes = _rows(db_session, execution_id)
        observed.append((attempt.status, execution.status, batch.status))
        return original(**kwargs)
    monkeypatch.setattr(runtime.llm_runtime, "invoke_llm", invoke)
    result = _run(db_session, execution_id)
    assert result.outcome == "ready", result
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    docs = db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)).all()
    sections = db_session.scalars(select(GeneratedDocumentSection)).all()
    assert calls == [1] and observed == [(AttemptStatus.submitted, ConsultationSplitExecutionStatus.processing, ConsultationSplitBatchStatus.generating)]
    assert execution.status is ConsultationSplitExecutionStatus.completed and batch.status is ConsultationSplitBatchStatus.ready and attempt.status is AttemptStatus.settled
    assert len(docs) == 2 and len(sections) == 2 and all(doc.title == "Consultation split note" for doc in docs)
    assert {doc.document_mode.value for doc in docs} == {"freeform", "structured"}
    structured_document = next(document for document in docs if document.document_mode is TemplateMode.structured)
    structured_sections = db_session.scalars(
        select(GeneratedDocumentSection).where(GeneratedDocumentSection.generated_document_id == structured_document.id)
    ).all()
    assert [(section.section_key, section.section_label, section.section_order) for section in structured_sections] == [
        ("problem", "Problem", 0), ("tasks", "Tasks", 1)
    ]
    assert [
        decrypt_text_for_owner(db_session, owner_user_id=owner.id, table="generated_document_sections",
            field="original_text_encrypted", record_id=section.id, stored_value=section.original_text_encrypted)
        for section in structured_sections
    ] == ["Synthetic problem", "Synthetic task"]
    assert all(outcome.status is ConsultationSplitTopicOutcomeStatus.ready for outcome in outcomes)
    assert len(db_session.scalars(select(ProviderUsageEvent).where(ProviderUsageEvent.consultation_split_execution_id == execution_id)).all()) == 1


def test_generation_reidentifies_saved_phi_placeholders_before_materializing_documents(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-reidentify-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    _execution, batch, _attempt, _outcomes = _rows(db_session, execution_id)
    from app.services.consultation_splits import read_split_batch_json
    pii_snapshot = read_split_batch_json(db_session, owner, batch=batch, field="pii_snapshot_encrypted")
    pii_snapshot["phi_index"] = [{"index": 1, "type": "PERSON", "value": "Synthetic Patient", "placeholder": "[PHI-1]"}]
    batch.pii_snapshot_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_batches",
        field="pii_snapshot_encrypted", record_id=batch.id, plaintext=pii_snapshot,
    ) or ""
    db_session.commit()

    plan = read_split_batch_json(db_session, owner, batch=batch, field="confirmed_plan_encrypted")
    response = json.dumps({"notes": [
        {
            "topic_uuid": topic["topic_uuid"],
            "mode": topic["template"]["mode"],
            "content": (
                {"problem": "[PHI-1] problem", "tasks": "Review [PHI-1]"}
                if topic["template"]["mode"] == "structured" else "[PHI-1] freeform note"
            ),
        }
        for topic in plan["topics"] if topic["disposition"] == "separate_note"
    ]})
    _provider(monkeypatch, response, [])

    assert _run(db_session, execution_id).outcome == "ready"
    documents = db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == batch.transcript_id)).all()
    assert all(
        "[PHI-1]" not in decrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="generated_documents",
            field="edited_output_text_encrypted", record_id=document.id,
            stored_value=document.edited_output_text_encrypted,
        )
        for document in documents
    )
    assert any(
        decrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="generated_documents",
            field="edited_output_text_encrypted", record_id=document.id,
            stored_value=document.edited_output_text_encrypted,
        ) == "Synthetic Patient freeform note"
        for document in documents
    )
    sections = db_session.scalars(select(GeneratedDocumentSection)).all()
    assert {decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="generated_document_sections",
        field="edited_text_encrypted", record_id=section.id, stored_value=section.edited_text_encrypted,
    ) for section in sections} == {"Synthetic Patient problem", "Review Synthetic Patient"}


@pytest.mark.parametrize("source_only", ["working_note", "dictation"])
def test_generation_materializes_source_only_batches_with_confirmation_bound_empty_version(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, source_only,
):
    owner = make_user(email=f"generation-source-only-{source_only}-{uuid4()}@example.com")
    transcript, execution_id = _queue(
        db_session, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, source_only=source_only,
    )
    execution, batch, _attempt, _outcomes = _rows(db_session, execution_id)
    analysis = db_session.get(ConsultationSplitAnalysis, batch.analysis_id)
    assert analysis is not None and analysis.transcript_version_id is None and analysis.redaction_run_id is None
    version = db_session.get(TranscriptVersion, batch.materialization_transcript_version_id)
    assert version is not None and version.transcript_id == transcript.id
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=version.id,
        stored_value=version.text_encrypted,
    ) == ""

    _provider(monkeypatch, _response(db_session, execution_id), [])
    assert _run(db_session, execution_id).outcome == "ready"
    documents = db_session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
    ).all()
    assert len(documents) == 2
    assert {document.transcript_version_id for document in documents} == {version.id}


@pytest.mark.parametrize("binding", ["missing", "wrong_root"])
def test_generation_rejects_invalid_confirmation_materialization_binding_before_provider_submission(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, binding,
):
    owner = make_user(email=f"generation-materialization-{binding}-{uuid4()}@example.com")
    transcript, execution_id = _queue(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
    )
    _execution, batch, _attempt, _outcomes = _rows(db_session, execution_id)
    if binding == "missing":
        batch.materialization_transcript_version_id = None
    else:
        other = Transcript(
            owner_user_id=owner.id,
            team_id=owner.team_id,
            title="Other synthetic consultation",
            ingestion_mode=TranscriptIngestionMode.whole_file,
            status=TranscriptStatus.ready,
            retention_days_applied=30,
            retention_expires_at=utcnow() + timedelta(days=30),
        )
        db_session.add(other)
        db_session.flush()
        wrong_version = TranscriptVersion(transcript_id=other.id, version_no=1, text_encrypted="")
        db_session.add(wrong_version)
        db_session.flush()
        wrong_version.text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=wrong_version.id,
            plaintext="Other transcript",
        ) or ""
        batch.materialization_transcript_version_id = wrong_version.id
    db_session.commit()

    calls = []
    _provider(monkeypatch, _response(db_session, execution_id), calls)
    result = _run(db_session, execution_id)
    execution, batch, attempt, _outcomes = _rows(db_session, execution_id)
    assert result.outcome == "failed"
    assert result.error_code == "consultation_split_materialization_binding_invalid"
    assert calls == []
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert batch.status is ConsultationSplitBatchStatus.failed
    assert attempt.status is AttemptStatus.cancelled
    assert db_session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
    ).all() == []


def test_generation_resolves_credential_before_transaction_and_recovers_encrypted_response_once(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-recover-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []
    def credential(_config):
        assert not db_session.in_transaction()
        return "token"
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", credential)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm", lambda **_k: (calls.append(1) or (_response(db_session, execution_id), {"total_tokens": 2})))
    runtime = __import__("app.services.consultation_split_generation_runtime", fromlist=["_finalize"])
    original_finalize = runtime._finalize
    monkeypatch.setattr(runtime, "_finalize", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("synthetic crash")))
    with pytest.raises(RuntimeError, match="synthetic crash"):
        _run(db_session, execution_id)
    db_session.rollback(); execution, _batch, attempt, _outcomes = _rows(db_session, execution_id)
    assert calls == [1] and attempt.status is AttemptStatus.submitted and execution.recoverable_response_encrypted
    assert "Synthetic freeform" not in execution.recoverable_response_encrypted
    monkeypatch.setattr(runtime, "_finalize", original_finalize)
    result = _run(db_session, execution_id)
    assert result.outcome == "ready", result
    assert calls == [1]


@pytest.mark.parametrize("payload", ["not-json", "{}"])
def test_generation_bad_output_queues_one_safe_automatic_recovery_without_documents(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, payload):
    owner = make_user(email=f"generation-invalid-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []; _provider(monkeypatch, payload, calls)
    result = _run(db_session, execution_id)
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    assert result.outcome == "failed" and result.error_code == "consultation_split_generation_invalid_output" and calls == [1]
    assert execution.status is ConsultationSplitExecutionStatus.failed and batch.status is ConsultationSplitBatchStatus.partially_ready and attempt.status is AttemptStatus.settled
    assert all(item.status is ConsultationSplitTopicOutcomeStatus.failed and item.error_code == result.error_code for item in outcomes)
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)).all() == []
    recovery = db_session.scalars(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
    )).all()
    assert len(recovery) == 2
    # Replaying finalization cannot submit, reserve, or enqueue another retry.
    assert _run(db_session, execution_id).outcome == "failed"
    assert len(db_session.scalars(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
    )).all()) == 2


def test_generation_mode_mismatch_retains_valid_sibling_without_documents(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-duplicate-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []; _provider(monkeypatch, _response(db_session, execution_id, malformed=True), calls)
    assert _run(db_session, execution_id).outcome == "ready"
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    assert batch.status is ConsultationSplitBatchStatus.partially_ready
    assert {item.status for item in outcomes} == {
        ConsultationSplitTopicOutcomeStatus.validated,
        ConsultationSplitTopicOutcomeStatus.failed,
    }
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)).all() == []
    assert _run(db_session, execution_id).outcome == "ready"
    assert calls == [1]


def test_automatic_recovery_uses_frozen_provider_and_manual_retry_uses_current_selection(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-auto-frozen-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []
    _provider(monkeypatch, _response(db_session, execution_id, malformed=True), calls)
    assert _run(db_session, execution_id).outcome == "ready"
    initial = db_session.get(ConsultationSplitExecution, execution_id)
    automatic = db_session.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == initial.batch_id,
        ConsultationSplitExecution.attempt_no == 2,
    ))
    assert automatic is not None
    assert (automatic.llm_config_id, automatic.provider_model) == (initial.llm_config_id, initial.provider_model)
    assert read_split_execution_json(db_session, owner, execution=automatic, field="provider_snapshot_encrypted") == read_split_execution_json(
        db_session, owner, execution=initial, field="provider_snapshot_encrypted"
    )

    # Once that automatic chance has terminally failed, a clinician-directed
    # retry is a new current-policy operation rather than a frozen replay.
    automatic.status = ConsultationSplitExecutionStatus.failed
    db_session.commit()
    current = make_llm_config(
        team=owner.team,
        actor=owner,
        label="Current recovery provider",
        model_name="current-model",
        available_models_json=["current-model"],
    )
    selection = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id))
    assert selection is not None
    selection.llm_config_id = current.id
    selection.allowed_models_json = ["current-model"]
    db_session.commit()
    queued = retry_missing_split_notes(
        db_session, owner, transcript_id=initial.transcript_id, batch_id=initial.batch_id,
    )
    manual = db_session.get(ConsultationSplitExecution, queued.execution_id)
    assert queued.replayed is False
    assert (manual.llm_config_id, manual.provider_model, manual.attempt_no) == (current.id, "current-model", 3)


@pytest.mark.real_db_connections
def test_manual_and_automatic_recovery_race_create_one_active_execution(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    """The source-scope lock makes a current-policy retry and frozen retry converge."""
    owner = make_user(email=f"generation-recovery-race-{uuid4()}@example.com")
    transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    monkeypatch.setattr(
        "app.services.consultation_split_recovery.queue_automatic_split_recovery",
        lambda *_args, **_kwargs: None,
    )
    _provider(monkeypatch, _response(db_session, execution_id, malformed=True), [])
    assert _run(db_session, execution_id).outcome == "ready"
    initial, batch, _attempt, _outcomes = _rows(db_session, execution_id)
    assert initial.status is ConsultationSplitExecutionStatus.completed
    assert batch.status is ConsultationSplitBatchStatus.partially_ready

    current = make_llm_config(
        team=owner.team, actor=owner, label="Race current provider", model_name="race-current-model",
        available_models_json=["race-current-model"],
    )
    selection = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id))
    assert selection is not None
    selection.llm_config_id = current.id
    selection.allowed_models_json = ["race-current-model"]
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_recovery.try_publish_task_dispatch_safely", lambda *_args: None)

    owner_id, transcript_id, batch_id, initial_id = owner.id, transcript.id, batch.id, initial.id
    factory = sessionmaker(bind=db_session.get_bind().engine, autoflush=False, future=True)
    barrier = Barrier(2)
    results, errors = [], []

    def queue_in_session(*, automatic: bool):
        with factory() as session:
            try:
                actor = session.get(type(owner), owner_id)
                barrier.wait(timeout=5)
                result = (
                    queue_automatic_split_recovery(
                        session, actor, transcript_id=transcript_id, batch_id=batch_id, execution_id=initial_id,
                    )
                    if automatic
                    else retry_missing_split_notes(session, actor, transcript_id=transcript_id, batch_id=batch_id)
                )
                results.append(result)
            except BaseException as exc:
                errors.append(exc)
                session.rollback()

    threads = [
        Thread(target=queue_in_session, kwargs={"automatic": True}),
        Thread(target=queue_in_session, kwargs={"automatic": False}),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    # An automatic loser returns None because its single chance is already
    # consumed; a manual loser replays the winner. Neither path creates a
    # second reservation/outbox chain.
    returned = [result for result in results if result is not None]
    assert returned
    assert len({result.execution_id for result in returned}) == 1
    db_session.expire_all()
    recoveries = db_session.scalars(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch_id,
        ConsultationSplitExecution.attempt_no > 1,
    )).all()
    assert len(recoveries) == 1
    assert recoveries[0].status is ConsultationSplitExecutionStatus.queued
    assert recoveries[0].attempt_no == 2


def test_no_automatic_recovery_after_submitted_no_response_timeout(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    """An unknown submitted attempt has no durable response proof and is never retried automatically."""
    owner = make_user(email=f"generation-no-response-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    execution, batch, attempt, _outcomes = _rows(db_session, execution_id)
    now = utcnow() + timedelta(minutes=1)
    mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, now=now - timedelta(minutes=1), deadline_at=now)
    execution.status = ConsultationSplitExecutionStatus.processing
    batch.status = ConsultationSplitBatchStatus.generating
    db_session.commit()

    assert process_quota_lifecycle(db_session, now=now) == 2
    db_session.expire_all()
    assert db_session.scalars(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
    )).all() == [db_session.get(ConsultationSplitExecution, execution_id)]


@pytest.mark.parametrize("failure_mode", ["reservation_expiry", "submitted_timeout", "failed_outbox"])
def test_quota_lifecycle_recovery_failure_preserves_validated_siblings_and_partial_actions(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, failure_mode,
):
    """A terminal recovery failure returns survivor batches to clinician-directed partial review."""
    owner = make_user(email=f"generation-recovery-quota-{failure_mode}-{uuid4()}@example.com")
    _transcript, initial_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    _provider(monkeypatch, _response(db_session, initial_id, malformed=True), [])
    assert _run(db_session, initial_id).outcome == "ready"
    initial, batch, _initial_attempt, _outcomes = _rows(db_session, initial_id)
    recovery = db_session.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.attempt_no == 2,
    ))
    recovery_attempt = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == recovery.id,
    ))
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == recovery.id))
    assert recovery is not None and recovery_attempt is not None and dispatch is not None
    now = utcnow() + timedelta(minutes=1)
    if failure_mode == "reservation_expiry":
        recovery_attempt.reservation_valid_until = now
    elif failure_mode == "submitted_timeout":
        mark_provider_attempt_submitted(
            db_session, attempt_id=recovery_attempt.id, now=now - timedelta(minutes=1), deadline_at=now,
        )
        recovery.status = ConsultationSplitExecutionStatus.processing
        batch.status = ConsultationSplitBatchStatus.generating
    else:
        dispatch.state = TaskDispatchState.failed
        dispatch.failed_at = now
    db_session.commit()

    assert process_quota_lifecycle(db_session, now=now) == 2
    db_session.expire_all()
    recovery = db_session.get(ConsultationSplitExecution, recovery.id)
    batch = db_session.get(ConsultationSplitBatch, batch.id)
    outcomes = db_session.scalars(select(ConsultationSplitTopicOutcome).where(
        ConsultationSplitTopicOutcome.transcript_id == initial.transcript_id,
    )).all()
    projection = read_workspace_split_batch(db_session, owner, transcript_id=initial.transcript_id)
    assert recovery.status is ConsultationSplitExecutionStatus.failed
    assert batch.status is ConsultationSplitBatchStatus.partially_ready
    assert {outcome.status for outcome in outcomes} == {
        ConsultationSplitTopicOutcomeStatus.validated,
        ConsultationSplitTopicOutcomeStatus.failed,
    }
    assert projection is not None
    assert projection.can_keep_available is True
    assert projection.can_retry_missing is True
    assert projection.active_execution_id is None
    if failure_mode == "submitted_timeout":
        assert len(db_session.scalars(select(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == batch.id,
        )).all()) == 2


@pytest.mark.real_db_connections
def test_keep_partial_primary_failure_rolls_back_replays_and_serializes_concurrent_calls(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-keep-partial-{uuid4()}@example.com")
    transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    # Hold back automatic queueing so this test can exercise Keep's own lock.
    monkeypatch.setattr(
        "app.services.consultation_split_recovery.queue_automatic_split_recovery",
        lambda *_args, **_kwargs: None,
    )
    _provider(monkeypatch, _response(db_session, execution_id, malformed=True), [])
    assert _run(db_session, execution_id).outcome == "ready"
    initial, batch, _attempt, outcomes = _rows(db_session, execution_id)
    assert batch.status is ConsultationSplitBatchStatus.partially_ready
    primary = next(outcome for outcome in outcomes if outcome.batch_topic.is_primary)
    survivor = next(outcome for outcome in outcomes if not outcome.batch_topic.is_primary)
    assert primary.status is ConsultationSplitTopicOutcomeStatus.failed
    assert survivor.status is ConsultationSplitTopicOutcomeStatus.validated

    partial_module = __import__("app.services.consultation_split_partial", fromlist=["encrypt_text_for_owner"])
    original_encrypt = partial_module.encrypt_text_for_owner
    monkeypatch.setattr(partial_module, "encrypt_text_for_owner", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic keep failure")))
    with pytest.raises(RuntimeError, match="synthetic keep failure"):
        keep_available_split_notes(db_session, owner, transcript_id=transcript.id, batch_id=batch.id)
    db_session.rollback()
    assert db_session.get(ConsultationSplitBatch, batch.id).status is ConsultationSplitBatchStatus.partially_ready
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all() == []
    monkeypatch.setattr(partial_module, "encrypt_text_for_owner", original_encrypt)

    owner_id, batch_id, transcript_id, survivor_topic_id = owner.id, batch.id, transcript.id, survivor.batch_topic_id
    db_session.rollback()
    # Each worker receives an independent session/connection. The fixture
    # session is never shared across the threads.
    factory = sessionmaker(bind=db_session.get_bind().engine, autoflush=False, future=True)
    barrier = Barrier(2)
    results, errors = [], []
    def keep_in_session():
        with factory() as session:
            try:
                actor = session.get(type(owner), owner_id)
                barrier.wait(timeout=5)
                results.append(keep_available_split_notes(session, actor, transcript_id=transcript_id, batch_id=batch_id))
            except BaseException as exc:
                errors.append(exc)
                session.rollback()
    threads = [Thread(target=keep_in_session), Thread(target=keep_in_session)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2 and all(len(documents) == 1 for documents in results)
    assert {document.id for documents in results for document in documents} == {results[0][0].id}
    assert results[0][0].consultation_split_batch_topic_id == survivor_topic_id
    assert db_session.get(ConsultationSplitBatch, batch_id).status is ConsultationSplitBatchStatus.completed_partial
    assert len(db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)).all()) == 1


def test_keep_available_notes_reidentifies_saved_phi_placeholders(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-keep-reidentify-{uuid4()}@example.com")
    transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    _execution, batch, _attempt, _outcomes = _rows(db_session, execution_id)
    from app.services.consultation_splits import read_split_batch_json
    pii_snapshot = read_split_batch_json(db_session, owner, batch=batch, field="pii_snapshot_encrypted")
    pii_snapshot["phi_index"] = [{"index": 1, "type": "PERSON", "value": "Synthetic Patient", "placeholder": "[PHI-1]"}]
    batch.pii_snapshot_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_batches",
        field="pii_snapshot_encrypted", record_id=batch.id, plaintext=pii_snapshot,
    ) or ""
    plan = read_split_batch_json(db_session, owner, batch=batch, field="confirmed_plan_encrypted")
    notes = []
    for topic in plan["topics"]:
        if topic["disposition"] != "separate_note":
            continue
        if topic["is_primary"]:
            notes.append({"topic_uuid": topic["topic_uuid"], "mode": "structured", "content": {"problem": "invalid", "tasks": "invalid"}})
        else:
            notes.append({"topic_uuid": topic["topic_uuid"], "mode": "structured", "content": {"problem": "[PHI-1] problem", "tasks": "Review [PHI-1]"}})
    db_session.commit()
    monkeypatch.setattr("app.services.consultation_split_recovery.queue_automatic_split_recovery", lambda *_args, **_kwargs: None)
    _provider(monkeypatch, json.dumps({"notes": notes}), [])

    assert _run(db_session, execution_id).outcome == "ready"
    documents = keep_available_split_notes(db_session, owner, transcript_id=transcript.id, batch_id=batch.id)
    assert len(documents) == 1
    sections = db_session.scalars(select(GeneratedDocumentSection).where(
        GeneratedDocumentSection.generated_document_id == documents[0].id,
    )).all()
    assert {decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="generated_document_sections",
        field="edited_text_encrypted", record_id=section.id, stored_value=section.edited_text_encrypted,
    ) for section in sections} == {"Synthetic Patient problem", "Review Synthetic Patient"}


def test_generation_non_separate_outcome_is_ready_encrypted_and_has_no_document(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-disposition-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch, included=True)
    calls = []; _provider(monkeypatch, _response(db_session, execution_id), calls)
    result = _run(db_session, execution_id)
    assert result.outcome == "ready", result
    execution, _batch, _attempt, outcomes = _rows(db_session, execution_id)
    non_separate = next(item for item in outcomes if item.batch_topic.disposition is ConsultationSplitTopicDisposition.include_in_primary)
    assert non_separate.status is ConsultationSplitTopicOutcomeStatus.ready and non_separate.output_encrypted
    assert decrypt_json_for_owner(db_session, owner_user_id=owner.id, table="consultation_split_topic_outcomes", field="output_encrypted", record_id=non_separate.id, stored_value=non_separate.output_encrypted) == {"disposition": "include_in_primary"}
    assert len(db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)).all()) == 2


def test_generation_persistence_failure_rolls_back_children_and_provider_errors_do_not_log_content(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, caplog):
    owner = make_user(email=f"generation-rollback-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []; _provider(monkeypatch, _response(db_session, execution_id), calls)
    runtime = __import__("app.services.consultation_split_generation_runtime", fromlist=["encrypt_text_for_owner"])
    original = runtime.encrypt_text_for_owner; count = {"value": 0}
    def fail_midway(*args, **kwargs):
        count["value"] += 1
        if count["value"] == 3: raise RuntimeError("synthetic persistence failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime, "encrypt_text_for_owner", fail_midway)
    assert _run(db_session, execution_id).outcome == "failed"
    execution, batch, _attempt, outcomes = _rows(db_session, execution_id)
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)).all() == []
    assert db_session.scalars(select(GeneratedDocumentSection)).all() == [] and batch.status is ConsultationSplitBatchStatus.failed
    assert all(item.status is ConsultationSplitTopicOutcomeStatus.failed for item in outcomes)
    assert "Synthetic freeform" not in caplog.text and "synthetic persistence failure" not in caplog.text


@pytest.mark.parametrize("failure", [AppError(504, "provider_timeout", "Synthetic provider body"), RuntimeError("Synthetic provider body")])
def test_generation_provider_failures_terminalize_without_logging_provider_content(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, caplog, failure):
    owner = make_user(email=f"generation-provider-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm", lambda **_k: (_ for _ in ()).throw(failure))
    result = _run(db_session, execution_id)
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    assert result.error_code == "consultation_split_provider_failed"
    assert execution.status is ConsultationSplitExecutionStatus.failed and batch.status is ConsultationSplitBatchStatus.failed
    assert attempt.status is AttemptStatus.settled and all(item.status is ConsultationSplitTopicOutcomeStatus.failed for item in outcomes)
    assert db_session.scalars(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id
    )).all() == [execution]
    assert "Synthetic provider body" not in caplog.text


def test_generation_topic_titles_are_encrypted_owner_only_and_document_deletion_preserves_batch_provenance(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-provenance-{uuid4()}@example.com")
    colleague = make_user(email=f"generation-colleague-{uuid4()}@example.com", team=owner.team)
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []; _provider(monkeypatch, _response(db_session, execution_id), calls)
    assert _run(db_session, execution_id).outcome == "ready"
    execution, batch, _attempt, outcomes = _rows(db_session, execution_id)
    topics = db_session.scalars(select(ConsultationSplitBatchTopic).where(ConsultationSplitBatchTopic.batch_id == batch.id)).all()
    assert [read_split_topic_title(db_session, owner, topic=topic) for topic in topics] == ["Synthetic primary", "Synthetic structured"]
    assert all("Synthetic" not in topic.title_encrypted for topic in topics)
    with pytest.raises(AppError):
        read_split_topic_title(db_session, colleague, topic=topics[0])
    documents = db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)).all()
    removed, sibling = documents
    removed_topic_id = removed.consultation_split_batch_topic_id
    db_session.delete(removed); db_session.commit(); db_session.rollback()
    surviving_topic = db_session.get(ConsultationSplitBatchTopic, removed_topic_id)
    assert surviving_topic is not None and surviving_topic.title_encrypted
    assert db_session.get(GeneratedDocument, sibling.id) is not None
    outcome = next(item for item in outcomes if item.batch_topic_id == removed_topic_id)
    assert outcome.status is ConsultationSplitTopicOutcomeStatus.ready and outcome.output_encrypted


def test_generation_expired_root_before_submission_cancels_reservation_without_provider(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-expired-{uuid4()}@example.com")
    transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.utcnow", lambda: transcript.retention_expires_at + timedelta(seconds=1))
    # Credential lookup is deliberately outside the runtime transaction; expiry
    # still prevents submission and provider invocation.
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm", lambda **_k: pytest.fail("provider invoked"))
    result = _run(db_session, execution_id)
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    assert result.error_code == "consultation_split_source_expired"
    assert execution.status is ConsultationSplitExecutionStatus.failed and batch.status is ConsultationSplitBatchStatus.failed
    assert attempt.status is AttemptStatus.cancelled and all(item.status is ConsultationSplitTopicOutcomeStatus.failed for item in outcomes)


@pytest.mark.parametrize(
    ("failure_mode", "error_code", "attempt_status", "attempt_outcome", "dispatch_state"),
    [
        (
            "reservation_expiry",
            QUOTA_RESERVATION_EXPIRED,
            AttemptStatus.cancelled,
            AttemptOutcome.cancelled,
            TaskDispatchState.cancelled,
        ),
        (
            "submitted_timeout",
            PROVIDER_ATTEMPT_OUTCOME_UNKNOWN,
            AttemptStatus.settled,
            AttemptOutcome.unknown,
            TaskDispatchState.cancelled,
        ),
        (
            "failed_outbox",
            TASK_DISPATCH_FAILED,
            AttemptStatus.cancelled,
            AttemptOutcome.cancelled,
            TaskDispatchState.failed,
        ),
    ],
)
def test_quota_lifecycle_terminalizes_every_pending_generation_topic_outcome_once(
    db_session,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    monkeypatch,
    failure_mode,
    error_code,
    attempt_status,
    attempt_outcome,
    dispatch_state,
    caplog,
):
    """Quota cleanup must fail a bundled generation without creating output."""
    owner = make_user(email=f"generation-quota-{failure_mode}-{uuid4()}@example.com")
    _transcript, execution_id = _queue(
        db_session,
        owner,
        make_llm_config,
        make_llm_selection,
        make_template,
        monkeypatch,
        included=True,
    )
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    dispatch = db_session.scalar(
        select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == execution.id)
    )
    assert dispatch is not None
    assert len(outcomes) == 3
    assert all(outcome.status is ConsultationSplitTopicOutcomeStatus.pending for outcome in outcomes)

    now = utcnow() + timedelta(minutes=1)
    if failure_mode == "reservation_expiry":
        attempt.reservation_valid_until = now
    elif failure_mode == "submitted_timeout":
        mark_provider_attempt_submitted(
            db_session,
            attempt_id=attempt.id,
            now=now - timedelta(minutes=1),
            deadline_at=now,
        )
        execution.status = ConsultationSplitExecutionStatus.processing
        batch.status = ConsultationSplitBatchStatus.generating
    else:
        dispatch.state = TaskDispatchState.failed
        dispatch.failed_at = now
    db_session.commit()

    caplog.clear()
    assert process_quota_lifecycle(db_session, now=now) == 2
    db_session.expire_all()
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    dispatch = db_session.scalar(
        select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == execution.id)
    )
    usage_events = db_session.scalars(
        select(ProviderUsageEvent).where(
            ProviderUsageEvent.consultation_split_execution_id == execution_id
        )
    ).all()

    assert dispatch is not None and dispatch.state is dispatch_state
    assert (execution.status, execution.error_code) == (
        ConsultationSplitExecutionStatus.failed,
        error_code,
    )
    assert (batch.status, batch.error_code) == (ConsultationSplitBatchStatus.failed, error_code)
    assert (attempt.status, attempt.outcome) == (attempt_status, attempt_outcome)
    assert attempt.settled_units == (attempt.reserved_units if failure_mode == "submitted_timeout" else None)
    assert len(db_session.scalars(
        select(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == execution_id
        )
    ).all()) == 1
    assert all(
        outcome.status is ConsultationSplitTopicOutcomeStatus.failed
        and outcome.error_code == error_code
        and outcome.output_encrypted is None
        for outcome in outcomes
    )
    assert len(outcomes) == 3
    assert usage_events == []
    assert db_session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == execution.transcript_id)
    ).all() == []
    assert "Synthetic working note" not in caplog.text
    assert "Synthetic transcript" not in caplog.text

    terminal_snapshot = _generation_terminal_snapshot(
        batch, execution, attempt, dispatch, outcomes
    )

    assert process_quota_lifecycle(db_session, now=now + timedelta(minutes=1)) == 0
    db_session.expire_all()
    assert db_session.scalar(
        select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution_id)
    ).status is attempt_status
    assert len(db_session.scalars(
        select(ConsultationSplitTopicOutcome).where(
            ConsultationSplitTopicOutcome.transcript_id == execution.transcript_id,
            ConsultationSplitTopicOutcome.status == ConsultationSplitTopicOutcomeStatus.failed,
        )
    ).all()) == 3
    assert db_session.scalars(
        select(ProviderUsageEvent).where(
            ProviderUsageEvent.consultation_split_execution_id == execution_id
        )
    ).all() == []
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    dispatch = db_session.scalar(
        select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == execution.id)
    )
    assert dispatch is not None
    assert _generation_terminal_snapshot(
        batch, execution, attempt, dispatch, outcomes
    ) == terminal_snapshot


def test_generation_transcript_root_delete_cascades_batch_execution_outcomes_and_documents(db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
    owner = make_user(email=f"generation-delete-{uuid4()}@example.com")
    transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    calls = []; _provider(monkeypatch, _response(db_session, execution_id), calls)
    assert _run(db_session, execution_id).outcome == "ready"
    execution, batch, _attempt, outcomes = _rows(db_session, execution_id)
    transcript_id, batch_id = transcript.id, batch.id
    document_ids = list(db_session.scalars(select(GeneratedDocument.id).where(GeneratedDocument.transcript_id == transcript_id)))
    outcome_ids = [outcome.id for outcome in outcomes]
    assert delete_transcripts(db_session, owner, transcript_ids=[transcript_id]) == 1
    assert db_session.get(Transcript, transcript_id) is None and db_session.get(ConsultationSplitBatch, batch_id) is None
    assert db_session.get(ConsultationSplitExecution, execution_id) is None
    assert all(db_session.get(ConsultationSplitTopicOutcome, outcome_id) is None for outcome_id in outcome_ids)
    assert all(db_session.get(GeneratedDocument, document_id) is None for document_id in document_ids)


def test_generation_without_checker_records_durable_unchecked_state_and_materializes_originals(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-unchecked-{uuid4()}@example.com")
    _transcript, execution_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch)
    _provider(monkeypatch, _response(db_session, execution_id), [])
    assert _run(db_session, execution_id).outcome == "ready"
    _execution, batch, _attempt, _outcomes = _rows(db_session, execution_id)
    assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
    assert batch.verification_reason == "verification_not_selected"
    assert batch.verification_completed_at is not None


def test_selected_checker_runs_once_before_structured_outputs_materialize(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-checked-{uuid4()}@example.com")
    transcript, generation_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, structured_only=True)
    config = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id)).config
    db_session.add(TeamHallucinationCheckSelection(
        team_id=owner.team_id, llm_config_id=config.id, selected_by_user_id=owner.id,
    ))
    db_session.commit()
    _provider(monkeypatch, _response(db_session, generation_id), [])
    result = _run(db_session, generation_id)
    assert result.outcome == "ready", _rows(db_session, generation_id)[:3]
    verification = db_session.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == db_session.get(ConsultationSplitExecution, generation_id).batch_id,
        ConsultationSplitExecution.kind == "verification",
    ))
    assert verification is not None
    verification_batch_id = verification.batch_id
    calls = []
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm",
            lambda **_kwargs: (calls.append(1) or (json.dumps({str(topic.topic_uuid): {"status": "unchanged"}
                for topic in db_session.scalars(select(ConsultationSplitBatchTopic).where(
                ConsultationSplitBatchTopic.batch_id == verification_batch_id)).all()
                if topic.disposition is ConsultationSplitTopicDisposition.separate_note}), {"total_tokens": 2})))
    verification_id = verification.id
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    assert calls == [1]
    db_session.expire_all()
    batch = db_session.get(ConsultationSplitBatch, verification_batch_id)
    assert batch.verification_status.value == "verified"
    assert batch.status is ConsultationSplitBatchStatus.ready
    assert len(db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all()) == 2


@pytest.mark.parametrize("gate", ["deployment", "preference"])
def test_submitted_verifier_finishes_after_gate_flip_without_reopening_manual_actions(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, gate,
):
    owner = make_user(email=f"verification-gate-flip-{gate}-{uuid4()}@example.com")
    transcript, verification_id = _queue_selected_verification(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
    )
    verification, batch, attempt, _outcomes = _rows(db_session, verification_id)
    counts_before = (
        db_session.scalar(select(func.count()).select_from(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == batch.id)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_id == verification_id)),
        db_session.scalar(select(func.count()).select_from(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == verification_id)),
    )
    _disable_split_gate(db_session, owner, monkeypatch, gate)
    for action in (keep_available_split_notes, retry_missing_split_notes):
        with pytest.raises(AppError, match="Consultation splitting is not enabled") as raised:
            action(db_session, owner, transcript_id=transcript.id, batch_id=batch.id)
        assert raised.value.code == "consultation_split_disabled"
        db_session.rollback()
    calls = []
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (calls.append(1) or (_verification_response(db_session, verification_id), {"total_tokens": 2})),
    )
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    db_session.expire_all()
    verification, batch, attempt, outcomes = _rows(db_session, verification_id)
    assert calls == [1]
    assert verification.status is ConsultationSplitExecutionStatus.completed
    assert attempt.status is AttemptStatus.settled and attempt.settled_at is not None
    assert batch.status is ConsultationSplitBatchStatus.ready
    assert all(outcome.status is ConsultationSplitTopicOutcomeStatus.ready for outcome in outcomes)
    assert len(db_session.scalars(select(GeneratedDocument).where(
        GeneratedDocument.transcript_id == transcript.id)).all()) == 2
    assert counts_before == (
        db_session.scalar(select(func.count()).select_from(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == batch.id)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_id == verification_id)),
        db_session.scalar(select(func.count()).select_from(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == verification_id)),
    )
    settled_at = attempt.settled_at
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    assert db_session.get(ProviderAttempt, attempt.id).settled_at == settled_at
    for action in (keep_available_split_notes, retry_missing_split_notes):
        with pytest.raises(AppError) as raised:
            action(db_session, owner, transcript_id=transcript.id, batch_id=batch.id)
        assert raised.value.code == "consultation_split_disabled"
        db_session.rollback()


def _assert_gate_closed_verifier_fail_open_result(
    db, owner, transcript, verification_id, *, expected_reason, expected_attempt_outcome,
):
    """Check that a submitted optional verifier releases immutable originals once."""
    db.expire_all()
    verification, batch, attempt, outcomes = _rows(db, verification_id)
    documents = db.scalars(select(GeneratedDocument).where(
        GeneratedDocument.transcript_id == transcript.id,
    )).all()
    separate = [
        outcome for outcome in outcomes
        if outcome.batch_topic.disposition is ConsultationSplitTopicDisposition.separate_note
    ]
    projection = read_workspace_split_batch(db, owner, transcript_id=transcript.id)

    assert verification.status is ConsultationSplitExecutionStatus.failed
    assert verification.error_code == expected_reason
    assert batch.status is ConsultationSplitBatchStatus.ready
    assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
    assert batch.verification_reason == expected_reason
    assert attempt.status is AttemptStatus.settled
    assert attempt.outcome is expected_attempt_outcome
    assert all(outcome.status is ConsultationSplitTopicOutcomeStatus.ready for outcome in separate)
    assert all(outcome.verified_output_encrypted is None for outcome in separate)
    assert {document.consultation_split_batch_topic_id for document in documents} == {
        outcome.batch_topic_id for outcome in separate
    }
    assert len(documents) == len(separate)
    for document in documents:
        outcome = next(item for item in separate if item.batch_topic_id == document.consultation_split_batch_topic_id)
        output = read_split_topic_outcome_output(db, owner, outcome=outcome)
        sections = db.scalars(select(GeneratedDocumentSection).where(
            GeneratedDocumentSection.generated_document_id == document.id,
        ).order_by(GeneratedDocumentSection.section_order)).all()
        assert [decrypt_text_for_owner(
            db, owner_user_id=owner.id, table="generated_document_sections",
            field="original_text_encrypted", record_id=section.id,
            stored_value=section.original_text_encrypted,
        ) for section in sections] == [output["content"]["problem"], output["content"]["tasks"]]
    assert projection is not None
    assert projection.can_keep_available is False
    assert projection.can_retry_missing is False
    return verification, batch, attempt


@pytest.mark.parametrize("gate", ["deployment", "preference"])
@pytest.mark.parametrize(
    ("provider_result", "expected_reason"),
    [
        ("timeout", "verification_provider_failed"),
        ("invalid", "verification_invalid_output"),
    ],
)
def test_submitted_verifier_fail_open_materializes_originals_after_gate_flip(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
    gate, provider_result, expected_reason,
):
    """A closed gate cannot strand originals after the checker was submitted."""
    owner = make_user(email=f"verification-fail-open-{gate}-{provider_result}-{uuid4()}@example.com")
    transcript, verification_id = _queue_selected_verification(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
    )
    verification, batch, _attempt, _outcomes = _rows(db_session, verification_id)
    batch_id = batch.id
    counts_before = (
        db_session.scalar(select(func.count()).select_from(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == batch_id)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_id == verification_id)),
        db_session.scalar(select(func.count()).select_from(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == verification_id)),
    )
    calls = []

    def provider(**_kwargs):
        live, _live_batch, live_attempt, _live_outcomes = _rows(db_session, verification_id)
        assert live.status is ConsultationSplitExecutionStatus.processing
        assert live_attempt.status is AttemptStatus.submitted
        _disable_split_gate(db_session, owner, monkeypatch, gate)
        calls.append(1)
        if provider_result == "timeout":
            raise AppError(504, "provider_timeout", "synthetic timeout")
        return "not-json", {"total_tokens": 2}

    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.resolve_generation_credential",
        lambda _config: "token",
    )
    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm", provider,
    )
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    verification, batch, attempt = _assert_gate_closed_verifier_fail_open_result(
        db_session, owner, transcript, verification_id,
        expected_reason=expected_reason, expected_attempt_outcome=AttemptOutcome.unknown,
    )
    assert calls == [1]
    assert counts_before == (
        db_session.scalar(select(func.count()).select_from(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == batch.id)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_id == verification_id)),
        db_session.scalar(select(func.count()).select_from(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == verification_id)),
    )
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    assert calls == [1]
    assert db_session.get(ProviderAttempt, attempt.id).settled_at == attempt.settled_at
    for action in (keep_available_split_notes, retry_missing_split_notes):
        with pytest.raises(AppError) as raised:
            action(db_session, owner, transcript_id=transcript.id, batch_id=batch.id)
        assert raised.value.code == "consultation_split_disabled"
        db_session.rollback()


@pytest.mark.parametrize("gate", ["deployment", "preference"])
def test_partial_generation_recovers_before_selected_checker_snapshots_stable_survivors(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, gate,
):
    """Recovery is the only next stage after a durable partial response."""
    owner = make_user(email=f"generation-recovery-before-checker-{uuid4()}@example.com")
    transcript, initial_id = _queue(
        db_session, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, structured_only=True,
    )
    config = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id)).config
    db_session.add(TeamHallucinationCheckSelection(
        team_id=owner.team_id, llm_config_id=config.id, selected_by_user_id=owner.id,
    ))
    db_session.commit()
    partial = json.loads(_response(db_session, initial_id))
    partial["notes"][0]["content"] = "invalid structured output"
    generation_calls = []
    failed_topic_uuid = None

    def generation_provider(**_kwargs):
        generation_calls.append(1)
        if len(generation_calls) == 1:
            return json.dumps(partial), {"total_tokens": 2}
        recovery = db_session.scalar(select(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == db_session.get(ConsultationSplitExecution, initial_id).batch_id,
            ConsultationSplitExecution.attempt_no == 2,
        ))
        assert recovery is not None
        recovery_response = json.loads(_response(db_session, recovery.id))
        assert failed_topic_uuid is not None
        recovery_response["notes"] = [
            note for note in recovery_response["notes"]
            if note["topic_uuid"] == failed_topic_uuid
        ]
        return json.dumps(recovery_response), {"total_tokens": 2}

    monkeypatch.setattr("app.services.consultation_split_generation_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr("app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm", generation_provider)
    assert _run(db_session, initial_id).outcome == "ready"
    initial, batch, initial_attempt, outcomes = _rows(db_session, initial_id)
    survivor = next(outcome for outcome in outcomes if outcome.status is ConsultationSplitTopicOutcomeStatus.validated)
    failed_topic_uuid = str(next(
        outcome for outcome in outcomes if outcome.status is ConsultationSplitTopicOutcomeStatus.failed
    ).batch_topic.topic_uuid)
    accepted_ciphertext = survivor.output_encrypted
    executions = db_session.scalars(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id).order_by(ConsultationSplitExecution.attempt_no)).all()
    assert [item.kind.value for item in executions] == ["generation", "generation"]
    assert [item.attempt_no for item in executions] == [1, 2]
    assert initial_attempt.status is AttemptStatus.settled
    assert len(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id.in_([item.id for item in executions]))).all()) == 2
    assert len(db_session.scalars(select(TaskDispatchOutbox).where(
        TaskDispatchOutbox.source_id.in_([item.id for item in executions]))).all()) == 2
    assert batch.status is ConsultationSplitBatchStatus.partially_ready
    assert batch.verification_status is ConsultationSplitVerificationStatus.pending

    recovery_id = executions[1].id
    recovery_result = _run(db_session, recovery_id)
    assert recovery_result.outcome == "ready", recovery_result
    db_session.expire_all()
    recovery, batch, recovery_attempt, outcomes = _rows(db_session, recovery_id)
    verification = db_session.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind == "verification",
    ))
    assert generation_calls == [1, 1]
    assert verification is not None, (batch.status, batch.verification_status, batch.verification_reason)
    assert recovery.status is ConsultationSplitExecutionStatus.completed
    assert recovery_attempt.status is AttemptStatus.settled
    assert batch.status is ConsultationSplitBatchStatus.verifying
    assert batch.verification_status is ConsultationSplitVerificationStatus.verifying
    assert next(outcome for outcome in outcomes if outcome.output_encrypted == accepted_ciphertext).output_encrypted == accepted_ciphertext

    # The recovery and its verifier were both queued under the enabled gate.
    # Closing either gate now may not strand their already durable survivor set.
    _disable_split_gate(db_session, owner, monkeypatch, gate)
    verification_calls = []
    verification_id = verification.id
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.resolve_generation_credential", lambda _config: "token")
    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (verification_calls.append(1) or (_verification_response(db_session, verification_id), {"total_tokens": 2})),
    )
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    db_session.expire_all()
    batch = db_session.get(ConsultationSplitBatch, batch.id)
    projection = read_workspace_split_batch(db_session, owner, transcript_id=transcript.id)
    execution_ids = db_session.scalars(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.batch_id == batch.id)).all()
    assert verification_calls == [1]
    assert len(execution_ids) == 3
    assert len(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id.in_(execution_ids))).all()) == 3
    assert len(db_session.scalars(select(TaskDispatchOutbox).where(
        TaskDispatchOutbox.source_id.in_(execution_ids))).all()) == 3
    assert batch.status is ConsultationSplitBatchStatus.ready
    assert batch.verification_status is ConsultationSplitVerificationStatus.verified
    assert len(db_session.scalars(select(GeneratedDocument).where(
        GeneratedDocument.transcript_id == transcript.id)).all()) == 2
    assert projection is not None
    assert projection.can_retry_missing is False and projection.can_keep_available is False


@pytest.mark.parametrize("gate", ["deployment", "preference"])
def test_manual_partial_actions_apply_effective_gate_before_root_lookup(
    db_session, make_user, monkeypatch, gate,
):
    """Disabled actions disclose neither a guessed root nor side effects."""
    owner = make_user(email=f"split-manual-gate-{gate}-{uuid4()}@example.com")
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false" if gate == "deployment" else "true")
    if gate == "preference":
        db_session.add(UserAppPreference(
            user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": False},
        ))
        db_session.commit()
    counts = (
        db_session.scalar(select(func.count()).select_from(ProviderAttempt)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox)),
    )
    for action in (retry_missing_split_notes, keep_available_split_notes):
        with pytest.raises(AppError) as raised:
            action(db_session, owner, transcript_id=uuid4(), batch_id=uuid4())
        assert raised.value.status_code == 403
        assert raised.value.code == "consultation_split_disabled"
        db_session.rollback()
    assert counts == (
        db_session.scalar(select(func.count()).select_from(ProviderAttempt)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox)),
    )


def test_checker_config_rotation_between_credential_read_and_submit_fails_open_without_call(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-checker-rotation-{uuid4()}@example.com")
    _transcript, generation_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, structured_only=True)
    config = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id)).config
    db_session.add(TeamHallucinationCheckSelection(team_id=owner.team_id, llm_config_id=config.id, selected_by_user_id=owner.id))
    db_session.commit()
    _provider(monkeypatch, _response(db_session, generation_id), [])
    assert _run(db_session, generation_id).outcome == "ready"
    verification = db_session.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == db_session.get(ConsultationSplitExecution, generation_id).batch_id,
        ConsultationSplitExecution.kind == "verification",
    ))
    assert verification is not None
    verification_id = verification.id
    calls = []
    def rotate_after_read(_detached):
        live = db_session.get(TeamLlmConfig, config.id)
        live.vault_secret_ref = "rotated-ref"
        db_session.commit()
        return "token"
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.resolve_generation_credential", rotate_after_read)
    monkeypatch.setattr("app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm", lambda **_kwargs: calls.append(1))
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=verification_id)
    assert calls == []
    db_session.expire_all()
    execution = db_session.get(ConsultationSplitExecution, verification_id)
    batch = db_session.get(ConsultationSplitBatch, execution.batch_id)
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
    assert batch.verification_reason == "verification_provider_config_invalid"


def test_checker_quota_rejection_does_not_unwind_complete_generation_materialization(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-checker-quota-{uuid4()}@example.com")
    transcript, generation_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, structured_only=True)
    config = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id)).config
    db_session.add(TeamHallucinationCheckSelection(team_id=owner.team_id, llm_config_id=config.id, selected_by_user_id=owner.id))
    owner.daily_token_limit = owner.monthly_token_limit = 0
    db_session.commit()
    provider_calls = []
    _provider(monkeypatch, _response(db_session, generation_id), provider_calls)
    assert _run(db_session, generation_id).outcome == "ready"
    batch_id = db_session.get(ConsultationSplitExecution, generation_id).batch_id
    assert batch_id is not None
    # Check from a new identity map: the failed nested checker reservation must
    # not be hidden by stale execution/attempt objects from the generation run.
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    with factory() as verification:
        execution = verification.get(ConsultationSplitExecution, generation_id)
        batch = verification.get(ConsultationSplitBatch, batch_id)
        assert execution is not None and batch is not None
        execution_ids = verification.scalars(select(ConsultationSplitExecution.id).where(
            ConsultationSplitExecution.batch_id == batch_id,
        )).all()
        attempts = verification.scalars(select(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id.in_(execution_ids),
        )).all()
        outboxes = verification.scalars(select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
            TaskDispatchOutbox.source_id.in_(execution_ids),
        )).all()
        usage = verification.scalars(select(ProviderUsageEvent).where(
            ProviderUsageEvent.consultation_split_execution_id.in_(execution_ids),
        )).all()
        assert execution_ids == [generation_id]
        assert len(attempts) == 1 and attempts[0].status is AttemptStatus.settled
        assert len(outboxes) == 1 and outboxes[0].source_id == generation_id
        assert len(usage) == 1 and usage[0].consultation_split_execution_id == generation_id
        assert batch.status is ConsultationSplitBatchStatus.ready
        assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
        assert batch.verification_reason == "verification_quota_unavailable"
        assert len(verification.scalars(select(GeneratedDocument).where(
            GeneratedDocument.transcript_id == transcript.id,
        )).all()) == 2
    assert provider_calls == [1]  # The optional checker made no provider call.


def test_checker_quota_rejection_preserves_partial_survivors_for_keep(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    owner = make_user(email=f"generation-checker-quota-partial-{uuid4()}@example.com")
    transcript, generation_id = _queue(db_session, owner, make_llm_config, make_llm_selection, make_template,
        monkeypatch, structured_only=True)
    config = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == owner.team_id)).config
    db_session.add(TeamHallucinationCheckSelection(team_id=owner.team_id, llm_config_id=config.id, selected_by_user_id=owner.id))
    owner.daily_token_limit = owner.monthly_token_limit = 0
    db_session.commit()
    partial_response = json.loads(_response(db_session, generation_id))
    partial_response["notes"][0]["content"] = "not structured"
    provider_calls = []
    _provider(monkeypatch, json.dumps(partial_response), provider_calls)
    # The test isolates the optional checker reservation from the separate,
    # already-covered automatic-recovery queue reservation.
    monkeypatch.setattr("app.services.consultation_split_recovery.queue_automatic_split_recovery", lambda *_args, **_kwargs: None)
    result = _run(db_session, generation_id)
    row, current_batch, current_attempt, _ = _rows(db_session, generation_id)
    assert result.outcome == "ready", (row.status, current_batch.status, current_attempt.status)
    assert provider_calls == [1]
    batch_id = db_session.get(ConsultationSplitExecution, generation_id).batch_id
    assert batch_id is not None
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    with factory() as verification:
        owner_in_verification = verification.get(User, owner.id)
        execution = verification.get(ConsultationSplitExecution, generation_id)
        batch = verification.get(ConsultationSplitBatch, batch_id)
        assert owner_in_verification is not None and execution is not None and batch is not None
        execution_ids = verification.scalars(select(ConsultationSplitExecution.id).where(
            ConsultationSplitExecution.batch_id == batch_id,
        )).all()
        attempts = verification.scalars(select(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id.in_(execution_ids),
        )).all()
        outboxes = verification.scalars(select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
            TaskDispatchOutbox.source_id.in_(execution_ids),
        )).all()
        usage = verification.scalars(select(ProviderUsageEvent).where(
            ProviderUsageEvent.consultation_split_execution_id.in_(execution_ids),
        )).all()
        outcomes = verification.scalars(select(ConsultationSplitTopicOutcome).where(
            ConsultationSplitTopicOutcome.transcript_id == transcript.id,
        )).all()
        projection = read_workspace_split_batch(
            verification, owner_in_verification, transcript_id=transcript.id,
        )
        assert execution.status is ConsultationSplitExecutionStatus.completed
        assert execution_ids == [generation_id]
        assert len(attempts) == 1 and attempts[0].status is AttemptStatus.settled
        assert len(outboxes) == 1 and outboxes[0].source_id == generation_id
        assert len(usage) == 1 and usage[0].consultation_split_execution_id == generation_id
        assert batch.status is ConsultationSplitBatchStatus.partially_ready
        assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
        assert batch.verification_reason == "verification_quota_unavailable"
        assert {outcome.status for outcome in outcomes} == {
            ConsultationSplitTopicOutcomeStatus.validated,
            ConsultationSplitTopicOutcomeStatus.failed,
        }
        assert projection is not None
        assert projection.can_keep_available is True
        assert projection.can_retry_missing is True
        assert verification.scalars(select(GeneratedDocument).where(
            GeneratedDocument.transcript_id == transcript.id,
        )).all() == []
    assert provider_calls == [1]  # The optional checker made no provider call.


@pytest.mark.real_db_connections
@pytest.mark.parametrize("gate", ["deployment", "preference"])
def test_verifier_deadline_wins_over_late_provider_response_without_a_second_call_or_settlement(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, gate,
):
    """A late verifier reply cannot revive lifecycle's fail-open decision."""
    owner = make_user(email=f"verification-timeout-{uuid4()}@example.com")
    transcript, execution_id = _queue_selected_verification(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
    )
    response = _verification_response(db_session, execution_id)
    _verification, initial_batch, _initial_attempt, _outcomes = _rows(db_session, execution_id)
    counts_before = (
        db_session.scalar(select(func.count()).select_from(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == initial_batch.id)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_id == execution_id)),
        db_session.scalar(select(func.count()).select_from(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == execution_id)),
    )
    started = Event()
    release = Event()
    calls: list[int] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.resolve_generation_credential",
        lambda _config: "token",
    )

    def provider(**_kwargs):
        calls.append(1)
        started.set()
        assert release.wait(timeout=10)
        return response, {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}

    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm", provider,
    )
    factory = sessionmaker(bind=db_session.get_bind().engine, autoflush=False, future=True)

    def run_worker():
        with factory() as worker:
            try:
                process_consultation_split_verification_execution(worker, execution_id=execution_id)
            except BaseException as exc:  # Keep failures visible after releasing the provider barrier.
                errors.append(exc)
                worker.rollback()

    worker = Thread(target=run_worker)
    worker.start()
    try:
        assert started.wait(timeout=10)
        with factory() as duplicate_worker:
            process_consultation_split_verification_execution(
                duplicate_worker, execution_id=execution_id,
            )
        assert calls == [1]

        deadline = utcnow() + timedelta(seconds=1)
        _disable_split_gate(db_session, owner, monkeypatch, gate)
        with factory() as lifecycle:
            attempt = lifecycle.scalar(select(ProviderAttempt).where(
                ProviderAttempt.consultation_split_execution_id == execution_id,
            ))
            assert attempt is not None
            attempt.deadline_at = deadline
            lifecycle.commit()
            assert process_quota_lifecycle(lifecycle, now=deadline) == 2

        db_session.expire_all()
        execution, batch, attempt = _assert_gate_closed_verifier_fail_open_result(
            db_session, owner, transcript, execution_id,
            expected_reason=PROVIDER_ATTEMPT_OUTCOME_UNKNOWN,
            expected_attempt_outcome=AttemptOutcome.unknown,
        )
        _execution, _batch, _attempt, outcomes = _rows(db_session, execution_id)
        assert attempt.status is AttemptStatus.settled and attempt.outcome is AttemptOutcome.unknown
        assert execution.status is ConsultationSplitExecutionStatus.failed
        assert execution.error_code == PROVIDER_ATTEMPT_OUTCOME_UNKNOWN
        assert execution.recoverable_response_encrypted is None
        assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
        assert batch.status is ConsultationSplitBatchStatus.ready
        assert all(outcome.status is ConsultationSplitTopicOutcomeStatus.ready for outcome in outcomes)
        assert len(db_session.scalars(select(GeneratedDocument).where(
            GeneratedDocument.transcript_id == transcript.id,
        )).all()) == len(outcomes)
        assert counts_before == (
            db_session.scalar(select(func.count()).select_from(ConsultationSplitExecution).where(
                ConsultationSplitExecution.batch_id == batch.id)),
            db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox).where(
                TaskDispatchOutbox.source_id == execution_id)),
            db_session.scalar(select(func.count()).select_from(ProviderAttempt).where(
                ProviderAttempt.consultation_split_execution_id == execution_id)),
        )
        terminal_attempt = (
            attempt.status, attempt.outcome, attempt.settlement_basis, attempt.settled_units,
            attempt.settled_at, attempt.reported_total_tokens,
        )
        terminal_usage_count = len(db_session.scalars(select(ProviderUsageEvent).where(
            ProviderUsageEvent.consultation_split_execution_id == execution_id,
        )).all())
    finally:
        release.set()
        worker.join(10)

    assert not worker.is_alive()
    assert errors == []
    db_session.expire_all()
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    assert (
        attempt.status, attempt.outcome, attempt.settlement_basis, attempt.settled_units,
        attempt.settled_at, attempt.reported_total_tokens,
    ) == terminal_attempt
    assert execution.status is ConsultationSplitExecutionStatus.failed
    assert execution.error_code == PROVIDER_ATTEMPT_OUTCOME_UNKNOWN
    assert execution.recoverable_response_encrypted is None
    assert batch.verification_status is ConsultationSplitVerificationStatus.unchecked
    assert all(outcome.status is ConsultationSplitTopicOutcomeStatus.ready for outcome in outcomes)
    assert len(db_session.scalars(select(ProviderUsageEvent).where(
        ProviderUsageEvent.consultation_split_execution_id == execution_id,
    )).all()) == terminal_usage_count
    assert calls == [1]
    for action in (keep_available_split_notes, retry_missing_split_notes):
        with pytest.raises(AppError) as raised:
            action(db_session, owner, transcript_id=transcript.id, batch_id=batch.id)
        assert raised.value.code == "consultation_split_disabled"
        db_session.rollback()


def test_verifier_recoverable_response_is_cascaded_by_expired_transcript_without_recall_or_leak(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, caplog,
):
    owner = make_user(email=f"verification-retention-{uuid4()}@example.com")
    transcript, execution_id = _queue_selected_verification(
        db_session, owner, make_llm_config, make_llm_selection, make_template, monkeypatch,
    )
    execution, batch, attempt, outcomes = _rows(db_session, execution_id)
    response_marker = "synthetic-verifier-recoverable-response"
    mark_provider_attempt_submitted(
        db_session, attempt_id=attempt.id, deadline_at=utcnow() + timedelta(minutes=10),
    )
    execution.status = ConsultationSplitExecutionStatus.processing
    execution.recoverable_response_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_executions",
        field="recoverable_response_encrypted", record_id=execution.id,
        plaintext={"text": response_marker, "usage": {"total_tokens": 5}},
    )
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert execution.recoverable_response_encrypted is not None
    assert response_marker not in execution.recoverable_response_encrypted
    batch_id, attempt_id, outcome_ids, transcript_id = (
        batch.id, attempt.id, [outcome.id for outcome in outcomes], transcript.id,
    )

    assert delete_expired_transcripts(db_session, now=utcnow()) == 1
    db_session.expire_all()
    retained_attempt = db_session.get(ProviderAttempt, attempt_id)
    assert db_session.get(Transcript, transcript_id) is None
    assert db_session.get(ConsultationSplitExecution, execution_id) is None
    assert db_session.get(ConsultationSplitBatch, batch_id) is None
    assert all(db_session.get(ConsultationSplitTopicOutcome, outcome_id) is None for outcome_id in outcome_ids)
    assert retained_attempt is not None
    assert retained_attempt.status is AttemptStatus.settled
    assert retained_attempt.outcome is AttemptOutcome.unknown
    assert retained_attempt.transcript_id is None
    assert retained_attempt.consultation_split_execution_id is None

    calls: list[int] = []
    monkeypatch.setattr(
        "app.services.consultation_split_verification_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: calls.append(1),
    )
    db_session.rollback()
    process_consultation_split_verification_execution(db_session, execution_id=execution_id)
    assert calls == []
    assert response_marker not in caplog.text
