"""One-time owner-owned fictional consultation for a first Scribe visit."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentSection,
    GeneratedDocumentStatus,
    HallucinationCheckStatus,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
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
from app.services.content_crypto import encrypt_json_for_owner, encrypt_text_for_owner
from app.services.dictations import dictation_combined_text, update_post_consultation_dictation
from app.services.templates import (
    set_generated_document_structured_working_note_snapshot,
    set_generated_document_text,
)
from app.services.transcripts import commit_transcript_text, start_transcript


EXAMPLE_TRANSCRIPT = (
    "Clinician: What brought you in today?\n"
    "Patient: I have had a dry cough for about ten days after a cold. It is worse at night.\n"
    "Clinician: Have you had a fever, chest pain, or shortness of breath?\n"
    "Patient: No fever or chest pain. I get a little breathless when I climb stairs, but it is not getting worse.\n"
    "Clinician: Any wheeze or blood when you cough?\n"
    "Patient: No wheeze or blood.\n"
    "Clinician: I will examine you now. Your temperature is 36.8 C, oxygen saturation is 98% on air, and your chest is clear.\n"
    "Clinician: We discussed that a cough can persist after a viral infection. Please seek urgent help if breathing worsens, you develop chest pain, cough blood, or feel seriously unwell. Arrange review if the cough is not improving over the next two weeks."
)

EXAMPLE_WORKING_NOTE = {
    "profile": "emis",
    "sections": {
        "problem": ["Dry cough for around ten days after a cold."],
        "history": [
            "Worse at night. No fever, chest pain, wheeze, or haemoptysis reported.",
            "Mild breathlessness on stairs, not worsening."
        ],
        "examination": ["Temperature 36.8 C; oxygen saturation 98% on air; chest clear."],
        "comment": ["Discussed that cough may persist after a viral infection."],
        "tasks": [
            "Plan: supportive care and observation.",
            "Seek urgent help for worsening breathing, chest pain, coughing blood, or feeling seriously unwell.",
            "Arrange review if not improving over the next two weeks."
        ],
    },
}
EXAMPLE_DICTATION = (
    "Working plan: supportive care and observation. Safety net for worsening breathlessness, chest pain, "
    "haemoptysis, or feeling seriously unwell. Review if not improving within two weeks."
)
EXAMPLE_NOTE_SECTIONS = (
    ("problem", "Problem", "Dry cough after a cold"),
    ("history", "History", "Dry cough for around ten days, worse at night. No fever, chest pain, wheeze, or haemoptysis reported. Mild breathlessness on stairs, not worsening."),
    ("examination", "Examination", "Temperature 36.8 C. Oxygen saturation 98% on air. Chest clear."),
    ("comment", "Comment", "Cough may persist after a viral infection."),
    ("tasks", "Tasks", "Plan: supportive care and observation. Seek urgent help for worsening breathing, chest pain, coughing blood, or feeling seriously unwell. Arrange review if not improving over the next two weeks."),
)
EXAMPLE_FOLLOWUP = (
    "Hello,\n\nAs discussed, a cough can continue for a while after a viral infection. "
    "Please seek urgent help if your breathing gets worse, you develop chest pain, cough up blood, "
    "or feel seriously unwell. Arrange a review if the cough is not improving over the next two weeks.\n\nTake care"
)
EXAMPLE_FOLLOWUP_PROMPT = "Write a clear follow-up message summarising the agreed advice and safety-netting."


def _save_example_document(
    db: Session,
    *,
    owner: User,
    transcript: Transcript,
    title: str,
    generator_type: GeneratedDocumentGeneratorType,
    output: str,
    mode: TemplateMode,
    version_id,
    working_note: dict,
    dictation: str,
    follow_up_prompt: str | None = None,
) -> GeneratedDocument:
    now = utcnow()
    document_id = uuid4()
    document = GeneratedDocument(
        id=document_id,
        owner_user_id=owner.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=version_id,
        redaction_run_id=None,
        generator_type=generator_type,
        template_version_id=None,
        quick_action_version_id=None,
        llm_config_id=None,
        source_template_name="Example",
        source_quick_action_name=None,
        prompt_snapshot_text=(
            "Create a concise EMIS consultation note from the supplied transcript and Working note. Use only supported facts and the recorded plan."
            if mode is TemplateMode.structured
            else "Write a concise patient follow-up message based only on the consultation and clinician dictation."
        ),
        structured_context_json=encrypt_json_for_owner(
            db, owner_user_id=owner.id, table="generated_documents", field="structured_context_json",
            record_id=document_id, plaintext=working_note,
        ),
        working_note_mode_snapshot=TranscriptWorkingNoteMode.structured,
        dictation_snapshot_encrypted=encrypt_text_for_owner(
            db, owner_user_id=owner.id, table="generated_documents", field="dictation_snapshot_encrypted",
            record_id=document_id, plaintext=dictation,
        ),
        structured_section_definitions_json=(
            {
                "profile": "emis",
                "sections": [
                    {"section_key": key, "section_label": EMIS_SECTION_LABELS[key], "section_order": i}
                    for i, key in enumerate(("problem", "history", "examination", "comment", "tasks"), start=1)
                ],
            }
            if mode is TemplateMode.structured else None
        ),
        generation_snapshot_json=(
            {
                "origin": "first_run_example",
                "submitted_template_config": StructuredTemplateConfig(
                    profile="emis",
                    sections=[
                        StructuredTemplateSectionConfig(section_key=key, instruction=label, section_order=i)
                        for i, (key, label) in enumerate(
                            (("problem", "Summarise the main problem."), ("history", "Summarise the reported history."),
                             ("examination", "Record examination findings."), ("comment", "Summarise the discussion."),
                             ("tasks", "Record the plan and safety net.")), start=1
                        )
                    ],
                ).model_dump(mode="json"),
            }
            if mode is TemplateMode.structured else {"origin": "first_run_example"}
        ),
        status=GeneratedDocumentStatus.ready,
        title=title,
        document_mode=mode,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used="Example",
        hallucination_check_status=HallucinationCheckStatus.not_applicable,
        started_at=now,
        completed_at=now,
        created_at=now,
        updated_at=now,
        follow_up_prompt_text=None,
    )
    set_generated_document_text(db, document=document, field="original_output_text_encrypted", plaintext=output)
    set_generated_document_text(db, document=document, field="edited_output_text_encrypted", plaintext=output)
    set_generated_document_structured_working_note_snapshot(db, document=document, plaintext=working_note)
    if follow_up_prompt is not None:
        set_generated_document_text(db, document=document, field="follow_up_prompt_text", plaintext=follow_up_prompt)
    db.add(document)
    db.flush()
    if mode is TemplateMode.structured:
        for order, (key, label, section_text) in enumerate(EXAMPLE_NOTE_SECTIONS, start=1):
            section_id = uuid4()
            db.add(GeneratedDocumentSection(
                id=section_id,
                generated_document_id=document.id,
                section_key=key,
                section_label=label,
                section_order=order,
                original_text_encrypted=encrypt_text_for_owner(
                    db, owner_user_id=owner.id, table="generated_document_sections", field="original_text_encrypted",
                    record_id=section_id, plaintext=section_text,
                ) or "",
                edited_text_encrypted=encrypt_text_for_owner(
                    db, owner_user_id=owner.id, table="generated_document_sections", field="edited_text_encrypted",
                    record_id=section_id, plaintext=section_text,
                ) or "",
            ))
    return document


def create_first_run_example_consultation(db: Session, *, owner: User) -> Transcript | None:
    """Create once under the owner lock; every content row commits together."""
    try:
        locked_owner = db.scalar(
            select(User).where(User.id == owner.id).with_for_update().execution_options(populate_existing=True)
        )
        if locked_owner is None:
            raise AppError(404, "not_found", "User not found")
        if locked_owner.is_system_admin or locked_owner.team_id is None or not locked_owner.example_consultation_pending:
            db.rollback()
            return None
        existing = db.scalar(select(Transcript.id).where(Transcript.owner_user_id == locked_owner.id).limit(1))
        if existing is not None:
            locked_owner.example_consultation_pending = False
            db.add(locked_owner)
            db.commit()
            return None

        structured = EXAMPLE_WORKING_NOTE
        transcript = start_transcript(
            db,
            locked_owner,
            TranscriptStart(
                title="Example consultation",
                structured_context_json=structured,
                ingestion_mode=TranscriptIngestionMode.whole_file,
            ),
            commit=False,
            is_example=True,
        )
        commit_transcript_text(
            db,
            locked_owner,
            transcript_id=transcript.id,
            plaintext=EXAMPLE_TRANSCRIPT,
            commit=False,
            preview_redaction=False,
        )
        dictation = update_post_consultation_dictation(
            db,
            locked_owner,
            transcript_id=transcript.id,
            combined_text=EXAMPLE_DICTATION,
            commit=False,
        )
        saved_dictation = dictation_combined_text(db, dictation=dictation) or ""
        from app.models import TranscriptVersion
        version = db.scalar(
            select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id).order_by(TranscriptVersion.version_no.desc())
        )
        if version is None:
            raise AppError(500, "example_seed_failed", "Example consultation could not be saved")
        _save_example_document(
            db, owner=locked_owner, transcript=transcript, title="Example consultation note",
            generator_type=GeneratedDocumentGeneratorType.template,
            output="\n\n".join(f"{label}\n{text}" for _key, label, text in EXAMPLE_NOTE_SECTIONS),
            mode=TemplateMode.structured, version_id=version.id, working_note=structured,
            dictation=saved_dictation,
        )
        _save_example_document(
            db, owner=locked_owner, transcript=transcript, title="Example follow-up",
            generator_type=GeneratedDocumentGeneratorType.followup,
            output=EXAMPLE_FOLLOWUP, mode=TemplateMode.freeform, version_id=version.id,
            working_note=structured, dictation=saved_dictation, follow_up_prompt=EXAMPLE_FOLLOWUP_PROMPT,
        )
        locked_owner.example_consultation_pending = False
        db.add(locked_owner)
        db.commit()
        db.refresh(transcript)
        return transcript
    except Exception:
        db.rollback()
        raise
