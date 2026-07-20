"""Shared presentation metadata for the permanent user workspace."""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from ..models import TeamRole, User
from ..services.preferences import get_user_app_preferences
from .transcribe_workspace import TRANSCRIPT_HISTORY_DEFAULT_LIMIT, list_transcript_history_page
from .templates import templates


WORKSPACE_SCRIBE: Final = "scribe"
WORKSPACE_ACCOUNT: Final = "account"
WORKSPACE_PREFERENCES: Final = "preferences"
WORKSPACE_TEMPLATES: Final = "templates"
WORKSPACE_QUICK_ACTIONS: Final = "quick-actions"
WORKSPACE_SMART_PHRASES: Final = "smart-phrases"
WORKSPACE_AI_SERVICES: Final = "ai-services"
WORKSPACE_TEAM_MEMBERS: Final = "team-members"
WORKSPACE_ACCOUNT_REQUESTS: Final = "account-requests"

WORKSPACE_SECTION_PATHS: Final[dict[str, str]] = {
    WORKSPACE_SCRIBE: "/workspace",
    WORKSPACE_ACCOUNT: "/workspace/account",
    WORKSPACE_PREFERENCES: "/workspace/preferences",
    WORKSPACE_TEMPLATES: "/workspace/library/templates",
    WORKSPACE_QUICK_ACTIONS: "/workspace/library/quick-actions",
    WORKSPACE_SMART_PHRASES: "/workspace/library/smart-phrases",
    WORKSPACE_AI_SERVICES: "/workspace/team/ai-services",
    WORKSPACE_TEAM_MEMBERS: "/workspace/team/members",
    WORKSPACE_ACCOUNT_REQUESTS: "/workspace/team/account-requests",
}

WORKSPACE_SECTION_TEMPLATES: Final[dict[str, str]] = {
    WORKSPACE_ACCOUNT: "settings/_account.html",
    WORKSPACE_PREFERENCES: "settings/_preferences.html",
    WORKSPACE_TEMPLATES: "settings/_template_library.html",
    WORKSPACE_QUICK_ACTIONS: "settings/_quick_action_library.html",
    WORKSPACE_SMART_PHRASES: "settings/_smart_phrase_library.html",
    WORKSPACE_AI_SERVICES: "settings/_ai_services.html",
    WORKSPACE_TEAM_MEMBERS: "settings/_team_members.html",
    WORKSPACE_ACCOUNT_REQUESTS: "settings/_account_requests.html",
}

WORKSPACE_LIBRARY_SECTIONS: Final = frozenset(
    {WORKSPACE_TEMPLATES, WORKSPACE_QUICK_ACTIONS, WORKSPACE_SMART_PHRASES}
)
WORKSPACE_TEAM_SECTIONS: Final = frozenset(
    {WORKSPACE_AI_SERVICES, WORKSPACE_TEAM_MEMBERS, WORKSPACE_ACCOUNT_REQUESTS}
)


def build_workspace_shell_context(
    db: Session,
    *,
    current_user: User,
    active_section: str,
) -> dict[str, object]:
    """Load shell-only data; never decrypt transcript-derived content."""
    if active_section not in WORKSPACE_SECTION_PATHS:
        raise ValueError("Unknown workspace section")
    recent_page: dict[str, object] = {"items": [], "has_more": False}
    if active_section == WORKSPACE_SCRIBE:
        recent_page = list_transcript_history_page(
            db,
            current_user,
            limit=TRANSCRIPT_HISTORY_DEFAULT_LIMIT,
        )
    recent_transcripts = recent_page["items"]
    return {
        "active_workspace_section": active_section,
        "workspace_content_template": WORKSPACE_SECTION_TEMPLATES.get(active_section),
        "workspace_section_paths": WORKSPACE_SECTION_PATHS,
        "workspace_recent_transcripts": recent_transcripts,
        "workspace_recent_has_more": recent_page["has_more"],
        # Only Scribe may publish an active ID. Non-Scribe pages must not
        # overwrite browser memory of an older, deliberately selected session.
        "active_transcript_id": None,
        "back_to_scribe_url": "/workspace?open_recent=1",
        "new_consultation_action": "/transcribe/sessions",
        "is_workspace_manager": (
            not current_user.is_system_admin
            and current_user.team_id is not None
            and current_user.team_role is TeamRole.leader
        ),
        "show_workspace_library": not current_user.is_system_admin and current_user.team_id is not None,
    }


def render_workspace(
    request,
    db: Session,
    *,
    current_user: User,
    active_section: str,
    section_context: dict[str, object] | None = None,
    status_code: int = 200,
):
    """Render trusted workspace shell around explicitly supplied section data."""
    context = {
        "request": request,
        "current_user": current_user,
        **build_workspace_shell_context(
            db,
            current_user=current_user,
            active_section=active_section,
        ),
        **(section_context or {}),
    }
    user_app_preferences_json = context.get("user_app_preferences_json")
    if not isinstance(user_app_preferences_json, dict):
        user_app_preference = get_user_app_preferences(db, current_user)
        user_app_preferences_json = (
            user_app_preference.preferences_json
            if user_app_preference is not None
            and isinstance(user_app_preference.preferences_json, dict)
            else {}
        )
    context["preferred_recording_mode"] = user_app_preferences_json.get(
        "preferred_recording_mode"
    )
    # Shell authority wins over legacy section context.
    context["active_workspace_section"] = active_section
    context["workspace_content_template"] = WORKSPACE_SECTION_TEMPLATES.get(active_section)
    context["is_manager"] = context["is_workspace_manager"]
    context["workspace_page_title"] = active_section.replace("-", " ").title()
    return templates.TemplateResponse(request, "workspace.html", context, status_code=status_code)
