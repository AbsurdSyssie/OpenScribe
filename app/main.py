import json
import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import AppError, app_error_handler, http_error_handler, rate_limit_error_handler, validation_error_handler
from .models import SessionAuthLevel, SttAdapterKind, TeamRole, TeamStatus, Transcript, TranscriptStatus, TranscriptVersion, User, UserSession, UserStatus, transcript_expiry
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
    MfaChallengeRequest,
    PasswordChangeRequest,
    RecoveryCodesResponse,
    SttConfigDetail,
    SttInspectRequest,
    SttInspectResult,
    SttConfigUpsert,
    SttSelectionDetail,
    SttSelectionUpsert,
    TeamCreate,
    TeamDetail,
    TeamListItem,
    TotpEnrollmentStartResponse,
    TotpVerifyRequest,
    TranscriptCommit,
    TranscriptCreate,
    TranscriptDetail,
    TranscriptIngestionAccepted,
    TranscriptIngestionJobDetail,
    TranscriptListItem,
    TranscriptStart,
    TrustedDeviceStatusResponse,
    UserCreate,
    UserDetail,
    UserListItem,
)
from .services.stt import (
    delete_stt_config as delete_stt_config_service,
    get_stt_config as get_stt_config_service,
    inspect_stt_contract as inspect_stt_contract_service,
    get_team_stt_selection as get_team_stt_selection_service,
    list_selectable_stt_configs as list_selectable_stt_configs_service,
    list_stt_configs as list_stt_configs_service,
    clear_team_stt_selection as clear_team_stt_selection_service,
    set_team_stt_selection as set_team_stt_selection_service,
    upsert_stt_config as upsert_stt_config_service,
)
from .services.admin import (
    approve_account_request as approve_account_request_service,
    create_account_request as create_account_request_service,
    create_bootstrap_admin,
    create_team as create_team_service,
    create_user as create_user_service,
    delete_user as delete_user_service,
    list_manageable_account_requests as list_manageable_account_requests_service,
    list_manageable_users as list_manageable_users_service,
    list_teams as list_teams_service,
    list_users as list_users_service,
    reactivate_user as reactivate_user_service,
    reject_account_request as reject_account_request_service,
    suspend_user as suspend_user_service,
    user_count as user_count_service,
    hash_password,
)
from .services.auth import (
    SESSION_COOKIE_NAME,
    TRUSTED_DEVICE_COOKIE_NAME,
    authenticate_user,
    create_session,
    current_pending_totp_method,
    determine_auth_level,
    generate_recovery_codes,
    login_auth_level,
    provisioning_qr_svg_data_uri,
    provisioning_uri,
    resolve_authenticated_session,
    resolve_trusted_device,
    revoke_session_by_token,
    rotate_session,
    skip_recovery_codes,
    start_totp_enrollment,
    touch_trusted_device_seen,
    trusted_device_fresh_until,
    trusted_device_satisfies_mfa,
    update_password_for_onboarding,
    verify_login_totp,
    verify_totp_enrollment,
)
from .services.transcripts import (
    attach_task_id_to_ingestion_job,
    create_transcript_from_payload,
    mark_ingestion_job_enqueue_failed,
    queue_audio_chunk_ingestion,
    queue_audio_file_ingestion,
    start_transcript as start_transcript_service,
)
from .tasks import enqueue_transcript_ingestion_job


@dataclass(slots=True)
class AuthenticatedContext:
    user: User
    session: UserSession
    token: str


app = FastAPI(title="OpenScribe MVP")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
api = APIRouter(prefix="/api/v1")
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URL", "redis://localhost:6379/0"),
    headers_enabled=False,
)
app.state.limiter = limiter
LOGIN_RATE_LIMIT = limiter.shared_limit("5/5 minutes", scope="login")
MFA_RATE_LIMIT = limiter.shared_limit("10/10 minutes", scope="mfa_totp")
ACCOUNT_REQUEST_RATE_LIMIT = limiter.shared_limit("3/hour", scope="account_request")

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_error_handler)


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


def _set_trusted_device_cookie(response: JSONResponse | RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=TRUSTED_DEVICE_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 30,
    )


def _clear_session_cookie(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _clear_trusted_device_cookie(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(TRUSTED_DEVICE_COOKIE_NAME, path="/")


def _post_login_redirect(context: AuthenticatedContext) -> str:
    if context.session.auth_level.value == "onboarding":
        return "/onboarding"
    if context.session.auth_level.value == "pending_mfa":
        return "/mfa/challenge"
    return "/admin" if context.user.is_system_admin else "/home"


def _post_login_redirect_for_user(user: User) -> str:
    auth_level = determine_auth_level(user)
    if auth_level.value == "onboarding":
        return "/onboarding"
    return "/admin" if user.is_system_admin else "/home"


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
    if context.session.auth_level.value == "pending_mfa":
        raise AppError(403, "mfa_required", "Complete TOTP verification before accessing this route")
    if context.session.auth_level is not determine_auth_level(context.user):
        raise AppError(401, "unauthorized", "Authentication required")
    if context.session.auth_level.value != "full":
        raise AppError(403, "onboarding_incomplete", "Complete onboarding before accessing this route")
    return context


def require_system_admin(context: AuthenticatedContext = Depends(require_full_context)) -> AuthenticatedContext:
    if not context.user.is_system_admin:
        raise AppError(403, "forbidden", "System admin access required")
    return context


def require_stt_selector(context: AuthenticatedContext = Depends(require_full_context)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    raise AppError(403, "forbidden", "STT selection access required")


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
    if context.session.auth_level.value == "pending_mfa":
        return None, RedirectResponse(url="/mfa/challenge", status_code=status.HTTP_303_SEE_OTHER)
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
    selected_team_id: str | None = None,
    selected_stt_config_id: str | None = None,
    stt_inspection: SttInspectResult | None = None,
    stt_form_override: dict[str, object] | None = None,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    selected_uuid = UUID(selected_team_id) if selected_team_id else None
    stt_configs = list_stt_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
    edit_stt_config = next((config for config in stt_configs if str(config.id) == selected_stt_config_id), None)
    stt_selection = get_team_stt_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    context = {
        "request": request,
        "current_user": current_user,
        "teams": list_teams_service(db),
        "users": list_users_service(db),
        "selected_team_id": selected_team_id,
        "selected_stt_config_id": selected_stt_config_id,
        "stt_configs": stt_configs,
        "stt_config": edit_stt_config,
        "stt_selection": stt_selection,
        "stt_inspection": stt_inspection,
        "stt_form": stt_form_override or stt_form_defaults(edit_stt_config, None),
        "selectable_stt_configs": list_selectable_stt_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else [],
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
    stt_selection = get_team_stt_selection_service(db, current_user) if is_manager else None
    selectable_stt_configs = list_selectable_stt_configs_service(db, current_user) if is_manager else []
    context = {
        "request": request,
        "current_user": current_user,
        "is_manager": is_manager,
        "manageable_users": list_manageable_users_service(db, current_user) if is_manager else [],
        "account_requests": list_manageable_account_requests_service(db, current_user) if is_manager else [],
        "stt_selection": stt_selection,
        "selectable_stt_configs": selectable_stt_configs,
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "home.html", context, status_code=status_code)


def stt_config_response(config) -> SttConfigDetail:
    return SttConfigDetail(
        id=config.id,
        team_id=config.team_id,
        label=config.label,
        adapter_kind=config.adapter_kind,
        base_url=config.base_url,
        transcribe_path=config.transcribe_path,
        auth_mode=config.auth_mode,
        model_name=config.model_name,
        available_models_json=list(config.available_models_json or []),
        file_field_name=config.file_field_name,
        language=config.language,
        response_text_path=config.response_text_path,
        extra_form_fields_json=config.extra_form_fields_json or {},
        is_active=config.is_active,
        has_secret=bool(config.vault_secret_ref),
        created_by_user_id=config.created_by_user_id,
        updated_by_user_id=config.updated_by_user_id,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def stt_selection_response(selection) -> SttSelectionDetail:
    config = selection.config
    resolved_model_name = selection.model_name_override or config.model_name
    resolved_language = selection.language_override or config.language
    return SttSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        stt_config_id=selection.stt_config_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_config_label=config.label,
        selected_config_adapter_kind=config.adapter_kind,
        selected_config_base_url=config.base_url,
        selected_config_transcribe_path=config.transcribe_path,
        model_name_override=selection.model_name_override,
        language_override=selection.language_override,
        resolved_model_name=resolved_model_name,
        resolved_language=resolved_language,
        available_models_json=list(config.available_models_json or []),
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def stt_form_defaults(config, inspection: SttInspectResult | None) -> dict[str, object]:
    if inspection is not None:
        return {
            "config_id": "",
            "label": "",
            "adapter_kind": inspection.adapter_kind.value,
            "base_url": inspection.base_url,
            "openapi_path": inspection.openapi_path or "/openapi.json",
            "transcribe_path": inspection.transcribe_path,
            "model_name": inspection.model_name or "",
            "available_models": inspection.available_models,
            "available_model_options": [option.model_dump(mode="json") for option in inspection.available_model_options],
            "file_field_name": inspection.file_field_name,
            "language": inspection.language or "",
            "response_text_path": inspection.response_text_path,
            "extra_form_fields_json": json.dumps(inspection.extra_form_fields_json) if inspection.extra_form_fields_json else "",
            "is_active": True,
        }
    if config is not None:
        return {
            "config_id": str(config.id),
            "label": config.label,
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "openapi_path": "/openapi.json" if config.adapter_kind is SttAdapterKind.generic_rest else "",
            "transcribe_path": config.transcribe_path,
            "model_name": config.model_name or "",
            "available_models": list(config.available_models_json or []),
            "available_model_options": [
                {"id": model, "source": "saved", "label": f"{model} (saved)"}
                for model in (config.available_models_json or [])
            ],
            "file_field_name": config.file_field_name,
            "language": config.language or "",
            "response_text_path": config.response_text_path,
            "extra_form_fields_json": json.dumps(config.extra_form_fields_json) if config.extra_form_fields_json else "",
            "is_active": config.is_active,
        }
    return {
        "config_id": "",
        "label": "",
        "adapter_kind": SttAdapterKind.generic_rest.value,
        "base_url": "",
        "openapi_path": "/openapi.json",
        "transcribe_path": "/v1/audio/transcriptions",
        "model_name": "",
        "available_models": [],
        "available_model_options": [],
        "file_field_name": "file",
        "language": "",
        "response_text_path": "text",
        "extra_form_fields_json": "",
        "is_active": True,
    }


def render_onboarding(
    request: Request,
    *,
    current_user: User,
    totp_secret: str | None = None,
    totp_uri: str | None = None,
    totp_qr_svg_data_uri: str | None = None,
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
        "totp_qr_svg_data_uri": totp_qr_svg_data_uri,
        "recovery_codes": recovery_codes,
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "onboarding.html", context, status_code=status_code)


def render_mfa_challenge(
    request: Request,
    *,
    current_user: User,
    message: str | None = None,
    message_kind: str = "error",
    status_code: int = 200,
):
    context = {
        "request": request,
        "current_user": current_user,
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "mfa_challenge.html", context, status_code=status_code)


def parse_extra_form_fields_json(raw_value: str) -> dict[str, str]:
    if not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise AppError(422, "business_rule_violation", "Extra form fields must be valid JSON", {"field": "extra_form_fields_json"}) from exc
    if not isinstance(parsed, dict):
        raise AppError(422, "business_rule_violation", "Extra form fields must be a JSON object", {"field": "extra_form_fields_json"})
    cleaned: dict[str, str] = {}
    for key, value in parsed.items():
        cleaned[str(key)] = str(value)
    return cleaned


@app.get("/health")
def health():
    return {"status": "ok"}


error_responses = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@api.post("/auth/login", response_model=LoginResponse, responses=error_responses)
@LOGIN_RATE_LIMIT
def api_login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.email, payload.password)
    trusted_device = resolve_trusted_device(db, user, request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME))
    auth_level = login_auth_level(user, trusted_device)
    if trusted_device and auth_level is SessionAuthLevel.full:
        touch_trusted_device_seen(db, trusted_device)
    token = create_session(db, user, auth_level=auth_level)
    body = LoginResponse(
        authenticated=True,
        auth_level=auth_level,
        redirect_to="/onboarding" if auth_level.value == "onboarding" else ("/mfa/challenge" if auth_level.value == "pending_mfa" else ("/admin" if user.is_system_admin else "/home")),
    )
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


@api.post("/auth/mfa/totp", response_model=LoginResponse, responses=error_responses)
@MFA_RATE_LIMIT
def api_login_mfa_totp(
    payload: MfaChallengeRequest,
    request: Request,
    context: AuthenticatedContext = Depends(require_authenticated_context),
    db: Session = Depends(get_db),
):
    if context.session.auth_level is not SessionAuthLevel.pending_mfa:
        raise AppError(409, "conflict", "MFA challenge is not pending for this session")
    user, trusted_device_token = verify_login_totp(
        db,
        context.user,
        code=payload.code,
        remember_device=payload.remember_device,
        device_label=request.headers.get("user-agent"),
    )
    token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    response = JSONResponse(
        LoginResponse(
            authenticated=True,
            auth_level=determine_auth_level(user),
            redirect_to="/admin" if user.is_system_admin else "/home",
        ).model_dump(mode="json")
    )
    _set_session_cookie(response, token)
    if trusted_device_token:
        _set_trusted_device_cookie(response, trusted_device_token)
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


@api.get("/auth/trusted-device", response_model=TrustedDeviceStatusResponse, responses=error_responses)
def api_trusted_device_status(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    cookie = request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME)
    device = resolve_trusted_device(db, context.user, cookie)
    if device and trusted_device_satisfies_mfa(device):
        return TrustedDeviceStatusResponse(trusted=True, requires_mfa=False, freshness_expires_at=trusted_device_fresh_until(device))
    return TrustedDeviceStatusResponse(trusted=device is not None, requires_mfa=True, freshness_expires_at=trusted_device_fresh_until(device) if device else None)


@api.post("/account-requests", response_model=AccountRequestDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
@ACCOUNT_REQUEST_RATE_LIMIT
def create_account_request(request: Request, payload: AccountRequestCreate, db: Session = Depends(get_db)):
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
    uri = provisioning_uri(context.user, method)
    return TotpEnrollmentStartResponse(
        secret=method.secret,
        provisioning_uri=uri,
        qr_code_svg_data_uri=provisioning_qr_svg_data_uri(uri),
    )


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


@api.get("/stt-configs", response_model=list[SttConfigDetail], responses=error_responses)
def list_stt_configs(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return [stt_config_response(config) for config in list_stt_configs_service(db, context.user, team_id=team_id)]


@api.get("/stt-configs/{config_id}", response_model=SttConfigDetail, responses=error_responses)
def get_stt_config(config_id: UUID, team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return stt_config_response(get_stt_config_service(db, context.user, config_id=config_id, team_id=team_id))


@api.post("/stt-configs/inspect", response_model=SttInspectResult, responses=error_responses)
def inspect_stt_config(payload: SttInspectRequest, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return inspect_stt_contract_service(db, context.user, payload)


@api.post("/stt-configs", response_model=SttConfigDetail, responses=error_responses)
def upsert_stt_config(payload: SttConfigUpsert, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return stt_config_response(upsert_stt_config_service(db, context.user, payload))


@api.delete("/stt-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_stt_config(config_id: UUID, team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    delete_stt_config_service(db, context.user, config_id=config_id, team_id=team_id)


@api.get("/stt-selection", response_model=SttSelectionDetail | None, responses=error_responses)
def get_stt_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_stt_selector), db: Session = Depends(get_db)):
    selection = get_team_stt_selection_service(db, context.user, team_id=team_id)
    return stt_selection_response(selection) if selection else None


@api.get("/stt-selection/options", response_model=list[SttConfigDetail], responses=error_responses)
def list_stt_selection_options(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_stt_selector), db: Session = Depends(get_db)):
    return [stt_config_response(config) for config in list_selectable_stt_configs_service(db, context.user, team_id=team_id)]


@api.post("/stt-selection", response_model=SttSelectionDetail, responses=error_responses)
def set_stt_selection(payload: SttSelectionUpsert, context: AuthenticatedContext = Depends(require_stt_selector), db: Session = Depends(get_db)):
    return stt_selection_response(set_team_stt_selection_service(db, context.user, payload))


@api.delete("/stt-selection", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_stt_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_stt_selector), db: Session = Depends(get_db)):
    clear_team_stt_selection_service(db, context.user, team_id=team_id)


@api.post("/users/{user_id}/suspend", response_model=UserDetail, responses=error_responses)
def suspend_user(user_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return suspend_user_service(db, context.user, user_id)


@api.post("/users/{user_id}/reactivate", response_model=UserDetail, responses=error_responses)
def reactivate_user(user_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return reactivate_user_service(db, context.user, user_id)


@api.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_user(user_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    delete_user_service(db, context.user, user_id)


@api.post("/transcripts", response_model=TranscriptDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_transcript(payload: TranscriptCreate, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return create_transcript_from_payload(db, context.user, payload)


@api.post("/transcripts/start", response_model=TranscriptDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def start_transcript(payload: TranscriptStart, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return start_transcript_service(db, context.user, payload)


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


@api.post("/transcripts/{transcript_id}/audio-chunks", response_model=TranscriptIngestionAccepted, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
def upload_transcript_audio_chunk(
    transcript_id: UUID,
    audio: UploadFile = File(...),
    chunk_sequence_no: int = Form(..., ge=1),
    declared_duration_seconds: float | None = Form(default=None, gt=0),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    audio_bytes = audio.file.read()
    transcript, job = queue_audio_chunk_ingestion(
        db,
        context.user,
        transcript_id=transcript_id,
        filename=audio.filename or "chunk.bin",
        chunk_sequence_no=chunk_sequence_no,
        declared_duration_seconds=declared_duration_seconds,
    )
    try:
        task_result = enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
    except Exception as exc:
        mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue live chunk ingestion")
        raise AppError(502, "ingestion_enqueue_failed", "Could not enqueue live chunk ingestion") from exc
    job = attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    refreshed_transcript = db.get(Transcript, transcript.id) or transcript
    return TranscriptIngestionAccepted(
        transcript=TranscriptDetail.model_validate(refreshed_transcript, from_attributes=True),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


@api.post("/transcripts/{transcript_id}/audio-file", response_model=TranscriptIngestionAccepted, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
def upload_transcript_audio_file(
    transcript_id: UUID,
    audio: UploadFile = File(...),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    audio_bytes = audio.file.read()
    transcript, job = queue_audio_file_ingestion(
        db,
        context.user,
        transcript_id=transcript_id,
        filename=audio.filename or "audio.bin",
    )
    try:
        task_result = enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
    except Exception as exc:
        mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion")
        raise AppError(502, "ingestion_enqueue_failed", "Could not enqueue file ingestion") from exc
    job = attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    refreshed_transcript = db.get(Transcript, transcript.id) or transcript
    return TranscriptIngestionAccepted(
        transcript=TranscriptDetail.model_validate(refreshed_transcript, from_attributes=True),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


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
@LOGIN_RATE_LIMIT
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    try:
        user = authenticate_user(db, email, password)
    except AppError as exc:
        return render_auth_page(request, db, message=exc.message, message_kind="error", status_code=exc.status_code)
    trusted_device = resolve_trusted_device(db, user, request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME))
    auth_level = login_auth_level(user, trusted_device)
    if trusted_device and auth_level is SessionAuthLevel.full:
        touch_trusted_device_seen(db, trusted_device)
    token = create_session(db, user, auth_level=auth_level)
    redirect_to = "/onboarding" if auth_level is SessionAuthLevel.onboarding else ("/mfa/challenge" if auth_level is SessionAuthLevel.pending_mfa else _post_login_redirect_for_user(user))
    response = RedirectResponse(url=redirect_to, status_code=status.HTTP_303_SEE_OTHER)
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
@ACCOUNT_REQUEST_RATE_LIMIT
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
    _set_session_cookie(response, token)
    if trusted_device_token:
        _set_trusted_device_cookie(response, trusted_device_token)
    return response


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
        totp_qr_svg_data_uri=provisioning_qr_svg_data_uri(provisioning_uri(refreshed_user, method)),
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
            totp_qr_svg_data_uri=provisioning_qr_svg_data_uri(provisioning_uri(context.user, method)) if method else None,
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
            "totp_qr_svg_data_uri": None,
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


@app.post("/home/stt-selection", response_class=HTMLResponse)
def home_set_stt_selection(
    request: Request,
    stt_config_id: str = Form(...),
    provider_model: str = Form(""),
    language: str = Form(""),
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
        return render_home(request, db, current_user=context.user, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/stt-selection/clear", response_class=HTMLResponse)
def home_clear_stt_selection(request: Request, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        clear_team_stt_selection_service(db, context.user)
    except AppError as exc:
        return render_home(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/users/{user_id}/suspend", response_class=HTMLResponse)
def home_suspend_user(request: Request, user_id: UUID, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        suspend_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_home(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/users/{user_id}/reactivate", response_class=HTMLResponse)
def home_reactivate_user(request: Request, user_id: UUID, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        reactivate_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_home(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/home", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/home/users/{user_id}/delete", response_class=HTMLResponse)
def home_delete_user(request: Request, user_id: UUID, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        delete_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_home(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
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
def admin_page(request: Request, team_id: str | None = None, stt_config_id: str | None = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(request, db, current_user=context.user, selected_team_id=team_id, selected_stt_config_id=stt_config_id)


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
    provider_model: str = Form(""),
    file_field_name: str = Form(""),
    language: str = Form(""),
    response_text_path: str = Form(""),
    extra_form_fields_json: str = Form(""),
    is_active: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
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
                bearer_token=bearer_token or None,
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
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url=f"/admin?team_id={team_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-configs/{config_id}/delete", response_class=HTMLResponse)
def admin_delete_stt_config(request: Request, config_id: UUID, team_id: str = Form(...), db: Session = Depends(get_db)):
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
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url=f"/admin?team_id={team_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-configs/inspect", response_class=HTMLResponse)
def admin_inspect_stt_config(
    request: Request,
    team_id: str = Form(...),
    label: str = Form(""),
    adapter_kind: str = Form(SttAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    openapi_path: str = Form(""),
    bearer_token: str = Form(""),
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
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=status_code)
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_stt_config_id=None,
        stt_inspection=inspection,
        stt_form_override={**stt_form_defaults(None, inspection), "label": label, "adapter_kind": inspection.adapter_kind.value},
        message="STT endpoint inspected. Review the inferred fields before saving.",
    )


@app.post("/admin/stt-selection", response_class=HTMLResponse)
def admin_set_stt_selection(
    request: Request,
    team_id: str = Form(...),
    stt_config_id: str = Form(...),
    provider_model: str = Form(""),
    language: str = Form(""),
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
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url=f"/admin?team_id={team_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-selection/clear", response_class=HTMLResponse)
def admin_clear_stt_selection(request: Request, team_id: str = Form(...), db: Session = Depends(get_db)):
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
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=status_code)
    return RedirectResponse(url=f"/admin?team_id={team_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/suspend", response_class=HTMLResponse)
def admin_suspend_user(request: Request, user_id: UUID, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        suspend_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/reactivate", response_class=HTMLResponse)
def admin_reactivate_user(request: Request, user_id: UUID, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        reactivate_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
def admin_delete_user(request: Request, user_id: UUID, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_user_service(db, context.user, user_id)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, message=exc.message, message_kind="error", status_code=exc.status_code)
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
