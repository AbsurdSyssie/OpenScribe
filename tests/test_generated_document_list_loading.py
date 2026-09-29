from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.errors import AppError
from app.models import (
    RedactionEntity,
    RedactionRun,
    RedactionRunStatus,
    GeneratedDocumentSection,
    TeamRole,
    TemplateMode,
    Transcript,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services import templates as template_service
from app.services.content_crypto import ensure_user_dek
from app.services.content_crypto import encrypt_text_for_owner
from app.web.presentation import generated_document_response


def _section_select_count(statements: list[str]) -> int:
    return sum(
        statement.lstrip().upper().startswith("SELECT")
        and "generated_document_sections" in statement.lower()
        for statement in statements
    )


def _redaction_run_select_count(statements: list[str]) -> int:
    return sum(
        statement.lstrip().upper().startswith("SELECT")
        and "redaction_runs" in statement.lower()
        for statement in statements
    )


def _redaction_entity_select_count(statements: list[str]) -> int:
    return sum(
        statement.lstrip().upper().startswith("SELECT")
        and "redaction_entities" in statement.lower()
        for statement in statements
    )


def test_generated_document_list_batches_sections_and_preserves_owner_root_guards(
    db_session,
    make_generated_document,
    make_team,
    make_user,
):
    team = make_team(name="Generated document list loading")
    owner = make_user(email="generated-list-owner@example.com", team=team)
    same_team_leader = make_user(
        email="generated-list-leader@example.com",
        team=team,
        team_role=TeamRole.leader,
    )
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Synthetic generated document list",
        status=TranscriptStatus.ready,
        current_draft_text_encrypted="",
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="",
    )
    db_session.add(transcript_version)
    db_session.commit()
    freeform = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=transcript_version,
        title="Freeform",
        output_text="Synthetic freeform text",
    )
    structured = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=transcript_version,
        title="Structured",
        output_text="Synthetic structured text",
    )
    structured.document_mode = TemplateMode.structured
    structured.created_at = freeform.created_at + timedelta(seconds=1)
    distinct_first = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=transcript_version,
        title="Distinct first",
        output_text="Synthetic distinct first text",
    )
    distinct_second = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=transcript_version,
        title="Distinct second",
        output_text="Synthetic distinct second text",
    )
    no_redaction_run = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=transcript_version,
        title="No redaction run",
        output_text="Synthetic no-redaction text",
    )
    distinct_first.created_at = structured.created_at + timedelta(seconds=1)
    distinct_second.created_at = distinct_first.created_at + timedelta(seconds=1)
    no_redaction_run.created_at = distinct_second.created_at + timedelta(seconds=1)
    ensure_user_dek(db_session, user=owner)
    for document, text in (
        (freeform, "Synthetic freeform text"),
        (structured, "Synthetic structured text"),
        (distinct_first, "Synthetic distinct first text"),
        (distinct_second, "Synthetic distinct second text"),
        (no_redaction_run, "Synthetic no-redaction text"),
    ):
        for field in ("original_output_text_encrypted", "edited_output_text_encrypted"):
            template_service.set_generated_document_text(
                db_session, document=document, field=field, plaintext=text
            )
    runs = [
        RedactionRun(
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            owner_user_id=owner.id,
            team_id=team.id,
            status=RedactionRunStatus.succeeded,
            redacted_text_encrypted="",
            mapping_hash="synthetic-shared-hash",
            entity_count=2,
            api_provider="synthetic",
            api_model_or_version="test",
        ),
        RedactionRun(
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            owner_user_id=owner.id,
            team_id=team.id,
            status=RedactionRunStatus.succeeded,
            redacted_text_encrypted="",
            mapping_hash="synthetic-distinct-first-hash",
            entity_count=1,
            api_provider="synthetic",
            api_model_or_version="test",
        ),
        RedactionRun(
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            owner_user_id=owner.id,
            team_id=team.id,
            status=RedactionRunStatus.succeeded,
            redacted_text_encrypted="",
            mapping_hash="synthetic-distinct-second-hash",
            entity_count=1,
            api_provider="synthetic",
            api_model_or_version="test",
        ),
    ]
    db_session.add_all(runs)
    db_session.flush()
    freeform.redaction_run_id = runs[0].id
    structured.redaction_run_id = runs[0].id
    distinct_first.redaction_run_id = runs[1].id
    distinct_second.redaction_run_id = runs[2].id
    entities = [
        (runs[0], 2, "PHONE", "[PHONE-2]", "Synthetic shared second"),
        (runs[0], 1, "PERSON", "[PERSON-1]", "Synthetic shared first"),
        (runs[1], 1, "DATE", "[DATE-1]", "Synthetic distinct first"),
        (runs[2], 1, "LOCATION", "[LOCATION-1]", "Synthetic distinct second"),
    ]
    for run, entity_order, entity_type, placeholder, original_value in entities:
        entity_id = uuid4()
        db_session.add(
            RedactionEntity(
                id=entity_id,
                redaction_run_id=run.id,
                entity_order=entity_order,
                entity_type=entity_type,
                placeholder=placeholder,
                original_value_encrypted=encrypt_text_for_owner(
                    db_session,
                    owner_user_id=owner.id,
                    table="redaction_entities",
                    field="original_value_encrypted",
                    record_id=entity_id,
                    plaintext=original_value,
                ),
                normalized_value_hash=f"synthetic-{entity_order}-{placeholder}",
                occurrence_count=entity_order,
            )
        )
    # Insert the later section first; relationship ordering must still use section_order.
    sections = [
        GeneratedDocumentSection(
            generated_document_id=structured.id,
            section_key="tasks",
            section_label="Tasks",
            section_order=2,
            original_text_encrypted="",
            edited_text_encrypted="",
        ),
        GeneratedDocumentSection(
            generated_document_id=structured.id,
            section_key="history",
            section_label="History",
            section_order=0,
            original_text_encrypted="",
            edited_text_encrypted="",
        ),
    ]
    db_session.add_all(sections)
    db_session.flush()
    for section in sections:
        for field in ("original_text_encrypted", "edited_text_encrypted"):
            template_service.set_generated_document_section_text(
                db_session,
                section=section,
                field=field,
                owner_user_id=owner.id,
                plaintext=f"Synthetic {section.section_key} {field.split('_')[0]}",
            )
    db_session.commit()

    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    db_session.expire_all()
    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        documents = template_service.list_generated_documents_for_transcript(
            db_session, owner, transcript_id=transcript.id
        )
        payloads = [generated_document_response(db_session, document, actor=owner) for document in documents]
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert _section_select_count(statements) == 1
    assert _redaction_run_select_count(statements) == 1
    assert _redaction_entity_select_count(statements) == 1
    assert [document.id for document in documents] == [
        no_redaction_run.id,
        distinct_second.id,
        distinct_first.id,
        structured.id,
        freeform.id,
    ]
    structured_payload = next(payload for payload in payloads if payload.id == structured.id)
    freeform_payload = next(payload for payload in payloads if payload.id == freeform.id)
    assert [(section.section_key, section.section_order) for section in structured_payload.sections] == [
        ("history", 0),
        ("tasks", 2),
    ]
    assert [section.edited_text_encrypted for section in structured_payload.sections] == [
        "Synthetic history edited",
        "Synthetic tasks edited",
    ]
    assert freeform_payload.edited_output_text == "Synthetic freeform text"
    assert freeform_payload.sections == []
    assert [entity.placeholder for entity in structured_payload.pii_entities] == ["[PERSON-1]", "[PHONE-2]"]
    assert [entity.occurrence_count for entity in structured_payload.pii_entities] == [1, 2]
    for payload in payloads:
        for entity in payload.pii_entities:
            entity_payload = entity.model_dump()
            assert set(entity_payload) == {"entity_type", "placeholder", "occurrence_count", "has_value"}
            assert "original_value_encrypted" not in entity_payload
            assert "normalized_value_hash" not in entity_payload
    assert next(payload for payload in payloads if payload.id == no_redaction_run.id).pii_entities == []

    for actor, expires_at, status_code in (
        (same_team_leader, transcript.retention_expires_at, 403),
        (owner, utcnow() - timedelta(seconds=1), 404),
    ):
        transcript.retention_expires_at = expires_at
        db_session.commit()
        db_session.expire_all()
        statements.clear()
        event.listen(bind, "before_cursor_execute", capture_statement)
        try:
            with pytest.raises(AppError) as exc_info:
                template_service.list_generated_documents_for_transcript(
                    db_session, actor, transcript_id=transcript.id
                )
        finally:
            event.remove(bind, "before_cursor_execute", capture_statement)
        assert exc_info.value.status_code == status_code
        assert _section_select_count(statements) == 0
        assert _redaction_run_select_count(statements) == 0
        assert _redaction_entity_select_count(statements) == 0
