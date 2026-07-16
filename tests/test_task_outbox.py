from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.models import (
    TaskDispatchKind,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    utcnow,
)
from app.services.task_outbox import (
    PUBLISH_ERROR_CODE,
    CeleryTaskDispatchPublisher,
    TaskDispatchPayloadMismatchError,
    add_pending_task_dispatch,
    cancel_pending_task_dispatch,
    publish_pending_task_dispatches,
)


class RecordingPublisher:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.dispatches: list[TaskDispatchOutbox] = []

    def publish(self, dispatch: TaskDispatchOutbox) -> None:
        self.dispatches.append(dispatch)
        if self.fail:
            raise RuntimeError("sensitive payload must not persist")


@pytest.mark.parametrize(
    ("dispatch_kind", "source_kind"),
    [
        (TaskDispatchKind.generation, TaskDispatchSourceKind.generated_document),
        (TaskDispatchKind.ingestion, TaskDispatchSourceKind.transcript_ingestion_job),
    ],
)
def test_add_pending_dispatch_is_idempotent_and_maps_source(db_session, dispatch_kind, source_kind):
    source_id = uuid4()

    first = add_pending_task_dispatch(db_session, dispatch_kind=dispatch_kind, source_id=source_id)
    second = add_pending_task_dispatch(db_session, dispatch_kind=dispatch_kind, source_id=source_id)
    db_session.commit()

    assert first is second
    assert first.source_kind is source_kind
    assert first.state is TaskDispatchState.pending
    assert len(db_session.scalars(select(TaskDispatchOutbox)).all()) == 1


def test_add_pending_dispatch_rejects_payload_mismatch(db_session):
    with pytest.raises(TaskDispatchPayloadMismatchError):
        add_pending_task_dispatch(
            db_session,
            dispatch_kind=TaskDispatchKind.generation,
            source_id=uuid4(),
            source_kind=TaskDispatchSourceKind.transcript_ingestion_job,
        )

    with pytest.raises(TaskDispatchPayloadMismatchError):
        add_pending_task_dispatch(
            db_session,
            dispatch_kind=TaskDispatchKind.ingestion,
            source_id=uuid4(),
            task_id=uuid4(),
        )


def test_default_publisher_maps_kwargs_and_uses_stored_deterministic_id(db_session, monkeypatch):
    generation = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    ingestion = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=uuid4())
    db_session.commit()
    calls = []

    def record_generation(**kwargs):
        calls.append(("generation", kwargs))

    def record_ingestion(**kwargs):
        calls.append(("ingestion", kwargs))

    monkeypatch.setattr("app.tasks.process_generated_document_task.apply_async", record_generation)
    monkeypatch.setattr("app.tasks.process_transcript_ingestion_job_task.apply_async", record_ingestion)
    publisher = CeleryTaskDispatchPublisher()
    publisher.publish(generation)
    publisher.publish(ingestion)

    assert calls == [
        ("generation", {"kwargs": {"document_id": str(generation.source_id)}, "task_id": str(generation.task_id)}),
        ("ingestion", {"kwargs": {"job_id": str(ingestion.source_id)}, "task_id": str(ingestion.task_id)}),
    ]


def test_publisher_marks_success(db_session):
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    db_session.commit()
    publisher = RecordingPublisher()

    assert publish_pending_task_dispatches(db_session, publisher=publisher, now=utcnow()) == 1
    db_session.refresh(dispatch)

    assert publisher.dispatches == [dispatch]
    assert dispatch.state is TaskDispatchState.published
    assert dispatch.published_at is not None
    assert dispatch.attempt_count == 0


def test_publisher_retries_with_safe_error_code_and_backoff(db_session):
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    db_session.commit()
    now = utcnow()

    assert publish_pending_task_dispatches(db_session, publisher=RecordingPublisher(fail=True), now=now) == 0
    db_session.refresh(dispatch)

    assert dispatch.state is TaskDispatchState.pending
    assert dispatch.attempt_count == 1
    assert dispatch.last_error_code == PUBLISH_ERROR_CODE
    assert dispatch.next_attempt_at == now + timedelta(seconds=10)
    assert dispatch.failed_at is None


def test_publisher_marks_failed_after_max_attempts(db_session):
    dispatch = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=uuid4())
    db_session.commit()
    now = utcnow()

    assert publish_pending_task_dispatches(
        db_session,
        publisher=RecordingPublisher(fail=True),
        max_attempts=1,
        now=now,
    ) == 0
    db_session.refresh(dispatch)

    assert dispatch.state is TaskDispatchState.failed
    assert dispatch.attempt_count == 1
    assert dispatch.failed_at == now
    assert dispatch.last_error_code == PUBLISH_ERROR_CODE


def test_cancel_only_pending_dispatch(db_session):
    pending = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    published = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=uuid4())
    published.state = TaskDispatchState.published
    published.published_at = utcnow()
    db_session.commit()

    assert cancel_pending_task_dispatch(db_session, task_id=pending.task_id) is True
    assert cancel_pending_task_dispatch(db_session, task_id=published.task_id) is False
    db_session.commit()
    assert pending.state is TaskDispatchState.cancelled
    assert pending.cancelled_at is not None
    assert published.state is TaskDispatchState.published


def test_cancel_claims_row_with_for_update_before_checking_pending_state():
    statements = []

    class CapturingSession:
        def scalar(self, statement):
            statements.append(statement)
            return None

    assert cancel_pending_task_dispatch(CapturingSession(), task_id=uuid4()) is False
    rendered = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in rendered
    assert "SKIP LOCKED" not in rendered


def test_publisher_skips_not_due_and_nonpending_rows(db_session):
    future = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    cancelled = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.ingestion, source_id=uuid4())
    future.next_attempt_at = utcnow() + timedelta(seconds=1)
    cancelled.state = TaskDispatchState.cancelled
    cancelled.cancelled_at = utcnow()
    db_session.commit()
    publisher = RecordingPublisher()

    assert publish_pending_task_dispatches(db_session, publisher=publisher, now=utcnow()) == 0
    assert publisher.dispatches == []


def test_publisher_respects_batch_bound(db_session):
    for _ in range(3):
        add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    db_session.commit()
    publisher = RecordingPublisher()

    assert publish_pending_task_dispatches(db_session, publisher=publisher, batch_size=2, now=utcnow()) == 2
    assert len(publisher.dispatches) == 2
    assert len(db_session.scalars(select(TaskDispatchOutbox).where(TaskDispatchOutbox.state == TaskDispatchState.pending)).all()) == 1


def test_concurrent_publishers_claim_and_commit_one_row_at_a_time(db_session):
    """A first publisher cannot unlock a preselected second row before it sends it."""
    first = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    second = add_pending_task_dispatch(db_session, dispatch_kind=TaskDispatchKind.generation, source_id=uuid4())
    now = utcnow()
    first.next_attempt_at = now - timedelta(seconds=1)
    second.next_attempt_at = now
    db_session.commit()
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    first_publish_started, release_first = Event(), Event()
    published_task_ids: list[object] = []

    class BlockingPublisher:
        def publish(self, dispatch):
            published_task_ids.append(dispatch.task_id)
            if dispatch.task_id == first.task_id:
                first_publish_started.set()
                assert release_first.wait(5)

    class ConcurrentPublisher:
        def publish(self, dispatch):
            published_task_ids.append(dispatch.task_id)

    def publish_first() -> None:
        with session_factory() as session:
            assert publish_pending_task_dispatches(session, publisher=BlockingPublisher(), batch_size=2, now=now) == 1

    def publish_second() -> None:
        assert first_publish_started.wait(5)
        with session_factory() as session:
            # First row remains locked; second publisher may claim only second row.
            assert publish_pending_task_dispatches(session, publisher=ConcurrentPublisher(), batch_size=2, now=now) == 1

    first_thread = Thread(target=publish_first)
    second_thread = Thread(target=publish_second)
    first_thread.start(); second_thread.start()
    assert first_publish_started.wait(5)
    second_thread.join(5)
    release_first.set()
    first_thread.join(5)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert sorted(published_task_ids, key=str) == sorted([first.task_id, second.task_id], key=str)
    db_session.expire_all()
    assert {item.state for item in db_session.scalars(select(TaskDispatchOutbox)).all()} == {TaskDispatchState.published}


def test_celery_outbox_task_is_registered_and_scheduled():
    import app.tasks  # noqa: F401 - triggers Celery task registration

    assert "openscribe.process_task_dispatch_outbox" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["publish-task-dispatch-outbox-every-10-seconds"]
    assert schedule == {
        "task": "openscribe.process_task_dispatch_outbox",
        "schedule": 10.0,
        "options": {"expires": 10.0},
    }
