import json
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import QuickAction, QuickActionVersion, TemplateMode, TemplateScope, User
from app.schemas.quick_action_io import QuickActionBundleEntry
from app.services.security_audit import record_security_event
from app.services.templates import (
    _latest_quick_action_version,
    _require_team_leader,
    _require_team_member,
    _serialize_asset_name,
    _serialize_prompt_text,
    _translate_quick_action_integrity_error,
)


QUICK_ACTION_BUNDLE_FORMAT = "openscribe-quick-action-bundle"
QUICK_ACTION_BUNDLE_FORMAT_VERSION = 1
QUICK_ACTION_BUNDLE_MAX_BYTES = 1024 * 1024
QUICK_ACTION_BUNDLE_MAX_ENTRIES = 100
_BUNDLE_FIELDS = {"format", "format_version", "quick_actions"}
_ENTRY_FIELDS = {"name", "description", "latest_version"}
_VERSION_FIELDS = {"mode", "prompt_text"}
_IGNORED_FIELD_MESSAGE = "Field was not recognised and will not be imported"


def export_quick_action_bundle(
    db: Session,
    actor: User,
    *,
    quick_action_ids: list[UUID],
) -> dict[str, object]:
    """Export latest versions of selected Quick Actions visible to the actor."""
    _require_team_member(actor)
    if (
        not quick_action_ids
        or len(quick_action_ids) > QUICK_ACTION_BUNDLE_MAX_ENTRIES
        or len(set(quick_action_ids)) != len(quick_action_ids)
    ):
        raise AppError(
            422,
            "business_rule_violation",
            "Select between 1 and 100 distinct quick actions",
        )

    visible = list(
        db.scalars(
            select(QuickAction).where(
                QuickAction.id.in_(quick_action_ids),
                (
                    (
                        (QuickAction.scope == TemplateScope.user)
                        & (QuickAction.owner_user_id == actor.id)
                    )
                    | (
                        (QuickAction.scope == TemplateScope.team)
                        & (QuickAction.team_id == actor.team_id)
                    )
                ),
            )
        )
    )
    by_id = {quick_action.id: quick_action for quick_action in visible}
    if any(quick_action_id not in by_id for quick_action_id in quick_action_ids):
        raise AppError(
            404,
            "not_found",
            "One or more quick actions were not found",
            {"resource": "quick_action"},
        )

    entries: list[dict[str, object]] = []
    for quick_action_id in quick_action_ids:
        quick_action = by_id[quick_action_id]
        version = _latest_quick_action_version(
            db,
            quick_action_id=quick_action.id,
        )
        if version.mode is not TemplateMode.freeform:
            raise AppError(
                422,
                "business_rule_violation",
                "Only freeform quick actions can be exported",
                {"resource": "quick_action"},
            )
        entries.append(
            {
                "name": quick_action.name,
                "description": quick_action.description,
                "latest_version": {
                    "mode": TemplateMode.freeform.value,
                    "prompt_text": version.prompt_text,
                },
            }
        )

    record_security_event(
        db,
        action="quick_action_bundle_exported",
        actor=actor,
        team_id=actor.team_id,
        details={
            "category": "template",
            "outcome": "success",
            "object_type": "quick_action",
            "quick_action_count": len(entries),
            "object_ids": [
                str(quick_action_id) for quick_action_id in quick_action_ids
            ],
        },
    )
    return {
        "format": QUICK_ACTION_BUNDLE_FORMAT,
        "format_version": QUICK_ACTION_BUNDLE_FORMAT_VERSION,
        "quick_actions": entries,
    }


def parse_quick_action_bundle(
    raw_bundle: bytes,
) -> tuple[
    list[QuickActionBundleEntry | None],
    list[dict[str, str]],
    list[list[dict[str, str]]],
]:
    """Strictly parse a bundle while warning on additive envelope fields."""
    if len(raw_bundle) > QUICK_ACTION_BUNDLE_MAX_BYTES:
        raise AppError(
            413,
            "payload_too_large",
            "Quick action bundle must not exceed 1 MiB",
        )
    try:
        decoded = raw_bundle.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(
            422,
            "validation_error",
            "Quick action bundle must be UTF-8 JSON",
        ) from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise AppError(
            422,
            "validation_error",
            "Quick action bundle is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise AppError(
            422,
            "validation_error",
            "Quick action bundle root must be an object",
        )
    if payload.get("format") != QUICK_ACTION_BUNDLE_FORMAT:
        raise AppError(
            422,
            "validation_error",
            "Unsupported quick action bundle format",
            {"field": "format"},
        )
    format_version = payload.get("format_version")
    if (
        not isinstance(format_version, int)
        or isinstance(format_version, bool)
        or format_version != QUICK_ACTION_BUNDLE_FORMAT_VERSION
    ):
        raise AppError(
            422,
            "validation_error",
            "Unsupported quick action bundle version",
            {"field": "format_version"},
        )
    raw_entries = payload.get("quick_actions")
    if (
        not isinstance(raw_entries, list)
        or not raw_entries
        or len(raw_entries) > QUICK_ACTION_BUNDLE_MAX_ENTRIES
    ):
        raise AppError(
            422,
            "validation_error",
            "Quick action bundle must contain between 1 and 100 quick actions",
            {"field": "quick_actions"},
        )

    warnings = [
        {"path": key, "message": _IGNORED_FIELD_MESSAGE}
        for key in payload
        if key not in _BUNDLE_FIELDS
    ]
    entries: list[QuickActionBundleEntry | None] = []
    entry_issues: list[list[dict[str, str]]] = []
    for index, raw_entry in enumerate(raw_entries):
        if isinstance(raw_entry, dict):
            warnings.extend(
                {
                    "path": f"quick_actions[{index}].{key}",
                    "message": _IGNORED_FIELD_MESSAGE,
                }
                for key in raw_entry
                if key not in _ENTRY_FIELDS
            )
            raw_version = raw_entry.get("latest_version")
            if isinstance(raw_version, dict):
                warnings.extend(
                    {
                        "path": f"quick_actions[{index}].latest_version.{key}",
                        "message": _IGNORED_FIELD_MESSAGE,
                    }
                    for key in raw_version
                    if key not in _VERSION_FIELDS
                )
                cleaned_version: object = {
                    key: raw_version[key]
                    for key in _VERSION_FIELDS
                    if key in raw_version
                }
            else:
                cleaned_version = raw_version
            cleaned = {
                key: raw_entry[key] for key in _ENTRY_FIELDS if key in raw_entry
            }
            if "latest_version" in cleaned:
                cleaned["latest_version"] = cleaned_version
        else:
            cleaned = raw_entry

        issues: list[dict[str, str]] = []
        try:
            entry = QuickActionBundleEntry.model_validate(cleaned)
            _serialize_asset_name(entry.name)
            _serialize_prompt_text(entry.latest_version.prompt_text)
        except (ValidationError, AppError) as exc:
            if isinstance(exc, ValidationError):
                issues.extend(
                    {
                        "path": f"quick_actions[{index}]."
                        + ".".join(str(part) for part in error["loc"]),
                        "message": error["msg"],
                    }
                    for error in exc.errors()
                )
            else:
                field = str((exc.details or {}).get("field") or "")
                if field == "prompt_text":
                    path = (
                        f"quick_actions[{index}].latest_version.prompt_text"
                    )
                elif field:
                    path = f"quick_actions[{index}].{field}"
                else:
                    path = f"quick_actions[{index}]"
                issues.append({"path": path, "message": exc.message})
            entry = None
        entries.append(entry)
        entry_issues.append(issues)
    return entries, warnings, entry_issues


def _destination_quick_actions(
    db: Session,
    actor: User,
    *,
    destination: TemplateScope,
) -> list[QuickAction]:
    if destination is TemplateScope.team:
        _require_team_leader(actor)
        return list(
            db.scalars(
                select(QuickAction).where(
                    QuickAction.scope == TemplateScope.team,
                    QuickAction.team_id == actor.team_id,
                )
            )
        )
    if destination is TemplateScope.user:
        _require_team_member(actor)
        return list(
            db.scalars(
                select(QuickAction).where(
                    QuickAction.scope == TemplateScope.user,
                    QuickAction.owner_user_id == actor.id,
                )
            )
        )
    raise AppError(
        422,
        "validation_error",
        "Import destination must be personal or team",
        {"field": "destination"},
    )


def _import_content(entry: QuickActionBundleEntry) -> tuple[object, ...]:
    return (
        (entry.description or "").strip() or None,
        TemplateMode.freeform.value,
        _serialize_prompt_text(entry.latest_version.prompt_text),
    )


def _stored_quick_action_content(
    db: Session,
    quick_action: QuickAction,
) -> tuple[object, ...]:
    version = _latest_quick_action_version(
        db,
        quick_action_id=quick_action.id,
    )
    return (
        (quick_action.description or "").strip() or None,
        version.mode.value,
        version.prompt_text.strip(),
    )


def _copy_name(name: str, reserved: set[str]) -> str:
    copy_no = 2
    suffix = f" copy {copy_no}"
    proposed_name = f"{name[: 255 - len(suffix)].rstrip()}{suffix}"
    while proposed_name.lower() in reserved:
        copy_no += 1
        suffix = f" copy {copy_no}"
        proposed_name = f"{name[: 255 - len(suffix)].rstrip()}{suffix}"
    return proposed_name


def plan_quick_action_bundle_import(
    db: Session,
    actor: User,
    *,
    destination: TemplateScope,
    raw_bundle: bytes,
) -> dict[str, object]:
    entries, warnings, entry_issues = parse_quick_action_bundle(raw_bundle)
    existing = _destination_quick_actions(
        db,
        actor,
        destination=destination,
    )
    by_name = {
        quick_action.name.strip().lower(): quick_action
        for quick_action in existing
    }
    reserved = set(by_name)
    preview: list[dict[str, object]] = []
    counts = {
        "total": len(entries),
        "importable": 0,
        "exact_copies": 0,
        "invalid": 0,
        "renamed": 0,
        "unknown_fields": len(warnings),
    }

    for index, entry in enumerate(entries):
        if entry is None:
            preview.append(
                {
                    "index": index,
                    "source_name": None,
                    "proposed_name": None,
                    "status": "invalid",
                    "selected_by_default": False,
                    "selectable": False,
                    "errors": entry_issues[index],
                    "warnings": [],
                }
            )
            counts["invalid"] += 1
            continue

        name = _serialize_asset_name(entry.name)
        normalized = name.lower()
        matched = by_name.get(normalized)
        exact = bool(
            matched
            and matched.is_active
            and _stored_quick_action_content(db, matched)
            == _import_content(entry)
        )
        proposed_name = name
        status = "ready"
        if normalized in reserved:
            proposed_name = _copy_name(name, reserved)
            status = "exact_copy" if exact else "renamed"
        reserved.add(proposed_name.lower())

        if status == "exact_copy":
            counts["exact_copies"] += 1
        elif status == "renamed":
            counts["renamed"] += 1
        counts["importable"] += 1
        preview.append(
            {
                "index": index,
                "source_name": name,
                "proposed_name": proposed_name,
                "status": status,
                "selected_by_default": not exact,
                "selectable": True,
                "errors": [],
                "warnings": [],
            }
        )
    return {
        "entries": preview,
        "warnings": warnings,
        "summary": counts,
    }


def import_quick_action_bundle(
    db: Session,
    actor: User,
    *,
    destination: TemplateScope,
    raw_bundle: bytes,
    selected_indexes: list[int],
) -> dict[str, object]:
    plan = plan_quick_action_bundle_import(
        db,
        actor,
        destination=destination,
        raw_bundle=raw_bundle,
    )
    if not isinstance(selected_indexes, list) or any(
        not isinstance(index, int) or isinstance(index, bool)
        for index in selected_indexes
    ):
        raise AppError(
            422,
            "validation_error",
            "Selected quick action indexes must be integers",
            {"field": "selected_indexes"},
        )
    selected = list(dict.fromkeys(selected_indexes))
    if not selected:
        raise AppError(
            422,
            "validation_error",
            "Select at least one quick action to import",
            {"field": "selected_indexes"},
        )
    preview_by_index = {
        item["index"]: item for item in plan["entries"]
    }
    if any(
        index not in preview_by_index
        or not preview_by_index[index]["selectable"]
        for index in selected
    ):
        raise AppError(
            422,
            "validation_error",
            "Selected quick action indexes include an invalid entry",
            {"field": "selected_indexes"},
        )

    entries, warnings, _ = parse_quick_action_bundle(raw_bundle)
    created: list[dict[str, object]] = []
    try:
        for index in selected:
            entry = entries[index]
            assert entry is not None
            planned = preview_by_index[index]
            quick_action = QuickAction(
                id=uuid4(),
                scope=destination,
                owner_user_id=(
                    actor.id
                    if destination is TemplateScope.user
                    else None
                ),
                team_id=(
                    actor.team_id
                    if destination is TemplateScope.team
                    else None
                ),
                name=planned["proposed_name"],
                description=(entry.description or "").strip() or None,
                is_active=True,
                created_by_user_id=actor.id,
            )
            db.add(quick_action)
            db.flush()
            db.add(
                QuickActionVersion(
                    id=uuid4(),
                    quick_action_id=quick_action.id,
                    version_no=1,
                    mode=TemplateMode.freeform,
                    prompt_text=_serialize_prompt_text(
                        entry.latest_version.prompt_text
                    ),
                    created_by_user_id=actor.id,
                )
            )
            created.append(
                {
                    "index": index,
                    "quick_action_id": str(quick_action.id),
                    "name": quick_action.name,
                }
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _translate_quick_action_integrity_error(exc)
    except Exception:
        db.rollback()
        raise

    record_security_event(
        db,
        action="quick_action_bundle_imported",
        actor=actor,
        team_id=actor.team_id,
        details={
            "category": "template",
            "outcome": "success",
            "object_type": "quick_action",
            "destination": destination.value,
            "selected_count": len(selected),
            "imported_count": len(created),
            "skipped_count": len(entries) - len(selected),
            "warning_count": len(warnings),
            "object_ids": [
                item["quick_action_id"] for item in created
            ],
        },
    )
    return {
        "created": created,
        "skipped_indexes": [
            index
            for index in range(len(entries))
            if index not in selected
        ],
        "warnings": warnings,
        "summary": {
            "selected": len(selected),
            "imported": len(created),
            "skipped": len(entries) - len(selected),
            "warning_count": len(warnings),
        },
    }
