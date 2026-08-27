"""Browser OpenID Connect login and account-linking routes."""

import logging
from urllib.parse import parse_qsl, urlencode

from ..main import *  # noqa: F401,F403
from ..main import (
    _clear_trusted_device_cookie,
    _current_context_optional,
    _enforce_localhost_only_dev_account,
    _page_context_or_redirect,
    _post_login_redirect,
    _post_login_redirect_for_user,
    _set_session_cookie,
    ACCOUNT_SECURITY_RATE_LIMIT,
    LOGIN_RATE_LIMIT,
)
from ..cookie_security import should_set_secure_cookie
from ..security_headers import oidc_form_action_origin
from ..services.account import reauthenticate_for_account_change, reauthenticate_for_oidc_link
from ..services.auth import (
    create_session,
    login_auth_level,
    resolve_trusted_device,
    revoke_sessions_for_user,
    revoke_trusted_devices_for_user,
    touch_trusted_device_seen,
)
from ..services.oidc import (
    OIDC_AUTHORIZATION_REQUEST_LIFETIME,
    OIDC_CODE_VERIFIER_COOKIE_NAME,
    OIDC_STATE_COOKIE_NAME,
    OidcProtocolError,
    authenticate_oidc_identity,
    begin_oidc_authorization,
    consume_oidc_authorization,
    exchange_oidc_code_for_identity,
    link_oidc_identity,
    oidc_config,
    resolve_oidc_link_session,
    unlink_oidc_identity,
)
from ..services.security_audit import record_security_event


OIDC_CALLBACK_BODY_MAX_BYTES = 8192
oidc_logger = logging.getLogger("openscribe.oidc")


def _log_oidc_event(
    event: str,
    *,
    provider_key: str,
    purpose: str,
    outcome: str,
    reason_code: str = "-",
    protocol_stage: str = "-",
    status_code: int = 0,
    level: int = logging.INFO,
) -> None:
    """Log bounded OIDC metadata without identities, claims, or secrets."""
    oidc_logger.log(
        level,
        "%s provider_key=%s purpose=%s outcome=%s reason_code=%s protocol_stage=%s status_code=%s",
        event,
        provider_key,
        purpose,
        outcome,
        reason_code,
        protocol_stage,
        status_code or "-",
    )


def _oidc_callback_path(provider_key: str) -> str:
    return f"/auth/oidc/{provider_key}/callback"


def _oidc_error_redirect(message: str) -> RedirectResponse:
    query = urlencode({"message": message, "message_kind": "error"})
    return RedirectResponse(
        url=f"/workspace/account?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _set_oidc_transaction_cookies(
    request: Request,
    response: Response,
    *,
    state: str,
    code_verifier: str,
    provider_key: str,
    response_mode: str,
) -> None:
    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    same_site = "none" if response_mode == "form_post" else "lax"
    max_age = int(OIDC_AUTHORIZATION_REQUEST_LIFETIME.total_seconds())
    for name, value in (
        (OIDC_STATE_COOKIE_NAME, state),
        (OIDC_CODE_VERIFIER_COOKIE_NAME, code_verifier),
    ):
        response.set_cookie(
            key=name,
            value=value,
            httponly=True,
            secure=secure_cookie,
            samesite=same_site,
            path=_oidc_callback_path(provider_key),
            max_age=max_age,
        )


def _clear_oidc_transaction_cookies(response: Response, provider_key: str) -> None:
    callback_path = _oidc_callback_path(provider_key)
    response.delete_cookie(OIDC_STATE_COOKIE_NAME, path=callback_path)
    response.delete_cookie(OIDC_CODE_VERIFIER_COOKIE_NAME, path=callback_path)


def _rotate_after_oidc_account_change(
    request: Request,
    response: Response,
    db: Session,
    context,
    *,
    reason: str,
) -> None:
    revoke_sessions_for_user(db, context.user, reason=reason)
    revoke_trusted_devices_for_user(db, context.user, reason=reason)
    token = create_session(db, context.user, auth_level=context.session.auth_level)
    _set_session_cookie(request, response, token)
    _clear_trusted_device_cookie(response)


@app.post("/auth/oidc/{provider_key}/login", response_class=HTMLResponse)
@LOGIN_RATE_LIMIT
async def oidc_login_start(
    request: Request,
    provider_key: str,
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    existing_context = _current_context_optional(request, db)
    if existing_context is not None:
        return RedirectResponse(
            url=_post_login_redirect(existing_context),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    config = oidc_config(provider_key)
    if config is None:
        return render_auth_page(
            request,
            db,
            message="Single sign-on is not configured",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    try:
        started = await begin_oidc_authorization(db, config, purpose="login")
    except OidcProtocolError as caught:
        record_security_event(
            db,
            action="oidc_login_start_failure",
            request=request,
            details={
                "category": "auth",
                "outcome": "failure",
                "reason_code": "provider_unavailable",
                "provider_key": config.provider_key,
                "protocol_stage": caught.stage,
            },
        )
        _log_oidc_event(
            "oidc_authorization_start_failure",
            provider_key=config.provider_key,
            purpose="login",
            outcome="failure",
            reason_code="provider_unavailable",
            protocol_stage=caught.stage,
            status_code=status.HTTP_502_BAD_GATEWAY,
            level=logging.WARNING,
        )
        return render_auth_page(
            request,
            db,
            message="Single sign-on is temporarily unavailable",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    record_security_event(
        db,
        action="oidc_login_started",
        request=request,
        details={"category": "auth", "outcome": "started", "provider_key": config.provider_key},
    )
    _log_oidc_event(
        "oidc_authorization_started",
        provider_key=config.provider_key,
        purpose="login",
        outcome="started",
    )
    response = RedirectResponse(
        url=started.authorization_url,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    request.state.oidc_form_action_origins = (
        oidc_form_action_origin(started.authorization_url),
    )
    _set_oidc_transaction_cookies(
        request,
        response,
        state=started.state,
        code_verifier=started.code_verifier,
        provider_key=config.provider_key,
        response_mode=config.response_mode,
    )
    return response


@app.post("/settings/account/oidc/{provider_key}/link", response_class=HTMLResponse)
@ACCOUNT_SECURITY_RATE_LIMIT
async def oidc_link_start(
    request: Request,
    provider_key: str,
    current_password: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    config = oidc_config(provider_key)
    if config is None:
        return _oidc_error_redirect("Single sign-on is not configured")
    try:
        reauthenticate_for_oidc_link(context.user, current_password=current_password)
        started = await begin_oidc_authorization(
            db,
            config,
            purpose="link",
            user=context.user,
            user_session=context.session,
        )
    except AppError as exc:
        return _oidc_error_redirect(exc.message)
    except OidcProtocolError as caught:
        record_security_event(
            db,
            action="oidc_link_start_failure",
            actor=context.user,
            target=context.user,
            request=request,
            details={
                "category": "account",
                "outcome": "failure",
                "reason_code": "provider_unavailable",
                "provider_key": config.provider_key,
                "protocol_stage": caught.stage,
            },
        )
        _log_oidc_event(
            "oidc_authorization_start_failure",
            provider_key=config.provider_key,
            purpose="link",
            outcome="failure",
            reason_code="provider_unavailable",
            protocol_stage=caught.stage,
            status_code=status.HTTP_502_BAD_GATEWAY,
            level=logging.WARNING,
        )
        return _oidc_error_redirect("Single sign-on is temporarily unavailable")
    record_security_event(
        db,
        action="oidc_link_started",
        actor=context.user,
        target=context.user,
        request=request,
        details={"category": "account", "outcome": "started", "provider_key": config.provider_key},
    )
    _log_oidc_event(
        "oidc_authorization_started",
        provider_key=config.provider_key,
        purpose="link",
        outcome="started",
    )
    response = RedirectResponse(
        url=started.authorization_url,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    request.state.oidc_form_action_origins = (
        oidc_form_action_origin(started.authorization_url),
    )
    _set_oidc_transaction_cookies(
        request,
        response,
        state=started.state,
        code_verifier=started.code_verifier,
        provider_key=config.provider_key,
        response_mode=config.response_mode,
    )
    return response


@app.post("/settings/account/oidc/{provider_key}/unlink", response_class=HTMLResponse)
@ACCOUNT_SECURITY_RATE_LIMIT
def oidc_unlink(
    request: Request,
    provider_key: str,
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
    config = oidc_config(provider_key)
    if config is None:
        return _oidc_error_redirect("Single sign-on is not configured")
    try:
        reauthenticate_for_account_change(
            db,
            context.user,
            current_password=current_password,
            mfa_code=mfa_code,
        )
        unlink_oidc_identity(db, context.user, config)
    except AppError as exc:
        return _oidc_error_redirect(exc.message)
    response = RedirectResponse(
        url="/workspace/account?message=Single+sign-on+removed&message_kind=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _rotate_after_oidc_account_change(
        request,
        response,
        db,
        context,
        reason="oidc_identity_unlinked",
    )
    record_security_event(
        db,
        action="oidc_identity_unlinked",
        actor=context.user,
        target=context.user,
        request=request,
        details={"category": "account", "outcome": "success", "provider_key": config.provider_key},
    )
    return response


async def _oidc_callback_params(request: Request) -> dict[str, str]:
    if request.method == "GET":
        value = getattr(request.state, "oidc_callback_query", {})
        return value if isinstance(value, dict) else {}
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return {}
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > OIDC_CALLBACK_BODY_MAX_BYTES:
            return {}
        body.extend(chunk)
    try:
        form_items = parse_qsl(
            body.decode("ascii"),
            keep_blank_values=True,
            max_num_fields=16,
        )
    except (UnicodeDecodeError, ValueError):
        return {}
    accepted_keys = {"code", "state", "error", "error_description"}
    received_keys = [key for key, _value in form_items if key in accepted_keys]
    if len(received_keys) != len(set(received_keys)):
        return {}
    return {key: value for key, value in form_items if key in accepted_keys}


async def _oidc_callback(request: Request, db: Session, provider_key: str):
    config = oidc_config(provider_key)
    if config is None:
        return render_auth_page(
            request,
            db,
            message="Single sign-on is not configured",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    expected_method = "POST" if config.response_mode == "form_post" else "GET"
    if request.method != expected_method:
        _log_oidc_event(
            "oidc_callback_failure",
            provider_key=config.provider_key,
            purpose="unknown",
            outcome="failure",
            reason_code="response_mode_mismatch",
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            level=logging.WARNING,
        )
        return render_auth_page(
            request,
            db,
            message="Single sign-on could not be verified",
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        )
    params = await _oidc_callback_params(request)
    state = params.get("state", "")
    code = params.get("code", "")
    provider_error = params.get("error", "")
    purpose = "login"
    context = None
    protocol_stage = None
    try:
        if len(state) > 256 or len(code) > 4096 or len(provider_error) > 128:
            raise AppError(401, "oidc_callback_invalid", "Single sign-on could not be verified")
        consumed = consume_oidc_authorization(
            db,
            provider_key=config.provider_key,
            state=state,
            state_cookie=request.cookies.get(OIDC_STATE_COOKIE_NAME),
            code_verifier=request.cookies.get(OIDC_CODE_VERIFIER_COOKIE_NAME),
        )
        purpose = consumed.purpose
        if purpose == "link":
            context = _current_context_optional(request, db)
            if context is None and SESSION_COOKIE_NAME not in request.cookies:
                resolved_link_session = resolve_oidc_link_session(
                    db,
                    user_id=consumed.user_id,
                    user_session_id=consumed.user_session_id,
                )
                if resolved_link_session is not None:
                    link_user, link_session = resolved_link_session
                    context = AuthenticatedContext(
                        user=link_user,
                        session=link_session,
                        token="",
                    )
            if (
                context is None
                or context.session.auth_level is not SessionAuthLevel.full
                or context.user.id != consumed.user_id
                or context.session.id != consumed.user_session_id
                or context.user.is_system_admin
            ):
                raise AppError(401, "oidc_link_session_invalid", "Sign in again before linking single sign-on")
        if provider_error or not code:
            raise AppError(401, "oidc_provider_rejected", "Single sign-on was cancelled or rejected")

        authorization_response_url = f"{config.redirect_uri}?{urlencode({'code': code, 'state': state})}"
        verified = await exchange_oidc_code_for_identity(
            config,
            authorization_response_url=authorization_response_url,
            state=state,
            code_verifier=request.cookies[OIDC_CODE_VERIFIER_COOKIE_NAME],
            nonce=consumed.nonce,
        )
        if purpose == "link":
            link_oidc_identity(
                db,
                context.user,
                config,
                issuer=verified.issuer,
                subject=verified.subject,
            )
            response = RedirectResponse(
                url="/workspace/account?message=Single+sign-on+linked&message_kind=success",
                status_code=status.HTTP_303_SEE_OTHER,
            )
            _rotate_after_oidc_account_change(
                request,
                response,
                db,
                context,
                reason="oidc_identity_linked",
            )
            record_security_event(
                db,
                action="oidc_identity_linked",
                actor=context.user,
                target=context.user,
                request=request,
                details={
                    "category": "account",
                    "outcome": "success",
                    "provider_key": config.provider_key,
                    "required_acr_satisfied": bool(config.required_acr_values),
                },
            )
            _log_oidc_event(
                "oidc_callback_success",
                provider_key=config.provider_key,
                purpose="link",
                outcome="success",
                status_code=status.HTTP_303_SEE_OTHER,
            )
            _clear_oidc_transaction_cookies(response, config.provider_key)
            return response

        user = authenticate_oidc_identity(
            db,
            config,
            issuer=verified.issuer,
            subject=verified.subject,
        )
        _enforce_localhost_only_dev_account(request, user)
        trusted_device = resolve_trusted_device(
            db,
            user,
            request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME),
        )
        auth_level = login_auth_level(user, trusted_device)
        if trusted_device and auth_level is SessionAuthLevel.full:
            touch_trusted_device_seen(db, trusted_device)
        token = create_session(db, user, auth_level=auth_level)
        record_security_event(
            db,
            action="oidc_login_success",
            actor=user,
            target=user,
            request=request,
            details={
                "category": "auth",
                "outcome": "success",
                "provider_key": config.provider_key,
                "auth_level": auth_level.value,
                "trusted_device_used": bool(
                    trusted_device and auth_level is SessionAuthLevel.full
                ),
                "required_acr_satisfied": bool(config.required_acr_values),
            },
        )
        _log_oidc_event(
            "oidc_callback_success",
            provider_key=config.provider_key,
            purpose="login",
            outcome="success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        redirect_to = (
            "/onboarding"
            if auth_level is SessionAuthLevel.onboarding
            else (
                "/mfa/challenge"
                if auth_level is SessionAuthLevel.pending_mfa
                else _post_login_redirect_for_user(user)
            )
        )
        response = RedirectResponse(url=redirect_to, status_code=status.HTTP_303_SEE_OTHER)
        _set_session_cookie(request, response, token)
        _clear_oidc_transaction_cookies(response, config.provider_key)
        return response
    except OidcProtocolError as caught:
        protocol_stage = caught.stage
        exc = AppError(401, "oidc_authentication_failed", "Single sign-on could not be verified")
    except AppError as caught:
        exc = caught

    failure_details = {
        "category": "auth" if purpose == "login" else "account",
        "outcome": "failure",
        "provider_key": config.provider_key,
        "reason_code": exc.code,
        "status_code": exc.status_code,
    }
    if protocol_stage is not None:
        failure_details["protocol_stage"] = protocol_stage
    record_security_event(
        db,
        action="oidc_login_failure" if purpose == "login" else "oidc_link_failure",
        actor=context.user if context is not None else None,
        target=context.user if context is not None else None,
        request=request,
        details=failure_details,
    )
    _log_oidc_event(
        "oidc_callback_failure",
        provider_key=config.provider_key,
        purpose=purpose,
        outcome="failure",
        reason_code=exc.code,
        protocol_stage=protocol_stage or "-",
        status_code=exc.status_code,
        level=logging.WARNING,
    )
    if purpose == "link" and context is not None:
        response = _oidc_error_redirect(exc.message)
    else:
        response = render_auth_page(
            request,
            db,
            message=exc.message,
            status_code=exc.status_code,
        )
    _clear_oidc_transaction_cookies(response, config.provider_key)
    return response


@app.get("/auth/oidc/{provider_key}/callback", response_class=HTMLResponse)
async def oidc_callback_query(
    request: Request,
    provider_key: str,
    db: Session = Depends(get_db),
):
    return await _oidc_callback(request, db, provider_key)


@app.post("/auth/oidc/{provider_key}/callback", response_class=HTMLResponse)
async def oidc_callback_form_post(
    request: Request,
    provider_key: str,
    db: Session = Depends(get_db),
):
    # The provider POST is authenticated by one-time state, nonce, PKCE, and
    # the transaction cookies. It is intentionally not a same-origin CSRF form.
    return await _oidc_callback(request, db, provider_key)
