"""Admin browser routes extracted from app.main."""

from .. import main as main_module
from ..main import *  # noqa: F401,F403
from ..main import (
    _admin_page_route_from_return_view,
    _admin_redirect_url,
    _admin_return_view_value,
    _page_context_or_redirect,
)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    team_id: str | None = None,
    stt_config_id: str | None = None,
    llm_config_id: str | None = None,
    deidentification_provider_id: str | None = None,
    default_template_id: str | None = None,
    default_quick_action_id: str | None = None,
    tab: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_stt_config_id=stt_config_id,
        selected_llm_config_id=llm_config_id,
        selected_deidentification_provider_id=deidentification_provider_id,
        selected_default_template_id=default_template_id,
        selected_default_quick_action_id=default_quick_action_id,
        active_admin_tab=tab,
        admin_page_route="/admin",
        admin_return_view="",
    )


@app.get("/admin-restyled", response_class=HTMLResponse)
def admin_restyled_page(
    request: Request,
    team_id: str | None = None,
    stt_config_id: str | None = None,
    llm_config_id: str | None = None,
    deidentification_provider_id: str | None = None,
    default_template_id: str | None = None,
    default_quick_action_id: str | None = None,
    tab: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_stt_config_id=stt_config_id,
        selected_llm_config_id=llm_config_id,
        selected_deidentification_provider_id=deidentification_provider_id,
        selected_default_template_id=default_template_id,
        selected_default_quick_action_id=default_quick_action_id,
        active_admin_tab=tab,
        admin_page_route="/admin-restyled",
        admin_return_view="restyled",
    )


@app.post("/admin/teams", response_class=HTMLResponse)
def admin_create_team(
    request: Request,
    name: str = Form(...),
    status_value: TeamStatus = Form(..., alias="status"),
    default_retention_days: int = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        create_team_service(db, TeamCreate(name=name, status=status_value, default_retention_days=default_retention_days), actor=context.user)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or None,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/teams/{team_id}/delete", response_class=HTMLResponse)
def admin_delete_team(
    request: Request,
    team_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form("directory"),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_team_service(db, context.user, team_id=team_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=str(team_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/templates/editor", response_class=HTMLResponse)
def admin_template_editor_page(
    request: Request,
    scope: str,
    template_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    return_view: str = "",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    if scope != "default":
        return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab="defaults"), status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_default_template_id=template_id,
        message=message,
        message_kind=safe_message_kind,
        active_admin_tab="defaults",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
        template_name="template_editor.html",
    )


@app.post("/admin/default-templates", response_class=HTMLResponse)
def admin_upsert_default_template(
    request: Request,
    template_id: str = Form(""),
    return_view: str = Form(""),
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
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        template_mode = TemplateMode(mode)
        template = upsert_default_template_service(
            db,
            context.user,
            DefaultPromptTemplateUpsert(
                template_id=UUID(template_id) if template_id else None,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                mode=template_mode,
                config_json=main_module._template_config_from_form(
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
        detail = exc.message if isinstance(exc, AppError) else "Invalid default template"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_template_id=template_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab="defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
            template_name="template_editor.html",
        )
    return RedirectResponse(
        url=f"/admin/templates/editor?scope=default&template_id={template.id}&return_view={_admin_return_view_value(return_view)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/default-templates/{template_id}/delete", response_class=HTMLResponse)
def admin_delete_default_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_default_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab="defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab="defaults"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/default-templates/{template_id}/duplicate", response_class=HTMLResponse)
def admin_duplicate_default_template(
    request: Request,
    template_id: UUID,
    return_view: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        duplicated = duplicate_default_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab="defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=f"/admin/templates/editor?scope=default&template_id={duplicated.id}&return_view={_admin_return_view_value(return_view)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/default-quick-actions", response_class=HTMLResponse)
def admin_upsert_default_quick_action(
    request: Request,
    quick_action_id: str = Form(""),
    return_view: str = Form(""),
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
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        upsert_default_quick_action_service(
            db,
            context.user,
            DefaultQuickActionUpsert(
                quick_action_id=UUID(quick_action_id) if quick_action_id else None,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid default quick action"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_quick_action_id=quick_action_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab="defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab="defaults"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/default-quick-actions/{quick_action_id}/delete", response_class=HTMLResponse)
def admin_delete_default_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_default_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab="defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab="defaults"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/default-quick-actions/{quick_action_id}/duplicate", response_class=HTMLResponse)
def admin_duplicate_default_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        duplicate_default_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab="defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab="defaults"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users", response_class=HTMLResponse)
def admin_create_user(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(...),
    temporary_password: str = Form(...),
    team_id: str = Form(""),
    team_role: str = Form(""),
    is_system_admin: str | None = Form(default=None),
    status_value: UserStatus = Form(..., alias="status"),
    mfa_required: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        create_user_service(
            db,
            UserCreate(
                full_name=full_name or None,
                email=email,
                temporary_password=temporary_password,
                team_id=UUID(team_id) if team_id else None,
                team_role=TeamRole(team_role) if team_role else None,
                is_system_admin=is_system_admin == "true",
                status=status_value,
                mfa_required=mfa_required == "true",
            ),
            actor=context.user,
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid user form submission"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or team_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "directory",
            team_id=return_team_id or team_id or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/stt-configs", response_class=HTMLResponse)
def admin_upsert_stt_config(
    request: Request,
    team_id: str = Form(...),
    config_id: str = Form(""),
    label: str = Form(...),
    adapter_kind: str = Form(SttAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    transcribe_path: str = Form(""),
    bearer_token: str = Form(""),
    preserved_bearer_token: str = Form(""),
    provider_model: str = Form(""),
    file_field_name: str = Form(""),
    language: str = Form(""),
    response_text_path: str = Form(""),
    extra_form_fields_json: str = Form(""),
    is_active: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    resolved_bearer_token = bearer_token or preserved_bearer_token or None
    try:
        upsert_stt_config_service(
            db,
            context.user,
            SttConfigUpsert(
                config_id=UUID(config_id) if config_id else None,
                team_id=UUID(team_id),
                label=label,
                adapter_kind=SttAdapterKind(adapter_kind),
                base_url=base_url,
                transcribe_path=transcribe_path,
                bearer_token=resolved_bearer_token,
                model_name=provider_model or None,
                file_field_name=file_field_name or "file",
                language=language or None,
                response_text_path=response_text_path or "text",
                extra_form_fields_json=parse_extra_form_fields_json(extra_form_fields_json),
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT configuration"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="stt",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/stt-configs/{config_id}/delete", response_class=HTMLResponse)
def admin_delete_stt_config(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_stt_config_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT delete request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/stt-configs/{config_id}/test", response_class=HTMLResponse)
def admin_test_stt_config(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        stt_test_result = main_module.run_saved_stt_config_test_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT test request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            selected_stt_config_id=str(config_id),
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_stt_config_id=str(config_id),
        stt_test_result=stt_test_result,
        message="STT test completed.",
        message_kind="success" if stt_test_result.get("success") else "error",
        active_admin_tab=return_tab or "providers",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/stt-configs/inspect", response_class=HTMLResponse)
def admin_inspect_stt_config(
    request: Request,
    team_id: str = Form(...),
    label: str = Form(""),
    adapter_kind: str = Form(SttAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    openapi_path: str = Form(""),
    bearer_token: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        inspection = inspect_stt_contract_service(
            db,
            context.user,
            SttInspectRequest(
                team_id=UUID(team_id),
                adapter_kind=SttAdapterKind(adapter_kind),
                base_url=base_url,
                openapi_path=openapi_path or None,
                bearer_token=bearer_token or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT inspection request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_stt_config_id=None,
        stt_inspection=inspection,
        stt_form_override={
            **stt_form_defaults(None, inspection),
            "label": label,
            "adapter_kind": inspection.adapter_kind.value,
            "preserved_bearer_token": bearer_token,
        },
        message="STT endpoint inspected. Review the inferred fields before saving.",
        active_admin_tab=return_tab or "providers",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/stt-selection", response_class=HTMLResponse)
def admin_set_stt_selection(
    request: Request,
    team_id: str = Form(...),
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
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        set_team_stt_selection_service(
            db,
            context.user,
            SttSelectionUpsert(
                team_id=UUID(team_id),
                purpose=SttSelectionPurpose(purpose),
                stt_config_id=UUID(stt_config_id),
                model_name_override=provider_model or None,
                language_override=language or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/stt-selection/clear", response_class=HTMLResponse)
def admin_clear_stt_selection(
    request: Request,
    team_id: str = Form(...),
    purpose: str = Form("conversation"),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        clear_team_stt_selection_service(db, context.user, team_id=UUID(team_id), purpose=SttSelectionPurpose(purpose))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT selection clear request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-configs/inspect", response_class=HTMLResponse)
def admin_inspect_llm_config(
    request: Request,
    team_id: str = Form(...),
    label: str = Form(""),
    adapter_kind: str = Form(LlmAdapterKind.openai_chat.value),
    base_url: str = Form(""),
    bedrock_region: str = Form(""),
    bearer_token: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        inspection = inspect_llm_contract_service(
            db,
            context.user,
            LlmInspectRequest(
                team_id=UUID(team_id),
                adapter_kind=LlmAdapterKind(adapter_kind),
                base_url=base_url,
                bedrock_region=bedrock_region or None,
                bearer_token=bearer_token or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM inspection request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_llm_config_id=None,
        llm_inspection=inspection,
        llm_form_override={
            **llm_form_defaults(None, inspection),
            "label": label,
            "adapter_kind": inspection.adapter_kind.value,
            "bedrock_region": (
                bedrock_region or bedrock_region_from_base_url(inspection.base_url) or DEFAULT_BEDROCK_CHAT_REGION
                if inspection.adapter_kind is LlmAdapterKind.bedrock_chat
                else ""
            ),
            "preserved_bearer_token": bearer_token,
        },
        message="LLM provider inspected. Review the inferred fields before saving.",
        active_admin_tab=return_tab or "providers",
        active_provider_tab="llm",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/llm-configs", response_class=HTMLResponse)
def admin_upsert_llm_config(
    request: Request,
    team_id: str = Form(...),
    config_id: str = Form(""),
    label: str = Form(...),
    adapter_kind: str = Form(LlmAdapterKind.openai_chat.value),
    base_url: str = Form(""),
    bedrock_region: str = Form(""),
    bearer_token: str = Form(""),
    preserved_bearer_token: str = Form(""),
    provider_model: str = Form(""),
    is_active: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    resolved_bearer_token = bearer_token or preserved_bearer_token or None
    try:
        upsert_llm_config_service(
            db,
            context.user,
            LlmConfigUpsert(
                config_id=UUID(config_id) if config_id else None,
                team_id=UUID(team_id),
                label=label,
                adapter_kind=LlmAdapterKind(adapter_kind),
                base_url=base_url,
                bedrock_region=bedrock_region or None,
                bearer_token=resolved_bearer_token,
                model_name=provider_model or None,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM configuration"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="llm",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-configs/{config_id}/delete", response_class=HTMLResponse)
def admin_delete_llm_config(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_llm_config_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM delete request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-selection", response_class=HTMLResponse)
def admin_set_llm_selection(
    request: Request,
    team_id: str = Form(...),
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
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        set_team_llm_selection_service(
            db,
            context.user,
            LlmSelectionUpsert(
                team_id=UUID(team_id),
                llm_config_id=UUID(llm_config_id),
                allowed_models_json=allowed_model_names,
                model_name_override=provider_model or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-selection/clear", response_class=HTMLResponse)
def admin_clear_llm_selection(
    request: Request,
    team_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        clear_team_llm_selection_service(db, context.user, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM selection clear request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-providers/inspect", response_class=HTMLResponse)
def admin_inspect_deidentification_provider(
    request: Request,
    team_id: str = Form(""),
    provider_id: str = Form(""),
    label: str = Form(...),
    adapter_kind: str = Form(DeidentificationAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    detect_path: str = Form(""),
    openapi_path: str = Form(""),
    auth_mode: str = Form(DeidentificationAuthMode.none.value),
    bearer_token: str = Form(""),
    request_text_field: str = Form("text"),
    request_language_field: str = Form(""),
    extra_headers_json: str = Form(""),
    extra_body_json: str = Form(""),
    response_entities_path: str = Form("entities"),
    response_start_field: str = Form("start"),
    response_end_field: str = Form("end"),
    response_type_field: str = Form("entity_type"),
    response_score_field: str = Form(""),
    response_model_version_path: str = Form(""),
    entity_type_map_json: str = Form(""),
    clinical_detection_enabled: str | None = Form(default=None),
    clinical_detection_allow_unredacted: str | None = Form(default=None),
    sample_text: str = Form("Jane Smith attended on 22 April 2026."),
    is_active: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    resolved_bearer_token = bearer_token or None
    form_override = {
        "provider_id": provider_id,
        "label": label,
        "adapter_kind": adapter_kind,
        "base_url": base_url,
        "detect_path": detect_path,
        "openapi_path": openapi_path,
        "auth_mode": auth_mode,
        "request_text_field": request_text_field,
        "request_language_field": request_language_field,
        "extra_headers_json": extra_headers_json,
        "extra_body_json": extra_body_json,
        "response_entities_path": response_entities_path,
        "response_start_field": response_start_field,
        "response_end_field": response_end_field,
        "response_type_field": response_type_field,
        "response_score_field": response_score_field,
        "response_model_version_path": response_model_version_path,
        "entity_type_map_json": entity_type_map_json,
        "clinical_detection_enabled": clinical_detection_enabled == "true",
        "clinical_detection_allow_unredacted": clinical_detection_allow_unredacted == "true",
        "sample_text": sample_text,
        "is_active": is_active == "true",
        "preserved_bearer_token": "",
    }
    try:
        inspection = inspect_deidentification_provider_service(
            db,
            context.user,
            DeidentificationProviderInspectRequest(
                provider_id=UUID(provider_id) if provider_id else None,
                label=label,
                adapter_kind=DeidentificationAdapterKind(adapter_kind),
                base_url=base_url,
                detect_path=detect_path,
                openapi_path=openapi_path or None,
                auth_mode=DeidentificationAuthMode(auth_mode),
                bearer_token=resolved_bearer_token,
                request_text_field=request_text_field,
                request_language_field=request_language_field or None,
                extra_headers_json=parse_string_map_json(extra_headers_json, field_name="extra_headers_json", label="Extra headers"),
                extra_body_json=parse_json_object(extra_body_json, field_name="extra_body_json", label="Extra body fields"),
                response_entities_path=response_entities_path,
                response_start_field=response_start_field,
                response_end_field=response_end_field,
                response_type_field=response_type_field,
                response_score_field=response_score_field or None,
                response_model_version_path=response_model_version_path or None,
                entity_type_map_json=parse_string_map_json(entity_type_map_json, field_name="entity_type_map_json", label="Entity type map"),
                clinical_detection_enabled=clinical_detection_enabled == "true",
                clinical_detection_allow_unredacted=clinical_detection_allow_unredacted == "true",
                sample_text=sample_text,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider inspection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id or None,
            selected_deidentification_provider_id=provider_id or None,
            deidentification_form_override=form_override,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    form_override.update(
        {
            "detect_path": inspection.detect_path,
            "openapi_path": inspection.openapi_path or openapi_path,
            "request_text_field": inspection.request_text_field,
            "request_language_field": inspection.request_language_field or "",
            "extra_body_json": json.dumps(inspection.extra_body_json) if inspection.extra_body_json else extra_body_json,
            "response_entities_path": inspection.response_entities_path,
            "response_start_field": inspection.response_start_field,
            "response_end_field": inspection.response_end_field,
            "response_type_field": inspection.response_type_field,
            "response_score_field": inspection.response_score_field or "",
            "response_model_version_path": inspection.response_model_version_path or "",
            "candidate_paths": inspection.candidate_paths,
        }
    )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id or None,
        selected_deidentification_provider_id=provider_id or None,
        deidentification_inspection=inspection,
        deidentification_form_override=form_override,
        message="Shared NLP endpoint ping succeeded.",
        message_kind="success",
        active_admin_tab=return_tab or "providers",
        active_provider_tab="deidentification",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/deidentification-providers", response_class=HTMLResponse)
def admin_upsert_deidentification_provider(
    request: Request,
    team_id: str = Form(""),
    provider_id: str = Form(""),
    label: str = Form(...),
    adapter_kind: str = Form(DeidentificationAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    detect_path: str = Form(""),
    auth_mode: str = Form(DeidentificationAuthMode.none.value),
    bearer_token: str = Form(""),
    request_text_field: str = Form("text"),
    request_language_field: str = Form(""),
    extra_headers_json: str = Form(""),
    extra_body_json: str = Form(""),
    response_entities_path: str = Form("entities"),
    response_start_field: str = Form("start"),
    response_end_field: str = Form("end"),
    response_type_field: str = Form("entity_type"),
    response_score_field: str = Form(""),
    response_model_version_path: str = Form(""),
    entity_type_map_json: str = Form(""),
    clinical_detection_enabled: str | None = Form(default=None),
    clinical_detection_allow_unredacted: str | None = Form(default=None),
    is_active: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    resolved_bearer_token = bearer_token or None
    try:
        provider = upsert_deidentification_provider_service(
            db,
            context.user,
            DeidentificationProviderUpsert(
                provider_id=UUID(provider_id) if provider_id else None,
                label=label,
                adapter_kind=DeidentificationAdapterKind(adapter_kind),
                base_url=base_url,
                detect_path=detect_path,
                auth_mode=DeidentificationAuthMode(auth_mode),
                bearer_token=resolved_bearer_token,
                request_text_field=request_text_field,
                request_language_field=request_language_field or None,
                extra_headers_json=parse_string_map_json(
                    extra_headers_json,
                    field_name="extra_headers_json",
                    label="Extra headers",
                ),
                extra_body_json=parse_json_object(
                    extra_body_json,
                    field_name="extra_body_json",
                    label="Extra body fields",
                ),
                response_entities_path=response_entities_path,
                response_start_field=response_start_field,
                response_end_field=response_end_field,
                response_type_field=response_type_field,
                response_score_field=response_score_field or None,
                response_model_version_path=response_model_version_path or None,
                entity_type_map_json=parse_string_map_json(
                    entity_type_map_json,
                    field_name="entity_type_map_json",
                    label="Entity type map",
                ),
                clinical_detection_enabled=clinical_detection_enabled == "true",
                clinical_detection_allow_unredacted=clinical_detection_allow_unredacted == "true",
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id or None,
            selected_deidentification_provider_id=provider_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "providers",
            team_id=team_id or None,
            deidentification_provider_id=str(provider.id),
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-providers/{provider_id}/delete", response_class=HTMLResponse)
def admin_delete_deidentification_provider(
    request: Request,
    provider_id: UUID,
    team_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_deidentification_provider_service(db, context.user, provider_id=provider_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id or None,
            selected_deidentification_provider_id=str(provider_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-provider-assignments", response_class=HTMLResponse)
def admin_assign_deidentification_provider(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        assign_deidentification_provider_to_team_service(
            db,
            context.user,
            DeidentificationProviderAssignmentUpsert(
                team_id=UUID(team_id),
                provider_id=UUID(provider_id),
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider assignment"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-selection", response_class=HTMLResponse)
def admin_set_deidentification_selection(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        set_team_deidentification_selection_service(
            db,
            context.user,
            DeidentificationSelectionUpsert(team_id=UUID(team_id), provider_id=UUID(provider_id)),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-selection/clear", response_class=HTMLResponse)
def admin_clear_deidentification_selection(
    request: Request,
    team_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        clear_team_deidentification_selection_service(db, context.user, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification selection clear request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/clinical-nlp-selection", response_class=HTMLResponse)
def admin_set_clinical_nlp_selection(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        set_team_clinical_nlp_selection_service(
            db,
            context.user,
            ClinicalNlpSelectionUpsert(team_id=UUID(team_id), provider_id=UUID(provider_id)),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid clinical NLP selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="deidentification",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/clinical-nlp-selection/clear", response_class=HTMLResponse)
def admin_clear_clinical_nlp_selection(
    request: Request,
    team_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        clear_team_clinical_nlp_selection_service(db, context.user, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid clinical NLP selection clear request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="deidentification",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-provider-assignments/remove", response_class=HTMLResponse)
def admin_remove_deidentification_provider_assignment(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        remove_deidentification_provider_assignment_service(
            db,
            context.user,
            team_id=UUID(team_id),
            provider_id=UUID(provider_id),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider assignment removal"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/users/{user_id}/suspend", response_class=HTMLResponse)
def admin_suspend_user(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        suspend_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or None,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/users/{user_id}/reactivate", response_class=HTMLResponse)
def admin_reactivate_user(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        reactivate_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or None,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/users/{user_id}/send-activation", response_class=HTMLResponse)
def admin_send_activation(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        send_account_activation_email_service(db, user, created_by=context.user)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


def _admin_break_glass_allowed() -> bool:
    if os.getenv("BREAK_GLASS_RECOVERY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return False
    return not email_password_reset_enabled_service() or os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() in {"1", "true", "yes"}


@app.post("/admin/users/{user_id}/recover-password", response_class=HTMLResponse)
def admin_recover_password_deprecated(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.", message_kind="error", status_code=status.HTTP_410_GONE, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/send-password-reset", response_class=HTMLResponse)
def admin_send_password_reset(request: Request, user_id: UUID, reason: str = Form(""), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        send_manager_password_reset_email_service(db, actor=context.user, target=user)
        record_security_event(db, action="manager_password_reset_email_sent", actor=context.user, target=user, request=request, details={"reason": reason or None})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/break-glass-password-reset", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def admin_break_glass_password_reset(request: Request, user_id: UUID, mfa_code: str = Form(...), reason: str = Form(...), confirm_email_unavailable: str | None = Form(default=None), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if not _admin_break_glass_allowed():
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(db, user, actor=context.user, reset_mfa=False, break_glass=True)
        record_security_event(db, action="break_glass_password_reset_generated", actor=context.user, target=user, request=request, details={"reason": reason, "expires_at": expires_at.isoformat()})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="Break-glass temporary password generated. It is shown once.", message_kind="success", recovery_temporary_password=temporary_password, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/reset-mfa", response_class=HTMLResponse)
def admin_reset_mfa(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        reset_user_mfa_for_reenrollment_service(db, user=user)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/recover-account", response_class=HTMLResponse)
def admin_recover_account_deprecated(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.", message_kind="error", status_code=status.HTTP_410_GONE, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/send-account-recovery", response_class=HTMLResponse)
def admin_send_account_recovery(request: Request, user_id: UUID, reason: str = Form(""), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        send_manager_account_recovery_email_service(db, actor=context.user, target=user)
        record_security_event(db, action="manager_account_recovery_email_sent", actor=context.user, target=user, request=request, details={"reason": reason or None})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/break-glass-account-recovery", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def admin_break_glass_account_recovery(request: Request, user_id: UUID, mfa_code: str = Form(...), reason: str = Form(...), confirm_email_unavailable: str | None = Form(default=None), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if not _admin_break_glass_allowed():
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(db, user, actor=context.user, reset_mfa=True, break_glass=True)
        record_security_event(db, action="break_glass_account_recovery_generated", actor=context.user, target=user, request=request, details={"reason": reason, "expires_at": expires_at.isoformat()})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="Break-glass temporary password generated and MFA reset. It is shown once.", message_kind="success", recovery_temporary_password=temporary_password, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
def admin_delete_user(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or None,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/account-requests/{request_id}/approve", response_class=HTMLResponse)
def admin_approve_account_request(
    request: Request,
    request_id: UUID,
    team_id: str = Form(...),
    temporary_password: str = Form(...),
    team_role: str = Form(...),
    review_notes: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        approve_account_request_service(
            db,
            context.user,
            request_id,
            AccountRequestApprove(
                team_id=UUID(team_id),
                temporary_password=temporary_password,
                team_role=TeamRole(team_role),
                review_notes=review_notes or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid account-request approval"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or team_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "requests",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "requests",
            team_id=return_team_id or team_id or None,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/account-requests/{request_id}/reject", response_class=HTMLResponse)
def admin_reject_account_request(
    request: Request,
    request_id: UUID,
    review_notes: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    return_team_id: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        reject_account_request_service(db, context.user, request_id, AccountRequestReject(review_notes=review_notes))
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=return_team_id or None,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "requests",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "requests", team_id=return_team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )
