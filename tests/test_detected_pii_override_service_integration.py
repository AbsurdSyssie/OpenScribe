from datetime import timedelta
from uuid import uuid4

import pytest

from app.models import (
    DetectedPiiDismissal,
    RedactionEntity,
    RedactionRun,
    RedactionRunStatus,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis
from app.services.content_crypto import encrypt_text_for_owner
from app.errors import AppError
from app.services.redaction import dismiss_detected_pii_entity_service, effective_redaction_text_and_phi_index
from app.services.templates import process_generated_document, queue_document_generation_from_template


RAW_TEXT = "Alice reports a persistent cough."
REDACTED_TEXT = "[PHI-1] reports a persistent cough."


def _transcript_with_detected_pii(db, owner, *, raw_text=RAW_TEXT, redacted_text=REDACTED_TEXT, entity_value="Alice"):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic detected PII consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext=raw_text,
    )
    version = TranscriptVersion(id=uuid4(), transcript_id=transcript.id, version_no=1, text_encrypted="")
    version.text_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=version.id,
        plaintext=raw_text,
    )
    run = RedactionRun(
        id=uuid4(),
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=RedactionRunStatus.succeeded,
        redacted_text_encrypted="",
        mapping_hash="synthetic-detected-pii",
        entity_count=1,
        api_provider="native_presidio",
    )
    run.redacted_text_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="redaction_runs",
        field="redacted_text_encrypted",
        record_id=run.id,
        plaintext=redacted_text,
    )
    entity = RedactionEntity(
        id=uuid4(),
        redaction_run_id=run.id,
        entity_order=1,
        entity_type="PERSON",
        placeholder="[PHI-1]",
        original_value_encrypted="",
        normalized_value_hash="synthetic-alice",
        occurrence_count=1,
    )
    entity.original_value_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="redaction_entities",
        field="original_value_encrypted",
        record_id=entity.id,
        plaintext=entity_value,
    )
    db.add_all([transcript, version, run, entity])
    db.commit()
    return transcript, version, run, entity


def _successful_rerun(db, *, transcript, version, owner, redacted_text, entity_order, entity_value):
    run = RedactionRun(
        id=uuid4(),
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=RedactionRunStatus.succeeded,
        redacted_text_encrypted="",
        mapping_hash=f"rerun-{entity_order}",
        entity_count=1,
        api_provider="native_presidio",
    )
    run.redacted_text_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="redaction_runs",
        field="redacted_text_encrypted",
        record_id=run.id,
        plaintext=redacted_text,
    )
    entity = RedactionEntity(
        id=uuid4(),
        redaction_run_id=run.id,
        entity_order=entity_order,
        entity_type="PERSON",
        placeholder=f"[PHI-{entity_order}]",
        original_value_encrypted="",
        normalized_value_hash=f"rerun-{entity_order}",
        occurrence_count=1,
    )
    entity.original_value_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="redaction_entities",
        field="original_value_encrypted",
        record_id=entity.id,
        plaintext=entity_value,
    )
    db.add_all([run, entity])
    db.commit()
    db.refresh(run)
    return run, entity


def test_detected_pii_dismissal_carries_to_same_version_rerun_with_new_placeholder(db_session, make_user):
    owner = make_user(email="detected-pii-rerun-owner@example.com")
    transcript, version, _, entity = _transcript_with_detected_pii(db_session, owner)
    assert dismiss_detected_pii_entity_service(db_session, owner, transcript_id=transcript.id, entity_id=entity.id) is True
    rerun, rerun_entity = _successful_rerun(
        db_session,
        transcript=transcript,
        version=version,
        owner=owner,
        redacted_text="[PHI-9] reports a persistent cough.",
        entity_order=9,
        entity_value="Alice",
    )

    effective = effective_redaction_text_and_phi_index(db_session, run=rerun)

    assert effective.redacted_text == RAW_TEXT
    assert effective.phi_index == []
    assert effective.dismissed_entity_ids == frozenset({rerun_entity.id})
    assert effective.requires_review_dismissal_ids == frozenset()


def test_changed_overlapping_same_version_rerun_keeps_baseline_and_requires_review(db_session, make_user):
    owner = make_user(email="detected-pii-overlap-owner@example.com")
    transcript, version, _, entity = _transcript_with_detected_pii(db_session, owner)
    assert dismiss_detected_pii_entity_service(db_session, owner, transcript_id=transcript.id, entity_id=entity.id) is True
    rerun, _ = _successful_rerun(
        db_session,
        transcript=transcript,
        version=version,
        owner=owner,
        redacted_text="[PHI-2] a persistent cough.",
        entity_order=2,
        entity_value="Alice reports",
    )

    effective = effective_redaction_text_and_phi_index(db_session, run=rerun)

    assert effective.redacted_text == "[PHI-2] a persistent cough."
    assert [item["placeholder"] for item in effective.phi_index] == ["[PHI-2]"]
    assert effective.dismissed_entity_ids == frozenset()
    assert effective.requires_review_dismissal_ids


def test_detected_pii_dismissal_does_not_carry_to_new_identical_transcript_version(db_session, make_user):
    owner = make_user(email="detected-pii-new-version-owner@example.com")
    transcript, _, _, entity = _transcript_with_detected_pii(db_session, owner)
    assert dismiss_detected_pii_entity_service(db_session, owner, transcript_id=transcript.id, entity_id=entity.id) is True
    new_version = TranscriptVersion(id=uuid4(), transcript_id=transcript.id, version_no=2, text_encrypted="")
    new_version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=new_version.id,
        plaintext=RAW_TEXT,
    )
    db_session.add(new_version)
    db_session.commit()
    rerun, rerun_entity = _successful_rerun(
        db_session,
        transcript=transcript,
        version=new_version,
        owner=owner,
        redacted_text=REDACTED_TEXT,
        entity_order=1,
        entity_value="Alice",
    )

    effective = effective_redaction_text_and_phi_index(db_session, run=rerun)

    assert effective.redacted_text == REDACTED_TEXT
    assert [item["placeholder"] for item in effective.phi_index] == ["[PHI-1]"]
    assert effective.dismissed_entity_ids == frozenset()
    assert effective.requires_review_dismissal_ids == frozenset()
    assert rerun_entity.id not in effective.dismissed_entity_ids


def test_repeated_placeholder_run_rejects_dismissal_without_creating_decision(db_session, make_user):
    owner = make_user(email="detected-pii-repeated-placeholder-owner@example.com")
    transcript, _, _, entity = _transcript_with_detected_pii(
        db_session,
        owner,
        raw_text="Alice and Alice.",
        redacted_text="[PHI-1] and [PHI-1].",
        entity_value="Alice",
    )

    with pytest.raises(AppError, match="reviewed again") as exc_info:
        dismiss_detected_pii_entity_service(db_session, owner, transcript_id=transcript.id, entity_id=entity.id)

    assert exc_info.value.code == "redaction_override_requires_review"
    assert db_session.query(DetectedPiiDismissal).count() == 0


def test_detected_pii_override_flows_to_template_provider_request_and_reidentification(
    db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch,
):
    team = make_team(name="Detected PII template integration")
    admin = make_user(email="detected-pii-template-admin@example.com", is_system_admin=True)
    owner = make_user(email="detected-pii-template-owner@example.com", team=team)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(owner=owner, actor=owner, name="Detected PII note", prompt_text="Write a concise note.")
    transcript, _, _, entity = _transcript_with_detected_pii(db_session, owner)
    assert dismiss_detected_pii_entity_service(db_session, owner, transcript_id=transcript.id, entity_id=entity.id) is True

    captured_request = {}
    reidentify_indexes = []
    monkeypatch.setattr("app.services.templates.try_publish_task_dispatch_safely", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.templates._resolve_generation_credential", lambda _config: "synthetic")
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: (captured_request.update(kwargs["request_body"]) or '{"title":"Synthetic","content":"Alice remains in the note."}', {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "duration_ms": 1}),
    )
    monkeypatch.setattr(
        "app.services.templates.reidentify_text",
        lambda text, *, phi_index: (reidentify_indexes.append(phi_index) or text),
    )

    document = queue_document_generation_from_template(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    processed = process_generated_document(db_session, document_id=document.id)

    assert "Consultation transcript:\nAlice reports a persistent cough." in captured_request["messages"][1]["content"]
    assert "[PHI-1]" not in captured_request["messages"][1]["content"]
    assert reidentify_indexes and all(index == [] for index in reidentify_indexes)
    assert processed.status.value == "ready"


def test_detected_pii_override_reprepares_split_source_and_invalidates_fingerprint(
    db_session, make_user, make_template,
):
    owner = make_user(email="detected-pii-split-owner@example.com")
    transcript, version, run, entity = _transcript_with_detected_pii(db_session, owner)
    make_template(owner=owner, actor=owner, name="Detected PII split template")

    baseline = prepare_source_bound_consultation_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        redaction_run_id=run.id,
    )
    assert baseline.source_snapshot["sources"]["transcript"] == REDACTED_TEXT
    assert [item["placeholder"] for item in baseline.source_snapshot["phi_index"]] == ["[PHI-1]"]

    assert dismiss_detected_pii_entity_service(db_session, owner, transcript_id=transcript.id, entity_id=entity.id) is True
    overridden = prepare_source_bound_consultation_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        redaction_run_id=run.id,
    )

    assert overridden.source_snapshot["sources"]["transcript"] == RAW_TEXT
    assert overridden.source_snapshot["phi_index"] == []
    assert overridden.source_state.source_fingerprint != baseline.source_state.source_fingerprint
    assert str(entity.id) in overridden.source_state.source_snapshot["detected_pii_dismissals"]
