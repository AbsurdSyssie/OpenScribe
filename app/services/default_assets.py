import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.default_assets import PRIMARY_TEMPLATE_NAME, RETIRED_TEMPLATE_NAMES
from app.default_assets.catalog import (
    BUILTIN_QUICK_ACTION_BUNDLE,
    BUILTIN_TEMPLATE_BUNDLE,
)
from app.errors import AppError
from app.models import (
    DefaultPromptTemplate,
    DefaultPromptTemplateVersion,
    DefaultQuickAction,
    DefaultQuickActionVersion,
    GeneratedDocument,
    GeneratedDocumentStatus,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    Team,
    TemplateMode,
    TemplateScope,
    User,
)
from app.normalization import normalize_team_name_key
from app.schemas.templates import (
    DefaultPromptTemplateUpsert,
    DefaultQuickActionUpsert,
)
from app.services.quick_action_io import parse_quick_action_bundle
from app.services.security_audit import record_security_event
from app.services.templates import (
    _detach_generated_documents_from_template,
    _latest_quick_action_version,
    _latest_template_version,
    _serialize_asset_name,
    _serialize_prompt_text,
    _serialize_template_config,
    _split_duplicate_asset_name,
    _template_version_config,
    parse_template_bundle,
)


def _load_builtin_templates() -> tuple[dict[str, object], ...]:
    entries, warnings, issues = parse_template_bundle(
        json.dumps(BUILTIN_TEMPLATE_BUNDLE).encode("utf-8")
    )
    if warnings or any(issues) or any(entry is None for entry in entries):
        raise RuntimeError("The built-in Template catalogue is invalid.")
    names = [entry.name.strip().lower() for entry in entries if entry is not None]
    if len(names) != len(set(names)) or PRIMARY_TEMPLATE_NAME.lower() not in names:
        raise RuntimeError("The built-in Template catalogue has invalid names.")
    templates = []
    for entry in entries:
        assert entry is not None
        config = (
            entry.latest_version.config_json.model_dump(mode="json")
            if entry.latest_version.config_json is not None
            else None
        )
        templates.append(
            {
                "name": entry.name,
                "description": entry.description,
                "prompt_text": entry.latest_version.prompt_text,
                "mode": entry.latest_version.mode,
                "config_json": config,
            }
        )
    templates.sort(key=lambda item: item["name"] == PRIMARY_TEMPLATE_NAME)
    return tuple(templates)


def _load_builtin_quick_actions() -> tuple[dict[str, object], ...]:
    entries, warnings, issues = parse_quick_action_bundle(
        json.dumps(BUILTIN_QUICK_ACTION_BUNDLE).encode("utf-8")
    )
    if warnings or any(issues) or any(entry is None for entry in entries):
        raise RuntimeError("The built-in Quick Action catalogue is invalid.")
    names = [entry.name.strip().lower() for entry in entries if entry is not None]
    if len(names) != len(set(names)):
        raise RuntimeError("The built-in Quick Action catalogue has duplicate names.")
    return tuple(
        {
            "name": entry.name,
            "description": entry.description,
            "prompt_text": entry.latest_version.prompt_text,
        }
        for entry in entries
        if entry is not None
    )


BUILTIN_DEFAULT_TEMPLATES = _load_builtin_templates()
BUILTIN_DEFAULT_TEMPLATE = next(
    template
    for template in BUILTIN_DEFAULT_TEMPLATES
    if template["name"] == PRIMARY_TEMPLATE_NAME
)
BUILTIN_DEFAULT_QUICK_ACTIONS = _load_builtin_quick_actions()

_LEGACY_BUILTIN_TEMPLATE = {
    "name": "Sectioned EMIS note",
    "description": "Starter structured consultation note for EMIS transfer.",
    "prompt_text": (
        "Write a concise EMIS-ready consultation note from the provided clinical "
        "sources. Include only clinically supported information and omit empty "
        "sections."
    ),
    "mode": TemplateMode.structured,
    "config_json": {
        "profile": "emis",
        "sections": [
            {
                "section_key": "problem",
                "instruction": "Summarise presenting problems and diagnoses.",
                "section_order": 1,
            },
            {
                "section_key": "history",
                "instruction": "Summarise relevant history, symptoms, and timeline.",
                "section_order": 2,
            },
            {
                "section_key": "family_history",
                "instruction": "Include relevant family history only when mentioned.",
                "section_order": 3,
            },
            {
                "section_key": "social_history",
                "instruction": (
                    "Include relevant social context, occupation, smoking, alcohol, "
                    "and support details only when mentioned."
                ),
                "section_order": 4,
            },
            {
                "section_key": "examination",
                "instruction": "Summarise examination findings and observations.",
                "section_order": 5,
            },
            {
                "section_key": "comment",
                "instruction": (
                    "Summarise assessment, safety-netting, and plan narrative."
                ),
                "section_order": 6,
            },
            {
                "section_key": "tasks",
                "instruction": (
                    "List agreed actions, referrals, prescriptions, and follow-up "
                    "tasks."
                ),
                "section_order": 7,
            },
            {
                "section_key": "investigations",
                "instruction": (
                    "List investigations ordered, reviewed, or discussed."
                ),
                "section_order": 8,
            },
        ],
    },
}

_LEGACY_BUILTIN_QUICK_ACTIONS = {
    "Patient follow-up message": {
        "description": "Draft a short patient-facing follow-up message.",
        "prompt_text": (
            "Draft a concise patient-facing follow-up message from the consultation. "
            "Use clear language, include agreed next steps, and avoid adding information "
            "not present in the transcript or note. Reading age of 10. Very short, for SMS."
        ),
    },
    "Referral letter": {
        "description": "Draft a referral letter from the consultation.",
        "prompt_text": (
            "Draft a referral letter using the consultation context. Include reason for "
            "referral, relevant history, examination, investigations, current plan, and "
            "requested action. Do not invent details."
        ),
    },
}


@dataclass(slots=True)
class DefaultAssetImportSummary:
    source_team_id: UUID
    source_team_name: str
    templates_imported: int
    quick_actions_imported: int


def _require_system_admin(actor: User) -> None:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "Default asset management requires a system admin account")


def _next_default_template_version_no(db: Session, *, template_id: UUID) -> int:
    current_max = db.scalar(select(func.max(DefaultPromptTemplateVersion.version_no)).where(DefaultPromptTemplateVersion.default_template_id == template_id))
    return (current_max or 0) + 1


def _next_default_quick_action_version_no(db: Session, *, quick_action_id: UUID) -> int:
    current_max = db.scalar(select(func.max(DefaultQuickActionVersion.version_no)).where(DefaultQuickActionVersion.default_quick_action_id == quick_action_id))
    return (current_max or 0) + 1


def _resolve_default_template(db: Session, *, template_id: UUID) -> DefaultPromptTemplate:
    template = db.get(DefaultPromptTemplate, template_id)
    if template is None:
        raise AppError(404, "not_found", "Default template not found", {"resource": "default_template", "template_id": str(template_id)})
    return template


def _resolve_default_quick_action(db: Session, *, quick_action_id: UUID) -> DefaultQuickAction:
    quick_action = db.get(DefaultQuickAction, quick_action_id)
    if quick_action is None:
        raise AppError(404, "not_found", "Default quick action not found", {"resource": "default_quick_action", "quick_action_id": str(quick_action_id)})
    return quick_action


def _resolve_source_team(db: Session, *, team_name: str) -> Team:
    normalized_name = normalize_team_name_key(team_name)
    team = db.scalar(select(Team).where(Team.name_key == normalized_name))
    if team is None:
        raise AppError(404, "not_found", "Source team not found", {"resource": "team", "team_name": team_name})
    return team


def _ensure_unique_default_template_name(db: Session, *, name: str, current_template_id: UUID | None = None) -> None:
    duplicate = db.scalar(
        select(DefaultPromptTemplate).where(
            func.lower(DefaultPromptTemplate.name) == name.strip().lower(),
            DefaultPromptTemplate.id != current_template_id if current_template_id is not None else True,
        )
    )
    if duplicate is not None:
        raise AppError(409, "conflict", "Default template name already exists", {"resource": "default_template", "field": "name"})


def _ensure_unique_default_quick_action_name(db: Session, *, name: str, current_quick_action_id: UUID | None = None) -> None:
    duplicate = db.scalar(
        select(DefaultQuickAction).where(
            func.lower(DefaultQuickAction.name) == name.strip().lower(),
            DefaultQuickAction.id != current_quick_action_id if current_quick_action_id is not None else True,
        )
    )
    if duplicate is not None:
        raise AppError(409, "conflict", "Default quick action name already exists", {"resource": "default_quick_action", "field": "name"})


def _latest_default_template_version(db: Session, *, template_id: UUID) -> DefaultPromptTemplateVersion:
    version = db.scalar(
        select(DefaultPromptTemplateVersion)
        .where(DefaultPromptTemplateVersion.default_template_id == template_id)
        .order_by(DefaultPromptTemplateVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise AppError(404, "not_found", "Default template version not found", {"resource": "default_template_version", "template_id": str(template_id)})
    return version


def _latest_default_quick_action_version(db: Session, *, quick_action_id: UUID) -> DefaultQuickActionVersion:
    version = db.scalar(
        select(DefaultQuickActionVersion)
        .where(DefaultQuickActionVersion.default_quick_action_id == quick_action_id)
        .order_by(DefaultQuickActionVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise AppError(404, "not_found", "Default quick action version not found", {"resource": "default_quick_action_version", "quick_action_id": str(quick_action_id)})
    return version


def _next_duplicate_default_name(existing_names: list[str], source_name: str) -> str:
    base_name, parsed_suffix = _split_duplicate_asset_name(source_name)
    candidate_base = base_name if parsed_suffix else source_name.strip()
    normalized_existing = {str(name or "").strip().lower() for name in existing_names}
    next_index = 2
    while f"{candidate_base} {next_index}".strip().lower() in normalized_existing:
        next_index += 1
    return f"{candidate_base} {next_index}"


def _default_template_exists(db: Session, *, name: str) -> bool:
    normalized_name = _serialize_asset_name(name)
    return db.scalar(select(DefaultPromptTemplate.id).where(func.lower(DefaultPromptTemplate.name) == normalized_name.lower()).limit(1)) is not None


def _default_quick_action_exists(db: Session, *, name: str) -> bool:
    normalized_name = _serialize_asset_name(name)
    return db.scalar(select(DefaultQuickAction.id).where(func.lower(DefaultQuickAction.name) == normalized_name.lower()).limit(1)) is not None


def _team_template_exists(db: Session, *, team: Team, name: str) -> bool:
    normalized_name = _serialize_asset_name(name)
    return (
        db.scalar(
            select(PromptTemplate.id)
            .where(
                PromptTemplate.scope == TemplateScope.team,
                PromptTemplate.team_id == team.id,
                func.lower(PromptTemplate.name) == normalized_name.lower(),
            )
            .limit(1)
        )
        is not None
    )


def _team_quick_action_exists(db: Session, *, team: Team, name: str) -> bool:
    normalized_name = _serialize_asset_name(name)
    return (
        db.scalar(
            select(QuickAction.id)
            .where(
                QuickAction.scope == TemplateScope.team,
                QuickAction.team_id == team.id,
                func.lower(QuickAction.name) == normalized_name.lower(),
            )
            .limit(1)
        )
        is not None
    )


def _default_quick_action_by_name(
    db: Session, *, name: str
) -> DefaultQuickAction | None:
    return db.scalar(
        select(DefaultQuickAction)
        .where(func.lower(DefaultQuickAction.name) == name.strip().lower())
        .limit(1)
    )


def _team_quick_action_by_name(
    db: Session, *, team: Team, name: str
) -> QuickAction | None:
    return db.scalar(
        select(QuickAction)
        .where(
            QuickAction.scope == TemplateScope.team,
            QuickAction.team_id == team.id,
            func.lower(QuickAction.name) == name.strip().lower(),
        )
        .limit(1)
    )


def _upgrade_legacy_default_quick_action(
    db: Session,
    *,
    quick_action: DefaultQuickAction,
    built_in: dict[str, object],
    actor: User,
) -> None:
    legacy = _LEGACY_BUILTIN_QUICK_ACTIONS.get(quick_action.name)
    if (
        legacy is None
        or not quick_action.is_active
        or quick_action.description != legacy["description"]
    ):
        return
    latest = _latest_default_quick_action_version(
        db, quick_action_id=quick_action.id
    )
    version_count = db.scalar(
        select(func.count())
        .select_from(DefaultQuickActionVersion)
        .where(
            DefaultQuickActionVersion.default_quick_action_id == quick_action.id
        )
    )
    if (
        version_count != 1
        or latest.mode is not TemplateMode.freeform
        or latest.prompt_text != legacy["prompt_text"]
    ):
        return
    quick_action.description = built_in["description"]
    db.add(quick_action)
    db.add(
        DefaultQuickActionVersion(
            id=uuid4(),
            default_quick_action_id=quick_action.id,
            version_no=_next_default_quick_action_version_no(
                db, quick_action_id=quick_action.id
            ),
            mode=TemplateMode.freeform,
            prompt_text=built_in["prompt_text"],
            created_by_user_id=actor.id,
        )
    )


def _upgrade_legacy_team_quick_action(
    db: Session,
    *,
    quick_action: QuickAction,
    built_in: dict[str, object],
    actor: User,
) -> None:
    legacy = _LEGACY_BUILTIN_QUICK_ACTIONS.get(quick_action.name)
    if (
        legacy is None
        or not quick_action.is_active
        or quick_action.description != legacy["description"]
    ):
        return
    latest = _latest_quick_action_version(db, quick_action_id=quick_action.id)
    version_count = db.scalar(
        select(func.count())
        .select_from(QuickActionVersion)
        .where(QuickActionVersion.quick_action_id == quick_action.id)
    )
    if (
        version_count != 1
        or latest.mode is not TemplateMode.freeform
        or latest.prompt_text != legacy["prompt_text"]
    ):
        return
    quick_action.description = built_in["description"]
    db.add(quick_action)
    db.add(
        QuickActionVersion(
            id=uuid4(),
            quick_action_id=quick_action.id,
            version_no=latest.version_no + 1,
            mode=TemplateMode.freeform,
            prompt_text=built_in["prompt_text"],
            created_by_user_id=actor.id,
        )
    )


def _retire_default_templates(db: Session) -> None:
    retired_names = {name.lower() for name in RETIRED_TEMPLATE_NAMES}
    for template in db.scalars(select(DefaultPromptTemplate)):
        if template.name.strip().lower() not in retired_names:
            continue
        latest = _latest_default_template_version(db, template_id=template.id)
        version_count = db.scalar(
            select(func.count())
            .select_from(DefaultPromptTemplateVersion)
            .where(
                DefaultPromptTemplateVersion.default_template_id == template.id
            )
        )
        if (
            template.is_active
            and template.description == _LEGACY_BUILTIN_TEMPLATE["description"]
            and version_count == 1
            and latest.prompt_text == _LEGACY_BUILTIN_TEMPLATE["prompt_text"]
            and latest.mode is _LEGACY_BUILTIN_TEMPLATE["mode"]
            and latest.config_json == _LEGACY_BUILTIN_TEMPLATE["config_json"]
        ):
            db.delete(template)
    db.flush()


def _retire_team_templates(db: Session, *, team: Team) -> None:
    retired_names = {name.lower() for name in RETIRED_TEMPLATE_NAMES}
    for template in db.scalars(
        select(PromptTemplate).where(
            PromptTemplate.scope == TemplateScope.team,
            PromptTemplate.team_id == team.id,
        )
    ):
        if template.name.strip().lower() not in retired_names:
            continue
        latest = _latest_template_version(db, template_id=template.id)
        version_count = db.scalar(
            select(func.count())
            .select_from(PromptTemplateVersion)
            .where(PromptTemplateVersion.template_id == template.id)
        )
        if (
            not template.is_active
            or template.description != _LEGACY_BUILTIN_TEMPLATE["description"]
            or version_count != 1
            or latest.prompt_text != _LEGACY_BUILTIN_TEMPLATE["prompt_text"]
            or latest.mode is not _LEGACY_BUILTIN_TEMPLATE["mode"]
            or latest.config_json != _LEGACY_BUILTIN_TEMPLATE["config_json"]
        ):
            continue
        version_ids = select(PromptTemplateVersion.id).where(
            PromptTemplateVersion.template_id == template.id
        )
        active_document_id = db.scalar(
            select(GeneratedDocument.id)
            .where(
                GeneratedDocument.template_version_id.in_(version_ids),
                GeneratedDocument.status.in_(
                    (
                        GeneratedDocumentStatus.queued,
                        GeneratedDocumentStatus.processing,
                    )
                ),
            )
            .limit(1)
        )
        if active_document_id is not None:
            continue
        _detach_generated_documents_from_template(db, template_id=template.id)
        db.delete(template)
    db.flush()


def ensure_builtin_default_assets(db: Session, actor: User) -> None:
    _require_system_admin(actor)
    _retire_default_templates(db)
    for built_in in BUILTIN_DEFAULT_TEMPLATES:
        if _default_template_exists(db, name=built_in["name"]):
            continue
        template = DefaultPromptTemplate(
            id=uuid4(),
            name=built_in["name"],
            description=built_in["description"],
            is_active=True,
            created_by_user_id=actor.id,
        )
        db.add(template)
        db.flush()
        db.add(
            DefaultPromptTemplateVersion(
                id=uuid4(),
                default_template_id=template.id,
                version_no=1,
                mode=built_in["mode"],
                prompt_text=built_in["prompt_text"],
                config_json=built_in["config_json"],
                created_by_user_id=actor.id,
            )
        )

    for built_in in BUILTIN_DEFAULT_QUICK_ACTIONS:
        existing = _default_quick_action_by_name(db, name=built_in["name"])
        if existing is not None:
            _upgrade_legacy_default_quick_action(
                db, quick_action=existing, built_in=built_in, actor=actor
            )
            continue
        quick_action = DefaultQuickAction(
            id=uuid4(),
            name=built_in["name"],
            description=built_in["description"],
            is_active=True,
            created_by_user_id=actor.id,
        )
        db.add(quick_action)
        db.flush()
        db.add(
            DefaultQuickActionVersion(
                id=uuid4(),
                default_quick_action_id=quick_action.id,
                version_no=1,
                mode=TemplateMode.freeform,
                prompt_text=built_in["prompt_text"],
                created_by_user_id=actor.id,
            )
        )


def ensure_builtin_team_assets(db: Session, *, team: Team, actor: User) -> None:
    _retire_team_templates(db, team=team)
    for built_in in BUILTIN_DEFAULT_TEMPLATES:
        if _team_template_exists(db, team=team, name=built_in["name"]):
            continue
        template = PromptTemplate(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=team.id,
            name=built_in["name"],
            description=built_in["description"],
            is_active=True,
            created_by_user_id=actor.id,
        )
        db.add(template)
        db.flush()
        db.add(
            PromptTemplateVersion(
                id=uuid4(),
                template_id=template.id,
                version_no=1,
                mode=built_in["mode"],
                prompt_text=built_in["prompt_text"],
                config_json=built_in["config_json"],
                created_by_user_id=actor.id,
            )
        )

    for built_in in BUILTIN_DEFAULT_QUICK_ACTIONS:
        existing = _team_quick_action_by_name(
            db, team=team, name=built_in["name"]
        )
        if existing is not None:
            _upgrade_legacy_team_quick_action(
                db, quick_action=existing, built_in=built_in, actor=actor
            )
            continue
        quick_action = QuickAction(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=team.id,
            name=built_in["name"],
            description=built_in["description"],
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
                prompt_text=built_in["prompt_text"],
                created_by_user_id=actor.id,
            )
        )


def list_default_templates(db: Session, actor: User) -> list[DefaultPromptTemplate]:
    _require_system_admin(actor)
    return list(db.scalars(select(DefaultPromptTemplate).order_by(DefaultPromptTemplate.updated_at.desc(), DefaultPromptTemplate.id.desc())))


def list_default_quick_actions(db: Session, actor: User) -> list[DefaultQuickAction]:
    _require_system_admin(actor)
    return list(db.scalars(select(DefaultQuickAction).order_by(DefaultQuickAction.updated_at.desc(), DefaultQuickAction.id.desc())))


def upsert_default_template(db: Session, actor: User, payload: DefaultPromptTemplateUpsert) -> DefaultPromptTemplate:
    _require_system_admin(actor)
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    template_name = _serialize_asset_name(payload.name)
    config_json = _serialize_template_config(payload)
    template = _resolve_default_template(db, template_id=payload.template_id) if payload.template_id else None
    created = template is None
    _ensure_unique_default_template_name(db, name=template_name, current_template_id=template.id if template is not None else None)
    if template is None:
        template = DefaultPromptTemplate(
            id=uuid4(),
            name=template_name,
            description=(payload.description or "").strip() or None,
            is_active=payload.is_active,
            created_by_user_id=actor.id,
        )
        db.add(template)
        db.flush()
    else:
        template.name = template_name
        template.description = (payload.description or "").strip() or None
        template.is_active = payload.is_active
        db.add(template)

    version = DefaultPromptTemplateVersion(
        id=uuid4(),
        default_template_id=template.id,
        version_no=_next_default_template_version_no(db, template_id=template.id),
        mode=payload.mode,
        prompt_text=prompt_text,
        config_json=config_json,
        created_by_user_id=actor.id,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Default template changed during save. Retry.", {"resource": "default_template"}) from exc
    db.refresh(template)
    record_security_event(
        db,
        action="default_template_created" if created else "default_template_updated",
        actor=actor,
        details={
            "category": "template",
            "outcome": "success",
            "object_type": "default_prompt_template",
            "object_id": str(template.id),
            "mode": payload.mode.value,
            "active": bool(template.is_active),
        },
    )
    return template


def upsert_default_quick_action(db: Session, actor: User, payload: DefaultQuickActionUpsert) -> DefaultQuickAction:
    _require_system_admin(actor)
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    quick_action_name = _serialize_asset_name(payload.name)
    quick_action = _resolve_default_quick_action(db, quick_action_id=payload.quick_action_id) if payload.quick_action_id else None
    created = quick_action is None
    _ensure_unique_default_quick_action_name(db, name=quick_action_name, current_quick_action_id=quick_action.id if quick_action is not None else None)
    if quick_action is None:
        quick_action = DefaultQuickAction(
            id=uuid4(),
            name=quick_action_name,
            description=(payload.description or "").strip() or None,
            is_active=payload.is_active,
            created_by_user_id=actor.id,
        )
        db.add(quick_action)
        db.flush()
    else:
        quick_action.name = quick_action_name
        quick_action.description = (payload.description or "").strip() or None
        quick_action.is_active = payload.is_active
        db.add(quick_action)

    version = DefaultQuickActionVersion(
        id=uuid4(),
        default_quick_action_id=quick_action.id,
        version_no=_next_default_quick_action_version_no(db, quick_action_id=quick_action.id),
        mode=TemplateMode.freeform,
        prompt_text=prompt_text,
        created_by_user_id=actor.id,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Default quick action changed during save. Retry.", {"resource": "default_quick_action"}) from exc
    db.refresh(quick_action)
    record_security_event(
        db,
        action="default_quick_action_created" if created else "default_quick_action_updated",
        actor=actor,
        details={
            "category": "template",
            "outcome": "success",
            "object_type": "default_quick_action",
            "object_id": str(quick_action.id),
            "mode": TemplateMode.freeform.value,
            "active": bool(quick_action.is_active),
        },
    )
    return quick_action


def delete_default_template(db: Session, actor: User, *, template_id: UUID) -> None:
    _require_system_admin(actor)
    template = _resolve_default_template(db, template_id=template_id)
    deleted_id = template.id
    db.delete(template)
    db.commit()
    record_security_event(db, action="default_template_deleted", actor=actor, details={"category": "template", "outcome": "success", "object_type": "default_prompt_template", "object_id": str(deleted_id)})


def delete_default_quick_action(db: Session, actor: User, *, quick_action_id: UUID) -> None:
    _require_system_admin(actor)
    quick_action = _resolve_default_quick_action(db, quick_action_id=quick_action_id)
    deleted_id = quick_action.id
    db.delete(quick_action)
    db.commit()
    record_security_event(db, action="default_quick_action_deleted", actor=actor, details={"category": "template", "outcome": "success", "object_type": "default_quick_action", "object_id": str(deleted_id)})


def duplicate_default_template(db: Session, actor: User, *, template_id: UUID) -> DefaultPromptTemplate:
    _require_system_admin(actor)
    template = _resolve_default_template(db, template_id=template_id)
    latest_version = _latest_default_template_version(db, template_id=template.id)
    return upsert_default_template(
        db,
        actor,
        DefaultPromptTemplateUpsert(
            name=_next_duplicate_default_name(list(db.scalars(select(DefaultPromptTemplate.name))), template.name),
            description=template.description,
            prompt_text=latest_version.prompt_text,
            mode=latest_version.mode,
            config_json=_template_version_config(latest_version),
            is_active=template.is_active,
        ),
    )


def duplicate_default_quick_action(db: Session, actor: User, *, quick_action_id: UUID) -> DefaultQuickAction:
    _require_system_admin(actor)
    quick_action = _resolve_default_quick_action(db, quick_action_id=quick_action_id)
    latest_version = _latest_default_quick_action_version(db, quick_action_id=quick_action.id)
    return upsert_default_quick_action(
        db,
        actor,
        DefaultQuickActionUpsert(
            name=_next_duplicate_default_name(list(db.scalars(select(DefaultQuickAction.name))), quick_action.name),
            description=quick_action.description,
            prompt_text=latest_version.prompt_text,
            is_active=quick_action.is_active,
        ),
    )


def import_team_assets_to_defaults(db: Session, actor: User, *, source_team_name: str) -> DefaultAssetImportSummary:
    _require_system_admin(actor)
    source_team = _resolve_source_team(db, team_name=source_team_name)
    imported_template_count = 0
    imported_quick_action_count = 0
    templates = list(
        db.scalars(
            select(PromptTemplate)
            .where(PromptTemplate.scope == TemplateScope.team, PromptTemplate.team_id == source_team.id)
            .order_by(PromptTemplate.updated_at.asc(), PromptTemplate.id.asc())
        )
    )
    quick_actions = list(
        db.scalars(
            select(QuickAction)
            .where(QuickAction.scope == TemplateScope.team, QuickAction.team_id == source_team.id)
            .order_by(QuickAction.updated_at.asc(), QuickAction.id.asc())
        )
    )

    for template in templates:
        normalized_template_name = _serialize_asset_name(template.name)
        if _default_template_exists(db, name=normalized_template_name):
            continue
        latest_version = _latest_template_version(db, template_id=template.id)
        imported_template = DefaultPromptTemplate(
            id=uuid4(),
            name=normalized_template_name,
            description=template.description,
            is_active=template.is_active,
            created_by_user_id=actor.id,
        )
        db.add(imported_template)
        db.flush()
        db.add(
            DefaultPromptTemplateVersion(
                id=uuid4(),
                default_template_id=imported_template.id,
                version_no=1,
                mode=latest_version.mode,
                prompt_text=latest_version.prompt_text,
                config_json=latest_version.config_json,
                created_by_user_id=actor.id,
            )
        )
        imported_template_count += 1

    for quick_action in quick_actions:
        normalized_quick_action_name = _serialize_asset_name(quick_action.name)
        if _default_quick_action_exists(db, name=normalized_quick_action_name):
            continue
        latest_version = _latest_quick_action_version(db, quick_action_id=quick_action.id)
        imported_quick_action = DefaultQuickAction(
            id=uuid4(),
            name=normalized_quick_action_name,
            description=quick_action.description,
            is_active=quick_action.is_active,
            created_by_user_id=actor.id,
        )
        db.add(imported_quick_action)
        db.flush()
        db.add(
            DefaultQuickActionVersion(
                id=uuid4(),
                default_quick_action_id=imported_quick_action.id,
                version_no=1,
                mode=TemplateMode.freeform,
                prompt_text=latest_version.prompt_text,
                created_by_user_id=actor.id,
            )
        )
        imported_quick_action_count += 1

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Default assets changed during import. Retry.", {"resource": "default_asset_import"}) from exc

    return DefaultAssetImportSummary(
        source_team_id=source_team.id,
        source_team_name=source_team.name,
        templates_imported=imported_template_count,
        quick_actions_imported=imported_quick_action_count,
    )


def seed_team_default_assets(db: Session, *, team: Team, actor: User) -> None:
    _require_system_admin(actor)
    _retire_team_templates(db, team=team)
    default_templates = list(db.scalars(select(DefaultPromptTemplate).where(DefaultPromptTemplate.is_active.is_(True)).order_by(DefaultPromptTemplate.updated_at.asc(), DefaultPromptTemplate.id.asc())))
    default_templates.sort(key=lambda template: template.name == PRIMARY_TEMPLATE_NAME)
    for default_template in default_templates:
        if _team_template_exists(db, team=team, name=default_template.name):
            continue
        latest_version = _latest_default_template_version(db, template_id=default_template.id)
        template = PromptTemplate(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=team.id,
            name=default_template.name,
            description=default_template.description,
            is_active=True,
            created_by_user_id=actor.id,
        )
        db.add(template)
        db.flush()
        db.add(
            PromptTemplateVersion(
                id=uuid4(),
                template_id=template.id,
                version_no=1,
                mode=latest_version.mode,
                prompt_text=latest_version.prompt_text,
                config_json=latest_version.config_json,
                created_by_user_id=actor.id,
            )
        )

    default_quick_actions = list(db.scalars(select(DefaultQuickAction).where(DefaultQuickAction.is_active.is_(True)).order_by(DefaultQuickAction.updated_at.asc(), DefaultQuickAction.id.asc())))
    for default_quick_action in default_quick_actions:
        if _team_quick_action_exists(db, team=team, name=default_quick_action.name):
            continue
        latest_version = _latest_default_quick_action_version(db, quick_action_id=default_quick_action.id)
        quick_action = QuickAction(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=team.id,
            name=default_quick_action.name,
            description=default_quick_action.description,
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
                prompt_text=latest_version.prompt_text,
                created_by_user_id=actor.id,
            )
        )
