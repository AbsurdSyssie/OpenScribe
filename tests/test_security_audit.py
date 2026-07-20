from pathlib import Path

from sqlalchemy import select

from app.models import SecurityAuditEvent, Team, TeamRole, TeamStatus
from app.schemas import UserCreate
from app.schemas.preferences import UserAppPreferencesUpsert
from app.schemas.smart_phrases import SmartPhraseCreate, SmartPhraseUpdate
from app.schemas.templates import DefaultPromptTemplateUpsert, PromptTemplateUpsert
from app.schemas.transcripts import TranscriptStart
from app.services.admin import create_user, delete_team, reactivate_user, suspend_user
from app.services.default_assets import delete_default_template, upsert_default_template
from app.services.preferences import clear_user_app_preferences, set_user_app_preferences
from app.services.security_audit import (
    AUDIT_TRUNCATION_MARKER,
    MAX_AUDIT_STRING_LENGTH,
    _safe_string,
    audit_subject_hash,
    audit_subject_hash_secret_configured_for_environment,
    record_security_event,
)
from app.services.smart_phrases import create_personal_smart_phrase, delete_personal_smart_phrase, update_personal_smart_phrase
from app.services.templates import queue_document_generation_from_template, upsert_personal_template
from app.services.transcripts import delete_transcripts, queue_audio_file_ingestion, start_transcript
from app.models import TemplateMode, TemplateScope, TranscriptIngestionMode, UserStatus


def _audit_events(db_session, action: str) -> list[SecurityAuditEvent]:
    return list(db_session.scalars(select(SecurityAuditEvent).where(SecurityAuditEvent.action == action).order_by(SecurityAuditEvent.created_at.asc())))


def test_raw_browser_capture_artifacts_are_not_tracked_in_prototype_tree():
    prototype_root = Path("transcriber_changes")

    assert not (prototype_root / "network_requests.json").exists()
    assert not (prototype_root / "page.html").exists()
    assert not (prototype_root / "index.html").exists()
    assert not (prototype_root / "screenshot.png").exists()
    assert not any((prototype_root / "css").glob("*"))


def test_record_security_event_redacts_nested_sensitive_values_and_sanitizes_strings(db_session):
    record_security_event(
        db_session,
        action="audit_sanitization_probe",
        details={
            "category": "auth",
            "safe": "line1\nline2",
            "password": "secret-password",
            "nested": {
                "api_key": "secret-api-key",
                "kept": "ok\rvalue",
                "items": [{"token": "secret-token", "value": "visible"}],
            },
        },
    )

    event = _audit_events(db_session, "audit_sanitization_probe")[0]
    assert event.details_json["safe"] == "line1\\nline2"
    assert "password" not in event.details_json
    assert "api_key" not in event.details_json["nested"]
    assert event.details_json["nested"]["kept"] == "ok\\rvalue"
    assert "token" not in event.details_json["nested"]["items"][0]
    assert event.details_json["nested"]["items"][0]["value"] == "visible"
    assert "secret" not in str(event.details_json)


def test_audit_safe_string_bounds_truncation_marker():
    exact = "x" * MAX_AUDIT_STRING_LENGTH
    one_over = "x" * (MAX_AUDIT_STRING_LENGTH + 1)
    newline_expanded = "x" * (MAX_AUDIT_STRING_LENGTH - 5) + "\n" + "y" * 10

    assert _safe_string(exact) == exact
    assert len(_safe_string(one_over)) == MAX_AUDIT_STRING_LENGTH
    assert _safe_string(one_over).endswith(AUDIT_TRUNCATION_MARKER)
    assert len(_safe_string(newline_expanded)) == MAX_AUDIT_STRING_LENGTH
    assert _safe_string(newline_expanded).endswith(AUDIT_TRUNCATION_MARKER)


def test_record_security_event_does_not_commit_unrelated_pending_changes(db_session):
    team = Team(name="Pending Audit Team", name_key="pending-audit-team", status=TeamStatus.active)
    db_session.add(team)

    record_security_event(db_session, action="audit_transaction_probe", details={"category": "auth"})
    db_session.rollback()

    assert _audit_events(db_session, "audit_transaction_probe")
    assert db_session.scalar(select(Team).where(Team.name == "Pending Audit Team")) is None


def test_record_security_event_write_failure_is_best_effort(db_session, monkeypatch):
    def failing_sessionmaker(*args, **kwargs):
        class FailingSession:
            def __enter__(self):
                raise RuntimeError("audit database unavailable")

            def __exit__(self, exc_type, exc, traceback):
                return False

        return FailingSession

    monkeypatch.setattr("app.services.security_audit.sessionmaker", failing_sessionmaker)

    record_security_event(db_session, action="audit_failure_probe", details={"category": "auth"})


def test_api_login_success_and_failure_are_durable_metadata_only(client, db_session, make_user):
    user = make_user(email="audit-login@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    failed = client.post("/api/v1/auth/login", json={"email": "audit-login@example.com", "password": "wrong-password"})
    succeeded = client.post("/api/v1/auth/login", json={"email": "audit-login@example.com", "password": "password-1"})

    assert failed.status_code == 401
    assert succeeded.status_code == 200
    failure = _audit_events(db_session, "login_failure")[0]
    success = _audit_events(db_session, "login_success")[0]
    assert failure.details_json["category"] == "auth"
    assert failure.details_json["outcome"] == "failure"
    assert failure.details_json["subject_hash"] == audit_subject_hash("audit-login@example.com")
    assert "audit-login@example.com" not in str(failure.details_json)
    assert "wrong-password" not in str(failure.details_json)
    assert success.actor_user_id == user.id
    assert success.target_user_id == user.id
    assert success.details_json["auth_level"] == "full"


def test_invalid_email_token_failure_is_audited_without_raw_token(client, db_session):
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "raw-reset-token-secret", "new_password": "NewPassword123"},
    )

    event = _audit_events(db_session, "auth_email_token_failure")[0]
    assert response.status_code == 422
    assert event.details_json["flow"] == "password_reset"
    assert event.details_json["reason_code"] == "token_invalid"
    assert "raw-reset-token-secret" not in str(event.details_json)
    assert "NewPassword123" not in str(event.details_json)


def test_cloudflare_origin_ip_is_env_gated(client, db_session, make_user, monkeypatch):
    make_user(email="audit-ip@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    ignored = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-ip@example.com", "password": "wrong-password"},
        headers={"CF-Connecting-IP": "203.0.113.10"},
    )
    monkeypatch.setenv("AUDIT_TRUST_CLOUDFLARE", "true")
    trusted = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-ip@example.com", "password": "wrong-password"},
        headers={"CF-Connecting-IP": "203.0.113.11"},
    )

    assert ignored.status_code == 401
    assert trusted.status_code == 401
    failures = _audit_events(db_session, "login_failure")
    assert failures[0].request_ip != "203.0.113.10"
    assert failures[1].request_ip == "203.0.113.11"


def test_trusted_audit_origin_ip_is_bounded(client, db_session, make_user, monkeypatch):
    monkeypatch.setenv("AUDIT_TRUST_CLOUDFLARE", "true")
    make_user(email="audit-long-ip@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-long-ip@example.com", "password": "wrong-password"},
        headers={"CF-Connecting-IP": "x" * 500},
    )

    event = _audit_events(db_session, "login_failure")[0]
    assert response.status_code == 401
    assert event.request_ip is not None
    assert len(event.request_ip) <= 255


def test_long_user_agent_is_persisted_with_bounded_truncation(client, db_session, make_user):
    make_user(email="audit-long-ua@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-long-ua@example.com", "password": "wrong-password"},
        headers={"User-Agent": "browser" + "x" * 2000},
    )

    event = _audit_events(db_session, "login_failure")[0]
    assert response.status_code == 401
    assert event.user_agent is not None
    assert len(event.user_agent) <= MAX_AUDIT_STRING_LENGTH
    assert event.user_agent.endswith(AUDIT_TRUNCATION_MARKER)


def test_audit_subject_hash_uses_keyed_hmac(monkeypatch):
    monkeypatch.setenv("AUDIT_SUBJECT_HASH_SECRET", "first-secret")
    first = audit_subject_hash("person@example.com")
    monkeypatch.setenv("AUDIT_SUBJECT_HASH_SECRET", "second-secret")
    second = audit_subject_hash("person@example.com")

    assert first is not None and first.startswith("hmac-sha256:")
    assert first != second


def test_audit_subject_hash_explicit_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUDIT_SUBJECT_HASH_SECRET", "audit-secret")
    monkeypatch.setenv("SECRET_KEY", "app-secret")

    override_hash = audit_subject_hash("person@example.com")
    monkeypatch.delenv("AUDIT_SUBJECT_HASH_SECRET")
    app_hash = audit_subject_hash("person@example.com")

    assert override_hash != app_hash


def test_audit_subject_hash_uses_explicit_application_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AUDIT_SUBJECT_HASH_SECRET", raising=False)
    monkeypatch.setenv("SECRET_KEY", "first-app-secret")
    first = audit_subject_hash("person@example.com")
    monkeypatch.setenv("SECRET_KEY", "second-app-secret")
    second = audit_subject_hash("person@example.com")

    assert first != second


def test_audit_subject_hash_uses_vault_secret_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AUDIT_SUBJECT_HASH_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("CSRF_SECRET", raising=False)
    monkeypatch.setattr("app.services.vault.get_or_create_platform_csrf_secret", lambda: "vault-secret")
    vault_hash = audit_subject_hash("person@example.com")
    monkeypatch.setenv("CSRF_SECRET", "different-secret")
    explicit_hash = audit_subject_hash("person@example.com")

    assert vault_hash != explicit_hash


def test_audit_subject_hash_secret_check_fails_closed_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AUDIT_SUBJECT_HASH_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("CSRF_SECRET", raising=False)
    monkeypatch.setattr("app.services.vault.get_or_create_platform_csrf_secret", lambda: (_ for _ in ()).throw(RuntimeError("vault down")))

    try:
        audit_subject_hash_secret_configured_for_environment()
    except RuntimeError as exc:
        assert "Vault-backed CSRF secret" in str(exc)
    else:
        raise AssertionError("Expected production audit secret check to fail")


def test_audit_subject_hash_local_fallback_is_dev_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AUDIT_SUBJECT_HASH_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("CSRF_SECRET", raising=False)

    assert audit_subject_hash("person@example.com") is not None


def test_large_audit_details_are_bounded(db_session):
    record_security_event(
        db_session,
        action="audit_large_details_probe",
        details={"category": "auth", "items": [{"value": "x" * 1024} for _ in range(200)]},
    )

    event = _audit_events(db_session, "audit_large_details_probe")[0]
    assert len(str(event.details_json)) < 9000


def test_csrf_rejection_is_audited_without_token_or_cookie(raw_client, db_session, make_user):
    make_user(email="audit-csrf@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert raw_client.post("/api/v1/auth/login", json={"email": "audit-csrf@example.com", "password": "password-1"}).status_code == 200

    rejected = raw_client.post(
        "/api/v1/transcripts/start",
        json={"title": "CSRF audit", "ingestion_mode": "whole_file"},
        headers={"Origin": "http://testserver"},
    )

    event = _audit_events(db_session, "csrf_rejected")[0]
    assert rejected.status_code == 403
    assert event.details_json["category"] == "csrf"
    assert event.details_json["reason_code"] == "invalid_or_missing_token"
    assert event.details_json["auth_authority_present"] is True
    assert "openscribe_session" not in str(event.details_json)
    assert raw_client.cookies.get("openscribe_session") not in str(event.details_json)


def test_access_denial_is_audited(client, db_session, make_user):
    user = make_user(email="audit-denied@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    assert client.post("/api/v1/auth/login", json={"email": "audit-denied@example.com", "password": "password-1"}).status_code == 200

    denied = client.get("/api/v1/teams")

    event = _audit_events(db_session, "access_denied")[0]
    assert denied.status_code == 403
    assert event.actor_user_id == user.id
    assert event.details_json["category"] == "access_control"
    assert event.details_json["reason_code"] == "system_admin_required"


def test_rate_limit_exceeded_is_audited(client, db_session, make_user):
    make_user(email="audit-rate-limit@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    responses = [
        client.post(
            "/api/v1/auth/login",
            json={"email": "audit-rate-limit@example.com", "password": f"wrong-password-{attempt}"},
        )
        for attempt in range(6)
    ]

    event = _audit_events(db_session, "rate_limit_exceeded")[0]
    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429
    assert event.details_json["category"] == "rate_limit"
    assert event.details_json["status_code"] == 429
    assert "wrong-password" not in str(event.details_json)


def test_security_relevant_validation_failure_is_audited_without_payload_value(client, db_session, make_user):
    make_user(email="audit-validation-admin@example.com", password="password-1", is_system_admin=True)
    assert client.post("/api/v1/auth/login", json={"email": "audit-validation-admin@example.com", "password": "password-1"}).status_code == 200

    response = client.post(
        "/api/v1/deidentification-providers/inspect",
        json={
            "label": "Audit deid",
            "base_url": "https://deid.example.com",
            "detect_path": "/detect",
            "extra_headers_json": {"Authorization": "Bearer raw-secret-header"},
        },
    )

    event = _audit_events(db_session, "security_validation_rejected")[0]
    assert response.status_code == 422
    assert event.details_json["category"] == "validation"
    assert event.details_json["reason_code"] == "security_relevant_validation"
    assert event.details_json["issues"][0]["field"].endswith("extra_headers_json")
    assert "raw-secret-header" not in str(event.details_json)


def test_team_delete_blocker_is_audited(db_session, make_user, make_team):
    team = make_team(name="Audit Blocked Team")
    admin = make_user(email="audit-delete-admin@example.com", is_system_admin=True)
    linked_admin = make_user(email="audit-linked-admin@example.com", is_system_admin=True)
    linked_admin.team_id = team.id
    db_session.add(linked_admin)
    db_session.commit()

    try:
        delete_team(db_session, admin, team_id=team.id)
    except Exception as exc:
        assert getattr(exc, "code", None) == "conflict"
    else:
        raise AssertionError("Expected team delete to be blocked")

    event = _audit_events(db_session, "team_delete_blocked")[0]
    assert event.actor_user_id == admin.id
    assert event.team_id == team.id
    assert event.details_json["reason_code"] == "team_contains_system_admin"
    assert event.details_json["blocked_user_id"] == str(linked_admin.id)


def test_account_lifecycle_events_are_persisted(db_session, make_user, make_team):
    team = make_team(name="Audit Team")
    admin = make_user(email="audit-admin@example.com", is_system_admin=True)
    user = create_user(
        db_session,
        UserCreate(
            full_name="Audited User",
            email="audited-user@example.com",
            temporary_password="temporary-password-1",
            team_id=team.id,
            team_role=TeamRole.user,
            is_system_admin=False,
        ),
        actor=admin,
    )

    suspend_user(db_session, admin, user.id)
    reactivate_user(db_session, admin, user.id)

    created = _audit_events(db_session, "user_created")[0]
    suspended = _audit_events(db_session, "account_suspended")[0]
    reactivated = _audit_events(db_session, "account_reactivated")[0]
    assert created.actor_user_id == admin.id
    assert created.target_user_id == user.id
    assert created.details_json["target_user_id"] == str(user.id)
    assert "temporary-password-1" not in str(created.details_json)
    assert suspended.details_json["target_status"] == UserStatus.suspended.value
    assert reactivated.details_json["target_status"] == UserStatus.active.value


def test_template_audit_excludes_prompt_text(db_session, make_user):
    actor = make_user(email="audit-template@example.com", mfa_required=False, mfa_enabled=False)
    payload = PromptTemplateUpsert(
        scope=TemplateScope.user,
        name="Audit Template",
        description="metadata only",
        prompt_text="Prompt must not enter audit log. Patient: Jane Secret.",
        mode=TemplateMode.freeform,
        is_active=True,
    )

    template = upsert_personal_template(db_session, actor, payload)

    event = _audit_events(db_session, "template_created")[0]
    assert event.actor_user_id == actor.id
    assert event.details_json["object_id"] == str(template.id)
    assert event.details_json["scope"] == TemplateScope.user.value
    assert "Jane Secret" not in str(event.details_json)
    assert "Prompt must not enter" not in str(event.details_json)


def test_user_preferences_and_smart_phrase_audit_exclude_phrase_content(db_session, make_user):
    actor = make_user(email="audit-phrase@example.com", mfa_required=False, mfa_enabled=False)

    preference = set_user_app_preferences(
        db_session,
        actor,
        UserAppPreferencesUpsert(preferred_recording_mode=TranscriptIngestionMode.whole_file, preferred_transcribe_tab="output"),
    )
    phrase = create_personal_smart_phrase(
        db_session,
        actor,
        SmartPhraseCreate(trigger="SAFE", expansion_text="Patient Jane Secret must not be audited", description="Private description"),
    )
    update_personal_smart_phrase(db_session, actor, smart_phrase_id=phrase.id, payload=SmartPhraseUpdate(expansion_text="Updated private content"))
    delete_personal_smart_phrase(db_session, actor, smart_phrase_id=phrase.id)
    clear_user_app_preferences(db_session, actor)

    preference_event = _audit_events(db_session, "user_app_preferences_set")[0]
    created = _audit_events(db_session, "smart_phrase_created")[0]
    updated = _audit_events(db_session, "smart_phrase_updated")[0]
    deleted = _audit_events(db_session, "smart_phrase_deleted")[0]
    cleared = _audit_events(db_session, "user_app_preferences_cleared")[0]
    assert preference_event.details_json["object_id"] == str(preference.id)
    assert preference_event.details_json["keys"] == ["preferred_recording_mode", "preferred_transcribe_tab"]
    assert created.details_json["object_id"] == str(phrase.id)
    assert updated.details_json["updated_fields"] == ["expansion_text"]
    assert deleted.details_json["object_id"] == str(phrase.id)
    assert cleared.details_json["object_id"] == str(preference.id)
    combined = " ".join(str(event.details_json) for event in [created, updated, deleted])
    assert "Jane Secret" not in combined
    assert "Private description" not in combined
    assert "Updated private content" not in combined


def test_default_asset_audit_excludes_prompt_text(db_session, make_user):
    admin = make_user(email="audit-default-admin@example.com", is_system_admin=True)

    template = upsert_default_template(
        db_session,
        admin,
        DefaultPromptTemplateUpsert(
            name="Audited default",
            description="Default description",
            prompt_text="Prompt includes Jane Secret and must stay out",
            mode=TemplateMode.freeform,
            is_active=True,
        ),
    )
    upsert_default_template(
        db_session,
        admin,
        DefaultPromptTemplateUpsert(
            template_id=template.id,
            name="Audited default updated",
            description="Updated description",
            prompt_text="Updated prompt includes John Secret and must stay out",
            mode=TemplateMode.freeform,
            is_active=False,
        ),
    )
    delete_default_template(db_session, admin, template_id=template.id)

    created = _audit_events(db_session, "default_template_created")[0]
    updated = _audit_events(db_session, "default_template_updated")[0]
    deleted = _audit_events(db_session, "default_template_deleted")[0]
    assert created.details_json["object_id"] == str(template.id)
    assert updated.details_json["active"] is False
    assert deleted.details_json["object_id"] == str(template.id)
    combined = " ".join(str(event.details_json) for event in [created, updated, deleted])
    assert "Jane Secret" not in combined
    assert "John Secret" not in combined
    assert "Default description" not in combined


def test_generation_queue_audit_excludes_prompt_and_transcript_content(
    db_session,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
):
    team = make_team(name="Audit Generation")
    owner = make_user(email="audit-generation@example.com", team=team, mfa_required=False, mfa_enabled=False)
    config = make_llm_config(team=team, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    template = make_template(owner=owner, team=team, prompt_text="Prompt mentions Jane Secret")
    transcript = start_transcript(
        db_session,
        owner,
        TranscriptStart(title="Private title", ingestion_mode=TranscriptIngestionMode.whole_file, current_draft_text_encrypted="Transcript says John Secret"),
    )

    document = queue_document_generation_from_template(db_session, owner, transcript_id=transcript.id, template_id=template.id)

    event = _audit_events(db_session, "generation_queued")[0]
    assert event.details_json["object_id"] == str(document.id)
    assert event.details_json["template_id"] == str(template.id)
    assert event.details_json["transcript_id"] == str(transcript.id)
    assert "Jane Secret" not in str(event.details_json)
    assert "John Secret" not in str(event.details_json)
    assert "Private title" not in str(event.details_json)


def test_audio_ingestion_queue_audit_excludes_filename_and_audio_content(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    monkeypatch,
):
    team = make_team(name="Audit Upload")
    owner = make_user(email="audit-upload@example.com", team=team, mfa_required=False, mfa_enabled=False)
    config = make_stt_config(team=team, available_models_json=["whisper-1"])
    make_stt_selection(config=config, actor=owner)
    transcript = start_transcript(db_session, owner, TranscriptStart(title="Upload title", ingestion_mode=TranscriptIngestionMode.whole_file))
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **kwargs: 4.25)

    _, job = queue_audio_file_ingestion(
        db_session,
        owner,
        transcript_id=transcript.id,
        filename="patient-jane-secret.wav",
        source_audio_blob=b"fake-audio-secret",
    )

    event = _audit_events(db_session, "audio_ingestion_queued")[0]
    assert event.details_json["object_id"] == str(job.id)
    assert event.details_json["job_kind"] == "audio_file"
    assert event.details_json["source_audio_size_bytes"] == len(b"fake-audio-secret")
    assert "patient-jane-secret.wav" not in str(event.details_json)
    assert "fake-audio-secret" not in str(event.details_json)


def test_transcript_delete_audit_excludes_transcript_content(db_session, make_user):
    owner = make_user(email="audit-transcript@example.com", mfa_required=False, mfa_enabled=False)
    transcript = start_transcript(
        db_session,
        owner,
        TranscriptStart(title="Synthetic title with possible patient name", ingestion_mode=TranscriptIngestionMode.whole_file),
    )

    deleted = delete_transcripts(db_session, owner, transcript_ids=[transcript.id])

    event = _audit_events(db_session, "transcript_root_deleted")[0]
    assert deleted == 1
    assert event.actor_user_id == owner.id
    assert event.details_json["object_ids"] == [str(transcript.id)]
    assert event.details_json["deleted_count"] == 1
    assert "Synthetic title" not in str(event.details_json)
