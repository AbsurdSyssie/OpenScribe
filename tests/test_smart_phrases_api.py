from uuid import uuid4

from sqlalchemy import select

from app.models import SmartPhrase, TeamRole
from app.models import UserOnboardingState
from app.schemas import UserCreate
from app.services.admin import create_user
from app.services.smart_phrases import DEFAULT_SMART_PHRASE_TRIGGER


def assert_error(response, *, status_code: int, code: str):
    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == code
    return body["error"]


def login(client, *, email: str, password: str):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    assert response.json()["auth_level"] == "full"
    return response


def test_smart_phrase_crud_owner_only_and_hard_delete(client, db_session, make_team, make_user):
    team = make_team()
    owner = make_user(email="smart-owner@example.com", password="password-1", team=team, mfa_required=False, mfa_enabled=False)
    other = make_user(email="smart-other@example.com", password="password-2", team=team, mfa_required=False, mfa_enabled=False)
    other_phrase = SmartPhrase(owner_user_id=other.id, trigger="OTHER", expansion_text="Other text")
    db_session.add(other_phrase)
    db_session.commit()

    login(client, email=owner.email, password="password-1")
    created = client.post(
        "/api/v1/smart-phrases/personal",
        json={"trigger": "bp_note", "expansion_text": "Blood pressure reviewed\nPlan agreed", "description": "BP"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["trigger"] == "BP_NOTE"
    assert body["expansion_text"] == "Blood pressure reviewed\nPlan agreed"
    assert body["owner_user_id"] == str(owner.id)

    available = client.get("/api/v1/smart-phrases/available")
    assert available.status_code == 200
    assert [phrase["trigger"] for phrase in available.json()] == ["BP_NOTE"]

    blocked = client.patch(f"/api/v1/smart-phrases/personal/{other_phrase.id}", json={"description": "No"})
    assert_error(blocked, status_code=404, code="not_found")

    updated = client.patch(
        f"/api/v1/smart-phrases/personal/{body['id']}",
        json={"trigger": "FOLLOWUP", "expansion_text": "Updated text", "description": None},
    )
    assert updated.status_code == 200
    assert updated.json()["trigger"] == "FOLLOWUP"
    assert updated.json()["description"] is None

    used = client.post(f"/api/v1/smart-phrases/personal/{body['id']}/used")
    assert used.status_code == 200
    assert used.json()["times_used"] == 1
    assert used.json()["last_used_at"] is not None

    deleted = client.delete(f"/api/v1/smart-phrases/personal/{body['id']}")
    assert deleted.status_code == 204
    assert db_session.get(SmartPhrase, body["id"]) is None


def test_smart_phrase_validation_and_duplicate_triggers(client, make_team, make_user):
    team = make_team()
    owner = make_user(email="smart-validation@example.com", password="password-1", team=team, mfa_required=False, mfa_enabled=False)
    login(client, email=owner.email, password="password-1")

    first = client.post("/api/v1/smart-phrases/personal", json={"trigger": "abc", "expansion_text": "Alpha"})
    assert first.status_code == 201

    duplicate = client.post("/api/v1/smart-phrases/personal", json={"trigger": "ABC", "expansion_text": "Beta"})
    assert_error(duplicate, status_code=409, code="conflict")

    invalid_trigger = client.post("/api/v1/smart-phrases/personal", json={"trigger": "bad trigger", "expansion_text": "Text"})
    assert invalid_trigger.status_code == 422

    too_long = client.post("/api/v1/smart-phrases/personal", json={"trigger": "LONG", "expansion_text": "x" * 2001})
    assert too_long.status_code == 422


def test_smart_phrase_admin_forbidden(client, make_user):
    admin = make_user(email="smart-admin@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    login(client, email=admin.email, password="password-1")

    response = client.get("/api/v1/smart-phrases/personal")
    assert_error(response, status_code=403, code="forbidden")


def test_default_smart_phrase_created_for_new_users_and_not_recreated(client, db_session, make_team, make_user):
    team = make_team()
    leader = make_user(email="smart-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)

    user = create_user(
        db_session,
        UserCreate(
            full_name="New Smart",
            email=f"new-smart-{uuid4()}@example.com",
            temporary_password="TempPass123",
            team_id=team.id,
            team_role=TeamRole.user,
            is_system_admin=False,
            mfa_required=False,
        ),
        actor=leader,
    )
    phrase = db_session.scalar(select(SmartPhrase).where(SmartPhrase.owner_user_id == user.id))
    assert phrase is not None
    assert phrase.trigger == DEFAULT_SMART_PHRASE_TRIGGER

    db_session.delete(phrase)
    user.must_change_password = False
    user.onboarding_state = UserOnboardingState.complete
    db_session.add(user)
    db_session.commit()
    login(client, email=user.email, password="TempPass123")
    listed = client.get("/api/v1/smart-phrases/personal")
    assert listed.status_code == 200
    assert listed.json() == []
