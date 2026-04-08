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
        create_team_service(db, TeamCreate(name=name, status=status_value, default_retention_days=default_retention_days))
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
        clear_team_stt_selection_service(db, context.user, team_id=UUID(team_id))
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
