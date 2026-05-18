import json
import logging
import re
import time
from datetime import timezone
from typing import Any, TypedDict
from uuid import UUID, uuid4

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    GeneratedDocument,
    GeneratedDocumentSection,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    LlmAdapterKind,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    ProviderFeatureType,
    ProviderUsageEvent,
    ProviderUsageEventType,
    TeamLlmConfig,
    TeamRole,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptManualPiiEntity,
    TranscriptStatus,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
    utcnow,
)
from app.schemas.templates import (
    EMIS_SECTION_KEYS,
    EMIS_SECTION_LABELS,
    GeneratedDocumentUpdateRequest,
    PromptTemplateUpsert,
    QuickActionUpsert,
    StructuredTemplateConfig,
)
from app.services.llm import resolve_user_llm
from app.services.content_crypto import decrypt_json_for_owner, decrypt_text_for_owner, encrypt_json_for_owner, encrypt_text_for_owner
from app.services.dictations import dictation_effective_text, get_post_consultation_dictation
from app.services.redaction import (
    combined_phi_index,
    ensure_redaction_run_for_transcript_version,
    next_placeholder_index,
    redaction_run_text,
    redact_transient_text,
    reidentify_text,
)
from app.services.transcripts import (
    manual_pii_entity_value,
    freeform_working_note_text,
    normalize_structured_working_note,
    set_transcript_structured_context,
    transcript_structured_context,
    transcript_version_text,
)
from app.services.vault import read_team_llm_bearer_token


usage_logger = logging.getLogger("openscribe.usage")

TEMPLATE_NAME_CONSTRAINTS = {"uq_templates_team_name_lower", "uq_templates_owner_name_lower"}
QUICK_ACTION_NAME_CONSTRAINTS = {"uq_quick_actions_team_name_lower", "uq_quick_actions_owner_name_lower"}
DICTATION_SOURCE_SPLIT_MARKER = "\n\n<<<POST_CONSULTATION_DICTATION_SPLIT>>>\n\n"
QUICK_ACTION_CONTEXT_MARKER = "\n\nAdditional context:\n"


class GenerationUsage(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None
    provider_duration_ms: int | None


def _structured_section_definitions_snapshot(template_config: StructuredTemplateConfig | None) -> dict | None:
    if template_config is None:
        return None
    return {
        "profile": template_config.profile,
        "sections": [
            {
                "section_key": section.section_key,
                "section_label": section.section_label,
                "section_order": section.section_order,
            }
            for section in sorted(template_config.sections, key=lambda item: item.section_order)
        ],
    }


def _allowed_structured_section_keys(document: GeneratedDocument, db: Session) -> set[str]:
    snapshot = document.structured_section_definitions_json
    if isinstance(snapshot, dict):
        sections = snapshot.get("sections")
        if isinstance(sections, list):
            keys = {
                str(section.get("section_key", "")).strip()
                for section in sections
                if isinstance(section, dict) and str(section.get("section_key", "")).strip()
            }
            if keys:
                return keys
    if document.template_version_id:
        template_version = db.get(PromptTemplateVersion, document.template_version_id)
        template_config = _template_version_config(template_version) if template_version else None
        if template_config is not None:
            return {section.section_key for section in template_config.sections}
    return set(EMIS_SECTION_KEYS)


def _raise_template_name_conflict(exc: IntegrityError) -> None:
    raise AppError(409, "conflict", "Template name already exists", {"resource": "template", "field": "name"}) from exc


def _raise_quick_action_name_conflict(exc: IntegrityError) -> None:
    raise AppError(409, "conflict", "Quick action name already exists", {"resource": "quick_action", "field": "name"}) from exc


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)


def _translate_template_integrity_error(exc: IntegrityError) -> None:
    constraint_name = _integrity_constraint_name(exc)
    if constraint_name in TEMPLATE_NAME_CONSTRAINTS:
        _raise_template_name_conflict(exc)
    if constraint_name == "uq_template_version_number":
        raise AppError(409, "conflict", "Template changed during save. Retry.", {"resource": "template"}) from exc
    raise exc


def _translate_quick_action_integrity_error(exc: IntegrityError) -> None:
    constraint_name = _integrity_constraint_name(exc)
    if constraint_name in QUICK_ACTION_NAME_CONSTRAINTS:
        _raise_quick_action_name_conflict(exc)
    if constraint_name == "uq_quick_action_version_number":
        raise AppError(409, "conflict", "Quick action changed during save. Retry.", {"resource": "quick_action"}) from exc
    raise exc


def _require_team_member(actor: User) -> None:
    if actor.is_system_admin or actor.team_id is None:
        raise AppError(403, "forbidden", "Template access is restricted to normal team users")


def _require_team_leader(actor: User) -> None:
    _require_team_member(actor)
    if actor.team_role is not TeamRole.leader:
        raise AppError(403, "forbidden", "Team template management access requires a leader account")


def _latest_template_version(db: Session, *, template_id: UUID) -> PromptTemplateVersion:
    version = db.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template_id)
        .order_by(PromptTemplateVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise AppError(404, "not_found", "Template version not found", {"resource": "template_version", "template_id": str(template_id)})
    return version


def _latest_quick_action_version(db: Session, *, quick_action_id: UUID) -> QuickActionVersion:
    version = db.scalar(
        select(QuickActionVersion)
        .where(QuickActionVersion.quick_action_id == quick_action_id)
        .order_by(QuickActionVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise AppError(
            404,
            "not_found",
            "Quick action version not found",
            {"resource": "quick_action_version", "quick_action_id": str(quick_action_id)},
        )
    return version


def _resolve_team_template_for_management(db: Session, actor: User, *, template_id: UUID) -> PromptTemplate:
    _require_team_leader(actor)
    template = db.scalar(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.scope == TemplateScope.team,
            PromptTemplate.team_id == actor.team_id,
        )
    )
    if template is None:
        raise AppError(404, "not_found", "Team template not found", {"resource": "template", "template_id": str(template_id)})
    return template


def _resolve_personal_template_for_management(db: Session, actor: User, *, template_id: UUID) -> PromptTemplate:
    _require_team_member(actor)
    template = db.scalar(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.scope == TemplateScope.user,
            PromptTemplate.owner_user_id == actor.id,
        )
    )
    if template is None:
        raise AppError(404, "not_found", "Personal template not found", {"resource": "template", "template_id": str(template_id)})
    return template


def _resolve_team_quick_action_for_management(db: Session, actor: User, *, quick_action_id: UUID) -> QuickAction:
    _require_team_leader(actor)
    quick_action = db.scalar(
        select(QuickAction).where(
            QuickAction.id == quick_action_id,
            QuickAction.scope == TemplateScope.team,
            QuickAction.team_id == actor.team_id,
        )
    )
    if quick_action is None:
        raise AppError(404, "not_found", "Team quick action not found", {"resource": "quick_action", "quick_action_id": str(quick_action_id)})
    return quick_action


def _resolve_personal_quick_action_for_management(db: Session, actor: User, *, quick_action_id: UUID) -> QuickAction:
    _require_team_member(actor)
    quick_action = db.scalar(
        select(QuickAction).where(
            QuickAction.id == quick_action_id,
            QuickAction.scope == TemplateScope.user,
            QuickAction.owner_user_id == actor.id,
        )
    )
    if quick_action is None:
        raise AppError(404, "not_found", "Personal quick action not found", {"resource": "quick_action", "quick_action_id": str(quick_action_id)})
    return quick_action


def _resolve_available_template_for_user(db: Session, actor: User, *, template_id: UUID) -> PromptTemplate:
    _require_team_member(actor)
    template = db.scalar(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.is_active.is_(True),
            (
                ((PromptTemplate.scope == TemplateScope.user) & (PromptTemplate.owner_user_id == actor.id))
                | ((PromptTemplate.scope == TemplateScope.team) & (PromptTemplate.team_id == actor.team_id))
            ),
        )
    )
    if template is None:
        raise AppError(404, "not_found", "Template not found", {"resource": "template", "template_id": str(template_id)})
    return template


def _resolve_available_quick_action_for_user(db: Session, actor: User, *, quick_action_id: UUID) -> QuickAction:
    _require_team_member(actor)
    quick_action = db.scalar(
        select(QuickAction).where(
            QuickAction.id == quick_action_id,
            QuickAction.is_active.is_(True),
            (
                ((QuickAction.scope == TemplateScope.user) & (QuickAction.owner_user_id == actor.id))
                | ((QuickAction.scope == TemplateScope.team) & (QuickAction.team_id == actor.team_id))
            ),
        )
    )
    if quick_action is None:
        raise AppError(404, "not_found", "Quick action not found", {"resource": "quick_action", "quick_action_id": str(quick_action_id)})
    return quick_action


def _next_template_version_no(db: Session, *, template_id: UUID) -> int:
    current_max = db.scalar(select(func.max(PromptTemplateVersion.version_no)).where(PromptTemplateVersion.template_id == template_id))
    return (current_max or 0) + 1


def _next_quick_action_version_no(db: Session, *, quick_action_id: UUID) -> int:
    current_max = db.scalar(select(func.max(QuickActionVersion.version_no)).where(QuickActionVersion.quick_action_id == quick_action_id))
    return (current_max or 0) + 1


def _serialize_prompt_text(raw_prompt_text: str) -> str:
    prompt_text = raw_prompt_text.strip()
    if not prompt_text:
        raise AppError(422, "business_rule_violation", "Template prompt text is required", {"field": "prompt_text"})
    return prompt_text


def _serialize_asset_name(raw_name: str, *, field: str = "name") -> str:
    name = raw_name.strip()
    if not name:
        raise AppError(422, "business_rule_violation", "Name is required", {"field": field})
    return name


def _split_duplicate_asset_name(name: str) -> tuple[str, int | None]:
    match = re.match(r"^(.*?)(?:\s+(\d+))?$", name.strip())
    if not match:
        return name.strip(), None
    base_name = (match.group(1) or "").strip()
    suffix = match.group(2)
    return base_name or name.strip(), int(suffix) if suffix else None


def _next_duplicate_template_name(db: Session, actor: User, *, scope: TemplateScope, source_name: str) -> str:
    base_name, parsed_suffix = _split_duplicate_asset_name(source_name)
    candidate_base = base_name if parsed_suffix else source_name.strip()
    if scope is TemplateScope.team:
        existing_names = list(
            db.scalars(
                select(PromptTemplate.name).where(
                    PromptTemplate.scope == TemplateScope.team,
                    PromptTemplate.team_id == actor.team_id,
                )
            )
        )
    else:
        existing_names = list(
            db.scalars(
                select(PromptTemplate.name).where(
                    PromptTemplate.scope == TemplateScope.user,
                    PromptTemplate.owner_user_id == actor.id,
                )
            )
        )
    normalized_existing = {str(name or "").strip().lower() for name in existing_names}
    next_index = 2
    while f"{candidate_base} {next_index}".strip().lower() in normalized_existing:
        next_index += 1
    return f"{candidate_base} {next_index}"


def _next_duplicate_quick_action_name(db: Session, actor: User, *, scope: TemplateScope, source_name: str) -> str:
    base_name, parsed_suffix = _split_duplicate_asset_name(source_name)
    candidate_base = base_name if parsed_suffix else source_name.strip()
    if scope is TemplateScope.team:
        existing_names = list(
            db.scalars(
                select(QuickAction.name).where(
                    QuickAction.scope == TemplateScope.team,
                    QuickAction.team_id == actor.team_id,
                )
            )
        )
    else:
        existing_names = list(
            db.scalars(
                select(QuickAction.name).where(
                    QuickAction.scope == TemplateScope.user,
                    QuickAction.owner_user_id == actor.id,
                )
            )
        )
    normalized_existing = {str(name or "").strip().lower() for name in existing_names}
    next_index = 2
    while f"{candidate_base} {next_index}".strip().lower() in normalized_existing:
        next_index += 1
    return f"{candidate_base} {next_index}"


def _ensure_unique_template_name(db: Session, actor: User, *, scope: TemplateScope, name: str, current_template_id: UUID | None = None) -> None:
    normalized_name = name.strip().lower()
    if scope is TemplateScope.team:
        duplicate = db.scalar(
            select(PromptTemplate).where(
                PromptTemplate.scope == TemplateScope.team,
                PromptTemplate.team_id == actor.team_id,
                func.lower(PromptTemplate.name) == normalized_name,
                PromptTemplate.id != current_template_id if current_template_id is not None else True,
            )
        )
        if duplicate is not None:
            raise AppError(409, "conflict", "Template name already exists", {"resource": "template", "field": "name"})
        return
    duplicate = db.scalar(
        select(PromptTemplate).where(
            PromptTemplate.scope == TemplateScope.user,
            PromptTemplate.owner_user_id == actor.id,
            func.lower(PromptTemplate.name) == normalized_name,
            PromptTemplate.id != current_template_id if current_template_id is not None else True,
        )
    )
    if duplicate is not None:
        raise AppError(409, "conflict", "Template name already exists", {"resource": "template", "field": "name"})


def _ensure_unique_quick_action_name(db: Session, actor: User, *, scope: TemplateScope, name: str, current_quick_action_id: UUID | None = None) -> None:
    normalized_name = name.strip().lower()
    if scope is TemplateScope.team:
        duplicate = db.scalar(
            select(QuickAction).where(
                QuickAction.scope == TemplateScope.team,
                QuickAction.team_id == actor.team_id,
                func.lower(QuickAction.name) == normalized_name,
                QuickAction.id != current_quick_action_id if current_quick_action_id is not None else True,
            )
        )
        if duplicate is not None:
            raise AppError(409, "conflict", "Quick action name already exists", {"resource": "quick_action", "field": "name"})
        return
    duplicate = db.scalar(
        select(QuickAction).where(
            QuickAction.scope == TemplateScope.user,
            QuickAction.owner_user_id == actor.id,
            func.lower(QuickAction.name) == normalized_name,
            QuickAction.id != current_quick_action_id if current_quick_action_id is not None else True,
        )
    )
    if duplicate is not None:
        raise AppError(409, "conflict", "Quick action name already exists", {"resource": "quick_action", "field": "name"})


def _serialize_template_config(payload: PromptTemplateUpsert) -> dict | None:
    if payload.mode is TemplateMode.freeform:
        return None
    if payload.mode is not TemplateMode.structured:
        raise AppError(422, "business_rule_violation", "Unsupported template mode", {"field": "mode"})
    if payload.config_json is None:
        raise AppError(422, "business_rule_violation", "Structured template config is required", {"field": "config_json"})
    if payload.config_json.profile != "emis":
        raise AppError(422, "business_rule_violation", "Only the EMIS structured profile is supported", {"field": "config_json.profile"})

    selected_sections: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for index, section in enumerate(payload.config_json.sections, start=1):
        section_key = section.section_key.strip()
        if section_key not in EMIS_SECTION_KEYS:
            raise AppError(422, "business_rule_violation", "Structured template uses an unsupported EMIS section", {"field": "config_json.sections", "section_key": section_key})
        if section_key in seen_keys:
            raise AppError(422, "business_rule_violation", "Structured template cannot include the same section twice", {"field": "config_json.sections", "section_key": section_key})
        instruction = section.instruction.strip()
        if not instruction:
            raise AppError(422, "business_rule_violation", "Structured template section instructions are required", {"field": "config_json.sections", "section_key": section_key})
        seen_keys.add(section_key)
        selected_sections.append(
            {
                "section_key": section_key,
                "section_label": EMIS_SECTION_LABELS[section_key],
                "instruction": instruction,
                "section_order": index,
            }
        )
    if not selected_sections:
        raise AppError(422, "business_rule_violation", "Structured EMIS templates require at least one section", {"field": "config_json.sections"})
    return {"profile": "emis", "sections": selected_sections}


def _template_version_config(version: PromptTemplateVersion) -> StructuredTemplateConfig | None:
    if not version.config_json:
        return None
    return StructuredTemplateConfig.model_validate(version.config_json)


def _serialize_structured_context(
    *,
    raw_context: dict[str, object] | None,
    template_config: StructuredTemplateConfig | None,
    ignore_unsupported_sections: bool = False,
) -> dict | None:
    if not raw_context:
        return None
    if template_config is None:
        raise AppError(422, "business_rule_violation", "Structured context is only supported for structured templates", {"field": "structured_context"})
    allowed_section_keys = {section.section_key for section in template_config.sections}
    clean: dict[str, list[str]] = {}
    for section_key, value in raw_context.items():
        normalized_key = section_key.strip()
        if normalized_key not in allowed_section_keys:
            if ignore_unsupported_sections:
                continue
            raise AppError(422, "business_rule_violation", "Structured context uses an unsupported section", {"field": "structured_context", "section_key": normalized_key})
        if isinstance(value, list):
            normalized_value = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        elif isinstance(value, str) and value.strip():
            normalized_value = [value.strip()]
        else:
            normalized_value = []
        if normalized_value:
            clean[normalized_key] = normalized_value
    return clean or None


def list_team_templates(db: Session, actor: User) -> list[PromptTemplate]:
    _require_team_leader(actor)
    return list(
        db.scalars(
            select(PromptTemplate)
            .where(PromptTemplate.scope == TemplateScope.team, PromptTemplate.team_id == actor.team_id)
            .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
        )
    )


def list_personal_templates(db: Session, actor: User) -> list[PromptTemplate]:
    _require_team_member(actor)
    return list(
        db.scalars(
            select(PromptTemplate)
            .where(PromptTemplate.scope == TemplateScope.user, PromptTemplate.owner_user_id == actor.id)
            .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
        )
    )


def list_available_templates_for_user(db: Session, actor: User) -> list[PromptTemplate]:
    _require_team_member(actor)
    return list(
        db.scalars(
            select(PromptTemplate)
            .where(
                PromptTemplate.is_active.is_(True),
                (
                    ((PromptTemplate.scope == TemplateScope.user) & (PromptTemplate.owner_user_id == actor.id))
                    | ((PromptTemplate.scope == TemplateScope.team) & (PromptTemplate.team_id == actor.team_id))
                ),
            )
            .order_by(PromptTemplate.scope.asc(), PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
        )
    )


def list_team_quick_actions(db: Session, actor: User) -> list[QuickAction]:
    _require_team_leader(actor)
    return list(
        db.scalars(
            select(QuickAction)
            .where(QuickAction.scope == TemplateScope.team, QuickAction.team_id == actor.team_id)
            .order_by(QuickAction.updated_at.desc(), QuickAction.id.desc())
        )
    )


def list_personal_quick_actions(db: Session, actor: User) -> list[QuickAction]:
    _require_team_member(actor)
    return list(
        db.scalars(
            select(QuickAction)
            .where(QuickAction.scope == TemplateScope.user, QuickAction.owner_user_id == actor.id)
            .order_by(QuickAction.updated_at.desc(), QuickAction.id.desc())
        )
    )


def list_available_quick_actions_for_user(db: Session, actor: User) -> list[QuickAction]:
    _require_team_member(actor)
    return list(
        db.scalars(
            select(QuickAction)
            .where(
                QuickAction.is_active.is_(True),
                (
                    ((QuickAction.scope == TemplateScope.user) & (QuickAction.owner_user_id == actor.id))
                    | ((QuickAction.scope == TemplateScope.team) & (QuickAction.team_id == actor.team_id))
                ),
            )
            .order_by(QuickAction.scope.asc(), QuickAction.updated_at.desc(), QuickAction.id.desc())
        )
    )


def upsert_team_template(db: Session, actor: User, payload: PromptTemplateUpsert) -> PromptTemplate:
    _require_team_leader(actor)
    if payload.scope is not TemplateScope.team:
        raise AppError(422, "business_rule_violation", "Team template payload must use team scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    template_name = _serialize_asset_name(payload.name)
    config_json = _serialize_template_config(payload)
    template = _resolve_team_template_for_management(db, actor, template_id=payload.template_id) if payload.template_id else None
    _ensure_unique_template_name(db, actor, scope=TemplateScope.team, name=template_name, current_template_id=template.id if template is not None else None)
    if template is None:
        template = PromptTemplate(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=actor.team_id,
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

    version = PromptTemplateVersion(
        id=uuid4(),
        template_id=template.id,
        version_no=_next_template_version_no(db, template_id=template.id),
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
        _translate_template_integrity_error(exc)
    except Exception:
        db.rollback()
        raise
    db.refresh(template)
    return template


def upsert_personal_template(db: Session, actor: User, payload: PromptTemplateUpsert) -> PromptTemplate:
    _require_team_member(actor)
    if payload.scope is not TemplateScope.user:
        raise AppError(422, "business_rule_violation", "Personal template payload must use user scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    template_name = _serialize_asset_name(payload.name)
    config_json = _serialize_template_config(payload)
    template = _resolve_personal_template_for_management(db, actor, template_id=payload.template_id) if payload.template_id else None
    _ensure_unique_template_name(db, actor, scope=TemplateScope.user, name=template_name, current_template_id=template.id if template is not None else None)
    if template is None:
        template = PromptTemplate(
            id=uuid4(),
            scope=TemplateScope.user,
            owner_user_id=actor.id,
            team_id=None,
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

    version = PromptTemplateVersion(
        id=uuid4(),
        template_id=template.id,
        version_no=_next_template_version_no(db, template_id=template.id),
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
        _translate_template_integrity_error(exc)
    except Exception:
        db.rollback()
        raise
    db.refresh(template)
    return template


def upsert_team_quick_action(db: Session, actor: User, payload: QuickActionUpsert) -> QuickAction:
    _require_team_leader(actor)
    if payload.scope is not TemplateScope.team:
        raise AppError(422, "business_rule_violation", "Team quick action payload must use team scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    quick_action_name = _serialize_asset_name(payload.name)
    quick_action = _resolve_team_quick_action_for_management(db, actor, quick_action_id=payload.quick_action_id) if payload.quick_action_id else None
    _ensure_unique_quick_action_name(
        db,
        actor,
        scope=TemplateScope.team,
        name=quick_action_name,
        current_quick_action_id=quick_action.id if quick_action is not None else None,
    )
    if quick_action is None:
        quick_action = QuickAction(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=actor.team_id,
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

    version = QuickActionVersion(
        id=uuid4(),
        quick_action_id=quick_action.id,
        version_no=_next_quick_action_version_no(db, quick_action_id=quick_action.id),
        mode=TemplateMode.freeform,
        prompt_text=prompt_text,
        created_by_user_id=actor.id,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _translate_quick_action_integrity_error(exc)
    except Exception:
        db.rollback()
        raise
    db.refresh(quick_action)
    return quick_action


def upsert_personal_quick_action(db: Session, actor: User, payload: QuickActionUpsert) -> QuickAction:
    _require_team_member(actor)
    if payload.scope is not TemplateScope.user:
        raise AppError(422, "business_rule_violation", "Personal quick action payload must use user scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    quick_action_name = _serialize_asset_name(payload.name)
    quick_action = _resolve_personal_quick_action_for_management(db, actor, quick_action_id=payload.quick_action_id) if payload.quick_action_id else None
    _ensure_unique_quick_action_name(
        db,
        actor,
        scope=TemplateScope.user,
        name=quick_action_name,
        current_quick_action_id=quick_action.id if quick_action is not None else None,
    )
    if quick_action is None:
        quick_action = QuickAction(
            id=uuid4(),
            scope=TemplateScope.user,
            owner_user_id=actor.id,
            team_id=None,
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

    version = QuickActionVersion(
        id=uuid4(),
        quick_action_id=quick_action.id,
        version_no=_next_quick_action_version_no(db, quick_action_id=quick_action.id),
        mode=TemplateMode.freeform,
        prompt_text=prompt_text,
        created_by_user_id=actor.id,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _translate_quick_action_integrity_error(exc)
    except Exception:
        db.rollback()
        raise
    db.refresh(quick_action)
    return quick_action


def delete_team_template(db: Session, actor: User, *, template_id: UUID) -> None:
    template = _resolve_team_template_for_management(db, actor, template_id=template_id)
    version_ids = list(db.scalars(select(PromptTemplateVersion.id).where(PromptTemplateVersion.template_id == template.id)))
    if version_ids:
        for document in db.scalars(select(GeneratedDocument).where(GeneratedDocument.template_version_id.in_(version_ids))):
            document.template_version_id = None
            db.add(document)
        db.flush()
    db.delete(template)
    db.commit()


def delete_personal_template(db: Session, actor: User, *, template_id: UUID) -> None:
    template = _resolve_personal_template_for_management(db, actor, template_id=template_id)
    version_ids = list(db.scalars(select(PromptTemplateVersion.id).where(PromptTemplateVersion.template_id == template.id)))
    if version_ids:
        for document in db.scalars(select(GeneratedDocument).where(GeneratedDocument.template_version_id.in_(version_ids))):
            document.template_version_id = None
            db.add(document)
        db.flush()
    db.delete(template)
    db.commit()


def duplicate_team_template(db: Session, actor: User, *, template_id: UUID) -> PromptTemplate:
    template = _resolve_team_template_for_management(db, actor, template_id=template_id)
    latest_version = _latest_template_version(db, template_id=template.id)
    return upsert_team_template(
        db,
        actor,
        PromptTemplateUpsert(
            scope=TemplateScope.team,
            name=_next_duplicate_template_name(db, actor, scope=TemplateScope.team, source_name=template.name),
            description=template.description,
            prompt_text=latest_version.prompt_text,
            mode=latest_version.mode,
            config_json=_template_version_config(latest_version),
            is_active=template.is_active,
        ),
    )


def duplicate_personal_template(db: Session, actor: User, *, template_id: UUID) -> PromptTemplate:
    template = _resolve_personal_template_for_management(db, actor, template_id=template_id)
    latest_version = _latest_template_version(db, template_id=template.id)
    return upsert_personal_template(
        db,
        actor,
        PromptTemplateUpsert(
            scope=TemplateScope.user,
            name=_next_duplicate_template_name(db, actor, scope=TemplateScope.user, source_name=template.name),
            description=template.description,
            prompt_text=latest_version.prompt_text,
            mode=latest_version.mode,
            config_json=_template_version_config(latest_version),
            is_active=template.is_active,
        ),
    )


def delete_team_quick_action(db: Session, actor: User, *, quick_action_id: UUID) -> None:
    quick_action = _resolve_team_quick_action_for_management(db, actor, quick_action_id=quick_action_id)
    version_ids = list(db.scalars(select(QuickActionVersion.id).where(QuickActionVersion.quick_action_id == quick_action.id)))
    if version_ids:
        for document in db.scalars(select(GeneratedDocument).where(GeneratedDocument.quick_action_version_id.in_(version_ids))):
            document.quick_action_version_id = None
            db.add(document)
        db.flush()
    db.delete(quick_action)
    db.commit()


def delete_personal_quick_action(db: Session, actor: User, *, quick_action_id: UUID) -> None:
    quick_action = _resolve_personal_quick_action_for_management(db, actor, quick_action_id=quick_action_id)
    version_ids = list(db.scalars(select(QuickActionVersion.id).where(QuickActionVersion.quick_action_id == quick_action.id)))
    if version_ids:
        for document in db.scalars(select(GeneratedDocument).where(GeneratedDocument.quick_action_version_id.in_(version_ids))):
            document.quick_action_version_id = None
            db.add(document)
        db.flush()
    db.delete(quick_action)
    db.commit()


def duplicate_team_quick_action(db: Session, actor: User, *, quick_action_id: UUID) -> QuickAction:
    quick_action = _resolve_team_quick_action_for_management(db, actor, quick_action_id=quick_action_id)
    latest_version = _latest_quick_action_version(db, quick_action_id=quick_action.id)
    return upsert_team_quick_action(
        db,
        actor,
        QuickActionUpsert(
            scope=TemplateScope.team,
            name=_next_duplicate_quick_action_name(db, actor, scope=TemplateScope.team, source_name=quick_action.name),
            description=quick_action.description,
            prompt_text=latest_version.prompt_text,
            is_active=quick_action.is_active,
        ),
    )


def duplicate_personal_quick_action(db: Session, actor: User, *, quick_action_id: UUID) -> QuickAction:
    quick_action = _resolve_personal_quick_action_for_management(db, actor, quick_action_id=quick_action_id)
    latest_version = _latest_quick_action_version(db, quick_action_id=quick_action.id)
    return upsert_personal_quick_action(
        db,
        actor,
        QuickActionUpsert(
            scope=TemplateScope.user,
            name=_next_duplicate_quick_action_name(db, actor, scope=TemplateScope.user, source_name=quick_action.name),
            description=quick_action.description,
            prompt_text=latest_version.prompt_text,
            is_active=quick_action.is_active,
        ),
    )


def list_generated_documents_for_transcript(db: Session, actor: User, *, transcript_id: UUID) -> list[GeneratedDocument]:
    _require_team_member(actor)
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    return list(
        db.scalars(
            select(GeneratedDocument)
            .where(GeneratedDocument.transcript_id == transcript_id, GeneratedDocument.owner_user_id == actor.id)
            .order_by(GeneratedDocument.created_at.desc(), GeneratedDocument.id.desc())
        )
    )


def delete_generated_document(db: Session, actor: User, *, generated_document_id: UUID) -> None:
    _require_team_member(actor)
    document = db.get(GeneratedDocument, generated_document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(generated_document_id)})
    if document.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Generated document access is restricted to the owning user")
    for event in document.provider_usage_events:
        event.generated_document_id = None
        db.add(event)
    db.delete(document)
    db.commit()


def _normalize_note_text(value: str | None) -> str:
    return "\n".join(
        line.strip()
        for line in str(value or "").splitlines()
        if line.strip()
    ).strip()


def _normalize_generated_document_title(value: str | None, *, fallback: str) -> str:
    title = " ".join(str(value or "").split()).strip()
    if not title:
        return fallback[:255] or "Generated document"
    return title[:255]


def _normalize_freeform_generated_text(value: str | None) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def update_generated_document_content(
    db: Session,
    actor: User,
    *,
    generated_document_id: UUID,
    payload: GeneratedDocumentUpdateRequest,
) -> GeneratedDocument:
    _require_team_member(actor)
    document = db.get(GeneratedDocument, generated_document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(generated_document_id)})
    if document.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Generated document access is restricted to the owning user")
    if document.status is not GeneratedDocumentStatus.ready:
        raise AppError(409, "conflict", "Generated document is not ready for editing")

    expected_updated_at = payload.expected_updated_at
    if expected_updated_at.tzinfo is None:
        expected_updated_at = expected_updated_at.replace(tzinfo=timezone.utc)
    if document.updated_at != expected_updated_at:
        raise AppError(409, "conflict", "Generated document has changed. Reload note before saving again.")

    title_changed = False

    if document.generator_type is GeneratedDocumentGeneratorType.template and document.document_mode is TemplateMode.structured:
        incoming_sections = sorted(payload.sections, key=lambda section: section.section_order)
        existing_sections = {section.section_key: section for section in document.sections}
        allowed_section_keys = _allowed_structured_section_keys(document, db)
        rendered_sections: list[dict[str, str | int]] = []
        any_section_edited = False
        seen_keys: set[str] = set()
        for incoming in incoming_sections:
            section_key = incoming.section_key.strip()
            if section_key not in allowed_section_keys:
                raise AppError(422, "business_rule_violation", "Structured note section is invalid", {"field": "sections", "section_key": section_key})
            if section_key in seen_keys:
                raise AppError(422, "business_rule_violation", "Structured note sections must not repeat keys", {"field": "sections", "section_key": section_key})
            seen_keys.add(section_key)
            section_record = existing_sections.get(section_key)
            if section_record is None:
                section_record = GeneratedDocumentSection(
                    id=uuid4(),
                    generated_document_id=document.id,
                    section_key=section_key,
                    section_label=(incoming.section_label or EMIS_SECTION_LABELS[section_key]).strip()[:255],
                    section_order=incoming.section_order,
                    original_text_encrypted="",
                    edited_text_encrypted="",
                    is_edited=False,
                )
            section_record.section_label = (incoming.section_label or EMIS_SECTION_LABELS[section_key]).strip()[:255]
            section_record.section_order = incoming.section_order
            normalized_text = _normalize_note_text(incoming.text)
            set_generated_document_section_text(
                db,
                section=section_record,
                field="edited_text_encrypted",
                owner_user_id=document.owner_user_id,
                plaintext=normalized_text,
            )
            original_text = _normalize_note_text(
                generated_document_section_text(db, section=section_record, field="original_text_encrypted")
                if section_key in existing_sections
                else ""
            )
            section_record.is_edited = normalized_text != original_text
            any_section_edited = any_section_edited or section_record.is_edited
            db.add(section_record)
            rendered_sections.append(
                {
                    "section_key": section_key,
                    "section_label": section_record.section_label,
                    "section_order": section_record.section_order,
                    "text": normalized_text,
                }
            )
        for section_key, section_record in existing_sections.items():
            if section_key not in seen_keys:
                db.delete(section_record)
        edited_output_text = _render_structured_sections_text(rendered_sections)
    elif document.generator_type is GeneratedDocumentGeneratorType.template:
        edited_output_text = _normalize_note_text(payload.edited_output_text)
    elif document.generator_type in {GeneratedDocumentGeneratorType.followup, GeneratedDocumentGeneratorType.quick_action} and document.document_mode is TemplateMode.freeform:
        edited_output_text = _normalize_freeform_generated_text(payload.edited_output_text)
        if payload.title is not None:
            normalized_title = _normalize_generated_document_title(payload.title, fallback=document.title)
            title_changed = normalized_title != document.title
            document.title = normalized_title
    else:
        raise AppError(422, "business_rule_violation", "Generated document does not support direct editing")

    original_output_text = (
        _normalize_note_text(generated_document_text(db, document=document, field="original_output_text_encrypted"))
        if document.generator_type is GeneratedDocumentGeneratorType.template
        else _normalize_freeform_generated_text(generated_document_text(db, document=document, field="original_output_text_encrypted"))
    )
    set_generated_document_text(db, document=document, field="edited_output_text_encrypted", plaintext=edited_output_text)
    if document.generator_type is GeneratedDocumentGeneratorType.template and document.document_mode is TemplateMode.structured:
        document.is_edited = any_section_edited or edited_output_text != original_output_text
    else:
        document.is_edited = title_changed or edited_output_text != original_output_text
    document.last_edited_at = utcnow()
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _snapshot_transcript_version(db: Session, *, transcript: Transcript, allow_empty: bool = False) -> TranscriptVersion:
    current_text = (
        decrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            stored_value=transcript.current_draft_text_encrypted,
        )
        or ""
    ).strip()
    if not current_text and not allow_empty:
        raise AppError(422, "business_rule_violation", "Transcript draft is empty", {"field": "current_draft_text_encrypted"})
    existing_versions = db.scalars(
        select(TranscriptVersion)
        .where(TranscriptVersion.transcript_id == transcript.id)
        .order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.created_at.desc(), TranscriptVersion.id.desc())
    )
    for existing_version in existing_versions:
        existing_text = (
            decrypt_text_for_owner(
                db,
                owner_user_id=transcript.owner_user_id,
                table="transcript_versions",
                field="text_encrypted",
                record_id=existing_version.id,
                stored_value=existing_version.text_encrypted,
            )
            or ""
        ).strip()
        if existing_text == current_text:
            transcript.status = TranscriptStatus.ready
            db.add(transcript)
            db.flush()
            return existing_version
    version_id = uuid4()
    current_max = db.scalar(select(func.max(TranscriptVersion.version_no)).where(TranscriptVersion.transcript_id == transcript.id))
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=(current_max or 0) + 1,
        text_encrypted=encrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext=current_text,
        ),
    )
    transcript.status = TranscriptStatus.ready
    db.add(version)
    db.add(transcript)
    db.flush()
    return version


def _generation_usage_event(
    *,
    event: str,
    document: GeneratedDocument,
    config: TeamLlmConfig | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    provider_duration_ms: int | None = None,
    status: str | None = None,
    ) -> None:
    usage_logger.info(
        event,
        extra={
            "event": event,
            "generated_document_id": str(document.id),
            "transcript_id": str(document.transcript_id),
            "owner_user_id": str(document.owner_user_id),
            "team_id": str(document.team_id),
            "template_version_id": str(document.template_version_id) if document.template_version_id else None,
            "provider_adapter": config.adapter_kind.value if config is not None else None,
            "model_used": document.model_used,
            "status": status or document.status.value,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": document.estimated_cost_usd,
            "duration_ms": duration_ms,
            "provider_duration_ms": provider_duration_ms,
            "error_code": document.error_code,
            "provider_error_code": document.provider_error_code,
            "provider_http_status": document.provider_http_status,
        },
    )


def _usage_event_type_from_name(event: str) -> ProviderUsageEventType:
    return {
        "llm_generation_queued": ProviderUsageEventType.queued,
        "llm_generation_started": ProviderUsageEventType.started,
        "llm_generation_completed": ProviderUsageEventType.completed,
        "llm_generation_failed": ProviderUsageEventType.failed,
        "llm_generation_enqueue_failed": ProviderUsageEventType.enqueue_failed,
    }[event]


def _persist_generation_usage_event(
    db: Session,
    *,
    event: str,
    document: GeneratedDocument,
    config: TeamLlmConfig | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    provider_duration_ms: int | None = None,
    status: str | None = None,
) -> ProviderUsageEvent:
    usage_event = ProviderUsageEvent(
        id=uuid4(),
        team_id=document.team_id,
        owner_user_id=document.owner_user_id,
        generated_document_id=document.id,
        transcript_id=document.transcript_id,
        llm_config_id=document.llm_config_id,
        feature_type=ProviderFeatureType.llm_generation,
        event_type=_usage_event_type_from_name(event),
        provider_adapter=config.adapter_kind.value if config is not None else None,
        model_name=document.model_used,
        status=status or document.status.value,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=document.estimated_cost_usd,
        duration_ms=duration_ms,
        provider_duration_ms=provider_duration_ms,
        error_code=document.error_code,
        provider_error_code=document.provider_error_code,
        provider_http_status=document.provider_http_status,
    )
    db.add(usage_event)
    db.commit()
    db.refresh(usage_event)
    return usage_event


def _record_generation_usage_event(
    db: Session,
    *,
    event: str,
    document: GeneratedDocument,
    config: TeamLlmConfig | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    provider_duration_ms: int | None = None,
    status: str | None = None,
) -> None:
    _persist_generation_usage_event(
        db,
        event=event,
        document=document,
        config=config,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        provider_duration_ms=provider_duration_ms,
        status=status,
    )
    _generation_usage_event(
        event=event,
        document=document,
        config=config,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        provider_duration_ms=provider_duration_ms,
        status=status,
    )


def _safe_provider_http_error_message(*, status_code: int | None, provider_error_code: str | None = None) -> str:
    if status_code == 400:
        return "The LLM provider rejected the generation request"
    if status_code in {401, 403}:
        return "The LLM provider rejected the configured credentials"
    if status_code == 404:
        if provider_error_code == "model_not_found" or (provider_error_code and "not found" in provider_error_code.lower()):
            return "The selected model is not available on the LLM provider"
        return "The requested LLM provider resource was not found"
    if status_code == 408:
        return "The LLM provider timed out"
    if status_code == 429:
        return "The LLM provider is rate limiting requests"
    if status_code is not None and 500 <= status_code <= 599:
        return "The LLM provider is temporarily unavailable"
    return "LLM generation failed"


def _openai_error_details(exc: APIStatusError) -> tuple[int | None, str | None]:
    status_code = getattr(exc, "status_code", None)
    provider_error_code = None
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code.strip():
                provider_error_code = code.strip()
    return status_code, provider_error_code


def _translate_openai_generation_error(exc: Exception) -> AppError:
    if isinstance(exc, APITimeoutError):
        return AppError(
            504,
            "llm_provider_timeout",
            "The LLM provider timed out",
            {"provider_error_code": "timeout"},
        )
    if isinstance(exc, APIConnectionError):
        return AppError(
            502,
            "llm_provider_unreachable",
            "Could not reach the LLM provider",
            {"provider_error_code": "connection_error"},
        )
    if isinstance(exc, APIStatusError):
        status_code, provider_error_code = _openai_error_details(exc)
        return AppError(
            502,
            "llm_generation_failed",
            _safe_provider_http_error_message(status_code=status_code, provider_error_code=provider_error_code),
            {
                "provider_http_status": status_code,
                "provider_error_code": provider_error_code,
            },
        )
    return AppError(502, "llm_generation_failed", "LLM generation failed")


def _translate_ollama_generation_error(exc: Exception) -> AppError:
    if isinstance(exc, httpx.TimeoutException):
        return AppError(
            504,
            "llm_provider_timeout",
            "The LLM provider timed out",
            {"provider_error_code": "timeout"},
        )
    if isinstance(exc, httpx.ConnectError):
        return AppError(
            502,
            "llm_provider_unreachable",
            "Could not reach the LLM provider",
            {"provider_error_code": "connection_error"},
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else None
        provider_error_code = None
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error_value = payload.get("error")
            if isinstance(error_value, str) and error_value.strip():
                provider_error_code = error_value.strip()[:255]
        return AppError(
            502,
            "llm_generation_failed",
            _safe_provider_http_error_message(status_code=status_code, provider_error_code=provider_error_code),
            {
                "provider_http_status": status_code,
                "provider_error_code": provider_error_code,
            },
        )
    return AppError(502, "llm_generation_failed", "LLM generation failed")


def _usage_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _generation_usage(
    *,
    input_tokens: object = None,
    output_tokens: object = None,
    total_tokens: object = None,
    duration_ms: object = None,
    provider_duration_ms: object = None,
) -> GenerationUsage:
    normalized_input_tokens = _usage_int(input_tokens)
    normalized_output_tokens = _usage_int(output_tokens)
    normalized_total_tokens = _usage_int(total_tokens)
    if normalized_total_tokens is None and normalized_input_tokens is not None and normalized_output_tokens is not None:
        normalized_total_tokens = normalized_input_tokens + normalized_output_tokens
    return {
        "input_tokens": normalized_input_tokens,
        "output_tokens": normalized_output_tokens,
        "total_tokens": normalized_total_tokens,
        "duration_ms": _usage_int(duration_ms),
        "provider_duration_ms": _usage_int(provider_duration_ms),
    }


def _estimated_cost_usd(*, config: TeamLlmConfig, usage: GenerationUsage) -> float | None:
    if config.adapter_kind is LlmAdapterKind.ollama_chat:
        return 0.0
    return None


def _resolve_runtime_llm_config(db: Session, *, document: GeneratedDocument) -> TeamLlmConfig:
    if document.llm_config_id is None:
        raise AppError(422, "business_rule_violation", "No LLM config snapshot is stored for this generated document")
    config = db.get(TeamLlmConfig, document.llm_config_id)
    if config is None or config.team_id != document.team_id:
        raise AppError(422, "business_rule_violation", "The queued LLM provider is no longer available for this team")
    return config


def _prompt_snapshot_text_for_document(db: Session, *, document: GeneratedDocument) -> str:
    prompt_text = (document.prompt_snapshot_text or "").strip()
    if prompt_text:
        return prompt_text
    if document.generator_type is GeneratedDocumentGeneratorType.template and document.template_version_id:
        template_version = db.get(PromptTemplateVersion, document.template_version_id)
        if template_version is not None:
            return template_version.prompt_text.strip()
    if document.generator_type is GeneratedDocumentGeneratorType.quick_action and document.quick_action_version_id:
        quick_action_version = db.get(QuickActionVersion, document.quick_action_version_id)
        if quick_action_version is not None:
            return quick_action_version.prompt_text.strip()
    return ""


def generated_document_text(db: Session, *, document: GeneratedDocument, field: str) -> str:
    stored_value = getattr(document, field)
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=document.owner_user_id,
            table="generated_documents",
            field=field,
            record_id=document.id,
            stored_value=stored_value,
        )
        or ""
    )


def set_generated_document_text(db: Session, *, document: GeneratedDocument, field: str, plaintext: str | None) -> None:
    setattr(
        document,
        field,
        encrypt_text_for_owner(
            db,
            owner_user_id=document.owner_user_id,
            table="generated_documents",
            field=field,
            record_id=document.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None,
    )


def generated_document_structured_context(db: Session, *, document: GeneratedDocument) -> dict:
    value = decrypt_json_for_owner(
        db,
        owner_user_id=document.owner_user_id,
        table="generated_documents",
        field="structured_context_json",
        record_id=document.id,
        stored_value=document.structured_context_json,
    )
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AppError(500, "content_crypto_invalid", "Generated document structured context is invalid")
    return value


def set_generated_document_structured_context(db: Session, *, document: GeneratedDocument, plaintext: dict | None) -> None:
    document.structured_context_json = (
        encrypt_json_for_owner(
            db,
            owner_user_id=document.owner_user_id,
            table="generated_documents",
            field="structured_context_json",
            record_id=document.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None
    )


def generated_document_structured_working_note_snapshot(db: Session, *, document: GeneratedDocument) -> dict | None:
    value = decrypt_json_for_owner(
        db,
        owner_user_id=document.owner_user_id,
        table="generated_documents",
        field="structured_working_note_snapshot_json",
        record_id=document.id,
        stored_value=document.structured_working_note_snapshot_json,
    )
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AppError(500, "content_crypto_invalid", "Generated document working note snapshot is invalid")
    return value


def set_generated_document_structured_working_note_snapshot(db: Session, *, document: GeneratedDocument, plaintext: dict | None) -> None:
    document.structured_working_note_snapshot_json = (
        encrypt_json_for_owner(
            db,
            owner_user_id=document.owner_user_id,
            table="generated_documents",
            field="structured_working_note_snapshot_json",
            record_id=document.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None
    )


def _working_note_snapshot_for_transcript(db: Session, *, transcript: Transcript) -> tuple[TranscriptWorkingNoteMode | None, str, dict | None]:
    if transcript.working_note_mode is TranscriptWorkingNoteMode.freeform:
        freeform_text = freeform_working_note_text(db, transcript=transcript).strip()
        return (TranscriptWorkingNoteMode.freeform, freeform_text, None) if freeform_text else (None, "", None)
    if transcript.working_note_mode is TranscriptWorkingNoteMode.structured:
        structured_note = normalize_structured_working_note(transcript_structured_context(db, transcript=transcript))
        return (TranscriptWorkingNoteMode.structured, "", structured_note) if structured_note is not None else (None, "", None)
    structured_note = normalize_structured_working_note(transcript_structured_context(db, transcript=transcript))
    if structured_note is not None:
        return TranscriptWorkingNoteMode.structured, "", structured_note
    return None, "", None


def _format_working_note_for_prompt(*, mode: TranscriptWorkingNoteMode | None, freeform_text: str = "", structured_note: dict | None = None) -> str:
    if mode is TranscriptWorkingNoteMode.freeform and freeform_text.strip():
        return f"Freeform working note:\n{freeform_text.strip()}"
    if mode is TranscriptWorkingNoteMode.structured and isinstance(structured_note, dict):
        sections = structured_note.get("sections")
        if isinstance(sections, dict):
            lines: list[str] = []
            for section_key in EMIS_SECTION_KEYS:
                raw_lines = sections.get(section_key)
                if isinstance(raw_lines, list):
                    values = [str(item).strip() for item in raw_lines if isinstance(item, str) and item.strip()]
                elif isinstance(raw_lines, str) and raw_lines.strip():
                    values = [raw_lines.strip()]
                else:
                    values = []
                if values:
                    lines.append(f"{EMIS_SECTION_LABELS.get(section_key, section_key)}:\n" + "\n".join(values))
            if lines:
                return "Structured EMIS working note:\n" + "\n\n".join(lines)
    return ""


def generated_document_llm_request_payload(db: Session, *, document: GeneratedDocument) -> dict | None:
    value = decrypt_json_for_owner(
        db,
        owner_user_id=document.owner_user_id,
        table="generated_documents",
        field="llm_request_payload_json_encrypted",
        record_id=document.id,
        stored_value=document.llm_request_payload_json_encrypted,
    )
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AppError(500, "content_crypto_invalid", "Generated document LLM request payload is invalid")
    return value


def set_generated_document_llm_request_payload(db: Session, *, document: GeneratedDocument, plaintext: dict | None) -> None:
    document.llm_request_payload_json_encrypted = (
        encrypt_json_for_owner(
            db,
            owner_user_id=document.owner_user_id,
            table="generated_documents",
            field="llm_request_payload_json_encrypted",
            record_id=document.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None
    )


def generated_document_section_text(db: Session, *, section: GeneratedDocumentSection, field: str) -> str:
    stored_value = getattr(section, field)
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=section.generated_document.owner_user_id,
            table="generated_document_sections",
            field=field,
            record_id=section.id,
            stored_value=stored_value,
        )
        or ""
    )


def set_generated_document_section_text(
    db: Session,
    *,
    section: GeneratedDocumentSection,
    field: str,
    owner_user_id: UUID,
    plaintext: str | None,
) -> None:
    setattr(
        section,
        field,
        encrypt_text_for_owner(
            db,
            owner_user_id=owner_user_id,
            table="generated_document_sections",
            field=field,
            record_id=section.id,
            plaintext=plaintext,
        )
        if plaintext is not None
        else None,
    )


def _generate_freeform_output_openai(
    *,
    api_key: str,
    base_url: str,
    request_body: dict[str, object],
 ) -> tuple[str, GenerationUsage]:
    started = time.perf_counter()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        completion = client.chat.completions.create(**request_body)
    except Exception as exc:  # pragma: no cover - exercised via service error behavior, not live provider calls
        raise _translate_openai_generation_error(exc) from exc

    message = completion.choices[0].message if completion.choices else None
    content = getattr(message, "content", None) if message is not None else None
    if isinstance(content, str):
        generated_text = content.strip()
    elif isinstance(content, list):
        generated_text = "".join(
            part.text for part in content if getattr(part, "type", None) == "text" and getattr(part, "text", None)
        ).strip()
    else:
        generated_text = ""
    if not generated_text:
        raise AppError(502, "llm_generation_failed", "LLM generation returned no note text")
    usage = getattr(completion, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
    total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
    return generated_text, _generation_usage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _generate_freeform_output_ollama(
    *,
    base_url: str,
    bearer_token: str | None,
    request_body: dict[str, object],
) -> tuple[str, GenerationUsage]:
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    started = time.perf_counter()
    generated_parts: list[str] = []
    final_payload: dict[str, object] | None = None
    try:
        with httpx.stream(
            "POST",
            f"{base_url.rstrip('/')}/api/chat",
            headers=headers,
            json=request_body,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=60.0),
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                payload = json.loads(raw_line)
                if not isinstance(payload, dict):
                    raise AppError(
                        502,
                        "llm_provider_bad_response",
                        "The LLM provider returned an unreadable response",
                        {"provider_error_code": "invalid_json"},
                    )
                message = payload.get("message", {})
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and content:
                    generated_parts.append(content)
                if payload.get("done") is True:
                    final_payload = payload
                    break
    except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover
        if isinstance(exc, ValueError):
            raise AppError(
                502,
                "llm_provider_bad_response",
                "The LLM provider returned an unreadable response",
                {"provider_error_code": "invalid_json"},
            ) from exc
        raise _translate_ollama_generation_error(exc) from exc

    payload = final_payload or {}
    generated_text = "".join(generated_parts).strip()
    if not generated_text:
        raise AppError(502, "llm_generation_failed", "LLM generation returned no note text")
    prompt_tokens = payload.get("prompt_eval_count")
    completion_tokens = payload.get("eval_count")
    provider_duration_raw = payload.get("total_duration")
    provider_duration_ms = int(provider_duration_raw / 1_000_000) if isinstance(provider_duration_raw, int) else None
    return generated_text, _generation_usage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        duration_ms=int((time.perf_counter() - started) * 1000),
        provider_duration_ms=provider_duration_ms,
    )


def _generation_request_snapshot(
    *,
    adapter_kind: LlmAdapterKind,
    model: str,
    user_id: UUID,
    system_message: str,
    user_message: str,
) -> dict[str, object]:
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message},
    ]
    if adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
        request_body: dict[str, object] = {
            "model": model,
            "temperature": 0.2,
            "max_completion_tokens": 1600,
            "user": str(user_id),
            "messages": messages,
        }
    elif adapter_kind is LlmAdapterKind.ollama_chat:
        request_body = {
            "model": model,
            "stream": True,
            "messages": messages,
        }
    else:  # pragma: no cover
        raise AppError(422, "business_rule_violation", "Unsupported LLM adapter", {"adapter_kind": adapter_kind.value})
    return request_body


def _build_template_generation_messages(
    *,
    template_name: str,
    prompt_text: str,
    transcript_text: str,
    dictation_text: str = "",
    working_note_text: str = "",
) -> tuple[str, str]:
    return (
        "You generate concise note output from consultation source material using the provided template instructions. "
        "Return only a valid JSON object with exactly two string fields: "
        "\"title\" and \"content\". "
        "The \"title\" field must be a short two to three word summary of the consultation for the user to read. "
        "The \"content\" field must contain the full note text. "
        "Do not include markdown fences, commentary, or any text outside the JSON object. "
        "If clinician-authored working note or post-consultation dictation is provided, treat it as stronger clinician-authored guidance for summary, assessment, terminology, and plan wording, while keeping transcript as chronology and factual anchor. "
        "Do not invent facts absent from the provided sources. "
        "The transcript and instructions may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        (
            f"Template name: {template_name}\n\n"
            f"Template instructions:\n{prompt_text}\n\n"
            f"Consultation transcript:\n{transcript_text}"
            + (f"\n\nConsultation working note:\n{working_note_text}" if working_note_text.strip() else "")
            + (f"\n\nPost-consultation dictation:\n{dictation_text}" if dictation_text.strip() else "")
        ),
    )


def _build_structured_template_generation_messages(
    *,
    template_name: str,
    global_instruction: str,
    transcript_text: str,
    dictation_text: str,
    template_config: StructuredTemplateConfig,
    working_note_text: str = "",
) -> tuple[str, str]:
    section_lines = [
        f'- "{section.section_key}": {section.instruction}'
        for section in sorted(template_config.sections, key=lambda item: item.section_order)
    ]
    return (
        "You generate a structured GP note from a transcript using the provided EMIS section instructions. "
        "Return only a valid JSON object with exactly two top-level fields: "
        "\"title\" and \"content\". "
        "The \"title\" field must be a short two to three word summary of the consultation for the user to read. "
        "The \"content\" field must be an object. "
        "Only use these allowed section keys, and do not invent any others: "
        f"{', '.join(section.section_key for section in sorted(template_config.sections, key=lambda item: item.section_order))}. "
        "Each included section value must be a string. Omit sections that have no relevant content. "
        "Do not include markdown fences, commentary, or any text outside the JSON object. "
        "If clinician-authored working note or post-consultation dictation is provided, treat it as stronger clinician-authored guidance for summary, assessment, terminology, and plan wording, while keeping transcript as chronology and factual anchor. "
        "Do not invent facts absent from the provided sources. "
        "The transcript and instructions may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        (
            f"Template name: {template_name}\n\n"
            f"Global instructions:\n{global_instruction}\n\n"
            "EMIS sections to fill:\n"
            f"{chr(10).join(section_lines)}\n\n"
            f"Consultation transcript:\n{transcript_text}"
            + (f"\n\nConsultation working note:\n{working_note_text}" if working_note_text.strip() else "")
            + (f"\n\nPost-consultation dictation:\n{dictation_text}" if dictation_text.strip() else "")
        ),
    )


def _parse_generated_note_json(payload_text: str) -> tuple[str, str]:
    payload = None
    for candidate in _generated_note_json_candidates(payload_text):
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned invalid JSON for the note output",
            {"provider_error_code": "invalid_json"},
        )

    if not isinstance(payload, dict):
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned an invalid note JSON object",
            {"provider_error_code": "invalid_json_shape"},
        )

    title = payload.get("title")
    content = payload.get("content")
    if not isinstance(title, str) or not title.strip():
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned a note JSON object without a valid title",
            {"provider_error_code": "missing_title"},
        )
    if not isinstance(content, str) or not content.strip():
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned a note JSON object without valid content",
            {"provider_error_code": "missing_content"},
        )

    return title.strip(), content.strip()


def _parse_generated_structured_note_json(
    payload_text: str,
    *,
    template_config: StructuredTemplateConfig,
) -> tuple[str, list[dict[str, str | int]]]:
    payload = None
    for candidate in _generated_note_json_candidates(payload_text):
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned invalid JSON for the structured note output",
            {"provider_error_code": "invalid_json"},
        )
    if not isinstance(payload, dict):
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned an invalid structured note JSON object",
            {"provider_error_code": "invalid_json_shape"},
        )
    title = payload.get("title")
    content = payload.get("content")
    if not isinstance(title, str) or not title.strip():
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned a structured note JSON object without a valid title",
            {"provider_error_code": "missing_title"},
        )
    if not isinstance(content, dict):
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned a structured note JSON object without a valid content object",
            {"provider_error_code": "invalid_json_shape"},
        )
    allowed_section_keys = {section.section_key for section in template_config.sections}
    unknown_keys = [key for key in content.keys() if key not in allowed_section_keys]
    if unknown_keys:
        raise AppError(
            502,
            "llm_generation_invalid_json",
            "LLM generation returned unsupported structured note sections",
            {"provider_error_code": "invalid_section_key"},
        )
    parsed_sections: list[dict[str, str | int]] = []
    for section in sorted(template_config.sections, key=lambda item: item.section_order):
        raw_value = content.get(section.section_key)
        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            raise AppError(
                502,
                "llm_generation_invalid_json",
                "LLM generation returned a structured note section with a non-string value",
                {"provider_error_code": "invalid_section_value", "section_key": section.section_key},
            )
        normalized = raw_value.strip()
        if not normalized:
            continue
        parsed_sections.append(
            {
                "section_key": section.section_key,
                "section_label": section.section_label,
                "section_order": section.section_order,
                "text": normalized,
            }
        )
    return title.strip(), parsed_sections


def _generated_note_json_candidates(payload_text: str) -> list[str]:
    candidates: list[str] = []
    normalized = payload_text.strip()
    if normalized:
        candidates.append(normalized)
        unfenced = _strip_markdown_code_fence(normalized)
        if unfenced and unfenced not in candidates:
            candidates.append(unfenced)
        extracted = _extract_first_balanced_json_object(unfenced or normalized)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
    return candidates


def _strip_markdown_code_fence(payload_text: str) -> str:
    stripped = payload_text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_first_balanced_json_object(payload_text: str) -> str | None:
    start = payload_text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(payload_text)):
        char = payload_text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return payload_text[start : index + 1]
    return None


def _transcript_title_can_be_auto_filled(title: str | None) -> bool:
    normalized = (title or "").strip()
    return normalized in {"", "Untitled session"}


def _effective_dictation_text(db: Session, *, transcript: Transcript) -> str:
    dictation = get_post_consultation_dictation(db, db.get(User, transcript.owner_user_id), transcript_id=transcript.id)
    if dictation is None:
        return ""
    return dictation_effective_text(db, dictation=dictation).strip()


def _redact_dynamic_prompt_text(
    db: Session,
    text: str | None,
    *,
    team_id: UUID,
    start_index: int,
) -> tuple[str | None, list[dict[str, Any]]]:
    if text is None:
        return None, []
    if not text.strip():
        return text, []
    result = redact_transient_text(db, text, team_id=team_id, start_index=start_index)
    return result["redacted_text"], list(result["phi_index"])


def _redact_dynamic_prompt_value(
    db: Session,
    value: object,
    *,
    team_id: UUID,
    start_index: int,
) -> tuple[object, list[dict[str, Any]]]:
    if isinstance(value, str):
        return _redact_dynamic_prompt_text(db, value, team_id=team_id, start_index=start_index)
    if isinstance(value, list):
        redacted_items: list[object] = []
        phi_index: list[dict[str, Any]] = []
        next_index = start_index
        for item in value:
            redacted_item, item_phi_index = _redact_dynamic_prompt_value(
                db,
                item,
                team_id=team_id,
                start_index=next_index,
            )
            redacted_items.append(redacted_item)
            phi_index.extend(item_phi_index)
            next_index += len(item_phi_index)
        return redacted_items, phi_index
    if isinstance(value, dict):
        redacted_dict: dict[object, object] = {}
        phi_index: list[dict[str, Any]] = []
        next_index = start_index
        for key, item in value.items():
            redacted_item, item_phi_index = _redact_dynamic_prompt_value(
                db,
                item,
                team_id=team_id,
                start_index=next_index,
            )
            redacted_dict[key] = redacted_item
            phi_index.extend(item_phi_index)
            next_index += len(item_phi_index)
        return redacted_dict, phi_index
    return value, []


def _redacted_generation_source_texts(
    db: Session,
    *,
    transcript_version: TranscriptVersion,
    redaction_run,
    dictation_text: str,
) -> tuple[str, str, list[dict[str, str | int]]]:
    transcript_text = redaction_run_text(db, run=redaction_run) or ""
    if not dictation_text.strip():
        return transcript_text, "", []
    redacted_dictation_text, phi_index = _redact_dynamic_prompt_text(
        db,
        dictation_text,
        team_id=transcript_version.transcript.team_id,
        start_index=next_placeholder_index(redaction_run),
    )
    return transcript_text.strip(), (redacted_dictation_text or "").strip(), phi_index


def _manual_pii_entities_for_transcript(db: Session, *, transcript_id: UUID, owner_user_id: UUID) -> list[TranscriptManualPiiEntity]:
    return list(
        db.scalars(
            select(TranscriptManualPiiEntity)
            .where(
                TranscriptManualPiiEntity.transcript_id == transcript_id,
                TranscriptManualPiiEntity.owner_user_id == owner_user_id,
            )
            .order_by(TranscriptManualPiiEntity.created_at.asc(), TranscriptManualPiiEntity.id.asc())
        )
    )


def _manual_pii_value_pattern(value: str) -> re.Pattern[str]:
    tokens = [token for token in re.split(r"\s+", value.strip()) if token]
    return re.compile(r"\s+".join(re.escape(token) for token in tokens), re.IGNORECASE)


def _apply_manual_pii_redaction(
    db: Session,
    *,
    transcript_id: UUID,
    owner_user_id: UUID,
    transcript_text: str,
    dictation_text: str,
    start_index: int,
) -> tuple[str, str, list[dict[str, str | int]]]:
    manual_entities = _manual_pii_entities_for_transcript(db, transcript_id=transcript_id, owner_user_id=owner_user_id)
    if not manual_entities:
        return transcript_text, dictation_text, []

    redacted_transcript_text = transcript_text
    redacted_dictation_text = dictation_text
    phi_index: list[dict[str, str | int]] = []
    next_index = start_index
    seen_values: set[str] = set()
    manual_items: list[tuple[str, str]] = []
    for entity in manual_entities:
        value = manual_pii_entity_value(db, entity=entity).strip()
        normalized_key = value.lower()
        if not value or normalized_key in seen_values:
            continue
        seen_values.add(normalized_key)
        manual_items.append((entity.entity_type, value))

    for entity_type, value in sorted(manual_items, key=lambda item: len(item[1]), reverse=True):
        pattern = _manual_pii_value_pattern(value)
        if pattern.search(redacted_transcript_text) is None and pattern.search(redacted_dictation_text) is None:
            continue
        placeholder = f"[PHI-{next_index}]"
        redacted_transcript_text = pattern.sub(placeholder, redacted_transcript_text)
        redacted_dictation_text = pattern.sub(placeholder, redacted_dictation_text)
        phi_index.append(
            {
                "index": next_index,
                "type": entity_type,
                "value": value,
                "placeholder": placeholder,
            }
        )
        next_index += 1
    return redacted_transcript_text, redacted_dictation_text, phi_index


def _build_followup_generation_messages(*, transcript_text: str, follow_up_prompt_text: str, dictation_text: str = "") -> tuple[str, str]:
    return (
        "You are a medical secretary writing in British English. "
        "Write a follow-up from doctor's perspective based only on consultation sources and requested follow-up task. "
        "If post-consultation dictation is provided, treat it as stronger clinician-authored guidance for summary, assessment, terminology, and plan wording, while keeping transcript as chronology and factual anchor. "
        "Return only the finished follow-up text. "
        "The transcript and request may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        f"Consultation transcript:\n{transcript_text}"
        + (f"\n\nPost-consultation dictation:\n{dictation_text}" if dictation_text.strip() else "")
        + f"\n\nFollow-up request:\n{follow_up_prompt_text}",
    )


def _build_quick_action_generation_messages(*, transcript_text: str, quick_action_text: str, dictation_text: str = "") -> tuple[str, str]:
    return (
        "You are a medical secretary writing in British English. "
        "Write from the perspective of the doctor. "
        "Complete the requested quick action using only consultation sources and quick action instructions. "
        "If post-consultation dictation is provided, treat it as stronger clinician-authored guidance for summary, assessment, terminology, and plan wording, while keeping transcript as chronology and factual anchor. "
        "Return only the finished text. "
        "The transcript and instructions may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        f"Consultation transcript:\n{transcript_text}"
        + (f"\n\nPost-consultation dictation:\n{dictation_text}" if dictation_text.strip() else "")
        + f"\n\nQuick action instructions:\n{quick_action_text}",
    )


def _render_structured_sections_text(sections: list[dict[str, str | int]]) -> str:
    rendered_parts: list[str] = []
    for section in sections:
        section_label = str(section["section_label"]).strip()
        section_text = str(section["text"]).strip()
        if section_text:
            rendered_parts.append(f"{section_label}\n{section_text}")
    return "\n\n".join(rendered_parts).strip()


def queue_document_generation_from_template(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    template_id: UUID,
    structured_context: dict[str, str] | None = None,
) -> GeneratedDocument:
    _require_team_member(actor)
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")

    template = _resolve_available_template_for_user(db, actor, template_id=template_id)
    latest_version = _latest_template_version(db, template_id=template.id)
    template_config = _template_version_config(latest_version)
    working_note_mode, freeform_working_note_snapshot, structured_working_note_snapshot = _working_note_snapshot_for_transcript(db, transcript=transcript)
    raw_structured_context = None
    if isinstance(structured_working_note_snapshot, dict):
        transcript_sections = structured_working_note_snapshot.get("sections")
        if isinstance(transcript_sections, dict):
            raw_structured_context = {
                str(section_key): value
                for section_key, value in transcript_sections.items()
                if isinstance(section_key, str)
            }
    elif structured_context is not None and working_note_mode is None:
        raw_structured_context = structured_context
        structured_working_note_snapshot = {"profile": "emis", "sections": structured_context}
        working_note_mode = TranscriptWorkingNoteMode.structured
    serialized_structured_context = _serialize_structured_context(
        raw_context=raw_structured_context,
        template_config=template_config,
        ignore_unsupported_sections=True,
    )
    if structured_context is not None and template_config is not None and transcript.working_note_mode in {None, TranscriptWorkingNoteMode.structured}:
        set_transcript_structured_context(
            db,
            transcript=transcript,
            plaintext={
                "profile": template_config.profile,
                "sections": dict(serialized_structured_context or {}),
            },
        )
        if serialized_structured_context:
            transcript.working_note_mode = TranscriptWorkingNoteMode.structured
            transcript.working_note_updated_at = utcnow()
        db.add(transcript)
    transcript_version = _snapshot_transcript_version(
        db,
        transcript=transcript,
        allow_empty=(
            bool(freeform_working_note_snapshot.strip())
            or bool(serialized_structured_context)
            or bool(_effective_dictation_text(db, transcript=transcript))
        ),
    )

    _, config, resolved_model_name, _ = resolve_user_llm(db, actor)
    if not resolved_model_name:
        raise AppError(422, "business_rule_violation", "No active LLM model is configured for this user", {"field": "preferred_model_name"})

    generated_document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=actor.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=None,
        generator_type=GeneratedDocumentGeneratorType.template,
        template_version_id=latest_version.id,
        llm_config_id=config.id,
        source_template_name=template.name,
        prompt_snapshot_text=latest_version.prompt_text,
        structured_context_json=None,
        working_note_mode_snapshot=working_note_mode,
        freeform_working_note_snapshot_encrypted=None,
        structured_working_note_snapshot_json=None,
        structured_section_definitions_json=_structured_section_definitions_snapshot(template_config),
        status=GeneratedDocumentStatus.queued,
        title=f"{template.name} output",
        document_mode=latest_version.mode,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used=resolved_model_name,
        llm_adapter_kind=config.adapter_kind.value,
        llm_base_url=config.base_url,
    )
    set_generated_document_structured_context(db, document=generated_document, plaintext=serialized_structured_context)
    set_generated_document_text(
        db,
        document=generated_document,
        field="freeform_working_note_snapshot_encrypted",
        plaintext=freeform_working_note_snapshot if working_note_mode is TranscriptWorkingNoteMode.freeform else None,
    )
    set_generated_document_structured_working_note_snapshot(
        db,
        document=generated_document,
        plaintext=structured_working_note_snapshot if working_note_mode is TranscriptWorkingNoteMode.structured else None,
    )
    set_generated_document_text(db, document=generated_document, field="original_output_text_encrypted", plaintext="")
    set_generated_document_text(db, document=generated_document, field="edited_output_text_encrypted", plaintext="")
    db.add(generated_document)
    db.commit()
    db.refresh(generated_document)
    _record_generation_usage_event(db, event="llm_generation_queued", document=generated_document, config=config)
    return generated_document


def queue_followup_generation(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    prompt_text: str,
) -> GeneratedDocument:
    _require_team_member(actor)
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")

    clean_prompt_text = prompt_text.strip()
    if not clean_prompt_text:
        raise AppError(422, "business_rule_violation", "Follow-up text is required", {"field": "prompt_text"})

    transcript_version = _snapshot_transcript_version(db, transcript=transcript)
    _, config, resolved_model_name, _ = resolve_user_llm(db, actor)
    if not resolved_model_name:
        raise AppError(422, "business_rule_violation", "No active LLM model is configured for this user", {"field": "preferred_model_name"})

    truncated_title = clean_prompt_text[:72]
    generated_document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=actor.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=None,
        generator_type=GeneratedDocumentGeneratorType.followup,
        template_version_id=None,
        llm_config_id=config.id,
        source_template_name="Follow-up",
        follow_up_prompt_text=None,
        status=GeneratedDocumentStatus.queued,
        title=f"Follow-up: {truncated_title}",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used=resolved_model_name,
        llm_adapter_kind=config.adapter_kind.value,
        llm_base_url=config.base_url,
    )
    set_generated_document_text(db, document=generated_document, field="follow_up_prompt_text", plaintext=clean_prompt_text)
    set_generated_document_text(db, document=generated_document, field="original_output_text_encrypted", plaintext="")
    set_generated_document_text(db, document=generated_document, field="edited_output_text_encrypted", plaintext="")
    db.add(generated_document)
    db.commit()
    db.refresh(generated_document)
    _record_generation_usage_event(db, event="llm_generation_queued", document=generated_document, config=config)
    return generated_document


def queue_quick_action_generation(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    quick_action_id: UUID,
    context_text: str | None = None,
) -> GeneratedDocument:
    _require_team_member(actor)
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")

    quick_action = _resolve_available_quick_action_for_user(db, actor, quick_action_id=quick_action_id)
    latest_version = _latest_quick_action_version(db, quick_action_id=quick_action.id)
    transcript_version = _snapshot_transcript_version(db, transcript=transcript)
    _, config, resolved_model_name, _ = resolve_user_llm(db, actor)
    if not resolved_model_name:
        raise AppError(422, "business_rule_violation", "No active LLM model is configured for this user", {"field": "preferred_model_name"})
    clean_context_text = (context_text or "").strip()
    prompt_snapshot_text = latest_version.prompt_text.strip()
    if clean_context_text:
        prompt_snapshot_text = f"{prompt_snapshot_text}{QUICK_ACTION_CONTEXT_MARKER}{clean_context_text}"

    generated_document = GeneratedDocument(
        id=uuid4(),
        owner_user_id=actor.id,
        team_id=transcript.team_id,
        transcript_id=transcript.id,
        transcript_version_id=transcript_version.id,
        redaction_run_id=None,
        generator_type=GeneratedDocumentGeneratorType.quick_action,
        template_version_id=None,
        quick_action_version_id=latest_version.id,
        llm_config_id=config.id,
        source_template_name="Quick action",
        source_quick_action_name=quick_action.name,
        prompt_snapshot_text=prompt_snapshot_text,
        status=GeneratedDocumentStatus.queued,
        title=f"Quick action: {quick_action.name}",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used=resolved_model_name,
        llm_adapter_kind=config.adapter_kind.value,
        llm_base_url=config.base_url,
    )
    set_generated_document_text(db, document=generated_document, field="original_output_text_encrypted", plaintext="")
    set_generated_document_text(db, document=generated_document, field="edited_output_text_encrypted", plaintext="")
    db.add(generated_document)
    db.commit()
    db.refresh(generated_document)
    _record_generation_usage_event(db, event="llm_generation_queued", document=generated_document, config=config)
    return generated_document


def attach_generated_document_task_id(db: Session, *, document_id: UUID, task_id: str | None) -> GeneratedDocument:
    document = db.get(GeneratedDocument, document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(document_id)})
    document.celery_task_id = task_id
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def mark_generated_document_enqueue_failed(db: Session, *, document_id: UUID, message: str) -> GeneratedDocument:
    document = db.get(GeneratedDocument, document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(document_id)})
    document.status = GeneratedDocumentStatus.failed
    document.error_code = "generation_enqueue_failed"
    document.error_message = message[:255]
    document.completed_at = utcnow()
    db.add(document)
    db.commit()
    db.refresh(document)
    _record_generation_usage_event(db, event="llm_generation_enqueue_failed", document=document, status=document.status.value)
    return document


def process_generated_document(db: Session, *, document_id: UUID) -> GeneratedDocument:
    document = db.get(GeneratedDocument, document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(document_id)})
    if document.status is GeneratedDocumentStatus.ready:
        return document

    config = _resolve_runtime_llm_config(db, document=document)
    transcript_version = db.get(TranscriptVersion, document.transcript_version_id)
    if transcript_version is None:
        raise AppError(422, "business_rule_violation", "Transcript snapshot is missing for this generated document")
    if not document.model_used:
        raise AppError(422, "business_rule_violation", "No resolved LLM model is stored for this generated document")
    redaction_run = ensure_redaction_run_for_transcript_version(db, transcript_version=transcript_version)
    document.redaction_run_id = redaction_run.id
    live_transcript = db.get(Transcript, document.transcript_id)
    dictation_text = _effective_dictation_text(db, transcript=live_transcript) if live_transcript is not None else ""
    transcript_text, dictation_text, extra_phi_index = _redacted_generation_source_texts(
        db,
        transcript_version=transcript_version,
        redaction_run=redaction_run,
        dictation_text=dictation_text,
    )
    transcript_text, dictation_text, manual_phi_index = _apply_manual_pii_redaction(
        db,
        transcript_id=document.transcript_id,
        owner_user_id=document.owner_user_id,
        transcript_text=transcript_text,
        dictation_text=dictation_text,
        start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
    )
    extra_phi_index.extend(manual_phi_index)

    if document.generator_type is GeneratedDocumentGeneratorType.template:
        working_note_text = ""
        if document.working_note_mode_snapshot is TranscriptWorkingNoteMode.freeform:
            freeform_working_note = generated_document_text(db, document=document, field="freeform_working_note_snapshot_encrypted").strip()
            if freeform_working_note:
                redacted_working_note, working_note_phi_index = _redact_dynamic_prompt_text(
                    db,
                    freeform_working_note,
                    team_id=document.team_id,
                    start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
                )
                extra_phi_index.extend(working_note_phi_index)
                redacted_working_note, _, manual_working_note_phi_index = _apply_manual_pii_redaction(
                    db,
                    transcript_id=document.transcript_id,
                    owner_user_id=document.owner_user_id,
                    transcript_text=redacted_working_note or "",
                    dictation_text="",
                    start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
                )
                extra_phi_index.extend(manual_working_note_phi_index)
                working_note_text = _format_working_note_for_prompt(
                    mode=TranscriptWorkingNoteMode.freeform,
                    freeform_text=redacted_working_note or "",
                )
        elif document.working_note_mode_snapshot is TranscriptWorkingNoteMode.structured:
            structured_working_note = generated_document_structured_working_note_snapshot(db, document=document)
            if structured_working_note:
                redacted_structured_working_note, structured_working_note_phi_index = _redact_dynamic_prompt_value(
                    db,
                    structured_working_note,
                    team_id=document.team_id,
                    start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
                )
                extra_phi_index.extend(structured_working_note_phi_index)
                if not isinstance(redacted_structured_working_note, dict):
                    raise AppError(500, "redaction_failed", "Working note redaction changed shape")
                working_note_text = _format_working_note_for_prompt(
                    mode=TranscriptWorkingNoteMode.structured,
                    structured_note=redacted_structured_working_note,
                )
                if working_note_text.strip():
                    working_note_text, _, manual_working_note_phi_index = _apply_manual_pii_redaction(
                        db,
                        transcript_id=document.transcript_id,
                        owner_user_id=document.owner_user_id,
                        transcript_text=working_note_text,
                        dictation_text="",
                        start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
                    )
                    extra_phi_index.extend(manual_working_note_phi_index)
        prompt_text = _prompt_snapshot_text_for_document(db, document=document)
        if not prompt_text:
            raise AppError(422, "business_rule_violation", "Template snapshot is missing for this generated document")
        template_config = None
        if document.template_version_id:
            template_version = db.get(PromptTemplateVersion, document.template_version_id)
            if template_version is not None:
                template_config = _template_version_config(template_version)
        structured_context_json = generated_document_structured_context(db, document=document)
        if document.document_mode is TemplateMode.structured:
            if template_config is None:
                raise AppError(422, "business_rule_violation", "Structured template config is missing for this generated document")
            redacted_structured_context, structured_phi_index = _redact_dynamic_prompt_value(
                db,
                structured_context_json,
                team_id=document.team_id,
                start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
            )
            extra_phi_index.extend(structured_phi_index)
            if not isinstance(redacted_structured_context, dict):
                raise AppError(500, "redaction_failed", "Structured context redaction changed shape")
            context_lines: list[str] = []
            for section in sorted(template_config.sections, key=lambda item: item.section_order):
                raw_prefill_value = redacted_structured_context.get(section.section_key, [])
                if isinstance(raw_prefill_value, list):
                    prefill_lines = [str(item).strip() for item in raw_prefill_value if isinstance(item, str) and item.strip()]
                elif isinstance(raw_prefill_value, str) and raw_prefill_value.strip():
                    prefill_lines = [raw_prefill_value.strip()]
                else:
                    prefill_lines = []
                if prefill_lines:
                    prefill_text = "\n".join(prefill_lines)
                    prefill_text, _, manual_prefill_phi_index = _apply_manual_pii_redaction(
                        db,
                        transcript_id=document.transcript_id,
                        owner_user_id=document.owner_user_id,
                        transcript_text=prefill_text,
                        dictation_text="",
                        start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
                    )
                    extra_phi_index.extend(manual_prefill_phi_index)
                    context_lines.append(f'- "{section.section_key}": {prefill_text}')
            context_block = "\n".join(context_lines).strip()
            prompt_with_context = prompt_text
            if context_block:
                prompt_with_context = f"{prompt_with_context}\n\nExisting section context to preserve or refine:\n{context_block}"
            system_message, user_message = _build_structured_template_generation_messages(
                template_name=document.source_template_name,
                global_instruction=prompt_with_context,
                transcript_text=transcript_text,
                dictation_text=dictation_text,
                template_config=template_config,
                working_note_text=working_note_text,
            )
        else:
            system_message, user_message = _build_template_generation_messages(
                template_name=document.source_template_name,
                prompt_text=prompt_text,
                transcript_text=transcript_text,
                dictation_text=dictation_text,
                working_note_text=working_note_text,
            )
    elif document.generator_type is GeneratedDocumentGeneratorType.followup:
        follow_up_prompt_text = generated_document_text(db, document=document, field="follow_up_prompt_text").strip()
        if not follow_up_prompt_text:
            raise AppError(422, "business_rule_violation", "Follow-up prompt text is missing for this generated document")
        redacted_follow_up_prompt_text, follow_up_phi_index = _redact_dynamic_prompt_text(
            db,
            follow_up_prompt_text,
            team_id=document.team_id,
            start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
        )
        extra_phi_index.extend(follow_up_phi_index)
        prompt_text_redacted, _, manual_prompt_phi_index = _apply_manual_pii_redaction(
            db,
            transcript_id=document.transcript_id,
            owner_user_id=document.owner_user_id,
            transcript_text=redacted_follow_up_prompt_text or "",
            dictation_text="",
            start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
        )
        extra_phi_index.extend(manual_prompt_phi_index)
        system_message, user_message = _build_followup_generation_messages(
            transcript_text=transcript_text,
            follow_up_prompt_text=prompt_text_redacted,
            dictation_text=dictation_text,
        )
    elif document.generator_type is GeneratedDocumentGeneratorType.quick_action:
        prompt_text = _prompt_snapshot_text_for_document(db, document=document)
        if not prompt_text:
            raise AppError(422, "business_rule_violation", "Quick action snapshot is missing for this generated document")
        quick_action_prompt_text = prompt_text
        quick_action_context_text = ""
        if QUICK_ACTION_CONTEXT_MARKER in prompt_text:
            quick_action_prompt_text, quick_action_context_text = prompt_text.split(QUICK_ACTION_CONTEXT_MARKER, 1)
        prompt_text_redacted = quick_action_prompt_text
        if quick_action_context_text.strip():
            redacted_context_text, context_phi_index = _redact_dynamic_prompt_text(
                db,
                quick_action_context_text,
                team_id=document.team_id,
                start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
            )
            extra_phi_index.extend(context_phi_index)
            redacted_context_text, _, manual_context_phi_index = _apply_manual_pii_redaction(
                db,
                transcript_id=document.transcript_id,
                owner_user_id=document.owner_user_id,
                transcript_text=redacted_context_text or "",
                dictation_text="",
                start_index=next_placeholder_index(redaction_run) + len(extra_phi_index),
            )
            extra_phi_index.extend(manual_context_phi_index)
            prompt_text_redacted = f"{prompt_text_redacted}{QUICK_ACTION_CONTEXT_MARKER}{redacted_context_text}"
        system_message, user_message = _build_quick_action_generation_messages(
            transcript_text=transcript_text,
            quick_action_text=prompt_text_redacted,
            dictation_text=dictation_text,
        )
    else:  # pragma: no cover
        raise AppError(422, "business_rule_violation", "Unsupported generated document type", {"generator_type": document.generator_type.value})

    adapter_kind = LlmAdapterKind(document.llm_adapter_kind or config.adapter_kind.value)
    base_url = document.llm_base_url or config.base_url
    llm_request_payload = _generation_request_snapshot(
        adapter_kind=adapter_kind,
        model=document.model_used,
        user_id=document.owner_user_id,
        system_message=system_message,
        user_message=user_message,
    )
    set_generated_document_llm_request_payload(db, document=document, plaintext=llm_request_payload)

    document.status = GeneratedDocumentStatus.processing
    document.started_at = document.started_at or utcnow()
    document.completed_at = None
    document.error_code = None
    document.provider_error_code = None
    document.provider_http_status = None
    document.error_message = None
    document.input_token_count = None
    document.output_token_count = None
    document.total_token_count = None
    document.estimated_cost_usd = None
    document.duration_ms = None
    document.provider_duration_ms = None
    db.add(document)
    db.commit()
    db.refresh(document)
    _record_generation_usage_event(db, event="llm_generation_started", document=document, config=config, status=document.status.value)

    try:
        bearer_token = read_team_llm_bearer_token(team_id=document.team_id, config_id=config.id) if config.vault_secret_ref else None
        request_body = llm_request_payload
        if adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
            generated_text, usage = _generate_freeform_output_openai(
                api_key=bearer_token or "",
                base_url=base_url,
                request_body=request_body,
            )
        elif adapter_kind is LlmAdapterKind.ollama_chat:
            generated_text, usage = _generate_freeform_output_ollama(
                base_url=base_url,
                bearer_token=bearer_token,
                request_body=request_body,
            )
        else:  # pragma: no cover
            raise AppError(422, "business_rule_violation", "Unsupported LLM adapter", {"adapter_kind": adapter_kind.value})
    except AppError as exc:
        document.status = GeneratedDocumentStatus.failed
        document.error_code = exc.code
        document.error_message = exc.message[:255]
        document.provider_error_code = (exc.details or {}).get("provider_error_code")
        document.provider_http_status = (exc.details or {}).get("provider_http_status")
        document.completed_at = utcnow()
        db.add(document)
        db.commit()
        db.refresh(document)
        _record_generation_usage_event(db, event="llm_generation_failed", document=document, config=config, status=document.status.value)
        return document
    except Exception:
        document.status = GeneratedDocumentStatus.failed
        document.error_code = "llm_generation_failed"
        document.error_message = "LLM generation failed"
        document.completed_at = utcnow()
        db.add(document)
        db.commit()
        db.refresh(document)
        _record_generation_usage_event(db, event="llm_generation_failed", document=document, config=config, status=document.status.value)
        return document

    phi_index = combined_phi_index(db, redaction_run, extra_phi_index=list(extra_phi_index))

    try:
        if document.generator_type is GeneratedDocumentGeneratorType.template:
            restored_title = None
            structured_sections: list[dict[str, str | int]] = []
            if document.document_mode is TemplateMode.structured:
                template_version = db.get(PromptTemplateVersion, document.template_version_id) if document.template_version_id else None
                template_config = _template_version_config(template_version) if template_version is not None else None
                if template_config is None:
                    raise AppError(422, "business_rule_violation", "Structured template config is missing for this generated document")
                redacted_title, redacted_sections = _parse_generated_structured_note_json(generated_text, template_config=template_config)
                restored_title = reidentify_text(redacted_title, phi_index=phi_index)
                for section in redacted_sections:
                    restored_section_text = reidentify_text(str(section["text"]), phi_index=phi_index)
                    structured_sections.append(
                        {
                            "section_key": str(section["section_key"]),
                            "section_label": str(section["section_label"]),
                            "section_order": int(section["section_order"]),
                            "text": restored_section_text,
                        }
                    )
                restored_text = _render_structured_sections_text(structured_sections)
            else:
                redacted_title, redacted_content = _parse_generated_note_json(generated_text)
                restored_title = reidentify_text(redacted_title, phi_index=phi_index)
                restored_text = reidentify_text(redacted_content, phi_index=phi_index)
        else:
            restored_text = reidentify_text(
                generated_text,
                phi_index=phi_index,
            )
            restored_title = None
            structured_sections = []
    except AppError as exc:
        document.status = GeneratedDocumentStatus.failed
        document.error_code = exc.code
        document.error_message = exc.message[:255]
        document.provider_error_code = (exc.details or {}).get("provider_error_code")
        set_generated_document_text(
            db,
            document=document,
            field="failed_provider_output_redacted_encrypted",
            plaintext=generated_text,
        )
        document.completed_at = utcnow()
        db.add(document)
        db.commit()
        db.refresh(document)
        _record_generation_usage_event(db, event="llm_generation_failed", document=document, config=config, status=document.status.value)
        return document
    set_generated_document_text(db, document=document, field="original_output_text_encrypted", plaintext=restored_text)
    set_generated_document_text(db, document=document, field="edited_output_text_encrypted", plaintext=restored_text)
    document.failed_provider_output_redacted_encrypted = None
    if document.generator_type is GeneratedDocumentGeneratorType.template:
        for existing_section in list(document.sections):
            db.delete(existing_section)
        for section in structured_sections:
            section_record = GeneratedDocumentSection(
                id=uuid4(),
                generated_document_id=document.id,
                section_key=str(section["section_key"]),
                section_label=str(section["section_label"]),
                section_order=int(section["section_order"]),
                original_text_encrypted="",
                edited_text_encrypted="",
                is_edited=False,
            )
            set_generated_document_section_text(
                db,
                section=section_record,
                field="original_text_encrypted",
                owner_user_id=document.owner_user_id,
                plaintext=str(section["text"]),
            )
            set_generated_document_section_text(
                db,
                section=section_record,
                field="edited_text_encrypted",
                owner_user_id=document.owner_user_id,
                plaintext=str(section["text"]),
            )
            db.add(section_record)
    if restored_title is not None:
        document.title = restored_title[:255]
        transcript = db.get(Transcript, document.transcript_id)
        if transcript is not None and _transcript_title_can_be_auto_filled(transcript.title):
            transcript.title = restored_title[:255]
            db.add(transcript)
    document.status = GeneratedDocumentStatus.ready
    document.completed_at = utcnow()
    document.error_code = None
    document.provider_error_code = None
    document.provider_http_status = None
    document.error_message = None
    document.input_token_count = usage.get("input_tokens")
    document.output_token_count = usage.get("output_tokens")
    document.total_token_count = usage.get("total_tokens")
    document.duration_ms = usage.get("duration_ms")
    document.provider_duration_ms = usage.get("provider_duration_ms")
    document.estimated_cost_usd = _estimated_cost_usd(config=config, usage=usage)
    db.add(document)
    db.commit()
    db.refresh(document)
    _record_generation_usage_event(
        db,
        event="llm_generation_completed",
        document=document,
        config=config,
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        duration_ms=usage.get("duration_ms"),
        provider_duration_ms=usage.get("provider_duration_ms"),
        status=document.status.value,
    )
    return document
