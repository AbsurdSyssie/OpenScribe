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


def _alembic_config() -> Config:
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DATABASE_URL
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def _reset_schema() -> None:
    engine.dispose()
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))


@pytest.mark.migration
def test_working_note_revision_migration_backfills_timestamped_notes_and_empty_roots():
    _reset_schema()
    config = _alembic_config()
    command.upgrade(config, "j2k3l4m5n6o7")

    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
            VALUES ('00000000-0000-0000-0000-000000000881', 'Revision Migration', 'revision migration',
                    'active', 30, NOW(), NOW())
        """))
        connection.execute(text("""
            INSERT INTO users (
                id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                must_change_password, onboarding_state, example_consultation_pending,
                mfa_required, mfa_enabled, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000882', 'Revision Owner', 'revision-migration@example.test',
                'hash', '00000000-0000-0000-0000-000000000881', 'user', false, 'active', false,
                'complete', false, false, true, NOW(), NOW()
            )
        """))
        connection.execute(text("""
            INSERT INTO transcripts (
                id, owner_user_id, team_id, title, working_note_updated_at, ingestion_mode, multiple_problems, is_example, status,
                next_live_chunk_sequence_no_applied, retention_days_applied, retention_expires_at, created_at
            ) VALUES
                ('00000000-0000-0000-0000-000000000883', '00000000-0000-0000-0000-000000000882',
                 '00000000-0000-0000-0000-000000000881', 'Existing note', NOW(), 'whole_file', false, false, 'ready',
                 1, 30, NOW() + INTERVAL '30 days', NOW()),
                ('00000000-0000-0000-0000-000000000884', '00000000-0000-0000-0000-000000000882',
                 '00000000-0000-0000-0000-000000000881', 'Empty note', NULL, 'whole_file', false, false, 'ready',
                 1, 30, NOW() + INTERVAL '30 days', NOW())
        """))

    command.upgrade(config, "head")
    with engine.connect() as connection:
        revisions = {
            row["title"]: row["working_note_revision"]
            for row in connection.execute(text("SELECT title, working_note_revision FROM transcripts")).mappings()
        }
        columns = {column["name"] for column in inspect(connection).get_columns("transcripts")}
        checks = {constraint["name"] for constraint in inspect(connection).get_check_constraints("transcripts")}

    assert revisions == {"Existing note": 1, "Empty note": 0}
    assert {"working_note_revision", "working_note_last_operation_id"} <= columns
    assert "ck_transcripts_working_note_revision_nonnegative" in checks

    command.downgrade(config, "j2k3l4m5n6o7")
    with engine.connect() as connection:
        old_columns = {column["name"] for column in inspect(connection).get_columns("transcripts")}
        retained_rows = connection.execute(text(
            "SELECT title, working_note_updated_at IS NOT NULL AS timestamped FROM transcripts"
        )).mappings().all()
        old_checks = {constraint["name"] for constraint in inspect(connection).get_check_constraints("transcripts")}

    assert "working_note_revision" not in old_columns
    assert "working_note_last_operation_id" not in old_columns
    assert "ck_transcripts_working_note_revision_nonnegative" not in old_checks
    assert {row["title"]: row["timestamped"] for row in retained_rows} == {"Existing note": True, "Empty note": False}

    command.upgrade(config, "head")
    with engine.connect() as connection:
        reupgraded = {
            row["title"]: row["working_note_revision"]
            for row in connection.execute(text("SELECT title, working_note_revision FROM transcripts")).mappings()
        }
    assert reupgraded == {"Existing note": 1, "Empty note": 0}
