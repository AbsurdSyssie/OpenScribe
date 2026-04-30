"""Browser page and form routes extracted from app.main."""

from ..main import *  # noqa: F401,F403
from ..main import (
    _bootstrap_allowed,
    _clear_session_cookie,
    _current_context_optional,
    _enforce_localhost_only_dev_account,
    _page_context_or_redirect,
    _post_login_redirect,
    _post_login_redirect_for_user,
    _set_session_cookie,
    _set_trusted_device_cookie,
)
from ..models import UserOnboardingState


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    context = _current_context_optional(request, db)
    if context is not None:
        return RedirectResponse(url=_post_login_redirect(context), status_code=status.HTTP_303_SEE_OTHER)
    return render_auth_page(request, db)


@app.post("/login", response_class=HTMLResponse)
@LOGIN_RATE_LIMIT
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    try:
        user = authenticate_user(db, email, password)
        _enforce_localhost_only_dev_account(request, user)
    except AppError as exc:
        return render_auth_page(request, db, message=exc.message, message_kind="error", status_code=exc.status_code)
    trusted_device = resolve_trusted_device(db, user, request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME))
    auth_level = login_auth_level(user, trusted_device)
    if trusted_device and auth_level is SessionAuthLevel.full:
        touch_trusted_device_seen(db, trusted_device)
    token = create_session(db, user, auth_level=auth_level)
    redirect_to = "/onboarding" if auth_level is SessionAuthLevel.onboarding else ("/mfa/challenge" if auth_level is SessionAuthLevel.pending_mfa else _post_login_redirect_for_user(user))
    response = RedirectResponse(url=redirect_to, status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(request, response, token)
    return response


@app.post("/logout", response_class=HTMLResponse)
def logout_submit(request: Request, csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session_by_token(db, token, reason="logout")
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _clear_session_cookie(response)
    return response


@app.get("/request-access", response_class=HTMLResponse)
def request_access_page(request: Request):
    return render_request_access_page(request)


@app.post("/request-access", response_class=HTMLResponse)
@ACCOUNT_REQUEST_RATE_LIMIT
def request_access_submit(
    request: Request,
    requested_name: str = Form(...),
    requested_email: str = Form(...),
    requested_team_name: str = Form(...),
    request_details: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    try:
        create_account_request_service(
            db,
            AccountRequestCreate(
                requested_name=requested_name,
                requested_email=requested_email,
                requested_team_name=requested_team_name,
                request_details=request_details or None,
            ),
        )
    except AppError as exc:
        return render_request_access_page(request, message=exc.message, message_kind="error", status_code=exc.status_code)
    return render_request_access_page(request, message="Account request submitted", message_kind="success")


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    reset_enabled = email_password_reset_enabled_service()
    message = None if reset_enabled else PASSWORD_RESET_EMAIL_DISABLED_MESSAGE
    return templates.TemplateResponse(
        request,
        "password_reset_request.html",
        {"request": request, "message": message, "password_reset_email_enabled": reset_enabled},
    )


@app.post("/forgot-password", response_class=HTMLResponse)
@LOGIN_RATE_LIMIT
def forgot_password_submit(request: Request, email: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    try:
        message = request_password_reset_service(db, email=email)
        status_code = status.HTTP_200_OK
    except AppError as exc:
        message = exc.message if exc.code == "mail_transport_disabled" else GENERIC_PASSWORD_RESET_MESSAGE
        status_code = exc.status_code if exc.code == "mail_transport_disabled" else status.HTTP_200_OK
    reset_enabled = email_password_reset_enabled_service()
    return templates.TemplateResponse(
        request,
        "password_reset_request.html",
        {"request": request, "message": message, "password_reset_email_enabled": reset_enabled},
        status_code=status_code,
    )


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    token_valid = bool(token and get_active_token_user_service(db, raw_token=token, purpose=AuthEmailTokenPurpose.password_reset))
    if not token_valid:
        token_valid = bool(token and get_active_token_user_service(db, raw_token=token, purpose=AuthEmailTokenPurpose.manager_password_reset))
    if not token_valid:
        token_valid = bool(token and get_active_token_user_service(db, raw_token=token, purpose=AuthEmailTokenPurpose.manager_account_recovery))
    return templates.TemplateResponse(
        request,
        "password_reset_confirm.html",
        {
            "request": request,
            "title": "Reset password",
            "message": None if token_valid else "Reset link is invalid or expired.",
            "token_valid": token_valid,
            "token": token,
            "action": "/reset-password",
            "button_label": "Reset password",
        },
        status_code=200 if token_valid else status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(request: Request, token: str = Form(...), new_password: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    try:
        confirm_password_reset_service(db, raw_token=token, new_password=new_password)
    except AppError as exc:
        return templates.TemplateResponse(
            request,
            "password_reset_confirm.html",
            {
                "request": request,
                "title": "Reset password",
                "message": exc.message,
                "token_valid": True,
                "token": token,
                "action": "/reset-password",
                "button_label": "Reset password",
            },
            status_code=exc.status_code,
        )
    return render_auth_page(request, db, message="Password reset complete. Sign in with your new password.", message_kind="success")


@app.get("/activate-account", response_class=HTMLResponse)
def activate_account_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    user = get_active_token_user_service(db, raw_token=token, purpose=AuthEmailTokenPurpose.account_activation) if token else None
    token_valid = bool(user and user.onboarding_state is UserOnboardingState.pending_password_change and user.must_change_password)
    return templates.TemplateResponse(
        request,
        "password_reset_confirm.html",
        {
            "request": request,
            "title": "Set up account",
            "message": None if token_valid else "Setup link is invalid or expired.",
            "token_valid": token_valid,
            "token": token,
            "action": "/activate-account",
            "button_label": "Set password",
        },
        status_code=200 if token_valid else status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.post("/activate-account", response_class=HTMLResponse)
def activate_account_submit(request: Request, token: str = Form(...), new_password: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    try:
        user, session_token = confirm_account_activation_service(db, raw_token=token, new_password=new_password)
    except AppError as exc:
        return templates.TemplateResponse(
            request,
            "password_reset_confirm.html",
            {
                "request": request,
                "title": "Set up account",
                "message": exc.message,
                "token_valid": True,
                "token": token,
                "action": "/activate-account",
                "button_label": "Set password",
            },
            status_code=exc.status_code,
        )
    response = RedirectResponse(url="/onboarding", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(request, response, session_token)
    return response


@app.post("/bootstrap/system-admin", response_class=HTMLResponse)
def bootstrap_system_admin(request: Request, email: str = Form(...), password: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    if not _bootstrap_allowed(db):
        return render_auth_page(
            request,
            db,
            message="Bootstrap is disabled once a user exists",
            message_kind="error",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    try:
        user = create_bootstrap_admin(db, email=email, password=password)
    except AppError as exc:
        return render_auth_page(request, db, message=exc.message, message_kind="error", status_code=exc.status_code)
    token = create_session(db, user)
    response = RedirectResponse(url="/onboarding", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(request, response, token)
    return response


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    if context.session.auth_level.value == "full":
        return RedirectResponse(url=_post_login_redirect(context), status_code=status.HTTP_303_SEE_OTHER)
    method = current_pending_totp_method(db, context.user)
    secret = method.secret if method and context.user.onboarding_state.value == "pending_totp_enrollment" else None
    uri = provisioning_uri(context.user, method) if method and secret else None
    qr_uri = provisioning_qr_svg_data_uri(uri) if uri else None
    return render_onboarding(request, current_user=context.user, totp_secret=secret, totp_uri=uri, totp_qr_svg_data_uri=qr_uri)


@app.get("/mfa/challenge", response_class=HTMLResponse)
def mfa_challenge_page(request: Request, db: Session = Depends(get_db)):
    context = _current_context_optional(request, db)
    if context is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if context.session.auth_level is SessionAuthLevel.pending_mfa:
        return render_mfa_challenge(request, current_user=context.user)
    return RedirectResponse(url=_post_login_redirect(context), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/mfa/challenge", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def mfa_challenge_submit(
    request: Request,
    code: str = Form(...),
    remember_device: str | None = Form(default=None),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context = _current_context_optional(request, db)
    if context is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if context.session.auth_level is not SessionAuthLevel.pending_mfa:
        return RedirectResponse(url=_post_login_redirect(context), status_code=status.HTTP_303_SEE_OTHER)
    try:
        user, trusted_device_token = verify_login_totp(
            db,
            context.user,
            code=code,
            remember_device=remember_device == "true",
            device_label=request.headers.get("user-agent"),
        )
        token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    except AppError as exc:
        return render_mfa_challenge(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    response = RedirectResponse(url="/admin" if user.is_system_admin else "/home", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(request, response, token)
    if trusted_device_token:
        _set_trusted_device_cookie(request, response, trusted_device_token)
    return response


@app.post("/onboarding/password", response_class=HTMLResponse)
def onboarding_password_submit(request: Request, new_password: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    try:
        user = update_password_for_onboarding(db, context.user, new_password_hash=hash_password(new_password))
    except AppError as exc:
        return render_onboarding(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return render_onboarding(request, current_user=user, message="Password updated. Continue to TOTP enrollment.", message_kind="success")


@app.post("/onboarding/totp/start", response_class=HTMLResponse)
def onboarding_totp_start_submit(request: Request, csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    try:
        method = start_totp_enrollment(db, context.user)
    except AppError as exc:
        return render_onboarding(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    refreshed_user = db.get(User, context.user.id)
    return render_onboarding(
        request,
        current_user=refreshed_user,
        totp_secret=method.secret,
        totp_uri=provisioning_uri(refreshed_user, method),
        totp_qr_svg_data_uri=provisioning_qr_svg_data_uri(provisioning_uri(refreshed_user, method)),
        message="TOTP secret created. Enter the 6-digit code from your authenticator app.",
        message_kind="success",
    )


@app.post("/onboarding/totp/verify", response_class=HTMLResponse)
def onboarding_totp_verify_submit(request: Request, code: str = Form(...), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    method = current_pending_totp_method(db, context.user)
    try:
        user = verify_totp_enrollment(db, context.user, code=code)
    except AppError as exc:
        return render_onboarding(
            request,
            current_user=context.user,
            totp_secret=method.secret if method else None,
            totp_uri=provisioning_uri(context.user, method) if method else None,
            totp_qr_svg_data_uri=provisioning_qr_svg_data_uri(provisioning_uri(context.user, method)) if method else None,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return render_onboarding(request, current_user=user, message="TOTP verified. Generate recovery codes or skip this optional step.", message_kind="success")


@app.post("/onboarding/recovery-codes", response_class=HTMLResponse)
def onboarding_recovery_codes_submit(request: Request, csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    try:
        codes = generate_recovery_codes(db, context.user)
        refreshed_user = db.get(User, context.user.id)
        token = rotate_session(db, context.token, refreshed_user, auth_level=determine_auth_level(refreshed_user))
    except AppError as exc:
        return render_onboarding(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    response = templates.TemplateResponse(
        request,
        "onboarding.html",
        {
            "request": request,
            "current_user": refreshed_user,
            "recovery_codes": codes,
            "message": "Recovery codes generated. Save them now; they will not be shown again.",
            "message_kind": "success",
            "totp_secret": None,
            "totp_uri": None,
            "totp_qr_svg_data_uri": None,
        },
    )
    _set_session_cookie(request, response, token)
    return response


@app.post("/onboarding/skip-recovery-codes", response_class=HTMLResponse)
def onboarding_skip_recovery_codes_submit(request: Request, csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    try:
        user = skip_recovery_codes(db, context.user)
        token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    except AppError as exc:
        return render_onboarding(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    response = RedirectResponse(url="/admin" if user.is_system_admin else "/home", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(request, response, token)
    return response
