from datetime import timedelta
from uuid import uuid4

from app.main import CSRF_COOKIE_NAME
from app.models import RedactionEntity, RedactionRun, RedactionRunStatus, Transcript, TranscriptStatus, TranscriptVersion, utcnow
from app.services.content_crypto import encrypt_text_for_owner, ensure_user_dek


def _login(client, *, email: str, password: str = "password-1"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _transcript_with_pii(db_session, *, owner, draft_text: str = "John Smith reports asthma."):
    ensure_user_dek(db_session, user=owner)
    transcript_id = uuid4()
    transcript = Transcript(
        id=transcript_id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="PII visit",
        status=TranscriptStatus.ready,
        current_draft_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript_id,
            plaintext=draft_text,
        ),
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    transcript_version_id = uuid4()
    version = TranscriptVersion(
        id=transcript_version_id,
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=transcript_version_id,
            plaintext=draft_text,
        ),
    )
    redaction_run_id = uuid4()
    run = RedactionRun(
        id=redaction_run_id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=RedactionRunStatus.succeeded,
        redacted_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="redaction_runs",
            field="redacted_text_encrypted",
            record_id=redaction_run_id,
            plaintext="[PHI-1] reports asthma.",
        ),
        mapping_hash="hash",
        entity_count=1,
        api_provider="native_presidio",
        api_model_or_version="test",
    )
    db_session.add_all([transcript, version, run])
    db_session.flush()
    entity_id = uuid4()
    entity = RedactionEntity(
        id=entity_id,
        redaction_run_id=run.id,
        entity_order=1,
        entity_type="PERSON",
        placeholder="[PHI-1]",
        original_value_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="redaction_entities",
            field="original_value_encrypted",
            record_id=entity_id,
            plaintext="John Smith",
        ),
        normalized_value_hash="hash",
        occurrence_count=1,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(transcript)
    return transcript


def _link_document_to_redaction_run(db_session, *, document, transcript):
    run = db_session.query(RedactionRun).filter(RedactionRun.transcript_id == transcript.id).one()
    document.redaction_run_id = run.id
    db_session.commit()
    db_session.refresh(document)
    return document


def test_owner_workspace_source_transcript_pii_entities_include_values_by_default(client, db_session, make_user):
    user = make_user(email="pii-owner@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    assert _login(client, email=user.email).status_code == 200

    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    entities = response.json()["active_transcript_pii_entities"]
    assert entities
    assert entities[0]["value"] == "John Smith"
    assert entities[0]["placeholder"] == "[PHI-1]"
    assert entities[0]["entity_type"] == "PERSON"
    assert entities[0]["has_value"] is True


def test_workspace_generated_document_pii_entities_do_not_include_values_by_default(
    client,
    db_session,
    make_user,
    make_generated_document,
):
    user = make_user(email="pii-doc-workspace@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    version = db_session.query(TranscriptVersion).filter(TranscriptVersion.transcript_id == transcript.id).one()
    document = make_generated_document(owner=user, transcript=transcript, transcript_version=version, output_text="Generated note")
    _link_document_to_redaction_run(db_session, document=document, transcript=transcript)
    assert _login(client, email=user.email).status_code == 200

    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    entities = response.json()["generated_documents"][0]["pii_entities"]
    assert entities
    assert "value" not in entities[0]
    assert entities[0]["placeholder"] == "[PHI-1]"
    assert entities[0]["entity_type"] == "PERSON"
    assert entities[0]["has_value"] is True


def test_reveal_pii_entities_returns_values_for_owner(client, db_session, make_user):
    user = make_user(email="pii-reveal@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    assert _login(client, email=user.email).status_code == 200

    response = client.post(f"/api/v1/transcripts/{transcript.id}/pii-entities/reveal")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["value"] == "John Smith"
    assert body[0]["placeholder"] == "[PHI-1]"


def test_reveal_pii_entities_rejects_non_owner(client, db_session, make_user):
    owner = make_user(email="pii-owner-only@example.com", mfa_required=False, mfa_enabled=False)
    other = make_user(email="pii-other@example.com", team=owner.team, mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=owner)
    assert _login(client, email=other.email).status_code == 200

    response = client.post(f"/api/v1/transcripts/{transcript.id}/pii-entities/reveal")

    assert response.status_code == 404


def test_reveal_pii_entities_requires_csrf(raw_client, db_session, make_user):
    user = make_user(email="pii-csrf@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    assert _login(raw_client, email=user.email).status_code == 200

    response = raw_client.post(
        f"/api/v1/transcripts/{transcript.id}/pii-entities/reveal",
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 403


def test_sensitive_api_responses_are_no_store(client, db_session, make_user, make_generated_document):
    user = make_user(email="pii-cache@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    version = db_session.query(TranscriptVersion).filter(TranscriptVersion.transcript_id == transcript.id).one()
    document = make_generated_document(owner=user, transcript=transcript, transcript_version=version, output_text="Generated note")
    assert _login(client, email=user.email).status_code == 200

    responses = [
        client.get("/api/v1/transcribe/workspace"),
        client.get(f"/api/v1/transcribe/workspace/stream?transcript_id={transcript.id}&once=true"),
        client.get(f"/api/v1/transcripts/{transcript.id}"),
        client.post(f"/api/v1/transcripts/{transcript.id}/pii-entities/reveal"),
        client.get(f"/api/v1/transcripts/{transcript.id}/generated-documents"),
        client.get(f"/api/v1/transcripts/{transcript.id}/post-consultation-dictation"),
        client.delete(f"/api/v1/generated-documents/{document.id}"),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200, 200, 204]
    for response in responses:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"


def test_transcript_detail_uses_plaintext_response_name(client, db_session, make_user):
    user = make_user(email="pii-name@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    assert _login(client, email=user.email).status_code == 200

    response = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["current_draft_text"] == "John Smith reports asthma."
    assert "current_draft_text_encrypted" not in body


def test_generated_document_response_uses_plaintext_names(client, db_session, make_user, make_generated_document):
    user = make_user(email="pii-doc@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    version = db_session.query(TranscriptVersion).filter(TranscriptVersion.transcript_id == transcript.id).one()
    make_generated_document(owner=user, transcript=transcript, transcript_version=version, output_text="Generated note")
    assert _login(client, email=user.email).status_code == 200

    response = client.get(f"/api/v1/transcripts/{transcript.id}/generated-documents")

    assert response.status_code == 200
    document = response.json()[0]
    assert document["edited_output_text"] == "Generated note"
    assert document["original_output_text"] == "Generated note"
    assert "edited_output_text_encrypted" not in document
    assert "original_output_text_encrypted" not in document


def test_generated_document_pii_entities_do_not_include_values_by_default(client, db_session, make_user, make_generated_document):
    user = make_user(email="pii-doc-list@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript_with_pii(db_session, owner=user)
    version = db_session.query(TranscriptVersion).filter(TranscriptVersion.transcript_id == transcript.id).one()
    document = make_generated_document(owner=user, transcript=transcript, transcript_version=version, output_text="Generated note")
    _link_document_to_redaction_run(db_session, document=document, transcript=transcript)
    assert _login(client, email=user.email).status_code == 200

    response = client.get(f"/api/v1/transcripts/{transcript.id}/generated-documents")

    assert response.status_code == 200
    entities = response.json()[0]["pii_entities"]
    assert entities
    assert "value" not in entities[0]
    assert entities[0]["placeholder"] == "[PHI-1]"
    assert entities[0]["entity_type"] == "PERSON"
    assert entities[0]["has_value"] is True
