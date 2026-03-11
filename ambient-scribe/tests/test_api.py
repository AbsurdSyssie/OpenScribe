from uuid import UUID

from sqlalchemy import select

from app.models import TranscriptVersion


def test_healthcheck(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_bootstrap_flow_and_owner_scoped_listing(client, db_session):
    team_response = client.post("/teams", json={"name": "Primary Team", "default_retention_days": 14})
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    owner_response = client.post(
        "/users",
        json={
            "email": "owner@example.com",
            "password_hash": "hash-1",
            "team_id": team_id,
            "team_role": "user",
        },
    )
    assert owner_response.status_code == 201
    owner_id = owner_response.json()["id"]

    other_response = client.post(
        "/users",
        json={
            "email": "other@example.com",
            "password_hash": "hash-2",
            "team_id": team_id,
            "team_role": "user",
        },
    )
    assert other_response.status_code == 201
    other_id = other_response.json()["id"]

    transcript_response = client.post(
        "/transcripts",
        json={
            "owner_user_id": owner_id,
            "team_id": team_id,
            "title": "Visit note",
            "current_draft_text_encrypted": "draft-1",
        },
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    assert transcript_response.json()["status"] == "recording"

    commit_response = client.post(
        f"/transcripts/{transcript_id}/commit",
        json={"text_encrypted": "final-text-v1"},
    )
    assert commit_response.status_code == 200
    assert commit_response.json()["status"] == "ready"

    versions = db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id)))
    version_rows = list(versions)
    assert len(version_rows) == 1
    assert version_rows[0].version_no == 1
    assert version_rows[0].text_encrypted == "final-text-v1"

    owner_list = client.get(f"/users/{owner_id}/transcripts")
    assert owner_list.status_code == 200
    assert [row["id"] for row in owner_list.json()] == [transcript_id]

    other_list = client.get(f"/users/{other_id}/transcripts")
    assert other_list.status_code == 200
    assert other_list.json() == []


def test_create_transcript_rejects_cross_team_owner(client):
    team_one = client.post("/teams", json={"name": "Team One"}).json()
    team_two = client.post("/teams", json={"name": "Team Two"}).json()
    user = client.post(
        "/users",
        json={
            "email": "member@example.com",
            "password_hash": "hash-3",
            "team_id": team_one["id"],
            "team_role": "user",
        },
    ).json()

    response = client.post(
        "/transcripts",
        json={
            "owner_user_id": user["id"],
            "team_id": team_two["id"],
            "title": "bad transcript",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Owner user does not belong to the provided team"
