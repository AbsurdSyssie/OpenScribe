import base64
from uuid import UUID

from app.celery_app import celery_app
from app.db import SessionLocal
from app.services.templates import process_generated_document
from app.services.transcripts import process_transcript_ingestion_job


@celery_app.task(name="openscribe.process_transcript_ingestion_job")
def process_transcript_ingestion_job_task(*, job_id: str, audio_b64: str) -> None:
    audio_bytes = base64.b64decode(audio_b64.encode("ascii"))
    with SessionLocal() as db:
        process_transcript_ingestion_job(db, job_id=UUID(job_id), audio_bytes=audio_bytes)


def enqueue_transcript_ingestion_job(*, job_id: UUID, audio_bytes: bytes):
    payload = base64.b64encode(audio_bytes).decode("ascii")
    return process_transcript_ingestion_job_task.delay(job_id=str(job_id), audio_b64=payload)


@celery_app.task(name="openscribe.process_generated_document")
def process_generated_document_task(*, document_id: str) -> None:
    with SessionLocal() as db:
        process_generated_document(db, document_id=UUID(document_id))


def enqueue_generated_document_job(*, document_id: UUID):
    return process_generated_document_task.delay(document_id=str(document_id))
