from datetime import timedelta
from uuid import uuid4

from sqlalchemy import event, insert

from app.models import SecurityAuditEvent, TeamRole, utcnow
from app.schemas import UserCreate
from app.services.admin import create_user, delete_team
from app.services.audit_detection import (
    audit_filter_options,
    list_security_audit_events,
    parse_since,
    summarize_security_audit_events,
)
from app.services.security_audit import audit_subject_hash, record_security_event


def test_audit_detection_flags_auth_access_abuse_and_admin_signals(db_session, make_user, make_team):
    team = make_team(name="Detection Team")
    admin = make_user(email="detection-admin@example.com", is_system_admin=True)
    user = make_user(email="detection-user@example.com", team=team, mfa_required=False, mfa_enabled=False)

    for _ in range(5):
        record_security_event(
            db_session,
            action="login_failure",
            details={
                "category": "auth",
                "outcome": "failure",
                "subject_hash": audit_subject_hash("detection-user@example.com"),
            },
        )
    for _ in range(5):
        record_security_event(
            db_session,
            action="access_denied",
            actor=user,
            request=None,
            details={"category": "access_control", "outcome": "denied", "route": "/api/v1/teams", "reason_code": "system_admin_required"},
        )
    create_user(
        db_session,
        UserCreate(
            full_name="Detection Managed",
            email="detection-managed@example.com",
            temporary_password="temporary-password-1",
            team_id=team.id,
            team_role=TeamRole.user,
            is_system_admin=False,
        ),
        actor=admin,
    )
    record_security_event(
        db_session,
        action="llm_config_updated",
        actor=admin,
        team_id=team.id,
        details={"category": "provider", "outcome": "success", "provider_type": "llm"},
    )

    report = summarize_security_audit_events(db_session, since=utcnow().replace(year=utcnow().year - 1))
    signals = {(signal["signal"], signal["action"]) for signal in report["signals"]}

    assert ("auth_failure_burst_by_subject", "login_failure") in signals
    assert ("access_denied_burst_by_actor_route", "access_denied") in signals
    assert ("high_risk_admin_or_destructive_action", "user_created") in signals
    assert ("provider_configuration_change", "llm_config_updated") in signals
    assert report["action_counts"]["login_failure"] == 5


def test_audit_detection_flags_rate_limit_validation_and_team_delete_blocker(db_session, make_user, make_team):
    team = make_team(name="Detection Blocked Team")
    admin = make_user(email="detection-delete-admin@example.com", is_system_admin=True)
    linked_admin = make_user(email="detection-linked-admin@example.com", is_system_admin=True)
    linked_admin.team_id = team.id
    db_session.add(linked_admin)
    db_session.commit()

    for _ in range(2):
        record_security_event(
            db_session,
            action="rate_limit_exceeded",
            details={"category": "rate_limit", "outcome": "blocked"},
        )
    for _ in range(3):
        record_security_event(
            db_session,
            action="security_validation_rejected",
            details={"category": "validation", "outcome": "blocked", "reason_code": "security_relevant_validation"},
        )
    try:
        delete_team(db_session, admin, team_id=team.id)
    except Exception:
        pass

    report = summarize_security_audit_events(db_session, since=utcnow().replace(year=utcnow().year - 1), validation_threshold=3)
    signals = {(signal["signal"], signal["action"]) for signal in report["signals"]}

    assert ("security_event_burst_by_ip", "rate_limit_exceeded") not in signals
    assert ("security_event_burst", "rate_limit_exceeded") in signals
    assert ("security_event_burst", "security_validation_rejected") in signals
    assert ("high_risk_admin_or_destructive_action", "team_delete_blocked") in signals
    assert report["action_counts"]["security_validation_rejected"] == 3


def test_audit_detection_summary_covers_full_window_above_previous_cap(db_session):
    now = utcnow()
    db_session.add(SecurityAuditEvent(action="account_deleted", created_at=now - timedelta(minutes=2)))
    db_session.execute(
        insert(SecurityAuditEvent),
        [
            {
                "id": uuid4(),
                "action": "newer_benign_event",
                "details_json": {"category": "system", "outcome": "success"},
                "created_at": now - timedelta(minutes=1),
            }
            for _ in range(10_000)
        ],
    )
    db_session.commit()

    report = summarize_security_audit_events(db_session, since=now - timedelta(hours=1))

    assert report["event_count"] == 10_001
    assert report["action_counts"] == {"account_deleted": 1, "newer_benign_event": 10_000}
    assert report["category_counts"] == {"system": 10_000, "uncategorized": 1}
    assert report["outcome_counts"] == {"success": 10_000, "unknown": 1}
    assert any(
        signal["signal"] == "high_risk_admin_or_destructive_action"
        and signal["action"] == "account_deleted"
        for signal in report["signals"]
    )


def test_parse_since_accepts_relative_and_iso_values():
    assert parse_since("24h") < utcnow()
    assert parse_since("7d") < utcnow()
    assert parse_since("2026-06-15T12:00:00+00:00").isoformat() == "2026-06-15T12:00:00+00:00"


def test_parse_since_clamps_extreme_lookback():
    assert parse_since("3650d") > utcnow().replace(year=utcnow().year - 1)
    assert parse_since("999999999999h") > utcnow().replace(year=utcnow().year - 1)


def test_audit_event_listing_filters_category_and_outcome_in_query(db_session):
    record_security_event(db_session, action="audit_filter_probe", details={"category": "auth", "outcome": "success"})
    record_security_event(db_session, action="audit_filter_probe", details={"category": "provider", "outcome": "failure"})

    events = list_security_audit_events(
        db_session,
        since=utcnow().replace(year=utcnow().year - 1),
        action="audit_filter_probe",
        category="provider",
        outcome="failure",
    )

    assert len(events) == 1
    assert events[0]["details"]["category"] == "provider"
    assert events[0]["details"]["outcome"] == "failure"


def test_audit_event_listing_includes_actor_and_target_email_for_display(db_session, make_team, make_user):
    team = make_team(name="Audit Display Identity Team")
    actor = make_user(email="audit-list-actor@example.com", team=team)
    target = make_user(email="audit-list-target@example.com", team=team)
    record_security_event(
        db_session,
        action="audit_display_identity_probe",
        actor=actor,
        target=target,
        details={"category": "auth", "outcome": "success"},
    )

    events = list_security_audit_events(
        db_session,
        since=utcnow().replace(year=utcnow().year - 1),
        action="audit_display_identity_probe",
    )

    assert len(events) == 1
    assert events[0]["actor_user_id"] == str(actor.id)
    assert events[0]["actor_email"] == "audit-list-actor@example.com"
    assert events[0]["target_user_id"] == str(target.id)
    assert events[0]["target_email"] == "audit-list-target@example.com"
    assert events[0]["team_id"] == str(team.id)
    assert events[0]["team_name"] == "Audit Display Identity Team"


def test_audit_filter_options_select_distinct_json_values_in_sql(db_session):
    record_security_event(
        db_session,
        action="audit_filter_auth",
        details={"category": "auth", "outcome": "success"},
    )
    record_security_event(
        db_session,
        action="audit_filter_auth_repeat",
        details={"category": "auth", "outcome": "success"},
    )
    record_security_event(
        db_session,
        action="audit_filter_provider",
        details={"category": "provider", "outcome": "failure"},
    )
    record_security_event(db_session, action="audit_filter_defaults", details={})
    record_security_event(
        db_session,
        action="audit_filter_empty_defaults",
        details={"category": "", "outcome": ""},
    )

    statements: list[str] = []
    bind = db_session.get_bind()

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        options = audit_filter_options(db_session, since=utcnow().replace(year=utcnow().year - 1))
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert options["categories"] == ["auth", "provider", "uncategorized"]
    assert options["outcomes"] == ["failure", "success", "unknown"]
    json_option_queries = [statement for statement in statements if "details_json" in statement]
    assert len(json_option_queries) == 2
    assert all("DISTINCT" in statement.upper() for statement in json_option_queries)


def test_audit_filter_options_are_windowed_and_limited(db_session):
    now = utcnow()
    db_session.add_all(
        [
            SecurityAuditEvent(
                action="old_action",
                request_ip="1.1.1.1",
                details_json={"category": "old_category", "outcome": "old_outcome"},
                created_at=now - timedelta(days=2),
            ),
            SecurityAuditEvent(
                action="recent_action_a",
                request_ip="2.2.2.2",
                details_json={"category": "recent_category_a", "outcome": "recent_outcome_a"},
                created_at=now - timedelta(minutes=2),
            ),
            SecurityAuditEvent(
                action="recent_action_b",
                request_ip="3.3.3.3",
                details_json={"category": "recent_category_b", "outcome": "recent_outcome_b"},
                created_at=now - timedelta(minutes=1),
            ),
            SecurityAuditEvent(
                action="recent_action_c",
                request_ip="4.4.4.4",
                details_json={"category": "recent_category_c", "outcome": "recent_outcome_c"},
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    options = audit_filter_options(db_session, since=now - timedelta(hours=1), limit=2)

    assert len(options["actions"]) == 2
    assert len(options["categories"]) == 2
    assert len(options["outcomes"]) == 2
    assert len(options["request_ips"]) == 2
    assert "old_action" not in options["actions"]
    assert "old_category" not in options["categories"]
    assert "old_outcome" not in options["outcomes"]
    assert {option["value"] for option in options["request_ips"]}.isdisjoint({"1.1.1.1"})
