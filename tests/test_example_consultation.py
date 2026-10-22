from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentSection,
    PostConsultationDictation,
    ProviderAttempt,
    ProviderUsageEvent,
    RedactionRun,
    TaskDispatchOutbox,
    Transcript,
    TranscriptVersion,
    TeamRole,
    utcnow,
)
from app.schemas.users import UserCreate
from app.schemas.templates import RegenerationSteeringPreset
from app.services.content_crypto import decrypt_text_for_owner
from app.services.dictations import dictation_combined_text
from app.services.example_consultation import (
    EXAMPLE_DICTATION,
    EXAMPLE_FOLLOWUP,
    EXAMPLE_NOTE_SECTIONS,
    EXAMPLE_TRANSCRIPT,
    create_first_run_example_consultation,
)
from app.services.transcripts import delete_transcripts
from app.services.templates import (
    _document_template_config,
    _prompt_snapshot_text_for_document,
    generated_document_section_text,
    generated_document_structured_working_note_snapshot,
    generated_document_text,
    queue_generated_document_regeneration,
)
from app.services.transcripts import transcript_draft_text, transcript_structured_context
from app.services.admin import create_user


def test_first_run_example_is_owner_encrypted_complete_and_idempotent(db_session, make_user):
    owner = make_user(email="example-owner@example.com")
    owner.example_consultation_pending = True
    db_session.commit()

    transcript = create_first_run_example_consultation(db_session, owner=owner)

    assert transcript is not None
    assert transcript.is_example is True
    assert transcript.owner_user_id == owner.id
    assert transcript.team_id == owner.team_id
    assert transcript.retention_days_applied == owner.team.default_retention_days
    assert transcript.retention_expires_at is not None
    db_session.refresh(owner)
    assert owner.example_consultation_pending is False
    assert transcript_draft_text(db_session, transcript=transcript) == EXAMPLE_TRANSCRIPT
    assert "Dry cough" not in (transcript.current_draft_text_encrypted or "")
    assert transcript_structured_context(db_session, transcript=transcript)["profile"] == "emis"

    version = db_session.scalar(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id))
    assert version is not None
    assert decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
        record_id=version.id, stored_value=version.text_encrypted,
    ) == EXAMPLE_TRANSCRIPT

    dictation = db_session.scalar(
        select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == transcript.id)
    )
    assert dictation is not None
    assert dictation_combined_text(db_session, dictation=dictation) == EXAMPLE_DICTATION
    assert EXAMPLE_DICTATION not in (dictation.combined_edited_text_encrypted or "")

    documents = list(
        db_session.scalars(
            select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id).order_by(GeneratedDocument.title)
        )
    )
    assert [document.title for document in documents] == ["Example consultation note", "Example follow-up"]
    for document in documents:
        assert document.owner_user_id == owner.id
        assert document.team_id == owner.team_id
        assert document.retention_expires_at == transcript.retention_expires_at
        assert document.redaction_run_id is None
        assert document.llm_config_id is None
        assert document.model_used == "Example"
        assert document.dictation_snapshot_encrypted
        assert document.structured_working_note_snapshot_json
        assert document.dictation_snapshot_encrypted != EXAMPLE_DICTATION
    followup = next(document for document in documents if document.title == "Example follow-up")
    assert generated_document_text(db_session, document=followup, field="edited_output_text_encrypted") == EXAMPLE_FOLLOWUP
    note = next(document for document in documents if document.title == "Example consultation note")
    assert note.generation_snapshot_json["submitted_template_config"]["profile"] == "emis"
    assert note.structured_section_definitions_json["profile"] == "emis"
    assert _document_template_config(db_session, document=note) is not None
    assert _prompt_snapshot_text_for_document(db_session, document=note).startswith("Create a concise EMIS")
    assert generated_document_structured_working_note_snapshot(db_session, document=note)["profile"] == "emis"
    sections = list(
        db_session.scalars(
            select(GeneratedDocumentSection)
            .where(GeneratedDocumentSection.generated_document_id == note.id)
            .order_by(GeneratedDocumentSection.section_order)
        )
    )
    assert [section.section_key for section in sections] == [key for key, _label, _text in EXAMPLE_NOTE_SECTIONS]
    assert all(generated_document_section_text(db_session, section=section, field="edited_text_encrypted") for section in sections)

    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == owner.id)).all() == [transcript]
    assert db_session.scalars(select(ProviderAttempt)).all() == []
    assert db_session.scalars(select(ProviderUsageEvent)).all() == []
    assert db_session.scalars(select(TaskDispatchOutbox)).all() == []
    assert db_session.scalars(select(RedactionRun).where(RedactionRun.transcript_id == transcript.id)).all() == []

    assert create_first_run_example_consultation(db_session, owner=owner) is None
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == owner.id)).all() == [transcript]
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all() == documents


def test_example_seed_skips_admin_and_existing_consultation(db_session, make_user):
    admin = make_user(email="example-admin@example.com", is_system_admin=True)
    assert create_first_run_example_consultation(db_session, owner=admin) is None
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == admin.id)).all() == []

    owner = make_user(email="example-used@example.com")
    owner.example_consultation_pending = True
    db_session.commit()
    from app.schemas.transcripts import TranscriptStart
    from app.services.transcripts import start_transcript

    existing = start_transcript(db_session, owner, TranscriptStart(title="Already started"))
    assert create_first_run_example_consultation(db_session, owner=owner) is None
    db_session.refresh(owner)
    assert owner.example_consultation_pending is False
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == owner.id)).all() == [existing]


def test_ordinary_consultation_start_clears_pending_example_marker(db_session, make_user):
    owner = make_user(email="example-normal-start@example.com")
    owner.example_consultation_pending = True
    db_session.commit()
    from app.schemas.transcripts import TranscriptStart
    from app.services.transcripts import start_transcript

    transcript = start_transcript(db_session, owner, TranscriptStart(title="First real consultation"))
    db_session.refresh(owner)
    assert transcript.is_example is False
    assert owner.example_consultation_pending is False


def test_production_account_creation_marks_only_team_users_for_example(db_session, make_team, make_user):
    team = make_team(name="Example account creation team")
    admin = make_user(email="example-provisioning-admin@example.com", is_system_admin=True)
    created_user = create_user(
        db_session,
        UserCreate(
            email="example-provisioned@example.com",
            temporary_password="SecureExamplePass2",
            team_id=team.id,
            team_role=TeamRole.user,
        ),
        actor=admin,
    )
    assert created_user.example_consultation_pending is True
    assert create_first_run_example_consultation(db_session, owner=created_user) is not None

    created_admin = create_user(
        db_session,
        UserCreate(
            email="example-provisioned-admin@example.com",
            temporary_password="SecureExamplePass3",
            is_system_admin=True,
        ),
        actor=admin,
    )
    assert created_admin.example_consultation_pending is False
    assert create_first_run_example_consultation(db_session, owner=created_admin) is None


def test_failed_seed_rolls_back_all_content_and_keeps_pending_marker(db_session, make_user, monkeypatch):
    owner = make_user(email="example-rollback@example.com")
    owner.example_consultation_pending = True
    db_session.commit()

    def fail_after_sources(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr("app.services.example_consultation._save_example_document", fail_after_sources)
    with pytest.raises(RuntimeError, match="fixture failure"):
        create_first_run_example_consultation(db_session, owner=owner)

    db_session.refresh(owner)
    assert owner.example_consultation_pending is True
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == owner.id)).all() == []
    assert db_session.scalars(select(PostConsultationDictation).where(PostConsultationDictation.owner_user_id == owner.id)).all() == []
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.owner_user_id == owner.id)).all() == []


def test_deleted_or_expired_example_never_reappears(db_session, make_user):
    owner = make_user(email="example-lifecycle@example.com")
    owner.example_consultation_pending = True
    db_session.commit()
    transcript = create_first_run_example_consultation(db_session, owner=owner)
    assert transcript is not None

    assert delete_transcripts(db_session, owner, transcript_ids=[transcript.id]) == 1
    assert create_first_run_example_consultation(db_session, owner=owner) is None
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == owner.id)).all() == []

    another = make_user(email="example-expired@example.com", team=owner.team)
    another.example_consultation_pending = True
    db_session.commit()
    expired = create_first_run_example_consultation(db_session, owner=another)
    assert expired is not None
    expired.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert create_first_run_example_consultation(db_session, owner=another) is None


def test_example_note_regeneration_uses_saved_structured_and_dictation_snapshots(
    db_session, make_user, make_llm_config, make_llm_selection
):
    owner = make_user(email="example-regeneration@example.com")
    owner.example_consultation_pending = True
    db_session.commit()
    transcript = create_first_run_example_consultation(db_session, owner=owner)
    note = db_session.scalar(
        select(GeneratedDocument).where(
            GeneratedDocument.transcript_id == transcript.id,
            GeneratedDocument.generator_type == GeneratedDocumentGeneratorType.template,
        )
    )
    config = make_llm_config(
        team=owner.team,
        actor=owner,
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])

    regenerated = queue_generated_document_regeneration(
        db_session,
        owner,
        generated_document_id=note.id,
        steering_preset=RegenerationSteeringPreset.less_detail,
    )

    assert regenerated.parent_generated_document_id == note.id
    assert regenerated.transcript_version_id == note.transcript_version_id
    assert regenerated.template_version_id is None
    assert regenerated.prompt_snapshot_text == note.prompt_snapshot_text
    assert regenerated.generation_snapshot_json["submitted_template_config"] == note.generation_snapshot_json["submitted_template_config"]
    assert _document_template_config(db_session, document=regenerated) is not None
    assert generated_document_text(
        db_session, document=regenerated, field="dictation_snapshot_encrypted"
    ) == EXAMPLE_DICTATION
    assert generated_document_structured_working_note_snapshot(
        db_session, document=regenerated
    )["profile"] == "emis"
    assert regenerated.dictation_snapshot_encrypted != note.dictation_snapshot_encrypted
    assert regenerated.structured_working_note_snapshot_json != note.structured_working_note_snapshot_json
