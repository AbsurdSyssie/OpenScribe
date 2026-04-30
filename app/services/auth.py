import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID

import pyotp
import segno
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import (
    MfaMethodType,
    SessionAuthLevel,
    SessionStatus,
    User,
    UserMfaMethod,
    UserOnboardingState,
    UserRecoveryCode,
    UserSession,
    UserTrustedDevice,
    UserStatus,
    utcnow,
)
from app.normalization import normalize_email
from app.services.passwords import hash_password, password_needs_rehash, verify_password


SESSION_COOKIE_NAME = "openscribe_session"
TRUSTED_DEVICE_COOKIE_NAME = "openscribe_trusted_device"
SESSION_LIFETIME = timedelta(hours=12)
TRUSTED_DEVICE_LIFETIME = timedelta(days=30)
MFA_FRESHNESS_WINDOW = timedelta(days=1)
RECOVERY_CODE_COUNT = 8


def opaque_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_token_hash(token: str) -> str:
    return opaque_token_hash(token)


def recovery_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def trusted_device_token_hash(token: str) -> str:
    return opaque_token_hash(token)


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    stmt = (
        select(User)
        .options(joinedload(User.team), joinedload(User.mfa_methods))
        .where(User.id == user_id)
    )
    return db.scalar(stmt)


def determine_auth_level(user: User) -> SessionAuthLevel:
    if user.onboarding_state is UserOnboardingState.complete:
        return SessionAuthLevel.full
    return SessionAuthLevel.onboarding


def active_primary_totp_method(user: User) -> UserMfaMethod | None:
    for method in user.mfa_methods:
        if method.method_type is MfaMethodType.totp and method.is_primary and method.is_active:
            return method
    return None


def authenticate_user(db: Session, email: str, password: str) -> User:
    normalized_email = normalize_email(email)
    stmt = (
        select(User)
        .options(joinedload(User.team), joinedload(User.mfa_methods))
        .where(User.email == normalized_email)
    )
    user = db.scalar(stmt)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError(401, "unauthorized", "Invalid email or password")
    if user.status is not UserStatus.active:
        raise AppError(403, "forbidden", "User account is not active", {"status": user.status.value})
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return get_user_by_id(db, user.id) or user


def create_session(db: Session, user: User, *, auth_level: SessionAuthLevel | None = None) -> str:
    token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        session_token_hash=session_token_hash(token),
        auth_level=auth_level or determine_auth_level(user),
        status=SessionStatus.active,
        expires_at=utcnow() + SESSION_LIFETIME,
    )
    db.add(session)
    db.commit()
    return token


def trusted_device_fresh_until(device: UserTrustedDevice) -> datetime:
    return device.last_mfa_verified_at + MFA_FRESHNESS_WINDOW


def revoke_session_by_token(db: Session, token: str, *, reason: str) -> None:
    session = db.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(token)))
    if session is None or session.status is not SessionStatus.active:
        return
    session.status = SessionStatus.revoked
    session.revoked_at = utcnow()
    session.revoke_reason = reason
    db.add(session)
    db.commit()


def revoke_sessions_for_user(db: Session, user: User, *, reason: str) -> None:
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.status == SessionStatus.active)
    )
    touched = False
    for session in sessions:
        session.status = SessionStatus.revoked
        session.revoked_at = utcnow()
        session.revoke_reason = reason
        db.add(session)
        touched = True
    if touched:
        db.commit()


def revoke_trusted_devices_for_user(db: Session, user: User, *, reason: str) -> None:
    devices = db.scalars(
        select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id, UserTrustedDevice.revoked_at.is_(None))
    )
    touched = False
    for device in devices:
        device.revoked_at = utcnow()
        device.revoke_reason = reason
        db.add(device)
        touched = True
    if touched:
        db.commit()


def resolve_authenticated_session(db: Session, raw_token: str) -> tuple[User, UserSession] | None:
    stmt = (
        select(UserSession)
        .options(joinedload(UserSession.user).joinedload(User.team), joinedload(UserSession.user).joinedload(User.mfa_methods))
        .where(UserSession.session_token_hash == session_token_hash(raw_token))
    )
    session = db.scalar(stmt)
    if session is None:
        return None
    if session.status is not SessionStatus.active or session.expires_at <= utcnow():
        if session.status is SessionStatus.active:
            session.status = SessionStatus.expired
            session.revoked_at = utcnow()
            session.revoke_reason = "expired"
            db.add(session)
            db.commit()
        return None

    user = session.user
    if user.status is not UserStatus.active:
        revoke_sessions_for_user(db, user, reason=f"user_{user.status.value}")
        revoke_trusted_devices_for_user(db, user, reason=f"user_{user.status.value}")
        return None

    expected_auth_level = determine_auth_level(user)
    if session.auth_level is not SessionAuthLevel.pending_mfa and session.auth_level is not expected_auth_level:
        session.auth_level = expected_auth_level

    session.last_seen_at = utcnow()
    db.add(session)
    db.commit()
    db.refresh(user)
    db.refresh(session)
    return user, session


def rotate_session(db: Session, current_token: str, user: User, *, auth_level: SessionAuthLevel | None = None) -> str:
    revoke_session_by_token(db, current_token, reason="rotated")
    return create_session(db, user, auth_level=auth_level)


def resolve_trusted_device(db: Session, user: User, raw_token: str | None) -> UserTrustedDevice | None:
    if not raw_token:
        return None
    device = db.scalar(
        select(UserTrustedDevice).where(
            UserTrustedDevice.user_id == user.id,
            UserTrustedDevice.device_token_hash == trusted_device_token_hash(raw_token),
        )
    )
    if device is None:
        return None
    if device.revoked_at is not None or device.expires_at <= utcnow():
        if device.revoked_at is None:
            device.revoked_at = utcnow()
            device.revoke_reason = "expired"
            db.add(device)
            db.commit()
        return None
    device.last_seen_at = utcnow()
    db.add(device)
    db.commit()
    return device


def user_requires_login_mfa(user: User) -> bool:
    return (
        user.onboarding_state is UserOnboardingState.complete
        and user.mfa_required
        and user.mfa_enabled
        and active_primary_totp_method(user) is not None
    )


def trusted_device_satisfies_mfa(device: UserTrustedDevice | None) -> bool:
    if device is None:
        return False
    return trusted_device_fresh_until(device) > utcnow()


def login_auth_level(user: User, trusted_device: UserTrustedDevice | None) -> SessionAuthLevel:
    baseline = determine_auth_level(user)
    if baseline is not SessionAuthLevel.full:
        return baseline
    if user_requires_login_mfa(user) and not trusted_device_satisfies_mfa(trusted_device):
        return SessionAuthLevel.pending_mfa
    return SessionAuthLevel.full


def update_password_for_onboarding(db: Session, user: User, *, new_password_hash: str) -> User:
    if user.onboarding_state is not UserOnboardingState.pending_password_change:
        raise AppError(409, "conflict", "Password change is not pending for this user")
    user.password_hash = new_password_hash
    user.must_change_password = False
    user.onboarding_state = (
        UserOnboardingState.complete
        if user.mfa_enabled and active_primary_totp_method(user) is not None
        else UserOnboardingState.pending_totp_enrollment
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def verify_login_totp(
    db: Session,
    user: User,
    *,
    code: str,
    remember_device: bool,
    device_label: str | None = None,
) -> tuple[User, str | None]:
    method = active_primary_totp_method(user)
    if method is None:
        raise AppError(409, "conflict", "TOTP challenge is not available for this user")

    totp = pyotp.TOTP(method.secret)
    if not totp.verify(code, valid_window=1):
        raise AppError(422, "business_rule_violation", "Invalid TOTP code")

    trusted_device_token: str | None = None
    if remember_device:
        trusted_device_token = secrets.token_urlsafe(32)
        device = UserTrustedDevice(
            user_id=user.id,
            device_token_hash=trusted_device_token_hash(trusted_device_token),
            label=device_label,
            expires_at=utcnow() + TRUSTED_DEVICE_LIFETIME,
            last_mfa_verified_at=utcnow(),
        )
        db.add(device)
    db.commit()
    refreshed = get_user_by_id(db, user.id) or user
    return refreshed, trusted_device_token


def touch_trusted_device_seen(db: Session, device: UserTrustedDevice) -> None:
    device.last_seen_at = utcnow()
    db.add(device)
    db.commit()


def start_totp_enrollment(db: Session, user: User) -> UserMfaMethod:
    if user.onboarding_state is not UserOnboardingState.pending_totp_enrollment:
        raise AppError(409, "conflict", "TOTP enrollment is not pending for this user")

    existing_methods = db.scalars(
        select(UserMfaMethod).where(UserMfaMethod.user_id == user.id, UserMfaMethod.method_type == MfaMethodType.totp)
    )
    for method in existing_methods:
        db.delete(method)

    method = UserMfaMethod(
        user_id=user.id,
        method_type=MfaMethodType.totp,
        secret=pyotp.random_base32(),
        is_primary=True,
        is_active=False,
    )
    db.add(method)
    db.commit()
    db.refresh(method)
    return method


def current_pending_totp_method(db: Session, user: User) -> UserMfaMethod | None:
    return db.scalar(
        select(UserMfaMethod).where(
            UserMfaMethod.user_id == user.id,
            UserMfaMethod.method_type == MfaMethodType.totp,
            UserMfaMethod.is_primary.is_(True),
        )
    )


def provisioning_uri(user: User, method: UserMfaMethod) -> str:
    totp = pyotp.TOTP(method.secret)
    return totp.provisioning_uri(name=user.email, issuer_name="OpenScribe")


def provisioning_qr_svg_data_uri(uri: str) -> str:
    return segno.make(uri).svg_data_uri(scale=4)


def verify_totp_enrollment(db: Session, user: User, *, code: str) -> User:
    if user.onboarding_state is not UserOnboardingState.pending_totp_enrollment:
        raise AppError(409, "conflict", "TOTP enrollment is not pending for this user")

    method = current_pending_totp_method(db, user)
    if method is None:
        raise AppError(409, "conflict", "TOTP enrollment has not been started")

    totp = pyotp.TOTP(method.secret)
    if not totp.verify(code, valid_window=1):
        raise AppError(422, "business_rule_violation", "Invalid TOTP code")

    method.is_active = True
    method.verified_at = utcnow()
    user.mfa_enabled = True
    user.onboarding_state = UserOnboardingState.pending_recovery_codes
    db.add(method)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def generate_recovery_codes(db: Session, user: User) -> list[str]:
    if user.onboarding_state is not UserOnboardingState.pending_recovery_codes:
        raise AppError(409, "conflict", "Recovery-code generation is not pending for this user")

    existing_codes = db.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    for existing in existing_codes:
        db.delete(existing)

    plain_codes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        code = secrets.token_hex(4)
        plain_codes.append(code)
        db.add(UserRecoveryCode(user_id=user.id, code_hash=recovery_code_hash(code)))

    user.onboarding_state = UserOnboardingState.complete
    db.add(user)
    db.commit()
    db.refresh(user)
    return plain_codes


def skip_recovery_codes(db: Session, user: User) -> User:
    if user.onboarding_state is not UserOnboardingState.pending_recovery_codes:
        raise AppError(409, "conflict", "Recovery-code step is not pending for this user")
    user.onboarding_state = UserOnboardingState.complete
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
