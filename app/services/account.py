import re
import unicodedata

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import User
from app.normalization import normalize_email
from app.services.auth import active_primary_totp_method, verify_active_totp_for_user
from app.services.passwords import hash_password, validate_password_strength, verify_password


_email_adapter = TypeAdapter(EmailStr)
_whitespace_re = re.compile(r"\s+")


def _reauthenticate(db: Session, user: User, *, current_password: str, mfa_code: str = "") -> None:
    if not verify_password(current_password, user.password_hash):
        raise AppError(401, "reauthentication_failed", "Current password is incorrect")
    if active_primary_totp_method(user) is None:
        return
    if not mfa_code.strip():
        raise AppError(403, "fresh_mfa_required", "Authenticator code is required")
    verify_active_totp_for_user(db, user, code=mfa_code.strip())


def update_own_name(db: Session, user: User, *, full_name: str) -> User:
    normalized_name = _whitespace_re.sub(" ", unicodedata.normalize("NFKC", full_name).strip())
    if len(normalized_name) > 255:
        raise AppError(422, "invalid_name", "Name must be 255 characters or fewer")
    user.full_name = normalized_name or None
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_own_email(
    db: Session,
    user: User,
    *,
    email: str,
    current_password: str,
    mfa_code: str = "",
) -> User:
    _reauthenticate(db, user, current_password=current_password, mfa_code=mfa_code)
    try:
        validated_email = str(_email_adapter.validate_python(email))
    except ValidationError as exc:
        raise AppError(422, "invalid_email", "Enter a valid email address") from exc
    normalized_email = normalize_email(validated_email)
    existing_user_id = db.scalar(select(User.id).where(User.email == normalized_email, User.id != user.id))
    if existing_user_id is not None:
        raise AppError(409, "email_unavailable", "Email address is unavailable")
    user.email = normalized_email
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "email_unavailable", "Email address is unavailable") from exc
    db.refresh(user)
    return user


def update_own_password(
    db: Session,
    user: User,
    *,
    current_password: str,
    new_password: str,
    confirm_password: str,
    mfa_code: str = "",
) -> User:
    _reauthenticate(db, user, current_password=current_password, mfa_code=mfa_code)
    if new_password != confirm_password:
        raise AppError(422, "password_mismatch", "New passwords do not match")
    validate_password_strength(new_password)
    if verify_password(new_password, user.password_hash):
        raise AppError(422, "password_unchanged", "New password must differ from current password")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.temporary_password_expires_at = None
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
