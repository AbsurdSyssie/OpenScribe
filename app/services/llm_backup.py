"""Owner-encrypted backup choices and database-backed one-shot failover claims.

Source services own their lifecycle locks, redaction, credentials, submission and
terminal outcome. These helpers neither commit nor dispatch provider requests.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    AttemptStatus, ConsultationSplitAnalysis, ConsultationSplitBatch,
    ConsultationSplitExecution, GeneratedDocument, LlmConfigSetupStatus,
    ProviderAttempt, QuotaResource, TeamLlmBackupSelection, TeamLlmConfig,
    TemplateSuggestionJob, Transcript, User, utcnow,
)
from app.services.content_crypto import (
    decrypt_json_for_owner, encrypt_json_for_existing_owner, is_encrypted_envelope,
)
from app.services.llm_adapters.runtime import build_provider_snapshot, validate_provider_snapshot_shape
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.quotas import reserve_provider_attempt

_SOURCES = (GeneratedDocument, TemplateSuggestionJob, ConsultationSplitAnalysis,
            ConsultationSplitBatch, ConsultationSplitExecution)
_FIELD = "backup_provider_snapshot_encrypted"


def _source(source: object) -> tuple[str, UUID, UUID, UUID]:
    if not isinstance(source, _SOURCES) or source.id is None:
        raise AppError(500, "llm_backup_source_invalid", "Backup source is invalid")
    return source.__tablename__, source.id, source.owner_user_id, source.team_id


def freeze_backup_selection(
    db: Session, *, source: object, owner_user_id: UUID, team_id: UUID,
) -> None:
    """Freeze the admin choice at source creation, in its existing transaction."""
    table, record_id, owner_id, source_team = _source(source)
    if owner_id != owner_user_id or source_team != team_id:
        raise AppError(403, "llm_backup_scope_mismatch", "Backup source scope is invalid")
    if source.backup_llm_config_id is not None or source.backup_provider_snapshot_encrypted is not None:
        raise AppError(409, "llm_backup_snapshot_already_frozen", "Backup selection is already frozen")
    choice = db.execute(select(TeamLlmBackupSelection.llm_config_id, TeamLlmBackupSelection.model_name)
        .where(TeamLlmBackupSelection.team_id == team_id)).first()
    if choice is None or choice.llm_config_id is None:
        return
    # Config edits lock config before selection. Keep the same order, freezing
    # the choice observed above even if a concurrent admin changes selection.
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == choice.llm_config_id)
        .execution_options(populate_existing=True).with_for_update())
    db.scalar(select(TeamLlmBackupSelection).where(TeamLlmBackupSelection.team_id == team_id)
        .execution_options(populate_existing=True).with_for_update())
    if (config is None or config.team_id != team_id or config.revision_of_config_id is not None
        or not config.is_active or config.setup_status != LlmConfigSetupStatus.ready
        or not choice.model_name or choice.model_name not in (config.available_models_json or [])):
        return
    snapshot = build_provider_snapshot(config=config, model=choice.model_name).to_dict()
    source.backup_provider_snapshot_encrypted = encrypt_json_for_existing_owner(
        db, owner_user_id=owner_id, table=table, field=_FIELD, record_id=record_id,
        plaintext=snapshot,
    )
    source.backup_llm_config_id = config.id



def read_backup_provider(db: Session, *, source: object) -> LlmProviderSnapshot | None:
    """Read a frozen choice. Runtime config/credential validation stays with caller."""
    table, record_id, owner_id, _ = _source(source)
    stored = source.backup_provider_snapshot_encrypted
    if stored is None:
        if source.backup_llm_config_id is not None:
            raise AppError(500, "llm_backup_snapshot_invalid", "Backup snapshot is unavailable")
        return None
    if not is_encrypted_envelope(stored):
        raise AppError(500, "llm_backup_snapshot_invalid", "Backup snapshot is unavailable")
    snapshot = decrypt_json_for_owner(db, owner_user_id=owner_id, table=table,
        field=_FIELD, record_id=record_id, stored_value=stored)
    validated = validate_provider_snapshot_shape(snapshot)
    if source.backup_llm_config_id is None or str(source.backup_llm_config_id) != validated["llm_config_id"]:
        raise AppError(500, "llm_backup_snapshot_invalid", "Backup snapshot is unavailable")
    return LlmProviderSnapshot(**validated)


def copy_backup_selection(db: Session, *, source: object, target: object) -> None:
    """Carry a queued parent's choice into a child without consulting admin state."""
    _, _, owner_id, team_id = _source(source)
    table, record_id, target_owner, target_team = _source(target)
    if owner_id != target_owner or team_id != target_team:
        raise AppError(403, "llm_backup_scope_mismatch", "Backup source scope is invalid")
    if target.backup_llm_config_id is not None or target.backup_provider_snapshot_encrypted is not None:
        raise AppError(409, "llm_backup_snapshot_already_frozen", "Backup selection is already frozen")
    provider = read_backup_provider(db, source=source)
    if provider is None:
        return
    target.backup_llm_config_id = source.backup_llm_config_id
    target.backup_provider_snapshot_encrypted = encrypt_json_for_existing_owner(
        db, owner_user_id=owner_id, table=table, field=_FIELD,
        record_id=record_id, plaintext=provider.to_dict(),
    )


def effective_provider_attempt(db: Session, *, primary: ProviderAttempt, lock: bool = False) -> ProviderAttempt:
    """Select one logical call's child; source locks must already be held."""
    primary_id = primary.backup_of_attempt_id or primary.id
    if lock:
        primary = db.scalar(select(ProviderAttempt).where(ProviderAttempt.id == primary_id)
            .execution_options(populate_existing=True).with_for_update())
        if primary is None:
            raise AppError(409, "llm_attempt_inactive", "The generation attempt is no longer active")
    statement = select(ProviderAttempt).where(ProviderAttempt.backup_of_attempt_id == primary_id)
    if lock:
        statement = statement.execution_options(populate_existing=True).with_for_update()
    return db.scalar(statement) or primary


def provider_result_is_current(
    db: Session, *, primary_id: UUID, submitted_attempt_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Fence a result to the exact dispatch; callers first lock source and root."""
    primary = db.get(ProviderAttempt, primary_id)
    if primary is None or primary.backup_of_attempt_id is not None:
        return False
    active = effective_provider_attempt(db, primary=primary, lock=True)
    current = now or utcnow()
    return (active.id == submitted_attempt_id and active.status is AttemptStatus.submitted
            and active.deadline_at is not None and active.deadline_at > current)


def reserve_backup_attempt(
    db: Session, *, primary: ProviderAttempt, provider: LlmProviderSnapshot,
    reserved_units: int, now: datetime | None = None, reservation_seconds: int = 300,
) -> ProviderAttempt:
    """Reserve one child after primary settlement, while source locks are held.

    Keep primary settlement outside any savepoint used by the caller to catch
    quota exhaustion. This helper does not roll back the surrounding transaction.
    Resolve the backup credential before marking this reservation submitted.
    """
    if primary.backup_of_attempt_id is not None or primary.resource is not QuotaResource.tokens:
        raise AppError(409, "provider_attempt_backup_identity_invalid", "Backup attempt identity conflicts")
    owner = db.get(User, primary.owner_user_id) if primary.owner_user_id else None
    root = db.get(Transcript, primary.transcript_id) if primary.transcript_id else None
    moment = now or utcnow()
    if (owner is None or owner.is_system_admin or root is None
        or owner.team_id != primary.team_id or root.team_id != primary.team_id
        or root.owner_user_id != owner.id or root.retention_expires_at <= moment):
        raise AppError(409, "llm_backup_source_inactive", "Backup source is unavailable")
    config = db.get(TeamLlmConfig, UUID(provider.llm_config_id))
    if config is None or config.team_id != primary.team_id:
        raise AppError(403, "llm_backup_scope_mismatch", "Backup provider scope is invalid")
    existing = db.scalar(select(ProviderAttempt).where(ProviderAttempt.backup_of_attempt_id == primary.id).with_for_update())
    if existing is not None:
        if existing.provider_model != provider.model or existing.provider_adapter != provider.adapter_kind:
            raise AppError(409, "provider_attempt_backup_identity_invalid", "Backup attempt identity conflicts")
        return existing
    return reserve_provider_attempt(
        db, team_id=primary.team_id, owner_user_id=primary.owner_user_id,
        resource=QuotaResource.tokens, attempt_kind=primary.attempt_kind,
        correlation_id=primary.id, attempt_number=1, backup_of_attempt_id=primary.id,
        reserved_units=reserved_units, authorized_at=moment,
        reservation_valid_until=moment + timedelta(seconds=reservation_seconds),
        transcript_id=primary.transcript_id,
        generated_document_id=primary.generated_document_id,
        consultation_split_execution_id=primary.consultation_split_execution_id,
        transcript_ingestion_job_id=primary.transcript_ingestion_job_id,
        provider_adapter=provider.adapter_kind, provider_model=provider.model,
    )


# Transport/output classification and adapter reconstruction have no DB concerns.
from app.services.llm_backup_policy import (  # noqa: E402,F401
    backup_is_distinct,
    can_failover as is_backup_eligible_provider_error,
    prepare_backup_request,
)
