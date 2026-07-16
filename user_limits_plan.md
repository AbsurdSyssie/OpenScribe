## Revised design

> **Implementation contract (2026-07-15):** Later code review found that the
> original "one adjustment table plus existing telemetry" footprint could not
> provide race-safe expenditure controls. Existing LLM telemetry omits some
> billable failures, ingestion rows omit synchronous STT calls and are deleted
> with transcript roots, and neither source carries durable reservations. The
> resolved design therefore keeps the four base limits and one policy-event
> ledger described below, but also adds a metadata-only provider-attempt ledger
> and task-dispatch outbox. Where this contract conflicts with later examples in
> this document, this contract wins.
>
> - Limits remain daily/monthly tokens and daily/monthly audio seconds.
> - `NULL` is unlimited. Zero is zero base allowance; an active explicit grant
>   may temporarily enable that resource.
> - Quota windows are UTC calendar days/months.
> - Quota enforcement starts prospectively when a window changes from unlimited
>   to finite. Historical telemetry is reporting-only and is not backfilled into
>   authoritative quota accounting.
> - Every potentially billable LLM or STT call receives one provider-attempt
>   row. This includes main generation, each hallucination-check attempt, live
>   and whole-file STT, explicit retries, dictation/context STT, and synthetic
>   provider tests. Synthetic admin tests have no normal-user quota owner.
> - Reservations are serialized per user. Accepted reservations survive reset,
>   grant expiry/revocation, and later limit reductions. Authorization time is
>   the accounting timestamp.
> - Reported total tokens settle actual token use. Unknown post-dispatch token
>   outcomes settle the conservative reservation. Audio settles server-measured
>   duration. Definite pre-dispatch failures release reservations.
> - Token input reservation uses a centralized conservative text-size estimate:
>   outbound UTF-8 byte length plus fixed message overhead and the configured
>   maximum completion tokens. Underestimation settles actual use, records a
>   safe operational error, and blocks later reservations until capacity exists.
> - Job, reservation, and deterministic task-dispatch intent commit together in
>   a metadata-only outbox. Workers atomically claim work immediately before
>   provider dispatch; duplicate delivery cannot invoke the provider twice.
> - Policy actor/revoker foreign keys use `ON DELETE SET NULL`. Immutable actor
>   UUID snapshots preserve provenance without blocking mandatory hard deletion.
> - Free-text administrative reasons remain in the quota ledger and must not
>   contain patient/clinical data. Security audit rows receive controlled reason
>   codes only, never the free text.
> - Base-limit changes create durable before/after policy events in the same
>   transaction as the `users` update.
> - Provider-attempt rows contain metadata only. Transcript/document/job links
>   become null when content is deleted; owner attribution becomes null on user
>   deletion; team deletion removes the attempt rows.
> - Initial administration is system-admin-only through a URL-addressable member
>   detail panel in canonical `/admin`. No JSON quota-management API is added in
>   the first slice.

Keep the four base limits on `users`, but add **one quota-adjustment ledger table** for temporary allowances and early resets.

Do not modify or delete usage telemetry when an admin resets a quota. The existing provider and ingestion records should remain intact for reporting and audit purposes. OpenScribe already uses those records for per-user usage reporting.

## 1. Permanent quota settings

Add these nullable fields to `users`:

```python
daily_token_limit: Mapped[int | None]
monthly_token_limit: Mapped[int | None]
daily_audio_seconds_limit: Mapped[int | None]
monthly_audio_seconds_limit: Mapped[int | None]
```

Semantics:

|            Value | Meaning            |
| ---------------: | ------------------ |
|           `NULL` | Unlimited          |
|              `0` | Feature disabled   |
| Positive integer | Enforced allowance |

Admins can edit these values from the user-management screen.

## 2. One adjustment table

```python
class UserQuotaAdjustment(Base):
    __tablename__ = "user_quota_adjustments"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    resource: Mapped[QuotaResource] = mapped_column(
        Enum(QuotaResource),
        nullable=False,
    )
    # tokens | audio_seconds

    period: Mapped[QuotaPeriod] = mapped_column(
        Enum(QuotaPeriod),
        nullable=False,
    )
    # daily | monthly

    adjustment_type: Mapped[QuotaAdjustmentType] = mapped_column(
        Enum(QuotaAdjustmentType),
        nullable=False,
    )
    # grant | reset

    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Required for grant, NULL for reset.

    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
```

This single table supports:

* Temporary extra tokens
* Temporary extra transcription time
* Daily quota reset
* Monthly quota reset
* Revoking a temporary grant
* Recording who made each change and why
* Displaying adjustment history

## 3. Temporary quota

Suppose a user has a monthly token limit of 500,000 and an admin grants another 100,000 tokens until Friday.

Create:

```python
UserQuotaAdjustment(
    user_id=user.id,
    resource=QuotaResource.tokens,
    period=QuotaPeriod.monthly,
    adjustment_type=QuotaAdjustmentType.grant,
    amount=100_000,
    effective_at=utcnow(),
    expires_at=friday_end,
    reason="Temporary allowance for backlog",
    created_by_user_id=admin.id,
)
```

The effective limit becomes:

```text
base limit + active grants
```

An active grant is one where:

```text
effective_at <= now
AND (expires_at IS NULL OR expires_at > now)
AND revoked_at IS NULL
```

The same mechanism handles audio:

```text
resource = audio_seconds
amount = 7,200
```

That grants two additional hours.

### Grant scope

Keep daily and monthly grants independent.

For example:

* `daily + 20,000 tokens` affects only the daily check.
* `monthly + 100,000 tokens` affects only the monthly check.
* An admin can grant both in one form submission when required.

This is clearer than attempting to infer how a single grant should affect both periods.

## 4. Early reset

An early reset creates a reset adjustment:

```python
UserQuotaAdjustment(
    user_id=user.id,
    resource=QuotaResource.tokens,
    period=QuotaPeriod.daily,
    adjustment_type=QuotaAdjustmentType.reset,
    amount=None,
    effective_at=utcnow(),
    reason="Reset approved after failed batch",
    created_by_user_id=admin.id,
)
```

When calculating usage, use the later of:

1. The natural window start
2. The most recent active reset in that window

```python
usage_start = max(
    natural_window_start,
    latest_reset_at or natural_window_start,
)
```

Then usage is summed from `usage_start`.

### Important reset behaviour

A **daily reset should not reset monthly usage**.

Example:

* Monthly usage: 300,000 tokens
* Today’s usage: 40,000 tokens
* Admin resets daily quota

After reset:

* Daily usage becomes zero.
* Monthly usage remains 300,000.

A full reset requires two adjustment records:

```text
tokens / daily / reset
tokens / monthly / reset
```

The admin UI can provide a “Reset both daily and monthly” action that creates both records in one transaction.

The same applies independently to audio usage.

## 5. Quota calculation

The quota service should return a detailed result:

```python
@dataclass(frozen=True)
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
```

Core calculation:

```python
effective_limit = (
    None
    if base_limit is None
    else base_limit + active_grant_total
)

consumed = usage_since(usage_start)
committed = consumed + pending_reserved

remaining = (
    None
    if effective_limit is None
    else max(0, effective_limit - committed)
)
```

Enforcement checks both windows:

```python
require_quota(
    db,
    user=user,
    resource=QuotaResource.tokens,
    requested_amount=reserved_tokens,
)
```

Internally:

```python
daily = calculate_quota_window(...)
monthly = calculate_quota_window(...)

for window in (daily, monthly):
    if (
        window.effective_limit is not None
        and window.consumed
            + window.pending_reserved
            + requested_amount
            > window.effective_limit
    ):
        raise quota_error(window)
```

## 6. Admin interface

Add a **Quota** section to the existing user detail/edit screen.

### Current status

Display:

| Resource | Period |    Used | Reserved | Base limit | Temporary | Remaining | Reset        |
| -------- | ------ | ------: | -------: | ---------: | --------: | --------: | ------------ |
| Tokens   | Today  |  12,400 |    1,600 |     25,000 |     5,000 |    16,000 | Midnight UTC |
| Tokens   | Month  | 180,000 |    1,600 |    500,000 |   100,000 |   418,400 | 1 August     |
| Audio    | Today  |    0.7h |     0.1h |         2h |         — |      1.2h | Midnight UTC |
| Audio    | Month  |     11h |     0.1h |        30h |        5h |     23.9h | 1 August     |

The existing admin service already has per-user token and ingestion aggregation structures, so the quota display can reuse similar SQL aggregation patterns.

### Permanent limits form

Fields:

```text
Daily token limit
Monthly token limit
Daily audio hours
Monthly audio hours
```

Allow an “Unlimited” checkbox for each value rather than requiring admins to understand that an empty field means unlimited.

Convert audio hours to seconds at the service boundary:

```python
audio_seconds = round(audio_hours * 3600)
```

Store integer seconds, not decimal hours.

### Temporary allowance form

Fields:

```text
Resource: Tokens / Audio
Period: Daily / Monthly
Amount
Expires: Date and time
Reason
```

Useful presets:

```text
Until end of today
Until end of current month
24 hours
7 days
Custom
No expiry
```

“No expiry” is technically supported, but the UI should describe it as an additional allowance rather than temporary quota.

### Reset actions

Provide separate actions:

```text
Reset daily token usage
Reset monthly token usage
Reset daily audio usage
Reset monthly audio usage
Reset all usage windows
```

Each action should:

1. Require a reason.
2. Show the amount currently consumed.
3. Explain what will remain counted.
4. Require explicit confirmation.
5. Create adjustment records within one transaction.

For example:

> Reset daily token quota? The user’s daily usage will restart from zero. Their monthly token usage will not change.

## 7. Admin service functions

Add these to `app/services/admin.py` or a small `app/services/admin_quotas.py`:

```python
def update_user_base_quotas(
    db: Session,
    *,
    actor: User,
    user_id: UUID,
    daily_token_limit: int | None,
    monthly_token_limit: int | None,
    daily_audio_seconds_limit: int | None,
    monthly_audio_seconds_limit: int | None,
) -> User:
    ...
```

```python
def grant_user_quota(
    db: Session,
    *,
    actor: User,
    user_id: UUID,
    resource: QuotaResource,
    period: QuotaPeriod,
    amount: int,
    expires_at: datetime | None,
    reason: str,
) -> UserQuotaAdjustment:
    ...
```

```python
def reset_user_quota(
    db: Session,
    *,
    actor: User,
    user_id: UUID,
    resource: QuotaResource,
    period: QuotaPeriod,
    reason: str,
) -> UserQuotaAdjustment:
    ...
```

```python
def revoke_user_quota_adjustment(
    db: Session,
    *,
    actor: User,
    adjustment_id: UUID,
    reason: str,
) -> UserQuotaAdjustment:
    ...
```

All should require a system administrator unless you deliberately want team leaders to manage their own users.

Given the sensitivity of provider expenditure controls, I would initially make this **system-admin only**. Team-leader quota management can be added later as a separate permission.

## 8. Audit logging

Use both:

1. The adjustment row, as the operational ledger.
2. The existing security-audit service, for security/admin activity.

The repository already imports and uses `record_security_event` within the admin service.

Suggested event names:

```text
user_quota_limits_updated
user_quota_grant_created
user_quota_grant_revoked
user_quota_daily_reset
user_quota_monthly_reset
user_quota_all_reset
```

Audit metadata should contain no clinical content:

```python
{
    "target_user_id": str(user.id),
    "resource": resource.value,
    "period": period.value,
    "amount": amount,
    "expires_at": expires_at.isoformat() if expires_at else None,
    "reason": reason,
    "previous_limits": previous_limits,
    "new_limits": new_limits,
}
```

Do not overwrite or delete an adjustment when reversing it. Set `revoked_at` and `revoked_by_user_id`.

A reset normally should not be reversible: usage may have occurred after the reset, making reversal ambiguous.

## 9. Validation rules

Use conservative bounds to prevent accidental values:

```python
MAX_DAILY_TOKENS = 10_000_000
MAX_MONTHLY_TOKENS = 100_000_000

MAX_DAILY_AUDIO_SECONDS = 24 * 3600
MAX_MONTHLY_AUDIO_SECONDS = 1_000 * 3600

MAX_GRANT_TOKENS = 100_000_000
MAX_GRANT_AUDIO_SECONDS = 1_000 * 3600
```

Also enforce:

* Limits and grants cannot be negative.
* `expires_at` must be in the future.
* Reason must not be blank.
* Monthly limit does not have to exceed daily limit, but the UI should warn when it does not.
* The target must be a normal user, unless admins themselves consume generation resources.
* Revoked and expired grants do not count.
* Reset records must have `amount = NULL`.
* Grant records must have `amount > 0`.

Add database check constraints for the final two rules.

## 10. Indexes

```python
Index(
    "ix_user_quota_adjustments_lookup",
    "user_id",
    "resource",
    "period",
    "effective_at",
)

Index(
    "ix_user_quota_adjustments_active_grants",
    "user_id",
    "resource",
    "period",
    "expires_at",
)
```

Continue to add the previously recommended telemetry indexes:

```python
Index(
    "ix_provider_usage_events_owner_event_created",
    "owner_user_id",
    "event_type",
    "created_at",
)

Index(
    "ix_transcript_ingestion_jobs_owner_created",
    "owner_user_id",
    "created_at",
)
```

## Resulting implementation footprint

This remains relatively small:

* Four columns on `users`
* One `user_quota_adjustments` table
* One quota calculation/enforcement service
* Four admin service operations
* A quota panel in the existing admin user UI
* Hooks at the generation and ingestion queue boundaries
* Existing telemetry remains the source of consumed usage
* Existing security audit infrastructure records admin actions

This is preferable to mutable daily/monthly counter rows because there are no reset jobs, no counter reconciliation process, and an admin reset never destroys historical usage.
