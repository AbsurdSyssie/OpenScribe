from uuid import UUID

import pyotp
from sqlalchemy import select

from app.models import AccountRequestStatus, TeamRole, TranscriptVersion, User, UserRecoveryCode, UserSession, UserStatus


def assert_error(response, *, status_code: int, code: str, message: str):
    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    return body["error"].get("details")


def login(client, *, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def finish_onboarding(client):
    start = client.post("/api/v1/onboarding/totp/start")
    assert start.status_code == 200
    code = pyotp.TOTP(start.json()["secret"]).now()
    verify = client.post("/api/v1/onboarding/totp/verify", json={"code": code})
    assert verify.status_code == 200
    skip = client.post("/api/v1/onboarding/skip-recovery-codes")
    assert skip.status_code == 200
    return skip


def test_healthcheck(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_account_request_submission_and_duplicate_rules(client, make_user):
    first = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Alice Example",
            "requested_email": "alice@example.com",
            "requested_team_name": "Clinic North",
            "request_details": "Need access",
        },
    )
    duplicate = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Alice Example",
            "requested_email": "ALICE@example.com",
            "requested_team_name": " clinic   north ",
            "request_details": "Need access again",
        },
    )

    assert first.status_code == 201
    assert first.json()["requested_email"] == "alice@example.com"
    assert_error(duplicate, status_code=409, code="conflict", message="Account request already exists")

    make_user(email="alice@example.com", password="password-1", is_system_admin=True)
    existing_user = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Alice Example",
            "requested_email": "alice@example.com",
            "requested_team_name": "Clinic North",
        },
    )
    assert_error(existing_user, status_code=409, code="conflict", message="User already exists")


def test_direct_managed_user_creation_sets_temp_password_onboarding_state(client, db_session, make_team, make_user):
    team = make_team(name="Ops Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")

    response = client.post(
        "/api/v1/users",
        json={
            "full_name": "Ops Lead",
            "email": "lead@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "leader",
            "status": "active",
            "mfa_required": True,
        },
    )
    duplicate = client.post(
        "/api/v1/users",
        json={
            "full_name": "Ops Lead",
            "email": "LEAD@example.com",
            "temporary_password": "TempPass2",
            "team_id": str(team.id),
            "team_role": "leader",
            "status": "active",
            "mfa_required": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["must_change_password"] is True
    assert response.json()["onboarding_state"] == "pending_password_change"
    persisted_user = db_session.get(User, UUID(response.json()["id"]))
    assert persisted_user is not None
    assert persisted_user.password_hash != "TempPass1"
    assert persisted_user.password_hash.startswith("scrypt$")
    assert_error(duplicate, status_code=409, code="conflict", message="User already exists")


def test_leader_can_review_only_own_team_requests_and_approve_them(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    other_team = make_team(name="Clinic South")
    leader = make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_account_request(requested_name="North Request", requested_email="north@example.com", requested_team_name="Clinic North")
    south_request = make_account_request(requested_name="South Request", requested_email="south@example.com", requested_team_name="Clinic South")

    login(client, email="leader@example.com", password="password-1")
    list_response = client.get("/api/v1/account-requests")
    assert list_response.status_code == 200
    assert [item["requested_email"] for item in list_response.json()] == ["north@example.com"]

    forbidden = client.post(
        f"/api/v1/account-requests/{south_request.id}/approve",
        json={"temporary_password": "TempPass1", "team_role": "user"},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Account-request review access required")

    approved = client.post(
        f"/api/v1/account-requests/{list_response.json()[0]['id']}/approve",
        json={"temporary_password": "TempPass1", "team_role": "user", "review_notes": "Looks valid"},
    )
    assert approved.status_code == 200
    assert approved.json()["team_id"] == str(team.id)


def test_temp_password_login_creates_onboarding_only_session_until_completion(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    create_response = client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )
    assert create_response.status_code == 201

    client.post("/api/v1/auth/logout")
    login_response = login(client, email="managed@example.com", password="TempPass1")
    assert login_response.status_code == 200
    assert login_response.json()["auth_level"] == "onboarding"
    assert login_response.json()["redirect_to"] == "/onboarding"

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["onboarding_state"] == "pending_password_change"

    blocked = client.get("/api/v1/users")
    assert_error(blocked, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")

    password_change = client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    assert password_change.status_code == 200
    assert password_change.json()["onboarding_state"] == "pending_totp_enrollment"

    finish = finish_onboarding(client)
    assert finish.json()["auth_level"] == "full"

    allowed = client.get("/api/v1/auth/me")
    assert allowed.status_code == 200
    assert allowed.json()["auth_level"] == "full"

    sessions = list(db_session.scalars(select(UserSession)))
    assert any(session.status.value == "revoked" for session in sessions)
    assert any(session.status.value == "active" and session.auth_level.value == "full" for session in sessions)


def test_recovery_code_generation_hashes_codes_and_unlocks_full_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )

    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    client.post("/api/v1/onboarding/totp/verify", json={"code": code})
    recovery = client.post("/api/v1/onboarding/recovery-codes")

    assert recovery.status_code == 200
    assert len(recovery.json()["codes"]) == 8

    stored = list(db_session.scalars(select(UserRecoveryCode)))
    assert len(stored) == 8
    assert all(item.code_hash not in recovery.json()["codes"] for item in stored)
    assert client.get("/api/v1/auth/me").json()["auth_level"] == "full"


def test_locking_a_user_revokes_active_sessions_immediately(client, db_session, make_user):
    user = make_user(email="member@example.com", password="password-1")
    login(client, email="member@example.com", password="password-1")
    assert client.get("/api/v1/auth/me").status_code == 200

    user.status = UserStatus.locked
    db_session.add(user)
    db_session.commit()

    revoked = client.get("/api/v1/auth/me")
    assert_error(revoked, status_code=401, code="unauthorized", message="Authentication required")
    sessions = list(db_session.scalars(select(UserSession).where(UserSession.user_id == user.id)))
    assert sessions
    assert all(session.status.value == "revoked" for session in sessions)


def test_transcript_routes_require_full_auth_and_preserve_owner_only_access(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team", default_retention_days=14)
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    unauthorized = client.post(
        "/api/v1/transcripts",
        json={"owner_user_id": str(owner.id), "team_id": str(team.id), "title": "Visit note", "current_draft_text_encrypted": "draft-1"},
    )
    assert_error(unauthorized, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="owner@example.com", password="password-1")
    transcript_response = client.post(
        "/api/v1/transcripts",
        json={"owner_user_id": str(owner.id), "team_id": str(team.id), "title": "Visit note", "current_draft_text_encrypted": "draft-1"},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]

    commit_one = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v1"})
    commit_two = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v2"})

    assert commit_one.status_code == 200
    assert commit_two.status_code == 200

    versions = db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id)))
    version_rows = list(versions)
    assert [row.version_no for row in version_rows] == [1, 2]
    assert version_rows[-1].text_encrypted == "final-text-v2"

    owner_list = client.get(f"/api/v1/users/{owner.id}/transcripts")
    other_list = client.get(f"/api/v1/users/{other.id}/transcripts")

    assert owner_list.status_code == 200
    assert [row["id"] for row in owner_list.json()] == [transcript_id]
    assert_error(other_list, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")
