from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from sqlalchemy import select

import pytest

from app.errors import AppError
from app.models import QuickAction, QuickActionVersion, SecurityAuditEvent, TeamRole, TemplateScope
from app.services.templates import fork_team_quick_action_to_personal


def test_member_can_fork_same_team_quick_action_to_personal(
    client,
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Quick action fork team")
    leader = make_user(email="quick-action-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="quick-action-member@example.com", password="password-2", team=team, team_role=TeamRole.user)
    shared = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Shared follow-up",
        prompt_text="Create concise follow-up.",
    )
    client.post("/login", data={"email": member.email, "password": "password-2"}, follow_redirects=False)

    response = client.post(
        f"/home/team-quick-actions/{shared.id}/fork",
        data={"return_view": "settings", "return_tab": "quick-actions"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/workspace/library/quick-actions?scope=personal&quick_action_id=")
    forked_id = UUID(parse_qs(urlparse(response.headers["location"]).query)["quick_action_id"][0])
    forked = db_session.get(QuickAction, forked_id)
    assert forked is not None
    assert forked.owner_user_id == member.id
    assert forked.scope == TemplateScope.user
    assert forked.name == "Shared follow-up 2"
    assert forked.team_id is None
    version = db_session.scalar(
        select(QuickActionVersion)
        .where(QuickActionVersion.quick_action_id == forked.id)
        .order_by(QuickActionVersion.version_no.desc())
    )
    assert version is not None
    assert version.prompt_text == "Create concise follow-up."
    audit = db_session.scalar(
        select(SecurityAuditEvent).where(SecurityAuditEvent.action == "quick_action_forked")
    )
    assert audit is not None
    assert audit.details_json["source_quick_action_id"] == str(shared.id)
    assert audit.details_json["forked_quick_action_id"] == str(forked.id)
    assert "Shared follow-up" not in str(audit.details_json)
    assert "Create concise follow-up" not in str(audit.details_json)


def test_member_cannot_fork_other_team_quick_action(
    client,
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    member_team = make_team(name="Member quick action team")
    other_team = make_team(name="Other quick action team")
    member = make_user(email="quick-action-cross-team@example.com", password="password-1", team=member_team, team_role=TeamRole.user)
    other_leader = make_user(email="quick-action-other-leader@example.com", password="password-2", team=other_team, team_role=TeamRole.leader)
    shared = make_quick_action(scope=TemplateScope.team, team=other_team, actor=other_leader, name="Other team action")
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    response = client.post(
        f"/home/team-quick-actions/{shared.id}/fork",
        data={"return_view": "settings", "return_tab": "quick-actions"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert db_session.scalar(
        select(QuickAction).where(QuickAction.scope == TemplateScope.user, QuickAction.owner_user_id == member.id)
    ) is None


def test_settings_quick_actions_show_same_team_read_only_and_embedded_editor(
    client,
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Quick action settings team")
    other_team = make_team(name="Other quick action settings team")
    member = make_user(email="quick-action-settings-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    leader = make_user(email="quick-action-settings-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    other_leader = make_user(email="quick-action-settings-other@example.com", password="password-3", team=other_team, team_role=TeamRole.leader)
    personal = make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Personal reminder")
    shared = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Shared reminder",
        prompt_text="Shared team instruction.",
    )
    make_quick_action(scope=TemplateScope.team, team=other_team, actor=other_leader, name="Other team reminder")
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    library = client.get("/settings?tab=quick-actions")
    selected_team = client.get(f"/settings?tab=quick-actions&scope=team&quick_action_id={shared.id}")
    selected_personal = client.get(f"/settings?tab=quick-actions&scope=personal&quick_action_id={personal.id}")

    assert library.status_code == 200
    assert 'aria-label="Quick action library"' in library.text
    assert "Shared reminder" in library.text
    assert "Other team reminder" not in library.text
    assert "Enabled · Read only" in library.text
    assert 'aria-label="New team quick action"' not in library.text
    assert selected_team.status_code == 200
    assert 'aria-label="Read-only team quick action"' in selected_team.text
    assert "Shared team instruction." in selected_team.text
    assert f'action="/home/team-quick-actions/{shared.id}/fork"' in selected_team.text
    assert 'action="/home/team-quick-actions"' not in selected_team.text
    denied = client.post(
        "/home/team-quick-actions",
        data={
            "quick_action_id": str(shared.id),
            "name": "Member edit",
            "description": "",
            "prompt_text": "Not allowed",
            "return_view": "settings",
            "return_tab": "quick-actions",
        },
    )
    assert denied.status_code == 403
    db_session.refresh(shared)
    assert shared.name == "Shared reminder"
    assert selected_personal.status_code == 200
    assert 'data-quick-action-editor' in selected_personal.text
    assert 'action="/home/personal-quick-actions"' in selected_personal.text
    assert f'name="quick_action_id" value="{personal.id}"' in selected_personal.text

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": leader.email, "password": "password-2"}, follow_redirects=False)
    leader_page = client.get(f"/settings?tab=quick-actions&scope=team&quick_action_id={shared.id}")
    assert leader_page.status_code == 200
    assert 'aria-label="Read-only team quick action"' not in leader_page.text
    assert 'action="/home/team-quick-actions"' in leader_page.text
    assert 'aria-label="New team quick action"' in leader_page.text


def test_quick_action_settings_validation_retains_values_and_delete_clears_selection(
    client,
    make_team,
    make_user,
):
    team = make_team(name="Quick action validation team")
    member = make_user(email="quick-action-validation@example.com", password="password-1", team=team, team_role=TeamRole.user)
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    invalid = client.post(
        "/home/personal-quick-actions",
        data={
            "name": "Unsaved action",
            "description": "Keep this description",
            "prompt_text": " ",
            "return_view": "settings",
            "return_tab": "quick-actions",
        },
    )
    assert invalid.status_code == 422
    assert 'value="Unsaved action"' in invalid.text
    assert 'value="Keep this description"' in invalid.text
    assert 'data-dirty-guard' in invalid.text

    created = client.post(
        "/home/personal-quick-actions",
        data={
            "name": "Delete me",
            "description": "",
            "prompt_text": "Write follow-up",
            "return_view": "settings",
            "return_tab": "quick-actions",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    created_id = parse_qs(urlparse(created.headers["location"]).query)["quick_action_id"][0]
    deleted = client.post(
        f"/home/personal-quick-actions/{created_id}/delete",
        data={"return_view": "settings", "return_tab": "quick-actions"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/workspace/library/quick-actions"


def test_quick_action_settings_partial_uses_master_detail_contract():
    source = Path("app/templates/settings/_quick_action_library.html").read_text()
    transcribe_source = Path("app/templates/transcribe/_workspace.html").read_text()

    assert 'aria-label="Quick action library"' in source
    assert 'id="personal-quick-action-heading">Personal</h3>' in source
    assert 'id="team-quick-action-heading">Team</h3>' in source
    assert "scope=personal&amp;quick_action_id=new" in source
    assert 'action="/home/team-quick-actions/{{ quick_action.id }}/fork"' in source
    assert "Back to quick actions" in source
    assert 'include "settings/_quick_action_editor.html"' in source
    assert 'data-settings-url="/settings?tab=quick-actions&scope=' in transcribe_source
    assert "&quick_action_id={{ quick_action.id }}" in transcribe_source


def test_quick_action_dirty_guard_ignores_late_csrf_transport_field():
    settings_source = Path("app/templates/settings.html").read_text()

    assert "data.delete('_csrf_token')" in settings_source
    assert "new URLSearchParams(data).toString()" in settings_source


def test_system_admin_cannot_fork_team_quick_action(
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Admin-blocked quick action team")
    leader = make_user(email="admin-blocked-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="quick-action-system-admin@example.com", password="password-2", is_system_admin=True)
    shared = make_quick_action(scope=TemplateScope.team, team=team, actor=leader, name="Admin-blocked action")

    with pytest.raises(AppError) as caught:
        fork_team_quick_action_to_personal(db_session, admin, quick_action_id=shared.id)

    assert caught.value.status_code == 403


def test_quick_action_library_renders_portability_controls_and_authorized_destinations(
    client,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Quick action portability")
    member = make_user(email="quick-action-portable-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    leader = make_user(email="quick-action-portable-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    personal = make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Portable personal action")
    shared = make_quick_action(scope=TemplateScope.team, team=team, actor=leader, name="Portable team action")

    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)
    member_page = client.get("/settings?tab=quick-actions")
    assert member_page.status_code == 200
    assert member_page.text.count("data-quick-action-export-checkbox") == 2
    assert f'value="{personal.id}" data-quick-action-export-checkbox' in member_page.text
    assert f'value="{shared.id}" data-quick-action-export-checkbox' in member_page.text
    assert 'name="quick-action-import-destination" value="personal" checked data-quick-action-import-destination' in member_page.text
    assert 'name="quick-action-import-destination" value="team" data-quick-action-import-destination' not in member_page.text

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": leader.email, "password": "password-2"}, follow_redirects=False)
    leader_page = client.get("/settings?tab=quick-actions")
    assert 'name="quick-action-import-destination" value="team" data-quick-action-import-destination' in leader_page.text


def test_quick_action_io_frontend_uses_safe_preflight_and_original_file_reupload():
    script = Path("app/static/js/settings/quick-action-io.js").read_text(encoding="utf-8")
    markup = Path("app/templates/settings/_quick_action_library.html").read_text(encoding="utf-8")

    for endpoint in (
        "/api/v1/quick-actions/export",
        "/api/v1/quick-actions/import/preflight",
        "/api/v1/quick-actions/import",
    ):
        assert endpoint in script
    assert "document.querySelector('[data-quick-action-import-dialog]')" in script
    assert "data.append('bundle', currentFile, currentFile.name)" in script
    assert "data.append('selected_indexes', JSON.stringify(indexes))" in script
    assert "JSON.parse(json)" in script
    assert "new File([json]" in script
    assert ".textContent =" in script
    assert "innerHTML" not in script
    assert "const isCleanSingleQuickAction = (body)" in script
    assert "await importCurrent([body.entries[0].index]);" in script
    assert 'class="template-library-utilities" aria-label="Quick action import and export"' in markup
    assert "Quick actions give OpenScribe a reusable instruction for the current consultation" in markup


def test_quick_action_import_shows_success_state_before_library_refresh():
    script = Path("app/static/js/settings/quick-action-io.js").read_text(encoding="utf-8")
    markup = Path("app/templates/settings/_quick_action_library.html").read_text(encoding="utf-8")

    assert "data-quick-action-import-success hidden" in markup
    assert 'data-lucide="party-popper"' in markup
    assert "data-quick-action-import-continue hidden" in markup
    assert "quick action${imported === 1 ? '' : 's'} imported and ready to use." in script
    assert "continueButton.focus()" in script
    assert "let seconds = 5" in script
    assert "continueButton.textContent = `Close (${seconds})`" in script
    assert "continueButton.addEventListener('click', finishImport)" in script


def test_quick_action_io_frontend_limits_exports_and_keeps_commits_open():
    script = Path("app/static/js/settings/quick-action-io.js").read_text(encoding="utf-8")

    assert "const MAX_EXPORT_ITEMS = 100;" in script
    assert "selected().length < Math.min(checks.length, MAX_EXPORT_ITEMS)" in script
    assert "checkbox.checked = shouldSelect && index < MAX_EXPORT_ITEMS;" in script
    assert "if (quickActionIds.length > MAX_EXPORT_ITEMS)" in script
    assert "let isCommitting = false;" in script
    assert "dialog.querySelector('form')?.addEventListener('submit', (event) => { if (isCommitting) event.preventDefault(); });" in script
    assert "dialog.addEventListener('cancel', (event) => { if (isCommitting) event.preventDefault(); });" in script


def test_quick_action_io_ignores_superseded_preflight_responses():
    script = Path("app/static/js/settings/quick-action-io.js").read_text(encoding="utf-8")

    assert "let preflightRequestId = 0;" in script
    assert "preflightRequestId += 1;" in script
    assert "const requestId = ++preflightRequestId;" in script
    assert script.count("if (requestId !== preflightRequestId) return;") >= 2


def test_quick_action_help_copies_schema_aware_ai_instructions():
    script = Path("app/static/js/settings/quick-action-io.js").read_text(encoding="utf-8")
    markup = Path("app/templates/settings/_quick_action_library.html").read_text(encoding="utf-8")

    assert "Create a quick action with AI" in markup
    assert "Copy instructions for AI" in markup
    for hook in (
        "data-quick-action-help-copy",
        "data-quick-action-help-status",
        "data-quick-action-help-fallback",
        "data-quick-action-help-prompt",
    ):
        assert hook in markup
        assert f"[{hook}]" in script
    assert "Ask only the questions needed to resolve information that is missing or unclear." in script
    assert 'Every latest_version must have mode "freeform".' in script
    assert "use only information supported by the consultation" in script
    assert "openscribe-quick-action-bundle-v1.schema.json" in script
    assert "navigator.clipboard.writeText" in script
    assert ".select()" in script
