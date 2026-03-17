import base64
import hashlib
import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import (
    AccountRequest,
    AccountRequestStatus,
    Team,
    TeamRole,
    Transcript,
    User,
    UserOnboardingState,
    UserStatus,
    utcnow,
)
from app.normalization import normalize_email, normalize_team_name_key
from app.schemas import (
    AccountRequestApprove,
    AccountRequestCreate,
    AccountRequestReject,
    TeamCreate,
    UserCreate,
)
from app.services.auth import revoke_sessions_for_user, revoke_trusted_devices_for_user

audit_logger = logging.getLogger("openscribe.audit")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    derived_b64 = base64.b64encode(derived).decode("ascii")
    return f"scrypt${salt_b64}${derived_b64}"


def create_team(db: Session, payload: TeamCreate) -> Team:
    stripped_name = payload.name.strip()
    team = Team(
        name=stripped_name,
        name_key=normalize_team_name_key(stripped_name),
        status=payload.status,
        default_retention_days=payload.default_retention_days,
    )
    db.add(team)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Team already exists", {"resource": "team", "field": "name"}) from exc
    db.refresh(team)
    return team


def list_teams(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.created_at.desc())))


def _resolve_manageable_team(db: Session, *, actor: User | None, payload: UserCreate) -> tuple[Team | None, TeamRole | None]:
    if payload.is_system_admin:
        if actor is not None and not actor.is_system_admin:
            raise AppError(403, "forbidden", "Only system admins may create system-admin accounts")
        team = None
        team_role = None
    else:
        if payload.team_id is None:
            raise AppError(
                422,
                "business_rule_violation",
                "Team is required for non-system-admin users",
                {"field": "team_id"},
            )
        if payload.team_role is None:
            raise AppError(
                422,
                "business_rule_violation",
                "Team role is required for non-system-admin users",
                {"field": "team_role"},
            )
        team = db.get(Team, payload.team_id)
        if not team:
            raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(payload.team_id)})
        team_role = payload.team_role

        if actor is not None and not actor.is_system_admin:
            if actor.team_role is not TeamRole.leader or actor.team_id != team.id:
                raise AppError(403, "forbidden", "Leaders may only manage users in their own team")

    return team, team_role


def _create_user_record(db: Session, payload: UserCreate, *, actor: User | None, onboarding_state: UserOnboardingState) -> User:
    team, team_role = _resolve_manageable_team(db, actor=actor, payload=payload)
    user = User(
        full_name=payload.full_name.strip() if payload.full_name else None,
        email=normalize_email(payload.email),
        password_hash=hash_password(payload.temporary_password),
        team_id=team.id if team else None,
        team_role=team_role,
        is_system_admin=payload.is_system_admin,
        status=payload.status,
        mfa_required=payload.mfa_required,
        mfa_enabled=False,
        must_change_password=onboarding_state is UserOnboardingState.pending_password_change,
        onboarding_state=onboarding_state,
    )
    db.add(user)
    return user


def create_user(db: Session, payload: UserCreate, *, actor: User | None = None) -> User:
    user = _create_user_record(
        db,
        payload,
        actor=actor,
        onboarding_state=UserOnboardingState.pending_password_change,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    stmt = select(User).options(joinedload(User.team)).order_by(User.created_at.desc())
    return list(db.scalars(stmt).unique())


def list_manageable_users(db: Session, actor: User) -> list[User]:
    stmt = select(User).options(joinedload(User.team)).order_by(User.created_at.desc())
    if actor.is_system_admin:
        return list(db.scalars(stmt).unique())
    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "User-management access required")
    stmt = stmt.where(User.team_id == actor.team_id, User.is_system_admin.is_(False))
    return list(db.scalars(stmt).unique())


def _active_system_admin_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(
                User.is_system_admin.is_(True),
                User.status == UserStatus.active,
            )
        )
        or 0
    )


def _log_account_lifecycle_event(*, actor: User, target: User, event: str) -> None:
    audit_logger.info(
        "account_lifecycle",
        extra={
            "event": event,
            "actor_user_id": str(actor.id),
            "actor_is_system_admin": actor.is_system_admin,
            "actor_team_id": str(actor.team_id) if actor.team_id else None,
            "target_user_id": str(target.id),
            "target_email": target.email,
            "target_team_id": str(target.team_id) if target.team_id else None,
            "target_team_role": target.team_role.value if target.team_role else None,
            "target_status": target.status.value,
            "target_is_system_admin": target.is_system_admin,
        },
    )


def _get_manageable_user(db: Session, actor: User, user_id) -> User:
    user = db.scalar(select(User).options(joinedload(User.team)).where(User.id == user_id))
    if user is None:
        raise AppError(404, "not_found", "User not found", {"resource": "user", "user_id": str(user_id)})
    if actor.id == user.id:
        raise AppError(403, "forbidden", "You may not manage your own account")
    if actor.is_system_admin:
        return user
    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "User-management access required")
    if user.is_system_admin:
        raise AppError(403, "forbidden", "Leaders may not manage system-admin accounts")
    if user.team_id != actor.team_id:
        raise AppError(403, "forbidden", "Leaders may only manage users in their own team")
    return user


def suspend_user(db: Session, actor: User, user_id) -> User:
    user = _get_manageable_user(db, actor, user_id)
    if user.status is UserStatus.suspended:
        raise AppError(409, "conflict", "User is already suspended", {"status": user.status.value})
    if user.is_system_admin and user.status is UserStatus.active and _active_system_admin_count(db) <= 1:
        raise AppError(409, "conflict", "Cannot suspend the last active system-admin account")

    user.status = UserStatus.suspended
    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="user_suspended")
    revoke_trusted_devices_for_user(db, user, reason="user_suspended")
    db.refresh(user)
    _log_account_lifecycle_event(actor=actor, target=user, event="account_suspended")
    return user


def reactivate_user(db: Session, actor: User, user_id) -> User:
    user = _get_manageable_user(db, actor, user_id)
    if user.status not in {UserStatus.suspended, UserStatus.disabled}:
        raise AppError(409, "conflict", "User is not eligible for reactivation", {"status": user.status.value})

    user.status = UserStatus.active
    user.must_change_password = True
    user.onboarding_state = UserOnboardingState.pending_password_change
    user.mfa_enabled = False

    for method in list(user.mfa_methods):
        db.delete(method)
    for code in list(user.recovery_codes):
        db.delete(code)

    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="user_reactivated_reset")
    revoke_trusted_devices_for_user(db, user, reason="user_reactivated_reset")
    db.refresh(user)
    _log_account_lifecycle_event(actor=actor, target=user, event="account_reactivated")
    return user


def delete_user(db: Session, actor: User, user_id) -> None:
    user = _get_manageable_user(db, actor, user_id)
    if user.is_system_admin and user.status is UserStatus.active and _active_system_admin_count(db) <= 1:
        raise AppError(409, "conflict", "Cannot delete the last active system-admin account")

    _log_account_lifecycle_event(actor=actor, target=user, event="account_deleted")

    revoke_sessions_for_user(db, user, reason="user_deleted")
    revoke_trusted_devices_for_user(db, user, reason="user_deleted")

    linked_requests = db.scalars(select(AccountRequest).where(AccountRequest.linked_user_id == user.id))
    for request in linked_requests:
        request.linked_user_id = None
        db.add(request)

    reviewed_requests = db.scalars(select(AccountRequest).where(AccountRequest.reviewed_by_user_id == user.id))
    for request in reviewed_requests:
        request.reviewed_by_user_id = None
        db.add(request)

    transcripts = db.scalars(select(Transcript).where(Transcript.owner_user_id == user.id))
    for transcript in transcripts:
        db.delete(transcript)

    db.flush()
    db.delete(user)
    db.commit()


def user_count(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


def create_bootstrap_admin(db: Session, *, email: str, password: str) -> User:
    payload = UserCreate(
        email=email,
        temporary_password=password,
        is_system_admin=True,
        mfa_required=True,
    )
    user = _create_user_record(
        db,
        payload,
        actor=None,
        onboarding_state=UserOnboardingState.pending_totp_enrollment,
    )
    user.must_change_password = False
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    db.refresh(user)
    return user


def create_account_request(db: Session, payload: AccountRequestCreate) -> AccountRequest:
    normalized_email = normalize_email(payload.requested_email)
    team_name = payload.requested_team_name.strip()
    team_name_key = normalize_team_name_key(team_name)

    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user is not None:
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"})

    duplicate_pending = db.scalar(
        select(AccountRequest).where(
            AccountRequest.requested_email == normalized_email,
            AccountRequest.requested_team_name_key == team_name_key,
            AccountRequest.status == AccountRequestStatus.pending,
        )
    )
    if duplicate_pending is not None:
        raise AppError(409, "conflict", "Account request already exists", {"resource": "account_request"})

    request = AccountRequest(
        requested_name=payload.requested_name.strip(),
        requested_email=normalized_email,
        requested_team_name=team_name,
        requested_team_name_key=team_name_key,
        request_details=payload.request_details.strip() if payload.request_details else None,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def _request_scope_clause(actor: User):
    if actor.is_system_admin:
        return True
    if actor.team_role is not TeamRole.leader or actor.team is None:
        raise AppError(403, "forbidden", "Account-request review access required")
    return AccountRequest.requested_team_name_key == actor.team.name_key


def list_manageable_account_requests(db: Session, actor: User) -> list[AccountRequest]:
    stmt = select(AccountRequest).order_by(AccountRequest.created_at.desc())
    scope = _request_scope_clause(actor)
    if scope is not True:
        stmt = stmt.where(scope)
    return list(db.scalars(stmt))


def _get_manageable_account_request(db: Session, actor: User, request_id) -> AccountRequest:
    request = db.get(AccountRequest, request_id)
    if request is None:
        raise AppError(404, "not_found", "Account request not found", {"resource": "account_request", "request_id": str(request_id)})
    scope = _request_scope_clause(actor)
    if scope is not True and request.requested_team_name_key != actor.team.name_key:
        raise AppError(403, "forbidden", "Account-request review access required")
    return request


def approve_account_request(db: Session, actor: User, request_id, payload: AccountRequestApprove) -> tuple[AccountRequest, User]:
    request = _get_manageable_account_request(db, actor, request_id)
    if request.status is not AccountRequestStatus.pending:
        raise AppError(409, "conflict", "Account request is no longer pending", {"resource": "account_request"})

    if actor.is_system_admin:
        team_id = payload.team_id
    else:
        team_id = actor.team_id

    user = _create_user_record(
        db,
        UserCreate(
            full_name=payload.full_name or request.requested_name,
            email=request.requested_email,
            temporary_password=payload.temporary_password,
            team_id=team_id,
            team_role=payload.team_role,
            is_system_admin=False,
            mfa_required=payload.mfa_required,
        ),
        actor=actor,
        onboarding_state=UserOnboardingState.pending_password_change,
    )
    request.status = AccountRequestStatus.approved
    request.review_notes = payload.review_notes.strip() if payload.review_notes else None
    request.reviewed_by_user_id = actor.id
    request.linked_user = user
    request.reviewed_at = utcnow()
    db.add(request)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    db.refresh(request)
    db.refresh(user)
    return request, user


def reject_account_request(db: Session, actor: User, request_id, payload: AccountRequestReject) -> AccountRequest:
    request = _get_manageable_account_request(db, actor, request_id)
    if request.status is not AccountRequestStatus.pending:
        raise AppError(409, "conflict", "Account request is no longer pending", {"resource": "account_request"})
    request.status = AccountRequestStatus.rejected
    request.review_notes = payload.review_notes.strip()
    request.reviewed_by_user_id = actor.id
    request.reviewed_at = utcnow()
    db.add(request)
    db.commit()
    db.refresh(request)
    return request
