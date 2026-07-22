"""Durable, metadata-only Celery dispatch outbox.

Creation and cancellation helpers flush only; their caller owns commit or
rollback. The publisher worker owns its session and commits its state changes.
This module never handles transcript-derived payloads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    TaskDispatchKind,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    utcnow,
)


DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_ATTEMPTS = 10
RETRY_BASE_SECONDS = 10
RETRY_MAX_SECONDS = 3600
PUBLISH_ERROR_CODE = "task_publish_failed"
_TASK_ID_NAMESPACE = UUID("7dd776d3-4e6b-4c50-bb51-df76be4a5c52")


class TaskDispatchPayloadMismatchError(ValueError):
    """A duplicate intent has different immutable dispatch metadata."""


class TaskDispatchPublisher(Protocol):
    def publish(self, dispatch: TaskDispatchOutbox) -> None:
        """Publish one dispatch using its stored deterministic task id."""


@dataclass(frozen=True, slots=True)
class CeleryTaskDispatchPublisher:
    """Celery adapter kept separate so publisher behavior is easy to mock."""

    def publish(self, dispatch: TaskDispatchOutbox) -> None:
        # Import lazily: app.tasks registers this worker and imports this module.
        from app.tasks import process_generated_document_task, process_transcript_ingestion_job_task

        task_id = str(dispatch.task_id)
        source_id = str(dispatch.source_id)
        if dispatch.dispatch_kind is TaskDispatchKind.generation:
            process_generated_document_task.apply_async(
                kwargs={"document_id": source_id},
                task_id=task_id,
            )
            return
        if dispatch.dispatch_kind is TaskDispatchKind.ingestion:
            process_transcript_ingestion_job_task.apply_async(
                kwargs={"job_id": source_id},
                task_id=task_id,
            )
            return
        raise ValueError("unsupported task dispatch kind")


def _expected_source_kind(dispatch_kind: TaskDispatchKind) -> TaskDispatchSourceKind:
    if dispatch_kind is TaskDispatchKind.generation:
        return TaskDispatchSourceKind.generated_document
    if dispatch_kind is TaskDispatchKind.ingestion:
        return TaskDispatchSourceKind.transcript_ingestion_job
    raise ValueError("unsupported task dispatch kind")


def _deterministic_task_id(dispatch_kind: TaskDispatchKind, source_kind: TaskDispatchSourceKind, source_id: UUID) -> UUID:
    return uuid5(_TASK_ID_NAMESPACE, f"{dispatch_kind.value}:{source_kind.value}:{source_id}")


def add_pending_task_dispatch(
    db: Session,
    *,
    dispatch_kind: TaskDispatchKind,
    source_id: UUID,
    source_kind: TaskDispatchSourceKind | None = None,
    task_id: UUID | None = None,
) -> TaskDispatchOutbox:
    """Add a pending intent and flush only; caller owns commit/rollback.

    The source mapping is the whole payload contract.  It is validated before
    persistence, and repeat calls return the original intent without changing
    its deterministic task id.
    """
    expected_source_kind = _expected_source_kind(dispatch_kind)
    if source_kind is not None and source_kind is not expected_source_kind:
        raise TaskDispatchPayloadMismatchError("dispatch kind and source kind do not match")
    source_kind = expected_source_kind
    expected_task_id = _deterministic_task_id(dispatch_kind, source_kind, source_id)
    if task_id is not None and task_id != expected_task_id:
        raise TaskDispatchPayloadMismatchError("task id does not match dispatch payload")

    existing = db.scalar(
        select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.dispatch_kind == dispatch_kind,
            TaskDispatchOutbox.source_kind == source_kind,
            TaskDispatchOutbox.source_id == source_id,
        )
    )
    if existing is not None:
        if existing.task_id != expected_task_id:
            raise TaskDispatchPayloadMismatchError("existing dispatch has different task id")
        return existing

    dispatch = TaskDispatchOutbox(
        task_id=expected_task_id,
        dispatch_kind=dispatch_kind,
        source_kind=source_kind,
        source_id=source_id,
        state=TaskDispatchState.pending,
        attempt_count=0,
        next_attempt_at=utcnow(),
    )
    db.add(dispatch)
    db.flush()
    return dispatch


def cancel_pending_task_dispatch(db: Session, *, task_id: UUID) -> bool:
    """Cancel only a pending row.  No commit; caller owns transaction."""
    dispatch = db.scalar(
        select(TaskDispatchOutbox)
        .where(TaskDispatchOutbox.task_id == task_id)
        .with_for_update()
    )
    if dispatch is None or dispatch.state is not TaskDispatchState.pending:
        return False
    dispatch.state = TaskDispatchState.cancelled
    dispatch.cancelled_at = utcnow()
    db.flush()
    return True


def _configured_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("TASK_OUTBOX_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS))))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def _retry_at(now: datetime, attempt_count: int) -> datetime:
    seconds = min(RETRY_BASE_SECONDS * (2 ** max(0, attempt_count - 1)), RETRY_MAX_SECONDS)
    return now + timedelta(seconds=seconds)


def publish_pending_task_dispatches(
    db: Session,
    *,
    publisher: TaskDispatchPublisher | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> int:
    """Publish at most one due, locked batch.  Returns successful publishes."""
    if batch_size < 1:
        return 0
    now = now or utcnow()
    publisher = publisher or CeleryTaskDispatchPublisher()
    max_attempts = max_attempts if max_attempts is not None else _configured_max_attempts()
    max_attempts = max(1, max_attempts)
    published = 0
    for _ in range(batch_size):
        # Claim one row per transaction. Committing a whole locked batch one
        # row at a time would release locks for unprocessed rows and allow a
        # concurrent publisher to send the same task.
        dispatch = db.scalar(
            select(TaskDispatchOutbox)
            .where(
                TaskDispatchOutbox.state == TaskDispatchState.pending,
                TaskDispatchOutbox.next_attempt_at <= now,
            )
            .order_by(TaskDispatchOutbox.next_attempt_at, TaskDispatchOutbox.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if dispatch is None:
            break
        try:
            publisher.publish(dispatch)
        except Exception:
            # Exception details can contain provider/task content. Persist code only.
            dispatch.attempt_count += 1
            dispatch.last_error_code = PUBLISH_ERROR_CODE
            if dispatch.attempt_count >= max_attempts:
                dispatch.state = TaskDispatchState.failed
                dispatch.failed_at = now
            else:
                dispatch.next_attempt_at = _retry_at(now, dispatch.attempt_count)
            db.commit()
            continue

        dispatch.state = TaskDispatchState.published
        dispatch.published_at = now
        dispatch.last_error_code = None
        db.commit()
        published += 1
    return published


def publish_task_dispatch(
    db: Session,
    *,
    task_id: UUID,
    publisher: TaskDispatchPublisher | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Publish exactly one outbox row immediately, claiming it with FOR UPDATE.

    Returns True if the row was successfully published. Returns False if it is
    missing, already terminal, or the publish attempt failed and the row was
    left for the Beat fallback publisher.

    Unlike ``publish_pending_task_dispatches`` this only touches the single row
    identified by ``task_id`` and never advances the cursor past it. The caller
    owns the surrounding transaction boundary: the function commits each
    individual publish attempt (success or failure) before returning so a network
    failure cannot leave a half-published row visible to consumers.
    """
    now = now or utcnow()
    publisher = publisher or CeleryTaskDispatchPublisher()
    max_attempts_setting = max_attempts if max_attempts is not None else _configured_max_attempts()
    max_attempts_setting = max(1, max_attempts_setting)
    dispatch = db.scalar(
        select(TaskDispatchOutbox)
        .where(TaskDispatchOutbox.task_id == task_id)
        .with_for_update()
    )
    if dispatch is None:
        return False
    if dispatch.state is not TaskDispatchState.pending:
        # Already published, cancelled, or failed by an earlier round. Do not
        # duplicate or replay; the Beat fallback remains the recovery lane.
        return False
    try:
        publisher.publish(dispatch)
    except Exception:
        # Exception details can contain provider/task content. Persist code only.
        dispatch.attempt_count += 1
        dispatch.last_error_code = PUBLISH_ERROR_CODE
        if dispatch.attempt_count >= max_attempts_setting:
            dispatch.state = TaskDispatchState.failed
            dispatch.failed_at = now
        else:
            dispatch.next_attempt_at = _retry_at(now, dispatch.attempt_count)
        db.commit()
        return False

    dispatch.state = TaskDispatchState.published
    dispatch.published_at = now
    dispatch.last_error_code = None
    db.commit()
    return True


def find_waiting_generation_dispatches_for_transcript(
    db: Session, *, transcript_id: UUID
) -> list[TaskDispatchOutbox]:
    """Return active generation dispatches for one transcript.

    Used by the transcript-completion trigger to fan generation out without
    waiting for each generation task's scheduled retry. Normally these rows
    are already ``published`` before the worker discovers that transcription
    is still in progress; ``pending`` rows cover broker publication failures.
    """
    # Imported lazily to avoid an import cycle: app.models -> app.services.*
    # callers already depend on this module.
    from app.models import GeneratedDocument, GeneratedDocumentStatus

    return list(
        db.scalars(
            select(TaskDispatchOutbox)
            .where(
                TaskDispatchOutbox.dispatch_kind == TaskDispatchKind.generation,
                TaskDispatchOutbox.state.in_(
                    (TaskDispatchState.pending, TaskDispatchState.published)
                ),
            )
            .join(
                GeneratedDocument,
                GeneratedDocument.id == TaskDispatchOutbox.source_id,
            )
            .where(
                GeneratedDocument.transcript_id == transcript_id,
                GeneratedDocument.status == GeneratedDocumentStatus.queued,
            )
        )
    )


def wake_published_generation_task_dispatch(
    db: Session,
    *,
    task_id: UUID,
    publisher: TaskDispatchPublisher | None = None,
) -> bool:
    """Republish one already-dispatched generation task as a readiness wake-up.

    This deliberately does not reopen or mutate the durable outbox lifecycle.
    Duplicate delivery is safe because generation workers take a per-document
    preparation guard before redaction, credential resolution, or provider
    dispatch. The task's scheduled retry remains the fallback if this
    best-effort broker publish fails.
    """
    dispatch = db.scalar(
        select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.task_id == task_id,
            TaskDispatchOutbox.dispatch_kind == TaskDispatchKind.generation,
            TaskDispatchOutbox.state == TaskDispatchState.published,
        )
    )
    if dispatch is None:
        return False
    (publisher or CeleryTaskDispatchPublisher()).publish(dispatch)
    return True


def try_publish_task_dispatch_safely(task_id) -> None:
    """Best-effort immediate publish from the request/commit path.

    A failure here is non-fatal: the durable outbox row stays pending and the
    Beat-driven publisher remains the fallback. Caller context is logged with
    tones that never leak confidential content.
    """
    import logging

    logger = logging.getLogger("openscribe.task_outbox")
    try:
        with SessionLocal() as db:
            publish_task_dispatch(db, task_id=task_id)
    except Exception:
        logger.warning(
            "task_dispatch_fast_path_skipped",
            extra={"event": "task_dispatch_fast_path_skipped"},
        )


def try_wake_published_generation_task_dispatch_safely(task_id) -> None:
    """Best-effort wake-up for generation work already sent to Celery."""
    import logging

    logger = logging.getLogger("openscribe.task_outbox")
    try:
        with SessionLocal() as db:
            wake_published_generation_task_dispatch(db, task_id=task_id)
    except Exception:
        logger.warning(
            "generation_dispatch_wake_skipped",
            extra={"event": "generation_dispatch_wake_skipped"},
        )
