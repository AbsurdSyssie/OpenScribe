from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import SecurityAuditEvent, utcnow


AUTH_FAILURE_ACTIONS = {"login_failure", "mfa_challenge_failure", "auth_email_token_failure"}
ACCESS_DENIAL_ACTIONS = {"access_denied"}
ABUSE_SIGNAL_ACTIONS = {"csrf_rejected", "rate_limit_exceeded", "security_validation_rejected"}
HIGH_RISK_ADMIN_ACTIONS = {
    "user_created",
    "account_suspended",
    "account_reactivated",
    "account_deleted",
    "team_created",
    "team_deleted",
    "team_delete_blocked",
    "break_glass_password_reset_generated",
    "break_glass_account_recovery_generated",
}
PROVIDER_CHANGE_ACTION_SUFFIXES = (
    "_config_created",
    "_config_updated",
    "_config_deleted",
    "_config_finalized",
    "_config_credential_replaced",
    "_selection_set",
    "_selection_cleared",
    "_provider_assigned",
    "_provider_assignment_removed",
    "_provider_deleted",
)
DESTRUCTIVE_ACTIONS = {"account_deleted", "team_deleted", "transcript_root_deleted", "generated_document_deleted"}
AUDIT_EVENT_DETAIL_ALLOWLIST = {
    "auth_level",
    "available_model_count",
    "category",
    "count",
    "credential_status",
    "deleted_count",
    "discovery_status",
    "duration_seconds",
    "entity_count",
    "flow",
    "generator_type",
    "job_kind",
    "method",
    "object_id",
    "object_ids",
    "object_type",
    "outcome",
    "preferred_model_set",
    "provider_type",
    "reason_code",
    "route",
    "scope",
    "setup_status",
    "source_audio_size_bytes",
    "status_code",
    "team_user_count",
    "trusted_device_created",
    "trusted_device_used",
}
MASKED_INTERNAL_IP_LABEL = "Private/internal IP masked"
MAX_AUDIT_LOOKBACK = timedelta(days=30)


@dataclass(frozen=True)
class AuditSignal:
    signal: str
    severity: str
    count: int
    key: str
    action: str | None = None
    route: str | None = None
    actor_user_id: str | None = None
    team_id: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    note: str | None = None


def parse_since(value: str | None) -> datetime:
    earliest = utcnow() - MAX_AUDIT_LOOKBACK
    if not value:
        return utcnow() - timedelta(hours=24)
    text = value.strip().lower()
    if text.endswith("h") and text[:-1].isdigit():
        return max(utcnow() - timedelta(hours=int(text[:-1])), earliest)
    if text.endswith("d") and text[:-1].isdigit():
        return max(utcnow() - timedelta(days=int(text[:-1])), earliest)
    parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
    since = parsed if parsed.tzinfo else parsed.replace(tzinfo=utcnow().tzinfo)
    return max(since, earliest)


def _string_id(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _details(event: SecurityAuditEvent) -> dict[str, Any]:
    return dict(event.details_json or {})


def _audit_signal_display_key(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.is_private or address.is_loopback or address.is_link_local:
        return MASKED_INTERNAL_IP_LABEL
    return value


def _count_by_value(db: Session, *, since: datetime, value: Any) -> dict[str, int]:
    rows = db.execute(
        select(value.label("value"), func.count().label("count"))
        .where(SecurityAuditEvent.created_at >= since)
        .group_by(value)
        .order_by(value.asc())
    )
    return {str(row.value): int(row.count) for row in rows}


def summarize_security_audit_events(
    db: Session,
    *,
    since: datetime,
    login_failure_threshold: int = 5,
    access_denied_threshold: int = 5,
    csrf_threshold: int = 5,
    validation_threshold: int = 3,
) -> dict[str, Any]:
    signals: list[AuditSignal] = []
    subject_hash = SecurityAuditEvent.details_json["subject_hash"].as_string()
    route = func.coalesce(func.nullif(SecurityAuditEvent.details_json["route"].as_string(), ""), "unknown-route")
    actor_key = func.coalesce(
        cast(SecurityAuditEvent.actor_user_id, String),
        func.nullif(SecurityAuditEvent.request_ip, ""),
        "anonymous",
    )
    category = func.coalesce(
        func.nullif(SecurityAuditEvent.details_json["category"].as_string(), ""),
        "uncategorized",
    )
    outcome = func.coalesce(
        func.nullif(SecurityAuditEvent.details_json["outcome"].as_string(), ""),
        "unknown",
    )
    group_fields = (
        func.count().label("count"),
        func.min(SecurityAuditEvent.created_at).label("first_seen"),
        func.max(SecurityAuditEvent.created_at).label("last_seen"),
    )

    subject_rows = db.execute(
        select(subject_hash.label("key"), *group_fields)
        .where(
            SecurityAuditEvent.created_at >= since,
            SecurityAuditEvent.action.in_(AUTH_FAILURE_ACTIONS),
            subject_hash.is_not(None),
            subject_hash != "",
        )
        .group_by(subject_hash)
    )
    for row in subject_rows:
        if row.count >= login_failure_threshold:
            signals.append(
                AuditSignal(
                    signal="auth_failure_burst_by_subject",
                    severity="medium",
                    count=row.count,
                    key=row.key,
                    action="login_failure",
                    first_seen=row.first_seen.isoformat(),
                    last_seen=row.last_seen.isoformat(),
                    note="Repeated authentication failures for same normalized subject hash.",
                )
            )

    ip_action_rows = db.execute(
        select(SecurityAuditEvent.request_ip.label("key"), SecurityAuditEvent.action, *group_fields)
        .where(
            SecurityAuditEvent.created_at >= since,
            SecurityAuditEvent.request_ip.is_not(None),
            SecurityAuditEvent.request_ip != "",
            SecurityAuditEvent.action.in_(AUTH_FAILURE_ACTIONS | ABUSE_SIGNAL_ACTIONS),
        )
        .group_by(SecurityAuditEvent.request_ip, SecurityAuditEvent.action)
    )
    for row in ip_action_rows:
        threshold = (
            validation_threshold
            if row.action == "security_validation_rejected"
            else csrf_threshold
            if row.action == "csrf_rejected"
            else login_failure_threshold
        )
        if row.count >= threshold:
            signals.append(
                AuditSignal(
                    signal="security_event_burst_by_ip",
                    severity="medium" if row.action != "rate_limit_exceeded" else "high",
                    count=row.count,
                    key=row.key,
                    action=row.action,
                    first_seen=row.first_seen.isoformat(),
                    last_seen=row.last_seen.isoformat(),
                )
            )

    action_rows = db.execute(
        select(SecurityAuditEvent.action, *group_fields)
        .where(SecurityAuditEvent.created_at >= since, SecurityAuditEvent.action.in_(ABUSE_SIGNAL_ACTIONS))
        .group_by(SecurityAuditEvent.action)
    )
    for row in action_rows:
        threshold = (
            validation_threshold
            if row.action == "security_validation_rejected"
            else csrf_threshold
            if row.action == "csrf_rejected"
            else 1
        )
        if row.count >= threshold:
            signals.append(
                AuditSignal(
                    signal="security_event_burst",
                    severity="medium" if row.action != "rate_limit_exceeded" else "high",
                    count=row.count,
                    key=row.action,
                    action=row.action,
                    first_seen=row.first_seen.isoformat(),
                    last_seen=row.last_seen.isoformat(),
                )
            )

    access_rows = db.execute(
        select(actor_key.label("key"), route.label("route"), *group_fields)
        .where(SecurityAuditEvent.created_at >= since, SecurityAuditEvent.action.in_(ACCESS_DENIAL_ACTIONS))
        .group_by(actor_key, route)
    )
    for row in access_rows:
        if row.count >= access_denied_threshold:
            signals.append(
                AuditSignal(
                    signal="access_denied_burst_by_actor_route",
                    severity="medium",
                    count=row.count,
                    key=row.key,
                    action="access_denied",
                    route=row.route,
                    first_seen=row.first_seen.isoformat(),
                    last_seen=row.last_seen.isoformat(),
                    note="Repeated denied access for same actor/IP and route.",
                )
            )

    high_risk_rows = db.execute(
        select(
            SecurityAuditEvent.id,
            SecurityAuditEvent.action,
            SecurityAuditEvent.actor_user_id,
            SecurityAuditEvent.team_id,
            SecurityAuditEvent.details_json["route"].as_string().label("route"),
            SecurityAuditEvent.created_at,
        )
        .where(
            SecurityAuditEvent.created_at >= since,
            SecurityAuditEvent.action.in_(HIGH_RISK_ADMIN_ACTIONS | DESTRUCTIVE_ACTIONS),
        )
        .order_by(SecurityAuditEvent.created_at.desc(), SecurityAuditEvent.id.desc())
    )
    for row in high_risk_rows:
        signals.append(
            AuditSignal(
                signal="high_risk_admin_or_destructive_action",
                severity="high" if row.action in DESTRUCTIVE_ACTIONS else "medium",
                count=1,
                key=str(row.id),
                action=row.action,
                route=row.route,
                actor_user_id=_string_id(row.actor_user_id),
                team_id=_string_id(row.team_id),
                first_seen=row.created_at.isoformat(),
                last_seen=row.created_at.isoformat(),
            )
        )

    provider_prefixes = ("stt_", "llm_", "deidentification_", "clinical_nlp_", "hallucination_check_")
    provider_change_filter = and_(
        or_(*(SecurityAuditEvent.action.startswith(prefix) for prefix in provider_prefixes)),
        or_(*(SecurityAuditEvent.action.endswith(suffix) for suffix in PROVIDER_CHANGE_ACTION_SUFFIXES)),
    )
    provider_rows = db.execute(
        select(
            SecurityAuditEvent.id,
            SecurityAuditEvent.action,
            SecurityAuditEvent.actor_user_id,
            SecurityAuditEvent.team_id,
            SecurityAuditEvent.created_at,
        )
        .where(SecurityAuditEvent.created_at >= since, provider_change_filter)
        .order_by(SecurityAuditEvent.created_at.desc(), SecurityAuditEvent.id.desc())
    )
    for row in provider_rows:
        signals.append(
            AuditSignal(
                signal="provider_configuration_change",
                severity="medium",
                count=1,
                key=str(row.id),
                action=row.action,
                actor_user_id=_string_id(row.actor_user_id),
                team_id=_string_id(row.team_id),
                first_seen=row.created_at.isoformat(),
                last_seen=row.created_at.isoformat(),
            )
        )

    action_counts = _count_by_value(db, since=since, value=SecurityAuditEvent.action)
    category_counts = _count_by_value(db, since=since, value=category)
    outcome_counts = _count_by_value(db, since=since, value=outcome)

    return {
        "since": since.isoformat(),
        "event_count": sum(action_counts.values()),
        "action_counts": action_counts,
        "category_counts": category_counts,
        "outcome_counts": outcome_counts,
        "signals": [
            {**asdict(signal), "display_key": _audit_signal_display_key(signal.key)}
            for signal in signals
        ],
    }


def audit_event_safe_details(event: SecurityAuditEvent) -> dict[str, Any]:
    details = _details(event)
    return {key: details[key] for key in sorted(AUDIT_EVENT_DETAIL_ALLOWLIST) if key in details}


def audit_event_display_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "Unrecognized origin"
    if address.is_private or address.is_loopback or address.is_link_local:
        return MASKED_INTERNAL_IP_LABEL
    return value


def list_security_audit_events(
    db: Session,
    *,
    since: datetime,
    limit: int = 100,
    action: str | None = None,
    category: str | None = None,
    outcome: str | None = None,
    request_ip: str | None = None,
    team_id: UUID | None = None,
    actor_user_id: UUID | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(limit, 250))
    statement = (
        select(SecurityAuditEvent)
        .options(joinedload(SecurityAuditEvent.actor), joinedload(SecurityAuditEvent.target), joinedload(SecurityAuditEvent.team))
        .where(SecurityAuditEvent.created_at >= since)
    )
    if action:
        statement = statement.where(SecurityAuditEvent.action == action)
    if request_ip:
        statement = statement.where(SecurityAuditEvent.request_ip == request_ip)
    if team_id:
        statement = statement.where(SecurityAuditEvent.team_id == team_id)
    if actor_user_id:
        statement = statement.where(SecurityAuditEvent.actor_user_id == actor_user_id)
    if category:
        statement = statement.where(SecurityAuditEvent.details_json["category"].as_string() == category)
    if outcome:
        statement = statement.where(SecurityAuditEvent.details_json["outcome"].as_string() == outcome)
    events = list(
        db.scalars(
            statement.order_by(SecurityAuditEvent.created_at.desc(), SecurityAuditEvent.id.desc()).limit(bounded_limit)
        )
    )
    return [
        {
            "id": str(event.id),
            "created_at": event.created_at.isoformat(),
            "action": event.action,
            "actor_user_id": _string_id(event.actor_user_id),
            "actor_email": event.actor.email if event.actor else None,
            "target_user_id": _string_id(event.target_user_id),
            "target_email": event.target.email if event.target else None,
            "team_id": _string_id(event.team_id),
            "team_name": event.team.name if event.team else None,
            "request_ip": event.request_ip,
            "display_request_ip": audit_event_display_ip(event.request_ip),
            "user_agent": event.user_agent[:160] if event.user_agent else None,
            "details": audit_event_safe_details(event),
        }
        for event in events
    ]


def _distinct_audit_detail_values(db: Session, *, key: str, default: str) -> list[str]:
    value = func.coalesce(func.nullif(SecurityAuditEvent.details_json[key].as_string(), ""), default)
    return list(db.scalars(select(value).distinct().order_by(value.asc())))


def audit_filter_options(db: Session) -> dict[str, list[str]]:
    return {
        "actions": list(db.scalars(select(SecurityAuditEvent.action).distinct().order_by(SecurityAuditEvent.action.asc()))),
        "categories": _distinct_audit_detail_values(db, key="category", default="uncategorized"),
        "outcomes": _distinct_audit_detail_values(db, key="outcome", default="unknown"),
        "request_ips": [
            {"value": value, "label": value}
            for value in db.scalars(
                select(SecurityAuditEvent.request_ip)
                .where(SecurityAuditEvent.request_ip.is_not(None))
                .distinct()
                .order_by(SecurityAuditEvent.request_ip.asc())
            )
            if value and audit_event_display_ip(value) == value
        ],
    }
