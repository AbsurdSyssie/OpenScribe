"""Upgrade/downgrade tests for additive Telehealth capture metadata.

A downgrade must refuse to erase any discarded capture audit provenance.
"""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.db_utils import ensure_safe_test_database_url


@pytest.mark.migration
def test_telehealth_capture_migrations_round_trip_and_refuse_data_loss():
    url = ensure_safe_test_database_url()
    engine = create_engine(url, future=True, poolclass=NullPool)
    engine.dispose()
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    os.environ["ALEMBIC_DATABASE_URL"] = url
    command.upgrade(config, "head")
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        assert {"telehealth_capture_sessions", "telehealth_capture_parts"} <= tables
        checks = {check["name"] for check in inspector.get_check_constraints(
            "telehealth_capture_sessions",
        )}
        assert "ck_telehealth_capture_sessions_status" in checks
        named_indexes = {index["name"] for index in inspector.get_indexes(
            "telehealth_capture_parts",
        )}
        assert "ix_telehealth_capture_parts_source_expiry" in named_indexes

    # Synthetic rows exercise the data-preserving downgrade protection.
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
            VALUES ('00000000-0000-0000-0000-000000008801', 'Telehealth Migration',
                    'telehealth migration', 'active', 30, NOW(), NOW())
        """))
        connection.execute(text("""
            INSERT INTO users (
                id, full_name, email, password_hash, team_id, team_role,
                is_system_admin, status, must_change_password, onboarding_state,
                mfa_required, mfa_enabled, example_consultation_pending,
                created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000008802', 'Migration Owner',
                'telehealth-migration@example.test', 'synthetic-hash',
                '00000000-0000-0000-0000-000000008801', 'user',
                false, 'active', false, 'complete', true, false, false,
                NOW(), NOW()
            )
        """))
        connection.execute(text("""
            INSERT INTO transcripts (
                id, owner_user_id, team_id, title, ingestion_mode, status,
                next_live_chunk_sequence_no_applied, retention_days_applied,
                retention_expires_at, created_at, multiple_problems, is_example
            ) VALUES (
                '00000000-0000-0000-0000-000000008803',
                '00000000-0000-0000-0000-000000008802',
                '00000000-0000-0000-0000-000000008801',
                'Synthetic migration source', 'whole_file', 'ready', 1, 30,
                NOW() + INTERVAL '30 days', NOW(), false, false
            )
        """))
        connection.execute(text("""
            INSERT INTO telehealth_capture_sessions (
                id, transcript_id, owner_user_id, team_id, status,
                timeline_sample_rate, failure_code,
                recording_started_at, recording_stopped_at, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000008804',
                '00000000-0000-0000-0000-000000008803',
                '00000000-0000-0000-0000-000000008802',
                '00000000-0000-0000-0000-000000008801',
                'discarded', 48000, 'capture_discarded',
                NOW(), NOW(), NOW(), NOW()
            )
        """))
    with pytest.raises(DBAPIError, match="Cannot downgrade with discarded telehealth capture sessions"):
        command.downgrade(config, "o9p0q1r2s3t4")

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM telehealth_capture_sessions"))
    command.downgrade(config, "o9p0q1r2s3t4")
    with engine.connect() as connection:
        with pytest.raises(DBAPIError):
            # After downgrade, discarded is no longer a permitted state.
            with connection.begin():
                connection.execute(text("""
                    INSERT INTO telehealth_capture_sessions (
                        id, transcript_id, owner_user_id, team_id, status,
                        timeline_sample_rate, recording_started_at,
                        created_at, updated_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000008805',
                        '00000000-0000-0000-0000-000000008803',
                        '00000000-0000-0000-0000-000000008802',
                        '00000000-0000-0000-0000-000000008801',
                        'discarded', 48000, NOW(), NOW(), NOW()
                    )
                """))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        # The previous downgrade must have preserved core consultation rows.
        assert connection.execute(text(
            "SELECT count(*) FROM transcripts WHERE id = '00000000-0000-0000-0000-000000008803'"
        )).scalar_one() == 1
