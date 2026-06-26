from __future__ import annotations

import ipaddress
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

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
MAX_SUMMARY_EVENTS = 10000


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


def _route(event: SecurityAuditEvent) -> str | None:
    value = _details(event).get("route")
    return str(value) if value else None


def _subject_hash(event: SecurityAuditEvent) -> str | None:
    value = _details(event).get("subject_hash")
    return str(value) if value else None


def _event_bounds(events: list[SecurityAuditEvent]) -> tuple[str | None, str | None]:
    if not events:
        return None, None
    ordered = sorted(events, key=lambda item: item.created_at)
    return ordered[0].created_at.isoformat(), ordered[-1].created_at.isoformat()


def _provider_change(action: str) -> bool:
    return action.startswith(("stt_", "llm_", "deidentification_", "clinical_nlp_", "hallucination_check_")) and action.endswith(PROVIDER_CHANGE_ACTION_SUFFIXES)


def _audit_signal_display_key(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.is_private or address.is_loopback or address.is_link_local:
        return MASKED_INTERNAL_IP_LABEL
    return value


def _fetch_events(db: Session, *, since: datetime) -> list[SecurityAuditEvent]:
    return list(
        db.scalars(
            select(SecurityAuditEvent)
            .where(SecurityAuditEvent.created_at >= since)
            .order_by(SecurityAuditEvent.created_at.asc(), SecurityAuditEvent.id.asc())
            .limit(MAX_SUMMARY_EVENTS)
        )
    )


def summarize_security_audit_events(
    db: Session,
    *,
    since: datetime,
    login_failure_threshold: int = 5,
    access_denied_threshold: int = 5,
    csrf_threshold: int = 5,
    validation_threshold: int = 3,
) -> dict[str, Any]:
    events = _fetch_events(db, since=since)
    signals: list[AuditSignal] = []

    by_subject: dict[str, list[SecurityAuditEvent]] = defaultdict(list)
    by_ip_action: dict[tuple[str, str], list[SecurityAuditEvent]] = defaultdict(list)
    by_action: dict[str, list[SecurityAuditEvent]] = defaultdict(list)
    by_actor_access: dict[tuple[str, str], list[SecurityAuditEvent]] = defaultdict(list)
    by_route_access: dict[tuple[str, str], list[SecurityAuditEvent]] = defaultdict(list)

    for event in events:
        details = _details(event)
        subject_hash = _subject_hash(event)
        if event.action in AUTH_FAILURE_ACTIONS and subject_hash:
            by_subject[subject_hash].append(event)
        if event.request_ip and event.action in AUTH_FAILURE_ACTIONS | ABUSE_SIGNAL_ACTIONS:
            by_ip_action[(event.request_ip, event.action)].append(event)
        if event.action in ABUSE_SIGNAL_ACTIONS:
            by_action[event.action].append(event)
        if event.action in ACCESS_DENIAL_ACTIONS:
            actor_key = _string_id(event.actor_user_id) or event.request_ip or "anonymous"
            route_key = _route(event) or "unknown-route"
            by_actor_access[(actor_key, route_key)].append(event)
            by_route_access[(route_key, str(details.get("reason_code") or "unknown"))].append(event)

    for subject_hash, group in by_subject.items():
        if len(group) >= login_failure_threshold:
            first_seen, last_seen = _event_bounds(group)
            signals.append(
                AuditSignal(
                    signal="auth_failure_burst_by_subject",
                    severity="medium",
                    count=len(group),
                    key=subject_hash,
                    action="login_failure",
                    first_seen=first_seen,
                    last_seen=last_seen,
                    note="Repeated authentication failures for same normalized subject hash.",
                )
            )

    for (ip, action), group in by_ip_action.items():
        threshold = validation_threshold if action == "security_validation_rejected" else csrf_threshold if action == "csrf_rejected" else login_failure_threshold
        if len(group) >= threshold:
            first_seen, last_seen = _event_bounds(group)
            signals.append(
                AuditSignal(
                    signal="security_event_burst_by_ip",
                    severity="medium" if action != "rate_limit_exceeded" else "high",
                    count=len(group),
                    key=ip,
                    action=action,
                    first_seen=first_seen,
                    last_seen=last_seen,
                )
            )

    for action, group in by_action.items():
        threshold = validation_threshold if action == "security_validation_rejected" else csrf_threshold if action == "csrf_rejected" else 1
        if len(group) >= threshold:
            first_seen, last_seen = _event_bounds(group)
            signals.append(
                AuditSignal(
                    signal="security_event_burst",
                    severity="medium" if action != "rate_limit_exceeded" else "high",
                    count=len(group),
                    key=action,
                    action=action,
                    first_seen=first_seen,
                    last_seen=last_seen,
                )
            )

    for (actor_key, route_key), group in by_actor_access.items():
        if len(group) >= access_denied_threshold:
            first_seen, last_seen = _event_bounds(group)
            signals.append(
                AuditSignal(
                    signal="access_denied_burst_by_actor_route",
                    severity="medium",
                    count=len(group),
                    key=actor_key,
                    action="access_denied",
                    route=route_key,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    note="Repeated denied access for same actor/IP and route.",
                )
            )

    high_risk_events = [event for event in events if event.action in HIGH_RISK_ADMIN_ACTIONS or event.action in DESTRUCTIVE_ACTIONS]
    for event in high_risk_events:
        signals.append(
            AuditSignal(
                signal="high_risk_admin_or_destructive_action",
                severity="high" if event.action in DESTRUCTIVE_ACTIONS else "medium",
                count=1,
                key=str(event.id),
                action=event.action,
                route=_route(event),
                actor_user_id=_string_id(event.actor_user_id),
                team_id=_string_id(event.team_id),
                first_seen=event.created_at.isoformat(),
                last_seen=event.created_at.isoformat(),
            )
        )

    provider_events = [event for event in events if _provider_change(event.action)]
    for event in provider_events:
        signals.append(
            AuditSignal(
                signal="provider_configuration_change",
                severity="medium",
                count=1,
                key=str(event.id),
                action=event.action,
                actor_user_id=_string_id(event.actor_user_id),
                team_id=_string_id(event.team_id),
                first_seen=event.created_at.isoformat(),
                last_seen=event.created_at.isoformat(),
            )
        )

    action_counts = Counter(event.action for event in events)
    category_counts = Counter(str(_details(event).get("category") or "uncategorized") for event in events)
    outcome_counts = Counter(str(_details(event).get("outcome") or "unknown") for event in events)

    return {
        "since": since.isoformat(),
        "event_count": len(events),
        "action_counts": dict(sorted(action_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
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
    statement = select(SecurityAuditEvent).where(SecurityAuditEvent.created_at >= since)
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
            "target_user_id": _string_id(event.target_user_id),
            "team_id": _string_id(event.team_id),
            "request_ip": event.request_ip,
            "display_request_ip": audit_event_display_ip(event.request_ip),
            "user_agent": event.user_agent[:160] if event.user_agent else None,
            "details": audit_event_safe_details(event),
        }
        for event in events
    ]


def audit_filter_options(db: Session) -> dict[str, list[str]]:
    rows = list(db.scalars(select(SecurityAuditEvent.details_json)))
    categories = {str(row.get("category") or "uncategorized") for row in rows if isinstance(row, dict)}
    outcomes = {str(row.get("outcome") or "unknown") for row in rows if isinstance(row, dict)}
    return {
        "actions": list(db.scalars(select(SecurityAuditEvent.action).distinct().order_by(SecurityAuditEvent.action.asc()))),
        "categories": sorted(categories),
        "outcomes": sorted(outcomes),
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
