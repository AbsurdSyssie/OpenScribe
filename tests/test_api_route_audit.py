from app.api_route_audit import AuditScenario, missing_route_specs, run_negative_audit
from app.models import SessionAuthLevel, TeamRole, UserOnboardingState
from app.services.auth import create_session


def test_api_route_audit_manifest_covers_every_api_route():
    assert missing_route_specs() == set()


def test_api_route_audit_negative_expectations_hold(client, db_session, make_team, make_user):
    team = make_team(name="Audit Team")
    onboarding_user = make_user(
        email="onboarding@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        onboarding_state=UserOnboardingState.pending_password_change,
        must_change_password=True,
        mfa_required=True,
        mfa_enabled=False,
    )
    pending_mfa_user = make_user(
        email="pending.mfa@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=True,
        mfa_enabled=True,
    )
    full_user = make_user(
        email="full.user@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=False,
        mfa_enabled=False,
    )
    leader = make_user(
        email="leader@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=False,
        mfa_enabled=False,
    )
    admin = make_user(
        email="admin@example.com",
        password="password-1",
        is_system_admin=True,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=False,
        mfa_enabled=False,
    )

    scenarios = {
        "anonymous": AuditScenario(name="anonymous"),
        "invalid_cookie": AuditScenario(name="invalid_cookie", session_cookie="invalid-session-cookie"),
        "onboarding": AuditScenario(name="onboarding", session_cookie=create_session(db_session, onboarding_user)),
        "pending_mfa": AuditScenario(name="pending_mfa", session_cookie=create_session(db_session, pending_mfa_user, auth_level=SessionAuthLevel.pending_mfa)),
        "full_user": AuditScenario(name="full_user", session_cookie=create_session(db_session, full_user)),
        "leader": AuditScenario(name="leader", session_cookie=create_session(db_session, leader)),
        "admin": AuditScenario(name="admin", session_cookie=create_session(db_session, admin)),
    }

    results = run_negative_audit(client, scenarios)

    failures = [
        f"{result.case.method} {result.case.path} {observation.scenario} -> "
        f"{observation.status_code}/{observation.error_code}"
        for result in results
        for observation in result.observations
        if not observation.ok
    ]
    assert failures == []
