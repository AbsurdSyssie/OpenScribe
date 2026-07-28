from app.models import GeneratedDocumentGeneratorType, GeneratedDocumentStatus, TemplateMode
from app.services.dictations import dictation_effective_text, get_post_consultation_dictation
from app.services.templates import (
    generated_document_section_text,
    generated_document_text,
    list_generated_documents_for_transcript,
)
from app.services.transcripts import transcript_draft_text, working_note_detail
from app.services.tutorials import (
    TUTORIAL_DICTATION_TEXT,
    TUTORIAL_TEMPLATE_NAME,
    TUTORIAL_TRANSCRIPT_TEXT,
    TUTORIAL_TRANSCRIPT_TITLE,
    create_scribe_tutorial_consultation,
)


def _login(client, user, password="Password123"):
    return client.post(
        "/login",
        data={"email": user.email, "password": password},
        follow_redirects=False,
    )


def test_tutorial_consultation_seeds_owner_scoped_synthetic_sources_and_outputs(
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Tutorial Service")
    user = make_user(email="tutorial-service@example.com", team=team)

    transcript = create_scribe_tutorial_consultation(db_session, user)

    assert transcript.owner_user_id == user.id
    assert transcript.team_id == team.id
    assert transcript.title == TUTORIAL_TRANSCRIPT_TITLE
    assert transcript_draft_text(db_session, transcript=transcript) == TUTORIAL_TRANSCRIPT_TEXT
    assert transcript.ingestion_jobs == []

    working_note = working_note_detail(db_session, user, transcript_id=transcript.id)
    assert working_note["mode"].value == "structured"
    assert working_note["structured_note"]["profile"] == "emis"
    assert working_note["structured_note"]["sections"]["problem"] == ["Mild headache for three days."]

    dictation = get_post_consultation_dictation(db_session, user, transcript_id=transcript.id)
    assert dictation is not None
    assert dictation_effective_text(db_session, dictation=dictation) == TUTORIAL_DICTATION_TEXT

    documents = list_generated_documents_for_transcript(
        db_session,
        user,
        transcript_id=transcript.id,
    )
    assert len(documents) == 3
    assert all(document.owner_user_id == user.id for document in documents)
    assert all(document.status is GeneratedDocumentStatus.ready for document in documents)

    note = next(
        document
        for document in documents
        if document.generator_type is GeneratedDocumentGeneratorType.template
    )
    assert note.document_mode is TemplateMode.structured
    assert note.source_template_name == TUTORIAL_TEMPLATE_NAME
    assert len(note.sections) == 5
    assert generated_document_section_text(
        db_session,
        section=note.sections[0],
        field="edited_text_encrypted",
    )

    followups = [
        document
        for document in documents
        if document.generator_type
        in {GeneratedDocumentGeneratorType.followup, GeneratedDocumentGeneratorType.quick_action}
    ]
    assert len(followups) == 2
    assert all(
        generated_document_text(
            db_session,
            document=document,
            field="edited_output_text_encrypted",
        )
        for document in followups
    )

    repeated = create_scribe_tutorial_consultation(db_session, user)
    assert repeated.id == transcript.id


def test_tutorial_route_creates_example_and_returns_to_the_workspace(
    client,
    make_team,
    make_user,
):
    team = make_team(name="Tutorial Route")
    user = make_user(
        email="tutorial-route@example.com",
        password="Password123",
        team=team,
    )
    _login(client, user)

    response = client.post("/transcribe/tutorial", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/workspace?transcript_id=")
    assert "tutorial=1" in response.headers["location"]

    workspace = client.get(response.headers["location"])
    assert workspace.status_code == 200
    assert TUTORIAL_TRANSCRIPT_TITLE in workspace.text
    assert TUTORIAL_TEMPLATE_NAME in workspace.text
    assert "Headache review" in workspace.text
    assert "Safety-net message" in workspace.text
