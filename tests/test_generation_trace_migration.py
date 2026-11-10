"""Owner trace migration preserves accounts and enforces short retention."""
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import GenerationTraceEvent, Team, Transcript, TranscriptStatus, User, utcnow
from tests.test_migrations import alembic_config, engine, reset_public_schema


@pytest.mark.migration
def test_trace_migration_preserves_existing_user_and_purges_traces_on_downgrade():
    reset_public_schema()
    config = alembic_config()
    command.upgrade(config, "fb1c2d3e4f50")
    owner_id = uuid4()
    with Session(engine) as db:
        team = Team(name="Synthetic trace migration", name_key="synthetic trace migration")
        db.add(team)
        db.flush()
        team_id = team.id
        db.execute(text("""INSERT INTO users
            (id, email, password_hash, team_id, team_role, is_system_admin, status, onboarding_state,
             example_consultation_pending, mfa_required, mfa_enabled, must_change_password, created_at, updated_at)
            VALUES (:id, :email, :password, :team_id, 'user', false, 'active', 'complete', false, false, false, false, now(), now())"""),
            {"id": owner_id, "email": "trace-migration@example.com", "password": "synthetic", "team_id": team_id})
        db.commit()
    command.upgrade(config, "head")
    with Session(engine) as db:
        owner = db.get(User, owner_id)
        assert owner.generation_trace_enabled is False
        now = utcnow()
        root = Transcript(owner_user_id=owner_id, team_id=team_id, status=TranscriptStatus.ready,
                          retention_days_applied=2, retention_expires_at=now + timedelta(days=2))
        db.add(root)
        db.flush()
        root_id = root.id
        def event(expires):
            return GenerationTraceEvent(id=uuid4(), transcript_id=root_id, owner_user_id=owner_id,
                workflow="llm_generation", request_ordinal=1, captured_at=now, expires_at=expires, state="pending")
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(event(now + timedelta(hours=24, seconds=1)))
            db.flush()
        db.add(event(now + timedelta(hours=24)))
        db.commit()
    command.downgrade(config, "fb1c2d3e4f50")
    assert "generation_trace_events" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with Session(engine) as db:
        assert db.get(User, owner_id).generation_trace_enabled is False
        assert db.get(Transcript, root_id) is not None
        assert db.query(GenerationTraceEvent).count() == 0
