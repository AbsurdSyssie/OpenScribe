import base64
from uuid import UUID

from app.celery_app import celery_app
from app.db import SessionLocal
from app.services.templates import GeneratedDocumentWaitingForTranscript, process_generated_document
from app.services.transcripts import process_transcript_ingestion_job


@celery_app.task(name="openscribe.process_transcript_ingestion_job")
def process_transcript_ingestion_job_task(*, job_id: str, audio_b64: str | None = None) -> None:
    legacy_audio_bytes = base64.b64decode(audio_b64.encode("ascii")) if audio_b64 else None
    with SessionLocal() as db:
        process_transcript_ingestion_job(db, job_id=UUID(job_id), legacy_audio_bytes=legacy_audio_bytes)


def enqueue_transcript_ingestion_job(*, job_id: UUID):
    return process_transcript_ingestion_job_task.delay(job_id=str(job_id))


@celery_app.task(name="openscribe.process_generated_document", bind=True, max_retries=None)
def process_generated_document_task(self, *, document_id: str) -> None:
    with SessionLocal() as db:
        try:
            process_generated_document(db, document_id=UUID(document_id))
        except GeneratedDocumentWaitingForTranscript as exc:
            raise self.retry(exc=exc, countdown=exc.retry_seconds) from exc


def enqueue_generated_document_job(*, document_id: UUID):
    return process_generated_document_task.delay(document_id=str(document_id))
