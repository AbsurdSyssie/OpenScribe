"""Exercise the deployed backup schema, including its guarded downgrade."""
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import AttemptKind, AttemptStatus, ProviderAttempt, QuotaResource, Team, utcnow
from tests.test_migrations import alembic_config, engine, reset_public_schema


@pytest.mark.migration
def test_backup_migration_round_trip_and_retained_attempt_downgrade_guard():
    reset_public_schema()
    config = alembic_config()
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert "team_llm_backup_selections" in inspector.get_table_names()
    for table in (
        "generated_documents", "template_suggestion_jobs", "consultation_split_analyses",
        "consultation_split_batches", "consultation_split_executions",
    ):
        columns = {item["name"] for item in inspector.get_columns(table)}
        assert {"backup_llm_config_id", "backup_provider_snapshot_encrypted"} <= columns
    indexes = {item["name"]: item for item in inspector.get_indexes("provider_attempts")}
    primary_index = indexes["uq_provider_attempts_split_execution"]
    assert primary_index["unique"]
    assert "backup_of_attempt_id IS NULL" in str(primary_index["dialect_options"]["postgresql_where"])

    # Metadata-only attempts suffice to prove the downgrade cannot discard
    # durable provider accounting. No content or credentials enter these rows.
    with Session(engine) as db:
        team = Team(name="Synthetic backup migration", name_key="synthetic backup migration")
        db.add(team)
        db.flush()
        now = utcnow()
        parent = ProviderAttempt(
            id=uuid4(), team_id=team.id, correlation_id=uuid4(), attempt_number=1,
            attempt_kind=AttemptKind.llm_generation, resource=QuotaResource.tokens,
            status=AttemptStatus.reserved, reserved_units=1, authorized_at=now,
            reservation_valid_until=now + timedelta(minutes=1),
        )
        db.add(parent)
        db.flush()
        child = ProviderAttempt(
            team_id=team.id, correlation_id=parent.id, backup_of_attempt_id=parent.id,
            attempt_number=1, attempt_kind=AttemptKind.llm_generation,
            resource=QuotaResource.tokens, status=AttemptStatus.reserved, reserved_units=1,
            authorized_at=now, reservation_valid_until=now + timedelta(minutes=1),
        )
        db.add(child)
        db.commit()
        parent_id = parent.id
    with pytest.raises(RuntimeError, match="durable backup provider attempts"):
        command.downgrade(config, "d3e4f5a6b7c8")
    with Session(engine) as db:
        # Parent deletion cascades its backup accounting row.
        db.delete(db.get(ProviderAttempt, parent_id))
        db.commit()
    command.downgrade(config, "d3e4f5a6b7c8")
    assert "team_llm_backup_selections" not in inspect(engine).get_table_names()
    assert "backup_of_attempt_id" not in {
        item["name"] for item in inspect(engine).get_columns("provider_attempts")
    }
    command.upgrade(config, "head")
    assert "team_llm_backup_selections" in inspect(engine).get_table_names()
