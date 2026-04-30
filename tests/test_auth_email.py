import re
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AuthEmailToken,
    AuthEmailTokenPurpose,
    MfaMethodType,
    SessionStatus,
    TeamRole,
    UserMfaMethod,
    UserOnboardingState,
    UserRecoveryCode,
    UserSession,
    UserTrustedDevice,
    utcnow,
)
from app.services.auth import create_session, session_token_hash, trusted_device_token_hash, verify_password


def _disable_mail(monkeypatch):
    monkeypatch.delenv("MAIL_TRANSPORT", raising=False)
    monkeypatch.delenv("APP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MAIL_FROM_ADDRESS", raising=False)


def _enable_stdout_mail(monkeypatch):
    monkeypatch.setenv("MAIL_TRANSPORT", "stdout")
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

    confirmed = client.post("/api/v1/auth/password-reset/confirm", json={"token": raw_token, "new_password": "new-password-1"})

    db_session.refresh(user)
    assert confirmed.status_code == 200
    assert verify_password("new-password-1", user.password_hash)
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

    api_response = client.post("/api/v1/auth/password-reset/confirm", json={"token": invalid_token, "new_password": "new-password-1"})
    browser_response = client.post("/reset-password", data={"token": invalid_token, "new_password": "new-password-1"})

    assert api_response.status_code == 422
    assert api_response.json()["error"]["code"] == "token_invalid"
    assert browser_response.status_code == 422
    assert "Reset or setup link is invalid" in browser_response.text


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

    activated = client.post("/api/v1/auth/account-activation/confirm", json={"token": raw_token, "new_password": "new-password-1"})

    db_session.refresh(user)
    assert activated.status_code == 200
    assert activated.json()["redirect_to"] == "/onboarding"
    assert client.cookies.get("openscribe_session")
    assert verify_password("new-password-1", user.password_hash)
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
    response = client.post("/api/v1/auth/account-activation/confirm", json={"token": raw_token, "new_password": "new-password-1"})

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

    response = client.post(f"/api/v1/users/{target.id}/recover-password")

    assert response.status_code == 403


def test_manager_recover_password_generates_temp_password_and_preserves_mfa(client, db_session, make_team, make_user):
    team = make_team(name="Temp Password Team")
    make_user(email="leader-temp@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
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
    db_session.add_all([trusted_device, mfa_method, recovery_code, token])
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": "leader-temp@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/recover-password")

    db_session.refresh(target)
    db_session.refresh(trusted_device)
    db_session.refresh(token)
    old_session_row = db_session.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_session)))
    temporary_password = response.json()["temporary_password"]
    assert response.status_code == 200
    assert len(temporary_password) >= 24
    assert response.json()["message"].startswith("Temporary password generated")
    assert verify_password(temporary_password, target.password_hash)
    assert not verify_password("old-password-1", target.password_hash)
    assert target.must_change_password is True
    assert target.onboarding_state is UserOnboardingState.pending_password_change
    assert target.mfa_enabled is True
    assert db_session.get(UserMfaMethod, mfa_method.id) is not None
    assert db_session.get(UserRecoveryCode, recovery_code.id) is not None
    assert old_session_row.status is SessionStatus.revoked
    assert trusted_device.revoked_at is not None
    assert token.used_at is not None

    login = client.post("/api/v1/auth/login", json={"email": "target-temp@example.com", "password": temporary_password})

    assert login.status_code == 200
    assert login.json()["redirect_to"] == "/onboarding"

    changed = client.post("/api/v1/onboarding/password", json={"new_password": "new-password-1"})

    db_session.refresh(target)
    assert changed.status_code == 200
    assert changed.json()["onboarding_state"] == "complete"
    assert target.onboarding_state is UserOnboardingState.complete
    assert target.mfa_enabled is True
    assert db_session.get(UserMfaMethod, mfa_method.id) is not None
    assert db_session.get(UserRecoveryCode, recovery_code.id) is not None


def test_manager_recover_account_generates_temp_password_and_clears_mfa(client, db_session, make_team, make_user):
    team = make_team(name="Full Recovery Team")
    make_user(email="leader-full-recovery@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(email="target-full-recovery@example.com", password="old-password-1", team=team, mfa_required=True, mfa_enabled=True)
    mfa_method = UserMfaMethod(user_id=target.id, method_type=MfaMethodType.totp, secret="JBSWY3DPEHPK3PXP", is_primary=True, is_active=True, verified_at=utcnow())
    recovery_code = UserRecoveryCode(user_id=target.id, code_hash="stored-recovery-code-hash")
    db_session.add_all([mfa_method, recovery_code])
    db_session.commit()
    client.post("/api/v1/auth/login", json={"email": "leader-full-recovery@example.com", "password": "password-1"})

    response = client.post(f"/api/v1/users/{target.id}/recover-account")

    db_session.refresh(target)
    temporary_password = response.json()["temporary_password"]
    assert response.status_code == 200
    assert response.json()["message"].startswith("Temporary password generated and MFA reset")
    assert verify_password(temporary_password, target.password_hash)
    assert target.must_change_password is True
    assert target.onboarding_state is UserOnboardingState.pending_password_change
    assert target.mfa_enabled is False
    assert db_session.scalars(select(UserMfaMethod).where(UserMfaMethod.user_id == target.id)).first() is None
    assert db_session.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == target.id)).first() is None


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


def test_browser_manager_recovery_shows_temp_password_modal_not_toast(client, make_team, make_user):
    team = make_team(name="Recovery Modal Team")
    make_user(email="leader-modal@example.com", password="password-1", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    target = make_user(email="target-modal@example.com", password="old-password-1", team=team, mfa_required=False, mfa_enabled=False)
    client.post("/login", data={"email": "leader-modal@example.com", "password": "password-1"}, follow_redirects=False)

    response = client.post(
        f"/home/users/{target.id}/recover-password",
        data={"return_tab": "team-management"},
    )

    match = re.search(r'<input[^>]+value="([^"]+)"[^>]+data-recovery-temp-password', response.text)
    assert response.status_code == 200
    assert 'data-recovery-modal' in response.text
    assert 'data-recovery-copy' in response.text
    assert 'Copy password' in response.text
    assert match is not None
    assert len(match.group(1)) >= 24
    assert f'<div class="panel flash success">Temporary password generated.</div>' in response.text
