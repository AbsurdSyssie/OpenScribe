"""Admin browser routes extracted from app.main."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from pydantic import ValidationError

from .. import main as main_module
from ..main import *  # noqa: F401,F403
from ..main import (
    _admin_page_route_from_return_view,
    _admin_redirect_url,
    _admin_return_view_value,
    _page_context_or_redirect,
)
from ..stt_normalization import normalize_stt_language
from ..models import LegalDocumentKind, LlmConfigSetupStatus, SecurityAuditHoldReason, SttConfigSetupStatus, Team, TeamLlmConfig, TeamSttConfig
from ..services.admin import update_team_default_retention as update_team_default_retention_service
from ..schemas import HallucinationCheckSelectionUpsert
from ..services.llm import (
    clear_team_hallucination_check_selection as clear_team_hallucination_check_selection_service,
    set_team_hallucination_check_selection as set_team_hallucination_check_selection_service,
    update_llm_config_details as update_llm_config_details_service,
)
from ..services.stt import update_stt_config_details as update_stt_config_details_service
from ..web.presentation import default_template_return_tab as resolve_default_template_return_tab
from ..models import QuotaPeriod, QuotaResource, User, UserQuotaReasonCode
from ..services.admin_quotas import (
    grant_user_quota_batch,
    reset_user_quota_batch,
    revoke_user_quota_grant,
    update_user_base_quotas_batch,
)
from ..schemas.legal_content import (
    LegalDocumentContent,
    LegalDocumentDraftCreate,
    LegalDocumentDraftUpdate,
    OperatorLegalProfileUpdate,
)
from ..services.legal_content import (
    create_legal_document_draft,
    create_legal_document_rollback_draft,
    get_operator_legal_profile,
    list_legal_document_versions,
    operator_legal_setup_warnings,
    publish_legal_document_draft,
    update_legal_document_draft,
    update_operator_legal_profile,
)
from ..services.legal_content_markdown import (
    LegalMarkdownError,
    LegalMarkdownParseResult,
    legal_content_to_markdown,
    parse_legal_markdown_result,
)
from ..services.legal_content_retention import (
    active_legal_document_holds,
    place_legal_document_hold,
    release_legal_document_hold,
)
from ..services.audit_retention import (
    place_security_audit_hold,
    release_security_audit_hold,
    renew_security_audit_hold,
)


def _quota_panel_target(db: Session, user_id: UUID) -> User | None:
    """Navigation scope only; quota service remains mutation authority."""
    target = db.get(User, user_id)
    if target is None or target.is_system_admin or target.team_id is None or target.team_role is None:
        return None
    return target


def _quota_panel_url(target: User, notice: str | None = None) -> str:
    params = {"team_id": str(target.team_id), "team_tab": "members", "member_id": str(target.id)}
    if notice:
        params["quota_notice"] = notice
    return f"/admin?{urlencode(params)}"


def _quota_error_page(request: Request, db: Session, actor: User, user_id: UUID, exc: AppError, values: dict[str, object]):
    target = _quota_panel_target(db, user_id)
    if target is None:
        return HTMLResponse("Not found", status_code=status.HTTP_404_NOT_FOUND)
    return render_admin(
        request, db, current_user=actor, selected_team_id=str(target.team_id), workspace_team_tab="members",
        selected_quota_member_id=str(target.id), quota_form_values=values, message=exc.message,
        message_kind="error", status_code=exc.status_code, active_admin_tab="home", admin_page_route="/admin",
        admin_return_view="workspace",
    )


def _quota_audio_seconds(hours: str) -> int:
    try:
        return int((Decimal(hours) * Decimal(3600)).to_integral_value())
    except (InvalidOperation, ValueError, OverflowError):
        raise AppError(422, "quota_limit_invalid", "Audio hours must be a number")


def _quota_expiry(preset: str, custom: str) -> tuple[datetime | None, str | None]:
    if preset == "none":
        return None, None
    if preset in {"24h", "7d", "end_today", "end_month"}:
        return None, preset
    if preset == "custom":
        try:
            parsed = datetime.fromisoformat(custom.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(422, "quota_expiry_invalid", "Custom expiry must be a valid UTC date and time")
        absolute = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        return absolute, None
    raise AppError(422, "quota_expiry_invalid", "Quota expiry preset is invalid")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    team_id: str | None = None,
    stt_config_id: str | None = None,
    llm_config_id: str | None = None,
    deidentification_provider_id: str | None = None,
    default_template_id: str | None = None,
    default_quick_action_id: str | None = None,
    tab: str | None = None,
    team_tab: str | None = None,
    member_id: str | None = None,
    quota_notice: str | None = None,
    range: str | None = None,
    audit_since: str | None = None,
    audit_action: str | None = None,
    audit_notice: str | None = None,
    kind: str = LegalDocumentKind.privacy.value,
    version_id: UUID | None = None,
    notice: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    if team_id:
        try:
            parsed_team_id = UUID(team_id)
        except ValueError:
            parsed_team_id = None
        if parsed_team_id is None or db.get(Team, parsed_team_id) is None:
            team_id = None
    team_tabs = {"overview", "members", "provider-policy", "stt", "llm", "deidentification", "defaults", "usage", "security", "danger"}
    if team_tab in team_tabs:
        resolved_team_tab = team_tab
    elif team_id and tab == "providers":
        resolved_team_tab = "provider-policy"
    else:
        resolved_team_tab = "overview" if team_id and tab not in {"directory", "requests", "system-admins", "global-defaults", "deid-providers", "usage", "audit", "legal"} else None
    # Member URL state is only meaningful in an explicit selected Members scope.
    # render_admin repeats membership eligibility before it reads quota data.
    if not (team_id and resolved_team_tab == "members" and member_id):
        member_id = None
    safe_notice = quota_notice if quota_notice in {"limits_updated", "grant_created", "usage_reset", "grant_revoked"} else None
    functional_tabs = {
        "providers",
        "directory",
        "requests",
        "system-admins",
        "global-defaults",
        "deid-providers",
        "usage",
        "defaults",
        "audit",
        "legal",
    }
    resolved_global_tab = tab if tab in functional_tabs else "providers"
    if resolved_global_tab == "legal":
        return _legal_admin_page(
            request,
            db,
            actor=context.user,
            selected_kind=_legal_kind(kind),
            selected_version_id=version_id,
            message=_LEGAL_ADMIN_MESSAGES.get(notice),
            message_kind=_legal_notice_kind(notice),
        )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_stt_config_id=stt_config_id,
        selected_llm_config_id=llm_config_id,
        selected_deidentification_provider_id=deidentification_provider_id,
        selected_default_template_id=default_template_id,
        selected_default_quick_action_id=default_quick_action_id,
        active_admin_tab=resolved_global_tab,
        admin_page_route="/admin",
        admin_return_view="workspace",
        template_name="admin_mockup.html",
        workspace_team_tab=resolved_team_tab,
        selected_quota_member_id=member_id,
        message={
            "limits_updated": "Quota limits updated.", "grant_created": "Quota allowance added.",
            "usage_reset": "Quota usage reset.", "grant_revoked": "Quota allowance revoked.",
        }.get(safe_notice) or {
            "hold_placed": "Audit hold placed.",
            "hold_renewed": "Audit hold renewed.",
            "hold_released": "Audit hold released.",
        }.get(audit_notice),
    )


def _legal_admin_page(
    request: Request,
    db: Session,
    *,
    actor: User,
    selected_kind: LegalDocumentKind,
    selected_version_id: UUID | None = None,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
    profile_form: dict[str, str] | None = None,
    markdown_source: str | None = None,
    effective_on: str | None = None,
):
    versions = list_legal_document_versions(db, kind=selected_kind)
    active_holds = active_legal_document_holds(db, version_ids=[version.id for version in versions])
    selected_version = next(
        (version for version in versions if version.id == selected_version_id),
        versions[0] if versions else None,
    )
    selected_content = None
    if selected_version is not None:
        selected_content = LegalDocumentContent.model_validate({"blocks": selected_version.blocks_json})
    selected_markdown = (
        markdown_source
        if markdown_source is not None
        else legal_content_to_markdown(selected_content)
        if selected_content is not None
        else "## Section heading\n\nApproved plain text."
    )
    preview_content = selected_content if markdown_source is None else None
    return render_admin(
        request,
        db,
        current_user=actor,
        active_admin_tab="legal",
        admin_page_route="/admin",
        admin_return_view="workspace",
        template_name="admin_mockup.html",
        message=message,
        message_kind=message_kind,
        status_code=status_code,
        legal_context={
            "profile": profile_form if profile_form is not None else get_operator_legal_profile(db),
            "setup_warnings": operator_legal_setup_warnings(db),
            "document_kinds": list(LegalDocumentKind),
            "selected_kind": selected_kind,
            "versions": versions,
            "active_holds": active_holds,
            "selected_version": selected_version,
            "selected_content": selected_content,
            "legal_preview_content": preview_content,
            "legal_markdown_source": selected_markdown,
            "legal_effective_on": effective_on
            or (selected_version.effective_on.isoformat() if selected_version is not None else date.today().isoformat()),
            "today": date.today().isoformat(),
        },
    )


_LEGAL_ADMIN_MESSAGES = {
    "profile_saved": "Operator profile saved.",
    "draft_created": "Draft created.",
    "draft_saved": "Draft saved.",
    "draft_created_scrubbed": "Draft created. Some unsupported formatting was removed. Check the preview before publishing.",
    "draft_saved_scrubbed": "Draft saved. Some unsupported formatting was removed. Check the preview before publishing.",
    "published": "Draft published.",
    "rollback_created": "Rollback draft created.",
    "hold_placed": "Legal hold placed.",
    "hold_released": "Legal hold released.",
}


def _legal_notice_kind(notice: str | None) -> str:
    return "warning" if notice in {"draft_created_scrubbed", "draft_saved_scrubbed"} else "success"


def _legal_admin_url(
    *,
    kind: LegalDocumentKind = LegalDocumentKind.privacy,
    version_id: UUID | None = None,
    notice: str | None = None,
) -> str:
    params: dict[str, str] = {"tab": "legal", "kind": kind.value}
    if version_id is not None:
        params["version_id"] = str(version_id)
    if notice is not None:
        params["notice"] = notice
    return f"/admin?{urlencode(params)}"


_OPERATOR_PROFILE_FIELD_LABELS = {
    "expected_revision": "Profile revision",
    "legal_name": "Legal name",
    "display_name": "Display name",
    "company_number": "Company number",
    "public_url": "Public HTTPS URL",
    "privacy_email": "Privacy email",
    "complaints_email": "Complaints email",
    "security_contact": "Security email",
    "postal_address": "Postal address",
    "cookie_banner_summary": "Cookie-banner summary",
}


def _operator_profile_validation_message(exc: ValidationError) -> str:
    first_error = exc.errors(include_input=False)[0]
    location = first_error.get("loc") or ()
    field_name = str(location[0]) if location else ""
    label = _OPERATOR_PROFILE_FIELD_LABELS.get(field_name, "Operator profile")
    detail = str(first_error.get("msg") or "is invalid").removeprefix("Value error, ")
    if field_name == "public_url" and detail.startswith("Link URL "):
        detail = detail.removeprefix("Link URL ")
    elif detail.lower().startswith(label.lower()):
        return detail
    return f"{label} {detail}"


def _legal_admin_context(request: Request, db: Session):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return None, response
    if not context.user.is_system_admin:
        return None, HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return context, None


def _legal_kind(value: str) -> LegalDocumentKind:
    try:
        return LegalDocumentKind(value)
    except ValueError as exc:
        raise AppError(422, "validation_error", "Legal document kind is invalid") from exc


def _legal_content_payload(markdown_source: str) -> LegalMarkdownParseResult:
    try:
        return parse_legal_markdown_result(markdown_source)
    except LegalMarkdownError as exc:
        raise AppError(422, "validation_error", str(exc)) from exc


@app.get("/admin/legal-content", response_class=HTMLResponse, include_in_schema=False)
def admin_legal_content_page(
    request: Request,
    kind: str = LegalDocumentKind.privacy.value,
    version_id: UUID | None = None,
    notice: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    safe_notice = notice if notice in _LEGAL_ADMIN_MESSAGES else None
    return RedirectResponse(
        _legal_admin_url(kind=selected_kind, version_id=version_id, notice=safe_notice),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@app.post("/admin/legal-content/profile", response_class=HTMLResponse, include_in_schema=False)
def admin_update_legal_profile(
    request: Request,
    kind: str = Form(LegalDocumentKind.privacy.value),
    expected_revision: str = Form(""),
    legal_name: str = Form(""),
    display_name: str = Form(""),
    company_number: str = Form(""),
    public_url: str = Form(""),
    privacy_email: str = Form(""),
    complaints_email: str = Form(""),
    security_contact: str = Form(""),
    postal_address: str = Form(""),
    cookie_banner_summary: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    profile_form = {
        "revision": expected_revision,
        "legal_name": legal_name,
        "display_name": display_name,
        "company_number": company_number,
        "public_url": public_url,
        "privacy_email": privacy_email,
        "complaints_email": complaints_email,
        "security_contact": security_contact,
        "postal_address": postal_address,
        "cookie_banner_summary": cookie_banner_summary,
    }
    error: AppError | None = None
    try:
        payload = OperatorLegalProfileUpdate(
            expected_revision=int(expected_revision) if expected_revision else None,
            legal_name=legal_name,
            display_name=display_name,
            company_number=company_number,
            public_url=public_url,
            privacy_email=privacy_email,
            complaints_email=complaints_email,
            security_contact=security_contact,
            postal_address=postal_address,
            cookie_banner_summary=cookie_banner_summary,
        )
        update_operator_legal_profile(db, actor=context.user, payload=payload)
    except AppError as exc:
        error = exc
    except ValidationError as exc:
        error = AppError(422, "validation_error", _operator_profile_validation_message(exc))
    except ValueError:
        error = AppError(422, "validation_error", "Profile revision is invalid; reload the page and try again")
    if error is not None:
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind,
            message=error.message, message_kind="error", status_code=error.status_code,
            profile_form=profile_form,
        )
    return RedirectResponse(
        _legal_admin_url(kind=selected_kind, notice="profile_saved"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/legal-content/drafts", response_class=HTMLResponse, include_in_schema=False)
def admin_create_legal_draft(
    request: Request,
    kind: str = Form(...),
    effective_on: str = Form(...),
    markdown_source: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = LegalDocumentKind.privacy
    try:
        selected_kind = _legal_kind(kind)
        parse_result = _legal_content_payload(markdown_source)
        version = create_legal_document_draft(
            db,
            actor=context.user,
            payload=LegalDocumentDraftCreate(
                kind=selected_kind,
                effective_on=date.fromisoformat(effective_on),
                content=parse_result.content,
            ),
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "validation_error", "Legal draft is invalid")
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind,
            message=error.message, message_kind="error", status_code=error.status_code,
            markdown_source=markdown_source, effective_on=effective_on,
        )
    return RedirectResponse(
        _legal_admin_url(
            kind=selected_kind,
            version_id=version.id,
            notice="draft_created_scrubbed" if parse_result.scrubbed_formatting else "draft_created",
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/legal-content/drafts/{version_id}", response_class=HTMLResponse, include_in_schema=False)
def admin_update_legal_draft(
    request: Request,
    version_id: UUID,
    kind: str = Form(...),
    expected_revision: int = Form(...),
    effective_on: str = Form(...),
    markdown_source: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    try:
        parse_result = _legal_content_payload(markdown_source)
        version = update_legal_document_draft(
            db,
            actor=context.user,
            version_id=version_id,
            payload=LegalDocumentDraftUpdate(
                expected_revision=expected_revision,
                effective_on=date.fromisoformat(effective_on),
                content=parse_result.content,
            ),
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "validation_error", "Legal draft is invalid")
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind, selected_version_id=version_id,
            message=error.message, message_kind="error", status_code=error.status_code,
            markdown_source=markdown_source, effective_on=effective_on,
        )
    return RedirectResponse(
        _legal_admin_url(
            kind=selected_kind,
            version_id=version.id,
            notice="draft_saved_scrubbed" if parse_result.scrubbed_formatting else "draft_saved",
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/legal-content/preview", response_class=HTMLResponse, include_in_schema=False)
def admin_preview_legal_markdown(
    request: Request,
    markdown_source: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    try:
        parse_result = _legal_content_payload(markdown_source)
    except AppError as exc:
        return HTMLResponse(exc.message, status_code=exc.status_code)
    preview_response = templates.TemplateResponse(
        request,
        "_legal_content_blocks.html",
        {"request": request, "blocks": parse_result.content.blocks},
    )
    if parse_result.scrubbed_formatting:
        preview_response.headers["X-OpenScribe-Legal-Formatting"] = "scrubbed"
    return preview_response


@app.post("/admin/legal-content/drafts/{version_id}/publish", response_class=HTMLResponse, include_in_schema=False)
def admin_publish_legal_draft(
    request: Request,
    version_id: UUID,
    kind: str = Form(...),
    expected_revision: int = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    try:
        version = publish_legal_document_draft(
            db, actor=context.user, version_id=version_id, expected_revision=expected_revision
        )
    except AppError as exc:
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind, selected_version_id=version_id,
            message=exc.message, message_kind="error", status_code=exc.status_code,
        )
    return RedirectResponse(
        _legal_admin_url(kind=selected_kind, version_id=version.id, notice="published"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/legal-content/versions/{version_id}/rollback", response_class=HTMLResponse, include_in_schema=False)
def admin_create_legal_rollback(
    request: Request,
    version_id: UUID,
    kind: str = Form(...),
    effective_on: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    try:
        version = create_legal_document_rollback_draft(
            db, actor=context.user, source_version_id=version_id, effective_on=date.fromisoformat(effective_on)
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "validation_error", "Rollback date is invalid")
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind, selected_version_id=version_id,
            message=error.message, message_kind="error", status_code=error.status_code,
        )
    return RedirectResponse(
        _legal_admin_url(kind=selected_kind, version_id=version.id, notice="rollback_created"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/legal-content/versions/{version_id}/holds", response_class=HTMLResponse, include_in_schema=False)
def admin_place_legal_document_hold(
    request: Request,
    version_id: UUID,
    kind: str = Form(...),
    reason: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    try:
        place_legal_document_hold(db, actor=context.user, version_id=version_id, reason=reason)
    except AppError as exc:
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind, selected_version_id=version_id,
            message=exc.message, message_kind="error", status_code=exc.status_code,
        )
    return RedirectResponse(
        _legal_admin_url(kind=selected_kind, version_id=version_id, notice="hold_placed"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/legal-content/holds/{hold_id}/release", response_class=HTMLResponse, include_in_schema=False)
def admin_release_legal_document_hold(
    request: Request,
    hold_id: UUID,
    version_id: UUID = Form(...),
    kind: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    selected_kind = _legal_kind(kind)
    try:
        release_legal_document_hold(db, actor=context.user, hold_id=hold_id)
    except AppError as exc:
        return _legal_admin_page(
            request, db, actor=context.user, selected_kind=selected_kind, selected_version_id=version_id,
            message=exc.message, message_kind="error", status_code=exc.status_code,
        )
    return RedirectResponse(
        _legal_admin_url(kind=selected_kind, version_id=version_id, notice="hold_released"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _audit_hold_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AppError(422, "validation_error", "Hold dates must be valid UTC date-times") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@app.post("/admin/audit/events/{event_id}/holds", response_class=HTMLResponse, include_in_schema=False)
def admin_place_security_audit_hold(
    request: Request,
    event_id: UUID,
    reason: str = Form(...),
    reference: str = Form(""),
    review_at: str = Form(...),
    expires_at: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    try:
        place_security_audit_hold(
            db, actor=context.user, event_id=event_id, reason=SecurityAuditHoldReason(reason),
            reference=reference, review_at=_audit_hold_datetime(review_at),
            expires_at=_audit_hold_datetime(expires_at),
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "validation_error", "Audit hold is invalid")
        return render_admin(
            request, db, current_user=context.user, active_admin_tab="audit", admin_page_route="/admin",
            admin_return_view="workspace", message=error.message, message_kind="error", status_code=error.status_code,
        )
    return RedirectResponse("/admin?tab=audit&audit_notice=hold_placed", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/audit/holds/{hold_id}/renew", response_class=HTMLResponse, include_in_schema=False)
def admin_renew_security_audit_hold(
    request: Request,
    hold_id: UUID,
    reason: str = Form(...),
    reference: str = Form(""),
    review_at: str = Form(...),
    expires_at: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    try:
        renew_security_audit_hold(
            db, actor=context.user, hold_id=hold_id, reason=SecurityAuditHoldReason(reason),
            reference=reference, review_at=_audit_hold_datetime(review_at),
            expires_at=_audit_hold_datetime(expires_at),
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "validation_error", "Audit hold renewal is invalid")
        return render_admin(
            request, db, current_user=context.user, active_admin_tab="audit", admin_page_route="/admin",
            admin_return_view="workspace", message=error.message, message_kind="error", status_code=error.status_code,
        )
    return RedirectResponse("/admin?tab=audit&audit_notice=hold_renewed", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/audit/holds/{hold_id}/release", response_class=HTMLResponse, include_in_schema=False)
def admin_release_security_audit_hold(
    request: Request,
    hold_id: UUID,
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _legal_admin_context(request, db)
    if response is not None:
        return response
    try:
        release_security_audit_hold(db, actor=context.user, hold_id=hold_id)
    except AppError as exc:
        return render_admin(
            request, db, current_user=context.user, active_admin_tab="audit", admin_page_route="/admin",
            admin_return_view="workspace", message=exc.message, message_kind="error", status_code=exc.status_code,
        )
    return RedirectResponse("/admin?tab=audit&audit_notice=hold_released", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/quotas/limits", response_class=HTMLResponse)
def admin_update_user_quota_limits(
    request: Request, user_id: UUID, daily_token_limit: str = Form(""), monthly_token_limit: str = Form(""),
    daily_audio_hours: str = Form(""), monthly_audio_hours: str = Form(""),
    daily_token_unlimited: str | None = Form(None), monthly_token_unlimited: str | None = Form(None),
    daily_audio_unlimited: str | None = Form(None), monthly_audio_unlimited: str | None = Form(None),
    reason_code: str = Form(""), reason: str = Form(""), operation_id: str = Form(""),
    csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    values = dict(request.query_params)
    values.update({"daily_token_limit": daily_token_limit, "monthly_token_limit": monthly_token_limit, "daily_audio_hours": daily_audio_hours, "monthly_audio_hours": monthly_audio_hours, "reason_code": reason_code, "reason": reason})
    try:
        result = update_user_base_quotas_batch(
            db, actor=context.user, user_id=user_id,
            daily_token_limit=None if daily_token_unlimited else int(daily_token_limit),
            monthly_token_limit=None if monthly_token_unlimited else int(monthly_token_limit),
            daily_audio_seconds_limit=None if daily_audio_unlimited else _quota_audio_seconds(daily_audio_hours),
            monthly_audio_seconds_limit=None if monthly_audio_unlimited else _quota_audio_seconds(monthly_audio_hours),
            operation_id=UUID(operation_id), reason_code=UserQuotaReasonCode(reason_code), reason=reason,
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "quota_limit_invalid", "Quota limits are invalid")
        return _quota_error_page(request, db, context.user, user_id, error, values)
    target = _quota_panel_target(db, result.user_id)
    return RedirectResponse(_quota_panel_url(target, "limits_updated"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/quota-grants", response_class=HTMLResponse)
def admin_grant_user_quota(
    request: Request, user_id: UUID, resource: str = Form(""), periods: list[str] = Form([]), amount: str = Form(""),
    audio_hours: str = Form(""), expiry_preset: str = Form("none"), expires_at: str = Form(""),
    reason_code: str = Form(""), reason: str = Form(""), operation_id: str = Form(""),
    csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None: return response
    if not context.user.is_system_admin: return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    values = {"resource": resource, "periods": periods, "amount": amount, "audio_hours": audio_hours, "expiry_preset": expiry_preset, "expires_at": expires_at, "reason_code": reason_code, "reason": reason}
    try:
        selected_resource = QuotaResource(resource)
        grant_amount = int(amount) if selected_resource is QuotaResource.tokens else _quota_audio_seconds(audio_hours)
        absolute_expiry, expiry_policy = _quota_expiry(expiry_preset, expires_at)
        result = grant_user_quota_batch(
            db, actor=context.user, user_id=user_id, resource=selected_resource,
            periods=tuple(QuotaPeriod(period) for period in periods), amount=grant_amount,
            expires_at=absolute_expiry, expiry_policy=expiry_policy, operation_id=UUID(operation_id),
            reason_code=UserQuotaReasonCode(reason_code), reason=reason,
        )
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "quota_grant_invalid", "Quota grant is invalid")
        return _quota_error_page(request, db, context.user, user_id, error, values)
    target = _quota_panel_target(db, result.user_id)
    return RedirectResponse(_quota_panel_url(target, "grant_created"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/quota-resets", response_class=HTMLResponse)
def admin_reset_user_quota(
    request: Request, user_id: UUID, windows: list[str] = Form([]), reason_code: str = Form(""), reason: str = Form(""),
    operation_id: str = Form(""), reset_all: str | None = Form(None),
    csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None: return response
    if not context.user.is_system_admin: return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    values = {"reason_code": reason_code, "reason": reason}
    try:
        selected = (
            tuple((resource, period) for resource in QuotaResource for period in QuotaPeriod)
            if reset_all
            else tuple((QuotaResource(value.split(":", 1)[0]), QuotaPeriod(value.split(":", 1)[1])) for value in windows)
        )
        result = reset_user_quota_batch(db, actor=context.user, user_id=user_id, windows=selected,
            operation_id=UUID(operation_id), reason_code=UserQuotaReasonCode(reason_code), reason=reason)
    except (ValueError, IndexError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "quota_windows_invalid", "Choose one or more quota windows")
        return _quota_error_page(request, db, context.user, user_id, error, values)
    target = _quota_panel_target(db, result.user_id)
    return RedirectResponse(_quota_panel_url(target, "usage_reset"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/quota-grants/{grant_id}/revoke", response_class=HTMLResponse)
def admin_revoke_user_quota_grant(
    request: Request, user_id: UUID, grant_id: UUID, reason_code: str = Form(""), reason: str = Form(""),
    revocation_operation_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None: return response
    if not context.user.is_system_admin: return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    values = {"reason_code": reason_code, "reason": reason}
    try:
        result = revoke_user_quota_grant(db, actor=context.user, user_id=user_id, grant_id=grant_id,
            revocation_operation_id=UUID(revocation_operation_id), reason_code=UserQuotaReasonCode(reason_code), reason=reason)
    except (ValueError, AppError) as exc:
        error = exc if isinstance(exc, AppError) else AppError(422, "quota_grant_invalid", "Quota grant revocation is invalid")
        return _quota_error_page(request, db, context.user, user_id, error, values)
    target = _quota_panel_target(db, result.user_id)
    return RedirectResponse(_quota_panel_url(target, "grant_revoked"), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin-restyled", response_class=HTMLResponse)
def admin_restyled_page(
    request: Request,
    team_id: str | None = None,
    stt_config_id: str | None = None,
    llm_config_id: str | None = None,
    deidentification_provider_id: str | None = None,
    default_template_id: str | None = None,
    default_quick_action_id: str | None = None,
    tab: str | None = None,
    team_tab: str | None = None,
    range: str | None = None,
    audit_since: str | None = None,
    audit_action: str | None = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    params = {
        key: value
        for key, value in {
            "team_id": team_id,
            "stt_config_id": stt_config_id,
            "llm_config_id": llm_config_id,
            "deidentification_provider_id": deidentification_provider_id,
            "default_template_id": default_template_id,
            "default_quick_action_id": default_quick_action_id,
            "tab": tab,
            "team_tab": team_tab,
            "range": range,
            "audit_since": audit_since,
            "audit_action": audit_action,
        }.items()
        if value is not None
    }
    location = "/admin"
    if params:
        location = f"{location}?{urlencode(params)}"
    return RedirectResponse(url=location, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


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
        create_team_service(db, TeamCreate(name=name, status=status_value, default_retention_days=default_retention_days), actor=context.user)
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


@app.post("/admin/teams/{team_id}/delete", response_class=HTMLResponse)
def admin_delete_team(
    request: Request,
    team_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form("directory"),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_team_service(db, context.user, team_id=team_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=str(team_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "directory",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/teams/{team_id}/retention", response_class=HTMLResponse)
def admin_update_team_retention(
    request: Request,
    team_id: UUID,
    default_retention_days: int = Form(...),
    return_view: str = Form("workspace"),
    return_tab: str = Form("defaults"),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        update_team_default_retention_service(db, context.user, team_id=team_id, default_retention_days=default_retention_days)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=str(team_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab,
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab, team_id=str(team_id)),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/templates/editor", response_class=HTMLResponse)
def admin_template_editor_page(
    request: Request,
    scope: str,
    template_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    return_view: str = "",
    return_tab: str = "",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return_tab = resolve_default_template_return_tab(return_view, return_tab)
    if scope != "default":
        return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab), status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_default_template_id=template_id,
        message=message,
        message_kind=safe_message_kind,
        active_admin_tab=return_tab,
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
        default_template_return_tab=return_tab,
        template_name="template_editor.html",
    )


@app.post("/admin/default-templates", response_class=HTMLResponse)
def admin_upsert_default_template(
    request: Request,
    template_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
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
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return_tab = resolve_default_template_return_tab(return_view, return_tab)
    try:
        template_mode = TemplateMode(mode)
        template = upsert_default_template_service(
            db,
            context.user,
            DefaultPromptTemplateUpsert(
                template_id=UUID(template_id) if template_id else None,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                mode=template_mode,
                config_json=main_module._template_config_from_form(
                    mode=template_mode,
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
        detail = exc.message if isinstance(exc, AppError) else "Invalid default template"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_template_id=template_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab,
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
            default_template_return_tab=return_tab,
            template_name="template_editor.html",
        )
    return RedirectResponse(
        url=(
            f"/admin/templates/editor?scope=default&template_id={template.id}"
            f"&return_view={_admin_return_view_value(return_view)}&return_tab={return_tab}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/default-templates/{template_id}/delete", response_class=HTMLResponse)
def admin_delete_default_template(
    request: Request,
    template_id: UUID,
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
    return_tab = resolve_default_template_return_tab(return_view, return_tab)
    try:
        delete_default_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab,
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
            default_template_return_tab=return_tab,
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/default-templates/{template_id}/duplicate", response_class=HTMLResponse)
def admin_duplicate_default_template(
    request: Request,
    template_id: UUID,
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
    return_tab = resolve_default_template_return_tab(return_view, return_tab)
    try:
        duplicated = duplicate_default_template_service(db, context.user, template_id=template_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_template_id=str(template_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab,
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
            default_template_return_tab=return_tab,
        )
    return RedirectResponse(
        url=(
            f"/admin/templates/editor?scope=default&template_id={duplicated.id}"
            f"&return_view={_admin_return_view_value(return_view)}&return_tab={return_tab}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/default-quick-actions", response_class=HTMLResponse)
def admin_upsert_default_quick_action(
    request: Request,
    quick_action_id: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form("defaults"),
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
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        upsert_default_quick_action_service(
            db,
            context.user,
            DefaultQuickActionUpsert(
                quick_action_id=UUID(quick_action_id) if quick_action_id else None,
                name=name,
                description=description or None,
                prompt_text=prompt_text,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid default quick action"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_quick_action_id=quick_action_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "defaults"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/default-quick-actions/{quick_action_id}/delete", response_class=HTMLResponse)
def admin_delete_default_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form("defaults"),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        delete_default_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "defaults"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/default-quick-actions/{quick_action_id}/duplicate", response_class=HTMLResponse)
def admin_duplicate_default_quick_action(
    request: Request,
    quick_action_id: UUID,
    return_view: str = Form(""),
    return_tab: str = Form("defaults"),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        duplicate_default_quick_action_service(db, context.user, quick_action_id=quick_action_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_default_quick_action_id=str(quick_action_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "defaults",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "defaults"), status_code=status.HTTP_303_SEE_OTHER)


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
    credential_action: str = Form("keep"),
    provider_model: str = Form(""),
    stt_model_field_name: str = Form("", alias="model_field_name"),
    file_field_name: str = Form(""),
    language: str = Form(""),
    language_field_name: str = Form(""),
    response_text_path: str = Form(""),
    segments_path: str = Form(""),
    segment_text_field: str = Form(""),
    segment_start_field: str = Form(""),
    segment_end_field: str = Form(""),
    segment_speaker_field: str = Form(""),
    extra_form_fields_json: str = Form(""),
    is_active: str | None = Form(default=None),
    confirm_duplicate: str | None = Form(default=None),
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
                credential_action="replace" if bearer_token else credential_action,
                model_name=provider_model or None,
                model_field_name=stt_model_field_name or None,
                file_field_name=file_field_name or "file",
                language=normalize_stt_language(language),
                language_field_name=language_field_name or None,
                response_text_path=response_text_path or "text",
                segments_path=segments_path or None,
                segment_text_field=segment_text_field or None,
                segment_start_field=segment_start_field or None,
                segment_end_field=segment_end_field or None,
                segment_speaker_field=segment_speaker_field or None,
                extra_form_fields_json=parse_extra_form_fields_json(extra_form_fields_json),
                is_active=is_active == "true",
                confirm_duplicate=confirm_duplicate == "true",
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
            active_provider_tab="stt",
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
        stt_test_result = main_module.run_saved_stt_config_test_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
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


@app.post("/admin/stt-configs/{config_id}/inspect", response_class=HTMLResponse)
def admin_reinspect_stt_config(
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
        config = main_module.reinspect_stt_config_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT re-inspection request"
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
        selected_stt_config_id=str(config.id),
        message=f"STT provider re-inspected: {config.credential_status.value}.",
        message_kind="success" if config.credential_status in {ProviderCredentialStatus.verified, ProviderCredentialStatus.partial} else "error",
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
        },
        message="STT endpoint inspected. Review the inferred fields before saving.",
        active_admin_tab=return_tab or "providers",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/stt-configs/drafts", response_class=HTMLResponse)
def admin_create_stt_config_draft(
    request: Request,
    team_id: str = Form(...),
    label: str = Form(""),
    provider_preset: str = Form(""),
    base_url: str = Form(""),
    openapi_path: str = Form(""),
    bearer_token: str = Form(""),
    revision_of_config_id: str = Form(""),
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
        config, _inspection = create_stt_config_draft_service(
            db,
            context.user,
            SttConfigDraftCreate(
                team_id=UUID(team_id),
                revision_of_config_id=UUID(revision_of_config_id) if revision_of_config_id else None,
                provider_preset=provider_preset,
                label=label or None,
                base_url=base_url,
                openapi_path=openapi_path or None,
                bearer_token=bearer_token or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT draft request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        stt_form = stt_form_defaults(None, None)
        stt_form.update({"label": label, "provider_preset": provider_preset or stt_form["provider_preset"], "base_url": base_url or stt_form["base_url"], "openapi_path": openapi_path or stt_form["openapi_path"]})
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            selected_stt_config_id=None,
            stt_form_override=stt_form,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="stt",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    redirect_url = _admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id)
    separator = "&" if "?" in redirect_url else "?"
    return RedirectResponse(url=f"{redirect_url}{separator}stt_config_id={config.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-configs/{config_id}/finalize", response_class=HTMLResponse)
def admin_finalize_stt_config_draft(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    label: str = Form(...),
    provider_model: str = Form(""),
    language: str = Form(""),
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
    try:
        finalize_stt_config_draft_service(
            db,
            context.user,
            SttConfigFinalize(team_id=UUID(team_id), config_id=config_id, label=label, model_name=provider_model or None, language=normalize_stt_language(language), is_active=is_active == "true"),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT finalization request"
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
            active_provider_tab="stt",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-configs/{config_id}/details", response_class=HTMLResponse)
def admin_update_stt_config_details(request: Request, config_id: UUID, team_id: str = Form(...), label: str = Form(...), is_active: str | None = Form(default=None), return_view: str = Form("workspace"), return_tab: str = Form("stt"), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        update_stt_config_details_service(db, context.user, config_id=config_id, team_id=UUID(team_id), label=label, is_active=is_active == "true")
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT details request"
        code = exc.status_code if isinstance(exc, AppError) else 400
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=code, active_admin_tab="home", workspace_team_tab=return_tab, admin_page_route="/admin", admin_return_view="workspace")
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab, team_id=team_id), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-configs/{config_id}/draft-cancel", response_class=HTMLResponse)
def admin_cancel_stt_config_draft(request: Request, config_id: UUID, team_id: str = Form(...), return_view: str = Form("workspace"), return_tab: str = Form("stt"), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        main_module.cancel_stt_config_draft_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT draft cancellation"
        code = exc.status_code if isinstance(exc, AppError) else 400
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=code, active_admin_tab=return_tab, admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab, team_id=team_id), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-configs/{config_id}/replace-credential", response_class=HTMLResponse)
def admin_replace_stt_config_draft_credential(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    bearer_token: str = Form(...),
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
        replace_stt_config_draft_credential_service(db, context.user, SttConfigDraftReplaceCredential(team_id=UUID(team_id), config_id=config_id, bearer_token=bearer_token))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid STT credential replacement request"
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
            active_provider_tab="stt",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    redirect_url = _admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id)
    separator = "&" if "?" in redirect_url else "?"
    return RedirectResponse(url=f"{redirect_url}{separator}stt_config_id={config_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/stt-selection", response_class=HTMLResponse)
def admin_set_stt_selection(
    request: Request,
    team_id: str = Form(...),
    stt_config_id: str = Form(...),
    purpose: str = Form("conversation"),
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
                purpose=SttSelectionPurpose(purpose),
                stt_config_id=UUID(stt_config_id),
                model_name_override=provider_model or None,
                language_override=normalize_stt_language(language),
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
    purpose: str = Form("conversation"),
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
        clear_team_stt_selection_service(db, context.user, team_id=UUID(team_id), purpose=SttSelectionPurpose(purpose))
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
    provider_preset: str = Form(""),
    adapter_kind: str = Form(LlmAdapterKind.openai_chat.value),
    base_url: str = Form(""),
    bedrock_region: str = Form(""),
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
                provider_preset=provider_preset,
                adapter_kind=LlmAdapterKind(adapter_kind),
                base_url=base_url,
                bedrock_region=bedrock_region or None,
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
            "provider_preset": inspection.provider_preset,
            "adapter_kind": inspection.adapter_kind.value,
            "bedrock_region": (
                bedrock_region or bedrock_region_from_base_url(inspection.base_url) or DEFAULT_BEDROCK_CHAT_REGION
                if inspection.adapter_kind is LlmAdapterKind.bedrock_chat
                else ""
            ),
        },
        message="LLM models discovered. Review the inferred fields before saving.",
        active_admin_tab=return_tab or "providers",
        active_provider_tab="llm",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/llm-configs/{config_id}/inspect", response_class=HTMLResponse)
def admin_inspect_saved_llm_config(
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
        inspection = main_module.inspect_saved_llm_config_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid saved LLM inspection request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            selected_llm_config_id=str(config_id),
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="llm",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id,
        selected_llm_config_id=str(config_id),
        llm_inspection=inspection,
        message="LLM provider re-inspected using saved credential.",
        message_kind="success" if inspection.discovery_status == "fetched" else "error",
        active_admin_tab=return_tab or "providers",
        active_provider_tab="llm",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/llm-configs/drafts", response_class=HTMLResponse)
def admin_create_llm_config_draft(
    request: Request,
    team_id: str = Form(...),
    label: str = Form(""),
    provider_preset: str = Form(""),
    base_url: str = Form(""),
    bedrock_region: str = Form(""),
    bearer_token: str = Form(""),
    revision_of_config_id: str = Form(""),
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
        config, _inspection = create_llm_config_draft_service(
            db,
            context.user,
            LlmConfigDraftCreate(
                team_id=UUID(team_id),
                revision_of_config_id=UUID(revision_of_config_id) if revision_of_config_id else None,
                provider_preset=provider_preset,
                label=label or None,
                base_url=base_url,
                bedrock_region=bedrock_region or None,
                bearer_token=bearer_token or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = "The API key was rejected by the provider. Check the key and try again." if isinstance(exc, AppError) and exc.code == "llm_invalid_credential" else (exc.message if isinstance(exc, AppError) else "Invalid LLM draft request")
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        llm_form = llm_form_defaults(None, None)
        llm_form.update(
            {
                "label": label,
                "provider_preset": provider_preset or llm_form["provider_preset"],
                "base_url": base_url or llm_form["base_url"],
                "bedrock_region": bedrock_region or llm_form["bedrock_region"],
            }
        )
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            selected_llm_config_id=None,
            llm_form_override=llm_form,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="llm",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    redirect_url = _admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id)
    separator = "&" if "?" in redirect_url else "?"
    return RedirectResponse(
        url=f"{redirect_url}{separator}llm_config_id={config.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-configs/{config_id}/finalize", response_class=HTMLResponse)
def admin_finalize_llm_config_draft(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    label: str = Form(...),
    provider_model: str = Form(...),
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
    try:
        finalize_llm_config_draft_service(
            db,
            context.user,
            LlmConfigFinalize(
                team_id=UUID(team_id),
                config_id=config_id,
                label=label,
                model_name=provider_model,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM finalization request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            selected_llm_config_id=str(config_id),
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="llm",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-configs/{config_id}/details", response_class=HTMLResponse)
def admin_update_llm_config_details(request: Request, config_id: UUID, team_id: str = Form(...), label: str = Form(...), is_active: str | None = Form(default=None), return_view: str = Form("workspace"), return_tab: str = Form("llm"), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        update_llm_config_details_service(db, context.user, config_id=config_id, team_id=UUID(team_id), label=label, is_active=is_active == "true")
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM details request"
        code = exc.status_code if isinstance(exc, AppError) else 400
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=code, active_admin_tab="home", workspace_team_tab=return_tab, admin_page_route="/admin", admin_return_view="workspace")
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab, team_id=team_id), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/llm-configs/{config_id}/draft-cancel", response_class=HTMLResponse)
def admin_cancel_llm_config_draft(request: Request, config_id: UUID, team_id: str = Form(...), return_view: str = Form("workspace"), return_tab: str = Form("llm"), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        main_module.cancel_llm_config_draft_service(db, context.user, config_id=config_id, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM draft cancellation"
        code = exc.status_code if isinstance(exc, AppError) else 400
        return render_admin(request, db, current_user=context.user, selected_team_id=team_id, message=detail, message_kind="error", status_code=code, active_admin_tab=return_tab, admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab, team_id=team_id), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/llm-configs/{config_id}/replace-credential", response_class=HTMLResponse)
def admin_replace_llm_config_draft_credential(
    request: Request,
    config_id: UUID,
    team_id: str = Form(...),
    bearer_token: str = Form(...),
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
        replace_llm_config_draft_credential_service(
            db,
            context.user,
            LlmConfigDraftReplaceCredential(team_id=UUID(team_id), config_id=config_id, bearer_token=bearer_token),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid LLM credential replacement request"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id,
            selected_llm_config_id=str(config_id),
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            active_provider_tab="llm",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    redirect_url = _admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id)
    separator = "&" if "?" in redirect_url else "?"
    return RedirectResponse(
        url=f"{redirect_url}{separator}llm_config_id={config_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/llm-configs", response_class=HTMLResponse)
def admin_upsert_llm_config(
    request: Request,
    team_id: str = Form(...),
    config_id: str = Form(""),
    label: str = Form(...),
    provider_preset: str = Form(""),
    adapter_kind: str = Form(LlmAdapterKind.openai_chat.value),
    base_url: str = Form(""),
    bedrock_region: str = Form(""),
    bearer_token: str = Form(""),
    credential_action: str = Form("keep"),
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
    try:
        upsert_llm_config_service(
            db,
            context.user,
            LlmConfigUpsert(
                config_id=UUID(config_id) if config_id else None,
                team_id=UUID(team_id),
                label=label,
                provider_preset=provider_preset,
                adapter_kind=LlmAdapterKind(adapter_kind),
                base_url=base_url,
                bedrock_region=bedrock_region or None,
                bearer_token=bearer_token or None,
                credential_action="replace" if bearer_token else credential_action,
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
            active_provider_tab="llm",
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


@app.post("/admin/hallucination-check-selection", response_class=HTMLResponse)
def admin_set_hallucination_check_selection(
    request: Request,
    team_id: str = Form(...),
    llm_config_id: str = Form(...),
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
        set_team_hallucination_check_selection_service(
            db,
            context.user,
            HallucinationCheckSelectionUpsert(
                team_id=UUID(team_id),
                llm_config_id=UUID(llm_config_id),
                model_name_override=provider_model or None,
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid hallucination checker selection"
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


@app.post("/admin/hallucination-check-selection/clear", response_class=HTMLResponse)
def admin_clear_hallucination_check_selection(
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
        clear_team_hallucination_check_selection_service(db, context.user, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid hallucination checker clear request"
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


@app.post("/admin/deidentification-providers/inspect", response_class=HTMLResponse)
def admin_inspect_deidentification_provider(
    request: Request,
    team_id: str = Form(""),
    provider_id: str = Form(""),
    label: str = Form(...),
    adapter_kind: str = Form(DeidentificationAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    detect_path: str = Form(""),
    openapi_path: str = Form(""),
    auth_mode: str = Form(DeidentificationAuthMode.none.value),
    bearer_token: str = Form(""),
    request_text_field: str = Form("text"),
    request_language_field: str = Form(""),
    extra_headers_json: str = Form(""),
    extra_body_json: str = Form(""),
    response_entities_path: str = Form("entities"),
    response_start_field: str = Form("start"),
    response_end_field: str = Form("end"),
    response_type_field: str = Form("entity_type"),
    response_score_field: str = Form(""),
    response_model_version_path: str = Form(""),
    entity_type_map_json: str = Form(""),
    clinical_detection_enabled: str | None = Form(default=None),
    clinical_detection_allow_unredacted: str | None = Form(default=None),
    sample_text: str = Form("Jane Smith attended on 22 April 2026."),
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
    resolved_bearer_token = bearer_token or None
    form_override = {
        "provider_id": provider_id,
        "label": label,
        "adapter_kind": adapter_kind,
        "base_url": base_url,
        "detect_path": detect_path,
        "openapi_path": openapi_path,
        "auth_mode": auth_mode,
        "request_text_field": request_text_field,
        "request_language_field": request_language_field,
        "extra_headers_json": extra_headers_json,
        "extra_body_json": extra_body_json,
        "response_entities_path": response_entities_path,
        "response_start_field": response_start_field,
        "response_end_field": response_end_field,
        "response_type_field": response_type_field,
        "response_score_field": response_score_field,
        "response_model_version_path": response_model_version_path,
        "entity_type_map_json": entity_type_map_json,
        "clinical_detection_enabled": clinical_detection_enabled == "true",
        "clinical_detection_allow_unredacted": clinical_detection_allow_unredacted == "true",
        "sample_text": sample_text,
        "is_active": is_active == "true",
        "preserved_bearer_token": "",
    }
    try:
        inspection = inspect_deidentification_provider_service(
            db,
            context.user,
            DeidentificationProviderInspectRequest(
                provider_id=UUID(provider_id) if provider_id else None,
                label=label,
                adapter_kind=DeidentificationAdapterKind(adapter_kind),
                base_url=base_url,
                detect_path=detect_path,
                openapi_path=openapi_path or None,
                auth_mode=DeidentificationAuthMode(auth_mode),
                bearer_token=resolved_bearer_token,
                request_text_field=request_text_field,
                request_language_field=request_language_field or None,
                extra_headers_json=parse_string_map_json(extra_headers_json, field_name="extra_headers_json", label="Extra headers"),
                extra_body_json=parse_json_object(extra_body_json, field_name="extra_body_json", label="Extra body fields"),
                response_entities_path=response_entities_path,
                response_start_field=response_start_field,
                response_end_field=response_end_field,
                response_type_field=response_type_field,
                response_score_field=response_score_field or None,
                response_model_version_path=response_model_version_path or None,
                entity_type_map_json=parse_string_map_json(entity_type_map_json, field_name="entity_type_map_json", label="Entity type map"),
                clinical_detection_enabled=clinical_detection_enabled == "true",
                clinical_detection_allow_unredacted=clinical_detection_allow_unredacted == "true",
                sample_text=sample_text,
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider inspection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id or None,
            selected_deidentification_provider_id=provider_id or None,
            deidentification_form_override=form_override,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    form_override.update(
        {
            "detect_path": inspection.detect_path,
            "openapi_path": inspection.openapi_path or openapi_path,
            "request_text_field": inspection.request_text_field,
            "request_language_field": inspection.request_language_field or "",
            "extra_body_json": json.dumps(inspection.extra_body_json) if inspection.extra_body_json else extra_body_json,
            "response_entities_path": inspection.response_entities_path,
            "response_start_field": inspection.response_start_field,
            "response_end_field": inspection.response_end_field,
            "response_type_field": inspection.response_type_field,
            "response_score_field": inspection.response_score_field or "",
            "response_model_version_path": inspection.response_model_version_path or "",
            "candidate_paths": inspection.candidate_paths,
        }
    )
    return render_admin(
        request,
        db,
        current_user=context.user,
        selected_team_id=team_id or None,
        selected_deidentification_provider_id=provider_id or None,
        deidentification_inspection=inspection,
        deidentification_form_override=form_override,
        message="Shared NLP endpoint ping succeeded.",
        message_kind="success",
        active_admin_tab=return_tab or "providers",
        active_provider_tab="deidentification",
        admin_page_route=_admin_page_route_from_return_view(return_view),
        admin_return_view=_admin_return_view_value(return_view),
    )


@app.post("/admin/deidentification-providers", response_class=HTMLResponse)
def admin_upsert_deidentification_provider(
    request: Request,
    team_id: str = Form(""),
    provider_id: str = Form(""),
    label: str = Form(...),
    adapter_kind: str = Form(DeidentificationAdapterKind.generic_rest.value),
    base_url: str = Form(""),
    detect_path: str = Form(""),
    auth_mode: str = Form(DeidentificationAuthMode.none.value),
    bearer_token: str = Form(""),
    request_text_field: str = Form("text"),
    request_language_field: str = Form(""),
    extra_headers_json: str = Form(""),
    extra_body_json: str = Form(""),
    response_entities_path: str = Form("entities"),
    response_start_field: str = Form("start"),
    response_end_field: str = Form("end"),
    response_type_field: str = Form("entity_type"),
    response_score_field: str = Form(""),
    response_model_version_path: str = Form(""),
    entity_type_map_json: str = Form(""),
    clinical_detection_enabled: str | None = Form(default=None),
    clinical_detection_allow_unredacted: str | None = Form(default=None),
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
    resolved_bearer_token = bearer_token or None
    try:
        provider = upsert_deidentification_provider_service(
            db,
            context.user,
            DeidentificationProviderUpsert(
                provider_id=UUID(provider_id) if provider_id else None,
                label=label,
                adapter_kind=DeidentificationAdapterKind(adapter_kind),
                base_url=base_url,
                detect_path=detect_path,
                auth_mode=DeidentificationAuthMode(auth_mode),
                bearer_token=resolved_bearer_token,
                request_text_field=request_text_field,
                request_language_field=request_language_field or None,
                extra_headers_json=parse_string_map_json(
                    extra_headers_json,
                    field_name="extra_headers_json",
                    label="Extra headers",
                ),
                extra_body_json=parse_json_object(
                    extra_body_json,
                    field_name="extra_body_json",
                    label="Extra body fields",
                ),
                response_entities_path=response_entities_path,
                response_start_field=response_start_field,
                response_end_field=response_end_field,
                response_type_field=response_type_field,
                response_score_field=response_score_field or None,
                response_model_version_path=response_model_version_path or None,
                entity_type_map_json=parse_string_map_json(
                    entity_type_map_json,
                    field_name="entity_type_map_json",
                    label="Entity type map",
                ),
                clinical_detection_enabled=clinical_detection_enabled == "true",
                clinical_detection_allow_unredacted=clinical_detection_allow_unredacted == "true",
                is_active=is_active == "true",
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id or None,
            selected_deidentification_provider_id=provider_id or None,
            message=detail,
            message_kind="error",
            status_code=status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(
            return_view=return_view,
            return_tab=return_tab or "providers",
            team_id=team_id or None,
            deidentification_provider_id=str(provider.id),
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-providers/{provider_id}/delete", response_class=HTMLResponse)
def admin_delete_deidentification_provider(
    request: Request,
    provider_id: UUID,
    team_id: str = Form(""),
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
        delete_deidentification_provider_service(db, context.user, provider_id=provider_id)
    except AppError as exc:
        return render_admin(
            request,
            db,
            current_user=context.user,
            selected_team_id=team_id or None,
            selected_deidentification_provider_id=str(provider_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
            active_admin_tab=return_tab or "providers",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id or None),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-provider-assignments", response_class=HTMLResponse)
def admin_assign_deidentification_provider(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
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
        assign_deidentification_provider_to_team_service(
            db,
            context.user,
            DeidentificationProviderAssignmentUpsert(
                team_id=UUID(team_id),
                provider_id=UUID(provider_id),
            ),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider assignment"
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


@app.post("/admin/deidentification-selection", response_class=HTMLResponse)
def admin_set_deidentification_selection(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
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
        set_team_deidentification_selection_service(
            db,
            context.user,
            DeidentificationSelectionUpsert(team_id=UUID(team_id), provider_id=UUID(provider_id)),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification selection"
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


@app.post("/admin/deidentification-selection/clear", response_class=HTMLResponse)
def admin_clear_deidentification_selection(
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
        clear_team_deidentification_selection_service(db, context.user, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification selection clear request"
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


@app.post("/admin/clinical-nlp-selection", response_class=HTMLResponse)
def admin_set_clinical_nlp_selection(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
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
        set_team_clinical_nlp_selection_service(
            db,
            context.user,
            ClinicalNlpSelectionUpsert(team_id=UUID(team_id), provider_id=UUID(provider_id)),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid clinical NLP selection"
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
            active_provider_tab="deidentification",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/clinical-nlp-selection/clear", response_class=HTMLResponse)
def admin_clear_clinical_nlp_selection(
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
        clear_team_clinical_nlp_selection_service(db, context.user, team_id=UUID(team_id))
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid clinical NLP selection clear request"
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
            active_provider_tab="deidentification",
            admin_page_route=_admin_page_route_from_return_view(return_view),
            admin_return_view=_admin_return_view_value(return_view),
        )
    return RedirectResponse(
        url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "providers", team_id=team_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/deidentification-provider-assignments/remove", response_class=HTMLResponse)
def admin_remove_deidentification_provider_assignment(
    request: Request,
    team_id: str = Form(...),
    provider_id: str = Form(...),
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
        remove_deidentification_provider_assignment_service(
            db,
            context.user,
            team_id=UUID(team_id),
            provider_id=UUID(provider_id),
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid de-identification provider assignment removal"
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


@app.post("/admin/users/{user_id}/send-activation", response_class=HTMLResponse)
def admin_send_activation(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        send_account_activation_email_service(db, user, created_by=context.user)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


def _admin_break_glass_allowed() -> bool:
    if os.getenv("BREAK_GLASS_RECOVERY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return False
    return not email_password_reset_enabled_service() or os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() in {"1", "true", "yes"}


@app.post("/admin/users/{user_id}/recover-password", response_class=HTMLResponse)
def admin_recover_password_deprecated(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.", message_kind="error", status_code=status.HTTP_410_GONE, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/send-password-reset", response_class=HTMLResponse)
def admin_send_password_reset(request: Request, user_id: UUID, reason: str = Form(""), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        send_manager_password_reset_email_service(db, actor=context.user, target=user)
        record_security_event(db, action="manager_password_reset_email_sent", actor=context.user, target=user, request=request, details={"reason": reason or None})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/break-glass-password-reset", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def admin_break_glass_password_reset(request: Request, user_id: UUID, mfa_code: str = Form(...), reason: str = Form(...), confirm_email_unavailable: str | None = Form(default=None), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if not _admin_break_glass_allowed():
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(db, context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(db, user, actor=context.user, reset_mfa=False, break_glass=True)
        record_security_event(db, action="break_glass_password_reset_generated", actor=context.user, target=user, request=request, details={"reason": reason, "expires_at": expires_at.isoformat()})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="Break-glass temporary password generated. It is shown once.", message_kind="success", recovery_temporary_password=temporary_password, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/reset-mfa", response_class=HTMLResponse)
def admin_reset_mfa(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        reset_user_mfa_for_reenrollment_service(db, user=user)
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/recover-account", response_class=HTMLResponse)
def admin_recover_account_deprecated(request: Request, user_id: UUID, return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.", message_kind="error", status_code=status.HTTP_410_GONE, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


@app.post("/admin/users/{user_id}/send-account-recovery", response_class=HTMLResponse)
def admin_send_account_recovery(request: Request, user_id: UUID, reason: str = Form(""), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        send_manager_account_recovery_email_service(db, actor=context.user, target=user)
        record_security_event(db, action="manager_account_recovery_email_sent", actor=context.user, target=user, request=request, details={"reason": reason or None})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return RedirectResponse(url=_admin_redirect_url(return_view=return_view, return_tab=return_tab or "directory", team_id=return_team_id or None), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/break-glass-account-recovery", response_class=HTMLResponse)
@MFA_RATE_LIMIT
def admin_break_glass_account_recovery(request: Request, user_id: UUID, mfa_code: str = Form(...), reason: str = Form(...), confirm_email_unavailable: str | None = Form(default=None), return_view: str = Form(""), return_tab: str = Form(""), return_team_id: str = Form(""), csrf_protected: BrowserCsrf = None, db: Session = Depends(get_db)):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if not context.user.is_system_admin:
        return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if not _admin_break_glass_allowed():
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(db, context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(db, user, actor=context.user, reset_mfa=True, break_glass=True)
        record_security_event(db, action="break_glass_account_recovery_generated", actor=context.user, target=user, request=request, details={"reason": reason, "expires_at": expires_at.isoformat()})
    except AppError as exc:
        return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message=exc.message, message_kind="error", status_code=exc.status_code, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))
    return render_admin(request, db, current_user=context.user, selected_team_id=return_team_id or None, message="Break-glass temporary password generated and MFA reset. It is shown once.", message_kind="success", recovery_temporary_password=temporary_password, active_admin_tab=return_tab or "directory", admin_page_route=_admin_page_route_from_return_view(return_view), admin_return_view=_admin_return_view_value(return_view))


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
