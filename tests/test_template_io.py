import json
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from sqlalchemy import func, select

from app.errors import AppError
from app.models import PromptTemplate, PromptTemplateVersion, SecurityAuditEvent, TeamRole, TemplateMode, TemplateScope
from app.services.templates import export_template_bundle, import_template_bundle, parse_template_bundle, plan_template_bundle_import


def bundle_bytes(*templates: dict, **extra: object) -> bytes:
    return json.dumps({"format": "openscribe-template-bundle", "format_version": 1, "templates": list(templates), **extra}).encode()


def freeform(name: str, prompt: str = "Write a note", **extra: object) -> dict:
    return {"name": name, "description": None, "latest_version": {"mode": "freeform", "prompt_text": prompt, "config_json": None}, **extra}


def add_template(db, *, actor, name, prompt="Write a note", scope=TemplateScope.user, active=True, owner=None, team=None):
    template = PromptTemplate(scope=scope, owner_user_id=(owner or actor).id if scope is TemplateScope.user else None, team_id=(team or actor.team).id if scope is TemplateScope.team else None, name=name, is_active=active, created_by_user_id=actor.id)
    db.add(template)
    db.flush()
    db.add(PromptTemplateVersion(template_id=template.id, version_no=1, mode=TemplateMode.freeform, prompt_text=prompt, created_by_user_id=actor.id))
    db.commit()
    return template


def login(client, *, email, password="password-1"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_parse_warns_for_unknown_outer_fields_but_rejects_unknown_structured_fields():
    raw = bundle_bytes(
        freeform("Portable", future_entry=True),
        {
            "name": "Unsafe structured",
            "description": None,
            "latest_version": {
                "mode": "structured",
                "prompt_text": "Write sections",
                "config_json": {"profile": "emis", "future_config": True, "sections": []},
            },
        },
        future_bundle=True,
    )

    entries, warnings, issues = parse_template_bundle(raw)

    assert entries[0] is not None
    assert entries[1] is None
    assert {warning["path"] for warning in warnings} == {"future_bundle", "templates[0].future_entry"}
    assert issues[1][0]["path"] == "templates[1].latest_version.config_json.future_config"


def test_parse_rejects_non_null_freeform_config_and_boolean_version():
    entry = freeform("Bad config")
    entry["latest_version"]["config_json"] = {"profile": "emis", "sections": []}
    entries, _, issues = parse_template_bundle(bundle_bytes(entry))
    assert entries == [None]
    assert issues[0][0]["path"].endswith("config_json")

    with pytest.raises(AppError, match="Unsupported template bundle version"):
        parse_template_bundle(json.dumps({"format": "openscribe-template-bundle", "format_version": True, "templates": [freeform("One")]}).encode())


@pytest.mark.parametrize(
    ("entry", "expected_path"),
    [
        (
            {
                "name": "Missing description",
                "latest_version": {
                    "mode": "freeform",
                    "prompt_text": "Write a note",
                    "config_json": None,
                },
            },
            "templates[0].description",
        ),
        (
            {
                "name": "Missing config",
                "description": None,
                "latest_version": {
                    "mode": "freeform",
                    "prompt_text": "Write a note",
                },
            },
            "templates[0].latest_version.config_json",
        ),
    ],
)
def test_parse_rejects_template_entries_missing_required_fields(entry, expected_path):
    entries, _, issues = parse_template_bundle(bundle_bytes(entry))

    assert entries == [None]
    assert issues[0][0]["path"] == expected_path


def test_parse_rejects_boolean_structured_section_order():
    structured = {
        "name": "Boolean section order",
        "description": None,
        "latest_version": {
            "mode": "structured",
            "prompt_text": "Write sections",
            "config_json": {
                "profile": "emis",
                "sections": [
                    {
                        "section_key": "problem",
                        "instruction": "Summarise the problem.",
                        "section_order": True,
                    }
                ],
            },
        },
    }

    entries, _, issues = parse_template_bundle(bundle_bytes(structured))

    assert entries == [None]
    assert issues[0][0]["path"] == "templates[0].latest_version.config_json.sections.0.section_order"


def test_public_schema_accepts_supported_bundle_and_enforces_mode_config_pairing():
    schema = json.loads(Path("app/static/schemas/openscribe-template-bundle-v1.schema.json").read_text(encoding="utf-8"))
    validate(json.loads(bundle_bytes(freeform("Portable"))), schema)
    invalid = freeform("Bad public contract")
    invalid["latest_version"]["config_json"] = {"profile": "emis", "sections": []}
    with pytest.raises(JsonSchemaValidationError):
        validate(json.loads(bundle_bytes(invalid)), schema)


def test_public_schema_uses_section_keys_without_redundant_labels():
    schema = json.loads(Path("app/static/schemas/openscribe-template-bundle-v1.schema.json").read_text(encoding="utf-8"))
    structured = {
        "name": "Anxiety assessment",
        "description": None,
        "latest_version": {
            "mode": "structured",
            "prompt_text": "Write an assessment",
            "config_json": {
                "profile": "emis",
                "sections": [
                    {
                        "section_key": "problem",
                        "instruction": "Record the presenting concern.",
                        "section_order": 1,
                    }
                ],
            },
        },
    }

    validate(json.loads(bundle_bytes(structured)), schema)
    structured["latest_version"]["config_json"]["sections"][0]["section_label"] = "Problem"
    with pytest.raises(JsonSchemaValidationError):
        validate(json.loads(bundle_bytes(structured)), schema)


def test_parse_accepts_label_free_sections_and_rejects_redundant_labels():
    structured = {
        "name": "Anxiety assessment",
        "description": None,
        "latest_version": {
            "mode": "structured",
            "prompt_text": "Write an assessment",
            "config_json": {
                "profile": "emis",
                "sections": [
                    {
                        "section_key": "problem",
                        "instruction": "Record the presenting concern.",
                        "section_order": 1,
                    }
                ],
            },
        },
    }

    entries, _, issues = parse_template_bundle(bundle_bytes(structured))
    assert entries[0] is not None
    assert issues == [[]]

    structured["latest_version"]["config_json"]["sections"][0]["section_label"] = "Problem"
    entries, _, issues = parse_template_bundle(bundle_bytes(structured))
    assert entries == [None]
    assert issues[0][0]["path"] == "templates[0].latest_version.config_json.sections[0].section_label"


def test_import_stores_label_free_config_and_export_normalizes_legacy_config(db_session, make_user):
    user = make_user()
    structured = {
        "name": "Anxiety assessment",
        "description": None,
        "latest_version": {
            "mode": "structured",
            "prompt_text": "Write an assessment",
            "config_json": {
                "profile": "emis",
                "sections": [
                    {
                        "section_key": "problem",
                        "instruction": "Record the presenting concern.",
                        "section_order": 1,
                    }
                ],
            },
        },
    }

    result = import_template_bundle(
        db_session,
        user,
        destination=TemplateScope.user,
        raw_bundle=bundle_bytes(structured),
        selected_indexes=[0],
    )
    template_id = result["created"][0]["template_id"]
    version = db_session.scalar(
        select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == UUID(template_id))
    )
    assert "section_label" not in version.config_json["sections"][0]

    version.config_json = {
        "profile": "emis",
        "sections": [
            {
                "section_key": "problem",
                "section_label": "Legacy label",
                "instruction": "Record the presenting concern.",
                "section_order": 1,
            }
        ],
    }
    db_session.add(version)
    db_session.commit()
    db_session.refresh(version)
    assert version.config_json["sections"][0]["section_label"] == "Legacy label"
    exported = export_template_bundle(db_session, user, template_ids=[version.template_id])
    assert "section_label" not in exported["templates"][0]["latest_version"]["config_json"]["sections"][0]


def test_import_preflight_and_commit_handle_exact_copy_and_suffix_atomically(db_session, make_user):
    user = make_user()
    existing = PromptTemplate(scope=TemplateScope.user, owner_user_id=user.id, name="SOAP", description=None, is_active=True, created_by_user_id=user.id)
    db_session.add(existing)
    db_session.flush()
    db_session.add(PromptTemplateVersion(template_id=existing.id, version_no=1, mode=TemplateMode.freeform, prompt_text="Write a note", created_by_user_id=user.id))
    db_session.commit()
    raw = bundle_bytes(freeform(" SOAP "), freeform("SOAP", prompt="Different"))

    preview = plan_template_bundle_import(db_session, user, destination=TemplateScope.user, raw_bundle=raw)

    assert [entry["status"] for entry in preview["entries"]] == ["exact_copy", "renamed"]
    assert preview["entries"][0]["selected_by_default"] is False
    assert preview["entries"][0]["proposed_name"] == "SOAP copy 2"
    assert preview["entries"][1]["proposed_name"] == "SOAP copy 3"

    result = import_template_bundle(db_session, user, destination=TemplateScope.user, raw_bundle=raw, selected_indexes=[0, 1])

    assert [item["name"] for item in result["created"]] == ["SOAP copy 2", "SOAP copy 3"]
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate).where(PromptTemplate.owner_user_id == user.id)) == 3


def test_team_import_requires_leader_without_writes(db_session, make_user):
    user = make_user(team_role=TeamRole.user)

    with pytest.raises(AppError) as exc_info:
        import_template_bundle(db_session, user, destination=TemplateScope.team, raw_bundle=bundle_bytes(freeform("Team note")), selected_indexes=[0])

    assert exc_info.value.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate)) == 0


def test_export_visible_inactive_templates_and_reject_foreign_selection_atomically(client, db_session, make_team, make_user):
    own_team = make_team(name="Own")
    other_team = make_team(name="Other")
    member = make_user(email="member@example.com", team=own_team, mfa_required=False, mfa_enabled=False)
    other = make_user(email="other@example.com", team=other_team)
    own = add_template(db_session, actor=member, name="Personal")
    inactive_team = add_template(db_session, actor=member, name="Inactive team", scope=TemplateScope.team, team=own_team, active=False)
    foreign = add_template(db_session, actor=other, name="Foreign")
    cross_team = add_template(db_session, actor=other, name="Cross-team", scope=TemplateScope.team, team=other_team)

    assert login(client, email=member.email).status_code == 200
    response = client.post("/api/v1/templates/export", json={"template_ids": [str(own.id), str(inactive_team.id)]})
    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()["templates"]] == ["Personal", "Inactive team"]

    denied = client.post("/api/v1/templates/export", json={"template_ids": [str(own.id), str(foreign.id)]})
    assert denied.status_code == 404
    cross_team_denied = client.post("/api/v1/templates/export", json={"template_ids": [str(own.id), str(cross_team.id)]})
    assert cross_team_denied.status_code == 404
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "template_bundle_exported"))
    encoded = json.dumps(event.details_json)
    assert "Personal" not in encoded
    assert "Inactive team" not in encoded


def test_import_selected_valid_ignores_invalid_but_invalid_selection_is_rejected(db_session, make_user):
    user = make_user()
    invalid = freeform("Invalid")
    invalid["latest_version"]["prompt_text"] = "   "
    raw = bundle_bytes(freeform("Valid"), invalid)

    result = import_template_bundle(db_session, user, destination=TemplateScope.user, raw_bundle=raw, selected_indexes=[0])
    assert result["summary"]["imported"] == 1
    assert result["skipped_indexes"] == [1]

    with pytest.raises(AppError) as exc_info:
        import_template_bundle(db_session, user, destination=TemplateScope.user, raw_bundle=raw, selected_indexes=[1])
    assert exc_info.value.status_code == 422
    with pytest.raises(AppError) as missing_index:
        import_template_bundle(db_session, user, destination=TemplateScope.user, raw_bundle=raw, selected_indexes=[99])
    assert missing_index.value.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate)) == 1


def test_import_rolls_back_every_selected_template_when_commit_fails(db_session, make_user, monkeypatch):
    user = make_user()
    original_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        import_template_bundle(db_session, user, destination=TemplateScope.user, raw_bundle=bundle_bytes(freeform("One"), freeform("Two")), selected_indexes=[0, 1])
    monkeypatch.setattr(db_session, "commit", original_commit)
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate)) == 0


def test_import_audit_contains_metadata_only(db_session, make_user):
    user = make_user()
    raw = bundle_bytes({"name": "Secret name", "description": "Secret description", "latest_version": {"mode": "freeform", "prompt_text": "Secret prompt", "config_json": None}})
    import_template_bundle(db_session, user, destination=TemplateScope.user, raw_bundle=raw, selected_indexes=[0])
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "template_bundle_imported"))
    encoded = json.dumps(event.details_json)
    assert "Secret" not in encoded
    assert "prompt" not in encoded.lower()
    assert event.details_json["imported_count"] == 1


def test_import_routes_require_auth_and_csrf_and_enforce_limits(raw_client, make_user):
    raw = bundle_bytes(freeform("Route template"))
    files = {"bundle": ("templates.json", raw, "application/json")}
    assert raw_client.post("/api/v1/templates/import/preflight", data={"destination": "personal"}, files=files).status_code == 401
    make_user(email="route@example.com", mfa_required=False, mfa_enabled=False)
    assert login(raw_client, email="route@example.com").status_code == 200
    no_csrf = raw_client.post("/api/v1/templates/import/preflight", data={"destination": "personal"}, files=files, headers={"Origin": "http://testserver"})
    assert no_csrf.status_code == 403


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (b"\xff", 422),
        (json.dumps({"format": "other", "format_version": 1, "templates": [freeform("One")]}).encode(), 422),
        (b" " * (1024 * 1024 + 1), 413),
    ],
)
def test_preflight_rejects_bad_file_payloads(client, make_user, raw, status):
    make_user(email="bad-file@example.com", mfa_required=False, mfa_enabled=False)
    assert login(client, email="bad-file@example.com").status_code == 200
    response = client.post("/api/v1/templates/import/preflight", data={"destination": "personal"}, files={"bundle": ("bundle.json", raw, "application/json")})
    assert response.status_code == status


def test_leader_can_import_team_through_route_and_member_cannot(client, db_session, make_team, make_user):
    team = make_team()
    leader = make_user(email="leader@example.com", team=team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    member = make_user(email="member-route@example.com", team=team, team_role=TeamRole.user, mfa_required=False, mfa_enabled=False)
    raw = bundle_bytes(freeform("Team import"))
    files = {"bundle": ("bundle.json", raw, "application/json")}
    assert login(client, email=leader.email).status_code == 200
    response = client.post("/api/v1/templates/import", data={"destination": "team", "selected_indexes": "[0]"}, files=files)
    assert response.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate).where(PromptTemplate.team_id == team.id)) == 1
    assert login(client, email=member.email).status_code == 200
    denied = client.post("/api/v1/templates/import", data={"destination": "team", "selected_indexes": "[0]"}, files=files)
    assert denied.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(PromptTemplate).where(PromptTemplate.team_id == team.id)) == 1
