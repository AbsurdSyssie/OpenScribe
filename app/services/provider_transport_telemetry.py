"""Allowlisted transport observations; no provider or clinical payloads."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from app.services.llm_execution import ExecutionBudget, execution_policy
from app.errors import AppError
from uuid import UUID
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import AttemptKind, AttemptStatus, ProviderAttempt, Transcript

from app.provider_transport_schema import EVENT_CODES, EVENT_KEYS


def validate_transport_event(event: dict[str, object]) -> dict[str, object]:
    if set(event) != EVENT_KEYS or not isinstance(event.get("code"), str) or event["code"] not in EVENT_CODES:
        raise ValueError("Invalid transport event")
    for key, minimum, maximum in (("request_ordinal", 1, 4), ("duration_ms", 0, 150000), ("retry_wait_ms", 0, 30000)):
        if type(event[key]) is not int or not minimum <= event[key] <= maximum:
            raise ValueError("Invalid transport event")
    status = event["http_status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("Invalid transport event")
    occurred = event["occurred_at"]
    if not isinstance(occurred, str):
        raise ValueError("Invalid transport event")
    parsed = datetime.fromisoformat(occurred)
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("Invalid transport event")
    return dict(event)


def transport_observer(attempt_id: UUID, *, session_factory: Callable[[], Session] = SessionLocal):
    """Persist observations independently before waiting; never resurrect work.

    Lock transcript before attempt, matching lifecycle deletion lock order.
    A session-factory seam lets committed-connection tests bind isolated engines.
    """
    def observe(event: dict[str, object] | None) -> bool:
        validated = validate_transport_event(event) if event is not None else None
        with session_factory() as db:
            identity = db.get(ProviderAttempt, attempt_id)
            if identity is None:
                return False
            now = datetime.now(timezone.utc)
            root_live = False
            if identity.transcript_id is not None:
                root = db.scalar(select(Transcript).where(Transcript.id == identity.transcript_id).with_for_update())
                root_live = root is not None and root.retention_expires_at > now

            attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.id == attempt_id).execution_options(populate_existing=True).with_for_update())
            if attempt is None:
                return False
            active = root_live and attempt.status is AttemptStatus.submitted and attempt.deadline_at is not None and attempt.deadline_at > now
            if validated is None:
                db.rollback()
                return active
            if attempt.status not in {AttemptStatus.submitted, AttemptStatus.settled}:
                return False
            events = list(attempt.transport_events_json or [])
            if len(events) >= 4 or validated["request_ordinal"] != len(events) + 1:
                return False
            attempt.transport_events_json = events + [validated]
            db.commit()
            return active
    observe.is_active = lambda: observe(None)
    return observe


@contextmanager
def provider_attempt_execution(db: Session, *, attempt_id: UUID, maximum_seconds: float, budget: ExecutionBudget | None = None):
    attempt = db.get(ProviderAttempt, attempt_id)
    if attempt is None or attempt.status is not AttemptStatus.submitted or attempt.deadline_at is None:
        raise AppError(409, "llm_attempt_inactive", "The generation attempt is no longer active")
    stage = ExecutionBudget.until(attempt.deadline_at, maximum_seconds=maximum_seconds)
    if budget is not None:
        stage = ExecutionBudget(min(stage.deadline, budget.deadline))
    # Bound to the caller's engine/isolated test connection; transactions remain
    # separate in deployment and fixture savepoints remain isolated in tests.
    observer = transport_observer(attempt_id, session_factory=lambda: Session(bind=db.get_bind(), join_transaction_mode="create_savepoint"))
    with execution_policy(budget=stage, observer=observer):
        yield
