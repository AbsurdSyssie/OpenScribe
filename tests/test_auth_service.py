import hashlib

import pyotp
import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import SessionStatus, TeamRole, User, UserEncryptionKey, UserOnboardingState, UserSession, UserTrustedDevice, utcnow
from app.services.admin import hash_password
from app.services.admin import create_bootstrap_admin, create_user as create_user_service
from app.services.auth import (
    create_session,
    recovery_code_hash,
    session_token_hash,
    start_totp_enrollment,
    totp_secret_for_method,
    trusted_device_token_hash,
    verify_active_totp_for_user,
    verify_login_totp,
    verify_password,
)
from app.services.content_crypto import DEK_CACHE_SESSION_KEY, is_encrypted_envelope
from app.services.content_crypto import ensure_user_dek
from app.schemas.users import UserCreate
from app.services.passwords import password_needs_rehash
from scripts.force_argon2id_password_rotation import rotate_non_argon2id_passwords


def test_hash_password_uses_argon2id_and_verifies():
    password_hash = hash_password("password-1")

    assert password_hash.startswith("$argon2id$")
    assert verify_password("password-1", password_hash) is True
    assert password_needs_rehash(password_hash) is False


def test_verify_password_rejects_wrong_password_and_malformed_hash():
    password_hash = hash_password("password-1")

    assert verify_password("password-2", password_hash) is False
    assert verify_password("password-1", "not-a-valid-hash") is False
    assert verify_password("password-1", "$2b$legacy-bcrypt-hash") is False


def test_force_argon2id_rotation_sets_temp_password_and_revokes_sessions(db_session, make_team, make_user):
    team = make_team(name="Password Upgrade Team")
    user = make_user(
        email="non-argon2id-password@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    user.password_hash = "$2b$non-argon2id-hash"
    old_session = create_session(db_session, user)
    trusted_device = UserTrustedDevice(
        user_id=user.id,
        device_token_hash=trusted_device_token_hash("non-argon2id-device-token"),
        expires_at=utcnow(),
        last_mfa_verified_at=utcnow(),
    )
    db_session.add_all([user, trusted_device])
    db_session.commit()

    rotated = rotate_non_argon2id_passwords(db_session)

    db_session.refresh(user)
    db_session.refresh(trusted_device)
    old_session_row = db_session.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_session)))
    assert len(rotated) == 1
    assert rotated[0].email == "non-argon2id-password@example.com"
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(rotated[0].temporary_password, user.password_hash) is True
    assert user.must_change_password is True
    assert user.onboarding_state is UserOnboardingState.pending_password_change
    assert old_session_row.status is SessionStatus.revoked
    assert trusted_device.revoked_at is not None


def test_session_and_recovery_hashes_are_deterministic_and_not_plaintext():
    assert session_token_hash("token-1") == session_token_hash("token-1")
    assert recovery_code_hash("code-1") == recovery_code_hash("code-1")
    assert session_token_hash("token-1") != "token-1"
    assert recovery_code_hash("code-1") != "code-1"


def test_legacy_plaintext_totp_remains_usable_without_creating_or_mutating_a_dek(
    db_session, make_user, make_totp_method,
):
    user = make_user(email="legacy-totp@example.com")
    method, plaintext_secret = make_totp_method(user=user, encrypted=False, verified_at=utcnow())
    original_secret_digest = hashlib.sha256(method.secret.encode("utf-8")).hexdigest()

    verify_active_totp_for_user(db_session, user, code=pyotp.TOTP(plaintext_secret).now())

    db_session.refresh(method)
    assert not is_encrypted_envelope(method.secret)
    assert hashlib.sha256(method.secret.encode("utf-8")).hexdigest() == original_secret_digest
    assert db_session.scalars(select(UserEncryptionKey).where(UserEncryptionKey.user_id == user.id)).all() == []


@pytest.mark.parametrize(
    "stored_secret",
    [
        "",
        "NOT-BASE32-0",
        "A" * 129,
        "   ",
        "{",
        '{"v":1}',
        '{"alg":"AES-256-GCM","ct":"","dkv":"bad","n":"","v":1}',
    ],
)
def test_malformed_or_json_like_totp_values_are_controlled_unreadable_errors(
    db_session, make_user, make_totp_method, stored_secret,
):
    user = make_user(email="malformed-totp@example.com")
    method, _ = make_totp_method(user=user, encrypted=False)
    method.secret = stored_secret
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        totp_secret_for_method(db_session, user=user, method=method)

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "mfa_secret_unreadable"


def test_enrollment_crypto_failure_rolls_back_and_preserves_existing_method(
    db_session, make_user, make_totp_method, monkeypatch,
):
    user = make_user(
        email="totp-reenrollment-rollback@example.com",
        onboarding_state=UserOnboardingState.pending_totp_enrollment,
        mfa_enabled=False,
    )
    existing_method, existing_plaintext = make_totp_method(user=user, encrypted=False, is_active=False)
    ensure_user_dek(db_session, user=user)
    db_session.commit()
    db_session.info.pop(DEK_CACHE_SESSION_KEY, None)

    def fail_unwrap(**_kwargs):
        raise AppError(502, "vault_read_failed", "Vault data key unwrap failed")

    monkeypatch.setattr("app.services.content_crypto.unwrap_user_content_data_key", fail_unwrap)

    with pytest.raises(AppError) as exc_info:
        start_totp_enrollment(db_session, user)

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "mfa_service_unavailable"
    preserved = db_session.get(type(existing_method), existing_method.id)
    assert preserved is not None
    assert preserved.secret == existing_plaintext


def test_reenrollment_replaces_existing_method_with_encrypted_method(
    db_session, make_user, make_totp_method,
):
    user = make_user(
        email="totp-reenrollment@example.com",
        onboarding_state=UserOnboardingState.pending_totp_enrollment,
        mfa_enabled=False,
    )
    existing_method, _ = make_totp_method(user=user, encrypted=False, is_active=False)

    replacement = start_totp_enrollment(db_session, user)

    assert db_session.get(type(existing_method), existing_method.id) is None
    assert is_encrypted_envelope(replacement.secret)
    assert totp_secret_for_method(db_session, user=user, method=replacement) != replacement.secret


def test_encrypted_totp_rejects_wrong_owner_and_wrong_record_aad(db_session, make_team, make_user, make_totp_method):
    team = make_team(name="TOTP AAD Team")
    owner = make_user(email="totp-owner@example.com", team=team)
    other_user = make_user(email="totp-other@example.com", team=team)
    owner_method, _ = make_totp_method(user=owner)
    wrong_record_method, _ = make_totp_method(user=owner, is_primary=False)

    owner_method.user_id = other_user.id
    wrong_record_method.secret = owner_method.secret
    db_session.commit()

    for user, method in ((other_user, owner_method), (owner, wrong_record_method)):
        with pytest.raises(AppError) as exc_info:
            totp_secret_for_method(db_session, user=user, method=method)
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "mfa_secret_unreadable"


def test_vault_unwrap_failure_is_controlled_and_cannot_create_trusted_device(
    db_session, make_user, make_totp_method, monkeypatch,
):
    user = make_user(email="vault-totp@example.com")
    _, plaintext_secret = make_totp_method(user=user, verified_at=utcnow())
    db_session.info.pop(DEK_CACHE_SESSION_KEY, None)

    def fail_unwrap(**_kwargs):
        raise AppError(502, "vault_read_failed", "Vault data key unwrap failed")

    monkeypatch.setattr("app.services.content_crypto.unwrap_user_content_data_key", fail_unwrap)

    with pytest.raises(AppError) as exc_info:
        verify_login_totp(
            db_session,
            user,
            code=pyotp.TOTP(plaintext_secret).now(),
            remember_device=True,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "mfa_service_unavailable"
    assert db_session.scalars(select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id)).all() == []


def test_create_user_provisions_one_dek_idempotently_and_rolls_back_on_key_failure(
    db_session, make_team, monkeypatch,
):
    team = make_team(name="DEK Provisioning Team")
    created = create_user_service(
        db_session,
        UserCreate(
            email="dek-provisioned@example.com",
            temporary_password="TempPass1",
            team_id=team.id,
            team_role=TeamRole.user,
        ),
    )
    first_key = ensure_user_dek(db_session, user=created)
    second_key = ensure_user_dek(db_session, user=created)

    assert first_key.id == second_key.id
    assert db_session.scalar(select(UserEncryptionKey).where(UserEncryptionKey.user_id == created.id)) is not None

    monkeypatch.setattr(
        "app.services.admin.ensure_user_dek",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppError(502, "vault_read_failed", "Vault unavailable")),
    )
    with pytest.raises(AppError) as exc_info:
        create_user_service(
            db_session,
            UserCreate(
                email="dek-rollback@example.com",
                temporary_password="TempPass1",
                team_id=team.id,
                team_role=TeamRole.user,
            ),
        )

    assert exc_info.value.code == "vault_read_failed"
    assert db_session.scalar(select(UserEncryptionKey).where(UserEncryptionKey.user_id == created.id)) is not None
    assert db_session.scalar(select(User).where(User.email == "dek-rollback@example.com")) is None


def test_bootstrap_admin_rolls_back_when_dek_provisioning_fails(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.admin.ensure_user_dek",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppError(502, "vault_write_failed", "Vault unavailable")),
    )

    with pytest.raises(AppError) as exc_info:
        create_bootstrap_admin(db_session, email="bootstrap-dek-failure@example.com", password="AdminPassword123")

    assert exc_info.value.code == "vault_write_failed"
    assert db_session.scalar(select(User).where(User.email == "bootstrap-dek-failure@example.com")) is None
