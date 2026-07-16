"""System-admin quota policy read model and mutation services.

Rows are metadata-only.  Mutations own their commit so an accepted operation
is durable before its best-effort security audit is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    QuotaPeriod,
    QuotaResource,
    User,
    UserQuotaPolicyEvent,
    UserQuotaPolicyEventType,
    UserQuotaReasonCode,
    utcnow,
)
from app.services.quotas import QuotaWindow, calculate_quota_window
from app.services.security_audit import record_security_event


MAX_DAILY_TOKENS = 10_000_000
MAX_MONTHLY_TOKENS = 100_000_000
MAX_DAILY_AUDIO_SECONDS = 24 * 3600
MAX_MONTHLY_AUDIO_SECONDS = 1_000 * 3600
MAX_GRANT_TOKENS = 100_000_000
MAX_GRANT_AUDIO_SECONDS = 1_000 * 3600

_WINDOWS = (
    (QuotaResource.tokens, QuotaPeriod.daily, "daily_token_limit", MAX_DAILY_TOKENS),
    (QuotaResource.tokens, QuotaPeriod.monthly, "monthly_token_limit", MAX_MONTHLY_TOKENS),
    (QuotaResource.audio_seconds, QuotaPeriod.daily, "daily_audio_seconds_limit", MAX_DAILY_AUDIO_SECONDS),
    (QuotaResource.audio_seconds, QuotaPeriod.monthly, "monthly_audio_seconds_limit", MAX_MONTHLY_AUDIO_SECONDS),
)
_WINDOW_BY_KEY = {(resource, period): (field, maximum) for resource, period, field, maximum in _WINDOWS}


@dataclass(frozen=True, slots=True)
class UserQuotaHistoryItem:
    id: UUID
    operation_id: UUID
    event_type: UserQuotaPolicyEventType
    resource: QuotaResource
    period: QuotaPeriod
    reason_code: UserQuotaReasonCode
    reason: str
    amount: int | None
    previous_limit: int | None
    new_limit: int | None
    effective_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    actor_user_id_snapshot: UUID
    actor_email: str | None
    revoker_user_id_snapshot: UUID | None
    revoker_email: str | None
    revocation_operation_id: UUID | None
    revocation_reason_code: UserQuotaReasonCode | None
    revocation_reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AdminUserQuotaDetail:
    user_id: UUID
    windows: tuple[QuotaWindow, QuotaWindow, QuotaWindow, QuotaWindow]
    history: tuple[UserQuotaHistoryItem, ...]


@dataclass(frozen=True, slots=True)
class UserQuotaMutationResult:
    user_id: UUID
    operation_id: UUID
    event_ids: tuple[UUID, ...]


def _now(value: datetime | None = None) -> datetime:
    value = value or utcnow()
    if value.tzinfo is None:
        raise AppError(422, "quota_invalid_timestamp", "Quota timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _reason(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise AppError(422, "quota_reason_required", "Quota reason is required")
    if len(clean) > 500:
        raise AppError(422, "quota_reason_too_long", "Quota reason is too long")
    return clean


def _reason_code(value: UserQuotaReasonCode) -> UserQuotaReasonCode:
    if not isinstance(value, UserQuotaReasonCode):
        raise AppError(422, "quota_reason_code_invalid", "Quota reason code is invalid")
    return value


def _require_actor(actor: User) -> None:
    if not actor.is_system_admin:
        raise AppError(403, "quota_admin_forbidden", "System administrator access is required")


def _locked_target(db: Session, *, actor: User, user_id: UUID) -> User:
    _require_actor(actor)
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise AppError(404, "quota_target_not_found", "Quota target was not found")
    if target.id == actor.id:
        raise AppError(403, "quota_target_self_forbidden", "Administrators cannot change their own quota")
    if target.is_system_admin or target.team_id is None or target.team_role is None:
        raise AppError(403, "quota_target_ineligible", "Quota target must be a normal team member")
    return target


def _target_for_read(db: Session, *, actor: User, user_id: UUID) -> User:
    _require_actor(actor)
    target = db.get(User, user_id)
    if target is None:
        raise AppError(404, "quota_target_not_found", "Quota target was not found")
    if target.id == actor.id:
        raise AppError(403, "quota_target_self_forbidden", "Administrators cannot view their own quota")
    if target.is_system_admin or target.team_id is None or target.team_role is None:
        raise AppError(403, "quota_target_ineligible", "Quota target must be a normal team member")
    return target


def _validate_limit(value: int | None, maximum: int, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > maximum:
        raise AppError(422, "quota_limit_invalid", "Quota limit is invalid", {"field": field, "max": maximum})


def _validate_grant(value: int, resource: QuotaResource) -> None:
    maximum = MAX_GRANT_TOKENS if resource is QuotaResource.tokens else MAX_GRANT_AUDIO_SECONDS
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > maximum:
        raise AppError(422, "quota_grant_invalid", "Quota grant is invalid", {"max": maximum})


def _events_for_operation(db: Session, *, target_id: UUID, operation_id: UUID) -> list[UserQuotaPolicyEvent]:
    return db.scalars(
        select(UserQuotaPolicyEvent)
        .where(UserQuotaPolicyEvent.target_user_id == target_id, UserQuotaPolicyEvent.operation_id == operation_id)
        .order_by(UserQuotaPolicyEvent.resource, UserQuotaPolicyEvent.period, UserQuotaPolicyEvent.id)
    ).all()


def _result(target: User, operation_id: UUID, events: list[UserQuotaPolicyEvent]) -> UserQuotaMutationResult:
    return UserQuotaMutationResult(target.id, operation_id, tuple(event.id for event in events))


def _audit(action: str, db: Session, *, actor: User, target: User, details: dict[str, object]) -> None:
    # Audit service owns failures; free-text reason must never enter details.
    try:
        record_security_event(db, action=action, actor=actor, target=target, team_id=target.team_id, details=details)
    except Exception:
        # Mutation already committed. Audit loss must not undo expenditure control.
        return


def get_admin_user_quota_detail(
    db: Session, *, actor: User, user_id: UUID, now: datetime | None = None
) -> AdminUserQuotaDetail:
    target = _target_for_read(db, actor=actor, user_id=user_id)
    current = _now(now)
    windows = tuple(
        calculate_quota_window(db, user=target, resource=resource, period=period, now=current)
        for resource, period, _, _ in _WINDOWS
    )
    events = db.scalars(
        select(UserQuotaPolicyEvent)
        .where(UserQuotaPolicyEvent.target_user_id == target.id)
        .order_by(UserQuotaPolicyEvent.created_at.desc(), UserQuotaPolicyEvent.id.desc())
        .limit(50)
    ).all()
    live_ids = {event.actor_user_id for event in events if event.actor_user_id} | {
        event.revoker_user_id for event in events if event.revoker_user_id
    }
    live_emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(live_ids))).all()) if live_ids else {}
    history = tuple(
        UserQuotaHistoryItem(
            id=event.id, operation_id=event.operation_id, event_type=event.event_type,
            resource=event.resource, period=event.period, reason_code=event.reason_code,
            reason=event.reason, amount=event.amount, previous_limit=event.previous_limit,
            new_limit=event.new_limit, effective_at=event.effective_at, expires_at=event.expires_at,
            revoked_at=event.revoked_at, actor_user_id_snapshot=event.actor_user_id_snapshot,
            actor_email=live_emails.get(event.actor_user_id),
            revoker_user_id_snapshot=event.revoker_user_id_snapshot,
            revoker_email=live_emails.get(event.revoker_user_id),
            revocation_operation_id=event.revocation_operation_id,
            revocation_reason_code=event.revocation_reason_code, revocation_reason=event.revocation_reason,
            created_at=event.created_at,
        )
        for event in events
    )
    return AdminUserQuotaDetail(target.id, windows, history)  # type: ignore[arg-type]


def update_user_base_quotas_batch(
    db: Session, *, actor: User, user_id: UUID, daily_token_limit: int | None,
    monthly_token_limit: int | None, daily_audio_seconds_limit: int | None,
    monthly_audio_seconds_limit: int | None, operation_id: UUID, reason_code: UserQuotaReasonCode,
    reason: str, now: datetime | None = None,
) -> UserQuotaMutationResult:
    clean_reason, code = _reason(reason), _reason_code(reason_code)
    requested = {
        "daily_token_limit": daily_token_limit, "monthly_token_limit": monthly_token_limit,
        "daily_audio_seconds_limit": daily_audio_seconds_limit,
        "monthly_audio_seconds_limit": monthly_audio_seconds_limit,
    }
    for _, _, field, maximum in _WINDOWS:
        _validate_limit(requested[field], maximum, field)
    target = _locked_target(db, actor=actor, user_id=user_id)
    existing = _events_for_operation(db, target_id=target.id, operation_id=operation_id)
    if existing:
        changed_keys = {(event.resource, event.period) for event in existing}
        if (
            len(existing) == len(changed_keys)
            and all(event.event_type is UserQuotaPolicyEventType.limit_change and event.actor_user_id_snapshot == actor.id
                    and event.reason_code is code and event.reason == clean_reason
                    and event.new_limit == requested[_WINDOW_BY_KEY[(event.resource, event.period)][0]] for event in existing)
            and all(
                (resource, period) in changed_keys or getattr(target, field) == requested[field]
                for resource, period, field, _ in _WINDOWS
            )
        ):
            return _result(target, operation_id, existing)
        raise AppError(409, "quota_operation_idempotency_conflict", "Quota operation conflicts with existing request")
    effective_at = _now(now)
    events: list[UserQuotaPolicyEvent] = []
    for resource, period, field, _ in _WINDOWS:
        previous, new = getattr(target, field), requested[field]
        if previous == new:
            continue
        setattr(target, field, new)
        events.append(UserQuotaPolicyEvent(
            operation_id=operation_id, target_user_id=target.id, actor_user_id=actor.id,
            actor_user_id_snapshot=actor.id, event_type=UserQuotaPolicyEventType.limit_change,
            resource=resource, period=period, reason_code=code, reason=clean_reason,
            previous_limit=previous, new_limit=new, effective_at=effective_at,
        ))
    if not events:
        raise AppError(409, "quota_no_changes", "Quota update makes no changes")
    db.add_all(events)
    db.commit()
    db.refresh(target)
    for event in events:
        db.refresh(event)
    _audit("user_quota_limits_updated", db, actor=actor, target=target, details={
        "operation_id": str(operation_id), "reason_code": code.value, "changes": [
            {"resource": event.resource.value, "period": event.period.value,
             "old": event.previous_limit, "new": event.new_limit} for event in events
        ],
    })
    return _result(target, operation_id, events)


def grant_user_quota_batch(
    db: Session, *, actor: User, user_id: UUID, resource: QuotaResource,
    periods: tuple[QuotaPeriod, ...] | list[QuotaPeriod] | set[QuotaPeriod], amount: int,
    expires_at: datetime | None, operation_id: UUID, reason_code: UserQuotaReasonCode,
    reason: str, now: datetime | None = None,
) -> UserQuotaMutationResult:
    clean_reason, code, effective_at = _reason(reason), _reason_code(reason_code), _now(now)
    if resource not in (QuotaResource.tokens, QuotaResource.audio_seconds):
        raise AppError(422, "quota_resource_invalid", "Quota resource is invalid")
    selected = tuple(sorted(set(periods), key=lambda value: value.value))
    if not selected or len(selected) > 2 or any(period not in (QuotaPeriod.daily, QuotaPeriod.monthly) for period in selected):
        raise AppError(422, "quota_periods_invalid", "Choose one or two quota periods")
    _validate_grant(amount, resource)
    if expires_at is not None and _now(expires_at) <= effective_at:
        raise AppError(422, "quota_expiry_invalid", "Quota grant expiry must be in future")
    target = _locked_target(db, actor=actor, user_id=user_id)
    existing = _events_for_operation(db, target_id=target.id, operation_id=operation_id)
    if existing:
        if (len(existing) == len(selected) and all(
            event.event_type is UserQuotaPolicyEventType.grant and event.actor_user_id_snapshot == actor.id
            and event.resource is resource and event.period in selected and event.amount == amount
            and event.expires_at == expires_at and event.reason_code is code and event.reason == clean_reason for event in existing
        )):
            return _result(target, operation_id, existing)
        raise AppError(409, "quota_operation_idempotency_conflict", "Quota operation conflicts with existing request")
    for period in selected:
        field, _ = _WINDOW_BY_KEY[(resource, period)]
        if getattr(target, field) is None:
            raise AppError(409, "quota_grant_unlimited", "Cannot grant quota to unlimited window", {"period": period.value})
    events = [UserQuotaPolicyEvent(
        operation_id=operation_id, target_user_id=target.id, actor_user_id=actor.id,
        actor_user_id_snapshot=actor.id, event_type=UserQuotaPolicyEventType.grant,
        resource=resource, period=period, reason_code=code, reason=clean_reason, amount=amount,
        effective_at=effective_at, expires_at=expires_at,
    ) for period in selected]
    db.add_all(events)
    db.commit()
    for event in events:
        db.refresh(event)
    _audit("user_quota_grant_created", db, actor=actor, target=target, details={
        "operation_id": str(operation_id), "reason_code": code.value, "resource": resource.value,
        "periods": [period.value for period in selected], "amount": amount,
        "expires_at": expires_at.isoformat() if expires_at else None,
    })
    return _result(target, operation_id, events)


def reset_user_quota_batch(
    db: Session, *, actor: User, user_id: UUID,
    windows: tuple[tuple[QuotaResource, QuotaPeriod], ...] | list[tuple[QuotaResource, QuotaPeriod]] | set[tuple[QuotaResource, QuotaPeriod]],
    operation_id: UUID, reason_code: UserQuotaReasonCode, reason: str, now: datetime | None = None,
) -> UserQuotaMutationResult:
    clean_reason, code, effective_at = _reason(reason), _reason_code(reason_code), _now(now)
    selected = tuple(sorted(set(windows), key=lambda value: (value[0].value, value[1].value)))
    if not selected or any(key not in _WINDOW_BY_KEY for key in selected):
        raise AppError(422, "quota_windows_invalid", "Choose one or more quota windows")
    target = _locked_target(db, actor=actor, user_id=user_id)
    existing = _events_for_operation(db, target_id=target.id, operation_id=operation_id)
    if existing:
        if (len(existing) == len(selected) and all(
            event.event_type is UserQuotaPolicyEventType.reset and event.actor_user_id_snapshot == actor.id
            and (event.resource, event.period) in selected and event.reason_code is code and event.reason == clean_reason
            for event in existing
        )):
            return _result(target, operation_id, existing)
        raise AppError(409, "quota_operation_idempotency_conflict", "Quota operation conflicts with existing request")
    events = [UserQuotaPolicyEvent(
        operation_id=operation_id, target_user_id=target.id, actor_user_id=actor.id,
        actor_user_id_snapshot=actor.id, event_type=UserQuotaPolicyEventType.reset,
        resource=resource, period=period, reason_code=code, reason=clean_reason, effective_at=effective_at,
    ) for resource, period in selected]
    db.add_all(events)
    db.commit()
    for event in events:
        db.refresh(event)
    _audit("user_quota_usage_reset", db, actor=actor, target=target, details={
        "operation_id": str(operation_id), "reason_code": code.value,
        "windows": [{"resource": resource.value, "period": period.value} for resource, period in selected],
    })
    return _result(target, operation_id, events)


def revoke_user_quota_grant(
    db: Session, *, actor: User, user_id: UUID, grant_id: UUID, revocation_operation_id: UUID,
    reason_code: UserQuotaReasonCode, reason: str, now: datetime | None = None,
) -> UserQuotaMutationResult:
    clean_reason, code, revoked_at = _reason(reason), _reason_code(reason_code), _now(now)
    target = _locked_target(db, actor=actor, user_id=user_id)
    grant = db.scalar(select(UserQuotaPolicyEvent).where(UserQuotaPolicyEvent.id == grant_id).with_for_update())
    if grant is None or grant.target_user_id != target.id:
        raise AppError(404, "quota_grant_not_found", "Quota grant was not found")
    operation_owner = db.scalar(
        select(UserQuotaPolicyEvent).where(UserQuotaPolicyEvent.revocation_operation_id == revocation_operation_id)
    )
    if operation_owner is not None and operation_owner.id != grant.id:
        raise AppError(409, "quota_revocation_idempotency_conflict", "Quota revocation conflicts with existing request")
    if grant.revocation_operation_id is not None:
        if (grant.revocation_operation_id == revocation_operation_id and grant.revoker_user_id_snapshot == actor.id
                and grant.revocation_reason_code is code and grant.revocation_reason == clean_reason):
            return _result(target, revocation_operation_id, [grant])
        raise AppError(409, "quota_revocation_idempotency_conflict", "Quota revocation conflicts with existing request")
    if (grant.event_type is not UserQuotaPolicyEventType.grant or grant.revoked_at is not None
            or grant.effective_at > revoked_at or (grant.expires_at is not None and grant.expires_at <= revoked_at)):
        raise AppError(409, "quota_grant_not_active", "Quota grant is not active")
    grant.revoked_at = revoked_at
    grant.revoker_user_id = actor.id
    grant.revoker_user_id_snapshot = actor.id
    grant.revocation_operation_id = revocation_operation_id
    grant.revocation_reason_code = code
    grant.revocation_reason = clean_reason
    db.commit()
    db.refresh(grant)
    _audit("user_quota_grant_revoked", db, actor=actor, target=target, details={
        "operation_id": str(revocation_operation_id), "reason_code": code.value, "grant_id": str(grant.id),
        "resource": grant.resource.value, "period": grant.period.value, "amount": grant.amount,
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
    })
    return _result(target, revocation_operation_id, [grant])


# Descriptive alias for callers which name read models as service actions.
admin_user_quota_detail = get_admin_user_quota_detail
