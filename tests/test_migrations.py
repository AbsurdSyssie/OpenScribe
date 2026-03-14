import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.db_utils import ensure_database_exists, ensure_safe_test_database_url


TEST_DATABASE_URL = ensure_safe_test_database_url()
ensure_database_exists(TEST_DATABASE_URL)
engine = create_engine(TEST_DATABASE_URL, future=True)


def alembic_config() -> Config:
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DATABASE_URL
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def reset_public_schema() -> None:
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


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
        "users",
        "user_sessions",
        "user_mfa_methods",
        "user_recovery_codes",
        "transcripts",
        "transcript_versions",
    }


@pytest.mark.migration
def test_alembic_head_uses_normalized_uniqueness_rules():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True)
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

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True)
    inspector = inspect(isolated_engine)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
    request_columns = {column["name"] for column in inspector.get_columns("account_requests")}

    assert {"full_name", "must_change_password", "onboarding_state"} <= user_columns
    assert {"session_token_hash", "auth_level", "status", "revoke_reason"} <= session_columns
    assert {"requested_name", "requested_email", "requested_team_name", "status"} <= request_columns
