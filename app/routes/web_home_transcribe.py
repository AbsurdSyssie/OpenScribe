"""Home and transcribe browser routes extracted from app.main."""

from urllib.parse import urlencode

from pydantic import ValidationError

from ..main import *  # noqa: F401,F403
from ..main import (
    _home_page_route_from_return_view,
    _home_redirect_url,
    _home_return_view_value,
    _home_template_editor_url,
    _home_template_name_from_return_view,
    _page_context_or_redirect,
    _clear_trusted_device_cookie,
    _set_session_cookie,
    _template_config_from_form,
    ACCOUNT_SECURITY_RATE_LIMIT,
)
from ..services.account import update_own_email, update_own_name, update_own_password
from ..services.auth import create_session, revoke_sessions_for_user, revoke_trusted_devices_for_user
from ..services.oidc import linked_oidc_identity, oidc_configs
from ..services.security_audit import record_security_event
from ..services.templates import fork_team_quick_action_to_personal as fork_team_quick_action_to_personal_service
from ..stt_normalization import normalize_stt_language
from ..web.workspace import (
    WORKSPACE_ACCOUNT,
    WORKSPACE_ACCOUNT_REQUESTS,
    WORKSPACE_AI_SERVICES,
    WORKSPACE_LIBRARY_SECTIONS,
    WORKSPACE_PREFERENCES,
    WORKSPACE_QUICK_ACTIONS,
    WORKSPACE_SMART_PHRASES,
    WORKSPACE_TEAM_MEMBERS,
    WORKSPACE_TEAM_SECTIONS,
    WORKSPACE_TEMPLATES,
    WORKSPACE_SECTION_PATHS,
    render_workspace,
)


def _settings_template_url(*, scope: str, template_id: str) -> str:
    return f"/workspace/library/templates?{urlencode({'scope': scope, 'template_id': template_id})}"


def _settings_quick_action_url(*, scope: str, quick_action_id: str) -> str:
    return f"/workspace/library/quick-actions?{urlencode({'scope': scope, 'quick_action_id': quick_action_id})}"


def _is_settings_return(return_view: str) -> bool:
    return _home_return_view_value(return_view) in {"settings", "workspace"}


def _render_home_feedback(request: Request, db: Session, **kwargs):
    """Keep explicit previews intact; wrap canonical feedback in workspace shell."""
    response = render_home(request, db, **kwargs)
    if kwargs.get("home_return_view") != "workspace":
        return response
    active_tab = kwargs.get("active_home_tab")
    sections = {
        "overview": WORKSPACE_PREFERENCES,
        "preferences": WORKSPACE_PREFERENCES,
        "templates": WORKSPACE_TEMPLATES,
        "quick-actions": WORKSPACE_QUICK_ACTIONS,
        "smart-phrases": WORKSPACE_SMART_PHRASES,
        "ai-services": WORKSPACE_AI_SERVICES,
        "team-management": WORKSPACE_TEAM_MEMBERS,
        "team-members": WORKSPACE_TEAM_MEMBERS,
        "account-requests": WORKSPACE_ACCOUNT_REQUESTS,
    }
    active_section = sections.get(active_tab, WORKSPACE_PREFERENCES)
    return render_workspace(
        request,
        db,
        current_user=kwargs["current_user"],
        active_section=active_section,
        section_context=dict(response.context),
        status_code=response.status_code,
    )


def _render_home_page(
    request: Request,
    db: Session,
    *,
    message: str | None,
    message_kind: str,
    queued_transcript_id: str | None,
    transcribe_tab: str | None,
    tab: str | None,
    modal: str | None,
    team_template_id: str | None,
    personal_template_id: str | None,
    team_quick_action_id: str | None,
    personal_quick_action_id: str | None,
    home_page_route: str,
    home_return_view: str,
    template_name: str,
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if modal in {"personal-template", "team-template"}:
        scope = "team" if modal == "team-template" else "personal"
        selected_template_id = team_template_id if scope == "team" else personal_template_id
        return RedirectResponse(
            url=_home_template_editor_url(
                scope=scope,
                template_id=selected_template_id,
                return_view=home_return_view,
                queued_transcript_id=queued_transcript_id,
                transcribe_tab=transcribe_tab,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_home(
        request,
        db,
        current_user=context.user,
        selected_team_template_id=team_template_id,
        selected_personal_template_id=personal_template_id,
        selected_team_quick_action_id=team_quick_action_id,
        selected_personal_quick_action_id=personal_quick_action_id,
        message=message,
        message_kind=safe_message_kind,
        queued_transcript_id=queued_transcript_id,
        active_home_tab=tab,
        active_home_modal=modal,
        template_name=template_name,
        home_page_route=home_page_route,
        home_return_view=home_return_view,
        transcribe_return_tab=transcribe_tab,
    )


@app.get("/home", response_class=HTMLResponse)
def home_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    return_view: str = "",
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
    tab: str | None = None,
    modal: str | None = None,
    team_template_id: str | None = None,
    personal_template_id: str | None = None,
    team_quick_action_id: str | None = None,
    personal_quick_action_id: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    legacy_tab = tab or "scribe"
    destination = _home_redirect_url(return_view="workspace", return_tab=legacy_tab)
    params: dict[str, str] = {}
    if legacy_tab == "templates":
        if team_template_id:
            params = {"scope": "team", "template_id": team_template_id}
        elif personal_template_id:
            params = {"scope": "personal", "template_id": personal_template_id}
        elif modal == "team-template":
            params = {"scope": "team", "template_id": "new"}
        elif modal == "personal-template":
            params = {"scope": "personal", "template_id": "new"}
    elif legacy_tab == "quick-actions":
        if team_quick_action_id:
            params = {"scope": "team", "quick_action_id": team_quick_action_id}
        elif personal_quick_action_id:
            params = {"scope": "personal", "quick_action_id": personal_quick_action_id}
        elif modal == "team-quick-action":
            params = {"scope": "team", "quick_action_id": "new"}
        elif modal == "personal-quick-action":
            params = {"scope": "personal", "quick_action_id": "new"}
    if message:
        params["message"] = message
        params["message_kind"] = message_kind if message_kind in {"success", "error"} else "success"
    if params:
        destination = f"{destination}?{urlencode(params)}"
    return RedirectResponse(url=destination, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    tab: str | None = None,
    scope: str | None = None,
    template_id: str | None = None,
    quick_action_id: str | None = None,
    smart_phrase_id: str | None = None,
):
    destinations = {
        "account": "/workspace/account",
        "preferences": "/workspace/preferences",
        "templates": "/workspace/library/templates",
        "quick-actions": "/workspace/library/quick-actions",
        "smart-phrases": "/workspace/library/smart-phrases",
        "ai-services": "/workspace/team/ai-services",
        "team-members": "/workspace/team/members",
        "team-management": "/workspace/team/members",
        "account-requests": "/workspace/team/account-requests",
    }
    destination = destinations.get(tab or "", "/workspace/preferences")
    params: dict[str, str] = {}
    if tab == "templates" and scope in {"personal", "team"}:
        editor_id = _valid_workspace_editor_id(template_id)
        if editor_id:
            params = {"scope": scope, "template_id": editor_id}
    elif tab == "quick-actions" and scope in {"personal", "team"}:
        editor_id = _valid_workspace_editor_id(quick_action_id)
        if editor_id:
            params = {"scope": scope, "quick_action_id": editor_id}
    elif tab == "smart-phrases":
        editor_id = _valid_workspace_editor_id(smart_phrase_id)
        if editor_id:
            params = {"smart_phrase_id": editor_id}
    if params:
        destination = f"{destination}?{urlencode(params)}"
    return RedirectResponse(
        url=destination,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


def _valid_workspace_editor_id(value: str | None) -> str | None:
    if value == "new":
        return value
    if not value:
        return None
    try:
        UUID(value)
    except ValueError:
        return None
    return value


def _workspace_section_page(
    request: Request,
    db: Session,
    *,
    section: str,
    scope: str | None = None,
    template_id: str | None = None,
    quick_action_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if section in WORKSPACE_LIBRARY_SECTIONS and context.user.team_id is None:
        return HTMLResponse("Workspace section unavailable", status_code=status.HTTP_403_FORBIDDEN)
    if section in WORKSPACE_TEAM_SECTIONS and (
        context.user.team_id is None or context.user.team_role is not TeamRole.leader
    ):
        return HTMLResponse("Workspace section unavailable", status_code=status.HTTP_403_FORBIDDEN)
    if section == WORKSPACE_ACCOUNT:
        safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
        configured_oidc = oidc_configs()
        return render_workspace(
            request,
            db,
            current_user=context.user,
            active_section=section,
            section_context={
                "message": message,
                "message_kind": safe_message_kind,
                "oidc_providers": tuple(
                    {
                        "key": config.provider_key,
                        "name": config.provider_name,
                        "linked": linked_oidc_identity(db, context.user, config) is not None,
                    }
                    for config in configured_oidc
                ),
            },
        )
    valid_scope = scope if scope in {"personal", "team"} else None
    valid_template_id = _valid_workspace_editor_id(template_id)
    valid_quick_action_id = _valid_workspace_editor_id(quick_action_id)
    legacy_response = _render_home_page(
        request,
        db,
        message=message,
        message_kind=message_kind,
        queued_transcript_id=None,
        transcribe_tab=None,
        tab=section,
        modal=None,
        team_template_id=valid_template_id if valid_scope == "team" else None,
        personal_template_id=valid_template_id if valid_scope == "personal" else None,
        team_quick_action_id=valid_quick_action_id if valid_scope == "team" else None,
        personal_quick_action_id=valid_quick_action_id if valid_scope == "personal" else None,
        template_name="settings.html",
        home_page_route=WORKSPACE_SECTION_PATHS[section],
        home_return_view="settings",
    )
    return render_workspace(
        request,
        db,
        current_user=context.user,
        active_section=section,
        section_context=dict(legacy_response.context),
        status_code=legacy_response.status_code,
    )


@app.get("/workspace/account", response_class=HTMLResponse)
def workspace_account_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    db: Session = Depends(get_db),
):
    return _workspace_section_page(
        request,
        db,
        section=WORKSPACE_ACCOUNT,
        message=message,
        message_kind=message_kind,
    )


@app.get("/workspace/preferences", response_class=HTMLResponse)
def workspace_preferences_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    db: Session = Depends(get_db),
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_PREFERENCES, message=message, message_kind=message_kind
    )


@app.get("/workspace/library/templates", response_class=HTMLResponse)
def workspace_templates_page(
    request: Request,
    scope: str | None = None,
    template_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    db: Session = Depends(get_db),
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_TEMPLATES, scope=scope, template_id=template_id,
        message=message, message_kind=message_kind
    )


@app.get("/workspace/library/quick-actions", response_class=HTMLResponse)
def workspace_quick_actions_page(
    request: Request,
    scope: str | None = None,
    quick_action_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    db: Session = Depends(get_db),
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_QUICK_ACTIONS, scope=scope, quick_action_id=quick_action_id,
        message=message, message_kind=message_kind
    )


@app.get("/workspace/library/smart-phrases", response_class=HTMLResponse)
def workspace_smart_phrases_page(
    request: Request, message: str | None = None, message_kind: str = "success",
    db: Session = Depends(get_db)
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_SMART_PHRASES, message=message, message_kind=message_kind
    )


@app.get("/workspace/team/ai-services", response_class=HTMLResponse)
def workspace_ai_services_page(
    request: Request, message: str | None = None, message_kind: str = "success",
    db: Session = Depends(get_db)
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_AI_SERVICES, message=message, message_kind=message_kind
    )


@app.get("/workspace/team/members", response_class=HTMLResponse)
def workspace_team_members_page(
    request: Request, message: str | None = None, message_kind: str = "success",
    db: Session = Depends(get_db)
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_TEAM_MEMBERS, message=message, message_kind=message_kind
    )


@app.get("/workspace/team/account-requests", response_class=HTMLResponse)
def workspace_account_requests_page(
    request: Request, message: str | None = None, message_kind: str = "success",
    db: Session = Depends(get_db)
):
    return _workspace_section_page(
        request, db, section=WORKSPACE_ACCOUNT_REQUESTS, message=message, message_kind=message_kind
    )


def _render_account_error(request: Request, db: Session, context, exc: AppError):
    record_security_event(
        db,
        action="account_change_failure",
        actor=context.user,
        target=context.user,
        request=request,
        details={"category": "account", "outcome": "failure", "reason_code": exc.code, "status_code": exc.status_code},
    )
    return render_workspace(
        request,
        db,
        current_user=context.user,
        active_section=WORKSPACE_ACCOUNT,
        section_context={"message": exc.message, "message_kind": "error"},
        status_code=exc.status_code,
    )


def _account_success_redirect(message: str) -> RedirectResponse:
    query = urlencode({"message": message, "message_kind": "success"})
    return RedirectResponse(url=f"/workspace/account?{query}", status_code=status.HTTP_303_SEE_OTHER)


def _rotate_account_session(request: Request, db: Session, context, *, reason: str) -> RedirectResponse:
    revoke_sessions_for_user(db, context.user, reason=reason)
    revoke_trusted_devices_for_user(db, context.user, reason=reason)
    token = create_session(db, context.user, auth_level=context.session.auth_level)
    response = _account_success_redirect("Account security updated")
    _set_session_cookie(request, response, token)
    _clear_trusted_device_cookie(response)
    return response


@app.post("/settings/account/name", response_class=HTMLResponse)
def settings_account_name(
    request: Request,
    full_name: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    try:
        user = update_own_name(db, context.user, full_name=full_name)
    except AppError as exc:
        return _render_account_error(request, db, context, exc)
    record_security_event(
        db,
        action="account_name_changed",
        actor=user,
        target=user,
        request=request,
        details={"category": "account", "outcome": "success", "changed_fields": ["full_name"]},
    )
    return _account_success_redirect("Name updated")


@app.post("/settings/account/email", response_class=HTMLResponse)
@ACCOUNT_SECURITY_RATE_LIMIT
def settings_account_email(
    request: Request,
    email: str = Form(...),
    current_password: str = Form(...),
    mfa_code: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    try:
        user = update_own_email(
            db,
            context.user,
            email=email,
            current_password=current_password,
            mfa_code=mfa_code,
        )
    except AppError as exc:
        return _render_account_error(request, db, context, exc)
    response = _rotate_account_session(request, db, context, reason="email_changed")
    record_security_event(
        db,
        action="account_email_changed",
        actor=user,
        target=user,
        request=request,
        details={"category": "account", "outcome": "success", "changed_fields": ["email"]},
    )
    return response


@app.post("/settings/account/password", response_class=HTMLResponse)
@ACCOUNT_SECURITY_RATE_LIMIT
def settings_account_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    mfa_code: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    try:
        user = update_own_password(
            db,
            context.user,
            current_password=current_password,
            new_password=new_password,
            confirm_password=confirm_password,
            mfa_code=mfa_code,
        )
    except AppError as exc:
        return _render_account_error(request, db, context, exc)
    response = _rotate_account_session(request, db, context, reason="password_changed")
    record_security_event(
        db,
        action="account_password_changed",
        actor=user,
        target=user,
        request=request,
        details={"category": "account", "outcome": "success", "changed_fields": ["password"]},
    )
    return response


@app.get("/home-restyled", response_class=HTMLResponse)
def home_restyled_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    return_view: str = "",
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
    tab: str | None = None,
    modal: str | None = None,
    team_template_id: str | None = None,
    personal_template_id: str | None = None,
    team_quick_action_id: str | None = None,
    personal_quick_action_id: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if modal in {"personal-template", "team-template"}:
        scope = "team" if modal == "team-template" else "personal"
        selected_template_id = team_template_id if scope == "team" else personal_template_id
        return RedirectResponse(
            url=_home_template_editor_url(
                scope=scope,
                template_id=selected_template_id,
                return_view=return_view or "restyled",
                queued_transcript_id=queued_transcript_id,
                transcribe_tab=transcribe_tab,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_home(
        request,
        db,
        current_user=context.user,
        selected_team_template_id=team_template_id,
        selected_personal_template_id=personal_template_id,
        selected_team_quick_action_id=team_quick_action_id,
        selected_personal_quick_action_id=personal_quick_action_id,
        message=message,
        message_kind=safe_message_kind,
        queued_transcript_id=queued_transcript_id,
        active_home_tab=tab,
        active_home_modal=modal,
        template_name="home.html",
        home_page_route="/home-restyled",
        home_return_view=_home_return_view_value(return_view or "restyled"),
        transcribe_return_tab=transcribe_tab,
    )


@app.get("/home2", response_class=HTMLResponse)
def home2_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    return_view: str = "",
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
    tab: str | None = None,
    modal: str | None = None,
    team_template_id: str | None = None,
    personal_template_id: str | None = None,
    team_quick_action_id: str | None = None,
    personal_quick_action_id: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if modal in {"personal-template", "team-template"}:
        scope = "team" if modal == "team-template" else "personal"
        selected_template_id = team_template_id if scope == "team" else personal_template_id
        return RedirectResponse(
            url=_home_template_editor_url(
                scope=scope,
                template_id=selected_template_id,
                return_view=return_view or "home2",
                queued_transcript_id=queued_transcript_id,
                transcribe_tab=transcribe_tab,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_home(
        request,
        db,
        current_user=context.user,
        selected_team_template_id=team_template_id,
        selected_personal_template_id=personal_template_id,
        selected_team_quick_action_id=team_quick_action_id,
        selected_personal_quick_action_id=personal_quick_action_id,
        message=message,
        message_kind=safe_message_kind,
        queued_transcript_id=queued_transcript_id,
        active_home_tab=tab,
        active_home_modal=modal,
        template_name="home.html",
        home_page_route="/home2",
        home_return_view=_home_return_view_value(return_view or "home2"),
        transcribe_return_tab=transcribe_tab,
        home_style_variant="home2",
    )


@app.get("/home/templates/editor", response_class=HTMLResponse)
def home_template_editor_page(
    request: Request,
    scope: str,
    template_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    return_view: str = "",
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if scope not in {"personal", "team"}:
        return RedirectResponse(url=_home_redirect_url(return_view=return_view, return_tab="templates"), status_code=status.HTTP_303_SEE_OTHER)
    if scope == "team" and context.user.team_role is not TeamRole.leader:
        return RedirectResponse(url=_home_redirect_url(return_view=return_view, return_tab="templates"), status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_home(
        request,
        db,
        current_user=context.user,
        selected_team_template_id=template_id if scope == "team" else None,
        selected_personal_template_id=template_id if scope == "personal" else None,
        message=message,
        message_kind=safe_message_kind,
        queued_transcript_id=queued_transcript_id,
        active_home_tab="templates",
        template_name="template_editor.html",
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
        transcribe_return_tab=transcribe_tab,
        template_editor_scope=scope,
    )


@app.post("/home/llm-selection", response_class=HTMLResponse)
def home_set_llm_selection(
    request: Request,
    llm_config_id: str = Form(...),
    allowed_model_names: list[str] = Form(default=[]),
    provider_model: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        set_team_llm_selection_service(
            db,
            context.user,
            LlmSelectionUpsert(
                llm_config_id=UUID(llm_config_id),
                allowed_models_json=allowed_model_names,
                model_name_override=provider_model or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="llm-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/llm-selection/clear", response_class=HTMLResponse)
def home_clear_llm_selection(
    request: Request,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        clear_team_llm_selection_service(db, context.user)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="llm-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/deidentification-selection", response_class=HTMLResponse)
def home_set_deidentification_selection(
    request: Request,
    provider_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        set_team_deidentification_selection_service(
            db,
            context.user,
            DeidentificationSelectionUpsert(provider_id=UUID(provider_id)),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="deidentification-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/deidentification-selection/clear", response_class=HTMLResponse)
def home_clear_deidentification_selection(
    request: Request,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        clear_team_deidentification_selection_service(db, context.user)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="deidentification-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/clinical-nlp-selection", response_class=HTMLResponse)
def home_set_clinical_nlp_selection(
    request: Request,
    provider_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        set_team_clinical_nlp_selection_service(
            db,
            context.user,
            ClinicalNlpSelectionUpsert(provider_id=UUID(provider_id)),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid clinical NLP selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="clinical-nlp-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/clinical-nlp-selection/clear", response_class=HTMLResponse)
def home_clear_clinical_nlp_selection(
    request: Request,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        clear_team_clinical_nlp_selection_service(db, context.user)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="clinical-nlp-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/llm-preference", response_class=HTMLResponse)
def home_set_llm_preference(
    request: Request,
    preferred_model_name: str = Form(""),
    note_generation_length: str = Form(""),
    llm_detail_level: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        set_user_llm_preference_service(
            db,
            context.user,
            UserLlmPreferenceUpsert(preferred_model_name=preferred_model_name or None),
        )
        if note_generation_length or llm_detail_level:
            preference = get_user_app_preferences_service(db, context.user)
            existing = preference.preferences_json if preference is not None and isinstance(preference.preferences_json, dict) else {}
            set_user_app_preferences_service(
                db,
                context.user,
                UserAppPreferencesUpsert(
                    favorite_quick_action_ids=existing.get("favorite_quick_action_ids") or [],
                    favorite_template_ids=existing.get("favorite_template_ids") or [],
                    default_quick_action_id=existing.get("default_quick_action_id"),
                    default_template_id=existing.get("default_template_id"),
                    template_suggestions_enabled=existing.get("template_suggestions_enabled") is not False,
                    llm_detail_level=llm_detail_level or existing.get("llm_detail_level"),
                    note_generation_length=note_generation_length or existing.get("note_generation_length"),
                    preferred_recording_mode=existing.get("preferred_recording_mode"),
                    preferred_transcribe_tab=existing.get("preferred_transcribe_tab"),
                ),
            )
    except (ValueError, ValidationError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM preference"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "preferences",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "preferences"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/template-suggestion-preference", response_class=HTMLResponse)
def home_set_template_suggestion_preference(
    request: Request,
    template_suggestions_enabled: str = Form("false"),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    preference = get_user_app_preferences_service(db, context.user)
    existing = preference.preferences_json if preference is not None and isinstance(preference.preferences_json, dict) else {}
    try:
        set_user_app_preferences_service(
            db,
            context.user,
            UserAppPreferencesUpsert(
                favorite_quick_action_ids=existing.get("favorite_quick_action_ids") or [],
                favorite_template_ids=existing.get("favorite_template_ids") or [],
                default_quick_action_id=existing.get("default_quick_action_id"),
                default_template_id=existing.get("default_template_id"),
                template_suggestions_enabled=template_suggestions_enabled == "true",
                llm_detail_level=existing.get("llm_detail_level"),
                note_generation_length=existing.get("note_generation_length"),
                preferred_recording_mode=existing.get("preferred_recording_mode"),
                preferred_transcribe_tab=existing.get("preferred_transcribe_tab"),
            ),
        )
    except AppError as exc:
        return _render_home_feedback(
            request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code,
            active_home_tab=return_tab or "preferences", template_name="settings.html" if _is_settings_return(return_view) else "home.html",
            home_page_route=_home_page_route_from_return_view(return_view), home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "preferences"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/llm-preference/clear", response_class=HTMLResponse)
def home_clear_llm_preference(
    request: Request,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        clear_user_llm_preference_service(db, context.user)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "preferences",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "preferences"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-templates", response_class=HTMLResponse)
def home_upsert_team_template(
    request: Request,
    template_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    home_modal: str = Form(""),
    name: str = Form(...),
    description: str = Form(""),
    prompt_text: str = Form(...),
    mode: str = Form("freeform"),
    section_prompt_problem: str = Form(""),
    section_prompt_history: str = Form(""),
    section_prompt_family_history: str = Form(""),
    section_prompt_social_history: str = Form(""),
    section_prompt_examination: str = Form(""),
    section_prompt_comment: str = Form(""),
    section_prompt_tasks: str = Form(""),
    section_prompt_investigations: str = Form(""),
    is_active: str | None = Form(default=None),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        template_mode = TemplateMode(mode)
        saved_template = upsert_team_template_service(
            db,
            context.user,
            PromptTemplateUpsert(
                template_id=UUID(template_id) if template_id else None,
                scope=TemplateScope.team,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                mode=template_mode,
                config_json=_template_config_from_form(
                    mode=template_mode,
                    section_values={
                        "problem": section_prompt_problem,
                        "history": section_prompt_history,
                        "family_history": section_prompt_family_history,
                        "social_history": section_prompt_social_history,
                        "examination": section_prompt_examination,
                        "comment": section_prompt_comment,
                        "tasks": section_prompt_tasks,
                        "investigations": section_prompt_investigations,
                    }
                ),
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid team template"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_template_id=template_id or ("new" if _is_settings_return(return_view) else None),
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name="settings.html" if _is_settings_return(return_view) else "template_editor.html",
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
            template_editor_scope="team",
            template_editor_form_values={
                "name": name,
                "description": description,
                "prompt_text": prompt_text,
                "mode": mode,
                "is_active": is_active == "true",
                "section_prompts": {
                    "problem": section_prompt_problem,
                    "history": section_prompt_history,
                    "family_history": section_prompt_family_history,
                    "social_history": section_prompt_social_history,
                    "examination": section_prompt_examination,
                    "comment": section_prompt_comment,
                    "tasks": section_prompt_tasks,
                    "investigations": section_prompt_investigations,
                },
            },
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_template_url(scope="team", template_id=str(saved_template.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "templates",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-templates/{template_id}/delete", response_class=HTMLResponse)
def home_delete_team_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        delete_team_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "templates",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-templates/{template_id}/duplicate", response_class=HTMLResponse)
def home_duplicate_team_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        duplicated = duplicate_team_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name="settings.html" if _is_settings_return(return_view) else "template_editor.html",
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
            template_editor_scope="team",
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_template_url(scope="team", template_id=str(duplicated.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_template_editor_url(
            scope="team",
            template_id=str(duplicated.id),
            return_view=return_view,
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-templates/{template_id}/fork", response_class=HTMLResponse)
def home_fork_team_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        forked = fork_team_template_to_personal_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_template_url(scope="personal", template_id=str(forked.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_template_editor_url(
            scope="personal",
            template_id=str(forked.id),
            return_view=return_view,
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/personal-templates", response_class=HTMLResponse)
def home_upsert_personal_template(
    request: Request,
    template_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    home_modal: str = Form(""),
    name: str = Form(...),
    description: str = Form(""),
    prompt_text: str = Form(...),
    mode: str = Form("freeform"),
    section_prompt_problem: str = Form(""),
    section_prompt_history: str = Form(""),
    section_prompt_family_history: str = Form(""),
    section_prompt_social_history: str = Form(""),
    section_prompt_examination: str = Form(""),
    section_prompt_comment: str = Form(""),
    section_prompt_tasks: str = Form(""),
    section_prompt_investigations: str = Form(""),
    is_active: str | None = Form(default=None),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        template_mode = TemplateMode(mode)
        saved_template = upsert_personal_template_service(
            db,
            context.user,
            PromptTemplateUpsert(
                template_id=UUID(template_id) if template_id else None,
                scope=TemplateScope.user,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                mode=template_mode,
                config_json=_template_config_from_form(
                    mode=template_mode,
                    section_values={
                        "problem": section_prompt_problem,
                        "history": section_prompt_history,
                        "family_history": section_prompt_family_history,
                        "social_history": section_prompt_social_history,
                        "examination": section_prompt_examination,
                        "comment": section_prompt_comment,
                        "tasks": section_prompt_tasks,
                        "investigations": section_prompt_investigations,
                    }
                ),
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid personal template"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_personal_template_id=template_id or ("new" if _is_settings_return(return_view) else None),
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name="settings.html" if _is_settings_return(return_view) else "template_editor.html",
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
            template_editor_scope="personal",
            template_editor_form_values={
                "name": name,
                "description": description,
                "prompt_text": prompt_text,
                "mode": mode,
                "is_active": is_active == "true",
                "section_prompts": {
                    "problem": section_prompt_problem,
                    "history": section_prompt_history,
                    "family_history": section_prompt_family_history,
                    "social_history": section_prompt_social_history,
                    "examination": section_prompt_examination,
                    "comment": section_prompt_comment,
                    "tasks": section_prompt_tasks,
                    "investigations": section_prompt_investigations,
                },
            },
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_template_url(scope="personal", template_id=str(saved_template.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "templates",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/personal-templates/{template_id}/duplicate", response_class=HTMLResponse)
def home_duplicate_personal_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        duplicated = duplicate_personal_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_personal_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name="settings.html" if _is_settings_return(return_view) else "template_editor.html",
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
            template_editor_scope="personal",
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_template_url(scope="personal", template_id=str(duplicated.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_template_editor_url(
            scope="personal",
            template_id=str(duplicated.id),
            return_view=return_view,
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/personal-templates/{template_id}/delete", response_class=HTMLResponse)
def home_delete_personal_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        delete_personal_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "templates",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-quick-actions", response_class=HTMLResponse)
def home_upsert_team_quick_action(
    request: Request,
    quick_action_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    home_modal: str = Form(""),
    name: str = Form(...),
    description: str = Form(""),
    prompt_text: str = Form(...),
    is_active: str | None = Form(default=None),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        saved_quick_action = upsert_team_quick_action_service(
            db,
            context.user,
            QuickActionUpsert(
                quick_action_id=UUID(quick_action_id) if quick_action_id else None,
                scope=TemplateScope.team,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid team quick action"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_quick_action_id=quick_action_id or ("new" if _is_settings_return(return_view) else None),
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            active_home_modal=None if _is_settings_return(return_view) else (home_modal or "team-quick-action"),
            status_code=status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
            quick_action_editor_form_values={
                "name": name,
                "description": description,
                "prompt_text": prompt_text,
                "is_active": is_active == "true",
            },
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_quick_action_url(scope="team", quick_action_id=str(saved_quick_action.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-quick-actions/{quick_action_id}/delete", response_class=HTMLResponse)
def home_delete_team_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        delete_team_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    if _is_settings_return(return_view):
        return RedirectResponse(url="/workspace/library/quick-actions", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-quick-actions/{quick_action_id}/duplicate", response_class=HTMLResponse)
def home_duplicate_team_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        duplicated = duplicate_team_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            active_home_modal="team-quick-action",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_quick_action_url(scope="team", quick_action_id=str(duplicated.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ) + f"&modal=team-quick-action&team_quick_action_id={duplicated.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/team-quick-actions/{quick_action_id}/fork", response_class=HTMLResponse)
def home_fork_team_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        forked = fork_team_quick_action_to_personal_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_team_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_quick_action_url(scope="personal", quick_action_id=str(forked.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ) + f"&modal=personal-quick-action&personal_quick_action_id={forked.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/personal-quick-actions", response_class=HTMLResponse)
def home_upsert_personal_quick_action(
    request: Request,
    quick_action_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    home_modal: str = Form(""),
    name: str = Form(...),
    description: str = Form(""),
    prompt_text: str = Form(...),
    is_active: str | None = Form(default=None),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        saved_quick_action = upsert_personal_quick_action_service(
            db,
            context.user,
            QuickActionUpsert(
                quick_action_id=UUID(quick_action_id) if quick_action_id else None,
                scope=TemplateScope.user,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid personal quick action"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_personal_quick_action_id=quick_action_id or ("new" if _is_settings_return(return_view) else None),
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            active_home_modal=None if _is_settings_return(return_view) else (home_modal or "personal-quick-action"),
            status_code=status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
            quick_action_editor_form_values={
                "name": name,
                "description": description,
                "prompt_text": prompt_text,
                "is_active": is_active == "true",
            },
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_quick_action_url(scope="personal", quick_action_id=str(saved_quick_action.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/personal-quick-actions/{quick_action_id}/duplicate", response_class=HTMLResponse)
def home_duplicate_personal_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        duplicated = duplicate_personal_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_personal_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            active_home_modal="personal-quick-action",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    if _is_settings_return(return_view):
        return RedirectResponse(
            url=_settings_quick_action_url(scope="personal", quick_action_id=str(duplicated.id)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ) + f"&modal=personal-quick-action&personal_quick_action_id={duplicated.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/personal-quick-actions/{quick_action_id}/delete", response_class=HTMLResponse)
def home_delete_personal_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    queued_transcript_id: str = Form(""),
    transcribe_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        delete_personal_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            selected_personal_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            status_code=exc.status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
        )
    if _is_settings_return(return_view):
        return RedirectResponse(url="/workspace/library/quick-actions", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/stt-selection", response_class=HTMLResponse)
def home_set_stt_selection(
    request: Request,
    stt_config_id: str = Form(...),
    purpose: str = Form("conversation"),
    provider_model: str = Form(""),
    language: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        set_team_stt_selection_service(
            db,
            context.user,
            SttSelectionUpsert(
                purpose=SttSelectionPurpose(purpose),
                stt_config_id=UUID(stt_config_id),
                model_name_override=provider_model or None,
                language_override=normalize_stt_language(language),
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="stt-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/stt-selection/clear", response_class=HTMLResponse)
def home_clear_stt_selection(
    request: Request,
    purpose: str = Form("conversation"),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        clear_team_stt_selection_service(db, context.user, purpose=SttSelectionPurpose(purpose))
    except AppError as exc:
        return _render_home_feedback(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "ai-services",
            active_home_modal="stt-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "ai-services"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
