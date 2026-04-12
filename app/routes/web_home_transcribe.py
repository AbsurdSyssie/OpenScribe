"""Home and transcribe browser routes extracted from app.main."""

from ..main import *  # noqa: F401,F403
from ..main import (
    _home_page_route_from_return_view,
    _home_redirect_url,
    _home_return_view_value,
    _home_template_name_from_return_view,
    _page_context_or_redirect,
    _structured_template_config_from_form,
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
        home_page_route="/home",
        home_return_view=_home_return_view_value(return_view),
        transcribe_return_tab=transcribe_tab,
    )


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
        return render_home(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "team-management",
            active_home_modal="llm-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
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
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "team-management",
            active_home_modal="llm-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/llm-preference", response_class=HTMLResponse)
def home_set_llm_preference(
    request: Request,
    preferred_model_name: str = Form(""),
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
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM preference"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_home(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "overview",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "overview"),
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
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "overview",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "overview"),
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
        upsert_team_template_service(
            db,
            context.user,
            PromptTemplateUpsert(
                template_id=UUID(template_id) if template_id else None,
                scope=TemplateScope.team,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                mode=TemplateMode(mode),
                config_json=_structured_template_config_from_form(
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
        return render_home(
            request,
            db,
            current_user=context.user,
            selected_team_template_id=template_id or None,
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            active_home_modal=home_modal or "team-template",
            status_code=status_code,
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
        return render_home(
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
        upsert_personal_template_service(
            db,
            context.user,
            PromptTemplateUpsert(
                template_id=UUID(template_id) if template_id else None,
                scope=TemplateScope.user,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                mode=TemplateMode(mode),
                config_json=_structured_template_config_from_form(
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
        return render_home(
            request,
            db,
            current_user=context.user,
            selected_personal_template_id=template_id or None,
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "templates",
            active_home_modal=home_modal or "personal-template",
            status_code=status_code,
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
        return render_home(
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
        upsert_team_quick_action_service(
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
        return render_home(
            request,
            db,
            current_user=context.user,
            selected_team_quick_action_id=quick_action_id or None,
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            active_home_modal=home_modal or "team-quick-action",
            status_code=status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
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
        return render_home(
            request,
            db,
            current_user=context.user,
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
    return RedirectResponse(
        url=_home_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "quick-actions",
            queued_transcript_id=queued_transcript_id or None,
            transcribe_tab=transcribe_tab or None,
        ),
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
        upsert_personal_quick_action_service(
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
        return render_home(
            request,
            db,
            current_user=context.user,
            selected_personal_quick_action_id=quick_action_id or None,
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "quick-actions",
            active_home_modal=home_modal or "personal-quick-action",
            status_code=status_code,
            queued_transcript_id=queued_transcript_id or None,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
            transcribe_return_tab=transcribe_tab or None,
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
        return render_home(
            request,
            db,
            current_user=context.user,
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
                stt_config_id=UUID(stt_config_id),
                model_name_override=provider_model or None,
                language_override=language or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_home(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "team-management",
            active_home_modal="stt-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/stt-selection/clear", response_class=HTMLResponse)
def home_clear_stt_selection(
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
        clear_team_stt_selection_service(db, context.user)
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_home_tab=return_tab or "team-management",
            active_home_modal="stt-settings",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
