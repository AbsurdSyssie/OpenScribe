"""Bounded metadata-only cleanup for quota attempts and dispatch intent.

Direct helpers flush only.  ``process_quota_lifecycle`` is the worker entry
point and is deliberately the only function in this module that commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AttemptOutcome,
    AttemptStatus,
    GeneratedDocument,
    GeneratedDocumentStatus,
    ProviderAttempt,
    ProviderSettlementBasis,
    QuotaResource,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    TranscriptStatus,
    User,
)


QUOTA_RESERVATION_EXPIRED = "quota_reservation_expired"
PROVIDER_ATTEMPT_OUTCOME_UNKNOWN = "provider_attempt_outcome_unknown"
TASK_DISPATCH_FAILED = "task_dispatch_failed"


@dataclass(frozen=True, slots=True)
class AttemptTerminalization:
    cancelled: int = 0
    settled_unknown: int = 0


def _utc(now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("quota lifecycle timestamps must be timezone-aware")
    return now.astimezone(UTC)


def _terminalize_locked_attempts(attempts: list[ProviderAttempt], now: datetime) -> AttemptTerminalization:
    cancelled = settled_unknown = 0
    for attempt in attempts:
        if attempt.status is AttemptStatus.reserved:
            attempt.status = AttemptStatus.cancelled
            attempt.outcome = AttemptOutcome.cancelled
            attempt.cancelled_at = now
            cancelled += 1
        elif attempt.status is AttemptStatus.submitted:
            attempt.status = AttemptStatus.settled
            attempt.outcome = AttemptOutcome.unknown
            attempt.settled_at = now
            if attempt.resource is QuotaResource.tokens:
                attempt.settlement_basis = ProviderSettlementBasis.conservative_unknown
                attempt.settled_units = attempt.reserved_units
            else:
                # Schema/reservation validation requires this server measurement.
                if attempt.measured_audio_seconds is None:
                    raise RuntimeError("submitted audio attempt is missing measured_audio_seconds")
                attempt.settlement_basis = ProviderSettlementBasis.measured
                attempt.settled_units = ceil(Decimal(str(attempt.measured_audio_seconds)))
            settled_unknown += 1
    return AttemptTerminalization(cancelled, settled_unknown)


def _lock_attempts(db: Session, where) -> list[ProviderAttempt]:
    return db.scalars(
        select(ProviderAttempt).where(where).order_by(ProviderAttempt.id).with_for_update()
    ).all()


def terminalize_attempts_for_transcripts(
    db: Session, transcript_ids: list[UUID] | tuple[UUID, ...], now: datetime
) -> AttemptTerminalization:
    """Terminalize all active attempt kinds for transcript roots. No commit."""
    ids = tuple(sorted(set(transcript_ids), key=str))
    if not ids:
        return AttemptTerminalization()
    now = _utc(now)
    identities = db.execute(
        select(Transcript.id, Transcript.owner_user_id).where(Transcript.id.in_(ids))
    ).all()
    owner_ids = tuple(sorted({item.owner_user_id for item in identities}, key=str))
    if owner_ids:
        db.scalars(select(User).where(User.id.in_(owner_ids)).order_by(User.id).with_for_update()).all()
    db.scalars(select(Transcript).where(Transcript.id.in_(ids)).order_by(Transcript.id).with_for_update()).all()
    result = _terminalize_locked_attempts(_lock_attempts(db, ProviderAttempt.transcript_id.in_(ids)), now)
    db.flush()
    return result


def terminalize_attempts_for_owner(db: Session, owner_user_id: UUID, now: datetime) -> AttemptTerminalization:
    """Terminalize active owner attempts before owner FK is nulled. No commit."""
    now = _utc(now)
    db.scalar(select(User).where(User.id == owner_user_id).with_for_update())
    result = _terminalize_locked_attempts(_lock_attempts(db, ProviderAttempt.owner_user_id == owner_user_id), now)
    db.flush()
    return result


def terminalize_attempts_for_generated_document(
    db: Session, document_id: UUID, now: datetime
) -> AttemptTerminalization:
    """Terminalize active document attempts before document deletion. No commit."""
    now = _utc(now)
    identity = db.scalar(select(GeneratedDocument).where(GeneratedDocument.id == document_id))
    if identity is None:
        return AttemptTerminalization()
    db.scalar(select(User).where(User.id == identity.owner_user_id).with_for_update())
    db.scalar(select(Transcript).where(Transcript.id == identity.transcript_id).with_for_update())
    db.scalar(select(GeneratedDocument).where(GeneratedDocument.id == document_id).with_for_update())
    result = _terminalize_locked_attempts(
        _lock_attempts(db, ProviderAttempt.generated_document_id == document_id), now
    )
    db.flush()
    return result


def cancel_pending_dispatches_for_sources(
    db: Session,
    *,
    generated_document_ids: tuple[UUID, ...] | list[UUID] = (),
    ingestion_job_ids: tuple[UUID, ...] | list[UUID] = (),
    now: datetime | None = None,
) -> int:
    """Cancel pending source dispatches only. Published dispatches are immutable."""
    now = _utc(now)
    document_ids = tuple(set(generated_document_ids))
    job_ids = tuple(set(ingestion_job_ids))
    if not document_ids and not job_ids:
        return 0
    clauses = []
    if document_ids:
        clauses.append((TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.generated_document) & (TaskDispatchOutbox.source_id.in_(document_ids)))
    if job_ids:
        clauses.append((TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.transcript_ingestion_job) & (TaskDispatchOutbox.source_id.in_(job_ids)))
    from sqlalchemy import or_
    dispatches = db.scalars(
        select(TaskDispatchOutbox).where(TaskDispatchOutbox.state == TaskDispatchState.pending, or_(*clauses))
        .order_by(TaskDispatchOutbox.task_id).with_for_update()
    ).all()
    for dispatch in dispatches:
        dispatch.state = TaskDispatchState.cancelled
        dispatch.cancelled_at = now
    db.flush()
    return len(dispatches)


def delete_dispatches_for_sources(
    db: Session,
    *,
    generated_document_ids: tuple[UUID, ...] | list[UUID] = (),
    ingestion_job_ids: tuple[UUID, ...] | list[UUID] = (),
) -> int:
    """Delete polymorphic dispatch metadata before its source is hard-deleted."""
    document_ids = tuple(set(generated_document_ids))
    job_ids = tuple(set(ingestion_job_ids))
    if not document_ids and not job_ids:
        return 0
    clauses = []
    if document_ids:
        clauses.append(
            (TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.generated_document)
            & TaskDispatchOutbox.source_id.in_(document_ids)
        )
    if job_ids:
        clauses.append(
            (TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.transcript_ingestion_job)
            & TaskDispatchOutbox.source_id.in_(job_ids)
        )
    from sqlalchemy import or_

    dispatches = db.scalars(
        select(TaskDispatchOutbox).where(or_(*clauses)).order_by(TaskDispatchOutbox.task_id).with_for_update()
    ).all()
    for dispatch in dispatches:
        db.delete(dispatch)
    db.flush()
    return len(dispatches)


def _fail_document(db: Session, document_id: UUID, error_code: str, now: datetime) -> bool:
    identity = db.scalar(select(GeneratedDocument).where(GeneratedDocument.id == document_id))
    if identity is None:
        return False
    # Keep normal owned lifecycle order: User -> Transcript -> source.
    db.scalar(select(User).where(User.id == identity.owner_user_id).with_for_update())
    db.scalar(select(Transcript).where(Transcript.id == identity.transcript_id).with_for_update())
    document = db.scalar(select(GeneratedDocument).where(GeneratedDocument.id == document_id).with_for_update())
    permitted = {GeneratedDocumentStatus.queued}
    if error_code == PROVIDER_ATTEMPT_OUTCOME_UNKNOWN:
        permitted.add(GeneratedDocumentStatus.processing)
    if document is None or document.status not in permitted:
        return False
    document.status = GeneratedDocumentStatus.failed
    document.error_code = error_code
    document.error_message = None
    document.completed_at = now
    cancel_pending_dispatches_for_sources(db, generated_document_ids=(document_id,), now=now)
    return True


def _fail_ingestion_job(db: Session, job_id: UUID, error_code: str, now: datetime) -> bool:
    identity = db.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.id == job_id))
    if identity is None:
        return False
    db.scalar(select(User).where(User.id == identity.owner_user_id).with_for_update())
    transcript = db.scalar(select(Transcript).where(Transcript.id == identity.transcript_id).with_for_update())
    job = db.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.id == job_id).with_for_update())
    permitted = {TranscriptIngestionJobStatus.queued}
    if error_code == PROVIDER_ATTEMPT_OUTCOME_UNKNOWN:
        permitted.add(TranscriptIngestionJobStatus.processing)
    if job is None or job.status not in permitted:
        return False
    job.status = TranscriptIngestionJobStatus.failed
    job.error_code = error_code
    job.error_message = None
    job.completed_at = now
    if transcript is not None and transcript.status is TranscriptStatus.transcribing:
        transcript.status = TranscriptStatus.failed
    cancel_pending_dispatches_for_sources(db, ingestion_job_ids=(job_id,), now=now)
    return True


def _reconcile_sources(db: Session, attempts: list[ProviderAttempt], now: datetime) -> int:
    changed = 0
    for attempt in attempts:
        error = QUOTA_RESERVATION_EXPIRED if attempt.status is AttemptStatus.cancelled else PROVIDER_ATTEMPT_OUTCOME_UNKNOWN
        if attempt.generated_document_id is not None:
            changed += _fail_document(db, attempt.generated_document_id, error, now)
        if attempt.transcript_ingestion_job_id is not None:
            changed += _fail_ingestion_job(db, attempt.transcript_ingestion_job_id, error, now)
    return changed


def process_quota_lifecycle(db: Session, batch_size: int = 100, now: datetime | None = None) -> int:
    """Commit one bounded cleanup batch; never reads or writes content payloads."""
    if batch_size <= 0:
        return 0
    now = _utc(now)
    # Read candidates without content columns, then lock ownership/source parents
    # before claiming attempts.  This preserves the normal User -> Transcript ->
    # source -> attempt order while SKIP LOCKED prevents competing workers from
    # processing an attempt twice.
    candidate_rows = db.execute(
        select(
            ProviderAttempt.id,
            ProviderAttempt.owner_user_id,
            ProviderAttempt.transcript_id,
            ProviderAttempt.generated_document_id,
            ProviderAttempt.transcript_ingestion_job_id,
        ).where(
            (ProviderAttempt.status == AttemptStatus.reserved) & (ProviderAttempt.reservation_valid_until <= now)
            | (ProviderAttempt.status == AttemptStatus.submitted) & (ProviderAttempt.deadline_at <= now)
        ).order_by(ProviderAttempt.reservation_valid_until, ProviderAttempt.deadline_at, ProviderAttempt.id).limit(batch_size)
    ).all()
    owner_ids = tuple(sorted({row.owner_user_id for row in candidate_rows if row.owner_user_id is not None}, key=str))
    transcript_ids = tuple(sorted({row.transcript_id for row in candidate_rows if row.transcript_id is not None}, key=str))
    document_ids = tuple(sorted({row.generated_document_id for row in candidate_rows if row.generated_document_id is not None}, key=str))
    job_ids = tuple(sorted({row.transcript_ingestion_job_id for row in candidate_rows if row.transcript_ingestion_job_id is not None}, key=str))
    if owner_ids:
        db.scalars(select(User).where(User.id.in_(owner_ids)).order_by(User.id).with_for_update()).all()
    if transcript_ids:
        db.scalars(select(Transcript).where(Transcript.id.in_(transcript_ids)).order_by(Transcript.id).with_for_update()).all()
    if document_ids:
        db.scalars(select(GeneratedDocument).where(GeneratedDocument.id.in_(document_ids)).order_by(GeneratedDocument.id).with_for_update()).all()
    if job_ids:
        db.scalars(select(TranscriptIngestionJob).where(TranscriptIngestionJob.id.in_(job_ids)).order_by(TranscriptIngestionJob.id).with_for_update()).all()
    candidate_ids = tuple(row.id for row in candidate_rows)
    expired = [] if not candidate_ids else db.scalars(
        select(ProviderAttempt).where(
            ProviderAttempt.id.in_(candidate_ids),
            (ProviderAttempt.status == AttemptStatus.reserved) & (ProviderAttempt.reservation_valid_until <= now)
            | (ProviderAttempt.status == AttemptStatus.submitted) & (ProviderAttempt.deadline_at <= now),
        ).order_by(ProviderAttempt.reservation_valid_until, ProviderAttempt.deadline_at, ProviderAttempt.id)
        .limit(batch_size).with_for_update(skip_locked=True)
    ).all()
    terminal = _terminalize_locked_attempts(expired, now)
    changed = terminal.cancelled + terminal.settled_unknown + _reconcile_sources(db, expired, now)
    remaining = batch_size - len(expired)
    if remaining:
        failed_documents = db.scalars(
            select(TaskDispatchOutbox)
            .join(
                GeneratedDocument,
                (TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.generated_document)
                & (TaskDispatchOutbox.source_id == GeneratedDocument.id),
            )
            .where(
                TaskDispatchOutbox.state == TaskDispatchState.failed,
                GeneratedDocument.status == GeneratedDocumentStatus.queued,
            )
            .order_by(TaskDispatchOutbox.failed_at, TaskDispatchOutbox.task_id)
            .limit(remaining)
        ).all()
        remaining -= len(failed_documents)
        failed_ingestion = [] if not remaining else db.scalars(
            select(TaskDispatchOutbox)
            .join(
                TranscriptIngestionJob,
                (TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.transcript_ingestion_job)
                & (TaskDispatchOutbox.source_id == TranscriptIngestionJob.id),
            )
            .where(
                TaskDispatchOutbox.state == TaskDispatchState.failed,
                TranscriptIngestionJob.status == TranscriptIngestionJobStatus.queued,
            )
            .order_by(TaskDispatchOutbox.failed_at, TaskDispatchOutbox.task_id)
            .limit(remaining)
        ).all()
        failed = [*failed_documents, *failed_ingestion]
        for dispatch in failed:
            if dispatch.source_kind is TaskDispatchSourceKind.generated_document:
                source = db.scalar(select(GeneratedDocument).where(GeneratedDocument.id == dispatch.source_id))
                if source is not None and source.status is GeneratedDocumentStatus.queued:
                    db.scalar(select(User).where(User.id == source.owner_user_id).with_for_update())
                    db.scalar(select(Transcript).where(Transcript.id == source.transcript_id).with_for_update())
                    source = db.scalar(select(GeneratedDocument).where(GeneratedDocument.id == source.id).with_for_update())
                    attempts = _lock_attempts(db, (ProviderAttempt.generated_document_id == source.id) & (ProviderAttempt.status == AttemptStatus.reserved))
                    changed += _terminalize_locked_attempts(attempts, now).cancelled
                    changed += _fail_document(db, source.id, TASK_DISPATCH_FAILED, now)
            else:
                source = db.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.id == dispatch.source_id))
                if source is not None and source.status is TranscriptIngestionJobStatus.queued:
                    db.scalar(select(User).where(User.id == source.owner_user_id).with_for_update())
                    db.scalar(select(Transcript).where(Transcript.id == source.transcript_id).with_for_update())
                    source = db.scalar(select(TranscriptIngestionJob).where(TranscriptIngestionJob.id == source.id).with_for_update())
                    attempts = _lock_attempts(db, (ProviderAttempt.transcript_ingestion_job_id == source.id) & (ProviderAttempt.status == AttemptStatus.reserved))
                    changed += _terminalize_locked_attempts(attempts, now).cancelled
                    changed += _fail_ingestion_job(db, source.id, TASK_DISPATCH_FAILED, now)
    db.flush()
    db.commit()
    return changed
