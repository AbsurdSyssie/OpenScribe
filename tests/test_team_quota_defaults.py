import pytest

from app.errors import AppError
from app.models import TeamRole
from app.schemas import AccountRequestApprove, TeamCreate, UserCreate
from app.services.admin import approve_account_request, create_team, create_user, update_team_quota_defaults
from app.services.local_demo import ensure_local_demo_user


def test_new_teams_start_with_finite_quota_defaults(db_session, make_user):
    admin = make_user(email="quota-defaults-team-admin@example.com", is_system_admin=True)
    team = create_team(db_session, TeamCreate(name="Finite defaults team"), actor=admin)

    assert (
        team.default_daily_token_limit,
        team.default_monthly_token_limit,
        team.default_daily_audio_seconds_limit,
        team.default_monthly_audio_seconds_limit,
    ) == (100_000, 1_000_000, 28_800, 259_200)


def test_new_local_demo_member_uses_team_quota_defaults(db_session, make_team):
    team = make_team(name="Local demo quota team")
    demo_user = ensure_local_demo_user(
        db_session,
        full_name="Demo clinician",
        email="demo-quota@example.test",
        password="DemoPassword1",
        team=team,
        team_role=TeamRole.user,
        is_system_admin=False,
    )

    assert (
        demo_user.daily_token_limit,
        demo_user.monthly_token_limit,
        demo_user.daily_audio_seconds_limit,
        demo_user.monthly_audio_seconds_limit,
    ) == (100_000, 1_000_000, 28_800, 259_200)


def test_team_quota_defaults_are_snapshotted_for_created_and_approved_users(
    db_session, make_team, make_user, make_account_request
):
    team = make_team(name="Quota snapshot team")
    admin = make_user(email="quota-snapshot-admin@example.com", is_system_admin=True)
    team.default_daily_token_limit = 12_345
    team.default_monthly_token_limit = 234_567
    team.default_daily_audio_seconds_limit = 0
    team.default_monthly_audio_seconds_limit = 32_400
    db_session.commit()

    created = create_user(
        db_session,
        UserCreate(
            email="quota-snapshot-created@example.com",
            temporary_password="TempPass1",
            team_id=team.id,
            team_role=TeamRole.user,
        ),
        actor=admin,
    )
    request = make_account_request(
        requested_name="Approved user",
        requested_email="quota-snapshot-approved@example.com",
        requested_team_name=team.name,
    )
    _, approved = approve_account_request(
        db_session,
        admin,
        request.id,
        AccountRequestApprove(
            team_id=team.id,
            team_role=TeamRole.leader,
            temporary_password="TempPass1",
        ),
    )

    expected = (12_345, 234_567, 0, 32_400)
    assert (
        created.daily_token_limit,
        created.monthly_token_limit,
        created.daily_audio_seconds_limit,
        created.monthly_audio_seconds_limit,
    ) == expected
    assert (
        approved.daily_token_limit,
        approved.monthly_token_limit,
        approved.daily_audio_seconds_limit,
        approved.monthly_audio_seconds_limit,
    ) == expected


def test_updating_team_defaults_leaves_existing_unlimited_and_overridden_members_unchanged(
    db_session, make_team, make_user
):
    team = make_team(name="Quota defaults future members")
    admin = make_user(email="quota-defaults-admin@example.com", is_system_admin=True)
    unlimited_member = make_user(email="quota-defaults-unlimited@example.com", team=team)
    overridden_member = make_user(email="quota-defaults-overridden@example.com", team=team)
    overridden_member.daily_token_limit = 77
    overridden_member.monthly_token_limit = 777
    overridden_member.daily_audio_seconds_limit = 120
    overridden_member.monthly_audio_seconds_limit = 1_200
    db_session.commit()

    updated = update_team_quota_defaults(
        db_session,
        admin,
        team_id=team.id,
        default_daily_token_limit=0,
        default_monthly_token_limit=100,
        default_daily_audio_seconds_limit=0,
        default_monthly_audio_seconds_limit=3_600,
    )

    assert updated.default_daily_token_limit == 0
    assert unlimited_member.daily_token_limit is None
    assert unlimited_member.monthly_token_limit is None
    assert unlimited_member.daily_audio_seconds_limit is None
    assert unlimited_member.monthly_audio_seconds_limit is None
    assert (
        overridden_member.daily_token_limit,
        overridden_member.monthly_token_limit,
        overridden_member.daily_audio_seconds_limit,
        overridden_member.monthly_audio_seconds_limit,
    ) == (77, 777, 120, 1_200)


def test_team_quota_defaults_require_system_admin_and_enforce_existing_ceilings(db_session, make_team, make_user):
    team = make_team(name="Quota defaults authorization")
    leader = make_user(email="quota-defaults-leader@example.com", team=team, team_role=TeamRole.leader)
    limits = {
        "default_daily_token_limit": 100_000,
        "default_monthly_token_limit": 1_000_000,
        "default_daily_audio_seconds_limit": 28_800,
        "default_monthly_audio_seconds_limit": 259_200,
    }

    with pytest.raises(AppError) as forbidden:
        update_team_quota_defaults(db_session, leader, team_id=team.id, **limits)
    assert (forbidden.value.status_code, forbidden.value.code) == (403, "forbidden")

    admin = make_user(email="quota-defaults-range-admin@example.com", is_system_admin=True)
    with pytest.raises(AppError) as invalid:
        update_team_quota_defaults(
            db_session,
            admin,
            team_id=team.id,
            **{**limits, "default_daily_token_limit": 10_000_001},
        )
    assert (invalid.value.status_code, invalid.value.code) == (422, "quota_limit_invalid")
