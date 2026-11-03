"""Team-scoped model allowances for saved LLM provider roots."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitVerificationStatus,
    GeneratedDocument,
    GeneratedDocumentStatus,
    TeamHallucinationCheckSelection,
    TeamLlmBackupSelection,
    TeamLlmConfig,
    TeamLlmProviderModelPolicy,
    TeamLlmSelection,
    TemplateMode,
)


ROLE_WARNING_REASONS = frozenset({
    "provider_unavailable", "no_enabled_models", "default_unavailable",
    "model_disabled", "model_unavailable",
})


def get_provider_model_policy(
    db: Session,
    *,
    config: TeamLlmConfig,
    lock: bool = False,
) -> TeamLlmProviderModelPolicy | None:
    """Return a root provider's persisted policy; never infer all models."""
    statement = select(TeamLlmProviderModelPolicy).where(
        TeamLlmProviderModelPolicy.team_id == config.team_id,
        TeamLlmProviderModelPolicy.llm_config_id == config.id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def model_allowed_for_provider(
    db: Session,
    *,
    config: TeamLlmConfig,
    model_name: str | None,
) -> bool:
    """Whether a model is both enabled by policy and currently in the catalog."""
    if not model_name or config.revision_of_config_id is not None:
        return False
    policy = get_provider_model_policy(db, config=config)
    if policy is None:
        return False
    return (
        model_name in _clean_model_ids(policy.enabled_models_json)
        and model_name in _clean_model_ids(config.available_models_json)
    )


def provider_model_policy_state(db: Session, *, config: TeamLlmConfig) -> dict[str, Any]:
    """Build safe policy state and role warnings for API/UI presentation."""
    policy = get_provider_model_policy(db, config=config)
    catalog = _clean_model_ids(config.available_models_json)
    enabled = _clean_model_ids(policy.enabled_models_json) if policy is not None else []
    default = policy.default_model_name if policy is not None else None
    invalid_enabled = [model for model in enabled if model not in catalog]
    default_valid = bool(default and default in enabled and default in catalog)
    warnings: list[dict[str, str | None]] = []

    def warn(role: str, model: str | None, reason: str) -> None:
        if reason in ROLE_WARNING_REASONS:
            warnings.append({"role": role, "reason": reason, "model_name": model})

    writing_config_id = db.scalar(select(TeamLlmSelection.llm_config_id).where(
        TeamLlmSelection.team_id == config.team_id,
    ))
    if writing_config_id == config.id:
        if not config.is_active or getattr(config.setup_status, "value", config.setup_status) != "ready" or config.revision_of_config_id is not None:
            warn("writing", default, "provider_unavailable")
        elif not enabled:
            warn("writing", None, "no_enabled_models")
        elif not default_valid:
            warn("writing", default, "default_unavailable")

    backup_config_id = db.scalar(select(TeamLlmBackupSelection.llm_config_id).where(
        TeamLlmBackupSelection.team_id == config.team_id,
    ))
    if backup_config_id == config.id:
        backup = db.scalar(select(TeamLlmBackupSelection.model_name).where(
            TeamLlmBackupSelection.team_id == config.team_id,
        ))
        _assignment_warning(warn, "backup", backup, enabled, catalog, config)

    checker_selection = db.scalar(select(TeamHallucinationCheckSelection).where(
        TeamHallucinationCheckSelection.team_id == config.team_id,
        TeamHallucinationCheckSelection.llm_config_id == config.id,
    ))
    if checker_selection is not None:
        checker = checker_selection.model_name_override
        _assignment_warning(warn, "checker", checker, enabled, catalog, config)

    role_warnings = warnings
    return {
        "team_id": config.team_id,
        "llm_config_id": config.id,
        "provider_available_models_json": catalog,
        "enabled_models_json": enabled,
        "default_model_name": default,
        "policy_valid": bool(policy is not None and (not enabled or (default_valid and not invalid_enabled))),
        "invalid_enabled_models_json": invalid_enabled,
        "default_model_valid": default_valid,
        "role_warnings": warnings,
    }


def _assignment_warning(warn, role: str, model: str | None, enabled: list[str], catalog: list[str], config: TeamLlmConfig) -> None:
    if not model:
        warn(role, model, "default_unavailable")
    elif (
        not config.is_active
        or getattr(config.setup_status, "value", config.setup_status) != "ready"
        or config.revision_of_config_id is not None
    ):
        warn(role, model, "provider_unavailable")
    elif model not in catalog:
        warn(role, model, "model_unavailable")
    elif model not in enabled:
        warn(role, model, "model_disabled")


def _clean_model_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def ensure_no_legacy_checker_work_for_policy_change(
    db: Session,
    *,
    team_id: UUID,
    config_id: UUID | None = None,
) -> None:
    """Fence migration-era accepted work whose checker assignment was not frozen.

    If ``config_id`` is supplied, only policy/catalog changes for the currently
    selected checker provider are blocked. Passing ``None`` fences changing or
    clearing the checker selection itself.
    """
    if config_id is not None:
        selected_config_id = db.scalar(select(TeamHallucinationCheckSelection.llm_config_id).where(
            TeamHallucinationCheckSelection.team_id == team_id,
        ).with_for_update())
        if selected_config_id != config_id:
            return

    document = db.scalar(select(GeneratedDocument.id).where(
        GeneratedDocument.team_id == team_id,
        GeneratedDocument.document_mode == TemplateMode.structured,
        GeneratedDocument.status.in_((GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing)),
        GeneratedDocument.hallucination_check_selection_snapshot_encrypted.is_(None),
    ).limit(1))
    if document is not None:
        _raise_legacy_checker_work_conflict()

    split_batch = db.scalar(select(ConsultationSplitBatch.id).where(
        ConsultationSplitBatch.team_id == team_id,
        ConsultationSplitBatch.status.in_((
            ConsultationSplitBatchStatus.generation_queued,
            ConsultationSplitBatchStatus.generating,
            ConsultationSplitBatchStatus.verifying,
            ConsultationSplitBatchStatus.partially_ready,
        )),
        ConsultationSplitBatch.verification_status.in_((
            ConsultationSplitVerificationStatus.pending,
            ConsultationSplitVerificationStatus.verifying,
        )),
        ConsultationSplitBatch.hallucination_check_selection_snapshot_encrypted.is_(None),
    ).limit(1))
    if split_batch is not None:
        _raise_legacy_checker_work_conflict()


def _raise_legacy_checker_work_conflict() -> None:
    raise AppError(
        409,
        "conflict",
        "Wait for previously queued checking work to finish before changing this checker setting.",
        {"resource": "legacy_checker_work_pending"},
    )
