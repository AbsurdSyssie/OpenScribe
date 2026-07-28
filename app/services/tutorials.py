from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentSection,
    GeneratedDocumentStatus,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionMode,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
    utcnow,
)
from app.schemas import TranscriptStart
from app.schemas.templates import (
    PromptTemplateUpsert,
    QuickActionUpsert,
    StructuredTemplateConfig,
    StructuredTemplateSectionConfig,
)
from app.services.content_crypto import encrypt_text_for_owner
from app.services.dictations import update_post_consultation_dictation
from app.services.templates import (
    set_generated_document_structured_working_note_snapshot,
    set_generated_document_text,
    upsert_personal_quick_action,
    upsert_personal_template,
)
from app.services.transcripts import active_transcript_condition, start_transcript


TUTORIAL_TRANSCRIPT_TITLE = "OpenScribe tutorial — synthetic example"
TUTORIAL_TEMPLATE_NAME = "Tutorial sectioned note"
TUTORIAL_QUICK_ACTION_NAME = "Tutorial follow-up summary"
TUTORIAL_MARKER = "openscribe_scribe_tutorial_v1"

TUTORIAL_TRANSCRIPT_TEXT = """Clinician: What brought you in today?
Patient: I have had a mild headache for three days.
Clinician: Did it start suddenly?
Patient: No. It came on slowly and has stayed mild.
Clinician: Any fever, weakness, trouble speaking, vision loss, or recent injury?
Patient: No.
Clinician: Have you been eating, drinking, and sleeping as usual?
Patient: I have missed some meals and slept poorly this week.
Clinician: We will review the symptoms, discuss simple measures, and agree when you should seek further help."""

TUTORIAL_WORKING_NOTE = {
    "profile": "emis",
    "sections": {
        "problem": ["Mild headache for three days."],
        "history": [
            "Gradual onset; symptoms have stayed mild.",
            "No fever, weakness, speech change, vision loss, or recent injury reported.",
            "Missed meals and poor sleep this week.",
        ],
        "examination": ["Synthetic example: alert and speaking normally during the consult."],
        "comment": ["Use cautious wording and keep the plan clear."],
        "tasks": ["Record advice and safety-netting after review."],
    },
}

TUTORIAL_DICTATION_TEXT = (
    "This is a synthetic training consult. Use cautious wording. The headache was mild and gradual, "
    "with no red-flag symptoms reported in the example. Note missed meals and poor sleep. Include a "
    "clear review plan and safety-netting."
)

TUTORIAL_TEMPLATE_PROMPT = (
    "Write a short sectioned consultation note from the transcript, working note, and dictation. "
    "Use only supported facts. Keep the plan clear."
)

TUTORIAL_TEMPLATE_SECTIONS = (
    ("problem", "State the main problem in one short line."),
    ("history", "Summarise the course, key symptoms, and important negatives."),
    ("examination", "Include only examination findings present in the sources."),
    ("comment", "Give a cautious clinical summary without adding facts."),
    ("tasks", "List the agreed actions and safety-netting."),
)

TUTORIAL_NOTE_SECTIONS = (
    ("problem", "Problem", "Mild headache for three days."),
    (
        "history",
        "History",
        "Gradual onset and remained mild.\nNo fever, weakness, speech change, vision loss, or recent injury reported.\nMissed meals and poor sleep this week.",
    ),
    ("examination", "Examination", "Alert and speaking normally during this synthetic consult."),
    (
        "comment",
        "Comment",
        "No red-flag symptoms were reported in the example. Review the diagnosis if symptoms change.",
    ),
    (
        "tasks",
        "Tasks",
        "Discuss regular meals, fluids, rest, and suitable simple pain relief.\nSeek urgent help for sudden severe pain, weakness, speech change, vision loss, confusion, or other marked deterioration.",
    ),
)

TUTORIAL_FOLLOWUPS = (
    {
        "generator_type": GeneratedDocumentGeneratorType.followup,
        "title": "Review summary",
        "request": "Write a short summary of the review and the agreed next steps.",
        "text": (
            "This synthetic review covered a mild headache that began gradually three days ago. "
            "No red-flag symptoms were reported in the example. Regular meals, fluids, rest, and a clear review plan were discussed."
        ),
    },
    {
        "generator_type": GeneratedDocumentGeneratorType.quick_action,
        "title": "Safety-net message",
        "request": "Give a brief follow-up summary with clear safety-netting.",
        "text": (
            "This is synthetic training text. Please seek urgent help if the headache becomes sudden or severe, "
            "or if weakness, speech change, vision loss, confusion, or marked deterioration develops."
        ),
    },
)


def _tutorial_template_config() -> StructuredTemplateConfig:
    return StructuredTemplateConfig(
        profile="emis",
        sections=[
            StructuredTemplateSectionConfig(
                section_key=section_key,
                instruction=instruction,
                section_order=index,
            )
            for index, (section_key, instruction) in enumerate(TUTORIAL_TEMPLATE_SECTIONS, start=1)
        ],
    )


def _latest_template_version(db: Session, *, template_id: UUID) -> PromptTemplateVersion | None:
    return db.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template_id)
        .order_by(PromptTemplateVersion.version_no.desc())
        .limit(1)
    )


def _latest_quick_action_version(db: Session, *, quick_action_id: UUID) -> QuickActionVersion:
    version = db.scalar(
        select(QuickActionVersion)
        .where(QuickActionVersion.quick_action_id == quick_action_id)
        .order_by(QuickActionVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise AppError(500, "tutorial_asset_invalid", "The tutorial quick action has no version")
    return version


def _ensure_tutorial_template(db: Session, actor: User) -> tuple[PromptTemplate, PromptTemplateVersion]:
    template = db.scalar(
        select(PromptTemplate).where(
            PromptTemplate.scope == TemplateScope.user,
            PromptTemplate.owner_user_id == actor.id,
            PromptTemplate.name == TUTORIAL_TEMPLATE_NAME,
        )
    )
    config = _tutorial_template_config()
    expected_config = config.model_dump(mode="json")
    latest = _latest_template_version(db, template_id=template.id) if template is not None else None
    needs_update = bool(
        template is None
        or latest is None
        or latest.mode is not TemplateMode.structured
        or latest.prompt_text.strip() != TUTORIAL_TEMPLATE_PROMPT
        or latest.config_json != expected_config
        or not template.is_active
    )
    if needs_update:
        template = upsert_personal_template(
            db,
            actor,
            PromptTemplateUpsert(
                template_id=template.id if template is not None else None,
                scope=TemplateScope.user,
                name=TUTORIAL_TEMPLATE_NAME,
                description="A synthetic sectioned template used by the Scribe guide.",
                prompt_text=TUTORIAL_TEMPLATE_PROMPT,
                mode=TemplateMode.structured,
                config_json=config,
                is_active=True,
            ),
        )
        latest = _latest_template_version(db, template_id=template.id)
    if latest is None:
        raise AppError(500, "tutorial_asset_invalid", "The tutorial template has no version")
    return template, latest


def _ensure_tutorial_quick_action(db: Session, actor: User) -> tuple[QuickAction, QuickActionVersion]:
    quick_action = db.scalar(
        select(QuickAction).where(
            QuickAction.scope == TemplateScope.user,
            QuickAction.owner_user_id == actor.id,
            QuickAction.name == TUTORIAL_QUICK_ACTION_NAME,
        )
    )
    expected_prompt = "Write a short follow-up summary. Include the agreed next steps and clear safety-netting."
    latest = _latest_quick_action_version(db, quick_action_id=quick_action.id) if quick_action is not None else None
    needs_update = bool(
        quick_action is None
        or latest is None
        or latest.prompt_text.strip() != expected_prompt
        or not quick_action.is_active
    )
    if needs_update:
        quick_action = upsert_personal_quick_action(
            db,
            actor,
            QuickActionUpsert(
                quick_action_id=quick_action.id if quick_action is not None else None,
                scope=TemplateScope.user,
                name=TUTORIAL_QUICK_ACTION_NAME,
                description="Creates a short synthetic follow-up for the Scribe guide.",
                prompt_text=expected_prompt,
                is_active=True,
            ),
        )
    return quick_action, _latest_quick_action_version(db, quick_action_id=quick_action.id)


def _existing_tutorial_transcript(db: Session, actor: User) -> Transcript | None:
    return db.scalar(
        select(Transcript)
        .where(
            Transcript.owner_user_id == actor.id,
            Transcript.title == TUTORIAL_TRANSCRIPT_TITLE,
            active_transcript_condition(),
        )
        .order_by(Transcript.created_at.desc())
        .limit(1)
    )


def _assert_tutorial_can_be_created(db: Session, actor: User) -> None:
    existing = db.scalar(
        select(Transcript.id)
        .where(Transcript.owner_user_id == actor.id, active_transcript_condition())
        .limit(1)
    )
    if existing is not None:
        raise AppError(
            409,
            "tutorial_consultation_not_empty",
            "Open the existing consultation before starting the guide.",
        )


def _create_transcript_version(db: Session, *, transcript: Transcript) -> TranscriptVersion:
    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext=TUTORIAL_TRANSCRIPT_TEXT,
        )
        or "",
    )
    db.add(version)
    db.flush()
    return version


def _tutorial_section_definitions() -> dict[str, object]:
    return {
        "profile": "emis",
        "sections": [
            {
                "section_key": section_key,
                "section_label": section_label,
                "section_order": index,
            }
            for index, (section_key, section_label, _text) in enumerate(TUTORIAL_NOTE_SECTIONS, start=1)
        ],
    }


def _create_note_document(
    db: Session,
    *,
    actor: User,
    transcript: Transcript,
    transcript_version: TranscriptVersion,
    template: PromptTemplate,
    template_version: PromptTemplateVersion,
) -> GeneratedDocument:
    now = utcnow()
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=actor.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=None,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template_version.id,
        quick_action_version_id=None,
        llm_config_id=None,
        source_template_name=template.name,
        source_quick_action_name=None,
        follow_up_prompt_text=None,
        prompt_snapshot_text=template_version.prompt_text,
        structured_context_json=None,
        generation_snapshot_json={"tutorial_marker": TUTORIAL_MARKER},
        working_note_mode_snapshot=TranscriptWorkingNoteMode.structured,
        freeform_working_note_snapshot_encrypted=None,
        structured_working_note_snapshot_json=None,
        structured_section_definitions_json=_tutorial_section_definitions(),
        status=GeneratedDocumentStatus.ready,
        title="Headache review",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used="Synthetic tutorial",
        completed_at=now,
        started_at=now,
        created_at=now - timedelta(minutes=2),
        updated_at=now - timedelta(minutes=2),
    )
    rendered_text = "\n\n".join(
        f"{section_label}\n{text}" for _section_key, section_label, text in TUTORIAL_NOTE_SECTIONS
    )
    set_generated_document_text(db, document=document, field="original_output_text_encrypted", plaintext=rendered_text)
    set_generated_document_text(db, document=document, field="edited_output_text_encrypted", plaintext=rendered_text)
    set_generated_document_structured_working_note_snapshot(
        db,
        document=document,
        plaintext=TUTORIAL_WORKING_NOTE,
    )
    db.add(document)
    db.flush()
    for index, (section_key, section_label, text) in enumerate(TUTORIAL_NOTE_SECTIONS, start=1):
        section_id = uuid4()
        section = GeneratedDocumentSection(
            id=section_id,
            generated_document_id=document.id,
            section_key=section_key,
            section_label=section_label,
            section_order=index,
            original_text_encrypted=encrypt_text_for_owner(
                db,
                owner_user_id=actor.id,
                table="generated_document_sections",
                field="original_text_encrypted",
                record_id=section_id,
                plaintext=text,
            )
            or "",
            edited_text_encrypted=encrypt_text_for_owner(
                db,
                owner_user_id=actor.id,
                table="generated_document_sections",
                field="edited_text_encrypted",
                record_id=section_id,
                plaintext=text,
            )
            or "",
            is_edited=False,
        )
        db.add(section)
    return document


def _create_followup_document(
    db: Session,
    *,
    actor: User,
    transcript: Transcript,
    transcript_version: TranscriptVersion,
    quick_action: QuickAction,
    quick_action_version: QuickActionVersion,
    item: dict[str, object],
    sequence: int,
) -> GeneratedDocument:
    now = utcnow()
    is_quick_action = item["generator_type"] is GeneratedDocumentGeneratorType.quick_action
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=actor.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=None,
        generator_type=item["generator_type"],
        template_version_id=None,
        quick_action_version_id=quick_action_version.id if is_quick_action else None,
        llm_config_id=None,
        source_template_name="Tutorial follow-up",
        source_quick_action_name=quick_action.name if is_quick_action else None,
        follow_up_prompt_text=None,
        prompt_snapshot_text=quick_action_version.prompt_text if is_quick_action else None,
        structured_context_json=None,
        generation_snapshot_json={"tutorial_marker": TUTORIAL_MARKER},
        working_note_mode_snapshot=TranscriptWorkingNoteMode.structured,
        freeform_working_note_snapshot_encrypted=None,
        structured_working_note_snapshot_json=None,
        structured_section_definitions_json=None,
        status=GeneratedDocumentStatus.ready,
        title=str(item["title"]),
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used="Synthetic tutorial",
        completed_at=now,
        started_at=now,
        created_at=now - timedelta(minutes=sequence),
        updated_at=now - timedelta(minutes=sequence),
    )
    set_generated_document_text(
        db,
        document=document,
        field="follow_up_prompt_text",
        plaintext=str(item["request"]),
    )
    set_generated_document_text(
        db,
        document=document,
        field="original_output_text_encrypted",
        plaintext=str(item["text"]),
    )
    set_generated_document_text(
        db,
        document=document,
        field="edited_output_text_encrypted",
        plaintext=str(item["text"]),
    )
    set_generated_document_structured_working_note_snapshot(
        db,
        document=document,
        plaintext=TUTORIAL_WORKING_NOTE,
    )
    db.add(document)
    return document


def create_scribe_tutorial_consultation(db: Session, actor: User) -> Transcript:
    if actor.is_system_admin or actor.team_id is None:
        raise AppError(403, "forbidden", "The Scribe guide is available to team users only.")

    existing = _existing_tutorial_transcript(db, actor)
    if existing is not None:
        return existing
    _assert_tutorial_can_be_created(db, actor)

    template, template_version = _ensure_tutorial_template(db, actor)
    quick_action, quick_action_version = _ensure_tutorial_quick_action(db, actor)
    transcript = start_transcript(
        db,
        actor,
        TranscriptStart(
            title=TUTORIAL_TRANSCRIPT_TITLE,
            current_draft_text_encrypted=TUTORIAL_TRANSCRIPT_TEXT,
            structured_context_json=TUTORIAL_WORKING_NOTE,
            ingestion_mode=TranscriptIngestionMode.whole_file,
        ),
    )
    update_post_consultation_dictation(
        db,
        actor,
        transcript_id=transcript.id,
        combined_text=TUTORIAL_DICTATION_TEXT,
    )

    try:
        transcript_version = _create_transcript_version(db, transcript=transcript)
        _create_note_document(
            db,
            actor=actor,
            transcript=transcript,
            transcript_version=transcript_version,
            template=template,
            template_version=template_version,
        )
        for sequence, item in enumerate(TUTORIAL_FOLLOWUPS, start=1):
            _create_followup_document(
                db,
                actor=actor,
                transcript=transcript,
                transcript_version=transcript_version,
                quick_action=quick_action,
                quick_action_version=quick_action_version,
                item=item,
                sequence=sequence,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(transcript)
    return transcript
