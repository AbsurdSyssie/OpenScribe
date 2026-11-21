import csv
import io
import re
from datetime import UTC, datetime

from app.models import ScribeTermsAcceptance, TeamRole


def test_system_admin_can_view_terms_history_and_download_safe_csv(
    client, db_session, make_team, make_user
):
    team = make_team(name="Terms evidence team")
    admin = make_user(
        email="terms-admin@example.com", password="password-1", is_system_admin=True
    )
    member = make_user(
        email="+member@example.com", password="password-1", team=team
    )
    member_id = member.id
    member.full_name = "=2+2"
    db_session.add_all(
        [
            ScribeTermsAcceptance(
                user_id=member.id,
                version_no=3,
                accepted_at=datetime(2026, 11, 20, 10, 11, 12, tzinfo=UTC),
            ),
            ScribeTermsAcceptance(
                user_id=member.id,
                version_no=4,
                accepted_at=datetime(2026, 11, 21, 9, 8, 7, tzinfo=UTC),
            ),
        ]
    )
    db_session.commit()
    client.post("/login", data={"email": admin.email, "password": "password-1"})

    page = client.get(f"/admin?team_id={team.id}&team_tab=members")
    assert page.status_code == 200
    assert "Terms acceptance history" in page.text
    assert "Version 3" in page.text
    assert "Version 4" in page.text
    assert f"/admin/users/{member.id}/scribe-terms.csv" in page.text
    assert "Download acceptance history (CSV)" in page.text

    response = client.get(f"/admin/users/{member.id}/scribe-terms.csv")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "attachment;" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["Account name", "Full name", "Terms version", "Accepted at (UTC)"]
    assert rows[1:] == [
        ["'+member@example.com", "'=2+2", "3", "2026-11-20T10:11:12Z"],
        ["'+member@example.com", "'=2+2", "4", "2026-11-21T09:08:07Z"],
    ]

    csrf_token = re.search(r'name="_csrf_token" value="([^"]+)"', page.text)
    assert csrf_token is not None
    deleted = client.post(
        f"/admin/users/{member.id}/delete",
        data={"_csrf_token": csrf_token.group(1), "return_team_id": str(team.id)},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    db_session.expire_all()
    assert (
        db_session.query(ScribeTermsAcceptance)
        .filter(ScribeTermsAcceptance.user_id == member_id)
        .count()
        == 0
    )


def test_non_system_admin_cannot_download_terms_acceptance_csv(
    client, make_team, make_user
):
    team = make_team(name="Terms evidence team")
    leader = make_user(
        email="terms-leader@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
    )
    client.post("/login", data={"email": leader.email, "password": "password-1"})

    response = client.get(f"/admin/users/{leader.id}/scribe-terms.csv")

    assert response.status_code == 403
