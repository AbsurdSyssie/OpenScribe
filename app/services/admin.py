import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import (
    AccountRequest,
    AccountRequestStatus,
    PromptTemplate,
    ProviderUsageEvent,
    ProviderUsageEventType,
    QuickAction,
    Team,
    TeamRole,
    TeamSttConfig,
    TeamSttSelection,
    TemplateScope,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
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
from app.services.content_crypto import ensure_user_dek
from app.services.transcripts import delete_retry_sources_for_transcripts

audit_logger = logging.getLogger("openscribe.audit")


@dataclass(slots=True)
class AdminUsageWindowSummary:
    label: str
    provider_completed_count: int
    provider_failed_count: int
    provider_total_tokens: int
    provider_estimated_cost_usd: float
    ingestion_job_count: int
    ingestion_failed_count: int
    whole_file_count: int
    live_chunk_count: int
    ingested_bytes: int
    ingested_duration_seconds: float

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)


@dataclass(slots=True)
class AdminUsageTeamRow:
    team_id: UUID
    team_name: str
    provider_completed_count: int = 0
    provider_failed_count: int = 0
    provider_total_tokens: int = 0
    ingestion_job_count: int = 0
    ingestion_failed_count: int = 0
    whole_file_count: int = 0
    live_chunk_count: int = 0
    ingested_bytes: int = 0
    ingested_duration_seconds: float = 0.0

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)


@dataclass(slots=True)
class AdminUsageUserRow:
    user_id: UUID
    email: str
    full_name: str | None
    provider_completed_count: int = 0
    provider_failed_count: int = 0
    provider_total_tokens: int = 0
    ingestion_job_count: int = 0
    ingestion_failed_count: int = 0
    whole_file_count: int = 0
    live_chunk_count: int = 0
    ingested_bytes: int = 0
    ingested_duration_seconds: float = 0.0

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)


def _failure_event_clause():
    return ProviderUsageEvent.event_type.in_(
        (ProviderUsageEventType.failed, ProviderUsageEventType.enqueue_failed)
    )


def _coerce_decimal(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _provider_usage_window_summary(db: Session, *, since, team_id: UUID | None) -> dict[str, int | float]:
    stmt = select(
        func.count(ProviderUsageEvent.id)
        .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
        .label("provider_completed_count"),
        func.count(ProviderUsageEvent.id)
        .filter(_failure_event_clause())
        .label("provider_failed_count"),
        func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.estimated_cost_usd), 0).label("provider_estimated_cost_usd"),
    ).where(ProviderUsageEvent.created_at >= since)
    if team_id is not None:
        stmt = stmt.where(ProviderUsageEvent.team_id == team_id)
    row = db.execute(stmt).one()
    return {
        "provider_completed_count": int(row.provider_completed_count or 0),
        "provider_failed_count": int(row.provider_failed_count or 0),
        "provider_total_tokens": int(row.provider_total_tokens or 0),
        "provider_estimated_cost_usd": _coerce_decimal(row.provider_estimated_cost_usd),
    }


def _ingestion_duration_expression():
    return func.coalesce(
        TranscriptIngestionJob.source_audio_duration_seconds,
        TranscriptIngestionJob.declared_duration_seconds,
        0.0,
    )


def _ingestion_window_summary(db: Session, *, since, team_id: UUID | None) -> dict[str, int | float]:
    stmt = select(
        func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
        .label("ingestion_failed_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file)
        .label("whole_file_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk)
        .label("live_chunk_count"),
        func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
        func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
    ).where(TranscriptIngestionJob.created_at >= since)
    if team_id is not None:
        stmt = stmt.where(TranscriptIngestionJob.team_id == team_id)
    row = db.execute(stmt).one()
    return {
        "ingestion_job_count": int(row.ingestion_job_count or 0),
        "ingestion_failed_count": int(row.ingestion_failed_count or 0),
        "whole_file_count": int(row.whole_file_count or 0),
        "live_chunk_count": int(row.live_chunk_count or 0),
        "ingested_bytes": int(row.ingested_bytes or 0),
        "ingested_duration_seconds": float(row.ingested_duration_seconds or 0.0),
    }


def _team_usage_rows(db: Session, *, since, team_id: UUID | None) -> list[AdminUsageTeamRow]:
    rows_by_team: dict[UUID, AdminUsageTeamRow] = {}

    provider_stmt = (
        select(
            Team.id.label("team_id"),
            Team.name.label("team_name"),
            func.count(ProviderUsageEvent.id)
            .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
            .label("provider_completed_count"),
            func.count(ProviderUsageEvent.id)
            .filter(_failure_event_clause())
            .label("provider_failed_count"),
            func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
        )
        .join(Team, Team.id == ProviderUsageEvent.team_id)
        .where(ProviderUsageEvent.created_at >= since)
        .group_by(Team.id, Team.name)
        .order_by(Team.name.asc())
    )
    if team_id is not None:
        provider_stmt = provider_stmt.where(ProviderUsageEvent.team_id == team_id)
    for row in db.execute(provider_stmt):
        rows_by_team[row.team_id] = AdminUsageTeamRow(
            team_id=row.team_id,
            team_name=row.team_name,
            provider_completed_count=int(row.provider_completed_count or 0),
            provider_failed_count=int(row.provider_failed_count or 0),
            provider_total_tokens=int(row.provider_total_tokens or 0),
        )

    ingestion_stmt = (
        select(
            Team.id.label("team_id"),
            Team.name.label("team_name"),
            func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
            .label("ingestion_failed_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file)
            .label("whole_file_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk)
            .label("live_chunk_count"),
            func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
            func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
        )
        .join(Team, Team.id == TranscriptIngestionJob.team_id)
        .where(TranscriptIngestionJob.created_at >= since)
        .group_by(Team.id, Team.name)
        .order_by(Team.name.asc())
    )
    if team_id is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.team_id == team_id)
    for row in db.execute(ingestion_stmt):
        usage = rows_by_team.get(row.team_id)
        if usage is None:
            usage = AdminUsageTeamRow(team_id=row.team_id, team_name=row.team_name)
            rows_by_team[row.team_id] = usage
        usage.ingestion_job_count = int(row.ingestion_job_count or 0)
        usage.ingestion_failed_count = int(row.ingestion_failed_count or 0)
        usage.whole_file_count = int(row.whole_file_count or 0)
        usage.live_chunk_count = int(row.live_chunk_count or 0)
        usage.ingested_bytes = int(row.ingested_bytes or 0)
        usage.ingested_duration_seconds = float(row.ingested_duration_seconds or 0.0)

    return sorted(rows_by_team.values(), key=lambda row: row.team_name.lower())


def _user_usage_rows(db: Session, *, since, team_id: UUID) -> list[AdminUsageUserRow]:
    rows_by_user: dict[UUID, AdminUsageUserRow] = {}

    provider_stmt = (
        select(
            User.id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            func.count(ProviderUsageEvent.id)
            .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
            .label("provider_completed_count"),
            func.count(ProviderUsageEvent.id)
            .filter(_failure_event_clause())
            .label("provider_failed_count"),
            func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
        )
        .join(User, User.id == ProviderUsageEvent.owner_user_id)
        .where(
            ProviderUsageEvent.created_at >= since,
            ProviderUsageEvent.team_id == team_id,
        )
        .group_by(User.id, User.email, User.full_name)
        .order_by(User.email.asc())
    )
    for row in db.execute(provider_stmt):
        rows_by_user[row.user_id] = AdminUsageUserRow(
            user_id=row.user_id,
            email=row.email,
            full_name=row.full_name,
            provider_completed_count=int(row.provider_completed_count or 0),
            provider_failed_count=int(row.provider_failed_count or 0),
            provider_total_tokens=int(row.provider_total_tokens or 0),
        )

    ingestion_stmt = (
        select(
            User.id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
            .label("ingestion_failed_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file)
            .label("whole_file_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk)
            .label("live_chunk_count"),
            func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
            func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
        )
        .join(User, User.id == TranscriptIngestionJob.owner_user_id)
        .where(
            TranscriptIngestionJob.created_at >= since,
            TranscriptIngestionJob.team_id == team_id,
        )
        .group_by(User.id, User.email, User.full_name)
        .order_by(User.email.asc())
    )
    for row in db.execute(ingestion_stmt):
        usage = rows_by_user.get(row.user_id)
        if usage is None:
            usage = AdminUsageUserRow(user_id=row.user_id, email=row.email, full_name=row.full_name)
            rows_by_user[row.user_id] = usage
        usage.ingestion_job_count = int(row.ingestion_job_count or 0)
        usage.ingestion_failed_count = int(row.ingestion_failed_count or 0)
        usage.whole_file_count = int(row.whole_file_count or 0)
        usage.live_chunk_count = int(row.live_chunk_count or 0)
        usage.ingested_bytes = int(row.ingested_bytes or 0)
        usage.ingested_duration_seconds = float(row.ingested_duration_seconds or 0.0)

    return sorted(rows_by_user.values(), key=lambda row: row.email.lower())


def admin_usage_overview(db: Session, *, team_id: UUID | None = None) -> dict[str, object]:
    windows = []
    for label, delta in (("Last 24 hours", timedelta(hours=24)), ("Last 7 days", timedelta(days=7))):
        since = utcnow() - delta
        windows.append(
            AdminUsageWindowSummary(
                label=label,
                **_provider_usage_window_summary(db, since=since, team_id=team_id),
                **_ingestion_window_summary(db, since=since, team_id=team_id),
            )
        )
    seven_day_since = utcnow() - timedelta(days=7)
    selected_team = db.get(Team, team_id) if team_id is not None else None
    return {
        "usage_window_summaries": windows,
        "usage_team_rows": _team_usage_rows(db, since=seven_day_since, team_id=team_id),
        "usage_user_rows": _user_usage_rows(db, since=seven_day_since, team_id=team_id) if team_id is not None else [],
        "usage_scope_team": selected_team,
    }


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
        db.flush()
        if not user.is_system_admin:
            ensure_user_dek(db, user=user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    except Exception:
        db.rollback()
        raise
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

    stt_configs_created = db.scalars(select(TeamSttConfig).where(TeamSttConfig.created_by_user_id == user.id))
    for config in stt_configs_created:
        config.created_by_user_id = actor.id
        db.add(config)

    stt_configs_updated = db.scalars(select(TeamSttConfig).where(TeamSttConfig.updated_by_user_id == user.id))
    for config in stt_configs_updated:
        config.updated_by_user_id = actor.id
        db.add(config)

    stt_selections = db.scalars(select(TeamSttSelection).where(TeamSttSelection.selected_by_user_id == user.id))
    for selection in stt_selections:
        selection.selected_by_user_id = actor.id
        db.add(selection)

    team_templates_created = db.scalars(select(PromptTemplate).where(PromptTemplate.scope == TemplateScope.team, PromptTemplate.created_by_user_id == user.id))
    for template in team_templates_created:
        template.created_by_user_id = actor.id
        db.add(template)

    personal_templates = db.scalars(select(PromptTemplate).where(PromptTemplate.scope == TemplateScope.user, PromptTemplate.owner_user_id == user.id))
    for template in personal_templates:
        db.delete(template)

    team_quick_actions_created = db.scalars(select(QuickAction).where(QuickAction.scope == TemplateScope.team, QuickAction.created_by_user_id == user.id))
    for quick_action in team_quick_actions_created:
        quick_action.created_by_user_id = actor.id
        db.add(quick_action)

    personal_quick_actions = db.scalars(select(QuickAction).where(QuickAction.scope == TemplateScope.user, QuickAction.owner_user_id == user.id))
    for quick_action in personal_quick_actions:
        db.delete(quick_action)

    transcripts = db.scalars(select(Transcript).where(Transcript.owner_user_id == user.id))
    transcript_rows = list(transcripts)
    delete_retry_sources_for_transcripts(db, transcript_ids=[transcript.id for transcript in transcript_rows])
    for transcript in transcript_rows:
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
        db.flush()
        ensure_user_dek(db, user=user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    except Exception:
        db.rollback()
        raise
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
