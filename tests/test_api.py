import io
from datetime import timedelta
from uuid import UUID
import wave

import httpx
import pyotp
from sqlalchemy import select

from app.models import (
    AccountRequest,
    AccountRequestStatus,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    LlmAdapterKind,
    LlmAuthMode,
    ProviderUsageEvent,
    ProviderUsageEventType,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    RedactionRun,
    SttAdapterKind,
    SttAuthMode,
    TeamLlmConfig,
    TeamLlmSelection,
    TeamRole,
    TeamSttConfig,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptStatus,
    Transcript,
    TranscriptIngestionMode,
    TranscriptVersion,
    TemplateMode,
    User,
    UserLlmPreference,
    UserRecoveryCode,
    UserSession,
    UserStatus,
    UserTrustedDevice,
    TemplateScope,
)
from app.schemas.templates import GenerateFollowupRequest
from app.services.audio import NormalizedAudio
from app.services.redaction import reidentify_text as redaction_reidentify_text
from app.services.stt import transcribe_with_team_stt
from app.services.templates import (
    process_generated_document,
    queue_document_generation_from_template as queue_document_generation_from_template_service,
    queue_followup_generation,
    queue_quick_action_generation,
)
from app.services.transcripts import process_transcript_ingestion_job


def assert_error(response, *, status_code: int, code: str, message: str):
    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    return body["error"].get("details")


def login(client, *, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def finish_onboarding(client):
    start = client.post("/api/v1/onboarding/totp/start")
    assert start.status_code == 200
    assert start.json()["qr_code_svg_data_uri"].startswith("data:image/svg+xml")
    code = pyotp.TOTP(start.json()["secret"]).now()
    verify = client.post("/api/v1/onboarding/totp/verify", json={"code": code})
    assert verify.status_code == 200
    skip = client.post("/api/v1/onboarding/skip-recovery-codes")
    assert skip.status_code == 200
    return skip


def complete_mfa_challenge(client, secret: str, *, remember_device: bool = False):
    code = pyotp.TOTP(secret).now()
    return client.post("/api/v1/auth/mfa/totp", json={"code": code, "remember_device": remember_device})


def make_test_wav_bytes(*, duration_seconds: float, sample_rate: int = 16000) -> bytes:
    frame_count = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


class FakeHttpxResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


STT_OPENAPI_DOCUMENT = {
    "openapi": "3.1.0",
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
            }
        },
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
                                    "chunk_mode": {"type": "string", "default": "memory", "description": "Chunk handling mode."},
                                },
                                "required": ["file"],
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
        },
    },
}


def test_healthcheck(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_login_is_rate_limited_after_repeated_attempts(client, make_user, caplog):
    make_user(email="member@example.com", password="password-1")
    caplog.set_level("WARNING", logger="openscribe.security")

    responses = [
        client.post(
            "/api/v1/auth/login",
            json={"email": "member@example.com", "password": f"wrong-pass-{attempt}"},
        )
        for attempt in range(6)
    ]

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert_error(responses[5], status_code=429, code="rate_limited", message="Too many requests")
    assert any(
        record.name == "openscribe.security"
        and record.msg == "rate_limit_exceeded"
        and getattr(record, "path", None) == "/api/v1/auth/login"
        for record in caplog.records
    )


def test_dev_seed_account_api_login_is_restricted_to_localhost(client, make_user):
    make_user(email="dev.user@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    local = client.post("/api/v1/auth/login", json={"email": "dev.user@example.com", "password": "password-1"})
    assert local.status_code == 200
    assert local.json()["authenticated"] is True

    remote = client.post(
        "/api/v1/auth/login",
        json={"email": "dev.user@example.com", "password": "password-1"},
        headers={"host": "192.168.1.77:8080", "origin": "http://192.168.1.77:8080"},
    )
    assert_error(remote, status_code=403, code="forbidden", message="Dev test accounts are available only from localhost")


def test_dev_seed_account_session_is_revoked_on_non_local_request(client, make_user):
    make_user(email="dev.user@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    login_response = client.post("/api/v1/auth/login", json={"email": "dev.user@example.com", "password": "password-1"})
    assert login_response.status_code == 200

    remote_me = client.get(
        "/api/v1/auth/me",
        headers={"host": "192.168.1.77:8080"},
    )
    assert_error(remote_me, status_code=401, code="unauthorized", message="Authentication required")

    local_me = client.get("/api/v1/auth/me")
    assert_error(local_me, status_code=401, code="unauthorized", message="Authentication required")


def test_public_account_request_submission_is_rate_limited(client):
    responses = []
    for attempt in range(4):
        responses.append(
            client.post(
                "/api/v1/account-requests",
                json={
                    "requested_name": f"Alice Example {attempt}",
                    "requested_email": f"alice{attempt}@example.com",
                    "requested_team_name": "Clinic North",
                    "request_details": "Need access",
                },
            )
        )

    assert [response.status_code for response in responses[:3]] == [201, 201, 201]
    assert_error(responses[3], status_code=429, code="rate_limited", message="Too many requests")


def test_public_account_request_submission_and_duplicate_rules(client, make_user):
    first = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Alice Example",
            "requested_email": "alice@example.com",
            "requested_team_name": "Clinic North",
            "request_details": "Need access",
        },
    )
    duplicate = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Alice Example",
            "requested_email": "ALICE@example.com",
            "requested_team_name": " clinic   north ",
            "request_details": "Need access again",
        },
    )

    assert first.status_code == 201
    assert first.json()["requested_email"] == "alice@example.com"
    assert_error(duplicate, status_code=409, code="conflict", message="Account request already exists")

    make_user(email="alice@example.com", password="password-1", is_system_admin=True)
    existing_user = client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Alice Example",
            "requested_email": "alice@example.com",
            "requested_team_name": "Clinic North",
        },
    )
    assert_error(existing_user, status_code=409, code="conflict", message="User already exists")


def test_direct_managed_user_creation_sets_temp_password_onboarding_state(client, db_session, make_team, make_user):
    team = make_team(name="Ops Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")

    response = client.post(
        "/api/v1/users",
        json={
            "full_name": "Ops Lead",
            "email": "lead@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "leader",
            "status": "active",
            "mfa_required": True,
        },
    )
    duplicate = client.post(
        "/api/v1/users",
        json={
            "full_name": "Ops Lead",
            "email": "LEAD@example.com",
            "temporary_password": "TempPass2",
            "team_id": str(team.id),
            "team_role": "leader",
            "status": "active",
            "mfa_required": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["must_change_password"] is True
    assert response.json()["onboarding_state"] == "pending_password_change"
    persisted_user = db_session.get(User, UUID(response.json()["id"]))
    assert persisted_user is not None
    assert persisted_user.password_hash != "TempPass1"
    assert persisted_user.password_hash.startswith("scrypt$")
    assert_error(duplicate, status_code=409, code="conflict", message="User already exists")


def test_leader_can_review_only_own_team_requests_and_approve_them(client, make_team, make_user, make_account_request):
    team = make_team(name="Clinic North")
    other_team = make_team(name="Clinic South")
    leader = make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_account_request(requested_name="North Request", requested_email="north@example.com", requested_team_name="Clinic North")
    south_request = make_account_request(requested_name="South Request", requested_email="south@example.com", requested_team_name="Clinic South")

    login(client, email="leader@example.com", password="password-1")
    list_response = client.get("/api/v1/account-requests")
    assert list_response.status_code == 200
    assert [item["requested_email"] for item in list_response.json()] == ["north@example.com"]

    forbidden = client.post(
        f"/api/v1/account-requests/{south_request.id}/approve",
        json={"temporary_password": "TempPass1", "team_role": "user"},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Account-request review access required")

    approved = client.post(
        f"/api/v1/account-requests/{list_response.json()[0]['id']}/approve",
        json={"temporary_password": "TempPass1", "team_role": "user", "review_notes": "Looks valid"},
    )
    assert approved.status_code == 200
    assert approved.json()["team_id"] == str(team.id)


def test_system_admin_can_provision_and_read_team_stt_configs_without_secret_reveal(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    login(client, email="admin@example.com", password="password-1")
    created = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(team.id),
            "label": "Clinic STT",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:7000",
            "bearer_token": "super-secret-token",
            "model_name": "whisper-1",
            "language": "en",
            "extra_form_fields_json": {"chunk_mode": "memory"},
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["team_id"] == str(team.id)
    assert body["has_secret"] is True
    assert body["adapter_kind"] == "openai_compatible_rest"
    assert "vault_secret_ref" not in body
    assert body["available_models_json"] == []

    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.id == UUID(body["id"])))
    assert persisted is not None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/stt/team/")

    listed = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert listed.status_code == 200
    assert [item["label"] for item in listed.json()] == ["Clinic STT"]

    fetched = client.get(f"/api/v1/stt-configs/{body['id']}?team_id={team.id}")
    assert fetched.status_code == 200
    assert fetched.json()["label"] == "Clinic STT"
    assert "super-secret-token" not in fetched.text


def test_system_admin_can_delete_provisioned_stt_config_without_leaking_secret(client, db_session, make_team, make_user, make_stt_config, make_stt_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=admin)

    login(client, email="admin@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/stt-configs/{config.id}?team_id={team.id}")
    assert deleted.status_code == 204

    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is None

    selection = client.get(f"/api/v1/stt-selection?team_id={team.id}")
    assert selection.status_code == 200
    assert selection.json() is None
    fetched = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert fetched.status_code == 200
    assert fetched.json() == []


def test_system_admin_can_inspect_stt_openapi_and_get_prefilled_fields(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    monkeypatch.setattr("app.services.stt.httpx.get", lambda *args, **kwargs: FakeHttpxResponse(STT_OPENAPI_DOCUMENT))

    login(client, email="admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/stt-configs/inspect",
        json={
            "team_id": str(team.id),
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:7000",
            "openapi_path": "/openapi.json",
            "bearer_token": "inspect-token",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["adapter_kind"] == "openai_compatible_rest"
    assert body["transcribe_path"] == "/v1/audio/transcriptions"
    assert body["model_name"] == "whisper-1"
    assert body["file_field_name"] == "file"
    assert body["language"] == "en"
    assert body["response_text_path"] == "text"
    assert body["extra_form_fields_json"] == {"chunk_mode": "memory"}
    assert any(tip["name"] == "file" and tip["required"] is True for tip in body["field_tips"])
    assert any(tip["name"] == "chunk_mode" and tip["description"] == "Chunk handling mode." for tip in body["field_tips"])
    assert any("OpenAI-compatible REST" in note for note in body["notes"])
    assert "inspect-token" not in inspected.text


def test_system_admin_can_inspect_openai_cloud_without_openapi_fetch(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.stt._list_openai_transcription_models",
        lambda **kwargs: ["gpt-4o-mini-transcribe", "whisper-1"],
    )

    login(client, email="admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/stt-configs/inspect",
        json={
            "team_id": str(team.id),
            "adapter_kind": "openai_cloud",
            "bearer_token": "secret-token",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["adapter_kind"] == "openai_cloud"
    assert body["base_url"] == "https://api.openai.com/v1"
    assert body["transcribe_path"] == "/v1/audio/transcriptions"
    assert body["model_name"] == "gpt-4o-mini-transcribe"
    assert body["available_models"] == ["gpt-4o-mini-transcribe", "whisper-1"]
    assert body["available_model_options"] == [
        {"id": "gpt-4o-mini-transcribe", "source": "fetched", "label": "gpt-4o-mini-transcribe (fetched)"},
        {"id": "whisper-1", "source": "fetched", "label": "whisper-1 (fetched)"},
    ]
    assert body["file_field_name"] == "file"
    assert body["response_text_path"] == "text"
    assert any("OpenAI Python SDK" in note for note in body["notes"])


def test_openai_cloud_inspection_falls_back_to_built_in_models_when_sdk_lookup_fails(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    def raise_lookup_failure(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.stt._list_openai_transcription_models", raise_lookup_failure)

    login(client, email="admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/stt-configs/inspect",
        json={
            "team_id": str(team.id),
            "adapter_kind": "openai_cloud",
            "bearer_token": "secret-token",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["adapter_kind"] == "openai_cloud"
    assert body["available_models"] == [
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe",
        "gpt-4o-transcribe-diarize",
        "whisper-1",
    ]
    assert body["available_model_options"] == [
        {"id": "gpt-4o-mini-transcribe", "source": "default", "label": "gpt-4o-mini-transcribe (default)"},
        {"id": "gpt-4o-transcribe", "source": "default", "label": "gpt-4o-transcribe (default)"},
        {"id": "gpt-4o-transcribe-diarize", "source": "default", "label": "gpt-4o-transcribe-diarize (default)"},
        {"id": "whisper-1", "source": "default", "label": "whisper-1 (default)"},
    ]
    assert body["model_name"] == "gpt-4o-mini-transcribe"
    assert any("fell back to the built-in supported transcription model list" in note for note in body["notes"])


def test_stt_routes_require_admin_provisioning_or_leader_selection_scope_and_full_auth(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)
    make_user(email="admin@example.com", password="password-3", is_system_admin=True)

    unauth = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert_error(unauth, status_code=401, code="unauthorized", message="Authentication required")
    unauth_inspect = client.post("/api/v1/stt-configs/inspect", json={"team_id": str(team.id), "base_url": "http://127.0.0.1:7000"})
    assert_error(unauth_inspect, status_code=401, code="unauthorized", message="Authentication required")
    unauth_options = client.get("/api/v1/stt-selection/options")
    assert_error(unauth_options, status_code=401, code="unauthorized", message="Authentication required")
    unauth_clear = client.delete("/api/v1/stt-selection")
    assert_error(unauth_clear, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="member@example.com", password="password-2")
    forbidden = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="System admin access required")
    forbidden_inspect = client.post("/api/v1/stt-configs/inspect", json={"team_id": str(team.id), "base_url": "http://127.0.0.1:7000"})
    assert_error(forbidden_inspect, status_code=403, code="forbidden", message="System admin access required")
    forbidden_options = client.get("/api/v1/stt-selection/options")
    assert_error(forbidden_options, status_code=403, code="forbidden", message="STT selection access required")
    forbidden_clear = client.delete("/api/v1/stt-selection")
    assert_error(forbidden_clear, status_code=403, code="forbidden", message="STT selection access required")
    client.post("/api/v1/auth/logout")

    client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password-3"},
    )
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )
    client.post("/api/v1/auth/logout")

    onboarding_login = login(client, email="managed@example.com", password="TempPass1")
    assert onboarding_login.status_code == 200
    onboarding = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert_error(onboarding, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")
    onboarding_inspect = client.post("/api/v1/stt-configs/inspect", json={"team_id": str(team.id), "base_url": "http://127.0.0.1:7000"})
    assert_error(onboarding_inspect, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")
    onboarding_options = client.get("/api/v1/stt-selection/options")
    assert_error(onboarding_options, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")
    onboarding_clear = client.delete("/api/v1/stt-selection")
    assert_error(onboarding_clear, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    mfa_login = login(client, email="managed@example.com", password="BetterPass1")
    assert mfa_login.status_code == 200
    assert mfa_login.json()["auth_level"] == "pending_mfa"
    pending_mfa = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert_error(pending_mfa, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")
    pending_mfa_inspect = client.post("/api/v1/stt-configs/inspect", json={"team_id": str(team.id), "base_url": "http://127.0.0.1:7000"})
    assert_error(pending_mfa_inspect, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")
    pending_mfa_options = client.get("/api/v1/stt-selection/options")
    assert_error(pending_mfa_options, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")
    pending_mfa_clear = client.delete("/api/v1/stt-selection")
    assert_error(pending_mfa_clear, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")


def test_stt_config_validates_urls_and_enforces_admin_provisioning_plus_leader_team_selection_scope(
    client, make_team, make_user, make_stt_config
):
    north = make_team(name="Clinic North")
    south = make_team(name="Clinic South")
    make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    make_user(email="leader@example.com", password="password-1", team=north, team_role=TeamRole.leader)

    login(client, email="admin@example.com", password="password-2")

    bad_remote = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(north.id),
            "label": "Bad STT",
            "base_url": "http://stt.example.com",
            "model_name": "whisper-1",
            "bearer_token": "token-1",
            "adapter_kind": "openai_compatible_rest",
            "is_active": True,
        },
    )
    assert bad_remote.status_code == 422

    bad_public_172 = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(north.id),
            "label": "Bad 172 STT",
            "base_url": "http://172.5.1.2:7000",
            "model_name": "whisper-1",
            "bearer_token": "token-1",
            "adapter_kind": "openai_compatible_rest",
            "is_active": True,
        },
    )
    assert bad_public_172.status_code == 422

    allowed_private_172 = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(north.id),
            "label": "Private 172 STT",
            "base_url": "http://172.16.1.2:7000",
            "model_name": "whisper-1",
            "bearer_token": "token-1",
            "adapter_kind": "openai_compatible_rest",
            "is_active": True,
        },
    )
    assert allowed_private_172.status_code == 200

    client.post("/api/v1/auth/logout")
    login(client, email="leader@example.com", password="password-1")
    admin = make_user(email="admin-provisioner@example.com", password="password-3", is_system_admin=True)
    north_config = make_stt_config(team=north, actor=admin)
    south_config = make_stt_config(team=south, actor=admin, label="South STT")

    forbidden_provision = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(north.id),
            "label": "Leader Provision Attempt",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:7000",
            "bearer_token": "token-1",
            "model_name": "whisper-1",
        },
    )
    assert_error(forbidden_provision, status_code=403, code="forbidden", message="System admin access required")

    allowed_selection = client.post(
        "/api/v1/stt-selection",
        json={"stt_config_id": str(north_config.id), "model_name_override": "whisper-1"},
    )
    assert allowed_selection.status_code == 200

    cross_team = client.post(
        "/api/v1/stt-selection",
        json={"team_id": str(south.id), "stt_config_id": str(south_config.id)},
    )
    assert_error(cross_team, status_code=403, code="forbidden", message="Leaders may only manage STT selection for their own team")


def test_leader_can_choose_and_clear_team_stt_selection(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin, model_name="whisper-1", available_models_json=["whisper-1", "gpt-4o-mini-transcribe"])

    login(client, email="leader@example.com", password="password-2")
    options = client.get("/api/v1/stt-selection/options")
    assert options.status_code == 200
    assert [item["id"] for item in options.json()] == [str(config.id)]

    selected = client.post(
        "/api/v1/stt-selection",
        json={
            "stt_config_id": str(config.id),
            "model_name_override": "gpt-4o-mini-transcribe",
            "language_override": "en",
        },
    )
    assert selected.status_code == 200
    body = selected.json()
    assert body["stt_config_id"] == str(config.id)
    assert body["selected_config_label"] == config.label
    assert body["resolved_model_name"] == "gpt-4o-mini-transcribe"
    assert body["resolved_language"] == "en"
    assert body["available_models_json"] == ["whisper-1", "gpt-4o-mini-transcribe"]

    fetched = client.get("/api/v1/stt-selection")
    assert fetched.status_code == 200
    assert fetched.json()["stt_config_id"] == str(config.id)

    cleared = client.delete("/api/v1/stt-selection")
    assert cleared.status_code == 204
    assert client.get("/api/v1/stt-selection").json() is None
    assert db_session.get(TeamSttConfig, config.id) is not None


def test_stt_selection_rejects_non_provider_model(client, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-stt-model@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-stt-model@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin, model_name="whisper-1", available_models_json=["whisper-1"])

    login(client, email="leader-stt-model@example.com", password="password-2")
    rejected = client.post(
        "/api/v1/stt-selection",
        json={
            "stt_config_id": str(config.id),
            "model_name_override": "not-a-real-model",
        },
    )
    assert_error(
        rejected,
        status_code=422,
        code="business_rule_violation",
        message="Selected STT model is not available for this provider",
    )


def test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    login(client, email="admin@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Clinic OpenAI",
            "adapter_kind": "openai_chat",
            "base_url": "https://api.openai.com/v1",
            "bearer_token": "super-secret-token",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["team_id"] == str(team.id)
    assert body["has_secret"] is True
    assert body["adapter_kind"] == "openai_chat"
    assert "vault_secret_ref" not in body

    persisted = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == UUID(body["id"])))
    assert persisted is not None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/llm/team/")

    listed = client.get(f"/api/v1/llm-configs?team_id={team.id}")
    assert listed.status_code == 200
    assert [item["label"] for item in listed.json()] == ["Clinic OpenAI"]


def test_system_admin_can_provision_local_ollama_without_secret(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda **kwargs: ["llama3.2", "mistral"])

    login(client, email="admin@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Local Ollama",
            "adapter_kind": "ollama_chat",
            "base_url": "http://localhost:11434",
            "model_name": "llama3.2",
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["adapter_kind"] == "ollama_chat"
    assert body["has_secret"] is False

    persisted = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == UUID(body["id"])))
    assert persisted is not None
    assert persisted.auth_mode.value == "none"
    assert persisted.vault_secret_ref == ""


def test_llm_config_validates_urls_and_enforces_admin_provisioning_plus_leader_team_selection_scope(
    client, make_team, make_user, make_llm_config
):
    north = make_team(name="Clinic North")
    south = make_team(name="Clinic South")
    make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    make_user(email="leader@example.com", password="password-1", team=north, team_role=TeamRole.leader)

    login(client, email="admin@example.com", password="password-2")
    bad_remote = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(north.id),
            "label": "Bad LLM",
            "base_url": "http://llm.example.com",
            "bearer_token": "token-1",
            "model_name": "gpt-4o-mini",
        },
    )
    assert bad_remote.status_code == 422

    allowed_local = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(north.id),
            "label": "Local LLM",
            "base_url": "http://127.0.0.1:7001",
            "bearer_token": "token-1",
            "model_name": "gpt-4o-mini",
        },
    )
    assert allowed_local.status_code == 200

    client.post("/api/v1/auth/logout")
    login(client, email="leader@example.com", password="password-1")
    admin = make_user(email="admin-provisioner@example.com", password="password-3", is_system_admin=True)
    north_config = make_llm_config(team=north, actor=admin, available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    south_config = make_llm_config(team=south, actor=admin, label="South LLM")

    forbidden_provision = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(north.id),
            "label": "Leader Provision Attempt",
            "base_url": "http://127.0.0.1:7001",
            "bearer_token": "token-1",
            "model_name": "gpt-4o-mini",
        },
    )
    assert_error(forbidden_provision, status_code=403, code="forbidden", message="System admin access required")

    allowed_selection = client.post(
        "/api/v1/llm-selection",
        json={"llm_config_id": str(north_config.id), "model_name_override": "gpt-4.1-mini"},
    )
    assert allowed_selection.status_code == 200

    cross_team = client.post(
        "/api/v1/llm-selection",
        json={"team_id": str(south.id), "llm_config_id": str(south_config.id)},
    )
    assert_error(cross_team, status_code=403, code="forbidden", message="Leaders may only manage LLM selection for their own team")


def test_leader_can_choose_and_clear_team_llm_selection(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])

    login(client, email="leader@example.com", password="password-2")
    options = client.get("/api/v1/llm-selection/options")
    assert options.status_code == 200
    assert [item["id"] for item in options.json()] == [str(config.id)]

    selected = client.post(
        "/api/v1/llm-selection",
        json={
            "llm_config_id": str(config.id),
            "allowed_models_json": ["gpt-4o-mini", "gpt-4.1-mini"],
            "model_name_override": "gpt-4.1-mini",
        },
    )
    assert selected.status_code == 200
    body = selected.json()
    assert body["llm_config_id"] == str(config.id)
    assert body["resolved_model_name"] == "gpt-4.1-mini"
    assert body["allowed_models_json"] == ["gpt-4o-mini", "gpt-4.1-mini"]

    cleared = client.delete("/api/v1/llm-selection")
    assert cleared.status_code == 204
    assert client.get("/api/v1/llm-selection").json() is None
    assert db_session.get(TeamLlmConfig, config.id) is not None


def test_user_can_set_llm_preference_and_falls_back_to_team_default(client, db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    user = make_user(email="user@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini", "gpt-4.1-mini"])
    selection = make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=["gpt-4o-mini", "gpt-4.1-mini"],
        model_name_override="gpt-4o-mini",
    )

    login(client, email="user@example.com", password="password-2")
    saved = client.post("/api/v1/llm-preference", json={"preferred_model_name": "gpt-4.1-mini"})
    assert saved.status_code == 200
    body = saved.json()
    assert body["preferred_model_name"] == "gpt-4.1-mini"
    assert body["resolved_model_name"] == "gpt-4.1-mini"

    persisted = db_session.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == user.id))
    assert persisted is not None
    assert persisted.preferred_model_name == "gpt-4.1-mini"

    selection.model_name_override = "gpt-4o-mini"
    selection.allowed_models_json = ["gpt-4o-mini"]
    db_session.add(selection)
    db_session.commit()

    fetched = client.get("/api/v1/llm-preference")
    assert fetched.status_code == 200
    assert fetched.json()["preferred_model_name"] == "gpt-4.1-mini"
    assert fetched.json()["resolved_model_name"] == "gpt-4o-mini"
    assert fetched.json()["allowed_models_json"] == ["gpt-4o-mini"]

    rejected = client.post("/api/v1/llm-preference", json={"preferred_model_name": "gpt-4.1-mini"})
    assert_error(
        rejected,
        status_code=422,
        code="business_rule_violation",
        message="Preferred model is not available for the active team LLM provider",
    )


def test_leader_cannot_allow_non_provider_llm_models(client, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic LLM")
    admin = make_user(email="admin-llm-model@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-llm-model@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])

    login(client, email="leader-llm-model@example.com", password="password-2")
    rejected = client.post(
        "/api/v1/llm-selection",
        json={
            "llm_config_id": str(config.id),
            "allowed_models_json": ["gpt-4o-mini", "made-up-model"],
            "model_name_override": "gpt-4o-mini",
        },
    )
    assert_error(
        rejected,
        status_code=422,
        code="business_rule_violation",
        message="Selected allowed models are not available for this LLM provider",
    )


def test_llm_routes_require_expected_auth_scope(client, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)
    make_user(email="admin@example.com", password="password-3", is_system_admin=True)

    unauth = client.get(f"/api/v1/llm-configs?team_id={team.id}")
    assert_error(unauth, status_code=401, code="unauthorized", message="Authentication required")
    unauth_selection = client.get("/api/v1/llm-selection/options")
    assert_error(unauth_selection, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="member@example.com", password="password-2")
    forbidden = client.get(f"/api/v1/llm-configs?team_id={team.id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="System admin access required")
    forbidden_selection = client.get("/api/v1/llm-selection/options")
    assert_error(forbidden_selection, status_code=403, code="forbidden", message="LLM selection access required")


def test_team_and_personal_template_routes_enforce_scope_and_allow_generation(
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
    owner = make_user(email="owner@example.com", password="password-3", team=team, team_role=TeamRole.user)
    other = make_user(email="other@example.com", password="password-4", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, model_name_override="gpt-4o-mini")

    class FakeTaskResult:
        id = "generated-task-1"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("Generated note body", {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168, "duration_ms": 10}),
    )

    login(client, email="leader@example.com", password="password-2")
    created_team_template = client.post(
        "/api/v1/templates/team",
        json={
            "scope": "team",
            "name": "Team SOAP",
            "description": "Shared note",
            "prompt_text": "Write a concise SOAP note.",
            "is_active": True,
        },
    )
    assert created_team_template.status_code == 200
    team_template_id = created_team_template.json()["id"]
    assert created_team_template.json()["scope"] == "team"

    client.post("/api/v1/auth/logout")
    login(client, email="owner@example.com", password="password-3")
    forbidden_team_create = client.post(
        "/api/v1/templates/team",
        json={"scope": "team", "name": "Nope", "prompt_text": "Nope", "is_active": True},
    )
    assert_error(forbidden_team_create, status_code=403, code="forbidden", message="User-management access required")

    created_personal_template = client.post(
        "/api/v1/templates/personal",
        json={
            "scope": "user",
            "name": "My letter",
            "description": "Personal prompt",
            "prompt_text": "Turn the transcript into a short letter.",
            "is_active": True,
        },
    )
    assert created_personal_template.status_code == 200
    personal_template_id = created_personal_template.json()["id"]
    available = client.get("/api/v1/templates/available")
    assert available.status_code == 200
    assert {row["id"] for row in available.json()} == {team_template_id, personal_template_id}

    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Visit note", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient says symptoms improved."},
    )
    assert started.status_code == 201
    transcript_id = started.json()["id"]

    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-output",
        json={"template_id": team_template_id},
    )
    assert generated.status_code == 202
    body = generated.json()
    assert body["transcript_id"] == transcript_id
    assert body["source_template_name"] == "Team SOAP"
    assert body["status"] == "queued"
    assert body["edited_output_text_encrypted"] == ""
    assert body["model_used"] == "gpt-4o-mini"

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.celery_task_id == "generated-task-1"

    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.edited_output_text_encrypted == "Generated note body"

    generated_rows = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert generated_rows.status_code == 200
    assert len(generated_rows.json()) == 1
    assert generated_rows.json()[0]["edited_output_text_encrypted"] == "Generated note body"

    versions = list(db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id))))
    assert len(versions) == 1
    assert versions[0].text_encrypted == "Patient says symptoms improved."

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-4")
    forbidden_read = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert_error(forbidden_read, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")
    forbidden_delete = client.delete(f"/api/v1/templates/personal/{personal_template_id}")
    assert_error(forbidden_delete, status_code=404, code="not_found", message="Personal template not found")


def test_template_generation_supports_ollama_adapter(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    team = make_team(name="Clinic Ollama")
    admin = make_user(email="admin-ollama@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-ollama@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(
        team=team,
        actor=admin,
        adapter_kind=LlmAdapterKind.ollama_chat,
        base_url="http://localhost:11434",
        model_name="llama3.2",
        available_models_json=["llama3.2"],
        has_secret=False,
    )
    make_llm_selection(config=config, actor=admin, allowed_models_json=["llama3.2"], model_name_override="llama3.2")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="My note", prompt_text="Write a concise note.")

    class FakeTaskResult:
        id = "generated-task-2"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_ollama",
        lambda **kwargs: ("Ollama note body", {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50, "duration_ms": 15, "provider_duration_ms": 9}),
    )

    login(client, email="owner-ollama@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Ollama visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript draft."},
    )
    assert started.status_code == 201
    transcript_id = started.json()["id"]

    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    assert generated.json()["status"] == "queued"

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.celery_task_id == "generated-task-2"
    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.edited_output_text_encrypted == "Ollama note body"
    assert persisted_document.model_used == "llama3.2"


def test_followup_generation_queues_and_processes_with_owner_scope(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    db_session,
):
    team = make_team(name="Clinic Followup")
    admin = make_user(email="admin-followup@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-followup@example.com", password="password-2", team=team, team_role=TeamRole.user)
    other = make_user(email="other-followup@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")

    class FakeTaskResult:
        id = "generated-task-followup"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("Please arrange repeat bloods in two weeks and advise review if symptoms persist.", {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34, "duration_ms": 10}),
    )

    login(client, email="owner-followup@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Follow-up visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient reports a persistent cough for three weeks."},
    )
    transcript_id = started.json()["id"]

    queued = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-followup",
        json={"prompt_text": "Arrange repeat bloods and advise review if the cough persists."},
    )
    assert queued.status_code == 202
    assert queued.json()["generator_type"] == "followup"
    assert queued.json()["status"] == "queued"
    assert queued.json()["follow_up_prompt_text"] == "Arrange repeat bloods and advise review if the cough persists."

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.generator_type is GeneratedDocumentGeneratorType.followup
    assert persisted_document.celery_task_id == "generated-task-followup"

    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert "repeat bloods" in processed.edited_output_text_encrypted

    generated_rows = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert generated_rows.status_code == 200
    assert generated_rows.json()[0]["generator_type"] == "followup"

    client.post("/api/v1/auth/logout")
    login(client, email="other-followup@example.com", password="password-3")
    forbidden = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-followup",
        json={"prompt_text": "Do something else"},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_process_generated_document_redacts_transcript_and_reidentifies_output(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_redaction_run,
):
    team = make_team(name="Clinic Redaction")
    admin = make_user(email="admin-redaction@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-redaction@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Redaction note", prompt_text="Write a note for John Smith.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Redaction session",
        current_draft_text_encrypted="John Smith reports headaches.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    transcript_version = db_session.get(TranscriptVersion, document.transcript_version_id)
    assert transcript_version is not None
    run = make_redaction_run(
        transcript=transcript,
        transcript_version=transcript_version,
        owner=owner,
        redacted_text="[PHI-1] reports headaches.",
        entities=[(1, "PERSON", "John Smith")],
    )

    monkeypatch.setattr(
        "app.services.templates.ensure_redaction_run_for_transcript_version",
        lambda db, *, transcript_version: run,
    )

    def fake_redact_transient_text(text: str, *, start_index: int):
        return {
            "redacted_text": "Write a note for [PHI-2].",
            "phi_mapping": {"phi-2": {"type": "PERSON", "value": "John Smith"}},
            "phi_index": [{"index": 2, "type": "PERSON", "value": "John Smith", "placeholder": "[PHI-2]"}],
            "phi_count": 1,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        }

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    monkeypatch.setattr("app.services.templates.reidentify_text", redaction_reidentify_text)

    def fake_generate(**kwargs):
        assert "[PHI-1] reports headaches." in kwargs["user_message"]
        assert "John Smith reports headaches." not in kwargs["user_message"]
        assert "Write a note for [PHI-2]." in kwargs["user_message"]
        return "[PHI-1] should rest and [PHI-2] should book review.", {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12, "duration_ms": 9}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.redaction_run_id == run.id
    assert processed.edited_output_text_encrypted == "John Smith should rest and John Smith should book review."


def test_process_generated_document_fails_on_invalid_placeholder_output(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_redaction_run,
):
    team = make_team(name="Clinic Placeholder")
    admin = make_user(email="admin-placeholder@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-placeholder@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Placeholder note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Placeholder session",
        current_draft_text_encrypted="John Smith reports headaches.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    transcript_version = db_session.get(TranscriptVersion, document.transcript_version_id)
    assert transcript_version is not None
    run = make_redaction_run(
        transcript=transcript,
        transcript_version=transcript_version,
        owner=owner,
        redacted_text="[PHI-1] reports headaches.",
        entities=[(1, "PERSON", "John Smith")],
    )
    monkeypatch.setattr(
        "app.services.templates.ensure_redaction_run_for_transcript_version",
        lambda db, *, transcript_version: run,
    )
    monkeypatch.setattr("app.services.templates.reidentify_text", redaction_reidentify_text)
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("[PHI-999] should rest.", {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.failed
    assert processed.error_code == "redaction_placeholder_invalid"
    assert processed.error_message == "Generated output contained an unknown PHI placeholder"


def test_local_dev_account_can_read_generated_document_redaction_debug(
    client,
    db_session,
    make_team,
    make_user,
    make_redaction_run,
):
    team = make_team(name="Clinic Dev Redaction")
    owner = make_user(email="dev.user@example.com", password="password-1", team=team, team_role=TeamRole.user, mfa_required=False, mfa_enabled=False)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Debug session",
        current_draft_text_encrypted="John Smith attended the clinic.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
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
        owner=owner,
        redacted_text="[PHI-1] attended the clinic.",
        entities=[(1, "PERSON", "John Smith")],
    )
    document = GeneratedDocument(
        owner_user_id=owner.id,
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

    login(client, email="dev.user@example.com", password="password-1")
    response = client.get(f"/api/v1/generated-documents/{document.id}/redaction-debug")

    assert response.status_code == 200
    body = response.json()
    assert body["generated_document_id"] == str(document.id)
    assert body["redaction_run_id"] == str(run.id)
    assert body["redacted_text"] == "[PHI-1] attended the clinic."
    assert body["entity_count"] == 1
    assert body["entities"] == [
        {
            "entity_order": 1,
            "entity_type": "PERSON",
            "placeholder": "[PHI-1]",
            "occurrence_count": 1,
        }
    ]


def test_non_dev_account_cannot_read_generated_document_redaction_debug(
    client,
    db_session,
    make_team,
    make_user,
    make_redaction_run,
):
    team = make_team(name="Clinic Non Dev Redaction")
    owner = make_user(email="owner-debug@example.com", password="password-1", team=team, team_role=TeamRole.user, mfa_required=False, mfa_enabled=False)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Debug session",
        current_draft_text_encrypted="Jane Smith attended the clinic.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Jane Smith attended the clinic.",
    )
    db_session.add(transcript_version)
    db_session.commit()
    run = make_redaction_run(
        transcript=transcript,
        transcript_version=transcript_version,
        owner=owner,
        redacted_text="[PHI-1] attended the clinic.",
        entities=[(1, "PERSON", "Jane Smith")],
    )
    document = GeneratedDocument(
        owner_user_id=owner.id,
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

    login(client, email="owner-debug@example.com", password="password-1")
    response = client.get(f"/api/v1/generated-documents/{document.id}/redaction-debug")

    assert_error(
        response,
        status_code=403,
        code="forbidden",
        message="Redaction debug is available only to localhost dev test accounts",
    )


def test_team_and_personal_quick_action_routes_enforce_scope_and_allow_generation(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Clinic Quick Actions")
    admin = make_user(email="admin-quick-action@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader-quick-action@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    owner = make_user(email="owner-quick-action@example.com", password="password-3", team=team, team_role=TeamRole.user)
    other = make_user(email="other-quick-action@example.com", password="password-4", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=leader, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")

    class FakeTaskResult:
        id = "generated-task-quick-action"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("Please arrange repeat bloods in one week and ask the patient to book a GP review if symptoms persist.", {"prompt_tokens": 21, "completion_tokens": 33, "total_tokens": 54, "duration_ms": 10}),
    )

    login(client, email="leader-quick-action@example.com", password="password-2")
    created_team_quick_action = client.post(
        "/api/v1/quick-actions/team",
        json={
            "scope": "team",
            "name": "Arrange repeat bloods",
            "description": "Shared quick action",
            "prompt_text": "Write a short follow-up arranging repeat bloods and advising review if symptoms persist.",
            "is_active": True,
        },
    )
    assert created_team_quick_action.status_code == 200
    team_quick_action_id = created_team_quick_action.json()["id"]
    assert created_team_quick_action.json()["scope"] == "team"

    client.post("/api/v1/auth/logout")
    login(client, email="owner-quick-action@example.com", password="password-3")
    created_personal_quick_action = client.post(
        "/api/v1/quick-actions/personal",
        json={
            "scope": "user",
            "name": "Send physio advice",
            "description": "Personal quick action",
            "prompt_text": "Write a short follow-up recommending physiotherapy exercises and review if pain worsens.",
            "is_active": True,
        },
    )
    assert created_personal_quick_action.status_code == 200
    personal_quick_action_id = created_personal_quick_action.json()["id"]

    available = client.get("/api/v1/quick-actions/available")
    assert available.status_code == 200
    assert {row["id"] for row in available.json()} == {team_quick_action_id, personal_quick_action_id}

    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Quick action visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient has ongoing fatigue and needs repeat bloods."},
    )
    transcript_id = started.json()["id"]

    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/run-quick-action",
        json={"quick_action_id": team_quick_action_id},
    )
    assert generated.status_code == 202
    body = generated.json()
    assert body["generator_type"] == "quick_action"
    assert body["source_quick_action_name"] == "Arrange repeat bloods"
    assert body["status"] == "queued"

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.generator_type is GeneratedDocumentGeneratorType.quick_action
    assert persisted_document.quick_action_version_id is not None
    assert persisted_document.celery_task_id == "generated-task-quick-action"

    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert "repeat bloods" in processed.edited_output_text_encrypted

    generated_rows = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert generated_rows.status_code == 200
    assert generated_rows.json()[0]["generator_type"] == "quick_action"
    assert generated_rows.json()[0]["source_quick_action_name"] == "Arrange repeat bloods"

    client.post("/api/v1/auth/logout")
    login(client, email="other-quick-action@example.com", password="password-4")
    forbidden = client.post(
        f"/api/v1/transcripts/{transcript_id}/run-quick-action",
        json={"quick_action_id": team_quick_action_id},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_generated_document_keeps_prompt_snapshot_after_template_delete(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Snapshot")
    admin = make_user(email="admin-template-snapshot@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-template-snapshot@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Snapshot note", prompt_text="Write a concise note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Snapshot session",
        current_draft_text_encrypted="Patient reports mild improvement.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("Generated note body", {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    from app.services.templates import delete_personal_template as delete_personal_template_service

    delete_personal_template_service(db_session, owner, template_id=template.id)
    refreshed = db_session.get(GeneratedDocument, document.id)
    assert refreshed is not None
    assert refreshed.template_version_id is None
    assert refreshed.prompt_snapshot_text == "Write a concise note."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.edited_output_text_encrypted == "Generated note body"


def test_generated_document_keeps_prompt_snapshot_after_quick_action_delete(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Quick Snapshot")
    admin = make_user(email="admin-quick-snapshot@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-quick-snapshot@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    quick_action = make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="SMS", prompt_text="Write a short SMS update.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Quick action session",
        current_draft_text_encrypted="Patient asks for a text update.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    document = queue_quick_action_generation(db_session, owner, transcript_id=transcript.id, quick_action_id=quick_action.id)
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("SMS body", {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10, "duration_ms": 8}),
    )

    from app.services.templates import delete_personal_quick_action as delete_personal_quick_action_service

    delete_personal_quick_action_service(db_session, owner, quick_action_id=quick_action.id)
    refreshed = db_session.get(GeneratedDocument, document.id)
    assert refreshed is not None
    assert refreshed.quick_action_version_id is None
    assert refreshed.prompt_snapshot_text == "Write a short SMS update."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.edited_output_text_encrypted == "SMS body"


def test_llm_config_cannot_be_changed_while_generated_documents_are_in_flight(client, db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template):
    team = make_team(name="Clinic Locked LLM")
    admin = make_user(email="admin-llm-lock@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-llm-lock@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Locked note", prompt_text="Write a concise note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Locked session",
        current_draft_text_encrypted="Patient reports improvement.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)

    login(client, email="admin-llm-lock@example.com", password="password-1")
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Updated LLM",
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "auth_mode": config.auth_mode.value,
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    assert_error(
        updated,
        status_code=409,
        code="conflict",
        message="Cannot edit this LLM config while generated documents are queued or processing",
    )

    deleted = client.delete(f"/api/v1/llm-configs/{config.id}?team_id={team.id}")
    assert_error(
        deleted,
        status_code=409,
        code="conflict",
        message="Cannot delete this LLM config while generated documents are queued or processing",
    )


def test_generate_output_is_rate_limited_per_authenticated_user(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Rate Limit")
    admin = make_user(email="admin-rate@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-rate@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="My note", prompt_text="Write a concise note.")

    class FakeTaskResult:
        id = "generated-task-rate"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    login(client, email="owner-rate@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Rate limited visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript draft."},
    )
    transcript_id = started.json()["id"]

    first = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    second = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})

    assert first.status_code == 202
    assert_error(second, status_code=429, code="rate_limited", message="Too many requests")


def test_process_generated_document_logs_usage_metadata(
    db_session,
    monkeypatch,
    caplog,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Usage")
    admin = make_user(email="admin-usage@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-usage@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Usage note", prompt_text="Write a concise note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Usage session",
        current_draft_text_encrypted="Patient says symptoms improved.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ("Generated note body", {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168, "duration_ms": 10}),
    )

    caplog.set_level("INFO", logger="openscribe.usage")
    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.input_token_count == 123
    assert processed.output_token_count == 45
    assert processed.total_token_count == 168
    assert processed.duration_ms == 10
    completed = [record for record in caplog.records if record.name == "openscribe.usage" and record.msg == "llm_generation_completed"]
    assert completed
    record = completed[-1]
    assert record.model_used == "gpt-4o-mini"
    assert record.prompt_tokens == 123
    assert record.completion_tokens == 45
    assert record.total_tokens == 168
    usage_events = list(
        db_session.scalars(
            select(ProviderUsageEvent)
            .where(ProviderUsageEvent.generated_document_id == document.id)
            .order_by(ProviderUsageEvent.created_at.asc())
        )
    )
    assert [event.event_type for event in usage_events] == [
        ProviderUsageEventType.queued,
        ProviderUsageEventType.started,
        ProviderUsageEventType.completed,
    ]
    assert usage_events[-1].owner_user_id == owner.id
    assert usage_events[-1].team_id == team.id
    assert usage_events[-1].prompt_tokens == 123
    assert usage_events[-1].completion_tokens == 45
    assert usage_events[-1].total_tokens == 168
    assert usage_events[-1].duration_ms == 10


def test_process_generated_document_persists_provider_failure_details(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Failure")
    admin = make_user(email="admin-failure@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-failure@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(
        team=team,
        actor=admin,
        adapter_kind=LlmAdapterKind.ollama_chat,
        base_url="http://localhost:11434",
        model_name="missing-model",
        available_models_json=["missing-model"],
        has_secret=False,
    )
    make_llm_selection(config=config, actor=admin, allowed_models_json=["missing-model"], model_name_override="missing-model")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Failure note", prompt_text="Write a concise note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Failure session",
        current_draft_text_encrypted="Patient says symptoms improved.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)

    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", "http://localhost:11434/api/chat")
        response = httpx.Response(
            404,
            request=request,
            headers={"content-type": "application/json"},
            content=b'{"error":"model \\"missing-model\\" not found"}',
        )
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr("app.services.templates.httpx.post", fake_post)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.failed
    assert processed.error_code == "llm_generation_failed"
    assert processed.error_message == "The selected model is not available on the LLM provider"
    assert processed.provider_http_status == 404
    assert processed.provider_error_code == 'model "missing-model" not found'
    failure_events = list(
        db_session.scalars(
            select(ProviderUsageEvent)
            .where(ProviderUsageEvent.generated_document_id == document.id)
            .order_by(ProviderUsageEvent.created_at.asc())
        )
    )
    assert [event.event_type for event in failure_events] == [
        ProviderUsageEventType.queued,
        ProviderUsageEventType.started,
        ProviderUsageEventType.failed,
    ]
    assert failure_events[-1].error_code == "llm_generation_failed"
    assert failure_events[-1].provider_http_status == 404
    assert failure_events[-1].provider_error_code == 'model "missing-model" not found'


def test_leader_can_suspend_and_reactivate_own_team_user(client, make_team, make_user):
    team = make_team(name="Clinic North")
    leader = make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="leader@example.com", password="password-1")

    suspended = client.post(f"/api/v1/users/{member.id}/suspend")
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    client.post("/api/v1/auth/logout")
    blocked_login = login(client, email="member@example.com", password="password-2")
    assert_error(blocked_login, status_code=403, code="forbidden", message="User account is not active")

    login(client, email="leader@example.com", password="password-1")
    reactivated = client.post(f"/api/v1/users/{member.id}/reactivate")
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    assert reactivated.json()["must_change_password"] is True
    assert reactivated.json()["onboarding_state"] == "pending_password_change"
    assert reactivated.json()["mfa_enabled"] is False

    client.post("/api/v1/auth/logout")
    resumed_login = login(client, email="member@example.com", password="password-2")
    assert resumed_login.status_code == 200
    assert resumed_login.json()["auth_level"] == "onboarding"
    assert resumed_login.json()["redirect_to"] == "/onboarding"


def test_manager_account_routes_require_authentication(client, make_team, make_user):
    team = make_team(name="Clinic North")
    member = make_user(email="member@example.com", password="password-1", team=team, team_role=TeamRole.user)

    suspend = client.post(f"/api/v1/users/{member.id}/suspend")
    reactivate = client.post(f"/api/v1/users/{member.id}/reactivate")
    delete = client.delete(f"/api/v1/users/{member.id}")

    assert_error(suspend, status_code=401, code="unauthorized", message="Authentication required")
    assert_error(reactivate, status_code=401, code="unauthorized", message="Authentication required")
    assert_error(delete, status_code=401, code="unauthorized", message="Authentication required")


def test_non_manager_cannot_suspend_reactivate_or_delete_users(client, make_team, make_user):
    team = make_team(name="Clinic North")
    acting_user = make_user(email="user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    target_user = make_user(email="target@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="user@example.com", password="password-1")

    suspend = client.post(f"/api/v1/users/{target_user.id}/suspend")
    reactivate = client.post(f"/api/v1/users/{target_user.id}/reactivate")
    delete = client.delete(f"/api/v1/users/{target_user.id}")

    assert_error(suspend, status_code=403, code="forbidden", message="User-management access required")
    assert_error(reactivate, status_code=403, code="forbidden", message="User-management access required")
    assert_error(delete, status_code=403, code="forbidden", message="User-management access required")


def test_onboarding_and_pending_mfa_sessions_cannot_use_manager_account_routes(client, make_team, make_user):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )
    client.post("/api/v1/auth/logout")

    onboarding_login = login(client, email="managed@example.com", password="TempPass1")
    assert onboarding_login.status_code == 200

    onboarding_suspend = client.post(f"/api/v1/users/{member.id}/suspend")
    onboarding_reactivate = client.post(f"/api/v1/users/{member.id}/reactivate")
    onboarding_delete = client.delete(f"/api/v1/users/{member.id}")

    assert_error(onboarding_suspend, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")
    assert_error(onboarding_reactivate, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")
    assert_error(onboarding_delete, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")

    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    mfa_login = login(client, email="managed@example.com", password="BetterPass1")
    assert mfa_login.status_code == 200
    assert mfa_login.json()["auth_level"] == "pending_mfa"

    mfa_suspend = client.post(f"/api/v1/users/{member.id}/suspend")
    mfa_reactivate = client.post(f"/api/v1/users/{member.id}/reactivate")
    mfa_delete = client.delete(f"/api/v1/users/{member.id}")

    assert_error(mfa_suspend, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")
    assert_error(mfa_reactivate, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")
    assert_error(mfa_delete, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")


def test_leader_cannot_suspend_cross_team_user_or_system_admin(client, make_team, make_user):
    north = make_team(name="Clinic North")
    south = make_team(name="Clinic South")
    make_user(email="root@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader@example.com", password="password-2", team=north, team_role=TeamRole.leader)
    south_user = make_user(email="south@example.com", password="password-3", team=south, team_role=TeamRole.user)
    admin_user = make_user(email="platform@example.com", password="password-4", is_system_admin=True)

    login(client, email="leader@example.com", password="password-2")

    cross_team = client.post(f"/api/v1/users/{south_user.id}/suspend")
    assert_error(cross_team, status_code=403, code="forbidden", message="Leaders may only manage users in their own team")

    sysadmin = client.post(f"/api/v1/users/{admin_user.id}/suspend")
    assert_error(sysadmin, status_code=403, code="forbidden", message="Leaders may not manage system-admin accounts")


def test_suspending_a_user_revokes_active_sessions_immediately(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    user = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="member@example.com", password="password-2")
    assert client.get("/api/v1/auth/me").status_code == 200

    login(client, email="admin@example.com", password="password-1")
    suspend_response = client.post(f"/api/v1/users/{user.id}/suspend")
    assert suspend_response.status_code == 200

    client.post("/api/v1/auth/logout")
    blocked_login = login(client, email="member@example.com", password="password-2")
    assert_error(blocked_login, status_code=403, code="forbidden", message="User account is not active")

    sessions = list(db_session.scalars(select(UserSession).where(UserSession.user_id == user.id)))
    assert sessions
    assert all(session.status.value == "revoked" for session in sessions)


def test_cannot_suspend_last_active_system_admin_or_self(client, make_user):
    primary_admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    login(client, email="admin@example.com", password="password-1")

    self_suspend = client.post(f"/api/v1/users/{primary_admin.id}/suspend")
    assert_error(self_suspend, status_code=403, code="forbidden", message="You may not manage your own account")

    second_admin = make_user(email="second@example.com", password="password-2", is_system_admin=True)
    suspend_second = client.post(f"/api/v1/users/{second_admin.id}/suspend")
    assert suspend_second.status_code == 200


def test_leader_can_delete_own_team_user_and_owned_transcripts(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    leader = make_user(email="leader@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member@example.com", password="password-2", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Visit note",
        current_draft_text_encrypted="draft",
        status="ready",
        retention_days_applied=14,
        retention_expires_at=team.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.refresh(transcript)
    db_session.add(TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="v1"))
    db_session.commit()

    account_request = AccountRequest(
        requested_name="Member",
        requested_email=member.email,
        requested_team_name=team.name,
        requested_team_name_key=team.name_key,
        status=AccountRequestStatus.approved,
        linked_user_id=member.id,
        reviewed_by_user_id=leader.id,
    )
    db_session.add(account_request)
    db_session.commit()
    db_session.refresh(account_request)

    login(client, email="leader@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/users/{member.id}")
    assert deleted.status_code == 204

    assert db_session.get(User, member.id) is None
    assert db_session.get(Transcript, transcript.id) is None
    versions = list(db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id)))
    assert versions == []
    refreshed_request = db_session.get(AccountRequest, account_request.id)
    assert refreshed_request is not None
    assert refreshed_request.linked_user_id is None
    assert refreshed_request.reviewed_by_user_id == leader.id


def test_leader_cannot_delete_cross_team_user_or_system_admin(client, make_team, make_user):
    north = make_team(name="Clinic North")
    south = make_team(name="Clinic South")
    leader = make_user(email="leader@example.com", password="password-1", team=north, team_role=TeamRole.leader)
    south_user = make_user(email="south@example.com", password="password-2", team=south, team_role=TeamRole.user)
    admin_user = make_user(email="platform@example.com", password="password-3", is_system_admin=True)

    login(client, email="leader@example.com", password="password-1")

    cross_team = client.delete(f"/api/v1/users/{south_user.id}")
    assert_error(cross_team, status_code=403, code="forbidden", message="Leaders may only manage users in their own team")

    sysadmin = client.delete(f"/api/v1/users/{admin_user.id}")
    assert_error(sysadmin, status_code=403, code="forbidden", message="Leaders may not manage system-admin accounts")


def test_admin_can_delete_other_admin_but_not_last_active_system_admin(client, make_user):
    primary_admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    secondary_admin = make_user(email="second@example.com", password="password-2", is_system_admin=True)

    login(client, email="admin@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/users/{secondary_admin.id}")
    assert deleted.status_code == 204

    last_active = client.delete(f"/api/v1/users/{primary_admin.id}")
    assert_error(last_active, status_code=403, code="forbidden", message="You may not manage your own account")


def test_temp_password_login_creates_onboarding_only_session_until_completion(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    create_response = client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )
    assert create_response.status_code == 201

    client.post("/api/v1/auth/logout")
    login_response = login(client, email="managed@example.com", password="TempPass1")
    assert login_response.status_code == 200
    assert login_response.json()["auth_level"] == "onboarding"
    assert login_response.json()["redirect_to"] == "/onboarding"

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["onboarding_state"] == "pending_password_change"

    blocked = client.get("/api/v1/users")
    assert_error(blocked, status_code=403, code="onboarding_incomplete", message="Complete onboarding before accessing this route")

    password_change = client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    assert password_change.status_code == 200
    assert password_change.json()["onboarding_state"] == "pending_totp_enrollment"

    finish = finish_onboarding(client)
    assert finish.json()["auth_level"] == "full"

    allowed = client.get("/api/v1/auth/me")
    assert allowed.status_code == 200
    assert allowed.json()["auth_level"] == "full"

    sessions = list(db_session.scalars(select(UserSession)))
    assert any(session.status.value == "revoked" for session in sessions)
    assert any(session.status.value == "active" and session.auth_level.value == "full" for session in sessions)


def test_completed_user_requires_mfa_on_next_login_without_trusted_device(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )

    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    client.post("/api/v1/onboarding/totp/verify", json={"code": code})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    second_login = login(client, email="managed@example.com", password="BetterPass1")
    assert second_login.status_code == 200
    assert second_login.json()["auth_level"] == "pending_mfa"
    assert second_login.json()["redirect_to"] == "/mfa/challenge"

    blocked = client.get("/api/v1/users")
    assert_error(blocked, status_code=403, code="mfa_required", message="Complete TOTP verification before accessing this route")

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["auth_level"] == "pending_mfa"

    challenge = complete_mfa_challenge(client, start.json()["secret"])
    assert challenge.status_code == 200
    assert challenge.json()["auth_level"] == "full"
    assert challenge.json()["redirect_to"] == "/home"

    assert client.get("/api/v1/auth/me").json()["auth_level"] == "full"


def test_mfa_challenge_is_rate_limited_after_repeated_invalid_codes(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )

    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="BetterPass1")

    responses = [
        client.post("/api/v1/auth/mfa/totp", json={"code": "000000", "remember_device": False})
        for _ in range(11)
    ]

    assert [response.status_code for response in responses[:10]] == [422] * 10
    assert_error(responses[10], status_code=429, code="rate_limited", message="Too many requests")


def test_remembered_device_skips_mfa_within_freshness_window(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )

    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    second_login = login(client, email="managed@example.com", password="BetterPass1")
    assert second_login.json()["auth_level"] == "pending_mfa"
    challenge = complete_mfa_challenge(client, secret, remember_device=True)
    assert challenge.status_code == 200

    stored_devices = list(db_session.scalars(select(UserTrustedDevice)))
    assert len(stored_devices) == 1
    assert stored_devices[0].device_token_hash

    client.post("/api/v1/auth/logout")
    third_login = login(client, email="managed@example.com", password="BetterPass1")
    assert third_login.status_code == 200
    assert third_login.json()["auth_level"] == "full"
    assert third_login.json()["redirect_to"] == "/home"


def test_expired_trusted_device_requires_mfa_again(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="BetterPass1")
    complete_mfa_challenge(client, secret, remember_device=True)
    client.post("/api/v1/auth/logout")

    device = db_session.scalar(select(UserTrustedDevice))
    assert device is not None
    device.last_mfa_verified_at = device.last_mfa_verified_at - timedelta(days=2)
    db_session.add(device)
    db_session.commit()

    relogin = login(client, email="managed@example.com", password="BetterPass1")
    assert relogin.status_code == 200
    assert relogin.json()["auth_level"] == "pending_mfa"


def test_locking_a_user_revokes_trusted_devices_immediately(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="BetterPass1")
    complete_mfa_challenge(client, secret, remember_device=True)
    user = db_session.scalar(select(User).where(User.email == "managed@example.com"))
    user.status = UserStatus.locked
    db_session.add(user)
    db_session.commit()

    revoked = client.get("/api/v1/auth/me")
    assert_error(revoked, status_code=401, code="unauthorized", message="Authentication required")
    devices = list(db_session.scalars(select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id)))
    assert devices
    assert all(device.revoked_at is not None for device in devices)


def test_recovery_code_generation_hashes_codes_and_unlocks_full_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )

    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    client.post("/api/v1/onboarding/totp/verify", json={"code": code})
    recovery = client.post("/api/v1/onboarding/recovery-codes")

    assert recovery.status_code == 200
    assert len(recovery.json()["codes"]) == 8

    stored = list(db_session.scalars(select(UserRecoveryCode)))
    assert len(stored) == 8
    assert all(item.code_hash not in recovery.json()["codes"] for item in stored)
    assert client.get("/api/v1/auth/me").json()["auth_level"] == "full"


def test_totp_start_returns_qr_code_svg_data_uri(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="admin@example.com", password="password-1")
    client.post(
        "/api/v1/users",
        json={
            "full_name": "Managed User",
            "email": "managed@example.com",
            "temporary_password": "TempPass1",
            "team_id": str(team.id),
            "team_role": "user",
            "status": "active",
            "mfa_required": True,
        },
    )

    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password="TempPass1")
    client.post("/api/v1/onboarding/password", json={"new_password": "BetterPass1"})
    start = client.post("/api/v1/onboarding/totp/start")

    assert start.status_code == 200
    assert start.json()["provisioning_uri"].startswith("otpauth://totp/")
    assert start.json()["qr_code_svg_data_uri"].startswith("data:image/svg+xml")


def test_locking_a_user_revokes_active_sessions_immediately(client, db_session, make_user):
    user = make_user(email="member@example.com", password="password-1")
    login(client, email="member@example.com", password="password-1")
    assert client.get("/api/v1/auth/me").status_code == 200

    user.status = UserStatus.locked
    db_session.add(user)
    db_session.commit()

    revoked = client.get("/api/v1/auth/me")
    assert_error(revoked, status_code=401, code="unauthorized", message="Authentication required")
    sessions = list(db_session.scalars(select(UserSession).where(UserSession.user_id == user.id)))
    assert sessions
    assert all(session.status.value == "revoked" for session in sessions)


def test_transcript_routes_require_full_auth_and_preserve_owner_only_access(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team", default_retention_days=14)
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)
    admin = make_user(email="admin@example.com", password="password-3", is_system_admin=True)

    unauthorized = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Visit note", "current_draft_text_encrypted": "draft-1", "ingestion_mode": "live_chunked"},
    )
    assert_error(unauthorized, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="owner@example.com", password="password-1")
    transcript_response = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Visit note", "current_draft_text_encrypted": "draft-1", "ingestion_mode": "live_chunked"},
    )
    assert transcript_response.status_code == 201
    assert transcript_response.json()["owner_user_id"] == str(owner.id)
    assert transcript_response.json()["team_id"] == str(team.id)
    assert transcript_response.json()["ingestion_mode"] == "live_chunked"
    assert transcript_response.json()["retention_days_applied"] == 14
    transcript_id = transcript_response.json()["id"]

    legacy_response = client.post(
        "/api/v1/transcripts",
        json={
            "owner_user_id": str(owner.id),
            "team_id": str(team.id),
            "title": "Visit note",
            "current_draft_text_encrypted": "draft-1",
            "ingestion_mode": "whole_file",
        },
    )
    assert legacy_response.status_code == 201
    assert legacy_response.json()["ingestion_mode"] == "whole_file"

    commit_one = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v1"})
    commit_two = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v2"})

    assert commit_one.status_code == 200
    assert commit_two.status_code == 200

    versions = db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id)))
    version_rows = list(versions)
    assert [row.version_no for row in version_rows] == [1, 2]
    assert version_rows[-1].text_encrypted == "final-text-v2"

    owner_list = client.get(f"/api/v1/users/{owner.id}/transcripts")
    other_list = client.get(f"/api/v1/users/{other.id}/transcripts")

    assert owner_list.status_code == 200
    owner_rows = owner_list.json()
    assert [row["id"] for row in owner_rows] == [legacy_response.json()["id"], transcript_id]
    assert [row["ingestion_mode"] for row in owner_rows] == ["whole_file", "live_chunked"]
    assert_error(other_list, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")

    client.post("/api/v1/auth/logout")
    login(client, email="admin@example.com", password="password-3")
    forbidden_admin = client.post("/api/v1/transcripts/start", json={"title": "Admin note", "ingestion_mode": "whole_file"})
    assert_error(forbidden_admin, status_code=403, code="forbidden", message="System-admin accounts cannot own transcript content")


def test_transcript_detail_route_is_owner_only(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    detail = client.get(f"/api/v1/transcripts/{transcript_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == transcript_id
    assert detail.json()["current_draft_text_encrypted"] is None

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.get(f"/api/v1/transcripts/{transcript_id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_transcript_title_update_is_owner_only(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Initial title", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    updated = client.patch(f"/api/v1/transcripts/{transcript_id}", json={"title": "Renamed session"})
    assert updated.status_code == 200
    assert updated.json()["title"] == "Renamed session"
    assert db_session.get(Transcript, UUID(transcript_id)).title == "Renamed session"

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.patch(f"/api/v1/transcripts/{transcript_id}", json={"title": "Hijacked"})
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_transcript_input_mode_can_switch_only_for_blank_idle_owner_session(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Initial title", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    switched = client.patch(f"/api/v1/transcripts/{transcript_id}", json={"ingestion_mode": "live_chunked"})
    assert switched.status_code == 200
    assert switched.json()["ingestion_mode"] == "live_chunked"

    job = TranscriptIngestionJob(
        transcript_id=UUID(transcript_id),
        job_kind=TranscriptIngestionJobKind.audio_file,
        chunk_sequence_no=None,
        source_filename="queued.wav",
        status=TranscriptIngestionJobStatus.queued,
    )
    db_session.add(job)
    db_session.commit()

    blocked = client.patch(f"/api/v1/transcripts/{transcript_id}", json={"ingestion_mode": "whole_file"})
    assert_error(
        blocked,
        status_code=409,
        code="business_rule_violation",
        message="Switch input mode before recording, uploading, or adding transcript content",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.patch(f"/api/v1/transcripts/{transcript_id}", json={"ingestion_mode": "whole_file"})
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_transcript_start_rejects_second_empty_latest_session_until_content_exists(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login(client, email="owner@example.com", password="password-1")
    first = client.post("/api/v1/transcripts/start", json={"title": "Blank", "ingestion_mode": "whole_file"})
    assert first.status_code == 201
    first_id = UUID(first.json()["id"])

    blocked = client.post("/api/v1/transcripts/start", json={"title": "Second blank", "ingestion_mode": "whole_file"})
    assert_error(
        blocked,
        status_code=409,
        code="business_rule_violation",
        message="Finish or delete the current empty session before creating a new one",
    )

    job = TranscriptIngestionJob(
        transcript_id=first_id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        chunk_sequence_no=None,
        source_filename="queued.wav",
        status=TranscriptIngestionJobStatus.queued,
    )
    latest = db_session.get(Transcript, first_id)
    assert latest is not None
    latest.status = TranscriptStatus.transcribing
    db_session.add(latest)
    db_session.add(job)
    db_session.commit()

    blocked_while_transcribing = client.post("/api/v1/transcripts/start", json={"title": "Second", "ingestion_mode": "whole_file"})
    assert_error(
        blocked_while_transcribing,
        status_code=409,
        code="business_rule_violation",
        message="Wait for the current session transcription to finish before creating a new one",
    )

    latest = db_session.get(Transcript, first_id)
    assert latest is not None
    latest.status = TranscriptStatus.ready
    db_session.add(latest)
    db_session.commit()

    allowed = client.post("/api/v1/transcripts/start", json={"title": "Second", "ingestion_mode": "whole_file"})
    assert allowed.status_code == 201


def test_transcript_delete_is_owner_only_and_cascades_versions_jobs_and_generated_documents(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_generated_document,
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Delete me", "ingestion_mode": "whole_file"})
    transcript_id = UUID(started.json()["id"])
    committed = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v1"})
    assert committed.status_code == 200
    version = db_session.scalar(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript_id))
    assert version is not None
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="My template")
    template_version = db_session.scalar(
        select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id, PromptTemplateVersion.version_no == 1)
    )
    assert template_version is not None
    generated_document = make_generated_document(
        owner=owner,
        transcript=db_session.get(Transcript, transcript_id),
        transcript_version=version,
        template_version=template_version,
    )
    queued = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert queued.status_code == 202
    job_id = UUID(queued.json()["job"]["id"])

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.delete(f"/api/v1/transcripts/{transcript_id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")

    client.post("/api/v1/auth/logout")
    login(client, email="owner@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/transcripts/{transcript_id}")
    assert deleted.status_code == 204
    assert db_session.get(Transcript, transcript_id) is None
    assert db_session.get(TranscriptVersion, version.id) is None
    assert db_session.get(TranscriptIngestionJob, job_id) is None
    assert db_session.get(GeneratedDocument, generated_document.id) is None


def test_live_audio_chunk_upload_queues_owner_job(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "live_chunked", "current_draft_text_encrypted": "draft-1"})
    transcript_id = started.json()["id"]

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1", "declared_duration_seconds": "12"},
    )

    assert uploaded.status_code == 202
    assert uploaded.json()["transcript"]["status"] == "transcribing"
    assert uploaded.json()["transcript"]["current_draft_text_encrypted"] == "draft-1"
    assert uploaded.json()["job"]["job_kind"] == "live_chunk"
    assert uploaded.json()["job"]["chunk_sequence_no"] == 1
    persisted = db_session.get(Transcript, UUID(transcript_id))
    assert persisted is not None
    assert persisted.current_draft_text_encrypted == "draft-1"
    job = db_session.get(TranscriptIngestionJob, UUID(uploaded.json()["job"]["id"]))
    assert job is not None
    assert job.status is TranscriptIngestionJobStatus.queued
    assert job.celery_task_id == "test-task-id"


def test_audio_chunk_route_enforces_owner_scope_and_live_chunk_mode(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    unauthorized = client.post(
        "/api/v1/transcripts/00000000-0000-0000-0000-000000000000/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )
    assert_error(unauthorized, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]
    wrong_mode = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )
    assert_error(
        wrong_mode,
        status_code=409,
        code="business_rule_violation",
        message="Transcript ingestion mode does not accept live audio chunks",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_duplicate_live_chunk_sequence_is_rejected(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "live_chunked"})
    transcript_id = started.json()["id"]

    first = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )
    assert first.status_code == 202

    response = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )

    assert_error(response, status_code=409, code="conflict", message="Chunk sequence number has already been submitted")


def test_processing_live_audio_chunk_jobs_applies_text_in_sequence(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "live_chunked", "current_draft_text_encrypted": "draft-1"})
    transcript_id = started.json()["id"]

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        return NormalizedAudio(filename=source_filename.replace(".webm", ".wav"), content_type="audio/wav", data=audio_bytes + b"-normalized")

    def fake_transcribe_with_team_stt(db, *, team_id, audio_bytes, filename, content_type):
        assert team_id == team.id
        assert content_type == "audio/wav"
        if filename == "chunk-1.wav":
            return "first chunk"
        if filename == "chunk-2.wav":
            return "second chunk"
        raise AssertionError(f"unexpected filename {filename}")

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_team_stt", fake_transcribe_with_team_stt)

    queued_one = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk-1.webm", b"chunk-1", "audio/webm")},
        data={"chunk_sequence_no": "1", "declared_duration_seconds": "12"},
    )
    queued_two = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk-2.webm", b"chunk-2", "audio/webm")},
        data={"chunk_sequence_no": "2", "declared_duration_seconds": "10"},
    )

    job_one_id = UUID(queued_one.json()["job"]["id"])
    job_two_id = UUID(queued_two.json()["job"]["id"])

    processed_two = process_transcript_ingestion_job(db_session, job_id=job_two_id, audio_bytes=b"chunk-2")
    assert processed_two.status is TranscriptIngestionJobStatus.completed

    transcript_after_two = db_session.get(Transcript, UUID(transcript_id))
    assert transcript_after_two is not None
    assert transcript_after_two.current_draft_text_encrypted == "draft-1"
    assert transcript_after_two.next_live_chunk_sequence_no_applied == 1

    processed_one = process_transcript_ingestion_job(db_session, job_id=job_one_id, audio_bytes=b"chunk-1")
    assert processed_one.status is TranscriptIngestionJobStatus.applied

    transcript_after_one = db_session.get(Transcript, UUID(transcript_id))
    assert transcript_after_one is not None
    assert transcript_after_one.current_draft_text_encrypted == "draft-1\nfirst chunk\nsecond chunk"
    assert transcript_after_one.next_live_chunk_sequence_no_applied == 3
    assert transcript_after_one.status.value == "transcribing"

    refreshed_two = db_session.get(TranscriptIngestionJob, job_two_id)
    assert refreshed_two is not None
    assert refreshed_two.status is TranscriptIngestionJobStatus.applied


def test_processing_live_audio_chunk_requires_active_team_stt_selection(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "live_chunked"})
    transcript_id = started.json()["id"]
    queued = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )
    job_id = UUID(queued.json()["job"]["id"])

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        return NormalizedAudio(filename="chunk.wav", content_type="audio/wav", data=b"normalized-audio")

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)

    try:
        process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-audio")
    except Exception as exc:
        assert isinstance(exc, Exception)
    else:
        raise AssertionError("Expected live chunk processing to fail without an active STT selection")

    failed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert failed_job is not None
    assert failed_job.status is TranscriptIngestionJobStatus.failed
    assert failed_job.error_code == "business_rule_violation"


def test_audio_file_upload_queues_job_for_whole_file_mode(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )

    assert uploaded.status_code == 202
    assert uploaded.json()["transcript"]["status"] == "transcribing"
    assert uploaded.json()["job"]["job_kind"] == "audio_file"
    assert uploaded.json()["job"]["chunk_sequence_no"] is None
    persisted = db_session.get(Transcript, UUID(transcript_id))
    assert persisted is not None
    assert persisted.current_draft_text_encrypted is None
    job = db_session.get(TranscriptIngestionJob, UUID(uploaded.json()["job"]["id"]))
    assert job is not None
    assert job.status is TranscriptIngestionJobStatus.queued
    assert job.celery_task_id == "test-task-id"


def test_audio_file_upload_rejects_duplicate_in_progress_whole_file_job(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = UUID(started.json()["id"])
    transcript = db_session.get(Transcript, transcript_id)
    assert transcript is not None
    transcript.status = TranscriptStatus.ready
    db_session.add(transcript)
    db_session.commit()

    job = TranscriptIngestionJob(
        transcript_id=transcript_id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        chunk_sequence_no=None,
        source_filename="existing.mp3",
        status=TranscriptIngestionJobStatus.processing,
    )
    db_session.add(job)
    db_session.commit()

    duplicate = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert_error(
        duplicate,
        status_code=409,
        code="conflict",
        message="A file transcription job is already in progress for this session",
    )


def test_audio_file_upload_is_rate_limited_per_authenticated_user(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=leader)
    owner = make_user(email="owner@example.com", password="password-3", team=team, team_role=TeamRole.user)

    transcript_one = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    transcript_two = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit two",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add_all([transcript_one, transcript_two])
    db_session.commit()

    login(client, email="owner@example.com", password="password-3")

    first = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-file",
        files={"audio": ("recording-one.mp3", b"raw-file-audio-1", "audio/mpeg")},
    )
    second = client.post(
        f"/api/v1/transcripts/{transcript_two.id}/audio-file",
        files={"audio": ("recording-two.mp3", b"raw-file-audio-2", "audio/mpeg")},
    )

    assert first.status_code == 202
    assert_error(second, status_code=429, code="rate_limited", message="Too many requests")


def test_audio_file_upload_rejects_oversized_payload(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=leader)
    owner = make_user(email="owner@example.com", password="password-3", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    monkeypatch.setattr("app.services.audio.WHOLE_FILE_MAX_UPLOAD_BYTES", 4)

    login(client, email="owner@example.com", password="password-3")
    oversized = client.post(
        f"/api/v1/transcripts/{transcript.id}/audio-file",
        files={"audio": ("recording.mp3", b"12345", "audio/mpeg")},
    )

    assert_error(
        oversized,
        status_code=413,
        code="payload_too_large",
        message="Audio file exceeds the current maximum upload size",
    )


def test_audio_file_upload_rate_limit_is_isolated_per_authenticated_user(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=leader)
    owner_one = make_user(email="owner-one@example.com", password="password-3", team=team, team_role=TeamRole.user)
    owner_two = make_user(email="owner-two@example.com", password="password-4", team=team, team_role=TeamRole.user)

    transcript_one = Transcript(
        owner_user_id=owner_one.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner_one.created_at,
    )
    transcript_two = Transcript(
        owner_user_id=owner_two.id,
        team_id=team.id,
        title="Visit two",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner_two.created_at,
    )
    db_session.add_all([transcript_one, transcript_two])
    db_session.commit()

    login(client, email="owner-one@example.com", password="password-3")
    first = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-file",
        files={"audio": ("recording-one.mp3", b"raw-file-audio-1", "audio/mpeg")},
    )
    assert first.status_code == 202

    client.post("/api/v1/auth/logout")
    login(client, email="owner-two@example.com", password="password-4")
    second = client.post(
        f"/api/v1/transcripts/{transcript_two.id}/audio-file",
        files={"audio": ("recording-two.mp3", b"raw-file-audio-2", "audio/mpeg")},
    )
    assert second.status_code == 202


def test_processing_audio_file_job_appends_transcript_draft_and_marks_ready(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner@example.com", password="password-1")
    started = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Imported visit",
            "ingestion_mode": "whole_file",
            "current_draft_text_encrypted": "earlier transcript",
        },
    )
    transcript_id = started.json()["id"]

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        assert audio_bytes == b"raw-file-audio"
        assert source_filename == "recording.mp3"
        return NormalizedAudio(
            filename="recording.wav",
            content_type="audio/wav",
            data=make_test_wav_bytes(duration_seconds=1.0),
        )

    def fake_transcribe_with_stt_snapshot(
        db,
        *,
        team_id,
        stt_config_id,
        adapter_kind,
        base_url,
        transcribe_path,
        file_field_name,
        response_text_path,
        extra_form_fields_json,
        model_name,
        language,
        audio_bytes,
        filename,
        content_type,
    ):
        assert team_id == team.id
        assert stt_config_id == config.id
        assert adapter_kind == config.adapter_kind.value
        assert base_url == config.base_url
        assert transcribe_path == config.transcribe_path
        assert file_field_name == config.file_field_name
        assert response_text_path == config.response_text_path
        assert extra_form_fields_json == (config.extra_form_fields_json or {})
        assert model_name == config.model_name
        assert language == config.language
        assert audio_bytes == make_test_wav_bytes(duration_seconds=1.0)
        assert filename == "recording.wav"
        assert content_type == "audio/wav"
        return "full file transcript"

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(uploaded.json()["job"]["id"])
    processed = process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-file-audio")

    assert uploaded.status_code == 202
    assert processed.status is TranscriptIngestionJobStatus.applied
    persisted = db_session.get(Transcript, UUID(transcript_id))
    assert persisted is not None
    assert persisted.current_draft_text_encrypted == "earlier transcript\nfull file transcript"
    assert persisted.status.value == "ready"


def test_audio_file_job_uses_snapshotted_stt_selection_after_team_selection_changes(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-snapshot@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-snapshot@example.com", password="password-2", is_system_admin=True)
    config_one = make_stt_config(
        team=team,
        actor=admin,
        label="Primary STT",
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        model_name="whisper-1",
        available_models_json=["whisper-1"],
    )
    config_two = make_stt_config(
        team=team,
        actor=admin,
        label="Secondary STT",
        base_url="http://127.0.0.1:9100",
        transcribe_path="/v1/alt/transcriptions",
        model_name="gpt-4o-mini-transcribe",
        available_models_json=["gpt-4o-mini-transcribe"],
    )
    selection = make_stt_selection(config=config_one, actor=owner)

    login(client, email="owner-snapshot@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]
    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(uploaded.json()["job"]["id"])

    selection.stt_config_id = config_two.id
    selection.model_name_override = "gpt-4o-mini-transcribe"
    db_session.add(selection)
    db_session.commit()

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        return NormalizedAudio(
            filename="recording.wav",
            content_type="audio/wav",
            data=make_test_wav_bytes(duration_seconds=1.0),
        )

    def fake_transcribe_with_stt_snapshot(
        db,
        *,
        team_id,
        stt_config_id,
        adapter_kind,
        base_url,
        transcribe_path,
        file_field_name,
        response_text_path,
        extra_form_fields_json,
        model_name,
        language,
        audio_bytes,
        filename,
        content_type,
    ):
        assert team_id == team.id
        assert stt_config_id == config_one.id
        assert base_url == config_one.base_url
        assert transcribe_path == config_one.transcribe_path
        assert model_name == "whisper-1"
        return "snapshotted provider transcript"

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)

    processed = process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-file-audio")
    assert processed.status is TranscriptIngestionJobStatus.applied
    refreshed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert refreshed_job is not None
    assert refreshed_job.stt_config_id == config_one.id
    assert refreshed_job.stt_base_url == config_one.base_url
    assert refreshed_job.stt_model_name == "whisper-1"


def test_stt_config_cannot_be_changed_while_jobs_are_in_flight(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-stt-lock@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-stt-lock@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, available_models_json=["whisper-1"])
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Admin-owned job anchor",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.transcribing,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    job = TranscriptIngestionJob(
        transcript_id=transcript.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="queued.wav",
        stt_config_id=config.id,
        status=TranscriptIngestionJobStatus.queued,
    )
    db_session.add(job)
    db_session.commit()

    login(client, email="admin-stt-lock@example.com", password="password-1")
    updated = client.post(
        "/api/v1/stt-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Updated STT",
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "transcribe_path": config.transcribe_path,
            "auth_mode": config.auth_mode.value,
            "model_name": "whisper-1",
            "file_field_name": config.file_field_name,
            "language": config.language,
            "response_text_path": config.response_text_path,
            "extra_form_fields_json": config.extra_form_fields_json,
            "is_active": True,
        },
    )
    assert_error(
        updated,
        status_code=409,
        code="conflict",
        message="Cannot edit this STT config while transcription jobs are queued or processing",
    )

    deleted = client.delete(f"/api/v1/stt-configs/{config.id}?team_id={team.id}")
    assert_error(
        deleted,
        status_code=409,
        code="conflict",
        message="Cannot delete this STT config while transcription jobs are queued or processing",
    )


def test_processing_audio_file_job_fails_when_normalized_duration_exceeds_limit(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner@example.com", password="password-1")
    started = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Imported visit",
            "ingestion_mode": "whole_file",
        },
    )
    transcript_id = started.json()["id"]

    monkeypatch.setattr("app.services.audio.WHOLE_FILE_MAX_DURATION_SECONDS", 1)

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        return NormalizedAudio(
            filename="recording.wav",
            content_type="audio/wav",
            data=make_test_wav_bytes(duration_seconds=2.0),
        )

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(uploaded.json()["job"]["id"])

    try:
        process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-file-audio")
    except Exception as exc:
        assert isinstance(exc, Exception)
    else:
        raise AssertionError("Expected file ingestion processing to fail when duration exceeds the limit")

    failed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert failed_job is not None
    assert failed_job.status is TranscriptIngestionJobStatus.failed
    assert failed_job.error_code == "business_rule_violation"
    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript is not None
    assert transcript.status is TranscriptStatus.failed


def test_audio_file_route_enforces_owner_scope_and_file_modes(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)

    unauthorized = client.post(
        "/api/v1/transcripts/00000000-0000-0000-0000-000000000000/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert_error(unauthorized, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "live_chunked"})
    transcript_id = started.json()["id"]
    wrong_mode = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert_error(
        wrong_mode,
        status_code=409,
        code="business_rule_violation",
        message="Transcript ingestion mode does not accept file ingestion",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_processing_audio_file_requires_active_team_stt_selection(client, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    response = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert_error(
        response,
        status_code=422,
        code="business_rule_violation",
        message="No active STT selection for team",
    )


def test_transcribe_with_team_stt_openai_compatible_rest_uses_vault_secret_and_response_path(
    db_session, make_team, make_user, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Compatible STT",
        adapter_kind=SttAdapterKind.openai_compatible_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        auth_mode=SttAuthMode.bearer,
        model_name="whisper-1",
        file_field_name="file",
        language="en",
        response_text_path="result.text",
        extra_form_fields_json={"response_format": "verbose_json"},
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)

    captured = {}

    def fake_read_team_stt_bearer_token(*, team_id, config_id):
        assert team_id == team.id
        assert config_id == config.id
        return "secret-token"

    def fake_httpx_post(url, *, headers, data, files, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        captured["timeout"] = timeout
        return FakeHttpxResponse({"result": {"text": "recognized text"}})

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", fake_read_team_stt_bearer_token)
    monkeypatch.setattr("app.services.stt.httpx.post", fake_httpx_post)

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"normalized-audio",
        filename="chunk.wav",
        content_type="audio/wav",
    )

    assert text == "recognized text"
    assert captured["url"] == "http://127.0.0.1:9000/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["data"]["model"] == "whisper-1"
    assert captured["data"]["language"] == "en"
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["files"]["file"] == ("chunk.wav", b"normalized-audio", "audio/wav")
