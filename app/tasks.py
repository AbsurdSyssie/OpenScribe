import base64
from uuid import UUID

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import ConsultationSplitExecution, ConsultationSplitExecutionKind, GeneratedDocument, TemplateSuggestionJob, TranscriptIngestionJob, utcnow
from app.services.template_suggestions import process_template_suggestion
from app.services.consultation_split_runtime import process_consultation_split_analysis_execution
from app.services.consultation_split_generation_runtime import process_consultation_split_generation_execution
from app.services.consultation_split_verification_runtime import process_consultation_split_verification_execution
from app.services.templates import GeneratedDocumentWaitingForTranscript, process_generated_document
from app.services.transcripts import delete_expired_transcripts, expire_ingestion_source_audio, process_transcript_audio_cleanup_jobs, process_transcript_ingestion_job
from app.services.audit_retention import expire_security_audit_events
from app.services.legal_content_retention import expire_legal_document_versions
from app.services.provider_secret_cleanup import process_provider_secret_cleanup_jobs
from app.services.task_outbox import publish_pending_task_dispatches
from app.services.quota_lifecycle import process_quota_lifecycle


def _stamp_worker_received(db, *, model_class, record_id: UUID) -> None:
    """Set worker_received_at once on first task execution, not on retries."""
    record = db.get(model_class, record_id)
    if record is not None and record.worker_received_at is None:
        record.worker_received_at = utcnow()
        db.add(record)
        db.commit()


@celery_app.task(name="openscribe.process_transcript_ingestion_job")
def process_transcript_ingestion_job_task(*, job_id: str, audio_b64: str | None = None) -> None:
    legacy_audio_bytes = base64.b64decode(audio_b64.encode("ascii")) if audio_b64 else None
    with SessionLocal() as db:
        _stamp_worker_received(db, model_class=TranscriptIngestionJob, record_id=UUID(job_id))
        process_transcript_ingestion_job(db, job_id=UUID(job_id), legacy_audio_bytes=legacy_audio_bytes)


def enqueue_transcript_ingestion_job(*, job_id: UUID):
    return process_transcript_ingestion_job_task.delay(job_id=str(job_id))


@celery_app.task(name="openscribe.process_generated_document", bind=True, max_retries=None)
def process_generated_document_task(self, *, document_id: str) -> None:
    with SessionLocal() as db:
        _stamp_worker_received(db, model_class=GeneratedDocument, record_id=UUID(document_id))
        try:
            process_generated_document(db, document_id=UUID(document_id))
        except GeneratedDocumentWaitingForTranscript as exc:
            raise self.retry(exc=exc, countdown=exc.retry_seconds) from exc


def enqueue_generated_document_job(*, document_id: UUID):
    return process_generated_document_task.delay(document_id=str(document_id))


@celery_app.task(name="openscribe.process_template_suggestion")
def process_template_suggestion_task(*, job_id: str) -> None:
    with SessionLocal() as db:
        _stamp_worker_received(db, model_class=TemplateSuggestionJob, record_id=UUID(job_id))
        process_template_suggestion(db, job_id=UUID(job_id))


@celery_app.task(name="openscribe.process_consultation_split_execution")
def process_consultation_split_execution_task(*, execution_id: str) -> None:
    """Run one safe, at-most-once consultation-split execution delivery.

    The task payload remains an execution UUID only.  The service consumes
    terminal results so provider exceptions (which may carry unsafe upstream
    detail) never reach Celery's exception logging path.
    """
    try:
        parsed_execution_id = UUID(execution_id)
    except (TypeError, ValueError, AttributeError):
        return
    with SessionLocal() as db:
        try:
            execution = db.get(ConsultationSplitExecution, parsed_execution_id)
            if execution is None:
                return
            execution_kind = execution.kind
            # Both split runtimes require a clean session before credential
            # resolution.  The task's routing lookup must not leak its read
            # transaction into that boundary.
            db.rollback()
            if execution_kind is ConsultationSplitExecutionKind.analysis:
                process_consultation_split_analysis_execution(db, execution_id=parsed_execution_id)
            elif execution_kind is ConsultationSplitExecutionKind.generation:
                process_consultation_split_generation_execution(db, execution_id=parsed_execution_id)
            elif execution_kind is ConsultationSplitExecutionKind.verification:
                process_consultation_split_verification_execution(db, execution_id=parsed_execution_id)
        except Exception:
            # The service has terminalized known provider failures itself.  A
            # truly unexpected worker error must not hand Celery raw exception
            # text which could contain provider content.  Roll back any local
            # state and leave submitted/no-response work for conservative quota
            # lifecycle terminalization rather than retrying a provider call.
            db.rollback()


@celery_app.task(name="openscribe.process_task_dispatch_outbox")
def process_task_dispatch_outbox_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return publish_pending_task_dispatches(db, batch_size=batch_size)


@celery_app.task(name="openscribe.process_quota_lifecycle")
def process_quota_lifecycle_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return process_quota_lifecycle(db, batch_size=batch_size)


@celery_app.task(name="openscribe.delete_expired_transcripts")
def delete_expired_transcripts_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return delete_expired_transcripts(db, batch_size=batch_size)


@celery_app.task(name="openscribe.process_transcript_audio_cleanup_jobs")
def process_transcript_audio_cleanup_jobs_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return process_transcript_audio_cleanup_jobs(db, batch_size=batch_size)


@celery_app.task(name="openscribe.expire_ingestion_source_audio")
def expire_ingestion_source_audio_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return expire_ingestion_source_audio(db, batch_size=batch_size)


@celery_app.task(name="openscribe.expire_security_audit_events")
def expire_security_audit_events_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return expire_security_audit_events(db, batch_size=batch_size)


@celery_app.task(name="openscribe.expire_legal_document_versions")
def expire_legal_document_versions_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return expire_legal_document_versions(db, batch_size=batch_size)


@celery_app.task(name="openscribe.process_provider_secret_cleanup_jobs")
def process_provider_secret_cleanup_jobs_task(*, batch_size: int = 100) -> int:
    with SessionLocal() as db:
        return process_provider_secret_cleanup_jobs(db, batch_size=batch_size)
