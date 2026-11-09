"""Migration contract for the durable accepted-partial capture state."""

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.test_migrations import (
    _seed_problem_plan_owner,
    alembic_config,
    engine,
    reset_public_schema,
)


@pytest.mark.migration
def test_telehealth_partial_status_migrates_and_downgrade_refuses_live_partial_rows():
    reset_public_schema()
    config = alembic_config()
    previous = "p0q1r2s3t4u5"
    target = "a1b2c3d4e5f6"
    command.upgrade(config, previous)
    with engine.begin() as connection:
        ids = _seed_problem_plan_owner(connection)
    session_id = "00000000-0000-0000-0000-000000009205"
    insert = text("""
        INSERT INTO telehealth_capture_sessions (
            id, transcript_id, owner_user_id, team_id, status,
            timeline_sample_rate, recording_started_at, created_at, updated_at
        ) VALUES (
            :id, :transcript_id, :owner_id, :team_id, :status,
            48000, NOW(), NOW(), NOW()
        )
    """)
    with engine.begin() as connection:
        checks = {item["name"]: item["sqltext"] for item in inspect(connection).get_check_constraints(
            "telehealth_capture_sessions",
        )}
        assert "'partial'" not in checks["ck_telehealth_capture_sessions_status"]
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(insert, {
                    "id": session_id, "transcript_id": ids["transcript"],
                    "owner_id": ids["owner"], "team_id": ids["team"], "status": "partial",
                })

    command.upgrade(config, target)
    with engine.begin() as connection:
        checks = {item["name"]: item["sqltext"] for item in inspect(connection).get_check_constraints(
            "telehealth_capture_sessions",
        )}
        assert "'partial'" in checks["ck_telehealth_capture_sessions_status"]
        connection.execute(insert, {
            "id": session_id, "transcript_id": ids["transcript"],
            "owner_id": ids["owner"], "team_id": ids["team"], "status": "partial",
        })

    with pytest.raises(RuntimeError, match="Cannot downgrade telehealth partial status"):
        command.downgrade(config, previous)

    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE telehealth_capture_sessions SET status = 'failed' WHERE id = :id"
        ), {"id": session_id})
    command.downgrade(config, previous)
    command.upgrade(config, "head")
