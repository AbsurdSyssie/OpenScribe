"""Backfill only definitive timeout usage while preserving independent success."""
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    ProviderAttempt,
    ProviderFeatureType,
    ProviderSettlementBasis,
    ProviderUsageEvent,
    ProviderUsageEventType,
    QuotaResource,
    Team,
    utcnow,
)
from tests.test_migrations import alembic_config, engine, reset_public_schema


@pytest.mark.migration
def test_timeout_waiver_migration_backfills_only_proven_llm_timeouts():
    reset_public_schema()
    config = alembic_config()
    command.upgrade(config, "g0b1c2d3e4f5")

    with Session(engine) as db:
        team = Team(name="Synthetic timeout migration", name_key="synthetic timeout migration")
        db.add(team)
        db.flush()
        now = utcnow()

        def transport_event(code, ordinal=1):
            return {
                "occurred_at": now.isoformat(timespec="seconds"),
                "request_ordinal": ordinal,
                "duration_ms": 5,
                "code": code,
                "http_status": None,
                "retry_wait_ms": 0,
            }

        def settled_attempt(*, kind, resource=QuotaResource.tokens, basis,
                            units, outcome, error=None, events=None, reported=None,
                            measured=None):
            attempt = ProviderAttempt(
                team_id=team.id,
                correlation_id=uuid4(),
                attempt_number=1,
                attempt_kind=kind,
                resource=resource,
                status=AttemptStatus.settled,
                outcome=outcome,
                settlement_basis=basis,
                reserved_units=100,
                settled_units=units,
                reported_input_tokens=(reported[0] if reported else None),
                reported_output_tokens=(reported[1] if reported else None),
                reported_total_tokens=(reported[2] if reported else None),
                measured_audio_seconds=measured,
                error_code=error,
                transport_events_json=events or [],
                authorized_at=now,
                reservation_valid_until=now + timedelta(minutes=2),
                submitted_at=now,
                deadline_at=now + timedelta(minutes=1),
                settled_at=now + timedelta(seconds=10),
            )
            db.add(attempt)
            db.flush()
            return attempt.id

        terminal_timeout_id = settled_attempt(
            kind=AttemptKind.llm_generation,
            basis=ProviderSettlementBasis.conservative_unknown,
            units=100,
            outcome=AttemptOutcome.unknown,
            events=[transport_event("timeout")],
            reported=(7, 9, None),
        )
        unrelated_unknown_id = settled_attempt(
            kind=AttemptKind.llm_generation,
            basis=ProviderSettlementBasis.conservative_unknown,
            units=100,
            outcome=AttemptOutcome.unknown,
            error="provider_unavailable",
            events=[transport_event("connection_error")],
        )
        recovered_success_id = settled_attempt(
            kind=AttemptKind.llm_generation,
            basis=ProviderSettlementBasis.reported,
            units=16,
            outcome=AttemptOutcome.succeeded,
            events=[transport_event("success"), transport_event("timeout", ordinal=2)],
            reported=(7, 9, 16),
        )
        stt_id = settled_attempt(
            kind=AttemptKind.stt_conversation,
            resource=QuotaResource.audio_seconds,
            basis=ProviderSettlementBasis.measured,
            units=12,
            outcome=AttemptOutcome.succeeded,
            measured=12,
        )

        transcript_id = uuid4()
        timed_out_event = ProviderUsageEvent(
            transcript_id=transcript_id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.failed,
            total_tokens=16,
            prompt_tokens=7,
            completion_tokens=9,
            estimated_cost_usd=0.001,
            error_code="llm_provider_timeout",
            provider_error_code="timeout",
        )
        checker_success_event = ProviderUsageEvent(
            transcript_id=transcript_id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed,
            total_tokens=5,
            prompt_tokens=2,
            completion_tokens=3,
            estimated_cost_usd=0.002,
        )
        db.add_all([timed_out_event, checker_success_event])
        db.commit()
        timed_out_event_id = timed_out_event.id
        checker_success_event_id = checker_success_event.id

    command.upgrade(config, "h1i2j3k4l5m6")
    with engine.connect() as connection:
        reflected_check = next(
            item["sqltext"] for item in inspect(connection).get_check_constraints("provider_attempts")
            if item["name"] == "ck_provider_attempts_settlement_basis_shape"
        )
        model_check = next(
            constraint.sqltext for constraint in ProviderAttempt.__table__.constraints
            if constraint.name == "ck_provider_attempts_settlement_basis_shape"
        )
        migration = ScriptDirectory.from_config(config).get_revision("h1i2j3k4l5m6").module
        assert str(model_check) == migration._basis_constraint(True)
        for expression in (reflected_check, model_check):
            assert "timeout_waived" in str(expression)
            assert "conservative_unknown" in str(expression)
            assert "stream_authorized" in str(expression)

        rows = connection.execute(text(
            "SELECT id, settlement_basis::text, settled_units, reported_input_tokens, "
            "reported_output_tokens, reported_total_tokens, "
            "outcome::text, error_code, transport_events_json FROM provider_attempts WHERE id = ANY(:ids)"
        ), {"ids": [terminal_timeout_id, unrelated_unknown_id, recovered_success_id, stt_id]}).all()
        attempts = {row.id: row for row in rows}
        waived = attempts[terminal_timeout_id]
        assert (waived.settlement_basis, waived.settled_units) == ("timeout_waived", 0)
        # Input/output metadata survives the backfill; unknown total usage is
        # still represented as NULL, as required by conservative_unknown shape.
        assert (waived.reported_input_tokens, waived.reported_output_tokens,
                waived.reported_total_tokens) == (7, 9, None)
        assert waived.outcome == "unknown"
        assert waived.error_code == "llm_provider_timeout"
        assert (attempts[unrelated_unknown_id].settlement_basis,
                attempts[unrelated_unknown_id].settled_units) == ("conservative_unknown", 100)
        # A final timeout transport event does not rewrite an already reported
        # success; the separate successful result remains billable.
        assert (attempts[recovered_success_id].settlement_basis,
                attempts[recovered_success_id].settled_units,
                attempts[recovered_success_id].reported_total_tokens) == ("reported", 16, 16)
        assert [event["code"] for event in attempts[recovered_success_id].transport_events_json] == [
            "success", "timeout",
        ]
        assert (attempts[stt_id].settlement_basis, attempts[stt_id].settled_units) == ("measured", 12)

        usage_rows = connection.execute(text(
            "SELECT id, event_type::text, prompt_tokens, completion_tokens, total_tokens, "
            "estimated_cost_usd FROM provider_usage_events WHERE id = ANY(:ids)"
        ), {"ids": [timed_out_event_id, checker_success_event_id]}).all()
        usage = {row.id: row for row in usage_rows}
        failed = usage[timed_out_event_id]
        assert (failed.event_type, failed.prompt_tokens, failed.completion_tokens,
                failed.total_tokens, failed.estimated_cost_usd) == ("failed", None, None, None, None)
        success = usage[checker_success_event_id]
        assert (success.event_type, success.prompt_tokens, success.completion_tokens,
                success.total_tokens, float(success.estimated_cost_usd)) == ("completed", 2, 3, 5, 0.002)

    # Downgrade restores the old conservative shape; re-upgrade re-applies the
    # evidence-based backfill, while the already-cleared event remains clear.
    command.downgrade(config, "g0b1c2d3e4f5")
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT settlement_basis::text, settled_units, reported_total_tokens "
            "FROM provider_attempts WHERE id = :id"
        ), {"id": terminal_timeout_id}).one() == ("conservative_unknown", 100, None)
        assert "timeout_waived" not in next(
            item["sqltext"] for item in inspect(connection).get_check_constraints("provider_attempts")
            if item["name"] == "ck_provider_attempts_settlement_basis_shape"
        )
    command.upgrade(config, "h1i2j3k4l5m6")
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT settlement_basis::text, settled_units FROM provider_attempts WHERE id = :id"
        ), {"id": terminal_timeout_id}).one() == ("timeout_waived", 0)
        assert connection.execute(text(
            "SELECT prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd "
            "FROM provider_usage_events WHERE id = :id"
        ), {"id": timed_out_event_id}).one() == (None, None, None, None)
