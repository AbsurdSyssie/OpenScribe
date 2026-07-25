import json
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.models import (
    QuickAction,
    QuickActionVersion,
    SecurityAuditEvent,
    TeamRole,
    TemplateMode,
    TemplateScope,
)
from app.services.quick_action_io import (
    export_quick_action_bundle,
    import_quick_action_bundle,
    parse_quick_action_bundle,
    plan_quick_action_bundle_import,
)


def bundle_bytes(*quick_actions: dict, **extra: object) -> bytes:
    return json.dumps(
        {
            "format": "openscribe-quick-action-bundle",
            "format_version": 1,
            "quick_actions": list(quick_actions),
            **extra,
        }
    ).encode()


def quick_action(
    name: str,
    prompt: str = "Write a follow-up",
    *,
    description: str | None = None,
    **extra: object,
) -> dict:
    return {
        "name": name,
        "description": description,
        "latest_version": {
            "mode": "freeform",
            "prompt_text": prompt,
        },
        **extra,
    }


def login(client, *, email: str, password: str = "password-1"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_parse_warns_for_additive_fields_and_rejects_non_freeform_entries():
    future = quick_action("Portable", future_entry=True)
    future["latest_version"]["future_version"] = True
    structured = quick_action("Unsafe")
    structured["latest_version"]["mode"] = "structured"

    entries, warnings, issues = parse_quick_action_bundle(
        bundle_bytes(future, structured, future_bundle=True)
    )

    assert entries[0] is not None
    assert entries[1] is None
    assert {warning["path"] for warning in warnings} == {
        "future_bundle",
        "quick_actions[0].future_entry",
        "quick_actions[0].latest_version.future_version",
    }
    assert issues[1][0]["path"] == "quick_actions[1].latest_version.mode"


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"{",
        b"[]",
        bundle_bytes(),
        json.dumps(
            {
                "format": "other",
                "format_version": 1,
                "quick_actions": [quick_action("One")],
            }
        ).encode(),
        json.dumps(
            {
                "format": "openscribe-quick-action-bundle",
                "format_version": True,
                "quick_actions": [quick_action("One")],
            }
        ).encode(),
    ],
)
def test_parse_rejects_invalid_envelopes(raw):
    with pytest.raises(AppError) as exc_info:
        parse_quick_action_bundle(raw)

    assert exc_info.value.status_code == 422


def test_parse_enforces_size_limit_and_strict_known_field_types():
    with pytest.raises(AppError) as oversized:
        parse_quick_action_bundle(b" " * (1024 * 1024 + 1))
    assert oversized.value.status_code == 413

    entries, _, issues = parse_quick_action_bundle(
        bundle_bytes(
            {
                "name": 42,
                "description": None,
                "latest_version": {
                    "mode": "freeform",
                    "prompt_text": "Prompt",
                },
            },
            {
                "name": "Blank prompt",
                "description": None,
                "latest_version": {
                    "mode": "freeform",
                    "prompt_text": "   ",
                },
            },
        )
    )
    assert entries == [None, None]
    assert issues[0][0]["path"] == "quick_actions[0].name"
    assert issues[1][0]["path"] == (
        "quick_actions[1].latest_version.prompt_text"
    )


def test_parse_rejects_quick_actions_missing_required_description():
    entry = quick_action("Missing description")
    del entry["description"]

    entries, _, issues = parse_quick_action_bundle(bundle_bytes(entry))

    assert entries == [None]
    assert issues[0][0]["path"] == "quick_actions[0].description"


def test_public_schema_accepts_supported_bundle_and_enforces_freeform_mode():
    schema = json.loads(
        Path(
            "app/static/schemas/"
            "openscribe-quick-action-bundle-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    validate(json.loads(bundle_bytes(quick_action("Portable"))), schema)

    invalid = quick_action("Structured")
    invalid["latest_version"]["mode"] = "structured"
    with pytest.raises(JsonSchemaValidationError):
        validate(json.loads(bundle_bytes(invalid)), schema)


def test_export_preserves_selection_order_uses_latest_version_and_omits_authority(
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Quick action export")
    member = make_user(email="export@example.com", team=team)
    personal = make_quick_action(
        owner=member,
        actor=member,
        team=team,
        name="Personal",
        prompt_text="Old prompt",
    )
    db_session.add(
        QuickActionVersion(
            quick_action_id=personal.id,
            version_no=2,
            mode=TemplateMode.freeform,
            prompt_text="Latest prompt",
            created_by_user_id=member.id,
        )
    )
    team_action = make_quick_action(
        scope=TemplateScope.team,
        team=team,
        actor=member,
        name="Inactive team",
        is_active=False,
    )
    db_session.commit()

    exported = export_quick_action_bundle(
        db_session,
        member,
        quick_action_ids=[team_action.id, personal.id],
    )

    assert [
        entry["name"] for entry in exported["quick_actions"]
    ] == ["Inactive team", "Personal"]
    assert exported["quick_actions"][1]["latest_version"] == {
        "mode": "freeform",
        "prompt_text": "Latest prompt",
    }
    encoded = json.dumps(exported)
    for forbidden in (
        str(personal.id),
        "owner_user_id",
        "team_id",
        "created_by_user_id",
        "version_no",
        "is_active",
        "created_at",
    ):
        assert forbidden not in encoded


def test_export_rejects_foreign_or_duplicate_selection_atomically(
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    own_team = make_team(name="Own export team")
    other_team = make_team(name="Other export team")
    actor = make_user(email="actor-export@example.com", team=own_team)
    other = make_user(email="other-export@example.com", team=other_team)
    own = make_quick_action(
        owner=actor,
        actor=actor,
        team=own_team,
        name="Own",
    )
    foreign = make_quick_action(
        owner=other,
        actor=other,
        team=other_team,
        name="Foreign",
    )
    cross_team = make_quick_action(
        scope=TemplateScope.team,
        team=other_team,
        actor=other,
        name="Cross team",
    )

    for ids, expected_status in (
        ([own.id, own.id], 422),
        ([own.id, foreign.id], 404),
        ([own.id, cross_team.id], 404),
    ):
        with pytest.raises(AppError) as exc_info:
            export_quick_action_bundle(
                db_session,
                actor,
                quick_action_ids=ids,
            )
        assert exc_info.value.status_code == expected_status

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.action
                == "quick_action_bundle_exported"
            )
        )
        == 0
    )


def test_preflight_matches_template_exact_copy_and_suffix_semantics(
    db_session,
    make_user,
    make_quick_action,
):
    actor = make_user()
    make_quick_action(
        owner=actor,
        actor=actor,
        team=actor.team,
        name="SOAP",
        description=None,
        prompt_text="Write a follow-up",
    )
    raw = bundle_bytes(
        quick_action(" SOAP "),
        quick_action("SOAP", prompt="Different"),
        quick_action("New"),
        quick_action("New"),
    )

    preview = plan_quick_action_bundle_import(
        db_session,
        actor,
        destination=TemplateScope.user,
        raw_bundle=raw,
    )

    assert [
        entry["status"] for entry in preview["entries"]
    ] == ["exact_copy", "renamed", "ready", "renamed"]
    assert [
        entry["proposed_name"] for entry in preview["entries"]
    ] == ["SOAP copy 2", "SOAP copy 3", "New", "New copy 2"]
    assert preview["entries"][0]["selected_by_default"] is False
    assert preview["entries"][1]["selected_by_default"] is True


def test_preflight_suffix_truncates_to_model_limit(
    db_session,
    make_user,
    make_quick_action,
):
    actor = make_user()
    original = "A" * 255
    make_quick_action(
        owner=actor,
        actor=actor,
        team=actor.team,
        name=original,
    )

    preview = plan_quick_action_bundle_import(
        db_session,
        actor,
        destination=TemplateScope.user,
        raw_bundle=bundle_bytes(quick_action(original, prompt="Other")),
    )

    proposed_name = preview["entries"][0]["proposed_name"]
    assert len(proposed_name) == 255
    assert proposed_name.endswith(" copy 2")


def test_team_import_requires_leader_and_system_admin_cannot_import_or_export(
    db_session,
    make_team,
    make_user,
    make_quick_action,
):
    team = make_team(name="Authority team")
    member = make_user(email="member-authority@example.com", team=team)
    admin = make_user(
        email="admin-authority@example.com",
        is_system_admin=True,
    )
    action = make_quick_action(
        owner=member,
        actor=member,
        team=team,
    )
    raw = bundle_bytes(quick_action("Team import"))

    with pytest.raises(AppError) as member_denied:
        import_quick_action_bundle(
            db_session,
            member,
            destination=TemplateScope.team,
            raw_bundle=raw,
            selected_indexes=[0],
        )
    assert member_denied.value.status_code == 403

    with pytest.raises(AppError) as admin_import_denied:
        import_quick_action_bundle(
            db_session,
            admin,
            destination=TemplateScope.user,
            raw_bundle=raw,
            selected_indexes=[0],
        )
    assert admin_import_denied.value.status_code == 403

    with pytest.raises(AppError) as admin_export_denied:
        export_quick_action_bundle(
            db_session,
            admin,
            quick_action_ids=[action.id],
        )
    assert admin_export_denied.value.status_code == 403


def test_import_creates_active_independent_roots_and_version_one(
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Import team")
    leader = make_user(
        email="leader-import@example.com",
        team=team,
        team_role=TeamRole.leader,
    )
    raw = bundle_bytes(
        quick_action(
            " One ",
            prompt=" Prompt one ",
            description=" Description ",
        ),
        quick_action("Two", prompt="Prompt two"),
    )

    result = import_quick_action_bundle(
        db_session,
        leader,
        destination=TemplateScope.team,
        raw_bundle=raw,
        selected_indexes=[1, 0, 1],
    )

    assert result["summary"] == {
        "selected": 2,
        "imported": 2,
        "skipped": 0,
        "warning_count": 0,
    }
    imported = list(
        db_session.scalars(
            select(QuickAction)
            .where(QuickAction.team_id == team.id)
            .order_by(QuickAction.name)
        )
    )
    assert [action.name for action in imported] == ["One", "Two"]
    assert all(action.scope is TemplateScope.team for action in imported)
    assert all(action.owner_user_id is None for action in imported)
    assert all(action.is_active for action in imported)
    assert imported[0].description == "Description"
    versions = list(
        db_session.scalars(
            select(QuickActionVersion).where(
                QuickActionVersion.quick_action_id.in_(
                    [action.id for action in imported]
                )
            )
        )
    )
    assert {version.version_no for version in versions} == {1}
    assert {version.mode for version in versions} == {
        TemplateMode.freeform
    }
    assert {version.prompt_text for version in versions} == {
        "Prompt one",
        "Prompt two",
    }


def test_import_rejects_invalid_selections_without_partial_writes(
    db_session,
    make_user,
):
    actor = make_user()
    invalid = quick_action("Invalid", prompt=" ")
    raw = bundle_bytes(quick_action("Valid"), invalid)

    result = import_quick_action_bundle(
        db_session,
        actor,
        destination=TemplateScope.user,
        raw_bundle=raw,
        selected_indexes=[0],
    )
    assert result["skipped_indexes"] == [1]

    for selected_indexes in ([], [1], [99], [False]):
        with pytest.raises(AppError) as exc_info:
            import_quick_action_bundle(
                db_session,
                actor,
                destination=TemplateScope.user,
                raw_bundle=raw,
                selected_indexes=selected_indexes,
            )
        assert exc_info.value.status_code == 422

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(QuickAction)
            .where(QuickAction.owner_user_id == actor.id)
        )
        == 1
    )


def test_import_rolls_back_all_selected_actions_when_commit_fails(
    db_session,
    make_user,
    monkeypatch,
):
    actor = make_user()
    original_commit = db_session.commit
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        import_quick_action_bundle(
            db_session,
            actor,
            destination=TemplateScope.user,
            raw_bundle=bundle_bytes(
                quick_action("One"),
                quick_action("Two"),
            ),
            selected_indexes=[0, 1],
        )

    monkeypatch.setattr(db_session, "commit", original_commit)
    assert (
        db_session.scalar(
            select(func.count()).select_from(QuickAction)
        )
        == 0
    )


def test_import_translates_raced_integrity_error_after_rollback(
    db_session,
    make_user,
    monkeypatch,
):
    actor = make_user()
    translated: list[IntegrityError] = []
    original_commit = db_session.commit
    error = IntegrityError("statement", {}, Exception("race"))
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(error),
    )

    def translate(exc):
        translated.append(exc)
        raise AppError(
            409,
            "conflict",
            "Quick action name already exists",
        )

    monkeypatch.setattr(
        "app.services.quick_action_io."
        "_translate_quick_action_integrity_error",
        translate,
    )

    with pytest.raises(AppError) as exc_info:
        import_quick_action_bundle(
            db_session,
            actor,
            destination=TemplateScope.user,
            raw_bundle=bundle_bytes(quick_action("Raced")),
            selected_indexes=[0],
        )

    monkeypatch.setattr(db_session, "commit", original_commit)
    assert exc_info.value.status_code == 409
    assert translated == [error]
    assert (
        db_session.scalar(
            select(func.count()).select_from(QuickAction)
        )
        == 0
    )


def test_quick_action_portability_routes_preflight_commit_and_export(
    client,
    db_session,
    make_team,
    make_user,
):
    team = make_team(name="Quick action route portability")
    leader = make_user(
        email="quick-action-route@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.leader,
        mfa_required=False,
        mfa_enabled=False,
    )
    assert login(client, email=leader.email).status_code == 200
    raw = bundle_bytes(quick_action("Route action"))
    files = {"bundle": ("quick-actions.json", raw, "application/json")}

    preview = client.post(
        "/api/v1/quick-actions/import/preflight",
        data={"destination": "team"},
        files=files,
    )
    assert preview.status_code == 200
    assert preview.json()["entries"][0]["status"] == "ready"

    committed = client.post(
        "/api/v1/quick-actions/import",
        data={"destination": "team", "selected_indexes": "[0]"},
        files=files,
    )
    assert committed.status_code == 200
    imported_id = committed.json()["created"][0]["quick_action_id"]
    assert db_session.scalar(
        select(func.count()).select_from(QuickAction).where(QuickAction.team_id == team.id)
    ) == 1

    exported = client.post(
        "/api/v1/quick-actions/export",
        json={"quick_action_ids": [imported_id]},
    )
    assert exported.status_code == 200
    assert exported.json()["format"] == "openscribe-quick-action-bundle"
    assert "openscribe-quick-actions.json" in exported.headers["content-disposition"]


def test_import_and_export_audits_contain_metadata_only(
    db_session,
    make_user,
):
    actor = make_user()
    raw = bundle_bytes(
        quick_action(
            "Secret name",
            prompt="Secret prompt",
            description="Secret description",
        )
    )

    result = import_quick_action_bundle(
        db_session,
        actor,
        destination=TemplateScope.user,
        raw_bundle=raw,
        selected_indexes=[0],
    )
    export_quick_action_bundle(
        db_session,
        actor,
        quick_action_ids=[
            UUID(result["created"][0]["quick_action_id"]),
        ],
    )

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action.in_(
                    [
                        "quick_action_bundle_imported",
                        "quick_action_bundle_exported",
                    ]
                )
            )
        )
    )
    assert len(events) == 2
    for event in events:
        encoded = json.dumps(event.details_json)
        assert "Secret" not in encoded
        assert "prompt" not in encoded.lower()
