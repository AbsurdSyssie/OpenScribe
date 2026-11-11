from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.services import llm_execution
from app.services.llm_adapters.types import LlmGenerationResult
from app.services.llm_adapters import runtime
from app.services.llm_backup_policy import can_failover


def _request_snapshot() -> dict[str, object]:
    return {
        "model": "publishers/google/models/gemini-synthetic",
        "contents": [{"role": "user", "parts": [{"text": "synthetic request"}]}],
        "config": {
            "system_instruction": "synthetic system",
            "temperature": 0.2,
            "max_output_tokens": 128,
        },
    }


def _result(tier: str | None = None) -> LlmGenerationResult:
    return LlmGenerationResult(
        text="synthetic response",
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
        duration_ms=1,
        provider_duration_ms=None,
        finish_reason="STOP",
        served_tier=tier,
    )


def _patch_no_wait(monkeypatch):
    monkeypatch.setattr(llm_execution.random, "uniform", lambda lower, _upper: lower)
    monkeypatch.setattr(llm_execution.time, "sleep", lambda _seconds: None)


def test_invoke_gemini_escalates_once_then_independent_call_starts_standard(monkeypatch):
    _patch_no_wait(monkeypatch)
    requests = []
    events = []
    responses = iter((AppError(
        429,
        "llm_provider_rate_limited",
        "synthetic capacity rejection",
        {"provider_http_status": 429, "temporary_capacity": True, "retry_after_seconds": 0},
    ), _result("priority"), _result("standard")))

    def fake_generate(**kwargs):
        requests.append(kwargs)
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    config = {
        "project_id": "synthetic-project",
        "location": "europe-west2",
        "capacity_mode": "shared",
        "priority_on_capacity_429": True,
    }
    credential = object()
    snapshot = _request_snapshot()

    with llm_execution.execution_policy(
        budget=llm_execution.ExecutionBudget.seconds(30),
        observer=lambda event: events.append(event) or True,
    ):
        first = runtime.invoke_gemini(provider_config=config, credential=credential, request_body=snapshot)
        second = runtime.invoke_gemini(provider_config=config, credential=credential, request_body=snapshot)

    assert [call["request_tier"] for call in requests] == ["standard", "priority", "standard"]
    assert all(call["project_id"] == "synthetic-project" for call in requests)
    assert all(call["location"] == "europe-west2" for call in requests)
    assert all(call["credentials"] is credential for call in requests)
    assert all(call["capacity_mode"] == "shared" for call in requests)
    assert all(call["request"] == requests[0]["request"] for call in requests)
    assert requests[0]["request"].model == "publishers/google/models/gemini-synthetic"
    assert first[0] == second[0] == "synthetic response"
    assert [event["request_tier"] for event in events] == ["standard", "priority", "standard"]
    assert events[1]["served_tier"] == "priority"
    assert events[2]["served_tier"] == "standard"
    assert "served_tier" not in first[1] and "served_tier" not in second[1]


def test_auto_mode_trace_records_priority_without_inventing_standard_header(monkeypatch):
    _patch_no_wait(monkeypatch)
    from app.services import generation_trace

    trace_calls = []
    requests = []
    events = []
    request_body = _request_snapshot()
    frozen_copy = copy.deepcopy(request_body)
    monkeypatch.setattr(
        generation_trace,
        "begin_request",
        lambda request, credential, ordinal, request_tier=None: (
            trace_calls.append((request, request_tier)) or None
        ),
    )

    def fake_generate(**kwargs):
        requests.append(kwargs)
        if kwargs["request_tier"] == "standard":
            raise AppError(429, "llm_provider_rate_limited", "synthetic capacity rejection", {
                "provider_http_status": 429,
                "temporary_capacity": True,
            })
        return _result("priority")

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    with llm_execution.execution_policy(
        budget=llm_execution.ExecutionBudget.seconds(30),
        observer=lambda event: events.append(event) or True,
    ):
        runtime.invoke_gemini(
            provider_config={
                "project_id": "synthetic-project",
                "location": "europe-west2",
                "capacity_mode": "auto",
                "priority_on_capacity_429": True,
            },
            credential=None,
            request_body=request_body,
        )

    assert [call["request_tier"] for call in requests] == ["standard", "priority"]
    assert [tier for _, tier in trace_calls] == [None, "priority"]
    assert all(trace_request == frozen_copy for trace_request, _ in trace_calls)
    assert events[0]["request_tier"] is None
    assert events[1]["request_tier"] == "priority"
    assert request_body == frozen_copy


def test_invoke_gemini_opt_out_keeps_four_standard_requests(monkeypatch):
    _patch_no_wait(monkeypatch)
    requests = []

    def fake_generate(**kwargs):
        requests.append(kwargs)
        raise AppError(429, "llm_provider_rate_limited", "synthetic capacity rejection", {
            "provider_http_status": 429,
            "temporary_capacity": True,
        })

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    with pytest.raises(AppError):
        runtime.invoke_gemini(
            provider_config={
                "project_id": "synthetic-project",
                "location": "europe-west2",
                "capacity_mode": "auto",
                "priority_on_capacity_429": False,
            },
            credential=None,
            request_body=_request_snapshot(),
        )

    assert [call["request_tier"] for call in requests] == ["standard"] * 4


@pytest.mark.parametrize(
    ("status", "temporary", "code"),
    [
        (429, False, "llm_provider_rate_limited"),  # hard quota classification
        (401, False, "llm_invalid_credential"),
        (403, False, "llm_permission_denied"),
        (503, True, "llm_generation_failed"),
        (None, False, "llm_provider_timeout"),
    ],
)
def test_invoke_gemini_does_not_escalate_non_capacity_failures(monkeypatch, status, temporary, code):
    requests = []

    def fake_generate(**kwargs):
        requests.append(kwargs)
        details = {"temporary_capacity": temporary}
        if status is not None:
            details["provider_http_status"] = status
        raise AppError(status or 504, code, "synthetic failure", details)

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    with pytest.raises(AppError):
        runtime.invoke_gemini(
            provider_config={
                "project_id": "synthetic-project",
                "location": "europe-west2",
                "capacity_mode": "auto",
                "priority_on_capacity_429": True,
            },
            credential=None,
            request_body=_request_snapshot(),
        )

    assert [call["request_tier"] for call in requests] == ["standard"]


@pytest.mark.parametrize("retry_after", [31, 900])
def test_invoke_gemini_does_not_escalate_when_retry_after_exceeds_budget_cap(monkeypatch, retry_after):
    requests = []

    def fake_generate(**kwargs):
        requests.append(kwargs)
        raise AppError(429, "llm_provider_rate_limited", "synthetic capacity rejection", {
            "provider_http_status": 429,
            "temporary_capacity": True,
            "retry_after_seconds": retry_after,
        })

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    with pytest.raises(AppError):
        runtime.invoke_gemini(
            provider_config={
                "project_id": "synthetic-project",
                "location": "europe-west2",
                "capacity_mode": "auto",
                "priority_on_capacity_429": True,
            },
            credential=None,
            request_body=_request_snapshot(),
        )

    assert [call["request_tier"] for call in requests] == ["standard"]


def test_lost_attempt_claim_after_standard_429_prevents_priority_dispatch(monkeypatch):
    _patch_no_wait(monkeypatch)
    dispatched = []
    events = []
    active_checks = iter((True, False))

    def observer(event):
        events.append(event)
        return True

    observer.is_active = lambda: next(active_checks)

    def standard(_budget):
        dispatched.append("standard")
        raise AppError(429, "llm_provider_rate_limited", "synthetic capacity rejection", {
            "provider_http_status": 429,
            "temporary_capacity": True,
        })

    def priority(_budget):
        dispatched.append("priority")
        return "should not dispatch"

    with llm_execution.execution_policy(
        budget=llm_execution.ExecutionBudget.seconds(30), observer=observer
    ), pytest.raises(AppError) as caught:
        llm_execution.bounded_request(standard, priority_fallback=priority, record_request_tier=True)

    assert caught.value.code == "llm_attempt_inactive"
    assert dispatched == ["standard"]
    assert len(events) == 1


def test_terminal_priority_capacity_429_remains_backup_eligible(monkeypatch):
    _patch_no_wait(monkeypatch)
    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs["request_tier"])
        raise AppError(429, "llm_provider_rate_limited", "synthetic capacity rejection", {
            "provider_http_status": 429,
            "temporary_capacity": True,
        })

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    with pytest.raises(AppError) as caught:
        runtime.invoke_gemini(
            provider_config={
                "project_id": "synthetic-project",
                "location": "europe-west2",
                "capacity_mode": "auto",
                "priority_on_capacity_429": True,
            },
            credential=None,
            request_body=_request_snapshot(),
        )

    assert calls == ["standard", "priority"]
    assert (caught.value.details or {}).get("backup_failure_class") == "temporary_capacity_exhausted"
    assert can_failover(caught.value)


def _load_migration(filename: str, module_name: str):
    migration_path = Path(__file__).parents[1] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_priority_transport_migration_round_trip_on_temporary_table():
    """Exercise the real migration DDL and downgrade data cleanup on PostgreSQL."""
    from tests.test_migrations import engine

    previous = _load_migration(
        "b9c0d1e2f3a6_add_provider_transport_events.py", "legacy_transport_migration"
    )
    migration = _load_migration(
        "02b3c4d5e6f7_add_priority_tiers_to_transport_events.py", "priority_tier_transport_migration"
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TEMP TABLE provider_attempts (
                id integer PRIMARY KEY,
                transport_events_json jsonb NOT NULL DEFAULT '[]'::jsonb,
                CONSTRAINT ck_provider_attempts_transport_events
                    CHECK (%s)
            ) ON COMMIT PRESERVE ROWS
        """ % previous.TRANSPORT_EVENTS_CONSTRAINT))
        migration.op = Operations(MigrationContext.configure(connection))

        legacy_events = [{
            "occurred_at": "2026-11-10T11:59:59+00:00",
            "request_ordinal": 1,
            "duration_ms": 9,
            "code": "success",
            "http_status": None,
            "retry_wait_ms": 0,
        }]
        connection.execute(
            text("INSERT INTO provider_attempts (id, transport_events_json) VALUES (0, CAST(:events AS jsonb))"),
            {"events": json.dumps(legacy_events)},
        )

        migration.upgrade()
        events = [
            {
                "occurred_at": "2026-11-10T12:00:00+00:00",
                "request_ordinal": 1,
                "duration_ms": 12,
                "code": "rate_limited",
                "http_status": 429,
                "retry_wait_ms": 1000,
                "request_tier": "standard",
                "served_tier": None,
            },
            {
                "occurred_at": "2026-11-10T12:00:01+00:00",
                "request_ordinal": 2,
                "duration_ms": 10,
                "code": "success",
                "http_status": None,
                "retry_wait_ms": 0,
                "request_tier": "priority",
                "served_tier": "priority",
            },
        ]
        connection.execute(
            text("INSERT INTO provider_attempts (id, transport_events_json) VALUES (1, CAST(:events AS jsonb))"),
            {"events": json.dumps(events)},
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("INSERT INTO provider_attempts (id, transport_events_json) VALUES (2, CAST(:events AS jsonb))"),
                {"events": json.dumps([dict(events[0], request_tier="unbounded")])},
            )

        migration.downgrade()
        downgraded = connection.execute(
            text("SELECT transport_events_json FROM provider_attempts WHERE id = 1")
        ).scalar_one()
        assert len(downgraded) == 2
        assert [event["request_ordinal"] for event in downgraded] == [1, 2]
        assert all("request_tier" not in event and "served_tier" not in event for event in downgraded)
        legacy_after_downgrade = connection.execute(
            text("SELECT transport_events_json FROM provider_attempts WHERE id = 0")
        ).scalar_one()
        assert legacy_after_downgrade == legacy_events

        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("INSERT INTO provider_attempts (id, transport_events_json) VALUES (3, CAST(:events AS jsonb))"),
                {"events": json.dumps([dict(events[0], request_tier="unbounded")])},
            )
