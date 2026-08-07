from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    SecurityAuditEvent,
    SecurityAuditEventHold,
    SecurityAuditHoldReason,
    User,
    utcnow,
)
from app.services.security_audit import record_security_event


SECURITY_AUDIT_RETENTION_MONTHS = 6
SECURITY_AUDIT_HOLD_MAX_DURATION = timedelta(days=90)


def subtract_calendar_months(value: datetime, months: int) -> datetime:
    if months < 0:
        raise ValueError("months must not be negative")
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _require_system_admin(actor: User) -> None:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin security-audit hold access required")


def _validate_hold_window(
    *,
    approved_at: datetime,
    review_at: datetime,
    expires_at: datetime,
) -> None:
    if review_at <= approved_at or expires_at <= approved_at:
        raise AppError(422, "validation_error", "Hold review and expiry must be in the future")
    if review_at > expires_at:
        raise AppError(422, "validation_error", "Hold review must not be after hold expiry")
    if expires_at > approved_at + SECURITY_AUDIT_HOLD_MAX_DURATION:
        raise AppError(422, "validation_error", "A security-audit hold approval cannot exceed 90 days")


def _validate_reference(reference: str | None) -> str | None:
    if reference is None or not reference.strip():
        return None
    clean = reference.strip()
    if len(clean) > 255 or any(ord(character) < 32 or ord(character) == 127 for character in clean):
        raise AppError(422, "validation_error", "Hold reference must be one line of at most 255 characters")
    return clean


def _release_expired_unreleased_hold(hold: SecurityAuditEventHold, *, now: datetime) -> bool:
    if hold.released_at is not None or hold.expires_at > now:
        return False
    hold.released_at = hold.expires_at
    hold.released_by_user_id = None
    return True


def place_security_audit_hold(
    db: Session,
    *,
    actor: User,
    event_id: UUID,
    reason: SecurityAuditHoldReason,
    reference: str | None,
    review_at: datetime,
    expires_at: datetime,
    now: datetime | None = None,
) -> SecurityAuditEventHold:
    _require_system_admin(actor)
    approved_at = now or utcnow()
    _validate_hold_window(approved_at=approved_at, review_at=review_at, expires_at=expires_at)
    event = db.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.id == event_id).with_for_update())
    if event is None:
        raise AppError(404, "not_found", "Security-audit event not found")
    previous = db.scalar(
        select(SecurityAuditEventHold)
        .where(
            SecurityAuditEventHold.security_audit_event_id == event.id,
            SecurityAuditEventHold.released_at.is_(None),
        )
        .with_for_update()
    )
    if previous is not None:
        if not _release_expired_unreleased_hold(previous, now=approved_at):
            raise AppError(409, "conflict", "This security-audit event already has an active hold")
        db.add(previous)
        db.flush()
    hold = SecurityAuditEventHold(
        security_audit_event_id=event.id,
        reason=reason,
        reference=_validate_reference(reference),
        owner_user_id=actor.id,
        created_at=approved_at,
        approved_at=approved_at,
        review_at=review_at,
        expires_at=expires_at,
    )
    db.add(hold)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "This security-audit event already has an active hold") from exc
    db.refresh(hold)
    record_security_event(
        db,
        action="security_audit_hold_placed",
        actor=actor,
        details={
            "category": "audit_retention",
            "outcome": "success",
            "object_type": "security_audit_event",
            "object_id": str(event.id),
            "hold_reason": reason.value,
            "hold_expires_at": expires_at.isoformat(),
        },
    )
    return hold


def renew_security_audit_hold(
    db: Session,
    *,
    actor: User,
    hold_id: UUID,
    reason: SecurityAuditHoldReason,
    reference: str | None,
    review_at: datetime,
    expires_at: datetime,
    now: datetime | None = None,
) -> SecurityAuditEventHold:
    _require_system_admin(actor)
    approved_at = now or utcnow()
    _validate_hold_window(approved_at=approved_at, review_at=review_at, expires_at=expires_at)
    hold = db.scalar(
        select(SecurityAuditEventHold).where(SecurityAuditEventHold.id == hold_id).with_for_update()
    )
    if hold is None:
        raise AppError(404, "not_found", "Security-audit hold not found")
    if hold.released_at is not None or hold.expires_at <= approved_at:
        raise AppError(409, "conflict", "Only a current active hold can be renewed")
    hold.reason = reason
    hold.reference = _validate_reference(reference)
    hold.owner_user_id = actor.id
    hold.approved_at = approved_at
    hold.review_at = review_at
    hold.expires_at = expires_at
    hold.renewal_count += 1
    db.add(hold)
    db.commit()
    db.refresh(hold)
    record_security_event(
        db,
        action="security_audit_hold_renewed",
        actor=actor,
        details={
            "category": "audit_retention",
            "outcome": "success",
            "object_type": "security_audit_event",
            "object_id": str(hold.security_audit_event_id),
            "hold_reason": reason.value,
            "hold_expires_at": expires_at.isoformat(),
            "renewal_count": hold.renewal_count,
        },
    )
    return hold


def release_security_audit_hold(
    db: Session,
    *,
    actor: User,
    hold_id: UUID,
    now: datetime | None = None,
) -> SecurityAuditEventHold:
    _require_system_admin(actor)
    released_at = now or utcnow()
    hold = db.scalar(
        select(SecurityAuditEventHold).where(SecurityAuditEventHold.id == hold_id).with_for_update()
    )
    if hold is None:
        raise AppError(404, "not_found", "Security-audit hold not found")
    if hold.released_at is not None:
        raise AppError(409, "conflict", "Security-audit hold is already released")
    hold.released_at = released_at
    hold.released_by_user_id = actor.id
    db.add(hold)
    db.commit()
    db.refresh(hold)
    record_security_event(
        db,
        action="security_audit_hold_released",
        actor=actor,
        details={
            "category": "audit_retention",
            "outcome": "success",
            "object_type": "security_audit_event",
            "object_id": str(hold.security_audit_event_id),
        },
    )
    return hold


def expire_security_audit_events(
    db: Session,
    *,
    batch_size: int = 100,
    now: datetime | None = None,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    comparison_now = now or utcnow()
    cutoff = subtract_calendar_months(comparison_now, SECURITY_AUDIT_RETENTION_MONTHS)

    expired_holds = list(
        db.scalars(
            select(SecurityAuditEventHold)
            .where(
                SecurityAuditEventHold.released_at.is_(None),
                SecurityAuditEventHold.expires_at <= comparison_now,
            )
            .order_by(SecurityAuditEventHold.expires_at.asc(), SecurityAuditEventHold.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    for hold in expired_holds:
        _release_expired_unreleased_hold(hold, now=comparison_now)
        db.add(hold)
    if expired_holds:
        db.flush()

    active_hold_exists = exists(
        select(SecurityAuditEventHold.id).where(
            SecurityAuditEventHold.security_audit_event_id == SecurityAuditEvent.id,
            SecurityAuditEventHold.released_at.is_(None),
            SecurityAuditEventHold.expires_at > comparison_now,
        )
    )
    events = list(
        db.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.created_at <= cutoff,
                ~active_hold_exists,
            )
            .order_by(SecurityAuditEvent.created_at.asc(), SecurityAuditEvent.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    for event in events:
        db.delete(event)
    db.commit()
    if events or expired_holds:
        record_security_event(
            db,
            action="security_audit_retention_processed",
            details={
                "category": "audit_retention",
                "outcome": "success",
                "deleted_count": len(events),
                "expired_hold_count": len(expired_holds),
                "retention_months": SECURITY_AUDIT_RETENTION_MONTHS,
            },
        )
    return len(events)


def active_security_audit_holds(
    db: Session,
    *,
    event_ids: list[UUID],
    now: datetime | None = None,
) -> dict[UUID, SecurityAuditEventHold]:
    if not event_ids:
        return {}
    comparison_now = now or utcnow()
    holds = db.scalars(
        select(SecurityAuditEventHold).where(
            SecurityAuditEventHold.security_audit_event_id.in_(event_ids),
            SecurityAuditEventHold.released_at.is_(None),
            SecurityAuditEventHold.expires_at > comparison_now,
        )
    )
    return {hold.security_audit_event_id: hold for hold in holds}
