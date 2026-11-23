"""Compact workspace output decrypts selected documents only.

This changes the workspace REST/SSE response, not the explicit full document
list or initial HTML render, which preserve their pre-existing content shape.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from app.models import (
    GeneratedDocumentGeneratorType,
    Transcript,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.web import presentation, transcribe_workspace


def test_compact_document_summary_never_decrypts_clinical_text(
    db_session, make_team, make_user, make_generated_document, monkeypatch,
):
    team = make_team(name="Compact document summary")
    owner = make_user(email=f"compact-document-{uuid4()}@example.com", team=team)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Synthetic transcript",
        status=TranscriptStatus.ready,
        current_draft_text_encrypted="",
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="")
    db_session.add(version)
    db_session.commit()

    doc = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=version,
        title="Synthetic note",
        output_text="Synthetic clinical text",
    )

    document_decrypt = Mock(side_effect=AssertionError("historical document text was decrypted"))
    section_decrypt = Mock(side_effect=AssertionError("historical section was decrypted"))
    request_decrypt = Mock(side_effect=AssertionError("historical request payload was decrypted"))
    monkeypatch.setattr(presentation, "generated_document_text_service", document_decrypt)
    monkeypatch.setattr(presentation, "generated_document_section_text_service", section_decrypt)
    monkeypatch.setattr(presentation, "generated_document_llm_request_payload_service", request_decrypt)

    summary = presentation.generated_document_response(
        db_session, doc, actor=owner, include_content=False,
    )

    assert summary.id == doc.id
    assert summary.title == "Synthetic note"
    assert summary.status == doc.status
    assert summary.content_hydrated is False
    assert summary.original_output_text == ""
    assert summary.edited_output_text == ""
    assert summary.llm_request_payload_json is None
    assert summary.sections == []
    document_decrypt.assert_not_called()
    section_decrypt.assert_not_called()
    request_decrypt.assert_not_called()


def test_compact_workspace_hydrates_only_selected_document_ids():
    note_a, note_b, followup_a, followup_b = [uuid4() for _ in range(4)]
    documents = [
        SimpleNamespace(id=note_a, generator_type=GeneratedDocumentGeneratorType.template),
        SimpleNamespace(id=note_b, generator_type=GeneratedDocumentGeneratorType.template),
        SimpleNamespace(id=followup_a, generator_type=GeneratedDocumentGeneratorType.followup),
        SimpleNamespace(id=followup_b, generator_type=GeneratedDocumentGeneratorType.quick_action),
    ]
    selected = transcribe_workspace.selected_workspace_document_ids(
        documents, selected_note_document_id=note_b,
        selected_followup_document_id=followup_b,
    )
    assert selected == frozenset({note_b, followup_b})

    # Wrong-type and unknown IDs must not cause arbitrary document hydration.
    selected = transcribe_workspace.selected_workspace_document_ids(
        documents, selected_note_document_id=followup_a,
        selected_followup_document_id=uuid4(),
    )
    assert selected == frozenset({note_a, followup_a})

    # With no selectors, the newest document of each category remains visible.
    assert transcribe_workspace.selected_workspace_document_ids(documents) == frozenset({
        note_a, followup_a,
    })


def test_workspace_response_only_requests_content_for_selected_documents(
    db_session, make_user, make_team, monkeypatch,
):
    team = make_team(name="Workspace compact selection")
    owner = make_user(email=f"compact-response-{uuid4()}@example.com", team=team)
    ids = [uuid4() for _ in range(3)]
    documents = [
        SimpleNamespace(id=ids[0], generator_type=GeneratedDocumentGeneratorType.template),
        SimpleNamespace(id=ids[1], generator_type=GeneratedDocumentGeneratorType.template),
        SimpleNamespace(id=ids[2], generator_type=GeneratedDocumentGeneratorType.followup),
    ]
    serialize_document = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(transcribe_workspace, "generated_document_response", serialize_document)
    # Capture the composition boundary without requiring unrelated workspace
    # response fields to satisfy the full API model in this focused unit test.
    monkeypatch.setattr(
        transcribe_workspace, "TranscribeWorkspaceDetail",
        lambda **payload: SimpleNamespace(**payload),
    )

    transcribe_workspace.transcribe_workspace_response(
        db_session, {"generated_documents": documents},
        current_user=owner, compact_documents=True,
        selected_note_document_id=ids[1],
        selected_followup_document_id=ids[2],
    )
    assert [
        (call.args[1].id, call.kwargs["include_content"])
        for call in serialize_document.call_args_list
    ] == [(ids[0], False), (ids[1], True), (ids[2], True)]

    serialize_document.reset_mock()
    transcribe_workspace.transcribe_workspace_response(
        db_session, {"generated_documents": documents}, current_user=owner,
    )
    assert all(call.kwargs["include_content"] for call in serialize_document.call_args_list)
