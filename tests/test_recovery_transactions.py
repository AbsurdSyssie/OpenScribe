from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import select, text

from app.errors import AppError
from app.models import AuthEmailToken, AuthEmailTokenPurpose, SessionStatus, UserSession, UserTrustedDevice, utcnow
from app.services.account import update_own_email, update_own_password
from app.services.auth import authenticate_user, create_session, lock_user_credentials, session_token_hash, trusted_device_token_hash, verify_password
from app.services.auth_email import confirm_password_reset, issue_auth_email_token

harness = importlib.import_module("conftest")


def test_failed_password_reset_during_revocation_rolls_back_all_authority(db_session, make_user, monkeypatch):
    user = make_user(email="atomic-reset@example.com", password="OriginalPassword123", mfa_required=False, mfa_enabled=False)
    raw_token = issue_auth_email_token(db_session, user, purpose=AuthEmailTokenPurpose.password_reset)
    old_session = create_session(db_session, user)
    trusted_device = UserTrustedDevice(
        user_id=user.id,
        device_token_hash=trusted_device_token_hash("atomic-reset-trusted-device"),
        expires_at=utcnow() + timedelta(days=1),
        last_mfa_verified_at=utcnow(),
    )
    db_session.add(trusted_device)
    db_session.commit()
    old_password_hash = user.password_hash
    auth_service = importlib.import_module("app.services.auth")
    original_revoke_trusted_devices = auth_service.revoke_trusted_devices_for_user

    def fail_during_trusted_device_revocation(db, target_user, **kwargs):
        original_revoke_trusted_devices(db, target_user, **kwargs)
        raise RuntimeError("simulated revocation failure")

    monkeypatch.setattr(auth_service, "revoke_trusted_devices_for_user", fail_during_trusted_device_revocation)
    with pytest.raises(RuntimeError, match="simulated revocation failure"):
        confirm_password_reset(db_session, raw_token=raw_token, new_password="ReplacementPassword123")

    db_session.rollback()
    db_session.expire_all()
    current_user = db_session.get(type(user), user.id)
    token = db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.user_id == user.id))
    session = db_session.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_session)))
    device = db_session.get(UserTrustedDevice, trusted_device.id)

    assert current_user.password_hash == old_password_hash
    assert verify_password("OriginalPassword123", current_user.password_hash)
    assert token.used_at is None
    assert session.status is SessionStatus.active
    assert device.revoked_at is None


@pytest.mark.parametrize("mutation", ["password", "email"])
def test_own_credential_change_invalidates_existing_reset_link(db_session, make_user, mutation):
    user = make_user(email=f"invalidate-{mutation}@example.com", password="OriginalPassword123", mfa_required=False, mfa_enabled=False)
    raw_token = issue_auth_email_token(db_session, user, purpose=AuthEmailTokenPurpose.password_reset)

    if mutation == "password":
        update_own_password(
            db_session,
            user,
            current_password="OriginalPassword123",
            new_password="ReplacementPassword123",
            confirm_password="ReplacementPassword123",
        )
    else:
        update_own_email(
            db_session,
            user,
            email=f"changed-{mutation}@example.com",
            current_password="OriginalPassword123",
        )

    with pytest.raises(AppError) as exc_info:
        confirm_password_reset(db_session, raw_token=raw_token, new_password="AnotherPassword123")

    assert exc_info.value.code == "token_used"


@pytest.mark.real_db_connections
def test_concurrent_password_reset_claim_has_one_winner(db_session, make_user):
    user = make_user(email="concurrent-reset@example.com", password="OriginalPassword123", mfa_required=False, mfa_enabled=False)
    raw_token = issue_auth_email_token(db_session, user, purpose=AuthEmailTokenPurpose.password_reset)
    user_id = user.id
    barrier = Barrier(2)

    def attempt(password):
        with harness.TestingSessionLocal() as independent_session:
            barrier.wait(timeout=10)
            try:
                confirm_password_reset(independent_session, raw_token=raw_token, new_password=password)
                return "success"
            except AppError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["ConcurrentPassword123", "OtherConcurrentPassword123"]))

    assert results.count("success") == 1
    assert results.count("token_used") == 1
    db_session.expire_all()
    user = db_session.get(type(user), user_id)
    assert verify_password("ConcurrentPassword123", user.password_hash) or verify_password("OtherConcurrentPassword123", user.password_hash)


@pytest.mark.real_db_connections
def test_login_waiting_on_password_reset_rechecks_new_password(db_session, make_user):
    user = make_user(email="reset-login-race@example.com", password="OriginalPassword123", mfa_required=False, mfa_enabled=False)
    raw_token = issue_auth_email_token(db_session, user, purpose=AuthEmailTokenPurpose.password_reset)
    user_id = user.id
    reset_locked_user = Event()
    allow_reset_to_finish = Event()
    login_started = Event()
    login_backend_pid = []

    def reset_password():
        with harness.TestingSessionLocal() as independent_session:
            target = independent_session.get(type(user), user_id)
            lock_user_credentials(independent_session, target)
            reset_locked_user.set()
            if not allow_reset_to_finish.wait(timeout=10):
                raise TimeoutError("login did not reach the reset lock")
            confirm_password_reset(independent_session, raw_token=raw_token, new_password="ReplacementPassword123")
            return "reset"

    def attempt_old_password():
        with harness.TestingSessionLocal() as independent_session:
            login_backend_pid.append(independent_session.scalar(text("SELECT pg_backend_pid()")))
            login_started.set()
            try:
                authenticate_user(independent_session, "reset-login-race@example.com", "OriginalPassword123", commit=False)
                return "login"
            except AppError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        reset_future = pool.submit(reset_password)
        assert reset_locked_user.wait(timeout=5)
        login_future = pool.submit(attempt_old_password)
        try:
            assert login_started.wait(timeout=5)
            deadline = time.monotonic() + 5
            login_is_waiting_on_lock = False
            while time.monotonic() < deadline:
                with harness.test_engine.connect() as observer:
                    wait_event = observer.execute(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                        {"pid": login_backend_pid[0]},
                    ).scalar_one_or_none()
                if wait_event == "Lock":
                    login_is_waiting_on_lock = True
                    break
                time.sleep(0.01)
        finally:
            allow_reset_to_finish.set()

        assert reset_future.result(timeout=15) == "reset"
        login_result = login_future.result(timeout=15)

    assert login_is_waiting_on_lock is True
    assert login_result == "unauthorized"
