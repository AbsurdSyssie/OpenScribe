from sqlalchemy import select

from app.models import SessionStatus, TeamRole, UserOnboardingState, UserSession, UserTrustedDevice, utcnow
from app.services.admin import hash_password
from app.services.auth import create_session, recovery_code_hash, session_token_hash, trusted_device_token_hash, verify_password
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
