"""Leader team-management browser routes extracted from the home route module."""

from ..main import *  # noqa: F401,F403
from ..main import (
    _home_page_route_from_return_view,
    _home_redirect_url,
    _home_return_view_value,
    _home_template_name_from_return_view,
    _page_context_or_redirect,
)


def _web_break_glass_allowed() -> bool:
    if os.getenv("BREAK_GLASS_RECOVERY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return False
    return not email_password_reset_enabled_service() or os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() in {"1", "true", "yes"}


def _render_home_recovery_error(request: Request, db: Session, context, exc: AppError, *, return_view: str, return_tab: str):
    return render_home(
        request,
        db,
        current_user=context.user,
        message=exc.message,
        message_kind="error",
        active_home_tab=return_tab or "team-management",
        status_code=exc.status_code,
        template_name=_home_template_name_from_return_view(return_view),
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
    )


@app.post("/home/users", response_class=HTMLResponse)
def home_create_user(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(...),
    temporary_password: str = Form(...),
    team_role: str = Form(...),
    status_value: UserStatus = Form(..., alias="status"),
    mfa_required: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        create_user_service(
            db,
            UserCreate(
                full_name=full_name or None,
                email=email,
                temporary_password=temporary_password,
                team_id=context.user.team_id,
                team_role=TeamRole(team_role),
                is_system_admin=False,
                status=status_value,
                mfa_required=mfa_required == "true",
            ),
            actor=context.user,
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid user form submission"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_home(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_home_tab=return_tab or "team-management",
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/users/{user_id}/suspend", response_class=HTMLResponse)
def home_suspend_user(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        suspend_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "team-management",
            status_code=exc.status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/users/{user_id}/reactivate", response_class=HTMLResponse)
def home_reactivate_user(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        reactivate_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "team-management",
            status_code=exc.status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/users/{user_id}/send-activation", response_class=HTMLResponse)
def home_send_activation(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        send_account_activation_email_service(db, user, created_by=context.user)
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "team-management",
            status_code=exc.status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/users/{user_id}/recover-password", response_class=HTMLResponse)
def home_recover_password_deprecated(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    return render_home(
        request,
        db,
        current_user=context.user,
        message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.",
        message_kind="error",
        status_code=status.HTTP_410_GONE,
        active_home_tab=return_tab or "team-management",
        template_name=_home_template_name_from_return_view(return_view),
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
    )


@app.post("/home/users/{user_id}/send-password-reset", response_class=HTMLResponse)
def home_send_password_reset(
    request: Request,
    user_id: UUID,
    reason: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        send_manager_password_reset_email_service(db, actor=context.user, target=user)
        record_security_event(db, action="manager_password_reset_email_sent", actor=context.user, target=user, request=request, details={"reason": reason or None})
    except AppError as exc:
        return _render_home_recovery_error(request, db, context, exc, return_view=return_view, return_tab=return_tab)
    return RedirectResponse(url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/users/{user_id}/break-glass-password-reset", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def home_break_glass_password_reset(
    request: Request,
    user_id: UUID,
    mfa_code: str = Form(...),
    reason: str = Form(...),
    confirm_email_unavailable: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if not _web_break_glass_allowed():
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(db, user, actor=context.user, reset_mfa=False, break_glass=True)
        record_security_event(db, action="break_glass_password_reset_generated", actor=context.user, target=user, request=request, details={"reason": reason, "expires_at": expires_at.isoformat()})
    except AppError as exc:
        return _render_home_recovery_error(request, db, context, exc, return_view=return_view, return_tab=return_tab)
    return render_home(
        request,
        db,
        current_user=context.user,
        message="Break-glass temporary password generated. It is shown once.",
        message_kind="success",
        recovery_temporary_password=temporary_password,
        active_home_tab=return_tab or "team-management",
        template_name=_home_template_name_from_return_view(return_view),
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
    )


@app.post("/home/users/{user_id}/reset-mfa", response_class=HTMLResponse)
def home_reset_mfa(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        reset_user_mfa_for_reenrollment_service(db, user=user)
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "team-management",
            status_code=exc.status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/users/{user_id}/recover-account", response_class=HTMLResponse)
def home_recover_account_deprecated(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    return render_home(
        request,
        db,
        current_user=context.user,
        message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.",
        message_kind="error",
        status_code=status.HTTP_410_GONE,
        active_home_tab=return_tab or "team-management",
        template_name=_home_template_name_from_return_view(return_view),
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
    )


@app.post("/home/users/{user_id}/send-account-recovery", response_class=HTMLResponse)
def home_send_account_recovery(
    request: Request,
    user_id: UUID,
    reason: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        send_manager_account_recovery_email_service(db, actor=context.user, target=user)
        record_security_event(db, action="manager_account_recovery_email_sent", actor=context.user, target=user, request=request, details={"reason": reason or None})
    except AppError as exc:
        return _render_home_recovery_error(request, db, context, exc, return_view=return_view, return_tab=return_tab)
    return RedirectResponse(url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/users/{user_id}/break-glass-account-recovery", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def home_break_glass_account_recovery(
    request: Request,
    user_id: UUID,
    mfa_code: str = Form(...),
    reason: str = Form(...),
    confirm_email_unavailable: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if not _web_break_glass_allowed():
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(db, user, actor=context.user, reset_mfa=True, break_glass=True)
        record_security_event(db, action="break_glass_account_recovery_generated", actor=context.user, target=user, request=request, details={"reason": reason, "expires_at": expires_at.isoformat()})
    except AppError as exc:
        return _render_home_recovery_error(request, db, context, exc, return_view=return_view, return_tab=return_tab)
    return render_home(
        request,
        db,
        current_user=context.user,
        message="Break-glass temporary password generated and MFA reset. It is shown once.",
        message_kind="success",
        recovery_temporary_password=temporary_password,
        active_home_tab=return_tab or "team-management",
        template_name=_home_template_name_from_return_view(return_view),
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
    )


@app.post("/home/users/{user_id}/delete", response_class=HTMLResponse)
def home_delete_user(
    request: Request,
    user_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        delete_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "team-management",
            status_code=exc.status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/account-requests/{request_id}/approve", response_class=HTMLResponse)
def home_approve_account_request(
    request: Request,
    request_id: UUID,
    temporary_password: str = Form(...),
    team_role: str = Form(...),
    review_notes: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        approve_account_request_service(
            db,
            context.user,
            request_id,
            AccountRequestApprove(
                temporary_password=temporary_password,
                team_role=TeamRole(team_role),
                review_notes=review_notes or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid account-request approval"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_home(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            active_home_tab=return_tab or "account-requests",
            status_code=status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "account-requests"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/account-requests/{request_id}/reject", response_class=HTMLResponse)
def home_reject_account_request(
    request: Request,
    request_id: UUID,
    review_notes: str = Form(...),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        reject_account_request_service(db, context.user, request_id, AccountRequestReject(review_notes=review_notes))
    except AppError as exc:
        return render_home(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            active_home_tab=return_tab or "account-requests",
            status_code=exc.status_code,
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "account-requests"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
