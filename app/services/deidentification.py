from __future__ import annotations

import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import (
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    DeidentificationProvider,
    Team,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    TeamRole,
    User,
)
from app.schemas import (
    DeidentificationProviderAssignmentUpsert,
    DeidentificationProviderUpsert,
    DeidentificationSelectionUpsert,
)
from app.services.vault import (
    delete_deidentification_bearer_token,
    read_deidentification_bearer_token,
    write_deidentification_bearer_token,
)


BUILTIN_DEIDENTIFICATION_PROVIDER_ID = UUID("00000000-0000-0000-0000-00000000d1d1")
cleanup_logger = logging.getLogger("openscribe.cleanup")


def _delete_deidentification_secret_best_effort(*, provider_id: UUID, secret_ref: str, event: str) -> None:
    try:
        delete_deidentification_bearer_token(provider_id=provider_id, secret_ref=secret_ref)
    except AppError as exc:
        cleanup_logger.warning(
            event,
            extra={"provider_id": str(provider_id), "error_code": exc.code},
        )


def _resolve_team(db: Session, *, team_id: UUID) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(team_id)})
    return team


def _resolve_admin_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin de-identification provisioning access required")
    if team_id is None:
        raise AppError(422, "business_rule_violation", "Team is required for de-identification team assignment management", {"field": "team_id"})
    return _resolve_team(db, team_id=team_id)


def _resolve_selection_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if actor.is_system_admin:
        if team_id is None:
            raise AppError(422, "business_rule_violation", "Team is required for de-identification selection management", {"field": "team_id"})
        return _resolve_team(db, team_id=team_id)
    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "De-identification selection access required")
    if team_id is not None and team_id != actor.team_id:
        raise AppError(403, "forbidden", "Leaders may only manage de-identification selection for their own team")
    return _resolve_team(db, team_id=actor.team_id)


def ensure_builtin_deidentification_provider(db: Session) -> DeidentificationProvider:
    provider = db.get(DeidentificationProvider, BUILTIN_DEIDENTIFICATION_PROVIDER_ID)
    if provider is not None:
        return provider
    provider = DeidentificationProvider(
        id=BUILTIN_DEIDENTIFICATION_PROVIDER_ID,
        label="Built-in Native Presidio",
        adapter_kind=DeidentificationAdapterKind.native_presidio,
        base_url="",
        detect_path="",
        auth_mode=DeidentificationAuthMode.none,
        request_text_field="text",
        request_language_field=None,
        extra_headers_json={},
        extra_body_json={},
        response_entities_path="entities",
        response_start_field="start",
        response_end_field="end",
        response_type_field="entity_type",
        response_score_field=None,
        response_model_version_path=None,
        entity_type_map_json={},
        vault_secret_ref="",
        is_active=True,
        is_builtin=True,
        created_by_user_id=None,
        updated_by_user_id=None,
    )
    db.add(provider)
    db.flush()
    return provider


def list_deidentification_providers(db: Session, actor: User) -> list[DeidentificationProvider]:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin de-identification provisioning access required")
    ensure_builtin_deidentification_provider(db)
    stmt = select(DeidentificationProvider).order_by(DeidentificationProvider.is_builtin.desc(), DeidentificationProvider.created_at.desc(), DeidentificationProvider.id.desc())
    return list(db.scalars(stmt))


def get_deidentification_provider(db: Session, actor: User, *, provider_id: UUID) -> DeidentificationProvider:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin de-identification provisioning access required")
    ensure_builtin_deidentification_provider(db)
    provider = db.get(DeidentificationProvider, provider_id)
    if provider is None:
        raise AppError(404, "not_found", "De-identification provider not found", {"resource": "deidentification_provider", "provider_id": str(provider_id)})
    return provider


def upsert_deidentification_provider(db: Session, actor: User, payload: DeidentificationProviderUpsert) -> DeidentificationProvider:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin de-identification provisioning access required")
    ensure_builtin_deidentification_provider(db)
    provider = None
    if payload.provider_id is not None:
        provider = db.get(DeidentificationProvider, payload.provider_id)
        if provider is None:
            raise AppError(404, "not_found", "De-identification provider not found", {"resource": "deidentification_provider", "provider_id": str(payload.provider_id)})
        if provider.is_builtin:
            raise AppError(409, "conflict", "Built-in de-identification provider cannot be edited")

    existing_vault_secret_ref = provider.vault_secret_ref if provider is not None else ""
    resolved_auth_mode = payload.auth_mode
    if payload.adapter_kind is DeidentificationAdapterKind.native_presidio:
        resolved_auth_mode = DeidentificationAuthMode.none

    if resolved_auth_mode is DeidentificationAuthMode.bearer and not payload.bearer_token and not existing_vault_secret_ref:
        raise AppError(
            422,
            "business_rule_violation",
            "Bearer token is required when configuring bearer-auth de-identification provider",
            {"field": "bearer_token"},
        )

    pending_secret_ref = ""
    old_secret_ref = existing_vault_secret_ref

    if provider is None:
        provider = DeidentificationProvider(
            id=uuid4(),
            label=payload.label.strip(),
            adapter_kind=payload.adapter_kind,
            base_url=payload.base_url,
            detect_path=payload.detect_path,
            auth_mode=resolved_auth_mode,
            request_text_field=payload.request_text_field.strip(),
            request_language_field=payload.request_language_field,
            extra_headers_json=payload.extra_headers_json,
            extra_body_json=payload.extra_body_json,
            response_entities_path=payload.response_entities_path.strip(),
            response_start_field=payload.response_start_field.strip(),
            response_end_field=payload.response_end_field.strip(),
            response_type_field=payload.response_type_field.strip(),
            response_score_field=payload.response_score_field,
            response_model_version_path=payload.response_model_version_path,
            entity_type_map_json=payload.entity_type_map_json,
            vault_secret_ref="",
            is_active=payload.is_active,
            is_builtin=False,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(provider)
        db.flush()
    else:
        provider.label = payload.label.strip()
        provider.adapter_kind = payload.adapter_kind
        provider.base_url = payload.base_url
        provider.detect_path = payload.detect_path
        provider.auth_mode = resolved_auth_mode
        provider.request_text_field = payload.request_text_field.strip()
        provider.request_language_field = payload.request_language_field
        provider.extra_headers_json = payload.extra_headers_json
        provider.extra_body_json = payload.extra_body_json
        provider.response_entities_path = payload.response_entities_path.strip()
        provider.response_start_field = payload.response_start_field.strip()
        provider.response_end_field = payload.response_end_field.strip()
        provider.response_type_field = payload.response_type_field.strip()
        provider.response_score_field = payload.response_score_field
        provider.response_model_version_path = payload.response_model_version_path
        provider.entity_type_map_json = payload.entity_type_map_json
        provider.is_active = payload.is_active
        provider.updated_by_user_id = actor.id
        db.add(provider)

    if provider.auth_mode is DeidentificationAuthMode.bearer and payload.bearer_token:
        pending_secret_ref = write_deidentification_bearer_token(
            provider_id=provider.id,
            bearer_token=payload.bearer_token,
            secret_id=uuid4(),
        )
        provider.vault_secret_ref = pending_secret_ref
    elif provider.auth_mode is DeidentificationAuthMode.none:
        provider.vault_secret_ref = ""

    try:
        db.commit()
    except Exception:
        db.rollback()
        if pending_secret_ref:
            _delete_deidentification_secret_best_effort(
                provider_id=provider.id,
                secret_ref=pending_secret_ref,
                event="deidentification_pending_secret_delete_failed",
            )
        raise
    if old_secret_ref and old_secret_ref != provider.vault_secret_ref:
        _delete_deidentification_secret_best_effort(
            provider_id=provider.id,
            secret_ref=old_secret_ref,
            event="deidentification_old_secret_delete_failed",
        )
    db.refresh(provider)
    return provider


def delete_deidentification_provider(db: Session, actor: User, *, provider_id: UUID) -> None:
    provider = get_deidentification_provider(db, actor, provider_id=provider_id)
    if provider.is_builtin:
        raise AppError(409, "conflict", "Built-in de-identification provider cannot be deleted")
    old_secret_ref = provider.vault_secret_ref
    selections = list(db.scalars(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.provider_id == provider.id)))
    for selection in selections:
        db.delete(selection)
    assignments = list(db.scalars(select(TeamDeidentificationProviderAssignment).where(TeamDeidentificationProviderAssignment.provider_id == provider.id)))
    for assignment in assignments:
        db.delete(assignment)
    if selections or assignments:
        db.flush()
    db.delete(provider)
    db.commit()
    if old_secret_ref:
        _delete_deidentification_secret_best_effort(
            provider_id=provider_id,
            secret_ref=old_secret_ref,
            event="deidentification_deleted_provider_secret_delete_failed",
        )


def list_team_deidentification_provider_assignments(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamDeidentificationProviderAssignment]:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    stmt = (
        select(TeamDeidentificationProviderAssignment)
        .options(joinedload(TeamDeidentificationProviderAssignment.provider))
        .where(TeamDeidentificationProviderAssignment.team_id == team.id)
        .order_by(TeamDeidentificationProviderAssignment.created_at.desc(), TeamDeidentificationProviderAssignment.id.desc())
    )
    return list(db.scalars(stmt))


def assign_deidentification_provider_to_team(
    db: Session,
    actor: User,
    payload: DeidentificationProviderAssignmentUpsert,
) -> TeamDeidentificationProviderAssignment:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    provider = get_deidentification_provider(db, actor, provider_id=payload.provider_id)
    if provider.is_builtin:
        raise AppError(409, "conflict", "Built-in de-identification provider is available to all teams and does not need assignment")
    assignment = db.scalar(
        select(TeamDeidentificationProviderAssignment).where(
            TeamDeidentificationProviderAssignment.team_id == team.id,
            TeamDeidentificationProviderAssignment.provider_id == provider.id,
        )
    )
    if assignment is not None:
        return assignment
    assignment = TeamDeidentificationProviderAssignment(
        id=uuid4(),
        team_id=team.id,
        provider_id=provider.id,
        assigned_by_user_id=actor.id,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return db.scalar(
        select(TeamDeidentificationProviderAssignment)
        .options(joinedload(TeamDeidentificationProviderAssignment.provider))
        .where(TeamDeidentificationProviderAssignment.id == assignment.id)
    ) or assignment


def remove_deidentification_provider_assignment(db: Session, actor: User, *, team_id: UUID, provider_id: UUID) -> None:
    _resolve_admin_scoped_team(db, actor, team_id=team_id)
    assignment = db.scalar(
        select(TeamDeidentificationProviderAssignment).where(
            TeamDeidentificationProviderAssignment.team_id == team_id,
            TeamDeidentificationProviderAssignment.provider_id == provider_id,
        )
    )
    if assignment is None:
        raise AppError(404, "not_found", "De-identification provider assignment not found", {"team_id": str(team_id), "provider_id": str(provider_id)})
    selection = db.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team_id, TeamDeidentificationSelection.provider_id == provider_id))
    if selection is not None:
        db.delete(selection)
        db.flush()
    db.delete(assignment)
    db.commit()


def list_selectable_deidentification_providers(db: Session, actor: User, *, team_id: UUID | None = None) -> list[DeidentificationProvider]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    builtin = ensure_builtin_deidentification_provider(db)
    assigned_stmt = (
        select(DeidentificationProvider)
        .join(TeamDeidentificationProviderAssignment, TeamDeidentificationProviderAssignment.provider_id == DeidentificationProvider.id)
        .where(
            TeamDeidentificationProviderAssignment.team_id == team.id,
            DeidentificationProvider.is_active.is_(True),
        )
        .order_by(DeidentificationProvider.created_at.desc(), DeidentificationProvider.id.desc())
    )
    providers = []
    if builtin.is_active:
        providers.append(builtin)
    providers.extend(list(db.scalars(assigned_stmt)))
    return providers


def get_team_deidentification_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> TeamDeidentificationSelection | None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    return db.scalar(
        select(TeamDeidentificationSelection)
        .options(joinedload(TeamDeidentificationSelection.provider))
        .where(TeamDeidentificationSelection.team_id == team.id)
    )


def set_team_deidentification_selection(db: Session, actor: User, payload: DeidentificationSelectionUpsert) -> TeamDeidentificationSelection:
    team = _resolve_selection_scoped_team(db, actor, team_id=payload.team_id)
    selectable_ids = {provider.id for provider in list_selectable_deidentification_providers(db, actor, team_id=team.id)}
    if payload.provider_id not in selectable_ids:
        raise AppError(404, "not_found", "Selectable de-identification provider not found", {"resource": "deidentification_provider", "provider_id": str(payload.provider_id)})
    selection = db.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id))
    if selection is None:
        selection = TeamDeidentificationSelection(
            id=uuid4(),
            team_id=team.id,
            provider_id=payload.provider_id,
            selected_by_user_id=actor.id,
        )
        db.add(selection)
    else:
        selection.provider_id = payload.provider_id
        selection.selected_by_user_id = actor.id
        db.add(selection)
    db.commit()
    db.refresh(selection)
    return db.scalar(
        select(TeamDeidentificationSelection)
        .options(joinedload(TeamDeidentificationSelection.provider))
        .where(TeamDeidentificationSelection.id == selection.id)
    ) or selection


def clear_team_deidentification_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    selection = db.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id))
    if selection is None:
        raise AppError(404, "not_found", "De-identification selection not found", {"resource": "deidentification_selection", "team_id": str(team.id)})
    db.delete(selection)
    db.commit()


def active_team_deidentification_provider(db: Session, *, team_id: UUID) -> DeidentificationProvider:
    builtin = ensure_builtin_deidentification_provider(db)
    selection = db.scalar(
        select(TeamDeidentificationSelection)
        .options(joinedload(TeamDeidentificationSelection.provider))
        .where(TeamDeidentificationSelection.team_id == team_id)
    )
    if selection is not None and selection.provider is not None and selection.provider.is_active:
        if selection.provider.is_builtin:
            return builtin
        assignment = db.scalar(
            select(TeamDeidentificationProviderAssignment.id).where(
                TeamDeidentificationProviderAssignment.team_id == team_id,
                TeamDeidentificationProviderAssignment.provider_id == selection.provider.id,
            )
        )
        if assignment is not None:
            return selection.provider
    if builtin.is_active:
        return builtin
    raise AppError(422, "business_rule_violation", "No active de-identification provider for team", {"team_id": str(team_id)})


def read_deidentification_provider_bearer_token(db: Session, *, provider_id: UUID) -> str:
    provider = db.get(DeidentificationProvider, provider_id)
    if provider is None:
        raise AppError(404, "not_found", "De-identification provider not found", {"provider_id": str(provider_id)})
    if not provider.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "No stored bearer token is configured for this de-identification provider", {"provider_id": str(provider_id)})
    return read_deidentification_bearer_token(provider_id=provider.id, secret_ref=provider.vault_secret_ref)
