from __future__ import annotations

from datetime import datetime
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models import ConsultationSplitExecution, ConsultationSplitExecutionKind
from app import tasks


_EVENT = "consultation_split_worker_failed"
_REASONS = {"unexpected_worker_error", "session_error", "rollback_error"}
_PHASES = {"session", "lookup", "analysis", "generation", "verification", "cleanup"}


class _FakeSession:
    def __init__(self, execution=None, *, get_error=None, rollback_errors=()):
        self.execution = execution
        self.get_error = get_error
        self.rollback_errors = list(rollback_errors)
        self.rollback_calls = 0
        self.mutations = []

    def get(self, model, execution_id):
        assert model is ConsultationSplitExecution
        assert isinstance(execution_id, UUID)
        if self.get_error is not None:
            raise self.get_error
        return self.execution

    def rollback(self):
        self.rollback_calls += 1
        if self.rollback_errors:
            raise self.rollback_errors.pop(0)


class _FakeSessionContext:
    def __init__(self, db=None, *, enter_error=None, exit_error=None):
        self.db = db
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.exit_calls = 0

    def __enter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self.db

    def __exit__(self, *_args):
        self.exit_calls += 1
        if self.exit_error is not None:
            raise self.exit_error
        return False


def _records(caplog):
    return [record for record in caplog.records if record.name == "openscribe.tasks"]


def _assert_safe_records(records, *, execution_id, expected):
    assert len(records) == len(expected)
    allowed_values = {
        "event": {_EVENT},
        "execution_id": {str(execution_id)},
        "phase": _PHASES,
        "reason_code": _REASONS,
    }
    for record, (phase, reason_code) in zip(records, expected, strict=True):
        assert record.msg == "%s execution_id=%s phase=%s reason_code=%s occurred_at=%s"
        assert record.getMessage() == (
            f"{_EVENT} execution_id={execution_id} phase={phase} "
            f"reason_code={reason_code} occurred_at={record.occurred_at}"
        )
        assert record.event == _EVENT
        assert record.execution_id == str(execution_id)
        assert record.phase == phase
        assert record.reason_code == reason_code
        parsed = datetime.fromisoformat(record.occurred_at)
        assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0
        assert record.exc_info is None
        assert record.stack_info is None
        assert record.phase in allowed_values["phase"]
        assert record.reason_code in allowed_values["reason_code"]
        assert all(allowed_values[key] == {getattr(record, key)} for key in ("event", "execution_id"))
        assert "synthetic-sensitive-payload" not in record.getMessage()
        assert "synthetic-sensitive-payload" not in repr(record.__dict__)


@pytest.mark.parametrize(
    ("kind", "runtime_name", "phase"),
    [
        (ConsultationSplitExecutionKind.analysis, "process_consultation_split_analysis_execution", "analysis"),
        (ConsultationSplitExecutionKind.generation, "process_consultation_split_generation_execution", "generation"),
        (ConsultationSplitExecutionKind.verification, "process_consultation_split_verification_execution", "verification"),
    ],
)
def test_split_execution_worker_logs_safe_runtime_failure_without_retry_or_mutation(
    monkeypatch, caplog, kind, runtime_name, phase,
):
    caplog.set_level(logging.ERROR, logger="openscribe.tasks")
    execution_id = uuid4()
    execution = SimpleNamespace(kind=kind)
    db = _FakeSession(execution)
    context = _FakeSessionContext(db)
    runtime_calls = []

    def fail(db_arg, *, execution_id):
        runtime_calls.append((db_arg, execution_id))
        raise RuntimeError("synthetic-sensitive-payload: fake clinical response")

    monkeypatch.setattr(tasks, "SessionLocal", lambda: context)
    monkeypatch.setattr(tasks, runtime_name, fail)

    tasks.process_consultation_split_execution_task(execution_id=str(execution_id))

    assert runtime_calls == [(db, execution_id)]
    assert db.rollback_calls == 2  # clean lookup transaction, then failure cleanup
    assert db.mutations == []
    assert context.exit_calls == 1
    assert "synthetic-sensitive-payload" not in caplog.text
    _assert_safe_records(_records(caplog), execution_id=execution_id,
                         expected=[(phase, "unexpected_worker_error")])


@pytest.mark.parametrize("failure_site", ["factory", "enter", "exit"])
def test_split_execution_worker_contains_session_boundary_failures(monkeypatch, caplog, failure_site):
    caplog.set_level(logging.ERROR, logger="openscribe.tasks")
    execution_id = uuid4()
    sensitive_error = RuntimeError("synthetic-sensitive-payload")
    if failure_site == "factory":
        monkeypatch.setattr(tasks, "SessionLocal", lambda: (_ for _ in ()).throw(sensitive_error))
    else:
        db = _FakeSession(SimpleNamespace(kind=ConsultationSplitExecutionKind.analysis))
        context = _FakeSessionContext(
            db,
            enter_error=sensitive_error if failure_site == "enter" else None,
            exit_error=sensitive_error if failure_site == "exit" else None,
        )
        monkeypatch.setattr(tasks, "SessionLocal", lambda: context)
        monkeypatch.setattr(
            tasks, "process_consultation_split_analysis_execution",
            lambda *_args, **_kwargs: None,
        )

    tasks.process_consultation_split_execution_task(execution_id=str(execution_id))

    _assert_safe_records(_records(caplog), execution_id=execution_id,
                         expected=[("session" if failure_site != "exit" else "cleanup", "session_error")])
    assert "synthetic-sensitive-payload" not in caplog.text


def test_split_execution_worker_logs_lookup_and_rollback_failures_safely(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="openscribe.tasks")
    execution_id = uuid4()
    lookup_error = RuntimeError("synthetic-sensitive-payload: lookup")
    rollback_error = RuntimeError("synthetic-sensitive-payload: rollback")
    db = _FakeSession(get_error=lookup_error, rollback_errors=[rollback_error])
    context = _FakeSessionContext(db)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: context)

    tasks.process_consultation_split_execution_task(execution_id=str(execution_id))

    assert db.rollback_calls == 1
    assert context.exit_calls == 1
    assert "synthetic-sensitive-payload" not in caplog.text
    _assert_safe_records(
        _records(caplog), execution_id=execution_id,
        expected=[("lookup", "unexpected_worker_error"), ("cleanup", "rollback_error")],
    )


def test_split_execution_worker_does_not_route_after_clean_rollback_failure(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="openscribe.tasks")
    execution_id = uuid4()
    db = _FakeSession(
        SimpleNamespace(kind=ConsultationSplitExecutionKind.analysis),
        rollback_errors=[RuntimeError("synthetic-sensitive-payload")],
    )
    monkeypatch.setattr(tasks, "SessionLocal", lambda: _FakeSessionContext(db))
    runtime_calls = []
    monkeypatch.setattr(
        tasks, "process_consultation_split_analysis_execution",
        lambda *_args, **_kwargs: runtime_calls.append(True),
    )

    tasks.process_consultation_split_execution_task(execution_id=str(execution_id))

    assert runtime_calls == []
    assert db.rollback_calls == 2
    assert "synthetic-sensitive-payload" not in caplog.text
    _assert_safe_records(
        _records(caplog), execution_id=execution_id,
        expected=[("cleanup", "rollback_error")],
    )


def test_split_execution_worker_contains_exit_failure_after_runtime_failure(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="openscribe.tasks")
    execution_id = uuid4()
    db = _FakeSession(SimpleNamespace(kind=ConsultationSplitExecutionKind.analysis))
    context = _FakeSessionContext(db, exit_error=RuntimeError("synthetic-sensitive-payload"))
    monkeypatch.setattr(tasks, "SessionLocal", lambda: context)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-sensitive-payload: provider content")

    monkeypatch.setattr(tasks, "process_consultation_split_analysis_execution", fail)
    tasks.process_consultation_split_execution_task(execution_id=str(execution_id))

    assert db.rollback_calls == 2
    assert context.exit_calls == 1
    assert "synthetic-sensitive-payload" not in caplog.text
    _assert_safe_records(
        _records(caplog), execution_id=execution_id,
        expected=[("analysis", "unexpected_worker_error"), ("cleanup", "session_error")],
    )


@pytest.mark.parametrize("case", ["success", "missing", "malformed"])
def test_split_execution_worker_keeps_success_and_noop_cases_silent(monkeypatch, caplog, case):
    caplog.set_level(logging.ERROR, logger="openscribe.tasks")
    execution_id = uuid4()
    if case == "malformed":
        tasks.process_consultation_split_execution_task(execution_id="not-a-uuid-sensitive-payload")
    else:
        kind = ConsultationSplitExecutionKind.analysis
        execution = SimpleNamespace(kind=kind) if case == "success" else None
        db = _FakeSession(execution)
        context = _FakeSessionContext(db)
        monkeypatch.setattr(tasks, "SessionLocal", lambda: context)
        if case == "success":
            monkeypatch.setattr(tasks, "process_consultation_split_analysis_execution", lambda *_a, **_k: None)
        tasks.process_consultation_split_execution_task(execution_id=str(execution_id))
    assert _records(caplog) == []
    assert "not-a-uuid-sensitive-payload" not in caplog.text
