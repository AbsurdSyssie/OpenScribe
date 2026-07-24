import json
from pathlib import Path

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from sqlalchemy import func, select

from app.errors import AppError
from app.models import SecurityAuditEvent, SmartPhrase
from app.services.smart_phrase_io import (
    SMART_PHRASE_BUNDLE_FORMAT,
    export_smart_phrase_bundle,
    import_smart_phrase_bundle,
    parse_smart_phrase_bundle,
    plan_smart_phrase_bundle_import,
)


def bundle_bytes(*phrases: dict, **extra: object) -> bytes:
    return json.dumps(
        {
            "format": SMART_PHRASE_BUNDLE_FORMAT,
            "format_version": 1,
            "smart_phrases": list(phrases),
            **extra,
        }
    ).encode()


def phrase(trigger: str, expansion_text: str = "Saved text", description: str | None = None, **extra: object) -> dict:
    return {"trigger": trigger, "expansion_text": expansion_text, "description": description, **extra}


def stored(db_session, user, trigger="EXISTING", expansion_text="Saved text", description=None, **extra):
    item = SmartPhrase(owner_user_id=user.id, trigger=trigger, expansion_text=expansion_text, description=description, **extra)
    db_session.add(item)
    db_session.commit()
    return item


def login(client, *, email: str, password: str = "password-1"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_schema_and_parser_are_strict_but_reuse_personal_normalization():
    schema = json.loads(Path("app/static/schemas/openscribe-smart-phrase-bundle-v1.schema.json").read_text())
    validate(json.loads(bundle_bytes(phrase("BP_NOTE"))), schema)
    with pytest.raises(JsonSchemaValidationError):
        validate(json.loads(bundle_bytes(phrase("BP_NOTE", id="not portable"))), schema)

    entries, warnings, issues = parse_smart_phrase_bundle(bundle_bytes(phrase(" bp_note ", " Text ", " Description ")))
    assert warnings == []
    assert issues == [[]]
    assert entries[0] is not None
    assert (entries[0].trigger, entries[0].expansion_text, entries[0].description) == ("BP_NOTE", "Text", "Description")

    _, _, issues = parse_smart_phrase_bundle(bundle_bytes(phrase("SAFE", id="no")))
    assert issues[0][0]["path"] == "smart_phrases[0].id"
    with pytest.raises(AppError, match="unknown fields"):
        parse_smart_phrase_bundle(bundle_bytes(phrase("SAFE"), unexpected=True))


def test_preflight_reserves_exact_copies_conflicts_and_safe_64_character_suffixes(db_session, make_user):
    user = make_user()
    trigger = "A" * 64
    stored(db_session, user, trigger=trigger, expansion_text="Same")
    raw = bundle_bytes(phrase(trigger, "Same"), phrase(trigger, "Different"), phrase(trigger, "Third"))

    preview = plan_smart_phrase_bundle_import(db_session, user, raw_bundle=raw)

    assert [entry["status"] for entry in preview["entries"]] == ["exact_copy", "renamed", "renamed"]
    assert [entry["selected_by_default"] for entry in preview["entries"]] == [False, True, True]
    assert [entry["proposed_trigger"] for entry in preview["entries"]] == ["A" * 59 + "_COPY", "A" * 57 + "_COPY_2", "A" * 57 + "_COPY_3"]
    assert all(len(entry["proposed_trigger"]) <= 64 for entry in preview["entries"])


def test_import_forced_exact_copy_selected_subset_is_atomic_and_resets_usage(db_session, make_user):
    user = make_user()
    stored(db_session, user, trigger="SAFE", expansion_text="Same", description="Description", times_used=8)
    raw = bundle_bytes(phrase("safe", "Same", "Description"), phrase("OTHER", "Other", "Other description"))

    result = import_smart_phrase_bundle(db_session, user, raw_bundle=raw, selected_indexes=[0])

    assert result["created"][0]["trigger"] == "SAFE_COPY"
    copied = db_session.scalar(select(SmartPhrase).where(SmartPhrase.owner_user_id == user.id, SmartPhrase.trigger == "SAFE_COPY"))
    assert copied is not None
    assert copied.times_used == 0
    assert copied.last_used_at is None
    assert db_session.scalar(select(func.count()).select_from(SmartPhrase).where(SmartPhrase.owner_user_id == user.id)) == 2

    invalid = bundle_bytes(phrase("VALID"), phrase("BAD", expansion_text="   "))
    result = import_smart_phrase_bundle(db_session, user, raw_bundle=invalid, selected_indexes=[0])
    assert result["summary"]["imported"] == 1
    with pytest.raises(AppError) as exc:
        import_smart_phrase_bundle(db_session, user, raw_bundle=invalid, selected_indexes=[1])
    assert exc.value.status_code == 422


def test_import_rolls_back_and_audits_metadata_only(db_session, make_user, monkeypatch):
    user = make_user()
    raw = bundle_bytes(phrase("SECRET_TRIGGER", "Secret expansion", "Secret description"))
    original_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        import_smart_phrase_bundle(db_session, user, raw_bundle=raw, selected_indexes=[0])
    monkeypatch.setattr(db_session, "commit", original_commit)
    assert db_session.scalar(select(func.count()).select_from(SmartPhrase)) == 0

    import_smart_phrase_bundle(db_session, user, raw_bundle=raw, selected_indexes=[0])
    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "smart_phrase_bundle_imported"))
    encoded = json.dumps(event.details_json)
    assert "SECRET" not in encoded
    assert "expansion" not in encoded.lower()
    assert event.details_json["imported_count"] == 1


def test_import_translates_raced_trigger_integrity_error_without_partial_writes(db_session, make_user, monkeypatch):
    user = make_user()
    raw = bundle_bytes(phrase("ONE"), phrase("TWO"))
    original_commit = db_session.commit

    from sqlalchemy.exc import IntegrityError

    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(IntegrityError("INSERT", {}, RuntimeError("raced trigger"))),
    )
    with pytest.raises(AppError) as exc:
        import_smart_phrase_bundle(db_session, user, raw_bundle=raw, selected_indexes=[0, 1])
    monkeypatch.setattr(db_session, "commit", original_commit)

    assert exc.value.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(SmartPhrase)) == 0


def test_export_owner_scope_metadata_and_admin_rejection(db_session, make_user):
    user = make_user()
    own = stored(db_session, user, trigger="OWN", expansion_text="Private text", description="Private description", times_used=4)
    other = make_user(email="other-io@example.com", team=user.team)
    foreign = stored(db_session, other, trigger="FOREIGN")

    exported = export_smart_phrase_bundle(db_session, user, smart_phrase_ids=[own.id])
    assert exported["smart_phrases"] == [{"trigger": "OWN", "expansion_text": "Private text", "description": "Private description"}]
    assert set(exported) == {"format", "format_version", "smart_phrases"}
    with pytest.raises(AppError) as exc:
        export_smart_phrase_bundle(db_session, user, smart_phrase_ids=[own.id, foreign.id])
    assert exc.value.status_code == 404

    admin = make_user(email="admin-io@example.com", is_system_admin=True)
    with pytest.raises(AppError) as admin_error:
        plan_smart_phrase_bundle_import(db_session, admin, raw_bundle=bundle_bytes(phrase("ADMIN")))
    assert admin_error.value.status_code == 403
    with pytest.raises(AppError) as export_admin_error:
        export_smart_phrase_bundle(db_session, admin, smart_phrase_ids=[own.id])
    assert export_admin_error.value.status_code == 403

    event = db_session.scalar(select(SecurityAuditEvent).where(SecurityAuditEvent.action == "smart_phrase_bundle_exported"))
    assert event is not None
    assert "Private" not in json.dumps(event.details_json)


def test_smart_phrase_portability_routes_preflight_commit_and_export(
    client,
    db_session,
    make_user,
):
    user = make_user(
        email="smart-phrase-route@example.com",
        password="password-1",
        mfa_required=False,
        mfa_enabled=False,
    )
    assert login(client, email=user.email).status_code == 200
    raw = bundle_bytes(phrase("ROUTE", "Route expansion", "Route description"))
    files = {"bundle": ("smart-phrases.json", raw, "application/json")}

    preview = client.post("/api/v1/smart-phrases/import/preflight", files=files)
    assert preview.status_code == 200
    assert preview.json()["entries"][0]["status"] == "ready"

    committed = client.post(
        "/api/v1/smart-phrases/import",
        data={"selected_indexes": "[0]"},
        files=files,
    )
    assert committed.status_code == 200
    imported_id = committed.json()["created"][0]["smart_phrase_id"]
    assert db_session.scalar(
        select(func.count()).select_from(SmartPhrase).where(SmartPhrase.owner_user_id == user.id)
    ) == 1

    exported = client.post(
        "/api/v1/smart-phrases/export",
        json={"smart_phrase_ids": [imported_id]},
    )
    assert exported.status_code == 200
    assert exported.json()["format"] == "openscribe-smart-phrase-bundle"
    assert "openscribe-smart-phrases.json" in exported.headers["content-disposition"]
