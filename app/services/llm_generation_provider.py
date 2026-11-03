"""Resolve a newly accepted writing provider without changing the role pointer."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import LlmConfigSetupStatus, TeamLlmConfig, TeamLlmSelection, User, UserLlmPreference
from app.services.llm import resolve_effective_user_llm_for_new_work
from app.services.llm_backup import read_backup_provider
from app.services.llm_adapters.runtime import validate_provider_snapshot_for_config


@dataclass(frozen=True, slots=True)
class WritingProvider:
    config: TeamLlmConfig
    model_name: str
    selection: TeamLlmSelection | None
    preference: UserLlmPreference | None
    uses_backup: bool


def resolve_new_writing_provider(
    db: Session,
    actor: User,
    *,
    unavailable_code: str = "business_rule_violation",
    unavailable_message: str = "No active LLM model is configured for this user",
) -> WritingProvider:
    """Use the selected writing model or an allowed backup for new work.

    The saved writing-provider pointer is never mutated. A backup promoted to
    this request's primary attempt remains identifiable through its frozen
    backup snapshot and usual attempt/accounting metadata.
    """
    selection, config, model_name, preference, uses_backup = resolve_effective_user_llm_for_new_work(db, actor)
    if config is not None and isinstance(model_name, str) and model_name.strip():
        return WritingProvider(config, model_name.strip(), selection, preference, uses_backup)
    raise AppError(
        422,
        unavailable_code,
        unavailable_message,
        {"field": "preferred_model_name"},
    )


def require_frozen_backup_matches(
    db: Session, *, source: object, config_id: UUID, model_name: str,
) -> None:
    """Fail acceptance if the backup changed after fallback resolution."""
    provider = read_backup_provider(db, source=source)
    if provider is None or provider.llm_config_id != str(config_id) or provider.model != model_name:
        raise AppError(409, "llm_backup_selection_changed", "Backup selection changed; retry generation")


def resolve_frozen_split_retry_provider(db: Session, actor: User, *, batch: object) -> WritingProvider:
    """Resolve a retry from its accepted primary or its already frozen backup."""
    selection = None
    config = None
    model_name = None
    preference = None
    selection, config, model_name, preference, uses_backup = resolve_effective_user_llm_for_new_work(db, actor)
    if config is not None and isinstance(model_name, str) and model_name.strip() and not uses_backup:
        return WritingProvider(config, model_name.strip(), selection, preference, False)
    backup = read_backup_provider(db, source=batch)
    if backup is not None:
        config = db.get(TeamLlmConfig, UUID(backup.llm_config_id))
        if config is None or config.team_id != actor.team_id or not config.is_active or config.setup_status != LlmConfigSetupStatus.ready:
            raise AppError(422, "consultation_split_llm_model_unavailable", "No LLM model is available for consultation splitting")
        validate_provider_snapshot_for_config(backup.to_dict(), config=config, expected_model=backup.model)
        return WritingProvider(config, backup.model, selection, preference, True)
    raise AppError(422, "consultation_split_llm_model_unavailable", "No LLM model is available for consultation splitting")
