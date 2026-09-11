from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitBatchTopic,
    ConsultationSplitDraft,
    ConsultationSplitDraftStatus,
    ConsultationSplitDraftTopic,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    ConsultationSplitTopicOutcome,
    ConsultationSplitTopicDisposition,
    GeneratedDocument,
    ProviderAttempt,
    ProviderFeatureType,
    ProviderUsageEvent,
    ProviderUsageEventType,
    TaskDispatchKind,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    TeamLlmConfig,
    QuotaResource,
    PromptTemplateVersion,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services.consultation_splits import (
    create_split_analysis,
    _create_split_analysis_execution_for_test,
    create_split_batch,
    create_split_batch_topic,
    _create_split_batch_execution_for_test,
    create_split_draft,
    create_split_draft_topic,
    create_split_topic_outcome,
    cancel_unpublished_split_analysis_work,
    queue_split_execution as _queue_split_execution,
    read_split_analysis_json,
    read_split_batch_json,
    read_split_batch_topic_template_snapshot,
    read_split_execution_json,
    read_split_topic_outcome_output,
    read_split_topic_title,
    reconcile_split_draft_staleness,
    require_split_owner_transcript,
    split_execution_phase_mapping,
    validate_queued_split_execution_for_runtime,
)
from app.services.content_crypto import (
    decrypt_json_for_owner,
    encrypt_json_for_owner,
    encrypt_text_for_owner,
    is_encrypted_envelope,
)
from app.services.transcripts import delete_expired_transcripts, delete_transcripts
from app.services.templates import delete_generated_document
from app.services.admin import delete_team, delete_user
from app.services.llm import delete_llm_config
from app.services.quota_lifecycle import process_quota_lifecycle
from app.services.quotas import mark_provider_attempt_submitted, reserve_provider_attempt


FINGERPRINT = "a" * 64


def _test_llm_config(db, owner):
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == owner.team_id))
    if config is not None:
        return config
    config = TeamLlmConfig(
        team_id=owner.team_id,
        label=f"Split test LLM {owner.team_id}",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        available_models_json=[],
        inspection_metadata_json={},
        provider_config_json={},
        vault_secret_ref=f"test:split/{uuid4()}",
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db.add(config)
    db.flush()
    return config


def _test_provider_snapshot(config, *, adapter=None, model=None):
    return {
        "llm_config_id": str(config.id),
        "provider_preset": str(getattr(config.provider_preset, "value", config.provider_preset)),
        "adapter_kind": adapter or str(getattr(config.adapter_kind, "value", config.adapter_kind)),
        "base_url": config.base_url,
        "model": model or config.model_name,
        "auth_mode": str(getattr(config.auth_mode, "value", config.auth_mode)),
        "provider_config": {},
    }


def _test_request_payload(config, *, model=None):
    return {"model": model or config.model_name, "max_completion_tokens": 1, "messages": []}


def queue_split_execution(db, owner, **kwargs):
    """Existing queue tests use a real same-team config without hiding the API rule.

    Focused tests below call ``_queue_split_execution`` directly to exercise
    missing, foreign-team, snapshot, and deletion cases.
    """
    config = _test_llm_config(db, owner)
    kwargs.setdefault("llm_config_id", config.id)
    kwargs.setdefault(
        "provider_snapshot",
        _test_provider_snapshot(
            config,
            adapter=kwargs.get("provider_adapter"),
            model=kwargs.get("provider_model"),
        ),
    )
    kwargs.setdefault("request_payload", _test_request_payload(config, model=kwargs.get("provider_model")))
    return _queue_split_execution(db, owner, **kwargs)


def _transcript(db, owner, *, expires_at=None):
    row = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=expires_at or utcnow() + timedelta(days=30),
    )
    db.add(row)
    db.flush()
    return row


def _tree(db, owner, make_template, *, fingerprint=FINGERPRINT, transcript=None):
    transcript = transcript or _transcript(db, owner)
    materialization_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="",
    )
    db.add(materialization_version)
    db.flush()
    template = make_template(owner=owner, actor=owner)
    version = db.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id))
    analysis = create_split_analysis(
        db, owner, transcript_id=transcript.id, source_fingerprint=fingerprint,
        source_snapshot={"transcript": "synthetic source"}, candidate_template_snapshot={"templates": [str(template.id)]},
        provider_snapshot={"provider": "synthetic"}, proposal={"topics": ["Primary synthetic topic"]},
    )
    draft = create_split_draft(db, owner, analysis=analysis)
    draft_topic = create_split_draft_topic(
        db, owner, draft=draft, title="Primary synthetic topic", topic_order=0, is_primary=True,
        disposition=ConsultationSplitTopicDisposition.separate_note, template_id=template.id, template_version_id=version.id,
    )
    batch = create_split_batch(
        db, owner, analysis=analysis, confirmed_plan={"topics": [str(draft_topic.topic_uuid)]},
        clinical_snapshot={"hints": []}, source_snapshot={"source": "synthetic source"},
        template_snapshot={"template": "Synthetic"}, pii_snapshot={"manual": []},
        provider_snapshot={"provider": "synthetic"}, note_options_snapshot={"detail": "normal"},
        materialization_transcript_version_id=materialization_version.id,
    )
    batch_topic = create_split_batch_topic(
        db, owner, batch=batch, title="Primary synthetic topic", topic_order=0, is_primary=True,
        disposition=ConsultationSplitTopicDisposition.separate_note, template_snapshot={"template": "Synthetic"}, topic_uuid=draft_topic.topic_uuid,
    )
    outcome = create_split_topic_outcome(db, owner, batch_topic=batch_topic, output={"text": "synthetic output"})
    execution = _create_split_analysis_execution_for_test(
        db, owner, analysis=analysis, attempt_no=1, provider_snapshot={"provider": "synthetic"},
        request_payload={"request": "synthetic request"}, recoverable_response={"response": "synthetic response"},
    )
    db.commit()
    return transcript, analysis, draft, draft_topic, batch, batch_topic, outcome, execution


def _intent_row(db, owner, transcript, *, analysis=None, client_idempotency_key=None, record_id=None):
    """Persist a direct fixture only; production intent creation is a later slice."""
    intent_id = record_id or uuid4()
    row = ConsultationSplitIntent(
        id=intent_id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        transcript_id=transcript.id,
        analysis_id=analysis.id if analysis is not None else None,
        client_idempotency_key=client_idempotency_key or uuid4(),
        status=ConsultationSplitIntentStatus.analysis_pending,
        retention_expires_at=transcript.retention_expires_at,
    )
    snapshot = {
        "selected_template": {
            "id": str(uuid4()),
            "version": 7,
            "name": "Synthetic private template",
            "prompt": "Do not store this in plaintext",
        },
        "generation_configuration": {"detail": "normal"},
    }
    row.generation_snapshot_encrypted = encrypt_json_for_owner(
        db,
        owner_user_id=owner.id,
        table="consultation_split_intents",
        field="generation_snapshot_encrypted",
        record_id=row.id,
        plaintext=snapshot,
    ) or ""
    db.add(row)
    db.flush()
    return row, snapshot


def test_passive_split_service_encrypts_every_content_slot_and_preserves_retention(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, draft, draft_topic, batch, batch_topic, outcome, execution = _tree(db_session, owner, make_template)

    encrypted_values = [
        analysis.source_snapshot_encrypted, analysis.candidate_template_snapshot_encrypted,
        analysis.provider_snapshot_encrypted, analysis.proposal_encrypted, draft_topic.title_encrypted,
        batch.confirmed_plan_encrypted, batch.clinical_snapshot_encrypted, batch.source_snapshot_encrypted,
        batch.template_snapshot_encrypted, batch.pii_snapshot_encrypted, batch.provider_snapshot_encrypted,
        batch.note_options_snapshot_encrypted, batch_topic.title_encrypted, batch_topic.template_snapshot_encrypted,
        outcome.output_encrypted, execution.provider_snapshot_encrypted, execution.request_payload_encrypted,
        execution.recoverable_response_encrypted,
    ]
    assert all(is_encrypted_envelope(value) for value in encrypted_values)
    assert all("synthetic source" not in value for value in encrypted_values if value)
    batch_execution = _create_split_batch_execution_for_test(
        db_session, owner, batch=batch, kind=ConsultationSplitExecutionKind.generation, attempt_no=1,
        provider_snapshot={"provider": "synthetic"}, request_payload={"request": "bundle"},
        recoverable_response={"response": "bundle"},
    )
    assert read_split_analysis_json(db_session, owner, analysis=analysis, field="proposal_encrypted") == {"topics": ["Primary synthetic topic"]}
    assert read_split_batch_json(db_session, owner, batch=batch, field="confirmed_plan_encrypted") == {"topics": [str(draft_topic.topic_uuid)]}
    assert read_split_batch_topic_template_snapshot(db_session, owner, topic=batch_topic) == {"template": "Synthetic"}
    assert read_split_topic_outcome_output(db_session, owner, outcome=outcome) == {"text": "synthetic output"}
    assert read_split_execution_json(db_session, owner, execution=execution, field="request_payload_encrypted") == {"request": "synthetic request"}
    assert read_split_execution_json(db_session, owner, execution=batch_execution, field="request_payload_encrypted") == {"request": "bundle"}
    assert read_split_topic_title(db_session, owner, topic=draft_topic) == "Primary synthetic topic"
    assert read_split_topic_title(db_session, owner, topic=batch_topic) == "Primary synthetic topic"
    for row in (analysis, draft, draft_topic, batch, batch_topic, outcome, execution):
        assert row.retention_expires_at == transcript.retention_expires_at


def test_split_intent_persists_only_encrypted_selection_snapshot_and_is_root_deleted(
    db_session, make_user,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint=FINGERPRINT,
    )
    key = uuid4()
    intent, snapshot = _intent_row(
        db_session,
        owner,
        transcript,
        analysis=analysis,
        client_idempotency_key=key,
    )
    second_intent, _ = _intent_row(db_session, owner, transcript, analysis=analysis)
    db_session.commit()

    assert second_intent.analysis_id == analysis.id
    assert intent.generated_document_id is None
    assert intent.generated_document is None
    assert intent.status is ConsultationSplitIntentStatus.analysis_pending
    assert is_encrypted_envelope(intent.generation_snapshot_encrypted)
    assert "Synthetic private template" not in intent.generation_snapshot_encrypted
    assert decrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_intents",
        field="generation_snapshot_encrypted",
        record_id=intent.id,
        stored_value=intent.generation_snapshot_encrypted,
    ) == snapshot

    duplicate_id = uuid4()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _intent_row(
                db_session,
                owner,
                transcript,
                analysis=analysis,
                client_idempotency_key=key,
                record_id=duplicate_id,
            )
    assert db_session.get(ConsultationSplitIntent, duplicate_id) is None

    intent_id = intent.id
    db_session.delete(analysis)
    db_session.commit()
    db_session.expire_all()
    retained_intent = db_session.get(ConsultationSplitIntent, intent_id)
    assert retained_intent is not None
    assert retained_intent.analysis_id is None

    delete_transcripts(db_session, owner, transcript_ids=[transcript.id])
    assert db_session.get(ConsultationSplitIntent, intent_id) is None


def test_bypassed_intent_has_one_document_link_without_copying_document_content_and_document_delete_keeps_consumption(
    db_session, make_user, make_generated_document,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext="synthetic source",
        ) or "",
    )
    db_session.add(version)
    db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    intent, snapshot = _intent_row(db_session, owner, transcript)
    intent.status = ConsultationSplitIntentStatus.bypassed
    intent.generated_document = document
    db_session.commit()

    assert intent.generated_document_id == document.id
    assert document.consultation_split_intent is intent
    assert document.title not in intent.generation_snapshot_encrypted
    assert decrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_intents",
        field="generation_snapshot_encrypted",
        record_id=intent.id,
        stored_value=intent.generation_snapshot_encrypted,
    ) == snapshot

    second_intent, _ = _intent_row(db_session, owner, transcript)
    second_intent.status = ConsultationSplitIntentStatus.bypassed
    second_intent.generated_document_id = document.id
    document_id = document.id
    intent_id = intent.id
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.flush()
    db_session.rollback()

    delete_generated_document(db_session, owner, generated_document_id=document_id)
    retained = db_session.get(ConsultationSplitIntent, intent_id)
    assert retained is not None
    assert retained.status is ConsultationSplitIntentStatus.bypassed
    assert retained.generated_document_id is None


def test_transcript_cascade_removes_bypassed_intent_and_bound_document(
    db_session, make_user, make_generated_document,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext="synthetic source",
        ) or "",
    )
    db_session.add(version)
    db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    intent, _ = _intent_row(db_session, owner, transcript)
    intent.status = ConsultationSplitIntentStatus.bypassed
    intent.generated_document_id = document.id
    db_session.commit()

    intent_id, document_id = intent.id, document.id
    delete_transcripts(db_session, owner, transcript_ids=[transcript.id])
    assert db_session.get(ConsultationSplitIntent, intent_id) is None
    assert db_session.get(GeneratedDocument, document_id) is None


def test_split_scope_and_aad_fail_closed(db_session, make_user, make_team, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    other = make_user(email=f"split-other-{uuid4()}@example.com", team=make_team(name=f"Other {uuid4()}"))
    _, analysis, _, _, _, batch_topic, _, _ = _tree(db_session, owner, make_template)
    with pytest.raises(AppError) as forbidden:
        read_split_analysis_json(db_session, other, analysis=analysis, field="proposal_encrypted")
    assert forbidden.value.status_code == 403

    first = batch_topic.title_encrypted
    batch_topic.title_encrypted = analysis.proposal_encrypted
    with pytest.raises(AppError) as swapped:
        read_split_topic_title(db_session, owner, topic=batch_topic)
    assert swapped.value.status_code == 500
    batch_topic.title_encrypted = first

    batch_topic.team_id = other.team_id
    with pytest.raises(AppError) as corrupt:
        read_split_topic_title(db_session, owner, topic=batch_topic)
    assert corrupt.value.status_code == 500

    batch_topic.team_id = owner.team_id
    sibling = create_split_batch_topic(
        db_session, owner, batch=db_session.get(ConsultationSplitBatch, batch_topic.batch_id),
        title="Sibling topic", topic_order=1, is_primary=False,
        disposition=ConsultationSplitTopicDisposition.separate_note, template_snapshot={"template": "Synthetic"},
    )
    sibling.title_encrypted = batch_topic.title_encrypted
    with pytest.raises(AppError) as record_replay:
        read_split_topic_title(db_session, owner, topic=sibling)
    assert record_replay.value.status_code == 500

    _, _, _, _, _, other_topic, _, _ = _tree(db_session, other, make_template)
    other_topic.title_encrypted = batch_topic.title_encrypted
    with pytest.raises(AppError) as owner_replay:
        read_split_topic_title(db_session, other, topic=other_topic)
    assert owner_replay.value.status_code == 500

    system_admin = make_user(email=f"split-admin-{uuid4()}@example.com", is_system_admin=True)
    with pytest.raises(AppError) as admin_denied:
        require_split_owner_transcript(db_session, system_admin, transcript_id=analysis.transcript_id)
    assert admin_denied.value.status_code == 403


def test_split_draft_staleness_is_scoped_monotonic_and_fingerprints_are_canonical(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, draft, *_ = _tree(db_session, owner, make_template)
    reconcile_split_draft_staleness(db_session, owner, draft=draft, current_server_fingerprint="b" * 64)
    assert draft.status is ConsultationSplitDraftStatus.stale
    reconcile_split_draft_staleness(db_session, owner, draft=draft, current_server_fingerprint=analysis.source_fingerprint)
    assert draft.status is ConsultationSplitDraftStatus.stale
    with pytest.raises(AppError):
        create_split_analysis(db_session, owner, transcript_id=analysis.transcript_id, source_fingerprint="A" * 64)
    other = make_user(email=f"split-other-{uuid4()}@example.com", team=owner.team)
    with pytest.raises(AppError) as cross_owner:
        reconcile_split_draft_staleness(db_session, other, draft=draft, current_server_fingerprint=analysis.source_fingerprint)
    assert cross_owner.value.status_code == 403
    system_admin = make_user(email=f"split-admin-{uuid4()}@example.com", is_system_admin=True)
    with pytest.raises(AppError) as admin_denied:
        reconcile_split_draft_staleness(db_session, system_admin, draft=draft, current_server_fingerprint=analysis.source_fingerprint)
    assert admin_denied.value.status_code == 403


def test_split_analysis_refs_template_pairing_and_corrupt_lineage_fail_closed(
    db_session, make_user, make_template, make_redaction_run,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, draft, _, batch, _, _, _ = _tree(db_session, owner, make_template)
    other_transcript = _transcript(db_session, owner)
    next_version_no = (db_session.scalar(select(func.max(TranscriptVersion.version_no)).where(
        TranscriptVersion.transcript_id == transcript.id,
    )) or 0) + 1
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id, transcript_id=transcript.id, version_no=next_version_no,
        text_encrypted=encrypt_text_for_owner(db_session, owner_user_id=owner.id, table="transcript_versions",
            field="text_encrypted", record_id=version_id, plaintext="metadata") or "",
    )
    other_version_id = uuid4()
    other_version = TranscriptVersion(
        id=other_version_id, transcript_id=other_transcript.id, version_no=1,
        text_encrypted=encrypt_text_for_owner(db_session, owner_user_id=owner.id, table="transcript_versions",
            field="text_encrypted", record_id=other_version_id, plaintext="metadata") or "",
    )
    second_version_id = uuid4()
    second_version = TranscriptVersion(
        id=second_version_id, transcript_id=transcript.id, version_no=next_version_no + 1,
        text_encrypted=encrypt_text_for_owner(db_session, owner_user_id=owner.id, table="transcript_versions",
            field="text_encrypted", record_id=second_version_id, plaintext="metadata") or "",
    )
    db_session.add_all((version, other_version, second_version)); db_session.flush()
    run = make_redaction_run(transcript=transcript, transcript_version=version, owner=owner)
    with pytest.raises(AppError) as bad_version:
        create_split_analysis(db_session, owner, transcript_id=transcript.id, source_fingerprint="b" * 64,
            transcript_version_id=other_version.id)
    assert bad_version.value.status_code == 422
    with pytest.raises(AppError) as bad_run_pair:
        create_split_analysis(db_session, owner, transcript_id=transcript.id, source_fingerprint="c" * 64,
            transcript_version_id=second_version.id, redaction_run_id=run.id)
    assert bad_run_pair.value.status_code == 422

    first_template = make_template(owner=owner, actor=owner, name=f"First {uuid4()}")
    second_template = make_template(owner=owner, actor=owner, name=f"Second {uuid4()}")
    second_version = db_session.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == second_template.id))
    with pytest.raises(AppError) as mismatch:
        create_split_draft_topic(db_session, owner, draft=draft, title="Mismatch", topic_order=1, is_primary=False,
            disposition=ConsultationSplitTopicDisposition.separate_note, template_id=first_template.id,
            template_version_id=second_version.id)
    assert mismatch.value.status_code == 422
    second_template.is_active = False
    with pytest.raises(AppError) as unavailable:
        create_split_draft_topic(db_session, owner, draft=draft, title="Unavailable", topic_order=1, is_primary=False,
            disposition=ConsultationSplitTopicDisposition.separate_note, template_id=second_template.id,
            template_version_id=second_version.id)
    assert unavailable.value.status_code == 422

    original = batch.source_fingerprint
    batch.source_fingerprint = "d" * 64
    with pytest.raises(AppError) as corrupt:
        read_split_batch_json(db_session, owner, batch=batch, field="confirmed_plan_encrypted")
    assert corrupt.value.status_code == 500
    batch.source_fingerprint = original
    analysis.transcript_version_id = other_version.id
    with pytest.raises(AppError) as corrupt_analysis:
        read_split_analysis_json(db_session, owner, analysis=analysis, field="proposal_encrypted")
    assert corrupt_analysis.value.status_code == 500


def test_split_rows_keep_root_retention_snapshot_after_team_policy_changes(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    original_deadline = transcript.retention_expires_at
    owner.team.default_retention_days = 1
    db_session.commit()
    _, analysis, draft, draft_topic, batch, batch_topic, outcome, execution = _tree(
        db_session, owner, make_template, transcript=transcript,
    )
    for row in (analysis, draft, draft_topic, batch, batch_topic, outcome, execution):
        assert row.retention_expires_at == original_deadline


def test_analysis_delete_is_restricted_but_transcript_root_cascade_deletes_every_split_table(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, *_ = _tree(db_session, owner, make_template)
    db_session.delete(analysis)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    delete_transcripts(db_session, owner, transcript_ids=[transcript.id])
    for model in (
        ConsultationSplitAnalysis, ConsultationSplitIntent, ConsultationSplitDraft, ConsultationSplitDraftTopic, ConsultationSplitBatch,
        ConsultationSplitBatchTopic, ConsultationSplitTopicOutcome, ConsultationSplitExecution,
    ):
        assert db_session.scalar(select(model).where(model.transcript_id == transcript.id)) is None


def test_expired_full_split_graph_is_invisible_before_physical_cleanup_then_cascades(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, draft, draft_topic, batch, batch_topic, outcome, execution = _tree(db_session, owner, make_template)
    intent, _ = _intent_row(db_session, owner, transcript, analysis=analysis)
    split_row_ids = [(type(row), row.id) for row in (analysis, intent, draft, draft_topic, batch, batch_topic, outcome, execution)]
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(AppError) as expired:
        read_split_analysis_json(db_session, owner, analysis=analysis, field="proposal_encrypted")
    assert expired.value.status_code == 404
    assert delete_expired_transcripts(db_session, now=utcnow()) == 1
    assert db_session.get(Transcript, transcript.id) is None
    for model, row_id in split_row_ids:
        assert db_session.get(model, row_id) is None


def test_deleting_one_split_child_document_keeps_sibling_and_provenance(
    db_session, make_user, make_template, make_generated_document,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, _, _, _, batch, first_topic, outcome, _ = _tree(db_session, owner, make_template)
    second_topic = create_split_batch_topic(
        db_session, owner, batch=batch, title="Secondary synthetic topic", topic_order=1, is_primary=False,
        disposition=ConsultationSplitTopicDisposition.separate_note, template_snapshot={"template": "Synthetic"},
    )
    second_outcome = create_split_topic_outcome(db_session, owner, batch_topic=second_topic, output={"text": "second"})
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=(db_session.scalar(select(func.max(TranscriptVersion.version_no)).where(
            TranscriptVersion.transcript_id == transcript.id,
        )) or 0) + 1,
        text_encrypted=encrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
            record_id=version_id, plaintext="metadata",
        ) or "",
    )
    db_session.add(version)
    db_session.flush()
    first_document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    second_document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    first_document.consultation_split_batch_topic_id = first_topic.id
    first_document.consultation_split_topic_uuid = first_topic.topic_uuid
    second_document.consultation_split_batch_topic_id = second_topic.id
    second_document.consultation_split_topic_uuid = second_topic.topic_uuid
    db_session.commit()

    delete_generated_document(db_session, owner, generated_document_id=first_document.id)
    assert db_session.get(GeneratedDocument, first_document.id) is None
    assert db_session.get(GeneratedDocument, second_document.id) is not None
    assert db_session.get(ConsultationSplitBatch, batch.id) is not None
    assert db_session.get(ConsultationSplitBatchTopic, first_topic.id) is not None
    assert db_session.get(ConsultationSplitBatchTopic, second_topic.id) is not None
    assert db_session.get(ConsultationSplitTopicOutcome, outcome.id) is not None
    assert db_session.get(ConsultationSplitTopicOutcome, second_outcome.id) is not None


@pytest.mark.parametrize("delete_kind", ["user", "team"])
def test_user_and_team_deletion_cascade_passive_split_rows(db_session, make_user, make_template, delete_kind):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, *_ = _tree(db_session, owner, make_template)
    _intent_row(db_session, owner, transcript, analysis=analysis)
    db_session.commit()
    actor = (
        make_user(email=f"split-leader-{uuid4()}@example.com", team=owner.team, team_role="leader")
        if delete_kind == "user"
        else make_user(email=f"split-admin-{uuid4()}@example.com", is_system_admin=True)
    )
    if delete_kind == "user":
        delete_user(db_session, actor, owner.id)
    else:
        delete_team(db_session, actor, team_id=owner.team_id)
    for model in (
        ConsultationSplitAnalysis, ConsultationSplitIntent, ConsultationSplitDraft, ConsultationSplitDraftTopic, ConsultationSplitBatch,
        ConsultationSplitBatchTopic, ConsultationSplitTopicOutcome, ConsultationSplitExecution,
    ):
        assert db_session.scalar(select(model).where(model.transcript_id == transcript.id)) is None


def test_split_queue_creates_execution_attempt_and_dispatch_atomically_without_provider_work(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, _, _, batch, _, _, passive_execution = _tree(db_session, owner, make_template)
    valid_until = utcnow() + timedelta(minutes=10)
    execution, attempt, dispatch = queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=123,
        reservation_valid_until=valid_until,
    )
    assert execution.id != passive_execution.id
    assert execution.attempt_no == 2
    assert attempt.consultation_split_execution_id == execution.id
    assert attempt.correlation_id == execution.id
    assert attempt.attempt_number == 1
    assert attempt.attempt_kind is AttemptKind.consultation_split_analysis
    assert attempt.status is AttemptStatus.reserved
    assert dispatch.dispatch_kind is TaskDispatchKind.consultation_split_analysis
    assert dispatch.source_kind is TaskDispatchSourceKind.consultation_split_execution
    assert dispatch.source_id == execution.id
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is True
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is True
    assert db_session.get(ProviderAttempt, attempt.id).status is AttemptStatus.reserved
    assert db_session.get(ConsultationSplitExecution, execution.id).status.value == "queued"

    generation, generation_attempt, generation_dispatch = queue_split_execution(
        db_session,
        owner,
        batch=batch,
        kind=ConsultationSplitExecutionKind.generation,
        reserved_units=456,
        reservation_valid_until=valid_until,
    )
    assert generation_attempt.attempt_kind is AttemptKind.consultation_split_generation
    assert generation_dispatch.dispatch_kind is TaskDispatchKind.consultation_split_generation
    assert generation.id != execution.id
    assert split_execution_phase_mapping(ConsultationSplitExecutionKind.verification) == (
        AttemptKind.consultation_split_verification,
        ProviderFeatureType.consultation_split_verification,
        TaskDispatchKind.consultation_split_verification,
    )


def test_production_split_queue_requires_a_real_same_team_llm_config(
    db_session, make_user, make_team, make_template, make_llm_config,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    deadline = utcnow() + timedelta(minutes=10)

    with pytest.raises(AppError) as missing:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=deadline,
        )
    assert missing.value.code == "consultation_split_llm_config_required"

    other_team_owner = make_user(
        email=f"split-other-{uuid4()}@example.com",
        team=make_team(name=f"Split other team {uuid4()}"),
    )
    foreign_config = make_llm_config(team=other_team_owner.team)
    with pytest.raises(AppError) as foreign:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=deadline,
            llm_config_id=foreign_config.id,
        )
    assert foreign.value.code == "consultation_split_llm_config_invalid"
    assert db_session.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.analysis_id == analysis.id)) is not None


def test_split_execution_provider_snapshot_binds_to_config_and_rejects_secret_material(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    config = _test_llm_config(db_session, owner)
    deadline = utcnow() + timedelta(minutes=10)
    snapshot = {
        "llm_config_id": str(config.id),
        "provider_preset": "openai",
        "adapter_kind": "openai_chat",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "auth_mode": "bearer",
        "provider_config": {},
    }
    execution, _, _ = _queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1,
        reservation_valid_until=deadline,
        llm_config_id=config.id,
        provider_snapshot=snapshot,
        request_payload=_test_request_payload(config),
    )
    assert execution.llm_config_id == config.id
    assert read_split_execution_json(
        db_session, owner, execution=execution, field="provider_snapshot_encrypted"
    ) == snapshot

    mismatched = dict(snapshot, llm_config_id=str(uuid4()))
    with pytest.raises(AppError) as wrong_config:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=deadline,
            llm_config_id=config.id,
            provider_snapshot=mismatched,
        )
    assert wrong_config.value.code == "consultation_split_provider_snapshot_config_mismatch"

    with pytest.raises(AppError) as wrong_metadata:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=deadline,
            llm_config_id=config.id,
            provider_adapter="ollama_chat",
            provider_snapshot=snapshot,
        )
    assert wrong_metadata.value.code == "consultation_split_provider_snapshot_metadata_mismatch"

    secret_snapshot = dict(snapshot, provider_config={"api_key": "must-not-persist"})
    with pytest.raises(AppError) as secret:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=deadline,
            llm_config_id=config.id,
            provider_snapshot=secret_snapshot,
        )
    assert secret.value.code == "consultation_split_provider_snapshot_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_preset", "ollama"),
        ("adapter_kind", "ollama_chat"),
        ("auth_mode", "none"),
        ("base_url", "https://other-provider.example"),
        ("model", "different-model"),
    ],
)
def test_split_execution_provider_snapshot_rejects_forged_config_metadata(
    db_session, make_user, make_template, field, value,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    config = _test_llm_config(db_session, owner)
    snapshot = _test_provider_snapshot(config)
    snapshot[field] = value

    with pytest.raises(AppError) as mismatch:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=utcnow() + timedelta(minutes=10),
            llm_config_id=config.id,
            provider_snapshot=snapshot,
        )
    assert mismatch.value.code == "consultation_split_provider_snapshot_config_mismatch"


@pytest.mark.parametrize(
    "provider_config",
    [
        {"unexpected": "value"},
        {"project_id": {"nested": "value"}},
        {"project_id": ["value"]},
        {"project_id": "value", "api_key": "must-not-persist"},
    ],
)
def test_split_execution_provider_snapshot_rejects_invalid_provider_config(
    db_session, make_user, make_template, provider_config,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    config = _test_llm_config(db_session, owner)
    snapshot = _test_provider_snapshot(config)
    snapshot["provider_config"] = provider_config

    with pytest.raises(AppError) as invalid:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=utcnow() + timedelta(minutes=10),
            llm_config_id=config.id,
            provider_snapshot=snapshot,
        )
    assert invalid.value.code == "consultation_split_provider_snapshot_invalid"


def test_split_execution_provider_snapshot_rejects_unsafe_url(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    config = _test_llm_config(db_session, owner)
    snapshot = _test_provider_snapshot(config)
    snapshot["base_url"] = "http://metadata.google.internal"

    with pytest.raises(AppError) as invalid:
        _queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=utcnow() + timedelta(minutes=10),
            llm_config_id=config.id,
            provider_snapshot=snapshot,
        )
    assert invalid.value.code == "consultation_split_provider_snapshot_invalid"


@pytest.mark.parametrize(
    "active_status",
    [ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing],
)
def test_llm_config_delete_blocks_active_split_execution_but_terminal_execution_detaches(
    db_session, make_user, make_template, make_llm_config, active_status,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    config = make_llm_config(team=owner.team)
    admin = make_user(email=f"split-admin-{uuid4()}@example.com", is_system_admin=True)
    execution, _, _ = _queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1,
        reservation_valid_until=utcnow() + timedelta(minutes=10),
        llm_config_id=config.id,
        provider_snapshot=_test_provider_snapshot(config),
        request_payload=_test_request_payload(config),
    )
    execution.status = active_status
    db_session.commit()

    with pytest.raises(AppError) as active:
        delete_llm_config(db_session, admin, config_id=config.id, team_id=owner.team_id)
    assert active.value.code == "conflict"
    assert active.value.message == "Cannot delete this LLM config while dependent work is queued or processing"
    assert db_session.get(TeamLlmConfig, config.id) is not None

    execution.status = ConsultationSplitExecutionStatus.failed
    execution.completed_at = utcnow()
    db_session.commit()
    delete_llm_config(db_session, admin, config_id=config.id, team_id=owner.team_id)
    db_session.refresh(execution)
    assert execution.llm_config_id is None


def test_runtime_validation_rejects_missing_or_mismatched_provider_snapshot(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    execution, _, _ = queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1,
        reservation_valid_until=utcnow() + timedelta(minutes=10),
    )
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is True
    execution.provider_snapshot_encrypted = None
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False

    config = _test_llm_config(db_session, owner)
    execution.provider_snapshot_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_executions",
        field="provider_snapshot_encrypted",
        record_id=execution.id,
        plaintext=_test_provider_snapshot(config),
    )
    execution.request_payload_encrypted = None
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False

    canonical = _test_provider_snapshot(config)
    forged = dict(canonical, adapter_kind="ollama_chat")
    execution.provider_snapshot_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_executions",
        field="provider_snapshot_encrypted",
        record_id=execution.id,
        plaintext=forged,
    )
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False

    execution.provider_snapshot_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_executions",
        field="provider_snapshot_encrypted",
        record_id=execution.id,
        plaintext="not-json",
    )
    execution.llm_config_id = config.id
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False


def test_runtime_validation_rejects_expired_root_and_corrupt_parent_scope(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, *_ = _tree(db_session, owner, make_template)
    execution, _, _ = queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1,
        reservation_valid_until=utcnow() + timedelta(minutes=10),
    )
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False

    transcript.retention_expires_at = utcnow() + timedelta(days=30)
    execution.retention_expires_at = transcript.retention_expires_at
    other_transcript = _transcript(db_session, owner)
    other_analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=other_transcript.id,
        source_fingerprint="b" * 64,
        source_snapshot={"transcript": "other synthetic source"},
        candidate_template_snapshot={"templates": []},
        provider_snapshot={"provider": "synthetic"},
    )
    execution.analysis_id = other_analysis.id
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False


def test_split_queue_rolls_back_all_queue_rows_when_reservation_fails_but_outer_work_commits(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    owner.daily_token_limit = owner.monthly_token_limit = 0
    db_session.commit()
    before_executions = db_session.scalars(
        select(ConsultationSplitExecution).where(ConsultationSplitExecution.analysis_id == analysis.id)
    ).all()
    before_attempts = db_session.scalars(select(ProviderAttempt)).all()
    before_dispatches = db_session.scalars(
        select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution
        )
    ).all()
    analysis.error_code = "outer_transaction_continues"

    with pytest.raises(AppError) as quota_error:
        queue_split_execution(
            db_session,
            owner,
            analysis=analysis,
            kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1,
            reservation_valid_until=utcnow() + timedelta(minutes=10),
        )
    assert quota_error.value.code == "quota_disabled"
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(ConsultationSplitAnalysis, analysis.id).error_code == "outer_transaction_continues"
    assert db_session.scalars(
        select(ConsultationSplitExecution).where(ConsultationSplitExecution.analysis_id == analysis.id)
    ).all() == before_executions
    assert db_session.scalars(select(ProviderAttempt)).all() == before_attempts
    assert db_session.scalars(
        select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution
        )
    ).all() == before_dispatches


@pytest.mark.parametrize(
    ("execution_kind", "wrong_attempt_kind"),
    [
        (ConsultationSplitExecutionKind.analysis, AttemptKind.consultation_split_generation),
        (ConsultationSplitExecutionKind.analysis, AttemptKind.consultation_split_verification),
        (ConsultationSplitExecutionKind.generation, AttemptKind.consultation_split_analysis),
        (ConsultationSplitExecutionKind.generation, AttemptKind.consultation_split_verification),
        (ConsultationSplitExecutionKind.verification, AttemptKind.consultation_split_analysis),
        (ConsultationSplitExecutionKind.verification, AttemptKind.consultation_split_generation),
    ],
)
def test_split_attempt_reservation_rejects_every_wrong_execution_kind_mapping(
    db_session, make_user, make_template, execution_kind, wrong_attempt_kind,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, _, _, batch, *_ = _tree(db_session, owner, make_template)
    executions = {
        ConsultationSplitExecutionKind.analysis: db_session.scalar(
            select(ConsultationSplitExecution).where(ConsultationSplitExecution.analysis_id == analysis.id)
        ),
        ConsultationSplitExecutionKind.generation: _create_split_batch_execution_for_test(
            db_session, owner, batch=batch, kind=ConsultationSplitExecutionKind.generation, attempt_no=1,
        ),
        ConsultationSplitExecutionKind.verification: _create_split_batch_execution_for_test(
            db_session, owner, batch=batch, kind=ConsultationSplitExecutionKind.verification, attempt_no=1,
        ),
    }
    execution = executions[execution_kind]
    assert execution is not None

    with pytest.raises(AppError) as mismatch:
        reserve_provider_attempt(
            db_session,
            team_id=owner.team_id,
            owner_user_id=owner.id,
            resource=QuotaResource.tokens,
            attempt_kind=wrong_attempt_kind,
            correlation_id=execution.id,
            attempt_number=1,
            reserved_units=1,
            reservation_valid_until=utcnow() + timedelta(minutes=10),
            transcript_id=transcript.id,
            consultation_split_execution_id=execution.id,
        )
    assert mismatch.value.code == "provider_attempt_split_execution_kind_mismatch"
    assert db_session.scalar(
        select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution.id)
    ) is None


def test_split_runtime_validation_requires_matching_durable_dispatch(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    execution, _, dispatch = queue_split_execution(
        db_session, owner, analysis=analysis, kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1, reservation_valid_until=utcnow() + timedelta(minutes=10),
    )
    db_session.delete(dispatch)
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False


def test_split_runtime_validation_rejects_mismatched_durable_dispatch_kind(db_session, make_user, make_template):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    execution, _, dispatch = queue_split_execution(
        db_session, owner, analysis=analysis, kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1, reservation_valid_until=utcnow() + timedelta(minutes=10),
    )
    dispatch.dispatch_kind = TaskDispatchKind.consultation_split_generation
    db_session.flush()
    assert validate_queued_split_execution_for_runtime(db_session, execution_id=execution.id) is False


def test_split_usage_is_execution_scoped_and_completed_event_is_not_duplicated(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    execution, _, _ = queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=10,
        reservation_valid_until=utcnow() + timedelta(minutes=10),
    )
    event = ProviderUsageEvent(
        team_id=owner.team_id,
        owner_user_id=owner.id,
        transcript_id=execution.transcript_id,
        consultation_split_execution_id=execution.id,
        feature_type=ProviderFeatureType.consultation_split_analysis,
        event_type=ProviderUsageEventType.completed,
    )
    db_session.add(event)
    db_session.flush()
    duplicate = ProviderUsageEvent(
        team_id=owner.team_id,
        owner_user_id=owner.id,
        transcript_id=execution.transcript_id,
        consultation_split_execution_id=execution.id,
        feature_type=ProviderFeatureType.consultation_split_analysis,
        event_type=ProviderUsageEventType.completed,
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(duplicate)
            db_session.flush()


def test_preference_off_helper_cancels_only_unpublished_reserved_analysis(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    transcript, analysis, _, _, batch, *_ = _tree(db_session, owner, make_template)
    deadline = utcnow() + timedelta(minutes=10)
    analysis_execution, analysis_attempt, analysis_dispatch = queue_split_execution(
        db_session, owner, analysis=analysis, kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=10, reservation_valid_until=deadline,
    )
    generation_execution, generation_attempt, _ = queue_split_execution(
        db_session, owner, batch=batch, kind=ConsultationSplitExecutionKind.generation,
        reserved_units=10, reservation_valid_until=deadline,
    )
    assert cancel_unpublished_split_analysis_work(db_session, owner, transcript_id=transcript.id) == 1
    assert analysis_attempt.status is AttemptStatus.cancelled
    assert analysis_execution.status.value == "cancelled"
    assert analysis_dispatch.state.value == "cancelled"
    assert generation_attempt.status is AttemptStatus.reserved
    assert generation_execution.status.value == "queued"


def test_split_reservation_expiry_fails_analysis_but_keeps_verification_fail_open(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, _, _, batch, *_ = _tree(db_session, owner, make_template)
    expires_at = utcnow() + timedelta(seconds=1)
    analysis_execution, analysis_attempt, _ = queue_split_execution(
        db_session, owner, analysis=analysis, kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=10, reservation_valid_until=expires_at,
    )
    verification_execution, verification_attempt, _ = queue_split_execution(
        db_session, owner, batch=batch, kind=ConsultationSplitExecutionKind.verification,
        reserved_units=10, reservation_valid_until=expires_at,
    )
    assert process_quota_lifecycle(db_session, now=expires_at + timedelta(seconds=1)) >= 2
    assert analysis_attempt.status is AttemptStatus.cancelled
    assert analysis_execution.status.value == "failed"
    assert analysis.status.value == "failed"
    assert verification_attempt.status is AttemptStatus.cancelled
    assert verification_execution.status.value == "failed"
    assert batch.status.value == "generation_queued"


def _new_split_batch(db_session, owner, analysis):
    materialization_version = TranscriptVersion(
        transcript_id=analysis.transcript_id,
        version_no=(db_session.scalar(select(func.max(TranscriptVersion.version_no)).where(
            TranscriptVersion.transcript_id == analysis.transcript_id,
        )) or 0) + 1,
        text_encrypted="",
    )
    db_session.add(materialization_version)
    db_session.flush()
    return create_split_batch(
        db_session,
        owner,
        analysis=analysis,
        confirmed_plan={"topics": []},
        clinical_snapshot={"hints": []},
        source_snapshot={"source": "synthetic source"},
        template_snapshot={"template": "Synthetic"},
        pii_snapshot={"manual": []},
        provider_snapshot={"provider": "synthetic"},
        note_options_snapshot={"detail": "normal"},
        materialization_transcript_version_id=materialization_version.id,
    )


def test_split_failed_outbox_terminalizes_attempts_and_preserves_phase_parent_semantics(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, _, _, generation_batch, *_ = _tree(db_session, owner, make_template)
    verification_batch = _new_split_batch(db_session, owner, analysis)
    verification_batch.status = ConsultationSplitBatchStatus.verifying
    now = utcnow()
    analysis_execution, analysis_attempt, analysis_dispatch = queue_split_execution(
        db_session, owner, analysis=analysis, kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=1, reservation_valid_until=now + timedelta(minutes=10),
    )
    generation_execution, generation_attempt, generation_dispatch = queue_split_execution(
        db_session, owner, batch=generation_batch, kind=ConsultationSplitExecutionKind.generation,
        reserved_units=1, reservation_valid_until=now + timedelta(minutes=10),
    )
    verification_execution, verification_attempt, verification_dispatch = queue_split_execution(
        db_session, owner, batch=verification_batch, kind=ConsultationSplitExecutionKind.verification,
        reserved_units=1, reservation_valid_until=now + timedelta(minutes=10),
    )
    for dispatch in (analysis_dispatch, generation_dispatch, verification_dispatch):
        dispatch.state = TaskDispatchState.failed
        dispatch.failed_at = now
    db_session.commit()

    assert process_quota_lifecycle(db_session, now=now) >= 6
    assert all(attempt.status is AttemptStatus.cancelled for attempt in (
        analysis_attempt, generation_attempt, verification_attempt,
    ))
    assert all(execution.status.value == "failed" for execution in (
        analysis_execution, generation_execution, verification_execution,
    ))
    assert analysis.status.value == "failed"
    assert generation_batch.status.value == "failed"
    assert verification_batch.status is ConsultationSplitBatchStatus.verifying


def test_split_submitted_timeout_terminalizes_attempts_and_preserves_phase_parent_semantics(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, _, _, generation_batch, *_ = _tree(db_session, owner, make_template)
    verification_batch = _new_split_batch(db_session, owner, analysis)
    verification_batch.status = ConsultationSplitBatchStatus.verifying
    now = utcnow()
    executions_and_attempts = [
        queue_split_execution(
            db_session, owner, analysis=analysis, kind=ConsultationSplitExecutionKind.analysis,
            reserved_units=1, reservation_valid_until=now + timedelta(minutes=10),
        ),
        queue_split_execution(
            db_session, owner, batch=generation_batch, kind=ConsultationSplitExecutionKind.generation,
            reserved_units=1, reservation_valid_until=now + timedelta(minutes=10),
        ),
        queue_split_execution(
            db_session, owner, batch=verification_batch, kind=ConsultationSplitExecutionKind.verification,
            reserved_units=1, reservation_valid_until=now + timedelta(minutes=10),
        ),
    ]
    deadline = now + timedelta(seconds=1)
    for _, attempt, _ in executions_and_attempts:
        mark_provider_attempt_submitted(
            db_session, attempt_id=attempt.id, now=now, deadline_at=deadline,
        )
    db_session.commit()

    assert process_quota_lifecycle(db_session, now=deadline + timedelta(seconds=1)) >= 6
    assert all(attempt.status is AttemptStatus.settled and attempt.outcome is AttemptOutcome.unknown for _, attempt, _ in executions_and_attempts)
    assert all(execution.status.value == "failed" for execution, _, _ in executions_and_attempts)
    assert analysis.status.value == "failed"
    assert generation_batch.status.value == "failed"
    assert verification_batch.status is ConsultationSplitBatchStatus.verifying


@pytest.mark.parametrize("delete_kind", ["user", "team"])
def test_split_provider_metadata_follows_established_owner_and_team_deletion_rules(
    db_session, make_user, make_template, delete_kind,
):
    owner = make_user(email=f"split-owner-{uuid4()}@example.com")
    _, analysis, *_ = _tree(db_session, owner, make_template)
    execution, attempt, _ = queue_split_execution(
        db_session,
        owner,
        analysis=analysis,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=10,
        reservation_valid_until=utcnow() + timedelta(minutes=10),
    )
    usage = ProviderUsageEvent(
        team_id=owner.team_id,
        owner_user_id=owner.id,
        transcript_id=execution.transcript_id,
        consultation_split_execution_id=execution.id,
        feature_type=ProviderFeatureType.consultation_split_analysis,
        event_type=ProviderUsageEventType.failed,
    )
    db_session.add(usage)
    db_session.commit()
    attempt_id = attempt.id
    usage_id = usage.id
    actor = (
        make_user(email=f"split-leader-{uuid4()}@example.com", team=owner.team, team_role="leader")
        if delete_kind == "user"
        else make_user(email=f"split-admin-{uuid4()}@example.com", is_system_admin=True)
    )
    if delete_kind == "user":
        delete_user(db_session, actor, owner.id)
        retained_attempt = db_session.get(ProviderAttempt, attempt_id)
        retained_usage = db_session.get(ProviderUsageEvent, usage_id)
        assert retained_attempt is not None
        assert retained_attempt.consultation_split_execution_id is None
        assert retained_attempt.owner_user_id is None
        assert retained_usage is not None
        assert retained_usage.consultation_split_execution_id is None
        assert retained_usage.owner_user_id is None
    else:
        delete_team(db_session, actor, team_id=owner.team_id)
        assert db_session.get(ProviderAttempt, attempt_id) is None
        assert db_session.get(ProviderUsageEvent, usage_id) is None
