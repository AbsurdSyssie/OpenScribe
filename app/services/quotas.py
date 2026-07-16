"""Race-safe, metadata-only provider quota accounting.

Callers own transaction commit/rollback.  This module never logs outbound
content or provider payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    GeneratedDocument,
    ProviderAttempt,
    ProviderSettlementBasis,
    QuotaPeriod,
    QuotaResource,
    Transcript,
    TranscriptIngestionJob,
    User,
    UserQuotaPolicyEvent,
    UserQuotaPolicyEventType,
)


# Counts framing/messages not represented in supplied outbound strings.
DEFAULT_TOKEN_MESSAGE_OVERHEAD = 16


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    resource: QuotaResource
    period: QuotaPeriod
    base_limit: int | None
    temporary_allowance: int
    effective_limit: int | None
    consumed: int
    pending_reserved: int
    remaining: int | None
    natural_start: datetime
    usage_start: datetime
    resets_at: datetime


@dataclass(frozen=True, slots=True)
class QuotaSummary:
    daily: QuotaWindow
    monthly: QuotaWindow


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    cancelled_reservations: int
    settled_submissions: int
    skipped_audio_submissions: int


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("quota timestamps must be timezone-aware UTC datetimes")
    return value.astimezone(UTC)


def _window_bounds(period: QuotaPeriod, now: datetime) -> tuple[datetime, datetime]:
    if period is QuotaPeriod.daily:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        return start, start.replace(year=start.year + 1, month=1)
    return start, start.replace(month=start.month + 1)


def _base_limit(user: User, resource: QuotaResource, period: QuotaPeriod) -> int | None:
    fields = {
        (QuotaResource.tokens, QuotaPeriod.daily): "daily_token_limit",
        (QuotaResource.tokens, QuotaPeriod.monthly): "monthly_token_limit",
        (QuotaResource.audio_seconds, QuotaPeriod.daily): "daily_audio_seconds_limit",
        (QuotaResource.audio_seconds, QuotaPeriod.monthly): "monthly_audio_seconds_limit",
    }
    return getattr(user, fields[(resource, period)])


def estimate_token_reservation(
    outbound_strings: list[str] | tuple[str, ...],
    *,
    max_completion_tokens: int,
    per_message_overhead: int = DEFAULT_TOKEN_MESSAGE_OVERHEAD,
) -> int:
    """Conservative byte-based estimate. Never persist/log supplied content."""
    if max_completion_tokens < 0 or per_message_overhead < 0:
        raise ValueError("token estimate inputs must be non-negative")
    return max(
        1,
        sum(len(value.encode("utf-8")) + per_message_overhead for value in outbound_strings)
        + max_completion_tokens,
    )


def calculate_quota_window(
    db: Session,
    *,
    user: User,
    resource: QuotaResource,
    period: QuotaPeriod,
    now: datetime | None = None,
) -> QuotaWindow:
    now = _utc(now)
    natural_start, resets_at = _window_bounds(period, now)
    base_limit = _base_limit(user, resource, period)

    events = db.scalars(
        select(UserQuotaPolicyEvent).where(
            UserQuotaPolicyEvent.target_user_id == user.id,
            UserQuotaPolicyEvent.resource == resource,
            UserQuotaPolicyEvent.period == period,
            UserQuotaPolicyEvent.effective_at <= now,
        )
    ).all()

    grants = [
        event.amount or 0
        for event in events
        if event.event_type is UserQuotaPolicyEventType.grant
        and event.revoked_at is None
        and (event.expires_at is None or event.expires_at > now)
    ]
    temporary_allowance = 0 if base_limit is None else sum(grants)
    effective_limit = None if base_limit is None else base_limit + temporary_allowance

    latest_reset = max(
        (
            event.effective_at
            for event in events
            if event.event_type is UserQuotaPolicyEventType.reset
            and event.effective_at >= natural_start
        ),
        default=natural_start,
    )
    # Only unlimited -> finite activates prospective accounting. Finite changes
    # deliberately do not restart a window.
    latest_activation = max(
        (
            event.effective_at
            for event in events
            if event.event_type is UserQuotaPolicyEventType.limit_change
            and event.previous_limit is None
            and event.new_limit is not None
            and event.effective_at >= natural_start
        ),
        default=natural_start,
    )
    usage_start = max(natural_start, latest_reset, latest_activation)

    consumed = int(
        db.scalar(
            select(func.coalesce(func.sum(ProviderAttempt.settled_units), 0)).where(
                ProviderAttempt.owner_user_id == user.id,
                ProviderAttempt.resource == resource,
                ProviderAttempt.status == AttemptStatus.settled,
                ProviderAttempt.authorized_at >= usage_start,
            )
        )
        or 0
    )
    # Pending work is deliberately independent of policy/reset boundaries:
    # accepted work remains committed until it settles or an unused reservation expires.
    pending_reserved = int(
        db.scalar(
            select(func.coalesce(func.sum(ProviderAttempt.reserved_units), 0)).where(
                ProviderAttempt.owner_user_id == user.id,
                ProviderAttempt.resource == resource,
                (
                    (ProviderAttempt.status == AttemptStatus.submitted)
                    | ((ProviderAttempt.status == AttemptStatus.reserved) & (ProviderAttempt.reservation_valid_until > now))
                ),
            )
        )
        or 0
    )
    remaining = None if effective_limit is None else max(0, effective_limit - consumed - pending_reserved)
    return QuotaWindow(
        resource=resource,
        period=period,
        base_limit=base_limit,
        temporary_allowance=temporary_allowance,
        effective_limit=effective_limit,
        consumed=consumed,
        pending_reserved=pending_reserved,
        remaining=remaining,
        natural_start=natural_start,
        usage_start=usage_start,
        resets_at=resets_at,
    )


def calculate_quota_summary(
    db: Session, *, user: User, resource: QuotaResource, now: datetime | None = None
) -> QuotaSummary:
    now = _utc(now)
    return QuotaSummary(
        daily=calculate_quota_window(db, user=user, resource=resource, period=QuotaPeriod.daily, now=now),
        monthly=calculate_quota_window(db, user=user, resource=resource, period=QuotaPeriod.monthly, now=now),
    )


def _quota_error(
    window: QuotaWindow,
    *,
    requested: int,
    required_units: int | None = None,
    delta: int | None = None,
) -> AppError:
    details = {
        "resource": window.resource.value,
        "period": window.period.value,
        "effective_limit": window.effective_limit,
        "consumed": window.consumed,
        "pending_reserved": window.pending_reserved,
        "requested": requested,
        "remaining": window.remaining,
        "resets_at": window.resets_at.isoformat(),
    }
    if required_units is not None:
        details["required_units"] = required_units
    if delta is not None:
        details["delta"] = delta
    if window.effective_limit == 0:
        return AppError(403, "quota_disabled", "Provider usage is disabled for this resource", details)
    return AppError(429, "quota_exceeded", "Provider quota exceeded", details)


def reserve_provider_attempt(
    db: Session,
    *,
    team_id: UUID,
    owner_user_id: UUID | None,
    resource: QuotaResource,
    attempt_kind: AttemptKind,
    correlation_id: UUID,
    attempt_number: int,
    reserved_units: int,
    reservation_valid_until: datetime,
    authorized_at: datetime | None = None,
    transcript_id: UUID | None = None,
    transcript_ingestion_job_id: UUID | None = None,
    generated_document_id: UUID | None = None,
    provider_adapter: str | None = None,
    provider_model: str | None = None,
    measured_audio_seconds: Decimal | float | None = None,
) -> ProviderAttempt:
    """Reserve units under locked owner row. Does not commit."""
    if reserved_units <= 0 or attempt_number < 1:
        raise ValueError("attempt number and reserved units must be positive")
    authorized_at = _utc(authorized_at)
    reservation_valid_until = _utc(reservation_valid_until)
    if reservation_valid_until <= authorized_at:
        raise ValueError("reservation_valid_until must be after authorized_at")
    measured = None if measured_audio_seconds is None else Decimal(str(measured_audio_seconds))
    if resource is QuotaResource.audio_seconds:
        if measured is None or measured <= 0:
            raise ValueError("audio reservations require positive measured_audio_seconds")
        if reserved_units != ceil(measured):
            raise ValueError("audio reserved_units must equal ceil(measured_audio_seconds)")
    elif measured is not None:
        raise ValueError("token reservations must not include measured_audio_seconds")

    def matches_existing(existing: ProviderAttempt) -> bool:
        return (
            existing.team_id == team_id
            and existing.owner_user_id == owner_user_id
            and existing.resource is resource
            and existing.attempt_kind is attempt_kind
            and existing.correlation_id == correlation_id
            and existing.attempt_number == attempt_number
            and existing.reserved_units == reserved_units
            and _utc(existing.authorized_at) == authorized_at
            and _utc(existing.reservation_valid_until) == reservation_valid_until
            and existing.transcript_id == transcript_id
            and existing.transcript_ingestion_job_id == transcript_ingestion_job_id
            and existing.generated_document_id == generated_document_id
            and existing.provider_adapter == provider_adapter
            and existing.provider_model == provider_model
            and (None if existing.measured_audio_seconds is None else Decimal(str(existing.measured_audio_seconds))) == measured
        )

    def existing_or_conflict() -> ProviderAttempt | None:
        existing = db.scalar(
            select(ProviderAttempt).where(
                ProviderAttempt.correlation_id == correlation_id,
                ProviderAttempt.attempt_number == attempt_number,
            )
        )
        if existing is None:
            return None
        if matches_existing(existing):
            return existing
        raise AppError(409, "provider_attempt_idempotency_conflict", "Provider attempt idempotency key conflicts")

    if owner_user_id is not None:
        owner = db.scalar(select(User).where(User.id == owner_user_id).with_for_update())
        if owner is None:
            raise AppError(404, "quota_owner_not_found", "Quota owner not found")
        if owner.team_id != team_id:
            raise AppError(403, "quota_owner_team_mismatch", "Quota owner is not in this team")
        reference_specs = (
            (Transcript, transcript_id, "transcript"),
            (TranscriptIngestionJob, transcript_ingestion_job_id, "transcript_ingestion_job"),
            (GeneratedDocument, generated_document_id, "generated_document"),
        )
        references: dict[str, object] = {}
        for model, reference_id, name in reference_specs:
            if reference_id is None:
                continue
            reference = db.get(model, reference_id)
            if reference is None:
                raise AppError(404, "provider_attempt_reference_not_found", "Provider attempt reference not found")
            if reference.team_id != team_id or reference.owner_user_id != owner_user_id:
                raise AppError(403, "provider_attempt_reference_scope_mismatch", "Provider attempt reference is outside owner scope")
            references[name] = reference
        ingestion_reference = references.get("transcript_ingestion_job")
        document_reference = references.get("generated_document")
        if ingestion_reference is not None and transcript_id is not None and ingestion_reference.transcript_id != transcript_id:
            raise AppError(409, "provider_attempt_reference_mismatch", "Provider attempt references do not share a transcript")
        if document_reference is not None and transcript_id is not None and document_reference.transcript_id != transcript_id:
            raise AppError(409, "provider_attempt_reference_mismatch", "Provider attempt references do not share a transcript")
        existing = existing_or_conflict()
        if existing is not None:
            return existing
        summary = calculate_quota_summary(db, user=owner, resource=resource, now=authorized_at)
        for window in (summary.daily, summary.monthly):
            if window.effective_limit is not None and window.consumed + window.pending_reserved + reserved_units > window.effective_limit:
                raise _quota_error(window, requested=reserved_units)
    else:
        if transcript_id is not None or transcript_ingestion_job_id is not None or generated_document_id is not None:
            raise AppError(422, "provider_attempt_owner_required", "Content-linked provider attempts require an owner")
        existing = existing_or_conflict()
        if existing is not None:
            return existing
    attempt = ProviderAttempt(
        team_id=team_id, owner_user_id=owner_user_id, resource=resource, attempt_kind=attempt_kind,
        correlation_id=correlation_id, attempt_number=attempt_number, reserved_units=reserved_units,
        status=AttemptStatus.reserved, authorized_at=authorized_at,
        reservation_valid_until=reservation_valid_until, transcript_id=transcript_id,
        transcript_ingestion_job_id=transcript_ingestion_job_id, generated_document_id=generated_document_id,
        provider_adapter=provider_adapter, provider_model=provider_model,
        measured_audio_seconds=measured,
    )
    try:
        # Savepoint prevents a concurrent unique-key loser from rolling back
        # unrelated caller work in the surrounding transaction.
        with db.begin_nested():
            db.add(attempt)
            db.flush()
    except IntegrityError:
        existing = existing_or_conflict()
        if existing is not None:
            return existing
        raise AppError(409, "provider_attempt_idempotency_conflict", "Provider attempt idempotency key conflicts")
    return attempt


def _locked_attempt(db: Session, attempt_id: UUID) -> ProviderAttempt:
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.id == attempt_id).with_for_update())
    if attempt is None:
        raise AppError(404, "provider_attempt_not_found", "Provider attempt not found")
    return attempt


def increase_provider_attempt_reservation(
    db: Session, *, attempt_id: UUID, required_units: int, now: datetime | None = None
) -> ProviderAttempt:
    """Increase one live token reservation under User -> ProviderAttempt locks.

    The initial unlocked read identifies owner so every owned path locks the User
    before locking the attempt. Callers retain transaction ownership.
    """
    now = _utc(now)
    identity = db.scalar(select(ProviderAttempt).where(ProviderAttempt.id == attempt_id))
    if identity is None:
        raise AppError(404, "provider_attempt_not_found", "Provider attempt not found")
    owner_user_id = identity.owner_user_id
    if owner_user_id is not None:
        owner = db.scalar(select(User).where(User.id == owner_user_id).with_for_update())
        if owner is None:
            raise AppError(404, "quota_owner_not_found", "Quota owner not found")
    else:
        owner = None
    attempt = _locked_attempt(db, attempt_id)
    if attempt.status is not AttemptStatus.reserved or attempt.reservation_valid_until <= now:
        raise _transition_error()
    if required_units <= attempt.reserved_units:
        return attempt
    if attempt.resource is QuotaResource.audio_seconds:
        raise AppError(
            409,
            "provider_attempt_audio_reservation_immutable",
            "Audio reservation cannot be expanded without a measured duration update",
        )
    delta = required_units - attempt.reserved_units
    # Owner attribution cannot change while the attempt row is locked. An
    # ownerless provider test stays deliberately unmetered.
    if attempt.owner_user_id is not None:
        if owner is None or owner.id != attempt.owner_user_id:
            raise AppError(409, "provider_attempt_owner_changed", "Provider attempt owner changed during reservation update")
        summary = calculate_quota_summary(db, user=owner, resource=attempt.resource, now=now)
        for window in (summary.daily, summary.monthly):
            # pending_reserved already includes this attempt's current units.
            if window.effective_limit is not None and window.consumed + window.pending_reserved + delta > window.effective_limit:
                raise _quota_error(
                    window,
                    requested=delta,
                    required_units=required_units,
                    delta=delta,
                )
    attempt.reserved_units = required_units
    db.flush()
    return attempt


def _transition_error() -> AppError:
    return AppError(409, "provider_attempt_invalid_transition", "Provider attempt transition conflicts with current state")


def mark_provider_attempt_submitted(
    db: Session, *, attempt_id: UUID, deadline_at: datetime, now: datetime | None = None
) -> ProviderAttempt:
    attempt = _locked_attempt(db, attempt_id)
    if attempt.status is AttemptStatus.submitted:
        return attempt
    now = _utc(now)
    deadline_at = _utc(deadline_at)
    if attempt.status is not AttemptStatus.reserved or attempt.reservation_valid_until <= now or deadline_at <= now:
        raise _transition_error()
    attempt.status = AttemptStatus.submitted
    attempt.submitted_at = now
    attempt.deadline_at = deadline_at
    db.flush()
    return attempt


def cancel_provider_attempt(db: Session, *, attempt_id: UUID, now: datetime | None = None) -> ProviderAttempt:
    attempt = _locked_attempt(db, attempt_id)
    if attempt.status is AttemptStatus.cancelled:
        return attempt
    if attempt.status is not AttemptStatus.reserved:
        raise _transition_error()
    attempt.status = AttemptStatus.cancelled
    attempt.outcome = AttemptOutcome.cancelled
    attempt.cancelled_at = _utc(now)
    db.flush()
    return attempt


def _settle(
    db: Session, *, attempt_id: UUID, outcome: AttemptOutcome, basis: ProviderSettlementBasis,
    settled_units: int, now: datetime | None = None, reported_total_tokens: int | None = None,
    reported_input_tokens: int | None = None, reported_output_tokens: int | None = None,
    measured_audio_seconds: Decimal | float | None = None,
) -> ProviderAttempt:
    attempt = _locked_attempt(db, attempt_id)
    if attempt.status is AttemptStatus.settled:
        if attempt.outcome is outcome and attempt.settlement_basis is basis and attempt.settled_units == settled_units:
            return attempt
        raise _transition_error()
    if attempt.status is not AttemptStatus.submitted:
        raise _transition_error()
    attempt.status = AttemptStatus.settled
    attempt.outcome = outcome
    attempt.settlement_basis = basis
    attempt.settled_units = settled_units
    attempt.settled_at = _utc(now)
    attempt.reported_total_tokens = reported_total_tokens
    attempt.reported_input_tokens = reported_input_tokens
    attempt.reported_output_tokens = reported_output_tokens
    attempt.measured_audio_seconds = measured_audio_seconds
    db.flush()
    return attempt


def settle_provider_attempt_tokens(
    db: Session, *, attempt_id: UUID, reported_total_tokens: int, outcome: AttemptOutcome = AttemptOutcome.succeeded,
    reported_input_tokens: int | None = None, reported_output_tokens: int | None = None, now: datetime | None = None,
) -> ProviderAttempt:
    if reported_total_tokens < 0:
        raise ValueError("reported_total_tokens must be non-negative")
    attempt = _locked_attempt(db, attempt_id)
    if attempt.resource is not QuotaResource.tokens:
        raise _transition_error()
    # _settle locks again in same transaction; PostgreSQL row lock is re-entrant.
    return _settle(db, attempt_id=attempt_id, outcome=outcome, basis=ProviderSettlementBasis.reported,
                   settled_units=reported_total_tokens, reported_total_tokens=reported_total_tokens,
                   reported_input_tokens=reported_input_tokens, reported_output_tokens=reported_output_tokens, now=now)


def settle_provider_attempt_audio(
    db: Session, *, attempt_id: UUID, measured_audio_seconds: Decimal | float,
    outcome: AttemptOutcome = AttemptOutcome.succeeded, now: datetime | None = None,
) -> ProviderAttempt:
    measured = Decimal(str(measured_audio_seconds))
    if measured <= 0:
        raise ValueError("measured_audio_seconds must be positive")
    attempt = _locked_attempt(db, attempt_id)
    if attempt.resource is not QuotaResource.audio_seconds:
        raise _transition_error()
    return _settle(db, attempt_id=attempt_id, outcome=outcome, basis=ProviderSettlementBasis.measured,
                   settled_units=ceil(measured), measured_audio_seconds=measured, now=now)


def settle_provider_attempt_unknown_tokens(db: Session, *, attempt_id: UUID, now: datetime | None = None) -> ProviderAttempt:
    attempt = _locked_attempt(db, attempt_id)
    if attempt.resource is not QuotaResource.tokens:
        raise _transition_error()
    return _settle(db, attempt_id=attempt_id, outcome=AttemptOutcome.unknown,
                   basis=ProviderSettlementBasis.conservative_unknown, settled_units=attempt.reserved_units, now=now)


# Explicit aliases keep call sites readable at dispatch/response boundaries.
settle_provider_attempt_reported_tokens = settle_provider_attempt_tokens
settle_provider_attempt_measured_audio = settle_provider_attempt_audio


def reconcile_provider_attempts(db: Session, *, now: datetime | None = None, limit: int = 100) -> ReconciliationResult:
    """Bounded worker cleanup. Audio requires persisted server measurement by schema."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    now = _utc(now)
    reservations = db.scalars(
        select(ProviderAttempt).where(ProviderAttempt.status == AttemptStatus.reserved,
                                      ProviderAttempt.reservation_valid_until <= now)
        .order_by(ProviderAttempt.reservation_valid_until).limit(limit).with_for_update(skip_locked=True)
    ).all()
    for attempt in reservations:
        attempt.status = AttemptStatus.cancelled
        attempt.outcome = AttemptOutcome.cancelled
        attempt.cancelled_at = now
    remaining = limit - len(reservations)
    submissions = [] if remaining == 0 else db.scalars(
        select(ProviderAttempt).where(ProviderAttempt.status == AttemptStatus.submitted,
                                      ProviderAttempt.deadline_at <= now)
        .order_by(ProviderAttempt.deadline_at).limit(remaining).with_for_update(skip_locked=True)
    ).all()
    settled = 0
    for attempt in submissions:
        if attempt.resource is QuotaResource.tokens:
            attempt.status = AttemptStatus.settled
            attempt.outcome = AttemptOutcome.unknown
            attempt.settlement_basis = ProviderSettlementBasis.conservative_unknown
            attempt.settled_units = attempt.reserved_units
            attempt.settled_at = now
            settled += 1
        else:
            # Reservation validation plus DB constraints guarantee this value.
            if attempt.measured_audio_seconds is None:
                raise RuntimeError("submitted audio attempt is missing measured_audio_seconds")
            attempt.status = AttemptStatus.settled
            attempt.outcome = AttemptOutcome.unknown
            attempt.settlement_basis = ProviderSettlementBasis.measured
            attempt.settled_units = ceil(Decimal(str(attempt.measured_audio_seconds)))
            attempt.settled_at = now
            settled += 1
    db.flush()
    return ReconciliationResult(len(reservations), settled, 0)
