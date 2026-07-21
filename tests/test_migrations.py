import logging
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.db_utils import ensure_database_exists, ensure_safe_test_database_url


TEST_DATABASE_URL = ensure_safe_test_database_url()
ensure_database_exists(TEST_DATABASE_URL)
engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)


def alembic_config() -> Config:
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DATABASE_URL
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def reset_public_schema() -> None:
    engine.dispose()
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))


def current_tables() -> set[str]:
    inspector = inspect(engine)
    return set(inspector.get_table_names())


@pytest.mark.migration
def test_alembic_upgrade_keeps_application_loggers_enabled():
    reset_public_schema()
    stt_logger = logging.getLogger("openscribe.stt")
    stt_logger.disabled = False

    command.upgrade(alembic_config(), "head")

    assert stt_logger.disabled is False


@pytest.mark.migration
def test_alembic_upgrade_head_creates_expected_schema_and_provider_config_revisions():
    reset_public_schema()

    command.upgrade(alembic_config(), "head")

    assert current_tables() == {
        "alembic_version",
        "auth_email_tokens",
        "account_requests",
        "clinical_entities",
        "clinical_entity_runs",
        "default_quick_actions",
        "default_quick_action_versions",
        "default_templates",
        "default_template_versions",
        "deidentification_providers",
        "generated_document_sections",
        "generated_documents",
        "post_consultation_dictation_segments",
        "post_consultation_dictations",
        "provider_attempts",
        "provider_secret_cleanup_jobs",
        "provider_usage_events",
        "quick_actions",
        "quick_action_versions",
        "redaction_entities",
        "redaction_runs",
        "security_audit_events",
        "smart_phrases",
        "transcript_manual_pii_entities",
        "teams",
        "team_deidentification_provider_assignments",
        "team_deidentification_selections",
        "team_clinical_nlp_selections",
        "team_llm_configs",
        "team_hallucination_check_selections",
        "team_llm_selections",
        "team_stt_configs",
        "team_stt_selections",
        "template_versions",
        "templates",
        "users",
        "user_llm_preferences",
        "user_encryption_keys",
        "user_app_preferences",
        "user_quota_policy_events",
        "user_trusted_devices",
        "user_sessions",
        "user_mfa_methods",
        "user_recovery_codes",
        "transcripts",
        "transcript_audio_cleanup_jobs",
        "transcript_ingestion_jobs",
        "transcript_versions",
        "task_dispatch_outbox",
    }
    audit_indexes = {index["name"] for index in inspect(engine).get_indexes("security_audit_events")}
    assert "ix_security_audit_events_created_at" in audit_indexes
    inspector = inspect(engine)
    for table in ("team_stt_configs", "team_llm_configs"):
        assert "revision_of_config_id" in {column["name"] for column in inspector.get_columns(table)}
        indexes = {index["name"]: index for index in inspector.get_indexes(table)}
        assert indexes[f"uq_{table}_team_label_lower"]["unique"] is True
        assert "revision_of_config_id IS NULL" in indexes[f"uq_{table}_team_label_lower"]["dialect_options"]["postgresql_where"]
        assert indexes[f"uq_{table}_pending_revision"]["unique"] is True
        assert "revision_of_config_id IS NOT NULL" in indexes[f"uq_{table}_pending_revision"]["dialect_options"]["postgresql_where"]
    provider_cleanup_columns = {column["name"] for column in inspector.get_columns("provider_secret_cleanup_jobs")}
    provider_cleanup_fks = inspector.get_foreign_keys("provider_secret_cleanup_jobs")
    provider_cleanup_indexes = inspector.get_indexes("provider_secret_cleanup_jobs")
    assert {
        "id",
        "secret_ref",
        "kind",
        "attempt_count",
        "last_error_code",
        "next_attempt_at",
        "created_at",
        "updated_at",
    } == provider_cleanup_columns
    assert provider_cleanup_fks == []
    assert any(item["name"] == "ix_provider_secret_cleanup_jobs_next_attempt_at" for item in provider_cleanup_indexes)
    team_llm_columns = {column["name"]: column for column in inspector.get_columns("team_llm_configs")}
    generated_document_columns = {column["name"]: column for column in inspector.get_columns("generated_documents")}
    assert team_llm_columns["provider_config_json"]["nullable"] is False
    assert team_llm_columns["provider_config_json"]["default"] is None
    assert generated_document_columns["llm_provider_config_json"]["nullable"] is True
    with engine.connect() as connection:
        stt_auth_modes = connection.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                WHERE pg_type.typname = 'sttauthmode'
                ORDER BY enumsortorder
                """
            )
        ).scalars().all()
        llm_auth_modes = connection.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                WHERE pg_type.typname = 'llmauthmode'
                ORDER BY enumsortorder
                """
            )
        ).scalars().all()
        llm_adapter_kinds = connection.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                WHERE pg_type.typname = 'llmadapterkind'
                ORDER BY enumsortorder
                """
            )
        ).scalars().all()
    assert stt_auth_modes == ["bearer", "none"]
    assert llm_auth_modes == ["bearer", "none", "google_adc", "google_service_account"]
    assert llm_adapter_kinds == ["openai_chat", "ollama_chat", "bedrock_chat", "gemini_enterprise"]


@pytest.mark.migration
def test_provider_revision_downgrade_blocks_while_pending_rows_hold_vault_refs():
    reset_public_schema()
    command.upgrade(alembic_config(), "w4x5y6z7a8b9")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES ('00000000-0000-0000-0000-000000000701', 'Revision Rollback', 'revision rollback', 'active', 30, NOW(), NOW())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000702', 'Rollback Admin', 'rollback-admin@example.com', 'hash',
                    NULL, NULL, true, 'active', false, 'complete', true, true, NOW(), NOW(), NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_configs (
                    id, team_id, revision_of_config_id, label, provider_preset, adapter_kind, base_url,
                    transcribe_path, auth_mode, available_models_json, file_field_name, response_text_path,
                    extra_form_fields_json, vault_secret_ref, setup_status, is_active,
                    created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000711', '00000000-0000-0000-0000-000000000701', NULL,
                    'Clinic STT', 'custom_rest_openapi', 'openai_compatible_rest', 'http://127.0.0.1:7000',
                    '/v1/audio/transcriptions', 'bearer', '[]'::json, 'file', 'text', '{}'::json,
                    'secret:stt-root', 'ready', true, '00000000-0000-0000-0000-000000000702',
                    '00000000-0000-0000-0000-000000000702', NOW(), NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000712', '00000000-0000-0000-0000-000000000701',
                    '00000000-0000-0000-0000-000000000711', 'Clinic STT', 'custom_rest_openapi',
                    'openai_compatible_rest', 'http://127.0.0.1:7001', '/v1/audio/transcriptions', 'bearer',
                    '[]'::json, 'file', 'text', '{}'::json, 'secret:stt-draft', 'pending', true,
                    '00000000-0000-0000-0000-000000000702', '00000000-0000-0000-0000-000000000702', NOW(), NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, revision_of_config_id, label, provider_preset, adapter_kind, base_url,
                    auth_mode, available_models_json, inspection_metadata_json, setup_status, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000721', '00000000-0000-0000-0000-000000000701', NULL,
                    'Clinic LLM', 'openai', 'openai_chat', 'https://api.openai.com/v1', 'bearer', '[]'::json,
                    '{}'::json, 'ready', 'secret:llm-root', true, '00000000-0000-0000-0000-000000000702',
                    '00000000-0000-0000-0000-000000000702', NOW(), NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000722', '00000000-0000-0000-0000-000000000701',
                    '00000000-0000-0000-0000-000000000721', 'Clinic LLM', 'openai', 'openai_chat',
                    'https://api.openai.com/v1', 'bearer', '[]'::json, '{}'::json, 'pending', 'secret:llm-draft',
                    true, '00000000-0000-0000-0000-000000000702', '00000000-0000-0000-0000-000000000702', NOW(), NOW()
                )
                """
            )
        )

    with pytest.raises(RuntimeError, match="contains pending revisions"):
        command.downgrade(alembic_config(), "v3w4x5y6z7a8")

    with isolated_engine.begin() as connection:
        inspector = inspect(connection)
        for table in ("team_stt_configs", "team_llm_configs"):
            assert "revision_of_config_id" in {column["name"] for column in inspector.get_columns(table)}
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 2


@pytest.mark.migration
def test_stt_no_auth_downgrade_blocks_while_configs_use_none():
    reset_public_schema()
    command.upgrade(alembic_config(), "x5y6z7a8b9c0")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
            VALUES ('00000000-0000-0000-0000-000000000731', 'No Auth Rollback', 'no auth rollback', 'active', 30, NOW(), NOW())
        """))
        connection.execute(text("""
            INSERT INTO users (
                id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000732', 'Rollback Admin', 'no-auth-rollback@example.com', 'hash',
                NULL, NULL, true, 'active', false, 'complete', true, true, NOW(), NOW(), NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO team_stt_configs (
                id, team_id, revision_of_config_id, label, provider_preset, adapter_kind, base_url,
                transcribe_path, auth_mode, available_models_json, file_field_name, response_text_path,
                extra_form_fields_json, vault_secret_ref, setup_status, is_active,
                created_by_user_id, updated_by_user_id, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000733', '00000000-0000-0000-0000-000000000731', NULL,
                'Local no auth', 'custom_rest_openapi', 'openai_compatible_rest', 'http://127.0.0.1:7000',
                '/v1/audio/transcriptions', 'none', '[]'::json, 'file', 'text', '{}'::json, '', 'ready', true,
                '00000000-0000-0000-0000-000000000732', '00000000-0000-0000-0000-000000000732', NOW(), NOW()
            )
        """))

    with pytest.raises(RuntimeError, match="auth_mode='none'"):
        command.downgrade(alembic_config(), "w4x5y6z7a8b9")


@pytest.mark.migration
def test_provider_cleanup_downgrade_blocks_while_jobs_retain_vault_refs():
    reset_public_schema()
    command.upgrade(alembic_config(), "b9c0d1e2f3a5")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO provider_secret_cleanup_jobs (id, secret_ref, kind)
            VALUES (
                '00000000-0000-0000-0000-000000000741',
                'secret:openscribe/llm/team/00000000-0000-0000-0000-000000000742/config/00000000-0000-0000-0000-000000000743',
                'llm'
            )
        """))

    with pytest.raises(RuntimeError, match="pending jobs retain Vault references"):
        command.downgrade(alembic_config(), "y6z7a8b9c0d1")

    with isolated_engine.connect() as connection:
        assert inspect(connection).has_table("provider_secret_cleanup_jobs")
        assert connection.execute(text("SELECT count(*) FROM provider_secret_cleanup_jobs")).scalar_one() == 1


@pytest.mark.migration
def test_audio_cleanup_downgrade_blocks_while_jobs_retain_vault_refs():
    reset_public_schema()
    command.upgrade(alembic_config(), "y6z7a8b9c0d1")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO transcript_audio_cleanup_jobs (id, secret_ref)
            VALUES (
                '00000000-0000-0000-0000-000000000751',
                'secret:openscribe/transcript-ingestion/00000000-0000-0000-0000-000000000752/source-audio'
            )
        """))

    with pytest.raises(RuntimeError, match="pending jobs retain Vault references"):
        command.downgrade(alembic_config(), "x5y6z7a8b9c0")

    with isolated_engine.connect() as connection:
        assert inspect(connection).has_table("transcript_audio_cleanup_jobs")
        assert connection.execute(text("SELECT count(*) FROM transcript_audio_cleanup_jobs")).scalar_one() == 1


@pytest.mark.migration
def test_security_audit_event_user_and_team_references_set_null_on_delete():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000101',
                    'Audit Delete Team',
                    'audit delete team',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, password_hash, full_name, team_id, team_role,
                    is_system_admin, status, must_change_password, onboarding_state,
                    mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000102',
                    'audit-delete@example.com',
                    'hash',
                    'Audit Delete',
                    '00000000-0000-0000-0000-000000000101',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    false,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO security_audit_events (
                    id, action, actor_user_id, target_user_id, team_id, details_json, created_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000103',
                    'account_deleted',
                    '00000000-0000-0000-0000-000000000102',
                    '00000000-0000-0000-0000-000000000102',
                    '00000000-0000-0000-0000-000000000101',
                    '{}'::jsonb,
                    NOW()
                )
                """
            )
        )
        connection.execute(text("DELETE FROM users WHERE id = '00000000-0000-0000-0000-000000000102'"))
        connection.execute(text("DELETE FROM teams WHERE id = '00000000-0000-0000-0000-000000000101'"))
        audit_row = connection.execute(
            text(
                """
                SELECT actor_user_id, target_user_id, team_id
                FROM security_audit_events
                WHERE id = '00000000-0000-0000-0000-000000000103'
                """
            )
        ).mappings().one()

    assert audit_row["actor_user_id"] is None
    assert audit_row["target_user_id"] is None
    assert audit_row["team_id"] is None


@pytest.mark.migration
def test_alembic_head_uses_normalized_uniqueness_rules():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000001',
                    'Clinic North',
                    'clinic north',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                SELECT
                    '00000000-0000-0000-0000-000000000002',
                    'Mixed Case',
                    'mixed.case@example.com',
                    'hash',
                    id,
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                FROM teams
                LIMIT 1
                """
            )
        )

        teams_uniques = inspect(connection).get_unique_constraints("teams")
        users_indexes = inspect(connection).get_indexes("users")

        assert any(item["name"] == "uq_teams_name_key" for item in teams_uniques)
        assert not any(item["name"] == "teams_name_key" and item["column_names"] == ["name"] for item in teams_uniques)
        assert any(item["name"] == "uq_users_email_lower" for item in users_indexes)
        assert not any(item["name"] == "users_email_key" for item in users_indexes)


@pytest.mark.migration
def test_alembic_head_adds_onboarding_and_session_tables():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    inspector = inspect(isolated_engine)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
    trusted_device_columns = {column["name"] for column in inspector.get_columns("user_trusted_devices")}
    request_columns = {column["name"] for column in inspector.get_columns("account_requests")}
    stt_columns = {column["name"] for column in inspector.get_columns("team_stt_configs")}
    stt_selection_columns = {column["name"] for column in inspector.get_columns("team_stt_selections")}
    llm_columns = {column["name"] for column in inspector.get_columns("team_llm_configs")}
    hallucination_check_selection_columns = {column["name"] for column in inspector.get_columns("team_hallucination_check_selections")}
    llm_selection_columns = {column["name"] for column in inspector.get_columns("team_llm_selections")}
    deidentification_provider_columns = {column["name"] for column in inspector.get_columns("deidentification_providers")}
    deidentification_assignment_columns = {column["name"] for column in inspector.get_columns("team_deidentification_provider_assignments")}
    deidentification_selection_columns = {column["name"] for column in inspector.get_columns("team_deidentification_selections")}
    clinical_nlp_selection_columns = {column["name"] for column in inspector.get_columns("team_clinical_nlp_selections")}
    user_llm_preference_columns = {column["name"] for column in inspector.get_columns("user_llm_preferences")}
    user_app_preference_columns = {column["name"] for column in inspector.get_columns("user_app_preferences")}
    template_indexes = inspector.get_indexes("templates")
    template_columns = {column["name"] for column in inspector.get_columns("templates")}
    template_version_columns = {column["name"] for column in inspector.get_columns("template_versions")}
    default_template_indexes = inspector.get_indexes("default_templates")
    default_template_columns = {column["name"] for column in inspector.get_columns("default_templates")}
    default_template_version_columns = {column["name"] for column in inspector.get_columns("default_template_versions")}
    quick_action_indexes = inspector.get_indexes("quick_actions")
    quick_action_columns = {column["name"] for column in inspector.get_columns("quick_actions")}
    quick_action_version_columns = {column["name"] for column in inspector.get_columns("quick_action_versions")}
    default_quick_action_indexes = inspector.get_indexes("default_quick_actions")
    default_quick_action_columns = {column["name"] for column in inspector.get_columns("default_quick_actions")}
    default_quick_action_version_columns = {column["name"] for column in inspector.get_columns("default_quick_action_versions")}
    generated_document_columns = {column["name"] for column in inspector.get_columns("generated_documents")}
    generated_document_section_columns = {column["name"] for column in inspector.get_columns("generated_document_sections")}
    post_consultation_dictation_columns = {column["name"] for column in inspector.get_columns("post_consultation_dictations")}
    post_consultation_dictation_segment_columns = {
        column["name"] for column in inspector.get_columns("post_consultation_dictation_segments")
    }
    provider_usage_event_columns = {column["name"] for column in inspector.get_columns("provider_usage_events")}
    redaction_run_columns = {column["name"] for column in inspector.get_columns("redaction_runs")}
    redaction_entity_columns = {column["name"] for column in inspector.get_columns("redaction_entities")}
    clinical_entity_run_columns = {column["name"] for column in inspector.get_columns("clinical_entity_runs")}
    clinical_entity_columns = {column["name"] for column in inspector.get_columns("clinical_entities")}
    clinical_entity_run_fks = inspector.get_foreign_keys("clinical_entity_runs")
    manual_pii_columns = {column["name"] for column in inspector.get_columns("transcript_manual_pii_entities")}
    transcript_columns = {column["name"] for column in inspector.get_columns("transcripts")}
    transcript_audio_cleanup_columns = {column["name"] for column in inspector.get_columns("transcript_audio_cleanup_jobs")}
    transcript_audio_cleanup_fks = inspector.get_foreign_keys("transcript_audio_cleanup_jobs")
    transcript_audio_cleanup_indexes = inspector.get_indexes("transcript_audio_cleanup_jobs")
    transcript_ingestion_job_columns = {column["name"] for column in inspector.get_columns("transcript_ingestion_jobs")}
    user_encryption_key_columns = {column["name"] for column in inspector.get_columns("user_encryption_keys")}
    auth_email_token_columns = {column["name"] for column in inspector.get_columns("auth_email_tokens")}
    smart_phrase_columns = {column["name"] for column in inspector.get_columns("smart_phrases")}
    smart_phrase_indexes = inspector.get_indexes("smart_phrases")
    smart_phrase_checks = inspector.get_check_constraints("smart_phrases")
    smart_phrase_fks = inspector.get_foreign_keys("smart_phrases")

    assert {"full_name", "must_change_password", "onboarding_state"} <= user_columns
    assert {"id", "secret_ref", "attempt_count", "last_error_code", "next_attempt_at", "created_at", "updated_at"} == transcript_audio_cleanup_columns
    assert transcript_audio_cleanup_fks == []
    assert any(item["name"] == "ix_transcript_audio_cleanup_jobs_next_attempt_at" for item in transcript_audio_cleanup_indexes)
    assert {"session_token_hash", "auth_level", "status", "revoke_reason"} <= session_columns
    assert {"device_token_hash", "last_mfa_verified_at", "expires_at", "revoke_reason"} <= trusted_device_columns
    assert {"requested_name", "requested_email", "requested_team_name", "status"} <= request_columns
    assert {
        "team_id",
        "provider_preset",
        "adapter_kind",
        "base_url",
        "transcribe_path",
        "vault_secret_ref",
        "response_text_path",
        "available_models_json",
        "model_field_name",
        "language_field_name",
        "segments_path",
        "segment_text_field",
        "segment_start_field",
        "segment_end_field",
        "segment_speaker_field",
        "credential_status",
        "credential_fingerprint",
        "inspection_metadata_json",
        "setup_status",
    } <= stt_columns
    stt_indexes = inspector.get_indexes("team_stt_configs")
    assert any(item["name"] == "uq_team_stt_configs_team_label_lower" for item in stt_indexes)
    with isolated_engine.connect() as connection:
        stt_adapter_values = set(
            connection.execute(
                text(
                    """
                    SELECT enumlabel
                    FROM pg_enum
                    JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                    WHERE pg_type.typname = 'sttadapterkind'
                    """
                )
            ).scalars()
        )
    assert "elevenlabs_speech_to_text" in stt_adapter_values
    assert {"team_id", "purpose", "stt_config_id", "model_name_override", "language_override", "selected_by_user_id"} <= stt_selection_columns
    llm_indexes = inspector.get_indexes("team_llm_configs")
    assert {"team_id", "provider_preset", "adapter_kind", "base_url", "vault_secret_ref", "available_models_json", "inspection_metadata_json", "setup_status"} <= llm_columns
    assert any(item["name"] == "ix_team_llm_configs_setup_status" for item in llm_indexes)
    assert any(item["name"] == "uq_team_llm_configs_team_label_lower" for item in llm_indexes)
    assert {"team_id", "llm_config_id", "model_name_override", "selected_by_user_id"} <= hallucination_check_selection_columns
    assert {"team_id", "llm_config_id", "allowed_models_json", "model_name_override", "selected_by_user_id"} <= llm_selection_columns
    assert {
        "label",
        "adapter_kind",
        "base_url",
        "detect_path",
        "auth_mode",
        "request_text_field",
        "response_entities_path",
        "response_start_field",
        "response_end_field",
        "response_type_field",
        "entity_type_map_json",
        "vault_secret_ref",
        "clinical_detection_enabled",
        "clinical_detection_allow_unredacted",
        "is_active",
        "is_builtin",
    } <= deidentification_provider_columns
    assert {"team_id", "provider_id", "assigned_by_user_id"} <= deidentification_assignment_columns
    assert {"team_id", "provider_id", "selected_by_user_id"} <= deidentification_selection_columns
    assert {"team_id", "provider_id", "selected_by_user_id"} <= clinical_nlp_selection_columns
    assert {"user_id", "preferred_model_name"} <= user_llm_preference_columns
    assert {"user_id", "preferences_json"} <= user_app_preference_columns
    assert {"user_id", "purpose", "token_hash", "expires_at", "used_at", "created_by_user_id"} <= auth_email_token_columns
    assert {"owner_user_id", "trigger", "expansion_text", "description", "last_used_at", "times_used"} <= smart_phrase_columns
    assert any(item["name"] == "uq_smart_phrases_owner_trigger_lower" for item in smart_phrase_indexes)
    assert any(item["name"] == "ck_smart_phrases_trigger_format" for item in smart_phrase_checks)
    assert any(item["name"] == "ck_smart_phrases_expansion_length" for item in smart_phrase_checks)
    assert any(
        fk["referred_table"] == "users"
        and fk["constrained_columns"] == ["owner_user_id"]
        and fk.get("options", {}).get("ondelete") == "CASCADE"
        for fk in smart_phrase_fks
    )
    assert any(item["name"] == "uq_templates_team_name_lower" for item in template_indexes)
    assert any(item["name"] == "uq_templates_owner_name_lower" for item in template_indexes)
    assert {"scope", "owner_user_id", "team_id", "name", "created_by_user_id"} <= template_columns
    assert {"template_id", "version_no", "mode", "prompt_text", "config_json"} <= template_version_columns
    assert any(item["name"] == "uq_default_templates_name_lower" for item in default_template_indexes)
    assert {"name", "description", "is_active", "created_by_user_id"} <= default_template_columns
    assert {"default_template_id", "version_no", "mode", "prompt_text", "config_json"} <= default_template_version_columns
    assert any(item["name"] == "uq_quick_actions_team_name_lower" for item in quick_action_indexes)
    assert any(item["name"] == "uq_quick_actions_owner_name_lower" for item in quick_action_indexes)
    assert {"scope", "owner_user_id", "team_id", "name", "created_by_user_id"} <= quick_action_columns
    assert {"quick_action_id", "version_no", "mode", "prompt_text"} <= quick_action_version_columns
    assert any(item["name"] == "uq_default_quick_actions_name_lower" for item in default_quick_action_indexes)
    assert {"name", "description", "is_active", "created_by_user_id"} <= default_quick_action_columns
    assert {"default_quick_action_id", "version_no", "mode", "prompt_text"} <= default_quick_action_version_columns
    assert {
        "owner_user_id",
        "team_id",
        "transcript_id",
        "transcript_version_id",
        "redaction_run_id",
        "template_version_id",
        "quick_action_version_id",
        "llm_config_id",
        "source_template_name",
        "source_quick_action_name",
        "follow_up_prompt_text",
        "prompt_snapshot_text",
        "structured_context_json",
        "generation_snapshot_json",
        "structured_section_definitions_json",
        "llm_request_payload_json_encrypted",
        "original_output_text_encrypted",
        "llm_adapter_kind",
        "llm_base_url",
        "celery_task_id",
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_usd",
        "duration_ms",
        "provider_duration_ms",
        "error_code",
        "provider_error_code",
        "provider_http_status",
        "error_message",
        "failed_provider_output_redacted_encrypted",
        "hallucination_check_debug_json_encrypted",
        "hallucination_check_status",
        "hallucination_check_llm_config_id",
        "hallucination_check_model_name",
        "hallucination_check_provider_snapshot_json",
        "hallucination_check_completed_at",
        "hallucination_check_applied_edit_count",
        "worker_received_at",
        "started_at",
        "completed_at",
    } <= generated_document_columns
    assert {
        "generated_document_id",
        "section_key",
        "section_label",
        "section_order",
        "original_text_encrypted",
        "edited_text_encrypted",
        "is_edited",
    } <= generated_document_section_columns
    assert {
        "transcript_id",
        "owner_user_id",
        "team_id",
        "combined_edited_text_encrypted",
        "is_combined_text_user_edited",
        "latest_appended_at",
        "created_at",
        "updated_at",
    } <= post_consultation_dictation_columns
    assert {
        "post_consultation_dictation_id",
        "owner_user_id",
        "team_id",
        "sequence_no",
        "asr_text_encrypted",
        "created_at",
    } <= post_consultation_dictation_segment_columns
    assert {
        "team_id",
        "owner_user_id",
        "generated_document_id",
        "transcript_id",
        "llm_config_id",
        "feature_type",
        "event_type",
        "provider_adapter",
        "model_name",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "duration_ms",
        "provider_duration_ms",
        "error_code",
        "provider_error_code",
        "provider_http_status",
        "created_at",
    } <= provider_usage_event_columns
    assert {
        "transcript_id",
        "transcript_version_id",
        "owner_user_id",
        "team_id",
        "status",
        "redacted_text_encrypted",
        "mapping_hash",
        "entity_count",
        "api_provider",
        "api_model_or_version",
        "error_code",
        "created_at",
        "updated_at",
        "failed_at",
    } <= redaction_run_columns
    assert {
        "redaction_run_id",
        "entity_order",
        "entity_type",
        "placeholder",
        "original_value_encrypted",
        "normalized_value_hash",
        "occurrence_count",
        "created_at",
    } <= redaction_entity_columns
    assert {
        "transcript_id",
        "transcript_version_id",
        "redaction_run_id",
        "owner_user_id",
        "team_id",
        "provider_id",
        "status",
        "source_text_redacted",
        "entity_count",
        "api_provider",
        "error_code",
        "created_at",
    } <= clinical_entity_run_columns
    provider_fk = next(item for item in clinical_entity_run_fks if item["referred_table"] == "deidentification_providers")
    assert provider_fk["options"].get("ondelete") == "SET NULL"
    assert {
        "clinical_entity_run_id",
        "entity_order",
        "entity_type",
        "value_encrypted",
        "normalized_value_hash",
        "occurrence_count",
        "score",
        "created_at",
    } <= clinical_entity_columns
    assert {
        "transcript_id",
        "owner_user_id",
        "team_id",
        "entity_type",
        "original_value_encrypted",
        "normalized_value_hash",
        "occurrence_count",
        "created_at",
        "updated_at",
    } <= manual_pii_columns
    assert {"owner_user_id", "team_id", "current_draft_text_encrypted", "structured_context_json", "ingestion_mode", "next_live_chunk_sequence_no_applied"} <= transcript_columns
    assert {
        "transcript_id",
        "owner_user_id",
        "team_id",
        "job_kind",
        "chunk_sequence_no",
        "status",
        "celery_task_id",
        "source_audio_blob",
        "source_audio_vault_ref",
        "source_audio_size_bytes",
        "source_audio_duration_seconds",
        "declared_duration_seconds",
        "result_text_encrypted",
        "stt_config_id",
        "stt_provider_preset",
        "stt_adapter_kind",
        "stt_base_url",
        "stt_transcribe_path",
        "stt_model_name",
        "stt_model_field_name",
        "stt_language",
        "stt_language_field_name",
        "stt_file_field_name",
        "stt_response_text_path",
        "stt_segments_path",
        "stt_segment_text_field",
        "stt_segment_start_field",
        "stt_segment_end_field",
        "stt_segment_speaker_field",
        "stt_extra_form_fields_json",
        "worker_received_at",
    } <= transcript_ingestion_job_columns
    assert {
        "user_id",
        "dek_version",
        "wrapped_dek",
        "kek_mount",
        "kek_key_name",
        "kek_key_version",
        "is_active",
        "created_at",
        "rotated_at",
    } <= user_encryption_key_columns


@pytest.mark.migration
def test_alembic_head_adds_post_consultation_dictation_constraints_and_cascades():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    inspector = inspect(isolated_engine)

    dictation_uniques = inspector.get_unique_constraints("post_consultation_dictations")
    segment_uniques = inspector.get_unique_constraints("post_consultation_dictation_segments")
    dictation_fks = inspector.get_foreign_keys("post_consultation_dictations")
    segment_fks = inspector.get_foreign_keys("post_consultation_dictation_segments")

    assert any(
        item["name"] == "uq_post_consultation_dictations_transcript"
        and item["column_names"] == ["transcript_id"]
        for item in dictation_uniques
    )
    assert any(
        item["name"] == "uq_post_consultation_dictation_segments_sequence"
        and item["column_names"] == ["post_consultation_dictation_id", "sequence_no"]
        for item in segment_uniques
    )
    assert any(
        item["referred_table"] == "transcripts"
        and item["constrained_columns"] == ["transcript_id"]
        and item.get("options", {}).get("ondelete") == "CASCADE"
        for item in dictation_fks
    )
    assert any(
        item["referred_table"] == "post_consultation_dictations"
        and item["constrained_columns"] == ["post_consultation_dictation_id"]
        and item.get("options", {}).get("ondelete") == "CASCADE"
        for item in segment_fks
    )


@pytest.mark.migration
def test_alembic_head_supports_multiple_stt_selection_purposes_per_team():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000401',
                    'Admin User',
                    'admin-purpose@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000402',
                    'Clinic Purpose',
                    'clinic purpose',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_configs (
                    id, team_id, label, adapter_kind, base_url, transcribe_path, auth_mode, model_name,
                    available_models_json, file_field_name, language, response_text_path, extra_form_fields_json, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000403',
                    '00000000-0000-0000-0000-000000000402',
                    'Conversation STT',
                    'openai_compatible_rest',
                    'http://127.0.0.1:7000',
                    '/v1/audio/transcriptions',
                    'bearer',
                    'whisper-1',
                    '[]'::json,
                    'file',
                    'en',
                    'text',
                    '{}'::json,
                    'secret:openscribe/stt/team/402/config/403',
                    true,
                    '00000000-0000-0000-0000-000000000401',
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000404',
                    '00000000-0000-0000-0000-000000000402',
                    'Dictation STT',
                    'openai_compatible_rest',
                    'http://127.0.0.1:7001',
                    '/v1/audio/transcriptions',
                    'bearer',
                    'gpt-4o-mini-transcribe',
                    '[]'::json,
                    'file',
                    'en',
                    'text',
                    '{}'::json,
                    'secret:openscribe/stt/team/402/config/404',
                    true,
                    '00000000-0000-0000-0000-000000000401',
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_selections (
                    id, team_id, purpose, stt_config_id, model_name_override, language_override, selected_by_user_id, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000405',
                    '00000000-0000-0000-0000-000000000402',
                    'conversation',
                    '00000000-0000-0000-0000-000000000403',
                    NULL,
                    NULL,
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000406',
                    '00000000-0000-0000-0000-000000000402',
                    'post_consultation_dictation',
                    '00000000-0000-0000-0000-000000000404',
                    NULL,
                    NULL,
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                )
                """
            )
        )

        count = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM team_stt_selections
                WHERE team_id = '00000000-0000-0000-0000-000000000402'
                """
            )
        ).scalar_one()

    assert count == 2


@pytest.mark.migration
def test_unique_asset_name_migration_dedupes_existing_rows_before_indexes():
    reset_public_schema()
    command.upgrade(alembic_config(), "aa7c8d9e0f1a")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000101',
                    'Clinic Dedupe',
                    'clinic dedupe',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000102',
                    'Dedupe User',
                    'dedupe@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000101',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO templates (id, scope, owner_user_id, team_id, name, description, is_active, created_by_user_id, created_at, updated_at)
                VALUES
                ('00000000-0000-0000-0000-000000000111', 'user', '00000000-0000-0000-0000-000000000102', NULL, 'Test Name', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW()),
                ('00000000-0000-0000-0000-000000000112', 'user', '00000000-0000-0000-0000-000000000102', NULL, ' test name ', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO quick_actions (id, scope, owner_user_id, team_id, name, description, is_active, created_by_user_id, created_at, updated_at)
                VALUES
                ('00000000-0000-0000-0000-000000000121', 'user', '00000000-0000-0000-0000-000000000102', NULL, 'Test Action', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW()),
                ('00000000-0000-0000-0000-000000000122', 'user', '00000000-0000-0000-0000-000000000102', NULL, 'test action', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW())
                """
            )
        )

    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        template_names = connection.execute(
            text(
                """
                SELECT name
                FROM templates
                WHERE owner_user_id = '00000000-0000-0000-0000-000000000102'
                ORDER BY name
                """
            )
        ).scalars().all()
        quick_action_names = connection.execute(
            text(
                """
                SELECT name
                FROM quick_actions
                WHERE owner_user_id = '00000000-0000-0000-0000-000000000102'
                ORDER BY name
                """
            )
        ).scalars().all()

    assert template_names == ["Test Name", "test name copy 2"]
    assert quick_action_names == ["Test Action", "test action copy 2"]


@pytest.mark.migration
def test_alembic_upgrade_dedupes_existing_asset_names_without_copy_name_collisions():
    reset_public_schema()
    command.upgrade(alembic_config(), "aa7c8d9e0f1a")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000201',
                    'Clinic Dedupe',
                    'clinic dedupe',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000202',
                    'Collision User',
                    'collision@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000201',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO templates (id, scope, owner_user_id, team_id, name, description, is_active, created_by_user_id, created_at, updated_at)
                VALUES
                ('00000000-0000-0000-0000-000000000211', 'user', '00000000-0000-0000-0000-000000000202', NULL, 'Clinic Letter', NULL, true, '00000000-0000-0000-0000-000000000202', NOW() - INTERVAL '2 minutes', NOW() - INTERVAL '2 minutes'),
                ('00000000-0000-0000-0000-000000000212', 'user', '00000000-0000-0000-0000-000000000202', NULL, 'clinic letter', NULL, true, '00000000-0000-0000-0000-000000000202', NOW() - INTERVAL '1 minute', NOW() - INTERVAL '1 minute'),
                ('00000000-0000-0000-0000-000000000213', 'user', '00000000-0000-0000-0000-000000000202', NULL, 'Clinic Letter copy 2', NULL, true, '00000000-0000-0000-0000-000000000202', NOW(), NOW())
                """
            )
        )
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        template_names = connection.execute(
            text(
                """
                SELECT name
                FROM templates
                WHERE owner_user_id = '00000000-0000-0000-0000-000000000202'
                ORDER BY name
                """
            )
        ).scalars().all()

    assert template_names == ["Clinic Letter", "Clinic Letter copy 2", "clinic letter copy 3"]


@pytest.mark.migration
def test_llm_config_label_migration_dedupes_before_unique_index():
    reset_public_schema()
    command.upgrade(alembic_config(), "b1c2d3e4f5a6")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES ('00000000-0000-0000-0000-000000000501', 'Clinic LLM Labels', 'clinic llm labels', 'active', 30, NOW(), NOW())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000502', 'LLM Admin', 'llm-label-admin@example.com', 'hash', NULL, NULL,
                    true, 'active', false, 'complete', true, true, NOW(), NOW(), NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                    available_models_json, inspection_metadata_json, setup_status, vault_secret_ref, is_active,
                    created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES
                ('00000000-0000-0000-0000-000000000511', '00000000-0000-0000-0000-000000000501', 'OpenRouter', 'openrouter', 'openai_chat', 'https://openrouter.ai/api/v1', 'bearer', 'model-a', '[]'::json, '{}'::json, 'ready', 'secret:a', true, '00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-000000000502', NOW() - INTERVAL '2 minutes', NOW() - INTERVAL '2 minutes'),
                ('00000000-0000-0000-0000-000000000512', '00000000-0000-0000-0000-000000000501', ' openrouter ', 'openrouter', 'openai_chat', 'https://openrouter.ai/api/v1', 'bearer', 'model-b', '[]'::json, '{}'::json, 'ready', 'secret:b', true, '00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-000000000502', NOW() - INTERVAL '1 minute', NOW() - INTERVAL '1 minute'),
                ('00000000-0000-0000-0000-000000000513', '00000000-0000-0000-0000-000000000501', 'OpenRouter copy 2', 'openrouter', 'openai_chat', 'https://openrouter.ai/api/v1', 'bearer', 'model-c', '[]'::json, '{}'::json, 'ready', 'secret:c', true, '00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-000000000502', NOW(), NOW())
                """
            )
        )

    command.upgrade(alembic_config(), "head")

    with isolated_engine.begin() as connection:
        names = connection.execute(
            text(
                """
                SELECT label
                FROM team_llm_configs
                WHERE team_id = '00000000-0000-0000-0000-000000000501'
                ORDER BY label
                """
            )
        ).scalars().all()
        indexes = inspect(connection).get_indexes("team_llm_configs")

    assert names == ["OpenRouter", "OpenRouter copy 2", "openrouter copy 3"]
    assert any(item["name"] == "uq_team_llm_configs_team_label_lower" for item in indexes)


@pytest.mark.migration
def test_alembic_upgrade_backfills_structured_section_snapshots_for_existing_documents():
    reset_public_schema()
    command.upgrade(alembic_config(), "bb8d9e0f1a2b")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000301',
                    'Clinic Snapshot',
                    'clinic snapshot',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000302',
                    'Snapshot User',
                    'snapshot-migration@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000301',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, owner_user_id, team_id, title, current_draft_text_encrypted, ingestion_mode, status,
                    retention_days_applied, retention_expires_at, created_at, next_live_chunk_sequence_no_applied
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000303',
                    '00000000-0000-0000-0000-000000000302',
                    '00000000-0000-0000-0000-000000000301',
                    'Visit',
                    'draft',
                    'whole_file',
                    'ready',
                    30,
                    NOW() + INTERVAL '30 days',
                    NOW(),
                    1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcript_versions (id, transcript_id, version_no, text_encrypted, created_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000304',
                    '00000000-0000-0000-0000-000000000303',
                    1,
                    'draft',
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO generated_documents (
                    id, owner_user_id, team_id, transcript_id, transcript_version_id, generator_type,
                    source_template_name, prompt_snapshot_text, status, title, document_mode,
                    original_output_text_encrypted, edited_output_text_encrypted, is_edited,
                    retention_expires_at, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000305',
                    '00000000-0000-0000-0000-000000000302',
                    '00000000-0000-0000-0000-000000000301',
                    '00000000-0000-0000-0000-000000000303',
                    '00000000-0000-0000-0000-000000000304',
                    'template',
                    'EMIS note',
                    'prompt',
                    'ready',
                    'Structured note',
                    'structured',
                    'Problem\nPain',
                    'Problem\nPain',
                    false,
                    NOW() + INTERVAL '30 days',
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO generated_document_sections (
                    id, generated_document_id, section_key, section_label, section_order,
                    original_text_encrypted, edited_text_encrypted, is_edited, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000306',
                    '00000000-0000-0000-0000-000000000305',
                    'problem',
                    'Problem',
                    1,
                    'Pain',
                    'Pain',
                    false,
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000307',
                    '00000000-0000-0000-0000-000000000305',
                    'history',
                    'History',
                    2,
                    'Started yesterday',
                    'Started yesterday',
                    false,
                    NOW(),
                    NOW()
                )
                """
            )
        )

    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        snapshot = connection.execute(
            text(
                """
                SELECT structured_section_definitions_json
                FROM generated_documents
                WHERE id = '00000000-0000-0000-0000-000000000305'
                """
            )
        ).scalar_one()

    assert snapshot == {
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "section_label": "Problem", "section_order": 1},
            {"section_key": "history", "section_label": "History", "section_order": 2},
        ],
    }


@pytest.mark.migration
def test_alembic_head_supports_new_stt_adapter_values():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000020',
                    'Admin User',
                    'admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000021',
                    'Clinic North',
                    'clinic north',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_configs (
                    id, team_id, label, adapter_kind, base_url, transcribe_path, auth_mode, model_name,
                    available_models_json, file_field_name, language, response_text_path, extra_form_fields_json, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000022',
                    '00000000-0000-0000-0000-000000000021',
                    'OpenAI Cloud',
                    'openai_cloud',
                    'https://api.openai.com/v1',
                    '/v1/audio/transcriptions',
                    'bearer',
                    'gpt-4o-mini-transcribe',
                    '[]'::json,
                    'file',
                    NULL,
                    'text',
                    '{}'::jsonb,
                    'secret:openscribe/stt/team/1/config/1',
                    true,
                    '00000000-0000-0000-0000-000000000020',
                    '00000000-0000-0000-0000-000000000020',
                    NOW(),
                    NOW()
                )
                """
            )
        )
        adapter_kind = connection.execute(text("SELECT adapter_kind::text FROM team_stt_configs")).scalar_one()

    assert adapter_kind == "openai_cloud"


@pytest.mark.migration
def test_elevenlabs_stt_adapter_migration_downgrades_enum_without_type_collision():
    reset_public_schema()
    config = alembic_config()
    command.upgrade(config, "e4f5a6b7c9d1")

    command.downgrade(config, "d3e4f5a6b7c9")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        adapter_values = connection.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE pg_type.typname = 'sttadapterkind'
                ORDER BY enumsortorder
                """
            )
        ).scalars().all()
        temporary_type_count = connection.execute(text("SELECT COUNT(*) FROM pg_type WHERE typname = 'sttadapterkind_new'")).scalar_one()

    assert adapter_values == ["generic_rest", "openai_cloud", "openai_compatible_rest"]
    assert temporary_type_count == 0


@pytest.mark.migration
def test_alembic_head_supports_llm_adapter_values():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000120',
                    'Admin User',
                    'llm-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000121',
                    'Clinic South',
                    'clinic south',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                    available_models_json, inspection_metadata_json, setup_status, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000122',
                    '00000000-0000-0000-0000-000000000121',
                    'Local Ollama',
                    'ollama',
                    'ollama_chat',
                    'http://localhost:11434',
                    'none',
                    'llama3.2',
                    '[]'::json,
                    '{}'::json,
                    'ready',
                    '',
                    true,
                    '00000000-0000-0000-0000-000000000120',
                    '00000000-0000-0000-0000-000000000120',
                    NOW(),
                    NOW()
                )
                """
            )
        )

        adapter_kind, auth_mode = connection.execute(text("SELECT adapter_kind::text, auth_mode::text FROM team_llm_configs")).one()

    assert adapter_kind == "ollama_chat"
    assert auth_mode == "none"


@pytest.mark.migration
def test_alembic_head_supports_bedrock_llm_adapter_value():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000130',
                    'Admin User',
                    'bedrock-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000131',
                    'Clinic Bedrock',
                    'clinic bedrock',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                    available_models_json, inspection_metadata_json, setup_status, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000132',
                    '00000000-0000-0000-0000-000000000131',
                    'Amazon Bedrock',
                    'bedrock_http_gateway',
                    'bedrock_chat',
                    'https://bedrock-mantle.us-east-1.api.aws/v1',
                    'bearer',
                    'anthropic.claude-3-7-sonnet-20250219-v1:0',
                    '[]'::json,
                    '{}'::json,
                    'ready',
                    'secret:openscribe/llm/team/1/config/2',
                    true,
                    '00000000-0000-0000-0000-000000000130',
                    '00000000-0000-0000-0000-000000000130',
                    NOW(),
                    NOW()
                )
                """
            )
        )

        adapter_kind, auth_mode = connection.execute(text("SELECT adapter_kind::text, auth_mode::text FROM team_llm_configs")).one()

    assert adapter_kind == "bedrock_chat"
    assert auth_mode == "bearer"


@pytest.mark.migration
def test_alembic_backfills_llm_provider_presets():
    reset_public_schema()
    command.upgrade(alembic_config(), "z6b7c8d9e0f1")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000140',
                    'Admin User',
                    'llm-preset-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000141',
                    'Clinic Presets',
                    'clinic presets',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        for config_id, label, adapter, base_url, model in [
            ("00000000-0000-0000-0000-000000000142", "OpenAI", "openai_chat", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("00000000-0000-0000-0000-000000000143", "OpenRouter", "openai_chat", "https://openrouter.ai/api/v1", "openai/gpt-4o"),
            ("00000000-0000-0000-0000-000000000144", "Bedrock", "bedrock_chat", "https://bedrock-mantle.eu-west-2.api.aws/v1", "anthropic.claude"),
            ("00000000-0000-0000-0000-000000000145", "Ollama", "ollama_chat", "http://localhost:11434", "llama3.2"),
            ("00000000-0000-0000-0000-000000000146", "Custom", "openai_chat", "https://llm.example.com/v1", "custom-model"),
        ]:
            connection.execute(
                text(
                    """
                    INSERT INTO team_llm_configs (
                        id, team_id, label, adapter_kind, base_url, auth_mode, model_name, available_models_json,
                        vault_secret_ref, is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                    )
                    VALUES (
                        :id,
                        '00000000-0000-0000-0000-000000000141',
                        :label,
                        :adapter_kind,
                        :base_url,
                        'bearer',
                        :model_name,
                        '[]'::json,
                        'secret:openscribe/llm/team/1/config/2',
                        true,
                        '00000000-0000-0000-0000-000000000140',
                        '00000000-0000-0000-0000-000000000140',
                        NOW(),
                        NOW()
                    )
                    """
                ),
                {"id": config_id, "label": label, "adapter_kind": adapter, "base_url": base_url, "model_name": model},
            )

    command.upgrade(alembic_config(), "head")

    with isolated_engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT label, provider_preset FROM team_llm_configs")).all())
        metadata = connection.execute(text("SELECT inspection_metadata_json::text FROM team_llm_configs LIMIT 1")).scalar_one()

    assert rows == {
        "OpenAI": "openai",
        "OpenRouter": "openrouter",
        "Bedrock": "bedrock_http_gateway",
        "Ollama": "ollama",
        "Custom": "custom_openai_compatible",
    }
    assert metadata == "{}"


@pytest.mark.migration
def test_alembic_backfills_llm_setup_status():
    reset_public_schema()
    command.upgrade(alembic_config(), "a0b1c2d3e4f6")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000150',
                    'Admin User',
                    'llm-setup-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000151',
                    'Clinic Setup',
                    'clinic setup',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        for config_id, label, model_name in [
            ("00000000-0000-0000-0000-000000000152", "Ready LLM", "gpt-4o-mini"),
            ("00000000-0000-0000-0000-000000000153", "Draft LLM", None),
        ]:
            connection.execute(
                text(
                    """
                    INSERT INTO team_llm_configs (
                        id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                        available_models_json, inspection_metadata_json, vault_secret_ref, is_active,
                        created_by_user_id, updated_by_user_id, created_at, updated_at
                    )
                    VALUES (
                        :id,
                        '00000000-0000-0000-0000-000000000151',
                        :label,
                        'openai',
                        'openai_chat',
                        'https://api.openai.com/v1',
                        'bearer',
                        :model_name,
                        '[]'::json,
                        '{}'::json,
                        'secret:openscribe/llm/team/1/config/2',
                        true,
                        '00000000-0000-0000-0000-000000000150',
                        '00000000-0000-0000-0000-000000000150',
                        NOW(),
                        NOW()
                    )
                    """
                ),
                {"id": config_id, "label": label, "model_name": model_name},
            )

    command.upgrade(alembic_config(), "head")

    with isolated_engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT label, setup_status FROM team_llm_configs")).all())

    assert rows == {"Ready LLM": "ready", "Draft LLM": "pending_model_selection"}


@pytest.mark.migration
def test_working_note_migration_backfills_encrypted_structured_context():
    reset_public_schema()
    command.upgrade(alembic_config(), "g6b7c9d1e2f3")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000170',
                    'Clinic Working Note',
                    'clinic working note',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000171',
                    'Working Note Owner',
                    'working-note-migration@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000170',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, owner_user_id, team_id, title, current_draft_text_encrypted, structured_context_json,
                    ingestion_mode, status, next_live_chunk_sequence_no_applied, retention_days_applied,
                    retention_expires_at, created_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000172',
                    '00000000-0000-0000-0000-000000000171',
                    '00000000-0000-0000-0000-000000000170',
                    'Encrypted structured context',
                    NULL,
                    to_json(CAST(:envelope AS text)),
                    'whole_file',
                    'ready',
                    1,
                    30,
                    NOW() + INTERVAL '30 days',
                    NOW()
                )
                """
            ),
            {"envelope": '{"alg":"AES-256-GCM","ct":"abc","dkv":1,"n":"abc","v":1}'},
        )

    command.upgrade(alembic_config(), "r7s8t9u0v1w2")

    with isolated_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT working_note_mode, working_note_updated_at
                FROM transcripts
                WHERE id = '00000000-0000-0000-0000-000000000172'
                """
            )
        ).one()

    assert row.working_note_mode == "structured"
    assert row.working_note_updated_at is not None


@pytest.mark.migration
def test_alembic_head_supports_simplified_transcript_ingestion_mode():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000030',
                    'Clinic North',
                    'clinic north',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000031',
                    'Owner User',
                    'owner@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000030',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, owner_user_id, team_id, title, current_draft_text_encrypted, ingestion_mode, status,
                    next_live_chunk_sequence_no_applied, retention_days_applied, retention_expires_at, created_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000032',
                    '00000000-0000-0000-0000-000000000031',
                    '00000000-0000-0000-0000-000000000030',
                    'Whole-file transcript',
                    NULL,
                    'whole_file',
                    'recording',
                    1,
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        ingestion_mode = connection.execute(text("SELECT ingestion_mode::text FROM transcripts")).scalar_one()

    assert ingestion_mode == "whole_file"


@pytest.mark.migration
def test_alembic_head_supports_suspended_user_status():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000010',
                    'Suspended User',
                    'suspended@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'suspended',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )

        status = connection.execute(text("SELECT status::text FROM users WHERE email = 'suspended@example.com'")).scalar_one()

    assert status == "suspended"


@pytest.mark.migration
def test_alembic_head_supports_quick_action_generated_document_type():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        values = connection.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE pg_type.typname = 'generateddocumentgeneratortype'
                ORDER BY enumsortorder
                """
            )
        ).scalars().all()

    assert "quick_action" in values


@pytest.mark.migration
def test_quota_accounting_foundation_schema_has_metadata_only_constraints_and_fks():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    inspector = inspect(engine)
    user_columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert {
        "daily_token_limit",
        "monthly_token_limit",
        "daily_audio_seconds_limit",
        "monthly_audio_seconds_limit",
    } <= set(user_columns)
    assert all(user_columns[name]["nullable"] for name in (
        "daily_token_limit",
        "monthly_token_limit",
        "daily_audio_seconds_limit",
        "monthly_audio_seconds_limit",
    ))

    ledger_checks = {item["name"] for item in inspector.get_check_constraints("user_quota_policy_events")}
    ledger_indexes = {item["name"] for item in inspector.get_indexes("user_quota_policy_events")}
    ledger_uniques = {item["name"] for item in inspector.get_unique_constraints("user_quota_policy_events")}
    ledger_fks = inspector.get_foreign_keys("user_quota_policy_events")
    assert {
        "ck_user_quota_policy_events_event_shape",
        "ck_user_quota_policy_events_expiry_after_effective",
        "ck_user_quota_policy_events_revocation_shape",
    } <= ledger_checks
    assert {
        "ix_user_quota_policy_events_target_created",
        "ix_user_quota_policy_events_lookup",
        "ix_user_quota_policy_events_active_grant",
    } <= ledger_indexes
    assert {
        "uq_uqpe_operation_target_resource_period_type",
        "uq_user_quota_policy_events_revocation_operation",
    } <= ledger_uniques
    assert any(fk["constrained_columns"] == ["target_user_id"] and fk["options"].get("ondelete") == "CASCADE" for fk in ledger_fks)
    assert sum(
        fk["constrained_columns"] in (["actor_user_id"], ["revoker_user_id"])
        and fk["options"].get("ondelete") == "SET NULL"
        for fk in ledger_fks
    ) == 2

    attempt_columns = {column["name"] for column in inspector.get_columns("provider_attempts")}
    attempt_checks = {item["name"] for item in inspector.get_check_constraints("provider_attempts")}
    attempt_indexes = {item["name"] for item in inspector.get_indexes("provider_attempts")}
    attempt_uniques = {item["name"] for item in inspector.get_unique_constraints("provider_attempts")}
    attempt_fks = inspector.get_foreign_keys("provider_attempts")
    assert {
        "authorized_at",
        "reservation_valid_until",
        "submitted_at",
        "settled_at",
        "cancelled_at",
        "deadline_at",
        "reserved_units",
        "settled_units",
        "reported_input_tokens",
        "reported_output_tokens",
        "reported_total_tokens",
        "measured_audio_seconds",
    } <= attempt_columns
    assert not {"prompt", "response", "content", "text", "secret", "vault_secret_ref"} & attempt_columns
    assert {
        "ck_provider_attempts_state_shape",
        "ck_provider_attempts_settlement_basis_shape",
        "ck_provider_attempts_resource_payload_shape",
    } <= attempt_checks
    assert {
        "ix_provider_attempts_owner_resource_authorized",
        "ix_provider_attempts_active_reservations",
        "ix_provider_attempts_submitted_deadline",
        "ix_provider_attempts_team_resource_settled",
    } <= attempt_indexes
    assert "uq_provider_attempts_correlation_attempt" in attempt_uniques
    assert any(fk["constrained_columns"] == ["team_id"] and fk["options"].get("ondelete") == "CASCADE" for fk in attempt_fks)
    assert sum(
        fk["constrained_columns"] in (["owner_user_id"], ["transcript_id"], ["transcript_ingestion_job_id"], ["generated_document_id"])
        and fk["options"].get("ondelete") == "SET NULL"
        for fk in attempt_fks
    ) == 4

    outbox_checks = {item["name"] for item in inspector.get_check_constraints("task_dispatch_outbox")}
    outbox_indexes = {item["name"] for item in inspector.get_indexes("task_dispatch_outbox")}
    assert {"ck_task_dispatch_outbox_attempt_count_nonnegative", "ck_task_dispatch_outbox_state_timestamps"} <= outbox_checks
    assert {"ix_task_dispatch_outbox_pending_retry", "ix_task_dispatch_outbox_source"} <= outbox_indexes

    expected_enums = {
        "quotaresource": ["tokens", "audio_seconds"],
        "quotaperiod": ["daily", "monthly"],
        "userquotapolicyeventtype": ["grant", "reset", "limit_change"],
        "userquotareasoncode": ["policy_change", "temporary_allowance", "failed_job_correction", "administrative_correction", "other"],
        "attemptkind": ["llm_generation", "llm_hallucination_check", "stt_conversation", "stt_post_consultation_dictation", "stt_prompt_context", "stt_provider_test"],
        "attemptstatus": ["reserved", "submitted", "settled", "cancelled"],
        "attemptoutcome": ["succeeded", "failed", "unknown", "cancelled"],
        "providersettlementbasis": ["reported", "measured", "conservative_unknown"],
        "taskdispatchkind": ["generation", "ingestion"],
        "taskdispatchstate": ["pending", "published", "cancelled", "failed"],
        "taskdispatchsourcekind": ["generated_document", "transcript_ingestion_job"],
        "hallucinationcheckstatus": [
            "not_applicable", "skipped_not_configured", "skipped_config_invalid", "failed_provider",
            "failed_invalid_response", "checked_unchanged", "checked_corrected", "skipped_quota",
        ],
    }
    with engine.connect() as connection:
        for enum_name, labels in expected_enums.items():
            actual = connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                    "WHERE pg_type.typname = :enum_name ORDER BY enumsortorder"
                ),
                {"enum_name": enum_name},
            ).scalars().all()
            assert actual == labels


@pytest.mark.migration
def test_quota_accounting_constraints_reject_incomplete_grants_and_attempt_states():
    """Exercise SQL constraints directly; ORM metadata must name same guards."""
    reset_public_schema()
    command.upgrade(alembic_config(), "head")
    inspector = inspect(engine)
    assert {
        "ck_user_quota_policy_events_event_shape",
        "ck_user_quota_policy_events_revocation_shape",
    } <= {item["name"] for item in inspector.get_check_constraints("user_quota_policy_events")}
    assert {
        "ck_provider_attempts_state_shape",
        "ck_provider_attempts_resource_payload_shape",
    } <= {item["name"] for item in inspector.get_check_constraints("provider_attempts")}

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    team_id = "00000000-0000-0000-0000-000000000811"
    user_id = "00000000-0000-0000-0000-000000000812"
    with isolated_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
            VALUES (:team_id, 'Constraint Team', 'constraint team', 'active', 30, NOW(), NOW())
        """), {"team_id": team_id})
        connection.execute(text("""
            INSERT INTO users (id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                               must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at)
            VALUES (:user_id, 'Constraint User', 'constraint@example.com', 'hash', :team_id, 'user', false,
                    'active', false, 'complete', false, false, NOW(), NOW())
        """), {"team_id": team_id, "user_id": user_id})

        valid_grant = """
            INSERT INTO user_quota_policy_events (
                id, operation_id, target_user_id, actor_user_id_snapshot, event_type, resource, period,
                reason_code, reason, amount, effective_at
            ) VALUES (:id, :operation_id, :user_id, :user_id, 'grant', 'tokens', 'daily',
                      'temporary_allowance', 'valid grant', :amount, NOW())
        """
        connection.execute(text(valid_grant), {"id": "00000000-0000-0000-0000-000000000813", "operation_id": "00000000-0000-0000-0000-000000000814", "user_id": user_id, "amount": 1})
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text(valid_grant), {"id": "00000000-0000-0000-0000-000000000815", "operation_id": "00000000-0000-0000-0000-000000000816", "user_id": user_id, "amount": None})
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("""
                    INSERT INTO user_quota_policy_events (
                        id, operation_id, target_user_id, actor_user_id_snapshot, revoker_user_id_snapshot,
                        revocation_operation_id, event_type, resource, period, reason_code, reason, amount,
                        effective_at, revoked_at, revocation_reason_code, revocation_reason
                    ) VALUES ('00000000-0000-0000-0000-000000000817', '00000000-0000-0000-0000-000000000818',
                              :user_id, :user_id, :user_id, '00000000-0000-0000-0000-000000000819', 'grant',
                              'tokens', 'monthly', 'temporary_allowance', 'revoked', 1, NOW(), NOW(), 'other', NULL)
                """), {"user_id": user_id})

        valid_attempt = """
            INSERT INTO provider_attempts (
                id, team_id, owner_user_id, correlation_id, attempt_number, attempt_kind, resource, status,
                reserved_units, authorized_at, reservation_valid_until, submitted_at, deadline_at
            ) VALUES (:id, :team_id, :user_id, :correlation_id, 1, 'llm_generation', 'tokens', 'submitted',
                      1, NOW(), NOW() + INTERVAL '2 minutes', NOW(), NOW() + INTERVAL '1 minute')
        """
        connection.execute(text(valid_attempt), {"id": "00000000-0000-0000-0000-000000000820", "team_id": team_id, "user_id": user_id, "correlation_id": "00000000-0000-0000-0000-000000000821"})
        for row_id, correlation_id, omitted_field in (
            ("00000000-0000-0000-0000-000000000822", "00000000-0000-0000-0000-000000000823", "submitted_at"),
            ("00000000-0000-0000-0000-000000000824", "00000000-0000-0000-0000-000000000825", "settled_at"),
        ):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    if omitted_field == "submitted_at":
                        connection.execute(text("""
                            INSERT INTO provider_attempts (id, team_id, owner_user_id, correlation_id, attempt_number, attempt_kind, resource, status, reserved_units, authorized_at, reservation_valid_until, deadline_at)
                            VALUES (:id, :team_id, :user_id, :correlation_id, 1, 'llm_generation', 'tokens', 'submitted', 1, NOW(), NOW() + INTERVAL '2 minutes', NOW() + INTERVAL '1 minute')
                        """), {"id": row_id, "team_id": team_id, "user_id": user_id, "correlation_id": correlation_id})
                    else:
                        connection.execute(text("""
                            INSERT INTO provider_attempts (id, team_id, owner_user_id, correlation_id, attempt_number, attempt_kind, resource, status, outcome, settlement_basis, reserved_units, settled_units, authorized_at, reservation_valid_until, submitted_at, deadline_at)
                            VALUES (:id, :team_id, :user_id, :correlation_id, 1, 'llm_generation', 'tokens', 'settled', 'succeeded', 'reported', 1, 1, NOW(), NOW() + INTERVAL '2 minutes', NOW(), NOW() + INTERVAL '1 minute')
                        """), {"id": row_id, "team_id": team_id, "user_id": user_id, "correlation_id": correlation_id})
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("""
                    INSERT INTO provider_attempts (id, team_id, owner_user_id, correlation_id, attempt_number, attempt_kind, resource, status, reserved_units, authorized_at, reservation_valid_until)
                    VALUES ('00000000-0000-0000-0000-000000000826', :team_id, :user_id, '00000000-0000-0000-0000-000000000827', 1, 'stt_conversation', 'audio_seconds', 'reserved', 1, NOW(), NOW() + INTERVAL '1 minute')
                """), {"team_id": team_id, "user_id": user_id})
        connection.execute(text("""
            INSERT INTO provider_attempts (id, team_id, owner_user_id, correlation_id, attempt_number, attempt_kind, resource, status, reserved_units, measured_audio_seconds, authorized_at, reservation_valid_until)
            VALUES ('00000000-0000-0000-0000-000000000828', :team_id, :user_id, '00000000-0000-0000-0000-000000000829', 1, 'stt_conversation', 'audio_seconds', 'reserved', 2, 1.5, NOW(), NOW() + INTERVAL '1 minute')
        """), {"team_id": team_id, "user_id": user_id})


@pytest.mark.migration
def test_quota_accounting_foundation_downgrade_fails_closed_then_removes_empty_schema():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")
    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
            VALUES ('00000000-0000-0000-0000-000000000801', 'Quota Rollback', 'quota rollback', 'active', 30, NOW(), NOW())
        """))
        connection.execute(text("""
            INSERT INTO users (
                id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000802', 'Quota User', 'quota-rollback@example.com', 'hash',
                '00000000-0000-0000-0000-000000000801', 'user', false, 'active', false, 'complete', true, false, NOW(), NOW()
            )
        """))
        connection.execute(text("UPDATE users SET daily_token_limit = 0 WHERE id = '00000000-0000-0000-0000-000000000802'"))

    with pytest.raises(RuntimeError, match="user quota limits are populated"):
        command.downgrade(alembic_config(), "b9c0d1e2f3a5")

    with isolated_engine.begin() as connection:
        connection.execute(text("UPDATE users SET daily_token_limit = NULL"))
        connection.execute(text("""
            INSERT INTO user_quota_policy_events (
                id, operation_id, target_user_id, actor_user_id_snapshot, event_type, resource, period,
                reason_code, reason, amount, effective_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000803', '00000000-0000-0000-0000-000000000804',
                '00000000-0000-0000-0000-000000000802', '00000000-0000-0000-0000-000000000802',
                'grant', 'tokens', 'daily', 'temporary_allowance', 'rollback blocker', 1, NOW()
            )
        """))
    with pytest.raises(RuntimeError, match="user_quota_policy_events contains rows"):
        command.downgrade(alembic_config(), "b9c0d1e2f3a5")

    with isolated_engine.begin() as connection:
        connection.execute(text("DELETE FROM user_quota_policy_events"))
        connection.execute(text("""
            INSERT INTO provider_attempts (
                id, team_id, owner_user_id, correlation_id, attempt_number, attempt_kind, resource,
                status, reserved_units, authorized_at, reservation_valid_until
            ) VALUES (
                '00000000-0000-0000-0000-000000000805', '00000000-0000-0000-0000-000000000801',
                '00000000-0000-0000-0000-000000000802', '00000000-0000-0000-0000-000000000806', 1,
                'llm_generation', 'tokens', 'reserved', 1, NOW(), NOW() + INTERVAL '1 minute'
            )
        """))
    with pytest.raises(RuntimeError, match="provider_attempts contains rows"):
        command.downgrade(alembic_config(), "b9c0d1e2f3a5")

    with isolated_engine.begin() as connection:
        connection.execute(text("DELETE FROM provider_attempts"))
        connection.execute(text("""
            INSERT INTO task_dispatch_outbox (task_id, dispatch_kind, state, source_kind, source_id)
            VALUES (
                '00000000-0000-0000-0000-000000000807', 'generation', 'pending', 'generated_document',
                '00000000-0000-0000-0000-000000000808'
            )
        """))
    with pytest.raises(RuntimeError, match="task_dispatch_outbox contains rows"):
        command.downgrade(alembic_config(), "b9c0d1e2f3a5")

    with isolated_engine.begin() as connection:
        connection.execute(text("DELETE FROM task_dispatch_outbox"))
    command.downgrade(alembic_config(), "b9c0d1e2f3a5")

    inspector = inspect(isolated_engine)
    assert not {"user_quota_policy_events", "provider_attempts", "task_dispatch_outbox"} & set(inspector.get_table_names())
    assert not {
        "daily_token_limit",
        "monthly_token_limit",
        "daily_audio_seconds_limit",
        "monthly_audio_seconds_limit",
    } & {column["name"] for column in inspector.get_columns("users")}
    with isolated_engine.connect() as connection:
        remaining_enums = connection.execute(
            text(
                "SELECT typname FROM pg_type WHERE typname IN "
                "('quotaresource', 'quotaperiod', 'userquotapolicyeventtype', 'userquotareasoncode', "
                "'attemptkind', 'attemptstatus', 'attemptoutcome', 'providersettlementbasis', "
                "'taskdispatchkind', 'taskdispatchstate', "
                "'taskdispatchsourcekind')"
            )
        ).scalars().all()
    assert remaining_enums == []
