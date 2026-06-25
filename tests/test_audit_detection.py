from app.models import TeamRole, utcnow
from app.schemas import UserCreate
from app.services.admin import create_user, delete_team
from app.services.audit_detection import parse_since, summarize_security_audit_events
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


def test_parse_since_accepts_relative_and_iso_values():
    assert parse_since("24h") < utcnow()
    assert parse_since("7d") < utcnow()
    assert parse_since("2026-06-15T12:00:00+00:00").isoformat() == "2026-06-15T12:00:00+00:00"
