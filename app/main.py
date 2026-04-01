import json
import os
import secrets
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .cookie_security import should_set_secure_cookie
from .errors import AppError, app_error_handler, http_error_handler, rate_limit_error_handler, validation_error_handler
from .models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    LlmAdapterKind,
    PromptTemplate,
    QuickAction,
    SessionAuthLevel,
    SessionStatus,
    SttAdapterKind,
    TeamRole,
    TeamStatus,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    User,
    UserSession,
    UserStatus,
    transcript_expiry,
    utcnow,
)
from .schemas import (
    AccountRequestApprove,
    AccountRequestCreate,
    AccountRequestDetail,
    AccountRequestListItem,
    AccountRequestReject,
    CurrentUserResponse,
    EMIS_SECTION_KEYS,
    EMIS_SECTION_LABELS,
    ErrorResponse,
    GenerateFollowupRequest,
    GenerateQuickActionRequest,
    GenerateTemplateOutputRequest,
    LlmConfigDetail,
    LlmConfigInspectResult,
    LlmInspectRequest,
    LlmConfigUpsert,
    LlmSelectionDetail,
    LlmSelectionUpsert,
    LoginRequest,
    LoginResponse,
    MfaChallengeRequest,
    PasswordChangeRequest,
    PromptTemplateDetail,
    PromptTemplateUpsert,
    QuickActionDetail,
    QuickActionUpsert,
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
    TranscriptUpdate,
    TrustedDeviceStatusResponse,
    UserCreate,
    UserDetail,
    UserLlmPreferenceDetail,
    UserLlmPreferenceUpsert,
    UserListItem,
    GeneratedDocumentDetail,
    GeneratedDocumentRedactionDebugDetail,
    TranscribeWorkspaceDetail,
)
from .services.templates import (
    attach_generated_document_task_id as attach_generated_document_task_id_service,
    delete_personal_quick_action as delete_personal_quick_action_service,
    delete_personal_template as delete_personal_template_service,
    delete_team_quick_action as delete_team_quick_action_service,
    delete_team_template as delete_team_template_service,
    list_available_quick_actions_for_user as list_available_quick_actions_for_user_service,
    list_available_templates_for_user as list_available_templates_for_user_service,
    list_generated_documents_for_transcript as list_generated_documents_for_transcript_service,
    list_personal_quick_actions as list_personal_quick_actions_service,
    list_personal_templates as list_personal_templates_service,
    list_team_quick_actions as list_team_quick_actions_service,
    list_team_templates as list_team_templates_service,
    mark_generated_document_enqueue_failed as mark_generated_document_enqueue_failed_service,
    queue_quick_action_generation as queue_quick_action_generation_service,
    queue_document_generation_from_template as queue_document_generation_from_template_service,
    queue_followup_generation as queue_followup_generation_service,
    upsert_personal_quick_action as upsert_personal_quick_action_service,
    upsert_personal_template as upsert_personal_template_service,
    upsert_team_quick_action as upsert_team_quick_action_service,
    upsert_team_template as upsert_team_template_service,
)
from .services.llm import (
    active_team_llm_selection as active_team_llm_selection_service,
    clear_team_llm_selection as clear_team_llm_selection_service,
    clear_user_llm_preference as clear_user_llm_preference_service,
    delete_llm_config as delete_llm_config_service,
    get_team_llm_selection as get_team_llm_selection_service,
    get_user_llm_preference as get_user_llm_preference_service,
    inspect_llm_contract as inspect_llm_contract_service,
    list_llm_configs as list_llm_configs_service,
    list_selectable_llm_configs as list_selectable_llm_configs_service,
    resolve_user_llm as resolve_user_llm_service,
    set_team_llm_selection as set_team_llm_selection_service,
    set_user_llm_preference as set_user_llm_preference_service,
    upsert_llm_config as upsert_llm_config_service,
)
from .services.stt import (
    active_team_stt_selection as active_team_stt_selection_service,
    delete_stt_config as delete_stt_config_service,
    get_stt_config as get_stt_config_service,
    inspect_stt_contract as inspect_stt_contract_service,
    get_team_stt_selection as get_team_stt_selection_service,
    list_selectable_stt_configs as list_selectable_stt_configs_service,
    list_stt_configs as list_stt_configs_service,
    run_saved_stt_config_test as run_saved_stt_config_test_service,
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
    session_token_hash,
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
    can_create_new_session as can_create_new_session_service,
    can_switch_transcript_ingestion_mode as can_switch_transcript_ingestion_mode_service,
    create_transcript_from_payload,
    delete_transcripts as delete_transcripts_service,
    latest_ingestion_job_for_transcript as latest_ingestion_job_for_transcript_service,
    mark_ingestion_job_enqueue_failed,
    queue_audio_chunk_ingestion,
    queue_audio_file_ingestion,
    start_transcript as start_transcript_service,
    update_transcript as update_transcript_service,
    update_transcript_title as update_transcript_title_service,
)
from .tasks import enqueue_generated_document_job, enqueue_transcript_ingestion_job
from .services.audio import enforce_whole_file_upload_size


@dataclass(slots=True)
class AuthenticatedContext:
    user: User
    session: UserSession
    token: str


app = FastAPI(title="OpenScribe MVP")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
api = APIRouter(prefix="/api/v1")
CSRF_COOKIE_NAME = "openscribe_csrf"
LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}


def _local_only_dev_emails() -> set[str]:
    return {
        os.getenv("DEV_TEST_LEADER_EMAIL", "dev.leader@example.com").strip().lower(),
        os.getenv("DEV_TEST_USER_EMAIL", "dev.user@example.com").strip().lower(),
    }


def _is_local_address(value: str | None) -> bool:
    if not value:
        return False
    candidate = value.strip().strip("[]").split(":", 1)[0].lower()
    if candidate in LOCALHOST_NAMES:
        return True
    try:
        return ip_address(candidate).is_loopback
    except ValueError:
        return False


def _request_is_localhost_only(request: Request) -> bool:
    host_header = request.headers.get("host")
    origin_header = request.headers.get("origin")
    client_host = request.client.host if request.client else None
    request_host = request.url.hostname

    origin_host = None
    if origin_header:
        try:
            origin_host = origin_header.split("://", 1)[1].split("/", 1)[0]
        except IndexError:
            origin_host = origin_header

    candidates = [host_header, origin_host, client_host, request_host]
    meaningful_candidates = [candidate for candidate in candidates if candidate]
    if not meaningful_candidates:
        return False
    return all(_is_local_address(candidate) for candidate in meaningful_candidates)


def _enforce_localhost_only_dev_account(request: Request, user: User) -> None:
    if user.email.lower() not in _local_only_dev_emails():
        return
    if _request_is_localhost_only(request):
        return
    raise AppError(403, "forbidden", "Dev test accounts are available only from localhost")


def whole_file_upload_rate_limit_key(request: Request) -> str:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        hashed_token = session_token_hash(raw_token)
        session_factory = getattr(request.app.state, "db_session_factory", SessionLocal)
        try:
            with session_factory() as rate_limit_db:
                user_id = rate_limit_db.scalar(
                    select(UserSession.user_id)
                    .join(User, User.id == UserSession.user_id)
                    .where(
                        UserSession.session_token_hash == hashed_token,
                        UserSession.status == SessionStatus.active,
                        UserSession.expires_at > utcnow(),
                        User.status == UserStatus.active,
                    )
                )
        except Exception:
            user_id = None
        if user_id is not None:
            subject = f"user:{user_id}"
        else:
            subject = f"session:{hashed_token[:16]}"
    else:
        subject = f"ip:{get_remote_address(request)}"
    request.state.rate_limit_subject = subject
    return subject


async def require_browser_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    submitted_token = csrf_header
    if submitted_token is None:
        form = await request.form()
        submitted_token = form.get("_csrf_token")
    if not cookie_token or not submitted_token or submitted_token != cookie_token:
        raise AppError(403, "forbidden", "CSRF verification failed")


BrowserCsrf = Annotated[None, Depends(require_browser_csrf)]


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URL", "redis://localhost:6379/0"),
    headers_enabled=False,
)
app.state.limiter = limiter
app.state.db_session_factory = SessionLocal
LOGIN_RATE_LIMIT = limiter.shared_limit("5/5 minutes", scope="login")
MFA_RATE_LIMIT = limiter.shared_limit("10/10 minutes", scope="mfa_totp")
ACCOUNT_REQUEST_RATE_LIMIT = limiter.shared_limit("3/hour", scope="account_request")
WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT = limiter.shared_limit(
    "1/5 seconds",
    scope="whole_file_upload_burst",
    key_func=whole_file_upload_rate_limit_key,
)
WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT = limiter.shared_limit(
    "100/day",
    scope="whole_file_upload_daily",
    key_func=whole_file_upload_rate_limit_key,
)
LLM_GENERATION_BURST_RATE_LIMIT = limiter.shared_limit(
    "1/5 seconds",
    scope="llm_generation_burst",
    key_func=whole_file_upload_rate_limit_key,
)
LLM_GENERATION_DAILY_RATE_LIMIT = limiter.shared_limit(
    "100/day",
    scope="llm_generation_daily",
    key_func=whole_file_upload_rate_limit_key,
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_error_handler)


async def browser_not_found_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return await http_error_handler(request, exc)
    if request.method not in {"GET", "HEAD"}:
        return await http_error_handler(request, exc)
    session_factory = getattr(request.app.state, "db_session_factory", SessionLocal)
    with session_factory() as db:
        context = _current_context_optional(request, db)
    redirect_to = "/home" if context is not None else "/login"
    return RedirectResponse(url=redirect_to, status_code=status.HTTP_303_SEE_OTHER)


app.add_exception_handler(404, browser_not_found_handler)


@app.middleware("http")
async def ensure_csrf_cookie(request: Request, call_next):
    response = await call_next(request)
    if request.method in {"GET", "HEAD"} and not request.cookies.get(CSRF_COOKIE_NAME):
        secure_cookie = should_set_secure_cookie(
            request_url=str(request.url),
            forwarded_proto=request.headers.get("x-forwarded-proto"),
        )
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=secrets.token_urlsafe(32),
            httponly=False,
            secure=secure_cookie,
            samesite="lax",
            path="/",
        )
    return response


def _set_session_cookie(request: Request, response: JSONResponse | RedirectResponse, token: str) -> None:
    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 12,
    )


def _set_trusted_device_cookie(request: Request, response: JSONResponse | RedirectResponse, token: str) -> None:
    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    response.set_cookie(
        key=TRUSTED_DEVICE_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure_cookie,
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
    if user.email.lower() in _local_only_dev_emails() and not _request_is_localhost_only(request):
        revoke_session_by_token(db, token, reason="dev_account_non_local")
        return None
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


def require_local_dev_debug_context(
    request: Request,
    context: AuthenticatedContext = Depends(require_full_context),
) -> AuthenticatedContext:
    if context.user.email.lower() not in _local_only_dev_emails() or not _request_is_localhost_only(request):
        raise AppError(403, "forbidden", "Redaction debug is available only to localhost dev test accounts")
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


def require_llm_selector(context: AuthenticatedContext = Depends(require_full_context)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    raise AppError(403, "forbidden", "LLM selection access required")


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
    selected_llm_config_id: str | None = None,
    stt_inspection: SttInspectResult | None = None,
    stt_form_override: dict[str, object] | None = None,
    stt_test_result: dict[str, object] | None = None,
    llm_inspection: LlmConfigInspectResult | None = None,
    llm_form_override: dict[str, object] | None = None,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
    active_admin_tab: str | None = None,
    admin_page_route: str = "/admin",
    admin_return_view: str = "",
    template_name: str | None = None,
):
    selected_uuid = UUID(selected_team_id) if selected_team_id else None
    stt_configs = list_stt_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
    edit_stt_config = next((config for config in stt_configs if str(config.id) == selected_stt_config_id), None)
    stt_selection = get_team_stt_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    llm_configs = list_llm_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
    edit_llm_config = next((config for config in llm_configs if str(config.id) == selected_llm_config_id), None)
    llm_selection = get_team_llm_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    available_admin_tabs = {"providers", "directory", "requests"}
    resolved_admin_tab = active_admin_tab if active_admin_tab in available_admin_tabs else "providers"
    context = {
        "request": request,
        "current_user": current_user,
        "teams": list_teams_service(db),
        "users": list_users_service(db),
        "selected_team_id": selected_team_id,
        "selected_stt_config_id": selected_stt_config_id,
        "selected_llm_config_id": selected_llm_config_id,
        "stt_configs": stt_configs,
        "stt_config": edit_stt_config,
        "stt_selection": stt_selection,
        "stt_inspection": stt_inspection,
        "stt_form": stt_form_override or stt_form_defaults(edit_stt_config, None),
        "stt_test_result": stt_test_result,
        "selectable_stt_configs": list_selectable_stt_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else [],
        "llm_configs": llm_configs,
        "llm_config": edit_llm_config,
        "llm_selection": llm_selection,
        "llm_inspection": llm_inspection,
        "llm_form": llm_form_override or llm_form_defaults(edit_llm_config, None),
        "selectable_llm_configs": list_selectable_llm_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else [],
        "account_requests": list_manageable_account_requests_service(db, current_user),
        "team_statuses": list(TeamStatus),
        "team_roles": list(TeamRole),
        "user_statuses": list(UserStatus),
        "active_admin_tab": resolved_admin_tab,
        "admin_page_route": admin_page_route,
        "admin_return_view": admin_return_view,
        "message": message,
        "message_kind": message_kind,
    }
    resolved_template_name = template_name or "admin.html"
    return templates.TemplateResponse(request, resolved_template_name, context, status_code=status_code)


def _admin_page_route_from_return_view(return_view: str | None) -> str:
    return "/admin-restyled" if return_view == "restyled" else "/admin"


def _admin_return_view_value(return_view: str | None) -> str:
    return "restyled" if return_view == "restyled" else ""


def _admin_redirect_url(
    *,
    return_view: str | None,
    return_tab: str | None = None,
    team_id: str | None = None,
    stt_config_id: str | None = None,
    llm_config_id: str | None = None,
) -> str:
    base = _admin_page_route_from_return_view(return_view)
    params: dict[str, str] = {}
    if team_id:
        params["team_id"] = team_id
    if stt_config_id:
        params["stt_config_id"] = stt_config_id
    if llm_config_id:
        params["llm_config_id"] = llm_config_id
    if return_tab:
        params["tab"] = return_tab
    return f"{base}?{urlencode(params)}" if params else base


def render_home(
    request: Request,
    db: Session,
    *,
    current_user: User,
    selected_team_template_id: str | None = None,
    selected_personal_template_id: str | None = None,
    selected_team_quick_action_id: str | None = None,
    selected_personal_quick_action_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    queued_transcript_id: str | None = None,
    active_home_tab: str | None = None,
    active_home_modal: str | None = None,
    status_code: int = 200,
    template_name: str = "home.html",
    home_page_route: str = "/home",
    home_return_view: str = "",
    transcribe_return_tab: str | None = None,
):
    def _structured_section_prompt_map(version) -> dict[str, str]:
        if version is None or not version.config_json or not isinstance(version.config_json, dict):
            return {}
        sections = version.config_json.get("sections")
        if not isinstance(sections, list):
            return {}
        prompts: dict[str, str] = {}
        for section in sections:
            if not isinstance(section, dict):
                continue
            key = section.get("section_key")
            instruction = section.get("instruction")
            if isinstance(key, str) and isinstance(instruction, str):
                prompts[key] = instruction
        return prompts

    is_manager = current_user.is_system_admin or current_user.team_role is TeamRole.leader
    stt_selection = None
    if current_user.team_id is not None:
        try:
            stt_selection = active_team_stt_selection_service(db, team_id=current_user.team_id)
        except AppError:
            stt_selection = get_team_stt_selection_service(db, current_user) if is_manager else None
    selectable_stt_configs = list_selectable_stt_configs_service(db, current_user) if is_manager else []
    llm_selection = None
    if current_user.team_id is not None:
        try:
            llm_selection = active_team_llm_selection_service(db, team_id=current_user.team_id)
        except AppError:
            llm_selection = get_team_llm_selection_service(db, current_user) if is_manager else None
    selectable_llm_configs = list_selectable_llm_configs_service(db, current_user) if is_manager else []
    user_llm_preference = None
    resolved_user_llm_model = None
    if not current_user.is_system_admin and current_user.team_id is not None:
        try:
            _, _, resolved_user_llm_model, user_llm_preference = resolve_user_llm_service(db, current_user)
        except AppError:
            user_llm_preference = get_user_llm_preference_service(db, current_user)
    team_leader_email = None
    if current_user.team_id is not None:
        team_leader_email = db.scalar(
            select(User.email)
            .where(
                User.team_id == current_user.team_id,
                User.team_role == TeamRole.leader,
                User.is_system_admin.is_(False),
                User.status == UserStatus.active,
            )
            .order_by(User.created_at.asc())
        )
    team_templates = list_team_templates_service(db, current_user) if is_manager else []
    personal_templates = list_personal_templates_service(db, current_user) if not current_user.is_system_admin and current_user.team_id is not None else []
    team_quick_actions = list_team_quick_actions_service(db, current_user) if is_manager else []
    personal_quick_actions = list_personal_quick_actions_service(db, current_user) if not current_user.is_system_admin and current_user.team_id is not None else []
    selected_team_template = next((template for template in team_templates if str(template.id) == selected_team_template_id), None)
    selected_personal_template = next((template for template in personal_templates if str(template.id) == selected_personal_template_id), None)
    selected_team_quick_action = next((quick_action for quick_action in team_quick_actions if str(quick_action.id) == selected_team_quick_action_id), None)
    selected_personal_quick_action = next((quick_action for quick_action in personal_quick_actions if str(quick_action.id) == selected_personal_quick_action_id), None)
    team_template_latest_version = _latest_template_version(selected_team_template) if selected_team_template is not None else None
    personal_template_latest_version = _latest_template_version(selected_personal_template) if selected_personal_template is not None else None
    team_quick_action_latest_version = _latest_quick_action_version(selected_team_quick_action) if selected_team_quick_action is not None else None
    personal_quick_action_latest_version = _latest_quick_action_version(selected_personal_quick_action) if selected_personal_quick_action is not None else None
    available_home_tabs = ["overview"]
    if not current_user.is_system_admin and current_user.team_id is not None:
        available_home_tabs.extend(["templates", "quick-actions"])
    if is_manager:
        available_home_tabs.extend(["team-management", "account-requests"])

    if active_home_tab in available_home_tabs:
        resolved_home_tab = active_home_tab
    elif selected_team_template or selected_personal_template:
        resolved_home_tab = "templates" if "templates" in available_home_tabs else "overview"
    elif selected_team_quick_action or selected_personal_quick_action:
        resolved_home_tab = "quick-actions" if "quick-actions" in available_home_tabs else "overview"
    else:
        resolved_home_tab = "overview"

    allowed_home_modals = {
        "personal-template",
        "team-template",
        "personal-quick-action",
        "team-quick-action",
    }
    resolved_home_modal = active_home_modal if active_home_modal in allowed_home_modals else None

    context = {
        "request": request,
        "current_user": current_user,
        "is_manager": is_manager,
        "manageable_users": list_manageable_users_service(db, current_user) if is_manager else [],
        "account_requests": list_manageable_account_requests_service(db, current_user) if is_manager else [],
        "stt_selection": stt_selection,
        "selectable_stt_configs": selectable_stt_configs,
        "llm_selection": llm_selection,
        "selectable_llm_configs": selectable_llm_configs,
        "user_llm_preference": user_llm_preference,
        "resolved_user_llm_model": resolved_user_llm_model,
        "team_leader_email": team_leader_email,
        "team_templates": team_templates,
        "personal_templates": personal_templates,
        "team_quick_actions": team_quick_actions,
        "personal_quick_actions": personal_quick_actions,
        "selected_team_template_id": selected_team_template_id,
        "selected_personal_template_id": selected_personal_template_id,
        "selected_team_quick_action_id": selected_team_quick_action_id,
        "selected_personal_quick_action_id": selected_personal_quick_action_id,
        "active_home_tab": resolved_home_tab,
        "active_home_modal": resolved_home_modal,
        "home_page_route": home_page_route,
        "home_return_view": home_return_view,
        "team_template": selected_team_template,
        "personal_template": selected_personal_template,
        "team_quick_action": selected_team_quick_action,
        "personal_quick_action": selected_personal_quick_action,
        "team_template_latest_version": team_template_latest_version,
        "personal_template_latest_version": personal_template_latest_version,
        "team_template_section_prompts": _structured_section_prompt_map(team_template_latest_version),
        "personal_template_section_prompts": _structured_section_prompt_map(personal_template_latest_version),
        "team_quick_action_latest_version": team_quick_action_latest_version,
        "personal_quick_action_latest_version": personal_quick_action_latest_version,
        "emis_sections": [{"key": key, "label": EMIS_SECTION_LABELS[key]} for key in EMIS_SECTION_KEYS],
        "message": message,
        "message_kind": message_kind,
        "queued_transcript_id": queued_transcript_id,
        "transcribe_return_tab": transcribe_return_tab,
    }
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def _home_template_name_from_return_view(return_view: str | None) -> str:
    return "home.html"


def _home_page_route_from_return_view(return_view: str | None) -> str:
    return "/home-restyled" if return_view == "restyled" else "/home"


def _home_return_view_value(return_view: str | None) -> str:
    if return_view == "restyled":
        return "restyled"
    if return_view == "transcribe":
        return "transcribe"
    return ""


def _home_redirect_url(
    *,
    return_view: str | None,
    return_tab: str | None = None,
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
) -> str:
    if return_view == "transcribe":
        params: dict[str, str] = {}
        if queued_transcript_id:
            params["transcript_id"] = queued_transcript_id
        params["tab"] = transcribe_tab or ("followups" if return_tab == "quick-actions" else "output")
        return f"/transcribe?{urlencode(params)}" if params else "/transcribe"
    base = "/home-restyled" if return_view == "restyled" else "/home"
    if return_tab:
        return f"{base}?tab={return_tab}"
    return base


def _active_structured_context_map(transcript: Transcript | None) -> dict[str, list[str]]:
    if transcript is None or not isinstance(transcript.structured_context_json, dict):
        return {}
    if transcript.structured_context_json.get("profile") != "emis":
        return {}
    sections = transcript.structured_context_json.get("sections")
    if not isinstance(sections, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for section_key, value in sections.items():
        if not isinstance(section_key, str):
            continue
        if isinstance(value, list):
            lines = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        elif isinstance(value, str) and value.strip():
            lines = [value.strip()]
        else:
            lines = []
        if lines:
            normalized[section_key] = lines
    return normalized


def _document_section_lines(document: GeneratedDocument | None) -> dict[str, list[str]]:
    if document is None:
        return {}
    line_map: dict[str, list[str]] = {}
    for section in getattr(document, "sections", []):
        raw_text = section.edited_text_encrypted or ""
        lines = [line for line in raw_text.splitlines() if line.strip()]
        line_map[str(section.id)] = lines or ([raw_text.strip()] if raw_text.strip() else [])
    return line_map


def _resolve_transcribe_workspace(
    db: Session,
    *,
    current_user: User,
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    request: Request | None = None,
) -> dict[str, object]:
    recent_transcripts = list(
        db.scalars(
            select(Transcript)
            .where(Transcript.owner_user_id == current_user.id)
            .order_by(Transcript.created_at.desc())
            .limit(12)
        )
    )
    active_transcript = None
    requested_transcript_id = queued_transcript_id or transcript_id
    if requested_transcript_id:
        try:
            selected_id = UUID(requested_transcript_id)
        except ValueError:
            selected_id = None
        if selected_id is not None:
            candidate = db.get(Transcript, selected_id)
            if candidate is not None and candidate.owner_user_id == current_user.id:
                active_transcript = candidate
    if active_transcript is None and recent_transcripts:
        active_transcript = recent_transcripts[0]
    active_transcript_latest_job = (
        latest_ingestion_job_for_transcript_service(db, transcript_id=active_transcript.id)
        if active_transcript is not None
        else None
    )

    team_leader_email = (
        db.scalar(
            select(User.email)
            .where(
                User.team_id == current_user.team_id,
                User.team_role == TeamRole.leader,
                User.is_system_admin.is_(False),
                User.status == UserStatus.active,
            )
            .order_by(User.created_at.asc())
        )
        if current_user.team_id is not None
        else None
    )
    stt_selection = None
    stt_available = False
    stt_status_message = None
    llm_selection = None
    user_llm_preference = None
    resolved_user_llm_model = None
    if current_user.team_id is not None:
        try:
            stt_selection = active_team_stt_selection_service(db, team_id=current_user.team_id)
        except AppError:
            stt_selection = None
        if stt_selection is None:
            stt_status_message = _missing_stt_selection_message(team_leader_email=team_leader_email)
        else:
            stt_available = True
        try:
            llm_selection = active_team_llm_selection_service(db, team_id=current_user.team_id)
        except AppError:
            llm_selection = None
    if not current_user.is_system_admin and current_user.team_id is not None:
        try:
            _, _, resolved_user_llm_model, user_llm_preference = resolve_user_llm_service(db, current_user)
        except AppError:
            user_llm_preference = get_user_llm_preference_service(db, current_user)
    can_create_new_session, new_session_block_message = can_create_new_session_service(db, current_user)
    can_switch_to_whole_file = False
    switch_mode_block_message = None
    if active_transcript is not None:
        _, can_switch_to_whole_file, whole_file_message = can_switch_transcript_ingestion_mode_service(
            db,
            current_user,
            transcript_id=active_transcript.id,
            target_mode=TranscriptIngestionMode.whole_file,
        )
        if not can_switch_to_whole_file and whole_file_message:
            switch_mode_block_message = whole_file_message
    available_templates = list_available_templates_for_user_service(db, current_user) if current_user.team_id is not None and not current_user.is_system_admin else []
    available_quick_actions = list_available_quick_actions_for_user_service(db, current_user) if current_user.team_id is not None and not current_user.is_system_admin else []
    generated_documents = (
        list_generated_documents_for_transcript_service(db, current_user, transcript_id=active_transcript.id)
        if active_transcript is not None and not current_user.is_system_admin
        else []
    )
    note_documents = [document for document in generated_documents if document.generator_type is GeneratedDocumentGeneratorType.template]
    followup_documents = [
        document
        for document in generated_documents
        if document.generator_type in {GeneratedDocumentGeneratorType.followup, GeneratedDocumentGeneratorType.quick_action}
    ]
    latest_generated_document = note_documents[0] if note_documents else None
    latest_followup_document = followup_documents[0] if followup_documents else None
    show_redaction_debug = bool(
        request is not None
        and current_user.email.lower() in _local_only_dev_emails()
        and _request_is_localhost_only(request)
    )
    active_structured_context = _active_structured_context_map(active_transcript)

    return {
        "recent_transcripts": recent_transcripts,
        "active_transcript": active_transcript,
        "active_transcript_latest_job": active_transcript_latest_job,
        "active_transcript_id": str(active_transcript.id) if active_transcript is not None else None,
        "stt_selection": stt_selection,
        "stt_available": stt_available,
        "stt_status_message": stt_status_message,
        "llm_selection": llm_selection,
        "user_llm_preference": user_llm_preference,
        "resolved_user_llm_model": resolved_user_llm_model,
        "queued_transcript_id": queued_transcript_id,
        "can_create_new_session": can_create_new_session,
        "new_session_block_message": new_session_block_message,
        "can_switch_to_whole_file": can_switch_to_whole_file,
        "switch_mode_block_message": switch_mode_block_message,
        "available_templates": available_templates,
        "available_quick_actions": available_quick_actions,
        "generated_documents": generated_documents,
        "note_documents": note_documents,
        "followup_documents": followup_documents,
        "latest_generated_document": latest_generated_document,
        "latest_generated_document_section_lines": _document_section_lines(latest_generated_document),
        "latest_followup_document": latest_followup_document,
        "active_structured_context": active_structured_context,
        "show_redaction_debug": show_redaction_debug,
        "emis_sections": [{"key": key, "label": EMIS_SECTION_LABELS[key]} for key in EMIS_SECTION_KEYS],
        "team_leader_email": team_leader_email,
    }


def render_transcribe(
    request: Request,
    db: Session,
    *,
    current_user: User,
    template_name: str = "transcribe.html",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    active_tab: str = "transcript",
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    workspace = _resolve_transcribe_workspace(
        db,
        current_user=current_user,
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        request=request,
    )
    workspace_endpoint = "/api/v1/transcribe/workspace"
    active_transcript = workspace.get("active_transcript")
    if isinstance(active_transcript, Transcript):
        workspace_endpoint = f"{workspace_endpoint}?transcript_id={active_transcript.id}"
    context = {
        "request": request,
        "current_user": current_user,
        **workspace,
        "workspace_endpoint": workspace_endpoint,
        "transcribe_route_base": request.url.path if request is not None else "/transcribe",
        "message": message,
        "message_kind": message_kind,
        "active_tab": active_tab if active_tab in {"transcript", "output", "followups"} else "transcript",
    }
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def _missing_stt_selection_message(*, team_leader_email: str | None) -> str:
    if team_leader_email:
        return f"No STT configured, please ask your team leader {team_leader_email}"
    return "No STT configured, please ask your team leader."


def _home_redirect(*, message: str, message_kind: str, queued_transcript_id: UUID | None = None) -> RedirectResponse:
    params: dict[str, str] = {"message": message, "message_kind": message_kind}
    if queued_transcript_id is not None:
        params["queued_transcript_id"] = str(queued_transcript_id)
    return RedirectResponse(url=f"/home?{urlencode(params)}", status_code=status.HTTP_303_SEE_OTHER)


def _transcribe_redirect(*, message: str, message_kind: str, queued_transcript_id: UUID | None = None) -> RedirectResponse:
    params: dict[str, str] = {"message": message, "message_kind": message_kind}
    if queued_transcript_id is not None:
        params["queued_transcript_id"] = str(queued_transcript_id)
    return RedirectResponse(url=f"/transcribe?{urlencode(params)}", status_code=status.HTTP_303_SEE_OTHER)


def _structured_template_config_from_form(*, section_values: dict[str, str]) -> dict | None:
    sections: list[dict[str, object]] = []
    for index, section_key in enumerate(EMIS_SECTION_KEYS, start=1):
        instruction = (section_values.get(section_key) or "").strip()
        if instruction:
            sections.append(
                {
                    "section_key": section_key,
                    "section_label": EMIS_SECTION_LABELS[section_key],
                    "instruction": instruction,
                    "section_order": index,
                }
            )
    if not sections:
        return None
    return {"profile": "emis", "sections": sections}


def _structured_context_from_form(*, section_values: dict[str, str]) -> dict[str, list[str]] | None:
    clean: dict[str, list[str]] = {}
    for section_key, raw_value in section_values.items():
        value = (raw_value or "").strip()
        if not value:
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = [value]
        if isinstance(parsed, list):
            lines = [str(item).strip() for item in parsed if isinstance(item, str) and item.strip()]
        elif isinstance(parsed, str) and parsed.strip():
            lines = [parsed.strip()]
        else:
            lines = []
        if lines:
            clean[section_key] = lines
    return clean or None


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


def transcript_detail_response(db: Session, transcript: Transcript) -> TranscriptDetail:
    latest_job = latest_ingestion_job_for_transcript_service(db, transcript_id=transcript.id)
    payload = TranscriptDetail.model_validate(transcript, from_attributes=True).model_dump()
    if latest_job is not None:
        payload["latest_ingestion_job_status"] = latest_job.status
        payload["latest_ingestion_error_code"] = latest_job.error_code
        payload["latest_ingestion_error_message"] = latest_job.error_message
    return TranscriptDetail.model_validate(payload)


def transcribe_workspace_response(db: Session, workspace: dict[str, object]) -> TranscribeWorkspaceDetail:
    active_transcript = workspace.get("active_transcript")
    recent_transcripts = workspace.get("recent_transcripts") or []
    generated_documents = workspace.get("generated_documents") or []
    available_templates = workspace.get("available_templates") or []
    available_quick_actions = workspace.get("available_quick_actions") or []
    return TranscribeWorkspaceDetail(
        recent_transcripts=[TranscriptListItem.model_validate(transcript, from_attributes=True) for transcript in recent_transcripts],
        active_transcript=transcript_detail_response(db, active_transcript) if isinstance(active_transcript, Transcript) else None,
        generated_documents=[generated_document_response(document) for document in generated_documents],
        available_templates=[template_response(template) for template in available_templates],
        available_quick_actions=[quick_action_response(quick_action) for quick_action in available_quick_actions],
        active_structured_context=dict(workspace.get("active_structured_context") or {}),
        stt_selected=bool(workspace.get("stt_selection")),
        stt_available=bool(workspace.get("stt_available")),
        stt_status_message=workspace.get("stt_status_message"),
        llm_selected=bool(workspace.get("llm_selection")),
        resolved_user_llm_model=workspace.get("resolved_user_llm_model"),
        can_create_new_session=bool(workspace.get("can_create_new_session")),
        new_session_block_message=workspace.get("new_session_block_message"),
        can_switch_to_whole_file=bool(workspace.get("can_switch_to_whole_file")),
        switch_mode_block_message=workspace.get("switch_mode_block_message"),
        team_leader_email=workspace.get("team_leader_email"),
    )


def stt_selection_response(selection) -> SttSelectionDetail:
    config = selection.config
    available_models_json = list(config.available_models_json or [])
    resolved_model_name = selection.model_name_override or config.model_name
    if available_models_json and resolved_model_name not in available_models_json:
        resolved_model_name = available_models_json[0]
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
        available_models_json=available_models_json,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def llm_config_response(config) -> LlmConfigDetail:
    return LlmConfigDetail(
        id=config.id,
        team_id=config.team_id,
        label=config.label,
        adapter_kind=config.adapter_kind,
        base_url=config.base_url,
        auth_mode=config.auth_mode,
        model_name=config.model_name,
        available_models_json=list(config.available_models_json or []),
        is_active=config.is_active,
        has_secret=bool(config.vault_secret_ref),
        created_by_user_id=config.created_by_user_id,
        updated_by_user_id=config.updated_by_user_id,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def llm_selection_response(selection) -> LlmSelectionDetail:
    config = selection.config
    resolved_model_name = selection.model_name_override or config.model_name
    allowed_models_json = list(selection.allowed_models_json or config.available_models_json or [])
    if resolved_model_name and allowed_models_json and resolved_model_name not in allowed_models_json:
        resolved_model_name = allowed_models_json[0]
    return LlmSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        llm_config_id=selection.llm_config_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_config_label=config.label,
        selected_config_adapter_kind=config.adapter_kind,
        selected_config_base_url=config.base_url,
        provider_available_models_json=list(config.available_models_json or []),
        allowed_models_json=allowed_models_json,
        model_name_override=selection.model_name_override,
        resolved_model_name=resolved_model_name,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def user_llm_preference_response(preference, *, resolved_model_name: str | None, allowed_models: list[str]) -> UserLlmPreferenceDetail:
    return UserLlmPreferenceDetail(
        id=preference.id,
        user_id=preference.user_id,
        preferred_model_name=preference.preferred_model_name,
        resolved_model_name=resolved_model_name,
        allowed_models_json=allowed_models,
        created_at=preference.created_at,
        updated_at=preference.updated_at,
    )


def _latest_template_version(template: PromptTemplate):
    return max(template.versions, key=lambda version: version.version_no)


def _latest_quick_action_version(quick_action: QuickAction):
    return max(quick_action.versions, key=lambda version: version.version_no)


def template_response(template: PromptTemplate) -> PromptTemplateDetail:
    latest_version = _latest_template_version(template)
    return PromptTemplateDetail(
        id=template.id,
        scope=template.scope,
        owner_user_id=template.owner_user_id,
        team_id=template.team_id,
        name=template.name,
        description=template.description,
        is_active=template.is_active,
        created_by_user_id=template.created_by_user_id,
        created_at=template.created_at,
        updated_at=template.updated_at,
        latest_version={
            "id": latest_version.id,
            "version_no": latest_version.version_no,
            "mode": latest_version.mode,
            "prompt_text": latest_version.prompt_text,
            "config_json": latest_version.config_json,
            "created_by_user_id": latest_version.created_by_user_id,
            "created_at": latest_version.created_at,
        },
    )


def quick_action_response(quick_action: QuickAction) -> QuickActionDetail:
    latest_version = _latest_quick_action_version(quick_action)
    return QuickActionDetail(
        id=quick_action.id,
        scope=quick_action.scope,
        owner_user_id=quick_action.owner_user_id,
        team_id=quick_action.team_id,
        name=quick_action.name,
        description=quick_action.description,
        is_active=quick_action.is_active,
        created_by_user_id=quick_action.created_by_user_id,
        created_at=quick_action.created_at,
        updated_at=quick_action.updated_at,
        latest_version={
            "id": latest_version.id,
            "version_no": latest_version.version_no,
            "mode": latest_version.mode,
            "prompt_text": latest_version.prompt_text,
            "created_by_user_id": latest_version.created_by_user_id,
            "created_at": latest_version.created_at,
        },
    )


def generated_document_response(document: GeneratedDocument) -> GeneratedDocumentDetail:
    return GeneratedDocumentDetail.model_validate(document, from_attributes=True)


def generated_document_redaction_debug_response(document: GeneratedDocument) -> GeneratedDocumentRedactionDebugDetail:
    if document.redaction_run is None:
        raise AppError(404, "not_found", "No redaction run is linked to this generated document")
    redaction_run = document.redaction_run
    entities = sorted(redaction_run.entities, key=lambda entity: entity.entity_order)
    return GeneratedDocumentRedactionDebugDetail(
        generated_document_id=document.id,
        redaction_run_id=redaction_run.id,
        transcript_version_id=redaction_run.transcript_version_id,
        status=redaction_run.status.value,
        api_provider=redaction_run.api_provider,
        api_model_or_version=redaction_run.api_model_or_version,
        entity_count=redaction_run.entity_count,
        mapping_hash=redaction_run.mapping_hash,
        redacted_text=redaction_run.redacted_text_encrypted or "",
        failed_provider_output_redacted_text=document.failed_provider_output_redacted_encrypted,
        entities=[
            {
                "entity_order": entity.entity_order,
                "entity_type": entity.entity_type,
                "placeholder": entity.placeholder,
                "occurrence_count": entity.occurrence_count,
            }
            for entity in entities
        ],
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
            "preserved_bearer_token": "",
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
            "preserved_bearer_token": "",
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
        "preserved_bearer_token": "",
    }


def llm_form_defaults(config, inspection: LlmConfigInspectResult | None) -> dict[str, object]:
    if inspection is not None:
        return {
            "config_id": "",
            "label": "",
            "adapter_kind": inspection.adapter_kind.value,
            "base_url": inspection.base_url,
            "model_name": inspection.model_name or "",
            "available_models": inspection.available_models,
            "available_model_options": [option.model_dump(mode="json") for option in inspection.available_model_options],
            "is_active": True,
            "preserved_bearer_token": "",
        }
    if config is not None:
        return {
            "config_id": str(config.id),
            "label": config.label,
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "model_name": config.model_name or "",
            "available_models": list(config.available_models_json or []),
            "available_model_options": [
                {"id": model, "source": "saved", "label": f"{model} (saved)"}
                for model in (config.available_models_json or [])
            ],
            "is_active": config.is_active,
            "preserved_bearer_token": "",
        }
    return {
        "config_id": "",
        "label": "",
        "adapter_kind": LlmAdapterKind.openai_chat.value,
        "base_url": "https://api.openai.com/v1",
        "model_name": "",
        "available_models": [],
        "available_model_options": [],
        "is_active": True,
        "preserved_bearer_token": "",
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
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@api.post("/auth/login", response_model=LoginResponse, responses=error_responses)
@LOGIN_RATE_LIMIT
def api_login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.email, payload.password)
    _enforce_localhost_only_dev_account(request, user)
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
    _set_session_cookie(request, response, token)
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
    _set_session_cookie(request, response, token)
    if trusted_device_token:
        _set_trusted_device_cookie(request, response, trusted_device_token)
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
    _set_session_cookie(request, response, token)
    return response


@api.post("/onboarding/skip-recovery-codes", response_model=LoginResponse, responses=error_responses)
def api_skip_recovery_codes(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = skip_recovery_codes(db, context.user)
    token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    response = JSONResponse(
        LoginResponse(authenticated=True, auth_level=determine_auth_level(user), redirect_to="/admin" if user.is_system_admin else "/home").model_dump(mode="json")
    )
    _set_session_cookie(request, response, token)
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


@api.get("/llm-configs", response_model=list[LlmConfigDetail], responses=error_responses)
def list_llm_configs(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return [llm_config_response(config) for config in list_llm_configs_service(db, context.user, team_id=team_id)]


@api.post("/llm-configs/inspect", response_model=LlmConfigInspectResult, responses=error_responses)
def inspect_llm_config(payload: LlmInspectRequest, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return inspect_llm_contract_service(db, context.user, payload)


@api.post("/llm-configs", response_model=LlmConfigDetail, responses=error_responses)
def upsert_llm_config(payload: LlmConfigUpsert, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return llm_config_response(upsert_llm_config_service(db, context.user, payload))


@api.delete("/llm-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_llm_config(config_id: UUID, team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    delete_llm_config_service(db, context.user, config_id=config_id, team_id=team_id)


@api.get("/llm-selection", response_model=LlmSelectionDetail | None, responses=error_responses)
def get_llm_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    selection = get_team_llm_selection_service(db, context.user, team_id=team_id)
    return llm_selection_response(selection) if selection else None


@api.get("/llm-selection/options", response_model=list[LlmConfigDetail], responses=error_responses)
def list_llm_selection_options(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    return [llm_config_response(config) for config in list_selectable_llm_configs_service(db, context.user, team_id=team_id)]


@api.post("/llm-selection", response_model=LlmSelectionDetail, responses=error_responses)
def set_llm_selection(payload: LlmSelectionUpsert, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    return llm_selection_response(set_team_llm_selection_service(db, context.user, payload))


@api.delete("/llm-selection", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_llm_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    clear_team_llm_selection_service(db, context.user, team_id=team_id)


@api.get("/llm-preference", response_model=UserLlmPreferenceDetail | None, responses=error_responses)
def get_llm_preference(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    preference = get_user_llm_preference_service(db, context.user)
    if preference is None:
        return None
    selection, config, resolved_model_name, _ = resolve_user_llm_service(db, context.user)
    allowed_models = list(selection.allowed_models_json or config.available_models_json or [])
    return user_llm_preference_response(preference, resolved_model_name=resolved_model_name, allowed_models=allowed_models)


@api.post("/llm-preference", response_model=UserLlmPreferenceDetail, responses=error_responses)
def set_llm_preference(payload: UserLlmPreferenceUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    preference = set_user_llm_preference_service(db, context.user, payload)
    selection, config, resolved_model_name, _ = resolve_user_llm_service(db, context.user)
    allowed_models = list(selection.allowed_models_json or config.available_models_json or [])
    return user_llm_preference_response(preference, resolved_model_name=resolved_model_name, allowed_models=allowed_models)


@api.delete("/llm-preference", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_llm_preference(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    clear_user_llm_preference_service(db, context.user)


@api.get("/templates/available", response_model=list[PromptTemplateDetail], responses=error_responses)
def list_available_templates(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [template_response(template) for template in list_available_templates_for_user_service(db, context.user)]


@api.get("/templates/team", response_model=list[PromptTemplateDetail], responses=error_responses)
def list_team_templates(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return [template_response(template) for template in list_team_templates_service(db, context.user)]


@api.post("/templates/team", response_model=PromptTemplateDetail, responses=error_responses)
def upsert_team_template(payload: PromptTemplateUpsert, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return template_response(upsert_team_template_service(db, context.user, payload))


@api.delete("/templates/team/{template_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_team_template(template_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    delete_team_template_service(db, context.user, template_id=template_id)


@api.get("/templates/personal", response_model=list[PromptTemplateDetail], responses=error_responses)
def list_personal_templates(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [template_response(template) for template in list_personal_templates_service(db, context.user)]


@api.post("/templates/personal", response_model=PromptTemplateDetail, responses=error_responses)
def upsert_personal_template(payload: PromptTemplateUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return template_response(upsert_personal_template_service(db, context.user, payload))


@api.delete("/templates/personal/{template_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_personal_template(template_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    delete_personal_template_service(db, context.user, template_id=template_id)


@api.get("/quick-actions/available", response_model=list[QuickActionDetail], responses=error_responses)
def list_available_quick_actions(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [quick_action_response(quick_action) for quick_action in list_available_quick_actions_for_user_service(db, context.user)]


@api.get("/quick-actions/team", response_model=list[QuickActionDetail], responses=error_responses)
def list_team_quick_actions(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return [quick_action_response(quick_action) for quick_action in list_team_quick_actions_service(db, context.user)]


@api.post("/quick-actions/team", response_model=QuickActionDetail, responses=error_responses)
def upsert_team_quick_action(payload: QuickActionUpsert, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return quick_action_response(upsert_team_quick_action_service(db, context.user, payload))


@api.delete("/quick-actions/team/{quick_action_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_team_quick_action(quick_action_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    delete_team_quick_action_service(db, context.user, quick_action_id=quick_action_id)


@api.get("/quick-actions/personal", response_model=list[QuickActionDetail], responses=error_responses)
def list_personal_quick_actions(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [quick_action_response(quick_action) for quick_action in list_personal_quick_actions_service(db, context.user)]


@api.post("/quick-actions/personal", response_model=QuickActionDetail, responses=error_responses)
def upsert_personal_quick_action(payload: QuickActionUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return quick_action_response(upsert_personal_quick_action_service(db, context.user, payload))


@api.delete("/quick-actions/personal/{quick_action_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_personal_quick_action(quick_action_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    delete_personal_quick_action_service(db, context.user, quick_action_id=quick_action_id)


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
    transcript = create_transcript_from_payload(db, context.user, payload)
    return transcript_detail_response(db, transcript)


@api.post("/transcripts/start", response_model=TranscriptDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def start_transcript(payload: TranscriptStart, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = start_transcript_service(db, context.user, payload)
    return transcript_detail_response(db, transcript)


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
    return transcript_detail_response(db, transcript)


@api.patch("/transcripts/{transcript_id}", response_model=TranscriptDetail, responses=error_responses)
def update_transcript(
    transcript_id: UUID,
    payload: TranscriptUpdate,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    transcript = update_transcript_service(
        db,
        context.user,
        transcript_id=transcript_id,
        title=payload.title,
        ingestion_mode=payload.ingestion_mode,
        structured_context_json=payload.structured_context_json,
    )
    return transcript_detail_response(db, transcript)


@api.delete("/transcripts/{transcript_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_transcript(
    transcript_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    delete_transcripts_service(db, context.user, transcript_ids=[transcript_id])


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
        transcript=transcript_detail_response(db, refreshed_transcript),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


@api.post("/transcripts/{transcript_id}/audio-file", response_model=TranscriptIngestionAccepted, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def upload_transcript_audio_file(
    request: Request,
    transcript_id: UUID,
    audio: UploadFile = File(...),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    audio_bytes = audio.file.read()
    enforce_whole_file_upload_size(audio_bytes=audio_bytes)
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
        transcript=transcript_detail_response(db, refreshed_transcript),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


@api.get("/transcripts/{transcript_id}", response_model=TranscriptDetail, responses=error_responses)
def get_transcript_detail(transcript_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != context.user.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    return transcript_detail_response(db, transcript)


@api.get("/transcribe/workspace", response_model=TranscribeWorkspaceDetail, responses=error_responses)
def get_transcribe_workspace(
    transcript_id: UUID | None = None,
    queued_transcript_id: UUID | None = None,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    workspace = _resolve_transcribe_workspace(
        db,
        current_user=context.user,
        transcript_id=str(transcript_id) if transcript_id is not None else None,
        queued_transcript_id=str(queued_transcript_id) if queued_transcript_id is not None else None,
    )
    return transcribe_workspace_response(db, workspace)


@api.get("/transcripts/{transcript_id}/generated-documents", response_model=list[GeneratedDocumentDetail], responses=error_responses)
def list_generated_documents_for_transcript(transcript_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [generated_document_response(document) for document in list_generated_documents_for_transcript_service(db, context.user, transcript_id=transcript_id)]


@api.get("/generated-documents/{generated_document_id}/redaction-debug", response_model=GeneratedDocumentRedactionDebugDetail, responses=error_responses)
def get_generated_document_redaction_debug(
    generated_document_id: UUID,
    context: AuthenticatedContext = Depends(require_local_dev_debug_context),
    db: Session = Depends(get_db),
):
    document = db.get(GeneratedDocument, generated_document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(generated_document_id)})
    if document.owner_user_id != context.user.id:
        raise AppError(403, "forbidden", "Generated document access is restricted to the owning user")
    return generated_document_redaction_debug_response(document)


@api.post("/transcripts/{transcript_id}/generate-output", response_model=GeneratedDocumentDetail, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def generate_transcript_output(
    request: Request,
    transcript_id: UUID,
    payload: GenerateTemplateOutputRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = None
    try:
        document = queue_document_generation_from_template_service(
            db,
            context.user,
            transcript_id=transcript_id,
            template_id=payload.template_id,
            structured_context=payload.structured_context,
        )
        task_result = enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError:
        raise
    except Exception as exc:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue note generation")
        raise AppError(502, "generation_enqueue_failed", "Could not enqueue note generation") from exc
    return generated_document_response(document)


@api.post("/transcripts/{transcript_id}/generate-followup", response_model=GeneratedDocumentDetail, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def generate_transcript_followup(
    request: Request,
    transcript_id: UUID,
    payload: GenerateFollowupRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = None
    try:
        document = queue_followup_generation_service(db, context.user, transcript_id=transcript_id, prompt_text=payload.prompt_text)
        task_result = enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError:
        raise
    except Exception as exc:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue follow-up generation")
        raise AppError(502, "generation_enqueue_failed", "Could not enqueue follow-up generation") from exc
    return generated_document_response(document)


@api.post("/transcripts/{transcript_id}/run-quick-action", response_model=GeneratedDocumentDetail, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def run_transcript_quick_action(
    request: Request,
    transcript_id: UUID,
    payload: GenerateQuickActionRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = None
    try:
        document = queue_quick_action_generation_service(db, context.user, transcript_id=transcript_id, quick_action_id=payload.quick_action_id)
        task_result = enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError:
        raise
    except Exception as exc:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue quick action generation")
        raise AppError(502, "generation_enqueue_failed", "Could not enqueue quick action generation") from exc
    return generated_document_response(document)


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


@app.get("/transcribe", response_class=HTMLResponse)
def transcribe_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    tab: str = "transcript",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_transcribe(
        request,
        db,
        current_user=context.user,
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        active_tab=tab,
        message=message,
        message_kind=safe_message_kind,
    )


@app.get("/transcribe-claude", response_class=HTMLResponse)
def transcribe_claude_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    tab: str = "transcript",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_transcribe(
        request,
        db,
        current_user=context.user,
        template_name="transcribe_claude.html",
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        active_tab=tab,
        message=message,
        message_kind=safe_message_kind,
    )


@app.get("/transcribe-glm-2", response_class=HTMLResponse)
def transcribe_glm_2_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    tab: str = "transcript",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_transcribe(
        request,
        db,
        current_user=context.user,
        template_name="transcribe.html",
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        active_tab=tab,
        message=message,
        message_kind=safe_message_kind,
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
        template = upsert_team_template_service(
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
        template = upsert_personal_template_service(
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
        quick_action = upsert_team_quick_action_service(
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
        quick_action = upsert_personal_quick_action_service(
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
            template_name=_home_template_name_from_return_view(return_view),
            home_page_route=_home_page_route_from_return_view(return_view),
            home_return_view=_home_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/home/transcripts/upload", response_class=HTMLResponse)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def home_upload_transcript_file(
    request: Request,
    title: str = Form(""),
    audio: UploadFile = File(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.team_id is None:
        return render_transcribe(
            request,
            db,
            current_user=context.user,
            message="Current user does not belong to a team",
            message_kind="error",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        active_team_stt_selection_service(db, team_id=context.user.team_id)
    except AppError as exc:
        if exc.code == "business_rule_violation":
            leader_email = db.scalar(
                select(User.email)
                .where(
                    User.team_id == context.user.team_id,
                    User.team_role == TeamRole.leader,
                    User.is_system_admin.is_(False),
                    User.status == UserStatus.active,
                )
                .order_by(User.created_at.asc())
            )
            return _transcribe_redirect(
                message=_missing_stt_selection_message(team_leader_email=leader_email),
                message_kind="error",
            )
        return _transcribe_redirect(message=exc.message, message_kind="error")
    audio_bytes = audio.file.read()
    job = None
    try:
        enforce_whole_file_upload_size(audio_bytes=audio_bytes)
        transcript = start_transcript_service(
            db,
            context.user,
            TranscriptStart(
                title=title or audio.filename or "Uploaded audio",
                ingestion_mode=TranscriptIngestionMode.whole_file,
            ),
        )
        _, job = queue_audio_file_ingestion(
            db,
            context.user,
            transcript_id=transcript.id,
            filename=audio.filename or "audio.bin",
        )
        task_result = enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
        attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect(message=exc.message, message_kind="error")
    except Exception as exc:
        if job is not None:
            mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion")
        return _transcribe_redirect(
            message="Could not enqueue file ingestion",
            message_kind="error",
        )

    return _transcribe_redirect(
        message="Audio file queued for transcription.",
        message_kind="success",
        queued_transcript_id=transcript.id,
    )


@app.post("/transcribe/upload", response_class=HTMLResponse)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def transcribe_upload_transcript_file(
    request: Request,
    transcript_id: str | None = Form(default=None),
    title: str = Form(""),
    audio: UploadFile = File(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.team_id is None:
        return render_transcribe(
            request,
            db,
            current_user=context.user,
            message="Current user does not belong to a team",
            message_kind="error",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        active_team_stt_selection_service(db, team_id=context.user.team_id)
    except AppError as exc:
        if exc.code == "business_rule_violation":
            leader_email = db.scalar(
                select(User.email)
                .where(
                    User.team_id == context.user.team_id,
                    User.team_role == TeamRole.leader,
                    User.is_system_admin.is_(False),
                    User.status == UserStatus.active,
                )
                .order_by(User.created_at.asc())
            )
            return _transcribe_redirect(
                message=_missing_stt_selection_message(team_leader_email=leader_email),
                message_kind="error",
                queued_transcript_id=UUID(transcript_id) if transcript_id else None,
            )
        return _transcribe_redirect(message=exc.message, message_kind="error")
    audio_bytes = audio.file.read()
    job = None
    try:
        enforce_whole_file_upload_size(audio_bytes=audio_bytes)
        if not transcript_id:
            raise AppError(409, "business_rule_violation", "Create or choose a transcript session before uploading audio")
        transcript = db.get(Transcript, UUID(transcript_id))
        if transcript is None or transcript.owner_user_id != context.user.id:
            raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": transcript_id})
        if title.strip():
            transcript = update_transcript_title_service(
                db,
                context.user,
                transcript_id=transcript.id,
                title=title,
            )
        _, job = queue_audio_file_ingestion(
            db,
            context.user,
            transcript_id=transcript.id,
            filename=audio.filename or "audio.bin",
        )
        task_result = enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
        attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect(
            message=exc.message,
            message_kind="error",
            queued_transcript_id=UUID(transcript_id) if transcript_id else None,
        )
    except Exception:
        if job is not None:
            mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion")
        return _transcribe_redirect(
            message="Could not enqueue file ingestion",
            message_kind="error",
            queued_transcript_id=UUID(transcript_id) if transcript_id else None,
        )
    return _transcribe_redirect(
        message="Audio file queued for transcription.",
        message_kind="success",
        queued_transcript_id=transcript.id,
    )


@app.post("/transcribe/generate-output", response_class=HTMLResponse)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def transcribe_generate_output(
    request: Request,
    transcript_id: UUID = Form(...),
    template_id: UUID = Form(...),
    context_problem: str = Form(""),
    context_history: str = Form(""),
    context_family_history: str = Form(""),
    context_social_history: str = Form(""),
    context_examination: str = Form(""),
    context_comment: str = Form(""),
    context_tasks: str = Form(""),
    context_investigations: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    document = None
    try:
        document = queue_document_generation_from_template_service(
            db,
            context.user,
            transcript_id=transcript_id,
            template_id=template_id,
            structured_context=_structured_context_from_form(
                section_values={
                    "problem": context_problem,
                    "history": context_history,
                    "family_history": context_family_history,
                    "social_history": context_social_history,
                    "examination": context_examination,
                    "comment": context_comment,
                    "tasks": context_tasks,
                    "investigations": context_investigations,
                }
            ),
        )
        task_result = enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect(
            message=exc.message,
            message_kind="error",
            queued_transcript_id=transcript_id,
        )
    except Exception:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue note generation")
        return _transcribe_redirect(
            message="Could not enqueue note generation",
            message_kind="error",
            queued_transcript_id=transcript_id,
        )
    return RedirectResponse(
        url=f"/transcribe?transcript_id={transcript_id}&tab=output&message=Queued+note+generation.&message_kind=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/transcribe/generate-followup", response_class=HTMLResponse)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def transcribe_generate_followup(
    request: Request,
    transcript_id: UUID = Form(...),
    prompt_text: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    document = None
    try:
        document = queue_followup_generation_service(db, context.user, transcript_id=transcript_id, prompt_text=prompt_text)
        task_result = enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': exc.message, 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue follow-up generation")
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Could not enqueue follow-up generation.', 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Queued follow-up generation.', 'message_kind': 'success'})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/transcribe/run-quick-action", response_class=HTMLResponse)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def transcribe_run_quick_action(
    request: Request,
    transcript_id: UUID = Form(...),
    quick_action_id: UUID = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    document = None
    try:
        document = queue_quick_action_generation_service(db, context.user, transcript_id=transcript_id, quick_action_id=quick_action_id)
        task_result = enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': exc.message, 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue quick action generation")
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Could not enqueue quick action generation.', 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Queued quick action generation.', 'message_kind': 'success'})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/transcribe/sessions", response_class=HTMLResponse)
def transcribe_create_session(
    request: Request,
    title: str = Form(""),
    ingestion_mode: TranscriptIngestionMode = Form(default=TranscriptIngestionMode.whole_file),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        transcript = start_transcript_service(
            db,
            context.user,
            TranscriptStart(
                title=title or "Untitled session",
                ingestion_mode=ingestion_mode,
            ),
        )
    except AppError as exc:
        return render_transcribe(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/transcribe?transcript_id={transcript.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/transcribe/sessions/delete", response_class=HTMLResponse)
def transcribe_delete_sessions(
    request: Request,
    transcript_ids: list[str] = Form(default=[]),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response

    try:
        delete_transcripts_service(
            db,
            context.user,
            transcript_ids=[UUID(transcript_id) for transcript_id in transcript_ids],
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid transcript selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_transcribe(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
        )
    return RedirectResponse(url="/transcribe", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/transcribe/sessions/{transcript_id}/title", response_class=HTMLResponse)
def transcribe_update_session_title(
    request: Request,
    transcript_id: UUID,
    title: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        update_transcript_title_service(
            db,
            context.user,
            transcript_id=transcript_id,
            title=title,
        )
    except AppError as exc:
        return render_transcribe(
            request,
            db,
            current_user=context.user,
            transcript_id=str(transcript_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/transcribe?transcript_id={transcript_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/transcribe/sessions/{transcript_id}/mode", response_class=HTMLResponse)
def transcribe_update_session_mode(
    request: Request,
    transcript_id: UUID,
    ingestion_mode: TranscriptIngestionMode = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        update_transcript_service(
            db,
            context.user,
            transcript_id=transcript_id,
            title=None,
            ingestion_mode=ingestion_mode,
            structured_context_json=None,
        )
    except AppError as exc:
        return render_transcribe(
            request,
            db,
            current_user=context.user,
            transcript_id=str(transcript_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/transcribe?transcript_id={transcript_id}", status_code=status.HTTP_303_SEE_OTHER)


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
        stt_test_result = run_saved_stt_config_test_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
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


app.include_router(api)
