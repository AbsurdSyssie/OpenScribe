import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
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
def test_alembic_upgrade_head_creates_expected_schema():
    reset_public_schema()

    command.upgrade(alembic_config(), "head")

    assert current_tables() == {
        "alembic_version",
        "account_requests",
        "teams",
        "team_stt_configs",
        "team_stt_selections",
        "users",
        "user_trusted_devices",
        "user_sessions",
        "user_mfa_methods",
        "user_recovery_codes",
        "transcripts",
        "transcript_ingestion_jobs",
        "transcript_versions",
    }


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
    transcript_columns = {column["name"] for column in inspector.get_columns("transcripts")}
    transcript_ingestion_job_columns = {column["name"] for column in inspector.get_columns("transcript_ingestion_jobs")}

    assert {"full_name", "must_change_password", "onboarding_state"} <= user_columns
    assert {"session_token_hash", "auth_level", "status", "revoke_reason"} <= session_columns
    assert {"device_token_hash", "last_mfa_verified_at", "expires_at", "revoke_reason"} <= trusted_device_columns
    assert {"requested_name", "requested_email", "requested_team_name", "status"} <= request_columns
    assert {"team_id", "adapter_kind", "base_url", "transcribe_path", "vault_secret_ref", "response_text_path", "available_models_json"} <= stt_columns
    assert {"team_id", "stt_config_id", "model_name_override", "language_override", "selected_by_user_id"} <= stt_selection_columns
    assert {"owner_user_id", "team_id", "current_draft_text_encrypted", "ingestion_mode", "next_live_chunk_sequence_no_applied"} <= transcript_columns
    assert {"transcript_id", "job_kind", "chunk_sequence_no", "status", "celery_task_id", "result_text_encrypted"} <= transcript_ingestion_job_columns


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
