import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import AppError, app_error_handler, http_error_handler, validation_error_handler
from .models import TeamRole, TeamStatus, Transcript, TranscriptStatus, TranscriptVersion, User, UserSession, UserStatus, transcript_expiry
from .schemas import (
    AccountRequestApprove,
    AccountRequestCreate,
    AccountRequestDetail,
    AccountRequestListItem,
    AccountRequestReject,
    CurrentUserResponse,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    RecoveryCodesResponse,
    TeamCreate,
    TeamDetail,
    TeamListItem,
    TotpEnrollmentStartResponse,
    TotpVerifyRequest,
    TranscriptCommit,
    TranscriptCreate,
    TranscriptDetail,
    TranscriptListItem,
    UserCreate,
    UserDetail,
    UserListItem,
)
from .services.admin import (
    approve_account_request as approve_account_request_service,
    create_account_request as create_account_request_service,
    create_bootstrap_admin,
    create_team as create_team_service,
    create_user as create_user_service,
    list_manageable_account_requests as list_manageable_account_requests_service,
    list_manageable_users as list_manageable_users_service,
    list_teams as list_teams_service,
    list_users as list_users_service,
    reject_account_request as reject_account_request_service,
    user_count as user_count_service,
    hash_password,
)
from .services.auth import (
    SESSION_COOKIE_NAME,
    authenticate_user,
    create_session,
    current_pending_totp_method,
    determine_auth_level,
    generate_recovery_codes,
    provisioning_uri,
    resolve_authenticated_session,
    revoke_session_by_token,
    rotate_session,
    skip_recovery_codes,
    start_totp_enrollment,
    update_password_for_onboarding,
    verify_totp_enrollment,
)


@dataclass(slots=True)
class AuthenticatedContext:
    user: User
    session: UserSession
    token: str


app = FastAPI(title="OpenScribe MVP")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
api = APIRouter(prefix="/api/v1")

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)


def _set_session_cookie(response: JSONResponse | RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 12,
    )


def _clear_session_cookie(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _post_login_redirect(context: AuthenticatedContext) -> str:
    if context.session.auth_level.value == "onboarding":
        return "/onboarding"
    return "/admin" if context.user.is_system_admin else "/home"


def _post_login_redirect_for_user(user: User) -> str:
    return "/onboarding" if determine_auth_level(user).value == "onboarding" else ("/admin" if user.is_system_admin else "/home")


def _user_count(db: Session) -> int:
    return user_count_service(db)


def _bootstrap_allowed(db: Session) -> bool:
    return _user_count(db) == 0


def _current_context_optional(request: Request, db: Session) -> AuthenticatedContext | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    resolved = resolve_authenticated_session(db, token)
    if resolved is None:
        return None
    user, session = resolved
    return AuthenticatedContext(user=user, session=session, token=token)


def require_authenticated_context(request: Request, db: Session = Depends(get_db)) -> AuthenticatedContext:
    context = _current_context_optional(request, db)
    if context is None:
        raise AppError(401, "unauthorized", "Authentication required")
    return context


def require_full_context(context: AuthenticatedContext = Depends(require_authenticated_context)) -> AuthenticatedContext:
    if context.session.auth_level is not determine_auth_level(context.user):
        raise AppError(401, "unauthorized", "Authentication required")
    if context.session.auth_level.value != "full":
        raise AppError(403, "onboarding_incomplete", "Complete onboarding before accessing this route")
    return context


def require_system_admin(context: AuthenticatedContext = Depends(require_full_context)) -> AuthenticatedContext:
    if not context.user.is_system_admin:
        raise AppError(403, "forbidden", "System admin access required")
    return context


def require_user_manager(context: AuthenticatedContext = Depends(require_full_context)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    raise AppError(403, "forbidden", "User-management access required")


def _page_context_or_redirect(request: Request, db: Session, *, require_full: bool) -> tuple[AuthenticatedContext | None, RedirectResponse | None]:
    context = _current_context_optional(request, db)
    if context is None:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if require_full and context.session.auth_level.value != "full":
        return None, RedirectResponse(url="/onboarding", status_code=status.HTTP_303_SEE_OTHER)
    return context, None


def render_auth_page(
    request: Request,
    db: Session,
    *,
    message: str | None = None,
    message_kind: str = "error",
    status_code: int = 200,
):
    context = {
        "request": request,
        "bootstrap_allowed": _bootstrap_allowed(db),
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "login.html", context, status_code=status_code)


def render_request_access_page(
    request: Request,
    *,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "request_access.html",
        {"request": request, "message": message, "message_kind": message_kind},
        status_code=status_code,
    )


def render_admin(
    request: Request,
    db: Session,
    *,
    current_user: User,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    context = {
        "request": request,
        "current_user": current_user,
        "teams": list_teams_service(db),
        "users": list_users_service(db),
        "account_requests": list_manageable_account_requests_service(db, current_user),
        "team_statuses": list(TeamStatus),
        "team_roles": list(TeamRole),
        "user_statuses": list(UserStatus),
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "admin.html", context, status_code=status_code)


def render_home(
    request: Request,
    db: Session,
    *,
    current_user: User,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    is_manager = current_user.is_system_admin or current_user.team_role is TeamRole.leader
    context = {
        "request": request,
        "current_user": current_user,
        "is_manager": is_manager,
        "manageable_users": list_manageable_users_service(db, current_user) if is_manager else [],
        "account_requests": list_manageable_account_requests_service(db, current_user) if is_manager else [],
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "home.html", context, status_code=status_code)


def render_onboarding(
    request: Request,
    *,
    current_user: User,
    totp_secret: str | None = None,
    totp_uri: str | None = None,
    recovery_codes: list[str] | None = None,
    message: str | None = None,
    message_kind: str = "error",
    status_code: int = 200,
):
    context = {
        "request": request,
        "current_user": current_user,
        "totp_secret": totp_secret,
        "totp_uri": totp_uri,
        "recovery_codes": recovery_codes,
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "onboarding.html", context, status_code=status_code)


@app.get("/health")
def health():
    return {"status": "ok"}


error_responses = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@api.post("/auth/login", response_model=LoginResponse, responses=error_responses)
def api_login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.email, payload.password)
    token = create_session(db, user)
    auth_level = determine_auth_level(user)
    body = LoginResponse(authenticated=True, auth_level=auth_level, redirect_to="/onboarding" if auth_level.value == "onboarding" else ("/admin" if user.is_system_admin else "/home"))
    response = JSONResponse(body.model_dump(mode="json"))
    _set_session_cookie(response, token)
    return response


@api.post("/auth/logout", response_model=LoginResponse)
def api_logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session_by_token(db, token, reason="logout")
    response = JSONResponse(LoginResponse(authenticated=False).model_dump(mode="json"))
    _clear_session_cookie(response)
    return response


@api.get("/auth/me", response_model=CurrentUserResponse, responses=error_responses)
def api_me(context: AuthenticatedContext = Depends(require_authenticated_context)):
    return CurrentUserResponse(
        id=str(context.user.id),
        full_name=context.user.full_name,
        email=context.user.email,
        is_system_admin=context.user.is_system_admin,
        team_id=str(context.user.team_id) if context.user.team_id else None,
        team_role=context.user.team_role.value if context.user.team_role else None,
        auth_level=context.session.auth_level,
        onboarding_state=context.user.onboarding_state,
    )


@api.post("/account-requests", response_model=AccountRequestDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_account_request(payload: AccountRequestCreate, db: Session = Depends(get_db)):
    return create_account_request_service(db, payload)


@api.get("/account-requests", response_model=list[AccountRequestListItem], responses=error_responses)
def list_account_requests(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return list_manageable_account_requests_service(db, context.user)


@api.post("/account-requests/{request_id}/approve", response_model=UserDetail, responses=error_responses)
def approve_account_request(request_id: UUID, payload: AccountRequestApprove, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    _, user = approve_account_request_service(db, context.user, request_id, payload)
    return user


@api.post("/account-requests/{request_id}/reject", response_model=AccountRequestDetail, responses=error_responses)
def reject_account_request(request_id: UUID, payload: AccountRequestReject, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return reject_account_request_service(db, context.user, request_id, payload)


@api.post("/onboarding/password", response_model=CurrentUserResponse, responses=error_responses)
def api_onboarding_password(payload: PasswordChangeRequest, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = update_password_for_onboarding(db, context.user, new_password_hash=hash_password(payload.new_password))
    return CurrentUserResponse(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        is_system_admin=user.is_system_admin,
        team_id=str(user.team_id) if user.team_id else None,
        team_role=user.team_role.value if user.team_role else None,
        auth_level=context.session.auth_level,
        onboarding_state=user.onboarding_state,
    )


@api.post("/onboarding/totp/start", response_model=TotpEnrollmentStartResponse, responses=error_responses)
def api_onboarding_totp_start(context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    method = start_totp_enrollment(db, context.user)
    return TotpEnrollmentStartResponse(secret=method.secret, provisioning_uri=provisioning_uri(context.user, method))


@api.post("/onboarding/totp/verify", response_model=CurrentUserResponse, responses=error_responses)
def api_onboarding_totp_verify(payload: TotpVerifyRequest, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = verify_totp_enrollment(db, context.user, code=payload.code)
    return CurrentUserResponse(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        is_system_admin=user.is_system_admin,
        team_id=str(user.team_id) if user.team_id else None,
        team_role=user.team_role.value if user.team_role else None,
        auth_level=context.session.auth_level,
        onboarding_state=user.onboarding_state,
    )


@api.post("/onboarding/recovery-codes", response_model=RecoveryCodesResponse, responses=error_responses)
def api_onboarding_recovery_codes(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    codes = generate_recovery_codes(db, context.user)
    refreshed_user = db.get(User, context.user.id)
    token = rotate_session(db, context.token, refreshed_user, auth_level=determine_auth_level(refreshed_user))
    response = JSONResponse(RecoveryCodesResponse(codes=codes).model_dump(mode="json"))
    _set_session_cookie(response, token)
    return response


@api.post("/onboarding/skip-recovery-codes", response_model=LoginResponse, responses=error_responses)
def api_skip_recovery_codes(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = skip_recovery_codes(db, context.user)
    token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    response = JSONResponse(
        LoginResponse(authenticated=True, auth_level=determine_auth_level(user), redirect_to="/admin" if user.is_system_admin else "/home").model_dump(mode="json")
    )
    _set_session_cookie(response, token)
    return response


@api.post("/teams", response_model=TeamDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_team(payload: TeamCreate, _: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return create_team_service(db, payload)


@api.get("/teams", response_model=list[TeamListItem], responses=error_responses)
def list_teams(_: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return list_teams_service(db)


@api.post("/users", response_model=UserDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_user(payload: UserCreate, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return create_user_service(db, payload, actor=context.user)


@api.get("/users", response_model=list[UserListItem], responses=error_responses)
def list_users(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return list_manageable_users_service(db, context.user)


@api.post("/transcripts", response_model=TranscriptDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_transcript(payload: TranscriptCreate, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    current_user = context.user
    owner = db.get(User, payload.owner_user_id)
    if not owner:
        raise AppError(404, "not_found", "Owner user not found", {"resource": "user", "user_id": str(payload.owner_user_id)})
    if current_user.id != payload.owner_user_id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    if owner.team_id != payload.team_id:
        raise AppError(
            422,
            "business_rule_violation",
            "Owner user does not belong to the provided team",
            {"owner_user_id": str(payload.owner_user_id), "team_id": str(payload.team_id)},
        )
    retention_days = payload.retention_days_applied or owner.team.default_retention_days
    transcript = Transcript(
        owner_user_id=payload.owner_user_id,
        team_id=payload.team_id,
        title=payload.title,
        current_draft_text_encrypted=payload.current_draft_text_encrypted,
        status=TranscriptStatus.recording,
        retention_days_applied=retention_days,
        retention_expires_at=transcript_expiry(retention_days),
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


@api.post("/transcripts/{transcript_id}/commit", response_model=TranscriptDetail, responses=error_responses)
def commit_transcript(transcript_id: UUID, payload: TranscriptCommit, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != context.user.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    current_max = db.scalar(select(func.max(TranscriptVersion.version_no)).where(TranscriptVersion.transcript_id == transcript.id))
    next_version = (current_max or 0) + 1
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=next_version,
        text_encrypted=payload.text_encrypted,
    )
    transcript.current_draft_text_encrypted = payload.text_encrypted
    transcript.status = TranscriptStatus.ready
    db.add(version)
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


@api.get("/users/{user_id}/transcripts", response_model=list[TranscriptListItem], responses=error_responses)
def list_user_transcripts(user_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    if user_id != context.user.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    rows = db.scalars(select(Transcript).where(Transcript.owner_user_id == user_id).order_by(Transcript.created_at.desc()))
    return list(rows)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    context = _current_context_optional(request, db)
    if context is not None:
        return RedirectResponse(url=_post_login_redirect(context), status_code=status.HTTP_303_SEE_OTHER)
    return render_auth_page(request, db)


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    try:
        user = authenticate_user(db, email, password)
    except AppError as exc:
        return render_auth_page(request, db, message=exc.message, message_kind="error", status_code=exc.status_code)
    token = create_session(db, user)
    response = RedirectResponse(url=_post_login_redirect_for_user(user), status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token)
    return response


@app.post("/logout", response_class=HTMLResponse)
def logout_submit(request: Request, db: Session = Depends(get_db)):
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
def request_access_submit(
    request: Request,
    requested_name: str = Form(...),
    requested_email: str = Form(...),
    requested_team_name: str = Form(...),
    request_details: str = Form(""),
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


@app.post("/bootstrap/system-admin", response_class=HTMLResponse)
def bootstrap_system_admin(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
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
    _set_session_cookie(response, token)
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
    return render_onboarding(request, current_user=context.user, totp_secret=secret, totp_uri=uri)


@app.post("/onboarding/password", response_class=HTMLResponse)
def onboarding_password_submit(request: Request, new_password: str = Form(...), db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    try:
        user = update_password_for_onboarding(db, context.user, new_password_hash=hash_password(new_password))
    except AppError as exc:
        return render_onboarding(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return render_onboarding(request, current_user=user, message="Password updated. Continue to TOTP enrollment.", message_kind="success")


@app.post("/onboarding/totp/start", response_class=HTMLResponse)
def onboarding_totp_start_submit(request: Request, db: Session = Depends(get_db)):
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
        message="TOTP secret created. Enter the 6-digit code from your authenticator app.",
        message_kind="success",
    )


@app.post("/onboarding/totp/verify", response_class=HTMLResponse)
def onboarding_totp_verify_submit(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
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
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return render_onboarding(request, current_user=user, message="TOTP verified. Generate recovery codes or skip this optional step.", message_kind="success")


@app.post("/onboarding/recovery-codes", response_class=HTMLResponse)
def onboarding_recovery_codes_submit(request: Request, db: Session = Depends(get_db)):
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
        },
    )
    _set_session_cookie(response, token)
    return response


@app.post("/onboarding/skip-recovery-codes", response_class=HTMLResponse)
def onboarding_skip_recovery_codes_submit(request: Request, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=False)
    if response is not None:
        return response
    try:
        user = skip_recovery_codes(db, context.user)
        token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    except AppError as exc:
        return render_onboarding(request, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    response = RedirectResponse(url="/admin" if user.is_system_admin else "/home", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token)
    return response


@app.get("/home", response_class=HTMLResponse)
def home_page(request: Request, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    return render_home(request, db, current_user=context.user)


@app.post("/home/users", response_class=HTMLResponse)
def home_create_user(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(...),
    temporary_password: str = Form(...),
    team_role: str = Form(...),
    status_value: UserStatus = Form(..., alias="status"),
    mfa_required: str | None = Form(default=None),
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
        return render_home(request, db, current_user=context.user, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/account-requests/{request_id}/approve", response_class=HTMLResponse)
def home_approve_account_request(
    request: Request,
    request_id: UUID,
    temporary_password: str = Form(...),
    team_role: str = Form(...),
    review_notes: str = Form(""),
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
        return render_home(request, db, current_user=context.user, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/account-requests/{request_id}/reject", response_class=HTMLResponse)
def home_reject_account_request(request: Request, request_id: UUID, review_notes: str = Form(...), db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        reject_account_request_service(db, context.user, request_id, AccountRequestReject(review_notes=review_notes))
    except AppError as exc:
        return render_home(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(request, db, current_user=context.user)


@app.post("/admin/teams", response_class=HTMLResponse)
def admin_create_team(request: Request, name: str = Form(...), status_value: TeamStatus = Form(..., alias="status"), default_retention_days: int = Form(...), db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        create_team_service(db, TeamCreate(name=name, status=status_value, default_retention_days=default_retention_days))
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


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
        return render_admin(request, db, current_user=context.user, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/account-requests/{request_id}/approve", response_class=HTMLResponse)
def admin_approve_account_request(
    request: Request,
    request_id: UUID,
    team_id: str = Form(...),
    temporary_password: str = Form(...),
    team_role: str = Form(...),
    review_notes: str = Form(""),
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
        return render_admin(request, db, current_user=context.user, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/account-requests/{request_id}/reject", response_class=HTMLResponse)
def admin_reject_account_request(request: Request, request_id: UUID, review_notes: str = Form(...), db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        reject_account_request_service(db, context.user, request_id, AccountRequestReject(review_notes=review_notes))
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


app.include_router(api)
