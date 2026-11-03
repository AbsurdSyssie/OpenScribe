"""Owner-encrypted acceptance-time snapshots for the optional LLM checker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    GeneratedDocument,
    GeneratedDocumentStatus,
    LlmConfigSetupStatus,
    TeamHallucinationCheckSelection,
    TeamLlmConfig,
)
from app.services.content_crypto import (
    decrypt_json_for_owner,
    encrypt_json_for_owner,
    is_encrypted_envelope,
)
from app.services.llm_adapters.runtime import (
    build_provider_snapshot,
    validate_provider_snapshot_for_config,
    validate_provider_snapshot_shape,
)

_FIELD = "hallucination_check_selection_snapshot_encrypted"
_STATUSES = frozenset({"not_configured", "invalid", "configured"})


def retire_checker_snapshots_for_config(db: Session, *, config_id: UUID) -> None:
    """Retire terminal encrypted checker snapshots before deleting a config.

    In-flight work is protected by the config delete guard. Terminal rows may
    remain under retention, so clear both the ciphertext and paired metadata to
    prevent later recovery from resurrecting a deleted provider.
    """
    db.execute(update(GeneratedDocument).where(
        GeneratedDocument.hallucination_check_selection_config_id == config_id,
        GeneratedDocument.status.notin_([GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing]),
    ).values(
        hallucination_check_selection_config_id=None,
        hallucination_check_selection_snapshot_encrypted=None,
    ).execution_options(synchronize_session="fetch"))
    db.execute(update(ConsultationSplitBatch).where(
        ConsultationSplitBatch.hallucination_check_selection_config_id == config_id,
        ConsultationSplitBatch.status.notin_([
            ConsultationSplitBatchStatus.generation_queued,
            ConsultationSplitBatchStatus.generating,
            ConsultationSplitBatchStatus.verifying,
        ]),
    ).values(
        hallucination_check_selection_config_id=None,
        hallucination_check_selection_snapshot_encrypted=None,
    ).execution_options(synchronize_session="fetch"))


@dataclass(frozen=True, slots=True)
class FrozenCheckerSelection:
    status: str
    config: TeamLlmConfig | None = None
    model_name: str | None = None
    provider_snapshot: dict[str, Any] | None = None


def _source_table(source: object) -> str:
    table = getattr(source, "__tablename__", None)
    if table not in {"generated_documents", "consultation_split_batches"}:
        raise AppError(500, "llm_checker_snapshot_source_invalid", "Checker selection source is invalid")
    return table


def _provider_model_allowed(db: Session, *, config: TeamLlmConfig, model_name: str) -> bool:
    from app.services.llm_model_policy import model_allowed_for_provider
    return model_allowed_for_provider(db, config=config, model_name=model_name)


def freeze_hallucination_check_selection(
    db: Session, *, source: object, owner_user_id: UUID, applicable: bool = True,
) -> FrozenCheckerSelection:
    """Freeze the checker status/model, or an explicit nonapplicable state."""
    table = _source_table(source)
    if getattr(source, "id", None) is None or getattr(source, "owner_user_id", None) != owner_user_id:
        raise AppError(500, "llm_checker_snapshot_source_invalid", "Checker selection source is invalid")
    field_value = getattr(source, _FIELD, None)
    if field_value is not None:
        raise AppError(409, "llm_checker_snapshot_already_frozen", "Checker selection is already frozen")
    if not applicable:
        snapshot: dict[str, Any] = {"version": 1, "status": "not_configured"}
        setattr(source, _FIELD, encrypt_json_for_owner(
            db, owner_user_id=owner_user_id, table=table, field=_FIELD,
            record_id=source.id, plaintext=snapshot,
        ))
        return FrozenCheckerSelection("not_configured")
    selection_config_id = db.scalar(select(TeamHallucinationCheckSelection.llm_config_id)
        .where(TeamHallucinationCheckSelection.team_id == source.team_id))
    config = None
    if selection_config_id is not None:
        config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == selection_config_id)
            .execution_options(populate_existing=True).with_for_update())
    selection = db.scalar(select(TeamHallucinationCheckSelection)
        .where(TeamHallucinationCheckSelection.team_id == source.team_id)
        .execution_options(populate_existing=True).with_for_update())
    if selection is not None and selection.llm_config_id != selection_config_id:
        # The administrator changed the selection while the queue boundary was
        # acquiring its ordered locks. Do not accept a mixed snapshot.
        raise AppError(409, "llm_checker_selection_changed", "Checker selection changed; retry generation")
    if selection is None:
        snapshot: dict[str, Any] = {"version": 1, "status": "not_configured"}
        result = FrozenCheckerSelection("not_configured")
    else:
        model_name = (selection.model_name_override or (config.model_name if config else None) or "").strip()
        if hasattr(source, "hallucination_check_selection_config_id") and config is not None:
            source.hallucination_check_selection_config_id = config.id
        if (
            config is None
            or config.team_id != source.team_id
            or config.revision_of_config_id is not None
            or not config.is_active
            or config.setup_status != LlmConfigSetupStatus.ready
            or not model_name
            or not _provider_model_allowed(db, config=config, model_name=model_name)
        ):
            snapshot = {"version": 1, "status": "invalid", "reason": "checker_unavailable"}
            result = FrozenCheckerSelection("invalid")
        else:
            provider = build_provider_snapshot(config=config, model=model_name).to_dict()
            snapshot = {"version": 1, "status": "configured", "provider_snapshot": provider}
            result = FrozenCheckerSelection("configured", config, model_name, provider)
    setattr(source, _FIELD, encrypt_json_for_owner(
        db, owner_user_id=owner_user_id, table=table, field=_FIELD,
        record_id=source.id, plaintext=snapshot,
    ))
    return result


def read_hallucination_check_selection(
    db: Session, *, source: object, owner_user_id: UUID | None = None,
) -> FrozenCheckerSelection | None:
    """Read a frozen choice; ``None`` identifies legacy work predating snapshots."""
    table = _source_table(source)
    owner_id = owner_user_id or getattr(source, "owner_user_id", None)
    record_id = getattr(source, "id", None)
    stored = getattr(source, _FIELD, None)
    if (
        not isinstance(owner_id, UUID) or not isinstance(record_id, UUID)
        or owner_id != getattr(source, "owner_user_id", None)
    ):
        raise AppError(500, "llm_checker_snapshot_source_invalid", "Checker selection source is invalid")
    if stored is None:
        if getattr(source, "hallucination_check_selection_config_id", None) is not None:
            raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
        return None
    if not is_encrypted_envelope(stored):
        raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
    payload = decrypt_json_for_owner(
        db, owner_user_id=owner_id, table=table, field=_FIELD,
        record_id=record_id, stored_value=stored,
    )
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("status") not in _STATUSES:
        raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
    status = payload["status"]
    if status != "configured":
        if set(payload) - {"version", "status", "reason"}:
            raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
        if status == "not_configured" and getattr(source, "hallucination_check_selection_config_id", None) is not None:
            raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
        return FrozenCheckerSelection(status)
    try:
        validated = validate_provider_snapshot_shape(payload.get("provider_snapshot"))
    except AppError as exc:
        raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable") from exc
    config = db.get(TeamLlmConfig, UUID(validated["llm_config_id"]))
    if config is None or config.team_id != source.team_id:
        raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
    metadata_config_id = getattr(source, "hallucination_check_selection_config_id", None)
    if metadata_config_id is None or str(metadata_config_id) != validated["llm_config_id"]:
        raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable")
    try:
        validated = validate_provider_snapshot_for_config(
            validated, config=config, expected_model=validated["model"],
        )
    except AppError as exc:
        raise AppError(500, "llm_checker_snapshot_invalid", "Checker selection snapshot is unavailable") from exc
    return FrozenCheckerSelection("configured", config, validated["model"], validated)


def copy_hallucination_check_selection(
    db: Session, *, source: object, target: object,
) -> None:
    """Copy the accepted checker choice to a same-owner/team child record."""
    source_table = _source_table(source)
    target_table = _source_table(target)
    if (
        getattr(source, "owner_user_id", None) != getattr(target, "owner_user_id", None)
        or getattr(source, "team_id", None) != getattr(target, "team_id", None)
    ):
        raise AppError(403, "llm_checker_snapshot_scope_mismatch", "Checker selection scope is invalid")
    if getattr(target, _FIELD, None) is not None:
        raise AppError(409, "llm_checker_snapshot_already_frozen", "Checker selection is already frozen")
    frozen = read_hallucination_check_selection(db, source=source)
    if frozen is None:
        return
    payload: dict[str, Any] = {"version": 1, "status": frozen.status}
    if frozen.provider_snapshot is not None:
        payload["provider_snapshot"] = frozen.provider_snapshot
    elif frozen.status == "invalid":
        payload["reason"] = "checker_unavailable"
    setattr(target, _FIELD, encrypt_json_for_owner(
        db, owner_user_id=target.owner_user_id, table=target_table,
        field=_FIELD, record_id=target.id, plaintext=payload,
    ))
    if hasattr(target, "hallucination_check_selection_config_id"):
        target.hallucination_check_selection_config_id = getattr(
            source, "hallucination_check_selection_config_id", None,
        )
