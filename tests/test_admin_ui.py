import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import pyotp
from sqlalchemy import func, select

from tests.constants import PERMANENT_TEST_PASSWORD

from app.errors import AppError
from app.models import (
    AccountRequest,
    ClinicalEntity,
    ClinicalEntityRun,
    DeidentificationAdapterKind,
    DeidentificationProvider,
    DefaultPromptTemplate,
    DefaultPromptTemplateVersion,
    DefaultQuickAction,
    DefaultQuickActionVersion,
    GeneratedDocument,
    GeneratedDocumentSection,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    LlmAdapterKind,
    LlmAuthMode,
    ProviderSecretCleanupJob,
    ProviderSecretCleanupKind,
    ProviderFeatureType,
    ProviderUsageEvent,
    ProviderUsageEventType,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    QuotaPeriod,
    QuotaResource,
    RedactionRunStatus,
    SecurityAuditEvent,
    Team,
    TeamClinicalNlpSelection,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    LlmConfigSetupStatus,
    MfaMethodType,
    SessionStatus,
    TeamHallucinationCheckSelection,
    TeamLlmConfig,
    TeamLlmSelection,
    TeamRole,
    TeamSttConfig,
    SttSelectionPurpose,
    SttAdapterKind,
    SttConfigSetupStatus,
    TeamSttSelection,
    TaskDispatchOutbox,
    TaskDispatchState,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptAudioCleanupJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionJob,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    User,
    UserEncryptionKey,
    UserAppPreference,
    UserLlmPreference,
    UserMfaMethod,
    UserSession,
    UserStatus,
    UserQuotaPolicyEvent,
    UserQuotaPolicyEventType,
    UserQuotaReasonCode,
    utcnow,
)
from app.services.default_assets import (
    BUILTIN_DEFAULT_QUICK_ACTIONS,
    BUILTIN_DEFAULT_TEMPLATE,
    BUILTIN_DEFAULT_TEMPLATES,
    _LEGACY_BUILTIN_QUICK_ACTIONS,
    _LEGACY_BUILTIN_TEMPLATE,
    ensure_builtin_default_assets,
    ensure_builtin_team_assets,
    import_team_assets_to_defaults,
)
from app.services.admin import admin_usage_overview
from app.services.content_crypto import is_encrypted_envelope
from app.services.dictations import update_post_consultation_dictation
from app.services.passwords import verify_password
from app.schemas.llm import LlmConfigInspectResult
from app.schemas.stt import SttInspectResult
from app.web.presentation import (
    admin_page_route_from_return_view,
    admin_redirect_url,
    admin_return_view_value,
    default_template_return_tab,
    home_page_route_from_return_view,
    home_redirect_url,
    home_return_view_value,
    home_template_name_from_return_view,
    llm_form_defaults,
    stt_form_defaults,
)


class FakeHttpxResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


STT_OPENAPI_DOCUMENT = {
    "openapi": "3.1.0",
    "paths": {
        "/v1/audio/transcriptions": {
            "post": {
                "summary": "Transcribe audio",
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "file": {"type": "string", "format": "binary", "description": "Audio file upload."},
                                    "model": {"type": "string", "default": "whisper-1", "description": "Model to use."},
                                    "language": {"type": "string", "example": "en", "description": "Language code."},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        }
    },
}


def make_ingestion_job_for_transcript(transcript: Transcript, **kwargs) -> TranscriptIngestionJob:
    return TranscriptIngestionJob(
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def stub_stt_health_checks(monkeypatch):
    return None


def test_login_page_exposes_bootstrap_when_database_is_empty(client):
    page = client.get("/login")

    assert page.status_code == 200
    assert 'action="/bootstrap/system-admin"' in page.text
    assert 'action="/transcribe/sessions"' not in page.text
    assert 'action="/transcribe/sessions/start"' not in page.text
    assert "Create the first system admin" in page.text


def test_request_access_page_submits_public_account_request(client):
    page = client.get("/request-access")
    assert page.status_code == 200
    assert "Request an account" in page.text

    submitted = client.post(
        "/request-access",
        data={
            "requested_name": "Alice Example",
            "requested_email": "alice@example.com",
            "requested_team_name": "Clinic North",
            "request_details": "Need access",
        },
    )
    assert submitted.status_code == 200
    assert "Account request submitted" in submitted.text


def test_login_form_is_rate_limited_after_repeated_attempts(client, make_user):
    make_user(email="member@example.com", password="password-1")

    responses = [
        client.post("/login", data={"email": "member@example.com", "password": f"wrong-pass-{attempt}"})
        for attempt in range(6)
    ]

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429
    assert "Too many requests" in responses[5].text
    assert "Please wait a moment and try again." in responses[5].text
    assert "Return to login" in responses[5].text


def test_dev_seed_account_browser_login_is_restricted_to_localhost(client, make_user):
    make_user(email="dev.user@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = client.post(
        "/login",
        data={"email": "dev.user@example.com", "password": "password-1"},
        headers={"host": "192.168.1.77:8080", "origin": "http://192.168.1.77:8080"},
    )

    assert response.status_code == 403
    assert "Dev test accounts are available only from localhost" in response.text


def test_bootstrap_system_admin_gets_dek_and_browser_totp_enrollment_uses_encrypted_secret(client, db_session):
    bootstrap_response = client.post(
        "/bootstrap/system-admin",
        data={"email": "admin@example.com", "password": "AdminPassword123"},
        follow_redirects=False,
    )
    assert bootstrap_response.status_code == 303
    assert bootstrap_response.headers["location"] == "/onboarding"

    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "Finish your secure setup." in page.text
    assert "OpenScribe account setup" in page.text

    start_page = client.post("/onboarding/totp/start")
    secret_match = re.search(r'<strong>([A-Z2-7]+)</strong>', start_page.text)
    enrolled_admin = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    enrolled_method = db_session.scalar(select(UserMfaMethod).where(UserMfaMethod.user_id == enrolled_admin.id))
    key_count = db_session.scalar(
        select(func.count()).select_from(UserEncryptionKey).where(
            UserEncryptionKey.user_id == enrolled_admin.id,
            UserEncryptionKey.is_active.is_(True),
        )
    )
    assert start_page.status_code == 200
    assert "Scan this QR code with your authenticator app." in start_page.text
    assert "data:image/svg+xml" in start_page.text
    assert start_page.headers["Cache-Control"] == "no-store"
    assert start_page.headers["Pragma"] == "no-cache"
    assert secret_match is not None
    assert enrolled_method is not None
    assert is_encrypted_envelope(enrolled_method.secret)
    assert key_count == 1

    verify = client.post("/onboarding/totp/verify", data={"code": pyotp.TOTP(secret_match.group(1)).now()})
    assert verify.status_code == 200
    assert "Recovery codes" in verify.text


def test_non_admin_login_redirects_to_workspace_and_leader_sees_review_tools(
    client,
    make_team,
    make_user,
    make_account_request,
):
    team = make_team(name="Clinic North")
    make_account_request(requested_name="Alice Example", requested_email="alice@example.com", requested_team_name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login_response = client.post(
        "/login",
        data={"email": "leader@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/workspace"

    workspace_page = client.get("/workspace")
    assert workspace_page.status_code == 200
    assert "OpenScribe" in workspace_page.text
    assert "Create new consultation" in workspace_page.text
    assert 'data-tour-overlay' in workspace_page.text
    assert 'data-tour-scrim="top"' in workspace_page.text
    assert "background: var(--accent);" in Path("app/static/css/components.css").read_text()

    review_page = client.get("/workspace/team/account-requests")
    assert review_page.status_code == 200
    assert "Account requests" in review_page.text

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"


def test_managed_account_forms_offer_secure_password_generation():
    home_html = Path("app/templates/home.html").read_text()
    admin_html = Path("app/templates/admin_mockup.html").read_text()
    generator_js = Path("app/static/js/generated-password.js").read_text()

    assert home_html.count("data-generated-password") == 2
    assert admin_html.count("data-generated-password") == 3
    for template in (home_html, admin_html):
        assert 'minlength="12"' in template
        assert "/static/js/generated-password.js?v=20260712-infield-controls" in template

    assert "/static/css/home.css?v=20260712-password-controls" in home_html
    assert 'id="member-password"' in admin_html
    assert 'id="member-password"\n                  name="temporary_password"' in admin_html

    assert 'const LENGTH = 12;' in generator_js
    assert 'window.crypto.getRandomValues(value);' in generator_js
    assert '"ABCDEFGHJKLMNPQRSTUVWXYZ"' in generator_js
    assert '"abcdefghijkmnopqrstuvwxyz"' in generator_js
    assert '"23456789"' in generator_js
    assert '"!@#$%&*+-=?"' in generator_js
    assert 'window.confirm("Replace the password currently entered?")' in generator_js
    assert 'await navigator.clipboard.writeText(password);' in generator_js
    assert 'document.querySelector(".app-shell") ? "btn small" : "btn-ghost-sm"' in generator_js
    assert 'field.className = "generated-password-field";' in generator_js
    assert 'copyButton.innerHTML = icon("copy");' in generator_js
    assert 'generateButton.innerHTML = icon("generate");' in generator_js
    assert 'visibilityButton.innerHTML = icon("eye");' in generator_js
    assert 'copyButton.hidden = true;' in generator_js
    assert 'copyButton.hidden = false;' in generator_js
    assert '"Password copied."' in generator_js
    assert '"Password generated and copied."' in generator_js
    assert '"Password generated. Copy it manually."' in generator_js
    assert 'visibilityButton.setAttribute("aria-pressed", String(!showing));' in generator_js


def test_admin_home_without_team_guards_missing_member_modal_controls(client, make_user):
    make_user(email="admin-no-team-modal@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": "admin-no-team-modal@example.com", "password": "password-1"}, follow_redirects=False)

    page = client.get("/admin")
    admin_html = Path("app/templates/admin_mockup.html").read_text()

    assert page.status_code == 200
    assert 'id="add-member-button"' not in page.text
    assert "if (addMemberButton && addMemberModal && cancelAddMember) {" in admin_html
    guarded_listener = admin_html.index('addMemberButton.addEventListener("click", openAddMemberModal);')
    guard = admin_html.index("if (addMemberButton && addMemberModal && cancelAddMember) {")
    assert guard < guarded_listener


def _admin_csrf(page_text: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]+)"', page_text)
    assert match is not None
    return match.group(1)


def _quota_panel_url(team: Team, member: User) -> str:
    return f"/admin?team_id={team.id}&team_tab=members&member_id={member.id}"


def _login_quota_admin(client, admin: User) -> None:
    response = client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    assert response.status_code == 303


def _quota_form(client, team: Team, member: User) -> tuple[str, str]:
    page = client.get(_quota_panel_url(team, member))
    assert page.status_code == 200
    return page.text, _admin_csrf(page.text)


def test_admin_member_quota_panel_is_scoped_and_addressable(client, make_team, make_user, monkeypatch):
    team = make_team(name="Quota Team")
    other_team = make_team(name="Other Quota Team")
    admin = make_user(email="quota-ui-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-ui-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="quota-ui-other@example.com", password="password-1", team=other_team, team_role=TeamRole.user)
    system_admin = make_user(email="quota-ui-system@example.com", password="password-1", is_system_admin=True)
    _login_quota_admin(client, admin)

    page = client.get(_quota_panel_url(team, member))
    assert page.status_code == 200
    assert f"/admin?team_id={team.id}&amp;team_tab=members&amp;member_id={member.id}" in page.text
    assert "Manage quotas" in page.text
    assert "UTC windows" in page.text
    assert "Unlimited" in page.text
    assert "No temporary allowance" in page.text

    calls = []
    monkeypatch.setattr("app.web.presentation.get_admin_user_quota_detail", lambda *args, **kwargs: calls.append(kwargs))
    for invalid_member in ("not-a-uuid", str(other.id), str(system_admin.id)):
        hidden = client.get(f"/admin?team_id={team.id}&team_tab=members&member_id={invalid_member}")
        assert "id=\"quota-panel\"" not in hidden.text
    assert calls == []

    for team_tab in ("overview", "provider-policy", "stt", "llm", "deidentification", "defaults", "usage", "security", "danger"):
        client.get(f"/admin?team_id={team.id}&team_tab={team_tab}&member_id={member.id}")
    assert calls == []


def test_admin_quota_panel_renders_unlimited_disabled_and_no_temporary_allowance(client, db_session, make_team, make_user):
    team = make_team(name="Quota Window Team")
    admin = make_user(email="quota-window-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-window-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    member.daily_token_limit = 0
    member.monthly_token_limit = 12
    member.daily_audio_seconds_limit = None
    member.monthly_audio_seconds_limit = 0
    db_session.commit()
    _login_quota_admin(client, admin)

    page, _ = _quota_form(client, team, member)

    assert page.count("Unlimited") >= 2
    assert page.count("Disabled") >= 2
    assert page.count("No temporary allowance") == 4


def test_admin_quota_panel_formats_tokens_and_audio_for_monitoring(client, db_session, make_team, make_user):
    team = make_team(name="Quota Formatting Team")
    admin = make_user(email="quota-format-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-format-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    member.daily_token_limit = 1_250_000
    member.monthly_token_limit = 9_500_000
    member.daily_audio_seconds_limit = 3_661
    member.monthly_audio_seconds_limit = 36_000
    db_session.commit()
    _login_quota_admin(client, admin)

    page, _ = _quota_form(client, team, member)

    assert "1,250,000 tokens" in page
    assert "9,500,000 tokens" in page
    assert "1h 1m 1s" in page
    assert "10h" in page
    assert "In progress" in page
    assert "Accepted work not yet settled" in page


def test_admin_quota_limits_post_updates_all_windows_and_uses_safe_prg(client, db_session, make_team, make_user):
    team = make_team(name="Quota Mutation Team")
    admin = make_user(email="quota-mutation-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-mutation-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other_team = make_team(name="Quota Mutation Other")
    other = make_user(email="quota-mutation-other@example.com", password="password-1", team=other_team, team_role=TeamRole.user)
    member.monthly_audio_seconds_limit = 3600
    db_session.commit()
    _login_quota_admin(client, admin)
    _, csrf = _quota_form(client, team, member)
    limits = client.post(f"/admin/users/{member.id}/quotas/limits", data={
        "_csrf_token": csrf, "operation_id": "11111111-1111-4111-8111-111111111111", "daily_token_limit": "0", "monthly_token_limit": "100", "daily_audio_hours": "1.5", "monthly_audio_hours": "999", "monthly_audio_unlimited": "true", "reason_code": "policy_change", "reason": "free reason must not enter URL",
        "return_team_id": str(other.id),
        "return_member_id": str(other.id),
    }, params={"team_id": str(other_team.id), "member_id": str(other.id)}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert limits.status_code == 303
    location = limits.headers["location"]
    assert location == f"/admin?team_id={team.id}&team_tab=members&member_id={member.id}&quota_notice=limits_updated"
    assert "free+reason" not in location
    assert parse_qs(urlparse(location).query) == {
        "team_id": [str(team.id)], "team_tab": ["members"], "member_id": [str(member.id)], "quota_notice": ["limits_updated"],
    }
    assert db_session.scalar(select(func.count(UserQuotaPolicyEvent.id)).where(UserQuotaPolicyEvent.target_user_id == member.id)) == 4
    db_session.refresh(member)
    assert (member.daily_token_limit, member.monthly_token_limit, member.daily_audio_seconds_limit, member.monthly_audio_seconds_limit) == (0, 100, 5400, None)
    assert "Quota limits updated." in client.get(location).text


@pytest.mark.parametrize(
    ("expiry_preset", "expected_duration"),
    (
        ("24h", timedelta(hours=24)), ("7d", timedelta(days=7)),
        ("end_today", None), ("end_month", None),
    ),
)
def test_admin_quota_grant_is_atomic_for_zero_limits_and_rejects_unlimited_safely(
    client, db_session, make_team, make_user, expiry_preset, expected_duration,
):
    team = make_team(name="Quota Grant Team")
    admin = make_user(email="quota-grant-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-grant-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    member.daily_token_limit = member.monthly_token_limit = 0
    db_session.commit()
    _login_quota_admin(client, admin)
    _, csrf = _quota_form(client, team, member)
    grant = client.post(f"/admin/users/{member.id}/quota-grants", data={
        "_csrf_token": csrf, "operation_id": "22222222-2222-4222-8222-222222222222", "resource": "tokens", "periods": ["daily", "monthly"], "amount": "5", "audio_hours": "", "expiry_preset": expiry_preset, "reason_code": "temporary_allowance", "reason": "allowance",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert grant.status_code == 303
    grants = db_session.scalars(select(UserQuotaPolicyEvent).where(UserQuotaPolicyEvent.operation_id == UUID("22222222-2222-4222-8222-222222222222"))).all()
    assert len(grants) == 2
    assert {(event.resource, event.period, event.amount) for event in grants} == {
        (QuotaResource.tokens, QuotaPeriod.daily, 5), (QuotaResource.tokens, QuotaPeriod.monthly, 5),
    }
    assert len({event.effective_at for event in grants}) == 1
    persisted_expiry = grants[0].expires_at
    if expected_duration is not None:
        assert persisted_expiry == grants[0].effective_at + expected_duration
    elif expiry_preset == "end_today":
        assert persisted_expiry == grants[0].effective_at.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        next_month = (grants[0].effective_at.replace(day=28) + timedelta(days=4)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        assert persisted_expiry == next_month - timedelta(microseconds=1)

    retry_page = client.get(grant.headers["location"])
    retry = client.post(f"/admin/users/{member.id}/quota-grants", data={
        "_csrf_token": _admin_csrf(retry_page.text), "operation_id": "22222222-2222-4222-8222-222222222222",
        "resource": "tokens", "periods": ["daily", "monthly"], "amount": "5", "audio_hours": "",
        "expiry_preset": expiry_preset, "reason_code": "temporary_allowance", "reason": "allowance",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert retry.status_code == 303
    db_session.expire_all()
    retried_grants = db_session.scalars(select(UserQuotaPolicyEvent).where(
        UserQuotaPolicyEvent.operation_id == UUID("22222222-2222-4222-8222-222222222222")
    )).all()
    assert len(retried_grants) == 2
    assert {event.expires_at for event in retried_grants} == {persisted_expiry}

    page, csrf = _quota_form(client, team, member)
    unsafe_reason = '<script>alert("quota")</script>'
    unlimited = client.post(f"/admin/users/{member.id}/quota-grants", data={
        "_csrf_token": csrf, "operation_id": "33333333-3333-4333-8333-333333333333", "resource": "audio_seconds", "periods": "daily", "amount": "1", "audio_hours": "1", "expiry_preset": "none", "reason_code": "temporary_allowance", "reason": unsafe_reason,
    }, headers={"Origin": "http://testserver"})
    assert unlimited.status_code == 409
    assert "Cannot grant quota to unlimited window" in unlimited.text
    assert '&lt;script&gt;alert(&#34;quota&#34;)&lt;/script&gt;' in unlimited.text
    assert unsafe_reason not in unlimited.text
    assert 'value="1"' in unlimited.text

    zero_amount = client.post(f"/admin/users/{member.id}/quota-grants", data={
        "_csrf_token": _admin_csrf(page), "operation_id": "44444444-4444-4444-8444-444444444444", "resource": "tokens", "periods": "daily", "amount": "0", "expiry_preset": "none", "reason_code": "temporary_allowance", "reason": "zero amount",
    }, headers={"Origin": "http://testserver"})
    assert zero_amount.status_code == 422
    assert "Quota grant is invalid" in zero_amount.text


def test_admin_quota_resets_selected_and_all_windows_share_operation_and_timestamp(client, db_session, make_team, make_user):
    team = make_team(name="Quota Reset Team")
    admin = make_user(email="quota-reset-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-reset-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    _login_quota_admin(client, admin)
    page, csrf = _quota_form(client, team, member)
    reset = client.post(f"/admin/users/{member.id}/quota-resets", data={
        "_csrf_token": csrf, "operation_id": "55555555-5555-4555-8555-555555555555", "windows": ["tokens:daily", "audio_seconds:monthly"], "reason_code": "administrative_correction", "reason": "reset selected",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert reset.status_code == 303
    resets = db_session.scalars(select(UserQuotaPolicyEvent).where(UserQuotaPolicyEvent.operation_id == UUID("55555555-5555-4555-8555-555555555555"))).all()
    assert {(event.resource, event.period) for event in resets} == {(QuotaResource.tokens, QuotaPeriod.daily), (QuotaResource.audio_seconds, QuotaPeriod.monthly)}
    assert len({event.effective_at for event in resets}) == 1

    all_reset = client.post(f"/admin/users/{member.id}/quota-resets", data={
        "_csrf_token": _admin_csrf(client.get(reset.headers["location"]).text), "operation_id": "66666666-6666-4666-8666-666666666666", "reset_all": "true", "reason_code": "administrative_correction", "reason": "reset all",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert all_reset.status_code == 303
    resets = db_session.scalars(select(UserQuotaPolicyEvent).where(UserQuotaPolicyEvent.operation_id == UUID("66666666-6666-4666-8666-666666666666"))).all()
    assert len(resets) == 4
    assert {(event.resource, event.period) for event in resets} == {
        (resource, period) for resource in QuotaResource for period in QuotaPeriod
    }
    assert len({event.effective_at for event in resets}) == 1


def test_admin_quota_revoke_uses_prg_and_rejects_revoked_or_expired_grants(client, db_session, make_team, make_user):
    team = make_team(name="Quota Revoke Team")
    admin = make_user(email="quota-revoke-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-revoke-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    member.daily_token_limit = 1
    db_session.commit()
    _login_quota_admin(client, admin)
    _, csrf = _quota_form(client, team, member)
    created = client.post(f"/admin/users/{member.id}/quota-grants", data={
        "_csrf_token": csrf, "operation_id": "77777777-7777-4777-8777-777777777777", "resource": "tokens", "periods": "daily", "amount": "5", "expiry_preset": "none", "reason_code": "temporary_allowance", "reason": "active grant",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert created.status_code == 303
    active_grant = db_session.scalar(select(UserQuotaPolicyEvent).where(UserQuotaPolicyEvent.operation_id == UUID("77777777-7777-4777-8777-777777777777")))
    assert active_grant is not None
    revoke = client.post(f"/admin/users/{member.id}/quota-grants/{active_grant.id}/revoke", data={
        "_csrf_token": _admin_csrf(client.get(created.headers["location"]).text), "revocation_operation_id": "88888888-8888-4888-8888-888888888888", "reason_code": "administrative_correction", "reason": "revoke active",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert revoke.status_code == 303
    assert revoke.headers["location"].endswith("quota_notice=grant_revoked")
    db_session.refresh(active_grant)
    assert active_grant.revoked_at is not None

    repeated = client.post(f"/admin/users/{member.id}/quota-grants/{active_grant.id}/revoke", data={
        "_csrf_token": _admin_csrf(client.get(revoke.headers["location"]).text), "revocation_operation_id": "99999999-9999-4999-8999-999999999999", "reason_code": "administrative_correction", "reason": "revoke again",
    }, headers={"Origin": "http://testserver"})
    assert repeated.status_code == 409
    assert "Quota revocation conflicts with existing request" in repeated.text

    expired = UserQuotaPolicyEvent(
        operation_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), target_user_id=member.id, actor_user_id=admin.id,
        actor_user_id_snapshot=admin.id, event_type=UserQuotaPolicyEventType.grant, resource=QuotaResource.tokens,
        period=QuotaPeriod.monthly, reason_code=UserQuotaReasonCode.temporary_allowance, reason="expired grant",
        amount=1, effective_at=utcnow() - timedelta(days=2), expires_at=utcnow() - timedelta(days=1),
    )
    db_session.add(expired)
    db_session.commit()
    expired_response = client.post(f"/admin/users/{member.id}/quota-grants/{expired.id}/revoke", data={
        "_csrf_token": _admin_csrf(client.get(_quota_panel_url(team, member)).text), "revocation_operation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "reason_code": "administrative_correction", "reason": "expired revoke",
    }, headers={"Origin": "http://testserver"})
    assert expired_response.status_code == 409
    assert "Quota grant is not active" in expired_response.text


def test_admin_quota_panel_keeps_old_active_grant_visible_and_revocable_beyond_history_cap(
    client, db_session, make_team, make_user,
):
    team = make_team(name="Quota Active Grant Team")
    admin = make_user(email="quota-old-grant-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(
        email="quota-old-grant-member@example.com", password="password-1", team=team, team_role=TeamRole.user
    )
    member.daily_token_limit = 1
    old_time = utcnow() - timedelta(days=2)
    active_grant = UserQuotaPolicyEvent(
        operation_id=UUID("12121212-1212-4212-8212-121212121212"), target_user_id=member.id,
        actor_user_id=admin.id, actor_user_id_snapshot=admin.id,
        event_type=UserQuotaPolicyEventType.grant, resource=QuotaResource.tokens,
        period=QuotaPeriod.daily, reason_code=UserQuotaReasonCode.temporary_allowance,
        reason="old active allowance", amount=5, effective_at=old_time, created_at=old_time,
    )
    db_session.add(active_grant)
    for index in range(51):
        db_session.add(UserQuotaPolicyEvent(
            operation_id=UUID(int=index + 1000), target_user_id=member.id, actor_user_id=admin.id,
            actor_user_id_snapshot=admin.id, event_type=UserQuotaPolicyEventType.reset,
            resource=QuotaResource.tokens, period=QuotaPeriod.daily,
            reason_code=UserQuotaReasonCode.other, reason=f"newer history {index}",
            effective_at=old_time + timedelta(days=1), created_at=old_time + timedelta(days=1, seconds=index),
        ))
    db_session.commit()

    _login_quota_admin(client, admin)
    page = client.get(_quota_panel_url(team, member))
    revoke_path = f"/admin/users/{member.id}/quota-grants/{active_grant.id}/revoke"
    assert page.status_code == 200
    assert "Active allowances" in page.text
    assert revoke_path in page.text

    response = client.post(revoke_path, data={
        "_csrf_token": _admin_csrf(page.text),
        "revocation_operation_id": "34343434-3434-4434-8434-343434343434",
        "reason_code": "administrative_correction", "reason": "revoke old active allowance",
    }, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(active_grant)
    assert active_grant.revoked_at is not None


def test_admin_quota_posts_require_system_admin_and_history_escapes_reason_after_actor_deletion(client, db_session, make_team, make_user):
    team = make_team(name="Quota Auth Team")
    other_team = make_team(name="Quota Auth Other Team")
    admin = make_user(email="quota-auth-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-auth-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other_member = make_user(email="quota-auth-other@example.com", password="password-1", team=other_team, team_role=TeamRole.user)
    leader = make_user(email="quota-auth-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    _login_quota_admin(client, admin)
    _, csrf = _quota_form(client, team, member)
    reason = '<img src=x onerror="alert(1)">'
    saved = client.post(f"/admin/users/{member.id}/quotas/limits", data={
        "_csrf_token": csrf, "operation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "daily_token_limit": "1", "monthly_token_limit": "2", "daily_audio_hours": "1", "monthly_audio_hours": "2", "reason_code": "policy_change", "reason": reason,
        "return_team_id": str(other_team.id), "return_member_id": str(other_member.id),
    }, params={"team_id": str(other_team.id), "member_id": str(other_member.id)}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert saved.status_code == 303
    assert f"team_id={team.id}" in saved.headers["location"]
    assert str(other_team.id) not in saved.headers["location"]

    for user in (member, leader):
        client.post("/logout", follow_redirects=False)
        assert client.post("/login", data={"email": user.email, "password": "password-1"}, follow_redirects=False).status_code == 303
        client.get("/home")
        csrf = client.cookies.get("openscribe_csrf", "")
        forbidden = client.post(f"/admin/users/{member.id}/quota-resets", data={
            "_csrf_token": csrf, "operation_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "reset_all": "true", "reason_code": "administrative_correction", "reason": "forbidden",
        }, headers={"Origin": "http://testserver"})
        assert forbidden.status_code == 403

    client.post("/logout", follow_redirects=False)
    db_session.delete(admin)
    db_session.commit()
    replacement_admin = make_user(email="quota-auth-replacement@example.com", password="password-1", is_system_admin=True)
    _login_quota_admin(client, replacement_admin)
    history = client.get(_quota_panel_url(team, member))
    assert history.status_code == 200
    assert '&lt;img src=x onerror=&#34;alert(1)&#34;&gt;' in history.text
    assert reason not in history.text
    assert str(admin.id) in history.text


def test_user_home_shows_team_stt_selection_when_configured(client, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_stt_selection(config=config, actor=leader)
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/home")

    assert page.status_code == 200
    assert "Clinic STT" in page.text


def test_home_restyled_preview_route_renders_for_signed_in_non_admin(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader-preview@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post(
        "/login",
        data={"email": "leader-preview@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    response = client.get("/home-restyled")

    assert response.status_code == 200
    assert 'class="brand home-pane__brand"' in response.text
    assert 'data-tab-target="ai-services"' in response.text
    assert 'data-tab-target="team-management"' in response.text
    assert "AI services" in response.text
    assert "Choose speech and writing services for your team." in response.text
    assert 'name="return_view" value="restyled"' in response.text
    assert "/home-restyled?tab=templates" in response.text


def test_home2_route_renders_admin2_styled_home_for_users_and_leaders(client, make_team, make_user):
    team = make_team(name="Clinic Home2")
    make_user(email="home2-user@example.com", password="password-1", team=team)
    make_user(email="home2-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "home2-user@example.com", "password": "password-1"}, follow_redirects=False)
    user_response = client.get("/home2")

    assert user_response.status_code == 200
    assert 'class="brand home-pane__brand"' in user_response.text
    assert 'class="home2"' in user_response.text
    assert "_home2_admin2_style" not in user_response.text
    assert '/static/css/home2.css?v=20260701-home-extract' in user_response.text
    assert 'data-tab-target="templates"' in user_response.text
    assert '<button type="button" class="tab-shell__tab" data-tab-target="team-management"' not in user_response.text
    assert 'name="return_view" value="home2"' in user_response.text
    assert "/home2?tab=templates" in user_response.text

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": "home2-leader@example.com", "password": "password-2"}, follow_redirects=False)
    leader_response = client.get("/home2")

    assert leader_response.status_code == 200
    assert 'data-tab-target="ai-services"' in leader_response.text
    assert 'data-tab-target="team-management"' in leader_response.text
    assert "Choose speech and writing services for your team." in leader_response.text


def test_home_tab_navigation_updates_url_and_rejects_missing_panels():
    home_html = Path("app/templates/home.html").read_text()
    components_css = Path("app/static/css/components.css").read_text()

    class HomeTabShellParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.panels_inside_shell = []

        def handle_starttag(self, tag, attrs):
            attr_map = dict(attrs)
            in_shell = any(frame["is_shell"] for frame in self.stack)
            panel = attr_map.get("data-tab-panel")
            if panel and in_shell:
                self.panels_inside_shell.append(panel)
            self.stack.append({"tag": tag, "is_shell": "data-tab-shell" in attr_map})

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index]["tag"] == tag:
                    del self.stack[index:]
                    break

    parser = HomeTabShellParser()
    parser.feed(home_html)

    assert "[hidden] { display: none !important; }" in components_css
    assert parser.panels_inside_shell == ["overview", "templates", "quick-actions", "smart-phrases", "ai-services", "team-management", "account-requests"]
    assert "const panelNames = new Set(panels.map((panel) => panel.dataset.tabPanel));" in home_html
    assert "if (!panelNames.has(name)) return;" in home_html
    assert "window.history.pushState({ homeTab: name }, '', selectedTab.dataset.tabUrl);" in home_html
    assert "window.addEventListener('popstate'" in home_html


def test_home2_blocks_system_admins_from_user_home(client, make_user):
    make_user(email="home2-admin@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "home2-admin@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.get("/home2", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_settings_requires_auth_and_blocks_system_admins(client, make_user):
    anonymous = client.get("/settings", follow_redirects=False)
    assert anonymous.status_code == 307
    assert anonymous.headers["location"] == "/workspace/preferences"
    assert client.get(anonymous.headers["location"], follow_redirects=False).headers["location"] == "/login"

    make_user(email="settings-admin@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": "settings-admin@example.com", "password": "password-1"}, follow_redirects=False)
    admin = client.get("/settings", follow_redirects=False)
    assert admin.status_code == 307
    assert admin.headers["location"] == "/workspace/preferences"
    admin_workspace = client.get(admin.headers["location"], follow_redirects=False)
    assert admin_workspace.status_code == 303
    assert admin_workspace.headers["location"] == "/admin"


def test_settings_role_scopes_user_and_leader_sections(client, make_team, make_user):
    team = make_team(name="Clinic Settings")
    member = make_user(email="settings-user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    leader = make_user(email="settings-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)
    user_page = client.get("/workspace/preferences")

    assert user_page.status_code == 200
    assert user_page.headers["Cache-Control"] == "no-store"
    assert '<link rel="stylesheet" href="/static/css/components.css?v=20260718-brand-lockup">' in user_page.text
    assert '<link rel="stylesheet" href="/static/css/settings.css?v=20260724-import-celebration">' in user_page.text
    assert 'aria-current="page"' in user_page.text
    assert "Preferences" in user_page.text
    assert '<div class="workspace-nav__group"><p data-sidebar-full>My Library</p>' in user_page.text
    assert 'href="/workspace/library/templates"' in user_page.text
    assert 'data-lucide="files"' in user_page.text
    assert 'My Templates' in user_page.text
    assert 'href="/workspace/library/quick-actions"' in user_page.text
    assert 'data-lucide="zap"' in user_page.text
    assert 'data-workspace-drawer-toggle' in user_page.text
    assert 'data-settings-menu' not in user_page.text
    assert "Smart phrases" in user_page.text
    assert user_page.text.index('href="/workspace/account"') < user_page.text.index('href="/workspace/preferences"')
    assert user_page.text.index('href="/workspace/library/quick-actions"') < user_page.text.index('href="/workspace/library/smart-phrases"')
    assert "Open scribe" not in user_page.text
    assert "AI services" not in user_page.text
    assert "Team members" not in user_page.text
    assert "Account requests" not in user_page.text
    assert 'data-back-to-scribe' in user_page.text
    assert "Back to Scribe" in user_page.text
    assert 'href="/transcribe"' not in user_page.text
    assert "Return home" not in user_page.text

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": leader.email, "password": "password-2"}, follow_redirects=False)
    leader_page = client.get("/workspace/team/members")

    assert leader_page.status_code == 200
    assert 'href="/workspace/team/ai-services"' in leader_page.text
    assert 'href="/workspace/team/members"' in leader_page.text
    assert 'href="/workspace/team/account-requests"' in leader_page.text
    assert 'data-settings-panel="team-members"' in leader_page.text
    assert f'action="/home/users/{member.id}/suspend"' in leader_page.text
    assert f'action="/home/users/{member.id}/reset-mfa"' in leader_page.text
    assert f'action="/home/users/{member.id}/delete"' in leader_page.text
    assert "Delete this user and all owned transcript content immediately?" in leader_page.text


def test_settings_normal_user_sees_same_team_templates_read_only(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Clinic Read-only Templates")
    other_team = make_team(name="Other Clinic Templates")
    member = make_user(email="template-reader@example.com", password="password-1", team=team, team_role=TeamRole.user)
    leader = make_user(email="template-owner@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    other_leader = make_user(email="other-template-owner@example.com", password="password-3", team=other_team, team_role=TeamRole.leader)
    shared_template = make_template(scope=TemplateScope.team, team=team, actor=leader, name="Shared clinic note")
    make_template(scope=TemplateScope.team, team=other_team, actor=other_leader, name="Other clinic note")
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    page = client.get("/workspace/library/templates")

    assert page.status_code == 200
    assert "Shared clinic note" in page.text
    assert "Other clinic note" not in page.text
    assert "freeform · Enabled · Read only" in page.text
    assert 'aria-label="New team template"' not in page.text
    assert f"/workspace/library/templates?scope=team&amp;template_id={shared_template.id}" in page.text
    assert f'action="/home/team-templates/{shared_template.id}/duplicate"' not in page.text
    assert f'action="/home/team-templates/{shared_template.id}/delete"' not in page.text
    assert f'action="/home/team-templates/{shared_template.id}/fork"' in page.text
    assert f'aria-label="Copy Shared clinic note to Personal"' in page.text

    denied = client.post(
        "/home/team-templates",
        data={
            "template_id": str(shared_template.id),
            "name": "Changed by member",
            "description": "",
            "prompt_text": "Changed prompt",
            "mode": "freeform",
            "return_view": "workspace",
            "return_tab": "templates",
            "is_active": "true",
        },
    )

    assert denied.status_code == 403
    db_session.refresh(shared_template)
    assert shared_template.name == "Shared clinic note"


def test_settings_templates_use_context_sidebar_and_embedded_personal_editor(
    client,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Clinic Template Library")
    member = make_user(email="template-library@example.com", password="password-1", team=team, team_role=TeamRole.user)
    personal = make_template(scope=TemplateScope.user, owner=member, actor=member, name="Personal consultation")
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    library = client.get("/workspace/library/templates")
    selected = client.get(f"/workspace/library/templates?scope=personal&template_id={personal.id}")

    assert library.status_code == 200
    assert 'data-workspace-drawer-toggle' in library.text
    assert 'data-settings-menu' not in library.text
    assert 'class="template-library-sidebar" aria-label="Template library"' in library.text
    assert 'id="personal-template-heading">Personal</h3>' in library.text
    assert 'id="team-template-heading">Team</h3>' in library.text
    assert "Select a template" in library.text
    assert selected.status_code == 200
    assert 'class="template-library-shell has-selection"' in selected.text
    assert 'class="template-library-back" href="/workspace/library/templates"' in selected.text
    assert f'href="/workspace/library/templates?scope=personal&amp;template_id={personal.id}" aria-current="page"' in selected.text
    assert 'data-template-editor' in selected.text
    assert 'action="/home/personal-templates"' in selected.text
    assert 'name="template_id" value="%s"' % personal.id in selected.text
    assert 'name="return_view" value="workspace"' in selected.text
    assert 'class="app-shell"' not in selected.text


def test_settings_team_template_selection_is_read_only_for_member_and_editable_for_leader(
    client,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Clinic Team Template Detail")
    member = make_user(email="team-template-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    leader = make_user(email="team-template-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    shared = make_template(scope=TemplateScope.team, team=team, actor=leader, name="Shared examination", prompt_text="Team-only config text")

    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)
    member_page = client.get(f"/workspace/library/templates?scope=team&template_id={shared.id}")

    assert member_page.status_code == 200
    assert 'data-template-editor-read-only' in member_page.text
    assert 'aria-label="Team template preview"' in member_page.text
    assert "Team-only config text" in member_page.text
    assert 'action="/home/team-templates" class="template-form"' not in member_page.text
    assert f'action="/home/team-templates/{shared.id}/fork"' in member_page.text
    assert ">Copy to Personal</button>" in member_page.text
    forked = client.post(
        f"/home/team-templates/{shared.id}/fork",
        data={"return_view": "workspace", "return_tab": "templates"},
        follow_redirects=False,
    )
    assert forked.status_code == 303
    assert forked.headers["location"].startswith("/workspace/library/templates?scope=personal&template_id=")

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": leader.email, "password": "password-2"}, follow_redirects=False)
    leader_page = client.get(f"/workspace/library/templates?scope=team&template_id={shared.id}")

    assert leader_page.status_code == 200
    assert 'data-template-editor-read-only' not in leader_page.text
    assert 'action="/home/team-templates" class="template-form"' in leader_page.text
    assert 'aria-label="New team template"' in leader_page.text
    duplicated = client.post(
        f"/home/team-templates/{shared.id}/duplicate",
        data={"return_view": "workspace", "return_tab": "templates"},
        follow_redirects=False,
    )
    assert duplicated.status_code == 303
    assert duplicated.headers["location"].startswith("/workspace/library/templates?scope=team&template_id=")


def test_settings_template_save_and_validation_stay_in_embedded_workspace(
    client,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Embedded Save")
    member = make_user(email="embedded-save@example.com", password="password-1", team=team, team_role=TeamRole.user)
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    invalid = client.post(
        "/home/personal-templates",
        data={"name": " Unsaved name ", "description": "Keep this description", "prompt_text": " ", "mode": "freeform", "return_view": "workspace", "return_tab": "templates"},
    )
    saved = client.post(
        "/home/personal-templates",
        data={"name": "Embedded template", "prompt_text": "Write note", "mode": "freeform", "return_view": "workspace", "return_tab": "templates", "is_active": "true"},
        follow_redirects=False,
    )

    assert invalid.status_code == 422
    assert 'data-settings-panel="templates"' in invalid.text
    assert 'class="template-library-shell has-selection"' in invalid.text
    assert 'action="/home/personal-templates"' in invalid.text
    assert 'value=" Unsaved name "' in invalid.text
    assert 'value="Keep this description"' in invalid.text
    assert saved.status_code == 303
    assert saved.headers["location"].startswith("/workspace/library/templates?scope=personal&template_id=")
    saved_id = saved.headers["location"].rsplit("=", 1)[1]
    duplicated = client.post(
        f"/home/personal-templates/{saved_id}/duplicate",
        data={"return_view": "workspace", "return_tab": "templates"},
        follow_redirects=False,
    )
    assert duplicated.status_code == 303
    assert duplicated.headers["location"].startswith("/workspace/library/templates?scope=personal&template_id=")


def test_settings_template_selection_does_not_expose_cross_team_template(
    client,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Clinic Scoped Library")
    other_team = make_team(name="Other Scoped Library")
    member = make_user(email="scoped-library@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other_leader = make_user(email="other-scoped-library@example.com", password="password-2", team=other_team, team_role=TeamRole.leader)
    foreign = make_template(scope=TemplateScope.team, team=other_team, actor=other_leader, name="Foreign secret template", prompt_text="Foreign prompt")
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)

    page = client.get(f"/workspace/library/templates?scope=team&template_id={foreign.id}")

    assert page.status_code == 200
    assert "Foreign secret template" not in page.text
    assert "Foreign prompt" not in page.text
    assert "Select a template" in page.text


def test_settings_account_section_renders_owner_profile_and_security_forms(client, make_team, make_user):
    team = make_team(name="Clinic Account Settings")
    member = make_user(email="account-settings@example.com", full_name="Account Owner", password="Password123", team=team)
    client.post("/login", data={"email": member.email, "password": "Password123"}, follow_redirects=False)

    page = client.get("/workspace/account")

    assert page.status_code == 200
    assert 'href="/workspace/account"' in page.text
    assert 'aria-current="page"' in page.text
    assert 'data-settings-panel="account"' in page.text
    assert 'action="/settings/account/name"' in page.text
    assert 'value="Account Owner"' in page.text
    assert 'action="/settings/account/email"' in page.text
    assert 'value="account-settings@example.com"' in page.text
    assert 'action="/settings/account/password"' in page.text
    assert 'autocomplete="current-password"' in page.text
    assert 'autocomplete="new-password"' in page.text


def test_workspace_settings_new_consultation_uses_preferred_recording_mode(
    client,
    make_team,
    make_user,
    make_user_app_preference,
):
    team = make_team(name="Clinic Account Recording Preference")
    member = make_user(
        email="account-recording-preference@example.com",
        password="Password123",
        team=team,
    )
    make_user_app_preference(
        user=member,
        preferences_json={"preferred_recording_mode": "live_chunked"},
    )
    client.post(
        "/login",
        data={"email": member.email, "password": "Password123"},
        follow_redirects=False,
    )

    for path in ("/workspace/account", "/workspace/preferences"):
        page = client.get(path)

        assert page.status_code == 200
        assert (
            '<form id="new-session-form" method="post" action="/transcribe/sessions" hidden>'
            '<input type="hidden" name="ingestion_mode" value="live_chunked">'
            "</form>"
        ) in page.text


def test_settings_account_updates_name_and_audits_without_profile_values(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Account Name")
    member = make_user(email="account-name@example.com", full_name="Before Name", password="Password123", team=team)
    client.post("/login", data={"email": member.email, "password": "Password123"}, follow_redirects=False)

    response = client.post("/settings/account/name", data={"full_name": "  After   Name  "}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/workspace/account")
    db_session.refresh(member)
    assert member.full_name == "After Name"
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "account_name_changed"))
    assert event is not None
    assert "After Name" not in str(event.details_json)


def test_settings_account_email_requires_password_uniqueness_and_rotates_sessions(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Account Email")
    member = make_user(email="account-email@example.com", password="Password123", team=team)
    make_user(email="already-used@example.com", password="Password123", team=team)
    client.post("/login", data={"email": member.email, "password": "Password123"}, follow_redirects=False)

    wrong_password = client.post(
        "/settings/account/email",
        data={"email": "new-email@example.com", "current_password": "WrongPassword123"},
    )
    duplicate = client.post(
        "/settings/account/email",
        data={"email": "already-used@example.com", "current_password": "Password123"},
    )

    assert wrong_password.status_code == 401
    assert "Current password is incorrect" in wrong_password.text
    assert duplicate.status_code == 409
    assert "Email address is unavailable" in duplicate.text
    db_session.refresh(member)
    assert member.email == "account-email@example.com"

    changed = client.post(
        "/settings/account/email",
        data={"email": " NEW-EMAIL@example.com ", "current_password": "Password123"},
        follow_redirects=False,
    )

    assert changed.status_code == 303
    db_session.refresh(member)
    assert member.email == "new-email@example.com"
    sessions = list(db_session.scalars(select(UserSession).where(UserSession.user_id == member.id)))
    assert sum(session.status is SessionStatus.active for session in sessions) == 1
    assert any(session.status is SessionStatus.revoked and session.revoke_reason == "email_changed" for session in sessions)
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "account_email_changed"))
    assert event is not None
    assert "new-email@example.com" not in str(event.details_json)


def test_settings_account_password_change_validates_and_rotates_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Account Password")
    member = make_user(email="account-password@example.com", password="Password123", team=team)
    client.post("/login", data={"email": member.email, "password": "Password123"}, follow_redirects=False)

    mismatch = client.post(
        "/settings/account/password",
        data={"current_password": "Password123", "new_password": "ChangedPassword123", "confirm_password": "DifferentPassword123"},
    )
    assert mismatch.status_code == 422
    assert "New passwords do not match" in mismatch.text

    changed = client.post(
        "/settings/account/password",
        data={"current_password": "Password123", "new_password": "ChangedPassword123", "confirm_password": "ChangedPassword123"},
        follow_redirects=False,
    )

    assert changed.status_code == 303
    db_session.refresh(member)
    assert verify_password("ChangedPassword123", member.password_hash)
    assert not verify_password("Password123", member.password_hash)
    sessions = list(db_session.scalars(select(UserSession).where(UserSession.user_id == member.id)))
    assert sum(session.status is SessionStatus.active for session in sessions) == 1
    assert any(session.status is SessionStatus.revoked and session.revoke_reason == "password_changed" for session in sessions)


def test_settings_account_email_requires_active_totp_when_configured(client, db_session, make_team, make_user, make_totp_method):
    team = make_team(name="Clinic Account MFA")
    member = make_user(email="account-mfa@example.com", password="Password123", team=team)
    client.post("/login", data={"email": member.email, "password": "Password123"}, follow_redirects=False)
    _, secret = make_totp_method(user=member)

    missing = client.post(
        "/settings/account/email",
        data={"email": "account-mfa-new@example.com", "current_password": "Password123", "mfa_code": ""},
    )
    assert missing.status_code == 403
    assert "Authenticator code is required" in missing.text

    changed = client.post(
        "/settings/account/email",
        data={"email": "account-mfa-new@example.com", "current_password": "Password123", "mfa_code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert changed.status_code == 303


def test_settings_quick_action_editor_and_invalid_tab_fallback(client, make_team, make_user):
    team = make_team(name="Clinic Settings Modal")
    user = make_user(email="settings-modal@example.com", password="password-1", team=team, team_role=TeamRole.user)
    client.post("/login", data={"email": user.email, "password": "password-1"}, follow_redirects=False)

    editor = client.get("/workspace/library/quick-actions?scope=personal&quick_action_id=new")
    fallback = client.get("/settings?tab=overview", follow_redirects=False)

    assert editor.status_code == 200
    assert 'data-quick-action-editor' in editor.text
    assert 'action="/home/personal-quick-actions"' in editor.text
    assert 'name="return_view" value="workspace"' in editor.text
    assert 'name="return_tab" value="quick-actions"' in editor.text
    assert 'class="modal-shell is-open"' not in editor.text
    assert fallback.status_code == 307
    assert fallback.headers["location"] == "/workspace/preferences"


def test_settings_return_view_helpers_are_closed_and_url_backed():
    assert home_return_view_value("settings") == "settings"
    assert home_page_route_from_return_view("settings") == "/settings"
    assert home_template_name_from_return_view("settings") == "settings.html"
    assert home_redirect_url(return_view="settings", return_tab="templates") == "/settings?tab=templates"
    assert home_return_view_value("workspace") == "workspace"
    assert home_page_route_from_return_view("workspace") == "/workspace/preferences"
    assert home_redirect_url(return_view="workspace", return_tab="templates") == "/workspace/library/templates"
    assert home_return_view_value("https://evil.example") == "workspace"
    assert home_page_route_from_return_view("https://evil.example") == "/workspace/preferences"
    assert home_template_name_from_return_view("https://evil.example") == "settings.html"


def test_settings_llm_preference_clear_returns_to_settings(client, db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic Settings Preference")
    admin = make_user(email="settings-pref-admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Settings LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    user = make_user(email="settings-pref-user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini", "gpt-4.1-mini"], model_name_override="gpt-4o-mini")
    db_session.add(UserLlmPreference(user_id=user.id, preferred_model_name="gpt-4.1-mini"))
    db_session.commit()
    client.post("/login", data={"email": user.email, "password": "password-1"}, follow_redirects=False)

    page = client.get("/workspace/preferences")
    assert "Use team default" in page.text
    assert '<details class="settings-advanced">' in page.text
    assert "<summary>Advanced</summary>" in page.text
    advanced = page.text.split('<details class="settings-advanced">', 1)[1].split("</details>", 1)[0]
    visible_preferences = page.text.split('<details class="settings-advanced">', 1)[0]
    assert '<select name="preferred_model_name"' in advanced
    assert 'action="/home/llm-preference/clear"' in advanced
    assert 'name="note_generation_length"' in visible_preferences
    assert 'name="llm_detail_level"' in visible_preferences
    assert '<input type="hidden" name="preferred_model_name" value="gpt-4.1-mini">' in visible_preferences

    saved = client.post(
        "/home/llm-preference",
        data={
            "preferred_model_name": "gpt-4.1-mini",
            "note_generation_length": "short",
            "llm_detail_level": "concise",
            "return_view": "workspace",
            "return_tab": "preferences",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    saved_model = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    saved_style = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == user.id))
    assert saved_model is not None and saved_model.preferred_model_name == "gpt-4.1-mini"
    assert saved_style is not None
    assert saved_style.preferences_json["note_generation_length"] == "short"
    assert saved_style.preferences_json["llm_detail_level"] == "concise"

    cleared = client.post(
        "/home/llm-preference/clear",
        data={"return_view": "workspace", "return_tab": "preferences"},
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/workspace/preferences"
    assert db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id)) is None


def test_settings_visible_style_form_drops_stale_model_override(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Clinic Stale Model Preference")
    admin = make_user(email="stale-model-admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, model_name="current-model", available_models_json=["current-model"])
    user = make_user(email="stale-model-user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["current-model"], model_name_override="current-model")
    db_session.add(UserLlmPreference(user_id=user.id, preferred_model_name="removed-model"))
    db_session.commit()
    client.post("/login", data={"email": user.email, "password": "password-1"}, follow_redirects=False)

    page = client.get("/workspace/preferences")
    visible_preferences = page.text.split('<details class="settings-advanced">', 1)[0]
    assert '<input type="hidden" name="preferred_model_name" value="">' in visible_preferences

    saved = client.post(
        "/home/llm-preference",
        data={
            "preferred_model_name": "",
            "note_generation_length": "long",
            "llm_detail_level": "detailed",
            "return_view": "workspace",
            "return_tab": "preferences",
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    db_session.expire_all()
    model_preference = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    style_preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == user.id))
    assert model_preference is not None and model_preference.preferred_model_name is None
    assert style_preference is not None
    assert style_preference.preferences_json["note_generation_length"] == "long"
    assert style_preference.preferences_json["llm_detail_level"] == "detailed"


def test_settings_leader_llm_policy_preserves_active_selection(client, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic Settings LLM Policy")
    admin = make_user(email="settings-policy-admin@example.com", password="password-3", is_system_admin=True)
    make_llm_config(team=team, actor=admin, label="First LLM", model_name="first-model", available_models_json=["first-model"])
    active_config = make_llm_config(
        team=team,
        actor=admin,
        label="Active LLM",
        model_name="model-a",
        available_models_json=["model-a", "model-b"],
    )
    leader = make_user(email="settings-policy-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_llm_selection(config=active_config, actor=leader, allowed_models_json=["model-b"], model_name_override="model-b")
    client.post("/login", data={"email": leader.email, "password": "password-1"}, follow_redirects=False)

    page = client.get("/workspace/team/ai-services")

    assert page.status_code == 200
    assert f'value="{active_config.id}" data-default-model="model-a" selected' in page.text
    assert 'name="allowed_model_names" value="model-b" checked' in page.text
    assert 'name="provider_model" value="model-b" checked' in page.text
    assert 'name="allowed_model_names" value="model-a" checked' not in page.text


def test_leader_home_separates_ai_services_from_team_member_admin(client, make_team, make_user, make_stt_config, make_llm_config):
    team = make_team(name="Clinic Services Split")
    admin = make_user(email="services-admin@example.com", password="password-2", is_system_admin=True)
    make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_llm_config(team=team, actor=admin, label="Clinic LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_user(email="services-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "services-leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")

    assert 'data-workspace-section="ai-services"' in page.text
    assert 'data-settings-panel="ai-services"' in page.text
    assert 'data-service-toggle="stt"' in page.text
    assert 'data-service-toggle="llm"' in page.text
    assert "Choose admin-provisioned services. Credentials stay private." in page.text
    assert "Speech to text" in page.text
    assert "Writing assistant" in page.text


def test_leader_home_ai_service_modal_query_opens_inline_editor(client, make_team, make_user, make_stt_config, make_llm_config):
    team = make_team(name="Clinic Inline Services")
    admin = make_user(email="inline-services-admin@example.com", password="password-2", is_system_admin=True)
    make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_llm_config(team=team, actor=admin, label="Clinic LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_user(email="inline-services-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "inline-services-leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")

    assert page.status_code == 200
    assert 'data-service-body="stt"' in page.text
    assert 'data-service-body="stt" hidden' in page.text
    assert 'data-service-toggle="stt">Configure<' in page.text


def test_leader_home_ai_service_errors_keep_inline_editor_open(client, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic Inline Errors")
    admin = make_user(email="inline-errors-admin@example.com", password="password-2", is_system_admin=True)
    make_llm_config(team=team, actor=admin, label="Clinic LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_user(email="inline-errors-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "inline-errors-leader@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.post(
        "/home/llm-selection",
        data={
            "llm_config_id": "not-a-uuid",
            "allowed_model_names": "gpt-4o-mini",
            "provider_model": "gpt-4o-mini",
            "return_tab": "ai-services",
        },
    )

    assert response.status_code == 400
    assert 'data-workspace-section="ai-services"' in response.text
    assert 'data-service-body="llm"' in response.text
    assert "Invalid LLM selection" in response.text


def test_admin_providers_panel_renders_deidentification_management(client, make_team, make_user, make_deidentification_provider):
    team = make_team(name="Clinic Deid Admin")
    admin = make_user(email="deid-panel-admin@example.com", password="password-1", is_system_admin=True)
    make_deidentification_provider(
        actor=admin,
        label="Clinic REST Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
    )

    client.post("/login", data={"email": "deid-panel-admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")

    assert page.status_code == 200
    assert "De-identification" in page.text
    assert "Assign provider" in page.text
    assert "Clinic REST Deid" in page.text
    assert 'action="/admin/deidentification-selection"' in page.text


def test_admin_can_provision_and_assign_deidentification_provider_from_web(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Deid Assign")
    make_user(email="deid-web-admin@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "deid-web-admin@example.com", "password": "password-1"}, follow_redirects=False)
    created = client.post(
        "/admin/deidentification-providers",
        data={
            "team_id": str(team.id),
            "label": "Web REST Deid",
            "adapter_kind": "generic_rest",
            "base_url": "https://deid.example.com",
            "detect_path": "/detect",
            "auth_mode": "none",
            "request_text_field": "text",
            "response_entities_path": "entities",
            "response_start_field": "start",
            "response_end_field": "end",
            "response_type_field": "entity_type",
            "clinical_detection_enabled": "true",
            "clinical_detection_allow_unredacted": "true",
            "is_active": "true",
            "return_tab": "providers",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    provider = db_session.scalar(select(DeidentificationProvider).where(DeidentificationProvider.label == "Web REST Deid"))
    assert provider is not None
    assert provider.clinical_detection_enabled is True
    assert provider.clinical_detection_allow_unredacted is True

    assigned = client.post(
        "/admin/deidentification-provider-assignments",
        data={"team_id": str(team.id), "provider_id": str(provider.id), "return_tab": "providers"},
        follow_redirects=False,
    )

    assert assigned.status_code == 303
    assignment = db_session.scalar(
        select(TeamDeidentificationProviderAssignment).where(
            TeamDeidentificationProviderAssignment.team_id == team.id,
            TeamDeidentificationProviderAssignment.provider_id == provider.id,
        )
    )
    assert assignment is not None

    selected = client.post(
        "/admin/deidentification-selection",
        data={"team_id": str(team.id), "provider_id": str(provider.id), "return_tab": "providers"},
        follow_redirects=False,
    )

    assert selected.status_code == 303
    selection = db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id))
    assert selection is not None
    assert selection.provider_id == provider.id

    clinical_selected = client.post(
        "/admin/clinical-nlp-selection",
        data={"team_id": str(team.id), "provider_id": str(provider.id), "return_tab": "providers"},
        follow_redirects=False,
    )
    assert clinical_selected.status_code == 303
    clinical_selection = db_session.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id))
    assert clinical_selection is not None
    assert clinical_selection.provider_id == provider.id

    page = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")
    assert page.status_code == 200
    assert "Web REST Deid" in page.text
    assert 'action="/admin/clinical-nlp-selection"' in page.text

    cleared = client.post(
        "/admin/deidentification-selection/clear",
        data={"team_id": str(team.id), "return_tab": "providers"},
        follow_redirects=False,
    )

    assert cleared.status_code == 303
    assert db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id)) is None
    assert db_session.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id)) is not None


def test_admin_deidentification_inspect_does_not_render_bearer_token(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    team = make_team(name="Clinic Deid Inspect")
    make_user(email="deid-inspect-web-admin@example.com", password="password-1", is_system_admin=True)
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "entities": [
                    {"start": 0, "end": 10, "entity_type": "PERSON", "score": 0.99},
                ],
            }

    def fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.deidentification.httpx.post", fake_post)

    client.post(
        "/login",
        data={"email": "deid-inspect-web-admin@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    inspect = client.post(
        "/admin/deidentification-providers/inspect",
        data={
            "team_id": str(team.id),
            "label": "Web Inspect Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9400",
            "detect_path": "/detect",
            "auth_mode": "bearer",
            "bearer_token": "secret-token",
            "request_text_field": "text",
            "response_entities_path": "entities",
            "response_start_field": "start",
            "response_end_field": "end",
            "response_type_field": "entity_type",
            "response_score_field": "score",
            "sample_text": "Jane Smith attended on 22 April 2026.",
            "is_active": "true",
            "return_view": "workspace",
            "return_tab": "deid-providers",
        },
    )

    assert inspect.status_code == 200
    assert "Shared NLP endpoint ping succeeded." in inspect.text
    assert "Add global provider" in inspect.text
    assert 'value="Web Inspect Deid"' in inspect.text
    assert "secret-token" not in inspect.text
    assert 'name="preserved_bearer_token" value="secret-token"' not in inspect.text
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}

    save_without_retyping = client.post(
        "/admin/deidentification-providers",
        data={
            "team_id": str(team.id),
            "label": "Web Inspect Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9400",
            "detect_path": "/detect",
            "auth_mode": "bearer",
            "bearer_token": "",
            "request_text_field": "text",
            "response_entities_path": "entities",
            "response_start_field": "start",
            "response_end_field": "end",
            "response_type_field": "entity_type",
            "is_active": "true",
        },
    )

    assert save_without_retyping.status_code == 400
    assert "Invalid de-identification provider" in save_without_retyping.text
    assert db_session.scalar(select(DeidentificationProvider).where(DeidentificationProvider.label == "Web Inspect Deid")) is None


def test_leader_home_can_manage_deidentification_selection_inline(
    client,
    db_session,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
):
    team = make_team(name="Clinic Deid Home")
    admin = make_user(email="deid-home-admin@example.com", password="password-2", is_system_admin=True)
    make_user(email="deid-home-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    provider = make_deidentification_provider(
        actor=admin,
        label="Leader REST Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)

    client.post("/login", data={"email": "deid-home-leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")

    assert page.status_code == 200
    assert 'data-service-body="deidentification"' in page.text
    assert 'data-service-body="deidentification" hidden' in page.text
    assert "Leader REST Deid" in page.text

    selected = client.post(
        "/home/deidentification-selection",
        data={"provider_id": str(provider.id), "return_tab": "ai-services"},
        follow_redirects=False,
    )
    assert selected.status_code == 303
    persisted = db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id))
    assert persisted is not None
    assert persisted.provider_id == provider.id

    cleared = client.post(
        "/home/deidentification-selection/clear",
        data={"return_tab": "ai-services"},
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id)) is None


def test_leader_home_can_enable_clinical_nlp_separately_from_deidentification(
    client,
    db_session,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
):
    team = make_team(name="Clinic NLP Home")
    admin = make_user(email="clinical-home-admin@example.com", password="password-2", is_system_admin=True)
    make_user(email="clinical-home-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    provider = make_deidentification_provider(
        actor=admin,
        label="OpenMedDetect",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://localhost:8090",
        detect_path="/analyze",
        clinical_detection_enabled=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)

    client.post("/login", data={"email": "clinical-home-leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")

    assert page.status_code == 200
    assert 'data-service-body="clinical-nlp"' in page.text
    assert 'data-service-body="clinical-nlp" hidden' in page.text
    assert "Clinical NLP" in page.text
    assert "OpenMedDetect" in page.text

    selected = client.post(
        "/home/clinical-nlp-selection",
        data={"provider_id": str(provider.id), "return_tab": "ai-services"},
        follow_redirects=False,
    )
    assert selected.status_code == 303
    clinical_selection = db_session.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id))
    assert clinical_selection is not None
    assert clinical_selection.provider_id == provider.id
    assert db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id)) is None

    cleared = client.post(
        "/home/clinical-nlp-selection/clear",
        data={"return_tab": "ai-services"},
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert db_session.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id)) is None


def test_home_restyled_llm_preference_redirect_preserves_preview_route(client, db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic Restyled Preference")
    admin = make_user(email="admin-restyled-pref@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    user = make_user(email="user-restyled-pref@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini", "gpt-4.1-mini"], model_name_override="gpt-4o-mini")

    client.post("/login", data={"email": "user-restyled-pref@example.com", "password": "password-1"}, follow_redirects=False)
    save = client.post(
        "/home/llm-preference",
        data={
            "preferred_model_name": "gpt-4.1-mini",
            "return_view": "restyled",
            "return_tab": "overview",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == "/home-restyled?tab=overview"
    preference = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    assert preference is not None
    assert preference.preferred_model_name == "gpt-4.1-mini"


def test_home_restyled_create_user_error_renders_preview_context(client, make_team, make_user):
    team = make_team(name="Clinic Restyled Create User")
    make_user(email="leader-restyled-create@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="existing-restyled-member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader-restyled-create@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.post(
        "/home/users",
        data={
            "full_name": "Duplicate Person",
            "email": "existing-restyled-member@example.com",
            "temporary_password": "password-9",
            "team_role": "user",
            "status": "active",
            "mfa_required": "true",
            "return_view": "restyled",
            "return_tab": "team-management",
        },
    )

    assert response.status_code == 409
    assert 'data-default-tab="team-management"' in response.text
    assert 'name="return_view" value="restyled"' in response.text


def test_home_restyled_account_request_reject_preserves_preview_redirect(client, db_session, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    account_request = make_account_request(
        requested_name="Alice Example",
        requested_email="alice@example.com",
        requested_team_name="Clinic North",
    )

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    rejected = client.post(
        f"/home/account-requests/{account_request.id}/reject",
        data={
            "review_notes": "No capacity",
            "return_view": "restyled",
            "return_tab": "account-requests",
        },
        follow_redirects=False,
    )

    assert rejected.status_code == 303
    assert rejected.headers["location"] == "/home-restyled?tab=account-requests"
    db_session.refresh(account_request)
    assert account_request.status.value == "rejected"


def test_home_restyled_account_request_approve_error_renders_preview_context(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    account_request = make_account_request(
        requested_name="Alice Example",
        requested_email="alice@example.com",
        requested_team_name="Clinic North",
    )

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.post(
        f"/home/account-requests/{account_request.id}/approve",
        data={
            "temporary_password": "password-1",
            "team_role": "not-a-role",
            "review_notes": "Needs review",
            "return_view": "restyled",
            "return_tab": "account-requests",
        },
    )

    assert response.status_code == 400
    assert 'data-default-tab="account-requests"' in response.text
    assert "/home-restyled?tab=templates" in response.text


def test_browser_manager_account_routes_redirect_to_login_without_auth(client, make_team, make_user):
    team = make_team(name="Clinic North")
    member = make_user(email="member@example.com", password="password-1", team=team, team_role=TeamRole.user)

    suspend = client.post(f"/home/users/{member.id}/suspend", follow_redirects=False)
    reactivate = client.post(f"/home/users/{member.id}/reactivate", follow_redirects=False)
    delete = client.post(f"/home/users/{member.id}/delete", follow_redirects=False)

    assert suspend.status_code == 303
    assert suspend.headers["location"] == "/login"
    assert reactivate.status_code == 303
    assert reactivate.headers["location"] == "/login"
    assert delete.status_code == 303
    assert delete.headers["location"] == "/login"


def test_invalid_browser_route_redirects_to_login_without_auth(client):
    response = client.get("/does-not-exist", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_route_shows_public_splash_without_auth(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Open-source clinical scribing" in response.text
    assert 'href="/static/css/tokens.css?v=20260701-token-harmonise"' in response.text
    assert 'href="/static/css/components.css?v=20260718-brand-lockup"' in response.text
    assert 'href="/static/css/splash.css?v=20260701-splash-token-harmonise"' in response.text
    assert 'href="/login"' in response.text
    assert 'href="/request-access"' in response.text
    assert 'src="/static/vendor/lucide/1.8.0/lucide.min.js"' in response.text
    assert 'data-lucide="feather"' in response.text
    assert "<svg" not in response.text


def test_root_route_redirects_authenticated_user_to_workspace(client, make_team, make_user):
    team = make_team(name="Clinic Root User")
    make_user(email="root-user@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "root-user@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/workspace"


def test_root_route_redirects_authenticated_admin_to_admin(client, make_user):
    make_user(email="root-admin@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "root-admin@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_invalid_browser_route_redirects_to_workspace_when_authenticated(client, make_team, make_user):
    team = make_team(name="Clinic Invalid Route")
    make_user(email="member-invalid-route@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-invalid-route@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.get("/does-not-exist", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/workspace"


@pytest.mark.parametrize(
    ("legacy_url", "canonical_url"),
    [
        ("/home", "/workspace"),
        ("/home?tab=scribe", "/workspace"),
        ("/home?tab=overview", "/workspace"),
        ("/home?tab=account", "/workspace/account"),
        ("/home?tab=preferences", "/workspace/preferences"),
        ("/home?tab=templates", "/workspace/library/templates"),
        ("/home?tab=quick-actions", "/workspace/library/quick-actions"),
        ("/home?tab=smart-phrases", "/workspace/library/smart-phrases"),
        ("/home?tab=ai-services", "/workspace/team/ai-services"),
        ("/home?tab=team-management", "/workspace/team/members"),
        ("/home?tab=account-requests", "/workspace/team/account-requests"),
    ],
)
def test_home_compatibility_landing_redirects_to_canonical_workspace(
    client,
    make_team,
    make_user,
    legacy_url,
    canonical_url,
):
    team = make_team(name=f"Clinic Home Redirect {canonical_url}")
    make_user(
        email=f"home-redirect-{canonical_url.replace('/', '-')}@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )

    client.post(
        "/login",
        data={
            "email": f"home-redirect-{canonical_url.replace('/', '-')}@example.com",
            "password": "password-1",
        },
        follow_redirects=False,
    )
    response = client.get(legacy_url, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == canonical_url


def test_home_compatibility_redirect_preserves_safe_asset_and_feedback_parameters(
    client, make_team, make_user
):
    team = make_team(name="Clinic Home Parameter Redirect")
    user = make_user(
        email="home-parameter-redirect@example.com",
        password="password-1",
        team=team,
    )
    template_id = uuid4()
    client.post(
        "/login",
        data={"email": user.email, "password": "password-1"},
        follow_redirects=False,
    )

    selected = client.get(
        f"/home?tab=templates&personal_template_id={template_id}",
        follow_redirects=False,
    )
    create = client.get(
        "/home?tab=quick-actions&modal=personal-quick-action",
        follow_redirects=False,
    )
    feedback = client.get(
        "/home?tab=preferences&message=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
        "&message_kind=warning",
        follow_redirects=False,
    )

    assert selected.headers["location"] == (
        f"/workspace/library/templates?scope=personal&template_id={template_id}"
    )
    assert create.headers["location"] == (
        "/workspace/library/quick-actions?scope=personal&quick_action_id=new"
    )
    assert feedback.headers["location"] == (
        "/workspace/preferences?message=%3Cscript%3Ealert%281%29%3C%2Fscript%3E"
        "&message_kind=success"
    )
    rendered = client.get(feedback.headers["location"])
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.text
    assert "<script>alert(1)</script>" not in rendered.text


def test_leader_home_can_suspend_and_reactivate_team_user(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    home_page = client.get("/workspace/team/members")
    assert "Suspend" in home_page.text

    suspend_response = client.post(f"/home/users/{member.id}/suspend", follow_redirects=False)
    assert suspend_response.status_code == 303
    assert suspend_response.headers["location"] == "/workspace/team/members"
    db_session.refresh(member)
    assert member.status is UserStatus.suspended

    client.get("/workspace/team/members")
    reactivate_response = client.post(f"/home/users/{member.id}/reactivate", follow_redirects=False)
    assert reactivate_response.status_code == 303
    assert reactivate_response.headers["location"] == "/workspace/team/members"
    db_session.refresh(member)
    assert member.status is UserStatus.active
    assert member.must_change_password is True


def test_leader_home_can_choose_active_stt_selection_from_provisioned_endpoints(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")
    assert "Speech to text" in page.text
    assert "Conversation transcription" in page.text
    assert "Clinic STT" in page.text

    save = client.post(
        "/home/stt-selection",
        data={
            "stt_config_id": str(config.id),
            "provider_model": "",
            "language": "en",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/workspace/team/ai-services"
    selection = db_session.scalar(select(TeamSttSelection).where(TeamSttSelection.team_id == team.id))
    assert selection is not None
    assert selection.stt_config_id == config.id
    assert selection.purpose is SttSelectionPurpose.conversation


def test_leader_home_can_choose_dictation_stt_selection_from_provisioned_endpoints(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-dictation@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic Dictation STT", model_name="whisper-1")
    make_user(email="leader-dictation@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader-dictation@example.com", "password": "password-1"}, follow_redirects=False)
    save = client.post(
        "/home/stt-selection",
        data={
            "purpose": "post_consultation_dictation",
            "stt_config_id": str(config.id),
            "provider_model": "",
            "language": "en",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/workspace/team/ai-services"

    selection = db_session.scalar(
        select(TeamSttSelection).where(
            TeamSttSelection.team_id == team.id,
            TeamSttSelection.purpose == SttSelectionPurpose.post_consultation_dictation,
        )
    )
    assert selection is not None
    assert selection.stt_config_id == config.id


def test_leader_home_can_clear_stt_selection_without_deleting_provisioned_endpoint(client, db_session, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT")
    leader = make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_stt_selection(config=config, actor=leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")
    assert "Speech to text" in page.text
    assert "Clear conversation" in page.text

    cleared = client.post("/home/stt-selection/clear", follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/workspace/team/ai-services"

    page_after = client.get("/workspace/team/ai-services")
    assert "Clear conversation" not in page_after.text
    assert db_session.scalar(
        select(TeamSttSelection).where(
            TeamSttSelection.team_id == team.id,
            TeamSttSelection.purpose == SttSelectionPurpose.conversation,
        )
    ) is None
    assert db_session.get(TeamSttConfig, config.id) is not None


def test_leader_home_can_choose_active_llm_selection_from_provisioned_providers(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/ai-services")
    assert "Writing assistant" in page.text
    assert "Clinic OpenAI" in page.text

    save = client.post(
        "/home/llm-selection",
        data={
            "llm_config_id": str(config.id),
            "allowed_model_names": ["gpt-4o-mini", "gpt-4.1-mini"],
            "provider_model": "gpt-4.1-mini",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/workspace/team/ai-services"
    selection = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id))
    assert selection is not None
    assert selection.llm_config_id == config.id
    assert selection.allowed_models_json == ["gpt-4o-mini", "gpt-4.1-mini"]
    assert selection.model_name_override == "gpt-4.1-mini"


def test_user_home_can_save_llm_preference(client, db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    user = make_user(email="user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini", "gpt-4.1-mini"], model_name_override="gpt-4o-mini")

    client.post("/login", data={"email": "user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/preferences")
    assert "Writing style" in page.text
    assert "Writing assistant model" in page.text
    assert "Short (up to ~1 page)" in page.text
    assert "Detailed" in page.text
    assert "Team allows:" not in page.text

    save = client.post(
        "/home/llm-preference",
        data={
            "preferred_model_name": "gpt-4.1-mini",
            "note_generation_length": "short",
            "llm_detail_level": "concise",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/workspace/preferences"
    preference = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    assert preference is not None
    assert preference.preferred_model_name == "gpt-4.1-mini"
    app_preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == user.id))
    assert app_preference is not None
    assert app_preference.preferences_json["note_generation_length"] == "short"
    assert app_preference.preferences_json["llm_detail_level"] == "concise"


def test_user_home_can_clear_llm_preference(client, db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic Clear Preference")
    admin = make_user(email="clear-pref-admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    user = make_user(email="clear-pref-user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini", "gpt-4.1-mini"], model_name_override="gpt-4o-mini")
    db_session.add(UserLlmPreference(user_id=user.id, preferred_model_name="gpt-4.1-mini"))
    db_session.commit()

    client.post("/login", data={"email": "clear-pref-user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/preferences")

    assert page.status_code == 200
    assert "Use team default" in page.text

    cleared = client.post("/home/llm-preference/clear", follow_redirects=False)

    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/workspace/preferences"
    assert db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id)) is None


def test_leader_home_can_create_team_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
    assert "Templates" in page.text

    save = client.post(
        "/home/team-templates",
        data={
            "name": "Team SOAP",
            "description": "Shared note prompt",
            "prompt_text": "Write a concise SOAP note.",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    template = db_session.scalar(select(PromptTemplate).where(PromptTemplate.team_id == team.id, PromptTemplate.name == "Team SOAP"))
    assert template is not None


def test_home_asset_rows_show_icon_actions_and_enabled_disabled_status(client, db_session, make_team, make_user, make_template, make_quick_action):
    team = make_team(name="Clinic Home Asset Row UI")
    member = make_user(email="home-asset-ui@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_template(scope=TemplateScope.user, owner=member, actor=member, name="My template", is_active=True)
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="My quick", is_active=False)

    client.post("/login", data={"email": "home-asset-ui@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/library/templates")

    assert page.status_code == 200
    assert "Personal" in page.text
    assert "Enabled" in page.text
    assert 'aria-label="Edit My template"' in page.text
    assert 'aria-label="Copy My template"' in page.text
    assert 'aria-label="Delete My template"' in page.text
    assert '/home/personal-templates/' in page.text
    assert '/duplicate' in page.text
    assert '/delete' in page.text

    quick_actions_page = client.get("/workspace/library/quick-actions")

    assert quick_actions_page.status_code == 200
    assert "Disabled" in quick_actions_page.text
    assert 'aria-label="Edit My quick"' in quick_actions_page.text
    assert 'aria-label="Copy My quick"' in quick_actions_page.text
    assert 'aria-label="Delete My quick"' in quick_actions_page.text


def test_template_editor_page_uses_dedicated_full_page_layout(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Template Editor Layout")
    make_user(email="template-editor-layout@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "template-editor-layout@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home/templates/editor?scope=personal")

    assert page.status_code == 200
    assert 'class="app-shell"' in page.text
    assert 'class="sidebar"' in page.text
    assert 'class="editor-pane"' in page.text
    assert 'class="template-form"' in page.text
    assert 'class="action-bar"' in page.text
    assert 'class="section-list"' in page.text
    assert 'class="section-row"' in page.text
    assert 'const CSRF_TOKEN = "' in page.text
    assert 'const COOKIE_NAME = "openscribe_csrf"' not in page.text
    assert 'Problem guidance' not in page.text
    assert '>Open<' not in page.text
    assert 'Personal template' in page.text


def test_settings_template_editor_returns_to_settings(client, make_team, make_user):
    team = make_team(name="Clinic Settings Editor")
    user = make_user(email="settings-editor@example.com", password="password-1", team=team, team_role=TeamRole.user)
    client.post("/login", data={"email": user.email, "password": "password-1"}, follow_redirects=False)

    editor = client.get("/home/templates/editor?scope=personal&return_view=settings&return_tab=templates")

    assert editor.status_code == 200
    assert 'href="/settings?tab=templates"' in editor.text
    assert 'name="return_view" value="settings"' in editor.text
    assert 'name="return_tab" value="templates"' in editor.text


def test_global_default_template_editor_return_flow_is_canonical(client, db_session, make_user):
    admin = make_user(email="global-default-editor@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    defaults = client.get("/admin?tab=global-defaults")

    assert defaults.status_code == 200
    assert '/admin/templates/editor?scope=default&amp;return_view=workspace&amp;return_tab=global-defaults' in defaults.text

    editor = client.get("/admin/templates/editor?scope=default&return_view=workspace&return_tab=defaults")

    assert editor.status_code == 200
    assert 'href="/admin?tab=global-defaults"' in editor.text
    assert 'name="return_tab" value="global-defaults"' in editor.text

    created = client.post(
        "/admin/default-templates",
        data={
            "return_view": "workspace",
            "return_tab": "global-defaults",
            "name": "Canonical default",
            "prompt_text": "Write a concise note.",
            "mode": "freeform",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    template = db_session.scalar(select(DefaultPromptTemplate).where(DefaultPromptTemplate.name == "Canonical default"))
    assert template is not None
    expected_editor_url = f"/admin/templates/editor?scope=default&template_id={template.id}&return_view=workspace&return_tab=global-defaults"
    assert created.headers["location"] == expected_editor_url

    saved = client.post(
        "/admin/default-templates",
        data={
            "template_id": str(template.id),
            "return_view": "workspace",
            "return_tab": "global-defaults",
            "name": "Canonical default updated",
            "prompt_text": "Write a concise updated note.",
            "mode": "freeform",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    duplicated = client.post(
        f"/admin/default-templates/{template.id}/duplicate",
        data={"return_view": "workspace", "return_tab": "global-defaults"},
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == expected_editor_url
    assert duplicated.status_code == 303
    assert duplicated.headers["location"].endswith("&return_view=workspace&return_tab=global-defaults")
    copied_id = UUID(duplicated.headers["location"].split("template_id=", 1)[1].split("&", 1)[0])

    deleted = client.post(
        f"/admin/default-templates/{copied_id}/delete",
        data={"return_view": "workspace", "return_tab": "global-defaults"},
        follow_redirects=False,
    )

    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/admin?tab=global-defaults"


def test_default_template_return_tab_closes_retired_views_to_canonical_admin():
    assert default_template_return_tab("workspace", "defaults") == "global-defaults"
    assert default_template_return_tab("", "anything") == "global-defaults"
    assert default_template_return_tab("legacy", "defaults") == "global-defaults"
    assert default_template_return_tab("admin2", "defaults") == "global-defaults"


def test_new_freeform_template_editor_hides_emis_sections_until_structured_mode(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Freeform Template Editor")
    make_user(email="freeform-template-editor@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "freeform-template-editor@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home/templates/editor?scope=personal")

    assert page.status_code == 200
    assert 'data-template-mode-select' in page.text
    assert 'data-template-sections hidden' in page.text
    assert "sections.hidden = modeSelect.value !== 'structured';" in page.text


def test_freeform_template_save_does_not_persist_section_config_from_form(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Freeform Template Save")
    member = make_user(email="freeform-template-save@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "freeform-template-save@example.com", "password": "password-1"}, follow_redirects=False)
    saved = client.post(
        "/home/personal-templates",
        data={
            "name": "Freeform note",
            "description": "No structured config",
            "prompt_text": "Write a concise note.",
            "mode": "freeform",
            "section_prompt_problem": "Should be ignored",
            "section_prompt_history": "Should also be ignored",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    template = db_session.scalar(select(PromptTemplate).where(PromptTemplate.owner_user_id == member.id, PromptTemplate.name == "Freeform note"))
    assert template is not None
    latest_version = db_session.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template.id)
        .order_by(PromptTemplateVersion.version_no.desc())
        .limit(1)
    )
    assert latest_version is not None
    assert latest_version.mode is TemplateMode.freeform
    assert latest_version.config_json is None


def test_team_template_editor_page_keeps_team_scope_for_new_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Team Template Editor Layout")
    make_user(email="team-template-editor-layout@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "team-template-editor-layout@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home/templates/editor?scope=team")

    assert page.status_code == 200
    assert 'New team template' in page.text
    assert 'action="/home/team-templates"' in page.text


def test_user_home_can_duplicate_personal_template_with_incremented_name(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Personal Template Copy")
    user = make_user(email="personal-template-copy@example.com", password="password-1", team=team, team_role=TeamRole.user)
    template = PromptTemplate(
        owner_user_id=user.id,
        scope=TemplateScope.user,
        name="My note",
        description="Personal prompt",
        is_active=True,
        created_by_user_id=user.id,
    )
    db_session.add(template)
    db_session.flush()
    db_session.add(PromptTemplateVersion(template_id=template.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Write note", created_by_user_id=user.id))
    db_session.commit()

    client.post("/login", data={"email": "personal-template-copy@example.com", "password": "password-1"}, follow_redirects=False)
    duplicated = client.post(f"/home/personal-templates/{template.id}/duplicate", data={"return_tab": "templates"}, follow_redirects=False)

    assert duplicated.status_code == 303
    copy = db_session.scalar(select(PromptTemplate).where(PromptTemplate.owner_user_id == user.id, PromptTemplate.name == "My note 2"))
    assert copy is not None
    latest_version = copy.versions[-1]
    assert latest_version.prompt_text == "Write note"
    assert duplicated.headers["location"] == f"/workspace/library/templates?scope=personal&template_id={copy.id}"


def test_leader_home_can_duplicate_team_template_with_next_suffix(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Team Template Copy")
    leader = make_user(email="team-template-copy@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    template = PromptTemplate(
        team_id=team.id,
        scope=TemplateScope.team,
        name="Team SOAP",
        description="Shared prompt",
        is_active=True,
        created_by_user_id=leader.id,
    )
    existing_copy = PromptTemplate(
        team_id=team.id,
        scope=TemplateScope.team,
        name="Team SOAP 2",
        description="Existing copy",
        is_active=True,
        created_by_user_id=leader.id,
    )
    db_session.add_all([template, existing_copy])
    db_session.flush()
    db_session.add(PromptTemplateVersion(template_id=template.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Write SOAP", created_by_user_id=leader.id))
    db_session.add(PromptTemplateVersion(template_id=existing_copy.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Older copy", created_by_user_id=leader.id))
    db_session.commit()

    client.post("/login", data={"email": "team-template-copy@example.com", "password": "password-1"}, follow_redirects=False)
    duplicated = client.post(f"/home/team-templates/{template.id}/duplicate", data={"return_tab": "templates"}, follow_redirects=False)

    assert duplicated.status_code == 303
    copy = db_session.scalar(select(PromptTemplate).where(PromptTemplate.team_id == team.id, PromptTemplate.name == "Team SOAP 3"))
    assert copy is not None
    assert duplicated.headers["location"] == f"/workspace/library/templates?scope=team&template_id={copy.id}"



def test_user_home_can_create_personal_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    user = make_user(email="user@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/library/templates?scope=personal&template_id=new")
    assert "Templates" in page.text

    save = client.post(
        "/home/personal-templates",
        data={
            "name": "My note",
            "description": "Personal note prompt",
            "prompt_text": "Write a concise follow-up note.",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    template = db_session.scalar(select(PromptTemplate).where(PromptTemplate.owner_user_id == user.id, PromptTemplate.name == "My note"))
    assert template is not None


def test_user_home_can_create_structured_emis_personal_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Structured UI")
    user = make_user(email="structured-user@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "structured-user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/library/templates?scope=personal&template_id=new")
    assert "Sectioned EMIS note" in page.text

    save = client.post(
        "/home/personal-templates",
        data={
            "name": "EMIS note",
            "description": "Structured note prompt",
            "mode": "structured",
            "prompt_text": "Use British English.",
            "section_prompt_problem": "Summarise the problem.",
            "section_prompt_history": "Summarise the history.",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    template = db_session.scalar(select(PromptTemplate).where(PromptTemplate.owner_user_id == user.id, PromptTemplate.name == "EMIS note"))
    assert template is not None
    latest_version = template.versions[-1]
    assert latest_version.mode is TemplateMode.structured
    assert latest_version.config_json["profile"] == "emis"


def test_leader_home_can_create_team_quick_action(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Quick Action Team")
    make_user(email="leader-quick-action@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader-quick-action@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/library/quick-actions?scope=team&quick_action_id=new")
    assert "Quick actions" in page.text

    save = client.post(
        "/home/team-quick-actions",
        data={
            "name": "Arrange review",
            "description": "Shared follow-up action",
            "prompt_text": "Write a short follow-up arranging a review appointment if symptoms persist.",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    quick_action = db_session.scalar(select(QuickAction).where(QuickAction.team_id == team.id, QuickAction.name == "Arrange review"))
    assert quick_action is not None


def test_user_home_can_duplicate_personal_quick_action_with_incremented_name(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Personal Quick Copy")
    user = make_user(email="personal-quick-copy@example.com", password="password-1", team=team, team_role=TeamRole.user)
    quick_action = QuickAction(
        owner_user_id=user.id,
        scope=TemplateScope.user,
        name="Arrange review",
        description="Personal quick action",
        is_active=True,
        created_by_user_id=user.id,
    )
    db_session.add(quick_action)
    db_session.flush()
    db_session.add(QuickActionVersion(quick_action_id=quick_action.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Write review message", created_by_user_id=user.id))
    db_session.commit()

    client.post("/login", data={"email": "personal-quick-copy@example.com", "password": "password-1"}, follow_redirects=False)
    duplicated = client.post(f"/home/personal-quick-actions/{quick_action.id}/duplicate", data={"return_tab": "quick-actions"}, follow_redirects=False)

    assert duplicated.status_code == 303
    copy = db_session.scalar(select(QuickAction).where(QuickAction.owner_user_id == user.id, QuickAction.name == "Arrange review 2"))
    assert copy is not None
    latest_version = copy.versions[-1]
    assert latest_version.prompt_text == "Write review message"
    assert duplicated.headers["location"] == f"/workspace/library/quick-actions?scope=personal&quick_action_id={copy.id}"


def test_leader_home_can_duplicate_team_quick_action_with_next_suffix(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Team Quick Copy")
    leader = make_user(email="team-quick-copy@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    quick_action = QuickAction(
        team_id=team.id,
        scope=TemplateScope.team,
        name="Book bloods",
        description="Shared quick action",
        is_active=True,
        created_by_user_id=leader.id,
    )
    existing_copy = QuickAction(
        team_id=team.id,
        scope=TemplateScope.team,
        name="Book bloods 2",
        description="Existing copy",
        is_active=True,
        created_by_user_id=leader.id,
    )
    db_session.add_all([quick_action, existing_copy])
    db_session.flush()
    db_session.add(QuickActionVersion(quick_action_id=quick_action.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Book tests", created_by_user_id=leader.id))
    db_session.add(QuickActionVersion(quick_action_id=existing_copy.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Older copy", created_by_user_id=leader.id))
    db_session.commit()

    client.post("/login", data={"email": "team-quick-copy@example.com", "password": "password-1"}, follow_redirects=False)
    duplicated = client.post(f"/home/team-quick-actions/{quick_action.id}/duplicate", data={"return_tab": "quick-actions"}, follow_redirects=False)

    assert duplicated.status_code == 303
    copy = db_session.scalar(select(QuickAction).where(QuickAction.team_id == team.id, QuickAction.name == "Book bloods 3"))
    assert copy is not None


def test_leader_home_team_quick_action_can_return_to_restyled_preview(client, db_session, make_team, make_user, make_quick_action):
    team = make_team(name="Clinic Restyled Quick Action Team")
    leader = make_user(email="leader-restyled-quick-action@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Patient SMS",
        prompt_text="Write a short message.",
    )

    client.post("/login", data={"email": "leader-restyled-quick-action@example.com", "password": "password-1"}, follow_redirects=False)

    page = client.get(f"/home-restyled?tab=quick-actions&modal=team-quick-action&team_quick_action_id={quick_action.id}")

    assert page.status_code == 200
    assert 'data-default-tab="quick-actions"' in page.text
    assert 'id="team-quick-action-modal"' in page.text
    assert 'modal-shell is-open' in page.text

    save = client.post(
        "/home/team-quick-actions",
        data={
            "quick_action_id": str(quick_action.id),
            "return_view": "restyled",
            "return_tab": "quick-actions",
            "home_modal": "team-quick-action",
            "name": "Patient SMS updated",
            "description": "Shared follow-up action",
            "prompt_text": "Write a short patient SMS with the agreed plan.",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == "/home-restyled?tab=quick-actions"
    updated = db_session.get(QuickAction, quick_action.id)
    assert updated is not None
    assert updated.name == "Patient SMS updated"


def test_user_home_can_create_personal_quick_action(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Quick Action Personal")
    user = make_user(email="user-quick-action@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "user-quick-action@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/library/quick-actions?scope=personal&quick_action_id=new")
    assert "Quick actions" in page.text

    save = client.post(
        "/home/personal-quick-actions",
        data={
            "name": "Book blood test",
            "description": "Personal follow-up action",
            "prompt_text": "Write a short follow-up asking the patient to book a blood test.",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    quick_action = db_session.scalar(select(QuickAction).where(QuickAction.owner_user_id == user.id, QuickAction.name == "Book blood test"))
    assert quick_action is not None


def test_leader_home_can_delete_team_user(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/members")
    assert "Delete" in page.text
    assert "Delete this user and all owned transcript content immediately?" in page.text

    delete_response = client.post(f"/home/users/{member.id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/workspace/team/members"
    assert db_session.get(type(member), member.id) is None


def test_user_delete_reassigns_hallucination_check_selection(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic Hallucination Delete User")
    leader = make_user(email="leader-delete-checker@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-delete-checker@example.com", password="password-2", team=team, team_role=TeamRole.user)
    llm_config = make_llm_config(team=team, actor=leader, label="Checker LLM", available_models_json=["gpt-4o-mini"])
    selection = TeamHallucinationCheckSelection(
        team_id=team.id,
        llm_config_id=llm_config.id,
        model_name_override="gpt-4o-mini",
        selected_by_user_id=member.id,
    )
    db_session.add(selection)
    db_session.commit()

    client.post("/login", data={"email": "leader-delete-checker@example.com", "password": "password-1"}, follow_redirects=False)
    delete_response = client.post(f"/home/users/{member.id}/delete", follow_redirects=False)

    assert delete_response.status_code == 303
    assert db_session.get(User, member.id) is None
    db_session.refresh(selection)
    assert selection.selected_by_user_id == leader.id


def test_home_restyled_team_management_uses_member_menu_without_duplicate_user_table(client, make_team, make_user):
    team = make_team(name="Clinic Restyled Team Management")
    make_user(email="leader-restyled-team@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="member-restyled-team@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader-restyled-team@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home-restyled?tab=team-management")
    home_css = Path("app/static/css/home.css").read_text(encoding="utf-8")

    assert page.status_code == 200
    assert "Manage members and configuration for Clinic Restyled Team Management." in page.text
    assert "Managed users" not in page.text
    assert "Suspend" in page.text
    assert "Delete" in page.text
    assert "overflow: visible; background: var(--card);" in home_css
    assert ".member-menu[open] { z-index: 40; }" in home_css
    assert "const memberMenuIdleMs = 3500;" in page.text
    assert "document.addEventListener('click', (event) =>" in page.text
    assert "if (menu.open && !menu.contains(event.target)) closeMemberMenu(menu);" in page.text


def test_home_page_uses_flat_sidebar_workspace_layout(client, make_team, make_user):
    team = make_team(name="Clinic Home Flat Layout")
    make_user(email="leader-home-flat@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader-home-flat@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/workspace/team/members")
    components_css = Path("app/static/css/components.css").read_text(encoding="utf-8")

    assert page.status_code == 200
    assert 'class="workspace-shell"' in page.text
    assert 'data-workspace-section="team-members"' in page.text
    assert 'aria-label="Workspace navigation"' in page.text
    assert 'class="brand workspace-sidebar__brand"' in page.text
    assert '<span class="brand-mark" aria-hidden="true"><i data-lucide="feather"></i></span>' in page.text
    assert '<span class="brand-name" data-sidebar-full>OpenScribe</span>' in page.text
    assert "Clinical workspace" not in page.text
    assert ".brand-mark" in components_css
    assert ".brand-name" in components_css
    assert "Back to Scribe" in page.text
    assert 'class="home-shell"' not in page.text
    assert 'class="home-sidebar"' not in page.text


def test_home_service_status_bar_is_visible_to_leaders_only(client, make_team, make_user):
    team = make_team(name="Clinic Home Service Visibility")
    make_user(email="leader-home-services@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="user-home-services@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "user-home-services@example.com", "password": "password-2"}, follow_redirects=False)
    for route in ("/home2",):
        page = client.get(route)
        assert page.status_code == 200
        assert 'class="stat-grid"' not in page.text

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": "leader-home-services@example.com", "password": "password-1"}, follow_redirects=False)
    for route in ("/home2",):
        page = client.get(route)
        assert page.status_code == 200
        assert 'class="stat-grid"' in page.text


def test_admin_restyled_compatibility_route_redirects_system_admin_to_canonical_workspace(client, make_team, make_user):
    team = make_team(name="Clinic Admin Preview")
    admin = make_user(email="admin-preview@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-preview@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(
        f"/admin-restyled?team_id={team.id}&team_tab=llm&stt_config_id=stt-id&llm_config_id=llm-id"
        "&deidentification_provider_id=deid-id&default_template_id=template-id&default_quick_action_id=action-id"
        "&tab=providers&range=30d&audit_since=24h&audit_action=user_locked",
        follow_redirects=False,
    )

    assert page.status_code == 307
    assert page.headers["location"] == (
        f"/admin?team_id={team.id}&stt_config_id=stt-id&llm_config_id=llm-id"
        "&deidentification_provider_id=deid-id&default_template_id=template-id&default_quick_action_id=action-id"
        "&tab=providers&team_tab=llm&range=30d&audit_since=24h&audit_action=user_locked"
    )


def test_admin_restyled_compatibility_route_keeps_system_admin_gate(client, make_team, make_user):
    team = make_team(name="Clinic Admin Compatibility Auth")
    make_user(email="admin-restyled-auth@example.com", password="password-1", is_system_admin=True)
    make_user(email="member-restyled-auth@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-restyled-auth@example.com", "password": "password-2"}, follow_redirects=False)
    page = client.get("/admin-restyled", follow_redirects=False)

    assert page.status_code == 403
    assert "location" not in page.headers


@pytest.mark.parametrize(
    "route",
    [
        "/legacy-admin",
        "/admin2",
        "/transcribe-glm-2",
        "/transcribe-claude",
        "/transcriber_col_changes",
    ],
)
def test_retired_prototype_routes_follow_browser_not_found_redirects(
    client,
    make_team,
    make_user,
    route,
):
    anonymous = client.get(route, follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"

    team = make_team(name=f"Retired prototype {route}")
    member = make_user(email=f"member-{route.strip('/').replace('_', '-')}@example.com", password="password-1", team=team)
    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)
    member_response = client.get(route, follow_redirects=False)
    assert member_response.status_code == 303
    assert member_response.headers["location"] == "/workspace"

    client.post("/logout", follow_redirects=False)
    leader = make_user(
        email=f"leader-{route.strip('/').replace('_', '-')}@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.leader,
    )
    client.post("/login", data={"email": leader.email, "password": "password-2"}, follow_redirects=False)
    leader_response = client.get(route, follow_redirects=False)
    assert leader_response.status_code == 303
    assert leader_response.headers["location"] == "/workspace"

    client.post("/logout", follow_redirects=False)
    admin = make_user(
        email=f"admin-{route.strip('/').replace('_', '-')}@example.com",
        password="password-3",
        is_system_admin=True,
    )
    client.post("/login", data={"email": admin.email, "password": "password-3"}, follow_redirects=False)
    admin_response = client.get(route, follow_redirects=False)
    assert admin_response.status_code == 303
    assert admin_response.headers["location"] == "/admin"





def test_canonical_admin_route_uses_workspace_template(client, make_team, make_user):
    team = make_team(name="Clinic Functional Admin")
    make_user(email="admin-workspace@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-workspace@example.com", "password": "password-1"}, follow_redirects=False)
    home = client.get("/admin")
    page = client.get(f"/admin?team_id={team.id}&tab=providers")

    assert home.status_code == 200
    assert '<main class="workspace">' in home.text
    assert page.status_code == 200
    assert "Clinic Functional Admin" in page.text
    assert 'class="admin-workspace"' in page.text
    assert 'data-panel="provider-policy"' in page.text
    assert 'name="return_view" value="workspace"' in page.text


@pytest.mark.parametrize("return_view", [None, "", "admin", "legacy", "admin2", "unknown"])
def test_invalid_or_absent_admin_return_view_defaults_to_canonical_workspace(return_view):
    assert admin_return_view_value(return_view) == "workspace"
    assert admin_page_route_from_return_view(return_view) == "/admin"
    assert admin_redirect_url(return_view=return_view, return_tab="members", team_id="team-id") == "/admin?team_id=team-id&team_tab=members"


@pytest.mark.parametrize(
    ("return_view", "return_tab", "expected_location"),
    [
        ("workspace", "members", "/admin?team_id={team_id}&team_tab=members"),
        ("restyled", "members", "/admin?team_id={team_id}&team_tab=members"),
        ("legacy", "directory", "/admin?team_id={team_id}&tab=directory"),
        ("admin2", "directory", "/admin?team_id={team_id}&tab=directory"),
        ("unknown", "directory", "/admin?team_id={team_id}&tab=directory"),
    ],
)
def test_admin_post_redirects_preserve_requested_workspace(
    client,
    make_team,
    make_user,
    return_view,
    return_tab,
    expected_location,
):
    team = make_team(name=f"Clinic Redirect {return_view}")
    make_user(email=f"admin-redirect-{return_view}@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": f"admin-redirect-{return_view}@example.com", "password": "password-1"}, follow_redirects=False)

    response = client.post(
        "/admin/teams",
        data={
            "name": f"Created Redirect {return_view}",
            "status": "active",
            "default_retention_days": "30",
            "return_view": return_view,
            "return_tab": return_tab,
            "return_team_id": str(team.id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == expected_location.format(team_id=team.id)


def test_admin_workspace_members_wire_existing_account_routes(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Workspace Members")
    make_user(email="workspace-members-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="existing.member@example.com", password="password-1", team=team)

    client.post("/login", data={"email": "workspace-members-admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=members")

    assert page.status_code == 200
    assert "existing.member@example.com" in page.text
    assert 'action="/admin/users"' in page.text
    assert f'name="team_id" value="{team.id}"' in page.text
    assert 'name="return_view" value="workspace"' in page.text
    assert f'action="/admin/users/{member.id}/suspend"' in page.text
    assert f'action="/admin/users/{member.id}/send-activation"' in page.text
    assert f'action="/admin/users/{member.id}/reset-mfa"' in page.text
    assert f'action="/admin/users/{member.id}/delete"' in page.text
    assert 'name="is_system_admin"' not in page.text

    response = client.post(
        "/admin/users",
        data={
            "full_name": "New Team Member",
            "email": "new.workspace.member@example.com",
            "temporary_password": "password-2",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": "true",
            "return_view": "workspace",
            "return_tab": "members",
            "return_team_id": str(team.id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin?team_id={team.id}&team_tab=members"


def test_admin_workspace_wires_team_deid_assignment_and_danger_actions(client, make_team, make_user, make_deidentification_provider):
    team = make_team(name="Clinic Workspace Lifecycle")
    make_user(email="workspace-lifecycle-admin@example.com", password="password-1", is_system_admin=True)
    provider = make_deidentification_provider(label="Shared De-ID")

    client.post("/login", data={"email": "workspace-lifecycle-admin@example.com", "password": "password-1"}, follow_redirects=False)
    deid = client.get(f"/admin?team_id={team.id}&team_tab=deidentification")
    danger = client.get(f"/admin?team_id={team.id}&team_tab=danger")

    assert deid.status_code == 200
    assert "Shared De-ID" in deid.text
    assert 'action="/admin/deidentification-provider-assignments"' in deid.text
    assert 'name="return_tab"' in deid.text and 'value="deidentification"' in deid.text
    assert danger.status_code == 200
    assert f'action="/admin/teams/{team.id}/delete"' in danger.text
    assert "This cannot be undone" in danger.text


def test_admin_workspace_updates_future_team_retention_default(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Workspace Retention", default_retention_days=30)
    make_user(email="workspace-retention-admin@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": "workspace-retention-admin@example.com", "password": "password-1"}, follow_redirects=False)

    page = client.get(f"/admin?team_id={team.id}&team_tab=defaults")
    assert page.status_code == 200
    assert f'action="/admin/teams/{team.id}/retention"' in page.text
    assert "Applies only to transcript roots created afterward" in page.text

    response = client.post(
        f"/admin/teams/{team.id}/retention",
        data={"default_retention_days": "45", "return_view": "workspace", "return_tab": "defaults"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin?team_id={team.id}&team_tab=defaults"
    db_session.refresh(team)
    assert team.default_retention_days == 45


def test_admin_workspace_provider_policy_wires_existing_selection_routes(client, make_team, make_user, make_stt_config, make_llm_config):
    team = make_team(name="Clinic Workspace Policy")
    admin = make_user(email="workspace-policy-admin@example.com", password="password-1", is_system_admin=True)
    stt = make_stt_config(team=team, actor=admin, label="Policy STT", model_name="whisper-default", available_models_json=["whisper-default", "whisper-fast"])
    llm = make_llm_config(team=team, actor=admin, label="Policy LLM", model_name="model-default", available_models_json=["model-default", "model-review"])
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    stt_response = client.post("/admin/stt-selection", data={"team_id": str(team.id), "stt_config_id": str(stt.id), "purpose": "conversation", "provider_model": "whisper-fast", "language": "en-GB", "return_view": "workspace", "return_tab": "provider-policy"}, follow_redirects=False)
    llm_response = client.post("/admin/llm-selection", data={"team_id": str(team.id), "llm_config_id": str(llm.id), "allowed_model_names": ["model-default", "model-review"], "provider_model": "model-review", "return_view": "workspace", "return_tab": "provider-policy"}, follow_redirects=False)
    assert stt_response.status_code == 303
    assert llm_response.status_code == 303

    page = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")

    assert page.status_code == 200
    assert "Policy STT" in page.text
    assert "Policy LLM" in page.text
    assert page.text.count('action="/admin/stt-selection"') == 2
    assert 'action="/admin/llm-selection"' in page.text
    assert 'action="/admin/hallucination-check-selection"' in page.text
    assert 'action="/admin/deidentification-selection"' in page.text
    assert 'name="return_tab"' in page.text and 'value="provider-policy"' in page.text
    assert page.text.count('data-policy-row=') == 6
    assert 'class="policy-table" data-provider-policy-table' in page.text
    assert 'action="/admin/stt-selection/clear"' in page.text
    assert 'action="/admin/llm-selection/clear"' in page.text
    assert 'value="whisper-fast"' in page.text and 'value="en-GB"' in page.text
    assert 'data-models=' in page.text and "model-review" in page.text
    assert 'data-policy-provider-select' in page.text and 'data-policy-model-select' in page.text
    assert "syncPolicyModels" in page.text
    assert "Whisper Production" not in page.text
    assert "OpenAI Production" not in page.text

    stt_page = client.get(f"/admin?team_id={team.id}&team_tab=stt")
    llm_page = client.get(f"/admin?team_id={team.id}&team_tab=llm")
    assert "Policy STT" in stt_page.text
    assert 'action="/admin/stt-configs/' in stt_page.text
    assert "/test" in stt_page.text and "/inspect" in stt_page.text and "/delete" in stt_page.text
    assert "Whisper Production" not in stt_page.text
    assert "Policy LLM" in llm_page.text
    assert 'action="/admin/llm-configs/' in llm_page.text
    assert "/inspect" in llm_page.text and "/delete" in llm_page.text
    assert "OpenAI Production" not in llm_page.text
    assert 'action="/admin/stt-configs/drafts"' in stt_page.text
    assert 'id="stt-provider-preset"' in stt_page.text and 'value="openai"' in stt_page.text
    assert 'action="/admin/llm-configs/drafts"' in llm_page.text
    assert 'id="llm-provider-preset"' in llm_page.text and 'value="openai"' in llm_page.text


def test_admin_workspace_global_sidebar_areas_render_real_controls(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic Global Admin")
    admin = make_user(email="workspace-global-admin@example.com", password="password-1", is_system_admin=True)
    request = make_account_request(requested_email="requested.workspace@example.com")
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    directory = client.get("/admin?tab=directory")
    requests = client.get("/admin?tab=requests")
    admins = client.get("/admin?tab=system-admins")
    defaults = client.get("/admin?tab=global-defaults")
    audit = client.get("/admin?tab=audit")
    global_usage = client.get("/admin?tab=usage")
    team_usage = client.get(f"/admin?tab=usage&team_id={team.id}")

    assert 'action="/admin/teams"' in directory.text
    assert f"/admin?team_id={team.id}&amp;team_tab=overview" in directory.text
    assert f'action="/admin/account-requests/{request.id}/approve"' in requests.text
    assert f'action="/admin/account-requests/{request.id}/reject"' in requests.text
    assert 'name="return_view"' in requests.text and 'value="workspace"' in requests.text
    assert 'action="/admin/users"' in admins.text
    assert 'name="is_system_admin"' in admins.text and 'value="true"' in admins.text
    assert '/admin/templates/editor?scope=default&amp;return_view=workspace' in defaults.text
    assert 'name="audit_since"' in audit.text
    assert "Service health and consumption across all teams. Metadata only." in global_usage.text
    assert "Service health and consumption for Clinic Global Admin. Metadata only." in team_usage.text
    assert "Service health and consumption across all teams. Metadata only." not in team_usage.text
    assert "Team identity, status and operational summary" not in team_usage.text


def test_admin_directory_without_teams_renders_csrf_bootstrap(client, make_user):
    admin = make_user(
        email="empty-directory-admin@example.com",
        password="password-1",
        is_system_admin=True,
    )
    client.post(
        "/login",
        data={"email": admin.email, "password": "password-1"},
        follow_redirects=False,
    )

    directory = client.get("/admin?tab=directory")

    assert directory.status_code == 200
    assert 'action="/admin/teams"' in directory.text
    assert "window.OpenScribeCSRF" in directory.text


def test_admin_workspace_pending_provider_drafts_offer_finalize_and_cancel(client, db_session, make_team, make_user, make_stt_config, make_llm_config):
    from app.models import LlmConfigSetupStatus, SttConfigSetupStatus

    team = make_team(name="Clinic Workspace Drafts")
    admin = make_user(email="workspace-drafts-admin@example.com", password="password-1", is_system_admin=True)
    stt = make_stt_config(team=team, actor=admin, label="Pending STT", available_models_json=["whisper-1"])
    llm = make_llm_config(team=team, actor=admin, label="Pending LLM", available_models_json=["gpt-4o-mini"])
    stt.setup_status = SttConfigSetupStatus.pending_model_selection
    llm.setup_status = LlmConfigSetupStatus.pending_model_selection
    db_session.commit()
    db_session.refresh(stt)
    db_session.refresh(llm)
    assert stt.setup_status == SttConfigSetupStatus.pending_model_selection.value
    assert llm.setup_status == LlmConfigSetupStatus.pending_model_selection.value
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    stt_page = client.get(f"/admin?team_id={team.id}&team_tab=stt&stt_config_id={stt.id}")
    llm_page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={llm.id}")

    assert "Finish Pending STT" in stt_page.text
    assert f'/admin/stt-configs/{stt.id}/finalize' in stt_page.text
    assert f'action="/admin/stt-configs/{stt.id}/draft-cancel"' in stt_page.text
    assert f'action="/admin/llm-configs/{llm.id}/finalize"' in llm_page.text
    assert f'action="/admin/llm-configs/{llm.id}/draft-cancel"' in llm_page.text


def test_admin_workspace_provider_redesign_has_explicit_safe_actions(client, db_session, make_team, make_user, make_stt_config, make_llm_config):
    team = make_team(name="Clinic Provider Actions")
    admin = make_user(email="provider-actions@example.com", password="password-1", is_system_admin=True)
    stt = make_stt_config(team=team, actor=admin, label="Original STT", base_url="http://127.0.0.1:9300")
    llm = make_llm_config(team=team, actor=admin, label="Original LLM", base_url="http://localhost:11434", available_models_json=["llama3"])
    stt_ref, llm_ref = stt.vault_secret_ref, llm.vault_secret_ref
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    stt_page = client.get(f"/admin?team_id={team.id}&team_tab=stt")
    llm_page = client.get(f"/admin?team_id={team.id}&team_tab=llm")
    assert "Edit details" not in stt_page.text + llm_page.text
    assert "Change connection" not in stt_page.text + llm_page.text
    assert 'data-edit-stt-provider' in stt_page.text and '>Edit</button>' in stt_page.text
    assert 'data-edit-llm-provider' in llm_page.text and '>Edit</button>' in llm_page.text
    assert 'name="revision_of_config_id"' in stt_page.text and f'data-config-id="{stt.id}"' in stt_page.text
    assert 'name="revision_of_config_id"' in llm_page.text and f'data-config-id="{llm.id}"' in llm_page.text
    assert 'addSttProviderButton.addEventListener("click", () => openSttWizard());' in stt_page.text
    assert 'addLlm.addEventListener("click", () => openLlm());' in llm_page.text
    assert 'const custom = llmProvider === "Custom OpenAI-compatible"' in llm_page.text
    assert 'document.getElementById("llm-base-url-field").hidden = !(' in llm_page.text
    assert 'document.getElementById("llm-region-field").hidden = !bedrock;' in llm_page.text
    assert 'document.getElementById("llm-base-url-field").hidden = false;' not in llm_page.text
    assert ".wizard .field[hidden]" in llm_page.text
    assert ".llm-model-option[hidden]" in llm_page.text
    assert "90px 100px minmax(260px, auto)" in llm_page.text
    assert ".llm-provider-row .quick-actions" in llm_page.text
    assert "minmax(130px, 0.7fr)" in stt_page.text
    assert ".stt-provider-row .quick-actions" in stt_page.text
    assert 'form.reset();' in stt_page.text and 'form.reset();' in llm_page.text
    assert "Leave blank to keep saved credential" in stt_page.text and "Leave blank to keep saved credential" in llm_page.text
    assert f'data-provider-label="{stt.label}"' in stt_page.text
    assert f'data-base-url="{stt.base_url}"' in stt_page.text
    assert f'data-provider-label="{llm.label}"' in llm_page.text
    assert f'data-base-url="{llm.base_url}"' in llm_page.text
    assert stt_ref not in stt_page.text and llm_ref not in llm_page.text
    markup = stt_page.text + llm_page.text
    assert "providerDefaults" not in markup
    assert "llmData" not in markup
    assert "Discovered model 1" not in markup
    assert "gpt-4.1-mini" not in markup
    assert 'apiJson("/api/v1/stt-configs/drafts"' in stt_page.text
    assert 'apiJson("/api/v1/llm-configs/drafts"' in llm_page.text
    assert 'revision_of_config_id: value("revision_of_config_id") || null' in markup
    assert 'bearer_token: value("bearer_token") || null' in markup
    assert 'if (llmProvider === "Gemini Enterprise")' in llm_page.text
    assert 'google_auth_method: value("google_auth_method") || null' in llm_page.text
    assert "body: JSON.stringify(payload)" in llm_page.text
    assert "/api/v1/stt-configs/${encodeURIComponent(sttDraftId)}/finalize" in stt_page.text
    assert "/api/v1/llm-configs/${encodeURIComponent(llmDraftId)}/finalize" in llm_page.text
    assert "result.available_model_options" in markup
    assert "result.config.base_url" in markup
    assert "vault_secret_ref" not in markup
    assert 'name="model_name"' in markup
    assert 'name="language"' in stt_page.text

    assert '/details"' not in stt_page.text + llm_page.text


def test_admin_provider_wizards_render_safe_contextual_errors(client, make_team, make_user):
    team = make_team(name="Clinic Wizard Errors")
    admin = make_user(email="wizard-errors@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    page = client.get(f"/admin?team_id={team.id}&team_tab=llm")
    markup = page.text

    assert markup.count('class="wizard-error"') == 2
    assert markup.count('role="alert" aria-live="assertive" tabindex="-1" hidden') == 2
    assert ".wizard-error[hidden]" in markup
    assert "border: 1px solid #b42318" in markup
    assert 'field.setAttribute("aria-invalid", "true")' in markup
    assert 'field.classList.add("wizard-field-error")' in markup
    assert 'field.removeAttribute("aria-invalid")' in markup
    assert 'typeof safeError.details?.field === "string"' in markup
    assert 'new Set(["label", "base_url", "openapi_path", "bedrock_region", "bearer_token", "model_name", "language", "provider_preset"])' in markup
    assert 'if (typeof sourceDetails?.field === "string") details = { field: sourceDetails.field };' in markup
    assert "error.status = response.status" in markup
    assert "error.code = code" in markup
    assert 'wizardError("llm-wizard-error", error);' in markup
    assert 'wizardError("stt-wizard-error", error);' in markup
    assert "JSON.stringify(sourceDetails)" not in markup
    assert "element.textContent = error" not in markup
    assert "Check the API key and confirm this account has access to the provider." in markup
    assert "Verify the endpoint, network connection, and provider availability." in markup
    assert "Review the highlighted field and the provider-specific requirements." in markup
    assert "The endpoint worked, but its model list is unavailable." in markup


def test_admin_change_llm_connection_stages_selected_revision(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Revision")
    admin = make_user(email="provider-revision@example.com", password="password-1", is_system_admin=True)
    root = make_llm_config(team=team, actor=admin, label="Local LLM", provider_preset="ollama", base_url="http://localhost:11434", available_models_json=["llama3"])
    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda *, base_url, bearer_token: ["llama3"])
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    response = client.post("/admin/llm-configs/drafts", data={"team_id": str(team.id), "revision_of_config_id": str(root.id), "provider_preset": "ollama", "label": root.label, "base_url": "http://localhost:11435", "return_view": "workspace", "return_tab": "llm"}, follow_redirects=False)
    assert response.status_code == 303
    revision = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.revision_of_config_id == root.id))
    assert revision is not None
    assert f"llm_config_id={revision.id}" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "Updating existing provider" in page.text
    assert "Discard change" in page.text
    assert root.vault_secret_ref not in page.text


def test_admin_provider_setup_keeps_team_scope_panel_before_team_selection(client, make_team, make_user):
    team = make_team(name="Clinic Provider Entry")
    make_user(email="admin-provider-entry@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-provider-entry@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=providers")
    invalid_team_page = client.get("/admin?tab=providers&team_id=00000000-0000-0000-0000-000000000000")
    selected_team_page = client.get(f"/admin?tab=providers&team_id={team.id}")

    assert page.status_code == 200
    assert 'class="panel provider-scope"' in page.text
    assert "Team scope" in page.text
    assert "Choose target team before editing provider availability or active selections." in page.text
    assert 'action="/admin"' in page.text
    assert 'name="tab" value="providers"' in page.text
    assert f'value="{team.id}"' in page.text
    assert "Clinic Provider Entry" in page.text
    assert "Configure STT, LLM, and de-identification." not in page.text
    assert invalid_team_page.status_code == 200
    assert 'class="panel provider-scope"' in invalid_team_page.text
    assert selected_team_page.status_code == 200
    assert 'data-panel="provider-policy"' in selected_team_page.text
    assert re.search(r'class="tab active"[^>]*data-tab="provider-policy"', selected_team_page.text, re.S)


def test_admin_llm_selection_uses_visible_model_tiles_and_default_dropdown(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic Admin LLM Tiles")
    admin = make_user(email="admin-llm-tiles@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Clinic LLM",
        model_name="gpt-4o-mini",
        available_models_json=["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
    )

    client.post("/login", data={"email": "admin-llm-tiles@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm")

    assert page.status_code == 200
    assert 'action="/admin/llm-configs/drafts"' in page.text
    assert "Clinic LLM" in page.text

    save = client.post(
        "/admin/llm-selection",
        data={
            "team_id": str(team.id),
            "llm_config_id": str(config.id),
            "allowed_model_names": ["gpt-4.1-mini", "gpt-4.1"],
            "provider_model": "gpt-4.1",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    selection = db_session.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id))
    assert selection is not None
    assert selection.allowed_models_json == ["gpt-4.1-mini", "gpt-4.1"]
    assert selection.model_name_override == "gpt-4.1"


def test_admin_pages_can_configure_hallucination_checker(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic Admin Checker UI")
    admin = make_user(email="admin-checker-ui@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Checker LLM",
        model_name="gpt-4o-mini",
        available_models_json=["gpt-4o-mini", "gpt-4.1-mini"],
    )

    client.post("/login", data={"email": "admin-checker-ui@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")
    assert page.status_code == 200
    assert "Hallucination checker" in page.text
    assert 'action="/admin/hallucination-check-selection"' in page.text
    assert 'data-policy-model-form' in page.text
    assert 'data-policy-model-select' in page.text
    assert "gpt-4.1-mini" in page.text
    assert "Unconfigured" in page.text

    saved = client.post(
        "/admin/hallucination-check-selection",
        data={"team_id": str(team.id), "llm_config_id": str(config.id), "provider_model": "gpt-4.1-mini"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    selection = db_session.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id))
    assert selection is not None
    assert selection.llm_config_id == config.id
    assert selection.model_name_override == "gpt-4.1-mini"

    cleared = client.post(
        "/admin/hallucination-check-selection/clear",
        data={"team_id": str(team.id)},
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert db_session.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id)) is None


def test_transcribe_documents_show_hallucination_check_panel():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    documents_js = (root / "app" / "static" / "js" / "transcribe" / "documents.js").read_text(encoding="utf-8")

    assert "Hallucination check" in documents_js
    assert "Debug payload not available. Set HALLUCINATION_CHECK_DEBUG_UI=1 before generating the note" in documents_js
    assert "documents.js?v=20260718-note-pill-datetime" in app_js


def test_admin_llm_draft_flow_hides_key_after_saved_and_shows_pending_state(
    client, db_session, make_team, make_user, make_llm_config
):
    team = make_team(name="Clinic LLM Draft UI")
    admin = make_user(email="admin-llm-draft-ui@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        label="OpenRouter · Clinic LLM Draft UI",
        provider_preset="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model_name=None,
        available_models_json=["openai/gpt-4o-mini"],
        is_active=False,
    )
    config.setup_status = LlmConfigSetupStatus.pending_model_selection
    db_session.add(config)
    db_session.commit()

    client.post("/login", data={"email": "admin-llm-draft-ui@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={config.id}")

    assert page.status_code == 200
    assert f"Finish {config.label}" in page.text
    assert "Finish provider setup" in page.text
    assert f'action="/admin/llm-configs/{config.id}/finalize"' in page.text
    assert f'action="/admin/llm-configs/{config.id}/replace-credential"' in page.text
    assert "Available for team selection" in page.text


def test_admin_llm_check_key_creates_draft_and_redirects_to_model_step(
    client, db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Clinic LLM Draft Create UI")
    make_user(email="admin-llm-draft-create-ui@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_openai_compatible_chat_models",
        lambda *, provider_preset, api_key, base_url: ["openai/gpt-4o-mini"],
    )
    monkeypatch.setattr(
        "app.services.llm.write_team_llm_bearer_token",
        lambda *, team_id, config_id, bearer_token: f"secret:openscribe/llm/team/{team_id}/config/{config_id}",
    )

    client.post("/login", data={"email": "admin-llm-draft-create-ui@example.com", "password": "password-1"}, follow_redirects=False)
    created = client.post(
        "/admin/llm-configs/drafts",
        data={
            "team_id": str(team.id),
            "label": "Admin Router",
            "provider_preset": "openrouter",
            "base_url": "",
            "bearer_token": "ui-secret",
            "return_view": "workspace",
            "return_tab": "stt",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    saved = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved is not None
    assert saved.setup_status == LlmConfigSetupStatus.pending_model_selection
    assert saved.model_name is None
    assert saved.label == "Admin Router"
    assert f"llm_config_id={saved.id}" in created.headers["location"]


def test_admin_stt_deepgram_draft_pages_show_model_dropdown_without_key_field(
    client, db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Clinic STT Draft UI")
    make_user(email="admin-stt-draft-ui@example.com", password="password-1", is_system_admin=True)

    def fake_get(url, *, headers=None, timeout=None):
        assert url == "https://api.eu.deepgram.com/v1/models"
        assert headers == {"Authorization": "Token dg-secret"}
        return FakeHttpxResponse(
            {
                "stt": [
                    {"canonical_name": "nova-3", "batch": True},
                    {"canonical_name": "nova-2", "batch": True},
                ]
            }
        )

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    client.post("/login", data={"email": "admin-stt-draft-ui@example.com", "password": "password-1"}, follow_redirects=False)
    created = client.post(
        "/admin/stt-configs/drafts",
        data={
            "team_id": str(team.id),
            "provider_preset": "deepgram",
            "bearer_token": "dg-secret",
            "return_view": "admin",
            "return_tab": "providers",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    saved = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved is not None
    assert saved.setup_status == SttConfigSetupStatus.pending_model_selection
    assert saved.available_models_json == ["nova-2", "nova-3"]

    page = client.get(created.headers["location"])
    assert page.status_code == 200
    assert team.name in page.text, created.headers["location"]
    assert f"Finish {saved.label}" in page.text, created.headers["location"]
    assert f'action="/admin/stt-configs/{saved.id}/finalize"' in page.text, page.text[:4000]
    finalize_form = page.text.split(f'action="/admin/stt-configs/{saved.id}/finalize"', 1)[1].split("</form>", 1)[0]
    assert '<select name="provider_model">' in finalize_form
    assert '<option value="nova-3"' in finalize_form
    assert '<option value="nova-2"' in finalize_form
    assert 'name="bearer_token"' not in finalize_form

    assert "dg-secret" not in page.text


def test_admin_llm_bad_key_stays_on_credential_step_without_ready_state(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Bad Key UI")
    make_user(email="admin-llm-bad-key-ui@example.com", password="password-1", is_system_admin=True)

    def reject_key(**kwargs):
        raise AppError(401, "llm_invalid_credential", "The API key was rejected by the provider.")

    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", reject_key)
    client.post("/login", data={"email": "admin-llm-bad-key-ui@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.post(
        "/admin/llm-configs/drafts",
        data={
            "team_id": str(team.id),
            "label": "Bad Router",
            "provider_preset": "openrouter",
            "base_url": "",
            "bearer_token": "bad-key",
            "return_view": "workspace",
            "return_tab": "llm",
        },
    )

    assert response.status_code == 401
    assert "The API key was rejected by the provider. Check the key and try again." in response.text
    assert 'action="/admin/llm-configs/drafts"' in response.text
    assert 'name="bearer_token"' in response.text
    assert "Ready · unavailable" not in response.text
    assert "Setup incomplete" not in response.text
    assert db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id)) is None


def test_admin_llm_manual_model_step_shows_discovery_warning(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Manual Warning UI")
    make_user(email="admin-llm-manual-warning-ui@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda *, base_url, bearer_token: [])

    client.post("/login", data={"email": "admin-llm-manual-warning-ui@example.com", "password": "password-1"}, follow_redirects=False)
    draft = client.post(
        "/admin/llm-configs/drafts",
        data={"team_id": str(team.id), "provider_preset": "ollama", "base_url": "http://localhost:11434"},
        follow_redirects=False,
    )
    assert draft.status_code == 303
    saved = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={saved.id}")

    assert "Models could not be discovered. You can save this model manually, but generation may fail if the model name or endpoint is wrong." in page.text



def test_admin_restyled_stt_config_redirect_normalizes_to_canonical_workspace(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Admin Restyled STT")
    make_user(email="admin-restyled-stt@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-restyled-stt@example.com", "password": "password-1"}, follow_redirects=False)
    save = client.post(
        "/admin/stt-configs",
        data={
            "team_id": str(team.id),
            "label": "Admin STT",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:7000",
            "bearer_token": "secret-token",
            "provider_model": "whisper-1",
            "language": "en",
            "extra_form_fields_json": "{\"chunk_mode\":\"memory\"}",
            "is_active": "true",
            "return_view": "restyled",
            "return_tab": "providers",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&tab=providers"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Admin STT"


def test_retired_admin2_stt_return_view_closes_to_canonical_admin(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Retired Admin2 STT")
    make_user(email="retired-admin2-stt@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "retired-admin2-stt@example.com", "password": "password-1"}, follow_redirects=False)
    save = client.post(
        "/admin/stt-configs",
        data={
            "team_id": str(team.id),
            "label": "Retired Admin2 STT",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:7000",
            "bearer_token": "secret-token",
            "provider_model": "whisper-1",
            "language": "en",
            "extra_form_fields_json": "{\"chunk_mode\":\"memory\"}",
            "is_active": "true",
            "return_view": "admin2",
            "return_tab": "stt",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&team_tab=stt"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Retired Admin2 STT"


def test_retired_admin2_quick_action_return_view_closes_to_canonical_admin(
    client, db_session, make_user, make_default_quick_action
):
    admin = make_user(email="retired-admin2-quick-actions@example.com", password="password-1", is_system_admin=True)
    quick_action = make_default_quick_action(actor=admin, name="Retired Admin2 existing action")

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    saved = client.post(
        "/admin/default-quick-actions",
        data={
            "quick_action_id": str(quick_action.id),
            "name": "Retired Admin2 saved action",
            "description": "Preserve tab",
            "prompt_text": "Write follow-up.",
            "is_active": "true",
            "return_view": "admin2",
            "return_tab": "quick-actions",
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin?tab=quick-actions"
    db_session.refresh(quick_action)
    assert quick_action.name == "Retired Admin2 saved action"




def test_non_admin_cannot_open_admin_audit_tab(client, make_team, make_user):
    team = make_team(name="Clinic Audit Blocked")
    make_user(email="audit-user@example.com", password="password-1", team=team)

    client.post("/login", data={"email": "audit-user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=audit")

    assert page.status_code == 403
    assert "Security audit" not in page.text


def test_admin_audit_tab_clamps_overflowing_lookback(client, make_user):
    admin = make_user(email="audit-overflow-admin@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)

    page = client.get("/admin?tab=audit&audit_since=999999999999h")

    assert page.status_code == 200


def test_admin_restyled_account_request_reject_redirects_to_canonical_workspace(client, db_session, make_team, make_user, make_account_request):
    make_team(name="Clinic Admin Requests")
    make_user(email="admin-restyled-requests@example.com", password="password-1", is_system_admin=True)
    account_request = make_account_request(
        requested_name="Admin Request Example",
        requested_email="admin-request@example.com",
        requested_team_name="Clinic Admin Requests",
    )

    client.post("/login", data={"email": "admin-restyled-requests@example.com", "password": "password-1"}, follow_redirects=False)
    rejected = client.post(
        f"/admin/account-requests/{account_request.id}/reject",
        data={
            "review_notes": "No capacity",
            "return_view": "restyled",
            "return_tab": "requests",
        },
        follow_redirects=False,
    )

    assert rejected.status_code == 303
    assert rejected.headers["location"] == "/admin?tab=requests"
    db_session.refresh(account_request)
    assert account_request.status.value == "rejected"


def test_user_home_upload_shows_missing_stt_message_with_team_leader_email(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-2"}, follow_redirects=False)
    response = client.post(
        "/transcribe/upload",
        data={"title": "Visit recording"},
        files={"audio": ("visit.wav", b"fake-audio", "audio/wav")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "message_kind=error" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "No STT configured, please ask your team leader leader@example.com" in page.text
    assert db_session.scalar(select(Transcript)) is None
    assert db_session.scalar(select(TranscriptIngestionJob)) is None


def test_user_home_can_queue_file_transcription_and_see_recent_transcript(client, db_session, monkeypatch, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_stt_selection(config=config, actor=leader)
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", lambda **kwargs: (_ for _ in ()).throw(AssertionError("direct publish forbidden")))
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    created = client.post(
        "/transcribe/sessions",
        data={"title": "Visit recording", "ingestion_mode": "whole_file"},
        follow_redirects=False,
    )
    transcript_id = created.headers["location"].split("transcript_id=", 1)[1]
    response = client.post(
        "/transcribe/upload",
        data={"title": "Visit recording", "transcript_id": transcript_id},
        files={"audio": ("visit.wav", b"fake-audio", "audio/wav")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "queued_transcript_id=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "Audio file queued for transcription" in page.text
    assert "OpenScribe" in page.text
    assert "Visit recording" in page.text

    transcript = db_session.scalar(select(Transcript).where(Transcript.title == "Visit recording"))
    assert transcript is not None
    assert transcript.ingestion_mode is TranscriptIngestionMode.whole_file
    job = db_session.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.transcript_id == transcript.id))
    assert job is not None
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id))
    assert dispatch is not None and dispatch.state is TaskDispatchState.pending
    assert job.celery_task_id == str(dispatch.task_id)


def test_browser_transcribe_upload_shares_rate_limit_bucket_with_api_route(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_stt_selection(config=config, actor=leader)
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)

    transcripts = [
        Transcript(
            owner_user_id=member.id,
            team_id=team.id,
            title=f"Visit {upload_no}",
            ingestion_mode=TranscriptIngestionMode.whole_file,
            status=TranscriptStatus.ready,
            retention_days_applied=30,
            retention_expires_at=utcnow() + timedelta(days=30),
        )
        for upload_no in range(1, 3)
    ]
    db_session.add_all(transcripts)
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)

    api_upload = client.post(
        f"/api/v1/transcripts/{transcripts[0].id}/audio-file",
        files={"audio": ("visit-1.wav", b"fake-audio", "audio/wav")},
    )
    assert api_upload.status_code == 202

    blocked_browser_upload = client.post(
        "/transcribe/upload",
        data={"title": "Visit 2", "transcript_id": str(transcripts[1].id)},
        files={"audio": ("visit-2.wav", b"fake-audio", "audio/wav")},
        follow_redirects=False,
    )
    assert blocked_browser_upload.status_code == 429


def test_browser_transcribe_upload_rejects_missing_csrf_token(
    raw_client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_stt_selection(config=config, actor=leader)
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    login_response = raw_client.post("/api/v1/auth/login", json={"email": "member@example.com", "password": "password-3"})
    assert login_response.status_code == 200
    page = raw_client.get("/transcribe")
    assert page.status_code == 200
    assert raw_client.cookies.get("openscribe_csrf")
    assert "Visit one" in page.text
    assert 'class="session-status-icon session-status-icon--waiting"' in page.text
    assert 'form="bulk-delete-sessions"' in page.text

    rejected = raw_client.post(
        "/transcribe/upload",
        data={"title": "Visit one", "transcript_id": str(transcript.id)},
        files={"audio": ("visit.wav", b"fake-audio", "audio/wav")},
    )

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "forbidden"
    assert rejected.json()["error"]["message"] == "Cross-origin request rejected"


def test_user_transcribe_page_shows_workspace_shell(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="member-shell@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-shell@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert "OpenScribe" in page.text
    assert 'data-new-session-button' in page.text
    assert 'data-session-list-sentinel' in page.text
    assert "Create new consultation" in page.text
    assert 'data-record-toggle' in page.text
    assert 'data-audio-action-trigger' in page.text
    assert 'data-recording-mode-select' in page.text
    assert 'data-active-draft' in page.text
    assert 'data-active-status' in page.text
    assert 'data-session-progress' in page.text
    assert 'data-copy-transcript' in page.text
    assert 'data-template-picker-button' in page.text
    assert 'data-template-picker-modal' in page.text
    assert 'data-note-options-menu' in page.text
    assert 'data-note-options-length-select' in page.text
    assert 'data-note-options-detail-select' in page.text
    assert "Note options" in page.text
    assert 'data-note-selector' in page.text
    assert 'Copy transcript' in page.text
    assert 'data-select-structured-selection' in page.text
    assert "Record" in page.text
    assert "Upload" in page.text
    assert "Quick guide" in page.text
    assert 'data-tour-overlay' in page.text
    assert 'data-tour-scrim="top"' in page.text
    assert 'data-tour-scrim="right"' in page.text
    assert "background: var(--accent);" in Path("app/static/css/transcribe.css").read_text()
    assert 'src="/static/vendor/lucide/1.8.0/lucide.min.js"' in page.text
    assert 'data-lucide="mic"' in page.text
    assert 'data-lucide="upload"' in page.text
    assert "Create a transcript root first" not in page.text
    assert 'action="/transcribe/sessions/delete"' in page.text
    assert 'data-route-base="/workspace"' in page.text
    assert 'data-workspace-stream-endpoint="' in page.text
    assert 'src="/static/vendor/onnxruntime-web/1.22.0/ort.wasm.min.js"' in page.text
    assert 'src="/static/vendor/vad-web/0.0.29/bundle.min.js"' in page.text
    assert 'id="transcribe-bootstrap"' in page.text
    assert 'data-session-panel-toggle' in page.text
    assert 'data-session-panel-close' in page.text
    assert 'data-primary-sidebar' in page.text
    assert 'data-sidebar-resize' in page.text
    assert 'href="/workspace/account"' in page.text
    assert 'href="/settings"' not in page.text
    assert 'aria-label="Workspace navigation"' in page.text
    assert "My Library" in page.text
    assert 'src="/static/js/transcribe/app.js?v=20260720-concurrent-followup"' in page.text
    assert "://medscribe.duckdns.org/static/js/transcribe/app.js" not in page.text



def test_transcribe_page_does_not_block_on_uncached_stt_health(client, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinic Transcribe Fast Paint")
    admin = make_user(email="fast-paint-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="fast-paint-member@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Slow health STT", base_url="http://127.0.0.1:9300")
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.conversation)

    def fail_if_live_health_checked(*args, **kwargs):
        raise AssertionError("initial page render should not perform live STT health check")

    monkeypatch.setattr("app.services.stt.httpx.get", fail_if_live_health_checked)

    client.post("/login", data={"email": member.email, "password": "password-2"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert "Speech service health has not been checked yet." in page.text


def test_transcribe_page_bootstraps_saved_working_note(client, make_team, make_user):
    team = make_team(name="Clinic Working Note")
    make_user(email="working-note@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "working-note@example.com", "password": "password-3"}, follow_redirects=False)
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Working note refresh", "ingestion_mode": "whole_file"},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    note_response = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Refresh keeps this working note."},
    )
    assert note_response.status_code == 200

    page = client.get(f"/transcribe?transcript_id={transcript_id}")

    assert page.status_code == 200
    assert '"activeWorkingNote"' in page.text
    assert "Refresh keeps this working note." in page.text


def _generate_create_form_block(html: str) -> str:
    return html.split("data-generate-output-form", 1)[1].split("</form>", 1)[0]


def _quick_action_select_block(html: str) -> str:
    return html.split('data-quick-action-select', 1)[0].rsplit('<select', 1)[1]


def _run_quick_action_trigger_block(html: str) -> str:
    return html.split('data-run-quick-action-trigger', 1)[0].rsplit('<button', 1)[1]


def _generate_followup_trigger_block(html: str) -> str:
    return html.split('data-generate-followup-trigger', 1)[0].rsplit('<button', 1)[1]


def test_transcribe_create_button_enabled_for_saved_working_note(
    client,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Working Note Create")
    admin = make_user(email="working-note-create-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="working-note-create-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="working-note-create-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(scope=TemplateScope.user, owner=member, actor=member, name="Working note template")

    client.post("/login", data={"email": "working-note-create-member@example.com", "password": "password-3"}, follow_redirects=False)
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Working note only", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    note_response = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Use conservative management."},
    )
    assert note_response.status_code == 200

    page = client.get(f"/transcribe?transcript_id={transcript_id}")

    assert page.status_code == 200
    assert "disabled" not in _generate_create_form_block(page.text)


def test_transcribe_quick_actions_enabled_for_saved_working_note(
    client,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Working Note Quick Action UI")
    admin = make_user(email="working-note-qa-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="working-note-qa-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="working-note-qa-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Working note message")

    client.post("/login", data={"email": "working-note-qa-member@example.com", "password": "password-3"}, follow_redirects=False)
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Working note only quick action", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    note_response = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Send a text after results."},
    )
    assert note_response.status_code == 200

    page = client.get(f"/transcribe?transcript_id={transcript_id}")

    assert page.status_code == 200
    assert "disabled" not in _quick_action_select_block(page.text)
    assert "disabled" not in _run_quick_action_trigger_block(page.text)

def test_transcribe_create_button_enabled_for_saved_dictation(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Dictation Create")
    admin = make_user(email="dictation-create-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="dictation-create-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="dictation-create-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(scope=TemplateScope.user, owner=member, actor=member, name="Dictation template")

    client.post("/login", data={"email": "dictation-create-member@example.com", "password": "password-3"}, follow_redirects=False)
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Dictation only", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    update_post_consultation_dictation(db_session, member, transcript_id=UUID(transcript_id), combined_text="Book blood tests.")

    page = client.get(f"/transcribe?transcript_id={transcript_id}")

    assert page.status_code == 200
    assert "disabled" not in _generate_create_form_block(page.text)


def test_transcribe_quick_actions_enabled_for_saved_dictation(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Dictation Quick Action UI")
    admin = make_user(email="dictation-qa-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="dictation-qa-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="dictation-qa-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Dictation message")

    client.post("/login", data={"email": "dictation-qa-member@example.com", "password": "password-3"}, follow_redirects=False)
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Dictation only quick action", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    update_post_consultation_dictation(db_session, member, transcript_id=UUID(transcript_id), combined_text="Send patient action list.")

    page = client.get(f"/transcribe?transcript_id={transcript_id}")

    assert page.status_code == 200
    assert "disabled" not in _quick_action_select_block(page.text)
    assert "disabled" not in _run_quick_action_trigger_block(page.text)


def test_transcribe_followups_enabled_for_saved_dictation(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Dictation Followup UI")
    admin = make_user(email="dictation-followup-ui-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="dictation-followup-ui-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="dictation-followup-ui-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Dictation message")

    client.post("/login", data={"email": "dictation-followup-ui-member@example.com", "password": "password-3"}, follow_redirects=False)
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Dictation only followup UI", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    assert transcript_response.status_code == 201
    transcript_id = transcript_response.json()["id"]
    update_post_consultation_dictation(db_session, member, transcript_id=UUID(transcript_id), combined_text="Send blood test instructions.")

    page = client.get(f"/transcribe?transcript_id={transcript_id}&tab=followups")

    assert page.status_code == 200
    assert "disabled" not in _generate_followup_trigger_block(page.text)
    assert re.search(
        r"<textarea\b(?=[^>]*data-quick-action-context-input)(?=[^>]*data-followup-prompt-input)(?![^>]*disabled)[^>]*>",
        page.text,
    )


def test_transcribe_create_button_ignores_existing_generated_note_without_source(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Old Generated Note")
    admin = make_user(email="old-note-create-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="old-note-create-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="old-note-create-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=member, actor=member, name="Old note template")
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Old generated note only",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=member.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=version.id,
            generator_type=GeneratedDocumentGeneratorType.template,
            template_version_id=template.versions[-1].id,
            source_template_name=template.name,
            status=GeneratedDocumentStatus.ready,
            title="Old note",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="Existing generated text",
            edited_output_text_encrypted="Existing generated text",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()
    client.post("/login", data={"email": "old-note-create-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "disabled" in _generate_create_form_block(page.text)


def test_transcribe_page_includes_mobile_layout_assets(client, make_team, make_user):
    team = make_team(name="Clinic Mobile")
    make_user(
        email="mobile-user@example.com",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )

    client.post(
        "/login",
        data={"email": "mobile-user@example.com", "password": "password-1"},
        follow_redirects=False,
    )

    page = client.get("/transcribe")

    assert page.status_code == 200
    assert "/static/css/tokens.css?v=20260701-token-harmonise" in page.text
    assert "/static/css/transcribe.css?v=20260719-session-panel-align" in page.text
    assert "/static/css/transcribe-mobile.css" in page.text
    assert "/static/js/workspace/app.js" in page.text
    assert "/static/js/transcribe/mobile.js" not in page.text
    assert 'data-workspace-endpoint="' in page.text


def test_user_transcribe_page_keeps_shared_mobile_navigation_reachable(client, make_team, make_user):
    team = make_team(name="Clinic Mobile Workspace Navigation")
    make_user(
        email="member-mobile-workspace@example.com",
        password="password-3",
        team=team,
        team_role=TeamRole.user,
    )

    client.post(
        "/login",
        data={"email": "member-mobile-workspace@example.com", "password": "password-3"},
        follow_redirects=False,
    )
    page = client.get("/workspace")
    workspace_css = Path("app/static/css/workspace.css").read_text()

    assert page.status_code == 200
    assert 'data-workspace-drawer-toggle' in page.text
    assert 'src="/static/js/workspace/app.js' in page.text
    assert ".workspace-page--scribe .workspace-mobile-header { display: none; }" not in workspace_css
    assert ".workspace-page--scribe .workspace-shell { flex-direction: column; }" in workspace_css


def test_user_transcribe_page_namespaces_legacy_note_tabs(client, make_team, make_user):
    team = make_team(name="Clinic Legacy Note Tabs")
    make_user(email="legacy-note-tabs@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "legacy-note-tabs@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert 'data-generated-structured-panel' in page.text
    assert 'data-generated-freeform-panel' in page.text
    assert 'data-tab-trigger="output"' in page.text
    assert 'data-tab-panel="output"' in page.text


def test_user_transcribe_page_exposes_home_and_context_settings_controls(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team = make_team(name="Clinic Transcribe Settings")
    member = make_user(email="member-transcribe-settings@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Current session",
        current_draft_text_encrypted="Draft text",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    make_template(scope=TemplateScope.user, owner=member, actor=member, name="My note")
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="My quick action")

    client.post("/login", data={"email": "member-transcribe-settings@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'href="/home"' not in page.text
    assert 'href="/workspace/account"' in page.text
    assert 'href="/workspace/preferences"' in page.text
    assert 'data-workspace-settings-link' in page.text
    assert 'justify-between border-b border-stone bg-white px-4 gap-3' in page.text
    assert f'data-settings-url="/workspace/library/templates?scope=personal&template_id=' in page.text
    assert 'return_view=transcribe' not in page.text
    assert 'queued_transcript_id=' not in page.text
    assert 'transcribe_tab=output' not in page.text
    assert f'data-settings-url="/workspace/library/quick-actions?scope=personal&quick_action_id=' in page.text


def test_transcribe_template_editor_save_returns_to_transcribe(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Transcribe Template Return")
    member = make_user(email="member-transcribe-template-return@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Current session",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-transcribe-template-return@example.com", "password": "password-3"}, follow_redirects=False)
    saved = client.post(
        "/home/personal-templates",
        data={
            "template_id": "",
            "return_view": "transcribe",
            "return_tab": "templates",
            "queued_transcript_id": str(transcript.id),
            "transcribe_tab": "output",
            "home_modal": "personal-template",
            "name": "Return note",
            "description": "",
            "prompt_text": "Write a note",
            "mode": "freeform",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == f"/workspace?transcript_id={transcript.id}&tab=output"


def test_transcribe_quick_action_editor_save_returns_to_transcribe(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Transcribe Quick Action Return")
    member = make_user(email="member-transcribe-quick-return@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Current session",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-transcribe-quick-return@example.com", "password": "password-3"}, follow_redirects=False)
    saved = client.post(
        "/home/personal-quick-actions",
        data={
            "quick_action_id": "",
            "return_view": "transcribe",
            "return_tab": "quick-actions",
            "queued_transcript_id": str(transcript.id),
            "transcribe_tab": "followups",
            "home_modal": "personal-quick-action",
            "name": "Return quick action",
            "description": "",
            "prompt_text": "Draft the follow-up",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == f"/workspace?transcript_id={transcript.id}&tab=followups"



def test_shared_csrf_fetch_limits_header_to_same_origin_api():
    source = Path("app/static/js/csrf.js").read_text()

    assert "input instanceof Request ? input.method" in source
    assert "input instanceof Request ? input.headers" in source
    assert "url.origin === window.location.origin" in source
    assert "url.pathname.startsWith('/api/v1/')" in source


def test_user_transcribe_page_uses_workspace_template(client, make_team, make_user):
    team = make_team(name="Clinic GLM UI")
    make_user(email="member-glm@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-glm@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert "OpenScribe" in page.text
    assert 'action="/transcribe/sessions/delete"' in page.text


def test_user_transcribe_page_renders_workspace_values(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic GLM Dynamic")
    member = make_user(email="member-glm-dynamic@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Dynamic hypertension review",
        current_draft_text_encrypted="Patient reports improved headaches.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(version)
    db_session.flush()
    document = GeneratedDocument(
        owner_user_id=member.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        document_mode=TemplateMode.freeform,
        title="Hypertension follow-up",
        source_template_name="GP Note",
        original_output_text_encrypted="Continue Amlodipine and review in four weeks.",
        edited_output_text_encrypted="Continue Amlodipine and review in four weeks.",
        status=GeneratedDocumentStatus.ready,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-dynamic@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Dynamic hypertension review" in page.text
    assert "Patient reports improved headaches." in page.text
    assert "Continue Amlodipine and review in four weeks." in page.text


def test_user_transcribe_page_shows_owner_pii_sidebar(
    client,
    db_session,
    make_team,
    make_user,
    make_redaction_run,
):
    team = make_team(name="Clinic PII Sidebar")
    member = make_user(email="member-pii-sidebar@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="PII sidebar review",
        current_draft_text_encrypted="John Smith called from 07123 456789.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(version)
    db_session.commit()
    make_redaction_run(
        transcript=transcript,
        transcript_version=version,
        owner=member,
        entities=[
            (1, "PERSON", "John Smith"),
            (2, "PHONE_NUMBER", "07123 456789"),
        ],
    )

    client.post("/login", data={"email": "member-pii-sidebar@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'class="pii-sidebar" data-pii-sidebar' in page.text
    assert 'data-pii-count>2</span>' in page.text
    assert "John Smith" in page.text
    assert "07123 456789" in page.text
    assert "PHONE NUMBER" in page.text
    assert '<th scope="col">Source</th>' not in page.text
    assert '<th scope="col">Reveal</th>' not in page.text
    assert 'data-toggle-pii-visibility' in page.text
    assert 'data-pii-reveal="true"' not in page.text


def test_user_transcribe_page_shows_clinical_entities_in_pii_area(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Clinical Sidebar")
    member = make_user(email="member-clinical-sidebar@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Clinical sidebar review",
        current_draft_text_encrypted="Patient reports asthma and dizziness.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(version)
    db_session.flush()
    clinical_run = ClinicalEntityRun(
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=member.id,
        team_id=team.id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=True,
        api_provider="Clinical NLP",
        entity_count=2,
    )
    db_session.add(clinical_run)
    db_session.flush()
    db_session.add_all(
        [
            ClinicalEntity(
                clinical_entity_run_id=clinical_run.id,
                entity_order=1,
                entity_type="DISEASE",
                value_encrypted="asthma",
                normalized_value_hash="hash-1",
                occurrence_count=1,
            ),
            ClinicalEntity(
                clinical_entity_run_id=clinical_run.id,
                entity_order=2,
                entity_type="SYMPTOM",
                value_encrypted="dizziness",
                normalized_value_hash="hash-2",
                occurrence_count=1,
            ),
        ]
    )
    db_session.commit()

    client.post("/login", data={"email": "member-clinical-sidebar@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'data-pii-count>2</span>' in page.text
    assert "asthma" in page.text
    assert "dizziness" in page.text
    assert "Clinical NLP" in page.text
    assert "pii-type--clinical" in page.text
    assert 'data-toggle-pii-visibility' in page.text


def test_transcribe_workspace_refresh_renders_updated_pii_entities():
    app_js = Path("app/static/js/transcribe/app.js").read_text()
    transcript_branch = app_js.split("if (transcript) {", 1)[1].split("} else {", 1)[0]

    assert "workspaceTranscriptPiiEntities = uniquePiiEntities(workspace.active_transcript_pii_entities || []);" in app_js
    assert "renderDraft(draftText, { force: activeTranscriptChanged });" in transcript_branch
    assert "if (piiRegionChanged) {\n            renderPiiEntities(workspaceTranscriptPiiEntities, { updateTranscriptHighlights: false });" in transcript_branch


def test_transcribe_copy_review_uses_real_panels_without_sentinel_elements():
    structured_js = Path("app/static/js/transcribe/structured.js").read_text()

    assert "data-structured-copy-review-sentinel" not in structured_js
    assert "data-freeform-copy-review-sentinel" not in structured_js
    assert "const sentinel = document.createElement('div');" not in structured_js
    assert "...document.querySelectorAll('[data-generated-structured-section]')" in structured_js
    assert "...document.querySelectorAll('[data-generated-freeform-panel]:not([hidden])')" in structured_js
    assert "target.hasAttribute('data-generated-freeform-panel')" in structured_js
    assert "structuredSectionReviewFingerprints = new Map();" in structured_js
    assert "freeformReviewFingerprint = null;" in structured_js
    assert "Generated note changed. Scroll to the bottom before copying." in structured_js
    assert "document.addEventListener('scroll', copyReviewViewportListener, true);" in structured_js
    assert "}, { threshold: 0 });" in structured_js


def test_transcribe_keyboard_reorder_skips_copy_review_sentinels():
    reorder_js = Path("app/static/js/transcribe/reorder.js").read_text()

    assert "function adjacentElementMatching" in reorder_js
    assert "adjacentElementMatching(row, 'previous', STRUCTURED_ROW_SELECTOR)" in reorder_js
    assert "adjacentElementMatching(row, 'next', STRUCTURED_ROW_SELECTOR)" in reorder_js
    assert "const previous = row.previousElementSibling;" not in reorder_js
    assert "const next = row.nextElementSibling;" not in reorder_js


def test_transcribe_reorder_blocks_blank_note_lines():
    root = Path(__file__).resolve().parents[1]
    reorder_js = (root / "app" / "static" / "js" / "transcribe" / "reorder.js").read_text(encoding="utf-8")
    structured_js = (root / "app" / "static" / "js" / "transcribe" / "structured.js").read_text(encoding="utf-8")
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    shell_extras = (root / "app" / "templates" / "transcribe" / "_shell_extras.html").read_text(encoding="utf-8")
    transcribe_css = (root / "app" / "static" / "css" / "transcribe.css").read_text(encoding="utf-8")

    assert "function rowHasMovableContent" in reorder_js
    assert "if (!rowHasMovableContent(row)) return null;" in reorder_js
    assert "if (!rowHasMovableContent(row)) return false;" in reorder_js
    assert "event.preventDefault();\n    if (!rowHasMovableContent(row)) return;" in reorder_js
    assert "event.item.dataset.reorderBlocked = blocked ? 'blank' : '';" in reorder_js
    assert "event.from.insertBefore(event.item, nextSibling instanceof HTMLElement && event.from.contains(nextSibling) ? nextSibling : null);" in reorder_js
    assert "dragHandle.disabled = isBlank;" in structured_js
    assert "row.classList.toggle('is-blank-line', isBlank);" in structured_js
    assert "Add text before reordering line" in structured_js
    assert "reorder.js?v=20260501-blank-line-reorder-guard" in app_js
    assert "/static/js/transcribe/app.js?v=20260720-concurrent-followup" in shell_extras
    assert '"activeWorkingNote": active_working_note' in shell_extras
    assert ".statement-row.is-blank-line .statement-drag-handle" in transcribe_css


def test_user_transcribe_page_exposes_workspace_hooks_and_pane_controls(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic GLM Hooks")
    member = make_user(email="member-glm-hooks@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Workspace hooks review",
        current_draft_text_encrypted="Patient reports stable BP readings at home.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-hooks@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'data-workspace-endpoint="' in page.text
    assert 'data-split-workspace' in page.text
    assert 'data-tab-panel="output"' in page.text
    assert 'data-tab-panel="followups"' in page.text
    assert 'data-tab-trigger="output"' in page.text
    assert 'data-tab-trigger="followups"' in page.text
    assert 'data-tab-trigger="history"' in page.text
    assert 'data-copy-structured-lines' in page.text
    assert 'data-structured-copy-status' in page.text
    assert 'data-followup-history' in page.text
    assert 'data-generate-output-form' in page.text
    assert 'data-quick-action-context-input' in page.text
    assert 'data-quick-action-context-record' in page.text
    assert 'data-quick-action-context-record-label' in page.text
    assert 'data-quick-action-context-stop' not in page.text
    assert 'Create' in page.text
    assert 'Saved instructions' not in page.text
    assert 'data-selected-template-mode' not in page.text


def test_user_transcribe_page_uses_structured_template_sections(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic GLM Structured")
    admin = make_user(email="glm-structured-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="glm-structured-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="glm-structured-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="Structured note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
                {"section_key": "history", "section_label": "History", "instruction": "Summarise the history.", "section_order": 2},
            ],
        },
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="GLM structured review",
        current_draft_text_encrypted="Patient is improving after antibiotics.",
        structured_context_json={"profile": "emis", "sections": {"problem": ["Acute sinusitis"], "tasks": ["Safety net advice"]}},
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "glm-structured-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'data-section-key="problem"' in page.text
    assert 'data-section-key="history"' in page.text
    assert 'data-section-key="family_history"' not in page.text


def test_user_transcribe_page_prioritises_latest_note_and_emis_driven_generation(client, db_session, make_team, make_user):
    team = make_team(name="Clinic GLM Note Priority")
    member = make_user(email="member-glm-note-priority@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="EMIS working draft",
        current_draft_text_encrypted=None,
        structured_context_json={"profile": "emis", "sections": {"problem": ["Psoriasis flare"]}},
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-note-priority@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Clinical Note" in page.text
    assert "Create" in page.text
    assert "Psoriasis flare" in page.text
    assert 'data-generate-output-form' in page.text
    assert 'data-structured-context-hidden' not in page.text
    assert 'name="context_social_history"' not in page.text
    assert 'name="context_examination"' not in page.text
    assert 'name="context_comment"' not in page.text
    assert 'name="context_tasks"' not in page.text
    assert 'name="context_investigations"' not in page.text


def test_user_transcribe_page_shows_stt_config_label(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinic GLM STT Label")
    admin = make_user(email="glm-stt-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="glm-stt-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(
        team=team,
        actor=admin,
        label="Parakeet Local",
        model_name=None,
    )
    make_stt_selection(config=config, actor=member)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="GLM STT label review",
        current_draft_text_encrypted="Transcript text.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "glm-stt-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Speech service:" in page.text
    assert "Parakeet Local" in page.text


def test_user_transcribe_page_shows_idle_status_with_team_stt_selected(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinic GLM STT Health")
    admin = make_user(email="glm-health-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="glm-health-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Parakeet Local", base_url="http://127.0.0.1:8000")
    make_stt_selection(config=config, actor=member)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Fresh session",
        current_draft_text_encrypted=None,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "glm-health-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "data-active-status" in page.text
    assert ">idle<" in page.text
    assert 'data-record-toggle' in page.text
    assert 'disabled title="Could not reach the STT provider health endpoint"' not in page.text


def test_user_transcribe_page_shows_resolved_user_llm_model(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(
        team=team,
        actor=admin,
        adapter_kind=LlmAdapterKind.ollama_chat,
        base_url="http://localhost:11434",
        model_name="embeddinggemma:latest",
        available_models_json=["embeddinggemma:latest", "blaifa/InternVL3_5:8b"],
        has_secret=False,
    )
    make_llm_selection(
        config=config,
        actor=leader,
        allowed_models_json=["embeddinggemma:latest", "blaifa/InternVL3_5:8b"],
        model_name_override="embeddinggemma:latest",
    )
    db_session.add(UserLlmPreference(user_id=member.id, preferred_model_name="blaifa/InternVL3_5:8b"))
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert "blaifa/InternVL3_5:8b" in page.text
    assert "embeddinggemma:latest" not in page.text


def test_user_transcribe_page_can_create_and_rename_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    created = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert created.headers["location"].startswith("/workspace?transcript_id=")
    transcript = db_session.scalar(select(Transcript).where(Transcript.title == "Untitled session"))
    assert transcript is not None
    assert transcript.ingestion_mode is TranscriptIngestionMode.whole_file

    renamed = client.post(
        f"/transcribe/sessions/{transcript.id}/title",
        data={"title": "Renamed review"},
        follow_redirects=False,
    )
    assert renamed.status_code == 303
    assert renamed.headers["location"] == f"/workspace?transcript_id={transcript.id}"
    db_session.refresh(transcript)
    assert transcript.title == "Renamed review"


def test_user_transcribe_page_can_create_live_chunked_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    created = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "live_chunked"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    transcript = db_session.scalar(select(Transcript).where(Transcript.title == "Untitled session"))
    assert transcript is not None
    assert transcript.ingestion_mode is TranscriptIngestionMode.live_chunked


def test_user_transcribe_page_renders_live_session_controls(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Live Render")
    member = make_user(email="member-live-render@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Live session",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-live-render@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Start live" in page.text
    assert 'data-recording-mode-select' in page.text
    assert "Live capture is not ready for your team yet." in page.text
    assert '<div class="sr-only" data-mic-status aria-live="polite">' in page.text
    assert 'data-active-status-pill' in page.text
    assert 'data-record-toggle' in page.text
    assert 'data-workspace-stream-endpoint="' in page.text
    assert 'src="/static/vendor/vad-web/0.0.29/bundle.min.js"' in page.text


def test_user_transcribe_page_truncates_document_switcher_labels(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Truncation")
    member = make_user(email="member-switch-truncate@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Patient is improving.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=member.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            generator_type=GeneratedDocumentGeneratorType.followup,
            source_template_name="Follow-up",
            follow_up_prompt_text="Please arrange a review appointment with the duty clinician tomorrow morning",
            status=GeneratedDocumentStatus.ready,
            title="Follow-up v1",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="Latest follow-up",
            edited_output_text_encrypted="Latest follow-up",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "member-switch-truncate@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=followups")

    assert page.status_code == 200
    assert "Please arrange a review appointment with the duty" in page.text
    assert "Latest follow-up" in page.text


def test_user_transcribe_page_shows_live_chunk_failure_message(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Live Failure")
    member = make_user(email="member-live-failure@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Live session",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="chunk-1.wav",
            status=TranscriptIngestionJobStatus.failed,
            error_code="stt_unavailable",
            error_message="Could not reach the STT provider",
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "member-live-failure@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Could not reach the STT provider" in page.text
    assert 'data-mic-status' in page.text


def test_user_transcribe_page_can_switch_blank_live_session_to_whole_file(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Live session",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    switched = client.post(
        f"/transcribe/sessions/{transcript.id}/mode",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=False,
    )

    assert switched.status_code == 303
    assert switched.headers["location"] == f"/workspace?transcript_id={transcript.id}"
    db_session.refresh(transcript)
    assert transcript.ingestion_mode is TranscriptIngestionMode.whole_file


def test_user_transcribe_page_can_switch_ready_session_with_content_between_modes(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Switch Content")
    member = make_user(email="member-switch-content@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Ready session",
        current_draft_text_encrypted="Existing transcript text.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-switch-content@example.com", "password": "password-3"}, follow_redirects=False)
    switched = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={"ingestion_mode": "live_chunked"},
    )

    assert switched.status_code == 200
    db_session.refresh(transcript)
    assert transcript.ingestion_mode is TranscriptIngestionMode.live_chunked


def test_user_transcribe_page_cannot_switch_mode_while_actively_recording(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Switch Recording")
    member = make_user(email="member-switch-recording@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Recording session",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-switch-recording@example.com", "password": "password-3"}, follow_redirects=False)
    blocked = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={"ingestion_mode": "whole_file"},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["message"] == "Stop the active recording before switching input mode"
    db_session.refresh(transcript)
    assert transcript.ingestion_mode is TranscriptIngestionMode.live_chunked


def test_user_transcribe_page_cannot_switch_mode_while_ingestion_job_is_still_running(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Switch Pending Ingestion")
    member = make_user(email="member-switch-pending@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Pending ingestion",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.transcribing,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="queued.wav",
            status=TranscriptIngestionJobStatus.queued,
            source_audio_size_bytes=len(b"raw-file-audio"),
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "member-switch-pending@example.com", "password": "password-3"}, follow_redirects=False)
    blocked = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={"ingestion_mode": "live_chunked"},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["message"] == "Wait for the current session transcription to finish before switching input mode"
    db_session.refresh(transcript)
    assert transcript.ingestion_mode is TranscriptIngestionMode.whole_file


def test_user_transcribe_page_shows_progress_for_transcribing_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    queued = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Queued batch",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.transcribing,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(queued)
    db_session.flush()
    db_session.add(
        make_ingestion_job_for_transcript(
            queued,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="queued.wav",
            status=TranscriptIngestionJobStatus.queued,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={queued.id}")

    assert page.status_code == 200
    assert "Turning the recording into text." in page.text
    assert 'data-transcription-loading' in page.text
    assert "Transcribing your conversation" in page.text
    assert 'data-active-draft hidden' in page.text


def test_user_transcribe_page_shows_specific_ingestion_failure_message(client, db_session, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic Failure Detail")
    member = make_user(email="member-failure@example.com", password="password-3", team=team, team_role=TeamRole.user)
    leader = make_user(email="leader-failure@example.com", password="password-5", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-failure@example.com", password="password-4", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_stt_selection(config=config, actor=leader)
    failed = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Failed session",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(failed)
    db_session.commit()
    job = make_ingestion_job_for_transcript(
        failed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_vault_ref="secret:openscribe/transcript-ingestion/admin-detail/source-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_config_secret_missing",
        error_message="The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
    )
    db_session.add(job)
    db_session.commit()

    client.post("/login", data={"email": "member-failure@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={failed.id}")

    assert page.status_code == 200
    assert "The selected STT configuration is missing its saved credential." in page.text
    assert "Ask a system admin to re-save the STT endpoint" in page.text
    assert "Try transcription again" in page.text


def test_user_transcribe_page_hides_retry_when_failed_upload_blob_is_missing(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Failure No Retry")
    member = make_user(email="member-no-retry@example.com", password="password-3", team=team, team_role=TeamRole.user)
    failed = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Failed session",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(failed)
    db_session.commit()
    job = make_ingestion_job_for_transcript(
        failed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_blob=None,
        source_audio_size_bytes=len(b"raw-file-audio"),
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
    )
    db_session.add(job)
    db_session.commit()

    client.post("/login", data={"email": "member-no-retry@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={failed.id}")

    assert page.status_code == 200
    assert "STT provider request failed" in page.text
    assert 'data-retry-ingestion-form hidden' in page.text


def test_user_can_retry_failed_file_transcription_from_browser(client, db_session, monkeypatch, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic Retry Detail")
    member = make_user(email="member-retry@example.com", password="password-3", team=team, team_role=TeamRole.user)
    leader = make_user(email="leader-retry@example.com", password="password-5", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-retry@example.com", password="password-4", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_stt_selection(config=config, actor=leader)
    failed = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Retry session",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(failed)
    db_session.commit()
    failed_job = make_ingestion_job_for_transcript(
        failed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_vault_ref="secret:openscribe/transcript-ingestion/admin-retry/source-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
    )
    db_session.add(failed_job)
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts.read_transcript_ingestion_source_audio", lambda **kwargs: b"raw-file-audio")
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", lambda **kwargs: (_ for _ in ()).throw(AssertionError("direct publish forbidden")))

    client.post("/login", data={"email": "member-retry@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={failed.id}")

    assert page.status_code == 200
    assert "Try transcription again" in page.text
    assert 'data-retry-ingestion-form hidden' not in page.text
    assert 'data-retry-ingestion-trigger' in page.text
    assert 'data-retry-ingestion-trigger\n                data-local-busy-protected\n                hidden disabled' not in page.text

    retried = client.post(
        "/transcribe/retry-file-ingestion",
        data={"transcript_id": str(failed.id)},
        follow_redirects=False,
    )

    assert retried.status_code == 303
    assert f"queued_transcript_id={failed.id}" in retried.headers["location"]
    refreshed_failed_job = db_session.get(TranscriptIngestionJob, failed_job.id)
    assert refreshed_failed_job is not None
    assert refreshed_failed_job.source_audio_blob is None
    assert refreshed_failed_job.source_audio_vault_ref is None
    assert refreshed_failed_job.source_audio_size_bytes is None
    queued_jobs = db_session.scalars(
        select(TranscriptIngestionJob)
        .where(TranscriptIngestionJob.transcript_id == failed.id)
        .order_by(TranscriptIngestionJob.created_at.desc(), TranscriptIngestionJob.id.desc())
    ).all()
    assert len(queued_jobs) >= 2
    latest_job = queued_jobs[0]
    assert latest_job.status is TranscriptIngestionJobStatus.queued
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == latest_job.id))
    assert dispatch is not None and dispatch.state is TaskDispatchState.pending
    assert latest_job.celery_task_id == str(dispatch.task_id)
    assert latest_job.source_audio_blob is None
    assert latest_job.source_audio_vault_ref is not None
    assert latest_job.source_audio_size_bytes == len(b"raw-file-audio")


def test_user_transcribe_page_blocks_new_blank_session_when_latest_is_still_empty(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    created = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    transcript = db_session.scalar(select(Transcript))
    assert transcript is not None

    page = client.get(f"/transcribe?transcript_id={transcript.id}")
    assert page.status_code == 200
    assert "Finish or delete the current empty session before creating a new one" not in page.text
    assert "data-new-session-block-message" not in page.text

    blocked = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=True,
    )
    assert blocked.status_code == 409
    assert "Finish or delete the current empty session before creating a new one" in blocked.text
    assert db_session.scalar(select(func.count(Transcript.id))) == 1


def test_user_transcribe_page_allows_new_session_when_latest_has_transcript_text(client, db_session, make_team, make_user):
    team = make_team(name="Clinic GLM Session Gate")
    member = make_user(email="member-glm-session@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-session@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'data-new-session-button' in page.text
    assert 'data-new-session-button' in page.text and 'disabled title="Finish or delete the current empty session before creating a new one"' not in page.text
    assert "Finish or delete the current empty session before creating a new one" not in page.text


def test_user_transcribe_page_syncs_generation_controls_after_workspace_refresh(client, db_session, make_team, make_user):
    team = make_team(name="Clinic GLM Refresh Controls")
    member = make_user(email="member-glm-refresh@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Pending transcript",
        current_draft_text_encrypted=None,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.transcribing,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-refresh@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'id="transcribe-bootstrap"' in page.text
    assert "js/transcribe/app.js" in page.text
    assert 'data-generate-output-form' in page.text


def test_user_transcribe_page_can_bulk_delete_selected_sessions(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    keep = Transcript(owner_user_id=member.id, team_id=team.id, title="Keep", ingestion_mode=TranscriptIngestionMode.whole_file, retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30))
    delete_one = Transcript(owner_user_id=member.id, team_id=team.id, title="Delete one", ingestion_mode=TranscriptIngestionMode.whole_file, retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30))
    delete_two = Transcript(owner_user_id=member.id, team_id=team.id, title="Delete two", ingestion_mode=TranscriptIngestionMode.live_chunked, retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30))
    db_session.add_all([keep, delete_one, delete_two])
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    deleted = client.post(
        "/transcribe/sessions/delete",
        data={"transcript_ids": [str(delete_one.id), str(delete_two.id)]},
        follow_redirects=False,
    )

    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/workspace"
    assert db_session.get(Transcript, keep.id) is not None
    assert db_session.get(Transcript, delete_one.id) is None
    assert db_session.get(Transcript, delete_two.id) is None


def test_user_transcribe_page_marks_non_empty_sessions_for_delete_confirmation(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Delete Confirmation")
    member = make_user(email="member-delete-confirm@example.com", password="password-3", team=team, team_role=TeamRole.user)
    empty = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Empty session",
        current_draft_text_encrypted="   ",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    draft = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Draft session",
        current_draft_text_encrypted="Private transcript detail",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    version_only = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Saved session",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    blank_version_only = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Blank saved session",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add_all([empty, draft, version_only, blank_version_only])
    db_session.commit()
    db_session.add_all([
        TranscriptVersion(
            transcript_id=version_only.id,
            version_no=1,
            text_encrypted="Saved transcript text",
        ),
        TranscriptVersion(
            transcript_id=blank_version_only.id,
            version_no=1,
            text_encrypted="   ",
        ),
    ])
    db_session.commit()

    client.post(
        "/login",
        data={"email": "member-delete-confirm@example.com", "password": "password-3"},
        follow_redirects=False,
    )
    page = client.get(f"/transcribe?transcript_id={empty.id}")

    assert page.status_code == 200
    assert page.text.count('data-has-transcript-content="true"') == 2
    assert page.text.count('data-has-transcript-content="false"') == 2
    assert "Private transcript detail" not in page.text
    assert "Saved transcript text" not in page.text


def test_user_transcribe_upload_targets_active_session_when_selected(client, db_session, monkeypatch, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    make_stt_selection(config=config, actor=leader)

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", lambda **kwargs: (_ for _ in ()).throw(AssertionError("direct publish forbidden")))
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    created = client.post(
        "/transcribe/sessions",
        data={"title": "Existing session", "ingestion_mode": "whole_file"},
        follow_redirects=False,
    )
    transcript_id = created.headers["location"].split("transcript_id=", 1)[1]

    uploaded = client.post(
        "/transcribe/upload",
        data={"transcript_id": transcript_id, "title": "Existing session"},
        files={"audio": ("visit.wav", b"fake-audio", "audio/wav")},
        follow_redirects=False,
    )

    assert uploaded.status_code == 303
    assert uploaded.headers["location"].endswith(f"queued_transcript_id={transcript_id}")
    transcripts = db_session.scalars(select(Transcript).order_by(Transcript.created_at.asc())).all()
    assert len(transcripts) == 1
    assert str(transcripts[0].id) == transcript_id
    job = db_session.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.transcript_id == transcripts[0].id))
    assert job is not None
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == job.id))
    assert dispatch is not None and dispatch.state is TaskDispatchState.pending
    assert job.celery_task_id == str(dispatch.task_id)


def test_user_transcribe_page_can_generate_note_output_from_template(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=member, actor=member, name="My note", prompt_text="Write a concise note.")
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    class FakeTaskResult:
        id = "generated-task-ui"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setenv("AUDIT_TRUST_CLOUDFLARE", "true")

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    generated = client.post(
        "/transcribe/generate-output",
        data={"transcript_id": str(transcript.id), "template_id": str(template.id)},
        headers={"CF-Connecting-IP": "203.0.113.51", "User-Agent": "pytest-generation-web"},
        follow_redirects=False,
    )

    assert generated.status_code == 303
    assert f"transcript_id={transcript.id}" in generated.headers["location"]
    assert "tab=output" in generated.headers["location"]
    audit_event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "generation_queued"))
    assert audit_event is not None
    assert audit_event.request_ip == "203.0.113.51"
    assert audit_event.user_agent == "pytest-generation-web"
    assert audit_event.details_json["method"] == "POST"
    assert audit_event.details_json["route"] == "/transcribe/generate-output"

    page = client.get(generated.headers["location"])
    assert page.status_code == 200
    assert "Queued note generation." in page.text


def test_user_transcribe_page_explains_quota_exhaustion(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Quota message clinic")
    admin = make_user(email="quota-message-admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="quota-message-member@example.com", password="password-2", team=team)
    member.daily_token_limit = 0
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="Quota message note",
        prompt_text="Write a concise note.",
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Quota message session",
        current_draft_text_encrypted="Synthetic consultation source.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add_all([member, transcript])
    db_session.commit()

    client.post(
        "/login",
        data={"email": "quota-message-member@example.com", "password": "password-2"},
        follow_redirects=False,
    )
    response = client.post(
        "/transcribe/generate-output",
        data={"transcript_id": str(transcript.id), "template_id": str(template.id)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Your usage quota has been used up. Contact your administrator for help." in response.text
    assert "effective_limit" not in response.text
    assert db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)) is None


def test_user_transcribe_page_shows_structured_emis_context_inputs(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Structured Context")
    admin = make_user(email="structured-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="structured-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="structured-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="EMIS note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
            ],
        },
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "structured-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'data-template-mode="structured"' in page.text
    assert 'data-generated-structured-section' in page.text
    assert 'data-section-key="problem"' in page.text
    assert 'data-generated-structured-section data-section-key="history"' not in page.text
    assert 'data-template-sections=' in page.text
    assert 'data-copy-structured-lines' in page.text
    assert 'data-copy-structured-section' in page.text
    assert 'aria-label="Copy Problem section"' in page.text


def test_user_transcribe_page_marks_structured_template_options_for_blank_note_editor(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Structured Blank Note")
    admin = make_user(email="structured-blank-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="structured-blank-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="structured-blank-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="EMIS note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
            ],
        },
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Blank session",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "structured-blank-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'data-template-mode="structured"' in page.text
    assert 'data-template-select' in page.text
    assert 'data-generated-structured-section' in page.text
    assert "Problem" in page.text
    assert 'data-structured-line-input' in page.text
    assert 'data-generated-freeform-panel' in page.text
    assert 'data-structured-note-empty-state' not in page.text
    assert 'No note lines yet' not in page.text
    assert 'Select a template and start recording. Add note lines here as the consultation unfolds.' not in page.text


def test_user_transcribe_page_enables_followups_from_structured_note_content(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_quick_action,
):
    team = make_team(name="Clinic Followups Structured Input")
    admin = make_user(email="followups-structured-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="followups-structured-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="followups-structured-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="Structured note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
            ],
        },
    )
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Patient SMS")
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Structured note only",
        current_draft_text_encrypted="",
        structured_context_json={"profile": "emis", "sections": {"problem": ["Known asthma"]}},
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "followups-structured-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=followups")

    assert page.status_code == 200
    assert 'data-run-quick-action-trigger' in page.text
    assert 'data-quick-action-select' in page.text
    assert 'data-quick-action-context-input' in page.text
    assert 'data-quick-action-context-record' in page.text
    assert 'data-quick-action-context-record-label' in page.text
    assert 'data-quick-action-context-stop' not in page.text
    assert 'data-followup-prompt-input' in page.text
    assert 'data-run-quick-action-trigger\ndisabled' not in page.text
    assert 'data-quick-action-select\nclass="sr-only"\n>' in page.text
    assert 'data-quick-action-card-list' in page.text


def test_user_transcribe_page_enables_followups_from_freeform_note_content(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_quick_action,
):
    team = make_team(name="Clinic Followups Freeform Input")
    admin = make_user(email="followups-freeform-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="followups-freeform-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="followups-freeform-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="Freeform note",
        prompt_text="Use British English.",
        mode=TemplateMode.freeform,
    )
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Patient SMS")
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Freeform note only",
        current_draft_text_encrypted=None,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="",
    )
    db_session.add(transcript_version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=member.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            generator_type=GeneratedDocumentGeneratorType.template,
            template_version_id=template.versions[-1].id,
            source_template_name=template.name,
            status=GeneratedDocumentStatus.ready,
            title="Freeform summary",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="Patient improving\nReview in one week",
            edited_output_text_encrypted="Patient improving\nReview in one week",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "followups-freeform-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=followups")

    assert page.status_code == 200
    assert 'data-run-quick-action-trigger' in page.text
    assert 'data-quick-action-context-input' in page.text
    assert 'data-run-quick-action-trigger\ndisabled' not in page.text
    assert re.search(
        r"<textarea\b(?=[^>]*data-quick-action-context-input)(?=[^>]*data-followup-prompt-input)[^>]*></textarea>",
        page.text,
    )


def test_transcribe_frontend_uses_global_template_selector_for_generation_controls():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    actions_js = (root / "app" / "static" / "js" / "transcribe" / "actions.js").read_text(encoding="utf-8")
    documents_js = (root / "app" / "static" / "js" / "transcribe" / "documents.js").read_text(encoding="utf-8")
    structured_js = (root / "app" / "static" / "js" / "transcribe" / "structured.js").read_text(encoding="utf-8")
    media_js = (root / "app" / "static" / "js" / "transcribe" / "media.js").read_text(encoding="utf-8")
    workspace_html = (root / "app" / "templates" / "transcribe" / "_workspace.html").read_text(encoding="utf-8")
    sidebar_html = (root / "app" / "templates" / "transcribe" / "_sidebar.html").read_text(encoding="utf-8")
    session_panel_html = (root / "app" / "templates" / "transcribe" / "_session_panel.html").read_text(encoding="utf-8")
    transcribe_css = (root / "app" / "static" / "css" / "transcribe.css").read_text(encoding="utf-8")
    shell_extras = (root / "app" / "templates" / "transcribe" / "_shell_extras.html").read_text(encoding="utf-8")

    assert "const generateOutputTemplateSelect = document.querySelector('[data-template-select]');" in app_js
    assert "const templateId = dom.generateOutputTemplateSelect?.value || dom.generateOutputForm.querySelector('[data-generate-template-id]')?.value || '';" in actions_js
    assert "shouldPreserveLiveMicStatus()" in app_js
    assert "captureController?.syncDisplayedDuration?.();" in app_js
    assert "Listening for speech..." in media_js
    assert "const micVisualizer = document.querySelector('[data-mic-visualizer]');" in app_js
    assert "const silencePrompt = document.querySelector('[data-vad-silence-prompt]');" in app_js
    assert "const VAD_SILENCE_PROMPT_MS = 30000;" in media_js
    assert "const STALE_CONSULT_RECORDING_WARNING_MS = 30000;" in app_js
    assert "const shouldWarnBeforeRecordingCurrentConsult = () => {" in app_js
    assert "latestSuccessfulIngestionCompletedAt = transcript?.latest_successful_ingestion_completed_at || null;" in app_js
    assert "confirmBeforeStartRecording," in app_js
    assert "confirmBeforeStartRecording," in media_js
    assert "New consultation started. Recording will begin here." in app_js
    assert "const syncSidebarTranscripts = (items, options = {}) => {" in app_js
    assert "revealTranscriptId: activeTranscriptChanged ? transcriptId : null," in app_js
    assert "const loadMoreSidebarTranscripts = async () => {" in app_js
    assert "new URL('/api/v1/transcripts', window.location.origin)" in app_js
    assert "const setupSidebarInfiniteScroll = () => {" in app_js
    assert "data-session-list-sentinel" in session_panel_html
    assert "const linksById = new Map(currentSessionLinks().map((link) => [link.dataset.transcriptId, link]));" in app_js
    assert "const seenIds = new Set();" in app_js
    assert "sessionList.replaceChildren(fragment);" in app_js
    assert "sessionList.style.minHeight = `${previousListHeight}px`;" in app_js
    assert "sessionList.style.minHeight = '';" in app_js
    assert "const revealSessionRailTranscript = (transcriptIdToReveal, scrollContainer) => {" in app_js
    assert "import { keepSessionRailItemVisible, reconcileSessionRailItems, sortSessionRailItems } from './sessionRail.js?v=20260719-preserve-loaded';" in app_js
    assert "keepSessionRailItemVisible({ scrollContainer, item, behavior });" in app_js
    assert "scrollContainer?.scrollTo({ top: previousScrollTop, behavior: 'auto' });" in app_js
    assert "document.addEventListener('transcribe:session-panel-opened'" in app_js
    assert "const sessionRailRegionChanged = sessionRailRegionSignature !== lastSessionRailRegionSignature;" in app_js
    assert "if (sessionRailRegionChanged) {" in app_js
    assert "const previousStructureSignature = workspaceRegionSignature(sessionRailItems.map((item) => ({" in app_js
    assert "const structureChanged = previousStructureSignature !== nextStructureSignature;" in app_js
    assert "if (structureChanged) renderSidebarTranscripts(options);" in app_js
    assert "preserveLoaded: sessionRailPaginationStarted && recentTranscriptsTopHasMore," in app_js
    assert "workspaceItems: incoming," in app_js
    assert "sidebarTranscripts.slice(0, sessionRailPageSize).map" in app_js
    assert "node.dataset.statusTone === descriptor.tone" in app_js
    assert "if (titleNode && titleNode.textContent !== nextTitle)" in app_js
    assert "const syncSessionRailSentinel = () => {" in app_js
    assert "sessionRailLoading = true;\n        syncSessionRailSentinel();" in app_js
    assert "const piiRegionChanged = piiRegionSignature !== lastPiiRegionSignature;" in app_js
    assert "if (piiRegionChanged) {" in app_js
    assert "const dictationRegionChanged = dictationRegionSignature !== lastDictationRegionSignature;" in app_js
    assert "if (dictationRegionChanged) renderDictation(dictation);" in app_js
    assert "const noteRegionChanged = noteRegionSignature !== lastNoteRegionSignature;" in app_js
    assert "if (noteRegionChanged) {" in app_js
    assert "if (followupRegionChanged) {" in app_js
    assert "const currentSessionLinks = () => sessionList ? [...sessionList.querySelectorAll('[data-session-link]')] : [];" in app_js
    assert "const currentSelectionBoxes = () => sessionList ? [...sessionList.querySelectorAll('[data-session-select]')] : [];" in app_js
    assert "sessionList?.addEventListener('change', (event) => {" in app_js
    assert "dom.sessionList?.addEventListener('click', async (event) => {" in actions_js
    assert actions_js.count("!(await persistPendingEditorsBeforeWorkspaceSwitch())") == 2
    assert "const persistPendingEditorsBeforeWorkspaceSwitch = async () => {" in app_js
    assert "await persistNoteEditsUntilDrained({ keepalive: false });" in app_js
    assert "await persistFollowupEditsUntilDrained({ keepalive: false });" in app_js
    assert "...(dom.sessionList || window.document).querySelectorAll('[data-session-select]')," in actions_js
    assert "dom.sessionLinks.forEach" not in actions_js
    assert "dom.selectionBoxes.filter" not in actions_js
    assert 'data-session-panel-toggle' in sidebar_html
    assert 'data-session-list' in session_panel_html
    assert 'data-sidebar-empty' in session_panel_html
    assert 'data-session-panel-close' in session_panel_html
    assert "transcribe:mobile-sidebar-close" in (root / "app" / "static" / "js" / "transcribe" / "mobile.js").read_text(encoding="utf-8")
    assert ".transcribe-sidebar-collapse-toggle" in transcribe_css
    assert "[data-sidebar-collapsed-control]" in transcribe_css
    assert ".session-panel-close" in transcribe_css
    assert "event.key !== 'Escape'" in shell_extras
    assert "document.dispatchEvent(new CustomEvent('transcribe:session-panel-opened'));" in shell_extras
    assert "const armSilencePromptTimer = () => {" in media_js
    assert "markVadSpeechStarted();" in media_js
    assert "markVadSpeechEndedOrIdle();" in media_js
    assert "silencePromptDismissedForCurrentSilentInterval = true;" in media_js
    assert "resetSilencePromptState();" in media_js
    assert "batchVadInstance = await buildBatchVadInstance();" in media_js
    assert "Speech detected. Voice-only batch capture is running..." in media_js
    assert "microphone-batch.wav" in media_js
    assert 'data-mic-visualizer' in workspace_html
    assert 'data-vad-silence-prompt' in workspace_html
    assert 'Are you still there?' in workspace_html
    assert 'data-vad-silence-prompt-dismiss' in workspace_html
    assert 'data-consult-boundary-modal' in workspace_html
    assert "It's been a while." in workspace_html
    assert 'Start recording or make a new consult?' in workspace_html
    assert 'data-consult-boundary-new' in workspace_html
    assert 'latestSuccessfulIngestionCompletedAt' in shell_extras
    assert ".vad-silence-prompt" in transcribe_css
    assert ".consult-boundary-modal" in transcribe_css
    assert ".consult-boundary-modal__header p" in transcribe_css
    assert "const RECORDING_DURATION_STORAGE_KEY = 'openscribe-glm2-recording-durations';" in media_js
    assert "const beginAccumulatedTimer = () => {" in media_js
    assert "const finalizeAccumulatedTimer = () => {" in media_js
    assert "syncDisplayedDuration: renderTimer," in media_js
    assert "handlePageLifecycleExit," in media_js
    assert "void finalizeLiveCaptureIfNeeded({ keepalive: true });" in media_js
    assert "captureController?.handlePageLifecycleExit?.();" in app_js
    assert "finalizeLiveCapture: async ({ keepalive = false } = {}) => {" in app_js
    assert "Tab moved to background. Flushing live capture before browser throttling can delay it..." in media_js
    assert "readActiveDraftText" in app_js
    assert "document.querySelectorAll('[data-legacy-note-workspace] .section-block')" in structured_js
    assert "row.className = 'statement-row';" in structured_js
    assert "textarea.className = 'statement-editor';" in structured_js
    assert "card.className = 'structured-section-block';" in structured_js
    assert "copyButton.setAttribute('data-copy-structured-section', '');" in structured_js
    assert "collectStructuredSectionLines" in structured_js
    assert "return collectSelectedNoteLines({ mode: 'structured' })" in structured_js
    assert ".filter((line) => line.sectionKey === sectionKey)" in structured_js
    assert "dom.generatedStructuredPanel.addEventListener('click'" in actions_js
    assert "const textToCopy = lines.join('\\n');" in actions_js
    assert "const textToCopy = label ? `${label}:\\n${body}` : body;" not in actions_js
    assert "navigator.clipboard.writeText(textToCopy);" in actions_js
    assert "data-structured-copy-review-sentinel" not in structured_js
    assert "data-freeform-copy-review-sentinel" not in structured_js
    assert "document.querySelectorAll('[data-generated-structured-section]')" in structured_js
    assert "document.querySelectorAll('[data-generated-freeform-panel]:not([hidden])')" in structured_js
    assert "noteCopyReviewBlocker" in structured_js
    assert "const activeRenderedGeneratedDocumentId = () => (" in structured_js
    assert "generatedStructuredDraft?.documentId" in structured_js
    assert "element.closest('[hidden]') || element.getClientRects().length === 0" in structured_js
    assert "entry.isIntersecting && visibleBottomReached(entry.target)" in structured_js
    assert "structuredSectionCopyReviewFingerprint" in structured_js
    assert "freeformCopyReviewFingerprint" in structured_js
    assert "scheduleCopyReviewRefresh();" in structured_js
    assert "document.addEventListener('scroll', copyReviewViewportListener, true);" in structured_js
    assert "}, { threshold: 0 });" in structured_js
    assert "window.requestAnimationFrame(() => observeCopyReviewTargets())" in structured_js
    assert "copyReviewObservationReady = false;" in structured_js
    assert "if (!copyReviewObservationReady) return;" in structured_js
    assert "const viewedThroughIndex = sections.indexOf(section);" in structured_js
    assert "button.dataset.copyReviewBlocked = blocked ? 'true' : 'false';" in structured_js
    assert "dom.copyStructuredLinesButton.dataset.copyReviewBlocked = blocked ? 'true' : 'false';" in structured_js
    assert "showFlash(copyReviewBlocker, 'error');" in actions_js
    assert "data-copy-review-status" in workspace_html
    assert "const hasGeneratedNote = Boolean(dom.latestGeneratedOutput?.dataset.latestGeneratedId);" in structured_js
    assert "section.text || section.edited_text || section.original_text || section.edited_text_encrypted" in structured_js
    assert "const templatePickerButton = document.querySelector('[data-template-picker-button]');" in app_js
    assert "const templatePickerModal = document.querySelector('[data-template-picker-modal]');" in app_js
    assert "syncWorkingNoteModeUi" not in app_js
    assert "const generatedFreeformPanel = document.querySelector('[data-generated-freeform-panel]');" in app_js
    assert "const selectStructuredSelectionButton = document.querySelector('[data-select-structured-selection]');" in app_js
    assert "const dictationCta = document.querySelector('[data-dictation-cta]');" in app_js
    assert "const dictationModal = document.querySelector('[data-dictation-modal]');" in app_js
    assert "const openDictationModal = async ({ highlightRecord = false } = {}) => {" in app_js
    assert "dictationRecordToggleLabel.textContent = isTranscribing ? 'Transcribing...' : (activeCapture ? 'Stop' : 'Record');" in app_js
    assert "dictationRecordToggleIcon.dataset.lucide = isTranscribing ? 'loader-2' : (activeCapture ? 'square' : 'mic');" in app_js
    assert "dictationPauseRecordingIcon.dataset.lucide = isPaused ? 'play' : 'pause';" in app_js
    assert "const dictationRetryTranscriptionButton = document.querySelector('[data-dictation-retry-transcription]');" in app_js
    assert "setDictationSessionProgress('Recorded audio kept locally. Retry transcription or close to discard it.');" in app_js
    assert "dictationRetryTranscriptionButton?.addEventListener('click', () => {" in app_js
    assert "Recording unavailable. Ask your team lead to enable post-consultation dictation." in app_js
    assert "dictationCta?.addEventListener('click', () => {" in app_js
    assert "dictationModalCloseButtons.forEach((button) => {" in app_js
    assert "post-consultation-dictation/preview-audio-file" in app_js
    assert "openscribe:dictation-nudge:" in app_js
    assert "dictation-record-highlight" in app_js
    assert "const activeStatusPill = document.querySelector('[data-active-status-pill]');" in app_js
    assert "const statusLabelForRecordingProgress = (message) => {" in app_js
    assert "getIsRecordingSwitchBlocked: () => Boolean(captureController?.isLiveCaptureUiActive?.()) || currentTranscriptStatus === 'recording'," in app_js
    assert "if (getIsRecordingSwitchBlocked?.()) {" in actions_js
    assert "Stop recording before switching consultations." in actions_js
    assert "Stop recording before creating a new consultation." in actions_js
    assert "let noteEditorDirty = false;" in app_js
    assert "let activeWorkingNote = bootstrap.activeWorkingNote || null;" in app_js
    assert "let dirtyNoteMode = null;" in app_js
    assert "dirtyNoteMode = currentRenderedNoteMode();" in app_js
    assert "const currentRenderedNoteMode = () => latestGeneratedOutput?.dataset?.latestGeneratedMode || selectedWorkingNoteMode();" in app_js
    assert "import { isWorkingNoteTargetId, workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';" in app_js
    assert "export const workingNoteTargetId = (transcriptId = '') => `working:${transcriptId || ''}`;" in (root / "app" / "static" / "js" / "transcribe" / "noteTargets.js").read_text(encoding="utf-8")
    assert "requestVersion === noteEditVersion" in app_js
    assert "renderWorkingNote(" not in app_js
    assert "const markNoteEditorDirty = () => {" in app_js
    assert "const shouldPreserveNoteEditorRender = (nextSelectedNoteDocumentId = currentRenderedNoteTargetId()) => {" in app_js
    assert "dirtyNoteDocumentId" not in app_js
    assert "const hasProtectedWorkingNoteEditor = () => (" not in app_js
    assert "const validNoteTargets = [...(transcriptId ? [{ id: workingNoteTargetId(transcriptId) }] : []), ...noteDocuments];" in app_js
    assert "const selectedEditorId = selectedNoteId || (state.hasActiveTranscript ? workingNoteTargetId(state.activeTranscriptId || '') : null);" in documents_js
    assert "shouldPreserveNoteEditorRender?.(selectedEditorId)" in documents_js
    assert "const workingNoteDocument = (state) => {" in documents_js
    assert "export function workingNoteToEditorDocument" in documents_js
    assert "id: workingNoteTargetId(transcriptId || '')," in documents_js
    assert "onNoteEditorChanged: markNoteEditorDirty," in app_js
    assert "const noteRenderState = renderSelectedNote();" in app_js
    assert "let preserveDirtyNoteEditor = true;" in app_js
    assert "preserveDirtyNoteEditor = Boolean(noteRenderState?.preservedEditor);" in app_js
    assert "if (!preserveDirtyNoteEditor) {" in app_js
    assert "const hasNoteInput = structuredEditor?.hasNoteInputContent?.() || false;" not in app_js
    assert "hasStructuredInput" not in app_js
    assert "hasStructuredContextContent" not in app_js
    assert "data-structured-context-hidden" not in workspace_html
    assert "syncStructuredContextHiddenInputs" not in structured_js
    assert "const hasGenerationSource = hasDraft || hasWorkingNote || hasDictation || transcriptWaitingForText;" in app_js
    assert "const canRunQuickAction = Boolean(transcriptId && hasLlmSelection && hasGenerationSource && hasSelectableOptions(runQuickActionSelect));" in app_js
    assert "const canGenerateFollowup = Boolean(transcriptId && hasLlmSelection && (hasDraft || hasWorkingNote || hasDictation || transcriptWaitingForText));" in app_js
    assert "runQuickActionTrigger.disabled = !canUsePrimaryFollowupAction;" in app_js
    assert "if (dom.generateFollowupTrigger?.disabled) {" in actions_js
    assert "showFlash('Select a quick action first.', 'warning');" in actions_js
    assert "./actions.js?v=20260719-workspace-switch-save" in app_js
    assert "const isDiscardableEmptyWorkingNoteDraft = () => (" in app_js
    assert "return { kind: 'working_note_empty_draft_discarded' };" in app_js
    assert "Empty working-note draft ignored." in app_js
    assert "const handleOutputTemplateChange = async () => {" in app_js
    assert "generateOutputTemplateSelect?.addEventListener('change', syncTemplatePickerUi);" not in app_js
    assert "generateOutputTemplateSelect.dispatchEvent(new Event('change', { bubbles: true }));\n        syncTemplatePickerUi();" not in app_js
    assert "structuredEditor.syncTemplateModeBadge?.();" in app_js
    assert "const canContinue = await handleOutputTemplateChange?.();" in actions_js
    assert "dom.generateOutputTemplateSelect.addEventListener('change', async () => {\n      structuredEditor.syncStructuredTemplateUi();" not in actions_js
    assert "const currentNoteUpdatedAt = () => latestGeneratedOutput?.dataset?.latestGeneratedUpdatedAt || '';" in app_js
    assert "import { captureNoteDirtyBaseline, noteBaselineForSave } from './noteSaveState.js?v=20260521-working-note-baseline-helpers';" in app_js
    assert "let dirtyNoteExpectedUpdatedAt = null;" in app_js
    assert "dirtyNoteExpectedUpdatedAt = captureNoteDirtyBaseline({" in app_js
    assert "const clearNoteDirtyBaseline = () => {" in app_js
    assert "const noteSaveExpectedUpdatedAtForTarget = (targetId) => noteBaselineForSave({" in app_js
    assert "noteBaselineForSave({" in app_js
    assert "const expectedUpdatedAt = noteSaveExpectedUpdatedAtForTarget(targetId);" in app_js
    assert "const expectedUpdatedAt = noteSaveExpectedUpdatedAtForTarget(generatedDocumentId);" in app_js
    assert "dirtyNoteExpectedUpdatedAt = savedDocument.updated_at || '';" in app_js
    assert "const deleteSelectedNote = async () => {" in actions_js
    assert "void deleteSelectedNote();" in actions_js
    assert "Delete this note permanently?" in actions_js
    assert "This consultation has transcript text. Delete it permanently?" in actions_js
    assert "One or more selected consultations have transcript text. Delete them permanently?" in actions_js
    assert "checkbox.dataset.hasTranscriptContent" in app_js
    assert "Could not delete the note." in actions_js
    assert "const buildNoteSaveRequest = () => {" in app_js
    assert "endpoint: `/api/v1/transcripts/${transcriptId}/working-note`" in app_js
    assert "serializeCurrentNoteEditor" in structured_js
    assert "saveWorkingNoteBeforeGeneration" in app_js
    assert "await saveWorkingNoteBeforeGeneration?.();" in actions_js
    assert "Clear the working note before generating." in app_js
    assert "const persistNoteEditsUntilDrained = async ({ keepalive = false } = {}) => {" in app_js
    assert "noteSaveQueued = true;\n            savedDocument = await noteSaveInFlight;" in app_js
    assert "const saved = await persistNoteEditsUntilDrained({ keepalive: false });" in app_js
    assert "includeUncheckedStructuredLines: false" not in app_js
    assert "let noteGenerationInFlight = null;" in app_js
    assert "let noteGenerationBusy = false;" in app_js
    assert "let noteGenerationCloseDictationAfterCurrentRequest = false;" in app_js
    assert "let noteOptionsSaveQueue = Promise.resolve();" in app_js
    assert "const noteOptionsPendingSaves = new Set();" in app_js
    assert "const runNoteOptionsSaveWithRetry = async (saveTask) => {" in app_js
    assert "return await saveTask();" in app_js
    assert "then(() => runNoteOptionsSaveWithRetry(saveTask))" in app_js
    assert "const enqueueNoteOptionsSave = (saveTask) => {" in app_js
    assert "const waitForPendingNoteOptionSaves = async () => {" in app_js
    assert "if (!(await waitForPendingNoteOptionSaves())) {" in app_js
    assert "Could not save note options. Queueing with last saved settings." in app_js
    assert app_js.index("if (!(await waitForPendingNoteOptionSaves())) {") < app_js.index(
        "const response = await csrfFetch(`/api/v1/transcripts/${generationTranscriptId}/generate-output`, {"
    )
    assert "const queued = await enqueueTemplateGeneration({ templateId });" in actions_js
    assert "if (!queued) return;" in actions_js
    assert "body: JSON.stringify({ template_id: templateId })," in app_js
    assert "body: JSON.stringify({ template_id: templateId })," not in actions_js
    assert "const generationTranscriptId = transcriptId;" in app_js
    assert "if (!generationTranscriptId || !templateId) return Promise.resolve(false);" in app_js
    assert "noteGenerationCloseDictationAfterCurrentRequest = noteGenerationCloseDictationAfterCurrentRequest || closeDictationModal;" in app_js
    assert "if (noteGenerationCloseDictationAfterCurrentRequest) {" in app_js
    assert "const response = await csrfFetch(`/api/v1/transcripts/${generationTranscriptId}/generate-output`, {" in app_js
    assert "handleTemplateGenerationQueued" not in app_js
    assert "const queued = await enqueueTemplateGeneration({ templateId, closeDictationModal: true });" in app_js
    assert "onNoteGenerationQueued" not in actions_js
    assert "silent: true" not in app_js
    assert "silent = false" not in app_js
    assert "generateOutputButton.disabled = generationBusy || !canGenerateNote;" in app_js
    assert "generateOutputTemplateSelect.disabled = generationBusy || !canChooseTemplate;" in app_js
    assert "templatePickerButton.disabled = generationBusy || !canChooseTemplate;" in app_js
    assert "button.disabled = generationBusy || !canChooseTemplate;" in app_js
    assert "runQuickActionSelect.disabled = !canRunQuickAction;" in app_js
    assert "quickActionContextInput.disabled = !canUseFollowupRequest;" in app_js
    assert "quickActionContextRecordButton.disabled = !canUseFollowupRequest;" in app_js
    assert "recordCustomPromptButton.disabled = !canUseFollowupRequest;" in app_js
    assert "quickActionQuickPicks.forEach((button) => {\n          button.disabled = !canRunQuickAction;" in app_js
    assert "quickActionCardRunButtons.forEach((button) => {\n          button.disabled = !canRunQuickAction;" in app_js
    assert "generateFollowupPromptInput.disabled = !canGenerateFollowup;" in app_js
    assert "generateFollowupTrigger.disabled = !canGenerateFollowup;" in app_js
    assert "runQuickActionTrigger.disabled = generationBusy ||" not in app_js
    assert "quickActionContextInput.disabled = generationBusy ||" not in app_js
    assert "generateFollowupTrigger.disabled = generationBusy || !canGenerateFollowup;" not in app_js
    assert "if (noteGenerationBusy || !generateOutputTemplateSelect || !templateId) return;" in app_js
    assert "syncGenerationAvailability," not in actions_js
    assert "const NOTE_GENERATION_CLICK_GUARD_MS" not in actions_js
    assert "noteGenerationGuarded" not in app_js
    assert "noteGenerationGuardUntil" not in actions_js
    assert "persistStructuredContextSilently" not in app_js
    assert "persistStructuredContextSilently" not in structured_js
    assert "scheduleStructuredContextSave" not in structured_js
    assert "lastSavedStructuredContext" not in structured_js
    assert "method: 'PATCH'" in app_js
    assert "keepalive," in app_js
    assert "void persistNoteEditsSilently();" in app_js
    assert "window.showToast?.(message, kind);" in app_js
    assert "flashWrap.hidden = true;" in app_js
    assert ".statement-input" in transcribe_css
    assert "template-picker-button" in transcribe_css
    assert "freeform-note-panel" in transcribe_css
    assert ".main-panel {" in transcribe_css
    assert "min-height: 100%;" in transcribe_css
    assert ".record-split-button {" in transcribe_css
    assert "overflow: visible;" in transcribe_css
    assert ".structured-workspace {" in transcribe_css
    assert "flex: 1;" in transcribe_css
    assert ".dictation-global-cta" in transcribe_css
    assert ".dictation-modal" in transcribe_css
    assert ".dictation-compact" in transcribe_css
    assert "@media (max-width: 1180px) {\n.transcript-review-grid {\ngrid-template-columns: minmax(0, 1fr);" in transcribe_css
    assert ".dictation-global-cta.dictation-nudge" in transcribe_css
    assert "@keyframes dictationRecordPulse" in transcribe_css
    assert "statement-content" in workspace_html
    assert '<div class="px-4 pt-3" hidden data-flash-wrap>' in workspace_html
    assert "data-generated-freeform-panel" in workspace_html
    assert "data-latest-generated-updated-at=" in workspace_html
    assert "data-active-status-pill" in workspace_html
    assert "data-clinical-nlp-status" in workspace_html
    assert "Clinical NLP has not run for this transcript yet." in workspace_html
    assert '"activeTranscriptClinicalNlpStatus": active_transcript_clinical_nlp_status' in shell_extras
    assert '<div class="sr-only" data-mic-status aria-live="polite">' in workspace_html
    assert 'data-dictation-cta' in workspace_html
    assert 'data-dictation-modal' in workspace_html
    assert 'data-dictation-compact' in workspace_html
    assert 'data-transcript-review-grid' in workspace_html
    assert 'Add dictation' in workspace_html
    assert 'Save & generate note' in workspace_html
    assert 'Upload audio' in workspace_html
    assert 'Recording unavailable. You can type dictation manually.' in workspace_html
    assert "data-new-session-block-message" not in sidebar_html
    assert "openscribe:legacy-workspace-document-selected" not in workspace_html
    assert "activateNoteTab('note')" not in workspace_html
    assert "data-generated-structured-sections" in workspace_html
    assert "data-followup-history" in workspace_html
    assert 'data-lucide="settings"' in workspace_html
    assert 'data-lucide="message-square-text"' in workspace_html
    assert 'data-lucide="sparkles"' in workspace_html
    assert '<div class="transcript-review-grid' in workspace_html
    assert '<div class="transcript-content flex-1 min-h-0 overflow-y-auto" data-active-draft' in workspace_html
    assert 'class="pii-sidebar" data-pii-sidebar' in workspace_html
    assert 'data-pii-table-wrap' in workspace_html
    assert 'data-pii-add-form' in workspace_html
    assert 'data-pii-add-value' in workspace_html
    assert "const piiCount = document.querySelector('[data-pii-count]');" in app_js
    assert "const piiVisibilityToggle = document.querySelector('[data-toggle-pii-visibility]');" in app_js
    assert "let piiMasked = false;" in app_js
    assert "const renderHighlightedTranscript = (text, entities = [], options = {}) => {" in app_js
    assert "const clinicalNlpStatus = document.querySelector('[data-clinical-nlp-status]');" in app_js
    assert "workspaceClinicalNlpStatus = workspace.active_transcript_clinical_nlp_status || { status: 'not_run', entity_count: 0, error_code: null };" in app_js
    assert "Clinical NLP complete:" in app_js
    assert "activeDraft.innerHTML = text" in app_js
    assert "const renderPiiEntities = (entities = [], options = {}) => {" in app_js
    assert "const allowReveal = options.allowReveal !== false;" in app_js
    assert "const updateTranscriptHighlights = options.updateTranscriptHighlights !== false;" in app_js
    assert "const displayRows = allowReveal" in app_js
    assert ": rows.map((entity) => ({ ...entity, value: '' }));" in app_js
    assert "currentPiiEntities = displayRows;" in app_js
    assert "renderDraft(currentDraftText || readActiveDraftText(), { force: true });" in app_js
    assert "getTranscriptText: () => currentDraftText," in app_js
    assert "getTranscriptText?.()" in actions_js
    assert "dom.activeDraft?.textContent" in actions_js
    assert '<th scope="col">Source</th>' not in app_js
    assert '<th scope="col">Reveal</th>' not in app_js
    assert 'class="pii-placeholder"' not in app_js
    assert "piiAddForm?.addEventListener('submit'" in app_js
    assert "csrfFetch(`/api/v1/transcripts/${transcriptId}/manual-pii`" in app_js
    assert "data-pii-delete" in app_js
    assert "piiVisibilityToggle?.addEventListener('click'" in app_js
    assert "${displayRows.map((entity) => `" in app_js
    assert "renderPiiEntities?.(selectedNote?.pii_entities" not in documents_js
    assert "renderPiiEntities," in app_js
    assert "dom.noteHistory?.addEventListener('click'" in actions_js
    assert "const wrapper = window.document.createElement('details');" in app_js
    assert "structuredEditor.renderStructuredSections(generatedStructuredDraft);" not in app_js
    assert "generatedDocument.status === 'ready' && generatedDocument.document_mode === 'freeform'" in structured_js
    assert "generatedDocument.status === 'ready' && generatedDocument.document_mode === 'freeform' && Boolean(generatedDocument.edited_output_text_encrypted)" not in structured_js
    assert "const autosizeStatementEditorsIn = (container) => {" in structured_js
    assert "autosizeStatementEditorsIn(dom.generatedStructuredPanel);" in structured_js
    assert "autosizeStatementEditorsIn(dom.generatedFreeformPanel);" in structured_js
    assert "...((generatedDocument ? generatedSectionMap.get(section.key) : structuredContext[section.key]) || [])" in structured_js
    assert "renderStructuredSections(null);" in structured_js
    assert "renderFreeformLines(null);" in structured_js
    assert "const activeGeneratedDocumentId = () => dom.latestGeneratedOutput?.dataset?.latestGeneratedId || '';" in structured_js
    assert "generatedStructuredDraft.templateId !==" not in structured_js
    assert "generatedStructuredDraft = buildGeneratedStructuredDraftFromDom() || generatedStructuredDraft;" in structured_js
    assert "const ensureSectionHasEditableRow = (sectionContainer) => {" in structured_js
    assert "if (rows.length === 0) {" in structured_js
    assert "ensureSectionHasEmptyRow(sectionContainer);" not in structured_js
    assert "ensureFreeformHasEmptyRow();" not in structured_js
    assert "const ensureFreeformHasEditableRow = () => {" in structured_js
    assert "onNoteEditorChanged?.();" in structured_js
    assert "const hasNoteInputContent = () => {" in structured_js
    assert "const renderSelectedNote = ({ forcePreserveEditor = false } = {}) => {" in documents_js
    assert "latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = selectedNote?.updated_at || \"\";" in documents_js
    assert "const preserveCurrentEditorRender = Boolean(" in documents_js
    assert "forcePreserveEditor || shouldPreserveNoteEditorRender?.(selectedEditorId)" in documents_js
    assert "if (!preserveCurrentEditorRender) {" in documents_js
    assert "wrapper.className = 'followup-output-card-v2 followup-llm-request-card-v2';" in documents_js
    assert "wrapper.dataset.generatedDocumentId = document.id || '';" in documents_js
    assert "wrapper.hidden = true;" in documents_js
    assert "const selectDocumentFromUi = async (kind, documentId) => {" in documents_js
    assert "const savedDocument = await persistNoteEditsSilently?.();" in documents_js
    assert "if (!savedDocument) {" in documents_js
    assert "clearNoteEditorDirty?.();" in documents_js
    assert "card.className = `followup-recent-item-v2${item.id === selectedId ? \" is-selected\" : \"\"}`;" in documents_js
    assert "followupHistory.innerHTML = '<div class=\"followup-empty-v2\">No follow-ups for this transcript yet.</div>';" in documents_js
    assert "window.refreshLucideIcons?.(root);" in app_js
    assert "const getRecordToggleIcon = () => document.querySelector('[data-record-toggle-icon]');" in app_js
    assert "const recordToggleIcon = getRecordToggleIcon();" in app_js
    assert "recordToggleIcon.dataset.lucide = isRecording" in app_js
    assert "refreshIcons?.(followupHistory);" in documents_js
    assert 'data-lucide="trash-2"' in workspace_html
    assert "No conversation text yet. Upload a recording or use the microphone to begin. The transcript will appear here as the consultation unfolds." not in app_js
    assert "transcriptEmpty.hidden = isTranscribing || hasDraft" in app_js
    assert "not active_template_generation_input_available" in workspace_html
    assert "not active_quick_action_input_available" in workspace_html


def test_transcribe_session_panel_desktop_open_state_is_persisted_without_affecting_mobile():
    root = Path(__file__).resolve().parents[1]
    shell_extras = (root / "app" / "templates" / "transcribe" / "_shell_extras.html").read_text(encoding="utf-8")

    assert "var sessionPanelStorageKey = 'openscribe:transcribe:consultations-open';" in shell_extras
    assert "return window.localStorage.getItem(sessionPanelStorageKey) === 'true';" in shell_extras
    assert "window.localStorage.setItem(sessionPanelStorageKey, open ? 'true' : 'false');" in shell_extras
    assert "if (options.persist && desktopSidebarMedia.matches)" in shell_extras
    assert "desktopSidebarMedia.matches && storedSessionPanelOpen()" in shell_extras
    assert "desktopSidebarMedia.addEventListener('change', syncSessionPanelViewport);" in shell_extras
    assert "sessionPanel.style.transition = 'none';" in shell_extras
    assert "sessionPanel.style.removeProperty('transition');" in shell_extras
    assert "sessionPanel.toggleAttribute('inert', !open);" in shell_extras
    assert "document.dispatchEvent(new CustomEvent('transcribe:session-panel-opened'));" in shell_extras


def test_transcribe_session_panel_uses_structural_lower_row_without_content_signatures():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    transcribe_html = (root / "app" / "templates" / "transcribe.html").read_text(encoding="utf-8")
    workspace_html = (root / "app" / "templates" / "transcribe" / "_workspace.html").read_text(encoding="utf-8")
    shell_extras = (root / "app" / "templates" / "transcribe" / "_shell_extras.html").read_text(encoding="utf-8")
    transcribe_css = (root / "app" / "static" / "css" / "transcribe.css").read_text(encoding="utf-8")
    document_region = app_js.split("const generatedDocumentRegionData = (document) => ({", 1)[1].split(
        "const piiEntityRegionData", 1
    )[0]

    assert '{% include "transcribe/_session_panel.html" %}' not in transcribe_html
    assert '<div class="transcribe-lower-row">\n{% include "transcribe/_session_panel.html" %}' in workspace_html
    assert workspace_html.index('data-tab-trigger="history"') < workspace_html.index('class="transcribe-lower-row"')
    assert workspace_html.count('data-workspace-context-header') == 3
    assert 'class="followup-output-header-v2 workspace-context-header" data-workspace-context-header' in workspace_html
    assert "var sessionPanelHeader = document.querySelector('[data-session-panel-header]');" in shell_extras
    assert "document.querySelector('[data-tab-panel]:not([hidden]) [data-workspace-context-header]')" in shell_extras
    assert "sessionPanel.style.setProperty('--workspace-context-header-height', headerHeight + 'px');" in shell_extras
    assert ".transcribe-lower-row {" in transcribe_css
    assert "height: var(--workspace-context-header-height, auto);" in transcribe_css
    assert "editedOutputText" not in document_region
    assert "followUpPromptText" not in document_region
    assert "llmRequestPayload" not in document_region


def test_live_chunk_upload_retries_only_structured_rate_limit_errors():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    media_js = (root / "app" / "static" / "js" / "transcribe" / "media.js").read_text(encoding="utf-8")

    assert "const parseErrorResponse = async (response, fallback) => {" in app_js
    assert "code: payload?.error?.code || null," in app_js
    assert "details: payload?.error?.details || null," in app_js
    assert "await parseErrorResponse(response, fallback)" in app_js
    assert "parseErrorResponse," in media_js
    assert "const errorResponse = await parseErrorResponse(response" in media_js
    assert "if (errorResponse.code !== 'rate_limited' || attempt === maxAttempts)" in media_js
    assert "response.headers.get('Retry-After')" in media_js
    assert "if (response.status !== 429 || attempt === maxAttempts)" not in media_js


def test_generated_document_pii_no_reveal_mode_strips_cached_values():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    documents_js = (root / "app" / "static" / "js" / "transcribe" / "documents.js").read_text(encoding="utf-8")

    assert "const allowReveal = options.allowReveal !== false;" in app_js
    assert "const updateTranscriptHighlights = options.updateTranscriptHighlights !== false;" in app_js
    assert "const displayRows = allowReveal" in app_js
    assert ": rows.map((entity) => ({ ...entity, value: '' }));" in app_js
    assert "currentPiiEntities = displayRows;" in app_js
    assert "renderDraft(currentDraftText || readActiveDraftText(), { force: true });" in app_js
    assert "${displayRows.map((entity) => `" in app_js
    assert "renderPiiEntities?.(selectedNote?.pii_entities" not in documents_js


def test_transcribe_static_asset_version_bumped_for_pii_source_visibility():
    root = Path(__file__).resolve().parents[1]
    shell_extras = (root / "app" / "templates" / "transcribe" / "_shell_extras.html").read_text(encoding="utf-8")

    assert "/static/js/transcribe/app.js?v=20260720-concurrent-followup" in shell_extras


def test_transcribe_workspace_keeps_all_assistant_tabs_inside_scroll_panel():
    root = Path(__file__).resolve().parents[1]
    workspace_html = (root / "app" / "templates" / "transcribe" / "_workspace.html").read_text(encoding="utf-8")

    class WorkspacePanelParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.panels = []

        def handle_starttag(self, tag, attrs):
            if tag != "div":
                return
            attr_map = dict(attrs)
            frame = {
                "class": attr_map.get("class", ""),
                "panel": attr_map.get("data-tab-panel"),
            }
            active_ancestors = [item["panel"] for item in self.stack if item["panel"]]
            if frame["panel"]:
                self.panels.append(
                    {
                        "panel": frame["panel"],
                        "ancestor_panels": active_ancestors,
                            "inside_scroll": any("transcribe-tab-workspace" in item["class"] for item in self.stack),
                    }
                )
            self.stack.append(frame)

        def handle_endtag(self, tag):
            if tag == "div" and self.stack:
                self.stack.pop()

    parser = WorkspacePanelParser()
    parser.feed(workspace_html)

    assert [item["panel"] for item in parser.panels] == ["output", "followups", "history"]
    assert all(item["inside_scroll"] for item in parser.panels)
    assert all(not item["ancestor_panels"] for item in parser.panels)


def test_active_templates_route_flash_messages_through_top_right_toasts():
    root = Path(__file__).resolve().parents[1]
    home_html = (root / "app" / "templates" / "home.html").read_text(encoding="utf-8")
    components_css = (root / "app" / "static" / "css" / "components.css").read_text(encoding="utf-8")
    auth_css = (root / "app" / "static" / "css" / "auth.css").read_text(encoding="utf-8")
    transcribe_css = (root / "app" / "static" / "css" / "transcribe.css").read_text(encoding="utf-8")
    admin_html = (root / "app" / "templates" / "admin_mockup.html").read_text(encoding="utf-8")
    login_html = (root / "app" / "templates" / "login.html").read_text(encoding="utf-8")
    onboarding_html = (root / "app" / "templates" / "onboarding.html").read_text(encoding="utf-8")
    request_access_html = (root / "app" / "templates" / "request_access.html").read_text(encoding="utf-8")
    mfa_html = (root / "app" / "templates" / "mfa_challenge.html").read_text(encoding="utf-8")

    assert "top: 24px;" in components_css
    assert "document.querySelectorAll('.flash').forEach((flash) => {" in home_html
    assert ".toast-container" not in transcribe_css
    assert "position: fixed;" in components_css and "data-toast-container" in admin_html
    assert "top: 24px;" in components_css and "data-toast-container" in login_html
    assert "data-toast-container" in onboarding_html
    assert "data-toast-container" in request_access_html
    assert "data-toast-container" in mfa_html


def test_auth_recovery_pages_use_current_shell_styling():
    root = Path(__file__).resolve().parents[1]
    onboarding_html = (root / "app" / "templates" / "onboarding.html").read_text(encoding="utf-8")
    reset_request_html = (root / "app" / "templates" / "password_reset_request.html").read_text(encoding="utf-8")
    reset_confirm_html = (root / "app" / "templates" / "password_reset_confirm.html").read_text(encoding="utf-8")
    login_html = (root / "app" / "templates" / "login.html").read_text(encoding="utf-8")
    request_access_html = (root / "app" / "templates" / "request_access.html").read_text(encoding="utf-8")
    mfa_html = (root / "app" / "templates" / "mfa_challenge.html").read_text(encoding="utf-8")
    auth_css = (root / "app" / "static" / "css" / "auth.css").read_text(encoding="utf-8")

    unchanged_auth_pages = (login_html, request_access_html, onboarding_html, reset_request_html, reset_confirm_html)
    for html in (*unchanged_auth_pages, mfa_html):
        assert '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">' in html
        assert '<link rel="stylesheet" href="/static/css/components.css?v=20260701-home-components">' in html
        assert '<body class="auth-page' in html
        assert "<style" not in html
        assert "panel hero" in html

    for html in unchanged_auth_pages:
        assert '<link rel="stylesheet" href="/static/css/auth.css?v=20260701-auth-extract">' in html
    assert '<link rel="stylesheet" href="/static/css/auth.css?v=20260718-mfa-code-slots">' in mfa_html

    assert "font-family: var(--font-display);" in auth_css
    assert "var(--accent)" in auth_css
    assert ".auth-page--login .auth-shell" in auth_css
    assert ".auth-page--onboarding .auth-shell" in auth_css
    assert "is-active" in onboarding_html
    assert "Account recovery" in reset_request_html
    assert "Secure account link" in reset_confirm_html


def test_mfa_challenge_gives_clear_authenticator_code_guidance():
    root = Path(__file__).resolve().parents[1]
    mfa_html = (root / "app" / "templates" / "mfa_challenge.html").read_text(encoding="utf-8")
    auth_css = (root / "app" / "static" / "css" / "auth.css").read_text(encoding="utf-8")

    assert 'class="mfa-device-visual" aria-hidden="true"' in mfa_html
    assert "Codes refresh about every 30 seconds." in mfa_html
    assert 'pattern="[0-9]{6}"' in mfa_html
    assert 'type="text"' in mfa_html
    assert 'autocomplete="one-time-code"' in mfa_html
    assert 'aria-describedby="mfa-code-help"' in mfa_html
    assert "autofocus" in mfa_html
    assert 'id="mfa-challenge-form" method="post" action="/mfa/challenge"' in mfa_html
    assert 'type="submit" form="mfa-challenge-form">Verify and continue</button>' in mfa_html
    assert '<form class="mfa-signout-form" method="post" action="/logout">' in mfa_html
    challenge_form_end = mfa_html.index("</form>", mfa_html.index('id="mfa-challenge-form"'))
    logout_form_start = mfa_html.index('<form class="mfa-signout-form"')
    assert challenge_form_end < logout_form_start
    assert ".mfa-device-visual" in auth_css
    assert ".mfa-code-input" in auth_css
    assert mfa_html.count('data-mfa-code-slot>') == 6
    assert 'data-mfa-countdown' not in mfa_html
    assert 'setInterval' not in mfa_html
    assert "replace(/[^0-9]/g, '').slice(0, 6)" in mfa_html
    assert "addEventListener('beforeinput'" in mfa_html
    assert "addEventListener('paste'" in mfa_html
    assert "@media (prefers-reduced-motion: reduce)" in auth_css
    assert ".mfa-actions-row" in auth_css
    actions_rule = auth_css.split(".mfa-actions-row {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in actions_rule
    assert "margin-top: 24px;" in actions_rule


def test_home_overview_and_asset_cards_keep_white_fill_like_team_cards():
    root = Path(__file__).resolve().parents[1]
    home_css = (root / "app" / "static" / "css" / "home.css").read_text(encoding="utf-8")

    assert ".overview-grid .panel {\n  background: var(--card);" in home_css
    assert ".asset-card {\n  display: grid;" in home_css
    assert "padding: 18px;\n  background: var(--card);" in home_css


def test_home_tab_script_finds_relocated_tab_nav():
    root = Path(__file__).resolve().parents[1]
    home_html = (root / "app" / "templates" / "home.html").read_text(encoding="utf-8")

    assert "const nav = document.querySelector('[data-tab-nav]')" in home_html
    assert "navContainer?.matches('[data-tab-nav]')" in home_html
    assert "nav.hidden = false;" in home_html


def test_user_transcribe_page_hides_emis_context_for_freeform_template(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Freeform Context")
    admin = make_user(email="freeform-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="freeform-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="freeform-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="Freeform note",
        prompt_text="Use British English.",
        mode=TemplateMode.freeform,
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "freeform-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert "Free text note" in page.text
    assert 'data-generated-freeform-panel' in page.text
    assert 'data-freeform-note-input' in page.text
    assert 'data-freeform-note-empty-state' not in page.text
    assert 'Select a template and start recording. Add note lines here as the consultation unfolds.' not in page.text


def test_user_transcribe_page_shows_transcript_and_followup_empty_states(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Clinic Transcript Empty State")
    admin = make_user(email="transcript-empty-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="transcript-empty-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="transcript-empty-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Blank session",
        current_draft_text_encrypted="Patient feels better.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(transcript_version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=member.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            generator_type=GeneratedDocumentGeneratorType.template,
            source_template_name="Freeform note",
            status=GeneratedDocumentStatus.ready,
            title="Blank note",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="",
            edited_output_text_encrypted="",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "transcript-empty-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=followups")

    assert page.status_code == 200
    assert "No note content yet." in page.text
    assert "Select a template and start recording. Add note lines here as the consultation unfolds." not in page.text
    assert "No follow-ups yet. Pick a quick action or write a custom request to create one from the current consultation." in page.text


def test_user_transcribe_page_shows_history_tab_empty_state(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic History Empty State")
    member = make_user(email="history-empty-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Blank session",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "history-empty-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=history")

    assert page.status_code == 200
    assert "Consultation sources" in page.text
    assert "Post-consultation dictation" in page.text
    assert 'data-dictation-modal' in page.text
    assert 'data-dictation-compact' in page.text
    assert 'data-dictation-record-toggle' in page.text
    assert "Start a recording to see your transcript" in page.text
    assert 'data-transcript-empty' in page.text


def test_user_transcribe_page_renders_ready_freeform_note_editor(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Freeform Editor")
    admin = make_user(email="freeform-editor-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="freeform-editor-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="freeform-editor-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="Freeform note",
        prompt_text="Use British English.",
        mode=TemplateMode.freeform,
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Patient is improving.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    generated = GeneratedDocument(
        owner_user_id=member.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template.versions[-1].id,
        source_template_name=template.name,
        status=GeneratedDocumentStatus.ready,
        title="Freeform summary",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="Line one\nLine two",
        edited_output_text_encrypted="Line one\nLine two",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(generated)
    db_session.commit()

    client.post("/login", data={"email": "freeform-editor-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'data-generated-freeform-panel' in page.text
    assert 'data-freeform-note-row' in page.text
    assert 'data-freeform-note-input' in page.text
    assert 'data-note-editor-toolbar' in page.text
    assert 'data-note-editor-header-toolbar' in page.text


def test_user_transcribe_page_reloads_persisted_structured_emis_context(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Structured Persist")
    admin = make_user(email="structured-persist-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="structured-persist-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="structured-persist-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_template(
        scope=TemplateScope.user,
        owner=member,
        actor=member,
        name="EMIS note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
                {"section_key": "tasks", "section_label": "Tasks", "instruction": "List tasks.", "section_order": 2},
            ],
        },
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        structured_context_json={"profile": "emis", "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]}},
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "structured-persist-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'data-section-key="problem"' in page.text
    assert "Known asthma" in page.text
    assert "Peak flow diary" in page.text


def test_recorded_upload_microphone_rolls_over_before_whole_file_limits():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    media_js = (root / "app" / "static" / "js" / "transcribe" / "media.js").read_text(encoding="utf-8")
    capture_docs = (root / "docs" / "transcript-capture.md").read_text(encoding="utf-8")

    assert "const batchRolloverMaxDurationMs = 12 * 60 * 1000;" in app_js
    assert "const batchRolloverMaxBytes = 22 * 1024 * 1024;" in app_js
    assert "batchRolloverConflictRetryMs," in app_js
    assert "batchForceRolloverRequested = true;" in media_js
    assert "void rolloverMicrophoneBatchCapture();" in media_js
    assert "batchRolloverUploadPending = true;" in media_js
    assert "const uploaded = await queueMicrophoneBatchUpload(blob, { rollover: true, transcriptId });" in media_js
    assert "if (!uploaded) {" in media_js
    assert "No later audio was recorded after the failed part." in media_js
    assert "batchSpeechSegments = [];" in media_js
    assert "await restartMicrophoneBatchAfterRollover();" in media_js
    assert "batchCaptureGeneration !== restartGeneration" in media_js
    assert "Previous recording part is still transcribing. Holding the next part locally, then retrying..." in media_js
    assert "const transcriptId = activeBatchTranscriptId();" in media_js
    assert "return true;" in media_js
    assert "return false;" in media_js
    assert "await queueMicrophoneBatchUpload(blob, { transcriptId });" in media_js
    assert "await uploadBatchAudio(blob, { transcriptId: uploadTranscriptId });" in media_js
    assert "`/api/v1/transcripts/${uploadTranscriptId}/audio-file`" in media_js
    assert "if (typeof uploadBatchAudio === 'function') {" in media_js
    assert "await uploadBatchAudio(blob, { transcriptId: uploadTranscriptId });" in media_js
    assert "isCaptureUiActive: () => (" in media_js
    assert "setMicButtons(isCaptureUiActive());" in app_js
    assert "captureController?.isCaptureUiActive?.()" in app_js
    assert "capture restarts for the same transcript only after that part is accepted" in capture_docs
    assert "capture stops instead of recording later audio" in capture_docs


def test_user_transcribe_page_exposes_workspace_api_endpoint(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Workspace UI")
    member = make_user(email="workspace-ui-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "workspace-ui-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert f'data-workspace-endpoint="/api/v1/transcribe/workspace?transcript_id={transcript.id}"' in page.text
    assert 'data-transcript-title-form' in page.text
    assert 'data-upload-form' in page.text
    assert 'data-generate-output-form' in page.text
    assert 'id="new-session-form"' in page.text
    assert 'id="bulk-delete-sessions"' in page.text
    assert 'data-session-link' in page.text


def test_user_transcribe_page_uses_preferred_recording_mode_for_new_session(
    client,
    make_team,
    make_user,
    make_user_app_preference,
):
    team = make_team(name="Clinic Preferred Recording")
    member = make_user(email="preferred-recording-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    make_user_app_preference(user=member, preferences_json={"preferred_recording_mode": "live_chunked"})

    client.post("/login", data={"email": "preferred-recording-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert 'name="ingestion_mode" value="live_chunked"' in page.text
    assert '<option value="live_chunked" selected>Live capture</option>' in page.text


def test_user_transcribe_page_uses_preferred_template_as_note_default(
    client,
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_user_app_preference,
):
    team = make_team(name="Clinic Preferred Template")
    admin = make_user(email="preferred-template-admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="preferred-template-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="preferred-template-member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    first_template = make_template(scope=TemplateScope.user, owner=member, actor=member, name="First note", prompt_text="Write first note.", mode=TemplateMode.freeform)
    preferred_template = make_template(scope=TemplateScope.user, owner=member, actor=member, name="Preferred note", prompt_text="Write preferred note.", mode=TemplateMode.structured)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    make_user_app_preference(user=member, preferences_json={"default_template_id": str(preferred_template.id)})

    client.post("/login", data={"email": "preferred-template-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert "Preferred note" in page.text
    assert f'value="{preferred_template.id}"' in page.text
    assert first_template.name in page.text


def test_user_transcribe_page_keeps_structured_output_refresh_hooks(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Structured Refresh UI")
    member = make_user(email="structured-refresh-ui@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(transcript_version)
    db_session.flush()
    generated_document = GeneratedDocument(
        owner_user_id=member.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=None,
        source_template_name="EMIS note",
        status=GeneratedDocumentStatus.ready,
        title="Chest review",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="Problem\nAsthma flare.",
        edited_output_text_encrypted="Problem\nAsthma flare.",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used="gpt-4o-mini",
    )
    db_session.add(generated_document)
    db_session.flush()
    db_session.add(
        GeneratedDocumentSection(
            generated_document_id=generated_document.id,
            section_key="problem",
            section_label="Problem",
            section_order=1,
            original_text_encrypted="Asthma flare.",
            edited_text_encrypted="Asthma flare.",
            is_edited=False,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "structured-refresh-ui@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'data-generated-structured-panel' in page.text
    assert 'data-generated-structured-sections' in page.text
    assert 'data-copy-structured-lines' in page.text
    assert 'data-structured-line-input' in page.text


def test_user_transcribe_page_uses_generated_note_snapshot_for_structured_sections(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Structured Snapshot UI")
    member = make_user(email="structured-snapshot-ui@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=transcript.current_draft_text_encrypted,
    )
    db_session.add(transcript_version)
    db_session.flush()
    generated_document = GeneratedDocument(
        owner_user_id=member.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=None,
        source_template_name="EMIS note",
        status=GeneratedDocumentStatus.ready,
        title="Chest review",
        document_mode=TemplateMode.structured,
        structured_section_definitions_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "section_order": 1},
            ],
        },
        original_output_text_encrypted="Problem\nAsthma flare.",
        edited_output_text_encrypted="Problem\nAsthma flare.",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used="gpt-4o-mini",
    )
    db_session.add(generated_document)
    db_session.flush()
    db_session.add(
        GeneratedDocumentSection(
            generated_document_id=generated_document.id,
            section_key="problem",
            section_label="Problem",
            section_order=1,
            original_text_encrypted="Asthma flare.",
            edited_text_encrypted="Asthma flare.",
            is_edited=False,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": "structured-snapshot-ui@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert '<h3 class="structured-section-title">Problem</h3>' in page.text
    assert 'data-copy-structured-section' in page.text
    assert 'aria-label="Copy Problem section"' in page.text
    assert '<h3 class="structured-section-title">History</h3>' not in page.text


def test_local_dev_transcribe_page_shows_redaction_debug_panel(
    client,
    db_session,
    make_team,
    make_user,
    make_redaction_run,
):
    team = make_team(name="Clinic Debug UI")
    member = make_user(email="dev.user@example.com", password="password-3", team=team, team_role=TeamRole.user, mfa_required=False, mfa_enabled=False)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="John Smith attended the clinic.",
    )
    db_session.add(transcript_version)
    db_session.commit()
    run = make_redaction_run(
        transcript=transcript,
        transcript_version=transcript_version,
        owner=member,
        redacted_text="[PHI-1] attended the clinic.",
        entities=[(1, "PERSON", "John Smith")],
    )
    document = GeneratedDocument(
        owner_user_id=member.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=run.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        source_template_name="Clinic note",
        status=GeneratedDocumentStatus.ready,
        title="Clinic note",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="done",
        edited_output_text_encrypted="done",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.commit()

    client.post("/login", data={"email": "dev.user@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert "Dev redaction debug" in page.text
    assert str(document.id) in page.text


def test_user_transcribe_page_can_queue_followup_generation(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Clinic Follow-up UI")
    admin = make_user(email="admin-followup-ui@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader-followup-ui@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-followup-ui@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    class FakeTaskResult:
        id = "generated-followup-task-ui"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    client.post("/login", data={"email": "member-followup-ui@example.com", "password": "password-3"}, follow_redirects=False)
    generated = client.post(
        "/transcribe/generate-followup",
        data={"transcript_id": str(transcript.id), "prompt_text": "Arrange blood tests and a review if symptoms persist."},
        follow_redirects=False,
    )

    assert generated.status_code == 303
    assert f"transcript_id={transcript.id}" in generated.headers["location"]
    assert "tab=followups" in generated.headers["location"]

    page = client.get(generated.headers["location"])
    assert page.status_code == 200
    assert "Queued follow-up generation." in page.text
    assert "Generating your follow-up" in page.text
    assert "preparing your follow-up..." in page.text
    assert "queued" in page.text


def test_user_transcribe_page_renders_generated_document_switchers(
    client,
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Clinic Document Switchers")
    leader = make_user(email="leader-document-switchers@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-document-switchers@example.com", password="password-3", team=team, team_role=TeamRole.user)
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Arrange review",
        prompt_text="Write follow-up review advice.",
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Patient is improving.",
    )
    db_session.add(transcript_version)
    db_session.commit()
    db_session.add_all(
        [
            GeneratedDocument(
                owner_user_id=member.id,
                team_id=team.id,
                transcript_id=transcript.id,
                transcript_version_id=transcript_version.id,
                generator_type=GeneratedDocumentGeneratorType.template,
                source_template_name="Clinic note",
                status=GeneratedDocumentStatus.ready,
                title="Visit summary v2",
                document_mode=TemplateMode.freeform,
                original_output_text_encrypted="Latest body",
                edited_output_text_encrypted="Latest body",
                retention_expires_at=transcript.retention_expires_at,
            ),
            GeneratedDocument(
                owner_user_id=member.id,
                team_id=team.id,
                transcript_id=transcript.id,
                transcript_version_id=transcript_version.id,
                generator_type=GeneratedDocumentGeneratorType.template,
                source_template_name="Clinic note",
                status=GeneratedDocumentStatus.ready,
                title="Visit summary v1",
                document_mode=TemplateMode.freeform,
                original_output_text_encrypted="Earlier body",
                edited_output_text_encrypted="Earlier body",
                retention_expires_at=transcript.retention_expires_at,
            ),
            GeneratedDocument(
                owner_user_id=member.id,
                team_id=team.id,
                transcript_id=transcript.id,
                transcript_version_id=transcript_version.id,
                generator_type=GeneratedDocumentGeneratorType.followup,
                source_template_name="Follow-up",
                follow_up_prompt_text="Send patient-facing review advice.",
                status=GeneratedDocumentStatus.ready,
                title="Follow-up v2",
                document_mode=TemplateMode.freeform,
                original_output_text_encrypted="Latest follow-up",
                edited_output_text_encrypted="Latest follow-up",
                retention_expires_at=transcript.retention_expires_at,
            ),
            GeneratedDocument(
                owner_user_id=member.id,
                team_id=team.id,
                transcript_id=transcript.id,
                transcript_version_id=transcript_version.id,
                generator_type=GeneratedDocumentGeneratorType.quick_action,
                source_template_name="Quick action",
                source_quick_action_name="Send SMS",
                status=GeneratedDocumentStatus.ready,
                title="Quick action v1",
                document_mode=TemplateMode.freeform,
                original_output_text_encrypted="SMS body",
                edited_output_text_encrypted="SMS body",
                retention_expires_at=transcript.retention_expires_at,
            ),
        ]
    )
    db_session.commit()

    client.post("/login", data={"email": "member-document-switchers@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'data-note-selector' in page.text
    assert 'data-note-selector-row' in page.text
    assert 'data-note-hover-delete' in page.text
    assert 'document-switcher-item__delete' in page.text
    assert 'data-note-delete' not in page.text
    assert re.search(r'data-note-selector-row[\s\S]*data-note-selector[\s\S]*data-note-hover-delete', page.text)
    assert re.search(
        r'Visit summary v2[\s\S]*?ready · <time datetime="[^"]+" data-note-created-at="[^"]+">\d{2}-\d{2}-\d{2} \d{2}:\d{2}</time>',
        page.text,
    )
    assert "Delete note permanently" in page.text
    assert "Visit summary v2" in page.text
    assert "Visit summary v1" in page.text
    assert 'data-document-kind="followup"' in page.text
    assert 'data-followup-recent-list' in page.text
    assert 'data-run-quick-action-form' in page.text
    assert f'value="{quick_action.id}"' in page.text
    assert 'data-copy-latest-followup' in page.text
    assert 'data-followup-delete-latest' in page.text
    assert 'data-followup-title-input' in page.text
    assert 'data-followup-body-input' in page.text
    assert 'data-followup-copy-body' in page.text
    assert "Follow-up v2" in page.text
    assert "Quick action v1" in page.text


def test_user_transcribe_page_can_run_quick_action(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Quick Action UI")
    admin = make_user(email="admin-quick-action-ui@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader-quick-action-ui@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-quick-action-ui@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")
    make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Send SMS",
        prompt_text="Write a short SMS update for the patient.",
    )
    make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Referral letter",
        prompt_text="Write a short referral letter.",
    )
    make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Call patient",
        prompt_text="Write a short callback message.",
    )
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Arrange review",
        prompt_text="Write a short follow-up arranging a GP review if symptoms persist.",
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    class FakeTaskResult:
        id = "generated-quick-action-task-ui"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    client.post("/login", data={"email": "member-quick-action-ui@example.com", "password": "password-3"}, follow_redirects=False)
    generated = client.post(
        "/transcribe/run-quick-action",
        data={
            "transcript_id": str(transcript.id),
            "quick_action_id": str(quick_action.id),
            "quick_action_context_text": "Mention the follow-up call.",
        },
        follow_redirects=False,
    )

    assert generated.status_code == 303
    assert f"transcript_id={transcript.id}" in generated.headers["location"]
    assert "tab=followups" in generated.headers["location"]

    page = client.get(generated.headers["location"])
    assert page.status_code == 200
    assert "Queued quick action generation." in page.text
    assert "Quick picks" in page.text
    assert page.text.count('data-quick-action-quick-pick') >= 4
    assert 'data-lucide="message-square"' in page.text
    assert 'data-lucide="file-text"' in page.text
    assert 'data-lucide="phone"' in page.text
    assert 'data-lucide="sparkles"' in page.text
    assert "Arrange review" in page.text
    assert "queued" in page.text

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id))
    assert persisted_document is not None
    assert persisted_document.prompt_snapshot_text == "Write a short follow-up arranging a GP review if symptoms persist.\n\nAdditional context:\nMention the follow-up call."


def test_user_transcribe_page_rejects_oversized_quick_action_context_on_form_submit(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Quick Action UI Limit")
    admin = make_user(email="admin-quick-action-ui-limit@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader-quick-action-ui-limit@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-quick-action-ui-limit@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Arrange review",
        prompt_text="Write a short follow-up arranging a GP review if symptoms persist.",
    )
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    class FakeTaskResult:
        id = "generated-quick-action-task-ui-limit"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    client.post("/login", data={"email": "member-quick-action-ui-limit@example.com", "password": "password-3"}, follow_redirects=False)
    generated = client.post(
        "/transcribe/run-quick-action",
        data={
            "transcript_id": str(transcript.id),
            "quick_action_id": str(quick_action.id),
            "quick_action_context_text": "x" * 4001,
        },
        follow_redirects=False,
    )

    assert generated.status_code == 303
    page = client.get(generated.headers["location"])
    assert page.status_code == 200
    assert "Additional context must be 4000 characters or fewer" in page.text
    assert db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)) is None


def test_admin_page_marks_current_account_protected(client, make_user):
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="member@example.com", password="password-2")

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin")

    assert page.status_code == 200


def test_user_transcribe_page_orders_templates_and_quick_picks_from_preferences(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
    make_user_app_preference,
):
    team = make_team(name="Clinic Favourite Order UI")
    member = make_user(email="member-favourites-ui@example.com", password="password-3", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    template_a = make_template(scope=TemplateScope.user, owner=member, actor=member, name="Alpha template")
    template_b = make_template(scope=TemplateScope.user, owner=member, actor=member, name="Beta template")
    quick_action_a = make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Alpha action", prompt_text="Alpha")
    quick_action_b = make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="Beta action", prompt_text="Beta")
    make_user_app_preference(
        user=member,
        preferences_json={
            "favorite_template_ids": [str(template_b.id)],
            "favorite_quick_action_ids": [str(quick_action_b.id)],
        },
    )
    db_session.commit()

    client.post("/login", data={"email": "member-favourites-ui@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert page.text.index("Beta template") < page.text.index("Alpha template")
    assert page.text.index("Beta action") < page.text.index("Alpha action")


def test_admin_page_can_save_team_stt_config_for_selected_team(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=stt")
    assert page.status_code == 200
    assert "Add STT provider" in page.text

    save = client.post(
        "/admin/stt-configs",
        data={
            "team_id": str(team.id),
            "label": "Admin STT",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:7000",
            "bearer_token": "secret-token",
            "provider_model": "whisper-1",
            "language": "en",
            "extra_form_fields_json": "{\"chunk_mode\":\"memory\"}",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&tab=providers"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Admin STT"


def test_admin_page_can_manage_default_assets(client, db_session, make_user):
    make_user(email="admin-default-assets@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-default-assets@example.com", "password": "password-1"}, follow_redirects=False)

    editor = client.get("/admin/templates/editor?scope=default")
    assert editor.status_code == 200
    assert "Default template" in editor.text

    saved_template = client.post(
        "/admin/default-templates",
        data={
            "name": "Default EMIS",
            "description": "Team starter note",
            "prompt_text": "Write an EMIS note.",
            "mode": "structured",
            "section_prompt_problem": "Problem summary",
            "section_prompt_history": "History summary",
            "section_prompt_family_history": "Family history",
            "section_prompt_social_history": "Social history",
            "section_prompt_examination": "Examination",
            "section_prompt_comment": "Comment",
            "section_prompt_tasks": "Tasks",
            "section_prompt_investigations": "Investigations",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert saved_template.status_code == 303
    persisted_template = db_session.scalar(select(DefaultPromptTemplate).where(DefaultPromptTemplate.name == "Default EMIS"))
    assert persisted_template is not None

    saved_quick_action = client.post(
        "/admin/default-quick-actions",
        data={
            "name": "Default SMS",
            "description": "Starter patient text",
            "prompt_text": "Write a short patient SMS.",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert saved_quick_action.status_code == 303
    persisted_quick_action = db_session.scalar(select(DefaultQuickAction).where(DefaultQuickAction.name == "Default SMS"))
    assert persisted_quick_action is not None

    defaults_page = client.get("/admin?tab=global-defaults")
    assert defaults_page.status_code == 200
    assert "Global defaults" in defaults_page.text
    assert "Templates" in defaults_page.text
    assert "Quick actions" in defaults_page.text
    assert "Default EMIS" in defaults_page.text
    assert "Default SMS" in defaults_page.text


def test_admin_team_creation_seeds_active_default_assets(client, db_session, make_user, make_default_template, make_default_quick_action):
    admin = make_user(email="admin-default-seed@example.com", password="password-1", is_system_admin=True)
    make_default_template(actor=admin, name="Starter note", prompt_text="Write a starter note.")
    make_default_quick_action(actor=admin, name="Starter action", prompt_text="Write a starter action.")
    make_default_template(actor=admin, name="Disabled note", prompt_text="Skip me.", is_active=False)
    make_default_quick_action(actor=admin, name="Disabled action", prompt_text="Skip me.", is_active=False)

    client.post("/login", data={"email": "admin-default-seed@example.com", "password": "password-1"}, follow_redirects=False)

    created = client.post(
        "/admin/teams",
        data={
            "name": "Seeded Clinic",
            "status": "active",
            "default_retention_days": "30",
            "return_tab": "defaults",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    team = db_session.scalar(select(func.count()).select_from(PromptTemplate).join(PromptTemplate.team).where(PromptTemplate.name == "Starter note"))
    assert team == 1
    assert db_session.scalar(select(func.count()).select_from(QuickAction).join(QuickAction.team).where(QuickAction.name == "Starter action")) == 1
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate).where(PromptTemplate.name == "Disabled note")) == 0
    assert db_session.scalar(select(func.count()).select_from(QuickAction).where(QuickAction.name == "Disabled action")) == 0


def test_admin_team_creation_seeds_builtin_assets_when_defaults_empty(client, db_session, make_user):
    make_user(email="admin-builtin-seed@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-builtin-seed@example.com", "password": "password-1"}, follow_redirects=False)
    created = client.post(
        "/admin/teams",
        data={"name": "Builtin Clinic", "status": "active", "default_retention_days": "30", "return_tab": "directory"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    team = db_session.scalar(select(Team).where(Team.name == "Builtin Clinic"))
    assert team is not None
    templates = list(
        db_session.scalars(
            select(PromptTemplate).where(PromptTemplate.team_id == team.id)
        )
    )
    assert {template.name for template in templates} == {
        built_in["name"] for built_in in BUILTIN_DEFAULT_TEMPLATES
    }
    assert "Sectioned EMIS note" not in {
        template.name for template in templates
    }
    template = next(
        template
        for template in templates
        if template.name == BUILTIN_DEFAULT_TEMPLATE["name"]
    )
    version = db_session.scalar(
        select(PromptTemplateVersion).where(
            PromptTemplateVersion.template_id == template.id
        )
    )
    assert version is not None
    assert version.mode is TemplateMode.structured
    assert [section["section_key"] for section in version.config_json["sections"]] == [
        "problem",
        "history",
        "family_history",
        "social_history",
        "examination",
        "comment",
        "tasks",
        "investigations",
    ]
    for built_in in BUILTIN_DEFAULT_QUICK_ACTIONS:
        assert db_session.scalar(select(QuickAction).where(QuickAction.team_id == team.id, QuickAction.name == built_in["name"])) is not None


def test_builtin_team_asset_seed_is_idempotent(db_session, make_team, make_user):
    team = make_team(name="Builtin Idempotent Clinic")
    leader = make_user(email="builtin-idempotent-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()
    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()

    assert db_session.scalar(
        select(func.count())
        .select_from(PromptTemplate)
        .where(PromptTemplate.team_id == team.id)
    ) == len(BUILTIN_DEFAULT_TEMPLATES)
    for built_in in BUILTIN_DEFAULT_TEMPLATES:
        assert db_session.scalar(
            select(func.count())
            .select_from(PromptTemplate)
            .where(
                PromptTemplate.team_id == team.id,
                PromptTemplate.name == built_in["name"],
            )
        ) == 1
    for built_in in BUILTIN_DEFAULT_QUICK_ACTIONS:
        assert db_session.scalar(select(func.count()).select_from(QuickAction).where(QuickAction.team_id == team.id, QuickAction.name == built_in["name"])) == 1


def test_builtin_team_asset_upgrade_retires_old_template_and_replaces_old_actions(
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team = make_team(name="Builtin Upgrade Clinic")
    leader = make_user(
        email="builtin-upgrade-leader@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )
    old_template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Sectioned EMIS note",
        description=_LEGACY_BUILTIN_TEMPLATE["description"],
        prompt_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
        mode=_LEGACY_BUILTIN_TEMPLATE["mode"],
        config_json=_LEGACY_BUILTIN_TEMPLATE["config_json"],
    )
    transcript = Transcript(
        owner_user_id=leader.id,
        team_id=team.id,
        title="Existing note",
        current_draft_text_encrypted="Synthetic transcript.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Synthetic transcript.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    generated_document = GeneratedDocument(
        owner_user_id=leader.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=old_template.versions[-1].id,
        source_template_name=old_template.name,
        prompt_snapshot_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
        status=GeneratedDocumentStatus.ready,
        title="Existing generated note",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="Synthetic generated note.",
        edited_output_text_encrypted="Synthetic generated note.",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(generated_document)
    db_session.commit()
    referral = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Referral letter",
        description=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["description"],
        prompt_text=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["prompt_text"],
    )

    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()

    assert db_session.scalar(
        select(PromptTemplate).where(
            PromptTemplate.team_id == team.id,
            PromptTemplate.name == "Sectioned EMIS note",
        )
    ) is None
    db_session.refresh(generated_document)
    assert generated_document.template_version_id is None
    assert generated_document.source_template_name == "Sectioned EMIS note"
    assert (
        generated_document.prompt_snapshot_text
        == _LEGACY_BUILTIN_TEMPLATE["prompt_text"]
    )
    assert generated_document.edited_output_text_encrypted == "Synthetic generated note."
    referral_versions = list(
        db_session.scalars(
            select(QuickActionVersion)
            .where(QuickActionVersion.quick_action_id == referral.id)
            .order_by(QuickActionVersion.version_no)
        )
    )
    expected = next(
        item
        for item in BUILTIN_DEFAULT_QUICK_ACTIONS
        if item["name"] == "Referral letter"
    )
    assert len(referral_versions) == 2
    assert referral_versions[-1].prompt_text == expected["prompt_text"]


def test_builtin_team_asset_upgrade_preserves_customized_retired_template(
    db_session,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Customized Builtin Upgrade Clinic")
    leader = make_user(
        email="customized-builtin-upgrade-leader@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )
    customized = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Sectioned EMIS note",
        prompt_text="Keep this team-written prompt.",
    )

    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()

    assert db_session.get(PromptTemplate, customized.id) is not None


def test_builtin_team_asset_upgrade_preserves_description_only_customizations(
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team = make_team(name="Description Customized Builtin Clinic")
    leader = make_user(
        email="description-customized-builtin@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )
    template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Sectioned EMIS note",
        description="Keep this team-written description.",
        prompt_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
        mode=_LEGACY_BUILTIN_TEMPLATE["mode"],
        config_json=_LEGACY_BUILTIN_TEMPLATE["config_json"],
    )
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Referral letter",
        description="Keep this team-written referral description.",
        prompt_text=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["prompt_text"],
    )

    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()

    assert db_session.get(PromptTemplate, template.id) is not None
    assert template.description == "Keep this team-written description."
    assert quick_action.description == "Keep this team-written referral description."
    assert len(quick_action.versions) == 1


def test_builtin_team_asset_upgrade_preserves_assets_with_edit_history(
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team = make_team(name="Reverted Builtin Clinic")
    leader = make_user(
        email="reverted-builtin@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )
    template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Sectioned EMIS note",
        description=_LEGACY_BUILTIN_TEMPLATE["description"],
        prompt_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
        mode=_LEGACY_BUILTIN_TEMPLATE["mode"],
        config_json=_LEGACY_BUILTIN_TEMPLATE["config_json"],
    )
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Referral letter",
        description=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["description"],
        prompt_text=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["prompt_text"],
    )
    db_session.add(
        PromptTemplateVersion(
            template_id=template.id,
            version_no=2,
            mode=_LEGACY_BUILTIN_TEMPLATE["mode"],
            prompt_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
            config_json=_LEGACY_BUILTIN_TEMPLATE["config_json"],
            created_by_user_id=leader.id,
        )
    )
    db_session.add(
        QuickActionVersion(
            quick_action_id=quick_action.id,
            version_no=2,
            mode=TemplateMode.freeform,
            prompt_text=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["prompt_text"],
            created_by_user_id=leader.id,
        )
    )
    db_session.commit()

    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()

    assert db_session.get(PromptTemplate, template.id) is not None
    assert len(template.versions) == 2
    assert len(quick_action.versions) == 2


def test_builtin_default_asset_upgrade_preserves_description_customizations(
    db_session,
    make_user,
    make_default_template,
    make_default_quick_action,
):
    admin = make_user(
        email="customized-default-assets@example.com",
        password="password-1",
        is_system_admin=True,
    )
    template = make_default_template(
        actor=admin,
        name="Sectioned EMIS note",
        description="Keep this administrator-written description.",
        prompt_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
        mode=_LEGACY_BUILTIN_TEMPLATE["mode"],
        config_json=_LEGACY_BUILTIN_TEMPLATE["config_json"],
    )
    quick_action = make_default_quick_action(
        actor=admin,
        name="Referral letter",
        description="Keep this administrator-written referral description.",
        prompt_text=_LEGACY_BUILTIN_QUICK_ACTIONS["Referral letter"]["prompt_text"],
    )

    ensure_builtin_default_assets(db_session, admin)
    db_session.commit()

    assert db_session.get(DefaultPromptTemplate, template.id) is not None
    assert template.description == "Keep this administrator-written description."
    assert (
        quick_action.description
        == "Keep this administrator-written referral description."
    )
    assert len(quick_action.versions) == 1


def test_builtin_team_asset_upgrade_defers_retirement_during_generation(
    db_session,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Active Builtin Upgrade Clinic")
    leader = make_user(
        email="active-builtin-upgrade-leader@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )
    old_template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Sectioned EMIS note",
        description=_LEGACY_BUILTIN_TEMPLATE["description"],
        prompt_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
        mode=_LEGACY_BUILTIN_TEMPLATE["mode"],
        config_json=_LEGACY_BUILTIN_TEMPLATE["config_json"],
    )
    transcript = Transcript(
        owner_user_id=leader.id,
        team_id=team.id,
        title="Queued note",
        current_draft_text_encrypted="Synthetic transcript.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Synthetic transcript.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=leader.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            generator_type=GeneratedDocumentGeneratorType.template,
            template_version_id=old_template.versions[-1].id,
            source_template_name=old_template.name,
            prompt_snapshot_text=_LEGACY_BUILTIN_TEMPLATE["prompt_text"],
            status=GeneratedDocumentStatus.queued,
            title="Queued generated note",
            document_mode=TemplateMode.structured,
            original_output_text_encrypted="",
            edited_output_text_encrypted="",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()

    ensure_builtin_team_assets(db_session, team=team, actor=leader)
    db_session.commit()

    assert db_session.get(PromptTemplate, old_template.id) is not None


def test_builtin_asset_catalog_is_embedded_and_valid() -> None:
    assert not Path("app/default_assets/templates.json").exists()
    assert not Path("app/default_assets/quick_actions.json").exists()
    assert BUILTIN_DEFAULT_TEMPLATE["name"] == "Daily Driver"
    assert {item["name"] for item in BUILTIN_DEFAULT_TEMPLATES} == {
        "Daily Driver",
        "GP Note",
        "GLP1 Review",
        "Depression",
        "Dictation cleaner",
    }
    assert {item["name"] for item in BUILTIN_DEFAULT_QUICK_ACTIONS} == {
        "Physio Referral",
        "Referral letter",
        "Patient follow-up message",
    }


def test_admin_page_can_delete_team_and_owned_records(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
    make_stt_config,
    make_stt_selection,
    make_llm_config,
    make_llm_selection,
    make_account_request,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    monkeypatch,
):
    admin = make_user(email="admin-delete-team@example.com", password="password-1", is_system_admin=True)
    team = make_team(name="Delete Clinic")
    leader = make_user(email="leader-delete-team@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-delete-team@example.com", password="password-1", team=team, team_role=TeamRole.user)
    account_request = make_account_request(requested_email="pending-delete-team@example.com", requested_team_name=team.name)
    make_template(scope=TemplateScope.team, team=team, actor=leader, name="Team note")
    make_quick_action(scope=TemplateScope.team, team=team, actor=leader, name="Team action", prompt_text="Send a follow-up")
    stt_config = make_stt_config(team=team, actor=admin, label="Team STT")
    make_stt_selection(config=stt_config, actor=admin)
    stt_secret_ref = stt_config.vault_secret_ref
    llm_config = make_llm_config(team=team, actor=admin, label="Team LLM", available_models_json=["gpt-4o-mini"])
    llm_secret_ref = llm_config.vault_secret_ref
    make_llm_selection(config=llm_config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    db_session.add(
        TeamHallucinationCheckSelection(
            team_id=team.id,
            llm_config_id=llm_config.id,
            model_name_override="gpt-4o-mini",
            selected_by_user_id=admin.id,
        )
    )
    deidentification_provider = make_deidentification_provider(actor=admin, label="Team Deid", adapter_kind=DeidentificationAdapterKind.generic_rest, base_url="https://deid.example.com", detect_path="/detect")
    make_deidentification_provider_assignment(team=team, provider=deidentification_provider, actor=admin)
    make_deidentification_selection(team=team, provider=deidentification_provider, actor=leader)

    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Delete me",
        current_draft_text_encrypted="Transcript text",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    retry_audio_ref = "secret:openscribe/transcript-ingestion/team-delete/source-audio"
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="team-delete.wav",
            status=TranscriptIngestionJobStatus.failed,
            source_audio_vault_ref=retry_audio_ref,
        )
    )
    db_session.add(
        ProviderUsageEvent(
            team_id=team.id,
            owner_user_id=member.id,
            generated_document_id=None,
            transcript_id=transcript.id,
            llm_config_id=llm_config.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed,
            provider_adapter="openai_chat",
            model_name="gpt-4o-mini",
            status="completed",
            total_tokens=42,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    client.post("/login", data={"email": "admin-delete-team@example.com", "password": "password-1"}, follow_redirects=False)
    deleted = client.post(f"/admin/teams/{team.id}/delete", data={"return_tab": "directory"}, follow_redirects=False)

    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/admin?tab=directory"
    assert db_session.get(Team, team.id) is None
    assert db_session.get(AccountRequest, account_request.id) is None
    assert db_session.get(TeamSttConfig, stt_config.id) is None
    assert db_session.get(TeamLlmConfig, llm_config.id) is None
    assert db_session.scalar(select(func.count()).select_from(TeamSttSelection).where(TeamSttSelection.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(TeamDeidentificationProviderAssignment).where(TeamDeidentificationProviderAssignment.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate).where(PromptTemplate.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(QuickAction).where(QuickAction.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(Transcript).where(Transcript.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProviderUsageEvent).where(ProviderUsageEvent.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProviderUsageEvent).where(ProviderUsageEvent.owner_user_id.in_([leader.id, member.id]))) == 0
    assert db_session.get(User, leader.id) is None
    assert db_session.get(User, member.id) is None
    audio_cleanup_job = db_session.scalar(select(TranscriptAudioCleanupJob))
    assert audio_cleanup_job is not None
    assert audio_cleanup_job.secret_ref == retry_audio_ref
    assert audio_cleanup_job.attempt_count == 1
    provider_cleanup_jobs = db_session.scalars(
        select(ProviderSecretCleanupJob).where(
            ProviderSecretCleanupJob.secret_ref.in_([stt_secret_ref, llm_secret_ref])
        )
    ).all()
    assert {(job.kind, job.secret_ref) for job in provider_cleanup_jobs} == {
        (ProviderSecretCleanupKind.stt, stt_secret_ref),
        (ProviderSecretCleanupKind.llm, llm_secret_ref),
    }


def test_admin_team_delete_checks_system_admin_members_before_vault_cleanup(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_llm_config,
    monkeypatch,
):
    admin = make_user(email="admin-delete-team-preflight@example.com", password="password-1", is_system_admin=True)
    team = make_team(name="Delete Preflight Clinic")
    team_admin = make_user(email="team-admin-preflight@example.com", password="password-1", is_system_admin=True)
    team_admin.team_id = team.id
    db_session.add(team_admin)
    stt_config = make_stt_config(team=team, actor=admin, label="Preflight STT")
    llm_config = make_llm_config(team=team, actor=admin, label="Preflight LLM")
    db_session.commit()

    client.post("/login", data={"email": "admin-delete-team-preflight@example.com", "password": "password-1"}, follow_redirects=False)
    blocked = client.post(f"/admin/teams/{team.id}/delete", data={"return_tab": "directory"}, follow_redirects=False)

    assert blocked.status_code == 409
    assert "Cannot delete a team that still contains a system-admin account" in blocked.text
    assert db_session.get(Team, team.id) is not None
    assert db_session.get(TeamSttConfig, stt_config.id) is not None
    assert db_session.get(TeamLlmConfig, llm_config.id) is not None
    assert db_session.scalar(select(ProviderSecretCleanupJob)) is None


def test_admin_team_delete_defers_vault_cleanup_until_after_db_commit(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_llm_config,
    monkeypatch,
):
    admin = make_user(email="admin-delete-team-deferred-vault@example.com", password="password-1", is_system_admin=True)
    team = make_team(name="Delete Deferred Vault Clinic")
    member = make_user(email="member-deferred-vault@example.com", password="password-1", team=team, team_role=TeamRole.user)
    stt_config = make_stt_config(team=team, actor=admin, label="Deferred STT")
    llm_config = make_llm_config(team=team, actor=admin, label="Deferred LLM")

    def fail_user_cleanup(db, actor, *, user):
        raise AppError(409, "conflict", "Synthetic user cleanup failure")

    monkeypatch.setattr("app.services.admin._delete_user_rows", fail_user_cleanup)

    client.post("/login", data={"email": "admin-delete-team-deferred-vault@example.com", "password": "password-1"}, follow_redirects=False)
    blocked = client.post(f"/admin/teams/{team.id}/delete", data={"return_tab": "directory"}, follow_redirects=False)

    assert blocked.status_code == 409
    assert "Synthetic user cleanup failure" in blocked.text
    assert db_session.get(Team, team.id) is not None
    assert db_session.get(User, member.id) is not None
    assert db_session.get(TeamSttConfig, stt_config.id) is not None
    assert db_session.get(TeamLlmConfig, llm_config.id) is not None
    assert db_session.scalar(select(ProviderSecretCleanupJob)) is None


def test_import_team_assets_to_defaults_copies_latest_team_assets(db_session, make_team, make_user, make_template, make_quick_action, make_default_template):
    admin = make_user(email="admin-import-defaults@example.com", password="password-1", is_system_admin=True)
    team = make_team(name="The Range")
    leader = make_user(email="range-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    make_default_template(actor=admin, name="Simple Consult", prompt_text="Existing default prompt")
    structured_template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="  Simple Consult  ",
        description="Structured source",
        prompt_text="Old prompt",
        mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [{"section_key": "problem", "section_label": "Problem", "section_order": 1}]},
    )
    db_session.add(
        PromptTemplateVersion(
            template_id=structured_template.id,
            version_no=2,
            mode=TemplateMode.structured,
            prompt_text="Latest structured prompt",
            config_json={"profile": "emis", "sections": [{"section_key": "history", "section_label": "History", "section_order": 1}]},
            created_by_user_id=leader.id,
        )
    )
    inactive_template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Unwell Child",
        description="Inactive source",
        prompt_text="Inactive prompt",
        is_active=False,
    )
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="  Referral Letter  ",
        description="Referral source",
        prompt_text="Old quick action",
    )
    db_session.add(
        QuickActionVersion(
            quick_action_id=quick_action.id,
            version_no=2,
            mode=TemplateMode.structured,
            prompt_text="Latest quick action",
            created_by_user_id=leader.id,
        )
    )
    db_session.commit()

    summary = import_team_assets_to_defaults(db_session, admin, source_team_name="The Range")

    assert summary.templates_imported == 1
    assert summary.quick_actions_imported == 1
    skipped_existing_template = db_session.scalar(select(DefaultPromptTemplate).where(DefaultPromptTemplate.name == "Simple Consult"))
    assert skipped_existing_template is not None
    assert db_session.scalar(select(func.count()).select_from(DefaultPromptTemplate).where(DefaultPromptTemplate.name == "Simple Consult")) == 1
    imported_default_template_version = db_session.scalar(
        select(DefaultPromptTemplateVersion)
        .join(DefaultPromptTemplate, DefaultPromptTemplate.id == DefaultPromptTemplateVersion.default_template_id)
        .where(DefaultPromptTemplate.name == inactive_template.name)
    )
    assert imported_default_template_version is not None
    assert imported_default_template_version.prompt_text == "Inactive prompt"
    assert imported_default_template_version.config_json is None

    imported_inactive_template = db_session.scalar(select(DefaultPromptTemplate).where(DefaultPromptTemplate.name == inactive_template.name))
    assert imported_inactive_template is not None
    assert imported_inactive_template.is_active is False

    imported_quick_action = db_session.scalar(select(DefaultQuickAction).where(DefaultQuickAction.name == "Referral Letter"))
    assert imported_quick_action is not None
    imported_default_quick_action_version = db_session.scalar(
        select(DefaultQuickActionVersion)
        .where(DefaultQuickActionVersion.default_quick_action_id == imported_quick_action.id)
    )
    assert imported_default_quick_action_version is not None
    assert imported_default_quick_action_version.mode is TemplateMode.freeform
    assert imported_default_quick_action_version.prompt_text == "Latest quick action"

    rerun_summary = import_team_assets_to_defaults(db_session, admin, source_team_name="The Range")
    assert rerun_summary.templates_imported == 0
    assert rerun_summary.quick_actions_imported == 0


def test_admin_page_can_clear_selected_team_stt_selection(client, db_session, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=admin)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)

    page = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")
    assert "Consultation STT" in page.text
    assert "Clear" in page.text

    cleared = client.post("/admin/stt-selection/clear", data={"team_id": str(team.id)}, follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == f"/admin?team_id={team.id}&tab=providers"

    page_after = client.get(f"/admin?team_id={team.id}&team_tab=provider-policy")
    assert "Unconfigured" in page_after.text
    assert db_session.scalar(
        select(TeamSttSelection).where(
            TeamSttSelection.team_id == team.id,
            TeamSttSelection.purpose == SttSelectionPurpose.conversation,
        )
    ) is None
    assert db_session.get(TeamSttConfig, config.id) is not None


def test_admin_page_can_assign_stt_config_to_dictation_purpose(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-purpose-ui@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Dictation Ready STT")

    client.post("/login", data={"email": "admin-purpose-ui@example.com", "password": "password-1"}, follow_redirects=False)

    save = client.post(
        "/admin/stt-selection",
        data={
            "team_id": str(team.id),
            "purpose": "post_consultation_dictation",
            "stt_config_id": str(config.id),
            "provider_model": "",
            "language": "en",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&tab=providers"

    selection = db_session.scalar(
        select(TeamSttSelection).where(
            TeamSttSelection.team_id == team.id,
            TeamSttSelection.purpose == SttSelectionPurpose.post_consultation_dictation,
        )
    )
    assert selection is not None
    assert selection.stt_config_id == config.id


def test_admin_page_can_delete_selected_team_stt_config(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    delete = client.post(f"/admin/stt-configs/{config.id}/delete", data={"team_id": str(team.id)}, follow_redirects=False)

    assert delete.status_code == 303
    assert delete.headers["location"] == f"/admin?team_id={team.id}&tab=providers"
    assert db_session.get(TeamSttConfig, config.id) is None


def test_legacy_admin_stt_inspect_return_renders_canonical_admin_without_secret(
    client,
    make_team,
    make_user,
    monkeypatch,
):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.stt._list_openai_transcription_models",
        lambda **kwargs: ["gpt-4o-mini-transcribe", "whisper-1"],
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    inspect = client.post(
        "/admin/stt-configs/inspect",
        data={
            "team_id": str(team.id),
            "label": "Admin STT",
            "adapter_kind": "openai_cloud",
            "base_url": "",
            "bearer_token": "secret-token",
            "return_view": "legacy",
            "return_tab": "providers",
        },
    )

    assert inspect.status_code == 200
    assert "STT endpoint inspected" in inspect.text
    assert "OpenScribe Admin — Team Workspace" in inspect.text
    assert 'name="return_view" value="workspace"' in inspect.text
    assert "admin-tabs" not in inspect.text
    assert 'name="preserved_bearer_token" value="secret-token"' not in inspect.text
    assert "secret-token" not in inspect.text


def test_admin_page_can_save_stt_config_after_inspect_with_retyped_token(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.stt._list_openai_transcription_models",
        lambda **kwargs: ["gpt-4o-mini-transcribe", "whisper-1"],
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    inspect = client.post(
        "/admin/stt-configs/inspect",
        data={
            "team_id": str(team.id),
            "label": "Admin STT",
            "adapter_kind": "openai_cloud",
            "base_url": "",
            "bearer_token": "secret-token",
        },
    )
    assert inspect.status_code == 200

    save = client.post(
        "/admin/stt-configs",
        data={
            "team_id": str(team.id),
            "config_id": "",
            "adapter_kind": "openai_cloud",
            "label": "Admin STT",
            "base_url": "https://api.openai.com/v1",
            "transcribe_path": "/v1/audio/transcriptions",
            "file_field_name": "file",
            "response_text_path": "text",
            "return_view": "workspace",
            "return_tab": "stt",
            "preserved_bearer_token": "",
            "bearer_token": "secret-token",
            "provider_model": "gpt-4o-mini-transcribe",
            "model_field_name": "model",
            "language": "",
            "language_field_name": "language",
            "extra_form_fields_json": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&team_tab=stt"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Admin STT"


def test_admin_page_can_save_no_auth_stt_config_without_token(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin-no-auth-stt@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-no-auth-stt@example.com", "password": "password-1"}, follow_redirects=False)
    save = client.post(
        "/admin/stt-configs",
        data={
            "team_id": str(team.id),
            "config_id": "",
            "adapter_kind": "openai_compatible_rest",
            "label": "Parakeet",
            "base_url": "http://127.0.0.1:8000",
            "transcribe_path": "/v1/audio/transcriptions",
            "file_field_name": "file",
            "response_text_path": "text",
            "preserved_bearer_token": "",
            "bearer_token": "",
            "provider_model": "parakeet",
            "language": "en",
            "extra_form_fields_json": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Parakeet"
    assert saved_config.vault_secret_ref == ""


def test_admin_optional_token_form_defaults_do_not_replace_blank_credentials():
    stt_form = stt_form_defaults(
        None,
        SttInspectResult(
            base_url="http://127.0.0.1:8000",
            openapi_path=None,
            adapter_kind=SttAdapterKind.openai_compatible_rest,
            transcribe_path="/v1/audio/transcriptions",
            model_name="parakeet",
            model_field_name="model",
            file_field_name="file",
            language="en",
            language_field_name="language",
            response_text_path="text",
            segments_path=None,
            segment_text_field=None,
            segment_start_field=None,
            segment_end_field=None,
            segment_speaker_field=None,
            extra_form_fields_json={},
            candidate_paths=[],
            operation_summary=None,
            available_models=["parakeet"],
            available_model_options=[],
            field_tips=[],
            notes=[],
        ),
    )
    llm_form = llm_form_defaults(
        None,
        LlmConfigInspectResult(
            provider_preset="ollama",
            provider_display_name="Ollama",
            base_url="http://localhost:11434",
            adapter_kind=LlmAdapterKind.ollama_chat,
            model_name="llama3.2",
            available_models=["llama3.2"],
            available_model_options=[],
            discovery_status="fetched",
            default_model_source="provider",
            requires_bearer_token=False,
            supports_model_discovery=True,
        ),
    )

    assert stt_form["credential_action"] == "keep"
    assert llm_form["credential_action"] == "keep"


def test_active_admin_templates_sync_optional_provider_credential_actions():
    admin_html = Path("app/templates/admin_mockup.html").read_text()

    assert 'name="preserved_bearer_token"' not in admin_html
    assert 'bearer_token: value("bearer_token") || null' in admin_html
    assert "vault_secret_ref" not in admin_html



def test_admin_page_renders_branded_llm_provider_defaults(client, make_team, make_user):
    team = make_team(name="Clinic LLM Brand Defaults")
    make_user(email="admin-llm-brand-defaults@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-llm-brand-defaults@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm")

    assert page.status_code == 200
    assert 'action="/admin/llm-configs/drafts"' in page.text
    assert 'id="llm-provider-preset"' in page.text


def test_admin_provider_save_errors_keep_matching_provider_tab(client, make_team, make_user):
    team = make_team(name="Clinic Provider Error Tabs")
    make_user(email="admin-provider-error-tabs@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-provider-error-tabs@example.com", "password": "password-1"}, follow_redirects=False)
    stt_error = client.post(
        "/admin/stt-configs",
        data={
            "team_id": str(team.id),
            "label": "Broken STT",
            "adapter_kind": "not_an_adapter",
            "base_url": "http://127.0.0.1:7000",
            "transcribe_path": "/v1/audio/transcriptions",
            "file_field_name": "file",
            "response_text_path": "text",
            "return_view": "workspace",
            "return_tab": "stt",
        },
    )
    llm_error = client.post(
        "/admin/llm-configs",
        data={
            "team_id": str(team.id),
            "label": "Broken LLM",
            "adapter_kind": "not_an_adapter",
            "base_url": "https://llm.example.com",
            "return_view": "workspace",
            "return_tab": "llm",
        },
    )

    assert stt_error.status_code == 400
    assert re.search(r'data-panel="stt"\s*>', stt_error.text)
    assert llm_error.status_code == 400
    assert re.search(r'data-panel="llm"\s*>', llm_error.text)


def test_admin_page_can_run_saved_stt_test_and_render_result(client, make_team, make_user, make_stt_config, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT")

    monkeypatch.setattr(
        "app.main.run_saved_stt_config_test_service",
        lambda db, actor, config_id, team_id: {
            "success": True,
            "health_status": "skipped",
            "sample_filename": "MoreOrLess.wav",
            "sample_size_bytes": 882920,
            "health_url": None,
            "transcribe_url": "http://127.0.0.1:7000/v1/audio/transcriptions",
            "model_name": "whisper-1",
            "language": "en",
            "duration_ms": 321,
            "transcript_text": "more or less",
            "error_code": None,
            "error_message": None,
            "provider_status_code": None,
            "provider_error_code": None,
        },
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    tested = client.post(
        f"/admin/stt-configs/{config.id}/test",
        data={"team_id": str(team.id), "return_view": "workspace", "return_tab": "stt"},
    )

    assert tested.status_code == 200
    assert 'class="admin-workspace"' in tested.text
    assert "STT test completed." in tested.text
    assert "data-stt-test-result" in tested.text
    assert "STT test passed" in tested.text
    assert "Health: skipped" in tested.text
    assert "whisper-1" in tested.text


def test_admin_page_renders_saved_stt_provider_error_details(client, make_team, make_user, make_stt_config, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT")

    monkeypatch.setattr(
        "app.main.run_saved_stt_config_test_service",
        lambda db, actor, config_id, team_id: {
            "success": False,
            "health_status": "skipped",
            "sample_filename": "MoreOrLess.wav",
            "sample_size_bytes": 882920,
            "health_url": None,
            "transcribe_url": "https://api.elevenlabs.io/v1/speech-to-text",
            "model_name": "scribe_v2",
            "language": None,
            "duration_ms": 321,
            "transcript_text": None,
            "error_code": "stt_request_failed",
            "error_message": "STT provider request failed",
            "provider_status_code": 401,
            "provider_error_code": "quota_exceeded",
        },
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    tested = client.post(
        f"/admin/stt-configs/{config.id}/test",
        data={"team_id": str(team.id), "return_view": "workspace", "return_tab": "stt"},
    )

    assert tested.status_code == 200
    assert "Provider error" in tested.text
    assert "quota_exceeded" in tested.text
    assert "HTTP 401" in tested.text


def test_admin_page_includes_client_side_stt_adapter_toggle(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=stt")

    assert page.status_code == 200
    assert 'id="stt-provider-preset"' in page.text
    assert 'action="/admin/stt-configs/drafts"' in page.text


def test_admin_page_can_inspect_and_save_llm_provider_with_retyped_api_key(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_openai_chat_models",
        lambda **kwargs: ["gpt-4o-mini", "gpt-4.1-mini"],
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    draft = client.post(
        "/admin/llm-configs/drafts",
        data={
            "team_id": str(team.id),
            "provider_preset": "openai",
            "base_url": "",
            "bearer_token": "secret-openai-key",
        },
        follow_redirects=False,
    )

    assert draft.status_code == 303
    saved_config = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved_config is not None
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={saved_config.id}")
    assert "secret-openai-key" not in page.text
    assert f'action="/admin/llm-configs/{saved_config.id}/finalize"' in page.text

    save = client.post(
        f"/admin/llm-configs/{saved_config.id}/finalize",
        data={
            "team_id": str(team.id),
            "label": "OpenAI Team LLM",
            "provider_model": "gpt-4o-mini",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&tab=providers"


def test_admin_reinspects_persisted_gemini_when_new_gemini_setup_is_disabled(
    client, db_session, make_team, make_user, make_llm_config, monkeypatch
):
    team = make_team(name="Admin Disabled Gemini Clinic")
    admin = make_user(email="admin-disabled-gemini-ui@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Persisted Gemini",
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        base_url="https://global-aiplatform.googleapis.com",
        model_name="publishers/google/models/gemini-old",
        available_models_json=["publishers/google/models/gemini-old"],
    )
    secret = {
        "type": "service_account",
        "client_email": "saved@admin-disabled-gemini.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----ADMIN-PERSISTED-SECRET-----END PRIVATE KEY-----",
        "private_key_id": "saved-key",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    config.auth_mode = LlmAuthMode.google_service_account
    config.provider_config_json = {
        "project_id": "admin-disabled-gemini-prod",
        "location": "global",
        "api_version": "v1",
        "capacity_mode": "shared",
    }
    db_session.add(config)
    db_session.commit()
    monkeypatch.setenv("ENABLE_GEMINI_ENTERPRISE_PROVIDER", "false")
    monkeypatch.setattr("app.services.llm.read_team_llm_secret", lambda **kwargs: {"credential_json": secret})
    monkeypatch.setattr("app.services.llm.service_account_credentials_from_info", lambda value: object())
    monkeypatch.setattr(
        "app.services.llm.discover_gemini_models",
        lambda **kwargs: ["publishers/google/models/gemini-new"],
    )

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={config.id}")
    inspected = client.post(
        f"/admin/llm-configs/{config.id}/inspect",
        data={"team_id": str(team.id), "return_tab": "providers"},
    )

    assert page.status_code == 200
    assert "Persisted Gemini" in page.text
    assert 'value="gemini_enterprise"' not in page.text
    assert inspected.status_code == 200
    assert "LLM provider re-inspected using saved credential." in inspected.text
    assert "ADMIN-PERSISTED-SECRET" not in inspected.text


def test_admin_page_can_inspect_and_save_bedrock_provider_with_retyped_api_key(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bedrock")
    make_user(email="admin-bedrock@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_bedrock_chat_models",
        lambda **kwargs: ["anthropic.claude-3-7-sonnet-20250219-v1:0", "amazon.nova-micro-v1:0"],
    )

    client.post("/login", data={"email": "admin-bedrock@example.com", "password": "password-1"}, follow_redirects=False)
    draft = client.post(
        "/admin/llm-configs/drafts",
        data={
            "team_id": str(team.id),
            "provider_preset": "bedrock_http_gateway",
            "base_url": "",
            "bedrock_region": "us-east-1",
            "bearer_token": "bedrock-api-key",
        },
        follow_redirects=False,
    )

    assert draft.status_code == 303
    saved_config = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved_config is not None
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={saved_config.id}")
    assert 'name="preserved_bearer_token" value="bedrock-api-key"' not in page.text
    assert "anthropic.claude-3-7-sonnet-20250219-v1:0" in page.text
    assert "https://bedrock-mantle.us-east-1.api.aws/v1" in page.text
    assert "us-east-1" in page.text

    save = client.post(
        f"/admin/llm-configs/{saved_config.id}/finalize",
        data={
            "team_id": str(team.id),
            "label": "Amazon Bedrock",
            "provider_model": "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert saved_config.adapter_kind.value == "bedrock_chat"
    assert saved_config.base_url == "https://bedrock-mantle.us-east-1.api.aws/v1"


def test_admin_bedrock_draft_ignores_stale_localhost_base_url(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bedrock Stale URL")
    make_user(email="admin-bedrock-stale@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_bedrock_chat_models",
        lambda **kwargs: ["amazon.nova-micro-v1:0"],
    )

    client.post("/login", data={"email": "admin-bedrock-stale@example.com", "password": "password-1"}, follow_redirects=False)
    draft = client.post(
        "/admin/llm-configs/drafts",
        data={
            "team_id": str(team.id),
            "label": "Bedrock stale URL",
            "provider_preset": "bedrock_http_gateway",
            "base_url": "http://localhost:11434",
            "bedrock_region": "us-west-2",
            "bearer_token": "bedrock-api-key",
            "return_view": "workspace",
            "return_tab": "llm",
        },
        follow_redirects=False,
    )

    assert draft.status_code == 303
    saved_config = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.provider_preset == "bedrock_http_gateway"
    assert saved_config.adapter_kind == LlmAdapterKind.bedrock_chat
    assert saved_config.base_url == "https://bedrock-mantle.us-west-2.api.aws/v1"


def test_admin_page_can_inspect_and_save_local_ollama_provider_without_api_key(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_ollama_chat_models",
        lambda **kwargs: ["llama3.2", "mistral"],
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    draft = client.post(
        "/admin/llm-configs/drafts",
        data={
            "team_id": str(team.id),
            "provider_preset": "ollama",
            "base_url": "http://localhost:11434",
            "bearer_token": "",
        },
        follow_redirects=False,
    )

    assert draft.status_code == 303
    saved_config = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved_config is not None
    page = client.get(f"/admin?team_id={team.id}&team_tab=llm&llm_config_id={saved_config.id}")
    assert "llama3.2" in page.text

    save = client.post(
        f"/admin/llm-configs/{saved_config.id}/finalize",
        data={
            "team_id": str(team.id),
            "label": "Local Ollama",
            "provider_model": "llama3.2",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert saved_config.adapter_kind.value == "ollama_chat"
    assert saved_config.vault_secret_ref == ""


def test_completed_user_login_redirects_to_mfa_challenge_then_workspace(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    client.post(
        "/admin/users",
        data={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": "true",
        },
        follow_redirects=False,
    )
    client.post("/logout", follow_redirects=False)

    client.post("/login", data={"email": "managed@example.com", "password": "TempPass1"}, follow_redirects=False)
    client.post("/onboarding/password", data={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    client.post("/onboarding/totp/verify", data={"code": code})
    client.post("/onboarding/skip-recovery-codes", follow_redirects=False)
    client.post("/logout", follow_redirects=False)

    login_response = client.post(
        "/login",
        data={"email": "managed@example.com", "password": PERMANENT_TEST_PASSWORD},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/mfa/challenge"

    page = client.get("/mfa/challenge")
    assert page.status_code == 200
    assert "Enter the code from your authenticator app." in page.text
    assert "Codes refresh about every 30 seconds." in page.text
    assert "Remember this browser for 24 hours" in page.text

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"

    login_again = client.post(
        "/login",
        data={"email": "managed@example.com", "password": PERMANENT_TEST_PASSWORD},
        follow_redirects=False,
    )
    assert login_again.status_code == 303
    assert login_again.headers["location"] == "/mfa/challenge"

    verify = client.post(
        "/mfa/challenge",
        data={"code": pyotp.TOTP(start.json()["secret"]).now(), "remember_device": "true"},
        follow_redirects=False,
    )
    assert verify.status_code == 303
    assert verify.headers["location"] == "/workspace"


def test_admin_page_lists_teams_users_and_account_requests(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="lead@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_account_request(requested_name="Alice Example", requested_email="alice@example.com", requested_team_name="Clinic North")

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=members")

    assert page.status_code == 200
    assert "Clinic North" in page.text
    assert "lead@example.com" in page.text
    requests_page = client.get("/admin?tab=requests")
    assert "Account requests" in requests_page.text
    assert "alice@example.com" in requests_page.text


def test_admin_page_usage_tab_shows_team_and_user_telemetry(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Usage")
    admin = make_user(email="admin-usage-ui@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-usage-ui@example.com", password="password-2", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Usage visit",
        retention_days_applied=team.default_retention_days,
        retention_expires_at=utcnow() + timedelta(days=team.default_retention_days),
    )
    db_session.add(transcript)
    db_session.flush()

    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="visit.wav",
            status=TranscriptIngestionJobStatus.applied,
            source_audio_size_bytes=2 * 1024 * 1024,
            source_audio_duration_seconds=1800.0,
        )
    )
    db_session.add(
        ProviderUsageEvent(
            team_id=team.id,
            owner_user_id=owner.id,
            transcript_id=transcript.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed,
            provider_adapter="ollama_chat",
            model_name="clinic-model",
            prompt_tokens=80,
            completion_tokens=43,
            total_tokens=123,
        )
    )
    db_session.commit()

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=usage")

    assert page.status_code == 200
    assert "Consumption and health" in page.text
    assert "Last 30 days" in page.text
    assert "User usage breakdown" in page.text
    assert "owner-usage-ui@example.com" in page.text
    assert "123" in page.text
    assert "80" in page.text
    assert "43" in page.text
    assert "Input tokens" in page.text
    assert "Output tokens" in page.text
    assert "2.0 MB" in page.text
    assert "0.50" in page.text
    assert "Activity share" in page.text
    assert page.text.count('class="usage-echart"') == 4


def test_new_admin_usage_table_shows_metadata_only_team_comparison(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Table")
    admin = make_user(email="admin-usage-table@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="private-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Private title must stay hidden",
        retention_days_applied=team.default_retention_days,
        retention_expires_at=utcnow() + timedelta(days=team.default_retention_days),
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(ProviderUsageEvent(
        team_id=team.id,
        owner_user_id=owner.id,
        transcript_id=transcript.id,
        feature_type=ProviderFeatureType.llm_generation,
        event_type=ProviderUsageEventType.completed,
        provider_adapter="ollama_chat",
        model_name="clinic-model",
        prompt_tokens=80,
        completion_tokens=43,
        total_tokens=123,
        created_at=utcnow() - timedelta(days=15),
    ))
    db_session.commit()

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=usage")

    assert page.status_code == 200
    assert "Team comparison · Last 30 days" in page.text
    assert "vs previous equal period" in page.text
    assert '<option value="30d" selected>Last 30 days</option>' in page.text
    assert '<option value="all">All available data</option>' in page.text
    assert 'class="usage-table"' in page.text
    assert "Window comparison" in page.text
    assert "Consumption and health · Last 30 days" in page.text
    assert "Input tokens" in page.text
    assert "Output tokens" in page.text
    assert "Speech ingestion" in page.text
    assert "Failure hotspots" in page.text
    assert "Provider and activity breakdown" in page.text
    assert page.text.count('class="usage-echart"') == 4
    assert 'data-usage-chart="input"' in page.text
    assert 'data-usage-chart="output"' in page.text
    assert 'data-usage-chart="audio"' in page.text
    assert 'data-usage-chart="failure"' in page.text
    assert 'id="usage-chart-data"' in page.text
    assert "/static/vendor/echarts/6.1.0/echarts.min.js" in page.text
    assert "/static/js/admin-usage-charts.js?v=20260713-range-submit" in page.text
    assert f'/admin?team_id={team.id}&amp;team_tab=usage&amp;range=30d' in page.text
    assert "Clinic Table" in page.text
    assert "80.0 / 43.0" in page.text
    assert "Private title must stay hidden" not in page.text
    assert "private-owner@example.com" not in page.text
    assert "Origin IP" not in page.text

    chart_js = Path("app/static/js/admin-usage-charts.js").read_text()
    assert 'renderer: "svg"' in chart_js
    assert 'name: rangeLabel' in chart_js
    assert 'name: "Previous equal period"' in chart_js
    assert 'type: "slider"' in chart_js
    assert 'aria: { enabled: true }' in chart_js
    assert "ResizeObserver" in chart_js
    assert 'control.form?.requestSubmit()' in chart_js


@pytest.mark.parametrize(("range_key", "range_days"), (("30d", 30), ("90d", 90)))
def test_admin_usage_trends_include_first_partial_day(
    db_session,
    make_team,
    make_user,
    monkeypatch,
    range_key,
    range_days,
):
    team = make_team(name=f"Boundary Clinic {range_key}")
    owner = make_user(email=f"usage-boundary-{range_key}@example.com", password="password-1", team=team, team_role=TeamRole.user)
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    range_since = now - timedelta(days=range_days)
    monkeypatch.setattr("app.services.admin.utcnow", lambda: now)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Boundary usage",
        retention_days_applied=team.default_retention_days,
        retention_expires_at=utcnow() + timedelta(days=team.default_retention_days),
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add_all(
        [
            ProviderUsageEvent(
                team_id=team.id,
                owner_user_id=owner.id,
                transcript_id=transcript.id,
                feature_type=ProviderFeatureType.llm_generation,
                event_type=ProviderUsageEventType.completed,
                provider_adapter="ollama_chat",
                model_name="boundary-model",
                prompt_tokens=123,
                completion_tokens=45,
                total_tokens=168,
                created_at=range_since + timedelta(hours=1),
            ),
            ProviderUsageEvent(
                team_id=team.id,
                owner_user_id=owner.id,
                transcript_id=transcript.id,
                feature_type=ProviderFeatureType.llm_generation,
                event_type=ProviderUsageEventType.completed,
                provider_adapter="ollama_chat",
                model_name="outside-model",
                prompt_tokens=999,
                completion_tokens=999,
                total_tokens=1998,
                created_at=range_since - timedelta(seconds=1),
            ),
            make_ingestion_job_for_transcript(
                transcript,
                job_kind=TranscriptIngestionJobKind.audio_file,
                source_filename="inside.wav",
                status=TranscriptIngestionJobStatus.applied,
                source_audio_duration_seconds=3600.0,
                created_at=range_since + timedelta(hours=1),
            ),
            make_ingestion_job_for_transcript(
                transcript,
                job_kind=TranscriptIngestionJobKind.audio_file,
                source_filename="outside.wav",
                status=TranscriptIngestionJobStatus.applied,
                source_audio_duration_seconds=7200.0,
                created_at=range_since - timedelta(seconds=1),
            ),
        ]
    )
    db_session.commit()

    overview = admin_usage_overview(db_session, team_id=team.id, range_key=range_key)

    first_bucket = overview["usage_comparison_trend_points"][0]
    assert first_bucket["current_input"] == 123
    assert first_bucket["current_output"] == 45
    assert first_bucket["current_audio"] == 1.0
    assert sum(point["current_input"] for point in overview["usage_comparison_trend_points"]) == 123
    assert sum(point["current_output"] for point in overview["usage_comparison_trend_points"]) == 45
    assert sum(point["current_audio"] for point in overview["usage_comparison_trend_points"]) == 1.0
    kpi_values = {card.label: card.value for card in overview["usage_kpi_cards"]}
    assert kpi_values[f"Input tokens · Last {range_days} days"] == "123"
    assert kpi_values[f"Output tokens · Last {range_days} days"] == "45"
    assert kpi_values["Audio processed"] == "1.00h"


def test_new_admin_usage_range_all_includes_retained_historical_metadata(client, db_session, make_team, make_user):
    team = make_team(name="Historical Clinic")
    admin = make_user(email="admin-history@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="historical-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Historical confidential title",
        retention_days_applied=team.default_retention_days,
        retention_expires_at=utcnow() + timedelta(days=team.default_retention_days),
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(ProviderUsageEvent(
        team_id=team.id,
        owner_user_id=owner.id,
        transcript_id=transcript.id,
        feature_type=ProviderFeatureType.llm_generation,
        event_type=ProviderUsageEventType.completed,
        provider_adapter="ollama_chat",
        model_name="historical-model",
        prompt_tokens=777,
        completion_tokens=222,
        total_tokens=999,
        created_at=utcnow() - timedelta(days=120),
    ))
    db_session.commit()

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    default_page = client.get("/admin?tab=usage")
    ninety_day_page = client.get("/admin?tab=usage&range=90d")
    yearly_page = client.get("/admin?tab=usage&range=1y")
    all_page = client.get("/admin?tab=usage&range=all")
    invalid_page = client.get("/admin?tab=usage&range=invalid")

    team_usage_link = f'/admin?team_id={team.id}&amp;team_tab=usage&amp;range='
    assert team_usage_link not in default_page.text
    assert team_usage_link not in ninety_day_page.text
    assert team_usage_link in yearly_page.text
    assert '<option value="1y" selected>Last year</option>' in yearly_page.text
    assert 'data-range-label="Last year"' in yearly_page.text
    assert team_usage_link in all_page.text
    assert '<option value="all" selected>All available data</option>' in all_page.text
    assert 'data-range-label="All available data"' in all_page.text
    assert 'data-has-comparison="false"' in all_page.text
    assert "historical-model" in all_page.text
    assert "Historical confidential title" not in all_page.text
    assert "historical-owner@example.com" not in all_page.text
    assert '<option value="30d" selected>Last 30 days</option>' in invalid_page.text


def test_new_admin_team_usage_tab_scopes_charts_and_user_table(client, db_session, make_team, make_user):
    team = make_team(name="Scoped Clinic")
    other_team = make_team(name="Other Clinic")
    admin = make_user(email="admin-team-usage@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="scoped-user@example.com", password="password-2", team=team, team_role=TeamRole.user)
    other_owner = make_user(email="other-user@example.com", password="password-3", team=other_team, team_role=TeamRole.user)

    for user, user_team, tokens, title in (
        (owner, team, 321, "Scoped confidential title"),
        (other_owner, other_team, 987, "Other confidential title"),
    ):
        transcript = Transcript(
            owner_user_id=user.id,
            team_id=user_team.id,
            title=title,
            retention_days_applied=user_team.default_retention_days,
            retention_expires_at=utcnow() + timedelta(days=user_team.default_retention_days),
        )
        db_session.add(transcript)
        db_session.flush()
        db_session.add(ProviderUsageEvent(
            team_id=user_team.id,
            owner_user_id=user.id,
            transcript_id=transcript.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed,
            provider_adapter="ollama_chat",
            model_name="scoped-model",
            prompt_tokens=tokens,
            completion_tokens=10,
            total_tokens=tokens + 10,
        ))
    db_session.commit()

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}&team_tab=usage&range=90d")

    assert page.status_code == 200
    assert 'data-panel="usage"' in page.text
    assert "Consumption and health for Scoped Clinic" in page.text
    assert "User usage breakdown" in page.text
    assert "scoped-user@example.com" in page.text
    assert "other-user@example.com" not in page.text
    assert re.search(r'"current_input"\s*:\s*321\b', page.text)
    assert not re.search(r'"current_input"\s*:\s*987\b', page.text)
    assert '<option value="90d" selected>Last 90 days</option>' in page.text
    assert f'name="team_id" value="{team.id}"' in page.text
    assert 'name="team_tab" value="usage"' in page.text
    assert page.text.count('class="usage-echart"') == 4
    assert "Scoped confidential title" not in page.text
    assert "Other confidential title" not in page.text


def test_admin_page_usage_tab_compacts_empty_daily_activity(client, make_user):
    admin = make_user(email="admin-empty-usage-ui@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=usage")

    assert page.status_code == 200
    assert "No generation or ingestion activity has been recorded for last 30 days." in page.text
    assert 'class="usage-chart__bars"' not in page.text


def test_admin_page_non_usage_tabs_skip_usage_rollups(client, monkeypatch, make_user):
    admin = make_user(email="admin-no-usage-rollup@example.com", password="password-1", is_system_admin=True)

    def fail_usage_rollup(*args, **kwargs):
        raise AssertionError("usage rollups should not run for non-usage admin tabs")

    monkeypatch.setattr("app.web.presentation.admin_usage_overview_service", fail_usage_rollup)

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=providers")

    assert page.status_code == 200
    assert 'class="panel provider-scope"' in page.text


def test_active_admin_gemini_wizard_uses_typed_google_fields_and_file_input():
    markup = Path("app/templates/admin_mockup.html").read_text()

    assert 'data-llm-provider-choice="Gemini Enterprise"' in markup
    assert 'name="google_project_id"' in markup
    assert 'name="google_location"' in markup
    assert 'list="llm-google-location-options"' in markup
    assert '<option value="europe-west2">London regional</option>' in markup
    assert "not eu-west2" in markup
    assert 'name="google_auth_method"' in markup
    assert 'id="llm-google-service-account-file" type="file"' in markup
    assert 'name="capacity_mode"' in markup
    assert "Check credentials and find models" in markup
    assert "google_service_account_json: googleCredential" in markup
    assert "external_account" not in markup
