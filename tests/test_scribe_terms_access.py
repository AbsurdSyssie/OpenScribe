from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from app import main
from app.errors import AppError
from app.models import LegalDocumentKind, TranscriptIngestionMode
from app.schemas.transcripts import TranscriptStart
from app.services.transcripts import start_transcript
from tests.test_legal_content import _publish


@pytest.mark.parametrize(
    ("path", "required"),
    [
        ("/api/v1/transcripts", True),
        ("/api/v1/transcripts/abc/working-note", True),
        ("/api/v1/transcribe/workspace/stream", True),
        ("/api/v1/generated-documents/abc", True),
        ("/api/v1/transcripts-export", False),
        ("/api/v1/transcriber-settings", False),
        ("/workspace/preferences", False),
    ],
)
def test_scribe_clinical_api_path_boundaries(path, required):
    assert main._is_scribe_clinical_api_path(path) is required


def test_unaccepted_clinical_api_uses_stable_error_code(monkeypatch):
    monkeypatch.setattr(
        "app.services.scribe_terms.required_scribe_terms_version",
        lambda db, *, user, session: 12,
    )
    monkeypatch.setattr(main, "_audit_request_rejected", lambda *args, **kwargs: None)
    context = SimpleNamespace(user=SimpleNamespace(id="owner"), session=object())

    with pytest.raises(AppError) as raised:
        main._require_scribe_terms_for_context(
            SimpleNamespace(url=SimpleNamespace(path="/api/v1/transcripts")),
            object(),
            context,
        )

    assert raised.value.status_code == 403
    assert raised.value.code == "scribe_terms_required"


def test_real_login_blocks_clinical_json_sse_and_websocket_but_allows_non_scribe_api(
    client, db_session, make_team, make_user,
):
    admin = make_user(email="scribe-terms-api-admin@example.com", is_system_admin=True)
    team = make_team(name="Scribe Terms API Clinic")
    user = make_user(
        email="scribe-terms-api-member@example.com",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    _publish(db_session, actor=admin, kind=LegalDocumentKind.terms)
    transcript = start_transcript(
        db_session,
        user,
        TranscriptStart(title="Synthetic unaccepted session", ingestion_mode=TranscriptIngestionMode.live_chunked),
    )
    db_session.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "password-1"},
    )
    assert login.status_code == 200

    clinical = client.get(f"/api/v1/transcripts/{transcript.id}")
    assert clinical.status_code == 403
    assert clinical.json()["error"]["code"] == "scribe_terms_required"

    event_stream = client.get(
        "/api/v1/transcribe/workspace/stream",
        params={"once": "true", "transcript_id": str(transcript.id)},
    )
    assert event_stream.status_code == 403
    assert event_stream.json()["error"]["code"] == "scribe_terms_required"

    non_scribe = client.get("/api/v1/auth/me")
    assert non_scribe.status_code == 200
    assert client.get("/api/v1/templates/available").status_code == 200

    with pytest.raises(WebSocketDisconnect) as raised:
        with client.websocket_connect(
            f"/api/v1/transcripts/{transcript.id}/deepgram-stream",
            headers={"Origin": "http://testserver"},
        ):
            pass
    assert raised.value.code == 4403

    acceptance_path = "/workspace/scribe-terms/accept"
    missing_csrf = client.post(
        acceptance_path,
        data={"version_no": 1, "accept_terms": "yes", "_csrf_token": ""},
        headers={"X-CSRF-Token": ""},
        follow_redirects=False,
    )
    assert missing_csrf.status_code == 403

    stale_version = client.post(
        acceptance_path, data={"version_no": 2, "accept_terms": "yes"}, follow_redirects=False,
    )
    assert stale_version.status_code == 409
    assert client.get(f"/api/v1/transcripts/{transcript.id}").status_code == 403

    accepted = client.post(
        acceptance_path, data={"version_no": 1, "accept_terms": "yes"}, follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert client.get(f"/api/v1/transcripts/{transcript.id}").status_code == 200
