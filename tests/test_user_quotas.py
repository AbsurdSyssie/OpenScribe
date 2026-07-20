from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import (
    AttemptKind, AttemptOutcome, AttemptStatus, ProviderAttempt, QuotaPeriod,
    QuotaResource, Transcript, TranscriptIngestionJob, TranscriptIngestionJobKind,
    TranscriptIngestionMode, TranscriptStatus, TranscriptVersion, UserQuotaPolicyEvent,
    UserQuotaPolicyEventType, UserQuotaReasonCode,
)
from app.services.quotas import (
    DEFAULT_TOKEN_MESSAGE_OVERHEAD, calculate_quota_window, estimate_token_reservation,
    cancel_provider_attempt, increase_provider_attempt_reservation, mark_provider_attempt_submitted,
    reconcile_provider_attempts, reserve_provider_attempt,
    settle_provider_attempt_audio, settle_provider_attempt_tokens, settle_provider_attempt_unknown_tokens,
)
from app.services.admin_quotas import (
    MAX_DAILY_TOKENS, get_admin_user_quota_detail, grant_user_quota_batch,
    reset_user_quota_batch, revoke_user_quota_grant, update_user_base_quotas_batch,
)


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _event(user, *, resource, period, kind, effective_at=NOW, amount=None, previous=None, new=None,
           expires_at=None, revoked_at=None):
    event = UserQuotaPolicyEvent(
        operation_id=uuid4(), target_user_id=user.id, actor_user_id=user.id,
        actor_user_id_snapshot=user.id, event_type=kind, resource=resource, period=period,
        reason_code=UserQuotaReasonCode.other, reason="synthetic quota test", amount=amount,
        previous_limit=previous, new_limit=new, effective_at=effective_at, expires_at=expires_at,
        revoked_at=revoked_at,
    )
    if revoked_at is not None:
        event.revoker_user_id = user.id
        event.revoker_user_id_snapshot = user.id
        event.revocation_operation_id = uuid4()
        event.revocation_reason_code = UserQuotaReasonCode.other
        event.revocation_reason = "synthetic revocation"
    return event


def _reserve(db, user, *, resource=QuotaResource.tokens, units=10, now=NOW, valid_for=timedelta(minutes=5),
             correlation_id=None, provider_adapter=None, measured_audio_seconds=None):
    if resource is QuotaResource.audio_seconds and measured_audio_seconds is None:
        measured_audio_seconds = Decimal(units)
    return reserve_provider_attempt(
        db, team_id=user.team_id, owner_user_id=user.id, resource=resource,
        attempt_kind=AttemptKind.llm_generation if resource is QuotaResource.tokens else AttemptKind.stt_conversation,
        correlation_id=correlation_id or uuid4(), attempt_number=1, reserved_units=units,
        authorized_at=now, reservation_valid_until=now + valid_for,
        provider_adapter=provider_adapter, measured_audio_seconds=measured_audio_seconds,
    )


def test_unlimited_ignores_grants_and_zero_base_grant_enables_user(db_session, make_user):
    user = make_user()
    db_session.add(_event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily,
                          kind=UserQuotaPolicyEventType.grant, amount=99))
    db_session.commit()
    unlimited = calculate_quota_window(db_session, user=user, resource=QuotaResource.tokens,
                                       period=QuotaPeriod.daily, now=NOW)
    assert (unlimited.effective_limit, unlimited.temporary_allowance, unlimited.remaining) == (None, 0, None)

    user.daily_token_limit = 0
    db_session.add(_event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily,
                          kind=UserQuotaPolicyEventType.grant, amount=20))
    db_session.commit()
    enabled = calculate_quota_window(db_session, user=user, resource=QuotaResource.tokens,
                                     period=QuotaPeriod.daily, now=NOW)
    assert (enabled.effective_limit, enabled.remaining) == (119, 119)


def test_active_future_expired_and_revoked_grants(db_session, make_user):
    user = make_user()
    user.daily_token_limit = 10
    db_session.add_all([
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily, kind=UserQuotaPolicyEventType.grant, amount=5),
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily, kind=UserQuotaPolicyEventType.grant, amount=7, effective_at=NOW + timedelta(seconds=1)),
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily, kind=UserQuotaPolicyEventType.grant, amount=11,
               effective_at=NOW - timedelta(seconds=1), expires_at=NOW),
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily, kind=UserQuotaPolicyEventType.grant, amount=13, revoked_at=NOW),
    ])
    db_session.commit()
    window = calculate_quota_window(db_session, user=user, resource=QuotaResource.tokens,
                                    period=QuotaPeriod.daily, now=NOW)
    assert (window.temporary_allowance, window.effective_limit) == (5, 15)


def test_daily_and_monthly_limited_independently(db_session, make_user):
    user = make_user()
    user.daily_token_limit = 10
    user.monthly_token_limit = 15
    db_session.commit()
    _reserve(db_session, user, units=10)
    db_session.commit()
    with pytest.raises(AppError) as error:
        _reserve(db_session, user, units=1)
    assert error.value.code == "quota_exceeded"


def test_activation_reset_and_pre_reset_pending(db_session, make_user):
    user = make_user()
    user.daily_token_limit = 100
    db_session.add_all([
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily,
               kind=UserQuotaPolicyEventType.limit_change, effective_at=NOW - timedelta(hours=3), previous=None, new=100),
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily,
               kind=UserQuotaPolicyEventType.limit_change, effective_at=NOW - timedelta(hours=2), previous=100, new=50),
        _event(user, resource=QuotaResource.tokens, period=QuotaPeriod.daily,
               kind=UserQuotaPolicyEventType.reset, effective_at=NOW - timedelta(hours=1)),
    ])
    db_session.commit()
    pending = _reserve(db_session, user, units=10, now=NOW - timedelta(hours=4), valid_for=timedelta(days=1))
    db_session.commit()
    window = calculate_quota_window(db_session, user=user, resource=QuotaResource.tokens,
                                    period=QuotaPeriod.daily, now=NOW)
    assert window.usage_start == NOW - timedelta(hours=1)
    assert window.pending_reserved == pending.reserved_units


def test_expired_reservation_excluded_and_disabled_error(db_session, make_user):
    user = make_user()
    user.daily_token_limit = 10
    user.monthly_token_limit = 10
    db_session.commit()
    _reserve(db_session, user, units=10, valid_for=timedelta(seconds=1))
    db_session.commit()
    assert calculate_quota_window(db_session, user=user, resource=QuotaResource.tokens,
                                  period=QuotaPeriod.daily, now=NOW + timedelta(minutes=1)).pending_reserved == 0
    user.daily_token_limit = user.monthly_token_limit = 0
    db_session.commit()
    with pytest.raises(AppError) as error:
        _reserve(db_session, user, units=1, now=NOW + timedelta(minutes=1))
    assert (error.value.status_code, error.value.code) == (403, "quota_disabled")
    assert error.value.details == {
        "resource": "tokens", "period": "daily", "effective_limit": 0,
        "consumed": 0, "pending_reserved": 0, "requested": 1, "remaining": 0,
        "resets_at": "2026-07-16T00:00:00+00:00",
    }


def test_audio_reservation_requires_matching_measurement_before_flush(db_session, make_user):
    user = make_user()
    audio = _reserve(db_session, user, resource=QuotaResource.audio_seconds, units=3,
                     measured_audio_seconds=Decimal("2.01"))
    assert audio.measured_audio_seconds == Decimal("2.01")
    pending_before = len(db_session.new)
    with pytest.raises(ValueError, match="ceil"):
        _reserve(db_session, user, resource=QuotaResource.audio_seconds, units=2,
                 measured_audio_seconds=Decimal("2.01"))
    with pytest.raises(ValueError, match="must not"):
        _reserve(db_session, user, measured_audio_seconds=Decimal("1"))
    assert len(db_session.new) == pending_before


def test_exact_reservation_retry_bypasses_exhausted_quota_and_conflicts(db_session, make_user):
    user = make_user()
    user.daily_token_limit = user.monthly_token_limit = 10
    db_session.commit()
    correlation_id = uuid4()
    first = _reserve(db_session, user, units=10, correlation_id=correlation_id, provider_adapter="synthetic")
    retry = _reserve(db_session, user, units=10, correlation_id=correlation_id, provider_adapter="synthetic")
    assert retry.id == first.id
    with pytest.raises(AppError) as error:
        _reserve(db_session, user, units=10, correlation_id=correlation_id, provider_adapter="different")
    assert (error.value.status_code, error.value.code) == (409, "provider_attempt_idempotency_conflict")


def test_increase_provider_attempt_reservation_enforces_delta_and_is_idempotent(db_session, make_user):
    user = make_user()
    user.daily_token_limit = user.monthly_token_limit = 15
    db_session.commit()
    attempt = _reserve(db_session, user, units=10)
    expanded = increase_provider_attempt_reservation(db_session, attempt_id=attempt.id, required_units=15, now=NOW)
    assert expanded.reserved_units == 15
    assert increase_provider_attempt_reservation(db_session, attempt_id=attempt.id, required_units=14, now=NOW).id == attempt.id
    with pytest.raises(AppError) as error:
        increase_provider_attempt_reservation(db_session, attempt_id=attempt.id, required_units=16, now=NOW)
    assert error.value.code == "quota_exceeded"
    assert error.value.details["required_units"] == 16
    assert error.value.details["delta"] == 1


def test_increase_provider_attempt_reservation_rejects_invalid_and_handles_ownerless(db_session, make_team, make_user):
    user = make_user()
    attempt = _reserve(db_session, user, units=2)
    mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, deadline_at=NOW + timedelta(minutes=1), now=NOW)
    with pytest.raises(AppError) as error:
        increase_provider_attempt_reservation(db_session, attempt_id=attempt.id, required_units=3, now=NOW)
    assert error.value.code == "provider_attempt_invalid_transition"
    audio = _reserve(db_session, user, resource=QuotaResource.audio_seconds, units=2,
                     measured_audio_seconds=Decimal("1.1"))
    with pytest.raises(AppError) as error:
        increase_provider_attempt_reservation(db_session, attempt_id=audio.id, required_units=3, now=NOW)
    assert error.value.code == "provider_attempt_audio_reservation_immutable"
    team = make_team(name="Unmetered increase team")
    unmetered = reserve_provider_attempt(
        db_session, team_id=team.id, owner_user_id=None, resource=QuotaResource.tokens,
        attempt_kind=AttemptKind.stt_provider_test, correlation_id=uuid4(), attempt_number=1,
        reserved_units=1, authorized_at=NOW, reservation_valid_until=NOW + timedelta(minutes=1),
    )
    assert increase_provider_attempt_reservation(db_session, attempt_id=unmetered.id, required_units=50, now=NOW).reserved_units == 50


def test_reservation_transitions_and_settlement_forms(db_session, make_user):
    user = make_user()
    token = _reserve(db_session, user, units=20)
    mark_provider_attempt_submitted(db_session, attempt_id=token.id, deadline_at=NOW + timedelta(minutes=2), now=NOW)
    settled = settle_provider_attempt_tokens(db_session, attempt_id=token.id, reported_total_tokens=17, now=NOW)
    assert (settled.status, settled.settled_units) == (AttemptStatus.settled, 17)
    assert settle_provider_attempt_tokens(db_session, attempt_id=token.id, reported_total_tokens=17, now=NOW).id == token.id

    audio = _reserve(db_session, user, resource=QuotaResource.audio_seconds, units=3)
    mark_provider_attempt_submitted(db_session, attempt_id=audio.id, deadline_at=NOW + timedelta(minutes=2), now=NOW)
    measured = settle_provider_attempt_audio(db_session, attempt_id=audio.id, measured_audio_seconds=Decimal("2.01"), now=NOW)
    assert measured.settled_units == 3

    unknown = _reserve(db_session, user, units=21)
    mark_provider_attempt_submitted(db_session, attempt_id=unknown.id, deadline_at=NOW + timedelta(minutes=2), now=NOW)
    assert settle_provider_attempt_unknown_tokens(db_session, attempt_id=unknown.id, now=NOW).settled_units == 21
    db_session.commit()


def test_invalid_transitions_and_ownerless_provider_test(db_session, make_team, make_user):
    user = make_user()
    attempt = _reserve(db_session, user)
    with pytest.raises(AppError):
        settle_provider_attempt_unknown_tokens(db_session, attempt_id=attempt.id)
    with pytest.raises(AppError):
        mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, deadline_at=NOW + timedelta(minutes=1), now=NOW + timedelta(days=1))
    assert cancel_provider_attempt(db_session, attempt_id=attempt.id, now=NOW).status is AttemptStatus.cancelled
    assert cancel_provider_attempt(db_session, attempt_id=attempt.id, now=NOW).status is AttemptStatus.cancelled
    team = make_team(name="Provider test team")
    test_attempt = reserve_provider_attempt(
        db_session, team_id=team.id, owner_user_id=None, resource=QuotaResource.tokens,
        attempt_kind=AttemptKind.stt_provider_test, correlation_id=uuid4(), attempt_number=1,
        reserved_units=1, authorized_at=NOW, reservation_valid_until=NOW + timedelta(minutes=1),
    )
    assert test_attempt.owner_user_id is None


def test_utf8_estimator_and_bounded_reconciliation(db_session, make_user):
    assert estimate_token_reservation(["é", "a"], max_completion_tokens=10) == 2 + 1 + 2 * DEFAULT_TOKEN_MESSAGE_OVERHEAD + 10
    user = make_user()
    reserved = _reserve(db_session, user, units=10, valid_for=timedelta(seconds=1))
    submitted = _reserve(db_session, user, units=11)
    mark_provider_attempt_submitted(db_session, attempt_id=submitted.id, deadline_at=NOW + timedelta(seconds=1), now=NOW)
    audio = _reserve(db_session, user, resource=QuotaResource.audio_seconds, units=3,
                     measured_audio_seconds=Decimal("2.01"))
    mark_provider_attempt_submitted(db_session, attempt_id=audio.id, deadline_at=NOW + timedelta(seconds=1), now=NOW)
    result = reconcile_provider_attempts(db_session, now=NOW + timedelta(minutes=1))
    assert (result.cancelled_reservations, result.settled_submissions, result.skipped_audio_submissions) == (1, 2, 0)
    assert db_session.get(ProviderAttempt, reserved.id).status is AttemptStatus.cancelled
    assert db_session.get(ProviderAttempt, submitted.id).outcome is AttemptOutcome.unknown
    assert db_session.get(ProviderAttempt, audio.id).outcome is AttemptOutcome.unknown


def test_postgres_user_lock_allows_only_one_competing_reservation(db_session, make_user):
    """Real independent sessions: second reservation reads first after FOR UPDATE release."""
    user = make_user()
    user.daily_token_limit = user.monthly_token_limit = 10
    db_session.commit()
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    locked = Event()
    release = Event()
    outcomes: list[str] = []

    def first() -> None:
        with session_factory() as session:
            _reserve(session, user, units=6)
            locked.set()
            assert release.wait(5)
            session.commit()
            outcomes.append("accepted")

    def second() -> None:
        assert locked.wait(5)
        with session_factory() as session:
            try:
                _reserve(session, user, units=6)
                session.commit()
                outcomes.append("unexpected_accept")
            except AppError as error:
                session.rollback()
                outcomes.append(error.code)

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert locked.wait(5)
    release.set()
    first_thread.join(5)
    second_thread.join(5)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert sorted(outcomes) == ["accepted", "quota_exceeded"]


def _quota_admin(make_user):
    return make_user(email=f"quota-admin-{uuid4()}@example.com", is_system_admin=True)


def _limits(db, *, actor, target, operation_id=None, **overrides):
    values = {
        "daily_token_limit": 10,
        "monthly_token_limit": 20,
        "daily_audio_seconds_limit": 30,
        "monthly_audio_seconds_limit": 40,
    }
    values.update(overrides)
    return update_user_base_quotas_batch(
        db, actor=actor, user_id=target.id, operation_id=operation_id or uuid4(),
        reason_code=UserQuotaReasonCode.policy_change, reason="  quota policy  ", now=NOW,
        **values,
    )


def test_admin_quota_target_auth_and_validation(db_session, make_user):
    actor, target = _quota_admin(make_user), make_user(email="quota-target@example.com")
    other = make_user(email="quota-other@example.com", team=target.team)
    with pytest.raises(AppError) as error:
        _limits(db_session, actor=target, target=other)
    assert error.value.code == "quota_admin_forbidden"
    with pytest.raises(AppError) as error:
        _limits(db_session, actor=actor, target=actor)
    assert error.value.code == "quota_target_self_forbidden"
    admin_target = _quota_admin(make_user)
    with pytest.raises(AppError) as error:
        _limits(db_session, actor=actor, target=admin_target)
    assert error.value.code == "quota_target_ineligible"
    with pytest.raises(AppError) as error:
        _limits(db_session, actor=actor, target=target, daily_token_limit=-1)
    assert error.value.code == "quota_limit_invalid"
    with pytest.raises(AppError) as error:
        _limits(db_session, actor=actor, target=target, daily_token_limit=MAX_DAILY_TOKENS + 1)
    assert error.value.code == "quota_limit_invalid"
    with pytest.raises(AppError) as error:
        update_user_base_quotas_batch(
            db_session, actor=actor, user_id=target.id, daily_token_limit=1, monthly_token_limit=1,
            daily_audio_seconds_limit=1, monthly_audio_seconds_limit=1, operation_id=uuid4(),
            reason_code=UserQuotaReasonCode.other, reason="  ", now=NOW,
        )
    assert error.value.code == "quota_reason_required"


def test_quota_limit_batch_durable_history_and_idempotency(db_session, make_user):
    actor, target = _quota_admin(make_user), make_user(email="quota-history@example.com")
    target.daily_token_limit = 2
    db_session.commit()
    operation_id = uuid4()
    first = _limits(db_session, actor=actor, target=target, operation_id=operation_id, daily_token_limit=3)
    retry = _limits(db_session, actor=actor, target=target, operation_id=operation_id, daily_token_limit=3)
    assert retry == first
    events = db_session.query(UserQuotaPolicyEvent).filter_by(operation_id=operation_id).all()
    assert len(events) == 4
    daily = next(event for event in events if event.resource is QuotaResource.tokens and event.period is QuotaPeriod.daily)
    assert (daily.previous_limit, daily.new_limit, daily.reason) == (2, 3, "quota policy")
    with pytest.raises(AppError) as error:
        _limits(db_session, actor=actor, target=target, operation_id=operation_id, daily_token_limit=4)
    assert error.value.code == "quota_operation_idempotency_conflict"


def test_grant_batch_validates_unlimited_and_enables_zero_atomically(db_session, make_user):
    actor, target = _quota_admin(make_user), make_user(email="quota-grant@example.com")
    with pytest.raises(AppError) as error:
        grant_user_quota_batch(
            db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
            periods=(QuotaPeriod.daily,), amount=1, expires_at=None, operation_id=uuid4(),
            reason_code=UserQuotaReasonCode.temporary_allowance, reason="grant", now=NOW,
        )
    assert error.value.code == "quota_grant_unlimited"
    target.daily_token_limit = target.monthly_token_limit = 0
    db_session.commit()
    operation_id = uuid4()
    result = grant_user_quota_batch(
        db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
        periods=(QuotaPeriod.daily, QuotaPeriod.monthly), amount=5, expires_at=NOW + timedelta(hours=1),
        operation_id=operation_id, reason_code=UserQuotaReasonCode.temporary_allowance, reason=" grant ", now=NOW,
    )
    assert len(result.event_ids) == 2
    assert calculate_quota_window(db_session, user=target, resource=QuotaResource.tokens, period=QuotaPeriod.daily, now=NOW).effective_limit == 5
    assert {event.effective_at for event in db_session.query(UserQuotaPolicyEvent).filter_by(operation_id=operation_id)} == {NOW}
    with pytest.raises(AppError) as error:
        grant_user_quota_batch(
            db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
            periods=(QuotaPeriod.daily,), amount=1, expires_at=NOW, operation_id=uuid4(),
            reason_code=UserQuotaReasonCode.other, reason="past", now=NOW,
        )
    assert error.value.code == "quota_expiry_invalid"


@pytest.mark.parametrize(
    ("expiry_policy", "first_now", "retry_now", "expected_expiry"),
    (
        ("24h", NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=24)),
        ("7d", NOW, NOW + timedelta(days=1), NOW + timedelta(days=7)),
        (
            "end_today", datetime(2026, 7, 15, 23, 59, tzinfo=UTC),
            datetime(2026, 7, 16, 1, tzinfo=UTC), datetime(2026, 7, 15, 23, 59, 59, 999999, tzinfo=UTC),
        ),
        (
            "end_month", datetime(2026, 7, 31, 23, 59, tzinfo=UTC),
            datetime(2026, 8, 1, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, 999999, tzinfo=UTC),
        ),
    ),
)
def test_preset_grant_expiry_is_stable_across_operation_retry(
    db_session, make_user, expiry_policy, first_now, retry_now, expected_expiry,
):
    actor, target = _quota_admin(make_user), make_user(email="quota-relative-expiry@example.com")
    target.daily_token_limit = 0
    db_session.commit()
    operation_id = uuid4()
    first = grant_user_quota_batch(
        db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
        periods=(QuotaPeriod.daily,), amount=5, expires_at=None, expiry_policy=expiry_policy,
        operation_id=operation_id, reason_code=UserQuotaReasonCode.temporary_allowance,
        reason="preset grant", now=first_now,
    )
    retry = grant_user_quota_batch(
        db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
        periods=(QuotaPeriod.daily,), amount=5, expires_at=None, expiry_policy=expiry_policy,
        operation_id=operation_id, reason_code=UserQuotaReasonCode.temporary_allowance,
        reason="preset grant", now=retry_now,
    )
    event = db_session.get(UserQuotaPolicyEvent, first.event_ids[0])
    assert retry == first
    assert event.expires_at == expected_expiry


def test_reset_batch_and_revoke_are_atomic_idempotent_and_keep_original_reason(db_session, make_user):
    actor, target = _quota_admin(make_user), make_user(email="quota-reset@example.com")
    _limits(db_session, actor=actor, target=target)
    reset_id = uuid4()
    reset = reset_user_quota_batch(
        db_session, actor=actor, user_id=target.id,
        windows={(QuotaResource.tokens, QuotaPeriod.daily), (QuotaResource.tokens, QuotaPeriod.monthly),
                 (QuotaResource.audio_seconds, QuotaPeriod.daily), (QuotaResource.audio_seconds, QuotaPeriod.monthly)},
        operation_id=reset_id, reason_code=UserQuotaReasonCode.administrative_correction, reason="reset", now=NOW,
    )
    assert len(reset.event_ids) == 4
    reset_events = db_session.query(UserQuotaPolicyEvent).filter_by(operation_id=reset_id).all()
    assert {event.effective_at for event in reset_events} == {NOW}
    grant = grant_user_quota_batch(
        db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
        periods=(QuotaPeriod.daily,), amount=4, expires_at=None, operation_id=uuid4(),
        reason_code=UserQuotaReasonCode.temporary_allowance, reason="original grant", now=NOW,
    )
    revoke_id = uuid4()
    revoked = revoke_user_quota_grant(
        db_session, actor=actor, user_id=target.id, grant_id=grant.event_ids[0],
        revocation_operation_id=revoke_id, reason_code=UserQuotaReasonCode.other, reason="revoke reason", now=NOW,
    )
    assert revoke_user_quota_grant(
        db_session, actor=actor, user_id=target.id, grant_id=grant.event_ids[0],
        revocation_operation_id=revoke_id, reason_code=UserQuotaReasonCode.other, reason="revoke reason", now=NOW,
    ) == revoked
    event = db_session.get(UserQuotaPolicyEvent, grant.event_ids[0])
    assert (event.reason, event.revocation_reason, event.revoked_at) == ("original grant", "revoke reason", NOW)
    with pytest.raises(AppError) as error:
        revoke_user_quota_grant(
            db_session, actor=actor, user_id=target.id, grant_id=grant.event_ids[0],
            revocation_operation_id=uuid4(), reason_code=UserQuotaReasonCode.other, reason="again", now=NOW,
        )
    assert error.value.code == "quota_revocation_idempotency_conflict"


def test_quota_read_model_uses_live_actor_email_snapshot_and_safe_audit(db_session, make_user, monkeypatch):
    actor, target = _quota_admin(make_user), make_user(email="quota-read@example.com")
    audit_calls = []
    monkeypatch.setattr("app.services.admin_quotas.record_security_event", lambda *args, **kwargs: audit_calls.append(kwargs))
    _limits(db_session, actor=actor, target=target)
    event = db_session.query(UserQuotaPolicyEvent).order_by(UserQuotaPolicyEvent.id).first()
    assert event is not None
    active = grant_user_quota_batch(
        db_session, actor=actor, user_id=target.id, resource=QuotaResource.tokens,
        periods=(QuotaPeriod.daily,), amount=5, expires_at=None, operation_id=uuid4(),
        reason_code=UserQuotaReasonCode.temporary_allowance, reason="old active allowance", now=NOW,
    )
    active_event = db_session.get(UserQuotaPolicyEvent, active.event_ids[0])
    active_event.created_at = NOW + timedelta(seconds=1)
    # More than the UI limit proves latest-50 ordering stays deterministic.
    for index in range(51):
        db_session.add(UserQuotaPolicyEvent(
            operation_id=uuid4(), target_user_id=target.id, actor_user_id=actor.id,
            actor_user_id_snapshot=actor.id, event_type=UserQuotaPolicyEventType.reset,
            resource=QuotaResource.tokens, period=QuotaPeriod.daily,
            reason_code=UserQuotaReasonCode.other, reason="history", effective_at=NOW,
            created_at=NOW + timedelta(seconds=index + 2),
        ))
    db_session.commit()
    detail = get_admin_user_quota_detail(db_session, actor=actor, user_id=target.id, now=NOW)
    assert len(detail.windows) == 4
    assert len(detail.history) == 50
    assert active_event.id not in {item.id for item in detail.history}
    assert [item.id for item in detail.active_grants] == [active_event.id]
    assert [item.created_at for item in detail.history] == sorted(
        (item.created_at for item in detail.history), reverse=True
    )
    assert detail.history[0].actor_email == actor.email
    assert "quota policy" not in str(audit_calls[0]["details"])
    db_session.delete(actor)
    db_session.commit()
    detail = get_admin_user_quota_detail(db_session, actor=_quota_admin(make_user), user_id=target.id, now=NOW)
    assert all(item.actor_email is None for item in detail.history)
    assert all(item.actor_user_id_snapshot == actor.id for item in detail.history)
    assert detail.active_grants[0].actor_email is None
    assert detail.active_grants[0].actor_user_id_snapshot == actor.id


def test_quota_audit_failure_does_not_rollback_mutation(db_session, make_user, monkeypatch):
    actor, target = _quota_admin(make_user), make_user(email="quota-audit@example.com")
    monkeypatch.setattr("app.services.admin_quotas.record_security_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit down")))
    _limits(db_session, actor=actor, target=target)
    assert db_session.get(type(target), target.id).daily_token_limit == 10


def test_reserve_provider_attempt_rejects_cross_scope_content_links_and_allows_owned_links(
    db_session, make_team, make_user, make_generated_document,
):
    team = make_team(name="Attempt scope team")
    other_team = make_team(name="Other attempt scope team")
    owner = make_user(email="attempt-owner@example.com", team=team)
    other = make_user(email="attempt-other@example.com", team=other_team)
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="owned", ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready, retention_days_applied=30, retention_expires_at=NOW + timedelta(days=1),
    )
    other_transcript = Transcript(
        owner_user_id=other.id, team_id=other_team.id, title="other", ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready, retention_days_applied=30, retention_expires_at=NOW + timedelta(days=1),
    )
    sibling_transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="sibling", ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready, retention_days_applied=30, retention_expires_at=NOW + timedelta(days=1),
    )
    db_session.add_all((transcript, other_transcript, sibling_transcript)); db_session.flush()
    job = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="owned.wav",
    )
    other_job = TranscriptIngestionJob(
        transcript_id=other_transcript.id, owner_user_id=other.id, team_id=other_team.id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="other.wav",
    )
    sibling_job = TranscriptIngestionJob(
        transcript_id=sibling_transcript.id, owner_user_id=owner.id, team_id=team.id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="sibling.wav",
    )
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="metadata")
    other_version = TranscriptVersion(transcript_id=other_transcript.id, version_no=1, text_encrypted="metadata")
    db_session.add_all((job, other_job, sibling_job, version, other_version)); db_session.flush()
    document = make_generated_document(owner=owner, transcript=transcript, transcript_version=version)
    other_document = make_generated_document(owner=other, transcript=other_transcript, transcript_version=other_version)
    db_session.commit()

    base = dict(
        team_id=team.id, owner_user_id=owner.id, resource=QuotaResource.tokens,
        attempt_kind=AttemptKind.llm_generation, attempt_number=1, reserved_units=1,
        reservation_valid_until=NOW + timedelta(minutes=1), authorized_at=NOW,
    )
    for field, value in (
        ("owner_user_id", other.id),
        ("transcript_id", other_transcript.id),
        ("transcript_ingestion_job_id", other_job.id),
        ("generated_document_id", other_document.id),
    ):
        with pytest.raises(AppError) as error:
            reserve_provider_attempt(db_session, correlation_id=uuid4(), **(base | {field: value}))
        assert error.value.code in {"quota_owner_team_mismatch", "provider_attempt_reference_scope_mismatch"}
        db_session.rollback()
    with pytest.raises(AppError) as error:
        reserve_provider_attempt(
            db_session, correlation_id=uuid4(), **(base | {"transcript_id": transcript.id, "generated_document_id": other_document.id})
        )
    assert error.value.code == "provider_attempt_reference_scope_mismatch"
    db_session.rollback()
    with pytest.raises(AppError) as error:
        reserve_provider_attempt(
            db_session, correlation_id=uuid4(), **(base | {
                "transcript_id": transcript.id,
                "transcript_ingestion_job_id": sibling_job.id,
            })
        )
    assert error.value.code == "provider_attempt_reference_mismatch"
    db_session.rollback()

    valid = reserve_provider_attempt(
        db_session, correlation_id=uuid4(), **(base | {
            "transcript_id": transcript.id,
            "transcript_ingestion_job_id": job.id,
            "generated_document_id": document.id,
        })
    )
    assert (valid.owner_user_id, valid.transcript_id, valid.transcript_ingestion_job_id, valid.generated_document_id) == (
        owner.id, transcript.id, job.id, document.id,
    )
    with pytest.raises(AppError) as error:
        reserve_provider_attempt(
            db_session, team_id=team.id, owner_user_id=None, resource=QuotaResource.tokens,
            attempt_kind=AttemptKind.stt_provider_test, correlation_id=uuid4(), attempt_number=1,
            reserved_units=1, authorized_at=NOW, reservation_valid_until=NOW + timedelta(minutes=1),
            transcript_id=transcript.id,
        )
    assert error.value.code == "provider_attempt_owner_required"
