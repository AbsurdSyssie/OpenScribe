"""Focused immutable regeneration coverage for generated clinical notes."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    ConsultationSplitTopicDisposition,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    PromptTemplateVersion,
    ProviderAttempt,
    TaskDispatchOutbox,
    TeamRole,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services.consultation_splits import (
    create_split_analysis,
    create_split_batch,
    create_split_batch_topic,
)
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner
from app.errors import AppError
from app.schemas.templates import RegenerationSteeringPreset
from app.services.templates import (
    PERSISTED_SPLIT_NOTE_TITLE,
    process_generated_document,
    queue_generated_document_regeneration,
    set_generated_document_text,
)


def _frozen_template(template, version):
    return {
        "template_id": str(template.id),
        "template_version_id": str(version.id),
        "template_version_no": version.version_no,
        "name": template.name,
        "description": template.description,
        "mode": "freeform",
        "prompt_text": version.prompt_text,
        "config": None,
        "structured_sections": None,
    }


def test_split_note_regeneration_uses_confirmed_snapshot_and_creates_revision(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Split regeneration clinic")
    admin = make_user(
        email="split-regeneration-admin@example.com",
        password="password-1",
        is_system_admin=True,
    )
    owner = make_user(
        email="split-regeneration-owner@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.user,
    )
    config = make_llm_config(
        team=team,
        actor=admin,
        model_name="gpt-4o-mini",
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=["gpt-4o-mini"],
        model_name_override="gpt-4o-mini",
    )
    template = make_template(
        owner=owner,
        actor=owner,
        name="Synthetic UTI template",
        prompt_text="Write a synthetic UTI consultation note.",
    )
    template_version = db_session.scalar(
        select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id)
    )
    assert template_version is not None

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Synthetic split consultation",
        current_draft_text_encrypted="Live content that must not be used for regeneration.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Frozen source lineage only.",
    )
    db_session.add(version)
    db_session.flush()

    source_snapshot = {
        "source_state": {},
        "sources": {
            "transcript": "Frozen synthetic UTI consultation.",
            "working_note": {"mode": None, "value": None},
            "dictation": "",
        },
        "clinical_nlp_hints": [],
        "phi_index": [],
    }
    template_snapshot = _frozen_template(template, template_version)
    first_topic_uuid, second_topic_uuid = uuid4(), uuid4()
    analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint="b" * 64,
        source_snapshot=source_snapshot,
        candidate_template_snapshot={"templates": []},
        provider_snapshot={"provider": "synthetic"},
        proposal={"topics": []},
    )
    confirmed_plan = {
        "intent_id": str(uuid4()),
        "analysis_id": str(analysis.id),
        "topics": [
            {
                "topic_uuid": str(first_topic_uuid),
                "title": "Synthetic UTI",
                "order": 0,
                "is_primary": True,
                "disposition": "separate_note",
                "template": template_snapshot,
            },
            {
                "topic_uuid": str(second_topic_uuid),
                "title": "Synthetic back pain",
                "order": 1,
                "is_primary": False,
                "disposition": "separate_note",
                "template": template_snapshot,
            },
        ],
    }
    batch = create_split_batch(
        db_session,
        owner,
        analysis=analysis,
        confirmed_plan=confirmed_plan,
        clinical_snapshot={"clinical_nlp_hints": []},
        source_snapshot=source_snapshot,
        template_snapshot={"topic_templates": {}},
        pii_snapshot={"manual_pii": [], "phi_index": []},
        provider_snapshot={"provider": "synthetic"},
        note_options_snapshot={"note_generation_length": "normal", "llm_detail_level": "balanced"},
        materialization_transcript_version_id=version.id,
    )
    topic = create_split_batch_topic(
        db_session,
        owner,
        batch=batch,
        title="Synthetic UTI",
        topic_order=0,
        is_primary=True,
        disposition=ConsultationSplitTopicDisposition.separate_note,
        template_snapshot=template_snapshot,
        topic_uuid=first_topic_uuid,
    )
    create_split_batch_topic(
        db_session,
        owner,
        batch=batch,
        title="Synthetic back pain",
        topic_order=1,
        is_primary=False,
        disposition=ConsultationSplitTopicDisposition.separate_note,
        template_snapshot=template_snapshot,
        topic_uuid=second_topic_uuid,
    )
    source_document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        consultation_split_batch_topic_id=topic.id,
        consultation_split_topic_uuid=topic.topic_uuid,
        regeneration_lineage_id=None,
        regeneration_revision_no=1,
        generator_type=GeneratedDocumentGeneratorType.template,
        llm_config_id=config.id,
        source_template_name=PERSISTED_SPLIT_NOTE_TITLE,
        status=GeneratedDocumentStatus.ready,
        title=PERSISTED_SPLIT_NOTE_TITLE,
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
        model_used="gpt-4o-mini",
        llm_adapter_kind=config.adapter_kind.value,
        llm_base_url=config.base_url,
        llm_provider_config_json=dict(config.provider_config_json or {}),
    )
    source_document.regeneration_lineage_id = source_document.id
    set_generated_document_text(
        db_session,
        document=source_document,
        field="original_output_text_encrypted",
        plaintext="Original synthetic UTI note.",
    )
    set_generated_document_text(
        db_session,
        document=source_document,
        field="edited_output_text_encrypted",
        plaintext="Clinician-edited synthetic UTI note.",
    )
    db_session.add(source_document)
    db_session.commit()

    def fake_redact(_db, text, *, team_id, start_index):
        del team_id, start_index
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "native_presidio",
            "api_model_or_version": "test",
        }

    provider_requests: list[dict] = []

    def fake_generate(**kwargs):
        provider_requests.append(kwargs["request_body"])
        return json.dumps(
            {
                "title": "Overall consultation",
                "notes": [
                    {
                        "topic_uuid": str(first_topic_uuid),
                        "mode": "freeform",
                        "content": "Regenerated synthetic UTI note.",
                    }
                ]
            }
        ), {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12, "duration_ms": 3}

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact)
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    queued = queue_generated_document_regeneration(
        db_session,
        owner,
        generated_document_id=source_document.id,
        steering_preset=RegenerationSteeringPreset.more_detail,
        steering_text="Emphasise the safety net.",
    )
    assert queued.parent_generated_document_id == source_document.id
    assert queued.regeneration_lineage_id == source_document.id
    assert queued.regeneration_revision_no == 2
    assert queued.consultation_split_batch_topic_id == topic.id
    assert queued.title == PERSISTED_SPLIT_NOTE_TITLE
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="regeneration_source_output_encrypted",
        record_id=queued.id,
        stored_value=queued.regeneration_source_output_encrypted,
    ) == "Clinician-edited synthetic UTI note."

    with pytest.raises(AppError) as in_flight:
        queue_generated_document_regeneration(
            db_session,
            owner,
            generated_document_id=source_document.id,
        )
    assert in_flight.value.status_code == 409

    processed = process_generated_document(db_session, document_id=queued.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.title == PERSISTED_SPLIT_NOTE_TITLE
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="edited_output_text_encrypted",
        record_id=processed.id,
        stored_value=processed.edited_output_text_encrypted,
    ) == "Regenerated synthetic UTI note."
    payload = json.loads(provider_requests[-1]["messages"][1]["content"])
    assert payload["sources"]["transcript"] == "Frozen synthetic UTI consultation."
    assert payload["previous_clinician_edited_note"] == "Clinician-edited synthetic UTI note."
    assert "Increase the detail" in payload["regeneration_steering"]
    assert "Emphasise the safety net." in payload["regeneration_steering"]


def test_template_note_regeneration_copies_frozen_inputs_and_latest_edited_text(
    db_session,
    make_team,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Template regeneration clinic")
    admin = make_user(
        email="template-regeneration-admin@example.com",
        password="password-1",
        is_system_admin=True,
    )
    owner = make_user(
        email="template-regeneration-owner@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.user,
    )
    config = make_llm_config(
        team=team,
        actor=admin,
        model_name="gpt-4o-mini",
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=["gpt-4o-mini"],
        model_name_override="gpt-4o-mini",
    )
    template = make_template(
        owner=owner,
        actor=owner,
        name="Frozen template",
        prompt_text="Use only the saved consultation source.",
    )
    template_version = db_session.scalar(
        select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id)
    )
    assert template_version is not None

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Frozen ordinary consultation",
        current_draft_text_encrypted="Live source that must not retarget regeneration.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
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
            plaintext="Frozen ordinary consultation source.",
        ),
    )
    db_session.add(version)
    db_session.flush()

    source_document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        regeneration_revision_no=1,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template_version.id,
        llm_config_id=config.id,
        source_template_name=template.name,
        prompt_snapshot_text=template_version.prompt_text,
        generation_snapshot_json={},
        status=GeneratedDocumentStatus.ready,
        title="Frozen template output",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
        model_used="gpt-4o-mini",
        llm_adapter_kind=config.adapter_kind.value,
        llm_base_url=config.base_url,
        llm_provider_config_json=dict(config.provider_config_json or {}),
    )
    source_document.regeneration_lineage_id = source_document.id
    set_generated_document_text(
        db_session,
        document=source_document,
        field="dictation_snapshot_encrypted",
        plaintext="Frozen dictation source.",
    )
    set_generated_document_text(
        db_session,
        document=source_document,
        field="freeform_working_note_snapshot_encrypted",
        plaintext="Frozen clinician working note.",
    )
    set_generated_document_text(
        db_session,
        document=source_document,
        field="original_output_text_encrypted",
        plaintext="Original generated note.",
    )
    set_generated_document_text(
        db_session,
        document=source_document,
        field="edited_output_text_encrypted",
        plaintext="Latest clinician-edited note.",
    )
    db_session.add(source_document)
    db_session.commit()

    regenerated = queue_generated_document_regeneration(
        db_session,
        owner,
        generated_document_id=source_document.id,
        steering_preset=RegenerationSteeringPreset.less_detail,
        steering_text="Keep the management plan.",
    )

    assert regenerated.parent_generated_document_id == source_document.id
    assert regenerated.regeneration_lineage_id == source_document.id
    assert regenerated.regeneration_revision_no == 2
    assert regenerated.transcript_version_id == version.id
    assert regenerated.template_version_id == template_version.id
    assert regenerated.prompt_snapshot_text == template_version.prompt_text
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="dictation_snapshot_encrypted",
        record_id=regenerated.id,
        stored_value=regenerated.dictation_snapshot_encrypted,
    ) == "Frozen dictation source."
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="freeform_working_note_snapshot_encrypted",
        record_id=regenerated.id,
        stored_value=regenerated.freeform_working_note_snapshot_encrypted,
    ) == "Frozen clinician working note."
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="regeneration_source_output_encrypted",
        record_id=regenerated.id,
        stored_value=regenerated.regeneration_source_output_encrypted,
    ) == "Latest clinician-edited note."
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="generation_steering_text_encrypted",
        record_id=regenerated.id,
        stored_value=regenerated.generation_steering_text_encrypted,
    ) == (
        "Make the note more concise while preserving clinically important facts and "
        "safety-netting.\n\nKeep the management plan."
    )
    assert db_session.scalar(
        select(ProviderAttempt).where(ProviderAttempt.generated_document_id == regenerated.id)
    ) is not None
    assert db_session.scalar(
        select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == regenerated.id)
    ) is not None
