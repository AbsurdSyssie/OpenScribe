from datetime import timedelta
from uuid import UUID

import pyotp
from sqlalchemy import select

from app.models import (
    AccountRequest,
    AccountRequestStatus,
    SttAdapterKind,
    SttAuthMode,
    TeamRole,
    TeamSttConfig,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    Transcript,
    TranscriptVersion,
    User,
    UserRecoveryCode,
    UserSession,
    UserStatus,
    UserTrustedDevice,
)
from app.services.audio import NormalizedAudio
from app.services.stt import transcribe_with_team_stt
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
            "ingestion_mode": "microphone_batch",
        },
    )
    assert legacy_response.status_code == 201
    assert legacy_response.json()["ingestion_mode"] == "microphone_batch"

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
    assert [row["ingestion_mode"] for row in owner_rows] == ["microphone_batch", "live_chunked"]
    assert_error(other_list, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")

    client.post("/api/v1/auth/logout")
    login(client, email="admin@example.com", password="password-3")
    forbidden_admin = client.post("/api/v1/transcripts/start", json={"title": "Admin note", "ingestion_mode": "file_upload"})
    assert_error(forbidden_admin, status_code=403, code="forbidden", message="System-admin accounts cannot own transcript content")


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
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "microphone_batch"})
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


def test_audio_file_upload_queues_job_for_file_modes(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "file_upload"})
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


def test_processing_audio_file_job_updates_transcript_draft_and_marks_ready(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "file_upload"})
    transcript_id = started.json()["id"]

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        assert audio_bytes == b"raw-file-audio"
        assert source_filename == "recording.mp3"
        return NormalizedAudio(filename="recording.wav", content_type="audio/wav", data=b"normalized-file-audio")

    def fake_transcribe_with_team_stt(db, *, team_id, audio_bytes, filename, content_type):
        assert team_id == team.id
        assert audio_bytes == b"normalized-file-audio"
        assert filename == "recording.wav"
        assert content_type == "audio/wav"
        return "full file transcript"

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_team_stt", fake_transcribe_with_team_stt)

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
    assert persisted.current_draft_text_encrypted == "full file transcript"
    assert persisted.status.value == "ready"


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


def test_processing_audio_file_requires_active_team_stt_selection(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "microphone_batch"})
    transcript_id = started.json()["id"]

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        return NormalizedAudio(filename="recording.wav", content_type="audio/wav", data=b"normalized-file-audio")

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)

    response = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(response.json()["job"]["id"])

    assert response.status_code == 202
    try:
        process_transcript_ingestion_job(db_session, job_id=job_id, audio_bytes=b"raw-file-audio")
    except Exception as exc:
        assert isinstance(exc, Exception)
    else:
        raise AssertionError("Expected file ingestion processing to fail without an active STT selection")

    failed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert failed_job is not None
    assert failed_job.status is TranscriptIngestionJobStatus.failed
    assert failed_job.error_code == "business_rule_violation"


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
