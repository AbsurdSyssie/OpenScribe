from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.default_assets import PRIMARY_TEMPLATE_NAME
from app.models import (
    PromptTemplateVersion,
    QuickActionVersion,
    TeamRole,
    TemplateMode,
    TemplateScope,
    utcnow,
)
from app.services import templates as template_service
from app.web.presentation import quick_action_response, template_response
from app.web.transcribe_workspace import _structured_section_option_payloads


def _load_with_version_query_count(db_session, *, table_name: str, load: Callable[[], list[object]], serialize: Callable[[object], object]):
    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    db_session.expire_all()
    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        assets = load()
        serialized = [serialize(asset) for asset in assets]
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    version_selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and table_name in statement
    ]
    return assets, serialized, len(version_selects)


def _add_latest_version(db_session, asset, *, version_model, actor_id) -> None:
    values = {
        "version_no": 2,
        "mode": TemplateMode.structured if version_model is PromptTemplateVersion else TemplateMode.freeform,
        "prompt_text": f"Latest {asset.name}",
        "created_by_user_id": actor_id,
    }
    if version_model is PromptTemplateVersion:
        values.update(
            template_id=asset.id,
            config_json={
                "profile": "emis",
                "sections": [{"section_key": "problem", "instruction": "Summarise the problem.", "section_order": 1}],
            },
        )
    else:
        values["quick_action_id"] = asset.id
    db_session.add(version_model(**values))


@pytest.mark.parametrize(
    (
        "asset_label, factory_fixture, version_model, version_table, serialize, team_list, member_list, personal_list, available_list, prioritise_primary"
    ),
    [
        (
            "template",
            "make_template",
            PromptTemplateVersion,
            "template_versions",
            template_response,
            template_service.list_team_templates,
            template_service.list_team_templates_for_member,
            template_service.list_personal_templates,
            template_service.list_available_templates_for_user,
            True,
        ),
        (
            "quick action",
            "make_quick_action",
            QuickActionVersion,
            "quick_action_versions",
            quick_action_response,
            template_service.list_team_quick_actions,
            template_service.list_team_quick_actions_for_member,
            template_service.list_personal_quick_actions,
            template_service.list_available_quick_actions_for_user,
            False,
        ),
    ],
)
def test_list_services_batch_complete_version_history_and_preserve_visibility(
    request,
    db_session,
    make_team,
    make_user,
    asset_label,
    factory_fixture,
    version_model,
    version_table,
    serialize,
    team_list,
    member_list,
    personal_list,
    available_list,
    prioritise_primary,
):
    factory = request.getfixturevalue(factory_fixture)
    team = make_team(name=f"Version batch {asset_label}s")
    other_team = make_team(name=f"Other version batch {asset_label}s")
    leader = make_user(email=f"{asset_label.replace(' ', '-')}-version-leader@example.com", team=team, team_role=TeamRole.leader)
    member = make_user(email=f"{asset_label.replace(' ', '-')}-version-member@example.com", team=team)
    same_team_other_member = make_user(email=f"{asset_label.replace(' ', '-')}-version-other@example.com", team=team)
    outsider = make_user(email=f"{asset_label.replace(' ', '-')}-version-outsider@example.com", team=other_team)
    team_assets = [
        factory(
            scope=TemplateScope.team,
            team=team,
            actor=leader,
            name=PRIMARY_TEMPLATE_NAME if prioritise_primary and index == 1 else f"Team {asset_label} {index}",
        )
        for index in range(3)
    ]
    personal_assets = [
        factory(scope=TemplateScope.user, team=team, owner=member, actor=member, name=f"Personal {asset_label} {index}")
        for index in range(3)
    ]
    inactive = factory(scope=TemplateScope.team, team=team, actor=leader, name=f"Inactive team {asset_label}", is_active=False)
    other_personal = factory(
        scope=TemplateScope.user,
        team=team,
        owner=same_team_other_member,
        actor=same_team_other_member,
        name=f"Other personal {asset_label}",
    )
    factory(scope=TemplateScope.team, team=other_team, actor=outsider, name=f"Outsider {asset_label}")
    for index, asset in enumerate([*team_assets, *personal_assets, inactive]):
        asset.updated_at = utcnow() + timedelta(seconds=index)
        _add_latest_version(db_session, asset, version_model=version_model, actor_id=leader.id)
    db_session.commit()

    team_expected = [inactive, team_assets[2], team_assets[1], team_assets[0]]
    personal_expected = [personal_assets[2], personal_assets[1], personal_assets[0]]
    available_team = (
        [team_assets[1], team_assets[2], team_assets[0]]
        if prioritise_primary
        else [team_assets[2], team_assets[1], team_assets[0]]
    )
    available_expected = [*available_team, *personal_expected]
    for load, expected in (
        (lambda: team_list(db_session, leader), team_expected),
        (lambda: member_list(db_session, member), team_expected),
        (lambda: personal_list(db_session, member), personal_expected),
        (lambda: available_list(db_session, member), available_expected),
    ):
        assets, serialized, version_selects = _load_with_version_query_count(
            db_session,
            table_name=version_table,
            load=load,
            serialize=serialize,
        )
        assert [asset.id for asset in assets] == [asset.id for asset in expected]
        assert other_personal.id not in {asset.id for asset in assets}
        assert version_selects == 1
        assert [asset.latest_version.version_no for asset in serialized] == [2] * len(expected)
        assert all({version.version_no for version in asset.versions} == {1, 2} for asset in assets)

    if prioritise_primary:
        assert _structured_section_option_payloads(available_expected) == {
            str(asset.id): [{"section_key": "problem", "section_label": "Problem", "section_order": 0}]
            for asset in available_expected
        }
