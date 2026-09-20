import os
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .cookie_security import app_environment, enforce_production_cookie_security, should_set_secure_cookie
from .errors import AppError, app_error_handler, http_error_handler, rate_limit_error_handler, validation_error_handler
from .models import (
    SessionStatus,
    SttSelectionPurpose,
    TeamRole,
    TemplateMode,
    User,
    UserSession,
    UserStatus,
    utcnow,
)
from .security_headers import content_security_policy, new_csp_nonce
from .schemas import EMIS_SECTION_KEYS, ErrorResponse
# Admin routes and tests still replace these service hooks through app.main.
from .services.llm import (
    cancel_llm_config_draft as cancel_llm_config_draft_service,
    inspect_saved_llm_config as inspect_saved_llm_config_service,
)
from .services.stt import (
    cancel_stt_config_draft as cancel_stt_config_draft_service,
    reinspect_stt_config as reinspect_stt_config_service,
    run_saved_stt_config_test as run_saved_stt_config_test_service,
)
from .services.admin import bootstrap_admin_is_configured, user_count as user_count_service
from .services.auth import (
    SESSION_COOKIE_NAME,
    TRUSTED_DEVICE_COOKIE_NAME,
    determine_auth_level,
    resolve_authenticated_session,
    revoke_session_by_token,
    session_token_hash,
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
from .services.oidc import oidc_configured_for_environment
from .services.security_audit import (
    audit_subject_hash_secret_configured_for_environment,
    record_security_event,
)
# Retained for tests that guard against bypassing transactional outbox dispatch.
from .tasks import enqueue_generated_document_job, enqueue_transcript_ingestion_job
from .web.presentation import (
    admin_page_route_from_return_view,
    admin_redirect_url,
    admin_return_view_value,
    home_page_route_from_return_view,
    home_template_editor_url,
    home_redirect_url,
    home_return_view_value,
    home_template_name_from_return_view,
    transcribe_redirect,
)
from .web.transcribe_workspace import open_realtime_workspace_db_session, serialize_sse_event


# Route modules and compatibility tests still use these helper aliases.
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


def _allowed_hosts_for_environment() -> list[str]:
    configured = [host.strip().lower() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()]
    environment = app_environment()
    if configured:
        if environment in {"production", "prod"} and any("*" in host for host in configured):
            raise RuntimeError("ALLOWED_HOSTS must not contain wildcards in production")
        return configured
    if environment not in {"production", "prod"}:
        return ["*"]
    public_host = urlsplit(os.getenv("APP_PUBLIC_URL", "")).hostname
    if not public_host:
        raise RuntimeError("ALLOWED_HOSTS or a valid APP_PUBLIC_URL is required in production")
    if "*" in public_host:
        raise RuntimeError("APP_PUBLIC_URL must not contain a wildcard host in production")
    return [public_host.lower()]


enforce_production_cookie_security()
csrf_secret_configured_for_environment()
audit_subject_hash_secret_configured_for_environment()
oidc_configured_for_environment()
app = FastAPI(title="OpenScribe MVP", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts_for_environment())
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


def _enabled_environment_flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes"}


def _validated_client_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def rate_limit_client_key(request: Request) -> str:
    """Return a stable abuse-control key from an explicitly trusted source.

    Proxy headers remain disabled by default. Deployments may trust one only
    when the named proxy is the sole route to the origin and overwrites that
    header. Invalid trusted-header values fail closed to the socket peer.
    """

    client_ip = None
    if _enabled_environment_flag("RATE_LIMIT_TRUST_CLOUDFLARE"):
        client_ip = _validated_client_ip(request.headers.get("cf-connecting-ip"))
    elif _enabled_environment_flag("RATE_LIMIT_TRUST_X_FORWARDED_FOR"):
        forwarded_for = request.headers.get("x-forwarded-for", "")
        client_ip = _validated_client_ip(forwarded_for.split(",", 1)[0])
    if client_ip is None:
        client_ip = _validated_client_ip(request.client.host if request.client else None) or "unknown"
    subject = f"ip:{client_ip}"
    request.state.rate_limit_subject = subject
    return subject


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
        subject = rate_limit_client_key(request)
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
    key_func=rate_limit_client_key,
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URL", "redis://localhost:6379/0"),
    key_prefix=os.getenv("RATE_LIMIT_KEY_PREFIX", ""),
    headers_enabled=False,
)
app.state.limiter = limiter
app.state.db_session_factory = SessionLocal
LOGIN_RATE_LIMIT = limiter.shared_limit("5/5 minutes", scope="login")
MFA_RATE_LIMIT = limiter.shared_limit("10/10 minutes", scope="mfa_totp")
ACCOUNT_SECURITY_RATE_LIMIT = limiter.shared_limit("5/5 minutes", scope="account_security")
ACCOUNT_REQUEST_RATE_LIMIT = limiter.shared_limit("3/hour", scope="account_request")
LIVE_CHUNK_UPLOAD_RATE_LIMIT = limiter.shared_limit(
    os.getenv("LIVE_CHUNK_UPLOAD_RATE_LIMIT", "1/second"),
    scope="live_chunk_upload",
    key_func=whole_file_upload_rate_limit_key,
)
WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT = limiter.shared_limit(
    os.getenv("WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT", "1/5 seconds"),
    scope="whole_file_upload_burst",
    key_func=whole_file_upload_rate_limit_key,
)
WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT = limiter.shared_limit(
    os.getenv("WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT", "100/day"),
    scope="whole_file_upload_daily",
    key_func=whole_file_upload_rate_limit_key,
)
LLM_GENERATION_BURST_RATE_LIMIT = limiter.shared_limit(
    os.getenv("LLM_GENERATION_BURST_RATE_LIMIT", "20/3 minutes"),
    scope="llm_generation_burst",
    key_func=whole_file_upload_rate_limit_key,
)
LLM_GENERATION_DAILY_RATE_LIMIT = limiter.shared_limit(
    os.getenv("LLM_GENERATION_DAILY_RATE_LIMIT", "200/day"),
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
    redirect_to = _post_login_redirect(context) if context is not None else "/login"
    return RedirectResponse(url=redirect_to, status_code=status.HTTP_303_SEE_OTHER)


app.add_exception_handler(404, browser_not_found_handler)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


SENSITIVE_NO_STORE_PATH_PREFIXES = (
    "/admin",
    "/onboarding",
    "/mfa/challenge",
    "/api/v1/transcribe",
    "/api/v1/transcripts",
    "/api/v1/generated-documents",
    "/api/v1/post-consultation-dictation",
    "/auth/oidc/",
    "/workspace",
)
PUBLIC_NO_STORE_PATHS = {
    "/login",
    "/forgot-password",
    "/request-access",
    "/settings",
    "/reset-password",
    "/activate-account",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/privacy",
    "/cookies",
    "/terms",
}
CSRF_COOKIE_SKIP_PATHS = {
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/security.txt",
    "/privacy",
    "/cookies",
    "/terms",
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
SECURITY_HEADER_X_ROBOTS_TAG = "noindex, nofollow, noarchive, nosnippet, noimageindex"
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
    request.state.legal_footer_links = ()
    request.state.legal_cookie_notice_version = "unpublished"
    request.state.operator_legal_profile = None
    path = request.url.path
    if (
        request.method == "GET"
        and not path.startswith("/api/")
        and not path.startswith("/static/")
        and path not in {"/health", "/robots.txt", "/sitemap.xml", "/.well-known/security.txt"}
    ):
        from app.services.legal_content import (
            get_operator_legal_profile,
            published_legal_footer_state,
        )

        session_factory = getattr(request.app.state, "db_session_factory", SessionLocal)
        with session_factory() as legal_db:
            request.state.legal_footer_links, cookie_notice_version = published_legal_footer_state(legal_db)
            request.state.operator_legal_profile = get_operator_legal_profile(legal_db)
            cookie_token = str(cookie_notice_version) if cookie_notice_version is not None else "unpublished"
            profile_token = (
                str(request.state.operator_legal_profile.revision)
                if request.state.operator_legal_profile is not None
                else "unpublished"
            )
            request.state.legal_cookie_notice_version = f"{cookie_token}:{profile_token}"
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
    response.headers.setdefault("X-Robots-Tag", SECURITY_HEADER_X_ROBOTS_TAG)
    response.headers.setdefault(
        "Content-Security-Policy",
        content_security_policy(
            request.state.csp_nonce,
            upgrade_insecure_requests=is_https,
            oidc_form_action_origins=getattr(
                request.state,
                "oidc_form_action_origins",
                (),
            ),
        ),
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
    if getattr(request.state, "session_cookie_issued", False):
        return response
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


@app.middleware("http")
async def redact_oidc_callback_query_from_access_log(request: Request, call_next):
    if (
        request.method == "GET"
        and request.url.path.startswith("/auth/oidc/")
        and request.url.path.endswith("/callback")
        and request.scope.get("query_string")
    ):
        raw_query = request.scope["query_string"]
        parsed: dict[str, str] = {}
        if len(raw_query) <= 4096:
            try:
                pairs = parse_qsl(
                    raw_query.decode("ascii"),
                    keep_blank_values=True,
                    max_num_fields=8,
                )
                if len({key for key, _value in pairs}) == len(pairs):
                    parsed = dict(pairs)
            except (UnicodeDecodeError, ValueError):
                parsed = {}
        request.state.oidc_callback_query = parsed
        # Uvicorn builds its access-log target from the mutable ASGI scope when
        # the response starts. Remove one-time codes and state before that point.
        request.scope["query_string"] = b""
    return await call_next(request)


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    request.state.session_cookie_issued = True
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
    return "/admin" if context.user.is_system_admin else "/workspace"


def _post_login_redirect_for_user(user: User) -> str:
    auth_level = determine_auth_level(user)
    if auth_level.value == "onboarding":
        return "/onboarding"
    return "/admin" if user.is_system_admin else "/workspace"


def _bootstrap_allowed(db: Session) -> bool:
    return bootstrap_admin_is_configured() and user_count_service(db) == 0


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
        return require_full_context(request, context, db)


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


def _template_config_from_form(*, mode: TemplateMode, section_values: dict[str, str]) -> dict | None:
    if mode is not TemplateMode.structured:
        return None
    sections: list[dict[str, object]] = []
    for index, section_key in enumerate(EMIS_SECTION_KEYS, start=1):
        instruction = (section_values.get(section_key) or "").strip()
        if instruction:
            sections.append(
                {
                    "section_key": section_key,
                    "instruction": instruction,
                    "section_order": index,
                }
            )
    if not sections:
        return None
    return {"profile": "emis", "sections": sections}


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
    require_full_context(request, context, db)
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
    code: {"model": ErrorResponse}
    for code in (401, 403, 404, 409, 429, 413, 422)
}


from .routes import api_routes as _api_routes  # noqa: F401
from .routes import web_admin as _web_admin  # noqa: F401
from .routes import web_home_transcribe as _web_home_transcribe  # noqa: F401
from .routes import web_oidc as _web_oidc  # noqa: F401
from .routes import web_pages as _web_pages  # noqa: F401
from .routes import web_team_management as _web_team_management  # noqa: F401
from .routes import web_transcribe as _web_transcribe  # noqa: F401


app.include_router(api)
