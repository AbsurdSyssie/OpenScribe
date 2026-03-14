import pyotp

from app.models import TeamRole


def test_login_page_exposes_bootstrap_when_database_is_empty(client):
    page = client.get("/login")

    assert page.status_code == 200
    assert "Create first system admin" in page.text


def test_request_access_page_submits_public_account_request(client):
    page = client.get("/request-access")
    assert page.status_code == 200
    assert "Request an account" in page.text

    submitted = client.post(
        "/request-access",
        data={
            "requested_name": "Alice Example",
            "requested_email": "alice@example.com",
            "requested_team_name": "Clinic North",
            "request_details": "Need access",
        },
    )
    assert submitted.status_code == 200
    assert "Account request submitted" in submitted.text


def test_bootstrap_redirects_to_onboarding_and_requires_totp_setup(client):
    bootstrap_response = client.post(
        "/bootstrap/system-admin",
        data={"email": "admin@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    assert bootstrap_response.status_code == 303
    assert bootstrap_response.headers["location"] == "/onboarding"

    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "Complete onboarding before normal access." in page.text

    client.post("/onboarding/totp/start")
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    verify = client.post("/onboarding/totp/verify", data={"code": code})
    assert verify.status_code == 200
    assert "Recovery codes" in verify.text


def test_non_admin_login_redirects_to_home_and_leader_sees_review_tools(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_account_request(requested_name="Alice Example", requested_email="alice@example.com", requested_team_name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login_response = client.post(
        "/login",
        data={"email": "leader@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/home"

    home_page = client.get("/home")
    assert home_page.status_code == 200
    assert "Create a managed user" in home_page.text
    assert "Account requests for your team" in home_page.text

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"


def test_admin_page_lists_teams_users_and_account_requests(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="lead@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_account_request(requested_name="Alice Example", requested_email="alice@example.com", requested_team_name="Clinic North")

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin")

    assert page.status_code == 200
    assert "Clinic North" in page.text
    assert "lead@example.com" in page.text
    assert "Account requests" in page.text
    assert "alice@example.com" in page.text
