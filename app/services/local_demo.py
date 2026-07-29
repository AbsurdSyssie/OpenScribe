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
    RedactionRun,
    RedactionRunStatus,
    Team,
    TeamRole,
    TeamStatus,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionMode,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
    UserEncryptionKey,
    UserOnboardingState,
    UserStatus,
    utcnow,
)
from app.normalization import normalize_email, normalize_team_name_key
from app.schemas import TranscriptStart
from app.schemas.templates import (
    PromptTemplateUpsert,
    StructuredTemplateConfig,
    StructuredTemplateSectionConfig,
)
from app.services.content_crypto import ensure_user_dek, encrypt_text_for_owner
from app.services.default_assets import (
    ensure_builtin_default_assets,
    ensure_builtin_team_assets,
    seed_team_default_assets,
)
from app.services.passwords import hash_password, validate_password_strength
from app.services.redaction import ensure_redaction_run_for_transcript_version
from app.services.templates import (
    set_generated_document_structured_working_note_snapshot,
    set_generated_document_text,
    upsert_personal_template,
)
from app.services.transcripts import commit_transcript_text, start_transcript


DEMO_TRANSCRIPT_TITLE = "Synthetic cough consultation"
DEMO_TEMPLATE_NAME = "Synthetic EMIS consultation note"
DEMO_GENERATION_MARKER = "openscribe_local_demo_v1"
DEMO_PROVIDER_LABEL = "OpenScribe synthetic example"

DEMO_TRANSCRIPT_TEXT = """Clinician: Please confirm your details.
Patient: My name is Jane Smith. I was born on 12 March 1985. My phone number is 07700 900123 and I live at 14 Example Road, Leeds.
Clinician: What brought you in today?
Patient: I have had a dry cough for three weeks. It started after a cold.
Clinician: Have you had shortness of breath, chest pain, coughing up blood, fever, weight loss, or night sweats?
Patient: No. I feel well otherwise.
Clinician: Do you smoke or take any regular medicines?
Patient: I do not smoke. I take ramipril for high blood pressure.
Clinician: Your temperature is 36.8 degrees, oxygen saturation is 98 percent on air, respiratory rate is 14, and your chest sounds clear.
Clinician: We will review whether ramipril may be contributing, agree the next step, and discuss when to seek urgent help."""

DEMO_WORKING_NOTE = {
    "profile": "emis",
    "sections": {
        "problem": ["Dry cough for three weeks."],
        "history": [
            "Started after a cold.",
            "No shortness of breath, chest pain, haemoptysis, fever, weight loss, or night sweats.",
            "Feels well otherwise.",
        ],
        "family_history": ["No relevant family history discussed."],
        "social_history": ["Does not smoke."],
        "examination": [
            "Temperature 36.8°C.",
            "Oxygen saturation 98% on air.",
            "Respiratory rate 14 breaths per minute.",
            "Chest clear on auscultation.",
        ],
        "comment": ["Ramipril may be contributing to the cough; requires clinical review."],
        "tasks": [
            "Review antihypertensive treatment.",
            "Give safety-net advice for breathing difficulty, chest pain, haemoptysis, or marked deterioration.",
        ],
        "investigations": ["Consider further investigation if the cough persists or the clinical picture changes."],
    },
}

DEMO_TEMPLATE_PROMPT = (
    "Write a concise EMIS consultation note from the saved transcript and Working note. "
    "Use only supported facts. Keep uncertainty and safety-netting clear."
)

DEMO_NOTE_SECTIONS = (
    ("problem", "Problem", "Dry cough for three weeks."),
    (
        "history",
        "History",
        "Dry cough began after a cold and has persisted for three weeks.\n"
        "No shortness of breath, chest pain, haemoptysis, fever, weight loss, or night sweats.\n"
        "Feels well otherwise. Takes ramipril for hypertension.",
    ),
    ("family_history", "Family history", "No relevant family history discussed."),
    ("social_history", "Social history", "Does not smoke."),
    (
        "examination",
        "Examination",
        "Temperature 36.8°C.\nOxygen saturation 98% on air.\n"
        "Respiratory rate 14 breaths per minute.\nChest clear on auscultation.",
    ),
    (
        "comment",
        "Comment",
        "Ramipril may be contributing to the cough. Review the diagnosis if symptoms persist or change.",
    ),
    (
        "tasks",
        "Tasks",
        "Review antihypertensive treatment.\n"
        "Advise urgent review for breathing difficulty, chest pain, haemoptysis, or marked deterioration.",
    ),
    (
        "investigations",
        "Investigations",
        "Consider further investigation if the cough persists or the clinical picture changes.",
    ),
)


def _latest_template_version(db: Session, *, template_id: UUID) -> PromptTemplateVersion | None:
    return db.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template_id)
        .order_by(PromptTemplateVersion.version_no.desc())
        .limit(1)
    )


def _latest_transcript_version(db: Session, *, transcript_id: UUID) -> TranscriptVersion | None:
    return db.scalar(
        select(TranscriptVersion)
        .where(TranscriptVersion.transcript_id == transcript_id)
        .order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.created_at.desc())
        .limit(1)
    )


def ensure_local_demo_team(db: Session, *, name: str) -> Team:
    name_key = normalize_team_name_key(name)
    team = db.scalar(select(Team).where(Team.name_key == name_key))
    if team is not None:
        return team
    team = Team(
        name=name,
        name_key=name_key,
        status=TeamStatus.active,
        default_retention_days=30,
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def ensure_local_demo_user(
    db: Session,
    *,
    full_name: str,
    email: str,
    password: str,
    team: Team | None,
    team_role: TeamRole | None,
    is_system_admin: bool,
) -> User:
    normalized_email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        validate_password_strength(password)
        user = User(
            full_name=full_name,
            email=normalized_email,
            password_hash=hash_password(password),
            team_id=team.id if team is not None else None,
            team_role=team_role,
            is_system_admin=is_system_admin,
            status=UserStatus.active,
            must_change_password=False,
            onboarding_state=UserOnboardingState.complete,
            mfa_required=False,
            mfa_enabled=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        expected_team_id = team.id if team is not None else None
        if (
            user.team_id != expected_team_id
            or user.team_role is not team_role
            or user.is_system_admin is not is_system_admin
        ):
            raise AppError(
                409,
                "local_demo_account_conflict",
                f"The existing account {normalized_email} does not match the local demo role.",
            )

    key = db.scalar(
        select(UserEncryptionKey)
        .where(UserEncryptionKey.user_id == user.id, UserEncryptionKey.is_active.is_(True))
        .limit(1)
    )
    if key is None:
        has_content = db.scalar(select(Transcript.id).where(Transcript.owner_user_id == user.id).limit(1))
        if has_content is not None:
            raise AppError(
                409,
                "local_demo_content_key_missing",
                f"The existing account {normalized_email} has content but no active content key.",
            )
        ensure_user_dek(db, user=user)
        db.commit()
        db.refresh(user)
    return user


def _ensure_demo_template(
    db: Session,
    *,
    clinician: User,
) -> tuple[PromptTemplate, PromptTemplateVersion]:
    template = db.scalar(
        select(PromptTemplate).where(
            PromptTemplate.scope == TemplateScope.user,
            PromptTemplate.owner_user_id == clinician.id,
            PromptTemplate.name == DEMO_TEMPLATE_NAME,
        )
    )
    if template is None:
        config = StructuredTemplateConfig(
            profile="emis",
            sections=[
                StructuredTemplateSectionConfig(
                    section_key=section_key,
                    instruction=f"Write the {label.lower()} section from supported facts only.",
                    section_order=index,
                )
                for index, (section_key, label, _text) in enumerate(DEMO_NOTE_SECTIONS, start=1)
            ],
        )
        template = upsert_personal_template(
            db,
            clinician,
            PromptTemplateUpsert(
                scope=TemplateScope.user,
                name=DEMO_TEMPLATE_NAME,
                description="A structured Template for the synthetic local example.",
                prompt_text=DEMO_TEMPLATE_PROMPT,
                mode=TemplateMode.structured,
                config_json=config,
                is_active=True,
            ),
        )
    version = _latest_template_version(db, template_id=template.id)
    if version is None:
        raise AppError(500, "local_demo_template_invalid", "The local demo Template has no version.")
    return template, version


def _existing_demo_transcript(db: Session, *, clinician: User) -> Transcript | None:
    return db.scalar(
        select(Transcript)
        .where(
            Transcript.owner_user_id == clinician.id,
            Transcript.title == DEMO_TRANSCRIPT_TITLE,
        )
        .order_by(Transcript.created_at.desc())
        .limit(1)
    )


def _ensure_demo_redaction(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
) -> RedactionRun:
    run = ensure_redaction_run_for_transcript_version(db, transcript_version=transcript_version)
    if run.status is not RedactionRunStatus.succeeded or run.entity_count < 1:
        raise AppError(
            500,
            "local_demo_redaction_failed",
            "Presidio did not create the synthetic transcript redaction preview.",
        )
    return run


def _section_definitions() -> dict[str, object]:
    return {
        "profile": "emis",
        "sections": [
            {
                "section_key": section_key,
                "section_label": label,
                "section_order": index,
            }
            for index, (section_key, label, _text) in enumerate(DEMO_NOTE_SECTIONS, start=1)
        ],
    }


def _existing_demo_document(db: Session, *, transcript: Transcript) -> GeneratedDocument | None:
    documents = db.scalars(
        select(GeneratedDocument).where(
            GeneratedDocument.transcript_id == transcript.id,
            GeneratedDocument.owner_user_id == transcript.owner_user_id,
        )
    )
    return next(
        (
            document
            for document in documents
            if (document.generation_snapshot_json or {}).get("local_demo_marker")
            == DEMO_GENERATION_MARKER
        ),
        None,
    )


def _create_demo_document(
    db: Session,
    *,
    clinician: User,
    transcript: Transcript,
    transcript_version: TranscriptVersion,
    redaction_run: RedactionRun,
    template: PromptTemplate,
    template_version: PromptTemplateVersion,
) -> GeneratedDocument:
    existing = _existing_demo_document(db, transcript=transcript)
    if existing is not None:
        return existing

    now = utcnow()
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=clinician.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=redaction_run.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template_version.id,
        llm_config_id=None,
        source_template_name=template.name,
        prompt_snapshot_text=template_version.prompt_text,
        generation_snapshot_json={
            "local_demo_marker": DEMO_GENERATION_MARKER,
            "origin": "synthetic_example",
            "provider_label": DEMO_PROVIDER_LABEL,
        },
        working_note_mode_snapshot=TranscriptWorkingNoteMode.structured,
        structured_section_definitions_json=_section_definitions(),
        status=GeneratedDocumentStatus.ready,
        title="Synthetic cough consultation note",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used=DEMO_PROVIDER_LABEL,
        started_at=now,
        completed_at=now,
        created_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
    )
    rendered_text = "\n\n".join(
        f"{label}\n{text}" for _key, label, text in DEMO_NOTE_SECTIONS
    )
    set_generated_document_text(
        db,
        document=document,
        field="original_output_text_encrypted",
        plaintext=rendered_text,
    )
    set_generated_document_text(
        db,
        document=document,
        field="edited_output_text_encrypted",
        plaintext=rendered_text,
    )
    set_generated_document_structured_working_note_snapshot(
        db,
        document=document,
        plaintext=DEMO_WORKING_NOTE,
    )
    db.add(document)
    db.flush()

    for index, (section_key, label, text) in enumerate(DEMO_NOTE_SECTIONS, start=1):
        section_id = uuid4()
        db.add(
            GeneratedDocumentSection(
                id=section_id,
                generated_document_id=document.id,
                section_key=section_key,
                section_label=label,
                section_order=index,
                original_text_encrypted=encrypt_text_for_owner(
                    db,
                    owner_user_id=clinician.id,
                    table="generated_document_sections",
                    field="original_text_encrypted",
                    record_id=section_id,
                    plaintext=text,
                )
                or "",
                edited_text_encrypted=encrypt_text_for_owner(
                    db,
                    owner_user_id=clinician.id,
                    table="generated_document_sections",
                    field="edited_text_encrypted",
                    record_id=section_id,
                    plaintext=text,
                )
                or "",
                is_edited=False,
            )
        )
    db.commit()
    db.refresh(document)
    return document


def ensure_local_demo_consultation(db: Session, *, clinician: User) -> Transcript:
    if clinician.is_system_admin or clinician.team_id is None:
        raise AppError(403, "forbidden", "The synthetic consultation needs a team clinician.")

    template, template_version = _ensure_demo_template(db, clinician=clinician)
    transcript = _existing_demo_transcript(db, clinician=clinician)
    if transcript is None:
        transcript = start_transcript(
            db,
            clinician,
            TranscriptStart(
                title=DEMO_TRANSCRIPT_TITLE,
                current_draft_text_encrypted=DEMO_TRANSCRIPT_TEXT,
                structured_context_json=DEMO_WORKING_NOTE,
                ingestion_mode=TranscriptIngestionMode.whole_file,
            ),
        )
        transcript = commit_transcript_text(
            db,
            clinician,
            transcript_id=transcript.id,
            plaintext=DEMO_TRANSCRIPT_TEXT,
        )

    transcript_version = _latest_transcript_version(db, transcript_id=transcript.id)
    if transcript_version is None:
        transcript = commit_transcript_text(
            db,
            clinician,
            transcript_id=transcript.id,
            plaintext=DEMO_TRANSCRIPT_TEXT,
        )
        transcript_version = _latest_transcript_version(db, transcript_id=transcript.id)
    if transcript_version is None:
        raise AppError(500, "local_demo_transcript_invalid", "The synthetic transcript has no saved version.")

    redaction_run = _ensure_demo_redaction(db, transcript_version=transcript_version)
    _create_demo_document(
        db,
        clinician=clinician,
        transcript=transcript,
        transcript_version=transcript_version,
        redaction_run=redaction_run,
        template=template,
        template_version=template_version,
    )
    return transcript


def bootstrap_local_demo(
    db: Session,
    *,
    team_name: str,
    admin_email: str,
    leader_email: str,
    clinician_email: str,
    password: str,
) -> dict[str, str]:
    team = ensure_local_demo_team(db, name=team_name)
    admin = ensure_local_demo_user(
        db,
        full_name="OpenScribe Demo Admin",
        email=admin_email,
        password=password,
        team=None,
        team_role=None,
        is_system_admin=True,
    )
    leader = ensure_local_demo_user(
        db,
        full_name="OpenScribe Demo Leader",
        email=leader_email,
        password=password,
        team=team,
        team_role=TeamRole.leader,
        is_system_admin=False,
    )
    clinician = ensure_local_demo_user(
        db,
        full_name="OpenScribe Demo Clinician",
        email=clinician_email,
        password=password,
        team=team,
        team_role=TeamRole.user,
        is_system_admin=False,
    )
    ensure_builtin_default_assets(db, admin)
    seed_team_default_assets(db, team=team, actor=admin)
    ensure_builtin_team_assets(db, team=team, actor=leader)
    db.commit()
    ensure_local_demo_consultation(db, clinician=clinician)
    return {
        "team_id": str(team.id),
        "admin_email": admin.email,
        "leader_email": leader.email,
        "clinician_email": clinician.email,
    }
