"""DB-first, durable cleanup for retired provider credentials."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    DeidentificationProvider,
    ProviderSecretCleanupJob,
    ProviderSecretCleanupKind,
    TeamLlmConfig,
    TeamSttConfig,
    utcnow,
)
from app.services.vault import delete_provider_secret_by_ref


logger = logging.getLogger("openscribe.cleanup")
PROVIDER_SECRET_CLEANUP_RETRY_BASE_SECONDS = 10
PROVIDER_SECRET_CLEANUP_RETRY_MAX_SECONDS = 60 * 60


def queue_provider_secret_cleanup(
    db: Session,
    *,
    kind: ProviderSecretCleanupKind,
    secret_refs: list[str] | tuple[str, ...] | set[str],
) -> list[UUID]:
    """Queue refs within caller transaction, before their DB references disappear."""
    refs = sorted({ref for ref in secret_refs if ref})
    if not refs:
        return []
    existing = {
        job.secret_ref: job
        for job in db.scalars(select(ProviderSecretCleanupJob).where(ProviderSecretCleanupJob.secret_ref.in_(refs)))
    }
    jobs: list[ProviderSecretCleanupJob] = []
    for secret_ref in refs:
        job = existing.get(secret_ref)
        if job is None:
            job = ProviderSecretCleanupJob(kind=kind, secret_ref=secret_ref, next_attempt_at=utcnow())
            db.add(job)
        elif job.kind != kind:
            raise AppError(500, "provider_secret_cleanup_kind_conflict", "Provider secret cleanup reference has conflicting kind")
        jobs.append(job)
    db.flush()
    return [job.id for job in jobs]


def queue_orphan_provider_secret_after_rollback(
    db: Session,
    *,
    kind: ProviderSecretCleanupKind,
    secret_ref: str,
) -> None:
    """Persist compensation after caller rollback leaves a newly written Vault secret orphaned."""
    if not secret_ref:
        return
    try:
        queue_provider_secret_cleanup(db, kind=kind, secret_refs=[secret_ref])
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("provider_secret_orphan_enqueue_failed", extra={"kind": kind.value})


def _is_live_provider_secret_ref(db: Session, *, secret_ref: str) -> bool:
    return any(
        db.scalar(stmt.limit(1)) is not None
        for stmt in (
            select(TeamSttConfig.id).where(TeamSttConfig.vault_secret_ref == secret_ref),
            select(TeamLlmConfig.id).where(TeamLlmConfig.vault_secret_ref == secret_ref),
            select(DeidentificationProvider.id).where(DeidentificationProvider.vault_secret_ref == secret_ref),
        )
    )


def process_provider_secret_cleanup_jobs(
    db: Session,
    *,
    job_ids: list[UUID] | None = None,
    batch_size: int = 100,
    now: datetime | None = None,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    comparison_now = now or utcnow()
    stmt = (
        select(ProviderSecretCleanupJob)
        .where(ProviderSecretCleanupJob.next_attempt_at <= comparison_now)
        .order_by(ProviderSecretCleanupJob.next_attempt_at.asc(), ProviderSecretCleanupJob.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    if job_ids is not None:
        if not job_ids:
            return 0
        stmt = stmt.where(ProviderSecretCleanupJob.id.in_(job_ids))

    deleted_count = 0
    for cleanup_job in db.scalars(stmt):
        if _is_live_provider_secret_ref(db, secret_ref=cleanup_job.secret_ref):
            # Ref was reused or deletion rolled back; never delete a live credential.
            db.delete(cleanup_job)
            continue
        try:
            delete_provider_secret_by_ref(kind=cleanup_job.kind, secret_ref=cleanup_job.secret_ref)
        except AppError as exc:
            cleanup_job.attempt_count += 1
            cleanup_job.last_error_code = exc.code
            delay = min(
                PROVIDER_SECRET_CLEANUP_RETRY_BASE_SECONDS * (2 ** min(cleanup_job.attempt_count - 1, 8)),
                PROVIDER_SECRET_CLEANUP_RETRY_MAX_SECONDS,
            )
            cleanup_job.next_attempt_at = comparison_now + timedelta(seconds=delay)
            logger.warning(
                "provider_secret_cleanup_retry_failed",
                extra={"cleanup_job_id": str(cleanup_job.id), "kind": cleanup_job.kind.value, "attempt_count": cleanup_job.attempt_count, "error_code": exc.code},
            )
            db.add(cleanup_job)
        else:
            db.delete(cleanup_job)
            deleted_count += 1
    db.commit()
    return deleted_count
