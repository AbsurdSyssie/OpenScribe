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

from app.errors import AppError
from app.models import Transcript, User, UserEncryptionKey
from app.services.content_crypto import ensure_user_dek, get_active_user_key
from app.services.transcripts import delete_retry_sources_for_transcripts
from app.services.vault import unwrap_user_content_data_key


def user_content_key_is_unreadable(db: Session, *, user: User) -> bool:
    key_record = get_active_user_key(db, user_id=user.id)
    if key_record is None:
        return db.scalar(select(Transcript.id).where(Transcript.owner_user_id == user.id).limit(1)) is not None
    try:
        unwrap_user_content_data_key(
            wrapped_dek=key_record.wrapped_dek,
            mount_point=key_record.kek_mount,
            key_name=key_record.kek_key_name,
        )
        return False
    except AppError as exc:
        if exc.code != "vault_read_failed":
            raise
        return True


def reset_owner_content_for_user(db: Session, *, user: User) -> None:
    transcript_rows = list(db.scalars(select(Transcript).where(Transcript.owner_user_id == user.id)))
    delete_retry_sources_for_transcripts(db, transcript_ids=[transcript.id for transcript in transcript_rows])
    for transcript in transcript_rows:
        db.delete(transcript)
    for key_record in list(db.scalars(select(UserEncryptionKey).where(UserEncryptionKey.user_id == user.id))):
        db.delete(key_record)
    db.commit()
    ensure_user_dek(db, user=user)
    db.commit()


def reset_unreadable_owner_content(
    db: Session,
    *,
    emails: set[str] | None = None,
    apply: bool = False,
) -> list[str]:
    unreadable: list[str] = []
    query = select(User).where(User.is_system_admin.is_(False)).order_by(User.email)
    for user in db.scalars(query):
        if emails and user.email not in emails:
            continue
        if not user_content_key_is_unreadable(db, user=user):
            continue
        unreadable.append(user.email)
        if apply:
            reset_owner_content_for_user(db, user=user)
    return unreadable


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset unreadable transcript-derived content for local owner accounts.")
    parser.add_argument("--apply", action="store_true", help="Delete unreadable transcript-derived content and issue a fresh DEK.")
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
        print("No unreadable owner-content keys found.")
        return

    for email in unreadable:
        print(email)
    if not args.apply:
        print("Dry run only. Re-run with --apply to delete unreadable transcript-derived content and issue fresh DEKs.")


if __name__ == "__main__":
    main()
