"""Leader team-management browser routes extracted from the home route module."""

from ..main import *  # noqa: F401,F403
from ..main import (
    _home_page_route_from_return_view,
    _home_redirect_url,
    _home_return_view_value,
    _home_template_name_from_return_view,
    _page_context_or_redirect,
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
