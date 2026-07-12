import json
import time
import io
import hashlib
import subprocess
from types import SimpleNamespace
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4
import wave

import httpx
import pytest
import pyotp
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from scripts.seed_dev_accounts import ensure_dev_system_admin, repair_dev_user_content_key_if_needed
from scripts.reset_unreadable_owner_content import reset_unreadable_owner_content

from app.errors import AppError
from app.main import CSRF_COOKIE_NAME, api, app as fastapi_app, get_db, require_full_context
from app.models import (
    AccountRequest,
    AccountRequestStatus,
    ClinicalEntity,
    ClinicalEntityRun,
    DefaultPromptTemplate,
    DefaultPromptTemplateVersion,
    DefaultQuickAction,
    DefaultQuickActionVersion,
    DeidentificationProvider,
    GeneratedDocument,
    GeneratedDocumentSection,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    HallucinationCheckStatus,
    LlmAdapterKind,
    LlmAuthMode,
    LlmConfigSetupStatus,
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    ProviderUsageEvent,
    ProviderUsageEventType,
    ProviderCredentialStatus,
    PromptTemplate,
    PromptTemplateVersion,
    PostConsultationDictation,
    PostConsultationDictationSegment,
    QuickAction,
    QuickActionVersion,
    RedactionRun,
    RedactionRunStatus,
    SecurityAuditEvent,
    SessionAuthLevel,
    SessionStatus,
    SttAdapterKind,
    SttAuthMode,
    SttConfigSetupStatus,
    SttProviderPreset,
    SttSelectionPurpose,
    Team,
    TeamClinicalNlpSelection,
    TeamLlmConfig,
    TeamHallucinationCheckSelection,
    TeamLlmSelection,
    TeamRole,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    TeamSttConfig,
    TeamSttSelection,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptStatus,
    Transcript,
    TranscriptIngestionMode,
    TranscriptManualPiiEntity,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    TemplateMode,
    User,
    UserEncryptionKey,
    UserAppPreference,
    UserLlmPreference,
    UserOnboardingState,
    UserRecoveryCode,
    UserSession,
    UserStatus,
    UserTrustedDevice,
    TemplateScope,
    utcnow,
)
from app.services.stt import STT_TRANSCRIPTION_TIMEOUT_SECONDS, _list_deepgram_stt_models, _list_elevenlabs_stt_models, _safe_http_error_details, _transcribe_via_elevenlabs, _transcribe_via_http, create_stt_config_draft, ensure_stt_service_healthy, paragraphize_timestamped_segments, replace_stt_config_draft_credential, resolve_selected_team_stt, run_saved_stt_config_test, transcribe_with_stt_snapshot
from app.schemas.templates import GenerateFollowupRequest, PromptTemplateUpsert, QuickActionUpsert
from app.schemas import LlmConfigUpsert, LlmInspectRequest, SttConfigDraftCreate, SttConfigDraftReplaceCredential, SttInspectResult
from app.services.audio import (
    AUDIO_FFMPEG_TIMEOUT_SECONDS,
    WHOLE_FILE_MAX_DURATION_SECONDS,
    WHOLE_FILE_MAX_UPLOAD_BYTES,
    NormalizedAudio,
    normalize_audio_to_wav_16k_mono,
    probe_audio_duration_seconds,
)
from app.services.content_crypto import (
    decrypt_json_for_owner,
    decrypt_text_for_owner,
    encrypt_json_for_owner,
    encrypt_text_for_owner,
    ensure_user_dek,
    is_encrypted_envelope,
    keyed_digest_for_owner,
)
from app.services.redaction import reidentify_text as redaction_reidentify_text
from app.services.redaction import ensure_redaction_run_for_transcript_version
from app.services.clinical_nlp import clinical_entity_value, ensure_clinical_entity_run_for_transcript_version
from app.services.llm import _list_mistral_chat_models, _list_together_chat_models, upsert_llm_config as upsert_llm_config_service
from app.services.llm_presets import LLM_PROVIDER_PRESETS, apply_provider_defaults, filter_discovered_models, infer_llm_provider_preset
from app.services.stt import transcribe_with_team_stt
from app.services.dictations import update_post_consultation_dictation
from app.services.templates import (
    DICTATION_SOURCE_SPLIT_MARKER,
    GeneratedDocumentWaitingForTranscript,
    _apply_hallucination_check_request_overrides,
    _generate_freeform_output_openai,
    _generate_freeform_output_ollama,
    _parse_generated_note_json,
    delete_personal_template,
    process_generated_document,
    generated_document_llm_request_payload,
    queue_document_generation_from_template as queue_document_generation_from_template_service,
    queue_followup_generation,
    queue_quick_action_generation,
    upsert_personal_quick_action,
    upsert_personal_template,
)
from app.services.transcripts import (
    WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS,
    WHOLE_FILE_HOURLY_UPLOAD_BYTES,
    create_manual_pii_entity,
    latest_ingestion_job_for_transcript as latest_ingestion_job_for_transcript_service,
    process_transcript_ingestion_job,
    save_working_note,
    start_transcript as start_transcript_service,
)
from app.schemas.transcripts import TranscriptStart, WorkingNoteUpdate
from app.services import vault as vault_service
from app.services.auth import SESSION_COOKIE_NAME, rotate_session
from app.services.vault import generate_user_content_data_key, unwrap_user_content_data_key
from tests.constants import PERMANENT_TEST_PASSWORD


def test_enqueue_transcript_ingestion_job_does_not_send_audio(monkeypatch):
    from app import tasks

    captured = {}

    class FakeTask:
        @staticmethod
        def delay(**kwargs):
            captured.update(kwargs)

            class Result:
                id = "task-1"

            return Result()

    monkeypatch.setattr(tasks, "process_transcript_ingestion_job_task", FakeTask)

    job_id = uuid4()
    tasks.enqueue_transcript_ingestion_job(job_id=job_id)

    assert captured == {"job_id": str(job_id)}
    assert "audio_b64" not in captured
    assert "audio_bytes" not in captured


def test_legacy_transcript_ingestion_task_payload_is_accepted(monkeypatch):
    from app import tasks

    captured = {}

    class FakeSession:
        def __enter__(self):
            return "db-session"

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(tasks, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        tasks,
        "process_transcript_ingestion_job",
        lambda db, **kwargs: captured.update({"db": db, **kwargs}),
    )

    job_id = uuid4()
    tasks.process_transcript_ingestion_job_task(job_id=str(job_id), audio_b64="bGVnYWN5LWF1ZGlv")

    assert captured == {"db": "db-session", "job_id": job_id, "legacy_audio_bytes": b"legacy-audio"}


def assert_error(response, *, status_code: int, code: str, message: str):
    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    return body["error"].get("details")


def login(client, *, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_api_docs_public_in_test_environment_by_default(client, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_DOCS", raising=False)
    monkeypatch.setenv("APP_ENV", "test")

    schema = client.get("/openapi.json")
    docs = client.get("/docs")
    redoc = client.get("/redoc")

    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "OpenScribe MVP"
    assert docs.status_code == 200
    assert "SwaggerUIBundle" in docs.text
    assert redoc.status_code == 200
    assert "ReDoc" in redoc.text


def test_api_docs_require_system_admin_in_production(client, make_team, make_user, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_DOCS", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    team = make_team(name="Docs Gate Clinic")
    make_user(email="docs-user@example.com", password="password-1", team=team)
    admin = make_user(email="docs-admin@example.com", password="password-2", is_system_admin=True)

    unauthenticated = client.get("/openapi.json")
    assert_error(unauthenticated, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="docs-user@example.com", password="password-1")
    forbidden = client.get("/docs")
    assert_error(forbidden, status_code=403, code="forbidden", message="System admin access required")

    login(client, email=admin.email, password="password-2")
    schema = client.get("/openapi.json")
    docs = client.get("/docs")

    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "OpenScribe MVP"
    assert docs.status_code == 200
    assert "SwaggerUIBundle" in docs.text


def test_api_docs_public_override_can_expose_docs(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_API_DOCS", "true")

    schema = client.get("/openapi.json")

    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "OpenScribe MVP"


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


def test_authenticated_unsafe_api_requires_csrf(raw_client, make_user):
    make_user(email="csrf-user@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-user@example.com", password="password-1").status_code == 200

    response = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF probe", "ingestion_mode": "whole_file"},
        headers={"Origin": "http://testserver"},
    )

    assert_error(response, status_code=403, code="forbidden", message="CSRF verification failed")


def test_authenticated_unsafe_api_accepts_matching_csrf(raw_client, make_user):
    make_user(email="csrf-ok@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-ok@example.com", password="password-1").status_code == 200
    csrf = raw_client.cookies.get(CSRF_COOKIE_NAME)

    response = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF ok", "ingestion_mode": "whole_file"},
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 201


def test_authenticated_unsafe_api_rejects_mismatched_csrf(raw_client, make_user):
    make_user(email="csrf-mismatch@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-mismatch@example.com", password="password-1").status_code == 200

    response = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF mismatch", "ingestion_mode": "whole_file"},
        headers={"Origin": "http://testserver", "X-CSRF-Token": "header-token"},
    )

    assert_error(response, status_code=403, code="forbidden", message="CSRF verification failed")


def test_authenticated_unsafe_api_rejects_cross_origin(raw_client, make_user):
    make_user(email="csrf-cross-origin@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-cross-origin@example.com", password="password-1").status_code == 200
    csrf = raw_client.cookies.get(CSRF_COOKIE_NAME)

    response = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF cross-origin", "ingestion_mode": "whole_file"},
        headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf},
    )

    assert_error(response, status_code=403, code="forbidden", message="Cross-origin request rejected")


def test_session_bound_csrf_rejects_old_token_after_session_rotation(raw_client, db_session, make_user):
    user = make_user(email="csrf-rotation@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-rotation@example.com", password="password-1").status_code == 200
    old_session = raw_client.cookies.get(SESSION_COOKIE_NAME)
    old_csrf = raw_client.cookies.get(CSRF_COOKIE_NAME)
    new_session = rotate_session(db_session, old_session, user)
    raw_client.cookies.set(SESSION_COOKIE_NAME, new_session)

    response = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF stale", "ingestion_mode": "whole_file"},
        headers={"Origin": "http://testserver", "X-CSRF-Token": old_csrf},
    )

    assert_error(response, status_code=403, code="forbidden", message="CSRF verification failed")


def test_authenticated_safe_api_does_not_require_csrf(raw_client, make_user):
    make_user(email="csrf-safe@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-safe@example.com", password="password-1").status_code == 200

    response = raw_client.get("/api/v1/auth/me")

    assert response.status_code == 200


def test_public_login_without_existing_auth_cookie_does_not_require_csrf(raw_client):
    response = raw_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong"},
    )

    assert response.status_code in {401, 422}
    assert response.json()["error"]["message"] != "CSRF verification failed"


def test_public_unsafe_endpoint_with_auth_cookie_requires_csrf(raw_client, make_user):
    make_user(email="csrf-public@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="csrf-public@example.com", password="password-1").status_code == 200

    response = raw_client.post(
        "/api/v1/account-requests",
        json={
            "requested_name": "Probe",
            "requested_email": "probe@example.com",
            "requested_team_name": "Probe Team",
            "request_details": "csrf probe",
        },
        headers={"Origin": "http://testserver"},
    )

    assert_error(response, status_code=403, code="forbidden", message="CSRF verification failed")


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


def test_audio_duration_probe_timeout_surfaces_app_error(monkeypatch):
    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] > 0
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("app.services.audio.subprocess.run", fake_run)

    with pytest.raises(AppError) as exc_info:
        probe_audio_duration_seconds(audio_bytes=b"raw-audio", source_filename="chunk.webm")

    assert exc_info.value.code == "audio_duration_probe_failed"
    assert exc_info.value.message == "Audio duration inspection timed out"


def test_audio_normalization_timeout_surfaces_app_error(monkeypatch):
    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] > 0
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("app.services.audio.subprocess.run", fake_run)

    with pytest.raises(AppError) as exc_info:
        normalize_audio_to_wav_16k_mono(audio_bytes=b"raw-audio", source_filename="chunk.webm")

    assert exc_info.value.code == "audio_normalization_failed"
    assert exc_info.value.message == "Audio normalization timed out"


def decrypt_transcript_draft(db_session, transcript: Transcript) -> str | None:
    return decrypt_text_for_owner(
        db_session,
        owner_user_id=transcript.owner_user_id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        stored_value=transcript.current_draft_text_encrypted,
    )


def decrypt_transcript_structured_context(db_session, transcript: Transcript) -> dict | None:
    return decrypt_json_for_owner(
        db_session,
        owner_user_id=transcript.owner_user_id,
        table="transcripts",
        field="structured_context_json",
        record_id=transcript.id,
        stored_value=transcript.structured_context_json,
    )


def decrypt_generated_document_field(db_session, document: GeneratedDocument, field: str) -> str | None:
    return decrypt_text_for_owner(
        db_session,
        owner_user_id=document.owner_user_id,
        table="generated_documents",
        field=field,
        record_id=document.id,
        stored_value=getattr(document, field),
    )


def decrypt_generated_document_structured_context(db_session, document: GeneratedDocument) -> dict | None:
    return decrypt_json_for_owner(
        db_session,
        owner_user_id=document.owner_user_id,
        table="generated_documents",
        field="structured_context_json",
        record_id=document.id,
        stored_value=document.structured_context_json,
    )


def decrypt_generated_document_structured_working_note_snapshot(db_session, document: GeneratedDocument) -> dict | None:
    return decrypt_json_for_owner(
        db_session,
        owner_user_id=document.owner_user_id,
        table="generated_documents",
        field="structured_working_note_snapshot_json",
        record_id=document.id,
        stored_value=document.structured_working_note_snapshot_json,
    )


def decrypt_generated_document_section_field(db_session, *, owner_user_id, section: GeneratedDocumentSection, field: str) -> str | None:
    return decrypt_text_for_owner(
        db_session,
        owner_user_id=owner_user_id,
        table="generated_document_sections",
        field=field,
        record_id=section.id,
        stored_value=getattr(section, field),
    )


def make_ingestion_job_for_transcript(transcript: Transcript, **kwargs) -> TranscriptIngestionJob:
    return TranscriptIngestionJob(
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        **kwargs,
    )


class FakeHttpxResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://testserver.local/stt")
            response = httpx.Response(self.status_code, request=request, json=self._payload)
            raise httpx.HTTPStatusError("stt request failed", request=request, response=response)


class FakeHttpxStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://testserver.local/api/chat")
            response = httpx.Response(self.status_code, request=request, json={"error": "upstream failure"})
            raise httpx.HTTPStatusError("ollama request failed", request=request, response=response)


STT_OPENAPI_DOCUMENT = {
    "openapi": "3.1.0",
    "info": {"title": "STT Test API", "version": "1.0.0"},
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


@pytest.mark.parametrize(("email", "is_system_admin"), [("dev.user@example.com", False), ("dev.admin@example.com", True)])
def test_dev_seed_account_api_login_is_restricted_to_localhost(client, make_user, email, is_system_admin):
    make_user(email=email, password="password-1", is_system_admin=is_system_admin, mfa_required=False, mfa_enabled=False)

    local = client.post("/api/v1/auth/login", json={"email": email, "password": "password-1"})
    assert local.status_code == 200
    assert local.json()["authenticated"] is True

    remote = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password-1"},
        headers={"host": "192.168.1.77:8080", "origin": "http://192.168.1.77:8080"},
    )
    assert_error(remote, status_code=403, code="forbidden", message="Dev test accounts are available only from localhost")


@pytest.mark.parametrize(("email", "is_system_admin"), [("dev.user@example.com", False), ("dev.admin@example.com", True)])
def test_dev_seed_account_session_is_revoked_on_non_local_request(client, make_user, email, is_system_admin):
    make_user(email=email, password="password-1", is_system_admin=is_system_admin, mfa_required=False, mfa_enabled=False)

    login_response = client.post("/api/v1/auth/login", json={"email": email, "password": "password-1"})
    assert login_response.status_code == 200

    remote_me = client.get(
        "/api/v1/auth/me",
        headers={"host": "192.168.1.77:8080"},
    )
    assert_error(remote_me, status_code=401, code="unauthorized", message="Authentication required")

    local_me = client.get("/api/v1/auth/me")
    assert_error(local_me, status_code=401, code="unauthorized", message="Authentication required")


def test_invalid_api_route_still_returns_json_not_found(client):
    response = client.get("/api/v1/does-not-exist")

    assert_error(response, status_code=404, code="business_rule_violation", message="Not Found")


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
    assert persisted_user.password_hash.startswith("$argon2id$")
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


def test_system_admin_can_provision_and_read_team_stt_configs_without_secret_reveal(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"text": "sample transcript"}))

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
    assert body["credential_status"] == "verified"
    assert "vault_secret_ref" not in body
    assert "super-secret-token" not in created.text

    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.id == UUID(body["id"])))
    assert persisted is not None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/stt/team/")
    assert persisted.credential_fingerprint
    assert persisted.credential_status is ProviderCredentialStatus.verified

    listed = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert listed.status_code == 200
    assert [item["label"] for item in listed.json()] == ["Clinic STT"]

    fetched = client.get(f"/api/v1/stt-configs/{body['id']}?team_id={team.id}")
    assert fetched.status_code == 200
    assert fetched.json()["label"] == "Clinic STT"
    assert "super-secret-token" not in fetched.text


def test_system_admin_can_create_and_finalize_stt_provider_draft(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-stt-draft@example.com", password="password-1", is_system_admin=True)

    def fake_get(url, *, headers=None, timeout=None):
        assert url == "https://api.deepgram.com/v1/models"
        assert headers == {"Authorization": "Token dg-secret"}
        assert timeout == 10.0
        return FakeHttpxResponse(
            {
                "stt": [
                    {"name": "Nova 3", "canonical_name": "nova-3", "batch": True},
                    {"name": "Streaming Only", "canonical_name": "stream-only", "batch": False},
                ],
                "tts": [{"canonical_name": "aura-2"}],
            }
        )

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    logged_in = login(client, email="admin-stt-draft@example.com", password="password-1")
    assert logged_in.status_code == 200, logged_in.text
    draft = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "deepgram", "bearer_token": "dg-secret"},
    )

    assert draft.status_code == 200, draft.text
    body = draft.json()
    config_id = UUID(body["config"]["id"])
    assert body["provider_display_name"] == "Deepgram"
    assert body["config"]["provider_preset"] == SttProviderPreset.deepgram.value
    assert body["config"]["setup_status"] == SttConfigSetupStatus.pending_model_selection.value
    assert body["config"]["is_active"] is False
    assert body["available_models"] == ["nova-3"]
    assert body["config"]["available_models_json"] == ["nova-3"]
    assert body["config"]["extra_form_fields_json"] == {"smart_format": "true", "mip_opt_out": "true"}
    assert "dg-secret" not in draft.text

    persisted = db_session.get(TeamSttConfig, config_id)
    assert persisted is not None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/stt/team/")
    assert persisted.model_name is None
    assert persisted.available_models_json == ["nova-3"]
    assert persisted.extra_form_fields_json == {"smart_format": "true", "mip_opt_out": "true"}

    options_before = client.get(f"/api/v1/stt-selection/options?team_id={team.id}")
    assert options_before.status_code == 200
    assert options_before.json() == []

    finalized = client.post(
        f"/api/v1/stt-configs/{config_id}/finalize",
        json={"team_id": str(team.id), "label": "Deepgram STT", "model_name": "nova-3", "language": "en", "is_active": True},
    )

    assert finalized.status_code == 200
    assert finalized.json()["setup_status"] == SttConfigSetupStatus.ready.value
    assert finalized.json()["is_active"] is True

    options_after = client.get(f"/api/v1/stt-selection/options?team_id={team.id}")
    assert options_after.status_code == 200
    assert [item["id"] for item in options_after.json()] == [str(config_id)]


def test_system_admin_can_replace_stt_draft_credential_without_body_config_id(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic STT Replace")
    make_user(email="admin-stt-replace@example.com", password="password-1", is_system_admin=True)
    seen_tokens = []

    def fake_get(url, *, headers=None, timeout=None):
        assert url == "https://api.deepgram.com/v1/models"
        seen_tokens.append(headers["Authorization"])
        return FakeHttpxResponse({"stt": [{"name": "Nova 3", "canonical_name": "nova-3", "batch": True}]})

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    logged_in = login(client, email="admin-stt-replace@example.com", password="password-1")
    assert logged_in.status_code == 200, logged_in.text
    draft = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "deepgram", "bearer_token": "dg-old"},
    )
    assert draft.status_code == 200, draft.text
    config_id = UUID(draft.json()["config"]["id"])

    replaced = client.post(
        f"/api/v1/stt-configs/{config_id}/replace-credential",
        json={"team_id": str(team.id), "bearer_token": "dg-new"},
    )

    assert replaced.status_code == 200
    assert replaced.json()["config"]["setup_status"] == SttConfigSetupStatus.pending_model_selection.value
    assert replaced.json()["available_models"] == ["nova-3"]
    assert "dg-new" not in replaced.text
    assert seen_tokens == ["Token dg-old", "Token dg-new"]

    persisted = db_session.get(TeamSttConfig, config_id)
    assert persisted is not None
    assert persisted.credential_status is ProviderCredentialStatus.verified
    assert persisted.credential_fingerprint


def test_pending_stt_provider_cannot_be_selected_directly(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-stt-pending@example.com", password="password-1", is_system_admin=True)

    def fake_get(url, *, headers=None, timeout=None):
        assert url == "https://api.elevenlabs.io/v1/models"
        assert headers == {"xi-api-key": "el-secret"}
        assert timeout == 10.0
        return FakeHttpxResponse({"models": [{"model_id": "scribe_v2", "name": "Scribe v2 speech-to-text"}]})

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    login(client, email="admin-stt-pending@example.com", password="password-1")
    draft = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "elevenlabs", "bearer_token": "el-secret"},
    )
    assert draft.status_code == 200
    config_id = draft.json()["config"]["id"]

    selected = client.post(
        "/api/v1/stt-selection",
        json={"team_id": str(team.id), "stt_config_id": config_id},
    )

    assert_error(selected, status_code=404, code="not_found", message="Selectable STT config not found")


def test_system_admin_elevenlabs_draft_validates_credential(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic ElevenLabs")
    make_user(email="admin-stt-el@example.com", password="password-1", is_system_admin=True)

    def fake_get(url, *, headers=None, timeout=None):
        assert url == "https://api.elevenlabs.io/v1/models"
        assert headers == {"xi-api-key": "el-secret"}
        return FakeHttpxResponse(
            {
                "models": [
                    {"model_id": "scribe_v2", "name": "Scribe v2 speech-to-text"},
                    {"model_id": "scribe_v1", "name": "Scribe v1 speech-to-text"},
                    {"model_id": "scribe_v2_realtime", "name": "Scribe v2 Realtime"},
                    {"model_id": "tts_only", "name": "Text to speech"},
                ]
            }
        )

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    login(client, email="admin-stt-el@example.com", password="password-1")
    response = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "elevenlabs", "bearer_token": "el-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["provider_preset"] == SttProviderPreset.elevenlabs.value
    assert body["config"]["adapter_kind"] == SttAdapterKind.elevenlabs_speech_to_text.value
    assert body["available_models"] == ["scribe_v2", "scribe_v1"]
    assert body["config"]["model_name"] is None
    assert body["config"]["language_field_name"] == "language_code"
    assert body["config"]["segments_path"] == "words"
    assert body["credential_status"] == ProviderCredentialStatus.verified.value
    assert "el-secret" not in response.text

    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id))
    assert persisted is not None
    assert persisted.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text
    assert persisted.available_models_json == ["scribe_v2", "scribe_v1"]
    assert persisted.model_name is None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/stt/team/")
    assert persisted.credential_fingerprint


def test_system_admin_elevenlabs_draft_rejects_invalid_credential(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bad ElevenLabs")
    make_user(email="admin-stt-bad-el@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.stt.httpx.get", lambda *args, **kwargs: FakeHttpxResponse({"detail": "invalid"}, status_code=401))

    login(client, email="admin-stt-bad-el@example.com", password="password-1")
    response = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "elevenlabs", "bearer_token": "bad-key"},
    )

    assert_error(response, status_code=401, code="stt_credential_invalid", message="The API key was rejected by ElevenLabs.")
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id)) is None


def test_elevenlabs_model_discovery_filters_sync_stt_models(monkeypatch):
    captured = {}

    def fake_get(url, *, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return FakeHttpxResponse(
            [
                {"model_id": "scribe_v2", "name": "Scribe v2"},
                {"model_id": "scribe_v1", "name": "Scribe v1"},
                {"model_id": "scribe_v2_realtime", "name": "Scribe v2 Realtime"},
                {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2"},
            ]
        )

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    models = _list_elevenlabs_stt_models(api_key="el-secret", base_url="https://api.elevenlabs.io")

    assert models == ["scribe_v2", "scribe_v1"]
    assert captured["url"] == "https://api.elevenlabs.io/v1/models"
    assert captured["headers"] == {"xi-api-key": "el-secret"}
    assert "Authorization" not in captured["headers"]
    assert captured["timeout"] == 10.0


def test_elevenlabs_model_discovery_rejects_invalid_key(monkeypatch):
    monkeypatch.setattr("app.services.stt.httpx.get", lambda *args, **kwargs: FakeHttpxResponse({"detail": "invalid"}, status_code=401))

    with pytest.raises(AppError) as exc_info:
        _list_elevenlabs_stt_models(api_key="bad-key", base_url="https://api.elevenlabs.io")

    assert exc_info.value.code == "stt_credential_invalid"
    assert exc_info.value.status_code == 401


def test_elevenlabs_finalize_rejects_non_sync_model(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic ElevenLabs Invalid Model")
    make_user(email="admin-stt-el-invalid-model@example.com", password="password-1", is_system_admin=True)

    monkeypatch.setattr("app.services.stt.httpx.get", lambda *args, **kwargs: FakeHttpxResponse({"models": []}))

    login(client, email="admin-stt-el-invalid-model@example.com", password="password-1")
    draft = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "elevenlabs", "bearer_token": "el-secret"},
    )
    assert draft.status_code == 200

    response = client.post(
        f"/api/v1/stt-configs/{draft.json()['config']['id']}/finalize",
        json={
            "team_id": str(team.id),
            "config_id": draft.json()["config"]["id"],
            "label": "ElevenLabs STT",
            "model_name": "scribe_v2_realtime",
            "is_active": True,
        },
    )

    assert_error(response, status_code=422, code="business_rule_violation", message="Selected ElevenLabs STT model is not supported")


def test_elevenlabs_transcription_uses_xi_api_key_not_bearer(monkeypatch):
    captured = {}

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured.update({"url": url, "headers": headers, "data": data, "files": files, "timeout": timeout})
        return FakeHttpxResponse({"text": "hello from elevenlabs"})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    result = _transcribe_via_elevenlabs(
        url="https://api.elevenlabs.io/v1/speech-to-text",
        api_key="el-secret",
        audio_bytes=b"audio",
        filename="audio.wav",
        content_type="audio/wav",
        model_name="scribe_v2",
        language=None,
        response_text_path="text",
    )

    assert result == "hello from elevenlabs"
    assert captured["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert captured["headers"] == {"xi-api-key": "el-secret"}
    assert "Authorization" not in captured["headers"]
    assert captured["data"] == {"model_id": "scribe_v2"}
    assert captured["files"]["file"][0] == "audio.wav"
    assert captured["timeout"] == STT_TRANSCRIPTION_TIMEOUT_SECONDS


@pytest.mark.parametrize("language", ["", "None", "none", "null", "undefined", "auto", "default", "provider_default"])
def test_elevenlabs_transcription_does_not_send_default_language(monkeypatch, language):
    captured = {}

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured["data"] = data or {}
        return FakeHttpxResponse({"text": "ok"})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    _transcribe_via_elevenlabs(
        url="https://api.elevenlabs.io/v1/speech-to-text",
        api_key="el-secret",
        audio_bytes=b"audio",
        filename="audio.wav",
        content_type="audio/wav",
        model_name="scribe_v2",
        language=language,
        response_text_path="text",
    )

    assert "language_code" not in captured["data"]


@pytest.mark.parametrize("language", ["None", "none", "auto", "default", ""])
def test_generic_stt_transport_does_not_send_default_language(monkeypatch, language):
    captured = {}

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured["data"] = data or {}
        return FakeHttpxResponse({"text": "ok"})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    _transcribe_via_http(
        provider_preset=SttProviderPreset.custom_openai_compatible.value,
        base_url="https://example.com",
        transcribe_path="/v1/audio/transcriptions",
        file_field_name="file",
        response_text_path="text",
        extra_form_fields_json={},
        bearer_token="secret",
        model_name="whisper-1",
        model_field_name="model",
        language=language,
        language_field_name="language",
        audio_bytes=b"audio",
        filename="audio.wav",
        content_type="audio/wav",
    )

    assert "language" not in captured["data"]


def test_system_admin_deepgram_draft_rejects_invalid_credential(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-stt-bad-dg@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.stt.httpx.get", lambda *args, **kwargs: FakeHttpxResponse({"error": "unauthorized"}, status_code=401))

    login(client, email="admin-stt-bad-dg@example.com", password="password-1")
    response = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "deepgram", "bearer_token": "bad-key"},
    )

    assert_error(response, status_code=401, code="stt_credential_invalid", message="The API key was rejected by Deepgram.")
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id)) is None


def test_stt_draft_commit_failure_cleans_written_vault_secret(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-stt-draft-cleanup@example.com", password="password-1", is_system_admin=True)
    written_config_ids: list[str] = []
    deleted_config_ids: list[str] = []

    monkeypatch.setattr("app.services.stt._list_openai_transcription_models", lambda **kwargs: ["whisper-1"])
    monkeypatch.setattr(
        "app.services.stt.write_team_stt_bearer_token",
        lambda *, team_id, config_id, bearer_token: written_config_ids.append(str(config_id)) or f"secret:openscribe/stt/team/{team_id}/config/{config_id}",
    )
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", lambda *, team_id, config_id, secret_ref=None: deleted_config_ids.append(str(config_id)))
    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(IntegrityError("insert", {}, Exception("duplicate"))))

    with pytest.raises(IntegrityError):
        create_stt_config_draft(
            db_session,
            admin,
            SttConfigDraftCreate(team_id=team.id, provider_preset=SttProviderPreset.openai.value, bearer_token="secret-token"),
        )

    assert written_config_ids
    assert deleted_config_ids == written_config_ids
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id)) is None


def test_stt_draft_replace_commit_failure_preserves_old_vault_secret(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-stt-draft-replace-cleanup@example.com", password="password-1", is_system_admin=True)
    secrets_by_ref: dict[str, str] = {}
    deleted_refs: list[str] = []

    def fake_write(*, team_id, config_id, bearer_token, secret_id=None):
        suffix = f"/{secret_id}" if secret_id else ""
        secret_ref = f"secret:openscribe/stt/team/{team_id}/config/{config_id}{suffix}"
        secrets_by_ref[secret_ref] = bearer_token
        return secret_ref

    def fake_delete(*, team_id, config_id, secret_ref=None):
        deleted_refs.append(secret_ref or f"secret:openscribe/stt/team/{team_id}/config/{config_id}")
        secrets_by_ref.pop(deleted_refs[-1], None)

    inspection = SttInspectResult(
        base_url="https://api.openai.com",
        openapi_path=None,
        adapter_kind=SttAdapterKind.openai_cloud,
        transcribe_path="/v1/audio/transcriptions",
        model_name="whisper-1",
        model_field_name="model",
        file_field_name="file",
        language=None,
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
        available_models=["whisper-1"],
        available_model_options=[],
        field_tips=[],
        notes=[],
    )

    monkeypatch.setattr("app.services.stt._list_openai_transcription_models", lambda **kwargs: ["whisper-1"])
    monkeypatch.setattr("app.services.stt.inspect_stt_contract", lambda *args, **kwargs: inspection)
    monkeypatch.setattr("app.services.stt.write_team_stt_bearer_token", fake_write)
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", fake_delete)
    config, _ = create_stt_config_draft(
        db_session,
        admin,
        SttConfigDraftCreate(team_id=team.id, provider_preset=SttProviderPreset.openai.value, bearer_token="old-token"),
    )
    old_secret_ref = config.vault_secret_ref

    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(IntegrityError("update", {}, Exception("duplicate"))))

    with pytest.raises(IntegrityError):
        replace_stt_config_draft_credential(
            db_session,
            admin,
            SttConfigDraftReplaceCredential(team_id=team.id, config_id=config.id, bearer_token="new-token"),
        )

    assert secrets_by_ref == {old_secret_ref: "old-token"}
    assert deleted_refs and old_secret_ref not in deleted_refs
    db_session.expire_all()
    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref == old_secret_ref


def test_stt_config_duplicate_warning_happens_before_vault_write_or_inspection(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-duplicate-stt@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"text": "sample transcript"}))
    login(client, email="admin-duplicate-stt@example.com", password="password-1")
    first = client.post(
        "/api/v1/stt-configs",
        json={"team_id": str(team.id), "label": "Clinic STT", "adapter_kind": "openai_compatible_rest", "base_url": "http://127.0.0.1:7000", "bearer_token": "same-secret", "model_name": "whisper-1"},
    )
    assert first.status_code == 200

    writes: list[str] = []
    inspections: list[str] = []
    monkeypatch.setattr("app.services.stt.write_team_stt_bearer_token", lambda **kwargs: writes.append(kwargs["bearer_token"]) or "secret:unexpected")
    monkeypatch.setattr("app.services.stt.inspect_stt_contract", lambda *args, **kwargs: inspections.append("called"))

    duplicate = client.post(
        "/api/v1/stt-configs",
        json={"team_id": str(team.id), "label": "Duplicate STT", "adapter_kind": "openai_compatible_rest", "base_url": "http://127.0.0.1:7000", "bearer_token": "same-secret", "model_name": "whisper-1"},
    )

    assert_error(
        duplicate,
        status_code=409,
        code="provider_credential_duplicate_warning",
        message="A saved STT provider for this team, adapter, endpoint, and credential already exists. Confirm duplicate to save anyway.",
    )
    assert writes == []
    assert inspections == []
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Duplicate STT")) is None


def test_stt_config_confirmed_duplicate_can_proceed(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-confirm-duplicate-stt@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"text": "sample transcript"}))
    login(client, email="admin-confirm-duplicate-stt@example.com", password="password-1")
    payload = {"team_id": str(team.id), "adapter_kind": "openai_compatible_rest", "base_url": "http://127.0.0.1:7000", "bearer_token": "same-secret", "model_name": "whisper-1"}
    first = client.post("/api/v1/stt-configs", json={**payload, "label": "Clinic STT"})
    second = client.post("/api/v1/stt-configs", json={**payload, "label": "Duplicate STT", "confirm_duplicate": True})

    assert first.status_code == 200
    assert second.status_code == 200
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Duplicate STT")) is not None


def test_stt_config_invalid_first_add_removes_db_row_before_vault_cleanup(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-invalid-stt@example.com", password="password-1", is_system_admin=True)
    events: list[str] = []

    def fake_delete(*, team_id, config_id, secret_ref=None):
        assert db_session.get(TeamSttConfig, config_id) is None
        events.append("deleted")

    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"error": "unauthorized"}, status_code=401))
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", fake_delete)

    login(client, email="admin-invalid-stt@example.com", password="password-1")
    created = client.post(
        "/api/v1/stt-configs",
        json={"team_id": str(team.id), "label": "Bad STT", "adapter_kind": "openai_compatible_rest", "base_url": "http://127.0.0.1:7000", "bearer_token": "bad-secret", "model_name": "whisper-1"},
    )

    assert_error(created, status_code=422, code="provider_credential_invalid", message="STT provider rejected the supplied credential")
    assert events == ["deleted"]
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Bad STT")) is None


def test_stt_config_valid_secret_with_discovery_failure_saves_partial(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-partial-stt@example.com", password="password-1", is_system_admin=True)

    def fail_model_discovery(**kwargs):
        raise AppError(502, "stt_inspection_failed", "Provider metadata discovery failed")

    monkeypatch.setattr("app.services.stt._list_openai_transcription_models", fail_model_discovery)
    login(client, email="admin-partial-stt@example.com", password="password-1")
    created = client.post(
        "/api/v1/stt-configs",
        json={"team_id": str(team.id), "label": "Partial STT", "adapter_kind": "openai_cloud", "bearer_token": "secret", "model_name": "whisper-1"},
    )

    assert created.status_code == 200
    assert created.json()["credential_status"] == "partial"
    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Partial STT"))
    assert persisted is not None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/stt/team/")
    assert persisted.credential_status is ProviderCredentialStatus.partial


def test_stt_reinspect_uses_saved_vault_secret_and_invalid_clears_selection(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-reinspect-stt@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, has_secret=True)
    make_stt_selection(config=config, actor=admin)
    reads: list[str] = []

    def fake_read(*, team_id, config_id, secret_ref=None):
        reads.append(str(config_id))
        return "saved-secret"

    def fake_inspect(*args, **kwargs):
        raise AppError(401, "unauthorized", "Provider rejected credential")

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", fake_read)
    monkeypatch.setattr("app.services.stt.inspect_stt_contract", fake_inspect)

    login(client, email="admin-reinspect-stt@example.com", password="password-1")
    reinspected = client.post(f"/api/v1/stt-configs/{config.id}/inspect?team_id={team.id}")

    assert reinspected.status_code == 200
    assert reads == [str(config.id)]
    assert reinspected.json()["credential_status"] == "invalid"
    db_session.refresh(config)
    assert config.credential_status is ProviderCredentialStatus.invalid
    assert db_session.scalar(select(TeamSttSelection).where(TeamSttSelection.stt_config_id == config.id)) is None


def test_system_admin_can_provision_openai_compatible_stt_without_secret(client, db_session, make_team, make_user):
    team = make_team(name="Clinic North")
    make_user(email="admin-no-auth-stt@example.com", password="password-1", is_system_admin=True)

    login(client, email="admin-no-auth-stt@example.com", password="password-1")
    created = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(team.id),
            "label": "Parakeet",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:8000",
            "model_name": "parakeet",
            "language": "en",
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["has_secret"] is False
    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.id == UUID(body["id"])))
    assert persisted is not None
    assert persisted.vault_secret_ref == ""


def test_system_admin_edit_preserves_saved_stt_secret_when_token_blank(client, db_session, make_team, make_user, make_stt_config, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-preserve-stt-secret@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, adapter_kind=SttAdapterKind.openai_compatible_rest, base_url="http://127.0.0.1:8000", model_name="parakeet")
    deleted: list[tuple[str, str]] = []

    def fake_delete_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
        deleted.append((str(team_id), str(config_id)))
        return None

    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", fake_delete_team_stt_bearer_token)

    login(client, email="admin-preserve-stt-secret@example.com", password="password-1")
    updated = client.post(
        "/api/v1/stt-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Parakeet edited",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:8000",
            "transcribe_path": "/v1/audio/transcriptions",
            "auth_mode": "bearer",
            "model_name": "parakeet",
            "file_field_name": "file",
            "language": "en",
            "response_text_path": "text",
            "extra_form_fields_json": {},
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref.startswith("secret:openscribe/stt/team/")
    assert deleted == []


def test_system_admin_can_explicitly_remove_saved_stt_secret(client, db_session, make_team, make_user, make_stt_config, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-remove-stt-secret@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, adapter_kind=SttAdapterKind.openai_compatible_rest, base_url="http://127.0.0.1:8000", model_name="parakeet")
    deleted: list[str] = []

    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", lambda *, team_id, config_id, secret_ref=None: deleted.append(str(config_id)))

    login(client, email="admin-remove-stt-secret@example.com", password="password-1")
    updated = client.post(
        "/api/v1/stt-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Parakeet no auth",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:8000",
            "transcribe_path": "/v1/audio/transcriptions",
            "auth_mode": "bearer",
            "credential_action": "remove",
            "model_name": "parakeet",
            "file_field_name": "file",
            "response_text_path": "text",
            "extra_form_fields_json": {},
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref == ""
    assert persisted.credential_fingerprint is None
    assert persisted.credential_status is ProviderCredentialStatus.unknown
    assert persisted.inspection_metadata_json == {"status": "unknown", "reason": "credential_removed"}
    assert deleted == [str(config.id)]


def test_generic_stt_save_with_token_tests_saved_contract_not_openapi_discovery(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin-generic-stt-save@example.com", password="password-1", is_system_admin=True)
    calls: dict[str, object] = {}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("generic save must not inspect default OpenAPI")

    def fake_post(url, *, headers, data, files, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["data"] = data
        calls["file_field"] = next(iter(files.keys()))
        return FakeHttpxResponse({"result": {"text": "sample transcript"}})

    monkeypatch.setattr("app.services.stt.inspect_stt_contract", fail_if_called)
    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    login(client, email="admin-generic-stt-save@example.com", password="password-1")
    created = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(team.id),
            "label": "Manual STT",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:7000",
            "transcribe_path": "/speech/transcribe",
            "bearer_token": "secret-token",
            "credential_action": "replace",
            "model_name": "clinic-whisper",
            "model_field_name": "model_id",
            "file_field_name": "audio_file",
            "language": "en",
            "language_field_name": "lang",
            "response_text_path": "result.text",
            "extra_form_fields_json": {"format": "json"},
            "is_active": True,
        },
    )

    assert created.status_code == 200
    assert created.json()["credential_status"] == "verified"
    assert calls["url"] == "http://127.0.0.1:7000/speech/transcribe"
    assert calls["headers"] == {"Authorization": "Bearer secret-token"}
    assert calls["data"] == {"format": "json", "model_id": "clinic-whisper", "lang": "en"}
    assert calls["file_field"] == "audio_file"
    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Manual STT"))
    assert persisted is not None
    assert persisted.inspection_metadata_json["sample_test"] == "passed"


def test_deepgram_direct_save_forces_mip_opt_out_and_known_runtime(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Deepgram Direct")
    make_user(email="admin-deepgram-direct@example.com", password="password-1", is_system_admin=True)
    calls: dict[str, object] = {}

    def fake_post(url, *, headers=None, params=None, content=None, data=None, files=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["params"] = params
        calls["content"] = content
        calls["data"] = data
        calls["files"] = files
        return FakeHttpxResponse({"results": {"channels": [{"alternatives": [{"transcript": "sample transcript"}]}]}})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    login(client, email="admin-deepgram-direct@example.com", password="password-1")
    created = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(team.id),
            "label": "Deepgram direct",
            "provider_preset": "custom_rest_openapi",
            "adapter_kind": "generic_rest",
            "base_url": "https://api.deepgram.com",
            "transcribe_path": "/v1/listen",
            "bearer_token": "dg-secret",
            "credential_action": "replace",
            "model_name": "nova-3",
            "model_field_name": "model",
            "file_field_name": "file",
            "language": "en",
            "language_field_name": "language",
            "response_text_path": "results.channels.0.alternatives.0.transcript",
            "extra_form_fields_json": {"smart_format": "true"},
            "is_active": True,
        },
    )

    assert created.status_code == 200, created.text
    assert created.json()["provider_preset"] == SttProviderPreset.deepgram.value
    assert created.json()["extra_form_fields_json"] == {"smart_format": "true", "mip_opt_out": "true"}
    assert calls["url"] == "https://api.deepgram.com/v1/listen"
    assert calls["headers"] == {"Authorization": "Token dg-secret", "Content-Type": "audio/wav"}
    assert calls["params"] == {"smart_format": "true", "mip_opt_out": "true", "model": "nova-3", "language": "en"}
    assert calls["content"]
    assert calls["data"] is None
    assert calls["files"] is None

    persisted = db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Deepgram direct"))
    assert persisted is not None
    assert persisted.provider_preset == SttProviderPreset.deepgram.value
    assert persisted.extra_form_fields_json == {"smart_format": "true", "mip_opt_out": "true"}


def test_deepgram_save_rejects_explicit_mip_opt_out_false(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Deepgram Unsafe")
    make_user(email="admin-deepgram-unsafe@example.com", password="password-1", is_system_admin=True)
    calls: list[str] = []

    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: calls.append("called") or FakeHttpxResponse({"text": "unexpected"}))

    login(client, email="admin-deepgram-unsafe@example.com", password="password-1")
    rejected = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(team.id),
            "label": "Unsafe Deepgram",
            "provider_preset": "deepgram",
            "adapter_kind": "generic_rest",
            "base_url": "https://api.deepgram.com",
            "transcribe_path": "/v1/listen",
            "bearer_token": "dg-secret",
            "credential_action": "replace",
            "model_name": "nova-3",
            "model_field_name": "model",
            "file_field_name": "file",
            "response_text_path": "results.channels.0.alternatives.0.transcript",
            "extra_form_fields_json": {"mip_opt_out": "false"},
            "is_active": True,
        },
    )

    assert_error(rejected, status_code=422, code="business_rule_violation", message="Deepgram STT requires mip_opt_out=true")
    assert calls == []
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Unsafe Deepgram")) is None


def test_deepgram_host_rejects_non_deepgram_adapter(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Deepgram Adapter")
    make_user(email="admin-deepgram-adapter@example.com", password="password-1", is_system_admin=True)
    calls: list[str] = []

    monkeypatch.setattr("app.services.stt._list_openai_transcription_models", lambda **kwargs: calls.append("called") or ["whisper-1"])

    login(client, email="admin-deepgram-adapter@example.com", password="password-1")
    rejected = client.post(
        "/api/v1/stt-configs",
        json={
            "team_id": str(team.id),
            "label": "Wrong Deepgram Adapter",
            "adapter_kind": "openai_cloud",
            "base_url": "https://api.deepgram.com",
            "bearer_token": "dg-secret",
            "model_name": "whisper-1",
        },
    )

    assert_error(rejected, status_code=422, code="business_rule_violation", message="Deepgram STT must use the Deepgram generic REST contract")
    assert calls == []
    assert db_session.scalar(select(TeamSttConfig).where(TeamSttConfig.label == "Wrong Deepgram Adapter")) is None


def test_generic_stt_bad_replacement_token_preserves_existing_config_and_selection(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Generic Replacement")
    admin = make_user(email="admin-generic-stt-replace@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(
        team=team,
        actor=admin,
        label="Existing Generic STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:7000",
        transcribe_path="/speech/transcribe",
        model_name="clinic-whisper",
        response_text_path="result.text",
        has_secret=True,
    )
    old_secret_ref = config.vault_secret_ref
    selection = make_stt_selection(config=config, actor=admin)
    written_tokens: list[str] = []
    deleted_config_ids: list[str] = []

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda *, team_id, config_id, secret_ref=None: "old-token")
    monkeypatch.setattr(
        "app.services.stt.write_team_stt_bearer_token",
        lambda *, team_id, config_id, bearer_token: written_tokens.append(bearer_token) or f"secret:openscribe/stt/team/{team_id}/config/{config_id}",
    )
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", lambda *, team_id, config_id, secret_ref=None: deleted_config_ids.append(str(config_id)))
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"error": "unauthorized"}, status_code=401))

    login(client, email="admin-generic-stt-replace@example.com", password="password-1")
    updated = client.post(
        "/api/v1/stt-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Replacement Generic STT",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:7000",
            "transcribe_path": "/speech/transcribe",
            "bearer_token": "bad-token",
            "credential_action": "replace",
            "model_name": "clinic-whisper",
            "file_field_name": "file",
            "response_text_path": "result.text",
            "extra_form_fields_json": {},
            "is_active": True,
        },
    )

    assert_error(updated, status_code=422, code="provider_credential_invalid", message="STT provider rejected the supplied credential")
    db_session.expire_all()
    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is not None
    assert persisted.label == "Existing Generic STT"
    assert persisted.vault_secret_ref == old_secret_ref
    assert db_session.get(TeamSttSelection, selection.id) is not None
    assert written_tokens == []
    assert deleted_config_ids == []


def test_openai_compatible_stt_bad_replacement_token_preserves_existing_config_and_selection(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic OpenAI Compatible Replacement")
    admin = make_user(email="admin-openai-compatible-stt-replace@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(
        team=team,
        actor=admin,
        label="Existing OpenAI Compatible STT",
        adapter_kind=SttAdapterKind.openai_compatible_rest,
        base_url="http://127.0.0.1:7000",
        transcribe_path="/v1/audio/transcriptions",
        model_name="whisper-1",
        response_text_path="text",
        has_secret=True,
    )
    old_secret_ref = config.vault_secret_ref
    selection = make_stt_selection(config=config, actor=admin)
    written_tokens: list[str] = []
    deleted_config_ids: list[str] = []

    monkeypatch.setattr(
        "app.services.stt.write_team_stt_bearer_token",
        lambda *, team_id, config_id, bearer_token: written_tokens.append(bearer_token) or f"secret:openscribe/stt/team/{team_id}/config/{config_id}",
    )
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", lambda *, team_id, config_id, secret_ref=None: deleted_config_ids.append(str(config_id)))
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"error": "unauthorized"}, status_code=401))

    login(client, email="admin-openai-compatible-stt-replace@example.com", password="password-1")
    updated = client.post(
        "/api/v1/stt-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Replacement OpenAI Compatible STT",
            "adapter_kind": "openai_compatible_rest",
            "base_url": "http://127.0.0.1:7000",
            "transcribe_path": "/v1/audio/transcriptions",
            "bearer_token": "bad-token",
            "credential_action": "replace",
            "model_name": "whisper-1",
            "file_field_name": "file",
            "response_text_path": "text",
            "extra_form_fields_json": {},
            "is_active": True,
        },
    )

    assert_error(updated, status_code=422, code="provider_credential_invalid", message="STT provider rejected the supplied credential")
    db_session.expire_all()
    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is not None
    assert persisted.label == "Existing OpenAI Compatible STT"
    assert persisted.vault_secret_ref == old_secret_ref
    assert db_session.get(TeamSttSelection, selection.id) is not None
    assert written_tokens == []
    assert deleted_config_ids == []


def test_system_admin_can_delete_provisioned_stt_config_without_leaking_secret(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=admin)
    secret_ref = config.vault_secret_ref
    deleted_refs: list[str | None] = []

    def fake_delete_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
        deleted_refs.append(secret_ref)

    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", fake_delete_team_stt_bearer_token)

    login(client, email="admin@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/stt-configs/{config.id}?team_id={team.id}")
    assert deleted.status_code == 204

    persisted = db_session.get(TeamSttConfig, config.id)
    assert persisted is None
    assert deleted_refs == [secret_ref]

    selection = client.get(f"/api/v1/stt-selection?team_id={team.id}")
    assert selection.status_code == 200
    assert selection.json() is None
    fetched = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert fetched.status_code == 200
    assert fetched.json() == []


def test_system_admin_can_inspect_stt_openapi_and_get_prefilled_fields(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)

    monkeypatch.setattr("app.services.provider_inspection.httpx.get", lambda *args, **kwargs: FakeHttpxResponse(STT_OPENAPI_DOCUMENT))

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
    assert body["model_field_name"] == "model"
    assert body["file_field_name"] == "file"
    assert body["language"] == "en"
    assert body["language_field_name"] == "language"
    assert body["response_text_path"] == "text"
    assert body["extra_form_fields_json"] == {"chunk_mode": "memory"}
    assert any(tip["name"] == "file" and tip["required"] is True for tip in body["field_tips"])
    assert any(tip["name"] == "chunk_mode" and tip["description"] == "Chunk handling mode." for tip in body["field_tips"])
    assert any("OpenAI-compatible REST" in note for note in body["notes"])
    assert "inspect-token" not in inspected.text


def test_system_admin_can_inspect_generic_stt_dynamic_field_names(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Generic STT Test API", "version": "1.0.0"},
        "paths": {
            "/speech/transcribe": {
                "post": {
                    "summary": "Speech transcription",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "audio_file": {"type": "string", "format": "binary"},
                                        "model_id": {"type": "string", "default": "clinic-whisper"},
                                        "lang": {"type": "string", "example": "en"},
                                    },
                                }
                            }
                        }
                    },
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"transcript": {"type": "string"}}}
                                }
                            }
                        }
                    },
                }
            }
        },
    }
    monkeypatch.setattr("app.services.provider_inspection.httpx.get", lambda *args, **kwargs: FakeHttpxResponse(document))

    login(client, email="admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/stt-configs/inspect",
        json={"team_id": str(team.id), "adapter_kind": "generic_rest", "base_url": "http://127.0.0.1:7000"},
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["transcribe_path"] == "/speech/transcribe"
    assert body["file_field_name"] == "audio_file"
    assert body["model_field_name"] == "model_id"
    assert body["model_name"] == "clinic-whisper"
    assert body["language_field_name"] == "lang"
    assert body["language"] == "en"
    assert body["response_text_path"] == "transcript"


def test_system_admin_can_test_saved_stt_config_with_bundled_sample(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(
        team=team,
        actor=admin,
        base_url="http://127.0.0.1:7000",
        transcribe_path="/v1/audio/transcriptions",
        file_field_name="file",
        response_text_path="text",
        model_name="whisper-1",
        language="en",
    )

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")
    monkeypatch.setattr(
        "app.services.stt._transcribe_via_http",
        lambda **kwargs: "more or less, I suppose",
    )

    result = run_saved_stt_config_test(db_session, admin, config_id=config.id, team_id=team.id)

    assert result["success"] is True
    assert result["health_status"] == "skipped"
    assert result["sample_filename"] == "MoreOrLess.wav"
    assert result["transcribe_url"] == "http://127.0.0.1:7000/v1/audio/transcriptions"
    assert result["model_name"] == "whisper-1"
    assert result["language"] == "en"
    assert result["transcript_text"] == "more or less, I suppose"
    assert result["error_code"] is None


def test_system_admin_can_test_saved_openai_stt_config_without_generic_fields(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-openai-stt-test@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(
        team=team,
        actor=admin,
        adapter_kind=SttAdapterKind.openai_cloud,
        base_url="https://api.openai.com/v1",
        transcribe_path="/v1/audio/transcriptions",
        file_field_name="file",
        response_text_path="text",
        model_name="whisper-1",
        language="en",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")

    def fake_openai_transcribe(**kwargs):
        captured.update(kwargs)
        return "openai transcript"

    monkeypatch.setattr("app.services.stt._transcribe_via_openai_cloud", fake_openai_transcribe)

    result = run_saved_stt_config_test(db_session, admin, config_id=config.id, team_id=team.id)

    assert result["success"] is True
    assert result["transcript_text"] == "openai transcript"
    assert captured["bearer_token"] == "secret-token"
    assert "model_field_name" not in captured
    assert "language_field_name" not in captured


def test_system_admin_can_test_saved_generic_stt_config_with_dynamic_fields(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-generic-stt-test@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(
        team=team,
        actor=admin,
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:7000",
        transcribe_path="/speech/transcribe",
        file_field_name="audio_file",
        response_text_path="transcript",
        model_name="clinic-whisper",
        language="en-GB",
    )
    config.model_field_name = "model_id"
    config.language_field_name = "lang"
    db_session.add(config)
    db_session.commit()
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")

    def fake_http_transcribe(**kwargs):
        captured.update(kwargs)
        return "generic transcript"

    monkeypatch.setattr("app.services.stt._transcribe_via_http", fake_http_transcribe)

    result = run_saved_stt_config_test(db_session, admin, config_id=config.id, team_id=team.id)

    assert result["success"] is True
    assert result["transcript_text"] == "generic transcript"
    assert captured["model_field_name"] == "model_id"
    assert captured["language_field_name"] == "lang"
    assert captured["file_field_name"] == "audio_file"


def test_system_admin_saved_test_uses_elevenlabs_runtime_path(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="Clinic ElevenLabs Test")
    admin = make_user(email="admin-elevenlabs-stt-test@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(
        team=team,
        actor=admin,
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="https://api.elevenlabs.io",
        transcribe_path="/v1/speech-to-text",
        model_name="scribe_v2",
        language="None",
    )
    config.provider_preset = SttProviderPreset.elevenlabs.value
    config.adapter_kind = SttAdapterKind.elevenlabs_speech_to_text
    config.model_field_name = "model_id"
    config.language_field_name = "language_code"
    db_session.add(config)
    db_session.commit()
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "el-secret")

    def fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured.update({"url": url, "headers": headers, "data": data, "files": files, "timeout": timeout})
        return FakeHttpxResponse({"text": "elevenlabs transcript"})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    result = run_saved_stt_config_test(db_session, admin, config_id=config.id, team_id=team.id)

    assert result["success"] is True
    assert result["transcript_text"] == "elevenlabs transcript"
    assert captured["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert captured["headers"] == {"xi-api-key": "el-secret"}
    assert "Authorization" not in captured["headers"]
    assert captured["data"] == {"model_id": "scribe_v2"}
    assert captured["files"]["file"][0] == "MoreOrLess.wav"

    config.language = "en"
    db_session.add(config)
    db_session.commit()
    run_saved_stt_config_test(db_session, admin, config_id=config.id, team_id=team.id)
    assert captured["data"] == {"model_id": "scribe_v2", "language_code": "en"}


def test_system_admin_stt_test_result_surfaces_provider_failure_without_secret_reveal(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    monkeypatch,
):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, base_url="http://127.0.0.1:7000")

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")
    def raise_failure(**kwargs):
        raise AppError(
            502,
            "stt_request_failed",
            "STT provider request failed",
            {"status_code": 401, "provider_error_code": "quota_exceeded"},
        )

    monkeypatch.setattr("app.services.stt._transcribe_via_http", raise_failure)

    result = run_saved_stt_config_test(db_session, admin, config_id=config.id, team_id=team.id)

    assert result["success"] is False
    assert result["health_status"] == "skipped"
    assert result["error_code"] == "stt_request_failed"
    assert result["error_message"] == "STT provider request failed"
    assert result["provider_status_code"] == 401
    assert result["provider_error_code"] == "quota_exceeded"
    assert result["transcript_text"] is None


def test_safe_http_error_details_includes_provider_error_code_without_message():
    request = httpx.Request("POST", "https://api.elevenlabs.io/v1/speech-to-text")
    response = httpx.Response(
        401,
        request=request,
        json={
            "detail": {
                "status": "quota_exceeded",
                "message": "This request includes provider account details",
            }
        },
    )
    exc = httpx.HTTPStatusError("provider failed", request=request, response=response)

    details = _safe_http_error_details(exc)

    assert details["status_code"] == 401
    assert details["provider_error_code"] == "quota_exceeded"
    assert "message" not in details


def test_paragraphize_timestamped_segments_splits_on_long_pauses():
    paragraphs = paragraphize_timestamped_segments(
        [
            {"start": 0.78, "end": 2.54, "text": "This is my voice.", "speaker": "UNKNOWN"},
            {"start": 4.7, "end": 13.18, "text": "For those who want to install the simple audio recorder application in Ubuntu and Ubuntu 24.04, here's the new Ubuntu PPA.", "speaker": "UNKNOWN"},
            {"start": 13.42, "end": 14.14, "text": "Update.", "speaker": "UNKNOWN"},
            {"start": 14.3, "end": 22.54, "text": "The PPA support until Ubuntu 24.04 no longer updates for 24.10 and higher due to a lack of upstream source development.", "speaker": "UNKNOWN"},
            {"start": 22.78, "end": 30.06, "text": "Audio recorder is a free open-source GTK3 audio recording application for Linux, and it has a stupid simple user interface.", "speaker": "UNKNOWN"},
        ],
        max_chars=420,
        pause_threshold_seconds=1.2,
    )

    assert paragraphs == [
        {
            "start": 0.78,
            "end": 2.54,
            "speaker": "UNKNOWN",
            "text": "This is my voice.",
            "segment_count": 1,
        },
        {
            "start": 4.7,
            "end": 30.06,
            "speaker": "UNKNOWN",
            "text": "For those who want to install the simple audio recorder application in Ubuntu and Ubuntu 24.04, here's the new Ubuntu PPA. Update. The PPA support until Ubuntu 24.04 no longer updates for 24.10 and higher due to a lack of upstream source development. Audio recorder is a free open-source GTK3 audio recording application for Linux, and it has a stupid simple user interface.",
            "segment_count": 4,
        },
    ]


def test_paragraphize_timestamped_segments_splits_on_speaker_change_and_length_cap():
    paragraphs = paragraphize_timestamped_segments(
        [
            {"start": 0.0, "end": 2.0, "text": "First speaker opening sentence.", "speaker": "speaker_1"},
            {"start": 2.1, "end": 4.0, "text": "Another sentence that still belongs to the same speaker.", "speaker": "speaker_1"},
            {"start": 4.1, "end": 5.0, "text": "Second speaker response.", "speaker": "speaker_2"},
            {"start": 5.1, "end": 7.5, "text": "A very long continuation that should be forced into another paragraph once the max character threshold is exceeded.", "speaker": "speaker_2"},
        ],
        max_chars=90,
        pause_threshold_seconds=1.2,
    )

    assert paragraphs == [
        {
            "start": 0.0,
            "end": 4.0,
            "speaker": "speaker_1",
            "text": "First speaker opening sentence. Another sentence that still belongs to the same speaker.",
            "segment_count": 2,
        },
        {
            "start": 4.1,
            "end": 5.0,
            "speaker": "speaker_2",
            "text": "Second speaker response.",
            "segment_count": 1,
        },
        {
            "start": 5.1,
            "end": 7.5,
            "speaker": "speaker_2",
            "text": "A very long continuation that should be forced into another paragraph once the max character threshold is exceeded.",
            "segment_count": 1,
        },
    ]


def test_paragraphize_timestamped_segments_default_heuristic_splits_parakeet_sample_more_aggressively():
    paragraphs = paragraphize_timestamped_segments(
        [
            {"start": 0.78, "end": 2.54, "text": "This is my voice.", "speaker": "UNKNOWN"},
            {"start": 4.7, "end": 13.18, "text": "For those who want to install the simple audio recorder application in Ubuntu and Ubuntu 24.04, here's the new Ubuntu PPA.", "speaker": "UNKNOWN"},
            {"start": 13.42, "end": 14.14, "text": "Update.", "speaker": "UNKNOWN"},
            {"start": 14.3, "end": 22.54, "text": "The PPA support until Ubuntu 24.04 no longer updates for 24.10 and higher due to a lack of upstream source development.", "speaker": "UNKNOWN"},
            {"start": 22.78, "end": 30.06, "text": "Audio recorder is a free open-source GTK3 audio recording application for Linux, and it has a stupid simple user interface.", "speaker": "UNKNOWN"},
        ]
    )

    assert [paragraph["text"] for paragraph in paragraphs] == [
        "This is my voice.",
        "For those who want to install the simple audio recorder application in Ubuntu and Ubuntu 24.04, here's the new Ubuntu PPA. Update.",
        "The PPA support until Ubuntu 24.04 no longer updates for 24.10 and higher due to a lack of upstream source development.",
        "Audio recorder is a free open-source GTK3 audio recording application for Linux, and it has a stupid simple user interface.",
    ]


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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    mfa_login = login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
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
    client, make_team, make_user, make_stt_config, monkeypatch
):
    north = make_team(name="Clinic North")
    south = make_team(name="Clinic South")
    make_user(email="admin@example.com", password="password-2", is_system_admin=True)
    make_user(email="leader@example.com", password="password-1", team=north, team_role=TeamRole.leader)
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"text": "sample transcript"}))

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
    north_config = make_stt_config(team=north, actor=admin, available_models_json=["whisper-1"])
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


def test_leader_can_manage_distinct_stt_selections_by_purpose(client, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic Purpose Split")
    admin = make_user(email="admin-purpose-split@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-purpose-split@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    conversation_config = make_stt_config(team=team, actor=admin, label="Conversation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    dictation_config = make_stt_config(
        team=team,
        actor=admin,
        label="Dictation STT",
        model_name="gpt-4o-mini-transcribe",
        available_models_json=["gpt-4o-mini-transcribe"],
    )

    login(client, email="leader-purpose-split@example.com", password="password-2")

    conversation_selected = client.post(
        "/api/v1/stt-selection",
        json={
            "purpose": "conversation",
            "stt_config_id": str(conversation_config.id),
        },
    )
    assert conversation_selected.status_code == 200
    assert conversation_selected.json()["purpose"] == "conversation"
    assert conversation_selected.json()["stt_config_id"] == str(conversation_config.id)

    dictation_selected = client.post(
        "/api/v1/stt-selection",
        json={
            "purpose": "post_consultation_dictation",
            "stt_config_id": str(dictation_config.id),
        },
    )
    assert dictation_selected.status_code == 200
    assert dictation_selected.json()["purpose"] == "post_consultation_dictation"
    assert dictation_selected.json()["stt_config_id"] == str(dictation_config.id)

    fetched_conversation = client.get("/api/v1/stt-selection")
    assert fetched_conversation.status_code == 200
    assert fetched_conversation.json()["stt_config_id"] == str(conversation_config.id)

    fetched_dictation = client.get("/api/v1/stt-selection?purpose=post_consultation_dictation")
    assert fetched_dictation.status_code == 200
    assert fetched_dictation.json()["stt_config_id"] == str(dictation_config.id)

    cleared_dictation = client.delete("/api/v1/stt-selection?purpose=post_consultation_dictation")
    assert cleared_dictation.status_code == 204
    assert client.get("/api/v1/stt-selection?purpose=post_consultation_dictation").json() is None
    assert client.get("/api/v1/stt-selection").json()["stt_config_id"] == str(conversation_config.id)


def test_dictation_stt_resolution_requires_dictation_selection_without_fallback(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
):
    team = make_team(name="Clinic No Fallback")
    admin = make_user(email="admin-no-fallback@example.com", password="password-1", is_system_admin=True)
    conversation_config = make_stt_config(team=team, actor=admin, label="Conversation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=conversation_config, purpose=SttSelectionPurpose.conversation)

    selection, config, resolved_model_name, resolved_language = resolve_selected_team_stt(
        db_session,
        team_id=team.id,
        purpose=SttSelectionPurpose.conversation,
    )
    assert selection.purpose is SttSelectionPurpose.conversation
    assert config.id == conversation_config.id
    assert resolved_model_name == "whisper-1"
    assert resolved_language == conversation_config.language

    with pytest.raises(AppError) as excinfo:
        resolve_selected_team_stt(
            db_session,
            team_id=team.id,
            purpose=SttSelectionPurpose.post_consultation_dictation,
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "business_rule_violation"
    assert excinfo.value.message == "No active STT selection for team and purpose"
    assert excinfo.value.details == {
        "team_id": str(team.id),
        "purpose": "post_consultation_dictation",
    }


def test_owner_can_upload_and_edit_post_consultation_dictation(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Dictation")
    admin = make_user(email="admin-dictation-flow@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-dictation-flow@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Dictation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.post_consultation_dictation)

    monkeypatch.setattr(
        "app.services.dictations.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="dictation.wav", content_type="audio/wav", data=b"normalized-dictation"),
    )
    monkeypatch.setattr("app.services.dictations.enforce_whole_file_duration_limit", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.services.dictations.transcribe_with_team_stt",
        lambda db, **kwargs: "Patient improving. Continue antibiotics for five days.",
    )

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Consultation Dictation"})
    transcript_id = transcript_created.json()["id"]

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation/audio-file",
        files={"audio": ("dictation.mp3", b"raw-dictation", "audio/mpeg")},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["effective_text"] == "Patient improving. Continue antibiotics for five days."
    assert body["is_combined_text_user_edited"] is False
    assert body["segment_count"] == 1

    dictation = db_session.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == UUID(transcript_id)))
    assert dictation is not None
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="post_consultation_dictation_segments",
        field="asr_text_encrypted",
        record_id=db_session.scalar(select(PostConsultationDictationSegment.id).where(PostConsultationDictationSegment.post_consultation_dictation_id == dictation.id)),
        stored_value=db_session.scalar(select(PostConsultationDictationSegment.asr_text_encrypted).where(PostConsultationDictationSegment.post_consultation_dictation_id == dictation.id)),
    ) == "Patient improving. Continue antibiotics for five days."

    updated = client.patch(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation",
        json={"combined_text": "Clinician summary edited."},
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["effective_text"] == "Clinician summary edited."
    assert updated_body["is_combined_text_user_edited"] is True

    cleared = client.patch(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation",
        json={"combined_text": ""},
    )
    assert cleared.status_code == 200
    cleared_body = cleared.json()
    assert cleared_body["effective_text"] == ""
    assert cleared_body["combined_edited_text_encrypted"] == ""
    assert cleared_body["is_combined_text_user_edited"] is True

    refreshed = client.get(f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation")
    assert refreshed.status_code == 200
    assert refreshed.json()["effective_text"] == ""
    assert refreshed.json()["combined_edited_text_encrypted"] == ""


def test_owner_can_preview_post_consultation_dictation_without_persisting(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Dictation Preview")
    admin = make_user(email="admin-dictation-preview@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-dictation-preview@example.com", password="password-2", team=team, team_role=TeamRole.user)
    other = make_user(email="other-dictation-preview@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Dictation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.post_consultation_dictation)

    monkeypatch.setattr(
        "app.services.dictations.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="dictation.wav", content_type="audio/wav", data=b"normalized-dictation"),
    )
    monkeypatch.setattr("app.services.dictations.enforce_whole_file_duration_limit", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.services.dictations.transcribe_with_team_stt",
        lambda db, **kwargs: "Preview-only clinician summary.",
    )

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Preview Dictation"})
    transcript_id = transcript_created.json()["id"]

    preview = client.post(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation/preview-audio-file",
        files={"audio": ("dictation.mp3", b"raw-dictation", "audio/mpeg")},
    )
    assert preview.status_code == 200
    assert preview.json() == {"text": "Preview-only clinician summary."}
    assert db_session.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == UUID(transcript_id))) is None
    assert db_session.scalar(select(PostConsultationDictationSegment)) is None

    client.post("/auth/logout")
    login(client, email=other.email, password="password-3")
    forbidden = client.post(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation/preview-audio-file",
        files={"audio": ("dictation.mp3", b"raw-dictation", "audio/mpeg")},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_quick_action_context_audio_preview_transcribes_for_owner_without_persisting(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Quick Action Context Preview")
    admin = make_user(email="admin-quick-action-context-preview@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-quick-action-context-preview@example.com", password="password-2", team=team, team_role=TeamRole.user)
    other = make_user(email="other-quick-action-context-preview@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Dictation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.post_consultation_dictation)

    monkeypatch.setattr(
        "app.services.dictations.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="quick-action-context.wav", content_type="audio/wav", data=b"normalized-context"),
    )
    monkeypatch.setattr("app.services.dictations.enforce_whole_file_duration_limit", lambda **kwargs: None)
    captured_stt_kwargs = {}

    def fake_transcribe_with_team_stt(db, **kwargs):
        captured_stt_kwargs.update(kwargs)
        return "Mention John Smith follow-up"

    monkeypatch.setattr("app.services.dictations.transcribe_with_team_stt", fake_transcribe_with_team_stt)

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Quick Action Context"})
    transcript_id = transcript_created.json()["id"]

    preview = client.post(
        f"/api/v1/transcripts/{transcript_id}/quick-action-context/preview-audio-file",
        files={"audio": ("context.webm", b"raw-context", "audio/webm")},
    )
    assert preview.status_code == 200
    assert preview.json() == {"text": "Mention John Smith follow-up"}
    assert captured_stt_kwargs["team_id"] == team.id
    assert captured_stt_kwargs["purpose"] is SttSelectionPurpose.post_consultation_dictation
    assert captured_stt_kwargs["audio_bytes"] == b"normalized-context"
    assert db_session.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == UUID(transcript_id))) is None
    assert db_session.scalar(select(PostConsultationDictationSegment)) is None

    client.post("/auth/logout")
    login(client, email=other.email, password="password-3")
    forbidden = client.post(
        f"/api/v1/transcripts/{transcript_id}/quick-action-context/preview-audio-file",
        files={"audio": ("context.webm", b"raw-context", "audio/webm")},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_post_consultation_dictation_upload_requires_dictation_stt_selection(
    client,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Missing Dictation STT")
    admin = make_user(email="admin-missing-dictation@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-missing-dictation@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Conversation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.conversation)

    monkeypatch.setattr(
        "app.services.dictations.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="dictation.wav", content_type="audio/wav", data=b"normalized-dictation"),
    )
    monkeypatch.setattr("app.services.dictations.enforce_whole_file_duration_limit", lambda **kwargs: None)

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Consultation Dictation"})
    transcript_id = transcript_created.json()["id"]

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation/audio-file",
        files={"audio": ("dictation.mp3", b"raw-dictation", "audio/mpeg")},
    )
    assert_error(
        uploaded,
        status_code=422,
        code="business_rule_violation",
        message="No active STT selection for team and purpose",
    )


def test_post_consultation_dictation_preview_requires_dictation_stt_selection(
    client,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Missing Dictation Preview STT")
    admin = make_user(email="admin-missing-dictation-preview@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-missing-dictation-preview@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Conversation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.conversation)

    monkeypatch.setattr(
        "app.services.dictations.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="dictation.wav", content_type="audio/wav", data=b"normalized-dictation"),
    )
    monkeypatch.setattr("app.services.dictations.enforce_whole_file_duration_limit", lambda **kwargs: None)

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Consultation Dictation"})
    transcript_id = transcript_created.json()["id"]

    preview = client.post(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation/preview-audio-file",
        files={"audio": ("dictation.mp3", b"raw-dictation", "audio/mpeg")},
    )
    assert_error(
        preview,
        status_code=422,
        code="business_rule_violation",
        message="No active STT selection for team and purpose",
    )


def test_transcribe_workspace_includes_post_consultation_dictation(
    client,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinic Workspace Dictation")
    admin = make_user(email="admin-workspace-dictation@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-workspace-dictation@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Dictation STT", model_name="whisper-1", available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.post_consultation_dictation)
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.conversation)

    monkeypatch.setattr(
        "app.services.dictations.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="dictation.wav", content_type="audio/wav", data=b"normalized-dictation"),
    )
    monkeypatch.setattr("app.services.dictations.enforce_whole_file_duration_limit", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.services.dictations.transcribe_with_team_stt",
        lambda db, **kwargs: "Extra dictation guidance.",
    )

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Workspace Dictation"})
    transcript_id = transcript_created.json()["id"]
    client.post(
        f"/api/v1/transcripts/{transcript_id}/post-consultation-dictation/audio-file",
        files={"audio": ("dictation.mp3", b"raw-dictation", "audio/mpeg")},
    )

    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript_id}")
    assert workspace.status_code == 200
    assert workspace.json()["post_consultation_dictation"]["effective_text"] == "Extra dictation guidance."
    assert workspace.json()["dictation_stt_selected"] is True


def test_transcribe_workspace_stt_health_plain_for_user_diagnostic_for_leader(
    client,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    from app.services.stt import clear_stt_health_cache

    clear_stt_health_cache()
    team = make_team(name="Clinic Workspace STT Health")
    admin = make_user(email="admin-workspace-stt-health@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-workspace-stt-health@example.com", password="password-2", team=team, team_role=TeamRole.user)
    leader = make_user(email="leader-workspace-stt-health@example.com", password="password-3", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin, label="Workspace Health STT", base_url="http://127.0.0.1:9100")
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.conversation)

    def fake_get(url, **kwargs):
        assert url == "http://127.0.0.1:9100/health"
        return FakeHttpxResponse({"error": {"code": "downstream_down"}}, status_code=503)

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Workspace STT health"})
    transcript_id = transcript_created.json()["id"]
    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript_id}")
    assert workspace.status_code == 200
    user_health = workspace.json()["stt_health"]
    assert user_health["status"] == "warning"
    assert user_health["message"] == "Speech service may be unavailable; transcription may fail."
    assert "details" not in user_health

    login(client, email=leader.email, password="password-3")
    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript_id}")
    assert workspace.status_code == 200
    leader_health = workspace.json()["stt_health"]
    assert leader_health["status"] == "warning"
    assert leader_health["details"]["status_code"] == 503
    assert leader_health["details"]["provider_error_code"] == "downstream_down"
    assert leader_health["details"]["health_url"] == "http://127.0.0.1:9100/health"


def test_transcribe_stt_health_recheck_bypasses_workspace_cache(
    client,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    from app.services.stt import clear_stt_health_cache

    clear_stt_health_cache()
    team = make_team(name="Clinic STT Health Recheck")
    admin = make_user(email="admin-stt-health-recheck@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-stt-health-recheck@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, label="Recheck STT", base_url="http://127.0.0.1:9200")
    make_stt_selection(config=config, actor=admin, purpose=SttSelectionPurpose.conversation)
    calls = {"count": 0}

    def fake_get(url, **kwargs):
        calls["count"] += 1
        return FakeHttpxResponse({"ok": True})

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    login(client, email=owner.email, password="password-2")
    transcript_created = client.post("/api/v1/transcripts/start", json={"title": "Recheck STT health"})
    transcript_id = transcript_created.json()["id"]
    first = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript_id}")
    second = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript_id}")
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1

    recheck = client.post("/api/v1/transcribe/stt-health/recheck")
    assert recheck.status_code == 200
    assert recheck.json()["status"] == "healthy"
    assert calls["count"] == 2


def test_stt_selection_rejects_config_with_missing_saved_secret(client, make_team, make_user, make_stt_config, monkeypatch):
    team = make_team(name="Clinic North")
    admin = make_user(email="admin-stt-secret@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-stt-secret@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(team=team, actor=admin, model_name="whisper-1")

    def fake_read_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
        raise AppError(
            502,
            "vault_read_failed",
            "STT provider credential is missing for the queued transcription config",
            {"team_id": str(team_id), "config_id": str(config_id)},
        )

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", fake_read_team_stt_bearer_token)

    login(client, email="leader-stt-secret@example.com", password="password-2")
    rejected = client.post(
        "/api/v1/stt-selection",
        json={"stt_config_id": str(config.id)},
    )

    assert_error(
        rejected,
        status_code=409,
        code="stt_config_secret_missing",
        message="The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
    )


def test_deepgram_stt_selection_requires_saved_secret(client, db_session, make_team, make_user, make_stt_config):
    team = make_team(name="Clinic Deepgram")
    admin = make_user(email="admin-deepgram-no-secret@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-deepgram-no-secret@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_stt_config(
        team=team,
        actor=admin,
        label="Deepgram STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="https://api.deepgram.com",
        transcribe_path="/v1/listen",
        model_name="nova-3",
        response_text_path="results.channels.0.alternatives.0.transcript",
        has_secret=False,
    )
    config.provider_preset = SttProviderPreset.deepgram.value
    db_session.add(config)
    db_session.commit()

    login(client, email="leader-deepgram-no-secret@example.com", password="password-2")
    rejected = client.post("/api/v1/stt-selection", json={"stt_config_id": str(config.id)})

    assert_error(
        rejected,
        status_code=409,
        code="stt_config_secret_missing",
        message="The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
    )


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


def test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", lambda **kwargs: ["gpt-4o-mini"])

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


def test_system_admin_can_create_and_finalize_llm_draft_without_secret_reveal(
    client, db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Clinic North")
    make_user(email="admin-llm-draft@example.com", password="password-1", is_system_admin=True)
    writes = []
    monkeypatch.setattr(
        "app.services.llm._list_openai_compatible_chat_models",
        lambda *, provider_preset, api_key, base_url: ["model-a", "model-b"],
    )
    monkeypatch.setattr(
        "app.services.llm.write_team_llm_bearer_token",
        lambda *, team_id, config_id, bearer_token: writes.append((team_id, config_id, bearer_token))
        or f"secret:openscribe/llm/team/{team_id}/config/{config_id}",
    )

    login(client, email="admin-llm-draft@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs/drafts",
        json={
            "team_id": str(team.id),
            "provider_preset": "openrouter",
            "bearer_token": "draft-secret",
        },
    )

    assert created.status_code == 200
    body = created.json()
    config_body = body["config"]
    config_id = UUID(config_body["id"])
    assert config_body["setup_status"] == "pending_model_selection"
    assert config_body["model_name"] is None
    assert config_body["is_active"] is False
    assert config_body["has_secret"] is True
    assert "draft-secret" not in str(body)
    assert body["available_models"] == ["model-a", "model-b"]
    assert writes and writes[0][2] == "draft-secret"

    persisted = db_session.get(TeamLlmConfig, config_id)
    assert persisted.setup_status == LlmConfigSetupStatus.pending_model_selection
    assert persisted.model_name is None
    assert persisted.available_models_json == ["model-a", "model-b"]
    assert persisted.vault_secret_ref.startswith("secret:openscribe/llm/team/")

    options = client.get(f"/api/v1/llm-selection/options?team_id={team.id}")
    assert options.status_code == 200
    assert options.json() == []

    finalized = client.post(
        f"/api/v1/llm-configs/{config_id}/finalize",
        json={
            "team_id": str(team.id),
            "label": "OpenRouter North",
            "model_name": "model-b",
            "is_active": True,
        },
    )
    assert finalized.status_code == 200
    final_body = finalized.json()
    assert final_body["setup_status"] == "ready"
    assert final_body["model_name"] == "model-b"
    assert final_body["is_active"] is True

    options = client.get(f"/api/v1/llm-selection/options?team_id={team.id}")
    assert [item["id"] for item in options.json()] == [str(config_id)]


def test_llm_draft_invalid_key_creates_no_config_or_vault_secret(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bad LLM Key")
    make_user(email="admin-bad-llm-key@example.com", password="password-1", is_system_admin=True)
    writes: list[str] = []

    def reject_key(**kwargs):
        raise AppError(401, "llm_invalid_credential", "The API key was rejected by the provider.")

    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", reject_key)
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda **kwargs: writes.append(kwargs["bearer_token"]) or "secret:unexpected")

    login(client, email="admin-bad-llm-key@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "openrouter", "label": "Bad Router", "bearer_token": "bad-key"},
    )

    assert_error(created, status_code=401, code="llm_invalid_credential", message="The API key was rejected by the provider.")
    assert writes == []
    assert db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id)) is None


def test_llm_save_invalid_key_with_manual_model_creates_no_config_or_vault_secret(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bad Manual LLM Key")
    make_user(email="admin-bad-manual-llm-key@example.com", password="password-1", is_system_admin=True)
    writes: list[str] = []

    def reject_key(**kwargs):
        raise AppError(401, "llm_invalid_credential", "The API key was rejected by the provider.")

    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", reject_key)
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda **kwargs: writes.append(kwargs["bearer_token"]) or "secret:unexpected")

    login(client, email="admin-bad-manual-llm-key@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "provider_preset": "openrouter",
            "label": "Bad Manual Router",
            "bearer_token": "bad-key",
            "model_name": "manual/router-model",
            "is_active": True,
        },
    )

    assert_error(created, status_code=401, code="llm_invalid_credential", message="The API key was rejected by the provider.")
    assert writes == []
    assert db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id)) is None


def test_llm_draft_preserves_supplied_label_and_falls_back_when_omitted(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Draft Labels")
    make_user(email="admin-llm-draft-labels@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", lambda *, provider_preset, api_key, base_url: ["model-a"])

    login(client, email="admin-llm-draft-labels@example.com", password="password-1")
    supplied = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "openrouter", "label": "Router Custom", "bearer_token": "router-key"},
    )
    fallback = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "openai", "bearer_token": "openai-key"},
    )

    assert supplied.status_code == 200
    assert supplied.json()["config"]["label"] == "Router Custom"
    assert fallback.status_code == 200
    assert fallback.json()["config"]["label"] == "OpenAI · Clinic Draft Labels"


def test_stt_revision_stays_pending_then_promotes_into_active_config(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinic STT Revision")
    other_team = make_team(name="Other STT Revision")
    admin = make_user(email="admin-stt-revision@example.com", password="password-1", is_system_admin=True)
    active = make_stt_config(team=team, actor=admin, label="Current STT", base_url="https://old-stt.example", model_name="old-model")
    selection = make_stt_selection(config=active, actor=admin)
    monkeypatch.setattr(
        "app.services.stt.inspect_stt_contract",
        lambda *args, **kwargs: SttInspectResult(
            adapter_kind=SttAdapterKind.openai_compatible_rest,
            base_url="https://new-stt.example",
            openapi_path=None,
            transcribe_path="/v1/audio/transcriptions",
            model_name=None,
            available_models=["new-model"],
            file_field_name="file",
            model_field_name="model",
            language=None,
            language_field_name="language",
            response_text_path="text",
            extra_form_fields_json={},
            candidate_paths=[],
            operation_summary=None,
            field_tips=[],
            notes=[],
        ),
    )
    monkeypatch.setattr("app.services.stt.write_team_stt_bearer_token", lambda **kwargs: f"secret:test/stt/{kwargs['config_id']}")
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", lambda **kwargs: None)

    login(client, email=admin.email, password="password-1")
    cross_team = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(other_team.id), "provider_preset": "custom_openai_compatible", "base_url": "https://new-stt.example", "bearer_token": "key", "revision_of_config_id": str(active.id)},
    )
    assert_error(cross_team, status_code=404, code="not_found", message="Ready STT config not found")

    created = client.post(
        "/api/v1/stt-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "custom_openai_compatible", "base_url": "https://new-stt.example", "bearer_token": "key", "revision_of_config_id": str(active.id)},
    )
    assert created.status_code == 200
    revision_id = UUID(created.json()["config"]["id"])
    db_session.refresh(active)
    assert (active.base_url, active.model_name, active.is_active) == ("https://old-stt.example", "old-model", True)
    listed = client.get(f"/api/v1/stt-configs?team_id={team.id}")
    assert [item["id"] for item in listed.json()] == [str(active.id)]
    assert resolve_selected_team_stt(db_session, team_id=team.id)[1].id == active.id

    finalized = client.post(
        f"/api/v1/stt-configs/{revision_id}/finalize",
        json={"team_id": str(team.id), "config_id": str(revision_id), "label": "Current STT", "model_name": "new-model", "language": "en-GB", "is_active": True},
    )
    assert finalized.status_code == 200
    assert finalized.json()["id"] == str(active.id)
    db_session.refresh(active)
    assert (active.base_url, active.model_name, active.language) == ("https://new-stt.example", "new-model", "en-GB")
    assert db_session.get(TeamSttConfig, revision_id) is None
    assert db_session.get(TeamSttSelection, selection.id).stt_config_id == active.id


def test_llm_revision_stays_pending_then_promotes_into_active_config(
    client, db_session, make_team, make_user, make_llm_config, make_llm_selection, monkeypatch
):
    team = make_team(name="Clinic LLM Revision")
    other_team = make_team(name="Other LLM Revision")
    admin = make_user(email="admin-llm-revision@example.com", password="password-1", is_system_admin=True)
    active = make_llm_config(team=team, actor=admin, label="Current LLM", base_url="https://old-llm.example/v1", model_name="old-model")
    selection = make_llm_selection(config=active, actor=admin)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", lambda **kwargs: ["new-model"])
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda **kwargs: f"secret:test/llm/{kwargs['config_id']}")
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", lambda **kwargs: None)

    login(client, email=admin.email, password="password-1")
    payload = {"provider_preset": "custom_openai_compatible", "base_url": "https://new-llm.example/v1", "bearer_token": "key", "revision_of_config_id": str(active.id)}
    cross_team = client.post("/api/v1/llm-configs/drafts", json={**payload, "team_id": str(other_team.id)})
    assert_error(cross_team, status_code=404, code="not_found", message="Ready LLM config not found")

    created = client.post("/api/v1/llm-configs/drafts", json={**payload, "team_id": str(team.id)})
    assert created.status_code == 200
    revision_id = UUID(created.json()["config"]["id"])
    db_session.refresh(active)
    assert (active.base_url, active.model_name, active.is_active) == ("https://old-llm.example/v1", "old-model", True)
    listed = client.get(f"/api/v1/llm-configs?team_id={team.id}")
    assert [item["id"] for item in listed.json()] == [str(active.id)]
    assert db_session.get(TeamLlmSelection, selection.id).llm_config_id == active.id

    finalized = client.post(
        f"/api/v1/llm-configs/{revision_id}/finalize",
        json={"team_id": str(team.id), "config_id": str(revision_id), "label": "Current LLM", "model_name": "new-model", "is_active": True},
    )
    assert finalized.status_code == 200
    assert finalized.json()["id"] == str(active.id)
    db_session.refresh(active)
    assert (active.base_url, active.model_name) == ("https://new-llm.example/v1", "new-model")
    assert db_session.get(TeamLlmConfig, revision_id) is None
    assert db_session.get(TeamLlmSelection, selection.id).llm_config_id == active.id


def test_provider_revisions_reuse_saved_credentials_and_cancel_preserves_shared_secret(
    client, db_session, make_team, make_user, make_stt_config, make_llm_config, monkeypatch
):
    team = make_team(name="Clinic Credential Reuse")
    admin = make_user(email="admin-credential-reuse@example.com", password="password-1", is_system_admin=True)
    stt = make_stt_config(team=team, actor=admin, label="Saved STT", base_url="https://api.openai.com/v1")
    llm = make_llm_config(team=team, actor=admin, label="Saved LLM", provider_preset="openai", base_url="https://api.openai.com/v1", available_models_json=["model-a"])
    stt_ref, llm_ref = stt.vault_secret_ref, llm.vault_secret_ref
    deleted_stt, deleted_llm = [], []
    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "saved-stt-key")
    monkeypatch.setattr("app.services.stt.inspect_stt_contract", lambda *args, **kwargs: SttInspectResult(adapter_kind=SttAdapterKind.openai_compatible_rest, base_url="https://api.openai.com/v1", openapi_path=None, transcribe_path="/audio/transcriptions", model_name=None, available_models=["whisper-1"], file_field_name="file", model_field_name="model", language=None, language_field_name="language", response_text_path="text", extra_form_fields_json={}, candidate_paths=[], operation_summary=None, field_tips=[], notes=[]))
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", lambda **kwargs: deleted_stt.append(kwargs))
    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda **kwargs: "saved-llm-key")
    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", lambda **kwargs: ["model-a"])
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", lambda **kwargs: deleted_llm.append(kwargs))
    login(client, email=admin.email, password="password-1")

    stt_created = client.post("/api/v1/stt-configs/drafts", json={"team_id": str(team.id), "provider_preset": "openai", "revision_of_config_id": str(stt.id)})
    llm_created = client.post("/api/v1/llm-configs/drafts", json={"team_id": str(team.id), "provider_preset": "openai", "revision_of_config_id": str(llm.id)})
    assert stt_created.status_code == 200 and llm_created.status_code == 200
    stt_revision = db_session.get(TeamSttConfig, UUID(stt_created.json()["config"]["id"]))
    llm_revision = db_session.get(TeamLlmConfig, UUID(llm_created.json()["config"]["id"]))
    assert stt_revision.vault_secret_ref == stt_ref
    assert llm_revision.vault_secret_ref == llm_ref

    assert client.post(f"/admin/stt-configs/{stt_revision.id}/draft-cancel", data={"team_id": str(team.id)}).status_code == 200
    assert client.post(f"/admin/llm-configs/{llm_revision.id}/draft-cancel", data={"team_id": str(team.id)}).status_code == 200
    db_session.refresh(stt)
    db_session.refresh(llm)
    assert stt.vault_secret_ref == stt_ref and llm.vault_secret_ref == llm_ref
    assert deleted_stt == [] and deleted_llm == []

def test_llm_revision_finalize_rejects_active_target_with_queued_document(
    client, db_session, make_team, make_user, make_llm_config, monkeypatch
):
    team = make_team(name="Clinic LLM Revision Guard")
    admin = make_user(email="admin-llm-revision-guard@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-llm-revision-guard@example.com", password="password-2", team=team, team_role=TeamRole.user)
    active = make_llm_config(team=team, actor=admin, label="Guarded LLM", model_name="old-model")
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Guarded generation",
        current_draft_text_encrypted="Encrypted test draft",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Encrypted test version",
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(
        GeneratedDocument(
            owner_user_id=owner.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=version.id,
            llm_config_id=active.id,
            generator_type=GeneratedDocumentGeneratorType.followup,
            source_template_name="Follow-up",
            status=GeneratedDocumentStatus.queued,
            title="Queued output",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="",
            edited_output_text_encrypted="",
            retention_expires_at=owner.created_at,
        )
    )
    db_session.commit()
    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", lambda **kwargs: ["new-model"])
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda **kwargs: f"secret:test/llm/{kwargs['config_id']}")

    login(client, email=admin.email, password="password-1")
    created = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "openai", "bearer_token": "key", "revision_of_config_id": str(active.id)},
    )
    revision_id = created.json()["config"]["id"]
    finalized = client.post(
        f"/api/v1/llm-configs/{revision_id}/finalize",
        json={"team_id": str(team.id), "config_id": revision_id, "label": "Guarded LLM", "model_name": "new-model", "is_active": True},
    )

    assert_error(finalized, status_code=409, code="conflict", message="Cannot edit this LLM config while generated documents are queued or processing")
    db_session.refresh(active)
    assert active.model_name == "old-model"
    assert db_session.get(TeamLlmConfig, UUID(revision_id)) is not None

def test_llm_config_labels_are_unique_per_team_case_and_trim_normalized(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Unique LLM")
    other_team = make_team(name="Clinic Unique Other")
    admin = make_user(email="admin-llm-unique@example.com", password="password-1", is_system_admin=True)
    existing = make_llm_config(team=team, actor=admin, label="OpenRouter", model_name="model-a", available_models_json=["model-a"])
    other = make_llm_config(team=other_team, actor=admin, label="openrouter", model_name="model-a", available_models_json=["model-a"])
    monkeypatch.setattr("app.services.llm._list_openai_compatible_chat_models", lambda *, provider_preset, api_key, base_url: ["model-a"])

    login(client, email="admin-llm-unique@example.com", password="password-1")
    duplicate_draft = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "openrouter", "label": " openrouter ", "bearer_token": "router-key"},
    )

    assert_error(duplicate_draft, status_code=409, code="conflict", message="An LLM provider with this name already exists for this team.")
    assert other.label == "openrouter"

    draft = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "openai", "label": "Draft Unique", "bearer_token": "openai-key"},
    )
    assert draft.status_code == 200
    draft_id = draft.json()["config"]["id"]
    duplicate_finalize = client.post(
        f"/api/v1/llm-configs/{draft_id}/finalize",
        json={"team_id": str(team.id), "config_id": draft_id, "label": " openrouter ", "model_name": "model-a", "is_active": True},
    )
    assert_error(duplicate_finalize, status_code=409, code="conflict", message="An LLM provider with this name already exists for this team.")
    db_session.refresh(existing)
    assert existing.label == "OpenRouter"


def test_llm_draft_manual_finalize_and_pending_selection_rejected(
    client, db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Clinic Draft Manual")
    make_user(email="admin-llm-manual@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-llm-manual@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda *, base_url, bearer_token: [])

    login(client, email="admin-llm-manual@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs/drafts",
        json={"team_id": str(team.id), "provider_preset": "ollama", "base_url": "http://localhost:11434"},
    )
    assert created.status_code == 200
    config_id = UUID(created.json()["config"]["id"])
    assert created.json()["discovery_status"] == "manual_required"

    client.post("/api/v1/auth/logout")
    login(client, email="leader-llm-manual@example.com", password="password-2")
    rejected = client.post("/api/v1/llm-selection", json={"llm_config_id": str(config_id)})
    assert_error(rejected, status_code=404, code="not_found", message="Selectable LLM config not found")

    client.post("/api/v1/auth/logout")
    login(client, email="admin-llm-manual@example.com", password="password-1")
    finalized = client.post(
        f"/api/v1/llm-configs/{config_id}/finalize",
        json={
            "team_id": str(team.id),
            "config_id": str(config_id),
            "label": "Ollama Manual",
            "model_name": "llama3.2",
            "is_active": False,
        },
    )
    assert finalized.status_code == 200
    persisted = db_session.get(TeamLlmConfig, config_id)
    assert persisted.setup_status == LlmConfigSetupStatus.ready
    assert persisted.available_models_json == ["llama3.2"]
    assert persisted.inspection_metadata_json["manual_model_name"] == "llama3.2"
    assert client.get(f"/api/v1/llm-selection/options?team_id={team.id}").json() == []


def test_llm_draft_replace_credential_reruns_discovery_and_resets_pending(
    client, db_session, make_team, make_user, make_llm_config, monkeypatch
):
    team = make_team(name="Clinic Replace")
    admin = make_user(email="admin-llm-replace@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        label="Ready LLM",
        model_name="old-model",
        available_models_json=["old-model"],
        is_active=True,
    )
    writes = []
    monkeypatch.setattr(
        "app.services.llm._list_openai_compatible_chat_models",
        lambda *, provider_preset, api_key, base_url: ["new-model"],
    )
    monkeypatch.setattr(
        "app.services.llm.write_team_llm_bearer_token",
        lambda *, team_id, config_id, bearer_token: writes.append(bearer_token)
        or f"secret:openscribe/llm/team/{team_id}/config/{config_id}/new",
    )

    login(client, email="admin-llm-replace@example.com", password="password-1")
    replaced = client.post(
        f"/api/v1/llm-configs/{config.id}/replace-credential",
        json={"team_id": str(team.id), "config_id": str(config.id), "bearer_token": "new-secret"},
    )

    assert replaced.status_code == 200
    body = replaced.json()
    assert "new-secret" not in str(body)
    assert body["config"]["setup_status"] == "pending_model_selection"
    assert body["config"]["is_active"] is False
    assert body["available_models"] == ["new-model"]
    assert writes == ["new-secret"]
    persisted = db_session.get(TeamLlmConfig, config.id)
    assert persisted.setup_status == LlmConfigSetupStatus.pending_model_selection
    assert persisted.model_name is None
    assert persisted.available_models_json == ["new-model"]


def test_llm_provider_preset_catalog_and_inference():
    assert list(LLM_PROVIDER_PRESETS) == [
        "openai",
        "openrouter",
        "xai",
        "groq",
        "mistral",
        "deepseek",
        "together",
        "ollama",
        "bedrock_http_gateway",
        "custom_openai_compatible",
    ]
    assert LLM_PROVIDER_PRESETS["openrouter"].default_base_url == "https://openrouter.ai/api/v1"
    assert LLM_PROVIDER_PRESETS["custom_openai_compatible"].default_base_url is None
    assert LLM_PROVIDER_PRESETS["bedrock_http_gateway"].default_bedrock_region == "eu-west-2"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://api.openai.com/v1") == "openai"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://openrouter.ai/api/v1") == "openrouter"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://api.x.ai/v1") == "xai"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://api.groq.com/openai/v1") == "groq"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://api.deepseek.com") == "deepseek"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://api.mistral.ai/v1") == "mistral"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://api.together.xyz/v1") == "together"
    assert infer_llm_provider_preset(LlmAdapterKind.bedrock_chat, "https://bedrock-mantle.eu-west-2.api.aws/v1") == "bedrock_http_gateway"
    assert infer_llm_provider_preset(LlmAdapterKind.ollama_chat, "http://localhost:11434") == "ollama"
    assert infer_llm_provider_preset(LlmAdapterKind.openai_chat, "https://llm.example.com/v1") == "custom_openai_compatible"
    assert apply_provider_defaults(provider_preset="bedrock_http_gateway", base_url="", bedrock_region="us-east-1")[2] == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert apply_provider_defaults(provider_preset="bedrock_http_gateway", base_url="http://localhost:11434", bedrock_region="us-west-2")[2] == "https://bedrock-mantle.us-west-2.api.aws/v1"
    assert apply_provider_defaults(provider_preset="bedrock_http_gateway", base_url="https://bedrock-mantle.eu-west-2.api.aws/v1", bedrock_region=None)[3] == "eu-west-2"


def test_llm_model_filtering_only_applies_openai_prefix_rules_to_openai():
    models = ["gpt-4.1-mini", "openai/text-embedding-3-small", "mistralai/mistral-large", "grok-4", "llama-3.3-70b"]

    assert filter_discovered_models("openai", models) == ["gpt-4.1-mini"]
    assert filter_discovered_models("openrouter", models) == ["gpt-4.1-mini", "grok-4", "llama-3.3-70b", "mistralai/mistral-large"]
    assert filter_discovered_models("xai", models) == ["gpt-4.1-mini", "grok-4", "llama-3.3-70b", "mistralai/mistral-large"]


def test_llm_schema_provider_defaults_use_shared_preset_catalog(monkeypatch):
    original = LLM_PROVIDER_PRESETS["openrouter"]
    monkeypatch.setitem(LLM_PROVIDER_PRESETS, "openrouter", replace(original, default_base_url="https://router.test/v1"))

    inspected = LlmInspectRequest(team_id=uuid4(), provider_preset="openrouter")
    upserted = LlmConfigUpsert(team_id=uuid4(), label="Router", provider_preset="openrouter", bearer_token="key", model_name="model-a")

    assert inspected.base_url == "https://router.test/v1"
    assert upserted.base_url == "https://router.test/v1"


def test_mistral_model_discovery_uses_chat_capability_metadata(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "mistral-large-latest", "capabilities": {"completion_chat": True}},
                    {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
                    {"id": "mistral-old", "archived": True, "capabilities": {"completion_chat": True}},
                    {"id": "mistral-no-capability", "capabilities": {}},
                ]
            }

    monkeypatch.setattr("app.services.llm.httpx.get", lambda *args, **kwargs: Response())

    assert _list_mistral_chat_models(api_key="mistral-key", base_url="https://api.mistral.ai/v1") == ["mistral-large-latest"]


def test_together_model_discovery_uses_type_metadata(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {"id": "meta-llama/Llama-3.3-70B-Instruct", "type": "chat"},
                {"name": "Qwen/Qwen2.5-Coder", "type": "code"},
                {"id": "mistralai/Mixtral", "type": "language"},
                {"id": "black-forest-labs/FLUX.1", "type": "image"},
                {"id": "BAAI/bge-base", "type": "embedding"},
                {"id": "rerank-model", "type": "rerank"},
                {"id": "moderation-model", "type": "moderation"},
            ]

    monkeypatch.setattr("app.services.llm.httpx.get", lambda *args, **kwargs: Response())

    assert _list_together_chat_models(api_key="together-key", base_url="https://api.together.xyz/v1") == [
        "Qwen/Qwen2.5-Coder",
        "meta-llama/Llama-3.3-70B-Instruct",
        "mistralai/Mixtral",
    ]


def test_llm_provider_preset_saves_and_reclassifies_base_url_override(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Presets")
    make_user(email="admin-llm-presets@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", lambda **kwargs: ["anthropic/claude-sonnet-4", "text-embedding-3-small"])

    login(client, email="admin-llm-presets@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Router",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "model_name": "anthropic/claude-sonnet-4",
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["provider_preset"] == "openrouter"
    assert body["base_url"] == "https://openrouter.ai/api/v1"
    assert body["available_models_json"] == ["anthropic/claude-sonnet-4"]
    assert "vault_secret_ref" not in body

    config = db_session.get(TeamLlmConfig, UUID(body["id"]))
    assert config is not None
    assert config.inspection_metadata_json["discovery_status"] == "fetched"

    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", lambda **kwargs: ["gpt-4o-mini"])
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "OpenAI-ish",
            "provider_preset": "openai",
            "base_url": "https://gateway.example.com/v1",
            "bearer_token": "new-key",
            "credential_action": "replace",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["provider_preset"] == "custom_openai_compatible"


def test_llm_save_validates_model_against_successful_live_discovery(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Model Validation")
    make_user(email="admin-llm-model-validation@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", lambda **kwargs: ["model-a", "model-b"])

    login(client, email="admin-llm-model-validation@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Router",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "model_name": "model-a",
            "is_active": True,
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["model_name"] == "model-a"
    assert body["inspection_metadata_json"]["inspected_at"]

    missing = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Router Missing",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "model_name": "missing-model",
            "is_active": True,
        },
    )
    assert_error(
        missing,
        status_code=422,
        code="business_rule_violation",
        message="Selected model is not available for this provider",
    )

    defaulted = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Router Default",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "is_active": True,
        },
    )
    assert defaulted.status_code == 200
    assert defaulted.json()["model_name"] == "model-a"


def test_llm_config_edit_validates_model_against_saved_provider_models(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic LLM Saved Model Validation")
    admin = make_user(email="admin-llm-saved-model-validation@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(
        team=team,
        actor=admin,
        provider_preset="openai",
        base_url="https://api.openai.com/v1",
        available_models_json=["model-a"],
        model_name="model-a",
        has_secret=True,
    )

    login(client, email="admin-llm-saved-model-validation@example.com", password="password-1")
    edited = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Edited LLM",
            "provider_preset": "openai",
            "base_url": "https://api.openai.com/v1",
            "credential_action": "keep",
            "model_name": "model-b",
            "is_active": True,
        },
    )

    assert_error(
        edited,
        status_code=422,
        code="business_rule_violation",
        message="Selected model is not available for this provider",
    )
    db_session.refresh(config)
    assert config.model_name == "model-a"
    assert config.available_models_json == ["model-a"]


def test_llm_save_rejects_missing_model_when_discovery_fails(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Missing Manual Model")
    make_user(email="admin-llm-missing-manual@example.com", password="password-1", is_system_admin=True)

    def fail_discovery(**kwargs):
        raise AppError(502, "llm_inspection_failed", "Could not load available models")

    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", fail_discovery)
    login(client, email="admin-llm-missing-manual@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Router",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "is_active": True,
        },
    )

    assert_error(
        created,
        status_code=422,
        code="business_rule_violation",
        message="Model name is required. Inspect models successfully or enter a model name manually.",
    )


def test_llm_zero_model_discovery_requires_manual_model(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Empty Discovery")
    make_user(email="admin-llm-empty-discovery@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", lambda **kwargs: [])

    login(client, email="admin-llm-empty-discovery@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/llm-configs/inspect",
        json={"team_id": str(team.id), "provider_preset": "openrouter", "bearer_token": "router-key"},
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["available_models"] == []
    assert body["available_model_options"] == []
    assert body["discovery_status"] == "manual_required"
    assert body["default_model_source"] == "manual"
    assert body["warnings"] == ["No compatible chat models were returned. Enter a model name manually."]

    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Empty Router",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "model_name": "manual/router-model",
            "is_active": True,
        },
    )

    assert created.status_code == 200
    created_body = created.json()
    assert created_body["available_models_json"] == ["manual/router-model"]
    assert created_body["inspection_metadata_json"]["discovery_status"] == "manual_required"
    assert created_body["inspection_metadata_json"]["default_model_source"] == "manual"
    assert created_body["inspection_metadata_json"]["manual_model_name"] == "manual/router-model"
    config = db_session.get(TeamLlmConfig, UUID(created_body["id"]))
    assert config is not None
    assert config.inspection_metadata_json["warnings"] == ["No compatible chat models were returned. Enter a model name manually."]


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


def test_system_admin_can_explicitly_remove_saved_ollama_secret(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Ollama Remove")
    admin = make_user(email="admin-remove-llm-secret@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, adapter_kind=LlmAdapterKind.ollama_chat, base_url="http://localhost:11434", model_name="llama3.2", has_secret=True)
    deleted: list[str] = []

    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda **kwargs: ["llama3.2"])
    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda *, team_id, config_id: "saved-ollama-secret")
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", lambda *, team_id, config_id: deleted.append(str(config_id)))

    login(client, email="admin-remove-llm-secret@example.com", password="password-1")
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Local Ollama",
            "adapter_kind": "ollama_chat",
            "base_url": "http://localhost:11434",
            "credential_action": "remove",
            "model_name": "llama3.2",
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    persisted = db_session.get(TeamLlmConfig, config.id)
    assert persisted is not None
    assert persisted.auth_mode.value == "none"
    assert persisted.vault_secret_ref == ""
    assert deleted == [str(config.id)]


def test_llm_secret_remove_deletes_vault_secret_before_db_commit(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Ollama Remove Ordering")
    admin = make_user(email="admin-remove-llm-secret-order@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, adapter_kind=LlmAdapterKind.ollama_chat, base_url="http://localhost:11434", model_name="llama3.2", has_secret=True)
    events: list[str] = []
    original_commit = db_session.commit

    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda **kwargs: ["llama3.2"])
    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda *, team_id, config_id: "saved-ollama-secret")
    login(client, email="admin-remove-llm-secret-order@example.com", password="password-1")
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", lambda *, team_id, config_id: events.append("delete"))
    monkeypatch.setattr(db_session, "commit", lambda: (events.append("commit"), original_commit())[1])
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Local Ollama",
            "adapter_kind": "ollama_chat",
            "base_url": "http://localhost:11434",
            "credential_action": "remove",
            "model_name": "llama3.2",
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    assert events[-2:] == ["delete", "commit"]
    persisted = db_session.get(TeamLlmConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref == ""


def test_llm_secret_remove_keeps_vault_secret_when_db_commit_fails(db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Ollama Commit Failure")
    admin = make_user(email="admin-remove-llm-secret-commit-fails@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, adapter_kind=LlmAdapterKind.ollama_chat, base_url="http://localhost:11434", model_name="llama3.2", has_secret=True)
    original_ref = config.vault_secret_ref
    events: list[str] = []

    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda **kwargs: ["llama3.2"])
    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda *, team_id, config_id: "saved-ollama-secret")
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", lambda *, team_id, config_id: events.append("delete"))
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", lambda *, team_id, config_id, bearer_token: events.append(f"restore:{bearer_token}") or original_ref)
    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))

    with pytest.raises(RuntimeError, match="commit failed"):
        upsert_llm_config_service(
            db_session,
            admin,
            LlmConfigUpsert(
                config_id=config.id,
                team_id=team.id,
                label="Local Ollama",
                adapter_kind=LlmAdapterKind.ollama_chat,
                base_url="http://localhost:11434",
                credential_action="remove",
                model_name="llama3.2",
                is_active=True,
            ),
        )

    assert events == ["delete", "restore:saved-ollama-secret"]
    db_session.rollback()
    persisted = db_session.get(TeamLlmConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref == original_ref


def test_llm_secret_remove_fails_closed_when_vault_delete_fails(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Ollama Cleanup Failure")
    admin = make_user(email="admin-remove-llm-secret-cleanup-fails@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, adapter_kind=LlmAdapterKind.ollama_chat, base_url="http://localhost:11434", model_name="llama3.2", has_secret=True)
    original_ref = config.vault_secret_ref

    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda **kwargs: ["llama3.2"])
    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda *, team_id, config_id: "saved-ollama-secret")

    def fail_delete(*, team_id, config_id):
        raise AppError(502, "vault_unavailable", "Vault unavailable")

    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", fail_delete)

    login(client, email="admin-remove-llm-secret-cleanup-fails@example.com", password="password-1")
    removed = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Local Ollama",
            "adapter_kind": "ollama_chat",
            "base_url": "http://localhost:11434",
            "credential_action": "remove",
            "model_name": "llama3.2",
            "is_active": True,
        },
    )

    assert_error(
        removed,
        status_code=502,
        code="vault_unavailable",
        message="Vault unavailable",
    )
    db_session.expire_all()
    persisted = db_session.get(TeamLlmConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref == original_ref
    assert persisted.auth_mode.value == "bearer"


def test_llm_secret_remove_clears_stale_vault_ref_when_read_fails(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic Ollama Stale Secret Ref")
    admin = make_user(email="admin-remove-llm-secret-stale-ref@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, actor=admin, adapter_kind=LlmAdapterKind.ollama_chat, base_url="http://localhost:11434", model_name="llama3.2", has_secret=True)
    deleted: list[str] = []

    monkeypatch.setattr("app.services.llm._list_ollama_chat_models", lambda **kwargs: ["llama3.2"])

    def fail_read(*, team_id, config_id):
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", fail_read)
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", lambda *, team_id, config_id: deleted.append(str(config_id)))

    login(client, email="admin-remove-llm-secret-stale-ref@example.com", password="password-1")
    removed = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Local Ollama",
            "adapter_kind": "ollama_chat",
            "base_url": "http://localhost:11434",
            "credential_action": "remove",
            "model_name": "llama3.2",
            "is_active": True,
        },
    )

    assert removed.status_code == 200
    assert deleted == [str(config.id)]
    persisted = db_session.get(TeamLlmConfig, config.id)
    assert persisted is not None
    assert persisted.vault_secret_ref == ""
    assert persisted.auth_mode.value == "none"


def test_system_admin_cannot_keep_missing_secret_when_switching_llm_to_required_adapter(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic LLM Missing Secret")
    admin = make_user(email="admin-missing-llm-secret@example.com", password="password-1", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(
        team=team,
        actor=admin,
        adapter_kind=LlmAdapterKind.ollama_chat,
        base_url="http://localhost:11434",
        model_name="llama3.2",
        available_models_json=["llama3.2"],
        has_secret=False,
    )

    monkeypatch.setattr("app.services.llm._list_openai_chat_models", lambda **kwargs: ["gpt-4o-mini"])

    login(client, email="admin-missing-llm-secret@example.com", password="password-1")
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Clinic OpenAI",
            "adapter_kind": "openai_chat",
            "base_url": "https://api.openai.com/v1",
            "credential_action": "keep",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )

    assert_error(
        updated,
        status_code=422,
        code="business_rule_violation",
        message="This LLM provider requires a saved bearer token",
    )
    db_session.refresh(config)
    assert config.adapter_kind is LlmAdapterKind.ollama_chat
    assert config.auth_mode.value == "none"
    assert config.vault_secret_ref == ""


def test_system_admin_can_inspect_bedrock_chat_models(client, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bedrock")
    make_user(email="admin-bedrock@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_bedrock_chat_models",
        lambda **kwargs: ["anthropic.claude-3-7-sonnet-20250219-v1:0", "amazon.nova-micro-v1:0"],
    )

    login(client, email="admin-bedrock@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/llm-configs/inspect",
        json={
            "team_id": str(team.id),
            "adapter_kind": "bedrock_chat",
            "base_url": "",
            "bedrock_region": "us-east-1",
            "bearer_token": "bedrock-api-key",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["adapter_kind"] == "bedrock_chat"
    assert body["base_url"] == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert body["available_models"] == [
        "anthropic.claude-3-7-sonnet-20250219-v1:0",
        "amazon.nova-micro-v1:0",
    ]
    assert body["discovery_status"] == "fetched"
    assert body["default_model_source"] == "provider"
    assert body["requires_bearer_token"] is True
    assert body["supports_model_discovery"] is True
    assert body["warnings"] == []
    assert "Amazon Bedrock model list" in body["notes"][0]


def test_system_admin_llm_inspection_exposes_manual_required_state(client, make_team, make_user):
    team = make_team(name="Clinic Bedrock Manual")
    make_user(email="admin-bedrock-manual@example.com", password="password-1", is_system_admin=True)

    login(client, email="admin-bedrock-manual@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/llm-configs/inspect",
        json={"team_id": str(team.id), "adapter_kind": "bedrock_chat", "bedrock_region": "us-east-1"},
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["available_models"] == []
    assert body["model_name"] is None
    assert body["discovery_status"] == "manual_required"
    assert body["default_model_source"] == "manual"
    assert body["warnings"] == ["Could not load region-specific Bedrock models. Enter a model ID manually or inspect again with credentials."]


def test_system_admin_saved_llm_inspection_uses_vault_key_and_updates_models(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic LLM Saved Inspect")
    admin = make_user(email="admin-saved-llm-inspect@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, available_models_json=["old-model"], model_name="old-model", has_secret=True)
    reads: list[str] = []

    def fake_read_team_llm_bearer_token(*, team_id, config_id):
        reads.append(str(config_id))
        return "saved-llm-key"

    def fake_list_openai_chat_models(*, api_key, base_url):
        assert api_key == "saved-llm-key"
        assert base_url == "https://api.openai.com/v1"
        return ["gpt-4.1", "gpt-4.1-mini"]

    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", fake_read_team_llm_bearer_token)
    monkeypatch.setattr("app.services.llm._list_openai_chat_models", fake_list_openai_chat_models)

    login(client, email="admin-saved-llm-inspect@example.com", password="password-1")
    inspected = client.post(f"/api/v1/llm-configs/{config.id}/inspect?team_id={team.id}")

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["discovery_status"] == "fetched"
    assert body["available_models"] == ["gpt-4.1", "gpt-4.1-mini"]
    assert "saved-llm-key" not in inspected.text
    assert reads == [str(config.id)]
    db_session.refresh(config)
    assert config.available_models_json == ["gpt-4.1", "gpt-4.1-mini"]
    assert config.model_name == "gpt-4.1"
    assert config.inspection_metadata_json["inspected_at"]


def test_llm_manual_model_after_failed_discovery_is_selectable_and_metadata_is_service_owned(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic LLM Manual")
    make_user(email="admin-llm-manual@example.com", password="password-1", is_system_admin=True)
    make_user(email="leader-llm-manual@example.com", password="password-2", team=team, team_role=TeamRole.leader)

    def fail_discovery(**kwargs):
        raise AppError(502, "llm_inspection_failed", "Could not load available models")

    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", fail_discovery)
    login(client, email="admin-llm-manual@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Manual Router",
            "provider_preset": "openrouter",
            "bearer_token": "router-key",
            "model_name": "manual/router-model",
            "inspection_metadata_json": {"discovery_status": "forged", "warnings": ["client controlled"]},
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["available_models_json"] == ["manual/router-model"]
    assert body["inspection_metadata_json"]["discovery_status"] == "manual_required"
    assert body["inspection_metadata_json"]["manual_model_name"] == "manual/router-model"
    assert body["inspection_metadata_json"]["inspected_at"]
    assert "forged" not in created.text

    client.post("/api/v1/auth/logout")
    login(client, email="leader-llm-manual@example.com", password="password-2")
    selected = client.post(
        "/api/v1/llm-selection",
        json={
            "llm_config_id": body["id"],
            "allowed_models_json": ["manual/router-model"],
            "model_name_override": "manual/router-model",
        },
    )

    assert selected.status_code == 200
    assert selected.json()["resolved_model_name"] == "manual/router-model"


def test_llm_endpoint_change_with_kept_secret_rediscover_models(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic LLM Rediscover")
    admin = make_user(email="admin-llm-rediscover@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, available_models_json=["gpt-4.1"], model_name="gpt-4.1", has_secret=True)
    reads: list[str] = []

    def fake_read_team_llm_bearer_token(*, team_id, config_id):
        reads.append(str(config_id))
        return "saved-router-key"

    def fake_list_openai_compatible_models(*, api_key, base_url):
        assert api_key == "saved-router-key"
        assert base_url == "https://openrouter.ai/api/v1"
        return ["anthropic/claude-sonnet-4"]

    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", fake_read_team_llm_bearer_token)
    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", fake_list_openai_compatible_models)
    login(client, email="admin-llm-rediscover@example.com", password="password-1")
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Router",
            "provider_preset": "openrouter",
            "credential_action": "keep",
            "model_name": "anthropic/claude-sonnet-4",
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    assert reads == [str(config.id)]
    db_session.refresh(config)
    assert config.provider_preset == "openrouter"
    assert config.available_models_json == ["anthropic/claude-sonnet-4"]
    assert "gpt-4.1" not in config.available_models_json
    assert config.inspection_metadata_json["discovery_status"] == "fetched"


def test_llm_endpoint_change_with_failed_rediscovery_clears_stale_models(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic LLM Clear Stale")
    admin = make_user(email="admin-llm-clear-stale@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, available_models_json=["gpt-4.1"], model_name="gpt-4.1", has_secret=True)

    def fail_discovery(**kwargs):
        raise AppError(502, "llm_inspection_failed", "Could not load available models")

    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda *, team_id, config_id: "saved-router-key")
    monkeypatch.setattr("app.services.llm._list_openai_compatible_models", fail_discovery)
    login(client, email="admin-llm-clear-stale@example.com", password="password-1")
    updated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Router Manual",
            "provider_preset": "openrouter",
            "credential_action": "keep",
            "model_name": "manual/router-model",
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    db_session.refresh(config)
    assert config.available_models_json == ["manual/router-model"]
    assert config.inspection_metadata_json["discovery_status"] == "manual_required"
    assert config.inspection_metadata_json["manual_model_name"] == "manual/router-model"


def test_saved_llm_inspection_failure_persists_metadata_without_overwriting_models(client, db_session, make_team, make_user, make_llm_config, monkeypatch):
    team = make_team(name="Clinic LLM Saved Inspect Failure")
    admin = make_user(email="admin-saved-llm-inspect-fail@example.com", password="password-1", is_system_admin=True)
    config = make_llm_config(team=team, actor=admin, available_models_json=["old-model"], model_name="old-model", has_secret=True)

    def fail_discovery(**kwargs):
        raise AppError(502, "llm_inspection_failed", "Could not load available models")

    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", lambda *, team_id, config_id: "saved-llm-key")
    monkeypatch.setattr("app.services.llm._list_openai_chat_models", fail_discovery)
    login(client, email="admin-saved-llm-inspect-fail@example.com", password="password-1")
    inspected = client.post(f"/api/v1/llm-configs/{config.id}/inspect?team_id={team.id}")

    assert inspected.status_code == 200
    assert inspected.json()["discovery_status"] == "manual_required"
    db_session.refresh(config)
    assert config.available_models_json == ["old-model"]
    assert config.model_name == "old-model"
    assert config.inspection_metadata_json["discovery_status"] == "manual_required"


def test_system_admin_can_provision_bedrock_without_secret_reveal(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic Bedrock")
    make_user(email="admin-bedrock@example.com", password="password-1", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.llm._list_bedrock_chat_models",
        lambda **kwargs: ["anthropic.claude-3-7-sonnet-20250219-v1:0", "amazon.nova-micro-v1:0"],
    )

    login(client, email="admin-bedrock@example.com", password="password-1")
    created = client.post(
        "/api/v1/llm-configs",
        json={
            "team_id": str(team.id),
            "label": "Clinic Bedrock",
            "adapter_kind": "bedrock_chat",
            "base_url": "",
            "bedrock_region": "us-east-1",
            "bearer_token": "bedrock-api-key",
            "model_name": "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "is_active": True,
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["adapter_kind"] == "bedrock_chat"
    assert body["has_secret"] is True

    persisted = db_session.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == UUID(body["id"])))
    assert persisted is not None
    assert persisted.auth_mode.value == "bearer"
    assert persisted.base_url == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert persisted.available_models_json == [
        "anthropic.claude-3-7-sonnet-20250219-v1:0",
        "amazon.nova-micro-v1:0",
    ]
    assert persisted.vault_secret_ref.startswith("secret:openscribe/llm/team/")


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
    assert body["selected_config_provider_preset"] == "openai"
    assert body["selected_config_provider_display_name"] == "OpenAI"
    assert body["resolved_model_name"] == "gpt-4.1-mini"
    assert body["allowed_models_json"] == ["gpt-4o-mini", "gpt-4.1-mini"]

    cleared = client.delete("/api/v1/llm-selection")
    assert cleared.status_code == 204
    assert client.get("/api/v1/llm-selection").json() is None
    assert db_session.get(TeamLlmConfig, config.id) is not None


def test_system_admin_can_provision_assign_and_leader_select_deidentification_provider(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Deid")
    make_user(email="deid-admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="deid-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)

    login(client, email="deid-admin@example.com", password="password-1")
    listed = client.get("/api/v1/deidentification-providers")
    assert listed.status_code == 200
    assert any(item["adapter_kind"] == "native_presidio" and item["is_builtin"] for item in listed.json())

    created = client.post(
        "/api/v1/deidentification-providers",
        json={
            "label": "Clinic REST Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9100",
            "detect_path": "/detect",
            "auth_mode": "bearer",
            "bearer_token": "secret-token",
            "request_text_field": "text",
            "response_entities_path": "entities",
            "response_start_field": "start",
            "response_end_field": "end",
            "response_type_field": "entity_type",
            "entity_type_map_json": {"PATIENT": "PERSON"},
            "clinical_detection_enabled": True,
            "clinical_detection_allow_unredacted": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["clinical_detection_enabled"] is True
    assert created.json()["clinical_detection_allow_unredacted"] is True
    provider_id = created.json()["id"]

    assigned = client.post(
        "/api/v1/deidentification-provider-assignments",
        json={"team_id": str(team.id), "provider_id": provider_id},
    )
    assert assigned.status_code == 200
    assert assigned.json()["team_id"] == str(team.id)
    assert assigned.json()["provider_id"] == provider_id

    login(client, email="deid-leader@example.com", password="password-2")
    options = client.get("/api/v1/deidentification-selection/options")
    assert options.status_code == 200
    option_ids = [item["id"] for item in options.json()]
    assert provider_id in option_ids
    assert any(item["is_builtin"] for item in options.json())

    selected = client.post("/api/v1/deidentification-selection", json={"provider_id": provider_id})
    assert selected.status_code == 200
    body = selected.json()
    assert body["provider_id"] == provider_id
    assert body["selected_provider_label"] == "Clinic REST Deid"
    assert body["selected_provider_adapter_kind"] == "generic_rest"

    persisted = db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id))
    assert persisted is not None
    assert str(persisted.provider_id) == provider_id

    cleared = client.delete("/api/v1/deidentification-selection")
    assert cleared.status_code == 204
    assert client.get("/api/v1/deidentification-selection").json() is None


def test_leader_can_enable_clinical_nlp_without_changing_pii_redaction_selection(
    client,
    db_session,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
):
    team = make_team(name="Clinic NLP API")
    admin = make_user(email="clinical-api-admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="clinical-api-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    provider = make_deidentification_provider(
        actor=admin,
        label="OpenMedDetect",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://localhost:8090",
        detect_path="/analyze",
        auth_mode=DeidentificationAuthMode.none,
        response_type_field="label",
        response_score_field="confidence",
        clinical_detection_enabled=True,
        clinical_detection_allow_unredacted=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)

    login(client, email="clinical-api-leader@example.com", password="password-2")
    options = client.get("/api/v1/clinical-nlp-selection/options")
    assert options.status_code == 200
    assert [item["id"] for item in options.json()] == [str(provider.id)]

    selected = client.post("/api/v1/clinical-nlp-selection", json={"provider_id": str(provider.id)})
    assert selected.status_code == 200
    body = selected.json()
    assert body["provider_id"] == str(provider.id)
    assert body["selected_provider_label"] == "OpenMedDetect"
    assert body["selected_provider_allows_unredacted"] is True

    assert db_session.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id)) is not None
    assert db_session.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id)) is None

    cleared = client.delete("/api/v1/clinical-nlp-selection")
    assert cleared.status_code == 204
    assert client.get("/api/v1/clinical-nlp-selection").json() is None


def test_system_admin_can_inspect_deidentification_provider_without_persisting_secret(client, make_user, monkeypatch):
    make_user(email="deid-inspect-admin@example.com", password="password-1", is_system_admin=True)
    sample_text = "Jane Smith attended on 22 April 2026."
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "meta": {"model": "deid-test-v1"},
                "entities": [
                    {"start": 0, "end": 10, "entity_type": "NAME", "score": 0.99},
                    {"start": 23, "end": 36, "entity_type": "DATE", "score": 0.95},
                ],
            }

    def fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.redaction.httpx.post", fake_post)

    login(client, email="deid-inspect-admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "Inspectable Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9400",
            "detect_path": "/detect",
            "auth_mode": "bearer",
            "bearer_token": "inspect-secret",
            "request_text_field": "input",
            "response_entities_path": "entities",
            "response_start_field": "start",
            "response_end_field": "end",
            "response_type_field": "entity_type",
            "response_score_field": "score",
            "response_model_version_path": "meta.model",
            "entity_type_map_json": {"NAME": "PERSON", "DATE": "DATE_TIME"},
            "sample_text": sample_text,
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["provider_label"] == "Inspectable Deid"
    assert body["api_model_or_version"] == "deid-test-v1"
    assert body["sample_text"] == sample_text
    assert body["entities"] == [
        {"start": 0, "end": 10, "entity_type": "PERSON", "score": 0.99, "value": "Jane Smith"},
        {"start": 23, "end": 36, "entity_type": "DATE_TIME", "score": 0.95, "value": "22 April 2026"},
    ]
    assert "inspect-secret" not in inspected.text
    assert captured == {
        "url": "http://127.0.0.1:9400/detect",
        "json": {"input": sample_text},
        "headers": {"Authorization": "Bearer inspect-secret"},
        "timeout": 20.0,
    }


def test_system_admin_can_inspect_deidentification_openapi_docs(client, make_user, monkeypatch):
    make_user(email="deid-openapi-admin@example.com", password="password-1", is_system_admin=True)
    fetched_urls: list[str] = []
    posted: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "openapi": "3.1.0",
                "paths": {
                    "/health": {"get": {"summary": "Health"}},
                    "/analyze": {
                        "post": {
                            "summary": "Detect PII entities",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["input"],
                                            "properties": {
                                                "input": {"type": "string", "description": "Text to inspect"},
                                                "language": {"type": "string", "default": "en"},
                                                "threshold": {"type": "number", "default": 0.4},
                                                "keep_mapping": {"type": "boolean", "default": False},
                                            },
                                        }
                                    }
                                }
                            },
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "properties": {
                                                    "items": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "properties": {
                                                                "begin": {"type": "integer"},
                                                                "stop": {"type": "integer"},
                                                                "label": {"type": "string"},
                                                                "confidence": {"type": "number"},
                                                            },
                                                        },
                                                    },
                                                    "model_version": {"type": "string"},
                                                },
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    },
                },
            }

    def fake_get(url, *, headers, timeout):
        fetched_urls.append(url)
        assert headers == {}
        assert timeout == 10.0
        return FakeResponse()

    class FakePostResponse:
        status_code = 200

        def json(self):
            return {
                "items": [
                    {"begin": 0, "stop": 10, "label": "PERSON", "confidence": 0.97},
                ],
                "model_version": "openapi-model",
            }

    def fake_post(url, *, json, headers, timeout):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        posted["timeout"] = timeout
        return FakePostResponse()

    monkeypatch.setattr("app.services.deidentification.httpx.get", fake_get)
    monkeypatch.setattr("app.services.deidentification.httpx.post", fake_post)

    login(client, email="deid-openapi-admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "Docs Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9500",
            "detect_path": "/docs",
            "auth_mode": "none",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert fetched_urls == ["http://127.0.0.1:9500/openapi.json"]
    assert body["detect_path"] == "/analyze"
    assert body["openapi_path"] == "/openapi.json"
    assert body["request_text_field"] == "input"
    assert body["request_language_field"] == "language"
    assert body["extra_body_json"] == {"threshold": 0.4, "keep_mapping": False}
    assert body["response_entities_path"] == "items"
    assert body["response_start_field"] == "begin"
    assert body["response_end_field"] == "stop"
    assert body["response_type_field"] == "label"
    assert body["response_score_field"] == "confidence"
    assert body["response_model_version_path"] == "model_version"
    assert body["candidate_paths"] == ["/analyze"]
    assert body["entities"] == [{"start": 0, "end": 10, "entity_type": "PERSON", "score": 0.97, "value": "Jane Smith"}]
    assert body["api_model_or_version"] == "openapi-model"
    assert body["raw_response_json"] == {
        "items": [{"begin": 0, "stop": 10, "label": "PERSON", "confidence": 0.97}],
        "model_version": "openapi-model",
    }
    assert posted == {
        "url": "http://127.0.0.1:9500/analyze",
        "json": {
            "threshold": 0.4,
            "keep_mapping": False,
            "input": "Jane Smith attended on 22 April 2026.",
            "language": "en",
        },
        "headers": {},
        "timeout": 20.0,
    }

    posted.clear()
    selected = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "Docs Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9500",
            "openapi_path": "/openapi.json",
            "detect_path": "/analyze",
            "auth_mode": "none",
        },
    )

    assert selected.status_code == 200
    assert posted["url"] == "http://127.0.0.1:9500/analyze"


def test_deidentification_inspect_adjusts_entity_fields_from_ping_response(client, make_user, monkeypatch):
    make_user(email="clinical-openapi-admin@example.com", password="password-1", is_system_admin=True)

    class FakeOpenAPIResponse:
        status_code = 200

        def json(self):
            return {
                "openapi": "3.1.0",
                "paths": {
                    "/analyze": {
                        "post": {
                            "summary": "Analyze",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["text"],
                                            "properties": {
                                                "text": {"type": "string"},
                                                "model_name": {"type": "string", "default": "disease_detection_superclinical"},
                                                "confidence_threshold": {"type": "number", "default": 0.0},
                                            },
                                        }
                                    }
                                }
                            },
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "properties": {
                                                    "entities": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "properties": {
                                                                "start": {"type": "integer"},
                                                                "end": {"type": "integer"},
                                                                "entity_type": {"type": "string"},
                                                                "score": {"type": "number"},
                                                            },
                                                        },
                                                    },
                                                    "model_name": {"type": "string"},
                                                },
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }

    class FakePostResponse:
        status_code = 200

        def json(self):
            return {
                "entities": [
                    {"start": 25, "end": 35, "text": "chest pain", "label": "DISEASE", "confidence": 0.96},
                    {"start": 40, "end": 49, "text": "dysphagia", "label": "DISEASE", "confidence": 0.95},
                ],
                "model_name": "disease_detection_superclinical",
            }

    monkeypatch.setattr("app.services.deidentification.httpx.get", lambda *args, **kwargs: FakeOpenAPIResponse())
    monkeypatch.setattr("app.services.deidentification.httpx.post", lambda *args, **kwargs: FakePostResponse())

    login(client, email="clinical-openapi-admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "OpenMedDetect",
            "adapter_kind": "generic_rest",
            "base_url": "http://localhost:8090",
            "openapi_path": "/openapi.json",
            "detect_path": "/analyze",
            "auth_mode": "none",
            "sample_text": "Jane Smith attended with chest pain and dysphagia.",
            "clinical_detection_enabled": True,
            "clinical_detection_allow_unredacted": True,
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["response_type_field"] == "label"
    assert body["response_score_field"] == "confidence"
    assert body["entities"] == [
        {"start": 25, "end": 35, "entity_type": "DISEASE", "score": 0.96, "value": "chest pain"},
        {"start": 40, "end": 49, "entity_type": "DISEASE", "score": 0.95, "value": "dysphagia"},
    ]
    assert any("response_type_field=label" in note for note in body["notes"])


def test_deidentification_openapi_docs_preserve_top_level_array_response_path(client, make_user, monkeypatch):
    make_user(email="deid-openapi-array-admin@example.com", password="password-1", is_system_admin=True)

    class FakeOpenAPIResponse:
        status_code = 200

        def json(self):
            return {
                "openapi": "3.1.0",
                "paths": {
                    "/analyze": {
                        "post": {
                            "summary": "Detect PII entities",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["text"],
                                            "properties": {"text": {"type": "string"}},
                                        }
                                    }
                                }
                            },
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "start": {"type": "integer"},
                                                        "end": {"type": "integer"},
                                                        "entity_type": {"type": "string"},
                                                        "score": {"type": "number"},
                                                    },
                                                },
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }

    class FakePostResponse:
        status_code = 200

        def json(self):
            return [{"start": 0, "end": 10, "entity_type": "PERSON", "score": 0.98}]

    monkeypatch.setattr("app.services.deidentification.httpx.get", lambda *args, **kwargs: FakeOpenAPIResponse())
    monkeypatch.setattr("app.services.deidentification.httpx.post", lambda *args, **kwargs: FakePostResponse())

    login(client, email="deid-openapi-array-admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "Array Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9700",
            "detect_path": "/openapi.json",
            "auth_mode": "none",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert body["response_entities_path"] == ""
    assert body["entities"] == [{"start": 0, "end": 10, "entity_type": "PERSON", "score": 0.98, "value": "Jane Smith"}]
    assert body["raw_response_json"] == [{"start": 0, "end": 10, "entity_type": "PERSON", "score": 0.98}]


def test_deidentification_inspect_prunes_forbidden_extra_fields_and_language_value(client, make_user, monkeypatch):
    make_user(email="deid-prune-admin@example.com", password="password-1", is_system_admin=True)
    posted_bodies: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

    def fake_post(url, *, json, headers, timeout):
        posted_bodies.append(dict(json))
        if len(posted_bodies) == 1:
            return FakeResponse(
                422,
                {
                    "error": {
                        "message": "Request validation failed",
                        "details": [
                            {"field": "body.method", "message": "Extra inputs are not permitted", "type": "extra_forbidden"},
                            {"field": "body.keep_year", "message": "Extra inputs are not permitted", "type": "extra_forbidden"},
                            {"field": "body.keep_mapping", "message": "Extra inputs are not permitted", "type": "extra_forbidden"},
                        ],
                    }
                },
            )
        return FakeResponse(200, {"entities": [{"start": 0, "end": 14, "entity_type": "PERSON", "score": 0.99}]})

    monkeypatch.setattr("app.services.deidentification.httpx.post", fake_post)

    login(client, email="deid-prune-admin@example.com", password="password-1")
    inspected = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "Prune Deid",
            "adapter_kind": "generic_rest",
            "base_url": "http://127.0.0.1:9600",
            "detect_path": "/pii/extract",
            "auth_mode": "none",
            "request_text_field": "text",
            "request_language_field": "en",
            "extra_body_json": {"method": "mask", "keep_year": True, "keep_mapping": False, "model_name": "ok"},
            "response_entities_path": "entities",
            "response_start_field": "start",
            "response_end_field": "end",
            "response_type_field": "entity_type",
            "response_score_field": "score",
            "sample_text": "Gemma Phillips attended.",
        },
    )

    assert inspected.status_code == 200
    body = inspected.json()
    assert posted_bodies == [
        {"method": "mask", "keep_year": True, "keep_mapping": False, "model_name": "ok", "text": "Gemma Phillips attended."},
        {"model_name": "ok", "text": "Gemma Phillips attended."},
    ]
    assert body["request_language_field"] is None
    assert body["extra_body_json"] == {"model_name": "ok"}
    assert body["entities"] == [{"start": 0, "end": 14, "entity_type": "PERSON", "score": 0.99, "value": "Gemma Phillips"}]
    assert any("Use a field name such as lang or language" in note for note in body["notes"])
    assert any("retried without: keep_mapping, keep_year, method" in note for note in body["notes"])


def test_deidentification_provider_rejects_secret_headers_and_missing_bearer_token(
    client,
    make_user,
    make_deidentification_provider,
):
    admin = make_user(email="deid-secret-admin@example.com", password="password-1", is_system_admin=True)
    existing_provider = make_deidentification_provider(
        actor=admin,
        label="No Secret Yet",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/pii/deidentify",
        auth_mode=DeidentificationAuthMode.none,
        has_secret=False,
    )

    login(client, email="deid-secret-admin@example.com", password="password-1")

    secret_header = client.post(
        "/api/v1/deidentification-providers",
        json={
            "label": "Header Secret",
            "adapter_kind": "generic_rest",
            "base_url": "https://deid.example.com",
            "detect_path": "/detect",
            "auth_mode": "none",
            "extra_headers_json": {"Authorization": "Bearer raw-secret"},
        },
    )
    assert secret_header.status_code == 422

    secret_body = client.post(
        "/api/v1/deidentification-providers",
        json={
            "label": "Body Secret",
            "adapter_kind": "generic_rest",
            "base_url": "https://deid.example.com",
            "detect_path": "/detect",
            "auth_mode": "none",
            "extra_body_json": {"api_key": "raw-secret"},
        },
    )
    assert secret_body.status_code == 422

    nested_secret_body = client.post(
        "/api/v1/deidentification-providers",
        json={
            "label": "Nested Body Secret",
            "adapter_kind": "generic_rest",
            "base_url": "https://deid.example.com",
            "detect_path": "/detect",
            "auth_mode": "none",
            "extra_body_json": {"auth": {"token": "raw-secret"}},
        },
    )
    assert nested_secret_body.status_code == 422

    missing_token = client.post(
        "/api/v1/deidentification-providers",
        json={
            "provider_id": str(existing_provider.id),
            "label": "No Secret Yet",
            "adapter_kind": "generic_rest",
            "base_url": "https://deid.example.com",
            "detect_path": "/detect",
            "auth_mode": "bearer",
        },
    )
    assert_error(
        missing_token,
        status_code=422,
        code="business_rule_violation",
        message="Bearer token is required when configuring bearer-auth de-identification provider",
    )


def test_deidentification_provider_upsert_cleans_pending_secret_only_after_commit_failure(
    db_session,
    make_user,
    make_deidentification_provider,
    monkeypatch,
):
    from app.schemas import DeidentificationProviderUpsert
    from app.services.deidentification import upsert_deidentification_provider

    admin = make_user(email="deid-commit-fail-admin@example.com", password="password-1", is_system_admin=True)
    provider = make_deidentification_provider(
        actor=admin,
        label="Commit Fail Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.bearer,
        has_secret=True,
    )
    old_secret_ref = provider.vault_secret_ref
    new_secret_ref = f"secret:openscribe/deidentification/provider/{provider.id}/replacement"
    deleted_secret_refs: list[str] = []

    monkeypatch.setattr(
        "app.services.deidentification.write_deidentification_bearer_token",
        lambda *, provider_id, bearer_token, secret_id=None: new_secret_ref,
    )
    monkeypatch.setattr(
        "app.services.deidentification.delete_deidentification_bearer_token",
        lambda *, provider_id, secret_ref=None: deleted_secret_refs.append(secret_ref or ""),
    )

    def fail_commit():
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        upsert_deidentification_provider(
            db_session,
            admin,
            DeidentificationProviderUpsert(
                provider_id=provider.id,
                label="Commit Fail Deid",
                adapter_kind=DeidentificationAdapterKind.generic_rest,
                base_url="https://deid.example.com",
                detect_path="/detect",
                auth_mode=DeidentificationAuthMode.bearer,
                bearer_token="new-secret",
            ),
        )

    assert deleted_secret_refs == [new_secret_ref]
    db_session.rollback()
    assert db_session.get(DeidentificationProvider, provider.id).vault_secret_ref == old_secret_ref


def test_deidentification_provider_delete_defers_vault_cleanup_until_after_db_commit(
    client,
    db_session,
    make_user,
    make_deidentification_provider,
    monkeypatch,
):
    admin = make_user(email="deid-delete-vault-admin@example.com", password="password-1", is_system_admin=True)
    provider = make_deidentification_provider(
        actor=admin,
        label="Delete Vault Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.bearer,
        has_secret=True,
    )
    old_secret_ref = provider.vault_secret_ref
    deleted_secret_refs: list[str] = []

    def fake_delete_secret(*, provider_id, secret_ref=None):
        assert db_session.get(DeidentificationProvider, provider.id) is None
        deleted_secret_refs.append(secret_ref or "")

    monkeypatch.setattr("app.services.deidentification.delete_deidentification_bearer_token", fake_delete_secret)

    login(client, email="deid-delete-vault-admin@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/deidentification-providers/{provider.id}")

    assert deleted.status_code == 204
    assert deleted_secret_refs == [old_secret_ref]


def test_deidentification_provider_delete_clears_clinical_nlp_refs(
    client,
    db_session,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_clinical_nlp_selection,
):
    team = make_team(name="Clinic Delete Clinical Provider")
    admin = make_user(email="deid-delete-clinical-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="deid-delete-clinical-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Delete Clinical NLP Provider",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://clinical.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.none,
        clinical_detection_enabled=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)
    selection = make_clinical_nlp_selection(team=team, provider=provider, actor=admin)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Clinical provider delete",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    run = ClinicalEntityRun(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=team.id,
        provider_id=provider.id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=True,
        api_provider=provider.label,
        entity_count=0,
    )
    db_session.add(run)
    db_session.commit()

    login(client, email="deid-delete-clinical-admin@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/deidentification-providers/{provider.id}")

    assert deleted.status_code == 204
    db_session.refresh(run)
    assert run.provider_id is None
    assert db_session.get(TeamClinicalNlpSelection, selection.id) is None
    assert db_session.get(DeidentificationProvider, provider.id) is None


def test_leader_cannot_select_unassigned_deidentification_provider(client, make_team, make_user, make_deidentification_provider):
    team = make_team(name="Clinic Deid Unassigned")
    admin = make_user(email="deid-unassigned-admin@example.com", password="password-1", is_system_admin=True)
    make_user(email="deid-unassigned-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    provider = make_deidentification_provider(
        actor=admin,
        label="Unassigned REST Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9200",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.none,
    )

    login(client, email="deid-unassigned-leader@example.com", password="password-2")
    options = client.get("/api/v1/deidentification-selection/options")
    assert options.status_code == 200
    assert all(item["id"] != str(provider.id) for item in options.json())

    rejected = client.post("/api/v1/deidentification-selection", json={"provider_id": str(provider.id)})
    assert_error(rejected, status_code=404, code="not_found", message="Selectable de-identification provider not found")


def test_deidentification_runtime_falls_back_to_builtin_when_selection_is_inactive(
    db_session,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    make_clinical_nlp_selection,
):
    from app.services.deidentification import BUILTIN_DEIDENTIFICATION_PROVIDER_ID, active_team_deidentification_provider

    team = make_team(name="Clinic Deid Fallback")
    admin = make_user(email="deid-fallback-admin@example.com", password="password-1", is_system_admin=True)
    inactive_provider = make_deidentification_provider(
        actor=admin,
        label="Inactive REST Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.none,
        is_active=False,
    )
    make_deidentification_provider_assignment(team=team, provider=inactive_provider, actor=admin)
    make_deidentification_selection(team=team, provider=inactive_provider, actor=admin)

    provider = active_team_deidentification_provider(db_session, team_id=team.id)

    assert provider.id == BUILTIN_DEIDENTIFICATION_PROVIDER_ID
    assert provider.adapter_kind is DeidentificationAdapterKind.native_presidio


def test_ensure_builtin_deidentification_provider_does_not_commit_caller_transaction(db_session):
    from app.services.deidentification import BUILTIN_DEIDENTIFICATION_PROVIDER_ID, ensure_builtin_deidentification_provider

    team_id = uuid4()
    team = Team(
        id=team_id,
        name="Rollback Builtin Provider Clinic",
        name_key=f"rollback-builtin-provider-{team_id}",
    )
    db_session.add(team)

    provider = ensure_builtin_deidentification_provider(db_session)

    assert provider.id == BUILTIN_DEIDENTIFICATION_PROVIDER_ID
    db_session.rollback()
    assert db_session.get(Team, team_id) is None
    assert db_session.get(DeidentificationProvider, BUILTIN_DEIDENTIFICATION_PROVIDER_ID) is None


def test_redaction_run_uses_selected_team_deidentification_provider_and_builtin_fallback(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    make_clinical_nlp_selection,
):
    team_with_external = make_team(name="Clinic External Deid")
    team_builtin = make_team(name="Clinic Builtin Deid")
    admin = make_user(email="deid-runtime-admin@example.com", password="password-1", is_system_admin=True)
    owner_external = make_user(email="deid-runtime-owner1@example.com", password="password-2", team=team_with_external, team_role=TeamRole.user)
    owner_builtin = make_user(email="deid-runtime-owner2@example.com", password="password-3", team=team_builtin, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Runtime REST Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9300",
        detect_path="/pii/deidentify",
        auth_mode=DeidentificationAuthMode.none,
    )
    make_deidentification_provider_assignment(team=team_with_external, provider=provider, actor=admin)
    make_deidentification_selection(team=team_with_external, provider=provider, actor=admin)

    def make_version(*, owner: User, text_value: str) -> TranscriptVersion:
        transcript = Transcript(
            owner_user_id=owner.id,
            team_id=owner.team_id,
            title="Redaction source",
            current_draft_text_encrypted=encrypt_text_for_owner(
                db_session,
                owner_user_id=owner.id,
                table="transcripts",
                field="current_draft_text_encrypted",
                record_id=uuid4(),
                plaintext=text_value,
            ),
            ingestion_mode=TranscriptIngestionMode.whole_file,
            status=TranscriptStatus.ready,
            retention_days_applied=30,
            retention_expires_at=owner.created_at,
        )
        db_session.add(transcript)
        db_session.flush()
        transcript.current_draft_text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            plaintext=text_value,
        )
        version = TranscriptVersion(
            transcript_id=transcript.id,
            version_no=1,
            text_encrypted=encrypt_text_for_owner(
                db_session,
                owner_user_id=owner.id,
                table="transcript_versions",
                field="text_encrypted",
                record_id=uuid4(),
                plaintext=text_value,
            ),
        )
        db_session.add(version)
        db_session.flush()
        version.text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version.id,
            plaintext=text_value,
        )
        db_session.add(transcript)
        db_session.add(version)
        db_session.commit()
        db_session.refresh(version)
        return version

    external_version = make_version(owner=owner_external, text_value="John Smith reports headaches.")
    builtin_version = make_version(owner=owner_builtin, text_value="Jane Doe reports dizziness.")

    captured_provider_ids: list[UUID] = []
    captured_provider_paths: list[str] = []

    from app.services.redaction import DeidentificationDetectionResult, Span
    from app.services.deidentification import BUILTIN_DEIDENTIFICATION_PROVIDER_ID

    def fake_detect_phi(db, *, provider, text, language, score_threshold, entities):
        captured_provider_ids.append(provider.id)
        captured_provider_paths.append(provider.detect_path)
        name = "John Smith" if "John Smith" in text else "Jane Doe"
        return DeidentificationDetectionResult(
            spans=[Span(start=0, end=len(name), entity_type="PERSON", score=0.99)],
            api_provider=provider.label,
            api_model_or_version="stub-model",
        )

    monkeypatch.setattr("app.services.redaction._detect_phi", fake_detect_phi)

    external_run = ensure_redaction_run_for_transcript_version(db_session, transcript_version=external_version)
    builtin_run = ensure_redaction_run_for_transcript_version(db_session, transcript_version=builtin_version)

    assert captured_provider_ids[0] == provider.id
    assert captured_provider_ids[1] == BUILTIN_DEIDENTIFICATION_PROVIDER_ID
    assert captured_provider_paths[0] == "/pii/deidentify"
    assert external_run.api_provider == "Runtime REST Deid"
    assert builtin_run.api_provider == "Built-in Native Presidio"
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner_external.id,
        table="redaction_runs",
        field="redacted_text_encrypted",
        record_id=external_run.id,
        stored_value=external_run.redacted_text_encrypted,
    ) == "[PHI-1] reports headaches."
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner_builtin.id,
        table="redaction_runs",
        field="redacted_text_encrypted",
        record_id=builtin_run.id,
        stored_value=builtin_run.redacted_text_encrypted,
    ) == "[PHI-1] reports dizziness."


def test_redaction_reuse_creates_missing_clinical_run(
    db_session,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_clinical_nlp_selection,
):
    team = make_team(name="Clinic Existing Redaction Clinical")
    admin = make_user(email="clinical-existing-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="clinical-existing-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Native Clinical NLP",
        adapter_kind=DeidentificationAdapterKind.native_presidio,
        clinical_detection_enabled=True,
        is_builtin=False,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)
    make_clinical_nlp_selection(team=team, provider=provider, actor=admin)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Existing redaction clinical",
        current_draft_text_encrypted="Alex reports asthma.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="")
    db_session.add(version)
    db_session.flush()
    version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=version.id,
        plaintext="Alex reports asthma.",
    )
    redaction_run = RedactionRun(
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=team.id,
        status=RedactionRunStatus.succeeded,
        api_provider="Existing Deid",
    )
    db_session.add(redaction_run)
    db_session.flush()
    redaction_run.redacted_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="redaction_runs",
        field="redacted_text_encrypted",
        record_id=redaction_run.id,
        plaintext="[PHI-1] reports asthma.",
    )
    db_session.commit()

    reused = ensure_redaction_run_for_transcript_version(db_session, transcript_version=version)

    assert reused.id == redaction_run.id
    clinical_run = db_session.scalar(select(ClinicalEntityRun).where(ClinicalEntityRun.transcript_version_id == version.id))
    assert clinical_run is not None
    assert clinical_run.redaction_run_id == redaction_run.id
    assert clinical_run.owner_user_id == owner.id
    assert clinical_run.team_id == team.id


def test_generic_rest_deidentification_spans_are_normalized_and_filtered(
    db_session,
    monkeypatch,
    make_user,
    make_deidentification_provider,
):
    from app.services.redaction import redact_text_with_mapping

    admin = make_user(email="deid-span-admin@example.com", password="password-1", is_system_admin=True)
    provider = make_deidentification_provider(
        actor=admin,
        label="REST Span Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.none,
        response_score_field="score",
        entity_type_map_json={"NAME": "PERSON"},
        clinical_detection_enabled=True,
    )
    text = "Patient John Smith has asthma and pain 5/10 today."
    name_start = text.index("John Smith")
    asthma_start = text.index("asthma")
    pain_start = text.index("5/10")
    today_start = text.index("today")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "entities": [
                    {"start": name_start - 1, "end": name_start + len("John Smith"), "entity_type": "NAME", "score": 0.99},
                    {"start": asthma_start, "end": asthma_start + len("asthma"), "entity_type": "DISEASE", "score": 0.99},
                    {"start": pain_start, "end": pain_start + len("5/10"), "entity_type": "PERSON", "score": 0.99},
                    {"start": today_start, "end": today_start + len("today"), "entity_type": "DATE_TIME", "score": 0.99},
                    {"start": 0, "end": len("Patient"), "entity_type": "PERSON", "score": 0.1},
                ]
            }

    monkeypatch.setattr("app.services.redaction.httpx.post", lambda *args, **kwargs: FakeResponse())

    result = redact_text_with_mapping(
        db_session,
        text,
        provider=provider,
        score_threshold=0.35,
        start_index=3,
    )

    assert result["redacted_text"] == "Patient [PHI-3] has asthma and pain 5/10 today."
    assert result["phi_index"] == [
        {"index": 3, "type": "PERSON", "value": "John Smith", "placeholder": "[PHI-3]"}
    ]


def test_clinical_detection_uses_redacted_text_for_remote_provider(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    make_clinical_nlp_selection,
):
    team = make_team(name="Clinic Remote Clinical NLP")
    admin = make_user(email="clinical-remote-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="clinical-remote-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Remote Clinical NLP",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://clinical.example.com",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.none,
        clinical_detection_enabled=True,
        clinical_detection_allow_unredacted=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)
    make_deidentification_selection(team=team, provider=provider, actor=admin)
    make_clinical_nlp_selection(team=team, provider=provider, actor=admin)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Remote clinical NLP",
        current_draft_text_encrypted="John Smith reports asthma.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="John Smith reports asthma.")
    db_session.add(version)
    db_session.commit()

    from app.services.redaction import DeidentificationDetectionResult, Span

    def fake_detect_phi(db, *, provider, text, language, score_threshold, entities):
        return DeidentificationDetectionResult(
            spans=[Span(start=0, end=len("John Smith"), entity_type="PERSON", score=0.99)],
            api_provider=provider.label,
            api_model_or_version="stub",
        )

    class FakeClinicalResponse:
        status_code = 200

        def json(self):
            return {"entities": [{"start": 16, "end": 22, "entity_type": "DISEASE"}]}

    captured_body = {}

    def fake_post(url, json, headers, timeout):
        captured_body.update(json)
        return FakeClinicalResponse()

    monkeypatch.setattr("app.services.redaction._detect_phi", fake_detect_phi)
    monkeypatch.setattr("app.services.redaction.httpx.post", fake_post)

    ensure_redaction_run_for_transcript_version(db_session, transcript_version=version)

    run = db_session.scalar(select(ClinicalEntityRun).where(ClinicalEntityRun.transcript_id == transcript.id))
    assert run is not None
    assert run.source_text_redacted is True
    assert captured_body["text"] == "[PHI-1] reports asthma."
    assert run.entity_count == 1


def test_clinical_detection_allows_unredacted_text_for_local_provider(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    make_clinical_nlp_selection,
):
    team = make_team(name="Clinic Local Clinical NLP")
    admin = make_user(email="clinical-local-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="clinical-local-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Local Clinical NLP",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9400",
        detect_path="/detect",
        auth_mode=DeidentificationAuthMode.none,
        clinical_detection_enabled=True,
        clinical_detection_allow_unredacted=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)
    make_deidentification_selection(team=team, provider=provider, actor=admin)
    make_clinical_nlp_selection(team=team, provider=provider, actor=admin)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Local clinical NLP",
        current_draft_text_encrypted="Jane Smith reports dizziness.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="Jane Smith reports dizziness.")
    db_session.add(version)
    db_session.commit()

    from app.services.redaction import DeidentificationDetectionResult, Span

    monkeypatch.setattr(
        "app.services.redaction._detect_phi",
        lambda db, *, provider, text, language, score_threshold, entities: DeidentificationDetectionResult(
            spans=[Span(start=0, end=len("Jane Smith"), entity_type="PERSON", score=0.99)],
            api_provider=provider.label,
            api_model_or_version="stub",
        ),
    )
    captured_body = {}

    class FakeClinicalResponse:
        status_code = 200

        def json(self):
            return {"entities": [{"start": 19, "end": 28, "entity_type": "SYMPTOM"}]}

    def fake_post(url, json, headers, timeout):
        captured_body.update(json)
        return FakeClinicalResponse()

    monkeypatch.setattr("app.services.redaction.httpx.post", fake_post)

    ensure_redaction_run_for_transcript_version(db_session, transcript_version=version)

    run = db_session.scalar(select(ClinicalEntityRun).where(ClinicalEntityRun.transcript_id == transcript.id))
    assert run is not None
    assert run.source_text_redacted is False
    assert captured_body["text"] == "Jane Smith reports dizziness."
    entity = db_session.scalar(select(ClinicalEntity).where(ClinicalEntity.clinical_entity_run_id == run.id))
    assert entity is not None
    raw_sha256 = hashlib.sha256("dizziness".encode("utf-8")).hexdigest()
    assert entity.normalized_value_hash != raw_sha256
    assert entity.normalized_value_hash == keyed_digest_for_owner(
        db_session,
        owner_user_id=owner.id,
        purpose="clinical_entities.normalized_value_hash",
        value="dizziness",
    )


def test_clinical_detection_chunks_long_local_analyze_requests(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_clinical_nlp_selection,
):
    from app.services.clinical_nlp import CLINICAL_NLP_MAX_CHUNK_CHARS

    team = make_team(name="Clinic Chunked Clinical NLP")
    admin = make_user(email="clinical-chunk-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="clinical-chunk-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Chunked OpenMedNER",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://localhost:8090",
        detect_path="/analyze",
        auth_mode=DeidentificationAuthMode.none,
        response_entities_path="entities",
        response_type_field="label",
        response_score_field="confidence",
        clinical_detection_enabled=True,
        clinical_detection_allow_unredacted=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)
    make_clinical_nlp_selection(team=team, provider=provider, actor=admin)
    source_text = ("Patient has asthma. " * 800).strip()
    assert len(source_text) > CLINICAL_NLP_MAX_CHUNK_CHARS
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Chunked clinical NLP",
        current_draft_text_encrypted=source_text,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="")
    db_session.add(version)
    db_session.flush()
    version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=version.id,
        plaintext=source_text,
    )
    db_session.commit()

    captured_bodies = []

    class FakeClinicalResponse:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            start = self.body["text"].index("asthma")
            return {"entities": [{"start": start, "end": start + len("asthma"), "label": "DISEASE", "confidence": 0.97}]}

    def fake_post(url, json, headers, timeout):
        captured_bodies.append(json)
        return FakeClinicalResponse(json)

    monkeypatch.setattr("app.services.redaction.httpx.post", fake_post)

    run = ensure_clinical_entity_run_for_transcript_version(db_session, transcript_version=version)

    assert run is not None
    assert run.status is RedactionRunStatus.succeeded
    assert run.entity_count == len(captured_bodies)
    assert len(captured_bodies) >= 2
    assert all(len(body["text"]) <= CLINICAL_NLP_MAX_CHUNK_CHARS for body in captured_bodies)
    assert all(body["sentence_detection"] is False for body in captured_bodies)
    db_session.flush()
    entities = list(db_session.scalars(select(ClinicalEntity).where(ClinicalEntity.clinical_entity_run_id == run.id)))
    assert len(entities) == len(captured_bodies)
    assert all(clinical_entity_value(db_session, entity=entity) == "asthma" for entity in entities)


def test_clinical_text_chunks_keep_offsets_and_sentence_boundaries():
    from app.services.clinical_nlp import _clinical_text_chunks

    text = "Alpha asthma. Beta diabetes. Gamma chest pain. Delta cough."
    chunks = _clinical_text_chunks(text, max_chars=28)

    assert chunks == [
        (0, "Alpha asthma."),
        (14, "Beta diabetes."),
        (29, "Gamma chest pain."),
        (47, "Delta cough."),
    ]


def test_clinical_detection_reruns_after_provider_config_update(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_clinical_nlp_selection,
):
    team = make_team(name="Clinic Clinical NLP Refresh")
    admin = make_user(email="clinical-refresh-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="clinical-refresh-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    provider = make_deidentification_provider(
        actor=admin,
        label="Refresh Clinical NLP",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9401",
        detect_path="/analyze",
        auth_mode=DeidentificationAuthMode.none,
        response_entities_path="entities",
        response_type_field="entity_type",
        response_score_field="score",
        clinical_detection_enabled=True,
        clinical_detection_allow_unredacted=True,
    )
    make_deidentification_provider_assignment(team=team, provider=provider, actor=admin)
    make_clinical_nlp_selection(team=team, provider=provider, actor=admin)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Clinical NLP refresh",
        current_draft_text_encrypted="Jane Smith attended with diarrhoea and chest pain.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="Jane Smith attended with diarrhoea and chest pain.",
    )
    db_session.add(version)
    db_session.flush()
    stale_run = ClinicalEntityRun(
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=team.id,
        provider_id=provider.id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=False,
        api_provider=provider.label,
        entity_count=0,
        created_at=utcnow() - timedelta(minutes=10),
    )
    db_session.add(stale_run)
    provider.updated_at = utcnow()
    db_session.add(provider)
    db_session.commit()

    class FakeClinicalResponse:
        status_code = 200

        def json(self):
            return {
                "entities": [
                    {"start": 25, "end": 34, "entity_type": "DISEASE", "score": 0.95},
                    {"start": 39, "end": 49, "entity_type": "DISEASE", "score": 0.97},
                ]
            }

    monkeypatch.setattr("app.services.redaction.httpx.post", lambda *args, **kwargs: FakeClinicalResponse())

    fresh_run = ensure_clinical_entity_run_for_transcript_version(db_session, transcript_version=version)

    assert fresh_run is not None
    assert fresh_run.id != stale_run.id
    assert fresh_run.entity_count == 2
    runs = list(db_session.scalars(select(ClinicalEntityRun).where(ClinicalEntityRun.transcript_version_id == version.id)))
    assert len(runs) == 2


def test_generic_rest_deidentification_locates_value_only_entities(
    db_session,
    monkeypatch,
    make_user,
    make_deidentification_provider,
):
    from app.services.redaction import redact_text_with_mapping

    admin = make_user(email="deid-value-admin@example.com", password="password-1", is_system_admin=True)
    provider = make_deidentification_provider(
        actor=admin,
        label="REST Value Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/pii/extract",
        auth_mode=DeidentificationAuthMode.none,
        response_entities_path="entities",
        response_type_field="label",
        response_score_field="confidence",
        entity_type_map_json={"NAME": "PERSON", "ADDRESS": "LOCATION"},
    )
    text = "Gemma Phillips, sixty-eight B Kenworthy Lane. Forty nine Harris Road."

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "entities": [
                    {"text": "Gemma Phillips", "label": "NAME", "confidence": 0.99},
                    {"text": "sixty-eight B Kenworthy Lane", "label": "ADDRESS", "confidence": 0.98},
                ]
            }

    monkeypatch.setattr("app.services.redaction.httpx.post", lambda *args, **kwargs: FakeResponse())

    result = redact_text_with_mapping(
        db_session,
        text,
        provider=provider,
        score_threshold=0.35,
    )

    assert result["redacted_text"] == "[PHI-1], [PHI-2]. Forty nine Harris Road."
    assert result["phi_index"] == [
        {"index": 1, "type": "PERSON", "value": "Gemma Phillips", "placeholder": "[PHI-1]"},
        {"index": 2, "type": "LOCATION", "value": "sixty-eight B Kenworthy Lane", "placeholder": "[PHI-2]"},
    ]


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


def test_user_can_set_get_and_clear_app_preferences(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team = make_team(name="Clinic Preferences")
    leader = make_user(email="leader-preferences@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    user = make_user(email="user-preferences@example.com", password="password-2", team=team, team_role=TeamRole.user)
    team_template = make_template(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Team note",
        prompt_text="Write a concise note.",
    )
    personal_template = make_template(
        scope=TemplateScope.user,
        owner=user,
        actor=user,
        name="Personal note",
        prompt_text="Write a personal note.",
    )
    team_quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Team SMS",
        prompt_text="Write a text message.",
    )
    personal_quick_action = make_quick_action(
        scope=TemplateScope.user,
        owner=user,
        actor=user,
        name="Personal callback",
        prompt_text="Write a callback note.",
    )

    login(client, email="user-preferences@example.com", password="password-2")
    saved = client.post(
        "/api/v1/app-preferences",
        json={
            "favorite_quick_action_ids": [str(team_quick_action.id), str(personal_quick_action.id)],
            "favorite_template_ids": [str(team_template.id), str(personal_template.id)],
            "default_quick_action_id": str(team_quick_action.id),
            "default_template_id": str(personal_template.id),
            "llm_detail_level": "detailed",
            "note_generation_length": "long",
            "preferred_recording_mode": "live_chunked",
            "preferred_transcribe_tab": "followups",
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["favorite_quick_action_ids"] == [str(team_quick_action.id), str(personal_quick_action.id)]
    assert body["favorite_template_ids"] == [str(team_template.id), str(personal_template.id)]
    assert body["default_quick_action_id"] == str(team_quick_action.id)
    assert body["default_template_id"] == str(personal_template.id)
    assert body["llm_detail_level"] == "detailed"
    assert body["note_generation_length"] == "long"
    assert body["preferred_recording_mode"] == "live_chunked"
    assert body["preferred_transcribe_tab"] == "followups"

    persisted = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == user.id))
    assert persisted is not None
    assert persisted.preferences_json == {
        "favorite_quick_action_ids": [str(team_quick_action.id), str(personal_quick_action.id)],
        "favorite_template_ids": [str(team_template.id), str(personal_template.id)],
        "default_quick_action_id": str(team_quick_action.id),
        "default_template_id": str(personal_template.id),
        "llm_detail_level": "detailed",
        "note_generation_length": "long",
        "preferred_recording_mode": "live_chunked",
        "preferred_transcribe_tab": "followups",
    }

    rejected_length = client.post("/api/v1/app-preferences", json={"note_generation_length": "giant"})
    assert rejected_length.status_code == 422

    fetched = client.get("/api/v1/app-preferences")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == str(persisted.id)
    assert fetched.json()["favorite_quick_action_ids"] == [str(team_quick_action.id), str(personal_quick_action.id)]

    cleared = client.delete("/api/v1/app-preferences")
    assert cleared.status_code == 204
    assert db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == user.id)) is None


def test_user_app_preferences_reject_unavailable_assets_and_system_admin(
    client,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team_one = make_team(name="Clinic Preferences One")
    team_two = make_team(name="Clinic Preferences Two")
    leader_one = make_user(email="leader-preferences-one@example.com", password="password-1", team=team_one, team_role=TeamRole.leader)
    leader_two = make_user(email="leader-preferences-two@example.com", password="password-2", team=team_two, team_role=TeamRole.leader)
    user = make_user(email="user-preferences-two@example.com", password="password-3", team=team_one, team_role=TeamRole.user)
    admin = make_user(email="admin-app-preferences@example.com", password="password-4", is_system_admin=True)
    foreign_template = make_template(scope=TemplateScope.team, team=team_two, actor=leader_two, name="Foreign note", prompt_text="Write a note.")
    foreign_quick_action = make_quick_action(scope=TemplateScope.team, team=team_two, actor=leader_two, name="Foreign SMS", prompt_text="Write a text.")

    login(client, email="user-preferences-two@example.com", password="password-3")
    rejected = client.post(
        "/api/v1/app-preferences",
        json={
            "favorite_quick_action_ids": [str(foreign_quick_action.id)],
            "default_template_id": str(foreign_template.id),
        },
    )
    assert_error(
        rejected,
        status_code=422,
        code="business_rule_violation",
        message="Selected quick action favourites are not available for this user",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="admin-app-preferences@example.com", password="password-4")
    forbidden = client.get("/api/v1/app-preferences")
    assert_error(
        forbidden,
        status_code=403,
        code="forbidden",
        message="User app preferences are restricted to normal team users",
    )


def test_get_app_preferences_drops_deleted_or_hidden_asset_refs(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
    make_user_app_preference,
):
    team = make_team(name="Clinic Preference Cleanup")
    leader = make_user(email="leader-preferences-cleanup@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    user = make_user(email="user-preferences-cleanup@example.com", password="password-2", team=team, team_role=TeamRole.user)
    template = make_template(
        scope=TemplateScope.user,
        owner=user,
        actor=user,
        name="Cleanup note",
        prompt_text="Write a note.",
    )
    quick_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=leader,
        name="Cleanup SMS",
        prompt_text="Write a text.",
    )
    preference = make_user_app_preference(
        user=user,
        preferences_json={
            "favorite_quick_action_ids": [str(quick_action.id)],
            "favorite_template_ids": [str(template.id)],
            "default_quick_action_id": str(quick_action.id),
            "default_template_id": str(template.id),
            "llm_detail_level": "balanced",
            "note_generation_length": "short",
        },
    )

    db_session.delete(template)
    db_session.delete(quick_action)
    db_session.commit()

    login(client, email="user-preferences-cleanup@example.com", password="password-2")
    fetched = client.get("/api/v1/app-preferences")
    assert fetched.status_code == 200
    assert fetched.json()["favorite_quick_action_ids"] == []
    assert fetched.json()["favorite_template_ids"] == []
    assert fetched.json()["default_quick_action_id"] is None
    assert fetched.json()["default_template_id"] is None
    assert fetched.json()["llm_detail_level"] == "balanced"
    assert fetched.json()["note_generation_length"] == "short"

    refreshed = db_session.get(UserAppPreference, preference.id)
    assert refreshed is not None
    assert refreshed.preferences_json == {"llm_detail_level": "balanced", "note_generation_length": "short"}


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
    captured_provider_request = {}

    def fake_generate_openai(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return '{"title":"Visit summary","content":"Generated note body"}', {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168, "duration_ms": 10}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

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
    team_template_version_id = created_team_template.json()["latest_version"]["id"]
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
    committed = client.post(
        f"/api/v1/transcripts/{transcript_id}/commit",
        json={"text_encrypted": "Patient says symptoms improved."},
    )
    assert committed.status_code == 200
    preview_redaction_run = db_session.scalar(select(RedactionRun).where(RedactionRun.transcript_id == UUID(transcript_id)))
    assert preview_redaction_run is not None

    monkeypatch.setenv("AUDIT_TRUST_CLOUDFLARE", "true")
    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-output",
        json={"template_id": team_template_id},
        headers={"CF-Connecting-IP": "203.0.113.50", "User-Agent": "pytest-generation-api"},
    )
    assert generated.status_code == 202
    body = generated.json()
    assert body["transcript_id"] == transcript_id
    assert body["source_template_name"] == "Team SOAP"
    assert body["status"] == "queued"
    assert body["edited_output_text"] == ""
    assert body["model_used"] == "gpt-4o-mini"
    audit_event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "generation_queued"))
    assert audit_event is not None
    assert audit_event.request_ip == "203.0.113.50"
    assert audit_event.user_agent == "pytest-generation-api"
    assert audit_event.details_json["method"] == "POST"
    assert audit_event.details_json["route"] == f"/api/v1/transcripts/{transcript_id}/generate-output"

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.celery_task_id == "generated-task-1"

    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.redaction_run_id == preview_redaction_run.id
    assert processed.title == "Visit summary"
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert is_encrypted_envelope(processed.llm_request_payload_json_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "Generated note body"
    assert "messages" not in processed.llm_request_payload_json_encrypted
    stored_request_payload = generated_document_llm_request_payload(db_session, document=processed)
    assert stored_request_payload == decrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="llm_request_payload_json_encrypted",
        record_id=processed.id,
        stored_value=processed.llm_request_payload_json_encrypted,
    )
    assert stored_request_payload == captured_provider_request
    assert stored_request_payload["model"] == "gpt-4o-mini"
    assert stored_request_payload["messages"] == captured_provider_request["messages"]
    assert stored_request_payload["messages"][1]["content"] == (
        "Template name: Team SOAP\n\n"
        "Template instructions:\nWrite a concise SOAP note.\n\n"
        "Consultation transcript:\nPatient says symptoms improved."
    )
    assert "vault" not in json.dumps(stored_request_payload).lower()
    assert "authorization" not in json.dumps(stored_request_payload).lower()
    assert "generation" not in stored_request_payload
    assert "input" not in stored_request_payload
    assert "provider" not in stored_request_payload
    assert "request" not in stored_request_payload

    generated_rows = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert generated_rows.status_code == 200
    assert len(generated_rows.json()) == 1
    assert generated_rows.json()[0]["edited_output_text"] == "Generated note body"
    assert generated_rows.json()[0]["input_token_count"] == 123
    assert generated_rows.json()[0]["output_token_count"] == 45
    assert generated_rows.json()[0]["llm_request_payload_json"] == stored_request_payload

    versions = list(db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id))))
    assert len(versions) == 1
    assert is_encrypted_envelope(versions[0].text_encrypted)
    assert (
        decrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=versions[0].id,
            stored_value=versions[0].text_encrypted,
        )
        == "Patient says symptoms improved."
    )

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-4")
    forbidden_read = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert_error(forbidden_read, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")
    forbidden_delete = client.delete(f"/api/v1/templates/personal/{personal_template_id}")
    assert_error(forbidden_delete, status_code=404, code="not_found", message="Personal template not found")


@pytest.mark.parametrize(
    ("saved_length", "saved_detail", "expected_cap", "expected_guidance"),
    [
        ("short", "concise", 800, "Use compact wording. Avoid unnecessary phrasing."),
        ("normal", "balanced", 1600, "Use clear standard clinical wording."),
        ("long", "detailed", 3200, "Use fuller wording where helpful."),
        (None, None, 1600, "Use clear standard clinical wording."),
    ],
)
def test_template_generation_uses_queued_note_options_for_cap_and_detail(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    saved_length,
    saved_detail,
    expected_cap,
    expected_guidance,
):
    team = make_team(name=f"Clinic Note Options {saved_length or 'default'}")
    admin = make_user(email=f"admin-note-options-{saved_length or 'default'}@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email=f"owner-note-options-{saved_length or 'default'}@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name=f"Options note {saved_length or 'default'}", prompt_text="Write a note.")
    captured_requests: list[dict[str, object]] = []

    class FakeTaskResult:
        id = "generated-note-options"

    def fake_generate_openai(**kwargs):
        captured_requests.append(kwargs["request_body"])
        return '{"title":"Options summary","content":"Generated note body"}', {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "duration_ms": 5}

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

    login(client, email=owner.email, password="password-2")
    if saved_length is not None and saved_detail is not None:
        saved = client.post("/api/v1/app-preferences", json={"note_generation_length": saved_length, "llm_detail_level": saved_detail})
        assert saved.status_code == 200
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Options visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient reports headaches."},
    )
    assert started.status_code == 201
    generated = client.post(f"/api/v1/transcripts/{started.json()['id']}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202

    changed = client.post("/api/v1/app-preferences", json={"note_generation_length": "long", "llm_detail_level": "detailed"})
    assert changed.status_code == 200
    processed = process_generated_document(db_session, document_id=UUID(generated.json()["id"]))
    assert processed.status is GeneratedDocumentStatus.ready

    assert captured_requests
    provider_request = captured_requests[0]
    assert provider_request["max_completion_tokens"] == expected_cap
    assert expected_guidance in provider_request["messages"][0]["content"]
    stored_request = generated_document_llm_request_payload(db_session, document=processed)
    assert stored_request["max_completion_tokens"] == expected_cap


def test_generation_waits_for_pending_transcription_then_uses_fresh_snapshot(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Generation Wait")
    admin = make_user(email="admin-generation-wait@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-generation-wait@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Wait note", prompt_text="Write a note.")

    class FakeTaskResult:
        id = "generation-wait-task"

    provider_requests: list[dict] = []

    def fake_generate_openai(**kwargs):
        provider_requests.append(kwargs["request_body"])
        return '{"title":"Waited note","content":"Generated after transcription"}', {
            "input_tokens": 10,
            "output_tokens": 6,
            "total_tokens": 16,
            "duration_ms": 5,
        }

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

    login(client, email=owner.email, password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Waiting visit", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript = db_session.get(Transcript, UUID(started.json()["id"]))
    assert transcript is not None
    job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="pending.wav",
        status=TranscriptIngestionJobStatus.queued,
    )
    transcript.status = TranscriptStatus.transcribing
    db_session.add(job)
    db_session.add(transcript)
    db_session.commit()

    generated = client.post(f"/api/v1/transcripts/{transcript.id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    db_session.refresh(transcript)
    assert transcript.status is TranscriptStatus.transcribing
    document = db_session.get(GeneratedDocument, UUID(generated.json()["id"]))
    assert document is not None
    initial_version_id = document.transcript_version_id

    with pytest.raises(GeneratedDocumentWaitingForTranscript):
        process_generated_document(db_session, document_id=document.id)
    assert provider_requests == []

    db_session.refresh(transcript)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="Final transcript text from STT.",
    )
    transcript.status = TranscriptStatus.ready
    job.status = TranscriptIngestionJobStatus.applied
    job.completed_at = utcnow()
    job.applied_at = utcnow()
    db_session.add(transcript)
    db_session.add(job)
    db_session.commit()

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.transcript_version_id != initial_version_id
    assert "Final transcript text from STT." in provider_requests[0]["messages"][1]["content"]
    fresh_version = db_session.get(TranscriptVersion, processed.transcript_version_id)
    assert fresh_version is not None
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=fresh_version.id,
        stored_value=fresh_version.text_encrypted,
    ) == "Final transcript text from STT."


def test_generation_without_pending_transcription_keeps_queued_snapshot(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Queued Snapshot")
    admin = make_user(email="admin-queued-snapshot@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-queued-snapshot@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Snapshot note", prompt_text="Write a note.")

    class FakeTaskResult:
        id = "queued-snapshot-task"

    provider_requests: list[dict] = []

    def fake_generate_openai(**kwargs):
        provider_requests.append(kwargs["request_body"])
        return '{"title":"Snapshot note","content":"Generated from queued snapshot"}', {
            "input_tokens": 10,
            "output_tokens": 6,
            "total_tokens": 16,
            "duration_ms": 5,
        }

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

    login(client, email=owner.email, password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Snapshot visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Original click-time text."},
    )
    assert started.status_code == 201
    transcript = db_session.get(Transcript, UUID(started.json()["id"]))
    assert transcript is not None

    generated = client.post(f"/api/v1/transcripts/{transcript.id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    document = db_session.get(GeneratedDocument, UUID(generated.json()["id"]))
    assert document is not None
    queued_version_id = document.transcript_version_id

    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="Edited after queue click.",
    )
    db_session.add(transcript)
    db_session.commit()

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.transcript_version_id == queued_version_id
    assert "Original click-time text." in provider_requests[0]["messages"][1]["content"]
    assert "Edited after queue click." not in provider_requests[0]["messages"][1]["content"]


def test_generation_allows_multiple_queued_followups_and_blocks_active_recording(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Generation Guards")
    admin = make_user(email="admin-generation-guards@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-generation-guards@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Guard note", prompt_text="Write a note.")

    class FakeTaskResult:
        id = "generation-guard-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    login(client, email=owner.email, password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Guard visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Ready text."},
    )
    assert started.status_code == 201
    transcript_id = started.json()["id"]

    first = client.post(f"/api/v1/transcripts/{transcript_id}/generate-followup", json={"prompt_text": "First queued follow-up"})
    assert first.status_code == 202
    second = client.post(f"/api/v1/transcripts/{transcript_id}/generate-followup", json={"prompt_text": "Second queued follow-up"})
    assert second.status_code == 202
    documents = list(db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id))))
    assert len(documents) == 2
    assert {document.status for document in documents} == {GeneratedDocumentStatus.queued}

    db_session.query(GeneratedDocument).delete()
    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript is not None
    transcript.status = TranscriptStatus.recording
    db_session.add(transcript)
    db_session.commit()
    recording = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert_error(recording, status_code=409, code="conflict", message="Stop recording before generating from this transcript")


def test_generation_wait_timeout_fails_without_llm_call(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Generation Timeout")
    admin = make_user(email="admin-generation-timeout@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-generation-timeout@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Timeout note", prompt_text="Write a note.")

    class FakeTaskResult:
        id = "generation-timeout-task"

    provider_called = False

    def fake_generate_openai(**kwargs):
        nonlocal provider_called
        provider_called = True
        return '{"title":"Should not run","content":"Should not run"}', {}

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    login(client, email=owner.email, password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Timeout visit", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript = db_session.get(Transcript, UUID(started.json()["id"]))
    assert transcript is not None
    job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="pending.wav",
        status=TranscriptIngestionJobStatus.queued,
    )
    transcript.status = TranscriptStatus.transcribing
    db_session.add(job)
    db_session.add(transcript)
    db_session.commit()

    generated = client.post(f"/api/v1/transcripts/{transcript.id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    document = db_session.get(GeneratedDocument, UUID(generated.json()["id"]))
    assert document is not None
    document.created_at = utcnow() - timedelta(seconds=121)
    job.created_at = document.created_at
    db_session.add(document)
    db_session.add(job)
    db_session.commit()

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.failed
    assert processed.error_code == "transcript_wait_timeout"
    assert provider_called is False


def test_working_note_routes_enforce_owner_mode_lock_and_clear(client, db_session, make_team, make_user):
    team = make_team(name="Working Note Clinic")
    owner = make_user(
        email="working-owner@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    other = make_user(
        email="working-other@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )

    login_response = login(client, email="working-owner@example.com", password="password-1")
    assert login_response.status_code == 200, login_response.text
    started = client.post("/api/v1/transcripts/start", json={"title": "Working note visit", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]

    empty = client.get(f"/api/v1/transcripts/{transcript_id}/working-note")
    assert empty.status_code == 200
    assert empty.json()["mode"] is None
    assert empty.json()["freeform_text"] == ""

    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "  Clinician plan: review BP next week.  "},
    )
    assert saved.status_code == 200
    assert saved.json()["mode"] == "freeform"
    assert saved.json()["freeform_text"] == "Clinician plan: review BP next week."
    assert saved.json()["updated_at"] is not None
    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript.working_note_mode is TranscriptWorkingNoteMode.freeform
    assert transcript.freeform_working_note_encrypted != "Clinician plan: review BP next week."

    stale_save = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "expected_updated_at": "2000-01-01T00:00:00+00:00",
            "freeform_text": "Do not overwrite newer working note.",
        },
    )
    assert_error(stale_save, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    omitted_expected_save = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "freeform_text": "Do not overwrite existing working note without a version token.",
        },
    )
    assert_error(omitted_expected_save, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    current_save = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "expected_updated_at": saved.json()["updated_at"],
            "freeform_text": "Clinician plan: review BP in two weeks.",
        },
    )
    assert current_save.status_code == 200
    assert current_save.json()["freeform_text"] == "Clinician plan: review BP in two weeks."
    listed = client.get("/api/v1/transcripts")
    assert listed.status_code == 200
    listed_transcript = next(item for item in listed.json()["items"] if item["id"] == transcript_id)
    assert listed_transcript["working_note_mode"] == "freeform"
    assert listed_transcript["has_working_note"] is True

    clear_without_version = client.request("DELETE", f"/api/v1/transcripts/{transcript_id}/working-note")
    assert_error(clear_without_version, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    mode_switch = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "structured",
            "expected_updated_at": current_save.json()["updated_at"],
            "structured_note": {"profile": "emis", "sections": {"problem": ["Hypertension"]}},
        },
    )
    assert_error(mode_switch, status_code=409, code="business_rule_violation", message="Clear the working note before switching mode.")

    client.post("/api/v1/auth/logout")
    login_response = login(client, email="working-other@example.com", password="password-2")
    assert login_response.status_code == 200, login_response.text
    forbidden = client.get(f"/api/v1/transcripts/{transcript_id}/working-note")
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")

    client.post("/api/v1/auth/logout")
    login_response = login(client, email="working-owner@example.com", password="password-1")
    assert login_response.status_code == 200, login_response.text
    stale_clear = client.request(
        "DELETE",
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"expected_updated_at": saved.json()["updated_at"]},
    )
    assert_error(stale_clear, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    cleared = client.request(
        "DELETE",
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"expected_updated_at": current_save.json()["updated_at"]},
    )
    assert cleared.status_code == 204
    cleared_read = client.get(f"/api/v1/transcripts/{transcript_id}/working-note")
    assert cleared_read.status_code == 200
    assert cleared_read.json()["mode"] is None
    assert cleared_read.json()["freeform_text"] == ""
    db_session.refresh(transcript)
    assert transcript.working_note_mode is None
    assert transcript.freeform_working_note_encrypted is None

    stale_resurrection = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "expected_updated_at": current_save.json()["updated_at"],
            "freeform_text": "Do not recreate a cleared working note.",
        },
    )
    assert_error(stale_resurrection, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    unsupported_section = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "structured",
            "structured_note": {
                "profile": "emis",
                "sections": {"problem": ["Hypertension"], "typo_section": ["Do not drop me"]},
            },
        },
    )
    assert_error(
        unsupported_section,
        status_code=422,
        code="validation_error",
        message="Structured working note contains unsupported section keys",
    )


def test_empty_legacy_structured_working_note_does_not_lock_mode(client, db_session, make_team, make_user):
    team = make_team(name="Empty Legacy Working Note")
    owner = make_user(email="empty-legacy-working@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        title="Empty legacy note",
        working_note_mode=TranscriptWorkingNoteMode.structured,
        working_note_updated_at=utcnow(),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    transcript.structured_context_json = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="structured_context_json",
        record_id=transcript.id,
        plaintext={"profile": "emis", "sections": {}},
    )
    db_session.add(transcript)
    db_session.commit()

    login(client, email="empty-legacy-working@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}/working-note")
    assert detail.status_code == 200
    assert detail.json()["mode"] is None
    assert detail.json()["structured_note"] is None
    assert detail.json()["updated_at"] is None

    listed = client.get("/api/v1/transcripts")
    assert listed.status_code == 200
    listed_transcript = next(item for item in listed.json()["items"] if item["id"] == str(transcript.id))
    assert listed_transcript["working_note_mode"] is None
    assert listed_transcript["has_working_note"] is False


def test_transcript_patch_rejects_invalid_structured_context_without_clearing_working_note(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Legacy Structured Patch Team")
    owner = make_user(email="legacy-structured-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="legacy-structured-owner@example.com", password="password-1")
    started = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Legacy structured patch",
            "ingestion_mode": "whole_file",
            "structured_context_json": {
                "profile": "emis",
                "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]},
            },
        },
    )
    assert started.status_code == 201
    transcript = db_session.get(Transcript, UUID(started.json()["id"]))
    assert transcript is not None
    original_updated_at = transcript.working_note_updated_at
    assert original_updated_at is not None

    missing_version_patch = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={"structured_context_json": {"profile": "emis", "sections": {"problem": ["Do not overwrite without version"]}}},
    )
    assert_error(missing_version_patch, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    stale_patch = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={
            "expected_updated_at": "2000-01-01T00:00:00+00:00",
            "structured_context_json": {"profile": "emis", "sections": {"problem": ["Do not overwrite from stale tab"]}},
        },
    )
    assert_error(stale_patch, status_code=409, code="conflict", message="Working note changed elsewhere. Reload before saving again.")

    empty_patch = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={"structured_context_json": {}},
    )
    assert_error(
        empty_patch,
        status_code=422,
        code="validation_error",
        message="Structured working note must use EMIS profile with at least one non-empty section",
    )

    non_emis_patch = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={"structured_context_json": {"profile": "other", "sections": {"problem": ["Do not replace note"]}}},
    )
    assert_error(
        non_emis_patch,
        status_code=422,
        code="validation_error",
        message="Structured working note must use EMIS profile with at least one non-empty section",
    )

    db_session.refresh(transcript)
    assert transcript.working_note_mode is TranscriptWorkingNoteMode.structured
    assert transcript.working_note_updated_at == original_updated_at
    assert decrypt_transcript_structured_context(db_session, transcript) == {
        "profile": "emis",
        "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]},
    }

    current_patch = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={
            "expected_updated_at": original_updated_at.isoformat(),
            "structured_context_json": {"profile": "emis", "sections": {"problem": ["Updated asthma plan"]}},
        },
    )
    assert current_patch.status_code == 200
    db_session.refresh(transcript)
    assert transcript.working_note_mode is TranscriptWorkingNoteMode.structured
    assert decrypt_transcript_structured_context(db_session, transcript) == {
        "profile": "emis",
        "sections": {"problem": ["Updated asthma plan"]},
    }


def test_transcript_create_rejects_invalid_structured_context(client, db_session, make_team, make_user):
    team = make_team(name="Structured Create Rejects")
    owner = make_user(email="owner-structured-create-rejects@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="owner-structured-create-rejects@example.com", password="password-1")

    start_response = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Invalid structured start",
            "ingestion_mode": "whole_file",
            "structured_context_json": {"profile": "other", "sections": {"problem": ["Do not drop"]}},
        },
    )
    assert_error(
        start_response,
        status_code=422,
        code="validation_error",
        message="Structured working note must use EMIS profile with at least one non-empty section",
    )

    create_response = client.post(
        "/api/v1/transcripts",
        json={
            "owner_user_id": str(owner.id),
            "team_id": str(team.id),
            "title": "Invalid structured create",
            "ingestion_mode": "whole_file",
            "structured_context_json": {"profile": "emis", "sections": ["Do not drop"]},
        },
    )
    assert_error(
        create_response,
        status_code=422,
        code="validation_error",
        message="Structured working note must use EMIS profile with at least one non-empty section",
    )
    assert db_session.scalar(select(Transcript).where(Transcript.owner_user_id == owner.id)) is None


def test_template_generation_uses_saved_working_note_when_transcript_empty(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Working Note Generation")
    admin = make_user(email="working-gen-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="working-gen-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Working note template", prompt_text="Write note.")

    class FakeTaskResult:
        id = "working-note-generated-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    captured_provider_request = {}

    def fake_generate_openai(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return '{"title":"Working note","content":"Generated from working note"}', {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "duration_ms": 5}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    login(client, email="working-gen-owner@example.com", password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Typed consult", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Patient prefers conservative management."},
    )
    assert saved.status_code == 200

    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert document is not None
    assert document.working_note_mode_snapshot is TranscriptWorkingNoteMode.freeform
    assert decrypt_generated_document_field(db_session, document, "freeform_working_note_snapshot_encrypted") == "Patient prefers conservative management."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    user_message = captured_provider_request["messages"][1]["content"]
    assert "Consultation transcript:\n" in user_message
    assert "Consultation working note:\nFreeform working note:\nPatient prefers conservative management." in user_message
    versions = list(db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id))))
    assert len(versions) == 1
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=versions[0].id,
        stored_value=versions[0].text_encrypted,
    ) == ""


def test_template_generation_excludes_existing_generated_note_context(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Working Note Context Boundary")
    admin = make_user(email="working-context-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="working-context-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Boundary template", prompt_text="Write note.")

    class FakeTaskResult:
        id = "working-note-context-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    provider_requests = []

    def fake_generate_openai(**kwargs):
        provider_requests.append(kwargs["request_body"])
        return '{"title":"Generated sentinel","content":"Generated note sentinel"}', {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "duration_ms": 5}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    login(client, email="working-context-owner@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Context consult", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript anchor."},
    )
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    transcript = db_session.get(Transcript, UUID(transcript_id))
    update_post_consultation_dictation(db_session, owner, transcript_id=transcript.id, combined_text="Dictation anchor.")
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Working note anchor."},
    )
    assert saved.status_code == 200

    first_response = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert first_response.status_code == 202
    first_document = process_generated_document(db_session, document_id=UUID(first_response.json()["id"]))
    edited = client.patch(
        f"/api/v1/generated-documents/{first_document.id}",
        json={
            "expected_updated_at": first_document.updated_at.isoformat(),
            "edited_output_text": "Edited generated note sentinel",
            "sections": [],
        },
    )
    assert edited.status_code == 200

    second_response = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert second_response.status_code == 202
    process_generated_document(db_session, document_id=UUID(second_response.json()["id"]))

    assert len(provider_requests) == 2
    second_user_message = provider_requests[1]["messages"][1]["content"]
    assert "Consultation transcript:\nTranscript anchor." in second_user_message
    assert "Post-consultation dictation:\nDictation anchor." in second_user_message
    assert "Consultation working note:\nFreeform working note:\nWorking note anchor." in second_user_message
    assert "Generated note sentinel" not in second_user_message
    assert "Edited generated note sentinel" not in second_user_message


def test_template_generation_fails_closed_when_working_note_redaction_fails(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Working Note Redaction")
    admin = make_user(email="working-redact-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="working-redact-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Fail closed template", prompt_text="Write note.")

    class FakeTaskResult:
        id = "working-note-redaction-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    provider_called = False

    def fake_generate_openai(**kwargs):
        nonlocal provider_called
        provider_called = True
        return '{"title":"Unsafe","content":"Should not run"}', {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "duration_ms": 1}

    def fail_redaction(*args, **kwargs):
        raise AppError(502, "redaction_failed", "Working note could not be redacted")

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    monkeypatch.setattr("app.services.templates.redact_transient_text", fail_redaction)
    login(client, email="working-redact-owner@example.com", password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Typed consult", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Patient name is Jane Smith."},
    )
    assert saved.status_code == 200
    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert document is not None

    with pytest.raises(AppError) as exc_info:
        process_generated_document(db_session, document_id=document.id)
    assert exc_info.value.code == "redaction_failed"
    assert provider_called is False
    db_session.refresh(document)
    assert document.llm_request_payload_json_encrypted is None


def test_followup_generation_uses_saved_working_note_when_transcript_empty(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Working Note Followup")
    admin = make_user(email="working-followup-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="working-followup-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")

    class FakeTaskResult:
        id = "working-note-followup-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    captured_provider_request = {}

    def fake_generate_openai(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return "Generated follow-up body", {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "duration_ms": 5}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    redaction_inputs = []

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        redaction_inputs.append(text)
        has_name = "Jane Smith" in text
        return {
            "redacted_text": text.replace("Jane Smith", f"[PHI-{start_index}]"),
            "phi_mapping": {"phi-1": {"type": "PERSON", "value": "Jane Smith"}} if has_name else {},
            "phi_index": [{"index": start_index, "type": "PERSON", "value": "Jane Smith", "placeholder": f"[PHI-{start_index}]"}] if has_name else [],
            "phi_count": 1 if has_name else 0,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        }

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    login(client, email="working-followup-owner@example.com", password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Typed consult", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Jane Smith wants SMS follow-up after blood results."},
    )
    assert saved.status_code == 200

    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-followup",
        json={"prompt_text": "Send patient update after bloods."},
    )
    assert generated.status_code == 202
    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert document is not None
    assert document.working_note_mode_snapshot is TranscriptWorkingNoteMode.freeform
    assert decrypt_generated_document_field(db_session, document, "freeform_working_note_snapshot_encrypted") == "Jane Smith wants SMS follow-up after blood results."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert redaction_inputs == [
        "Jane Smith wants SMS follow-up after blood results.",
        "Send patient update after bloods.",
    ]
    user_message = captured_provider_request["messages"][1]["content"]
    assert "Consultation transcript:\n" in user_message
    assert "Consultation working note:\nFreeform working note:\n[PHI-1] wants SMS follow-up after blood results." in user_message
    assert "Jane Smith" not in user_message
    assert "Follow-up request:\nSend patient update after bloods." in user_message
    versions = list(db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id))))
    assert len(versions) == 1
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=versions[0].id,
        stored_value=versions[0].text_encrypted,
    ) == ""


def test_followup_generation_uses_saved_dictation_when_transcript_empty(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
):
    team = make_team(name="Dictation Followup")
    admin = make_user(email="dictation-followup-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="dictation-followup-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"], has_secret=False)
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")

    class FakeTaskResult:
        id = "dictation-followup-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    captured_provider_request = {}

    def fake_generate_openai(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return "Generated dictation follow-up", {"input_tokens": 9, "output_tokens": 5, "total_tokens": 14, "duration_ms": 4}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

    login(client, email="dictation-followup-owner@example.com", password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Dictation-only followup", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    transcript = db_session.get(Transcript, UUID(transcript_id))
    update_post_consultation_dictation(db_session, owner, transcript_id=transcript.id, combined_text="Book repeat U&E blood test next week.")

    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-followup",
        json={"prompt_text": "Create patient SMS."},
    )
    assert generated.status_code == 202

    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert document is not None
    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "Generated dictation follow-up"
    user_message = captured_provider_request["messages"][1]["content"]
    assert "Consultation transcript:\n" in user_message
    assert "Post-consultation dictation:\nBook repeat U&E blood test next week." in user_message
    assert "Follow-up request:\nCreate patient SMS." in user_message


def test_quick_action_generation_uses_saved_working_note_when_transcript_empty(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Working Note Quick Action")
    admin = make_user(email="working-quick-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="working-quick-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    quick_action = make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="Patient message", prompt_text="Write a patient SMS.")

    class FakeTaskResult:
        id = "working-note-quick-action-task"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    captured_provider_request = {}

    def fake_generate_openai(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return "Generated quick action body", {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "duration_ms": 5}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    redaction_inputs = []

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        redaction_inputs.append(text)
        return {
            "redacted_text": text.replace("Jane Smith", f"[PHI-{start_index}]"),
            "phi_mapping": {"phi-1": {"type": "PERSON", "value": "Jane Smith"}},
            "phi_index": [{"index": start_index, "type": "PERSON", "value": "Jane Smith", "placeholder": f"[PHI-{start_index}]"}],
            "phi_count": 1,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        }

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    login(client, email="working-quick-owner@example.com", password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Typed consult", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Jane Smith prefers a text update after blood results."},
    )
    assert saved.status_code == 200

    generated = client.post(f"/api/v1/transcripts/{transcript_id}/run-quick-action", json={"quick_action_id": str(quick_action.id)})
    assert generated.status_code == 202
    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert document is not None
    assert document.working_note_mode_snapshot is TranscriptWorkingNoteMode.freeform
    assert decrypt_generated_document_field(db_session, document, "freeform_working_note_snapshot_encrypted") == "Jane Smith prefers a text update after blood results."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert redaction_inputs == ["Jane Smith prefers a text update after blood results."]
    user_message = captured_provider_request["messages"][1]["content"]
    assert "Consultation transcript:\n" in user_message
    assert "Consultation working note:\nFreeform working note:\n[PHI-1] prefers a text update after blood results." in user_message
    assert "Jane Smith" not in user_message
    assert "Quick action instructions:\nWrite a patient SMS." in user_message


def test_quick_action_generation_fails_closed_when_working_note_redaction_fails(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Working Note Quick Redaction")
    admin = make_user(email="working-quick-redact-admin@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="working-quick-redact-owner@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, model_name_override="gpt-4o-mini")
    quick_action = make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="Patient message", prompt_text="Write a patient SMS.")
    provider_called = False

    def fake_generate_openai(**kwargs):
        nonlocal provider_called
        provider_called = True
        return "Unsafe output", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "duration_ms": 1}

    def fail_redaction(*args, **kwargs):
        raise AppError(502, "redaction_failed", "Working note could not be redacted")

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    monkeypatch.setattr("app.services.templates.redact_transient_text", fail_redaction)
    login(client, email="working-quick-redact-owner@example.com", password="password-2")
    started = client.post("/api/v1/transcripts/start", json={"title": "Typed consult", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Patient name is Jane Smith."},
    )
    assert saved.status_code == 200
    document = queue_quick_action_generation(db_session, owner, transcript_id=UUID(transcript_id), quick_action_id=quick_action.id)

    with pytest.raises(AppError) as exc_info:
        process_generated_document(db_session, document_id=document.id)
    assert exc_info.value.code == "redaction_failed"
    assert provider_called is False
    db_session.refresh(document)
    assert document.llm_request_payload_json_encrypted is None


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
        lambda **kwargs: ('{"title":"Ollama summary","content":"Ollama note body"}', {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50, "duration_ms": 15, "provider_duration_ms": 9}),
    )

    login(client, email="owner-ollama@example.com", password="password-2")
    saved_preferences = client.post("/api/v1/app-preferences", json={"note_generation_length": "long", "llm_detail_level": "balanced"})
    assert saved_preferences.status_code == 200
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
    assert processed.title == "Ollama summary"
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "Ollama note body"
    assert persisted_document.model_used == "llama3.2"
    assert generated_document_llm_request_payload(db_session, document=processed)["options"] == {"num_predict": 3200}


def test_template_generation_supports_bedrock_adapter(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    team = make_team(name="Clinic Bedrock")
    admin = make_user(email="admin-bedrock-template@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-bedrock-template@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(
        team=team,
        actor=admin,
        adapter_kind=LlmAdapterKind.bedrock_chat,
        base_url="https://bedrock-mantle.us-east-1.api.aws/v1",
        model_name="anthropic.claude-3-7-sonnet-20250219-v1:0",
        available_models_json=["anthropic.claude-3-7-sonnet-20250219-v1:0"],
        has_secret=True,
    )
    make_llm_selection(
        config=config,
        actor=admin,
        allowed_models_json=["anthropic.claude-3-7-sonnet-20250219-v1:0"],
        model_name_override="anthropic.claude-3-7-sonnet-20250219-v1:0",
    )
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Bedrock note", prompt_text="Write a concise note.")

    class FakeTaskResult:
        id = "generated-task-bedrock"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: ('{"title":"Bedrock summary","content":"Bedrock note body"}', {"input_tokens": 12, "output_tokens": 18, "total_tokens": 30, "duration_ms": 20}),
    )

    login(client, email="owner-bedrock-template@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Bedrock visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript draft."},
    )
    assert started.status_code == 201
    transcript_id = started.json()["id"]

    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202
    assert generated.json()["status"] == "queued"

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.llm_adapter_kind == "bedrock_chat"
    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.title == "Bedrock summary"
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "Bedrock note body"
    assert persisted_document.model_used == "anthropic.claude-3-7-sonnet-20250219-v1:0"


def test_structured_emis_template_generation_persists_sections(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    team = make_team(name="Clinic Structured")
    admin = make_user(email="admin-structured@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-structured@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="EMIS note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
                {"section_key": "history", "section_label": "History", "instruction": "Summarise the history.", "section_order": 2},
                {"section_key": "tasks", "section_label": "Tasks", "instruction": "List the tasks.", "section_order": 3},
            ],
        },
    )

    class FakeTaskResult:
        id = "generated-task-structured"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())
    monkeypatch.setattr(
        "app.services.templates._generate_freeform_output_openai",
        lambda **kwargs: (
            '{"title":"Chest review","content":{"problem":"Asthma flare.","history":"Cough improving.","tasks":"Repeat peak flow diary."}}',
            {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "duration_ms": 25},
        ),
    )

    login(client, email="owner-structured@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Structured visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript draft."},
    )
    transcript_id = started.json()["id"]
    saved_context = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "structured",
            "structured_note": {
                "profile": "emis",
                "sections": {"problem": ["Known asthma"], "tasks": ["Need safety netting"]},
            },
        },
    )
    assert saved_context.status_code == 200

    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-output",
        json={"template_id": str(template.id)},
    )
    assert generated.status_code == 202

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.document_mode is TemplateMode.structured
    assert processed.title == "Chest review"
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert "Problem\nAsthma flare." in (decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") or "")
    sections = list(
        db_session.scalars(
            select(GeneratedDocumentSection)
            .where(GeneratedDocumentSection.generated_document_id == processed.id)
            .order_by(GeneratedDocumentSection.section_order.asc())
        )
    )
    assert [section.section_key for section in sections] == ["problem", "history", "tasks"]
    assert is_encrypted_envelope(sections[0].edited_text_encrypted)
    assert decrypt_generated_document_section_field(
        db_session,
        owner_user_id=owner.id,
        section=sections[0],
        field="edited_text_encrypted",
    ) == "Asthma flare."
    detail = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert detail.status_code == 200
    assert detail.json()[0]["document_mode"] == "structured"
    assert len(detail.json()[0]["sections"]) == 3
    persisted_transcript = db_session.get(Transcript, UUID(transcript_id))
    assert decrypt_transcript_structured_context(db_session, persisted_transcript) == {
        "profile": "emis",
        "sections": {
            "problem": ["Known asthma"],
            "tasks": ["Need safety netting"],
        },
    }


def test_system_admin_can_manage_hallucination_check_selection(client, db_session, make_team, make_user, make_llm_config):
    team = make_team(name="Clinic Hallucination Checker")
    admin = make_user(email="admin-hcheck@example.com", password="password-1", is_system_admin=True)
    leader = make_user(email="leader-hcheck@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    config = make_llm_config(
        team=team,
        actor=admin,
        model_name="gpt-4o-mini",
        available_models_json=["gpt-4o-mini", "gpt-4.1-mini"],
        has_secret=False,
    )

    login(client, email="leader-hcheck@example.com", password="password-2")
    forbidden = client.post(
        "/api/v1/hallucination-check-selection",
        json={"team_id": str(team.id), "llm_config_id": str(config.id), "model_name_override": "gpt-4.1-mini"},
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="System admin access required")

    client.post("/api/v1/auth/logout")
    login(client, email="admin-hcheck@example.com", password="password-1")
    selected = client.post(
        "/api/v1/hallucination-check-selection",
        json={"team_id": str(team.id), "llm_config_id": str(config.id), "model_name_override": "gpt-4.1-mini"},
    )
    assert selected.status_code == 200
    assert selected.json()["team_id"] == str(team.id)
    assert selected.json()["resolved_model_name"] == "gpt-4.1-mini"
    assert db_session.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id)) is not None

    fetched = client.get(f"/api/v1/hallucination-check-selection?team_id={team.id}")
    assert fetched.status_code == 200
    assert fetched.json()["llm_config_id"] == str(config.id)

    cleared = client.delete(f"/api/v1/hallucination-check-selection?team_id={team.id}")
    assert cleared.status_code == 204
    assert db_session.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id)) is None


def test_structured_hallucination_check_applies_exact_patch_and_records_debug(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    monkeypatch.setenv("HALLUCINATION_CHECK_DEBUG_UI", "1")
    team = make_team(name="Clinic Hallucination Runtime")
    admin = make_user(email="admin-hcheck-runtime@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-hcheck-runtime@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"], has_secret=False)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    db_session.add(
        TeamHallucinationCheckSelection(
            team_id=team.id,
            llm_config_id=config.id,
            model_name_override="gpt-4o-mini",
            selected_by_user_id=admin.id,
        )
    )
    db_session.commit()
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="EMIS checker note",
        prompt_text="Use source only.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
                {"section_key": "tasks", "section_label": "Tasks", "instruction": "List the tasks.", "section_order": 2},
            ],
        },
    )

    calls = []

    def fake_generate_openai(**kwargs):
        calls.append(kwargs["request_body"])
        if len(calls) == 1:
            return (
                '{"title":"Cough review","content":{"problem":"Cough improving. Diagnosed pneumonia.","tasks":"Continue fluids. Start antibiotics."}}',
                {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "duration_ms": 25},
            )
        return (
            '{"status":"corrected","edits":[{"section_key":"problem","original":" Diagnosed pneumonia.","replacement":""},{"section_key":"tasks","original":" Start antibiotics.","replacement":""}]}',
            {"input_tokens": 11, "output_tokens": 12, "total_tokens": 23, "duration_ms": 15},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

    login(client, email="owner-hcheck-runtime@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Checker visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient says cough improving and will continue fluids."},
    )
    transcript_id = started.json()["id"]
    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202

    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.hallucination_check_status is HallucinationCheckStatus.checked_corrected
    assert processed.hallucination_check_applied_edit_count == 2
    assert processed.hallucination_check_llm_config_id == config.id
    assert "Template instructions" not in calls[1]["messages"][1]["content"]
    assert "TRANSCRIPT" in calls[1]["messages"][1]["content"]

    sections = {section.section_key: section for section in processed.sections}
    assert decrypt_generated_document_section_field(db_session, owner_user_id=owner.id, section=sections["problem"], field="edited_text_encrypted") == "Cough improving."
    assert decrypt_generated_document_section_field(db_session, owner_user_id=owner.id, section=sections["tasks"], field="edited_text_encrypted") == "Continue fluids."
    assert "Diagnosed pneumonia" not in (decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") or "")
    assert is_encrypted_envelope(processed.hallucination_check_debug_json_encrypted)

    detail = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    assert detail.status_code == 200
    assert detail.json()[0]["hallucination_check_bucket"] == "checked"
    assert detail.json()[0]["hallucination_check_debug_json"]["initial_note"]["sections"][0]["content"] == "Cough improving. Diagnosed pneumonia."
    assert detail.json()[0]["hallucination_check_debug_json"]["checker_edits"][1]["original"] == " Start antibiotics."


def test_structured_hallucination_check_provider_failure_records_safe_debug(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    monkeypatch.setenv("HALLUCINATION_CHECK_DEBUG_UI", "1")
    team = make_team(name="Clinic Hallucination Provider Fail")
    admin = make_user(email="admin-hcheck-fail@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-hcheck-fail@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"], has_secret=False)
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    db_session.add(
        TeamHallucinationCheckSelection(
            team_id=team.id,
            llm_config_id=config.id,
            model_name_override="gpt-4o-mini",
            selected_by_user_id=admin.id,
        )
    )
    db_session.commit()
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="EMIS checker provider fail",
        prompt_text="Use source only.",
        mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [{"section_key": "problem", "section_label": "Problem", "instruction": "Summarise.", "section_order": 1}]},
    )

    calls = []

    def fake_generate_openai(**kwargs):
        calls.append(kwargs["request_body"])
        if len(calls) == 1:
            return ('{"title":"Cough review","content":{"problem":"Cough improving."}}', {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "duration_ms": 25})
        raise AppError(
            502,
            "llm_generation_failed",
            "The LLM provider rejected the generation request",
            {"provider_http_status": 400, "provider_error_code": "bad_request"},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)

    login(client, email="owner-hcheck-fail@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Checker provider fail", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient says cough improving."},
    )
    transcript_id = started.json()["id"]
    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202

    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.hallucination_check_status is HallucinationCheckStatus.failed_provider

    detail = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    debug = detail.json()[0]["hallucination_check_debug_json"]
    assert detail.json()[0]["hallucination_check_bucket"] == "unchecked"
    assert debug["failure_code"] == "llm_generation_failed"
    assert debug["failure_message"] == "The LLM provider rejected the generation request"
    assert debug["provider_http_status"] == 400
    assert debug["provider_error_code"] == "bad_request"


def test_structured_hallucination_check_vault_failure_does_not_fail_document(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    monkeypatch.setenv("HALLUCINATION_CHECK_DEBUG_UI", "1")
    team = make_team(name="Clinic Hallucination Vault Fail")
    admin = make_user(email="admin-hcheck-vault@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-hcheck-vault@example.com", password="password-2", team=team, team_role=TeamRole.user)
    main_config = make_llm_config(team=team, actor=admin, label="Main LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"], has_secret=False)
    checker_config = make_llm_config(team=team, actor=admin, label="Checker LLM", model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"], has_secret=True)
    make_llm_selection(config=main_config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    db_session.add(
        TeamHallucinationCheckSelection(
            team_id=team.id,
            llm_config_id=checker_config.id,
            model_name_override="gpt-4o-mini",
            selected_by_user_id=admin.id,
        )
    )
    db_session.commit()
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="EMIS checker vault fail",
        prompt_text="Use source only.",
        mode=TemplateMode.structured,
        config_json={"profile": "emis", "sections": [{"section_key": "problem", "section_label": "Problem", "instruction": "Summarise.", "section_order": 1}]},
    )

    calls = []

    def fake_generate_openai(**kwargs):
        calls.append(kwargs["request_body"])
        return ('{"title":"Cough review","content":{"problem":"Cough improving."}}', {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "duration_ms": 25})

    def fake_read_team_llm_bearer_token(*, team_id, config_id):
        assert config_id == checker_config.id
        raise AppError(502, "vault_read_failed", "Vault secret read failed")

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_openai)
    monkeypatch.setattr("app.services.templates.read_team_llm_bearer_token", fake_read_team_llm_bearer_token)

    login(client, email="owner-hcheck-vault@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Checker vault fail", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient says cough improving."},
    )
    transcript_id = started.json()["id"]
    generated = client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
    assert generated.status_code == 202

    document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.hallucination_check_status is HallucinationCheckStatus.failed_provider
    assert processed.hallucination_check_llm_config_id == checker_config.id
    assert len(calls) == 1

    usage_event = db_session.scalar(
        select(ProviderUsageEvent).where(
            ProviderUsageEvent.generated_document_id == processed.id,
            ProviderUsageEvent.status == "hallucination_check:failed_provider",
        )
    )
    assert usage_event is not None
    assert usage_event.event_type is ProviderUsageEventType.failed
    assert usage_event.error_code == "vault_read_failed"

    detail = client.get(f"/api/v1/transcripts/{transcript_id}/generated-documents")
    debug = detail.json()[0]["hallucination_check_debug_json"]
    assert detail.json()[0]["hallucination_check_bucket"] == "unchecked"
    assert debug["failure_code"] == "vault_read_failed"
    assert debug["failure_message"] == "Vault secret read failed"


def test_openai_generation_extracts_text_from_content_part_dicts(monkeypatch):
    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=[
                                {"type": "text", "text": '{"status":"unchanged"}'},
                            ]
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("app.services.templates.OpenAI", FakeClient)

    text, usage = _generate_freeform_output_openai(
        api_key="test-key",
        base_url="https://llm.example.test/v1",
        request_body={"model": "checker-model", "messages": []},
    )

    assert text == '{"status":"unchanged"}'
    assert usage["input_tokens"] == 3
    assert usage["output_tokens"] == 4


def test_gpt_oss_hallucination_checker_uses_low_reasoning_and_larger_cap():
    request_body = {"model": "openai.gpt-oss-120b", "temperature": 0.2, "max_completion_tokens": 1600}

    _apply_hallucination_check_request_overrides(request_body, model_name="openai.gpt-oss-120b")

    assert request_body["temperature"] == 0
    assert request_body["reasoning_effort"] == "low"
    assert request_body["max_completion_tokens"] == 4000


def test_non_gpt_oss_hallucination_checker_keeps_existing_token_cap():
    request_body = {"model": "deepseek.v3.2", "temperature": 0.2, "max_completion_tokens": 1600}

    _apply_hallucination_check_request_overrides(request_body, model_name="deepseek.v3.2")

    assert request_body["temperature"] == 0
    assert "reasoning_effort" not in request_body
    assert request_body["max_completion_tokens"] == 1600


def test_structured_emis_generation_allows_blank_transcript_when_structured_context_is_present(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    team = make_team(name="Clinic Structured Blank Draft")
    admin = make_user(email="admin-structured-blank@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-structured-blank@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Blank EMIS note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
                {"section_key": "tasks", "section_label": "Tasks", "instruction": "List the tasks.", "section_order": 2},
            ],
        },
    )

    class FakeTaskResult:
        id = "generated-task-structured-blank"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    login(client, email="owner-structured-blank@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Blank structured visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    transcript_id = started.json()["id"]
    saved_context = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "structured",
            "structured_note": {
                "profile": "emis",
                "sections": {"problem": ["Patient reports headache"]},
            },
        },
    )
    assert saved_context.status_code == 200

    generated = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-output",
        json={"template_id": str(template.id)},
    )
    assert generated.status_code == 202

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    persisted_version = db_session.get(TranscriptVersion, persisted_document.transcript_version_id)
    assert persisted_version is not None
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=persisted_version.id,
        stored_value=persisted_version.text_encrypted,
    ) == ""
    persisted_transcript = db_session.get(Transcript, UUID(transcript_id))
    assert decrypt_transcript_structured_context(db_session, persisted_transcript) == {
        "profile": "emis",
        "sections": {
            "problem": ["Patient reports headache"],
        },
    }


def test_structured_emis_generation_snapshots_working_note_without_structured_context_duplication(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    team = make_team(name="Clinic Structured Reuse")
    admin = make_user(email="admin-structured-reuse@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-structured-reuse@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
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
        owner_user_id=owner.id,
        team_id=team.id,
        title="Structured visit",
        current_draft_text_encrypted="Transcript draft.",
        working_note_mode=TranscriptWorkingNoteMode.structured,
        structured_context_json={"profile": "emis", "sections": {"problem": ["Known asthma"]}},
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    class FakeTaskResult:
        id = "generated-task-structured-reuse"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    login(client, email="owner-structured-reuse@example.com", password="password-2")
    generated = client.post(
        f"/api/v1/transcripts/{transcript.id}/generate-output",
        json={"template_id": str(template.id)},
    )
    assert generated.status_code == 202

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id))
    assert persisted_document is not None
    assert persisted_document.working_note_mode_snapshot is TranscriptWorkingNoteMode.structured
    assert decrypt_generated_document_structured_context(db_session, persisted_document) is None
    assert decrypt_generated_document_structured_working_note_snapshot(db_session, persisted_document) == {
        "profile": "emis",
        "sections": {"problem": ["Known asthma"]},
    }


def test_generate_output_rejects_transient_structured_context_payload(
    client,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    db_session,
):
    team = make_team(name="Clinic Structured Payload Rejection")
    admin = make_user(email="admin-structured-filter@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-structured-filter@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Reduced EMIS note",
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
        owner_user_id=owner.id,
        team_id=team.id,
        title="Structured visit",
        current_draft_text_encrypted="Transcript draft.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    login(client, email="owner-structured-filter@example.com", password="password-2")
    generated = client.post(
        f"/api/v1/transcripts/{transcript.id}/generate-output",
        json={
            "template_id": str(template.id),
            "structured_context": {
                "problem": "Known asthma",
                "tasks": "Peak flow diary",
            },
        },
    )
    assert generated.status_code == 422
    assert generated.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "details": {"issue_count": 1},
        }
    }
    assert "structured_context" not in generated.text
    assert "Known asthma" not in generated.text
    assert "Peak flow diary" not in generated.text
    assert "extra_forbidden" not in generated.text
    assert "loc" not in generated.text

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id))
    assert persisted_document is None
    persisted_transcript = db_session.get(Transcript, transcript.id)
    assert persisted_transcript is not None
    assert decrypt_transcript_structured_context(db_session, persisted_transcript) is None


def test_template_api_returns_structured_config_json(
    client,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Clinic Template API")
    owner = make_user(email="template-api-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="EMIS note",
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

    login(client, email="template-api-owner@example.com", password="password-1")
    response = client.get("/api/v1/templates/personal")
    assert response.status_code == 200
    returned_template = next(item for item in response.json() if item["id"] == str(template.id))
    assert returned_template["latest_version"]["mode"] == "structured"
    assert returned_template["latest_version"]["config_json"] == {
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise the problem.", "section_order": 1},
            {"section_key": "history", "section_label": "History", "instruction": "Summarise the history.", "section_order": 2},
        ],
    }


def test_personal_template_api_rejects_duplicate_name_for_same_user(client, make_team, make_user, make_template):
    team = make_team(name="Clinic Template Duplicate")
    owner = make_user(email="template-dup-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Clinic Letter", prompt_text="Write note.")

    login(client, email="template-dup-owner@example.com", password="password-1")
    duplicate = client.post(
        "/api/v1/templates/personal",
        json={
            "scope": "user",
            "name": " clinic letter ",
            "description": "Duplicate by case/space",
            "prompt_text": "Write another note.",
            "is_active": True,
        },
    )

    assert_error(duplicate, status_code=409, code="conflict", message="Template name already exists")


def test_personal_quick_action_api_rejects_duplicate_name_for_same_user(client, make_team, make_user, make_quick_action):
    team = make_team(name="Clinic Quick Action Duplicate")
    owner = make_user(email="quick-action-dup-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)
    make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="Patient SMS")

    login(client, email="quick-action-dup-owner@example.com", password="password-1")
    duplicate = client.post(
        "/api/v1/quick-actions/personal",
        json={
            "scope": "user",
            "name": " patient sms ",
            "description": "Duplicate by case/space",
            "prompt_text": "Write quick action.",
            "is_active": True,
        },
    )

    assert_error(duplicate, status_code=409, code="conflict", message="Quick action name already exists")


def test_generate_freeform_output_ollama_streams_chunks_and_collects_usage(monkeypatch):
    def fake_stream(method, url, **kwargs):
        assert method == "POST"
        assert url == "http://localhost:11434/api/chat"
        assert kwargs["json"]["stream"] is True
        timeout = kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 300.0
        return FakeHttpxStreamResponse(
            [
                '{"message":{"content":"{\\"title\\":\\"Visit"},"done":false}',
                '{"message":{"content":" summary\\",\\"content\\":\\"Body text\\"}"},"done":false}',
                '{"done":true,"prompt_eval_count":17,"eval_count":23,"total_duration":9000000}',
            ]
        )

    monkeypatch.setattr("app.services.templates.httpx.stream", fake_stream)

    generated_text, usage = _generate_freeform_output_ollama(
        base_url="http://localhost:11434",
        bearer_token=None,
        request_body={
            "model": "llama3.2",
            "stream": True,
            "messages": [
                {"role": "system", "content": "System"},
                {"role": "user", "content": "User"},
            ],
        },
    )

    assert generated_text == '{"title":"Visit summary","content":"Body text"}'
    assert usage["input_tokens"] == 17
    assert usage["output_tokens"] == 23
    assert usage["total_tokens"] == 40
    assert usage["provider_duration_ms"] == 9


def test_generate_freeform_output_ollama_surfaces_stream_timeouts(monkeypatch):
    def fake_stream(*args, **kwargs):
        raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", "http://localhost:11434/api/chat"))

    monkeypatch.setattr("app.services.templates.httpx.stream", fake_stream)

    try:
        _generate_freeform_output_ollama(
            base_url="http://localhost:11434",
            bearer_token=None,
            request_body={
                "model": "llama3.2",
                "stream": True,
                "messages": [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "User"},
                ],
            },
        )
        assert False, "expected AppError"
    except AppError as exc:
        assert exc.code == "llm_provider_timeout"
        assert exc.message == "The LLM provider timed out"


def test_parse_generated_note_json_coerces_markdown_fenced_payload():
    title, content = _parse_generated_note_json(
        '```json\n{"title":"Visit summary","content":"Body text"}\n```'
    )

    assert title == "Visit summary"
    assert content == "Body text"


def test_parse_generated_note_json_coerces_surrounding_prose():
    title, content = _parse_generated_note_json(
        'Here is the note:\n{"title":"Visit summary","content":"Body text"}\nThanks.'
    )

    assert title == "Visit summary"
    assert content == "Body text"


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
    captured_provider_request = {}

    def fake_generate_followup(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return "Please arrange repeat bloods in two weeks and advise review if symptoms persist.", {"input_tokens": 12, "output_tokens": 22, "total_tokens": 34, "duration_ms": 10}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_followup)
    monkeypatch.setattr(
        "app.services.templates.redact_transient_text",
        lambda db, text, *, team_id, start_index: {
            "redacted_text": f"Arrange repeat bloods for [PHI-{start_index}] and advise review if the cough persists.",
            "phi_mapping": {"phi-1": {"type": "PERSON", "value": "John Smith"}},
            "phi_index": [{"index": start_index, "type": "PERSON", "value": "John Smith", "placeholder": f"[PHI-{start_index}]"}],
            "phi_count": 1,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        },
    )

    login(client, email="owner-followup@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Follow-up visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Patient reports a persistent cough for three weeks."},
    )
    transcript_id = started.json()["id"]

    queued = client.post(
        f"/api/v1/transcripts/{transcript_id}/generate-followup",
        json={"prompt_text": "Arrange repeat bloods for John Smith and advise review if the cough persists."},
    )
    assert queued.status_code == 202
    assert queued.json()["generator_type"] == "followup"
    assert queued.json()["status"] == "queued"
    assert queued.json()["follow_up_prompt_text"] == "Arrange repeat bloods for John Smith and advise review if the cough persists."

    persisted_document = db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id)))
    assert persisted_document is not None
    assert persisted_document.generator_type is GeneratedDocumentGeneratorType.followup
    assert persisted_document.celery_task_id == "generated-task-followup"
    assert is_encrypted_envelope(persisted_document.follow_up_prompt_text)
    assert decrypt_generated_document_field(db_session, persisted_document, "follow_up_prompt_text") == "Arrange repeat bloods for John Smith and advise review if the cough persists."

    processed = process_generated_document(db_session, document_id=persisted_document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert "repeat bloods" in (decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") or "")
    followup_payload = generated_document_llm_request_payload(db_session, document=processed)
    assert followup_payload == captured_provider_request
    assert followup_payload["messages"] == captured_provider_request["messages"]
    assert "Arrange repeat bloods for [PHI-1]" in json.dumps(followup_payload)
    assert "Arrange repeat bloods for John Smith" not in json.dumps(followup_payload)
    assert "generation" not in followup_payload
    assert "input" not in followup_payload
    assert "provider" not in followup_payload
    assert "request" not in followup_payload

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
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Redaction note", prompt_text="You are a GP who works in the NHS.")

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

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        raise AssertionError(f"static template text should not be transiently redacted: {text}")

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    monkeypatch.setattr("app.services.templates.reidentify_text", redaction_reidentify_text)

    def fake_generate(**kwargs):
        user_message = kwargs["request_body"]["messages"][1]["content"]
        assert "[PHI-1] reports headaches." in user_message
        assert "John Smith reports headaches." not in user_message
        assert "You are a GP who works in the NHS." in user_message
        assert "You are a [PHI-" not in user_message
        return (
            '{"title":"[PHI-1] review","content":"[PHI-1] should rest."}',
            {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.redaction_run_id == run.id
    assert processed.title == "John Smith review"
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "John Smith should rest."


def test_process_generated_document_applies_manual_pii_before_provider_call(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Manual PII Redaction")
    admin = make_user(email="admin-manual-redaction@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-manual-redaction@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Manual PII note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Manual PII redaction session",
        current_draft_text_encrypted="Patient lives at Riverside\n   House and reports headaches.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    create_manual_pii_entity(
        db_session,
        owner,
        transcript_id=transcript.id,
        entity_type="ADDRESS",
        value="Riverside House",
    )
    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    transcript_version = db_session.get(TranscriptVersion, document.transcript_version_id)
    assert transcript_version is not None
    run = RedactionRun(
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        owner_user_id=owner.id,
        team_id=team.id,
        status=RedactionRunStatus.succeeded,
        redacted_text_encrypted="Patient lives at Riverside\n   House and reports headaches.",
        mapping_hash="manual-redaction-source",
        entity_count=0,
        api_provider="native_presidio",
        api_model_or_version="en_core_web_sm",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    monkeypatch.setattr(
        "app.services.templates.ensure_redaction_run_for_transcript_version",
        lambda db, *, transcript_version: run,
    )
    monkeypatch.setattr("app.services.templates.reidentify_text", redaction_reidentify_text)
    monkeypatch.setattr(
        "app.services.templates.redact_transient_text",
        lambda db, text, *, team_id, start_index: {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        },
    )

    def fake_generate(**kwargs):
        user_message = kwargs["request_body"]["messages"][1]["content"]
        assert "Riverside House" not in user_message
        assert "Riverside\n   House" not in user_message
        assert "Patient lives at [PHI-1] and reports headaches." in user_message
        return (
            '{"title":"Manual PII","content":"Send letter to [PHI-1]."}',
            {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.redaction_run_id == run.id
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "Send letter to Riverside House."


def test_process_generated_document_redacts_dictation_before_provider_call(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_redaction_run,
):
    team = make_team(name="Clinic Dictation Redaction")
    admin = make_user(email="admin-dictation-redaction@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-dictation-redaction@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Dictation redaction note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Dictation redaction session",
        current_draft_text_encrypted="John Smith reports headaches.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    update_post_consultation_dictation(db_session, owner, transcript_id=transcript.id, combined_text="John Smith should book blood tests.")
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

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        if text == "John Smith should book blood tests.":
            return {
                "redacted_text": "[PHI-2] should book blood tests.",
                "phi_mapping": {
                    "phi-2": {"type": "PERSON", "value": "John Smith"},
                },
                "phi_index": [
                    {"index": 2, "type": "PERSON", "value": "John Smith", "placeholder": "[PHI-2]"},
                ],
                "phi_count": 1,
                "api_provider": "native_presidio",
                "api_model_or_version": "en_core_web_sm",
            }
        raise AssertionError(f"unexpected transient redaction input: {text}")

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    monkeypatch.setattr("app.services.templates.reidentify_text", redaction_reidentify_text)

    captured = {}

    def fake_generate(**kwargs):
        captured["user_message"] = kwargs["request_body"]["messages"][1]["content"]
        return (
            '{"title":"[PHI-1] review","content":"[PHI-2] should book review."}',
            {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert "Consultation transcript:\n[PHI-1] reports headaches." in captured["user_message"]
    assert "Post-consultation dictation:\n[PHI-2] should book blood tests." in captured["user_message"]
    assert "John Smith should book blood tests." not in captured["user_message"]
    assert processed.title == "John Smith review"
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "John Smith should book review."


def test_process_generated_document_redacts_dictation_only_session_before_provider_call(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Dictation Only Redaction")
    admin = make_user(email="admin-dictation-only-redaction@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-dictation-only-redaction@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Dictation only note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Dictation-only session",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    update_post_consultation_dictation(db_session, owner, transcript_id=transcript.id, combined_text="John Smith needs blood tests.")

    document = queue_document_generation_from_template_service(db_session, owner, transcript_id=transcript.id, template_id=template.id)
    transcript_version = db_session.get(TranscriptVersion, document.transcript_version_id)
    assert transcript_version is not None

    empty_provider_calls = []

    def reject_empty_persistent_redaction(db, text: str, *, provider, start_index: int = 1):
        empty_provider_calls.append(text)
        if not text.strip():
            raise AssertionError("empty transcript text should not be sent to de-identification provider")
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": provider.label,
            "api_model_or_version": None,
        }

    monkeypatch.setattr("app.services.redaction.redact_text_with_mapping", reject_empty_persistent_redaction)

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        if text == "John Smith needs blood tests.":
            return {
                "redacted_text": "[PHI-1] needs blood tests.",
                "phi_mapping": {"phi-1": {"type": "PERSON", "value": "John Smith"}},
                "phi_index": [{"index": 1, "type": "PERSON", "value": "John Smith", "placeholder": "[PHI-1]"}],
                "phi_count": 1,
                "api_provider": "native_presidio",
                "api_model_or_version": "en_core_web_sm",
            }
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        }

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    monkeypatch.setattr("app.services.templates.reidentify_text", redaction_reidentify_text)

    captured = {}

    def fake_generate(**kwargs):
        captured["user_message"] = kwargs["request_body"]["messages"][1]["content"]
        return (
            '{"title":"Dictation only","content":"[PHI-1] needs blood tests."}',
            {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert empty_provider_calls == []
    assert "Post-consultation dictation:\n[PHI-1] needs blood tests." in captured["user_message"]
    assert "John Smith needs blood tests." not in captured["user_message"]
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "John Smith needs blood tests."


def test_process_generated_document_redaction_boundary_for_static_and_structured_dynamic_inputs(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_redaction_run,
):
    team = make_team(name="Clinic Redaction Boundary")
    admin = make_user(email="admin-redaction-boundary@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-redaction-boundary@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="EMIS redaction boundary",
        prompt_text="You are a GP who works in the NHS.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "history", "section_label": "History", "instruction": "Summarise history.", "section_order": 1},
                {"section_key": "social_history", "section_label": "Social History", "instruction": "Summarise social history.", "section_order": 2},
            ],
        },
    )
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Boundary session",
        current_draft_text_encrypted="John Smith attended with low mood.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    update_post_consultation_dictation(db_session, owner, transcript_id=transcript.id, combined_text="John Smith dictated additional context.")
    save_working_note(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=WorkingNoteUpdate(
            mode=TranscriptWorkingNoteMode.structured,
            structured_note={
                "profile": "emis",
                "sections": {
                    "history": ["John Smith has poor sleep."],
                    "social_history": ["John Smith lives alone."],
                },
            },
        ),
    )
    document = queue_document_generation_from_template_service(
        db_session,
        owner,
        transcript_id=transcript.id,
        template_id=template.id,
    )
    transcript_version = db_session.get(TranscriptVersion, document.transcript_version_id)
    run = make_redaction_run(
        transcript=transcript,
        transcript_version=transcript_version,
        owner=owner,
        redacted_text="[PHI-1] attended with low mood.",
        entities=[(1, "PERSON", "John Smith")],
    )
    monkeypatch.setattr("app.services.templates.ensure_redaction_run_for_transcript_version", lambda db, *, transcript_version: run)

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        assert "GP" not in text
        assert "NHS" not in text
        mapping = {
            "John Smith dictated additional context.": "[PHI-2] dictated additional context.",
            "John Smith has poor sleep.": "[PHI-3] has poor sleep.",
            "John Smith lives alone.": "[PHI-4] lives alone.",
        }
        if text not in mapping:
            return {
                "redacted_text": text,
                "phi_mapping": {},
                "phi_index": [],
                "phi_count": 0,
                "api_provider": "native_presidio",
                "api_model_or_version": "en_core_web_sm",
            }
        redacted = mapping[text]
        return {
            "redacted_text": redacted,
            "phi_mapping": {f"phi-{start_index}": {"type": "PERSON", "value": "John Smith"}},
            "phi_index": [{"index": start_index, "type": "PERSON", "value": "John Smith", "placeholder": f"[PHI-{start_index}]"}],
            "phi_count": 1,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        }

    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    captured_provider_request = {}

    def fake_generate(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return (
            '{"title":"[PHI-1] review","content":{"history":"[PHI-3] has poor sleep.","social_history":"[PHI-4] lives alone."}}',
            {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9},
        )

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate)

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    provider_request_json = json.dumps(captured_provider_request)
    assert "You are a GP who works in the NHS." in provider_request_json
    assert "You are a [PHI-" not in provider_request_json
    assert "[PHI-1] attended with low mood." in provider_request_json
    assert "John Smith attended with low mood." not in provider_request_json
    assert "[PHI-2] dictated additional context." in provider_request_json
    assert "John Smith dictated additional context." not in provider_request_json
    assert "[PHI-3] has poor sleep." in provider_request_json
    assert "[PHI-4] lives alone." in provider_request_json
    assert "John Smith has poor sleep." not in provider_request_json
    assert "John Smith lives alone." not in provider_request_json
    displayed_request = generated_document_llm_request_payload(db_session, document=processed)
    assert displayed_request == captured_provider_request
    assert "generation" not in displayed_request
    assert "input" not in displayed_request
    assert "provider" not in displayed_request
    assert "request" not in displayed_request


def test_ensure_redaction_run_encrypts_redacted_text_and_entity_values(
    db_session,
    monkeypatch,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Redaction Encryption")
    owner = make_user(email="owner-redaction-encryption@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript_id = uuid4()
    transcript = Transcript(
        id=transcript_id,
        owner_user_id=owner.id,
        team_id=team.id,
        title="Encrypted redaction session",
        current_draft_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript_id,
            plaintext="John Smith reports headaches.",
        ),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    version_id = uuid4()
    transcript_version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext="John Smith reports headaches.",
        ),
    )
    db_session.add(transcript_version)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.redaction.redact_text_with_mapping",
        lambda db, text, **kwargs: {
            "redacted_text": "[PHI-1] reports headaches.",
            "phi_mapping": {"phi-1": {"type": "PERSON", "value": "John Smith"}},
            "phi_index": [{"index": 1, "type": "PERSON", "value": "John Smith", "placeholder": "[PHI-1]"}],
            "phi_count": 1,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        },
    )

    run = ensure_redaction_run_for_transcript_version(db_session, transcript_version=transcript_version)

    assert is_encrypted_envelope(run.redacted_text_encrypted)
    assert (
        decrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="redaction_runs",
            field="redacted_text_encrypted",
            record_id=run.id,
            stored_value=run.redacted_text_encrypted,
        )
        == "[PHI-1] reports headaches."
    )
    entity = run.entities[0]
    assert is_encrypted_envelope(entity.original_value_encrypted)
    assert (
        decrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="redaction_entities",
            field="original_value_encrypted",
            record_id=entity.id,
            stored_value=entity.original_value_encrypted,
        )
        == "John Smith"
    )


def test_process_generated_document_uses_first_note_title_to_fill_default_session_title(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Session Title")
    admin = make_user(email="admin-session-title@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-session-title@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Session title note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Untitled session",
        current_draft_text_encrypted="Patient reports ankle pain.",
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
        lambda **kwargs: ('{"title":"Ankle review","content":"Generated note body"}', {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.title == "Ankle review"
    db_session.refresh(transcript)
    assert transcript.title == "Ankle review"


def test_process_generated_document_does_not_overwrite_custom_session_title(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Preserve Session Title")
    admin = make_user(email="admin-preserve-title@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-preserve-title@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Session title note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Telephone review",
        current_draft_text_encrypted="Patient reports improved symptoms.",
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
        lambda **kwargs: ('{"title":"Improvement review","content":"Generated note body"}', {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.title == "Improvement review"
    db_session.refresh(transcript)
    assert transcript.title == "Telephone review"


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
        lambda **kwargs: ('{"title":"Broken review","content":"[PHI-999] should rest."}', {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.failed
    assert processed.error_code == "redaction_placeholder_invalid"
    assert processed.error_message == "Generated output contained an unknown PHI placeholder"


def test_process_generated_document_fails_on_invalid_note_json(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Invalid JSON")
    admin = make_user(email="admin-invalid-json@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-invalid-json@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="Invalid JSON note", prompt_text="Write a note.")

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Invalid JSON session",
        current_draft_text_encrypted="Patient reports dizziness.",
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
        lambda **kwargs: ("not-json", {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.failed
    assert processed.error_code == "llm_generation_invalid_json"
    assert processed.provider_error_code == "invalid_json"


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
    assert body["failed_provider_output_redacted_text"] is None
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


def test_local_dev_account_redaction_debug_includes_failed_provider_output(
    client,
    db_session,
    make_team,
    make_user,
    make_redaction_run,
):
    team = make_team(name="Clinic Failed Redaction Debug")
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
        status=GeneratedDocumentStatus.failed,
        title="Clinic note",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        failed_provider_output_redacted_encrypted='{"title":"Broken"',
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.commit()

    login(client, email="dev.user@example.com", password="password-1")
    response = client.get(f"/api/v1/generated-documents/{document.id}/redaction-debug")

    assert response.status_code == 200
    body = response.json()
    assert body["failed_provider_output_redacted_text"] == '{"title":"Broken"'


def test_owner_can_delete_generated_document_and_sections_cascade(client, db_session, make_team, make_user):
    team = make_team(name="Clinic Generated Document Delete")
    owner = make_user(email="owner-generated-delete@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-generated-delete@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Delete document session",
        current_draft_text_encrypted="Patient is improving.",
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
        text_encrypted="Patient is improving.",
    )
    db_session.add(transcript_version)
    db_session.commit()
    document = GeneratedDocument(
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        source_template_name="Clinic note",
        status=GeneratedDocumentStatus.ready,
        title="Clinic note",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="Problem\nImproving.",
        edited_output_text_encrypted="Problem\nImproving.",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.flush()
    section = GeneratedDocumentSection(
        generated_document_id=document.id,
        section_key="problem",
        section_label="Problem",
        section_order=1,
        original_text_encrypted="Improving.",
        edited_text_encrypted="Improving.",
    )
    db_session.add(section)
    db_session.commit()

    login(client, email="other-generated-delete@example.com", password="password-2")
    forbidden = client.delete(f"/api/v1/generated-documents/{document.id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="Generated document access is restricted to the owning user")
    assert db_session.get(GeneratedDocument, document.id) is not None

    login(client, email="owner-generated-delete@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/generated-documents/{document.id}")
    assert deleted.status_code == 204
    assert db_session.get(GeneratedDocument, document.id) is None
    assert db_session.get(GeneratedDocumentSection, section.id) is None
    assert db_session.get(Transcript, transcript.id) is not None


def test_generated_document_update_saves_note_content_and_detects_revision_conflicts(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Note Save API")
    owner = make_user(email="owner-note-save@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-note-save@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        current_draft_text_encrypted="Patient improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=uuid4(),
            plaintext="Patient improving.",
        ),
    )
    db_session.add(transcript_version)
    db_session.flush()
    transcript_version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=transcript_version.id,
        plaintext="Patient improving.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=None,
        source_template_name="Clinic note",
        status=GeneratedDocumentStatus.ready,
        title="Clinic note",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.flush()
    document.original_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="original_output_text_encrypted",
        record_id=document.id,
        plaintext="Problem\nImproving.",
    )
    document.edited_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="edited_output_text_encrypted",
        record_id=document.id,
        plaintext="Problem\nImproving.",
    )
    db_session.add(document)
    db_session.flush()
    section = GeneratedDocumentSection(
        id=uuid4(),
        generated_document_id=document.id,
        section_key="problem",
        section_label="Problem",
        section_order=0,
        original_text_encrypted="",
        edited_text_encrypted="",
        is_edited=False,
    )
    db_session.add(section)
    db_session.flush()
    section.original_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_document_sections",
        field="original_text_encrypted",
        record_id=section.id,
        plaintext="Improving.",
    )
    section.edited_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_document_sections",
        field="edited_text_encrypted",
        record_id=section.id,
        plaintext="Improving.",
    )
    db_session.add(section)
    db_session.commit()

    login(client, email="owner-note-save@example.com", password="password-1")
    saved = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": document.updated_at.isoformat(),
            "edited_output_text": "",
            "sections": [
                {
                    "section_key": "problem",
                    "section_label": "Problem",
                    "section_order": 0,
                    "text": "Improving more",
                },
                {
                    "section_key": "tasks",
                    "section_label": "Tasks",
                    "section_order": 1,
                    "text": "Review in two weeks",
                },
            ],
        },
    )

    assert saved.status_code == 200
    db_session.refresh(document)
    persisted_sections = {item.section_key: item for item in document.sections}
    assert decrypt_generated_document_field(db_session, document, "edited_output_text_encrypted") == "Problem\nImproving more\n\nTasks\nReview in two weeks"
    assert decrypt_generated_document_section_field(db_session, owner_user_id=owner.id, section=persisted_sections["problem"], field="edited_text_encrypted") == "Improving more"
    assert decrypt_generated_document_section_field(db_session, owner_user_id=owner.id, section=persisted_sections["tasks"], field="edited_text_encrypted") == "Review in two weeks"
    assert document.is_edited is True
    assert document.last_edited_at is not None

    stale = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": "2000-01-01T00:00:00+00:00",
            "edited_output_text": "",
            "sections": [],
        },
    )
    assert_error(stale, status_code=409, code="conflict", message="Generated document has changed. Reload note before saving again.")

    login(client, email="other-note-save@example.com", password="password-2")
    forbidden = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": document.updated_at.isoformat(),
            "edited_output_text": "",
            "sections": [],
        },
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Generated document access is restricted to the owning user")


def test_generated_document_update_saves_followup_title_and_body_for_owner(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Followup Save API")
    owner = make_user(email="owner-followup-save@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-followup-save@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        current_draft_text_encrypted="Patient improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
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
    transcript_version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=transcript_version.id,
        plaintext="Patient improving.",
    )
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.followup,
        source_template_name="Follow-up",
        follow_up_prompt_text="Write a patient message.",
        status=GeneratedDocumentStatus.ready,
        title="Original follow-up",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.flush()
    document.original_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="original_output_text_encrypted",
        record_id=document.id,
        plaintext="Old body",
    )
    document.edited_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="edited_output_text_encrypted",
        record_id=document.id,
        plaintext="Old body",
    )
    db_session.add_all([transcript_version, document])
    db_session.commit()

    login(client, email="owner-followup-save@example.com", password="password-1")
    saved = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": document.updated_at.isoformat(),
            "title": "Edited patient message",
            "edited_output_text": "Line one\n\nLine two",
            "sections": [],
        },
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["title"] == "Edited patient message"
    assert body["edited_output_text"] == "Line one\n\nLine two"
    db_session.refresh(document)
    assert document.title == "Edited patient message"
    assert decrypt_generated_document_field(db_session, document, "edited_output_text_encrypted") == "Line one\n\nLine two"
    assert document.is_edited is True
    assert document.last_edited_at is not None

    login(client, email="other-followup-save@example.com", password="password-2")
    forbidden = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": document.updated_at.isoformat(),
            "title": "Wrong owner edit",
            "edited_output_text": "Nope",
            "sections": [],
        },
    )
    assert_error(forbidden, status_code=403, code="forbidden", message="Generated document access is restricted to the owning user")


def test_generated_document_update_rejects_duplicate_structured_section_keys(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Structured Duplicate Team")
    owner = make_user(email="owner-note-duplicate@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        current_draft_text_encrypted="Patient improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=uuid4(),
            plaintext="Patient improving.",
        ),
    )
    db_session.add(transcript_version)
    db_session.flush()
    transcript_version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=transcript_version.id,
        plaintext="Patient improving.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=None,
        source_template_name="Clinic note",
        status=GeneratedDocumentStatus.ready,
        title="Clinic note",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.flush()
    document.original_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="original_output_text_encrypted",
        record_id=document.id,
        plaintext="Problem\nImproving.",
    )
    document.edited_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="edited_output_text_encrypted",
        record_id=document.id,
        plaintext="Problem\nImproving.",
    )
    db_session.add(document)
    db_session.commit()

    login(client, email="owner-note-duplicate@example.com", password="password-1")
    duplicate = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": document.updated_at.isoformat(),
            "edited_output_text": "",
            "sections": [
                {
                    "section_key": "problem",
                    "section_label": "Problem",
                    "section_order": 0,
                    "text": "Improving more",
                },
                {
                    "section_key": "problem",
                    "section_label": "Problem again",
                    "section_order": 1,
                    "text": "Duplicated section",
                },
            ],
        },
    )

    assert_error(duplicate, status_code=422, code="business_rule_violation", message="Structured note sections must not repeat keys")
    db_session.refresh(document)
    assert decrypt_generated_document_field(db_session, document, "edited_output_text_encrypted") == "Problem\nImproving."


def test_generated_document_update_rejects_sections_removed_by_template(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Structured Restricted Team")
    owner = make_user(email="owner-note-restricted@example.com", password="password-1", team=team, team_role=TeamRole.user)
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Restricted EMIS note",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise problem.", "section_order": 1},
                {"section_key": "history", "section_label": "History", "instruction": "Summarise history.", "section_order": 2},
            ],
        },
    )
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        current_draft_text_encrypted="Patient improving.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=uuid4(),
            plaintext="Patient improving.",
        ),
    )
    db_session.add(transcript_version)
    db_session.flush()
    transcript_version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=transcript_version.id,
        plaintext="Patient improving.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template.versions[-1].id,
        source_template_name="Restricted EMIS note",
        status=GeneratedDocumentStatus.ready,
        title="Clinic note",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(document)
    db_session.flush()
    document.original_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="original_output_text_encrypted",
        record_id=document.id,
        plaintext="Problem\nImproving.",
    )
    document.edited_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="edited_output_text_encrypted",
        record_id=document.id,
        plaintext="Problem\nImproving.",
    )
    db_session.add(document)
    db_session.commit()

    login(client, email="owner-note-restricted@example.com", password="password-1")
    invalid = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": document.updated_at.isoformat(),
            "edited_output_text": "",
            "sections": [
                {
                    "section_key": "problem",
                    "section_label": "Problem",
                    "section_order": 0,
                    "text": "Improving more",
                },
                {
                    "section_key": "tasks",
                    "section_label": "Tasks",
                    "section_order": 1,
                    "text": "Should not be allowed",
                },
            ],
        },
    )

    assert_error(invalid, status_code=422, code="business_rule_violation", message="Structured note section is invalid")


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
        lambda **kwargs: ("Please arrange repeat bloods in one week and ask the patient to book a GP review if symptoms persist.", {"input_tokens": 21, "output_tokens": 33, "total_tokens": 54, "duration_ms": 10}),
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
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert "repeat bloods" in (decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") or "")

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


def test_run_quick_action_rejects_oversized_context_text(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Quick Action Limits")
    admin = make_user(email="admin-quick-limit@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-quick-limit@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    quick_action = make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="SMS", prompt_text="Write SMS.")

    class FakeTaskResult:
        id = "generated-task-quick-limit"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    login(client, email="owner-quick-limit@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Quick action visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript draft."},
    )
    transcript_id = started.json()["id"]

    oversized = client.post(
        f"/api/v1/transcripts/{transcript_id}/run-quick-action",
        json={"quick_action_id": str(quick_action.id), "context_text": "x" * 4001},
    )

    assert oversized.status_code == 422
    assert db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id))) is None


def test_run_quick_action_rejects_empty_consultation_sources(
    client,
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Clinic Quick Action Empty Sources")
    owner = make_user(email="owner-quick-empty@example.com", password="password-2", team=team, team_role=TeamRole.user)
    quick_action = make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="SMS", prompt_text="Write SMS.")

    login(client, email="owner-quick-empty@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Quick action empty", "ingestion_mode": "whole_file", "current_draft_text_encrypted": ""},
    )
    transcript_id = started.json()["id"]

    response = client.post(
        f"/api/v1/transcripts/{transcript_id}/run-quick-action",
        json={"quick_action_id": str(quick_action.id)},
    )

    assert_error(response, status_code=422, code="business_rule_violation", message="Transcript draft is empty")
    assert db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.transcript_id == UUID(transcript_id))) is None


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
        lambda **kwargs: ('{"title":"Snapshot note","content":"Generated note body"}', {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12, "duration_ms": 9}),
    )

    from app.services.templates import delete_personal_template as delete_personal_template_service

    delete_personal_template_service(db_session, owner, template_id=template.id)
    refreshed = db_session.get(GeneratedDocument, document.id)
    assert refreshed is not None
    assert refreshed.template_version_id is None
    assert refreshed.prompt_snapshot_text == "Write a concise note."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.title == "Snapshot note"
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "Generated note body"


def test_structured_generated_document_keeps_allowed_sections_after_template_delete(
    client,
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Clinic Structured Snapshot")
    admin = make_user(email="admin-template-structured-snapshot@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-template-structured-snapshot@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, model_name="gpt-4o-mini", available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=admin, allowed_models_json=["gpt-4o-mini"], model_name_override="gpt-4o-mini")
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Restricted template",
        prompt_text="Use British English.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "instruction": "Summarise problem.", "section_order": 1},
                {"section_key": "history", "section_label": "History", "instruction": "Summarise history.", "section_order": 2},
            ],
        },
    )
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
    refreshed = db_session.get(GeneratedDocument, document.id)
    assert refreshed is not None
    refreshed.status = GeneratedDocumentStatus.ready
    db_session.add(refreshed)
    db_session.commit()
    db_session.refresh(refreshed)
    assert refreshed.structured_section_definitions_json == {
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "section_label": "Problem", "section_order": 1},
            {"section_key": "history", "section_label": "History", "section_order": 2},
        ],
    }

    delete_personal_template(db_session, owner, template_id=template.id)
    db_session.refresh(refreshed)
    assert refreshed.template_version_id is None

    login(client, email="owner-template-structured-snapshot@example.com", password="password-2")
    invalid = client.patch(
        f"/api/v1/generated-documents/{document.id}",
        json={
            "expected_updated_at": refreshed.updated_at.isoformat(),
            "edited_output_text": "",
            "sections": [
                {"section_key": "problem", "section_label": "Problem", "section_order": 0, "text": "Improving"},
                {"section_key": "tasks", "section_label": "Tasks", "section_order": 1, "text": "Should stay blocked"},
            ],
        },
    )

    assert_error(invalid, status_code=422, code="business_rule_violation", message="Structured note section is invalid")


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
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"note_generation_length": "short", "llm_detail_level": "concise"}))

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
    captured_provider_request = {}

    def fake_generate_quick_action(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return "SMS body", {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10, "duration_ms": 8}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_quick_action)

    from app.services.templates import delete_personal_quick_action as delete_personal_quick_action_service

    delete_personal_quick_action_service(db_session, owner, quick_action_id=quick_action.id)
    refreshed = db_session.get(GeneratedDocument, document.id)
    assert refreshed is not None
    assert refreshed.quick_action_version_id is None
    assert refreshed.prompt_snapshot_text == "Write a short SMS update."

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "SMS body"
    quick_action_payload = generated_document_llm_request_payload(db_session, document=processed)
    assert quick_action_payload == captured_provider_request
    assert quick_action_payload["max_completion_tokens"] == 1600
    assert quick_action_payload["messages"] == captured_provider_request["messages"]
    assert "Output detail:" not in quick_action_payload["messages"][0]["content"]
    assert "generation" not in quick_action_payload
    assert "input" not in quick_action_payload
    assert "provider" not in quick_action_payload
    assert "request" not in quick_action_payload


def test_upsert_personal_template_translates_raced_integrity_error_to_conflict(
    db_session,
    monkeypatch,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Template Race")
    owner = make_user(email="template-race-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)

    monkeypatch.setattr("app.services.templates._ensure_unique_template_name", lambda *args, **kwargs: None)

    class FakeDiag:
        constraint_name = "uq_template_version_number"

    class FakeOrig(Exception):
        diag = FakeDiag()

    original_commit = db_session.commit

    def fake_commit():
        db_session.commit = original_commit
        raise IntegrityError("INSERT", {}, FakeOrig())

    db_session.commit = fake_commit

    with pytest.raises(AppError) as exc_info:
        upsert_personal_template(
            db_session,
            owner,
            PromptTemplateUpsert(
                scope=TemplateScope.user,
                template_id=None,
                name="Clinic Letter",
                description=None,
                prompt_text="Write note.",
                mode=TemplateMode.freeform,
                config_json=None,
                is_active=True,
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.message == "Template changed during save. Retry."


def test_upsert_personal_quick_action_translates_raced_integrity_error_to_conflict(
    db_session,
    monkeypatch,
    make_team,
    make_user,
):
    team = make_team(name="Clinic Quick Action Race")
    owner = make_user(email="quick-race-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)

    monkeypatch.setattr("app.services.templates._ensure_unique_quick_action_name", lambda *args, **kwargs: None)

    class FakeDiag:
        constraint_name = "uq_quick_action_version_number"

    class FakeOrig(Exception):
        diag = FakeDiag()

    original_commit = db_session.commit

    def fake_commit():
        db_session.commit = original_commit
        raise IntegrityError("INSERT", {}, FakeOrig())

    db_session.commit = fake_commit

    with pytest.raises(AppError) as exc_info:
        upsert_personal_quick_action(
            db_session,
            owner,
            QuickActionUpsert(
                scope=TemplateScope.user,
                quick_action_id=None,
                name="Patient SMS",
                description=None,
                prompt_text="Write quick action.",
                is_active=True,
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.message == "Quick action changed during save. Retry."


def test_generated_document_keeps_prompt_snapshot_with_quick_action_context(
    db_session,
    monkeypatch,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_quick_action,
):
    team = make_team(name="Clinic Quick Context")
    admin = make_user(email="admin-quick-context@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-quick-context@example.com", password="password-2", team=team, team_role=TeamRole.user)
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

    document = queue_quick_action_generation(
        db_session,
        owner,
        transcript_id=transcript.id,
        quick_action_id=quick_action.id,
        context_text="Mention John Smith's agreed follow-up call.",
    )
    assert document.prompt_snapshot_text == "Write a short SMS update.\n\nAdditional context:\nMention John Smith's agreed follow-up call."
    monkeypatch.setattr(
        "app.services.templates.redact_transient_text",
        lambda db, text, *, team_id, start_index: {
            "redacted_text": f"Mention [PHI-{start_index}]'s agreed follow-up call.",
            "phi_mapping": {"phi-1": {"type": "PERSON", "value": "John Smith"}},
            "phi_index": [{"index": start_index, "type": "PERSON", "value": "John Smith", "placeholder": f"[PHI-{start_index}]"}],
            "phi_count": 1,
            "api_provider": "native_presidio",
            "api_model_or_version": "en_core_web_sm",
        },
    )

    captured_provider_request = {}

    def fake_generate_quick_action_with_context(**kwargs):
        captured_provider_request.update(kwargs["request_body"])
        return "SMS body", {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10, "duration_ms": 8}

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_generate_quick_action_with_context)

    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status is GeneratedDocumentStatus.ready
    assert is_encrypted_envelope(processed.edited_output_text_encrypted)
    assert decrypt_generated_document_field(db_session, processed, "edited_output_text_encrypted") == "SMS body"
    quick_action_payload = generated_document_llm_request_payload(db_session, document=processed)
    assert quick_action_payload == captured_provider_request
    assert quick_action_payload["messages"] == captured_provider_request["messages"]
    assert "Write a short SMS update." in json.dumps(quick_action_payload)
    assert "Write a [PHI-" not in json.dumps(quick_action_payload)
    assert "Mention [PHI-1]'s agreed follow-up call." in json.dumps(quick_action_payload)
    assert "Mention John Smith's agreed follow-up call." not in json.dumps(quick_action_payload)
    assert "generation" not in quick_action_payload
    assert "input" not in quick_action_payload
    assert "provider" not in quick_action_payload
    assert "request" not in quick_action_payload


def test_llm_config_cannot_be_changed_while_generated_documents_are_in_flight(client, db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch):
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

    writes = []
    monkeypatch.setattr(
        "app.services.llm._list_openai_compatible_chat_models",
        lambda *, provider_preset, api_key, base_url: ["gpt-4o-mini"],
    )
    monkeypatch.setattr(
        "app.services.llm.write_team_llm_bearer_token",
        lambda *, team_id, config_id, bearer_token: writes.append(bearer_token)
        or f"secret:openscribe/llm/team/{team_id}/config/{config_id}/replacement",
    )
    corrected = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Incidental form edit ignored while in flight",
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "auth_mode": config.auth_mode.value,
            "bearer_token": "correct-key",
            "credential_action": "replace",
            "model_name": "gpt-4o-mini",
            "is_active": False,
        },
    )
    assert corrected.status_code == 200
    assert "correct-key" not in str(corrected.json())
    assert writes == ["correct-key"]
    db_session.refresh(config)
    assert config.label == "Provisioned LLM"
    assert config.is_active is True
    assert config.model_name == "gpt-4o-mini"

    writes.clear()
    replaced = client.post(
        f"/api/v1/llm-configs/{config.id}/replace-credential",
        json={"team_id": str(team.id), "config_id": str(config.id), "bearer_token": "correct-key-2"},
    )
    assert replaced.status_code == 200
    assert replaced.json()["config"]["setup_status"] == "ready"
    assert replaced.json()["config"]["is_active"] is True
    assert replaced.json()["config"]["model_name"] == "gpt-4o-mini"
    assert writes == ["correct-key-2"]

    writes.clear()
    db_session.refresh(config)
    secret_ref_before_rejection = config.vault_secret_ref
    monkeypatch.setattr(
        "app.services.llm._list_openai_compatible_chat_models",
        lambda *, provider_preset, api_key, base_url: ["other-model"],
    )
    incompatible = client.post(
        f"/api/v1/llm-configs/{config.id}/replace-credential",
        json={"team_id": str(team.id), "config_id": str(config.id), "bearer_token": "wrong-provider-key"},
    )
    assert_error(
        incompatible,
        status_code=409,
        code="conflict",
        message="Replacement credential does not expose the model used by queued or processing generated documents",
    )
    assert writes == []
    db_session.refresh(config)
    assert config.vault_secret_ref == secret_ref_before_rejection
    assert config.model_name == "gpt-4o-mini"
    assert config.setup_status == LlmConfigSetupStatus.ready

    config.setup_status = LlmConfigSetupStatus.pending_model_selection
    config.is_active = False
    db_session.add(config)
    db_session.commit()
    finalized = client.post(
        f"/api/v1/llm-configs/{config.id}/finalize",
        json={
            "team_id": str(team.id),
            "config_id": str(config.id),
            "label": "Provisioned LLM",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    assert finalized.status_code == 200
    assert finalized.json()["setup_status"] == "ready"
    assert finalized.json()["is_active"] is True

    config.is_active = False
    db_session.add(config)
    db_session.commit()
    activated = client.post(
        "/api/v1/llm-configs",
        json={
            "config_id": str(config.id),
            "team_id": str(team.id),
            "label": "Provisioned LLM",
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "auth_mode": config.auth_mode.value,
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    assert activated.status_code == 200
    assert activated.json()["setup_status"] == "ready"
    assert activated.json()["is_active"] is True


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
        next_id = 0

        def __init__(self):
            type(self).next_id += 1
            self.id = f"generated-task-rate-{type(self).next_id}"

    monkeypatch.setattr("app.main.enqueue_generated_document_job", lambda **kwargs: FakeTaskResult())

    login(client, email="owner-rate@example.com", password="password-2")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Rate limited visit", "ingestion_mode": "whole_file", "current_draft_text_encrypted": "Transcript draft."},
    )
    transcript_id = started.json()["id"]

    responses = [
        client.post(f"/api/v1/transcripts/{transcript_id}/generate-output", json={"template_id": str(template.id)})
        for _ in range(21)
    ]

    assert [response.status_code for response in responses[:20]] == [202] * 20
    assert_error(responses[20], status_code=429, code="rate_limited", message="Too many requests")


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
        lambda **kwargs: ('{"title":"Usage note","content":"Generated note body"}', {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168, "duration_ms": 10}),
    )

    caplog.set_level("INFO", logger="openscribe.usage")
    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.ready
    assert processed.title == "Usage note"
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

    class FakeOllamaErrorStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            response = httpx.Response(
                404,
                request=request,
                headers={"content-type": "application/json"},
                content=b'{"error":"model \\"missing-model\\" not found"}',
            )
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        def iter_lines(self):
            return iter(())

    monkeypatch.setattr("app.services.templates.httpx.stream", lambda *args, **kwargs: FakeOllamaErrorStream())

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

    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    mfa_login = login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
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


def test_leader_can_delete_own_team_user_and_owned_transcripts(client, db_session, make_team, make_user, monkeypatch):
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
    ingestion_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_vault_ref="secret:openscribe/transcript-ingestion/user-delete/source-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
    )
    db_session.add(ingestion_job)
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

    deleted_secret_refs: list[str] = []
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted_secret_refs.append(secret_ref),
    )

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
    assert deleted_secret_refs == ["secret:openscribe/transcript-ingestion/user-delete/source-audio"]


def test_system_admin_delete_reassigns_metadata_foreign_keys(
    client,
    db_session,
    make_team,
    make_user,
    make_default_template,
    make_default_quick_action,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    make_stt_config,
    make_stt_selection,
    make_llm_config,
    make_llm_selection,
):
    actor = make_user(email="admin-delete-metadata-actor@example.com", password="password-1", is_system_admin=True)
    target = make_user(email="admin-delete-metadata-target@example.com", password="password-2", is_system_admin=True)
    team = make_team(name="Metadata Delete Clinic")
    leader = make_user(email="leader-delete-metadata@example.com", password="password-3", team=team, team_role=TeamRole.leader)
    default_template = make_default_template(actor=target, name="Metadata default template")
    default_quick_action = make_default_quick_action(actor=target, name="Metadata default action")
    deidentification_provider = make_deidentification_provider(
        actor=target,
        label="Metadata REST Deid",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid.example.com",
        detect_path="/detect",
    )
    assignment = make_deidentification_provider_assignment(team=team, provider=deidentification_provider, actor=target)
    selection = make_deidentification_selection(team=team, provider=deidentification_provider, actor=target)
    stt_config = make_stt_config(team=team, actor=target, label="Metadata STT")
    stt_selection = make_stt_selection(config=stt_config, actor=target)
    llm_config = make_llm_config(team=team, actor=target, label="Metadata LLM")
    llm_selection = make_llm_selection(config=llm_config, actor=target)
    team_template = PromptTemplate(
        scope=TemplateScope.team,
        owner_user_id=None,
        team_id=team.id,
        name="Metadata team template",
        created_by_user_id=leader.id,
    )
    db_session.add(team_template)
    db_session.flush()
    team_template_version = PromptTemplateVersion(
        template_id=team_template.id,
        version_no=1,
        mode=TemplateMode.freeform,
        prompt_text="Team prompt",
        created_by_user_id=leader.id,
    )
    team_quick_action = QuickAction(
        scope=TemplateScope.team,
        owner_user_id=None,
        team_id=team.id,
        name="Metadata team action",
        created_by_user_id=leader.id,
    )
    db_session.add(team_quick_action)
    db_session.flush()
    team_quick_action_version = QuickActionVersion(
        quick_action_id=team_quick_action.id,
        version_no=1,
        mode=TemplateMode.freeform,
        prompt_text="Team action",
        created_by_user_id=leader.id,
    )
    db_session.add_all([team_template_version, team_quick_action_version])
    db_session.commit()

    login(client, email="admin-delete-metadata-actor@example.com", password="password-1")
    deleted_target = client.delete(f"/api/v1/users/{target.id}")
    deleted_leader = client.delete(f"/api/v1/users/{leader.id}")

    assert deleted_target.status_code == 204
    assert deleted_leader.status_code == 204
    assert db_session.get(User, target.id) is None
    assert db_session.get(User, leader.id) is None

    assert db_session.get(DefaultPromptTemplate, default_template.id).created_by_user_id == actor.id
    default_template_version = db_session.scalar(select(DefaultPromptTemplateVersion).where(DefaultPromptTemplateVersion.default_template_id == default_template.id))
    assert default_template_version.created_by_user_id == actor.id
    assert db_session.get(DefaultQuickAction, default_quick_action.id).created_by_user_id == actor.id
    default_quick_action_version = db_session.scalar(select(DefaultQuickActionVersion).where(DefaultQuickActionVersion.default_quick_action_id == default_quick_action.id))
    assert default_quick_action_version.created_by_user_id == actor.id
    refreshed_deidentification_provider = db_session.get(DeidentificationProvider, deidentification_provider.id)
    assert refreshed_deidentification_provider.created_by_user_id == actor.id
    assert refreshed_deidentification_provider.updated_by_user_id == actor.id
    assert db_session.get(TeamDeidentificationProviderAssignment, assignment.id).assigned_by_user_id == actor.id
    assert db_session.get(TeamDeidentificationSelection, selection.id).selected_by_user_id == actor.id
    assert db_session.get(TeamSttConfig, stt_config.id).created_by_user_id == actor.id
    assert db_session.get(TeamSttConfig, stt_config.id).updated_by_user_id == actor.id
    assert db_session.get(TeamSttSelection, stt_selection.id).selected_by_user_id == actor.id
    assert db_session.get(TeamLlmConfig, llm_config.id).created_by_user_id == actor.id
    assert db_session.get(TeamLlmConfig, llm_config.id).updated_by_user_id == actor.id
    assert db_session.get(TeamLlmSelection, llm_selection.id).selected_by_user_id == actor.id
    assert db_session.get(PromptTemplate, team_template.id).created_by_user_id == actor.id
    assert db_session.get(PromptTemplateVersion, team_template_version.id).created_by_user_id == actor.id
    assert db_session.get(QuickAction, team_quick_action.id).created_by_user_id == actor.id
    assert db_session.get(QuickActionVersion, team_quick_action_version.id).created_by_user_id == actor.id


def test_leader_can_delete_user_even_when_retry_audio_vault_cleanup_fails(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinic North")
    leader = make_user(email="leader-delete-best-effort@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    member = make_user(email="member-delete-best-effort@example.com", password="password-2", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=member.id,
        team_id=team.id,
        title="Visit note",
        current_draft_text_encrypted="draft",
        status="failed",
        retention_days_applied=14,
        retention_expires_at=team.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="recording.mp3",
            source_audio_vault_ref="secret:openscribe/transcript-ingestion/user-best-effort/source-audio",
            source_audio_size_bytes=len(b"raw-file-audio"),
            status=TranscriptIngestionJobStatus.failed,
            error_code="stt_request_failed",
            error_message="STT provider request failed",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    login(client, email="leader-delete-best-effort@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/users/{member.id}")

    assert deleted.status_code == 204
    assert db_session.get(User, member.id) is None
    assert db_session.get(Transcript, transcript.id) is None


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

    weak_password_change = client.post("/api/v1/onboarding/password", json={"new_password": "ShortPass12"})
    assert_error(
        weak_password_change,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
    )
    assert weak_password_change.json()["error"]["details"] == {"issue_count": 1}

    password_change = client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    code = pyotp.TOTP(start.json()["secret"]).now()
    client.post("/api/v1/onboarding/totp/verify", json={"code": code})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    second_login = login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)

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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")

    second_login = login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
    assert second_login.json()["auth_level"] == "pending_mfa"
    challenge = complete_mfa_challenge(client, secret, remember_device=True)
    assert challenge.status_code == 200

    stored_devices = list(db_session.scalars(select(UserTrustedDevice)))
    assert len(stored_devices) == 1
    assert stored_devices[0].device_token_hash

    client.post("/api/v1/auth/logout")
    third_login = login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
    complete_mfa_challenge(client, secret, remember_device=True)
    client.post("/api/v1/auth/logout")

    device = db_session.scalar(select(UserTrustedDevice))
    assert device is not None
    device.last_mfa_verified_at = device.last_mfa_verified_at - timedelta(days=2)
    db_session.add(device)
    db_session.commit()

    relogin = login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
    start = client.post("/api/v1/onboarding/totp/start")
    secret = start.json()["secret"]
    client.post("/api/v1/onboarding/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    client.post("/api/v1/onboarding/skip-recovery-codes")
    client.post("/api/v1/auth/logout")
    login(client, email="managed@example.com", password=PERMANENT_TEST_PASSWORD)
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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
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
    client.post("/api/v1/onboarding/password", json={"new_password": PERMANENT_TEST_PASSWORD})
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


def test_transcript_routes_require_full_auth_and_preserve_owner_only_access(client, db_session, make_team, make_user, monkeypatch):
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

    from app.services.redaction import DeidentificationDetectionResult, Span

    detected_texts: list[str] = []

    def fake_detect_phi(db, *, provider, text, language, score_threshold, entities):
        detected_texts.append(text)
        return DeidentificationDetectionResult(
            spans=[Span(start=0, end=5, entity_type="PERSON", score=0.99)] if text.startswith("final") else [],
            api_provider=provider.label,
            api_model_or_version="stub-model",
        )

    monkeypatch.setattr("app.services.redaction._detect_phi", fake_detect_phi)

    commit_one = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v1"})
    commit_two = client.post(f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": "final-text-v2"})

    assert commit_one.status_code == 200
    assert commit_two.status_code == 200

    versions = db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id)))
    version_rows = list(versions)
    assert [row.version_no for row in version_rows] == [1, 2]
    assert is_encrypted_envelope(version_rows[-1].text_encrypted)
    assert (
        decrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_rows[-1].id,
            stored_value=version_rows[-1].text_encrypted,
        )
        == "final-text-v2"
    )
    redaction_runs = list(db_session.scalars(select(RedactionRun).where(RedactionRun.transcript_id == UUID(transcript_id)).order_by(RedactionRun.created_at)))
    assert [run.status.value for run in redaction_runs] == ["succeeded", "succeeded"]
    assert [run.entity_count for run in redaction_runs] == [1, 1]
    assert detected_texts == ["final-text-v1", "final-text-v2"]
    assert (
        decrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="redaction_runs",
            field="redacted_text_encrypted",
            record_id=redaction_runs[-1].id,
            stored_value=redaction_runs[-1].redacted_text_encrypted,
        )
        == "[PHI-1]-text-v2"
    )

    owner_list = client.get("/api/v1/transcripts")

    assert owner_list.status_code == 200
    owner_rows = owner_list.json()["items"]
    assert [row["id"] for row in owner_rows] == [legacy_response.json()["id"], transcript_id]
    assert [row["ingestion_mode"] for row in owner_rows] == ["whole_file", "live_chunked"]

    client.post("/api/v1/auth/logout")
    login(client, email="admin@example.com", password="password-3")
    forbidden_admin = client.post("/api/v1/transcripts/start", json={"title": "Admin note", "ingestion_mode": "whole_file"})
    assert_error(forbidden_admin, status_code=403, code="forbidden", message="System-admin accounts cannot own transcript content")


def test_start_transcript_encrypts_draft_at_rest_and_provisions_owner_key(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-encrypted-start@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="owner-encrypted-start@example.com", password="password-1")
    started = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Encrypted visit",
            "ingestion_mode": "whole_file",
            "current_draft_text_encrypted": "Sensitive transcript draft",
        },
    )

    assert started.status_code == 201
    assert started.json()["current_draft_text"] == "Sensitive transcript draft"
    transcript = db_session.get(Transcript, UUID(started.json()["id"]))
    assert transcript is not None
    assert is_encrypted_envelope(transcript.current_draft_text_encrypted)
    assert decrypt_transcript_draft(db_session, transcript) == "Sensitive transcript draft"
    key_row = db_session.query(UserEncryptionKey).filter(UserEncryptionKey.user_id == owner.id).one_or_none()
    assert key_row is not None
    assert key_row.wrapped_dek.startswith("vault:v1:")
    assert key_row.is_active is True


def test_transcript_detail_route_decrypts_structured_context_before_validation(client, db_session, make_team, make_user):
    team = make_team(name="Structured Detail Team")
    owner = make_user(email="owner-structured-detail@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="owner-structured-detail@example.com", password="password-1")
    started = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Structured visit",
            "ingestion_mode": "whole_file",
            "structured_context_json": {
                "profile": "emis",
                "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]},
            },
        },
    )

    assert started.status_code == 201
    transcript = db_session.get(Transcript, UUID(started.json()["id"]))
    assert transcript is not None
    assert is_encrypted_envelope(transcript.structured_context_json)

    detail = client.get(f"/api/v1/transcripts/{transcript.id}")
    assert detail.status_code == 200
    assert detail.json()["structured_context_json"] == {
        "profile": "emis",
        "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]},
    }


def test_unwrap_user_content_data_key_does_not_attempt_transit_bootstrap(monkeypatch):
    class FakeTransit:
        @staticmethod
        def decrypt_data(*, name, ciphertext, mount_point):
            assert name == "openscribe-user-content-kek"
            assert ciphertext == "vault:v1:wrapped-1"
            assert mount_point == "transit"
            return {"data": {"plaintext": "QUJDRA=="}}

    class FakeSecrets:
        transit = FakeTransit()

    class FakeClient:
        secrets = FakeSecrets()

    def fail_bootstrap():
        raise AssertionError("bootstrap should not be called for unwrap")

    monkeypatch.setattr("app.services.vault.ensure_user_content_transit_ready", fail_bootstrap)
    monkeypatch.setattr("app.services.vault.vault_client", lambda: FakeClient())

    assert unwrap_user_content_data_key(wrapped_dek="vault:v1:wrapped-1") == b"ABCD"


def test_unwrap_user_content_data_key_uses_explicit_kek_metadata(monkeypatch):
    class FakeTransit:
        @staticmethod
        def decrypt_data(*, name, ciphertext, mount_point):
            assert name == "legacy-kek"
            assert ciphertext == "vault:v1:wrapped-legacy"
            assert mount_point == "legacy-transit"
            return {"data": {"plaintext": "QUJDRA=="}}

    class FakeSecrets:
        transit = FakeTransit()

    class FakeClient:
        secrets = FakeSecrets()

    monkeypatch.setattr("app.services.vault.vault_client", lambda: FakeClient())

    assert (
        unwrap_user_content_data_key(
            wrapped_dek="vault:v1:wrapped-legacy",
            mount_point="legacy-transit",
            key_name="legacy-kek",
        )
        == b"ABCD"
    )


def test_generate_user_content_data_key_does_not_attempt_transit_bootstrap(monkeypatch):
    class FakeTransit:
        @staticmethod
        def generate_data_key(*, name, key_type, mount_point):
            assert name == "openscribe-user-content-kek"
            assert key_type == "plaintext"
            assert mount_point == "transit"
            return {"data": {"plaintext": "QUJDRA==", "ciphertext": "vault:v1:wrapped-1", "key_version": 1}}

    class FakeSecrets:
        transit = FakeTransit()

    class FakeClient:
        secrets = FakeSecrets()

    def fail_bootstrap():
        raise AssertionError("bootstrap should not be called for generate")

    monkeypatch.setattr("app.services.vault.ensure_user_content_transit_ready", fail_bootstrap)
    monkeypatch.setattr("app.services.vault.vault_client", lambda: FakeClient())

    plaintext, wrapped_dek, key_version = generate_user_content_data_key()
    assert plaintext == b"ABCD"
    assert wrapped_dek == "vault:v1:wrapped-1"
    assert key_version == 1


def test_resolve_vault_token_prefers_local_token_file_when_env_token_is_dev_default(monkeypatch, tmp_path):
    token_file = tmp_path / "root-token"
    token_file.write_text("local-token\n", encoding="utf-8")

    monkeypatch.setattr(vault_service, "VAULT_ADDR", "http://127.0.0.1:8200")
    monkeypatch.setattr(vault_service, "VAULT_TOKEN", "root")
    monkeypatch.setattr(vault_service, "VAULT_TOKEN_FILE", None)
    monkeypatch.setattr(vault_service, "DEFAULT_LOCAL_VAULT_TOKEN_FILE", token_file)

    assert vault_service._resolve_vault_token() == "local-token"


def test_ensure_vault_kv_ready_enables_v2_mount(monkeypatch):
    calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeSys:
        @staticmethod
        def list_mounted_secrets_engines():
            return {"data": {}}

        @staticmethod
        def enable_secrets_engine(*, backend_type, path, options):
            calls.append((backend_type, path, options))

    class FakeClient:
        sys = FakeSys()

    monkeypatch.setattr("app.services.vault.vault_client", lambda: FakeClient())
    monkeypatch.setattr("app.services.vault.VAULT_KV_MOUNT", "secret")

    vault_service.ensure_vault_kv_ready()

    assert calls == [("kv", "secret", {"version": "2"})]


def test_ensure_vault_kv_ready_rejects_non_v2_mount(monkeypatch):
    class FakeSys:
        @staticmethod
        def list_mounted_secrets_engines():
            return {"data": {"secret/": {"options": {"version": "1"}}}}

    class FakeClient:
        sys = FakeSys()

    monkeypatch.setattr("app.services.vault.vault_client", lambda: FakeClient())
    monkeypatch.setattr("app.services.vault.VAULT_KV_MOUNT", "secret")

    with pytest.raises(AppError) as exc_info:
        vault_service.ensure_vault_kv_ready()

    assert exc_info.value.code == "vault_bootstrap_failed"
    assert exc_info.value.message == "Vault KV mount must use version 2"


def test_dev_seed_repair_resets_transcript_content_when_active_key_cannot_be_unwrapped(
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    team = make_team(name="Dev Seed Repair Team")
    user = make_user(email="dev.user@example.com", password="password-1", team=team, team_role=TeamRole.user)
    key_row = ensure_user_dek(db_session, user=user)
    transcript = Transcript(
        owner_user_id=user.id,
        team_id=team.id,
        title="Broken dev transcript",
        current_draft_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=user.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=uuid4(),
            plaintext="Sensitive transcript draft",
        ),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=user.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    old_wrapped_dek = key_row.wrapped_dek

    def fail_unwrap(*, wrapped_dek: str, mount_point: str | None = None, key_name: str | None = None) -> bytes:
        if wrapped_dek == old_wrapped_dek:
            raise AppError(502, "vault_read_failed", "Vault data key unwrap failed")
        return b"healthy-key"

    monkeypatch.setattr("scripts.seed_dev_accounts.unwrap_user_content_data_key", fail_unwrap)

    repair_dev_user_content_key_if_needed(db_session, user=user)

    assert db_session.get(Transcript, transcript.id) is None
    repaired_key = db_session.query(UserEncryptionKey).filter(UserEncryptionKey.user_id == user.id, UserEncryptionKey.is_active.is_(True)).one()
    assert repaired_key.wrapped_dek != old_wrapped_dek


def test_dev_seed_system_admin_is_teamless_full_auth_and_content_key_free(
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Dev Admin Seed Team")
    stale_user = make_user(email="dev.admin@example.com", password="password-1", team=team, team_role=TeamRole.user)
    stale_key = ensure_user_dek(db_session, user=stale_user)
    session = UserSession(
        user_id=stale_user.id,
        session_token_hash="stale-session",
        auth_level=SessionAuthLevel.full,
        status=SessionStatus.active,
        expires_at=utcnow() + timedelta(hours=1),
    )
    trusted_device = UserTrustedDevice(
        user_id=stale_user.id,
        device_token_hash="stale-device",
        label="old crawl device",
        expires_at=utcnow() + timedelta(hours=1),
        last_mfa_verified_at=utcnow(),
    )
    db_session.add_all([session, trusted_device])
    db_session.commit()

    admin = ensure_dev_system_admin(
        db_session,
        full_name="Dev Test Admin",
        email="dev.admin@example.com",
        password="test1234",
    )

    assert admin.id == stale_user.id
    assert admin.email == "dev.admin@example.com"
    assert admin.team_id is None
    assert admin.team_role is None
    assert admin.is_system_admin is True
    assert admin.status is UserStatus.active
    assert admin.must_change_password is False
    assert admin.onboarding_state is UserOnboardingState.complete
    assert admin.mfa_required is False
    assert admin.mfa_enabled is False
    assert db_session.get(UserSession, session.id) is None
    assert db_session.get(UserTrustedDevice, trusted_device.id) is None
    assert db_session.get(UserEncryptionKey, stale_key.id) is None


def test_reset_unreadable_owner_content_dry_run_and_apply(
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    team = make_team(name="Unreadable Owner Reset Team")
    broken_user = make_user(email="broken-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)
    healthy_user = make_user(email="healthy-owner@example.com", password="password-1", team=team, team_role=TeamRole.user)

    broken_key = ensure_user_dek(db_session, user=broken_user)
    ensure_user_dek(db_session, user=healthy_user)

    broken_transcript = Transcript(
        owner_user_id=broken_user.id,
        team_id=team.id,
        title="Broken owner transcript",
        current_draft_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=broken_user.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=uuid4(),
            plaintext="Sensitive transcript draft",
        ),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=broken_user.created_at,
    )
    healthy_transcript = Transcript(
        owner_user_id=healthy_user.id,
        team_id=team.id,
        title="Healthy owner transcript",
        current_draft_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=healthy_user.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=uuid4(),
            plaintext="Safe transcript draft",
        ),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=healthy_user.created_at,
    )
    db_session.add_all([broken_transcript, healthy_transcript])
    db_session.commit()
    old_wrapped_dek = broken_key.wrapped_dek

    def fail_unwrap(*, wrapped_dek: str, mount_point: str | None = None, key_name: str | None = None) -> bytes:
        if wrapped_dek == old_wrapped_dek:
            raise AppError(502, "vault_read_failed", "Vault data key unwrap failed")
        return b"healthy-key"

    monkeypatch.setattr("scripts.reset_unreadable_owner_content.unwrap_user_content_data_key", fail_unwrap)

    dry_run = reset_unreadable_owner_content(db_session, apply=False)
    assert dry_run == ["broken-owner@example.com"]
    assert db_session.get(Transcript, broken_transcript.id) is not None

    applied = reset_unreadable_owner_content(db_session, apply=True)
    assert applied == ["broken-owner@example.com"]
    assert db_session.get(Transcript, broken_transcript.id) is None
    assert db_session.get(Transcript, healthy_transcript.id) is not None

    repaired_key = (
        db_session.query(UserEncryptionKey)
        .filter(UserEncryptionKey.user_id == broken_user.id, UserEncryptionKey.is_active.is_(True))
        .one()
    )
    assert repaired_key.wrapped_dek != old_wrapped_dek


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
    assert detail.json()["current_draft_text"] is None

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.get(f"/api/v1/transcripts/{transcript_id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_transcript_detail_includes_latest_ingestion_failure(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-detail-error@example.com", password="password-1", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_vault_ref="secret:openscribe/transcript-ingestion/detail/source-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_config_secret_missing",
        error_message="The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
    )
    db_session.add(job)
    db_session.commit()

    login(client, email="owner-detail-error@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert detail.status_code == 200
    assert detail.json()["latest_ingestion_job_status"] == "failed"
    assert detail.json()["latest_ingestion_error_code"] == "stt_config_secret_missing"
    assert detail.json()["latest_ingestion_error_message"] == "The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth."
    assert detail.json()["latest_ingestion_retry_available"] is True


def test_transcript_detail_includes_latest_successful_ingestion_completed_at(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-detail-success-at@example.com", password="password-1", team=team, team_role=TeamRole.user)
    completed_at = utcnow() - timedelta(minutes=1)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    older_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="older.mp3",
        status=TranscriptIngestionJobStatus.applied,
        completed_at=completed_at - timedelta(minutes=5),
    )
    latest_successful_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="latest.mp3",
        status=TranscriptIngestionJobStatus.applied,
        completed_at=completed_at,
    )
    db_session.add_all([older_job, latest_successful_job])
    db_session.commit()

    login(client, email="owner-detail-success-at@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert detail.status_code == 200
    assert detail.json()["latest_successful_ingestion_completed_at"] == completed_at.isoformat().replace("+00:00", "Z")


def test_transcript_detail_hides_retry_when_failed_upload_blob_is_missing(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-detail-no-retry@example.com", password="password-1", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    job = make_ingestion_job_for_transcript(
        transcript,
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

    login(client, email="owner-detail-no-retry@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert detail.status_code == 200
    assert detail.json()["latest_ingestion_retry_available"] is False


def test_transcript_detail_includes_next_live_chunk_sequence_number(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-live-sequence@example.com", password="password-1", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live visit",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add_all(
        [
            make_ingestion_job_for_transcript(
                transcript,
                job_kind=TranscriptIngestionJobKind.live_chunk,
                chunk_sequence_no=1,
                source_filename="chunk-1.webm",
                status=TranscriptIngestionJobStatus.applied,
            ),
            make_ingestion_job_for_transcript(
                transcript,
                job_kind=TranscriptIngestionJobKind.live_chunk,
                chunk_sequence_no=2,
                source_filename="chunk-2.webm",
                status=TranscriptIngestionJobStatus.queued,
            ),
        ]
    )
    db_session.commit()

    login(client, email="owner-live-sequence@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert detail.status_code == 200
    assert detail.json()["next_live_chunk_sequence_no_upload"] == 3


def test_transcribe_workspace_endpoint_returns_owner_workspace_state(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_quick_action,
):
    team = make_team(name="Workspace Team")
    owner = make_user(email="owner-workspace@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-workspace@example.com", password="password-2", team=team, team_role=TeamRole.user)
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="My note", prompt_text="Write a note.")
    quick_action = make_quick_action(scope=TemplateScope.user, owner=owner, actor=owner, name="Send SMS", prompt_text="Draft an SMS.")
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Existing session",
        current_draft_text_encrypted="Patient is improving.",
        structured_context_json={"profile": "emis", "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]}},
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
        text_encrypted="Patient is improving.",
    )
    db_session.add(transcript_version)
    db_session.commit()
    generated = GeneratedDocument(
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template.versions[-1].id,
        source_template_name=template.name,
        status=GeneratedDocumentStatus.ready,
        title="Visit summary",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="Body text",
        edited_output_text_encrypted="Body text",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(generated)
    db_session.commit()

    login(client, email="owner-workspace@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_transcript"]["id"] == str(transcript.id)
    assert payload["active_transcript"]["current_draft_text"] == "Patient is improving."
    assert payload["active_structured_context"] == {
        "problem": ["Known asthma"],
        "tasks": ["Peak flow diary"],
    }
    assert [item["id"] for item in payload["recent_transcripts"]] == [str(transcript.id)]
    assert payload["recent_transcripts"][0]["has_transcript_content"] is True
    assert payload["active_transcript"]["has_transcript_content"] is True
    assert [item["name"] for item in payload["available_templates"]] == ["My note"]
    assert [item["name"] for item in payload["available_quick_actions"]] == ["Send SMS"]
    assert [item["id"] for item in payload["generated_documents"]] == [str(generated.id)]
    assert payload["stt_selected"] is False
    assert payload["stt_available"] is False
    assert "No STT configured" in payload["stt_status_message"]
    assert payload["can_create_new_session"] is True
    assert payload["new_session_block_message"] is None

    client.post("/api/v1/auth/logout")
    login(client, email="other-workspace@example.com", password="password-2")
    forbidden = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")
    assert forbidden.status_code == 200
    assert forbidden.json()["active_transcript"] is None


def test_transcript_list_endpoint_pages_owner_consults_by_keyset(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Paged Consult Team")
    owner = make_user(email="paged-consults@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="paged-consults-other@example.com", password="password-2", team=team, team_role=TeamRole.user)
    base_time = utcnow() - timedelta(days=1)
    owner_transcripts = []
    for index in range(15):
        owner_transcripts.append(
            Transcript(
                owner_user_id=owner.id,
                team_id=team.id,
                title=f"Owner consult {index}",
                ingestion_mode=TranscriptIngestionMode.whole_file,
                status=TranscriptStatus.ready,
                retention_days_applied=30,
                retention_expires_at=base_time + timedelta(days=30),
                created_at=base_time + timedelta(minutes=index),
            )
        )
    db_session.add_all(owner_transcripts)
    db_session.add(
        Transcript(
            owner_user_id=other.id,
            team_id=team.id,
            title="Other consult",
            current_draft_text_encrypted="Other user content",
            ingestion_mode=TranscriptIngestionMode.whole_file,
            status=TranscriptStatus.ready,
            retention_days_applied=30,
            retention_expires_at=base_time + timedelta(days=30),
            created_at=base_time + timedelta(minutes=99),
        )
    )
    db_session.commit()

    login(client, email="paged-consults@example.com", password="password-1")
    first_page = client.get("/api/v1/transcripts?limit=12")

    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]
    assert [item["title"] for item in first_payload["items"]] == [f"Owner consult {index}" for index in range(14, 2, -1)]
    assert "current_draft_text" not in first_payload["items"][0]

    second_page = client.get(f"/api/v1/transcripts?limit=12&cursor={first_payload['next_cursor']}")

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert second_payload["has_more"] is False
    assert second_payload["next_cursor"] is None
    assert [item["title"] for item in second_payload["items"]] == ["Owner consult 2", "Owner consult 1", "Owner consult 0"]

    client.post("/api/v1/auth/logout")
    login(client, email="paged-consults-other@example.com", password="password-2")
    other_page = client.get("/api/v1/transcripts")
    assert other_page.status_code == 200
    assert [item["title"] for item in other_page.json()["items"]] == ["Other consult"]


def test_transcript_list_endpoint_rejects_invalid_cursor(client, make_team, make_user):
    team = make_team(name="Bad Cursor Team")
    make_user(email="bad-cursor@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="bad-cursor@example.com", password="password-1")
    response = client.get("/api/v1/transcripts?cursor=not-a-cursor")

    assert_error(response, status_code=400, code="invalid_cursor", message="Transcript history cursor is invalid")


def test_transcribe_workspace_includes_active_old_consult_in_recent_rail(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Old Active Consult Team")
    owner = make_user(email="old-active@example.com", password="password-1", team=team, team_role=TeamRole.user)
    base_time = utcnow() - timedelta(days=1)
    old_transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Older selected consult",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=base_time + timedelta(days=30),
        created_at=base_time,
    )
    recent_transcripts = [
        Transcript(
            owner_user_id=owner.id,
            team_id=team.id,
            title=f"Recent consult {index}",
            ingestion_mode=TranscriptIngestionMode.whole_file,
            status=TranscriptStatus.ready,
            retention_days_applied=30,
            retention_expires_at=base_time + timedelta(days=30),
            created_at=base_time + timedelta(minutes=index + 1),
        )
        for index in range(12)
    ]
    db_session.add_all([old_transcript, *recent_transcripts])
    db_session.commit()

    login(client, email="old-active@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={old_transcript.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_transcript"]["id"] == str(old_transcript.id)
    assert len(payload["recent_transcripts"]) == 13
    assert str(old_transcript.id) in [item["id"] for item in payload["recent_transcripts"]]
    assert payload["recent_transcripts_has_more"] is True
    assert payload["recent_transcripts_next_cursor"]


def test_transcribe_workspace_endpoint_ignores_blank_transcript_versions_for_content_flag(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Workspace Blank Version Team")
    owner = make_user(email="owner-blank-version@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Blank saved session",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        TranscriptVersion(
            transcript_id=transcript.id,
            version_no=1,
            text_encrypted="   ",
        )
    )
    db_session.commit()

    login(client, email="owner-blank-version@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_transcript"]["has_transcript_content"] is False
    assert payload["recent_transcripts"][0]["has_transcript_content"] is False


def test_working_note_only_session_allows_new_session(
    client,
    make_team,
    make_user,
):
    team = make_team(name="Working Note New Session Team")
    owner = make_user(email="owner-working-note-new-session@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="owner-working-note-new-session@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Working note only", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    saved = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "Clinician-only plan exists."},
    )
    assert saved.status_code == 200

    next_session = client.post("/api/v1/transcripts/start", json={"title": "Next session", "ingestion_mode": "whole_file"})
    assert next_session.status_code == 201


def test_transcribe_workspace_endpoint_returns_owner_pii_entities(
    client,
    db_session,
    make_team,
    make_user,
    make_redaction_run,
):
    team = make_team(name="Workspace PII Team")
    owner = make_user(email="owner-workspace-pii@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-workspace-pii@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="PII workspace session",
        current_draft_text_encrypted="John Smith called from 07123 456789.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
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
    run = make_redaction_run(
        transcript=transcript,
        transcript_version=version,
        owner=owner,
        entities=[
            (1, "PERSON", "John Smith"),
            (2, "PHONE_NUMBER", "07123 456789"),
        ],
    )
    db_session.add(
        GeneratedDocument(
            owner_user_id=owner.id,
            team_id=team.id,
            transcript_id=transcript.id,
            transcript_version_id=version.id,
            redaction_run_id=run.id,
            generator_type=GeneratedDocumentGeneratorType.template,
            source_template_name="PII note",
            status=GeneratedDocumentStatus.ready,
            title="PII note",
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted="Note body",
            edited_output_text_encrypted="Note body",
            retention_expires_at=transcript.retention_expires_at,
        )
    )
    db_session.commit()

    login(client, email="owner-workspace-pii@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    payload = response.json()
    expected_transcript_entities = [
        {"id": None, "entity_type": "PERSON", "value": "John Smith", "has_value": True, "placeholder": "[PHI-1]", "occurrence_count": 1, "source": "detected"},
        {"id": None, "entity_type": "PHONE_NUMBER", "value": "07123 456789", "has_value": True, "placeholder": "[PHI-2]", "occurrence_count": 1, "source": "detected"},
    ]
    expected_document_entities = [
        {"entity_type": "PERSON", "has_value": True, "placeholder": "[PHI-1]", "occurrence_count": 1},
        {"entity_type": "PHONE_NUMBER", "has_value": True, "placeholder": "[PHI-2]", "occurrence_count": 1},
    ]
    assert payload["active_transcript_pii_entities"] == expected_transcript_entities
    assert payload["active_transcript_redaction_status"] == {
        "status": "succeeded",
        "entity_count": 2,
        "error_code": None,
    }
    assert payload["active_transcript_clinical_nlp_status"] == {
        "status": "not_run",
        "entity_count": 0,
        "error_code": None,
    }
    assert payload["generated_documents"][0]["pii_entities"] == expected_document_entities

    failed_run = RedactionRun(
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=team.id,
        status=RedactionRunStatus.failed,
        redacted_text_encrypted=None,
        mapping_hash=None,
        entity_count=0,
        api_provider="native_presidio",
        api_model_or_version="en_core_web_sm",
        error_code="redaction_failed",
        created_at=utcnow() + timedelta(seconds=1),
        failed_at=utcnow(),
    )
    db_session.add(failed_run)
    db_session.commit()

    failed_workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")
    assert failed_workspace.status_code == 200
    failed_payload = failed_workspace.json()
    assert failed_payload["active_transcript_pii_entities"] == []
    assert failed_payload["active_transcript_redaction_status"] == {
        "status": "failed",
        "entity_count": 0,
        "error_code": "redaction_failed",
    }

    client.post("/api/v1/auth/logout")
    login(client, email="other-workspace-pii@example.com", password="password-2")
    forbidden = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")
    assert forbidden.status_code == 200
    assert forbidden.json()["active_transcript"] is None
    assert forbidden.json()["active_transcript_pii_entities"] == []


def test_owner_can_add_and_delete_manual_pii_entities(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Manual PII Team")
    owner = make_user(email="owner-manual-pii@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-manual-pii@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Manual PII workspace session",
        current_draft_text_encrypted="Patient mentioned Riverside House.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    login(client, email="owner-manual-pii@example.com", password="password-1")
    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/manual-pii",
        json={"entity_type": "ADDRESS", "value": "Riverside House", "occurrence_count": 2},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["entity_type"] == "ADDRESS"
    assert body["value"] == "Riverside House"
    assert body["placeholder"] == "Manual"
    assert body["occurrence_count"] == 2
    assert body["source"] == "manual"
    entity_id = UUID(body["id"])

    stored = db_session.get(TranscriptManualPiiEntity, entity_id)
    assert stored is not None
    assert stored.owner_user_id == owner.id
    assert stored.team_id == team.id
    assert stored.transcript_id == transcript.id
    assert is_encrypted_envelope(stored.original_value_encrypted)
    assert stored.original_value_encrypted != "Riverside House"
    assert stored.normalized_value_hash != hashlib.sha256("riverside house".encode("utf-8")).hexdigest()
    assert decrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_manual_pii_entities",
        field="original_value_encrypted",
        record_id=stored.id,
        stored_value=stored.original_value_encrypted,
    ) == "Riverside House"

    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")
    assert workspace.status_code == 200
    assert workspace.json()["active_transcript_pii_entities"] == [body]

    duplicate = client.post(
        f"/api/v1/transcripts/{transcript.id}/manual-pii",
        json={"entity_type": "ADDRESS", "value": "  riverside   house  ", "occurrence_count": 1},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == body["id"]
    assert db_session.get(TranscriptManualPiiEntity, entity_id).normalized_value_hash == stored.normalized_value_hash
    assert db_session.query(TranscriptManualPiiEntity).filter(TranscriptManualPiiEntity.transcript_id == transcript.id).count() == 1

    client.post("/api/v1/auth/logout")
    login(client, email="other-manual-pii@example.com", password="password-2")
    forbidden_add = client.post(
        f"/api/v1/transcripts/{transcript.id}/manual-pii",
        json={"entity_type": "ADDRESS", "value": "Other value"},
    )
    assert_error(
        forbidden_add,
        status_code=403,
        code="forbidden",
        message="Transcript access is restricted to the owning user",
    )
    forbidden_delete = client.delete(f"/api/v1/transcripts/{transcript.id}/manual-pii/{entity_id}")
    assert_error(
        forbidden_delete,
        status_code=403,
        code="forbidden",
        message="Transcript access is restricted to the owning user",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="owner-manual-pii@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/transcripts/{transcript.id}/manual-pii/{entity_id}")
    assert deleted.status_code == 204
    assert db_session.get(TranscriptManualPiiEntity, entity_id) is None


def test_manual_pii_duplicate_lookup_accepts_legacy_sha256_hash(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Manual PII Legacy Hash Team")
    owner = make_user(email="owner-manual-pii-legacy@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Manual PII legacy session",
        current_draft_text_encrypted="Patient mentioned Riverside House.",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    entity_id = uuid4()
    legacy_hash = hashlib.sha256("riverside house".encode("utf-8")).hexdigest()
    db_session.add(
        TranscriptManualPiiEntity(
            id=entity_id,
            transcript_id=transcript.id,
            owner_user_id=owner.id,
            team_id=team.id,
            entity_type="ADDRESS",
            original_value_encrypted=encrypt_text_for_owner(
                db_session,
                owner_user_id=owner.id,
                table="transcript_manual_pii_entities",
                field="original_value_encrypted",
                record_id=entity_id,
                plaintext="Riverside House",
            ),
            normalized_value_hash=legacy_hash,
            occurrence_count=1,
        )
    )
    db_session.commit()

    login(client, email="owner-manual-pii-legacy@example.com", password="password-1")
    duplicate = client.post(
        f"/api/v1/transcripts/{transcript.id}/manual-pii",
        json={"entity_type": "ADDRESS", "value": "  Riverside   House  ", "occurrence_count": 3},
    )

    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == str(entity_id)
    assert duplicate.json()["occurrence_count"] == 3
    stored = db_session.get(TranscriptManualPiiEntity, entity_id)
    assert stored is not None
    assert stored.normalized_value_hash != legacy_hash
    assert stored.normalized_value_hash == keyed_digest_for_owner(
        db_session,
        owner_user_id=owner.id,
        purpose="transcript_manual_pii_entities.normalized_value_hash",
        value="riverside house",
    )
    assert db_session.query(TranscriptManualPiiEntity).filter(TranscriptManualPiiEntity.transcript_id == transcript.id).count() == 1


def test_transcript_delete_cascades_manual_pii_entities(db_session, make_team, make_user):
    team = make_team(name="Manual PII Cascade Team")
    owner = make_user(email="owner-manual-pii-cascade@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Manual PII cascade session",
        current_draft_text_encrypted="Draft",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    entity_id = uuid4()
    entity = TranscriptManualPiiEntity(
        id=entity_id,
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=team.id,
        entity_type="PERSON",
        original_value_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_manual_pii_entities",
            field="original_value_encrypted",
            record_id=entity_id,
            plaintext="Cascade Patient",
        ),
        normalized_value_hash="manual-hash",
        occurrence_count=1,
    )
    db_session.add(entity)
    db_session.commit()

    db_session.delete(transcript)
    db_session.commit()

    assert db_session.get(TranscriptManualPiiEntity, entity_id) is None


def test_transcribe_workspace_endpoint_reuses_unwrapped_owner_dek_for_multiple_fields(
    client,
    db_session,
    monkeypatch,
    fake_user_content_transit,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Workspace DEK Cache Team")
    owner = make_user(email="owner-workspace-cache@example.com", password="password-1", team=team, team_role=TeamRole.user)
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner, name="My note", prompt_text="Write a note.")
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Encrypted workspace session",
        current_draft_text_encrypted=None,
        structured_context_json=None,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="Patient is improving.",
    )
    transcript.structured_context_json = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="structured_context_json",
        record_id=transcript.id,
        plaintext={"profile": "emis", "sections": {"problem": ["Known asthma"], "tasks": ["Peak flow diary"]}},
    )
    db_session.add(transcript)
    db_session.flush()
    transcript_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=uuid4(),
            plaintext="Patient is improving.",
        ),
    )
    db_session.add(transcript_version)
    db_session.flush()
    transcript_version.text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=transcript_version.id,
        plaintext="Patient is improving.",
    )
    db_session.add(transcript_version)
    db_session.flush()
    generated = GeneratedDocument(
        id=uuid4(),
        owner_user_id=owner.id,
        team_id=team.id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=template.versions[-1].id,
        source_template_name=template.name,
        status=GeneratedDocumentStatus.ready,
        title="Visit summary",
        document_mode=TemplateMode.structured,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(generated)
    db_session.flush()
    generated.original_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="original_output_text_encrypted",
        record_id=generated.id,
        plaintext="Body text",
    )
    generated.edited_output_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_documents",
        field="edited_output_text_encrypted",
        record_id=generated.id,
        plaintext="Body text",
    )
    db_session.add(generated)
    db_session.flush()
    section = GeneratedDocumentSection(
        id=uuid4(),
        generated_document_id=generated.id,
        section_key="problem",
        section_label="Problem",
        section_order=1,
        original_text_encrypted="",
        edited_text_encrypted="",
        is_edited=False,
    )
    db_session.add(section)
    db_session.flush()
    section.original_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_document_sections",
        field="original_text_encrypted",
        record_id=section.id,
        plaintext="Asthma flare.",
    )
    section.edited_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="generated_document_sections",
        field="edited_text_encrypted",
        record_id=section.id,
        plaintext="Asthma flare.",
    )
    db_session.add(section)
    db_session.commit()

    unwrap_calls = {"count": 0}

    def counting_unwrap(*, wrapped_dek: str, mount_point: str | None = None, key_name: str | None = None) -> bytes:
        unwrap_calls["count"] += 1
        return fake_user_content_transit[wrapped_dek]

    monkeypatch.setattr("app.services.content_crypto.unwrap_user_content_data_key", counting_unwrap)
    db_session.info.clear()

    login(client, email="owner-workspace-cache@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    assert unwrap_calls["count"] == 1


def test_transcribe_workspace_endpoint_uses_row_kek_metadata_for_dek_unwrap(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
    fake_user_content_transit,
):
    team = make_team(name="Owner Metadata Team")
    owner = make_user(email="owner-kek-metadata@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Metadata session",
        retention_days_applied=team.default_retention_days,
        retention_expires_at=utcnow() + timedelta(days=team.default_retention_days),
    )
    db_session.add(transcript)
    db_session.flush()

    key_record = ensure_user_dek(db_session, user=owner)
    key_record.kek_mount = "legacy-transit"
    key_record.kek_key_name = "legacy-kek"
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="Encrypted draft",
    )
    db_session.add(transcript)
    db_session.commit()

    unwrap_calls: list[tuple[str | None, str | None]] = []

    def counting_unwrap(*, wrapped_dek: str, mount_point: str | None = None, key_name: str | None = None) -> bytes:
        unwrap_calls.append((mount_point, key_name))
        return fake_user_content_transit[wrapped_dek]

    monkeypatch.setattr("app.services.content_crypto.unwrap_user_content_data_key", counting_unwrap)
    db_session.info.clear()

    login(client, email="owner-kek-metadata@example.com", password="password-1")
    response = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert response.status_code == 200
    assert response.json()["current_draft_text"] == "Encrypted draft"
    assert unwrap_calls == [("legacy-transit", "legacy-kek")]


def test_transcribe_workspace_endpoint_does_not_health_check_stt_service(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Workspace Health Team")
    owner = make_user(email="owner-workspace-health@example.com", password="password-1", team=team, team_role=TeamRole.user)
    admin = make_user(email="admin-workspace-health@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin, label="Clinic STT", base_url="http://127.0.0.1:7000")
    make_stt_selection(config=config, actor=admin)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Idle session",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    call_count = 0

    def fail_if_called(**kwargs):
        nonlocal call_count
        call_count += 1
        raise AssertionError("workspace endpoint should not probe STT health")

    monkeypatch.setattr("app.services.stt.ensure_stt_service_healthy", fail_if_called)

    login(client, email="owner-workspace-health@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stt_selected"] is True
    assert payload["stt_available"] is True
    assert payload["stt_status_message"] is None
    assert call_count == 0


def test_transcribe_workspace_stream_returns_owner_workspace_event(client, db_session, make_team, make_user):
    team = make_team(name="Workspace Stream Team")
    owner = make_user(email="owner-workspace-stream@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-workspace-stream@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Streaming session",
        current_draft_text_encrypted="Live content",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    login(client, email="owner-workspace-stream@example.com", password="password-1")
    response = client.get(f"/api/v1/transcribe/workspace/stream?transcript_id={transcript.id}&once=true")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    lines = body.splitlines()

    assert "event: workspace" in lines
    data_line = next(line for line in lines if line.startswith("data: "))
    payload = json.loads(data_line[6:])
    assert payload["active_transcript"]["id"] == str(transcript.id)
    assert payload["active_transcript"]["current_draft_text"] == "Live content"

    client.post("/api/v1/auth/logout")
    login(client, email="other-workspace-stream@example.com", password="password-2")
    response = client.get(f"/api/v1/transcribe/workspace/stream?transcript_id={transcript.id}&once=true")
    assert response.status_code == 200
    body = response.text
    lines = body.splitlines()

    data_line = next(line for line in lines if line.startswith("data: "))
    payload = json.loads(data_line[6:])
    assert payload["active_transcript"] is None


def test_transcribe_workspace_stream_route_does_not_depend_on_request_scoped_db_session():
    route = next(
        route
        for route in api.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/transcribe/workspace/stream"
    )
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert get_db not in dependency_calls
    assert require_full_context not in dependency_calls


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


def test_start_transcript_ignores_client_retention_override(client, db_session, make_team, make_user):
    team = make_team(name="Retention API Team", default_retention_days=14)
    owner = make_user(email="retention-start@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="retention-start@example.com", password="password-1")
    response = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Retention attempt",
            "ingestion_mode": "whole_file",
            "retention_days_applied": 999,
        },
    )

    assert response.status_code == 201
    assert response.json()["retention_days_applied"] == 14
    persisted = db_session.get(Transcript, UUID(response.json()["id"]))
    assert persisted is not None
    assert persisted.retention_days_applied == 14


def test_create_transcript_endpoint_uses_team_retention_default(client, db_session, make_team, make_user):
    team = make_team(name="Retention Create Team", default_retention_days=21)
    owner = make_user(email="retention-create@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="retention-create@example.com", password="password-1")
    response = client.post(
        "/api/v1/transcripts",
        json={
            "owner_user_id": str(owner.id),
            "team_id": str(team.id),
            "title": "Create retention attempt",
            "ingestion_mode": "whole_file",
            "retention_days_applied": 999,
        },
    )

    assert response.status_code == 201
    assert response.json()["retention_days_applied"] == 21
    persisted = db_session.get(Transcript, UUID(response.json()["id"]))
    assert persisted is not None
    assert persisted.retention_days_applied == 21


def test_start_transcript_service_applies_team_retention_default(db_session, make_team, make_user):
    team = make_team(name="Retention Service Team", default_retention_days=7)
    owner = make_user(email="retention-service@example.com", password="password-1", team=team, team_role=TeamRole.user)

    transcript = start_transcript_service(
        db_session,
        owner,
        TranscriptStart(title="Service retention", ingestion_mode=TranscriptIngestionMode.whole_file),
    )

    assert transcript.retention_days_applied == 7


def test_update_transcript_cannot_extend_retention(client, db_session, make_team, make_user):
    team = make_team(name="Retention Update Team", default_retention_days=10)
    owner = make_user(email="retention-update@example.com", password="password-1", team=team, team_role=TeamRole.user)

    login(client, email="retention-update@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Initial title", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = UUID(started.json()["id"])
    transcript = db_session.get(Transcript, transcript_id)
    assert transcript is not None
    original_expires_at = transcript.retention_expires_at

    updated = client.patch(
        f"/api/v1/transcripts/{transcript_id}",
        json={"title": "Updated title", "retention_days_applied": 999},
    )

    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated title"
    assert updated.json()["retention_days_applied"] == 10
    db_session.refresh(transcript)
    assert transcript.retention_days_applied == 10
    assert transcript.retention_expires_at == original_expires_at


def test_team_retention_cannot_exceed_max(client, make_user):
    make_user(email="retention-admin@example.com", password="password-1", is_system_admin=True)
    login(client, email="retention-admin@example.com", password="password-1")

    response = client.post(
        "/api/v1/teams",
        json={
            "name": "Unsafe retention",
            "status": "active",
            "default_retention_days": 9999,
        },
    )

    details = assert_error(
        response,
        status_code=422,
        code="business_rule_violation",
        message="Retention must be between 1 and 90 days",
    )
    assert details == {"field": "default_retention_days", "min": 1, "max": 90}


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

    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript is not None
    job = make_ingestion_job_for_transcript(
        transcript,
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
        message="Wait for the current session transcription to finish before switching input mode",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.patch(f"/api/v1/transcripts/{transcript_id}", json={"ingestion_mode": "whole_file"})
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")


def test_transcript_start_rejects_second_blank_but_allows_new_session_while_transcribing(
    client,
    db_session,
    make_team,
    make_user,
):
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

    latest = db_session.get(Transcript, first_id)
    assert latest is not None
    job = make_ingestion_job_for_transcript(
        latest,
        job_kind=TranscriptIngestionJobKind.audio_file,
        chunk_sequence_no=None,
        source_filename="queued.wav",
        status=TranscriptIngestionJobStatus.queued,
    )
    latest.status = TranscriptStatus.transcribing
    db_session.add(latest)
    db_session.add(job)
    db_session.commit()

    allowed = client.post("/api/v1/transcripts/start", json={"title": "Second", "ingestion_mode": "whole_file"})
    assert allowed.status_code == 201
    second_id = UUID(allowed.json()["id"])
    assert second_id != first_id
    db_session.refresh(latest)
    db_session.refresh(job)
    assert latest.status is TranscriptStatus.transcribing
    assert job.status is TranscriptIngestionJobStatus.queued


def test_transcript_delete_is_owner_only_and_cascades_versions_jobs_and_generated_documents(
    client,
    db_session,
    make_team,
    make_user,
    make_template,
    make_generated_document,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    other = make_user(email="other@example.com", password="password-2", team=team, team_role=TeamRole.user)
    admin = make_user(email="delete-admin@example.com", password="password-3", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

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
    clinical_run = ClinicalEntityRun(
        transcript_id=transcript_id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=team.id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=True,
        api_provider="Clinical NLP",
        entity_count=1,
    )
    db_session.add(clinical_run)
    db_session.flush()
    clinical_entity = ClinicalEntity(
        clinical_entity_run_id=clinical_run.id,
        entity_order=1,
        entity_type="DISEASE",
        value_encrypted="asthma",
        normalized_value_hash="hash",
        occurrence_count=1,
    )
    db_session.add(clinical_entity)
    db_session.commit()
    deleted_secret_refs: list[str] = []
    queued = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert queued.status_code == 202
    job_id = UUID(queued.json()["job"]["id"])
    job = db_session.get(TranscriptIngestionJob, job_id)
    assert job is not None
    assert job.source_audio_vault_ref is not None

    client.post("/api/v1/auth/logout")
    login(client, email="other@example.com", password="password-2")
    forbidden = client.delete(f"/api/v1/transcripts/{transcript_id}")
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")

    client.post("/api/v1/auth/logout")
    login(client, email="owner@example.com", password="password-1")
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted_secret_refs.append(secret_ref),
    )
    deleted = client.delete(f"/api/v1/transcripts/{transcript_id}")
    assert deleted.status_code == 204
    assert db_session.get(Transcript, transcript_id) is None
    assert db_session.get(TranscriptVersion, version.id) is None
    assert db_session.get(ClinicalEntityRun, clinical_run.id) is None
    assert db_session.get(ClinicalEntity, clinical_entity.id) is None
    assert db_session.get(TranscriptIngestionJob, job_id) is None
    assert db_session.get(GeneratedDocument, generated_document.id) is None
    assert deleted_secret_refs == [job.source_audio_vault_ref]


def test_transcript_delete_still_succeeds_when_retry_audio_vault_cleanup_fails(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-delete-best-effort@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Delete me",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="recording.mp3",
            source_audio_vault_ref="secret:openscribe/transcript-ingestion/delete-best-effort/source-audio",
            source_audio_size_bytes=len(b"raw-file-audio"),
            status=TranscriptIngestionJobStatus.failed,
            error_code="stt_request_failed",
            error_message="STT provider request failed",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    login(client, email="owner-delete-best-effort@example.com", password="password-1")
    deleted = client.delete(f"/api/v1/transcripts/{transcript.id}")

    assert deleted.status_code == 204
    assert db_session.get(Transcript, transcript.id) is None


def test_live_audio_chunk_upload_queues_owner_job(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-live-owner-job@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    config.segments_path = "result.utterances"
    config.segment_text_field = "transcript"
    config.segment_start_field = "start_time"
    config.segment_end_field = "end_time"
    config.segment_speaker_field = "speaker_id"
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 12.0)

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
    assert uploaded.json()["transcript"]["current_draft_text"] == "draft-1"
    assert uploaded.json()["job"]["job_kind"] == "live_chunk"
    assert uploaded.json()["job"]["chunk_sequence_no"] == 1
    persisted = db_session.get(Transcript, UUID(transcript_id))
    assert persisted is not None
    assert is_encrypted_envelope(persisted.current_draft_text_encrypted)
    assert decrypt_transcript_draft(db_session, persisted) == "draft-1"
    job = db_session.get(TranscriptIngestionJob, UUID(uploaded.json()["job"]["id"]))
    assert job is not None
    assert job.status is TranscriptIngestionJobStatus.queued
    assert job.celery_task_id == "test-task-id"
    assert job.source_audio_blob is None
    assert job.source_audio_vault_ref is not None
    assert job.source_audio_size_bytes == len(b"raw-audio")
    assert job.declared_duration_seconds == 12
    assert job.stt_segments_path == "result.utterances"
    assert job.stt_segment_text_field == "transcript"
    assert job.stt_segment_start_field == "start_time"
    assert job.stt_segment_end_field == "end_time"
    assert job.stt_segment_speaker_field == "speaker_id"


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


def test_duplicate_live_chunk_sequence_is_rejected(client, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-live-duplicate@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)
    monkeypatch.setattr("app.services.transcripts.LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", 1.0)
    login(client, email="owner@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Visit", "ingestion_mode": "live_chunked"})
    transcript_id = started.json()["id"]

    first = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )
    assert first.status_code == 202
    time.sleep(1.05)

    response = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1"},
    )

    assert_error(response, status_code=409, code="conflict", message="Chunk sequence number has already been submitted")


def test_live_audio_chunk_upload_is_rate_limited_per_authenticated_user(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-live-limit@example.com", password="password-1", is_system_admin=True)
    owner_one = make_user(email="owner-live-one@example.com", password="password-2", team=team, team_role=TeamRole.user)
    owner_two = make_user(email="owner-live-two@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner_one)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)

    transcript_one = Transcript(
        owner_user_id=owner_one.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner_one.created_at,
    )
    transcript_two = Transcript(
        owner_user_id=owner_two.id,
        team_id=team.id,
        title="Visit two",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner_two.created_at,
    )
    db_session.add_all([transcript_one, transcript_two])
    db_session.commit()

    login(client, email="owner-live-one@example.com", password="password-2")
    first = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-chunks",
        files={"audio": ("chunk-one.webm", b"raw-audio-1", "audio/webm")},
        data={"chunk_sequence_no": "1", "declared_duration_seconds": "1"},
    )
    assert first.status_code == 202

    second = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-chunks",
        files={"audio": ("chunk-two.webm", b"raw-audio-2", "audio/webm")},
        data={"chunk_sequence_no": "2", "declared_duration_seconds": "1"},
    )
    assert_error(second, status_code=429, code="rate_limited", message="Too many requests")

    client.post("/api/v1/auth/logout")
    login(client, email="owner-live-two@example.com", password="password-3")
    third = client.post(
        f"/api/v1/transcripts/{transcript_two.id}/audio-chunks",
        files={"audio": ("chunk-three.webm", b"raw-audio-3", "audio/webm")},
        data={"chunk_sequence_no": "1", "declared_duration_seconds": "1"},
    )
    assert third.status_code == 202


def test_live_audio_chunk_upload_enforces_hourly_duration_budget(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-live-duration@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-live-duration@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", 5.0)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 2.0)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="earlier.webm",
            source_audio_size_bytes=len(b"earlier-audio"),
            declared_duration_seconds=4.0,
            status=TranscriptIngestionJobStatus.applied,
            created_at=utcnow() - timedelta(minutes=10),
            updated_at=utcnow() - timedelta(minutes=10),
            applied_at=utcnow() - timedelta(minutes=10),
        )
    )
    db_session.commit()

    login(client, email="owner-live-duration@example.com", password="password-2")
    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "2", "declared_duration_seconds": "2"},
    )

    details = assert_error(
        response,
        status_code=429,
        code="rate_limited",
        message="Live transcription hourly audio limit exceeded",
    )
    assert details["window"] == "1 hour"
    assert details["max_seconds"] == 5.0
    assert details["used_seconds"] == 4.0
    assert details["requested_seconds"] == 2.0


def test_live_audio_chunk_hourly_duration_budget_is_isolated_per_owner(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-live-duration-isolated@example.com", password="password-1", is_system_admin=True)
    owner_one = make_user(email="owner-live-cap-one@example.com", password="password-2", team=team, team_role=TeamRole.user)
    owner_two = make_user(email="owner-live-cap-two@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner_one)
    monkeypatch.setattr("app.services.transcripts.LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", 5.0)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 1.0)

    transcript_one = Transcript(
        owner_user_id=owner_one.id,
        team_id=team.id,
        title="Visit one",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner_one.created_at,
    )
    transcript_two = Transcript(
        owner_user_id=owner_two.id,
        team_id=team.id,
        title="Visit two",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner_two.created_at,
    )
    db_session.add_all([transcript_one, transcript_two])
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript_one,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="earlier.webm",
            source_audio_size_bytes=len(b"earlier-audio"),
            declared_duration_seconds=4.5,
            status=TranscriptIngestionJobStatus.applied,
            created_at=utcnow() - timedelta(minutes=5),
            updated_at=utcnow() - timedelta(minutes=5),
            applied_at=utcnow() - timedelta(minutes=5),
        )
    )
    db_session.commit()

    login(client, email="owner-live-cap-one@example.com", password="password-2")
    blocked = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "2", "declared_duration_seconds": "1"},
    )
    assert_error(
        blocked,
        status_code=429,
        code="rate_limited",
        message="Live transcription hourly audio limit exceeded",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="owner-live-cap-two@example.com", password="password-3")
    allowed = client.post(
        f"/api/v1/transcripts/{transcript_two.id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "1", "declared_duration_seconds": "1"},
    )
    assert allowed.status_code == 202


def test_live_audio_chunk_upload_measures_duration_when_declared_value_is_missing(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-live-measured@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-live-measured@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS", 5.0)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 2.5)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Visit",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="earlier.webm",
            source_audio_size_bytes=len(b"earlier-audio"),
            declared_duration_seconds=4.0,
            status=TranscriptIngestionJobStatus.applied,
            created_at=utcnow() - timedelta(minutes=10),
            updated_at=utcnow() - timedelta(minutes=10),
            applied_at=utcnow() - timedelta(minutes=10),
        )
    )
    db_session.commit()

    login(client, email="owner-live-measured@example.com", password="password-2")
    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/audio-chunks",
        files={"audio": ("chunk.webm", b"raw-audio", "audio/webm")},
        data={"chunk_sequence_no": "2"},
    )

    details = assert_error(
        response,
        status_code=429,
        code="rate_limited",
        message="Live transcription hourly audio limit exceeded",
    )
    assert details["requested_seconds"] == 2.5


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

    def fake_transcribe_with_stt_snapshot(
        db,
        *,
        team_id,
        stt_config_id,
        provider_preset,
        adapter_kind,
        base_url,
        transcribe_path,
        file_field_name,
        response_text_path,
        extra_form_fields_json,
        model_name,
        model_field_name,
        language,
        language_field_name,
        segments_path,
        segment_text_field,
        segment_start_field,
        segment_end_field,
        segment_speaker_field,
        audio_bytes,
        filename,
        content_type,
    ):
        assert team_id == team.id
        assert stt_config_id == config.id
        assert provider_preset == config.provider_preset
        assert content_type == "audio/wav"
        assert segments_path is None
        assert segment_text_field is None
        assert segment_start_field is None
        assert segment_end_field is None
        assert segment_speaker_field is None
        if filename == "chunk-1.wav":
            return "first chunk"
        if filename == "chunk-2.wav":
            return "second chunk"
        raise AssertionError(f"unexpected filename {filename}")

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 42.5)
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 10.0)

    queued_one = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk-1.webm", b"chunk-1", "audio/webm")},
        data={"chunk_sequence_no": "1", "declared_duration_seconds": "12"},
    )
    job_one_id = UUID(queued_one.json()["job"]["id"])
    time.sleep(1.05)
    queued_two = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-chunks",
        files={"audio": ("chunk-2.webm", b"chunk-2", "audio/webm")},
        data={"chunk_sequence_no": "2", "declared_duration_seconds": "10"},
    )
    job_two_id = UUID(queued_two.json()["job"]["id"])

    processed_two = process_transcript_ingestion_job(db_session, job_id=job_two_id)
    assert processed_two.status is TranscriptIngestionJobStatus.completed

    transcript_after_two = db_session.get(Transcript, UUID(transcript_id))
    assert transcript_after_two is not None
    assert is_encrypted_envelope(transcript_after_two.current_draft_text_encrypted)
    assert decrypt_transcript_draft(db_session, transcript_after_two) == "draft-1"
    assert transcript_after_two.next_live_chunk_sequence_no_applied == 1

    processed_one = process_transcript_ingestion_job(db_session, job_id=job_one_id)
    assert processed_one.status is TranscriptIngestionJobStatus.applied

    transcript_after_one = db_session.get(Transcript, UUID(transcript_id))
    assert transcript_after_one is not None
    assert is_encrypted_envelope(transcript_after_one.current_draft_text_encrypted)
    assert decrypt_transcript_draft(db_session, transcript_after_one) == "draft-1\nfirst chunk\nsecond chunk"
    assert transcript_after_one.next_live_chunk_sequence_no_applied == 3
    assert transcript_after_one.status.value == "ready"

    refreshed_two = db_session.get(TranscriptIngestionJob, job_two_id)
    assert refreshed_two is not None
    assert refreshed_two.status is TranscriptIngestionJobStatus.applied


def test_finalize_live_capture_applies_completed_chunks_and_creates_preview_redaction(client, db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Live Finalize Team")
    owner = make_user(email="owner-live-finalize@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-live-finalize@example.com", password="password-2", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live finalize",
        current_draft_text_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=uuid4(),
            plaintext="initial draft",
        ),
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        next_live_chunk_sequence_no_applied=1,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="initial draft",
    )
    job_id = uuid4()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            id=job_id,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="chunk-1.wav",
            status=TranscriptIngestionJobStatus.completed,
            result_text_encrypted=encrypt_text_for_owner(
                db_session,
                owner_user_id=owner.id,
                table="transcript_ingestion_jobs",
                field="result_text_encrypted",
                record_id=job_id,
                plaintext="John Smith attended",
            ),
        )
    )
    db_session.commit()

    from app.services.redaction import DeidentificationDetectionResult, Span

    detected_texts: list[str] = []

    def fake_detect_phi(db, *, provider, text, language, score_threshold, entities):
        detected_texts.append(text)
        start = text.index("John Smith")
        return DeidentificationDetectionResult(
            spans=[Span(start=start, end=start + len("John Smith"), entity_type="PERSON", score=0.99)],
            api_provider=provider.label,
            api_model_or_version="stub-model",
        )

    monkeypatch.setattr("app.services.redaction._detect_phi", fake_detect_phi)

    login(client, email="other-live-finalize@example.com", password="password-2")
    forbidden = client.post(f"/api/v1/transcripts/{transcript.id}/finalize-live-capture")
    assert_error(forbidden, status_code=403, code="forbidden", message="Transcript access is restricted to the owning user")

    client.post("/api/v1/auth/logout")
    login(client, email="owner-live-finalize@example.com", password="password-1")
    finalized = client.post(f"/api/v1/transcripts/{transcript.id}/finalize-live-capture")

    assert finalized.status_code == 200
    assert finalized.json()["status"] == "ready"
    refreshed = db_session.get(Transcript, transcript.id)
    assert refreshed is not None
    assert decrypt_transcript_draft(db_session, refreshed) == "initial draft\nJohn Smith attended"
    assert refreshed.next_live_chunk_sequence_no_applied == 2
    version = db_session.scalar(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id))
    assert version is not None
    run = db_session.scalar(select(RedactionRun).where(RedactionRun.transcript_version_id == version.id))
    assert run is not None
    assert run.entity_count == 1
    assert detected_texts == ["initial draft\nJohn Smith attended"]

    second_finalize = client.post(f"/api/v1/transcripts/{transcript.id}/finalize-live-capture")
    assert second_finalize.status_code == 200
    assert db_session.query(TranscriptVersion).filter(TranscriptVersion.transcript_id == transcript.id).count() == 1
    assert db_session.query(RedactionRun).filter(RedactionRun.transcript_id == transcript.id).count() == 1


def test_finalize_live_capture_with_pending_chunk_defers_preview_redaction(client, db_session, make_team, make_user):
    team = make_team(name="Live Pending Finalize Team")
    owner = make_user(email="owner-live-pending-finalize@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live pending finalize",
        current_draft_text_encrypted="draft",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        next_live_chunk_sequence_no_applied=1,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="chunk-1.wav",
            status=TranscriptIngestionJobStatus.queued,
        )
    )
    db_session.commit()

    login(client, email="owner-live-pending-finalize@example.com", password="password-1")
    finalized = client.post(f"/api/v1/transcripts/{transcript.id}/finalize-live-capture")

    assert finalized.status_code == 200
    assert finalized.json()["status"] == "transcribing"
    assert db_session.query(TranscriptVersion).filter(TranscriptVersion.transcript_id == transcript.id).count() == 0
    assert db_session.query(RedactionRun).filter(RedactionRun.transcript_id == transcript.id).count() == 0


def test_finalize_live_capture_without_pending_chunks_marks_ready(client, db_session, make_team, make_user):
    team = make_team(name="Live Empty Finalize Team")
    owner = make_user(email="owner-live-empty-finalize@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live empty finalize",
        current_draft_text_encrypted=None,
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.recording,
        next_live_chunk_sequence_no_applied=1,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    login(client, email="owner-live-empty-finalize@example.com", password="password-1")
    finalized = client.post(f"/api/v1/transcripts/{transcript.id}/finalize-live-capture")

    assert finalized.status_code == 200
    assert finalized.json()["status"] == "ready"
    refreshed = db_session.get(Transcript, transcript.id)
    assert refreshed is not None
    assert refreshed.status is TranscriptStatus.ready


def test_finalize_live_capture_rejects_non_live_transcripts(client, db_session, make_team, make_user):
    team = make_team(name="Non Live Finalize Team")
    owner = make_user(email="owner-non-live-finalize@example.com", password="password-1", team=team, team_role=TeamRole.user)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Whole file",
        current_draft_text_encrypted="draft",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()

    login(client, email="owner-non-live-finalize@example.com", password="password-1")
    response = client.post(f"/api/v1/transcripts/{transcript.id}/finalize-live-capture")

    assert_error(
        response,
        status_code=409,
        code="business_rule_violation",
        message="Only live capture transcripts can be finalized",
    )


def test_transcript_detail_reconciles_completed_live_chunks_after_failed_gap(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-live-gap@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live visit",
        current_draft_text_encrypted="draft-1",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.transcribing,
        next_live_chunk_sequence_no_applied=1,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add_all(
        [
            make_ingestion_job_for_transcript(
                transcript,
                job_kind=TranscriptIngestionJobKind.live_chunk,
                chunk_sequence_no=1,
                source_filename="chunk-1.wav",
                status=TranscriptIngestionJobStatus.failed,
                error_code="stt_unavailable",
                error_message="Could not reach the STT provider",
            ),
            make_ingestion_job_for_transcript(
                transcript,
                job_kind=TranscriptIngestionJobKind.live_chunk,
                chunk_sequence_no=2,
                source_filename="chunk-2.wav",
                status=TranscriptIngestionJobStatus.completed,
                result_text_encrypted="second chunk",
            ),
        ]
    )
    db_session.commit()

    login(client, email="owner-live-gap@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert detail.status_code == 200
    assert detail.json()["current_draft_text"] == "draft-1\nsecond chunk"
    assert detail.json()["next_live_chunk_sequence_no_upload"] == 3
    assert detail.json()["status"] == "ready"
    refreshed_transcript = db_session.get(Transcript, transcript.id)
    assert refreshed_transcript is not None
    assert refreshed_transcript.next_live_chunk_sequence_no_applied == 3
    assert refreshed_transcript.status is TranscriptStatus.ready
    refreshed_job = db_session.scalar(
        select(TranscriptIngestionJob).where(
            TranscriptIngestionJob.transcript_id == transcript.id,
            TranscriptIngestionJob.chunk_sequence_no == 2,
        )
    )
    assert refreshed_job is not None
    assert refreshed_job.status is TranscriptIngestionJobStatus.applied


def test_transcript_detail_reconciles_completed_live_chunks_after_stale_processing_gap(
    client, db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-live-stale@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live visit",
        current_draft_text_encrypted="draft-1",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.transcribing,
        next_live_chunk_sequence_no_applied=1,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    stale_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=1,
        source_filename="chunk-1.wav",
        status=TranscriptIngestionJobStatus.processing,
        started_at=utcnow() - timedelta(minutes=20),
    )
    later_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=2,
        source_filename="chunk-2.wav",
        status=TranscriptIngestionJobStatus.completed,
        result_text_encrypted="second chunk",
    )
    db_session.add_all([stale_job, later_job])
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts.LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS", 60)

    login(client, email="owner-live-stale@example.com", password="password-1")
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")

    assert detail.status_code == 200
    assert detail.json()["current_draft_text"] == "draft-1\nsecond chunk"
    assert detail.json()["next_live_chunk_sequence_no_upload"] == 3
    assert detail.json()["status"] == "ready"

    refreshed_stale_job = db_session.get(TranscriptIngestionJob, stale_job.id)
    assert refreshed_stale_job is not None
    assert refreshed_stale_job.status is TranscriptIngestionJobStatus.failed
    assert refreshed_stale_job.error_code == "ingestion_processing_stale"
    refreshed_later_job = db_session.get(TranscriptIngestionJob, later_job.id)
    assert refreshed_later_job is not None
    assert refreshed_later_job.status is TranscriptIngestionJobStatus.applied


def test_processing_transcript_ingestion_job_skips_already_failed_job(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-failed-worker@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live visit",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    failed_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=1,
        source_filename="chunk-1.wav",
        status=TranscriptIngestionJobStatus.failed,
        error_code="ingestion_processing_stale",
        error_message="Live audio chunk processing timed out before completion",
    )
    db_session.add(failed_job)
    db_session.commit()

    def fail_if_called(**kwargs):
        raise AssertionError("failed ingestion jobs should not be processed by late worker delivery")

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fail_if_called)

    processed = process_transcript_ingestion_job(db_session, job_id=failed_job.id)

    assert processed.status is TranscriptIngestionJobStatus.failed
    assert processed.error_code == "ingestion_processing_stale"


def test_processing_transcript_ingestion_job_does_not_revive_midflight_failed_job(
    db_session, make_team, make_user, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-midflight-failed@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live visit",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.transcribing,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        chunk_sequence_no=1,
        source_filename="chunk-1.webm",
        status=TranscriptIngestionJobStatus.queued,
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.transcripts.normalize_audio_to_wav_16k_mono",
        lambda **kwargs: NormalizedAudio(filename="chunk-1.wav", content_type="audio/wav", data=b"normalized"),
    )
    monkeypatch.setattr("app.services.transcripts.read_transcript_ingestion_source_audio", lambda **kwargs: b"raw-audio")
    job.source_audio_vault_ref = "secret:openscribe/transcript-ingestion/midflight/source-audio"
    db_session.add(job)
    db_session.commit()

    def fake_transcribe_with_stt_snapshot(db, **kwargs):
        refreshed_job = db.get(TranscriptIngestionJob, job.id)
        refreshed_transcript = db.get(Transcript, transcript.id)
        assert refreshed_job is not None
        assert refreshed_transcript is not None
        refreshed_job.status = TranscriptIngestionJobStatus.failed
        refreshed_job.error_code = "ingestion_processing_stale"
        refreshed_job.error_message = "Live audio chunk processing timed out before completion"
        refreshed_transcript.status = TranscriptStatus.failed
        db.add(refreshed_job)
        db.add(refreshed_transcript)
        db.commit()
        return "late text"

    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)

    processed = process_transcript_ingestion_job(db_session, job_id=job.id)

    assert processed.status is TranscriptIngestionJobStatus.failed
    assert processed.error_code == "ingestion_processing_stale"
    assert processed.result_text_encrypted is None
    refreshed_transcript = db_session.get(Transcript, transcript.id)
    assert refreshed_transcript is not None
    assert refreshed_transcript.status is TranscriptStatus.failed


def test_transcript_workspace_reconciles_stale_live_chunk_session_to_ready(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-live-ready@example.com", password="password-1", team=team, team_role=TeamRole.leader)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Live visit",
        current_draft_text_encrypted="draft-1",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.transcribing,
        next_live_chunk_sequence_no_applied=2,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.live_chunk,
            chunk_sequence_no=1,
            source_filename="chunk-1.wav",
            status=TranscriptIngestionJobStatus.applied,
            result_text_encrypted="first chunk",
        )
    )
    db_session.commit()

    login(client, email="owner-live-ready@example.com", password="password-1")
    workspace = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert workspace.status_code == 200
    payload = workspace.json()
    assert payload["active_transcript"]["status"] == "ready"
    assert payload["can_create_new_session"] is True

    refreshed_transcript = db_session.get(Transcript, transcript.id)
    assert refreshed_transcript is not None
    assert refreshed_transcript.status is TranscriptStatus.ready


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
    assert_error(
        queued,
        status_code=422,
        code="business_rule_violation",
        message="No active STT selection for team and purpose",
    )


def test_stt_health_probe_uses_explicit_healthcheck_url_and_bearer_auth(monkeypatch):
    calls = []

    class DummyResponse:
        def raise_for_status(self):
            return None

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return DummyResponse()

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    ensure_stt_service_healthy(
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://ignored.example",
        bearer_token="secret-token",
        healthcheck_url="http://stt.example/internal/ready",
    )

    assert calls == [("http://stt.example/internal/ready", {"Authorization": "Bearer secret-token"}, 5.0)]


def test_stt_health_probe_skips_without_explicit_healthcheck_url(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("health probe should be skipped when no explicit health URL is configured")

    monkeypatch.setattr("app.services.stt.httpx.get", fail_if_called)

    ensure_stt_service_healthy(
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://stt.example",
        bearer_token="secret-token",
        healthcheck_url=None,
    )


def test_safe_http_error_details_redacts_url_credentials_and_query():
    request = httpx.Request(
        "POST",
        "http://user:pass@stt.example:7000/v1/audio/transcriptions?token=secret&sig=abc",
    )
    exc = httpx.ConnectError("boom", request=request)

    details = _safe_http_error_details(exc)

    assert details["url"] == "http://stt.example:7000/v1/audio/transcriptions"


def test_audio_file_upload_queues_job_for_whole_file_mode(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-audio-file@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    config.segments_path = "result.utterances"
    config.segment_text_field = "transcript"
    config.segment_start_field = "start_time"
    config.segment_end_field = "end_time"
    config.segment_speaker_field = "speaker_id"
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 42.5)
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
    assert job.source_audio_blob is None
    assert job.source_audio_vault_ref is not None
    assert job.source_audio_size_bytes == len(b"raw-file-audio")
    assert job.source_audio_duration_seconds == 42.5
    assert job.stt_segments_path == "result.utterances"
    assert job.stt_segment_text_field == "transcript"
    assert job.stt_segment_start_field == "start_time"
    assert job.stt_segment_end_field == "end_time"
    assert job.stt_segment_speaker_field == "speaker_id"


def test_retry_audio_file_route_requeues_failed_blob_for_owner(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-retry@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-retry@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Retry me",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    failed_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_vault_ref="secret:openscribe/transcript-ingestion/retry/source-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        source_audio_duration_seconds=15.25,
        stt_config_id=config.id,
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=config.model_name,
        stt_language=config.language,
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
        stt_extra_form_fields_json=config.extra_form_fields_json or {},
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
    )
    db_session.add(failed_job)
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts.read_transcript_ingestion_source_audio", lambda **kwargs: b"raw-file-audio")

    login(client, email="owner-retry@example.com", password="password-1")
    retried = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")

    assert retried.status_code == 202
    body = retried.json()
    assert body["transcript"]["status"] == "transcribing"
    assert body["job"]["job_kind"] == "audio_file"
    refreshed_failed_job = db_session.get(TranscriptIngestionJob, failed_job.id)
    assert refreshed_failed_job is not None
    assert refreshed_failed_job.source_audio_blob is None
    assert refreshed_failed_job.source_audio_size_bytes is None
    assert refreshed_failed_job.source_audio_duration_seconds is None
    assert refreshed_failed_job.source_audio_vault_ref is None
    new_job = db_session.get(TranscriptIngestionJob, UUID(body["job"]["id"]))
    assert new_job is not None
    assert new_job.source_audio_blob is None
    assert new_job.source_audio_vault_ref is not None
    assert new_job.source_audio_size_bytes == len(b"raw-file-audio")
    assert new_job.source_audio_duration_seconds == 15.25
    assert new_job.source_filename == "recording.mp3"
    assert new_job.celery_task_id == "test-task-id"


def test_retry_audio_file_route_excludes_failed_job_from_hourly_budget(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-retry-budget@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-retry-budget@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_UPLOAD_BYTES", len(b"raw-file-audio"))
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS", 15.25)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Retry budget",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    failed_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_blob=b"raw-file-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        source_audio_duration_seconds=15.25,
        stt_config_id=config.id,
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=config.model_name,
        stt_language=config.language,
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
        stt_extra_form_fields_json=config.extra_form_fields_json or {},
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
        created_at=utcnow() - timedelta(minutes=5),
        updated_at=utcnow() - timedelta(minutes=5),
        completed_at=utcnow() - timedelta(minutes=5),
    )
    db_session.add(failed_job)
    db_session.commit()

    login(client, email="owner-retry-budget@example.com", password="password-1")
    retried = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")

    assert retried.status_code == 202


def test_retry_audio_file_route_rejects_when_failed_blob_is_missing(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-retry-missing@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-retry-missing@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Retry unavailable",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="recording.mp3",
            status=TranscriptIngestionJobStatus.failed,
            error_code="stt_request_failed",
            error_message="STT provider request failed",
        )
    )
    db_session.commit()

    login(client, email="owner-retry-missing@example.com", password="password-1")
    retried = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")

    assert_error(
        retried,
        status_code=409,
        code="ingestion_retry_unavailable",
        message="The failed upload is no longer available to retry. Upload the audio file again.",
    )


def test_retry_audio_file_route_rejects_when_vault_retry_audio_is_missing(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-retry-vault-missing@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-retry-vault-missing@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Retry unavailable",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="recording.mp3",
            source_audio_vault_ref="secret:openscribe/transcript-ingestion/missing/source-audio",
            source_audio_size_bytes=len(b"raw-file-audio"),
            status=TranscriptIngestionJobStatus.failed,
            error_code="stt_request_failed",
            error_message="STT provider request failed",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.transcripts.read_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_read_failed", "Stored retry audio is missing")),
    )

    login(client, email="owner-retry-vault-missing@example.com", password="password-1")
    retried = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")

    assert_error(
        retried,
        status_code=409,
        code="ingestion_retry_unavailable",
        message="The failed upload is no longer available to retry. Upload the audio file again.",
    )


def test_audio_file_upload_enqueue_failure_preserves_retry_source_for_uploaded_file(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-enqueue-fail@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-enqueue-fail@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 18.0)

    def fail_enqueue(**kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", fail_enqueue)

    login(client, email="owner-enqueue-fail@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )

    assert_error(
        uploaded,
        status_code=502,
        code="ingestion_enqueue_failed",
        message="Could not enqueue file ingestion",
    )
    latest_job = latest_ingestion_job_for_transcript_service(db_session, transcript_id=UUID(transcript_id))
    assert latest_job is not None
    assert latest_job.status is TranscriptIngestionJobStatus.failed
    assert latest_job.source_audio_vault_ref is not None
    detail = client.get(f"/api/v1/transcripts/{transcript_id}")
    assert detail.status_code == 200
    assert detail.json()["latest_ingestion_retry_available"] is True


def test_retry_audio_file_enqueue_failure_keeps_retry_source_available(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-retry-enqueue-fail@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-retry-enqueue-fail@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Retry me later",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    failed_job = make_ingestion_job_for_transcript(
        transcript,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="recording.mp3",
        source_audio_blob=None,
        source_audio_vault_ref="secret:openscribe/transcript-ingestion/legacy/source-audio",
        source_audio_size_bytes=len(b"raw-file-audio"),
        source_audio_duration_seconds=15.25,
        stt_config_id=config.id,
        stt_adapter_kind=config.adapter_kind.value,
        stt_base_url=config.base_url,
        stt_transcribe_path=config.transcribe_path,
        stt_model_name=config.model_name,
        stt_language=config.language,
        stt_file_field_name=config.file_field_name,
        stt_response_text_path=config.response_text_path,
        stt_extra_form_fields_json=config.extra_form_fields_json or {},
        status=TranscriptIngestionJobStatus.failed,
        error_code="stt_request_failed",
        error_message="STT provider request failed",
    )
    db_session.add(failed_job)
    db_session.commit()

    stored_audio = {failed_job.source_audio_vault_ref: b"raw-file-audio"}
    monkeypatch.setattr("app.services.transcripts.read_transcript_ingestion_source_audio", lambda *, secret_ref: stored_audio[secret_ref])

    def fail_enqueue(**kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", fail_enqueue)

    login(client, email="owner-retry-enqueue-fail@example.com", password="password-1")
    retried = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")

    assert_error(
        retried,
        status_code=502,
        code="ingestion_enqueue_failed",
        message="Could not enqueue file ingestion retry",
    )
    refreshed_failed_job = db_session.get(TranscriptIngestionJob, failed_job.id)
    assert refreshed_failed_job is not None
    assert refreshed_failed_job.source_audio_vault_ref == "secret:openscribe/transcript-ingestion/legacy/source-audio"
    assert refreshed_failed_job.source_audio_size_bytes is None
    latest_job = latest_ingestion_job_for_transcript_service(db_session, transcript_id=transcript.id)
    assert latest_job is not None
    assert latest_job.id != failed_job.id
    assert latest_job.status is TranscriptIngestionJobStatus.failed
    assert latest_job.source_audio_vault_ref is not None
    assert latest_job.source_audio_vault_ref != "secret:openscribe/transcript-ingestion/legacy/source-audio"
    detail = client.get(f"/api/v1/transcripts/{transcript.id}")
    assert detail.status_code == 200
    assert detail.json()["latest_ingestion_retry_available"] is True


def test_audio_file_upload_fails_immediately_when_selected_stt_secret_is_missing(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-missing-secret@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-missing-secret@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    def fake_read_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
        raise AppError(
            502,
            "vault_read_failed",
            "STT provider credential is missing for the queued transcription config",
            {"team_id": str(team_id), "config_id": str(config_id)},
        )

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", fake_read_team_stt_bearer_token)

    login(client, email="owner-missing-secret@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )

    assert_error(
        uploaded,
        status_code=409,
        code="stt_config_secret_missing",
        message="The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
    )
    assert db_session.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.transcript_id == UUID(transcript_id))) is None
    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript is not None
    assert transcript.status is TranscriptStatus.ready


def test_audio_file_upload_does_not_preflight_stt_health_before_queueing(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-health@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-health@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner-health@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    calls = []

    def fake_ensure_stt_service_healthy(**kwargs):
        calls.append(kwargs)
        raise AssertionError("whole-file queueing should not perform a separate STT health probe")

    monkeypatch.setattr("app.services.stt.ensure_stt_service_healthy", fake_ensure_stt_service_healthy)

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )

    assert uploaded.status_code == 202
    assert calls == []


def test_audio_file_upload_queues_even_if_health_probe_helper_would_fail(
    client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-healthfail@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-healthfail@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner-healthfail@example.com", password="password-1")
    started = client.post("/api/v1/transcripts/start", json={"title": "Imported visit", "ingestion_mode": "whole_file"})
    transcript_id = UUID(started.json()["id"])

    def fake_ensure_stt_service_healthy(**kwargs):
        raise AssertionError("whole-file queueing should not call the STT health probe helper")

    monkeypatch.setattr("app.services.stt.ensure_stt_service_healthy", fake_ensure_stt_service_healthy)

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )

    assert uploaded.status_code == 202
    transcript = db_session.get(Transcript, transcript_id)
    assert transcript is not None
    assert transcript.status is TranscriptStatus.transcribing
    jobs = db_session.scalars(
        select(TranscriptIngestionJob).where(TranscriptIngestionJob.transcript_id == transcript_id)
    ).all()
    assert len(jobs) == 1


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

    job = make_ingestion_job_for_transcript(
        transcript,
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


def test_whole_file_upload_default_caps_match_four_hour_policy():
    assert WHOLE_FILE_MAX_UPLOAD_BYTES == 200 * 1024 * 1024
    assert WHOLE_FILE_MAX_DURATION_SECONDS == 4 * 60 * 60
    assert WHOLE_FILE_HOURLY_UPLOAD_BYTES == 200 * 1024 * 1024
    assert WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS == 4 * 60 * 60
    assert AUDIO_FFMPEG_TIMEOUT_SECONDS == 30 * 60
    assert STT_TRANSCRIPTION_TIMEOUT_SECONDS == 4 * 60 * 60


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


def test_audio_file_upload_enforces_hourly_upload_size_budget(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-file-size-budget@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-file-size-budget@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_UPLOAD_BYTES", 10)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS", 9999.0)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 5.0)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Imported visit",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="earlier.mp3",
            source_audio_size_bytes=9,
            source_audio_duration_seconds=4.0,
            status=TranscriptIngestionJobStatus.applied,
            created_at=utcnow() - timedelta(minutes=10),
            updated_at=utcnow() - timedelta(minutes=10),
            applied_at=utcnow() - timedelta(minutes=10),
        )
    )
    db_session.commit()

    login(client, email="owner-file-size-budget@example.com", password="password-2")
    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/audio-file",
        files={"audio": ("recording.mp3", b"12", "audio/mpeg")},
    )

    details = assert_error(
        response,
        status_code=429,
        code="rate_limited",
        message="Whole-file hourly upload size limit exceeded",
    )
    assert details["window"] == "1 hour"
    assert details["max_bytes"] == 10
    assert details["used_bytes"] == 9
    assert details["requested_bytes"] == 2


def test_audio_file_upload_enforces_hourly_duration_budget(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-file-duration-budget@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email="owner-file-duration-budget@example.com", password="password-2", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_UPLOAD_BYTES", 999999)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS", 5.0)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 2.0)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Imported visit",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="earlier.mp3",
            source_audio_size_bytes=20,
            source_audio_duration_seconds=4.5,
            status=TranscriptIngestionJobStatus.applied,
            created_at=utcnow() - timedelta(minutes=5),
            updated_at=utcnow() - timedelta(minutes=5),
            applied_at=utcnow() - timedelta(minutes=5),
        )
    )
    db_session.commit()

    login(client, email="owner-file-duration-budget@example.com", password="password-2")
    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )

    details = assert_error(
        response,
        status_code=429,
        code="rate_limited",
        message="Whole-file hourly audio limit exceeded",
    )
    assert details["window"] == "1 hour"
    assert details["max_seconds"] == 5.0
    assert details["used_seconds"] == 4.5
    assert details["requested_seconds"] == 2.0


def test_audio_file_upload_hourly_budget_is_isolated_per_owner(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    admin = make_user(email="admin-file-budget-isolated@example.com", password="password-1", is_system_admin=True)
    owner_one = make_user(email="owner-file-cap-one@example.com", password="password-2", team=team, team_role=TeamRole.user)
    owner_two = make_user(email="owner-file-cap-two@example.com", password="password-3", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner_one)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_UPLOAD_BYTES", 999999)
    monkeypatch.setattr("app.services.transcripts.WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS", 5.0)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 1.0)

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
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript_one,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="earlier.mp3",
            source_audio_size_bytes=20,
            source_audio_duration_seconds=4.8,
            status=TranscriptIngestionJobStatus.applied,
            created_at=utcnow() - timedelta(minutes=20),
            updated_at=utcnow() - timedelta(minutes=20),
            applied_at=utcnow() - timedelta(minutes=20),
        )
    )
    db_session.commit()

    login(client, email="owner-file-cap-one@example.com", password="password-2")
    blocked = client.post(
        f"/api/v1/transcripts/{transcript_one.id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert_error(
        blocked,
        status_code=429,
        code="rate_limited",
        message="Whole-file hourly audio limit exceeded",
    )

    client.post("/api/v1/auth/logout")
    login(client, email="owner-file-cap-two@example.com", password="password-3")
    allowed = client.post(
        f"/api/v1/transcripts/{transcript_two.id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    assert allowed.status_code == 202


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
        provider_preset,
        adapter_kind,
        base_url,
        transcribe_path,
        file_field_name,
        response_text_path,
        extra_form_fields_json,
        model_name,
        model_field_name,
        language,
        language_field_name,
        segments_path,
        segment_text_field,
        segment_start_field,
        segment_end_field,
        segment_speaker_field,
        audio_bytes,
        filename,
        content_type,
    ):
        assert team_id == team.id
        assert stt_config_id == config.id
        assert provider_preset == config.provider_preset
        assert adapter_kind == config.adapter_kind.value
        assert base_url == config.base_url
        assert transcribe_path == config.transcribe_path
        assert file_field_name == config.file_field_name
        assert response_text_path == config.response_text_path
        assert extra_form_fields_json == (config.extra_form_fields_json or {})
        assert model_name == config.model_name
        assert model_field_name == (config.model_field_name or "model")
        assert language == config.language
        assert language_field_name == (config.language_field_name or "language")
        assert segments_path == config.segments_path
        assert segment_text_field == config.segment_text_field
        assert segment_start_field == config.segment_start_field
        assert segment_end_field == config.segment_end_field
        assert segment_speaker_field == config.segment_speaker_field
        assert audio_bytes == make_test_wav_bytes(duration_seconds=1.0)
        assert filename == "recording.wav"
        assert content_type == "audio/wav"
        return "full file transcript"

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 42.5)

    from app.services.redaction import DeidentificationDetectionResult, Span

    detected_texts: list[str] = []

    def fake_detect_phi(db, *, provider, text, language, score_threshold, entities):
        detected_texts.append(text)
        start = text.index("full file")
        return DeidentificationDetectionResult(
            spans=[Span(start=start, end=start + len("full file"), entity_type="PERSON", score=0.99)],
            api_provider=provider.label,
            api_model_or_version="stub-model",
        )

    monkeypatch.setattr("app.services.redaction._detect_phi", fake_detect_phi)

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(uploaded.json()["job"]["id"])
    processed = process_transcript_ingestion_job(db_session, job_id=job_id)

    assert uploaded.status_code == 202
    assert processed.status is TranscriptIngestionJobStatus.applied
    refreshed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert refreshed_job is not None
    assert refreshed_job.source_audio_blob is None
    assert refreshed_job.source_audio_vault_ref is None
    assert refreshed_job.source_audio_size_bytes == len(b"raw-file-audio")
    assert refreshed_job.source_audio_duration_seconds == 42.5
    persisted = db_session.get(Transcript, UUID(transcript_id))
    assert persisted is not None
    assert is_encrypted_envelope(persisted.current_draft_text_encrypted)
    assert decrypt_transcript_draft(db_session, persisted) == "earlier transcript\nfull file transcript"
    assert persisted.status.value == "ready"
    versions = list(db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == UUID(transcript_id))))
    assert len(versions) == 1
    assert (
        decrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=versions[0].id,
            stored_value=versions[0].text_encrypted,
        )
        == "earlier transcript\nfull file transcript"
    )
    redaction_run = db_session.scalar(select(RedactionRun).where(RedactionRun.transcript_version_id == versions[0].id))
    assert redaction_run is not None
    assert redaction_run.entity_count == 1
    assert detected_texts == ["earlier transcript\nfull file transcript"]


def test_processing_audio_file_job_keeps_vault_ref_when_cleanup_delete_fails(client, db_session, make_team, make_user, make_stt_config, make_stt_selection, monkeypatch):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-cleanup-fail@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-cleanup-fail@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner-cleanup-fail@example.com", password="password-1")
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
        return NormalizedAudio(
            filename="recording.wav",
            content_type="audio/wav",
            data=make_test_wav_bytes(duration_seconds=1.0),
        )

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", lambda *args, **kwargs: "full file transcript")
    monkeypatch.setattr("app.services.transcripts.probe_audio_duration_seconds", lambda **kwargs: 42.5)
    monkeypatch.setattr(
        "app.services.transcripts.delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: (_ for _ in ()).throw(AppError(502, "vault_unavailable", "Vault is unavailable")),
    )

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(uploaded.json()["job"]["id"])
    processed = process_transcript_ingestion_job(db_session, job_id=job_id)

    assert processed.status is TranscriptIngestionJobStatus.applied
    refreshed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert refreshed_job is not None
    assert refreshed_job.source_audio_vault_ref is not None


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
        provider_preset,
        adapter_kind,
        base_url,
        transcribe_path,
        file_field_name,
        response_text_path,
        extra_form_fields_json,
        model_name,
        model_field_name,
        language,
        language_field_name,
        segments_path,
        segment_text_field,
        segment_start_field,
        segment_end_field,
        segment_speaker_field,
        audio_bytes,
        filename,
        content_type,
    ):
        assert team_id == team.id
        assert stt_config_id == config_one.id
        assert provider_preset == config_one.provider_preset
        assert base_url == config_one.base_url
        assert transcribe_path == config_one.transcribe_path
        assert model_name == "whisper-1"
        assert model_field_name == (config_one.model_field_name or "model")
        assert segments_path == config_one.segments_path
        assert segment_text_field == config_one.segment_text_field
        assert segment_start_field == config_one.segment_start_field
        assert segment_end_field == config_one.segment_end_field
        assert segment_speaker_field == config_one.segment_speaker_field
        return "snapshotted provider transcript"

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)

    processed = process_transcript_ingestion_job(db_session, job_id=job_id)
    assert processed.status is TranscriptIngestionJobStatus.applied
    refreshed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert refreshed_job is not None
    assert refreshed_job.stt_config_id == config_one.id
    assert refreshed_job.stt_provider_preset == config_one.provider_preset
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
    job = make_ingestion_job_for_transcript(
        transcript,
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

    processed = process_transcript_ingestion_job(db_session, job_id=job_id)

    failed_job = db_session.get(TranscriptIngestionJob, job_id)
    assert failed_job is not None
    assert processed.id == failed_job.id
    assert failed_job.status is TranscriptIngestionJobStatus.failed
    assert failed_job.error_code == "business_rule_violation"
    transcript = db_session.get(Transcript, UUID(transcript_id))
    assert transcript is not None
    assert transcript.status is TranscriptStatus.failed


def test_processing_audio_file_job_marks_failed_cleanly_when_stt_secret_is_missing(
    client,
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-secret-missing@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    admin = make_user(email="admin-secret-missing@example.com", password="password-2", is_system_admin=True)
    config = make_stt_config(team=team, actor=admin)
    make_stt_selection(config=config, actor=owner)

    login(client, email="owner-secret-missing@example.com", password="password-1")
    started = client.post(
        "/api/v1/transcripts/start",
        json={"title": "Imported visit", "ingestion_mode": "whole_file"},
    )
    transcript_id = started.json()["id"]

    def fake_normalize_audio_to_wav_16k_mono(*, audio_bytes, source_filename):
        return NormalizedAudio(
            filename="recording.wav",
            content_type="audio/wav",
            data=make_test_wav_bytes(duration_seconds=1.0),
        )

    def fake_transcribe_with_stt_snapshot(db, **kwargs):
        raise AppError(
            409,
            "stt_config_secret_missing",
            "The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
            {"team_id": str(team.id), "config_id": str(config.id)},
        )

    monkeypatch.setattr("app.services.transcripts.normalize_audio_to_wav_16k_mono", fake_normalize_audio_to_wav_16k_mono)
    monkeypatch.setattr("app.services.transcripts.transcribe_with_stt_snapshot", fake_transcribe_with_stt_snapshot)

    uploaded = client.post(
        f"/api/v1/transcripts/{transcript_id}/audio-file",
        files={"audio": ("recording.mp3", b"raw-file-audio", "audio/mpeg")},
    )
    job_id = UUID(uploaded.json()["job"]["id"])

    processed = process_transcript_ingestion_job(db_session, job_id=job_id)

    assert processed.status is TranscriptIngestionJobStatus.failed
    assert processed.error_code == "stt_config_secret_missing"
    assert processed.error_message == "The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth."
    assert processed.source_audio_blob is None
    assert processed.source_audio_vault_ref is not None
    assert processed.source_audio_size_bytes == len(b"raw-file-audio")
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


def test_retry_audio_file_route_enforces_owner_scope(client, db_session, make_team, make_user):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-retry-scope@example.com", password="password-1", team=team, team_role=TeamRole.user)
    other = make_user(email="other-retry-scope@example.com", password="password-2", team=team, team_role=TeamRole.user)

    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Retry scope",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.failed,
        retention_days_applied=30,
        retention_expires_at=owner.created_at,
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(
        make_ingestion_job_for_transcript(
            transcript,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename="recording.mp3",
            source_audio_blob=b"raw-file-audio",
            source_audio_size_bytes=len(b"raw-file-audio"),
            status=TranscriptIngestionJobStatus.failed,
        )
    )
    db_session.commit()

    unauthorized = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")
    assert_error(unauthorized, status_code=401, code="unauthorized", message="Authentication required")

    login(client, email="other-retry-scope@example.com", password="password-2")
    forbidden = client.post(f"/api/v1/transcripts/{transcript.id}/retry-audio-file")
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
        message="No active STT selection for team and purpose",
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

    def fake_read_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
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
    assert captured["timeout"] == STT_TRANSCRIPTION_TIMEOUT_SECONDS


def test_deepgram_model_discovery_uses_models_endpoint(monkeypatch):
    captured = {}

    def fake_get(url, *, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return FakeHttpxResponse(
            {
                "stt": [
                    {"name": "Nova 3", "canonical_name": "nova-3", "batch": True, "streaming": True},
                    {"name": "Streaming Only", "canonical_name": "stream-only", "batch": False, "streaming": True},
                ],
                "tts": [{"canonical_name": "aura-2"}],
            }
        )

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    models = _list_deepgram_stt_models(api_key="dg-secret", base_url="https://api.deepgram.com")

    assert models == ["nova-3"]
    assert captured["url"] == "https://api.deepgram.com/v1/models"
    assert captured["headers"] == {"Authorization": "Token dg-secret"}
    assert captured["timeout"] == 10.0


def test_deepgram_model_discovery_rejects_invalid_key(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeHttpxResponse({"error": "unauthorized"}, status_code=401)

    monkeypatch.setattr("app.services.stt.httpx.get", fake_get)

    with pytest.raises(AppError) as exc_info:
        _list_deepgram_stt_models(api_key="bad-key", base_url="https://api.deepgram.com")

    assert exc_info.value.code == "stt_credential_invalid"
    assert exc_info.value.status_code == 401


def test_deepgram_transcription_uses_query_params_and_raw_audio(monkeypatch):
    captured = {}

    def fake_post(url, *, headers=None, params=None, content=None, data=None, files=None, timeout=None):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "content": content,
                "data": data,
                "files": files,
                "timeout": timeout,
            }
        )
        return FakeHttpxResponse({"results": {"channels": [{"alternatives": [{"transcript": "hello from deepgram"}]}]}})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    result = _transcribe_via_http(
        base_url="https://api.deepgram.com",
        transcribe_path="/v1/listen",
        file_field_name="file",
        response_text_path="results.channels.0.alternatives.0.transcript",
        extra_form_fields_json={"smart_format": "true", "mip_opt_out": "false"},
        bearer_token="dg-secret",
        model_name="nova-3",
        model_field_name="model",
        language="en",
        language_field_name="language",
        audio_bytes=b"wav-bytes",
        filename="audio.wav",
        content_type="audio/wav",
        provider_preset=SttProviderPreset.deepgram.value,
    )

    assert result == "hello from deepgram"
    assert captured["url"] == "https://api.deepgram.com/v1/listen"
    assert captured["headers"] == {"Authorization": "Token dg-secret", "Content-Type": "audio/wav"}
    assert captured["params"] == {"smart_format": "true", "mip_opt_out": "true", "model": "nova-3", "language": "en"}
    assert captured["content"] == b"wav-bytes"
    assert captured["data"] is None
    assert captured["files"] is None
    assert captured["timeout"] == STT_TRANSCRIPTION_TIMEOUT_SECONDS


def test_transcribe_with_team_stt_deepgram_uses_raw_audio_transport(
    db_session, make_team, make_user, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-deepgram@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Deepgram STT",
        provider_preset=SttProviderPreset.deepgram.value,
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="https://api.deepgram.com",
        transcribe_path="/v1/listen",
        auth_mode=SttAuthMode.bearer,
        model_name="nova-3",
        model_field_name="model",
        file_field_name="file",
        language="en",
        language_field_name="language",
        response_text_path="results.channels.0.alternatives.0.transcript",
        extra_form_fields_json={"smart_format": "true"},
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    captured = {}

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "dg-secret")

    def fake_post(url, *, headers=None, params=None, content=None, data=None, files=None, timeout=None):
        captured.update({"headers": headers, "params": params, "content": content, "data": data, "files": files})
        return FakeHttpxResponse({"results": {"channels": [{"alternatives": [{"transcript": "recognized text"}]}]}})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_post)

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"normalized-audio",
        filename="chunk.wav",
        content_type="audio/wav",
    )

    assert text == "recognized text"
    assert captured["headers"]["Authorization"] == "Token dg-secret"
    assert captured["params"] == {"smart_format": "true", "mip_opt_out": "true", "model": "nova-3", "language": "en"}
    assert captured["content"] == b"normalized-audio"
    assert captured["data"] is None
    assert captured["files"] is None


def test_transcribe_with_team_stt_uses_saved_model_and_language_field_names(
    db_session, make_team, make_user, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-dynamic-stt@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Dynamic STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/speech/transcribe",
        auth_mode=SttAuthMode.bearer,
        model_name="clinic-whisper",
        model_field_name="model_id",
        file_field_name="audio_file",
        language="en-GB",
        language_field_name="lang",
        response_text_path="$.results[0].alternatives[0].transcript",
        extra_form_fields_json=None,
        vault_secret_ref="",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    captured = {}

    def fake_httpx_post(url, *, headers, data, files, timeout):
        captured["data"] = data
        captured["files"] = files
        return FakeHttpxResponse({"results": [{"alternatives": [{"transcript": "recognized text"}]}]})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_httpx_post)

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"normalized-audio",
        filename="chunk.wav",
        content_type="audio/wav",
    )

    assert text == "recognized text"
    assert captured["data"] == {"model_id": "clinic-whisper", "lang": "en-GB"}
    assert "model" not in captured["data"]
    assert "language" not in captured["data"]
    assert captured["files"]["audio_file"] == ("chunk.wav", b"normalized-audio", "audio/wav")


def test_transcribe_with_team_stt_openai_compatible_rest_allows_no_auth_config(
    db_session, make_team, make_user, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-no-auth@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Parakeet",
        adapter_kind=SttAdapterKind.openai_compatible_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        auth_mode=SttAuthMode.bearer,
        model_name="parakeet",
        file_field_name="file",
        language="en",
        response_text_path="text",
        extra_form_fields_json=None,
        vault_secret_ref="",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)

    captured = {}

    def fake_httpx_post(url, *, headers, data, files, timeout):
        captured["headers"] = headers
        return FakeHttpxResponse({"text": "recognized text"})

    monkeypatch.setattr("app.services.stt.httpx.post", fake_httpx_post)

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"normalized-audio",
        filename="chunk.wav",
        content_type="audio/wav",
    )

    assert text == "recognized text"
    assert captured["headers"] == {}


def test_transcribe_with_team_stt_invalid_json_log_redacts_provider_url(
    db_session, make_team, make_user, make_stt_selection, monkeypatch, caplog
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-invalid-json@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Signed URL STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://user:pass@stt.example:9000?token=secret",
        transcribe_path="/v1/audio/transcriptions",
        auth_mode=SttAuthMode.bearer,
        model_name="whisper-1",
        file_field_name="file",
        language="en",
        response_text_path="text",
        extra_form_fields_json=None,
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    caplog.set_level("WARNING", logger="openscribe.stt")

    class InvalidJsonResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: InvalidJsonResponse())

    try:
        transcribe_with_team_stt(
            db_session,
            team_id=team.id,
            audio_bytes=b"normalized-audio",
            filename="chunk.wav",
            content_type="audio/wav",
        )
    except AppError as exc:
        assert exc.code == "stt_response_invalid"
        log_record = next(record for record in caplog.records if record.message == "stt_http_response_invalid_json")
        assert log_record.stt_transport["url"] == "http://stt.example:9000"
        assert "user:pass" not in log_record.stt_transport["url"]
        assert "token=secret" not in log_record.stt_transport["url"]
    else:
        raise AssertionError("Expected invalid JSON STT response to raise an AppError")


def test_transcribe_with_team_stt_paragraphizes_timestamped_segments_when_present(
    db_session, make_team, make_user, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-segments@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Parakeet STT",
        adapter_kind=SttAdapterKind.openai_compatible_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        auth_mode=SttAuthMode.bearer,
        model_name="parakeet",
        file_field_name="file",
        language="en",
        response_text_path="text",
        extra_form_fields_json={"response_format": "verbose_json", "timestamps": "segment"},
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")
    monkeypatch.setattr(
        "app.services.stt.httpx.post",
        lambda *args, **kwargs: FakeHttpxResponse(
            {
                "text": "flat transcript that should not win",
                "segments": [
                    {"start": 0.78, "end": 2.54, "text": "This is my voice.", "speaker": "UNKNOWN", "id": 0},
                    {"start": 4.7, "end": 13.18, "text": "For those who want to install the simple audio recorder application in Ubuntu and Ubuntu 24.04, here's the new Ubuntu PPA.", "speaker": "UNKNOWN", "id": 1},
                    {"start": 13.42, "end": 14.14, "text": "Update.", "speaker": "UNKNOWN", "id": 2},
                ],
            }
        ),
    )

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"normalized-audio",
        filename="chunk.wav",
        content_type="audio/wav",
    )

    assert text == "This is my voice.\n\nFor those who want to install the simple audio recorder application in Ubuntu and Ubuntu 24.04, here's the new Ubuntu PPA. Update."


def test_transcribe_with_team_stt_uses_configured_segment_fields(
    db_session, make_team, make_user, make_stt_selection, monkeypatch
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-custom-segments@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Custom STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/speech/transcribe",
        auth_mode=SttAuthMode.bearer,
        model_name="clinic-whisper",
        model_field_name="model_id",
        file_field_name="audio_file",
        language="en",
        language_field_name="lang",
        response_text_path="transcript",
        segments_path="result.utterances",
        segment_text_field="transcript",
        segment_start_field="start_time",
        segment_end_field="end_time",
        segment_speaker_field="speaker_id",
        extra_form_fields_json={},
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")
    monkeypatch.setattr(
        "app.services.stt.httpx.post",
        lambda *args, **kwargs: FakeHttpxResponse(
            {
                "transcript": "flat transcript that should not win",
                "result": {
                    "utterances": [
                        {"start_time": 0.0, "end_time": 1.0, "transcript": "Mapped field one.", "speaker_id": "A"},
                        {"start_time": 1.2, "end_time": 2.0, "transcript": "Mapped field two.", "speaker_id": "A"},
                    ]
                },
            }
        ),
    )

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"normalized-audio",
        filename="chunk.wav",
        content_type="audio/wav",
    )

    assert text == "Mapped field one. Mapped field two."


def test_transcribe_with_stt_snapshot_supports_old_and_new_snapshot_fields(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-snapshot@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = make_stt_config(
        team=team,
        actor=owner,
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        file_field_name="file",
        response_text_path="text",
        model_name="default-model",
        language="en",
    )
    make_stt_selection(config=config, actor=owner)
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")

    def fake_http_transcribe(**kwargs):
        captured.update(kwargs)
        return "snapshot transcript"

    monkeypatch.setattr("app.services.stt._transcribe_via_http", fake_http_transcribe)

    fallback_text = transcribe_with_stt_snapshot(
        db_session,
        team_id=team.id,
        stt_config_id=None,
        adapter_kind=None,
        base_url=None,
        transcribe_path=None,
        file_field_name=None,
        response_text_path=None,
        extra_form_fields_json=None,
        model_name=None,
        language=None,
        audio_bytes=b"audio",
        filename="old.wav",
        content_type="audio/wav",
    )
    assert fallback_text == "snapshot transcript"
    assert captured["model_field_name"] == (config.model_field_name or "model")

    captured.clear()
    snapshot_text = transcribe_with_stt_snapshot(
        db_session,
        team_id=team.id,
        stt_config_id=config.id,
        adapter_kind=SttAdapterKind.generic_rest.value,
        base_url="https://api.deepgram.com",
        transcribe_path="/speech/transcribe",
        file_field_name="audio_file",
        response_text_path="transcript",
        extra_form_fields_json={},
        model_name="clinic-whisper",
        language="en-GB",
        audio_bytes=b"audio",
        filename="new.wav",
        content_type="audio/wav",
        model_field_name="model_id",
        language_field_name="lang",
        segments_path="result.utterances",
        segment_text_field="transcript",
        segment_start_field="start_time",
        segment_end_field="end_time",
        segment_speaker_field="speaker_id",
    )

    assert snapshot_text == "snapshot transcript"
    assert captured["provider_preset"] == SttProviderPreset.custom_rest_openapi.value
    assert captured["model_field_name"] == "model_id"
    assert captured["language_field_name"] == "lang"
    assert captured["file_field_name"] == "audio_file"
    assert captured["segments_path"] == "result.utterances"
    assert captured["segment_text_field"] == "transcript"
    assert captured["segment_start_field"] == "start_time"
    assert captured["segment_end_field"] == "end_time"
    assert captured["segment_speaker_field"] == "speaker_id"

    captured.clear()
    explicit_preset_text = transcribe_with_stt_snapshot(
        db_session,
        team_id=team.id,
        stt_config_id=config.id,
        adapter_kind=SttAdapterKind.generic_rest.value,
        base_url="https://api.deepgram.com",
        transcribe_path="/v1/listen",
        file_field_name="file",
        response_text_path="results.channels.0.alternatives.0.transcript",
        extra_form_fields_json={},
        model_name="nova-3",
        language="en-GB",
        audio_bytes=b"audio",
        filename="deepgram.wav",
        content_type="audio/wav",
        provider_preset=SttProviderPreset.deepgram.value,
    )

    assert explicit_preset_text == "snapshot transcript"
    assert captured["provider_preset"] == SttProviderPreset.deepgram.value


def test_transcribe_with_team_stt_deepgram_host_preserves_openai_adapter(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Clinical Team OpenAI Host")
    owner = make_user(email="owner-openai-deepgram-host@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = make_stt_config(
        team=team,
        actor=owner,
        adapter_kind=SttAdapterKind.openai_cloud,
        base_url="https://api.deepgram.com",
        transcribe_path="/v1/audio/transcriptions",
        file_field_name="file",
        response_text_path="text",
        model_name="whisper-1",
        language="en",
    )
    make_stt_selection(config=config, actor=owner)
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "openai-token")

    def fake_openai_transcribe(**kwargs):
        captured.update(kwargs)
        return "openai transcript"

    def fail_http_transcribe(**kwargs):  # pragma: no cover
        raise AssertionError("Deepgram/generic HTTP transport should not be used")

    monkeypatch.setattr("app.services.stt._transcribe_via_openai_cloud", fake_openai_transcribe)
    monkeypatch.setattr("app.services.stt._transcribe_via_http", fail_http_transcribe)

    text = transcribe_with_team_stt(
        db_session,
        team_id=team.id,
        audio_bytes=b"audio",
        filename="openai.wav",
        content_type="audio/wav",
    )

    assert text == "openai transcript"
    assert captured["base_url"] == "https://api.deepgram.com"
    assert captured["model_name"] == "whisper-1"


def test_transcribe_with_team_stt_generic_rest_surfaces_connect_errors_cleanly(
    db_session, make_team, make_user, make_stt_selection, monkeypatch, caplog
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-connect@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Compatible STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        auth_mode=SttAuthMode.bearer,
        model_name="whisper-1",
        file_field_name="file",
        language="en",
        response_text_path="result.text",
        extra_form_fields_json=None,
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    caplog.set_level("WARNING", logger="openscribe.stt")

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")

    def fake_httpx_post(*args, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", "http://127.0.0.1:9000/v1/audio/transcriptions"))

    monkeypatch.setattr("app.services.stt.httpx.post", fake_httpx_post)

    try:
        transcribe_with_team_stt(
            db_session,
            team_id=team.id,
            audio_bytes=b"normalized-audio",
            filename="chunk.wav",
            content_type="audio/wav",
        )
    except AppError as exc:
        assert exc.code == "stt_unavailable"
        assert exc.message == "Could not reach the STT provider"
        assert exc.details == {"provider_error_code": "connection_error"}
        assert "stt_http_request_failed" in caplog.text
        log_record = next(record for record in caplog.records if record.message == "stt_http_request_failed")
        assert log_record.stt_transport["provider_error_code"] == "connection_error"
        assert log_record.stt_transport["error_type"] == "ConnectError"
        assert log_record.stt_transport["url"] == "http://127.0.0.1:9000/v1/audio/transcriptions"
        assert log_record.stt_transport["audio_byte_count"] == len(b"normalized-audio")
        assert log_record.stt_transport["form_field_keys"] == ["language", "model"]
    else:
        raise AssertionError("Expected generic REST STT connect failure to raise an AppError")


def test_transcribe_with_team_stt_generic_rest_surfaces_http_status_failures_cleanly(
    db_session, make_team, make_user, make_stt_selection, monkeypatch, caplog
):
    team = make_team(name="Clinical Team")
    owner = make_user(email="owner-http-status@example.com", password="password-1", team=team, team_role=TeamRole.leader)
    config = TeamSttConfig(
        team_id=team.id,
        label="Compatible STT",
        adapter_kind=SttAdapterKind.generic_rest,
        base_url="http://127.0.0.1:9000",
        transcribe_path="/v1/audio/transcriptions",
        auth_mode=SttAuthMode.bearer,
        model_name="whisper-1",
        file_field_name="file",
        language="en",
        response_text_path="result.text",
        extra_form_fields_json=None,
        vault_secret_ref="secret:openscribe/stt/team/test/config/test",
        is_active=True,
        created_by_user_id=owner.id,
        updated_by_user_id=owner.id,
    )
    db_session.add(config)
    db_session.commit()
    make_stt_selection(config=config, actor=owner)
    caplog.set_level("WARNING", logger="openscribe.stt")

    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", lambda **kwargs: "secret-token")
    monkeypatch.setattr("app.services.stt.httpx.post", lambda *args, **kwargs: FakeHttpxResponse({"error": "bad request"}, status_code=503))

    try:
        transcribe_with_team_stt(
            db_session,
            team_id=team.id,
            audio_bytes=b"normalized-audio",
            filename="chunk.wav",
            content_type="audio/wav",
        )
    except AppError as exc:
        assert exc.code == "stt_request_failed"
        assert exc.message == "STT provider request failed"
        assert exc.details == {
            "status_code": 503,
            "provider_status_code": 503,
            "provider_error_code": "http_status_error",
        }
        assert "stt_http_request_failed" in caplog.text
        log_record = next(record for record in caplog.records if record.message == "stt_http_request_failed")
        assert log_record.stt_transport["provider_error_code"] == "http_status_error"
        assert log_record.stt_transport["status_code"] == 503
        assert log_record.stt_transport["url"] == "http://testserver.local/stt"
    else:
        raise AssertionError("Expected generic REST STT HTTP status failure to raise an AppError")
