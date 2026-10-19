"""Safe Celery failure diagnostics for generation task boundaries."""
import traceback
from uuid import uuid4

import pytest

from app import tasks
from app.services.templates import GeneratedDocumentWaitingForTranscript


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.mark.parametrize(
    ("task_name", "argument_name", "task_arg", "service_name", "record_model"),
    [
        (
            "process_transcript_ingestion_job_task", "job_id", "job", "process_transcript_ingestion_job",
            tasks.TranscriptIngestionJob,
        ),
        (
            "process_generated_document_task", "document_id", "document", "process_generated_document",
            tasks.GeneratedDocument,
        ),
        (
            "process_template_suggestion_task", "job_id", "suggestion", "process_template_suggestion",
            tasks.TemplateSuggestionJob,
        ),
    ],
)
def test_task_service_failures_emit_safe_log_and_content_free_traceback(
    monkeypatch, caplog, task_name, argument_name, task_arg, service_name, record_model,
):
    caplog.set_level("ERROR", logger="openscribe.tasks")
    record_id = uuid4()
    sentinel = "provider payload and SQL detail must stay private"
    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(tasks, "_stamp_worker_received", lambda *_args, **_kwargs: None)

    def fail(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(tasks, service_name, fail)
    task = getattr(tasks, task_name)
    with pytest.raises(RuntimeError) as raised:
        task.run(**{argument_name: str(record_id)})

    formatted = "".join(traceback.format_exception(raised.value))
    record = next(record for record in caplog.records if record.name == "openscribe.tasks")
    assert str(record_id) in record.getMessage()
    assert f"record_id={record_id}" in record.getMessage()
    assert record.event == "generation_task_boundary_failed"
    assert record.phase == "service"
    assert record.reason_code == "unexpected_error"
    assert sentinel not in formatted
    assert sentinel not in caplog.text
    assert str(record_model.__name__) not in record.getMessage()


def test_task_session_entry_failure_is_sanitized(monkeypatch, caplog):
    caplog.set_level("ERROR", logger="openscribe.tasks")
    record_id = uuid4()
    sentinel = "database connection string secret"

    class BrokenSession:
        def __enter__(self):
            raise RuntimeError(sentinel)

    monkeypatch.setattr(tasks, "SessionLocal", BrokenSession)

    with pytest.raises(RuntimeError) as raised:
        tasks.process_template_suggestion_task.run(job_id=str(record_id))

    formatted = "".join(traceback.format_exception(raised.value))
    record = next(record for record in caplog.records if record.name == "openscribe.tasks")
    assert record.phase == "session"
    assert record.reason_code == "unexpected_error"
    assert str(record_id) in record.getMessage()
    assert sentinel not in formatted
    assert sentinel not in caplog.text


def test_task_stamp_failure_is_sanitized(monkeypatch, caplog):
    caplog.set_level("ERROR", logger="openscribe.tasks")
    record_id = uuid4()
    sentinel = "database row payload sentinel"
    monkeypatch.setattr(tasks, "SessionLocal", _Session)

    def fail_stamp(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(tasks, "_stamp_worker_received", fail_stamp)

    with pytest.raises(RuntimeError) as raised:
        tasks.process_template_suggestion_task.run(job_id=str(record_id))

    formatted = "".join(traceback.format_exception(raised.value))
    record = next(record for record in caplog.records if record.name == "openscribe.tasks")
    assert record.phase == "stamp"
    assert record.reason_code == "unexpected_error"
    assert str(record_id) in record.getMessage()
    assert sentinel not in formatted
    assert sentinel not in caplog.text


def test_task_session_exit_failure_is_sanitized(monkeypatch, caplog):
    caplog.set_level("ERROR", logger="openscribe.tasks")
    record_id = uuid4()
    sentinel = "session close detail sentinel"

    class BrokenExitSession(_Session):
        def __exit__(self, *_args):
            raise RuntimeError(sentinel)

    monkeypatch.setattr(tasks, "SessionLocal", BrokenExitSession)
    monkeypatch.setattr(tasks, "_stamp_worker_received", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "process_template_suggestion", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError) as raised:
        tasks.process_template_suggestion_task.run(job_id=str(record_id))

    formatted = "".join(traceback.format_exception(raised.value))
    record = next(record for record in caplog.records if record.name == "openscribe.tasks")
    assert record.phase == "cleanup"
    assert record.reason_code == "unexpected_error"
    assert str(record_id) in record.getMessage()
    assert sentinel not in formatted
    assert sentinel not in caplog.text


def test_legacy_ingestion_audio_is_redacted_from_celery_failure_repr(monkeypatch, caplog):
    caplog.set_level("ERROR", logger="openscribe.tasks")
    record_id = uuid4()
    sentinel = "synthetic legacy audio payload sentinel"
    task = tasks.process_transcript_ingestion_job_task
    request_kwargs = {"job_id": str(record_id), "audio_b64": sentinel}
    task.push_request(kwargs=request_kwargs, kwargsrepr="unsafe original representation", called_directly=False)
    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(tasks, "_stamp_worker_received", lambda *_args, **_kwargs: None)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic service error")

    monkeypatch.setattr(tasks, "process_transcript_ingestion_job", fail)
    try:
        with pytest.raises(RuntimeError):
            task.run(job_id=str(record_id), audio_b64=sentinel)

        # Celery's _log_error prefers kwargsrepr; the actual task payload stays
        # intact for task behavior, signals, and any retry path.
        assert task.request.kwargsrepr == "{'job_id': '<redacted>', 'audio_b64': '<redacted>', 'dispatch_sequence': '<redacted>'}"
        assert task.request.kwargs["audio_b64"] == sentinel
        assert sentinel not in task.request.kwargsrepr
        assert sentinel not in caplog.text
    finally:
        task.pop_request()


def test_generated_document_wait_keeps_direct_call_behavior(monkeypatch):
    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(tasks, "_stamp_worker_received", lambda *_args, **_kwargs: None)

    def wait(*_args, **_kwargs):
        raise GeneratedDocumentWaitingForTranscript(uuid4(), retry_seconds=17)

    monkeypatch.setattr(tasks, "process_generated_document", wait)

    with pytest.raises(GeneratedDocumentWaitingForTranscript) as raised:
        tasks.process_generated_document_task.run(document_id=str(uuid4()))

    assert raised.value.retry_seconds == 17


def test_generated_document_wait_preserves_worker_retry_exception_and_countdown(monkeypatch):
    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(tasks, "_stamp_worker_received", lambda *_args, **_kwargs: None)
    waiting = GeneratedDocumentWaitingForTranscript(uuid4(), retry_seconds=17)

    def wait(*_args, **_kwargs):
        raise waiting

    monkeypatch.setattr(tasks, "process_generated_document", wait)
    retry_call = {}

    def retry(*, exc, countdown):
        retry_call.update(exc=exc, countdown=countdown)
        raise tasks.Retry(exc=exc, when=countdown)

    monkeypatch.setattr(tasks.process_generated_document_task, "retry", retry)
    with pytest.raises(tasks.Retry) as raised:
        tasks.process_generated_document_task.run(document_id=str(uuid4()))

    assert retry_call == {"exc": waiting, "countdown": 17}
    assert raised.value.exc is waiting
    assert raised.value.when == 17
