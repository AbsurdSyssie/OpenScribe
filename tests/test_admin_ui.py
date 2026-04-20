import pytest
import pyotp
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID
from sqlalchemy import func, select

from app.errors import AppError
from app.models import (
    AccountRequest,
    DeidentificationAdapterKind,
    DefaultPromptTemplate,
    DefaultPromptTemplateVersion,
    DefaultQuickAction,
    DefaultQuickActionVersion,
    GeneratedDocument,
    GeneratedDocumentSection,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    LlmAdapterKind,
    ProviderFeatureType,
    ProviderUsageEvent,
    ProviderUsageEventType,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    Team,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    TeamLlmConfig,
    TeamLlmSelection,
    TeamRole,
    TeamSttConfig,
    SttSelectionPurpose,
    TeamSttSelection,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionJob,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    User,
    UserLlmPreference,
    UserStatus,
    utcnow,
)
from app.services.default_assets import import_team_assets_to_defaults


class FakeHttpxResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


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


def test_bootstrap_redirects_to_onboarding_and_requires_totp_setup(client):
    bootstrap_response = client.post(
        "/bootstrap/system-admin",
        data={"email": "admin@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    assert bootstrap_response.status_code == 303
    assert bootstrap_response.headers["location"] == "/onboarding"

    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "Finish setting up your account." in page.text

    start_page = client.post("/onboarding/totp/start")
    assert start_page.status_code == 200
    assert "Scan this QR code with your authenticator app." in start_page.text
    assert "data:image/svg+xml" in start_page.text

    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    verify = client.post("/onboarding/totp/verify", data={"code": code})
    assert verify.status_code == 200
    assert "Recovery codes" in verify.text


def test_non_admin_login_redirects_to_home_and_leader_sees_review_tools(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_account_request(requested_name="Alice Example", requested_email="alice@example.com", requested_team_name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login_response = client.post(
        "/login",
        data={"email": "leader@example.com", "password": "password-1"},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/home"

    home_page = client.get("/home")
    assert home_page.status_code == 200
    assert "Your OpenScribe home" in home_page.text
    assert "Open consultation notes" in home_page.text
    assert "Guide" in home_page.text
    assert 'data-tour-overlay' in home_page.text
    assert 'data-tour-scrim="top"' in home_page.text
    assert "background: var(--accent);" in home_page.text
    assert "Create a new team member" in home_page.text
    assert "Account requests" in home_page.text

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"


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
    assert "Your OpenScribe home" in response.text
    assert 'data-tab-target="ai-services"' in response.text
    assert 'data-tab-target="team-management"' in response.text
    assert "AI services" in response.text
    assert "Choose speech and writing services for your team." in response.text
    assert 'name="return_view" value="restyled"' in response.text
    assert "/home-restyled?tab=templates" in response.text


def test_leader_home_separates_ai_services_from_team_member_admin(client, make_team, make_user, make_stt_config, make_llm_config):
    team = make_team(name="Clinic Services Split")
    admin = make_user(email="services-admin@example.com", password="password-2", is_system_admin=True)
    make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_llm_config(team=team, actor=admin, label="Clinic LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_user(email="services-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "services-leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")

    assert 'data-tab-target="ai-services"' in page.text
    assert 'data-tab-panel="ai-services"' in page.text
    assert "service-section--stt" in page.text
    assert "service-section--llm" in page.text
    assert "Choose speech and writing services for your team." in page.text
    assert "Speech to text" in page.text
    assert "Writing assistant" in page.text


def test_leader_home_ai_service_modal_query_opens_inline_editor(client, make_team, make_user, make_stt_config, make_llm_config):
    team = make_team(name="Clinic Inline Services")
    admin = make_user(email="inline-services-admin@example.com", password="password-2", is_system_admin=True)
    make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_llm_config(team=team, actor=admin, label="Clinic LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_user(email="inline-services-leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "inline-services-leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home?tab=ai-services&modal=stt-settings")

    assert page.status_code == 200
    assert 'data-service-body="stt"' in page.text
    assert 'data-service-body="stt" hidden' not in page.text
    assert 'data-service-toggle="stt">Close<' in page.text


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
    assert 'data-default-tab="ai-services"' in response.text
    assert 'data-service-body="llm"' in response.text
    assert 'data-service-body="llm" hidden' not in response.text
    assert 'data-service-toggle="llm">Close<' in response.text


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


def test_invalid_browser_route_redirects_to_home_when_authenticated(client, make_team, make_user):
    team = make_team(name="Clinic Invalid Route")
    make_user(email="member-invalid-route@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-invalid-route@example.com", "password": "password-1"}, follow_redirects=False)
    response = client.get("/does-not-exist", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/home"


def test_leader_home_can_suspend_and_reactivate_team_user(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    home_page = client.get("/home")
    assert "Suspend" in home_page.text

    suspend_response = client.post(f"/home/users/{member.id}/suspend", follow_redirects=False)
    assert suspend_response.status_code == 303
    assert suspend_response.headers["location"] == "/home?tab=team-management"
    db_session.refresh(member)
    assert member.status is UserStatus.suspended

    client.get("/home")
    reactivate_response = client.post(f"/home/users/{member.id}/reactivate", follow_redirects=False)
    assert reactivate_response.status_code == 303
    assert reactivate_response.headers["location"] == "/home?tab=team-management"
    db_session.refresh(member)
    assert member.status is UserStatus.active
    assert member.must_change_password is True


def test_leader_home_can_choose_active_stt_selection_from_provisioned_endpoints(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
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
    assert save.headers["location"] == "/home?tab=team-management"
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
    assert save.headers["location"] == "/home?tab=team-management"

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
    page = client.get("/home")
    assert "Speech to text" in page.text
    assert "Clear conversation" in page.text

    cleared = client.post("/home/stt-selection/clear", follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/home?tab=team-management"

    page_after = client.get("/home")
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
    page = client.get("/home")
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
    assert save.headers["location"] == "/home?tab=team-management"
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
    page = client.get("/home")
    assert "Your writing assistant preference" in page.text
    assert "Clinic OpenAI" in page.text
    assert "Team allows:" not in page.text

    save = client.post(
        "/home/llm-preference",
        data={"preferred_model_name": "gpt-4.1-mini"},
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/home?tab=overview"
    preference = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    assert preference is not None
    assert preference.preferred_model_name == "gpt-4.1-mini"


def test_user_home_can_clear_llm_preference(client, db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic Clear Preference")
    admin = make_user(email="clear-pref-admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    user = make_user(email="clear-pref-user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini", "gpt-4.1-mini"], model_name_override="gpt-4o-mini")
    db_session.add(UserLlmPreference(user_id=user.id, preferred_model_name="gpt-4.1-mini"))
    db_session.commit()

    client.post("/login", data={"email": "clear-pref-user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")

    assert page.status_code == 200
    assert "Use team default" in page.text

    cleared = client.post("/home/llm-preference/clear", follow_redirects=False)

    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/home?tab=overview"
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
    page = client.get("/home?tab=templates")

    assert page.status_code == 200
    assert "Personal" in page.text
    assert "Enabled" in page.text
    assert 'title="Edit template"' in page.text
    assert 'title="Copy template"' in page.text
    assert 'title="Delete template"' in page.text
    assert '/home/personal-templates/' in page.text
    assert '/duplicate' in page.text
    assert '/delete' in page.text

    quick_actions_page = client.get("/home?tab=quick-actions")

    assert quick_actions_page.status_code == 200
    assert "Disabled" in quick_actions_page.text
    assert 'title="Edit quick action"' in quick_actions_page.text
    assert 'title="Copy quick action"' in quick_actions_page.text
    assert 'title="Delete quick action"' in quick_actions_page.text


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
    assert 'const COOKIE_NAME = "openscribe_csrf"' in page.text
    assert 'Problem guidance' not in page.text
    assert '>Open<' not in page.text
    assert 'Personal template' in page.text


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
    assert duplicated.headers["location"].startswith("/home/templates/editor?scope=personal")
    assert f"template_id={copy.id}" in duplicated.headers["location"]


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
    assert duplicated.headers["location"].startswith("/home/templates/editor?scope=team")
    assert f"template_id={copy.id}" in duplicated.headers["location"]



def test_user_home_can_create_personal_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    user = make_user(email="user@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
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
    page = client.get("/home")
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
    page = client.get("/home")
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
    assert f"personal_quick_action_id={copy.id}" in duplicated.headers["location"]


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
    page = client.get("/home")
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
    page = client.get("/home")
    assert "Delete" in page.text
    assert "Delete this user and all owned transcript content immediately?" in page.text

    delete_response = client.post(f"/home/users/{member.id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/home?tab=team-management"
    assert db_session.get(type(member), member.id) is None


def test_home_restyled_team_management_uses_member_menu_without_duplicate_user_table(client, make_team, make_user):
    team = make_team(name="Clinic Restyled Team Management")
    make_user(email="leader-restyled-team@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="member-restyled-team@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader-restyled-team@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home-restyled?tab=team-management")

    assert page.status_code == 200
    assert "Manage members and configuration for Clinic Restyled Team Management." in page.text
    assert "Managed users" not in page.text
    assert "Suspend" in page.text
    assert "Delete" in page.text


def test_home_page_uses_flat_sidebar_workspace_layout(client, make_team, make_user):
    team = make_team(name="Clinic Home Flat Layout")
    make_user(email="leader-home-flat@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader-home-flat@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home?tab=team-management")

    assert page.status_code == 200
    assert 'class="home-pane"' in page.text
    assert 'data-tab-nav hidden role="tablist" aria-label="Home sections"' in page.text
    assert 'data-tab-shell data-default-tab="team-management"' in page.text
    assert "Clinical workspace" in page.text
    assert "Open consultation notes" in page.text
    assert 'class="home-shell"' not in page.text
    assert 'class="home-sidebar"' not in page.text


def test_admin_restyled_preview_route_renders_for_system_admin(client, make_team, make_user):
    team = make_team(name="Clinic Admin Preview")
    admin = make_user(email="admin-preview@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-preview@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin-restyled?team_id={team.id}")

    assert page.status_code == 200
    assert "Team STT endpoints" in page.text
    assert "Provisioned endpoints" in page.text
    assert 'form method="get" action="/admin-restyled"' in page.text
    assert 'name="return_view" value="restyled"' in page.text


def test_admin_page_uses_flat_sidebar_workspace_layout(client, make_user):
    make_user(email="admin-flat-layout@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin-flat-layout@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=directory")

    assert page.status_code == 200
    assert 'class="admin-shell"' in page.text
    assert 'class="admin-sidebar"' in page.text
    assert 'class="admin-pane"' in page.text
    assert 'data-tab-nav role="tablist" aria-label="Admin sections"' in page.text
    assert 'data-tab-shell data-default-tab="directory"' in page.text
    assert "Provider setup" in page.text
    assert "Create team" in page.text
    assert "Create managed user" in page.text
    assert 'section class="hero panel"' not in page.text
    assert 'class="panel tab-shell__nav"' not in page.text
    assert "border-radius: 18px" not in page.text


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
    page = client.get(f"/admin?team_id={team.id}")

    assert page.status_code == 200
    assert 'class="model-toggle-grid"' in page.text
    assert 'class="model-toggle-card is-enabled"' in page.text
    assert 'data-llm-default-model-select' in page.text
    assert '<input type="radio" name="provider_model"' not in page.text
    assert "Default model choices come from visible models only." in page.text

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


def test_admin_restyled_stt_config_redirect_preserves_preview_route(client, db_session, make_team, make_user):
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
    assert save.headers["location"] == f"/admin-restyled?team_id={team.id}&tab=providers"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Admin STT"


def test_admin_restyled_account_request_reject_preserves_preview_route(client, db_session, make_team, make_user, make_account_request):
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
    assert rejected.headers["location"] == "/admin-restyled?tab=requests"
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

    class FakeTaskResult:
        id = "celery-task-1"

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", lambda **kwargs: FakeTaskResult())

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
    assert "Ambient Scribe" in page.text
    assert "Visit recording" in page.text

    transcript = db_session.scalar(select(Transcript).where(Transcript.title == "Visit recording"))
    assert transcript is not None
    assert transcript.ingestion_mode is TranscriptIngestionMode.whole_file
    job = db_session.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.transcript_id == transcript.id))
    assert job is not None
    assert job.celery_task_id == "celery-task-1"


def test_browser_transcribe_upload_shares_rate_limit_bucket_with_api_route(
    client,
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

    transcript_one = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=member.created_at,
    )
    transcript_two = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Visit two",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=member.created_at,
    )
    db_session.add_all([transcript_one, transcript_two])
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)

    api_upload = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-file",
        files={"audio": ("visit-one.wav", b"fake-audio-one", "audio/wav")},
    )
    assert api_upload.status_code == 202

    browser_upload = client.post(
        "/transcribe/upload",
        data={"title": "Visit two", "transcript_id": str(transcript_two.id)},
        files={"audio": ("visit-two.wav", b"fake-audio-two", "audio/wav")},
    )
    assert browser_upload.status_code == 429
    assert "Too many requests" in browser_upload.text
    assert "Return to transcription workspace" in browser_upload.text


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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    login_response = raw_client.post("/api/v1/auth/login", json={"email": "member@example.com", "password": "password-3"})
    assert login_response.status_code == 200
    page = raw_client.get("/transcribe")
    assert page.status_code == 200
    assert raw_client.cookies.get("openscribe_csrf")

    rejected = raw_client.post(
        "/transcribe/upload",
        data={"title": "Visit one", "transcript_id": str(transcript.id)},
        files={"audio": ("visit.wav", b"fake-audio", "audio/wav")},
    )

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "forbidden"
    assert rejected.json()["error"]["message"] == "CSRF verification failed"


def test_user_transcribe_page_shows_workspace_shell(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert "Ambient Scribe" in page.text
    assert 'data-new-session-button' in page.text
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
    assert 'Copy transcript' in page.text
    assert 'data-select-structured-selection' in page.text
    assert "Record" in page.text
    assert "Upload" in page.text
    assert "Guide" in page.text
    assert 'data-tour-overlay' in page.text
    assert 'data-tour-scrim="top"' in page.text
    assert 'data-tour-scrim="right"' in page.text
    assert "background: var(--accent);" in page.text
    assert "https://unpkg.com/lucide@1.8.0" in page.text
    assert 'data-lucide="mic"' in page.text
    assert 'data-lucide="upload"' in page.text
    assert "Create a transcript root first" not in page.text
    assert 'action="/transcribe/sessions/delete"' in page.text
    assert 'data-route-base="/transcribe"' in page.text
    assert 'data-workspace-stream-endpoint="' in page.text
    assert "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/ort.wasm.min.js" in page.text
    assert "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.29/dist/bundle.min.js" in page.text
    assert 'id="transcribe-bootstrap"' in page.text
    assert 'src="/static/js/transcribe/app.js"' in page.text
    assert "://medscribe.duckdns.org/static/js/transcribe/app.js" not in page.text


def test_user_transcribe_page_namespaces_legacy_note_tabs(client, make_team, make_user):
    team = make_team(name="Clinic Legacy Note Tabs")
    make_user(email="legacy-note-tabs@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "legacy-note-tabs@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe")

    assert page.status_code == 200
    assert 'data-legacy-note-workspace' in page.text
    assert 'data-note-tab-trigger="note"' in page.text
    assert 'data-note-tab-panel="note"' in page.text
    assert 'data-tab-trigger="note"' not in page.text
    assert 'data-tab-panel="note"' not in page.text


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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    make_template(scope=TemplateScope.user, owner=member, actor=member, name="My note")
    make_quick_action(scope=TemplateScope.user, owner=member, actor=member, name="My quick action")

    client.post("/login", data={"email": "member-transcribe-settings@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'href="/home"' in page.text
    assert 'data-workspace-settings-link' in page.text
    assert 'justify-between border-b border-stone bg-white px-4 gap-3' in page.text
    assert f'data-settings-url="/home/templates/editor?scope=personal&template_id=' in page.text
    assert 'return_view=transcribe' in page.text
    assert f'queued_transcript_id={transcript.id}' in page.text
    assert 'transcribe_tab=output' in page.text
    assert f'data-settings-url="/home?tab=quick-actions&modal=personal-quick-action&personal_quick_action_id=' in page.text
    assert 'transcribe_tab=followups' in page.text
    assert "Home" in page.text


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
        retention_expires_at=member.created_at,
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
    assert saved.headers["location"] == f"/transcribe?transcript_id={transcript.id}&tab=output"


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
        retention_expires_at=member.created_at,
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
    assert saved.headers["location"] == f"/transcribe?transcript_id={transcript.id}&tab=followups"


def test_user_transcribe_claude_page_uses_alternate_template(client, make_team, make_user):
    team = make_team(name="Clinic Claude UI")
    make_user(email="member-claude@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-claude@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe-claude")

    assert page.status_code == 200
    assert "LocalScribe" in page.text
    assert "Follow-ups" in page.text


def test_user_transcribe_glm_2_page_uses_alternate_template(client, make_team, make_user):
    team = make_team(name="Clinic GLM UI")
    make_user(email="member-glm@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member-glm@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get("/transcribe-glm-2")

    assert page.status_code == 200
    assert "Ambient Scribe" in page.text
    assert 'action="/transcribe/sessions/delete"' in page.text


def test_user_transcribe_glm_2_page_renders_workspace_values(
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
        retention_expires_at=member.created_at,
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
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Dynamic hypertension review" in page.text
    assert "Patient reports improved headaches." in page.text
    assert "Continue Amlodipine and review in four weeks." in page.text


def test_user_transcribe_glm_2_page_exposes_workspace_hooks_and_pane_controls(
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-hooks@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'data-workspace-endpoint="' in page.text
    assert 'data-pane-toggle="collapsed"' in page.text
    assert 'data-pane-toggle="normal"' in page.text
    assert 'data-pane-toggle="expanded"' in page.text
    assert 'divider-widget' in page.text
    assert 'data-divider-grip' in page.text
    assert 'data-tab-trigger="output"' in page.text
    assert 'data-tab-trigger="followups"' in page.text
    assert 'data-tab-trigger="history"' in page.text
    assert 'data-copy-structured-lines' in page.text
    assert 'data-structured-copy-status' in page.text
    assert 'data-latest-followup-output' in page.text
    assert 'data-generate-output-form' in page.text
    assert 'data-quick-action-context-input' in page.text
    assert 'Create note' in page.text
    assert 'Saved instructions' not in page.text
    assert 'data-selected-template-mode' not in page.text


def test_user_transcribe_glm_2_page_shows_all_emis_sections_for_structured_templates(
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "glm-structured-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'name="context_problem"' in page.text
    assert 'name="context_history"' in page.text
    assert 'name="context_family_history"' in page.text


def test_user_transcribe_glm_2_page_prioritises_latest_note_and_emis_driven_generation(client, db_session, make_team, make_user):
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-note-priority@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert page.text.index("Selected note") < page.text.index("Create a note")
    assert "Use the current consultation text, or the sectioned note details, with your chosen writing assistant." in page.text
    assert 'data-generate-output-form' in page.text
    assert 'data-structured-context-hidden' in page.text
    assert 'name="context_social_history"' in page.text
    assert 'name="context_examination"' in page.text
    assert 'name="context_comment"' in page.text
    assert 'name="context_tasks"' in page.text
    assert 'name="context_investigations"' in page.text


def test_user_transcribe_glm_2_page_shows_stt_config_label(
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "glm-stt-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert "Speech service:" in page.text
    assert "Parakeet Local" in page.text


def test_user_transcribe_glm_2_page_shows_idle_status_with_team_stt_selected(
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "glm-health-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

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
    assert created.headers["location"].startswith("/transcribe?transcript_id=")
    transcript = db_session.scalar(select(Transcript).where(Transcript.title == "Untitled session"))
    assert transcript is not None
    assert transcript.ingestion_mode is TranscriptIngestionMode.whole_file

    renamed = client.post(
        f"/transcribe/sessions/{transcript.id}/title",
        data={"title": "Renamed review"},
        follow_redirects=False,
    )
    assert renamed.status_code == 303
    assert renamed.headers["location"] == f"/transcribe?transcript_id={transcript.id}"
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
        retention_expires_at=member.created_at,
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
    assert "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.29/dist/bundle.min.js" in page.text


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
        retention_expires_at=member.created_at,
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
    assert "Please arrange a review…" in page.text
    assert "Please arrange a review appointment with the duty clinician tomorrow morning" in page.text


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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
    assert switched.headers["location"] == f"/transcribe?transcript_id={transcript.id}"
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
    )
    db_session.add(failed)
    db_session.commit()
    job = make_ingestion_job_for_transcript(
        failed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_blob=b"raw-file-audio",
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
    )
    db_session.add(failed)
    db_session.commit()
    failed_job = make_ingestion_job_for_transcript(
        failed,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_blob=b"raw-file-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
    )
    db_session.add(failed_job)
    db_session.commit()

    class FakeTaskResult:
        id = "retry-task-1"

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", lambda **kwargs: FakeTaskResult())

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
    assert latest_job.celery_task_id == "retry-task-1"
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


def test_user_transcribe_glm_2_page_allows_new_session_when_latest_has_transcript_text(client, db_session, make_team, make_user):
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-session@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'data-new-session-button' in page.text
    assert 'data-new-session-button' in page.text and 'disabled title="Finish or delete the current empty session before creating a new one"' not in page.text
    assert "Finish or delete the current empty session before creating a new one" not in page.text


def test_user_transcribe_glm_2_page_syncs_generation_controls_after_workspace_refresh(client, db_session, make_team, make_user):
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "member-glm-refresh@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe-glm-2?transcript_id={transcript.id}")

    assert page.status_code == 200
    assert 'id="transcribe-bootstrap"' in page.text
    assert "js/transcribe/app.js" in page.text
    assert 'data-generate-output-form' in page.text


def test_user_transcribe_page_can_bulk_delete_selected_sessions(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    member = make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    keep = Transcript(owner_user_id=member.id, team_id=team.id, title="Keep", ingestion_mode=TranscriptIngestionMode.whole_file, retention_days_applied=30, retention_expires_at=member.created_at)
    delete_one = Transcript(owner_user_id=member.id, team_id=team.id, title="Delete one", ingestion_mode=TranscriptIngestionMode.whole_file, retention_days_applied=30, retention_expires_at=member.created_at)
    delete_two = Transcript(owner_user_id=member.id, team_id=team.id, title="Delete two", ingestion_mode=TranscriptIngestionMode.live_chunked, retention_days_applied=30, retention_expires_at=member.created_at)
    db_session.add_all([keep, delete_one, delete_two])
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    deleted = client.post(
        "/transcribe/sessions/delete",
        data={"transcript_ids": [str(delete_one.id), str(delete_two.id)]},
        follow_redirects=False,
    )

    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/transcribe"
    assert db_session.get(Transcript, keep.id) is not None
    assert db_session.get(Transcript, delete_one.id) is None
    assert db_session.get(Transcript, delete_two.id) is None


def test_user_transcribe_upload_targets_active_session_when_selected(client, db_session, monkeypatch, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", model_name="whisper-1")
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)
    make_stt_selection(config=config, actor=leader)

    class FakeTaskResult:
        id = "celery-task-2"

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", lambda **kwargs: FakeTaskResult())

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
    assert job.celery_task_id == "celery-task-2"


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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    class FakeTaskResult:
        id = "generated-task-ui"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    generated = client.post(
        "/transcribe/generate-output",
        data={"transcript_id": str(transcript.id), "template_id": str(template.id)},
        follow_redirects=False,
    )

    assert generated.status_code == 303
    assert f"transcript_id={transcript.id}" in generated.headers["location"]
    assert "tab=output" in generated.headers["location"]

    page = client.get(generated.headers["location"])
    assert page.status_code == 200
    assert "Queued note generation." in page.text


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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "structured-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'name="context_problem"' in page.text
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
        retention_expires_at=member.created_at,
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
    assert 'data-structured-note-empty-state' in page.text
    assert 'No note lines yet' in page.text
    assert 'Select a template and start recording. Add note lines here as the consultation unfolds.' in page.text


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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "followups-structured-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=followups")

    assert page.status_code == 200
    assert 'data-run-quick-action-trigger' in page.text
    assert 'data-quick-action-select' in page.text
    assert 'data-quick-action-context-input' in page.text
    assert 'data-followup-prompt-input' in page.text
    assert 'data-run-quick-action-trigger\ndisabled' not in page.text
    assert 'data-quick-action-select\nclass="followup-select"\n>' in page.text


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
        retention_expires_at=member.created_at,
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
    assert 'data-quick-action-context-input></textarea>' in page.text


def test_transcribe_frontend_uses_global_template_selector_for_generation_controls():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")
    actions_js = (root / "app" / "static" / "js" / "transcribe" / "actions.js").read_text(encoding="utf-8")
    structured_js = (root / "app" / "static" / "js" / "transcribe" / "structured.js").read_text(encoding="utf-8")
    media_js = (root / "app" / "static" / "js" / "transcribe" / "media.js").read_text(encoding="utf-8")
    workspace_html = (root / "app" / "templates" / "transcribe" / "_workspace.html").read_text(encoding="utf-8")
    sidebar_html = (root / "app" / "templates" / "transcribe" / "_sidebar.html").read_text(encoding="utf-8")
    head_assets = (root / "app" / "templates" / "transcribe" / "_head_assets.html").read_text(encoding="utf-8")

    assert "const generateOutputTemplateSelect = document.querySelector('[data-template-select]');" in app_js
    assert "const templateId = dom.generateOutputTemplateSelect?.value || dom.generateOutputForm.querySelector('[data-generate-template-id]')?.value || '';" in actions_js
    assert "shouldPreserveLiveMicStatus()" in app_js
    assert "captureController?.syncDisplayedDuration?.();" in app_js
    assert "Listening for speech..." in media_js
    assert "const micVisualizer = document.querySelector('[data-mic-visualizer]');" in app_js
    assert "batchVadInstance = await buildBatchVadInstance();" in media_js
    assert "Speech detected. Voice-only batch capture is running..." in media_js
    assert "microphone-batch.wav" in media_js
    assert 'data-mic-visualizer' in workspace_html
    assert "const RECORDING_DURATION_STORAGE_KEY = 'openscribe-glm2-recording-durations';" in media_js
    assert "const beginAccumulatedTimer = () => {" in media_js
    assert "const finalizeAccumulatedTimer = () => {" in media_js
    assert "syncDisplayedDuration: renderTimer," in media_js
    assert "readActiveDraftText" in app_js
    assert "document.querySelectorAll('[data-legacy-note-workspace] .section-block')" in structured_js
    assert "row.className = 'statement-row';" in structured_js
    assert "textarea.className = 'statement-editor';" in structured_js
    assert "card.className = 'structured-section-block';" in structured_js
    assert "copyButton.setAttribute('data-copy-structured-section', '');" in structured_js
    assert "collectStructuredSectionLines" in structured_js
    assert "dom.generatedStructuredPanel.addEventListener('click'" in actions_js
    assert "navigator.clipboard.writeText(textToCopy);" in actions_js
    assert "const hasGeneratedNote = Boolean(dom.latestGeneratedOutput?.dataset.latestGeneratedId);" in structured_js
    assert "const templatePickerButton = document.querySelector('[data-template-picker-button]');" in app_js
    assert "const templatePickerModal = document.querySelector('[data-template-picker-modal]');" in app_js
    assert "const generatedFreeformPanel = document.querySelector('[data-generated-freeform-panel]');" in app_js
    assert "const selectStructuredSelectionButton = document.querySelector('[data-select-structured-selection]');" in app_js
    assert "const activeStatusPill = document.querySelector('[data-active-status-pill]');" in app_js
    assert "const statusLabelForRecordingProgress = (message) => {" in app_js
    assert "getIsRecordingSwitchBlocked: () => Boolean(captureController?.isLiveCaptureUiActive?.()) || currentTranscriptStatus === 'recording'," in app_js
    assert "if (getIsRecordingSwitchBlocked?.()) {" in actions_js
    assert "Stop recording before switching consultations." in actions_js
    assert "Stop recording before creating a new consultation." in actions_js
    assert "let noteEditorDirty = false;" in app_js
    assert "const markNoteEditorDirty = () => {" in app_js
    assert "const shouldPreserveNoteEditorRender = (nextSelectedNoteDocumentId = currentRenderedNoteDocumentId()) => {" in app_js
    assert "onNoteEditorChanged: markNoteEditorDirty," in app_js
    assert "const preserveDirtyNoteEditor = shouldPreserveNoteEditorRender(selectedNoteDocumentId || '');" in app_js
    assert "renderSelectedNote({ preserveEditor: preserveDirtyNoteEditor });" in app_js
    assert "if (!preserveDirtyNoteEditor) {" in app_js
    assert "const hasNoteInput = structuredEditor?.hasNoteInputContent?.() || false;" in app_js
    assert "const canRunQuickAction = Boolean(transcriptId && hasLlmSelection && (hasDraft || hasNoteInput) && hasSelectableOptions(runQuickActionSelect));" in app_js
    assert "const canGenerateFollowup = Boolean(transcriptId && hasLlmSelection && (hasDraft || hasNoteInput));" in app_js
    assert "const currentNoteUpdatedAt = () => latestGeneratedOutput?.dataset?.latestGeneratedUpdatedAt || '';" in app_js
    assert "const noteDeleteButton = document.querySelector('[data-note-delete]');" in app_js
    assert "dom.noteDeleteButton?.addEventListener('click'" in actions_js
    assert "Delete this note permanently?" in actions_js
    assert "Could not delete the note." in actions_js
    assert "const buildNoteSavePayload = () => {" in app_js
    assert "method: 'PATCH'" in app_js
    assert "keepalive," in app_js
    assert "void persistNoteEditsSilently();" in app_js
    assert "window.showToast?.(message, kind);" in app_js
    assert "flashWrap.hidden = true;" in app_js
    assert ".statement-input" in head_assets
    assert "template-picker-button" in head_assets
    assert "freeform-note-panel" in head_assets
    assert ".main-panel {" in head_assets
    assert "min-height: 100%;" in head_assets
    assert ".record-split-button {" in head_assets
    assert "overflow: visible;" in head_assets
    assert ".structured-workspace {" in head_assets
    assert "flex: 1;" in head_assets
    assert "statement-content" in workspace_html
    assert '<div class="px-4 pt-3" hidden data-flash-wrap>' in workspace_html
    assert "data-generated-freeform-panel" in workspace_html
    assert "data-latest-generated-updated-at=" in workspace_html
    assert "data-active-status-pill" in workspace_html
    assert '<div class="sr-only" data-mic-status aria-live="polite">' in workspace_html
    assert "data-new-session-block-message" not in sidebar_html
    assert "openscribe:legacy-workspace-document-selected" not in workspace_html
    assert "activateNoteTab('note')" not in workspace_html
    assert "data-generated-structured-sections" in workspace_html
    assert "data-followup-history" in workspace_html
    assert 'data-lucide="settings"' in workspace_html
    assert 'data-lucide="message-square-more"' in workspace_html
    assert 'data-lucide="sparkles"' in workspace_html
    assert '<div class="transcript-content flex-1 min-h-0 overflow-y-auto" data-active-draft>' in workspace_html
    assert "structuredEditor.renderStructuredSections(generatedStructuredDraft);" not in app_js
    assert "generatedDocument.status === 'ready' && generatedDocument.document_mode === 'freeform'" in structured_js
    assert "generatedDocument.status === 'ready' && generatedDocument.document_mode === 'freeform' && Boolean(generatedDocument.edited_output_text_encrypted)" not in structured_js
    assert "const autosizeStatementEditorsIn = (container) => {" in structured_js
    assert "window.requestAnimationFrame(() => autosizeStatementEditorsIn(dom.generatedStructuredPanel));" in structured_js
    assert "window.requestAnimationFrame(() => autosizeStatementEditorsIn(dom.generatedFreeformPanel));" in structured_js
    assert "...((generatedDocument ? generatedSectionMap.get(section.key) : structuredContext[section.key]) || [])" in structured_js
    assert "renderStructuredSections(null);" in structured_js
    assert "renderFreeformLines(null);" in structured_js
    assert "const activeGeneratedDocumentId = () => dom.latestGeneratedOutput?.dataset?.latestGeneratedId || '';" in structured_js
    assert "if (selectedOutputTemplateMode() !== 'structured' || activeGeneratedDocumentId()) {" in structured_js
    assert "generatedStructuredDraft.templateId !==" not in structured_js
    assert "generatedStructuredDraft = buildGeneratedStructuredDraftFromDom() || generatedStructuredDraft;" in structured_js
    assert "const ensureSectionHasEditableRow = (sectionContainer) => {" in structured_js
    assert "if (rows.length === 0) {" in structured_js
    assert "ensureSectionHasEmptyRow(sectionContainer);" not in structured_js
    assert "ensureFreeformHasEmptyRow();" not in structured_js
    assert "const ensureFreeformHasEditableRow = () => {" in structured_js
    assert "onNoteEditorChanged?.();" in structured_js
    assert "const hasNoteInputContent = () => {" in structured_js
    documents_js = (root / "app" / "static" / "js" / "transcribe" / "documents.js").read_text(encoding="utf-8")
    assert "const renderSelectedNote = ({ preserveEditor = false } = {}) => {" in documents_js
    assert "latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = selectedNote?.updated_at || \"\";" in documents_js
    assert "if (!preserveEditor && !shouldPreserveNoteEditorRender?.(selectedNote?.id || '')) {" in documents_js
    assert "const selectDocumentFromUi = async (kind, documentId) => {" in documents_js
    assert "const savedDocument = await persistNoteEditsSilently?.();" in documents_js
    assert "if (!savedDocument) {" in documents_js
    assert "clearNoteEditorDirty?.();" in documents_js
    assert "card.className = `followup-card${item.id === selectedId ? \" followup-card--active\" : \"\"}`;" in documents_js
    assert "followupHistory.innerHTML = `\n        <div class=\"empty-state\">" in documents_js
    assert "window.refreshLucideIcons?.(root);" in app_js
    assert "const getRecordToggleIcon = () => document.querySelector('[data-record-toggle-icon]');" in app_js
    assert "const recordToggleIcon = getRecordToggleIcon();" in app_js
    assert "recordToggleIcon.dataset.lucide = isRecording" in app_js
    assert "refreshIcons?.(followupHistory);" in documents_js
    assert 'data-lucide="trash-2"' in documents_js
    assert "No conversation text yet. Upload a recording or use the microphone to begin. The transcript will appear here as the consultation unfolds." in app_js
    assert "not active_note_input_available" in workspace_html


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
                        "inside_scroll": any(item["class"] == "flex-1 overflow-y-auto bg-parchment" for item in self.stack),
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
    transcribe_head = (root / "app" / "templates" / "transcribe" / "_head_assets.html").read_text(encoding="utf-8")
    admin_html = (root / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    login_html = (root / "app" / "templates" / "login.html").read_text(encoding="utf-8")
    onboarding_html = (root / "app" / "templates" / "onboarding.html").read_text(encoding="utf-8")
    request_access_html = (root / "app" / "templates" / "request_access.html").read_text(encoding="utf-8")
    mfa_html = (root / "app" / "templates" / "mfa_challenge.html").read_text(encoding="utf-8")

    assert "top: 24px;" in home_html
    assert "document.querySelectorAll('.flash').forEach((flash) => {" in home_html
    assert "top: 1.5rem;" in transcribe_head
    assert "position: fixed;" in admin_html and "data-toast-container" in admin_html
    assert "top: 24px;" in login_html and "data-toast-container" in login_html
    assert "top: 24px;" in onboarding_html and "data-toast-container" in onboarding_html
    assert "top:24px;" in request_access_html and "data-toast-container" in request_access_html
    assert "top:24px;" in mfa_html and "data-toast-container" in mfa_html


def test_home_overview_and_asset_cards_keep_white_fill_like_team_cards():
    root = Path(__file__).resolve().parents[1]
    home_html = (root / "app" / "templates" / "home.html").read_text(encoding="utf-8")

    assert ".overview-grid .panel {\n  background: var(--card);" in home_html
    assert ".asset-card {\n  display: grid;" in home_html
    assert "padding: 18px;\n  background: var(--card);" in home_html


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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "freeform-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert "Free text note" in page.text
    assert 'data-generated-freeform-panel' in page.text
    assert 'data-freeform-note-input' in page.text
    assert 'data-freeform-note-empty-state' in page.text
    assert 'Select a template and start recording. Add note lines here as the consultation unfolds.' in page.text


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
        retention_expires_at=member.created_at,
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
    assert "No note content yet. Select a template and start recording. Add note lines here as the consultation unfolds." in page.text
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "history-empty-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=history")

    assert page.status_code == 200
    assert "Consultation sources" in page.text
    assert "Post-consultation dictation" in page.text
    assert 'action="/transcribe/dictation/upload"' in page.text
    assert 'action="/transcribe/dictation/save"' in page.text
    assert 'data-dictation-record-toggle' in page.text
    assert "No conversation text yet. Upload recording or use microphone to begin. Transcript will appear here as consultation unfolds." in page.text


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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    client.post("/login", data={"email": "structured-persist-member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={transcript.id}&tab=output")

    assert page.status_code == 200
    assert 'name="context_problem"' in page.text
    assert "Known asthma" in page.text
    assert "Peak flow diary" in page.text


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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
    assert "Selected follow-up" in page.text
    assert "Arrange blood tests and a review if symptoms persist." in page.text
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
        retention_expires_at=member.created_at,
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
    assert 'data-note-delete' in page.text
    assert "Delete selected note permanently" in page.text
    assert "Visit summary v2" in page.text
    assert "Visit summary v1" in page.text
    assert 'data-document-kind="followup"' in page.text
    assert 'data-followup-document-select' in page.text
    assert 'data-run-quick-action-form' in page.text
    assert f'value="{quick_action.id}"' in page.text
    assert 'data-followup-copy' in page.text
    assert 'data-followup-delete' in page.text
    assert 'data-followup-copy-body' in page.text
    assert "Send patient-facing review advice." in page.text
    assert "Send SMS" in page.text


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
        retention_expires_at=member.created_at,
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
    assert 'data-quick-action-kind="sms"' in page.text
    assert 'data-quick-action-kind="letter"' in page.text
    assert 'data-quick-action-kind="call"' in page.text
    assert 'data-quick-action-kind="general"' in page.text
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
        retention_expires_at=member.created_at,
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
        retention_expires_at=member.created_at,
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
    page = client.get(f"/admin?team_id={team.id}")
    assert page.status_code == 200
    assert "Team STT endpoints" in page.text

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

    defaults_page = client.get("/admin?tab=defaults")
    assert defaults_page.status_code == 200
    assert "Default templates" in defaults_page.text
    assert "Default quick actions" in defaults_page.text
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
    llm_config = make_llm_config(team=team, actor=admin, label="Team LLM", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=llm_config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
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
    assert db_session.scalar(select(func.count()).select_from(TeamDeidentificationProviderAssignment).where(TeamDeidentificationProviderAssignment.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate).where(PromptTemplate.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(QuickAction).where(QuickAction.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(Transcript).where(Transcript.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProviderUsageEvent).where(ProviderUsageEvent.team_id == team.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProviderUsageEvent).where(ProviderUsageEvent.owner_user_id.in_([leader.id, member.id]))) == 0
    assert db_session.get(User, leader.id) is None
    assert db_session.get(User, member.id) is None


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

    deleted_secret_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.admin.delete_team_stt_bearer_token",
        lambda *, team_id, config_id: deleted_secret_calls.append(f"stt:{config_id}"),
    )
    monkeypatch.setattr(
        "app.services.admin.delete_team_llm_bearer_token",
        lambda *, team_id, config_id: deleted_secret_calls.append(f"llm:{config_id}"),
    )

    client.post("/login", data={"email": "admin-delete-team-preflight@example.com", "password": "password-1"}, follow_redirects=False)
    blocked = client.post(f"/admin/teams/{team.id}/delete", data={"return_tab": "directory"}, follow_redirects=False)

    assert blocked.status_code == 409
    assert "Cannot delete a team that still contains a system-admin account" in blocked.text
    assert db_session.get(Team, team.id) is not None
    assert db_session.get(TeamSttConfig, stt_config.id) is not None
    assert db_session.get(TeamLlmConfig, llm_config.id) is not None
    assert deleted_secret_calls == []


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

    deleted_secret_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.admin.delete_team_stt_bearer_token",
        lambda *, team_id, config_id: deleted_secret_calls.append(f"stt:{config_id}"),
    )
    monkeypatch.setattr(
        "app.services.admin.delete_team_llm_bearer_token",
        lambda *, team_id, config_id: deleted_secret_calls.append(f"llm:{config_id}"),
    )

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
    assert deleted_secret_calls == []


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

    page = client.get(f"/admin?team_id={team.id}")
    assert "Current active selections" in page.text
    assert "Clear conversation selection" in page.text

    cleared = client.post("/admin/stt-selection/clear", data={"team_id": str(team.id)}, follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == f"/admin?team_id={team.id}&tab=providers"

    page_after = client.get(f"/admin?team_id={team.id}")
    assert "Add provisioned endpoint" in page_after.text
    assert "Clear conversation selection" not in page_after.text
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


def test_admin_page_can_inspect_team_stt_config_before_saving(client, make_team, make_user, monkeypatch):
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
    assert "STT endpoint inspected" in inspect.text
    assert "openai_cloud" in inspect.text
    assert "/v1/audio/transcriptions" in inspect.text
    assert '<select name="provider_model">' in inspect.text
    assert '>gpt-4o-mini-transcribe (fetched)<' in inspect.text
    assert '>whisper-1 (fetched)<' in inspect.text
    assert "Audio file upload." in inspect.text
    assert "API key" in inspect.text
    assert 'data-show-for="generic_rest" hidden' in inspect.text
    assert 'name="preserved_bearer_token" value="secret-token"' in inspect.text


def test_admin_page_can_save_stt_config_after_inspect_without_retyping_token(client, db_session, make_team, make_user, monkeypatch):
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
            "preserved_bearer_token": "secret-token",
            "bearer_token": "",
            "provider_model": "gpt-4o-mini-transcribe",
            "language": "",
            "extra_form_fields_json": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&tab=providers"
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
        },
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    tested = client.post(f"/admin/stt-configs/{config.id}/test", data={"team_id": str(team.id)})

    assert tested.status_code == 200
    assert "STT test completed." in tested.text
    assert "Bundled STT sample test" in tested.text
    assert "MoreOrLess.wav" in tested.text
    assert "more or less" in tested.text
    assert "Test STT" in tested.text


def test_admin_page_includes_client_side_stt_adapter_toggle(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get(f"/admin?team_id={team.id}")

    assert page.status_code == 200
    assert "data-stt-adapter-select" in page.text
    assert "applyAdapterState" in page.text
    assert 'data-require-for="generic_rest openai_compatible_rest"' in page.text
    assert "data-openai-base-url" in page.text


def test_admin_page_can_inspect_and_save_llm_provider_without_retyping_api_key(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_openai_chat_models",
        lambda **kwargs: ["gpt-4o-mini", "gpt-4.1-mini"],
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    inspect = client.post(
        "/admin/llm-configs/inspect",
        data={
            "team_id": str(team.id),
            "label": "OpenAI Team LLM",
            "adapter_kind": "openai_chat",
            "base_url": "",
            "bearer_token": "secret-openai-key",
        },
    )

    assert inspect.status_code == 200
    assert 'name="preserved_bearer_token" value="secret-openai-key"' in inspect.text
    assert ">gpt-4o-mini (fetched)<" in inspect.text

    save = client.post(
        "/admin/llm-configs",
        data={
            "team_id": str(team.id),
            "config_id": "",
            "adapter_kind": "openai_chat",
            "label": "OpenAI Team LLM",
            "base_url": "https://api.openai.com/v1",
            "preserved_bearer_token": "secret-openai-key",
            "bearer_token": "",
            "provider_model": "gpt-4o-mini",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    assert save.headers["location"] == f"/admin?team_id={team.id}&tab=providers"


def test_admin_page_can_inspect_and_save_bedrock_provider_without_retyping_api_key(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bedrock")
    make_user(email="admin-bedrock@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_bedrock_chat_models",
        lambda **kwargs: ["anthropic.claude-3-7-sonnet-20250219-v1:0", "amazon.nova-micro-v1:0"],
    )

    client.post("/login", data={"email": "admin-bedrock@example.com", "password": "password-1"}, follow_redirects=False)
    inspect = client.post(
        "/admin/llm-configs/inspect",
        data={
            "team_id": str(team.id),
            "label": "Amazon Bedrock",
            "adapter_kind": "bedrock_chat",
            "base_url": "",
            "bedrock_region": "us-east-1",
            "bearer_token": "bedrock-api-key",
        },
    )

    assert inspect.status_code == 200
    assert 'name="preserved_bearer_token" value="bedrock-api-key"' in inspect.text
    assert ">anthropic.claude-3-7-sonnet-20250219-v1:0 (fetched)<" in inspect.text
    assert "https://bedrock-mantle.us-east-1.api.aws/v1" in inspect.text
    assert 'name="bedrock_region" value="us-east-1"' in inspect.text

    save = client.post(
        "/admin/llm-configs",
        data={
            "team_id": str(team.id),
            "config_id": "",
            "adapter_kind": "bedrock_chat",
            "label": "Amazon Bedrock",
            "base_url": "",
            "bedrock_region": "us-east-1",
            "preserved_bearer_token": "bedrock-api-key",
            "bearer_token": "",
            "provider_model": "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    saved_config = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.adapter_kind.value == "bedrock_chat"
    assert saved_config.base_url == "https://bedrock-mantle.us-east-1.api.aws/v1"


def test_admin_page_can_inspect_and_save_local_ollama_provider_without_api_key(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_ollama_chat_models",
        lambda **kwargs: ["llama3.2", "mistral"],
    )

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    inspect = client.post(
        "/admin/llm-configs/inspect",
        data={
            "team_id": str(team.id),
            "label": "Local Ollama",
            "adapter_kind": "ollama_chat",
            "base_url": "http://localhost:11434",
            "bearer_token": "",
        },
    )

    assert inspect.status_code == 200
    assert ">llama3.2 (fetched)<" in inspect.text

    save = client.post(
        "/admin/llm-configs",
        data={
            "team_id": str(team.id),
            "config_id": "",
            "adapter_kind": "ollama_chat",
            "label": "Local Ollama",
            "base_url": "http://localhost:11434",
            "preserved_bearer_token": "",
            "bearer_token": "",
            "provider_model": "llama3.2",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert save.status_code == 303
    saved_config = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.adapter_kind.value == "ollama_chat"
    assert saved_config.vault_secret_ref == ""


def test_completed_user_login_redirects_to_mfa_challenge_then_home(client, make_team, make_user):
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
    client.post("/onboarding/password", data={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    client.post("/onboarding/totp/verify", data={"code": code})
    client.post("/onboarding/skip-recovery-codes", follow_redirects=False)
    client.post("/logout", follow_redirects=False)

    login_response = client.post(
        "/login",
        data={"email": "managed@example.com", "password": "BetterPass1"},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/mfa/challenge"

    page = client.get("/mfa/challenge")
    assert page.status_code == 200
    assert "Enter your TOTP code." in page.text
    assert "Remember this browser for 24 hours" in page.text

    verify = client.post(
        "/mfa/challenge",
        data={"code": pyotp.TOTP(start.json()["secret"]).now(), "remember_device": "true"},
        follow_redirects=False,
    )
    assert verify.status_code == 303
    assert verify.headers["location"] == "/home"


def test_admin_page_lists_teams_users_and_account_requests(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="lead@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    make_account_request(requested_name="Alice Example", requested_email="alice@example.com", requested_team_name="Clinic North")

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin")

    assert page.status_code == 200
    assert "Clinic North" in page.text
    assert "lead@example.com" in page.text
    assert "Account requests" in page.text
    assert "alice@example.com" in page.text
    assert "Delete this team, all team users, and all team-owned data immediately?" in page.text
    assert "Delete this user and all owned transcript content immediately?" in page.text


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
    page = client.get(f"/admin?tab=usage&team_id={team.id}")

    assert page.status_code == 200
    assert "Usage overview" in page.text
    assert "Last 24 hours" in page.text
    assert "Last 30 days" in page.text
    assert "Daily activity" in page.text
    assert "Team comparison, last 7 days" in page.text
    assert "Provider and model mix" in page.text
    assert "Speech ingestion mix" in page.text
    assert "User activity in Clinic Usage" in page.text
    assert "owner-usage-ui@example.com" in page.text
    assert "123" in page.text
    assert "80" in page.text
    assert "43" in page.text
    assert "Input tokens in 7 days" in page.text
    assert "Output tokens in 7 days" in page.text
    assert "Input tokens" in page.text
    assert "Output tokens" in page.text
    assert "2.0 MB" in page.text
    assert "0.50" in page.text
    assert "Share of team activity" in page.text


def test_admin_page_non_usage_tabs_skip_usage_rollups(client, monkeypatch, make_user):
    admin = make_user(email="admin-no-usage-rollup@example.com", password="password-1", is_system_admin=True)

    def fail_usage_rollup(*args, **kwargs):
        raise AssertionError("usage rollups should not run for non-usage admin tabs")

    monkeypatch.setattr("app.web.presentation.admin_usage_overview_service", fail_usage_rollup)

    client.post("/login", data={"email": admin.email, "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin?tab=providers")

    assert page.status_code == 200
    assert "Usage overview" in page.text
