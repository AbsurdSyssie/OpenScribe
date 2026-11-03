from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.errors import AppError
from app.models import (
    GeneratedDocumentStatus, TeamHallucinationCheckSelection, TeamRole,
    TemplateMode, TemplateScope, Transcript, TranscriptIngestionMode, TranscriptStatus,
    transcript_expiry,
)
from app.services.llm_checker_selection import (
    copy_hallucination_check_selection,
    freeze_hallucination_check_selection,
    read_hallucination_check_selection,
    retire_checker_snapshots_for_config,
)
from app.services.templates import queue_document_generation_from_template
from app.services.transcripts import set_transcript_draft_text
from app.services.llm_model_policy import model_allowed_for_provider


@pytest.fixture
def checker_documents(db_session, make_team, make_user, make_llm_config,
                     make_llm_selection, make_template):
    team = make_team(name=f"Checker snapshot {uuid4()}")
    admin = make_user(email=f"checker-admin-{uuid4()}@example.com", is_system_admin=True)
    owner = make_user(email=f"checker-owner-{uuid4()}@example.com", team=team, team_role=TeamRole.user)
    writing = make_llm_config(team=team, actor=admin, has_secret=False,
        model_name="writing-model", available_models_json=["writing-model"])
    make_llm_selection(config=writing, actor=admin, model_name_override="writing-model")
    checker = make_llm_config(team=team, actor=admin, has_secret=False,
        label="Checker", model_name="checker-model", available_models_json=["checker-model", "new-model"])
    selection = TeamHallucinationCheckSelection(
        team_id=team.id, llm_config_id=checker.id, model_name_override="checker-model",
        selected_by_user_id=admin.id,
    )
    db_session.add(selection)
    db_session.flush()
    assert checker.setup_status == "ready"
    assert model_allowed_for_provider(db_session, config=checker, model_name="checker-model")
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="Synthetic visit",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=transcript_expiry(30),
    )
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic source text.")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner,
        name="Synthetic checker template", prompt_text="Use synthetic evidence.", mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [{
            "section_key": "problem", "instruction": "Summarise synthetic evidence.", "section_order": 0,
        }]})
    db_session.commit()
    first = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id)
    second = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id)
    return SimpleNamespace(team=team, admin=admin, owner=owner, checker=checker,
        selection=selection, transcript=transcript, template=template, first=first, second=second)


def test_frozen_checker_keeps_model_and_copy_rebinds_encryption_aad(db_session, checker_documents):
    ctx = checker_documents
    target = SimpleNamespace(
        __tablename__="generated_documents", id=uuid4(), owner_user_id=ctx.owner.id,
        team_id=ctx.team.id, hallucination_check_selection_snapshot_encrypted=None,
        hallucination_check_selection_config_id=None,
    )
    accepted = read_hallucination_check_selection(db_session, source=ctx.first)
    assert accepted is not None and accepted.status == "configured", accepted
    ctx.selection.model_name_override = "new-model"
    ctx.checker.model_policy.enabled_models_json = ["new-model"]
    ctx.checker.model_policy.default_model_name = "new-model"
    db_session.commit()

    frozen = read_hallucination_check_selection(db_session, source=ctx.first, owner_user_id=ctx.owner.id)
    assert frozen is not None and frozen.status == "configured"
    assert frozen.model_name == "checker-model"
    copy_hallucination_check_selection(db_session, source=ctx.first, target=target)
    copied = read_hallucination_check_selection(db_session, source=target)
    assert copied is not None and copied.status == "configured"
    assert copied.model_name == "checker-model"
    assert target.hallucination_check_selection_snapshot_encrypted != ctx.first.hallucination_check_selection_snapshot_encrypted
    assert target.hallucination_check_selection_config_id == ctx.checker.id


def test_new_freeze_records_invalid_checker_and_keeps_config_reference(db_session, checker_documents):
    ctx = checker_documents
    ctx.checker.model_policy.enabled_models_json = []
    ctx.checker.model_policy.default_model_name = None
    db_session.commit()
    source = SimpleNamespace(
        __tablename__="generated_documents", id=uuid4(), owner_user_id=ctx.owner.id,
        team_id=ctx.team.id, hallucination_check_selection_snapshot_encrypted=None,
        hallucination_check_selection_config_id=None,
    )

    frozen = freeze_hallucination_check_selection(
        db_session, source=source, owner_user_id=ctx.owner.id)

    assert frozen.status == "invalid"
    assert source.hallucination_check_selection_config_id == ctx.checker.id
    assert read_hallucination_check_selection(db_session, source=source).status == "invalid"


def test_checker_reader_rejects_owner_mismatch_plaintext_and_detached_config_metadata(
    db_session, checker_documents,
):
    ctx = checker_documents
    with pytest.raises(AppError):
        read_hallucination_check_selection(db_session, source=ctx.first, owner_user_id=uuid4())
    original = ctx.first.hallucination_check_selection_snapshot_encrypted
    ctx.first.hallucination_check_selection_snapshot_encrypted = "plaintext"
    with pytest.raises(AppError):
        read_hallucination_check_selection(db_session, source=ctx.first)
    ctx.first.hallucination_check_selection_snapshot_encrypted = original
    ctx.first.hallucination_check_selection_config_id = None
    with pytest.raises(AppError):
        read_hallucination_check_selection(db_session, source=ctx.first)


def test_copy_rejects_cross_owner_or_team_scope(db_session, checker_documents, make_user, make_team):
    ctx = checker_documents
    other_team = make_team(name=f"Other checker scope {uuid4()}")
    other_owner = make_user(email=f"other-checker-owner-{uuid4()}@example.com", team=other_team)
    target = SimpleNamespace(
        __tablename__="generated_documents", id=uuid4(), owner_user_id=other_owner.id,
        team_id=other_team.id, hallucination_check_selection_snapshot_encrypted=None,
        hallucination_check_selection_config_id=None,
    )
    with pytest.raises(AppError):
        copy_hallucination_check_selection(db_session, source=ctx.first, target=target)


def test_retirement_clears_only_terminal_document_checker_snapshots(db_session, checker_documents):
    ctx = checker_documents
    ctx.first.status = GeneratedDocumentStatus.ready
    db_session.flush()

    retire_checker_snapshots_for_config(db_session, config_id=ctx.checker.id)
    db_session.flush()
    db_session.refresh(ctx.first)
    db_session.refresh(ctx.second)

    assert ctx.first.hallucination_check_selection_config_id is None
    assert ctx.first.hallucination_check_selection_snapshot_encrypted is None
    assert ctx.second.hallucination_check_selection_config_id == ctx.checker.id
    assert ctx.second.hallucination_check_selection_snapshot_encrypted
