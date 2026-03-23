import logging
import time
from uuid import UUID, uuid4

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    GeneratedDocument,
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
    TranscriptStatus,
    TranscriptVersion,
    User,
    utcnow,
)
from app.schemas.templates import PromptTemplateUpsert, QuickActionUpsert
from app.services.llm import resolve_user_llm
from app.services.redaction import (
    combined_phi_index,
    ensure_redaction_run_for_transcript_version,
    next_placeholder_index,
    redact_transient_text,
    reidentify_text,
)
from app.services.vault import read_team_llm_bearer_token


usage_logger = logging.getLogger("openscribe.usage")


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
    template = _resolve_team_template_for_management(db, actor, template_id=payload.template_id) if payload.template_id else None
    if template is None:
        template = PromptTemplate(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=actor.team_id,
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            is_active=payload.is_active,
            created_by_user_id=actor.id,
        )
        db.add(template)
        db.flush()
    else:
        template.name = payload.name.strip()
        template.description = (payload.description or "").strip() or None
        template.is_active = payload.is_active
        db.add(template)

    version = PromptTemplateVersion(
        id=uuid4(),
        template_id=template.id,
        version_no=_next_template_version_no(db, template_id=template.id),
        mode=TemplateMode.freeform,
        prompt_text=prompt_text,
        created_by_user_id=actor.id,
    )
    db.add(version)
    db.commit()
    db.refresh(template)
    return template


def upsert_personal_template(db: Session, actor: User, payload: PromptTemplateUpsert) -> PromptTemplate:
    _require_team_member(actor)
    if payload.scope is not TemplateScope.user:
        raise AppError(422, "business_rule_violation", "Personal template payload must use user scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    template = _resolve_personal_template_for_management(db, actor, template_id=payload.template_id) if payload.template_id else None
    if template is None:
        template = PromptTemplate(
            id=uuid4(),
            scope=TemplateScope.user,
            owner_user_id=actor.id,
            team_id=None,
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            is_active=payload.is_active,
            created_by_user_id=actor.id,
        )
        db.add(template)
        db.flush()
    else:
        template.name = payload.name.strip()
        template.description = (payload.description or "").strip() or None
        template.is_active = payload.is_active
        db.add(template)

    version = PromptTemplateVersion(
        id=uuid4(),
        template_id=template.id,
        version_no=_next_template_version_no(db, template_id=template.id),
        mode=TemplateMode.freeform,
        prompt_text=prompt_text,
        created_by_user_id=actor.id,
    )
    db.add(version)
    db.commit()
    db.refresh(template)
    return template


def upsert_team_quick_action(db: Session, actor: User, payload: QuickActionUpsert) -> QuickAction:
    _require_team_leader(actor)
    if payload.scope is not TemplateScope.team:
        raise AppError(422, "business_rule_violation", "Team quick action payload must use team scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    quick_action = _resolve_team_quick_action_for_management(db, actor, quick_action_id=payload.quick_action_id) if payload.quick_action_id else None
    if quick_action is None:
        quick_action = QuickAction(
            id=uuid4(),
            scope=TemplateScope.team,
            owner_user_id=None,
            team_id=actor.team_id,
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            is_active=payload.is_active,
            created_by_user_id=actor.id,
        )
        db.add(quick_action)
        db.flush()
    else:
        quick_action.name = payload.name.strip()
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
    db.commit()
    db.refresh(quick_action)
    return quick_action


def upsert_personal_quick_action(db: Session, actor: User, payload: QuickActionUpsert) -> QuickAction:
    _require_team_member(actor)
    if payload.scope is not TemplateScope.user:
        raise AppError(422, "business_rule_violation", "Personal quick action payload must use user scope", {"field": "scope"})
    prompt_text = _serialize_prompt_text(payload.prompt_text)
    quick_action = _resolve_personal_quick_action_for_management(db, actor, quick_action_id=payload.quick_action_id) if payload.quick_action_id else None
    if quick_action is None:
        quick_action = QuickAction(
            id=uuid4(),
            scope=TemplateScope.user,
            owner_user_id=actor.id,
            team_id=None,
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            is_active=payload.is_active,
            created_by_user_id=actor.id,
        )
        db.add(quick_action)
        db.flush()
    else:
        quick_action.name = payload.name.strip()
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
    db.commit()
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


def _snapshot_transcript_version(db: Session, *, transcript: Transcript) -> TranscriptVersion:
    current_text = (transcript.current_draft_text_encrypted or "").strip()
    if not current_text:
        raise AppError(422, "business_rule_violation", "Transcript draft is empty", {"field": "current_draft_text_encrypted"})
    current_max = db.scalar(select(func.max(TranscriptVersion.version_no)).where(TranscriptVersion.transcript_id == transcript.id))
    version = TranscriptVersion(
        id=uuid4(),
        transcript_id=transcript.id,
        version_no=(current_max or 0) + 1,
        text_encrypted=current_text,
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


def _estimated_cost_usd(*, config: TeamLlmConfig, usage: dict[str, int | None]) -> float | None:
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


def _generate_freeform_output_openai(
    *,
    api_key: str,
    base_url: str,
    model: str,
    user_id: UUID,
    system_message: str,
    user_message: str,
) -> tuple[str, dict[str, int | None]]:
    started = time.perf_counter()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_completion_tokens=1600,
            user=str(user_id),
            messages=[
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
        )
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
    return generated_text, {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _generate_freeform_output_ollama(
    *,
    base_url: str,
    bearer_token: str | None,
    model: str,
    system_message: str,
    user_message: str,
) -> tuple[str, dict[str, int | None]]:
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/chat",
            headers=headers,
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": system_message,
                    },
                    {
                        "role": "user",
                        "content": user_message,
                    },
                ],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover
        if isinstance(exc, ValueError):
            raise AppError(
                502,
                "llm_provider_bad_response",
                "The LLM provider returned an unreadable response",
                {"provider_error_code": "invalid_json"},
            ) from exc
        raise _translate_ollama_generation_error(exc) from exc

    message = payload.get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    generated_text = content.strip() if isinstance(content, str) else ""
    if not generated_text:
        raise AppError(502, "llm_generation_failed", "LLM generation returned no note text")
    prompt_tokens = payload.get("prompt_eval_count")
    completion_tokens = payload.get("eval_count")
    provider_duration_raw = payload.get("total_duration")
    provider_duration_ms = int(provider_duration_raw / 1_000_000) if isinstance(provider_duration_raw, int) else None
    total_tokens = None
    if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
        total_tokens = prompt_tokens + completion_tokens
    return generated_text, {
        "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
        "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
        "total_tokens": total_tokens,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "provider_duration_ms": provider_duration_ms,
    }


def _build_template_generation_messages(
    *,
    template_name: str,
    prompt_text: str,
    transcript_text: str,
) -> tuple[str, str]:
    return (
        "You generate concise note output from a transcript using the provided template instructions. "
        "Return only the finished note text. "
        "The transcript and instructions may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        (
            f"Template name: {template_name}\n\n"
            f"Template instructions:\n{prompt_text}\n\n"
            f"Transcript:\n{transcript_text}"
        ),
    )


def _build_followup_generation_messages(*, transcript_text: str, follow_up_prompt_text: str) -> tuple[str, str]:
    return (
        "You are a medical secretary writing in British English. "
        "Write a follow-up from the doctor's perspective based only on the transcript and the requested follow-up task. "
        "Return only the finished follow-up text. "
        "The transcript and request may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        f"Transcript:\n{transcript_text}\n\nFollow-up request:\n{follow_up_prompt_text}",
    )


def _build_quick_action_generation_messages(*, transcript_text: str, quick_action_text: str) -> tuple[str, str]:
    return (
        "You are a medical secretary writing in British English. "
        "Write from the perspective of the doctor. "
        "Complete the requested quick action using only the transcript and the quick action instructions. "
        "Return only the finished text. "
        "The transcript and instructions may contain pseudonym placeholders like [PHI-1]. "
        "Treat them as deliberate confidential replacements. Preserve any placeholder exactly as written and do not invent new placeholders.",
        f"Transcript:\n{transcript_text}\n\nQuick action instructions:\n{quick_action_text}",
    )


def queue_document_generation_from_template(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    template_id: UUID,
) -> GeneratedDocument:
    _require_team_member(actor)
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != actor.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")

    template = _resolve_available_template_for_user(db, actor, template_id=template_id)
    latest_version = _latest_template_version(db, template_id=template.id)
    transcript_version = _snapshot_transcript_version(db, transcript=transcript)

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
        status=GeneratedDocumentStatus.queued,
        title=f"{template.name} output",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted="",
        edited_output_text_encrypted="",
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used=resolved_model_name,
        llm_adapter_kind=config.adapter_kind.value,
        llm_base_url=config.base_url,
    )
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
        follow_up_prompt_text=clean_prompt_text,
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
        prompt_snapshot_text=latest_version.prompt_text,
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
    transcript_text = redaction_run.redacted_text_encrypted or ""
    extra_phi_index: list[dict[str, str | int]] = []

    if document.generator_type is GeneratedDocumentGeneratorType.template:
        prompt_text = _prompt_snapshot_text_for_document(db, document=document)
        if not prompt_text:
            raise AppError(422, "business_rule_violation", "Template snapshot is missing for this generated document")
        prompt_redaction = redact_transient_text(prompt_text, start_index=next_placeholder_index(redaction_run))
        extra_phi_index = list(prompt_redaction["phi_index"])
        system_message, user_message = _build_template_generation_messages(
            template_name=document.source_template_name,
            prompt_text=prompt_redaction["redacted_text"],
            transcript_text=transcript_text,
        )
    elif document.generator_type is GeneratedDocumentGeneratorType.followup:
        follow_up_prompt_text = (document.follow_up_prompt_text or "").strip()
        if not follow_up_prompt_text:
            raise AppError(422, "business_rule_violation", "Follow-up prompt text is missing for this generated document")
        prompt_redaction = redact_transient_text(follow_up_prompt_text, start_index=next_placeholder_index(redaction_run))
        extra_phi_index = list(prompt_redaction["phi_index"])
        system_message, user_message = _build_followup_generation_messages(
            transcript_text=transcript_text,
            follow_up_prompt_text=prompt_redaction["redacted_text"],
        )
    elif document.generator_type is GeneratedDocumentGeneratorType.quick_action:
        prompt_text = _prompt_snapshot_text_for_document(db, document=document)
        if not prompt_text:
            raise AppError(422, "business_rule_violation", "Quick action snapshot is missing for this generated document")
        prompt_redaction = redact_transient_text(prompt_text, start_index=next_placeholder_index(redaction_run))
        extra_phi_index = list(prompt_redaction["phi_index"])
        system_message, user_message = _build_quick_action_generation_messages(
            transcript_text=transcript_text,
            quick_action_text=prompt_redaction["redacted_text"],
        )
    else:  # pragma: no cover
        raise AppError(422, "business_rule_violation", "Unsupported generated document type", {"generator_type": document.generator_type.value})

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
        adapter_kind = LlmAdapterKind(document.llm_adapter_kind or config.adapter_kind.value)
        base_url = document.llm_base_url or config.base_url
        if adapter_kind is LlmAdapterKind.openai_chat:
            generated_text, usage = _generate_freeform_output_openai(
                api_key=bearer_token or "",
                base_url=base_url,
                model=document.model_used,
                user_id=document.owner_user_id,
                system_message=system_message,
                user_message=user_message,
            )
        elif adapter_kind is LlmAdapterKind.ollama_chat:
            generated_text, usage = _generate_freeform_output_ollama(
                base_url=base_url,
                bearer_token=bearer_token,
                model=document.model_used,
                system_message=system_message,
                user_message=user_message,
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

    try:
        restored_text = reidentify_text(
            generated_text,
            phi_index=combined_phi_index(redaction_run, extra_phi_index=list(extra_phi_index)),
        )
    except AppError as exc:
        document.status = GeneratedDocumentStatus.failed
        document.error_code = exc.code
        document.error_message = exc.message[:255]
        document.completed_at = utcnow()
        db.add(document)
        db.commit()
        db.refresh(document)
        _record_generation_usage_event(db, event="llm_generation_failed", document=document, config=config, status=document.status.value)
        return document
    document.original_output_text_encrypted = restored_text
    document.edited_output_text_encrypted = restored_text
    document.status = GeneratedDocumentStatus.ready
    document.completed_at = utcnow()
    document.error_code = None
    document.provider_error_code = None
    document.provider_http_status = None
    document.error_message = None
    document.input_token_count = _usage_int(usage.get("prompt_tokens"))
    document.output_token_count = _usage_int(usage.get("completion_tokens"))
    document.total_token_count = _usage_int(usage.get("total_tokens"))
    document.duration_ms = _usage_int(usage.get("duration_ms"))
    document.provider_duration_ms = _usage_int(usage.get("provider_duration_ms"))
    document.estimated_cost_usd = _estimated_cost_usd(config=config, usage=usage)
    db.add(document)
    db.commit()
    db.refresh(document)
    _record_generation_usage_event(
        db,
        event="llm_generation_completed",
        document=document,
        config=config,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        duration_ms=usage.get("duration_ms"),
        provider_duration_ms=usage.get("provider_duration_ms"),
        status=document.status.value,
    )
    return document
