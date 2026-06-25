import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.errors import AppError
from app.models import (
    AuthEmailToken,
    AuthEmailTokenPurpose,
    MfaMethodType,
    SecurityAuditEvent,
    SessionStatus,
    TeamRole,
    UserStatus,
    UserRecoveryMode,
    UserMfaMethod,
    UserOnboardingState,
    UserRecoveryCode,
    UserSession,
    UserTrustedDevice,
    utcnow,
)
from app.services.auth import create_session, session_token_hash, trusted_device_token_hash, verify_password
from app.services.security_audit import audit_subject_hash, request_ip


def _disable_mail(monkeypatch):
    monkeypatch.delenv("MAIL_TRANSPORT", raising=False)
    monkeypatch.delenv("APP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MAIL_FROM_ADDRESS", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)


def _enable_stdout_mail(monkeypatch):
    monkeypatch.setenv("MAIL_TRANSPORT", "stdout")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://openscribe.example.com")
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "no-reply@example.com")
    monkeypatch.setenv("MAIL_FROM_NAME", "OpenScribe")


def _extract_token_from_message(message) -> str:
    for word in message.text_body.split():
        if word.startswith("https://openscribe.example.com/"):
            parsed = urlparse(word)
            return parse_qs(parsed.query)["token"][0]
    raise AssertionError("message did not include token URL")


def test_password_reset_request_is_generic_and_uses_hashed_token(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    make_user(email="reset@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    existing = client.post("/api/v1/auth/password-reset/request", json={"email": "reset@example.com"})
    missing = client.post("/api/v1/auth/password-reset/request", json={"email": "missing@example.com"})

    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json() == missing.json()
    assert len(captured) == 1
    raw_token = _extract_token_from_message(captured[0])
    token_row = db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.purpose == AuthEmailTokenPurpose.password_reset))
    assert token_row is not None
    assert token_row.token_hash != raw_token


def test_password_reset_request_is_audited_without_raw_email(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: None)
    make_user(email="audit-reset@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = client.post("/api/v1/auth/password-reset/request", json={"email": "audit-reset@example.com"})

    assert response.status_code == 200
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "password_reset_requested"))
    assert event is not None
    assert event.details_json["category"] == "auth"
    assert event.details_json["outcome"] == "accepted"
    assert event.details_json["flow"] == "password_reset"
    assert event.details_json["subject_hash"] == audit_subject_hash("audit-reset@example.com")
    assert "audit-reset@example.com" not in str(event.details_json)


def test_auth_email_idempotency_key_does_not_include_raw_token(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    make_user(email="idempotency-reset@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = client.post("/api/v1/auth/password-reset/request", json={"email": "idempotency-reset@example.com"})

    assert response.status_code == 200
    assert len(captured) == 1
    raw_token = _extract_token_from_message(captured[0])
    token_row = db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.purpose == AuthEmailTokenPurpose.password_reset))
    assert token_row is not None
    assert captured[0].idempotency_key == f"auth-email-{token_row.id}"
    assert raw_token not in captured[0].idempotency_key
    assert raw_token[:12] not in captured[0].idempotency_key


def test_password_reset_request_disabled_tells_user_to_contact_admin(client, db_session, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    make_user(email="reset-disabled@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    response = client.post("/api/v1/auth/password-reset/request", json={"email": "reset-disabled@example.com"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_transport_disabled"
    assert "Contact your team leader or system administrator" in response.json()["error"]["message"]
    assert db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.purpose == AuthEmailTokenPurpose.password_reset)) is None


def test_password_reset_request_misconfigured_mail_is_not_enumerable(client, db_session, make_user, monkeypatch):
    monkeypatch.setenv("MAIL_TRANSPORT", "stdout")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("APP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "no-reply@example.com")
    make_user(email="reset-misconfigured@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    existing = client.post("/api/v1/auth/password-reset/request", json={"email": "reset-misconfigured@example.com"})
    missing = client.post("/api/v1/auth/password-reset/request", json={"email": "missing-misconfigured@example.com"})

    assert existing.status_code == 503
    assert missing.status_code == 503
    assert existing.json() == missing.json()
    assert db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.purpose == AuthEmailTokenPurpose.password_reset)) is None


def test_password_reset_request_send_failure_is_not_enumerable(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    make_user(email="reset-send-failure@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    def fail_send(*args, **kwargs):
        raise AppError(502, "mail_resend_unavailable", "Resend mail service is unavailable")

    monkeypatch.setattr("app.services.auth_email.send_transactional_email", fail_send)

    existing = client.post("/api/v1/auth/password-reset/request", json={"email": "reset-send-failure@example.com"})
    missing = client.post("/api/v1/auth/password-reset/request", json={"email": "missing-send-failure@example.com"})

    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json() == missing.json()


def test_password_reset_browser_hides_email_flow_when_mail_disabled(client, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    make_user(email="browser-reset-disabled@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    login_page = client.get("/login")
    forgot_page = client.get("/forgot-password")

    assert login_page.status_code == 200
    assert "/forgot-password" not in login_page.text
    assert "contact your team leader or system administrator" in login_page.text
    assert forgot_page.status_code == 200
    assert "Send reset link" not in forgot_page.text
    assert "Email password reset is not enabled" in forgot_page.text
    assert "Account recovery" in forgot_page.text
    assert "panel hero" in forgot_page.text


def test_password_reset_browser_pages_use_current_auth_shell(client, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    make_user(email="browser-reset-shell@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    request_page = client.get("/forgot-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": "browser-reset-shell@example.com"})
    confirm_page = client.get(f"/reset-password?token={_extract_token_from_message(captured[0])}")

    assert request_page.status_code == 200
    assert "DM Sans" in request_page.text
    assert "panel hero" in request_page.text
    assert "Send reset link" in request_page.text
    assert confirm_page.status_code == 200
    assert "Secure account link" in confirm_page.text
    assert "panel hero" in confirm_page.text
    assert "Return to login" in confirm_page.text


def test_password_reset_confirm_changes_password_and_revokes_sessions(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    user = make_user(email="reset-confirm@example.com", password="password-1", mfa_required=False, mfa_enabled=False)
    old_session = create_session(db_session, user)
    trusted_device = UserTrustedDevice(
        user_id=user.id,
        device_token_hash=trusted_device_token_hash("device-token"),
        expires_at=utcnow(),
        last_mfa_verified_at=utcnow(),
    )
    db_session.add(trusted_device)
    db_session.commit()
    client.post("/api/v1/auth/password-reset/request", json={"email": "reset-confirm@example.com"})
    raw_token = _extract_token_from_message(captured[0])

    confirmed = client.post("/api/v1/auth/password-reset/confirm", json={"token": raw_token, "new_password": "NewPassword123"})

    db_session.refresh(user)
    assert confirmed.status_code == 200
    assert verify_password("NewPassword123", user.password_hash)
    assert db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.user_id == user.id)).used_at is not None
    assert db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.user_id == user.id)).token_hash != raw_token
    old_session_row = db_session.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_session)))
    assert old_session_row.status is SessionStatus.revoked
    db_session.refresh(trusted_device)
    assert trusted_device.revoked_at is not None


def test_password_reset_confirm_rejects_invalid_token_before_hashing(client, monkeypatch):
    def fail_hash(_password):
        raise AssertionError("hash_password should not run for invalid reset token")

    monkeypatch.setattr("app.services.auth_email.hash_password", fail_hash)

    invalid_token = "invalid-token-0000"

    api_response = client.post("/api/v1/auth/password-reset/confirm", json={"token": invalid_token, "new_password": "NewPassword123"})
    browser_response = client.post("/reset-password", data={"token": invalid_token, "new_password": "NewPassword123"})

    assert api_response.status_code == 422
    assert api_response.json()["error"]["code"] == "token_invalid"
    assert browser_response.status_code == 422
    assert "Reset or setup link is invalid" in browser_response.text


def test_user_chosen_passwords_require_complexity(client):
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "invalid-token-0000", "new_password": "lowercase-123"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"] == {"issue_count": 1}


def test_account_activation_sets_password_and_creates_onboarding_session(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    user = make_user(
        email="activate@example.com",
        password="temporary-1",
        onboarding_state=UserOnboardingState.pending_password_change,
        must_change_password=True,
        mfa_enabled=False,
    )
    from app.services.auth_email import send_account_activation_email

    send_account_activation_email(db_session, user)
    raw_token = _extract_token_from_message(captured[0])

    activated = client.post("/api/v1/auth/account-activation/confirm", json={"token": raw_token, "new_password": "NewPassword123"})

    db_session.refresh(user)
    assert activated.status_code == 200
    assert activated.json()["redirect_to"] == "/onboarding"
    assert client.cookies.get("openscribe_session")
    assert verify_password("NewPassword123", user.password_hash)
    assert user.must_change_password is False
    assert user.onboarding_state is UserOnboardingState.pending_totp_enrollment


def test_account_activation_is_restricted_to_first_password_setup(client, db_session, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    user = make_user(
        email="already-active@example.com",
        password="password-1",
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=True,
        mfa_enabled=True,
    )
    mfa_method = UserMfaMethod(user_id=user.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    db_session.add(mfa_method)
    db_session.commit()
    from app.services.auth_email import issue_auth_email_token, send_account_activation_email

    with pytest.raises(AppError) as exc:
        send_account_activation_email(db_session, user)

    raw_token = issue_auth_email_token(db_session, user, purpose=AuthEmailTokenPurpose.account_activation)

    def fail_hash(_password):
        raise AssertionError("hash_password should not run for completed activation token")

    monkeypatch.setattr("app.services.auth_email.hash_password", fail_hash)

    page = client.get(f"/activate-account?token={raw_token}")
    response = client.post("/api/v1/auth/account-activation/confirm", json={"token": raw_token, "new_password": "NewPassword123"})

    db_session.refresh(user)
    assert exc.value.status_code == 409
    assert captured == []
    assert page.status_code == 422
    assert "Setup link is invalid or expired." in page.text
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "activation_not_pending"
    assert verify_password("password-1", user.password_hash)
    assert user.onboarding_state is UserOnboardingState.complete
    assert user.mfa_enabled is True
    assert db_session.get(UserMfaMethod, mfa_method.id) is not None


def test_leader_cannot_recover_cross_team_user(client, make_team, make_user):
    north = make_team(name="Recovery North")
    south = make_team(name="Recovery South")
    make_user(email="leader-recovery@example.com", password="password-1", team=north, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(email="south-user@example.com", password="password-1", team=south, mfa_required=False, mfa_enabled=False)
    client.post("/api/v1/auth/login", json={"email": "leader-recovery@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/send-password-reset", json={"reason": "support request"})

    assert response.status_code == 403


def test_legacy_manager_recovery_endpoint_is_closed(client, make_team, make_user):
    team = make_team(name="Deprecated Recovery Team")
    make_user(email="leader-deprecated@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(email="target-deprecated@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    client.post("/api/v1/auth/login", json={"email": "leader-deprecated@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/recover-password")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "deprecated_recovery_endpoint"


def test_manager_email_recovery_sends_token_and_audits(client, db_session, make_team, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    team = make_team(name="Email Manager Recovery Team")
    leader = make_user(email="leader-email-recovery@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(email="target-email-recovery@example.com", password="old-password-1", team=team, mfa_required=True, mfa_enabled=True)
    client.post("/api/v1/auth/login", json={"email": "leader-email-recovery@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/send-password-reset", json={"reason": "user called support"})

    assert response.status_code == 200
    assert len(captured) == 1
    token_row = db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.user_id == target.id, AuthEmailToken.purpose == AuthEmailTokenPurpose.manager_password_reset))
    assert token_row is not None
    assert token_row.created_by_user_id == leader.id
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "manager_password_reset_email_sent"))
    assert event is not None
    assert event.actor_user_id == leader.id
    assert event.target_user_id == target.id
    assert event.details_json["reason"] == "user called support"


def test_manager_email_recovery_blocks_inactive_targets(client, db_session, make_team, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    captured = []
    monkeypatch.setattr("app.services.auth_email.send_transactional_email", lambda message: captured.append(message))
    team = make_team(name="Inactive Email Recovery Team")
    make_user(email="leader-inactive-recovery@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(
        email="target-inactive-recovery@example.com",
        password="old-password-1",
        team=team,
        status=UserStatus.suspended,
        mfa_required=False,
        mfa_enabled=False,
    )
    client.post("/api/v1/auth/login", json={"email": "leader-inactive-recovery@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/send-password-reset", json={"reason": "support request"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert captured == []
    token_row = db_session.scalar(select(AuthEmailToken).where(AuthEmailToken.user_id == target.id))
    assert token_row is None


def test_break_glass_recover_password_generates_temp_password_and_preserves_mfa(client, db_session, make_team, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    team = make_team(name="Temp Password Team")
    leader = make_user(email="leader-temp@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=True)
    leader_mfa = UserMfaMethod(user_id=leader.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    target = make_user(email="target-temp@example.com", password="old-password-1", team=team, mfa_required=True, mfa_enabled=True)
    old_session = create_session(db_session, target)
    trusted_device = UserTrustedDevice(
        user_id=target.id,
        device_token_hash=trusted_device_token_hash("target-device-token"),
        expires_at=utcnow(),
        last_mfa_verified_at=utcnow(),
    )
    mfa_method = UserMfaMethod(user_id=target.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    recovery_code = UserRecoveryCode(user_id=target.id, code_hash="stored-password-only-recovery-code-hash")
    token = AuthEmailToken(
        user_id=target.id,
        purpose=AuthEmailTokenPurpose.manager_password_reset,
        token_hash="existing-reset-token-hash",
        expires_at=utcnow(),
    )
    db_session.add_all([leader_mfa, trusted_device, mfa_method, recovery_code, token])
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": "leader-temp@example.com", "password": "password-1"})
    mfa_code = pyotp.TOTP(leader_mfa.secret).now()

    response = client.post(
        f"/api/v1/users/{target.id}/break-glass-password-reset",
        json={"mfa_code": mfa_code, "reason": "email outage", "confirm_email_unavailable": True},
    )

    db_session.refresh(target)
    db_session.refresh(trusted_device)
    db_session.refresh(token)
    old_session_row = db_session.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_session)))
    temporary_password = response.json()["temporary_password"]
    assert response.status_code == 200
    assert len(temporary_password) >= 24
    assert response.json()["message"].startswith("Break-glass temporary password generated")
    assert response.json()["recovery_mode"] == "break_glass_password_reset"
    assert verify_password(temporary_password, target.password_hash)
    assert not verify_password("old-password-1", target.password_hash)
    assert target.must_change_password is True
    assert target.onboarding_state is UserOnboardingState.pending_password_change
    assert target.recovery_mode is UserRecoveryMode.break_glass_password_reset
    assert target.recovery_started_by_user_id == leader.id
    assert target.temporary_password_expires_at is not None
    assert target.mfa_enabled is True
    assert db_session.get(UserMfaMethod, mfa_method.id) is not None
    assert db_session.get(UserRecoveryCode, recovery_code.id) is not None
    assert old_session_row.status is SessionStatus.revoked
    assert trusted_device.revoked_at is not None
    assert token.used_at is not None
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "break_glass_password_reset_generated"))
    assert event is not None
    assert event.details_json["reason"] == "email outage"
    assert "mfa_code" not in event.details_json

    login = client.post("/api/v1/auth/login", json={"email": "target-temp@example.com", "password": temporary_password})

    assert login.status_code == 200
    assert login.json()["redirect_to"] == "/onboarding"

    changed = client.post("/api/v1/onboarding/password", json={"new_password": "NewPassword123"})

    db_session.refresh(target)
    assert changed.status_code == 200
    assert changed.json()["onboarding_state"] == "complete"
    assert target.onboarding_state is UserOnboardingState.complete
    assert target.recovery_mode is None
    assert target.temporary_password_expires_at is None
    assert target.mfa_enabled is True
    assert db_session.get(UserMfaMethod, mfa_method.id) is not None
    assert db_session.get(UserRecoveryCode, recovery_code.id) is not None


def test_break_glass_recover_account_generates_temp_password_and_clears_mfa(client, db_session, make_team, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    team = make_team(name="Full Recovery Team")
    leader = make_user(email="leader-full-recovery@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=True)
    leader_mfa = UserMfaMethod(user_id=leader.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    target = make_user(email="target-full-recovery@example.com", password="old-password-1", team=team, mfa_required=True, mfa_enabled=True)
    mfa_method = UserMfaMethod(user_id=target.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    recovery_code = UserRecoveryCode(user_id=target.id, code_hash="stored-recovery-code-hash")
    db_session.add_all([leader_mfa, mfa_method, recovery_code])
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": "leader-full-recovery@example.com", "password": "password-1"})
    mfa_code = pyotp.TOTP(leader_mfa.secret).now()

    response = client.post(
        f"/api/v1/users/{target.id}/break-glass-account-recovery",
        json={"mfa_code": mfa_code, "reason": "lost device and mail off", "confirm_email_unavailable": True},
    )

    db_session.refresh(target)
    temporary_password = response.json()["temporary_password"]
    assert response.status_code == 200
    assert response.json()["message"].startswith("Break-glass temporary password generated and MFA reset")
    assert verify_password(temporary_password, target.password_hash)
    assert target.must_change_password is True
    assert target.onboarding_state is UserOnboardingState.pending_password_change
    assert target.recovery_mode is UserRecoveryMode.break_glass_account_recovery
    assert target.mfa_enabled is False
    assert db_session.scalars(select(UserMfaMethod).where(UserMfaMethod.user_id == target.id)).first() is None
    assert db_session.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == target.id)).first() is None


def test_break_glass_requires_manager_totp_and_confirmation(client, make_team, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    team = make_team(name="Break Glass MFA Gate Team")
    make_user(email="leader-no-mfa@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(email="target-no-mfa@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    client.post("/api/v1/auth/login", json={"email": "leader-no-mfa@example.com", "password": "password-1"})

    missing_confirmation = client.post(
        f"/api/v1/users/{target.id}/break-glass-password-reset",
        json={"mfa_code": "123456", "reason": "email outage", "confirm_email_unavailable": False},
    )
    no_totp = client.post(
        f"/api/v1/users/{target.id}/break-glass-password-reset",
        json={"mfa_code": "123456", "reason": "email outage", "confirm_email_unavailable": True},
    )

    assert missing_confirmation.status_code == 422
    assert missing_confirmation.json()["error"]["code"] == "confirmation_required"
    assert no_totp.status_code == 403
    assert no_totp.json()["error"]["code"] == "fresh_mfa_required"


def test_break_glass_totp_attempts_are_rate_limited(client, db_session, make_team, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    team = make_team(name="Break Glass Rate Limit Team")
    leader = make_user(email="leader-break-glass-rate@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=True)
    leader_mfa = UserMfaMethod(user_id=leader.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    target = make_user(email="target-break-glass-rate@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    db_session.add(leader_mfa)
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": "leader-break-glass-rate@example.com", "password": "password-1"})

    for _ in range(10):
        response = client.post(
            f"/api/v1/users/{target.id}/break-glass-password-reset",
            json={"mfa_code": "000000", "reason": "email outage", "confirm_email_unavailable": True},
        )
        assert response.status_code == 422

    limited = client.post(
        f"/api/v1/users/{target.id}/break-glass-password-reset",
        json={"mfa_code": pyotp.TOTP(leader_mfa.secret).now(), "reason": "email outage", "confirm_email_unavailable": True},
    )

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


def test_security_audit_request_ip_ignores_forwarded_header_by_default(monkeypatch):
    monkeypatch.delenv("AUDIT_TRUST_X_FORWARDED_FOR", raising=False)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/audit-test",
            "headers": [(b"x-forwarded-for", b"198.51.100.10, 198.51.100.11")],
            "client": ("203.0.113.20", 54321),
        }
    )

    assert request_ip(request) == "203.0.113.20"


def test_security_audit_request_ip_can_trust_forwarded_header(monkeypatch):
    monkeypatch.setenv("AUDIT_TRUST_X_FORWARDED_FOR", "true")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/audit-test",
            "headers": [(b"x-forwarded-for", b"198.51.100.10, 198.51.100.11")],
            "client": ("203.0.113.20", 54321),
        }
    )

    assert request_ip(request) == "198.51.100.10"


def test_temporary_recovery_password_expiry_blocks_login(client, db_session, make_team, make_user):
    team = make_team(name="Expired Recovery Team")
    target = make_user(
        email="target-expired-recovery@example.com",
        password="temporary-password-1",
        team=team,
        must_change_password=True,
        onboarding_state=UserOnboardingState.pending_password_change,
    )
    target.temporary_password_expires_at = utcnow() - timedelta(minutes=1)
    target.recovery_mode = UserRecoveryMode.break_glass_password_reset
    db_session.add(target)
    db_session.commit()

    login = client.post("/api/v1/auth/login", json={"email": "target-expired-recovery@example.com", "password": "temporary-password-1"})

    assert login.status_code == 403
    assert login.json()["error"]["code"] == "temporary_password_expired"


def test_manager_reset_mfa_preserves_pending_password_change(client, db_session, make_team, make_user):
    team = make_team(name="MFA Reset Pending Password Team")
    make_user(email="leader-mfa-reset@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(
        email="target-mfa-reset@example.com",
        password="temporary-password-1",
        team=team,
        mfa_required=True,
        mfa_enabled=True,
        onboarding_state=UserOnboardingState.pending_password_change,
        must_change_password=True,
    )
    old_session = create_session(db_session, target)
    trusted_device = UserTrustedDevice(
        user_id=target.id,
        device_token_hash=trusted_device_token_hash("mfa-reset-device-token"),
        expires_at=utcnow(),
        last_mfa_verified_at=utcnow(),
    )
    mfa_method = UserMfaMethod(user_id=target.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    recovery_code = UserRecoveryCode(user_id=target.id, code_hash="stored-mfa-reset-recovery-code-hash")
    db_session.add_all([trusted_device, mfa_method, recovery_code])
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": "leader-mfa-reset@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/reset-mfa")

    db_session.refresh(target)
    db_session.refresh(trusted_device)
    old_session_row = db_session.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_session)))
    assert response.status_code == 200
    assert response.json()["onboarding_state"] == "pending_password_change"
    assert target.must_change_password is True
    assert target.onboarding_state is UserOnboardingState.pending_password_change
    assert target.mfa_enabled is False
    assert db_session.scalars(select(UserMfaMethod).where(UserMfaMethod.user_id == target.id)).first() is None
    assert db_session.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == target.id)).first() is None
    assert old_session_row.status is SessionStatus.revoked
    assert trusted_device.revoked_at is not None


def test_browser_break_glass_recovery_shows_temp_password_modal_not_toast(client, db_session, make_team, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    team = make_team(name="Recovery Modal Team")
    leader = make_user(email="leader-modal@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=True)
    leader_mfa = UserMfaMethod(user_id=leader.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    db_session.add(leader_mfa)
    db_session.commit()
    target = make_user(email="target-modal@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    client.post("/login", data={"email": "leader-modal@example.com", "password": "password-1"}, follow_redirects=False)

    response = client.post(
        f"/home/users/{target.id}/break-glass-password-reset",
        data={"return_tab": "team-management", "reason": "email outage", "mfa_code": pyotp.TOTP(leader_mfa.secret).now(), "confirm_email_unavailable": "true"},
    )

    match = re.search(r'<input[^>]+value="([^"]+)"[^>]+data-recovery-temp-password', response.text)
    assert response.status_code == 200
    assert 'data-recovery-modal' in response.text
    assert 'data-recovery-copy' in response.text
    assert 'Copy password' in response.text
    assert match is not None
    assert len(match.group(1)) >= 24
    assert f'<div class="panel flash success">Break-glass temporary password generated. It is shown once.</div>' in response.text


def test_browser_temporary_recovery_password_login_is_audited(client, db_session, make_team, make_user, monkeypatch):
    _disable_mail(monkeypatch)
    team = make_team(name="Browser Recovery Login Audit Team")
    leader = make_user(email="leader-browser-audit@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=True)
    leader_mfa = UserMfaMethod(user_id=leader.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    db_session.add(leader_mfa)
    db_session.commit()
    target = make_user(email="target-browser-audit@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    client.post("/login", data={"email": "leader-browser-audit@example.com", "password": "password-1"}, follow_redirects=False)
    recovery = client.post(
        f"/home/users/{target.id}/break-glass-password-reset",
        data={"return_tab": "team-management", "reason": "email outage", "mfa_code": pyotp.TOTP(leader_mfa.secret).now(), "confirm_email_unavailable": "true"},
    )
    temporary_password = re.search(r'<input[^>]+value="([^"]+)"[^>]+data-recovery-temp-password', recovery.text).group(1)
    client.cookies.clear()

    login = client.post("/login", data={"email": "target-browser-audit@example.com", "password": temporary_password}, follow_redirects=False)

    assert login.status_code == 303
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "temporary_recovery_password_login", SecurityAuditEvent.target_user_id == target.id))
    assert event is not None
    assert event.details_json["recovery_mode"] == "break_glass_password_reset"


def test_browser_recovery_menu_shows_break_glass_when_mail_coexistence_allowed(client, make_team, make_user, monkeypatch):
    _enable_stdout_mail(monkeypatch)
    monkeypatch.setenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "true")
    team = make_team(name="Recovery Coexistence Team")
    make_user(email="leader-coexist@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    make_user(email="target-coexist@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    client.post("/login", data={"email": "leader-coexist@example.com", "password": "password-1"}, follow_redirects=False)

    response = client.get("/home?tab=team-management")

    assert response.status_code == 200
    assert "/home/users/" in response.text
    assert "send-password-reset" in response.text
    assert "break-glass-password-reset" in response.text
