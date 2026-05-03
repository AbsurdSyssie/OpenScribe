import html
import logging
import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import AuthEmailToken, AuthEmailTokenPurpose, MfaMethodType, TeamRole, User, UserMfaMethod, UserOnboardingState, UserRecoveryCode, UserStatus, utcnow
from app.normalization import normalize_email
from app.services.auth import create_session, opaque_token_hash, revoke_sessions_for_user, revoke_trusted_devices_for_user
from app.services.mail import MAIL_TRANSPORT_DISABLED, MailMessage, load_mail_config_from_env, send_transactional_email, validate_mail_config
from app.services.passwords import hash_password


AUTH_EMAIL_TOKEN_LIFETIME = timedelta(hours=1)
GENERIC_PASSWORD_RESET_MESSAGE = "If the email matches an eligible local account, reset instructions have been sent."
PASSWORD_RESET_EMAIL_DISABLED_MESSAGE = "Email password reset is not enabled. Contact your team leader or system administrator for a reset."
auth_email_logger = logging.getLogger("openscribe.auth_email")


def email_password_reset_enabled() -> bool:
    try:
        config = load_mail_config_from_env()
        validate_mail_config(config)
        return config.transport != MAIL_TRANSPORT_DISABLED
    except AppError:
        return False


def _token_url(*, path: str, token: str) -> str:
    config = load_mail_config_from_env()
    if not config.app_public_url:
        raise AppError(500, "mail_public_url_missing", "Public application URL is not configured")
    return f"{config.app_public_url.rstrip('/')}{path}?token={token}"


def issue_auth_email_token(
    db: Session,
    user: User,
    *,
    purpose: AuthEmailTokenPurpose,
    created_by: User | None = None,
    expires_in: timedelta = AUTH_EMAIL_TOKEN_LIFETIME,
) -> str:
    now = utcnow()
    existing_tokens = db.scalars(
        select(AuthEmailToken).where(
            AuthEmailToken.user_id == user.id,
            AuthEmailToken.purpose == purpose,
            AuthEmailToken.used_at.is_(None),
        )
    )
    for existing in existing_tokens:
        existing.used_at = now
        db.add(existing)

    raw_token = secrets.token_urlsafe(32)
    db.add(
        AuthEmailToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=opaque_token_hash(raw_token),
            expires_at=now + expires_in,
            created_by_user_id=created_by.id if created_by else None,
            created_at=now,
        )
    )
    db.commit()
    return raw_token


def _auth_email_idempotency_key(db: Session, *, raw_token: str) -> str:
    token_record = db.scalar(select(AuthEmailToken).where(AuthEmailToken.token_hash == opaque_token_hash(raw_token)))
    if token_record is None:
        raise AppError(500, "auth_email_token_missing", "Auth email token record is missing")
    return f"auth-email-{token_record.id}"


def send_account_activation_email(db: Session, user: User, *, created_by: User | None = None) -> None:
    if not _account_activation_allowed(user):
        raise AppError(409, "activation_not_pending", "Account setup links are only available before first password setup")
    config = load_mail_config_from_env()
    if config.transport == "disabled":
        send_transactional_email(
            MailMessage(
                purpose=AuthEmailTokenPurpose.account_activation.value,
                to_email=user.email,
                subject="Set up your OpenScribe account",
                text_body="Mail transport is disabled.",
            ),
            config=config,
        )
        return
    token = issue_auth_email_token(db, user, purpose=AuthEmailTokenPurpose.account_activation, created_by=created_by)
    setup_url = _token_url(path="/activate-account", token=token)
    send_transactional_email(
        MailMessage(
            purpose=AuthEmailTokenPurpose.account_activation.value,
            to_email=user.email,
            subject="Set up your OpenScribe account",
            text_body=(
                "Set up your OpenScribe account using this link:\n\n"
                f"{setup_url}\n\n"
                "This link expires in 1 hour. If you did not expect this email, ignore it."
            ),
            html_body=(
                "<p>Set up your OpenScribe account using this link:</p>"
                f'<p><a href="{html.escape(setup_url)}">Set up account</a></p>'
                "<p>This link expires in 1 hour. If you did not expect this email, ignore it.</p>"
            ),
            idempotency_key=_auth_email_idempotency_key(db, raw_token=token),
        )
    )


def request_password_reset(db: Session, *, email: str) -> str:
    if not email_password_reset_enabled():
        raise AppError(503, "mail_transport_disabled", PASSWORD_RESET_EMAIL_DISABLED_MESSAGE)
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or user.status is not UserStatus.active:
        return GENERIC_PASSWORD_RESET_MESSAGE
    try:
        send_password_reset_email(db, user, purpose=AuthEmailTokenPurpose.password_reset)
    except AppError as exc:
        auth_email_logger.warning(
            "password_reset_email_send_failed",
            extra={"event": "password_reset_email_send_failed", "error_code": exc.code},
        )
    return GENERIC_PASSWORD_RESET_MESSAGE


def send_password_reset_email(
    db: Session,
    user: User,
    *,
    purpose: AuthEmailTokenPurpose,
    created_by: User | None = None,
) -> None:
    config = load_mail_config_from_env()
    if config.transport == "disabled":
        send_transactional_email(
            MailMessage(
                purpose=purpose.value,
                to_email=user.email,
                subject="Reset your OpenScribe password",
                text_body="Mail transport is disabled.",
            ),
            config=config,
        )
        return
    token = issue_auth_email_token(db, user, purpose=purpose, created_by=created_by)
    reset_url = _token_url(path="/reset-password", token=token)
    send_transactional_email(
        MailMessage(
            purpose=purpose.value,
            to_email=user.email,
            subject="Reset your OpenScribe password",
            text_body=(
                "Reset your OpenScribe password using this link:\n\n"
                f"{reset_url}\n\n"
                "This link expires in 1 hour. If you did not request this, ignore it."
            ),
            html_body=(
                "<p>Reset your OpenScribe password using this link:</p>"
                f'<p><a href="{html.escape(reset_url)}">Reset password</a></p>'
                "<p>This link expires in 1 hour. If you did not request this, ignore it.</p>"
            ),
            idempotency_key=_auth_email_idempotency_key(db, raw_token=token),
        )
    )


def _consume_token(db: Session, *, raw_token: str, allowed_purposes: set[AuthEmailTokenPurpose]) -> AuthEmailToken:
    token = db.scalar(
        select(AuthEmailToken)
        .options(joinedload(AuthEmailToken.user).joinedload(User.mfa_methods), joinedload(AuthEmailToken.user).joinedload(User.recovery_codes))
        .where(AuthEmailToken.token_hash == opaque_token_hash(raw_token))
    )
    if token is None or token.purpose not in allowed_purposes:
        raise AppError(422, "token_invalid", "Reset or setup link is invalid")
    if token.used_at is not None:
        raise AppError(422, "token_used", "Reset or setup link has already been used")
    if token.expires_at <= utcnow():
        raise AppError(422, "token_expired", "Reset or setup link has expired")
    if token.user.status is not UserStatus.active:
        raise AppError(403, "forbidden", "User account is not active", {"status": token.user.status.value})
    token.used_at = utcnow()
    db.add(token)
    return token


def _account_activation_allowed(user: User) -> bool:
    return user.onboarding_state is UserOnboardingState.pending_password_change and user.must_change_password


def confirm_account_activation(db: Session, *, raw_token: str, new_password: str) -> tuple[User, str]:
    token = _consume_token(db, raw_token=raw_token, allowed_purposes={AuthEmailTokenPurpose.account_activation})
    user = token.user
    if not _account_activation_allowed(user):
        raise AppError(409, "activation_not_pending", "Account setup link is no longer valid for this user")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.onboarding_state = UserOnboardingState.pending_totp_enrollment
    user.mfa_enabled = False
    _delete_mfa_state(db, user)
    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="account_activation_completed")
    revoke_trusted_devices_for_user(db, user, reason="account_activation_completed")
    refreshed = db.get(User, user.id) or user
    return refreshed, create_session(db, refreshed)


def confirm_password_reset(db: Session, *, raw_token: str, new_password: str) -> User:
    token = _consume_token(
        db,
        raw_token=raw_token,
        allowed_purposes={
            AuthEmailTokenPurpose.password_reset,
            AuthEmailTokenPurpose.manager_password_reset,
            AuthEmailTokenPurpose.manager_account_recovery,
        },
    )
    user = token.user
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    if token.purpose is AuthEmailTokenPurpose.manager_account_recovery:
        user.onboarding_state = UserOnboardingState.pending_totp_enrollment
        user.mfa_enabled = False
        _delete_mfa_state(db, user)
    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="password_reset")
    revoke_trusted_devices_for_user(db, user, reason="password_reset")
    return db.get(User, user.id) or user


def reset_user_mfa_for_reenrollment(db: Session, *, user: User) -> User:
    if user.onboarding_state is not UserOnboardingState.pending_password_change:
        user.onboarding_state = UserOnboardingState.pending_totp_enrollment
    user.mfa_enabled = False
    _delete_mfa_state(db, user)
    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="mfa_reset")
    revoke_trusted_devices_for_user(db, user, reason="mfa_reset")
    return db.get(User, user.id) or user


def _delete_mfa_state(db: Session, user: User) -> None:
    for method in db.scalars(select(UserMfaMethod).where(UserMfaMethod.user_id == user.id, UserMfaMethod.method_type == MfaMethodType.totp)):
        db.delete(method)
    for code in db.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)):
        db.delete(code)


def get_active_token_user(db: Session, *, raw_token: str, purpose: AuthEmailTokenPurpose) -> User | None:
    token = db.scalar(
        select(AuthEmailToken)
        .options(joinedload(AuthEmailToken.user))
        .where(
            AuthEmailToken.token_hash == opaque_token_hash(raw_token),
            AuthEmailToken.purpose == purpose,
            AuthEmailToken.used_at.is_(None),
            AuthEmailToken.expires_at > utcnow(),
        )
    )
    return token.user if token else None


def get_manageable_user_for_recovery(db: Session, actor: User, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError(404, "not_found", "User not found", {"resource": "user", "user_id": str(user_id)})
    if actor.id == user.id:
        raise AppError(403, "forbidden", "You may not manage your own account")
    if actor.is_system_admin:
        return user
    if actor.team_role is not TeamRole.leader:
        raise AppError(403, "forbidden", "User-management access required")
    if user.is_system_admin:
        raise AppError(403, "forbidden", "Leaders may not manage system-admin accounts")
    if actor.team_id is None or actor.team_id != user.team_id:
        raise AppError(403, "forbidden", "Leaders may only manage users in their own team")
    return user
