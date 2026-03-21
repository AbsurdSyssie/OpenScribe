import pyotp
from uuid import UUID
from sqlalchemy import func, select

from app.models import (
    LlmAdapterKind,
    PromptTemplate,
    QuickAction,
    TeamLlmConfig,
    TeamLlmSelection,
    TeamRole,
    TeamSttConfig,
    TeamSttSelection,
    TemplateScope,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionMode,
    TranscriptStatus,
    UserLlmPreference,
    UserStatus,
)


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


def test_login_page_exposes_bootstrap_when_database_is_empty(client):
    page = client.get("/login")

    assert page.status_code == 200
    assert "Create first system admin" in page.text


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
    assert "Complete onboarding before normal access." in page.text

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
    assert "Open transcription workspace" in home_page.text
    assert "Create a managed user" in home_page.text
    assert "Account requests for your team" in home_page.text

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"


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


def test_leader_home_can_suspend_and_reactivate_team_user(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    home_page = client.get("/home")
    assert "Suspend" in home_page.text

    suspend_response = client.post(f"/home/users/{member.id}/suspend", follow_redirects=False)
    assert suspend_response.status_code == 303
    assert suspend_response.headers["location"] == "/home"
    db_session.refresh(member)
    assert member.status is UserStatus.suspended

    client.get("/home")
    reactivate_response = client.post(f"/home/users/{member.id}/reactivate", follow_redirects=False)
    assert reactivate_response.status_code == 303
    assert reactivate_response.headers["location"] == "/home"
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
    assert "Team STT selection" in page.text
    assert "Provisioned endpoint" in page.text
    assert "Clinic STT" in page.text

    save = client.post(
        "/home/stt-selection",
        data={
            "stt_config_id": str(config.id),
            "provider_model": "whisper-1",
            "language": "en",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/home"
    selection = db_session.scalar(select(TeamSttSelection).where(TeamSttSelection.team_id == team.id))
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
    assert "Current active selection" in page.text
    assert "Clear active STT selection" in page.text

    cleared = client.post("/home/stt-selection/clear", follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/home"

    page_after = client.get("/home")
    assert "Choose active endpoint" in page_after.text
    assert "Current active selection" not in page_after.text
    assert db_session.scalar(select(TeamSttSelection).where(TeamSttSelection.team_id == team.id)) is None
    assert db_session.get(TeamSttConfig, config.id) is not None


def test_leader_home_can_choose_active_llm_selection_from_provisioned_providers(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, label="Clinic OpenAI", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
    assert "Team LLM selection" in page.text
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
    assert save.headers["location"] == "/home"
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
    assert "My default LLM" in page.text
    assert "Clinic OpenAI" in page.text
    assert "Available models for your team" in page.text

    save = client.post(
        "/home/llm-preference",
        data={"preferred_model_name": "gpt-4.1-mini"},
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/home"
    preference = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    assert preference is not None
    assert preference.preferred_model_name == "gpt-4.1-mini"


def test_leader_home_can_create_team_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
    assert "Team templates" in page.text

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


def test_user_home_can_create_personal_template(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    user = make_user(email="user@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "user@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
    assert "Personal templates" in page.text

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


def test_leader_home_can_create_team_quick_action(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Quick Action Team")
    make_user(email="leader-quick-action@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    client.post("/login", data={"email": "leader-quick-action@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
    assert "Team quick actions" in page.text

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


def test_user_home_can_create_personal_quick_action(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Quick Action Personal")
    user = make_user(email="user-quick-action@example.com", password="password-1", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "user-quick-action@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/home")
    assert "Personal quick actions" in page.text

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
    assert "Delete permanently" in page.text

    delete_response = client.post(f"/home/users/{member.id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/home"
    assert db_session.get(type(member), member.id) is None


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
    assert "Transcription workspace" in page.text
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
    assert "Transcription workspace" in page.text
    assert "New session" in page.text
    assert "Microphone batch" in page.text
    assert "Audio input" in page.text
    assert "Upload from file" in page.text
    assert "Create or select a session to begin." in page.text
    assert "Create mic batch session" not in page.text
    assert "Generated note output" in page.text
    assert "Follow-ups" in page.text
    assert "Create a transcript root first" not in page.text


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


def test_user_transcribe_page_can_switch_blank_live_session_to_whole_file(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="member@example.com", password="password-3", team=team, team_role=TeamRole.user)

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    created = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "live_chunked"},
        follow_redirects=False,
    )
    transcript_id = created.headers["location"].split("transcript_id=", 1)[1]

    switched = client.post(
        f"/transcribe/sessions/{transcript_id}/mode",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=False,
    )

    assert switched.status_code == 303
    assert switched.headers["location"] == f"/transcribe?transcript_id={transcript_id}"
    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript is not None
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
    db_session.commit()

    client.post("/login", data={"email": "member@example.com", "password": "password-3"}, follow_redirects=False)
    page = client.get(f"/transcribe?transcript_id={queued.id}")

    assert page.status_code == 200
    assert "Background transcription is in progress." in page.text


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

    blocked = client.post(
        "/transcribe/sessions",
        data={"ingestion_mode": "whole_file"},
        follow_redirects=True,
    )
    assert blocked.status_code == 409
    assert "Finish or delete the current empty session before creating a new one" in blocked.text
    assert db_session.scalar(select(func.count(Transcript.id))) == 1


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
    assert "Latest output:" in page.text
    assert "My note output" in page.text
    assert "queued" in page.text


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
    assert "Latest follow-up:" in page.text
    assert "Arrange blood tests and a review if symptoms persist." in page.text
    assert "queued" in page.text


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
        data={"transcript_id": str(transcript.id), "quick_action_id": str(quick_action.id)},
        follow_redirects=False,
    )

    assert generated.status_code == 303
    assert f"transcript_id={transcript.id}" in generated.headers["location"]
    assert "tab=followups" in generated.headers["location"]

    page = client.get(generated.headers["location"])
    assert page.status_code == 200
    assert "Queued quick action generation." in page.text
    assert "Arrange review" in page.text
    assert "queued" in page.text


def test_admin_page_marks_current_account_protected(client, make_user):
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="member@example.com", password="password-2")

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    page = client.get("/admin")

    assert page.status_code == 200
    assert "Protected" in page.text
    assert "Delete permanently" in page.text


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
    assert save.headers["location"] == f"/admin?team_id={team.id}"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Admin STT"


def test_admin_page_can_clear_selected_team_stt_selection(client, db_session, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=admin)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)

    page = client.get(f"/admin?team_id={team.id}")
    assert "Current active selection" in page.text
    assert "Clear active STT selection" in page.text

    cleared = client.post("/admin/stt-selection/clear", data={"team_id": str(team.id)}, follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == f"/admin?team_id={team.id}"

    page_after = client.get(f"/admin?team_id={team.id}")
    assert "Add provisioned endpoint" in page_after.text
    assert "Current active selection" not in page_after.text
    assert db_session.scalar(select(TeamSttSelection).where(TeamSttSelection.team_id == team.id)) is None
    assert db_session.get(TeamSttConfig, config.id) is not None


def test_admin_page_can_delete_selected_team_stt_config(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)

    client.post("/login", data={"email": "admin@example.com", "password": "password-1"}, follow_redirects=False)
    delete = client.post(f"/admin/stt-configs/{config.id}/delete", data={"team_id": str(team.id)}, follow_redirects=False)

    assert delete.status_code == 303
    assert delete.headers["location"] == f"/admin?team_id={team.id}"
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
    assert save.headers["location"] == f"/admin?team_id={team.id}"
    saved_config = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert saved_config is not None
    assert saved_config.label == "Admin STT"


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
    assert save.headers["location"] == f"/admin?team_id={team.id}"


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
