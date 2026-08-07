from __future__ import annotations

import calendar
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    LegalDocumentVersion,
    LegalDocumentVersionHold,
    LegalDocumentVersionState,
    User,
    utcnow,
)
from app.services.security_audit import add_security_event


LEGAL_DRAFT_RETENTION_MONTHS = 12
LEGAL_SUPERSEDED_RETENTION_YEARS = 6


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _require_system_admin(actor: User) -> None:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin legal-hold access required")


def _clean_reason(reason: str) -> str:
    clean = reason.strip()
    if not clean or len(clean) > 500 or any(ord(character) < 32 or ord(character) == 127 for character in clean):
        raise AppError(422, "validation_error", "Legal-hold reason must be one line of at most 500 characters")
    return clean


def place_legal_document_hold(
    db: Session,
    *,
    actor: User,
    version_id: UUID,
    reason: str,
    now: datetime | None = None,
) -> LegalDocumentVersionHold:
    _require_system_admin(actor)
    version = db.scalar(
        select(LegalDocumentVersion).where(LegalDocumentVersion.id == version_id).with_for_update()
    )
    if version is None:
        raise AppError(404, "not_found", "Legal document version not found")
    existing = db.scalar(
        select(LegalDocumentVersionHold).where(
            LegalDocumentVersionHold.legal_document_version_id == version_id,
            LegalDocumentVersionHold.released_at.is_(None),
        )
    )
    if existing is not None:
        raise AppError(409, "conflict", "This legal document version already has an active hold")
    hold = LegalDocumentVersionHold(
        legal_document_version_id=version_id,
        reason=_clean_reason(reason),
        created_by_user_id=actor.id,
        created_at=now or utcnow(),
    )
    db.add(hold)
    try:
        add_security_event(
            db,
            action="legal_document_hold_placed",
            actor=actor,
            details={
                "category": "legal_content",
                "outcome": "success",
                "object_type": "legal_document_version",
                "object_id": str(version_id),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "This legal document version already has an active hold") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(hold)
    return hold


def release_legal_document_hold(
    db: Session,
    *,
    actor: User,
    hold_id: UUID,
    now: datetime | None = None,
) -> LegalDocumentVersionHold:
    _require_system_admin(actor)
    hold = db.scalar(
        select(LegalDocumentVersionHold).where(LegalDocumentVersionHold.id == hold_id).with_for_update()
    )
    if hold is None:
        raise AppError(404, "not_found", "Legal document hold not found")
    if hold.released_at is not None:
        raise AppError(409, "conflict", "Legal document hold is already released")
    hold.released_at = now or utcnow()
    hold.released_by_user_id = actor.id
    db.add(hold)
    try:
        add_security_event(
            db,
            action="legal_document_hold_released",
            actor=actor,
            details={
                "category": "legal_content",
                "outcome": "success",
                "object_type": "legal_document_version",
                "object_id": str(hold.legal_document_version_id),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(hold)
    return hold


def active_legal_document_holds(
    db: Session,
    *,
    version_ids: list[UUID],
) -> dict[UUID, LegalDocumentVersionHold]:
    if not version_ids:
        return {}
    rows = db.scalars(
        select(LegalDocumentVersionHold).where(
            LegalDocumentVersionHold.legal_document_version_id.in_(version_ids),
            LegalDocumentVersionHold.released_at.is_(None),
        )
    )
    return {row.legal_document_version_id: row for row in rows}


def expire_legal_document_versions(
    db: Session,
    *,
    batch_size: int = 100,
    now: datetime | None = None,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    comparison_now = now or utcnow()
    draft_cutoff = _subtract_calendar_months(comparison_now, LEGAL_DRAFT_RETENTION_MONTHS)
    superseded_cutoff = _subtract_calendar_months(
        comparison_now,
        LEGAL_SUPERSEDED_RETENTION_YEARS * 12,
    )
    active_hold = exists(
        select(LegalDocumentVersionHold.id).where(
            LegalDocumentVersionHold.legal_document_version_id == LegalDocumentVersion.id,
            LegalDocumentVersionHold.released_at.is_(None),
        )
    )
    versions = list(
        db.scalars(
            select(LegalDocumentVersion)
            .where(
                or_(
                    (
                        (LegalDocumentVersion.state == LegalDocumentVersionState.draft)
                        & (LegalDocumentVersion.updated_at <= draft_cutoff)
                    ),
                    (
                        (LegalDocumentVersion.state == LegalDocumentVersionState.superseded)
                        & (LegalDocumentVersion.superseded_at <= superseded_cutoff)
                    ),
                ),
                ~active_hold,
            )
            .order_by(LegalDocumentVersion.updated_at.asc(), LegalDocumentVersion.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    states = {state.value: 0 for state in LegalDocumentVersionState}
    for version in versions:
        states[version.state.value] += 1
        db.delete(version)
    try:
        if versions:
            add_security_event(
                db,
                action="legal_document_retention_processed",
                details={
                    "category": "legal_content",
                    "outcome": "success",
                    "deleted_draft_count": states[LegalDocumentVersionState.draft.value],
                    "deleted_superseded_count": states[LegalDocumentVersionState.superseded.value],
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(versions)
