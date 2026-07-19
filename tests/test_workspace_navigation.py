import pytest

from app.models import TeamRole
from app.schemas import TranscriptStart
from app.services.transcripts import start_transcript
from app.web.workspace import (
    WORKSPACE_ACCOUNT,
    WORKSPACE_SECTION_TEMPLATES,
    build_workspace_shell_context,
)
from app.web.presentation import (
    home_page_route_from_return_view,
    home_redirect_url,
    home_return_view_value,
)


def _login(client, user, password="Password123"):
    return client.post(
        "/login",
        data={"email": user.email, "password": password},
        follow_redirects=False,
    )


def test_workspace_scribe_and_personal_routes_load(client, make_team, make_user):
    team = make_team(name="Workspace Personal")
    user = make_user(email="workspace@example.com", password="Password123", team=team)
    _login(client, user)

    scribe = client.get("/workspace")
    account = client.get("/workspace/account")
    preferences = client.get("/workspace/preferences")

    assert scribe.status_code == 200
    assert scribe.headers["Cache-Control"] == "no-store"
    assert 'data-workspace-section="scribe"' in scribe.text
    assert scribe.context["transcribe_route_base"] == "/workspace"
    assert account.status_code == 200
    assert account.headers["Cache-Control"] == "no-store"
    assert 'data-workspace-section="account"' in account.text
    assert 'class="settings-shell"' not in account.text
    assert 'data-settings-panel="account"' in account.text
    assert preferences.status_code == 200
    assert 'data-workspace-section="preferences"' in preferences.text
    assert 'data-settings-panel="preferences"' in preferences.text


def test_workspace_library_routes_load_for_team_member(client, make_team, make_user):
    team = make_team(name="Workspace Library")
    user = make_user(email="library@example.com", password="Password123", team=team)
    _login(client, user)

    for path in (
        "/workspace/library/templates",
        "/workspace/library/quick-actions",
        "/workspace/library/smart-phrases",
    ):
        assert client.get(path).status_code == 200


def test_workspace_team_routes_require_leader(client, make_team, make_user):
    team = make_team(name="Workspace Team Guard")
    member = make_user(email="member@example.com", password="Password123", team=team)
    _login(client, member)
    team_paths = (
        "/workspace/team/ai-services",
        "/workspace/team/members",
        "/workspace/team/account-requests",
    )

    for path in team_paths:
        assert client.get(path).status_code == 403

    client.post("/logout", follow_redirects=False)
    leader = make_user(
        email="leader@example.com",
        password="Password123",
        team=team,
        team_role=TeamRole.leader,
    )
    _login(client, leader)
    for path in team_paths:
        assert client.get(path).status_code == 200


def test_workspace_routes_keep_system_admin_separate(client, make_user):
    admin = make_user(
        email="workspace-admin@example.com",
        password="Password123",
        is_system_admin=True,
    )
    _login(client, admin)

    for path in ("/workspace", "/workspace/account", "/workspace/team/members"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"


def test_workspace_shell_context_lists_only_owner_consultations(
    db_session, make_team, make_user
):
    team = make_team(name="Workspace Ownership")
    owner = make_user(email="owner@example.com", team=team)
    other = make_user(email="other@example.com", team=team)
    owned = start_transcript(db_session, owner, TranscriptStart(title="Owned consultation"))
    foreign = start_transcript(db_session, other, TranscriptStart(title="Foreign consultation"))

    context = build_workspace_shell_context(
        db_session,
        current_user=owner,
        active_section=WORKSPACE_ACCOUNT,
    )

    listed_ids = {str(item.id) for item in context["workspace_recent_transcripts"]}
    assert str(owned.id) in listed_ids
    assert str(foreign.id) not in listed_ids
    assert context["active_transcript_id"] is None
    assert context["workspace_content_template"] == WORKSPACE_SECTION_TEMPLATES[WORKSPACE_ACCOUNT]


def test_workspace_shell_context_rejects_untrusted_section(db_session, make_user):
    user = make_user(email="unknown-section@example.com")

    try:
        build_workspace_shell_context(
            db_session,
            current_user=user,
            active_section="../../admin",
        )
    except ValueError as exc:
        assert str(exc) == "Unknown workspace section"
    else:
        raise AssertionError("Untrusted workspace section accepted")


def test_workspace_does_not_expose_foreign_consultation(
    client, db_session, make_team, make_user
):
    team = make_team(name="Workspace Foreign Guard")
    owner = make_user(email="guard-owner@example.com", password="Password123", team=team)
    other = make_user(email="guard-other@example.com", team=team)
    foreign = start_transcript(
        db_session, other, TranscriptStart(title="Foreign confidential consultation")
    )
    _login(client, owner)

    response = client.get(f"/workspace?transcript_id={foreign.id}")

    assert response.status_code == 200
    assert "Foreign confidential consultation" not in response.text


def test_new_consultation_post_redirects_to_canonical_workspace(
    client, make_team, make_user
):
    team = make_team(name="Workspace Create")
    user = make_user(email="workspace-create@example.com", password="Password123", team=team)
    _login(client, user)

    response = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/workspace?transcript_id=")


def test_transcribe_compatibility_redirect_preserves_only_transcript_id(raw_client):
    transcript_id = "11111111-1111-1111-1111-111111111111"
    response = raw_client.get(
        f"/transcribe?transcript_id={transcript_id}&tab=followups&next=https://evil.example",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == f"/workspace?transcript_id={transcript_id}"


@pytest.mark.parametrize(
    ("tab", "destination"),
    [
        ("account", "/workspace/account"),
        ("preferences", "/workspace/preferences"),
        ("templates", "/workspace/library/templates"),
        ("quick-actions", "/workspace/library/quick-actions"),
        ("smart-phrases", "/workspace/library/smart-phrases"),
        ("ai-services", "/workspace/team/ai-services"),
        ("team-members", "/workspace/team/members"),
        ("team-management", "/workspace/team/members"),
        ("account-requests", "/workspace/team/account-requests"),
        (None, "/workspace/preferences"),
        ("unknown", "/workspace/preferences"),
    ],
)
def test_settings_compatibility_redirect_uses_closed_tab_map(raw_client, tab, destination):
    query = f"?tab={tab}&next=https://evil.example" if tab else "?next=https://evil.example"
    response = raw_client.get(f"/settings{query}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == destination


def test_settings_compatibility_preserves_only_valid_editor_selection(raw_client):
    template_id = "11111111-1111-1111-1111-111111111111"
    response = raw_client.get(
        f"/settings?tab=templates&scope=team&template_id={template_id}&next=https://evil.example",
        follow_redirects=False,
    )
    invalid = raw_client.get(
        "/settings?tab=templates&scope=team&template_id=../../admin",
        follow_redirects=False,
    )

    assert response.headers["location"] == (
        f"/workspace/library/templates?scope=team&template_id={template_id}"
    )
    assert invalid.headers["location"] == "/workspace/library/templates"


def test_workspace_return_view_helpers_are_closed_and_canonical():
    assert home_return_view_value("workspace") == "workspace"
    assert home_page_route_from_return_view("workspace") == "/workspace/preferences"
    assert home_redirect_url(return_view="workspace", return_tab="templates") == "/workspace/library/templates"
    assert home_redirect_url(return_view="workspace", return_tab="team-management") == "/workspace/team/members"
    assert home_redirect_url(return_view="workspace", return_tab="unknown") == "/workspace/preferences"
