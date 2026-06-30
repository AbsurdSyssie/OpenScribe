import asyncio
import os
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .cookie_security import app_environment, enforce_production_cookie_security, should_set_secure_cookie
from .errors import AppError, app_error_handler, http_error_handler, rate_limit_error_handler, validation_error_handler
from .models import (
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    LlmAdapterKind,
    ProviderCredentialStatus,
    PromptTemplate,
    QuickAction,
    SessionAuthLevel,
    SessionStatus,
    SttAdapterKind,
    SttSelectionPurpose,
    TeamRole,
    TeamStatus,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    User,
    UserSession,
    UserStatus,
    AuthEmailTokenPurpose,
    transcript_expiry,
    utcnow,
)
from .security_headers import content_security_policy, new_csp_nonce
from .schemas import (
    AccountRequestApprove,
    AccountActivationConfirmRequest,
    BreakGlassRecoveryRequest,
    AccountRequestCreate,
    AccountRequestDetail,
    AccountRequestListItem,
    ClinicalNlpSelectionDetail,
    ClinicalNlpSelectionUpsert,
    DeidentificationProviderAssignmentDetail,
    DeidentificationProviderAssignmentUpsert,
    DeidentificationProviderDetail,
    DeidentificationProviderInspectRequest,
    DeidentificationProviderUpsert,
    DeidentificationInspectResult,
    DeidentificationSelectionDetail,
    DeidentificationSelectionUpsert,
    AccountRequestReject,
    CurrentUserResponse,
    DefaultPromptTemplateUpsert,
    DefaultQuickActionUpsert,
    PostConsultationDictationDetail,
    PostConsultationDictationPreview,
    PostConsultationDictationUpdate,
    PromptContextPreview,
    EMIS_SECTION_KEYS,
    EMIS_SECTION_LABELS,
    ErrorResponse,
    GenerateFollowupRequest,
    GenerateQuickActionRequest,
    GenerateTemplateOutputRequest,
    GenericMessageResponse,
    LlmConfigDraftCreate,
    LlmConfigDraftCreateResult,
    LlmConfigDraftReplaceCredential,
    LlmConfigDetail,
    LlmConfigFinalize,
    LlmConfigInspectResult,
    LlmInspectRequest,
    LlmConfigUpsert,
    LlmSelectionDetail,
    LlmSelectionUpsert,
    LoginRequest,
    LoginResponse,
    ManagerRecoveryEmailRequest,
    ManagerRecoveryResponse,
    MfaChallengeRequest,
    PasswordChangeRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PromptTemplateDetail,
    PromptTemplateUpsert,
    QuickActionDetail,
    QuickActionUpsert,
    RecoveryCodesResponse,
    SttConfigDraftCreate,
    SttConfigDraftCreateResult,
    SttConfigDraftReplaceCredential,
    SttConfigDetail,
    SttConfigFinalize,
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
    TranscriptManualPiiEntityCreate,
    TranscriptPiiEntityDetail,
    TranscriptStart,
    TranscriptUpdate,
    TrustedDeviceStatusResponse,
    UserCreate,
    UserAppPreferencesDetail,
    UserAppPreferencesUpsert,
    UserDetail,
    UserLlmPreferenceDetail,
    UserLlmPreferenceUpsert,
    UserListItem,
    GeneratedDocumentDetail,
    GeneratedDocumentSectionDetail,
    GeneratedDocumentUpdateRequest,
    GeneratedDocumentRedactionDebugDetail,
    TranscribeWorkspaceDetail,
)
from .llm_provider_defaults import DEFAULT_BEDROCK_CHAT_REGION, bedrock_region_from_base_url
from .services.templates import (
    attach_generated_document_task_id as attach_generated_document_task_id_service,
    delete_generated_document as delete_generated_document_service,
    delete_personal_quick_action as delete_personal_quick_action_service,
    delete_personal_template as delete_personal_template_service,
    delete_team_quick_action as delete_team_quick_action_service,
    delete_team_template as delete_team_template_service,
    duplicate_personal_quick_action as duplicate_personal_quick_action_service,
    duplicate_personal_template as duplicate_personal_template_service,
    duplicate_team_quick_action as duplicate_team_quick_action_service,
    duplicate_team_template as duplicate_team_template_service,
    list_available_quick_actions_for_user as list_available_quick_actions_for_user_service,
    list_available_templates_for_user as list_available_templates_for_user_service,
    list_generated_documents_for_transcript as list_generated_documents_for_transcript_service,
    update_generated_document_content as update_generated_document_content_service,
    list_personal_quick_actions as list_personal_quick_actions_service,
    list_personal_templates as list_personal_templates_service,
    list_team_quick_actions as list_team_quick_actions_service,
    list_team_templates as list_team_templates_service,
    mark_generated_document_enqueue_failed as mark_generated_document_enqueue_failed_service,
    generated_document_section_text as generated_document_section_text_service,
    generated_document_text as generated_document_text_service,
    queue_quick_action_generation as queue_quick_action_generation_service,
    queue_document_generation_from_template as queue_document_generation_from_template_service,
    queue_followup_generation as queue_followup_generation_service,
    upsert_personal_quick_action as upsert_personal_quick_action_service,
    upsert_personal_template as upsert_personal_template_service,
    upsert_team_quick_action as upsert_team_quick_action_service,
    upsert_team_template as upsert_team_template_service,
)
from .services.default_assets import (
    delete_default_quick_action as delete_default_quick_action_service,
    delete_default_template as delete_default_template_service,
    duplicate_default_quick_action as duplicate_default_quick_action_service,
    duplicate_default_template as duplicate_default_template_service,
    list_default_quick_actions as list_default_quick_actions_service,
    list_default_templates as list_default_templates_service,
    upsert_default_quick_action as upsert_default_quick_action_service,
    upsert_default_template as upsert_default_template_service,
)
from .services.llm import (
    active_team_llm_selection as active_team_llm_selection_service,
    clear_team_llm_selection as clear_team_llm_selection_service,
    clear_user_llm_preference as clear_user_llm_preference_service,
    create_llm_config_draft as create_llm_config_draft_service,
    delete_llm_config as delete_llm_config_service,
    finalize_llm_config_draft as finalize_llm_config_draft_service,
    get_team_llm_selection as get_team_llm_selection_service,
    get_user_llm_preference as get_user_llm_preference_service,
    inspect_llm_contract as inspect_llm_contract_service,
    inspect_saved_llm_config as inspect_saved_llm_config_service,
    list_llm_configs as list_llm_configs_service,
    list_selectable_llm_configs as list_selectable_llm_configs_service,
    resolve_user_llm as resolve_user_llm_service,
    replace_llm_config_draft_credential as replace_llm_config_draft_credential_service,
    set_team_llm_selection as set_team_llm_selection_service,
    set_user_llm_preference as set_user_llm_preference_service,
    upsert_llm_config as upsert_llm_config_service,
)
from .services.deidentification import (
    assign_deidentification_provider_to_team as assign_deidentification_provider_to_team_service,
    clear_team_clinical_nlp_selection as clear_team_clinical_nlp_selection_service,
    clear_team_deidentification_selection as clear_team_deidentification_selection_service,
    delete_deidentification_provider as delete_deidentification_provider_service,
    get_team_clinical_nlp_selection as get_team_clinical_nlp_selection_service,
    get_team_deidentification_selection as get_team_deidentification_selection_service,
    inspect_deidentification_provider as inspect_deidentification_provider_service,
    list_deidentification_providers as list_deidentification_providers_service,
    list_selectable_clinical_nlp_providers as list_selectable_clinical_nlp_providers_service,
    list_selectable_deidentification_providers as list_selectable_deidentification_providers_service,
    list_team_deidentification_provider_assignments as list_team_deidentification_provider_assignments_service,
    remove_deidentification_provider_assignment as remove_deidentification_provider_assignment_service,
    set_team_clinical_nlp_selection as set_team_clinical_nlp_selection_service,
    set_team_deidentification_selection as set_team_deidentification_selection_service,
    upsert_deidentification_provider as upsert_deidentification_provider_service,
)
from .services.preferences import (
    clear_user_app_preferences as clear_user_app_preferences_service,
    get_user_app_preferences as get_user_app_preferences_service,
    set_user_app_preferences as set_user_app_preferences_service,
)
from .services.stt import (
    active_team_stt_selection as active_team_stt_selection_service,
    create_stt_config_draft as create_stt_config_draft_service,
    delete_stt_config as delete_stt_config_service,
    finalize_stt_config_draft as finalize_stt_config_draft_service,
    get_stt_config as get_stt_config_service,
    inspect_stt_contract as inspect_stt_contract_service,
    get_team_stt_selection as get_team_stt_selection_service,
    list_selectable_stt_configs as list_selectable_stt_configs_service,
    list_stt_configs as list_stt_configs_service,
    reinspect_stt_config as reinspect_stt_config_service,
    replace_stt_config_draft_credential as replace_stt_config_draft_credential_service,
    run_saved_stt_config_test as run_saved_stt_config_test_service,
    clear_team_stt_selection as clear_team_stt_selection_service,
    set_team_stt_selection as set_team_stt_selection_service,
    upsert_stt_config as upsert_stt_config_service,
)
from .services.dictations import (
    append_post_consultation_dictation_audio,
    dictation_detail_response,
    get_post_consultation_dictation,
    transcribe_prompt_context_audio,
    transcribe_post_consultation_dictation_audio,
    update_post_consultation_dictation,
)
from .services.admin import (
    admin_usage_overview as admin_usage_overview_service,
    approve_account_request as approve_account_request_service,
    create_account_request as create_account_request_service,
    create_bootstrap_admin,
    create_team as create_team_service,
    create_user as create_user_service,
    delete_team as delete_team_service,
    delete_user as delete_user_service,
    list_manageable_account_requests as list_manageable_account_requests_service,
    list_manageable_users as list_manageable_users_service,
    list_teams as list_teams_service,
    list_users as list_users_service,
    reactivate_user as reactivate_user_service,
    reject_account_request as reject_account_request_service,
    reset_user_password_to_temporary as reset_user_password_to_temporary_service,
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
    verify_active_totp_for_user,
    verify_login_totp,
    verify_totp_enrollment,
)
from .services.csrf import (
    CSRF_ANON_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_SAFE_METHODS,
    anonymous_csrf_token,
    csrf_secret_configured_for_environment,
    new_anonymous_nonce,
    session_csrf_token,
    verify_csrf_token,
)
from .services.auth_email import (
    GENERIC_PASSWORD_RESET_MESSAGE,
    PASSWORD_RESET_EMAIL_DISABLED_MESSAGE,
    confirm_account_activation as confirm_account_activation_service,
    confirm_password_reset as confirm_password_reset_service,
    email_password_reset_enabled as email_password_reset_enabled_service,
    get_active_token_user as get_active_token_user_service,
    get_manageable_user_for_recovery as get_manageable_user_for_recovery_service,
    request_password_reset as request_password_reset_service,
    reset_user_mfa_for_reenrollment as reset_user_mfa_for_reenrollment_service,
    send_account_activation_email as send_account_activation_email_service,
    send_manager_account_recovery_email as send_manager_account_recovery_email_service,
    send_manager_password_reset_email as send_manager_password_reset_email_service,
    send_password_reset_email as send_password_reset_email_service,
)
from .services.security_audit import audit_subject_hash, audit_subject_hash_secret_configured_for_environment, record_security_event
from .services.transcripts import (
    attach_task_id_to_ingestion_job,
    can_create_new_session as can_create_new_session_service,
    can_switch_transcript_ingestion_mode as can_switch_transcript_ingestion_mode_service,
    clear_ingestion_retry_source,
    commit_transcript_text as commit_transcript_text_service,
    create_manual_pii_entity as create_manual_pii_entity_service,
    create_transcript_from_payload,
    delete_manual_pii_entity as delete_manual_pii_entity_service,
    delete_transcripts as delete_transcripts_service,
    finalize_live_capture as finalize_live_capture_service,
    latest_ingestion_job_for_transcript as latest_ingestion_job_for_transcript_service,
    mark_ingestion_job_enqueue_failed,
    next_live_chunk_sequence_no_for_transcript as next_live_chunk_sequence_no_for_transcript_service,
    queue_audio_chunk_ingestion,
    queue_audio_file_ingestion,
    reconcile_live_chunk_progress as reconcile_live_chunk_progress_service,
    retry_audio_file_ingestion,
    start_transcript as start_transcript_service,
    transcript_draft_text as transcript_draft_text_service,
    transcript_structured_context as transcript_structured_context_service,
    update_transcript as update_transcript_service,
    update_transcript_title as update_transcript_title_service,
)
from .tasks import enqueue_generated_document_job, enqueue_transcript_ingestion_job
from .services.audio import enforce_whole_file_upload_size
from .services.redaction import redaction_run_text as redaction_run_text_service
from .web.presentation import (
    admin_page_route_from_return_view,
    admin_redirect_url,
    admin_return_view_value,
    clinical_nlp_selection_response,
    deidentification_provider_assignment_response,
    deidentification_provider_response,
    deidentification_selection_response,
    generated_document_redaction_debug_response,
    generated_document_response,
    home_page_route_from_return_view,
    home_template_editor_url,
    home_redirect_url,
    home_return_view_value,
    home_template_name_from_return_view,
    llm_config_response,
    llm_form_defaults,
    llm_selection_response,
    parse_extra_form_fields_json,
    parse_json_object,
    parse_string_map_json,
    quick_action_response,
    render_admin,
    render_auth_page,
    render_home,
    render_mfa_challenge,
    render_onboarding,
    render_request_access_page,
    stt_config_response,
    stt_form_defaults,
    stt_selection_response,
    template_response,
    transcribe_redirect,
    user_app_preferences_response,
    user_llm_preference_response,
)
from .web.templates import templates
from .web.transcribe_workspace import (
    open_realtime_workspace_db_session,
    render_transcribe,
    resolve_realtime_workspace_user,
    resolve_transcribe_workspace_detail,
    serialize_sse_event,
    stream_transcribe_workspace_events,
    transcript_detail_response,
    transcript_manual_pii_entity_response,
    transcript_pii_entities_response,
)


# Compatibility aliases for the first main.py extraction slice. Existing route
# handlers still use the legacy helper names while the file is being broken up.
_open_realtime_workspace_db_session = open_realtime_workspace_db_session
_serialize_sse_event = serialize_sse_event
_home_redirect_url = home_redirect_url
_home_return_view_value = home_return_view_value
_home_page_route_from_return_view = home_page_route_from_return_view
_home_template_editor_url = home_template_editor_url
_home_template_name_from_return_view = home_template_name_from_return_view
_admin_redirect_url = admin_redirect_url
_admin_return_view_value = admin_return_view_value
_admin_page_route_from_return_view = admin_page_route_from_return_view
_transcribe_redirect = transcribe_redirect


@dataclass(slots=True)
class AuthenticatedContext:
    user: User
    session: UserSession
    token: str


enforce_production_cookie_security()
csrf_secret_configured_for_environment()
audit_subject_hash_secret_configured_for_environment()
app = FastAPI(title="OpenScribe MVP", docs_url=None, redoc_url=None, openapi_url=None)
LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _local_only_dev_emails() -> set[str]:
    return {
        os.getenv("DEV_TEST_ADMIN_EMAIL", "dev.admin@example.com").strip().lower(),
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


def _request_is_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _origin_allowed(request: Request) -> bool:
    if request.method in CSRF_SAFE_METHODS:
        return True

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    trust_forwarded_origin = os.getenv("TRUST_FORWARDED_ORIGIN_HEADERS", "false").lower() in {"1", "true", "yes"}
    expected_scheme = request.url.scheme
    expected_host = request.headers.get("host")
    if trust_forwarded_origin:
        expected_scheme = request.headers.get("x-forwarded-proto", expected_scheme).split(",", 1)[0].strip()
        expected_host = request.headers.get("x-forwarded-host") or expected_host

    if not expected_host:
        return False

    expected_origin = f"{expected_scheme}://{expected_host}"

    if origin:
        return origin == expected_origin

    if referer:
        parsed = urlsplit(referer)
        return f"{parsed.scheme}://{parsed.netloc}" == expected_origin

    return False


def _audit_request_rejected(
    db: Session,
    request: Request,
    *,
    action: str,
    category: str,
    reason_code: str,
    status_code: int,
    actor: User | None = None,
    team_id: UUID | None = None,
    details: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "category": category,
        "outcome": "denied" if status_code in {401, 403} else "failure",
        "reason_code": reason_code,
        "status_code": status_code,
    }
    if details:
        payload.update(details)
    record_security_event(
        db,
        action=action,
        actor=actor,
        target=actor,
        team_id=team_id or (actor.team_id if actor else None),
        request=request,
        details=payload,
    )


async def require_browser_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_db),
) -> None:
    if request.method in CSRF_SAFE_METHODS:
        return

    if not _origin_allowed(request):
        _audit_request_rejected(
            db,
            request,
            action="csrf_rejected",
            category="csrf",
            reason_code="cross_origin",
            status_code=403,
        )
        raise AppError(403, "forbidden", "Cross-origin request rejected")

    submitted_token = csrf_header
    if submitted_token is None:
        form = await request.form()
        submitted_token = form.get("_csrf_token")

    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    anon_nonce = request.cookies.get(CSRF_ANON_COOKIE_NAME)

    if not submitted_token or not verify_csrf_token(
        submitted_token=str(submitted_token),
        raw_session_token=raw_session_token,
        anon_nonce=anon_nonce,
    ):
        _audit_request_rejected(
            db,
            request,
            action="csrf_rejected",
            category="csrf",
            reason_code="invalid_or_missing_token",
            status_code=403,
            details={"auth_authority_present": bool(raw_session_token), "anon_nonce_present": bool(anon_nonce)},
        )
        raise AppError(403, "forbidden", "CSRF verification failed")


BrowserCsrf = Annotated[None, Depends(require_browser_csrf)]


async def require_api_csrf(
    request: Request,
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_db),
) -> None:
    if request.method in CSRF_SAFE_METHODS:
        return

    has_cookie_backed_authority = bool(
        request.cookies.get(SESSION_COOKIE_NAME)
        or request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME)
    )
    if not has_cookie_backed_authority:
        return

    if not _origin_allowed(request):
        _audit_request_rejected(
            db,
            request,
            action="csrf_rejected",
            category="csrf",
            reason_code="cross_origin",
            status_code=403,
        )
        raise AppError(403, "forbidden", "Cross-origin request rejected")

    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    anon_nonce = request.cookies.get(CSRF_ANON_COOKIE_NAME)
    if not csrf_header or not verify_csrf_token(
        submitted_token=csrf_header,
        raw_session_token=raw_session_token,
        anon_nonce=anon_nonce,
    ):
        _audit_request_rejected(
            db,
            request,
            action="csrf_rejected",
            category="csrf",
            reason_code="invalid_or_missing_token",
            status_code=403,
            details={"auth_authority_present": bool(raw_session_token), "anon_nonce_present": bool(anon_nonce)},
        )
        raise AppError(403, "forbidden", "CSRF verification failed")


api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_csrf)])


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
LIVE_CHUNK_UPLOAD_RATE_LIMIT = limiter.shared_limit(
    "1/second",
    scope="live_chunk_upload",
    key_func=whole_file_upload_rate_limit_key,
)
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
    "20/3 minutes",
    scope="llm_generation_burst",
    key_func=whole_file_upload_rate_limit_key,
)
LLM_GENERATION_DAILY_RATE_LIMIT = limiter.shared_limit(
    "200/day",
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
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


SENSITIVE_NO_STORE_PATH_PREFIXES = (
    "/api/v1/transcribe",
    "/api/v1/transcripts",
    "/api/v1/generated-documents",
    "/api/v1/post-consultation-dictation",
)
PUBLIC_NO_STORE_PATHS = {
    "/login",
    "/forgot-password",
    "/request-access",
    "/reset-password",
    "/activate-account",
    "/docs",
    "/redoc",
    "/openapi.json",
}
CSRF_COOKIE_SKIP_PATHS = {
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/security.txt",
}
CSRF_COOKIE_SKIP_PREFIXES = ("/static/",)
PUBLIC_CACHE_PATHS = {
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/security.txt",
}
STATIC_CACHE_CONTROL = "public, max-age=3600"
PUBLIC_METADATA_CACHE_CONTROL = "public, max-age=3600"
NO_STORE_CACHE_CONTROL = "no-store"
SECURITY_HEADER_HSTS_VALUE = "max-age=31536000; includeSubDomains"
SECURITY_HEADER_PERMISSIONS_POLICY = (
    "camera=(), geolocation=(), payment=(), usb=(), fullscreen=(self), microphone=(self)"
)
HSTS_SOURCE_APP = "app"
HSTS_SOURCE_PROXY = "proxy"
HSTS_SOURCE_PROXY_STATIC_FALLBACK = "proxy_static_fallback"


def _hsts_source() -> str:
    return os.getenv("HSTS_SOURCE", HSTS_SOURCE_APP).strip().lower()


def _should_set_hsts(request: Request) -> bool:
    hsts_source = _hsts_source()
    return hsts_source == HSTS_SOURCE_APP or (
        hsts_source == HSTS_SOURCE_PROXY_STATIC_FALLBACK and request.url.path.startswith("/static/")
    )


def _should_issue_csrf_cookie(request: Request) -> bool:
    path = request.url.path
    return path not in CSRF_COOKIE_SKIP_PATHS and not path.startswith(CSRF_COOKIE_SKIP_PREFIXES)


def _set_cache_headers(request: Request, response: Response) -> None:
    path = request.url.path
    if (
        path == "/"
        or path == "/api"
        or path.startswith("/api/")
        or path in PUBLIC_NO_STORE_PATHS
        or path.startswith(SENSITIVE_NO_STORE_PATH_PREFIXES)
    ):
        response.headers["Cache-Control"] = NO_STORE_CACHE_CONTROL
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return
    if path in PUBLIC_CACHE_PATHS:
        response.headers.setdefault("Cache-Control", PUBLIC_METADATA_CACHE_CONTROL)
        return
    if path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", STATIC_CACHE_CONTROL)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    request.state.csp_nonce = new_csp_nonce()
    response = await call_next(request)
    is_https = _request_is_https(request)

    if is_https and _should_set_hsts(request):
        response.headers.setdefault("Strict-Transport-Security", SECURITY_HEADER_HSTS_VALUE)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Embedder-Policy", "credentialless")
    response.headers.setdefault("Permissions-Policy", SECURITY_HEADER_PERMISSIONS_POLICY)
    response.headers.setdefault(
        "Content-Security-Policy",
        content_security_policy(request.state.csp_nonce, upgrade_insecure_requests=is_https),
    )
    _set_cache_headers(request, response)

    return response


@app.middleware("http")
async def ensure_csrf_cookie(request: Request, call_next):
    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    anon_nonce = request.cookies.get(CSRF_ANON_COOKIE_NAME) or new_anonymous_nonce()
    request.state.csrf_token = (
        session_csrf_token(raw_session_token)
        if raw_session_token
        else anonymous_csrf_token(anon_nonce)
    )

    response = await call_next(request)
    if request.method not in {"GET", "HEAD"} or not _should_issue_csrf_cookie(request):
        return response

    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    if raw_session_token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=request.state.csrf_token,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(CSRF_ANON_COOKIE_NAME, path="/")
        return response

    response.set_cookie(
        key=CSRF_ANON_COOKIE_NAME,
        value=anon_nonce,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=request.state.csrf_token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    return response


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
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
    _set_csrf_cookie_for_session(request, response, token)


def _set_csrf_cookie_for_session(request: Request, response: Response, token: str) -> None:
    secure_cookie = should_set_secure_cookie(
        request_url=str(request.url),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=session_csrf_token(token),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(CSRF_ANON_COOKIE_NAME, path="/")


def _set_trusted_device_cookie(request: Request, response: Response, token: str) -> None:
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


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    _clear_csrf_cookie(response)


def _clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_ANON_COOKIE_NAME, path="/")


def _clear_trusted_device_cookie(response: Response) -> None:
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
        _audit_request_rejected(
            db,
            request,
            action="access_denied",
            category="access_control",
            reason_code="authentication_required",
            status_code=401,
        )
        raise AppError(401, "unauthorized", "Authentication required")
    return context


def require_full_context(
    request: Request,
    context: AuthenticatedContext = Depends(require_authenticated_context),
    db: Session = Depends(get_db),
) -> AuthenticatedContext:
    if context.session.auth_level.value == "pending_mfa":
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="mfa_required", status_code=403, actor=context.user)
        raise AppError(403, "mfa_required", "Complete TOTP verification before accessing this route")
    if context.session.auth_level is not determine_auth_level(context.user):
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="auth_level_mismatch", status_code=401, actor=context.user)
        raise AppError(401, "unauthorized", "Authentication required")
    if context.session.auth_level.value != "full":
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="onboarding_incomplete", status_code=403, actor=context.user)
        raise AppError(403, "onboarding_incomplete", "Complete onboarding before accessing this route")
    return context


def _require_full_context_from_token(request: Request, raw_session_token: str | None) -> AuthenticatedContext:
    if not raw_session_token:
        with _open_realtime_workspace_db_session(request) as db:
            _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="authentication_required", status_code=401)
        raise AppError(401, "unauthorized", "Authentication required")
    with _open_realtime_workspace_db_session(request) as db:
        resolved = resolve_authenticated_session(db, raw_session_token)
        if resolved is None:
            _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="authentication_required", status_code=401)
            raise AppError(401, "unauthorized", "Authentication required")
        user, session = resolved
        if user.email.lower() in _local_only_dev_emails() and not _request_is_localhost_only(request):
            revoke_session_by_token(db, raw_session_token, reason="dev_account_non_local")
            _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="local_dev_debug_required", status_code=403, actor=user)
            raise AppError(401, "unauthorized", "Authentication required")
        context = AuthenticatedContext(user=user, session=session, token=raw_session_token)
        if context.session.auth_level.value == "pending_mfa":
            _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="mfa_required", status_code=403, actor=context.user)
            raise AppError(403, "mfa_required", "Complete TOTP verification before accessing this route")
        if context.session.auth_level is not determine_auth_level(context.user):
            _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="auth_level_mismatch", status_code=401, actor=context.user)
            raise AppError(401, "unauthorized", "Authentication required")
        if context.session.auth_level.value != "full":
            _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="onboarding_incomplete", status_code=403, actor=context.user)
            raise AppError(403, "onboarding_incomplete", "Complete onboarding before accessing this route")
        return context


def require_local_dev_debug_context(
    request: Request,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
) -> AuthenticatedContext:
    if context.user.email.lower() not in _local_only_dev_emails() or not _request_is_localhost_only(request):
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="local_dev_debug_required", status_code=403, actor=context.user)
        raise AppError(403, "forbidden", "Redaction debug is available only to localhost dev test accounts")
    return context


def require_system_admin(request: Request, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)) -> AuthenticatedContext:
    if not context.user.is_system_admin:
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="system_admin_required", status_code=403, actor=context.user)
        raise AppError(403, "forbidden", "System admin access required")
    return context


def require_stt_selector(request: Request, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="stt_selector_required", status_code=403, actor=context.user)
    raise AppError(403, "forbidden", "STT selection access required")


def require_llm_selector(request: Request, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="llm_selector_required", status_code=403, actor=context.user)
    raise AppError(403, "forbidden", "LLM selection access required")


def require_deidentification_selector(request: Request, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="deidentification_selector_required", status_code=403, actor=context.user)
    raise AppError(403, "forbidden", "De-identification selection access required")


def require_user_manager(request: Request, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)) -> AuthenticatedContext:
    if context.user.is_system_admin:
        return context
    if context.user.team_role is TeamRole.leader and context.user.team_id is not None:
        return context
    _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="user_manager_required", status_code=403, actor=context.user)
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


def _template_config_from_form(*, mode: TemplateMode, section_values: dict[str, str]) -> dict | None:
    if mode is not TemplateMode.structured:
        return None
    return _structured_template_config_from_form(section_values=section_values)


API_DOCS_PUBLIC_ENV = "PUBLIC_API_DOCS"
PRODUCTION_ENVIRONMENTS = {"production", "prod"}
TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def _public_api_docs_enabled() -> bool:
    configured = os.getenv(API_DOCS_PUBLIC_ENV)
    if configured is not None:
        value = configured.strip().lower()
        if value in TRUE_ENV_VALUES:
            return True
        if value in FALSE_ENV_VALUES:
            return False
    return app_environment() not in PRODUCTION_ENVIRONMENTS


def _require_api_docs_access(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthenticatedContext | None:
    if _public_api_docs_enabled():
        return None
    context = _current_context_optional(request, db)
    if context is None:
        _audit_request_rejected(
            db,
            request,
            action="access_denied",
            category="access_control",
            reason_code="api_docs_authentication_required",
            status_code=401,
        )
        raise AppError(401, "unauthorized", "Authentication required")
    if context.session.auth_level.value == "pending_mfa":
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="mfa_required", status_code=403, actor=context.user)
        raise AppError(403, "mfa_required", "Complete TOTP verification before accessing this route")
    if context.session.auth_level is not determine_auth_level(context.user):
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="auth_level_mismatch", status_code=401, actor=context.user)
        raise AppError(401, "unauthorized", "Authentication required")
    if context.session.auth_level.value != "full":
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="onboarding_incomplete", status_code=403, actor=context.user)
        raise AppError(403, "onboarding_incomplete", "Complete onboarding before accessing this route")
    if not context.user.is_system_admin:
        _audit_request_rejected(db, request, action="access_denied", category="access_control", reason_code="api_docs_system_admin_required", status_code=403, actor=context.user)
        raise AppError(403, "forbidden", "System admin access required")
    return context


@app.get("/openapi.json", include_in_schema=False)
def openapi_json(_context: AuthenticatedContext | None = Depends(_require_api_docs_access)):
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def swagger_docs(_context: AuthenticatedContext | None = Depends(_require_api_docs_access)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="OpenScribe MVP - API docs")


@app.get("/redoc", include_in_schema=False)
def redoc_docs(_context: AuthenticatedContext | None = Depends(_require_api_docs_access)):
    return get_redoc_html(openapi_url="/openapi.json", title="OpenScribe MVP - ReDoc")


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


from .routes import api_routes as _api_routes  # noqa: F401
from .routes import web_admin as _web_admin  # noqa: F401
from .routes import web_home_transcribe as _web_home_transcribe  # noqa: F401
from .routes import web_pages as _web_pages  # noqa: F401
from .routes import web_team_management as _web_team_management  # noqa: F401
from .routes import web_transcribe as _web_transcribe  # noqa: F401


app.include_router(api)
