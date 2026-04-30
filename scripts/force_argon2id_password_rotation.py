from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models import SessionStatus, User, UserOnboardingState, UserSession, UserTrustedDevice, utcnow
from app.services.passwords import hash_password


@dataclass(slots=True)
class RotatedPassword:
    email: str
    temporary_password: str


def _needs_rotation(user: User) -> bool:
    return not user.password_hash.startswith("$argon2id$")


def rotate_non_argon2id_passwords(db: Session) -> list[RotatedPassword]:
    rotated: list[RotatedPassword] = []
    users = list(db.scalars(select(User).order_by(User.email.asc())))
    for user in users:
        if not _needs_rotation(user):
            continue

        temporary_password = secrets.token_urlsafe(24)
        user.password_hash = hash_password(temporary_password)
        user.must_change_password = True
        user.onboarding_state = UserOnboardingState.pending_password_change
        db.add(user)

        now = utcnow()
        for session in db.scalars(
            select(UserSession).where(UserSession.user_id == user.id, UserSession.status == SessionStatus.active)
        ):
            session.status = SessionStatus.revoked
            session.revoked_at = now
            session.revoke_reason = "argon2id_password_rotation"
            db.add(session)
        for device in db.scalars(
            select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id, UserTrustedDevice.revoked_at.is_(None))
        ):
            device.revoked_at = now
            device.revoke_reason = "argon2id_password_rotation"
            db.add(device)

        rotated.append(RotatedPassword(email=user.email, temporary_password=temporary_password))

    db.commit()
    return rotated


def main() -> None:
    if "--confirm-dev-password-rotation" not in sys.argv:
        raise SystemExit(
            "This rotates local user passwords and prints temporary passwords once.\n"
            "Run: python scripts/force_argon2id_password_rotation.py --confirm-dev-password-rotation"
        )
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url, future=True)
    with Session(engine) as db:
        rotated = rotate_non_argon2id_passwords(db)

    if not rotated:
        print("All user password hashes already use Argon2id.")
        return

    print("Rotated non-Argon2id password hashes. Temporary passwords shown once:")
    for item in rotated:
        print(f"{item.email}\t{item.temporary_password}")


if __name__ == "__main__":
    main()
