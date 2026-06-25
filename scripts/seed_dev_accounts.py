from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.errors import AppError
from app.models import Team, TeamRole, TeamStatus, Transcript, User, UserEncryptionKey, UserOnboardingState, UserStatus, UserTrustedDevice
from app.normalization import normalize_email, normalize_team_name_key
from app.services.admin import hash_password
from app.services.content_crypto import ensure_user_dek, get_active_user_key
from app.services.default_assets import ensure_builtin_team_assets
from app.services.transcripts import delete_retry_sources_for_transcripts
from app.services.vault import unwrap_user_content_data_key


def _env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise SystemExit(f"{name} must not be empty")
    return value


def ensure_team(db: Session, *, team_name: str) -> Team:
    team_name_key = normalize_team_name_key(team_name)
    team = db.scalar(select(Team).where(Team.name_key == team_name_key))
    if team is None:
        team = Team(name=team_name, name_key=team_name_key, status=TeamStatus.active, default_retention_days=30)
        db.add(team)
        db.commit()
        db.refresh(team)
        return team

    team.name = team_name
    team.name_key = team_name_key
    team.status = TeamStatus.active
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def ensure_dev_user(
    db: Session,
    *,
    full_name: str,
    email: str,
    password: str,
    team: Team,
    team_role: TeamRole,
) -> User:
    normalized_email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        user = User(
            full_name=full_name,
            email=normalized_email,
            password_hash=hash_password(password),
            team_id=team.id,
            team_role=team_role,
            is_system_admin=False,
            status=UserStatus.active,
            must_change_password=False,
            onboarding_state=UserOnboardingState.complete,
            mfa_required=False,
            mfa_enabled=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.full_name = full_name
        user.email = normalized_email
        user.password_hash = hash_password(password)
        user.team_id = team.id
        user.team_role = team_role
        user.is_system_admin = False
        user.status = UserStatus.active
        user.must_change_password = False
        user.onboarding_state = UserOnboardingState.complete
        user.mfa_required = False
        user.mfa_enabled = False
        db.add(user)
        db.commit()
        db.refresh(user)

    for method in list(user.mfa_methods):
        db.delete(method)
    for code in list(user.recovery_codes):
        db.delete(code)
    for device in db.scalars(select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id)):
        db.delete(device)
    for session in list(user.sessions):
        db.delete(session)
    db.commit()
    db.refresh(user)
    repair_dev_user_content_key_if_needed(db, user=user)
    return user


def ensure_dev_system_admin(
    db: Session,
    *,
    full_name: str,
    email: str,
    password: str,
) -> User:
    normalized_email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        user = User(
            full_name=full_name,
            email=normalized_email,
            password_hash=hash_password(password),
            team_id=None,
            team_role=None,
            is_system_admin=True,
            status=UserStatus.active,
            must_change_password=False,
            onboarding_state=UserOnboardingState.complete,
            mfa_required=False,
            mfa_enabled=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if _user_has_transcript_content(db, user=user):
            _reset_dev_user_transcript_content(db, user=user)
            db.refresh(user)

        user.full_name = full_name
        user.email = normalized_email
        user.password_hash = hash_password(password)
        user.team_id = None
        user.team_role = None
        user.is_system_admin = True
        user.status = UserStatus.active
        user.must_change_password = False
        user.onboarding_state = UserOnboardingState.complete
        user.mfa_required = False
        user.mfa_enabled = False
        db.add(user)
        db.commit()
        db.refresh(user)

    for method in list(user.mfa_methods):
        db.delete(method)
    for code in list(user.recovery_codes):
        db.delete(code)
    for device in db.scalars(select(UserTrustedDevice).where(UserTrustedDevice.user_id == user.id)):
        db.delete(device)
    for session in list(user.sessions):
        db.delete(session)
    for key_record in list(db.scalars(select(UserEncryptionKey).where(UserEncryptionKey.user_id == user.id))):
        db.delete(key_record)
    db.commit()
    db.refresh(user)
    return user


def repair_dev_user_content_key_if_needed(db: Session, *, user: User) -> None:
    key_record = get_active_user_key(db, user_id=user.id)
    if key_record is None:
        if _user_has_transcript_content(db, user=user):
            _reset_dev_user_transcript_content(db, user=user)
        ensure_user_dek(db, user=user)
        db.commit()
        db.refresh(user)
        return

    try:
        unwrap_user_content_data_key(
            wrapped_dek=key_record.wrapped_dek,
            mount_point=key_record.kek_mount,
            key_name=key_record.kek_key_name,
        )
    except AppError as exc:
        if exc.code != "vault_read_failed":
            raise
        _reset_dev_user_transcript_content(db, user=user)
        ensure_user_dek(db, user=user)
        db.commit()
        db.refresh(user)


def _user_has_transcript_content(db: Session, *, user: User) -> bool:
    return db.scalar(select(Transcript.id).where(Transcript.owner_user_id == user.id).limit(1)) is not None


def _reset_dev_user_transcript_content(db: Session, *, user: User) -> None:
    transcript_rows = list(db.scalars(select(Transcript).where(Transcript.owner_user_id == user.id)))
    delete_retry_sources_for_transcripts(db, transcript_ids=[transcript.id for transcript in transcript_rows])
    for transcript in transcript_rows:
        db.delete(transcript)
    for key_record in list(db.scalars(select(UserEncryptionKey).where(UserEncryptionKey.user_id == user.id))):
        db.delete(key_record)
    db.commit()


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    team_name = _env("DEV_TEST_TEAM_NAME", "Dev Test Team")
    leader_email = _env("DEV_TEST_LEADER_EMAIL", "dev.leader@example.com")
    leader_password = _env("DEV_TEST_LEADER_PASSWORD", "test1234")
    user_email = _env("DEV_TEST_USER_EMAIL", "dev.user@example.com")
    user_password = _env("DEV_TEST_USER_PASSWORD", "test1234")
    admin_email = _env("DEV_TEST_ADMIN_EMAIL", "dev.admin@example.com")
    admin_password = _env("DEV_TEST_ADMIN_PASSWORD", "test1234")

    engine = create_engine(database_url, future=True)
    with Session(engine) as db:
        team = ensure_team(db, team_name=team_name)
        admin = ensure_dev_system_admin(
            db,
            full_name="Dev Test Admin",
            email=admin_email,
            password=admin_password,
        )
        leader = ensure_dev_user(
            db,
            full_name="Dev Test Leader",
            email=leader_email,
            password=leader_password,
            team=team,
            team_role=TeamRole.leader,
        )
        user = ensure_dev_user(
            db,
            full_name="Dev Test User",
            email=user_email,
            password=user_password,
            team=team,
            team_role=TeamRole.user,
        )
        ensure_builtin_team_assets(db, team=team, actor=leader)
        db.commit()
        admin_email = admin.email
        leader_email = leader.email
        user_email = user.email

    print("Seeded dev test accounts:")
    print(f"team={team_name}")
    print(f"admin_email={admin_email}")
    print(f"leader_email={leader_email}")
    print(f"user_email={user_email}")


if __name__ == "__main__":
    main()
