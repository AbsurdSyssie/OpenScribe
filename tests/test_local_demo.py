from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    DefaultPromptTemplate,
    DefaultQuickAction,
    GeneratedDocument,
    GeneratedDocumentSection,
    ProviderAttempt,
    ProviderUsageEvent,
    PromptTemplate,
    QuickAction,
    RedactionEntity,
    RedactionRun,
    TaskDispatchOutbox,
    Team,
    TeamRole,
    Transcript,
    TranscriptVersion,
    User,
)
from app.services.default_assets import (
    BUILTIN_DEFAULT_QUICK_ACTIONS,
    BUILTIN_DEFAULT_TEMPLATES,
)
from app.services.local_demo import (
    DEMO_GENERATION_MARKER,
    DEMO_NOTE_SECTIONS,
    DEMO_PROVIDER_LABEL,
    DEMO_TRANSCRIPT_TITLE,
    bootstrap_local_demo,
)
from app.services.passwords import validate_password_strength
from app.services.templates import generated_document_section_text
from scripts.seed_demo import marker_matches_database, require_demo_bootstrap_enabled


def test_bootstrap_local_demo_creates_owner_scoped_encrypted_example_once(db_session):
    accounts = bootstrap_local_demo(
        db_session,
        team_name="OpenScribe Demo Team",
        admin_email="admin@openscribe.local",
        leader_email="leader@openscribe.local",
        clinician_email="clinician@openscribe.local",
        password="OpenScribeLocal27",
    )

    assert {key: accounts[key] for key in ("admin_email", "leader_email", "clinician_email")} == {
        "admin_email": "admin@openscribe.local",
        "leader_email": "leader@openscribe.local",
        "clinician_email": "clinician@openscribe.local",
    }
    admin = db_session.scalar(select(User).where(User.email == accounts["admin_email"]))
    leader = db_session.scalar(select(User).where(User.email == accounts["leader_email"]))
    clinician = db_session.scalar(select(User).where(User.email == accounts["clinician_email"]))
    assert admin is not None and admin.is_system_admin and admin.team_id is None
    assert leader is not None and leader.team_role is TeamRole.leader
    assert clinician is not None and clinician.team_role is TeamRole.user
    assert leader.team_id == clinician.team_id
    assert {
        item.name for item in db_session.scalars(select(DefaultPromptTemplate))
    } == {item["name"] for item in BUILTIN_DEFAULT_TEMPLATES}
    assert {
        item.name for item in db_session.scalars(select(DefaultQuickAction))
    } == {item["name"] for item in BUILTIN_DEFAULT_QUICK_ACTIONS}
    assert {
        item.name
        for item in db_session.scalars(
            select(PromptTemplate).where(PromptTemplate.team_id == leader.team_id)
        )
    } == {item["name"] for item in BUILTIN_DEFAULT_TEMPLATES}
    assert {
        item.name
        for item in db_session.scalars(
            select(QuickAction).where(QuickAction.team_id == leader.team_id)
        )
    } == {item["name"] for item in BUILTIN_DEFAULT_QUICK_ACTIONS}

    transcript = db_session.scalar(
        select(Transcript).where(
            Transcript.owner_user_id == clinician.id,
            Transcript.title == DEMO_TRANSCRIPT_TITLE,
        )
    )
    assert transcript is not None
    assert transcript.owner_user_id == clinician.id
    assert transcript.team_id == clinician.team_id
    assert transcript.retention_days_applied == 30
    assert "Dry cough" not in transcript.current_draft_text_encrypted
    assert "Dry cough" not in (transcript.structured_context_json or "")
    assert db_session.scalars(
        select(Transcript).where(Transcript.owner_user_id.in_([admin.id, leader.id]))
    ).all() == []

    version = db_session.scalar(
        select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id)
    )
    assert version is not None
    assert "Dry cough" not in version.text_encrypted
    redaction = db_session.scalar(
        select(RedactionRun).where(RedactionRun.transcript_version_id == version.id)
    )
    assert redaction is not None
    assert redaction.owner_user_id == clinician.id
    assert redaction.entity_count > 0
    assert "presidio" in redaction.api_provider.lower()
    entities = db_session.scalars(
        select(RedactionEntity).where(RedactionEntity.redaction_run_id == redaction.id)
    ).all()
    assert entities
    assert all("Jane Smith" not in entity.original_value_encrypted for entity in entities)

    document = db_session.scalar(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
    )
    assert document is not None
    assert document.owner_user_id == clinician.id
    assert document.redaction_run_id == redaction.id
    assert document.model_used == DEMO_PROVIDER_LABEL
    assert document.llm_config_id is None
    assert "Dry cough" not in document.original_output_text_encrypted
    assert "Dry cough" not in document.edited_output_text_encrypted
    assert document.generation_snapshot_json == {
        "local_demo_marker": DEMO_GENERATION_MARKER,
        "origin": "synthetic_example",
        "provider_label": DEMO_PROVIDER_LABEL,
    }
    sections = list(
        db_session.scalars(
            select(GeneratedDocumentSection)
            .where(GeneratedDocumentSection.generated_document_id == document.id)
            .order_by(GeneratedDocumentSection.section_order)
        )
    )
    assert [section.section_key for section in sections] == [
        key for key, _label, _text in DEMO_NOTE_SECTIONS
    ]
    assert all(
        generated_document_section_text(
            db_session,
            section=section,
            field="edited_text_encrypted",
        )
        for section in sections
    )
    assert db_session.scalars(select(ProviderAttempt)).all() == []
    assert db_session.scalars(select(ProviderUsageEvent)).all() == []
    assert db_session.scalars(select(TaskDispatchOutbox)).all() == []

    original_password_hash = clinician.password_hash
    clinician.full_name = "Changed by evaluator"
    db_session.add(clinician)
    db_session.commit()

    bootstrap_local_demo(
        db_session,
        team_name="OpenScribe Demo Team",
        admin_email="admin@openscribe.local",
        leader_email="leader@openscribe.local",
        clinician_email="clinician@openscribe.local",
        password="OpenScribeLocal27",
    )

    db_session.refresh(clinician)
    assert clinician.full_name == "Changed by evaluator"
    assert clinician.password_hash == original_password_hash
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == clinician.id)).all() == [
        transcript
    ]
    assert db_session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
    ).all() == [document]


def test_demo_password_matches_normal_policy() -> None:
    assert validate_password_strength("OpenScribeLocal27") == "OpenScribeLocal27"


def test_demo_seed_requires_local_environment_and_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_BOOTSTRAP_ENABLED", "true")
    with pytest.raises(SystemExit, match="only when APP_ENV is local or development"):
        require_demo_bootstrap_enabled()

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("DEMO_BOOTSTRAP_ENABLED")
    with pytest.raises(SystemExit, match="DEMO_BOOTSTRAP_ENABLED=true"):
        require_demo_bootstrap_enabled()

    monkeypatch.setenv("DEMO_BOOTSTRAP_ENABLED", "true")
    require_demo_bootstrap_enabled()


def test_seed_demo_marker_belongs_to_current_database(
    db_session,
    make_team,
    tmp_path: Path,
):
    team = make_team(name="Marker team")
    marker = tmp_path / "bootstrap-complete"
    marker.write_text(
        f'{{"team_id": "{team.id}", "version": "openscribe_local_demo_v2"}}\n',
        encoding="utf-8",
    )
    assert marker_matches_database(db_session, marker_path=marker)

    marker.write_text(
        '{"team_id": "00000000-0000-0000-0000-000000000001", '
        '"version": "openscribe_local_demo_v2"}\n',
        encoding="utf-8",
    )
    assert not marker_matches_database(db_session, marker_path=marker)
    assert db_session.scalar(select(Team.id).where(Team.id == team.id)) == team.id
