"""Additive, owner-encrypted synthetic consultations for local dev accounts."""
from __future__ import annotations

import json
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentSection,
    GeneratedDocumentStatus,
    HallucinationCheckStatus,
    TeamRole,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
    utcnow,
)
from app.schemas.templates import (
    EMIS_SECTION_LABELS,
    StructuredTemplateConfig,
    StructuredTemplateSectionConfig,
)
from app.schemas.transcripts import TranscriptStart
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner
from app.services.dev_demo_content import DEMO_CONSULTATIONS
from app.services.dictations import dictation_combined_text, update_post_consultation_dictation
from app.services.templates import _parse_generated_structured_note_json, set_generated_document_structured_working_note_snapshot, set_generated_document_text
from app.services.transcripts import commit_transcript_text, start_transcript


DEMO_SEED_NAMESPACE = UUID("4e751e70-0b5c-467e-9ca4-c4f6067735ac")
DEMO_NOTE_PROMPT = (
    "Create a concise EMIS consultation note from the saved sources. "
    "Use only supported facts and retain uncertainty and safety-netting."
)
DEMO_FOLLOW_UP_PROMPT = (
    "Write a clear patient follow-up message based only on the agreed plan."
)


def demo_transcript_id(owner: User, seed_key: str) -> UUID:
    return uuid5(DEMO_SEED_NAMESPACE, f"{owner.id}:{seed_key}")


def _structured_note_config() -> dict[str, object]:
    return StructuredTemplateConfig(
        profile="emis",
        sections=[
            StructuredTemplateSectionConfig(
                section_key=key,
                instruction=f"Record supported facts for {EMIS_SECTION_LABELS[key]}.",
                section_order=index,
            )
            for index, key in enumerate(EMIS_SECTION_LABELS, start=1)
        ],
    ).model_dump(mode="json")


def _latest_transcript_version(db: Session, *, transcript_id: UUID) -> TranscriptVersion | None:
    return db.scalar(
        select(TranscriptVersion)
        .where(TranscriptVersion.transcript_id == transcript_id)
        .order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.created_at.desc())
        .limit(1)
    )


def _create_synthetic_split(db: Session, *, owner: User, transcript: Transcript,
                            version: TranscriptVersion, redaction_run: object,
                            case: dict[str, object]) -> None:
    """Persist validated split-note examples without claiming provider work."""
    from app.models import ConsultationSplitAnalysisStatus, ConsultationSplitBatchStatus, ConsultationSplitTopicDisposition, ConsultationSplitTopicOutcomeStatus
    from app.services.consultation_split_analysis import parse_split_analysis_output
    from app.services.consultation_split_generation import SplitGenerationTopic, parse_split_generation
    from app.services.consultation_split_materialization import materialize_split_document
    from app.services.consultation_splits import create_split_analysis, create_split_batch, create_split_batch_topic, create_split_topic_outcome
    from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis

    items = case.get("split_topics")
    if not isinstance(items, list) or len(items) < 2:
        raise AppError(500, "dev_demo_split_invalid", "A synthetic split example is invalid")
    prepared = prepare_source_bound_consultation_split_analysis(
        db, owner, transcript_id=transcript.id,
        transcript_version_id=version.id, redaction_run_id=redaction_run.id,
    )
    source_state = prepared.source_state
    fingerprint = source_state.source_fingerprint
    prepared_snapshot = prepared.source_snapshot
    phi_index = prepared_snapshot["phi_index"]
    proposal = parse_split_analysis_output(json.dumps({"topics": [
        {"title": item["title"], "is_primary": index == 0, "disposition": "separate_note", "template_id": None}
        for index, item in enumerate(items)
    ]}))
    analysis = create_split_analysis(
        db, owner, transcript_id=transcript.id, transcript_version_id=version.id,
        redaction_run_id=redaction_run.id, source_fingerprint=fingerprint,
        source_snapshot=source_state.source_snapshot,
        candidate_template_snapshot=source_state.candidate_template_snapshot,
        provider_snapshot={"origin": "synthetic_dev_demo", "provider_used": False},
        proposal={"topics": [
            {"topic_uuid": str(topic.topic_uuid), "title": item["title"], "is_primary": index == 0,
             "disposition": "separate_note", "template_id": None}
            for index, (topic, item) in enumerate(zip(proposal.topics, items))
        ]},
    )
    analysis.status = ConsultationSplitAnalysisStatus.ready
    analysis.completed_at = utcnow()
    config = _structured_note_config()
    section_definitions = [{"section_key": key, "section_label": EMIS_SECTION_LABELS[key], "section_order": index}
                           for index, key in enumerate(EMIS_SECTION_LABELS, start=1)]
    templates = [{
        "template_id": str(uuid5(transcript.id, f"demo-split-template:{index}")),
        "template_version_id": str(uuid5(transcript.id, f"demo-split-template-version:{index}")),
        "template_version_no": 1, "name": "Synthetic dev EMIS note", "description": "Synthetic fixture snapshot",
        "mode": "structured", "prompt_text": DEMO_NOTE_PROMPT, "config": config,
        "structured_sections": {"profile": "emis", "sections": section_definitions},
    } for index in range(len(items))]
    source_snapshot = {
        **prepared_snapshot,
    }
    topic_uuids = [topic.topic_uuid for topic in proposal.topics]
    plan = {"intent_id": str(uuid5(transcript.id, "demo-split-intent")), "analysis_id": str(analysis.id), "topics": [
        {"topic_uuid": str(topic_uuid), "title": item["title"], "order": index, "is_primary": index == 0,
         "disposition": "separate_note", "template": templates[index]}
        for index, (topic_uuid, item) in enumerate(zip(topic_uuids, items))
    ]}
    batch = create_split_batch(
        db, owner, analysis=analysis, confirmed_plan=plan,
        clinical_snapshot={"clinical_nlp_hints": prepared_snapshot["clinical_nlp_hints"]}, source_snapshot=source_snapshot,
        template_snapshot={"origin": "synthetic_dev_demo", "topics": templates},
        pii_snapshot={"origin": "synthetic_dev_demo", "phi_index": phi_index},
        provider_snapshot={"origin": "synthetic_dev_demo", "provider_used": False},
        note_options_snapshot={"note_generation_length": "normal", "llm_detail_level": "balanced"},
        materialization_transcript_version_id=version.id,
    )
    batch.status = ConsultationSplitBatchStatus.ready
    batch.verification_status = "unchecked"
    batch.completed_at = utcnow()
    batch_topics = []
    generation_topics = []
    for index, (topic_uuid, item) in enumerate(zip(topic_uuids, items)):
        topic = create_split_batch_topic(
            db, owner, batch=batch, title=str(item["title"]), topic_order=index,
            is_primary=index == 0, disposition=ConsultationSplitTopicDisposition.separate_note,
            template_snapshot=templates[index], topic_uuid=topic_uuid,
        )
        batch_topics.append(topic)
        generation_topics.append(SplitGenerationTopic(topic_uuid=topic_uuid, mode="structured", section_keys=tuple(EMIS_SECTION_LABELS)))
    raw = json.dumps({"title": case["title"], "notes": [
        {"topic_uuid": str(topic_uuid), "mode": "structured", "content": {
            key: str(item["sections"].get(key, "")) for key in EMIS_SECTION_LABELS
        }} for topic_uuid, item in zip(topic_uuids, items)
    ]})
    validated = parse_split_generation(raw, topics=generation_topics)
    outcomes = []
    for topic, note in zip(batch_topics, validated):
        outcome = create_split_topic_outcome(db, owner, batch_topic=topic,
            output={"mode": note.mode, "content": note.content})
        outcome.status = ConsultationSplitTopicOutcomeStatus.validated
        outcomes.append(outcome)
    for topic, note in zip(batch_topics, validated):
        document = materialize_split_document(db, owner=owner, batch=batch, topic=topic,
            accepted_output={"mode": note.mode, "content": note.content},
            template_snapshot=templates[topic.topic_order], phi_index=phi_index,
            materialization_version=version)
        document.generation_snapshot_json = {
            "origin": "synthetic_dev_demo",
            "seed_key": case["key"],
            "split_topic_uuid": str(topic.topic_uuid),
        }
    for outcome in outcomes:
        outcome.status = ConsultationSplitTopicOutcomeStatus.ready


def _create_demo_document(
    db: Session,
    *,
    owner: User,
    transcript: Transcript,
    version: TranscriptVersion,
    dictation: str,
    case: dict[str, object],
    title: str,
    output: str,
    redaction_run_id: UUID,
    follow_up: bool = False,
) -> GeneratedDocument:
    document_id = uuid5(transcript.id, "follow-up" if follow_up else "structured-note")
    existing = db.get(GeneratedDocument, document_id)
    if existing is not None:
        return existing
    now = utcnow()
    sections = case["note_sections"]
    if not isinstance(sections, dict):
        raise AppError(500, "dev_demo_fixture_invalid", "A synthetic demo fixture is invalid")
    config_value = _structured_note_config()
    config = StructuredTemplateConfig.model_validate(config_value)
    if follow_up:
        mode = TemplateMode.freeform
        parsed_sections: list[dict[str, str | int]] = []
        rendered = output
        structured_definitions = None
        generation_snapshot = {"origin": "synthetic_dev_demo", "seed_key": case["key"]}
        generator_type = GeneratedDocumentGeneratorType.followup
    else:
        mode = TemplateMode.structured
        validated_payload = json.dumps({"title": title, "content": sections}, ensure_ascii=False)
        _parsed_title, parsed_sections = _parse_generated_structured_note_json(
            validated_payload,
            template_config=config,
        )
        rendered = "\n\n".join(
            f"{EMIS_SECTION_LABELS[str(section['section_key'])]}\n{section['text']}"
            for section in parsed_sections
        )
        structured_definitions = {
            "profile": "emis",
            "sections": [
                {
                    "section_key": section.section_key,
                    "section_label": EMIS_SECTION_LABELS[section.section_key],
                    "section_order": section.section_order,
                }
                for section in sorted(config.sections, key=lambda item: item.section_order)
            ],
        }
        generation_snapshot = {
            "origin": "synthetic_dev_demo",
            "seed_key": case["key"],
            "submitted_template_config": config.model_dump(mode="json"),
        }
        generator_type = GeneratedDocumentGeneratorType.template

    document = GeneratedDocument(
        id=document_id,
        owner_user_id=owner.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        redaction_run_id=redaction_run_id,
        generator_type=generator_type,
        template_version_id=None,
        llm_config_id=None,
        source_template_name="Synthetic dev EMIS note" if not follow_up else "Synthetic demo follow-up",
        prompt_snapshot_text=DEMO_FOLLOW_UP_PROMPT if follow_up else DEMO_NOTE_PROMPT,
        structured_context_json=None,
        generation_snapshot_json=generation_snapshot,
        working_note_mode_snapshot=TranscriptWorkingNoteMode.structured,
        structured_section_definitions_json=structured_definitions,
        status=GeneratedDocumentStatus.ready,
        title=title,
        document_mode=mode,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used=None,
        hallucination_check_status=HallucinationCheckStatus.not_applicable,
        started_at=now,
        completed_at=now,
        created_at=now,
        updated_at=now,
        follow_up_prompt_text=None,
    )
    set_generated_document_text(db, document=document, field="original_output_text_encrypted", plaintext=rendered)
    set_generated_document_text(db, document=document, field="edited_output_text_encrypted", plaintext=rendered)
    if follow_up:
        set_generated_document_text(db, document=document, field="follow_up_prompt_text", plaintext=DEMO_FOLLOW_UP_PROMPT)
    set_generated_document_structured_working_note_snapshot(
        db, document=document, plaintext=case["working_note"]
    )
    # Keep the source snapshot distinct from the visible output.
    from app.services.content_crypto import encrypt_json_for_owner, encrypt_text_for_owner

    document.structured_context_json = encrypt_json_for_owner(
        db,
        owner_user_id=owner.id,
        table="generated_documents",
        field="structured_context_json",
        record_id=document.id,
        plaintext=case["working_note"],
    )
    document.dictation_snapshot_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="generated_documents",
        field="dictation_snapshot_encrypted",
        record_id=document.id,
        plaintext=dictation,
    )
    db.add(document)
    db.flush()
    for item in parsed_sections:
        section_id = uuid5(document.id, f"section:{item['section_key']}")
        plaintext = str(item["text"])
        db.add(
            GeneratedDocumentSection(
                id=section_id,
                generated_document_id=document.id,
                section_key=str(item["section_key"]),
                section_label=EMIS_SECTION_LABELS[str(item["section_key"])],
                section_order=int(item["section_order"]),
                original_text_encrypted=encrypt_text_for_owner(
                    db,
                    owner_user_id=owner.id,
                    table="generated_document_sections",
                    field="original_text_encrypted",
                    record_id=section_id,
                    plaintext=plaintext,
                ) or "",
                edited_text_encrypted=encrypt_text_for_owner(
                    db,
                    owner_user_id=owner.id,
                    table="generated_document_sections",
                    field="edited_text_encrypted",
                    record_id=section_id,
                    plaintext=plaintext,
                ) or "",
            )
        )
    return document


def _seed_case_documents(db: Session, *, owner: User, transcript: Transcript,
                         case: dict[str, object]) -> None:
    """Fill missing initial-seed children only when saved sources are untouched."""
    from app.models import ConsultationSplitBatch, PostConsultationDictation
    from app.services.dictations import dictation_combined_text
    from app.services.transcripts import transcript_draft_text, transcript_structured_context
    from app.services.redaction import ensure_redaction_run_for_transcript_version

    latest = _latest_transcript_version(db, transcript_id=transcript.id)
    if latest is None:
        return
    source_text = decrypt_text_for_owner(
        db, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
        record_id=latest.id, stored_value=latest.text_encrypted,
    ) or ""
    if (
        source_text != case["transcript"]
        or transcript_draft_text(db, transcript=transcript) != case["transcript"]
        or transcript_structured_context(db, transcript=transcript) != case["working_note"]
    ):
        return
    dictation_row = db.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == transcript.id))
    if dictation_row is None:
        dictation_row = update_post_consultation_dictation(
            db, owner, transcript_id=transcript.id, combined_text=case["dictation"], commit=False,
        )
    elif dictation_combined_text(db, dictation=dictation_row) != case["dictation"]:
        return
    redaction_run = ensure_redaction_run_for_transcript_version(db, transcript_version=latest)
    saved_dictation = dictation_combined_text(db, dictation=dictation_row) or ""
    if not case.get("split_topics"):
        _create_demo_document(
            db, owner=owner, transcript=transcript, version=latest,
            dictation=saved_dictation, case=case, title=f"{case['title']} note",
            output="\n\n".join(f"{EMIS_SECTION_LABELS[key]}\n{text}" for key, text in case["note_sections"].items()),
            redaction_run_id=redaction_run.id,
        )
    _create_demo_document(
        db, owner=owner, transcript=transcript, version=latest,
        dictation=saved_dictation, case=case,
        title=f"{case['title']} follow-up", output=case["patient_follow_up"],
        redaction_run_id=redaction_run.id, follow_up=True,
    )
    split_batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.transcript_id == transcript.id)) if case.get("split_topics") else None
    if case.get("split_topics") and split_batch is None:
        _create_synthetic_split(db, owner=owner, transcript=transcript, version=latest,
                                redaction_run=redaction_run, case=case)
    elif split_batch is not None:
        from app.services.consultation_splits import read_split_analysis_json

        provider_snapshot = read_split_analysis_json(db, owner, analysis=split_batch.analysis, field="provider_snapshot_encrypted")
        if isinstance(provider_snapshot, dict) and provider_snapshot.get("origin") == "synthetic_dev_demo":
            # The per-root marker exposes the split workspace for this synthetic
            # example without changing the account preference or global rollout.
            transcript.multiple_problems = True
            split_documents = db.scalars(select(GeneratedDocument).where(
                GeneratedDocument.transcript_id == transcript.id,
                GeneratedDocument.consultation_split_batch_topic_id.is_not(None),
            )).all()
            for document in split_documents:
                if (
                    not document.generation_snapshot_json
                    and document.model_used is None
                    and document.llm_config_id is None
                    and document.llm_adapter_kind is None
                ):
                    document.generation_snapshot_json = {
                        "origin": "synthetic_dev_demo",
                        "seed_key": case["key"],
                        "split_topic_uuid": str(document.consultation_split_topic_uuid),
                    }


def seed_dev_demo_consultations(db: Session, *, owner: User, reset: bool = False) -> list[Transcript]:
    """Add any missing synthetic cases; preserve existing roots and user edits."""
    if owner.is_system_admin or owner.team_id is None or owner.team_role is not TeamRole.user:
        raise AppError(403, "forbidden", "Synthetic dev demo content needs a normal team clinician")
    db.scalar(select(User.id).where(User.id == owner.id).with_for_update())
    if reset:
        from app.services.transcripts import delete_transcripts

        ids = [demo_transcript_id(owner, str(case["key"])) for case in DEMO_CONSULTATIONS]
        existing_ids = list(db.scalars(select(Transcript.id).where(Transcript.id.in_(ids))))
        if existing_ids:
            delete_transcripts(db, owner, transcript_ids=existing_ids)
    result: list[Transcript] = []
    for case in DEMO_CONSULTATIONS:
        transcript_id = demo_transcript_id(owner, case["key"])
        transcript = db.get(Transcript, transcript_id)
        if transcript is not None:
            if transcript.owner_user_id != owner.id or transcript.team_id != owner.team_id:
                raise AppError(409, "dev_demo_root_conflict", "A synthetic demo consultation identity is already in use")
            _seed_case_documents(db, owner=owner, transcript=transcript, case=case)
            result.append(transcript)
            continue
        working_note = case["working_note"]
        transcript = start_transcript(
            db,
            owner,
            TranscriptStart(
                title=case["title"],
                current_draft_text_encrypted=case["transcript"],
                structured_context_json=working_note,
                ingestion_mode=TranscriptIngestionMode.whole_file,
            ),
            commit=False,
            transcript_id=transcript_id,
        )
        commit_transcript_text(
            db,
            owner,
            transcript_id=transcript.id,
            plaintext=case["transcript"],
            commit=False,
            preview_redaction=True,
        )
        dictation_row = update_post_consultation_dictation(
            db,
            owner,
            transcript_id=transcript.id,
            combined_text=case["dictation"],
            commit=False,
        )
        version = _latest_transcript_version(db, transcript_id=transcript.id)
        if version is None:
            raise AppError(500, "dev_demo_transcript_invalid", "The synthetic consultation has no saved version")
        if case.get("split_topics"):
            transcript.multiple_problems = True
        from app.services.redaction import ensure_redaction_run_for_transcript_version

        redaction_run = ensure_redaction_run_for_transcript_version(db, transcript_version=version)
        saved_dictation = dictation_combined_text(db, dictation=dictation_row) or ""
        if not case.get("split_topics"):
            _create_demo_document(
                db,
                owner=owner,
                transcript=transcript,
                version=version,
                dictation=saved_dictation,
                case=case,
                title=f"{case['title']} note",
                output="\n\n".join(
                    f"{EMIS_SECTION_LABELS[key]}\n{text}"
                    for key, text in case["note_sections"].items()
                ),
                redaction_run_id=redaction_run.id,
            )
        _create_demo_document(
            db,
            owner=owner,
            transcript=transcript,
            version=version,
            dictation=saved_dictation,
            case=case,
            title=f"{case['title']} follow-up",
            output=case["patient_follow_up"],
            redaction_run_id=redaction_run.id,
            follow_up=True,
        )
        if case.get("split_topics"):
            _create_synthetic_split(
                db, owner=owner, transcript=transcript, version=version,
                redaction_run=redaction_run, case=case,
            )
        result.append(transcript)
    db.commit()
    return result
