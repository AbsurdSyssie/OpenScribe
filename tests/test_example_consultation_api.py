from sqlalchemy import select

from app.models import Transcript


def test_first_workspace_visit_seeds_example_through_authenticated_post(client, db_session, make_user):
    owner = make_user(email="example-api@example.com", password="password-1")
    owner.example_consultation_pending = True
    db_session.commit()

    logged_in = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})
    assert logged_in.status_code == 200
    html = client.get("/workspace")
    assert html.status_code == 200
    assert 'data-example-consultation-pending="true"' in html.text

    workspace = client.get("/api/v1/transcribe/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["example_consultation_pending"] is True
    assert workspace.json()["active_transcript"] is None

    created = client.post("/api/v1/transcribe/example-consultation")
    assert created.status_code == 200
    transcript_id = created.json()["id"]
    assert transcript_id
    transcript = db_session.get(Transcript, transcript_id)
    assert transcript is not None and transcript.is_example is True

    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript_id}")
    assert workspace.status_code == 200
    assert workspace.json()["example_consultation_pending"] is False
    assert workspace.json()["active_transcript"]["is_example"] is True
    assert len(workspace.json()["generated_documents"]) == 2

    replay = client.post("/api/v1/transcribe/example-consultation")
    assert replay.status_code == 200
    assert replay.json() == {"id": None}
    assert db_session.scalars(select(Transcript).where(Transcript.owner_user_id == owner.id)).all() == [transcript]
