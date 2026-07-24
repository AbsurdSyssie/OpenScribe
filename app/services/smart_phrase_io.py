"""Portable, personal-only Smart Phrase import and export services.

Routes deliberately remain outside this module.  They should pass the original
uploaded bytes into both preflight and commit so the commit can re-plan against
the current database state.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import SmartPhrase, User
from app.schemas.smart_phrase_io import SmartPhraseBundleEntry
from app.services.security_audit import record_security_event


SMART_PHRASE_BUNDLE_FORMAT = "openscribe-smart-phrase-bundle"
SMART_PHRASE_BUNDLE_FORMAT_VERSION = 1
SMART_PHRASE_BUNDLE_MAX_BYTES = 1024 * 1024
SMART_PHRASE_BUNDLE_MAX_ENTRIES = 100
_ENTRY_FIELDS = {"trigger", "expansion_text", "description"}
_ROOT_FIELDS = {"format", "format_version", "smart_phrases"}


def _require_personal_phrase_actor(actor: User) -> None:
    if actor.is_system_admin or actor.team_id is None:
        raise AppError(403, "forbidden", "Smart phrases are available only to normal team users")


def _invalid_bundle(message: str, *, field: str | None = None) -> AppError:
    return AppError(422, "validation_error", message, {"field": field} if field else None)


def _entry_issue_path(index: int, location: tuple[Any, ...]) -> str:
    suffix = ".".join(str(part) for part in location)
    return f"smart_phrases[{index}]" + (f".{suffix}" if suffix else "")


def parse_smart_phrase_bundle(
    raw_bundle: bytes,
) -> tuple[list[SmartPhraseBundleEntry | None], list[dict[str, str]], list[list[dict[str, str]]]]:
    """Parse a bundle without accepting unknown fields or invalid entries.

    Invalid entries are retained as ``None`` so a user can import another valid
    selected entry from the same bundle.  Envelope errors reject the entire
    bundle because its identity cannot be trusted.
    """
    if not isinstance(raw_bundle, bytes):
        raise _invalid_bundle("Smart phrase bundle must be UTF-8 JSON")
    if len(raw_bundle) > SMART_PHRASE_BUNDLE_MAX_BYTES:
        raise AppError(413, "payload_too_large", "Smart phrase bundle must not exceed 1 MiB")
    try:
        decoded = raw_bundle.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid_bundle("Smart phrase bundle must be UTF-8 JSON") from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise _invalid_bundle("Smart phrase bundle is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise _invalid_bundle("Smart phrase bundle root must be an object")
    unknown_root_fields = set(payload) - _ROOT_FIELDS
    if unknown_root_fields:
        raise _invalid_bundle("Smart phrase bundle contains unknown fields", field=sorted(unknown_root_fields)[0])
    if payload.get("format") != SMART_PHRASE_BUNDLE_FORMAT:
        raise _invalid_bundle("Unsupported smart phrase bundle format", field="format")
    version = payload.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != SMART_PHRASE_BUNDLE_FORMAT_VERSION:
        raise _invalid_bundle("Unsupported smart phrase bundle version", field="format_version")
    raw_entries = payload.get("smart_phrases")
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > SMART_PHRASE_BUNDLE_MAX_ENTRIES:
        raise _invalid_bundle("Smart phrase bundle must contain between 1 and 100 smart phrases", field="smart_phrases")

    entries: list[SmartPhraseBundleEntry | None] = []
    entry_issues: list[list[dict[str, str]]] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            entries.append(None)
            entry_issues.append([{"path": f"smart_phrases[{index}]", "message": "Input should be a valid object"}])
            continue
        unknown_entry_fields = set(raw_entry) - _ENTRY_FIELDS
        if unknown_entry_fields:
            entries.append(None)
            entry_issues.append(
                [
                    {
                        "path": f"smart_phrases[{index}].{field}",
                        "message": "Field is not permitted in a smart phrase bundle",
                    }
                    for field in sorted(unknown_entry_fields)
                ]
            )
            continue
        try:
            entry = SmartPhraseBundleEntry.model_validate(raw_entry)
        except ValidationError as exc:
            entries.append(None)
            entry_issues.append(
                [
                    {"path": _entry_issue_path(index, tuple(error["loc"])), "message": error["msg"]}
                    for error in exc.errors()
                ]
            )
            continue
        entries.append(entry)
        entry_issues.append([])
    return entries, [], entry_issues


def _stored_content(phrase: SmartPhrase) -> tuple[str, str | None]:
    return phrase.expansion_text.strip(), (phrase.description or "").strip() or None


def _entry_content(entry: SmartPhraseBundleEntry) -> tuple[str, str | None]:
    return entry.expansion_text, entry.description


def _copy_trigger(trigger: str, reserved: set[str]) -> str:
    """Return the deterministic, valid copy trigger, reserving no value itself."""
    suffix_number = 1
    while True:
        suffix = "_COPY" if suffix_number == 1 else f"_COPY_{suffix_number}"
        candidate = f"{trigger[: 64 - len(suffix)]}{suffix}"
        if candidate not in reserved:
            return candidate
        suffix_number += 1


def plan_smart_phrase_bundle_import(db: Session, actor: User, *, raw_bundle: bytes) -> dict[str, object]:
    _require_personal_phrase_actor(actor)
    entries, warnings, entry_issues = parse_smart_phrase_bundle(raw_bundle)
    existing = list(
        db.scalars(select(SmartPhrase).where(SmartPhrase.owner_user_id == actor.id))
    )
    by_trigger = {phrase.trigger.upper(): phrase for phrase in existing}
    reserved = set(by_trigger)
    preview: list[dict[str, object]] = []
    counts = {"total": len(entries), "importable": 0, "exact_copies": 0, "invalid": 0, "renamed": 0, "unknown_fields": 0}
    for index, entry in enumerate(entries):
        if entry is None:
            preview.append(
                {
                    "index": index,
                    "source_trigger": None,
                    "proposed_trigger": None,
                    "status": "invalid",
                    "selected_by_default": False,
                    "selectable": False,
                    "errors": entry_issues[index],
                    "warnings": [],
                }
            )
            counts["invalid"] += 1
            continue
        matched = by_trigger.get(entry.trigger)
        exact = bool(matched and _stored_content(matched) == _entry_content(entry))
        proposed_trigger = entry.trigger
        status = "ready"
        if entry.trigger in reserved:
            proposed_trigger = _copy_trigger(entry.trigger, reserved)
            status = "exact_copy" if exact else "renamed"
        reserved.add(proposed_trigger)
        if status == "exact_copy":
            counts["exact_copies"] += 1
        elif status == "renamed":
            counts["renamed"] += 1
        counts["importable"] += 1
        preview.append(
            {
                "index": index,
                "source_trigger": entry.trigger,
                "proposed_trigger": proposed_trigger,
                "status": status,
                "selected_by_default": not exact,
                "selectable": True,
                "errors": [],
                "warnings": [],
            }
        )
    return {"entries": preview, "warnings": warnings, "summary": counts}


def _validate_selected_indexes(selected_indexes: list[int], preview: list[dict[str, object]]) -> list[int]:
    if not isinstance(selected_indexes, list):
        raise _invalid_bundle("Selected smart phrase indexes must be a list", field="selected_indexes")
    selected: list[int] = []
    for index in selected_indexes:
        if not isinstance(index, int) or isinstance(index, bool):
            raise _invalid_bundle("Selected smart phrase indexes must be integers", field="selected_indexes")
        if index not in selected:
            selected.append(index)
    if not selected:
        raise _invalid_bundle("Select at least one smart phrase to import", field="selected_indexes")
    preview_by_index = {item["index"]: item for item in preview}
    if any(index not in preview_by_index or not preview_by_index[index]["selectable"] for index in selected):
        raise _invalid_bundle("Selected smart phrase indexes include an invalid entry", field="selected_indexes")
    return selected


def import_smart_phrase_bundle(
    db: Session,
    actor: User,
    *,
    raw_bundle: bytes,
    selected_indexes: list[int],
) -> dict[str, object]:
    """Re-plan and atomically create the selected portable smart phrases."""
    _require_personal_phrase_actor(actor)
    plan = plan_smart_phrase_bundle_import(db, actor, raw_bundle=raw_bundle)
    preview = plan["entries"]
    assert isinstance(preview, list)
    selected = _validate_selected_indexes(selected_indexes, preview)
    entries, warnings, _ = parse_smart_phrase_bundle(raw_bundle)
    preview_by_index = {item["index"]: item for item in preview}
    created: list[dict[str, object]] = []
    try:
        for index in selected:
            entry = entries[index]
            assert entry is not None
            planned = preview_by_index[index]
            trigger = planned["proposed_trigger"]
            assert isinstance(trigger, str)
            phrase = SmartPhrase(
                id=uuid4(),
                owner_user_id=actor.id,
                trigger=trigger,
                expansion_text=entry.expansion_text,
                description=entry.description,
                times_used=0,
                last_used_at=None,
            )
            db.add(phrase)
            created.append({"index": index, "smart_phrase_id": str(phrase.id), "trigger": phrase.trigger})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409,
            "conflict",
            "Smart phrase triggers changed during import; review and try again",
            {"resource": "smart_phrase", "field": "trigger"},
        ) from exc
    except Exception:
        db.rollback()
        raise
    record_security_event(
        db,
        action="smart_phrase_bundle_imported",
        actor=actor,
        target=actor,
        team_id=actor.team_id,
        details={
            "category": "template",
            "outcome": "success",
            "object_type": "smart_phrase",
            "selected_count": len(selected),
            "imported_count": len(created),
            "skipped_count": len(entries) - len(selected),
            "warning_count": len(warnings),
            "object_ids": [item["smart_phrase_id"] for item in created],
        },
    )
    return {
        "created": created,
        "skipped_indexes": [index for index in range(len(entries)) if index not in selected],
        "warnings": warnings,
        "summary": {
            "selected": len(selected),
            "imported": len(created),
            "skipped": len(entries) - len(selected),
            "warning_count": len(warnings),
        },
    }


def export_smart_phrase_bundle(db: Session, actor: User, *, smart_phrase_ids: list[UUID]) -> dict[str, object]:
    """Export explicit owner-selected phrases, without usage or database metadata."""
    _require_personal_phrase_actor(actor)
    if (
        not isinstance(smart_phrase_ids, list)
        or not smart_phrase_ids
        or len(smart_phrase_ids) > SMART_PHRASE_BUNDLE_MAX_ENTRIES
        or len(set(smart_phrase_ids)) != len(smart_phrase_ids)
    ):
        raise _invalid_bundle("Select between 1 and 100 distinct smart phrases", field="smart_phrase_ids")
    visible = list(
        db.scalars(
            select(SmartPhrase).where(
                SmartPhrase.id.in_(smart_phrase_ids),
                SmartPhrase.owner_user_id == actor.id,
            )
        )
    )
    by_id = {phrase.id: phrase for phrase in visible}
    if any(phrase_id not in by_id for phrase_id in smart_phrase_ids):
        raise AppError(404, "not_found", "One or more smart phrases were not found", {"resource": "smart_phrase"})
    entries = [
        {
            "trigger": by_id[phrase_id].trigger,
            "expansion_text": by_id[phrase_id].expansion_text,
            "description": by_id[phrase_id].description,
        }
        for phrase_id in smart_phrase_ids
    ]
    record_security_event(
        db,
        action="smart_phrase_bundle_exported",
        actor=actor,
        target=actor,
        team_id=actor.team_id,
        details={
            "category": "template",
            "outcome": "success",
            "object_type": "smart_phrase",
            "smart_phrase_count": len(entries),
            "object_ids": [str(phrase_id) for phrase_id in smart_phrase_ids],
        },
    )
    return {
        "format": SMART_PHRASE_BUNDLE_FORMAT,
        "format_version": SMART_PHRASE_BUNDLE_FORMAT_VERSION,
        "smart_phrases": entries,
    }
