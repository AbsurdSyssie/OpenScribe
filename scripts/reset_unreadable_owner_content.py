from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models import (
    SessionStatus,
    Transcript,
    User,
    UserEncryptionKey,
    UserMfaMethod,
    UserOnboardingState,
    UserRecoveryCode,
    UserSession,
    UserTrustedDevice,
    utcnow,
)
from app.services.content_crypto import ensure_user_dek, get_active_user_key, is_encrypted_envelope
from app.services.transcripts import process_transcript_audio_cleanup_jobs, queue_retry_source_cleanup_for_transcripts
from app.services.vault import unwrap_user_content_data_key


def _user_has_key_dependent_mfa(db: Session, *, user: User) -> bool:
    for stored_secret in db.scalars(select(UserMfaMethod.secret).where(UserMfaMethod.user_id == user.id)):
        if is_encrypted_envelope(stored_secret) or stored_secret.lstrip().startswith("{"):
            return True
    return False


def _user_has_key_dependent_data(db: Session, *, user: User) -> bool:
    has_transcript = db.scalar(select(Transcript.id).where(Transcript.owner_user_id == user.id).limit(1)) is not None
    return has_transcript or _user_has_key_dependent_mfa(db, user=user)


def user_content_key_is_unreadable(db: Session, *, user: User) -> bool:
    if not _user_has_key_dependent_data(db, user=user):
        return False
    key_record = get_active_user_key(db, user_id=user.id)
    if key_record is None:
        return True
    unwrap_user_content_data_key(
        wrapped_dek=key_record.wrapped_dek,
        mount_point=key_record.kek_mount,
        key_name=key_record.kek_key_name,
    )
    return False


def reset_owner_content_for_user(db: Session, *, user: User) -> None:
    cleanup_job_ids = []
    try:
        transcript_rows = list(db.scalars(select(Transcript).where(Transcript.owner_user_id == user.id)))
        cleanup_job_ids = queue_retry_source_cleanup_for_transcripts(
            db,
            transcript_ids=[transcript.id for transcript in transcript_rows],
        )
        for transcript in transcript_rows:
            db.delete(transcript)
        for key_record in list(db.scalars(select(UserEncryptionKey).where(UserEncryptionKey.user_id == user.id))):
            db.delete(key_record)

        for method in db.scalars(select(UserMfaMethod).where(UserMfaMethod.user_id == user.id)):
            db.delete(method)
        for recovery_code in db.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)):
            db.delete(recovery_code)

        now = utcnow()
        for device in db.scalars(
            select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id, UserTrustedDevice.revoked_at.is_(None))
        ):
            device.revoked_at = now
            device.revoke_reason = "encryption_key_reset"
            db.add(device)
        for session in db.scalars(
            select(UserSession).where(UserSession.user_id == user.id, UserSession.status == SessionStatus.active)
        ):
            session.status = SessionStatus.revoked
            session.revoked_at = now
            session.revoke_reason = "encryption_key_reset"
            db.add(session)

        user.mfa_enabled = False
        if user.onboarding_state is not UserOnboardingState.pending_password_change:
            user.onboarding_state = UserOnboardingState.pending_totp_enrollment
        db.add(user)
        db.flush()
        ensure_user_dek(db, user=user)
        db.commit()
    except Exception:
        db.rollback()
        raise
    process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_job_ids)


def reset_unreadable_owner_content(
    db: Session,
    *,
    emails: set[str] | None = None,
    apply: bool = False,
) -> list[str]:
    unreadable: list[str] = []
    users_to_reset: list[User] = []
    query = select(User).order_by(User.email)
    for user in db.scalars(query):
        if emails and user.email not in emails:
            continue
        if not user_content_key_is_unreadable(db, user=user):
            continue
        unreadable.append(user.email)
        users_to_reset.append(user)
    if apply:
        for user in users_to_reset:
            reset_owner_content_for_user(db, user=user)
    return unreadable


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset user data and MFA state whose DEK record is missing.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete data whose DEK record is missing, clear MFA state, revoke auth authority, and issue a fresh DEK.",
    )
    parser.add_argument("--email", action="append", default=[], help="Limit the reset to one or more specific user emails.")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    emails = {email.strip().lower() for email in args.email if email.strip()} or None
    engine = create_engine(database_url, future=True)
    with Session(engine) as db:
        unreadable = reset_unreadable_owner_content(db, emails=emails, apply=args.apply)

    if not unreadable:
        print("No missing user-data keys found.")
        return

    for email in unreadable:
        print(email)
    if not args.apply:
        print("Dry run only. Re-run with --apply to reset missing-key dependencies and issue fresh DEKs.")


if __name__ == "__main__":
    main()
