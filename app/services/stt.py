import io
import hashlib
import hmac
import json
import logging
import os
from datetime import timedelta
from math import ceil
from pathlib import Path
from time import monotonic, time
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from openai import APIStatusError, AuthenticationError, OpenAI, PermissionDeniedError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import AttemptKind, AttemptOutcome, ProviderCredentialStatus, ProviderSecretCleanupKind, QuotaResource, SttAdapterKind, SttAuthMode, SttConfigSetupStatus, SttProviderPreset, SttSelectionPurpose, Team, TeamRole, TeamSttConfig, TeamSttSelection, TranscriptIngestionJob, TranscriptIngestionJobStatus, User, utcnow
from app.schemas import (
    SttConfigDraftCreate,
    SttConfigDraftReplaceCredential,
    SttConfigFinalize,
    SttConfigUpsert,
    SttInspectFieldTip,
    SttInspectRequest,
    SttInspectResult,
    SttModelOption,
    SttSelectionUpsert,
)
from app.stt_normalization import normalize_optional_stt_text, normalize_stt_language
from app.services.stt_presets import (
    apply_stt_provider_defaults,
    default_stt_config_label,
    get_stt_provider_preset,
    is_deepgram_stt_base_url,
    resolve_stt_provider_preset,
)
from app.services.security_audit import record_security_event
from app.services.vault import delete_team_stt_bearer_token, read_team_stt_bearer_token, write_team_stt_bearer_token
from app.services.provider_secret_cleanup import queue_orphan_provider_secret_after_rollback, queue_provider_secret_cleanup
from app.services.quotas import mark_provider_attempt_submitted, reserve_provider_attempt, settle_provider_attempt_audio
from app.services.provider_errors import safe_provider_error_code
from app.provider_url_security import require_safe_provider_url
from app.services.audio import normalized_wav_duration_seconds
from app.services.provider_inspection import (
    ProviderResponseTooLargeError,
    dereference_openapi_document,
    display_default_from_schema_property,
    extract_json_path,
    fetch_openapi_document,
    operation_request_schema,
    operation_response_schema,
    read_limited_httpx_response,
)


SUPPORTED_OPENAI_TRANSCRIPTION_MODELS = (
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "gpt-4o-transcribe-diarize",
    "whisper-1",
)
ELEVENLABS_PREFERRED_STT_MODELS = (
    "scribe_v2",
    "scribe_v1",
)
ELEVENLABS_SYNC_STT_MODEL_IDS = set(ELEVENLABS_PREFERRED_STT_MODELS)
STT_ADAPTERS_REQUIRING_BEARER_AUTH = frozenset(
    {
        SttAdapterKind.openai_cloud,
        SttAdapterKind.elevenlabs_speech_to_text,
    }
)
DEEPGRAM_MIP_OPT_OUT_FIELD = "mip_opt_out"
DEEPGRAM_MIP_OPT_OUT_TRUE = "true"
DEEPGRAM_TRUE_VALUES = {"true", "1", "yes", "on"}
DEEPGRAM_FALSE_VALUES = {"false", "0", "no", "off"}


logger = logging.getLogger("openscribe.stt")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STT_SAMPLE_PATH = REPO_ROOT / "tests" / "MoreOrLess.wav"
STT_TRANSCRIPTION_TIMEOUT_SECONDS = float(os.getenv("STT_TRANSCRIPTION_TIMEOUT_SECONDS", str(4 * 60 * 60)))
STT_MODEL_DISCOVERY_MAX_RESPONSE_BYTES = 1024 * 1024
STT_PROVIDER_ERROR_RESPONSE_MAX_BYTES = 64 * 1024
STT_TRANSCRIPTION_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
SYNC_STT_RESERVATION_GRACE_SECONDS = 60
_STT_CREDENTIAL_UNRESOLVED = object()
_STT_PROVIDER_ERROR_CODE_UNREAD = object()
_STT_PROVIDER_ERROR_CODE_ATTRIBUTE = "_openscribe_stt_provider_error_code"


def _record_stt_audit(db: Session, *, action: str, actor: User, team_id: UUID, config_id: UUID | None = None, outcome: str = "success", **details: Any) -> None:
    payload: dict[str, Any] = {"category": "provider", "outcome": outcome, "provider_type": "stt"}
    if config_id is not None:
        payload.update({"object_type": "team_stt_config", "object_id": str(config_id)})
    payload.update(details)
    record_security_event(db, action=action, actor=actor, team_id=team_id, details=payload)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _resolve_team(db: Session, *, team_id: UUID) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(team_id)})
    return team


def _resolve_admin_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin STT provisioning access required")
    if team_id is None:
        raise AppError(422, "business_rule_violation", "Team is required for system-admin STT management", {"field": "team_id"})
    return _resolve_team(db, team_id=team_id)


def _provider_fingerprint_secret() -> bytes:
    material = os.getenv("PROVIDER_CREDENTIAL_FINGERPRINT_SECRET") or os.getenv("SECRET_KEY") or os.getenv("CSRF_SECRET") or "openscribe-dev-provider-fingerprint"
    return material.encode("utf-8")


def _credential_fingerprint(secret: str | None) -> str | None:
    if not secret:
        return None
    return hmac.new(_provider_fingerprint_secret(), secret.encode("utf-8"), hashlib.sha256).hexdigest()


def _status_metadata_from_inspection(inspection: SttInspectResult, *, status: ProviderCredentialStatus, warning: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "adapter_kind": inspection.adapter_kind.value,
        "candidate_paths": list(inspection.candidate_paths),
        "available_models_count": len(inspection.available_models),
        "notes": list(inspection.notes),
    }
    if warning:
        metadata["warning"] = warning
    metadata["status"] = status.value
    return metadata


def _inspection_status(inspection: SttInspectResult, *, had_secret: bool) -> ProviderCredentialStatus:
    if had_secret and any("failed" in note.lower() or "fallback" in note.lower() for note in inspection.notes):
        return ProviderCredentialStatus.partial
    return ProviderCredentialStatus.verified


def _is_credential_rejection(exc: AppError) -> bool:
    status_code = (exc.details or {}).get("status_code") if exc.details else None
    return exc.status_code in {401, 403} or status_code in {401, 403} or exc.code in {"unauthorized", "provider_credential_invalid", "stt_credential_invalid"}


def _duplicate_stt_config(
    db: Session,
    *,
    team_id: UUID,
    adapter_kind: SttAdapterKind,
    base_url: str,
    fingerprint: str | None,
    exclude_config_id: UUID | None = None,
) -> TeamSttConfig | None:
    if not fingerprint:
        return None
    stmt = select(TeamSttConfig).where(
        TeamSttConfig.team_id == team_id,
        TeamSttConfig.revision_of_config_id.is_(None),
        TeamSttConfig.adapter_kind == adapter_kind,
        TeamSttConfig.base_url == base_url,
        TeamSttConfig.credential_fingerprint == fingerprint,
    )
    if exclude_config_id is not None:
        stmt = stmt.where(TeamSttConfig.id != exclude_config_id)
    return db.scalar(stmt.limit(1))


def _ensure_unique_stt_config_label(db: Session, *, team_id: UUID, label: str, current_config_id: UUID | None = None) -> None:
    normalized = label.strip().lower()
    stmt = select(TeamSttConfig.id).where(
        TeamSttConfig.team_id == team_id,
        TeamSttConfig.revision_of_config_id.is_(None),
        func.lower(func.btrim(TeamSttConfig.label)) == normalized,
    )
    if current_config_id is not None:
        stmt = stmt.where(TeamSttConfig.id != current_config_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise AppError(409, "conflict", "An STT provider with this name already exists for this team.", {"field": "label"})


def _raise_stt_label_conflict_if_needed(exc: IntegrityError) -> None:
    if "uq_team_stt_configs_team_label_lower" in str(exc.orig):
        raise AppError(409, "conflict", "An STT provider with this name already exists for this team.", {"field": "label"}) from exc


def _normalize_deepgram_extra_query_params(
    extra_fields: dict[str, str] | None,
    *,
    provider_preset: str | SttProviderPreset | None,
    adapter_kind: SttAdapterKind,
    base_url: str,
    reject_explicit_non_true: bool,
) -> dict[str, str]:
    resolved_preset = resolve_stt_provider_preset(provider_preset, adapter_kind, base_url)
    fields = dict(extra_fields or {})
    if resolved_preset != SttProviderPreset.deepgram.value:
        return fields

    for key in list(fields.keys()):
        if key.strip().lower() != DEEPGRAM_MIP_OPT_OUT_FIELD:
            continue
        raw_value = fields.pop(key)
        normalized_value = str(raw_value).strip().lower()
        if reject_explicit_non_true and normalized_value not in DEEPGRAM_TRUE_VALUES:
            raise AppError(
                422,
                "business_rule_violation",
                "Deepgram STT requires mip_opt_out=true",
                {"field": "extra_form_fields_json.mip_opt_out"},
            )
        if not reject_explicit_non_true and normalized_value in DEEPGRAM_FALSE_VALUES:
            continue
    fields[DEEPGRAM_MIP_OPT_OUT_FIELD] = DEEPGRAM_MIP_OPT_OUT_TRUE
    return fields


def _stt_model_options(models: list[str], *, source: str) -> list[SttModelOption]:
    return [SttModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


def _status_metadata_from_preset_inspection(
    inspection: SttInspectResult,
    *,
    provider_preset: str,
    provider_display_name: str,
    status: ProviderCredentialStatus,
    warning: str | None = None,
) -> dict[str, Any]:
    metadata = _status_metadata_from_inspection(inspection, status=status, warning=warning)
    metadata["provider_preset"] = provider_preset
    metadata["provider_display_name"] = provider_display_name
    metadata["inspected_at"] = utcnow().isoformat()
    return metadata


def _apply_stt_inspection_to_config(config: TeamSttConfig, inspection: SttInspectResult) -> None:
    config.adapter_kind = inspection.adapter_kind
    config.base_url = inspection.base_url
    config.transcribe_path = inspection.transcribe_path
    config.model_name = inspection.model_name
    config.model_field_name = inspection.model_field_name or ("model" if inspection.model_name else None)
    config.available_models_json = list(inspection.available_models)
    config.file_field_name = inspection.file_field_name
    config.language = inspection.language
    config.language_field_name = inspection.language_field_name or ("language" if inspection.language else None)
    config.response_text_path = inspection.response_text_path
    config.segments_path = inspection.segments_path
    config.segment_text_field = inspection.segment_text_field
    config.segment_start_field = inspection.segment_start_field
    config.segment_end_field = inspection.segment_end_field
    config.segment_speaker_field = inspection.segment_speaker_field
    config.extra_form_fields_json = inspection.extra_form_fields_json


def _clear_stt_selections_for_config(db: Session, *, config_id: UUID) -> int:
    selections = list(db.scalars(select(TeamSttSelection).where(TeamSttSelection.stt_config_id == config_id)))
    for selection in selections:
        db.delete(selection)
    if selections:
        db.flush()
    return len(selections)


def _resolve_selection_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if actor.is_system_admin:
        if team_id is None:
            raise AppError(422, "business_rule_violation", "Team is required for STT selection management", {"field": "team_id"})
        return _resolve_team(db, team_id=team_id)

    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "STT selection access required")
    if team_id is not None and team_id != actor.team_id:
        raise AppError(403, "forbidden", "Leaders may only manage STT selection for their own team")
    return _resolve_team(db, team_id=actor.team_id)


def list_stt_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamSttConfig]:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    stmt = select(TeamSttConfig).where(TeamSttConfig.team_id == team.id, TeamSttConfig.revision_of_config_id.is_(None)).order_by(TeamSttConfig.created_at.desc(), TeamSttConfig.id.desc())
    return list(db.scalars(stmt))


def get_stt_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> TeamSttConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    config = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == config_id, TeamSttConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "STT config not found", {"resource": "stt_config", "config_id": str(config_id)})
    return config


def update_stt_config_details(
    db: Session,
    actor: User,
    *,
    config_id: UUID,
    team_id: UUID,
    label: str,
    is_active: bool,
) -> TeamSttConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    config = db.scalar(
        select(TeamSttConfig).where(
            TeamSttConfig.id == config_id,
            TeamSttConfig.team_id == team.id,
            TeamSttConfig.revision_of_config_id.is_(None),
            TeamSttConfig.setup_status == SttConfigSetupStatus.ready,
        )
    )
    if config is None:
        raise AppError(404, "not_found", "Ready STT config not found", {"resource": "stt_config", "config_id": str(config_id)})
    normalized_label = label.strip()
    if not normalized_label:
        raise AppError(422, "business_rule_violation", "STT provider name is required", {"field": "label"})
    _ensure_unique_stt_config_label(db, team_id=team.id, label=normalized_label, current_config_id=config.id)
    config.label = normalized_label
    config.is_active = is_active
    config.updated_by_user_id = actor.id
    db.add(config)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_stt_label_conflict_if_needed(exc)
        raise
    db.refresh(config)
    _record_stt_audit(db, action="stt_config_details_updated", actor=actor, team_id=team.id, config_id=config.id, active=config.is_active)
    return config


def _stt_config_has_in_flight_jobs(db: Session, *, config_id: UUID) -> bool:
    return db.scalar(
        select(TranscriptIngestionJob.id).where(
            TranscriptIngestionJob.stt_config_id == config_id,
            TranscriptIngestionJob.status.in_([TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing]),
        ).limit(1)
    ) is not None


def delete_stt_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> None:
    config = get_stt_config(db, actor, config_id=config_id, team_id=team_id)
    if _stt_config_has_in_flight_jobs(db, config_id=config.id):
        raise AppError(
            409,
            "conflict",
            "Cannot delete this STT config while transcription jobs are queued or processing",
            {"config_id": str(config.id)},
        )
    revisions = list(db.scalars(select(TeamSttConfig).where(TeamSttConfig.revision_of_config_id == config.id))) if config.revision_of_config_id is None else []
    revision_secrets = [item.vault_secret_ref for item in revisions if item.vault_secret_ref]
    secret_team_id = config.team_id
    secret_config_id = config.id
    secret_ref = config.vault_secret_ref
    _clear_stt_selections_for_config(db, config_id=config.id)
    queue_provider_secret_cleanup(
        db,
        kind=ProviderSecretCleanupKind.stt,
        secret_refs=[*revision_secrets, secret_ref],
    )
    db.delete(config)
    db.commit()
    _record_stt_audit(db, action="stt_config_deleted", actor=actor, team_id=secret_team_id, config_id=secret_config_id)


def cancel_stt_config_draft(db: Session, actor: User, *, config_id: UUID, team_id: UUID) -> None:
    config = get_stt_config(db, actor, config_id=config_id, team_id=team_id)
    if config.setup_status != SttConfigSetupStatus.pending_model_selection:
        raise AppError(409, "conflict", "Only a pending STT setup draft can be cancelled")
    delete_stt_config(db, actor, config_id=config_id, team_id=team_id)


def list_selectable_stt_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamSttConfig]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    stmt = (
        select(TeamSttConfig)
        .where(
            TeamSttConfig.team_id == team.id,
            TeamSttConfig.revision_of_config_id.is_(None),
            TeamSttConfig.is_active.is_(True),
            TeamSttConfig.setup_status == SttConfigSetupStatus.ready,
            TeamSttConfig.credential_status != ProviderCredentialStatus.invalid,
        )
        .order_by(TeamSttConfig.created_at.desc(), TeamSttConfig.id.desc())
    )
    return list(db.scalars(stmt))


def get_team_stt_selection(
    db: Session,
    actor: User,
    *,
    team_id: UUID | None = None,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
) -> TeamSttSelection | None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    return db.scalar(
        select(TeamSttSelection)
        .options(joinedload(TeamSttSelection.config))
        .where(TeamSttSelection.team_id == team.id, TeamSttSelection.purpose == purpose)
    )


def set_team_stt_selection(db: Session, actor: User, payload: SttSelectionUpsert) -> TeamSttSelection:
    team = _resolve_selection_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(
        select(TeamSttConfig).where(
            TeamSttConfig.id == payload.stt_config_id,
            TeamSttConfig.team_id == team.id,
            TeamSttConfig.revision_of_config_id.is_(None),
            TeamSttConfig.is_active.is_(True),
            TeamSttConfig.setup_status == SttConfigSetupStatus.ready,
            TeamSttConfig.credential_status != ProviderCredentialStatus.invalid,
        )
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable STT config not found", {"resource": "stt_config", "config_id": str(payload.stt_config_id)})
    if config.credential_status is ProviderCredentialStatus.invalid:
        raise AppError(422, "business_rule_violation", "Invalid STT provider credentials cannot be selected", {"field": "stt_config_id"})
    ensure_stt_config_credential_ready(team_id=team.id, config=config)

    provider_models = list(config.available_models_json or [])
    override = payload.model_name_override.strip() if payload.model_name_override else None
    if config.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text and override:
        _validated_elevenlabs_model(override, field="model_name_override")
    if override:
        if not provider_models:
            raise AppError(
                422,
                "business_rule_violation",
                "Selected STT provider does not currently expose selectable models",
                {"field": "model_name_override"},
            )
        if override not in provider_models:
            raise AppError(
                422,
                "business_rule_violation",
                "Selected STT model is not available for this provider",
                {"field": "model_name_override"},
            )
    selection = db.scalar(
        select(TeamSttSelection).where(
            TeamSttSelection.team_id == team.id,
            TeamSttSelection.purpose == payload.purpose,
        )
    )
    if selection is None:
        selection = TeamSttSelection(
            id=uuid4(),
            team_id=team.id,
            purpose=payload.purpose,
            stt_config_id=config.id,
            model_name_override=override,
            language_override=normalize_stt_language(payload.language_override),
            selected_by_user_id=actor.id,
        )
        db.add(selection)
    else:
        selection.purpose = payload.purpose
        selection.stt_config_id = config.id
        selection.model_name_override = override
        selection.language_override = normalize_stt_language(payload.language_override)
        selection.selected_by_user_id = actor.id
        db.add(selection)

    db.commit()
    db.refresh(selection)
    _record_stt_audit(db, action="stt_selection_set", actor=actor, team_id=team.id, config_id=config.id, purpose=payload.purpose.value)
    return db.scalar(
        select(TeamSttSelection)
        .options(joinedload(TeamSttSelection.config))
        .where(TeamSttSelection.id == selection.id)
    ) or selection


def clear_team_stt_selection(
    db: Session,
    actor: User,
    *,
    team_id: UUID | None = None,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
) -> None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    selection = db.scalar(
        select(TeamSttSelection).where(
            TeamSttSelection.team_id == team.id,
            TeamSttSelection.purpose == purpose,
        )
    )
    if selection is None:
        raise AppError(
            404,
            "not_found",
            "STT selection not found",
            {"resource": "stt_selection", "team_id": str(team.id), "purpose": purpose.value},
        )
    db.delete(selection)
    db.commit()
    _record_stt_audit(db, action="stt_selection_cleared", actor=actor, team_id=team.id, purpose=purpose.value)


def active_team_stt_selection(
    db: Session,
    *,
    team_id: UUID,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
) -> TeamSttSelection:
    selection = db.scalar(
        select(TeamSttSelection)
        .options(joinedload(TeamSttSelection.config))
        .where(TeamSttSelection.team_id == team_id, TeamSttSelection.purpose == purpose)
    )
    if selection is None or selection.config is None or not selection.config.is_active or selection.config.setup_status != SttConfigSetupStatus.ready:
        raise AppError(
            422,
            "business_rule_violation",
            "No active STT selection for team and purpose",
            {"team_id": str(team_id), "purpose": purpose.value},
        )
    return selection


def resolve_selected_team_stt(
    db: Session,
    *,
    team_id: UUID,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
) -> tuple[TeamSttSelection, TeamSttConfig, str | None, str | None]:
    selection = active_team_stt_selection(db, team_id=team_id, purpose=purpose)
    config = selection.config
    provider_models = list(config.available_models_json or [])
    resolved_model_name = normalize_optional_stt_text(selection.model_name_override) or normalize_optional_stt_text(config.model_name)
    if provider_models:
        if resolved_model_name not in provider_models:
            resolved_model_name = provider_models[0]
    if config.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        resolved_model_name = _validated_elevenlabs_model(resolved_model_name)
    resolved_language = normalize_stt_language(selection.language_override) or normalize_stt_language(config.language)
    return selection, config, resolved_model_name, resolved_language


def _missing_stt_credential_error(*, team_id: UUID, config_id: UUID) -> AppError:
    return AppError(
        409,
        "stt_config_secret_missing",
        "The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
        {"team_id": str(team_id), "config_id": str(config_id)},
    )


def _resolve_stt_provider_preset_for_admin_write(
    provider_preset: str | SttProviderPreset | None,
    adapter_kind: SttAdapterKind,
    base_url: str,
) -> str:
    if is_deepgram_stt_base_url(base_url):
        if adapter_kind is not SttAdapterKind.generic_rest:
            raise AppError(
                422,
                "business_rule_violation",
                "Deepgram STT must use the Deepgram generic REST contract",
                {"field": "adapter_kind"},
            )
        return SttProviderPreset.deepgram.value
    return resolve_stt_provider_preset(provider_preset, adapter_kind, base_url)


def _read_saved_stt_bearer_token(*, team_id: UUID, config: TeamSttConfig) -> str | None:
    if _stt_config_requires_saved_credential(config) and config.auth_mode is not SttAuthMode.bearer:
        raise _missing_stt_credential_error(team_id=team_id, config_id=config.id)
    if config.auth_mode is not SttAuthMode.bearer:
        return None
    if not config.vault_secret_ref:
        if _stt_config_requires_saved_credential(config):
            raise _missing_stt_credential_error(team_id=team_id, config_id=config.id)
        return None
    try:
        return read_team_stt_bearer_token(team_id=team_id, config_id=config.id, secret_ref=config.vault_secret_ref)
    except AppError as exc:
        if exc.code == "vault_read_failed":
            raise _missing_stt_credential_error(team_id=team_id, config_id=config.id) from exc
        raise


def _stt_config_requires_saved_credential(config: TeamSttConfig) -> bool:
    if config.adapter_kind in STT_ADAPTERS_REQUIRING_BEARER_AUTH:
        return True
    try:
        preset = get_stt_provider_preset(resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url))
    except ValueError:
        return False
    return preset.requires_api_key


def _validate_stt_auth_mode_for_adapter(*, adapter_kind: SttAdapterKind, auth_mode: SttAuthMode) -> None:
    """Reject no-auth configs for provider-specific adapters with mandatory keys."""
    if adapter_kind in STT_ADAPTERS_REQUIRING_BEARER_AUTH and auth_mode is not SttAuthMode.bearer:
        raise AppError(
            422,
            "business_rule_violation",
            "This STT adapter requires auth_mode=bearer",
            {"field": "auth_mode", "adapter_kind": adapter_kind.value},
        )


def _read_stt_snapshot_bearer_token(
    db: Session,
    *,
    team_id: UUID,
    stt_config_id: UUID,
    adapter_kind: SttAdapterKind,
) -> str | None:
    config = db.get(TeamSttConfig, stt_config_id)
    if config is not None:
        return _read_saved_stt_bearer_token(team_id=team_id, config=config)
    if adapter_kind in STT_ADAPTERS_REQUIRING_BEARER_AUTH:
        raise _missing_stt_credential_error(team_id=team_id, config_id=stt_config_id)
    return None


def ensure_stt_config_credential_ready(*, team_id: UUID, config: TeamSttConfig) -> None:
    _read_saved_stt_bearer_token(team_id=team_id, config=config)


def _normalized_known_adapter_fields(adapter_kind: SttAdapterKind) -> tuple[str, str, str]:
    if adapter_kind in {SttAdapterKind.openai_cloud, SttAdapterKind.openai_compatible_rest}:
        return "/v1/audio/transcriptions", "file", "text"
    if adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        return "/v1/speech-to-text", "file", "text"
    raise ValueError(f"Unsupported known adapter kind: {adapter_kind}")


def _candidate_stt_openapi_paths(openapi_path: str | None) -> list[str]:
    paths: list[str] = []
    if openapi_path:
        if openapi_path.endswith(".json"):
            paths.append(openapi_path)
        elif openapi_path in {"/docs", "/redoc"}:
            paths.append("/openapi.json")
        else:
            paths.append(openapi_path)
    paths.extend(["/openapi.json", "/docs", "/redoc"])
    return list(dict.fromkeys(paths))


def _list_openai_transcription_models(*, api_key: str, base_url: str) -> list[str]:
    require_safe_provider_url(base_url)
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models_page = client.models.list()
    except (AuthenticationError, PermissionDeniedError) as exc:  # pragma: no cover
        raise AppError(401, "stt_credential_invalid", "The API key was rejected by the provider.", {"provider_status": getattr(exc, "status_code", None)}) from exc
    except APIStatusError as exc:  # pragma: no cover
        if exc.status_code in {401, 403}:
            raise AppError(401, "stt_credential_invalid", "The API key was rejected by the provider.", {"provider_status": exc.status_code}) from exc
        raise AppError(502, "stt_inspection_failed", "Could not load available OpenAI transcription models", {"provider_status": exc.status_code}) from exc
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "stt_inspection_failed", "Could not load available OpenAI transcription models") from exc

    return sorted(
        {
            model.id
            for model in getattr(models_page, "data", [])
            if getattr(model, "id", None) in SUPPORTED_OPENAI_TRANSCRIPTION_MODELS
        }
    )


def _list_deepgram_stt_models(*, api_key: str, base_url: str) -> list[str]:
    require_safe_provider_url(base_url)
    try:
        with httpx.stream(
            "GET",
            f"{base_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Token {api_key}"},
            timeout=10.0,
        ) as response:
            response.raise_for_status()
            payload = json.loads(read_limited_httpx_response(response, max_bytes=STT_MODEL_DISCOVERY_MAX_RESPONSE_BYTES))
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {401, 403}:
            raise AppError(
                401,
                "stt_credential_invalid",
                "The API key was rejected by Deepgram.",
                {"provider_status": status_code},
            ) from exc
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not load available Deepgram STT models",
            {"provider_status": status_code},
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not reach Deepgram model discovery",
            {"provider_error_code": _safe_http_error_details(exc).get("provider_error_code")},
        ) from exc
    except ProviderResponseTooLargeError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "Deepgram model discovery response exceeded the permitted size",
            {"provider_error_code": "response_too_large"},
        ) from exc
    except ValueError as exc:
        raise AppError(502, "stt_inspection_failed", "Deepgram model discovery returned invalid JSON") from exc

    stt_models = payload.get("stt")
    if not isinstance(stt_models, list):
        return []

    discovered: list[str] = []
    for item in stt_models:
        if not isinstance(item, dict):
            continue
        if item.get("batch") is False:
            continue
        model_id = item.get("canonical_name") or item.get("name")
        if isinstance(model_id, str) and model_id.strip():
            discovered.append(model_id.strip())
    return sorted(set(discovered))


def _list_elevenlabs_stt_models(*, api_key: str, base_url: str) -> list[str]:
    require_safe_provider_url(base_url)
    try:
        with httpx.stream(
            "GET",
            f"{base_url.rstrip('/')}/v1/models",
            headers={"xi-api-key": api_key},
            timeout=10.0,
        ) as response:
            response.raise_for_status()
            payload = json.loads(read_limited_httpx_response(response, max_bytes=STT_MODEL_DISCOVERY_MAX_RESPONSE_BYTES))
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {401, 403}:
            raise AppError(
                401,
                "stt_credential_invalid",
                "The API key was rejected by ElevenLabs.",
                {"provider_status": status_code},
            ) from exc
        raise AppError(502, "stt_inspection_failed", "Could not load available ElevenLabs STT models", {"provider_status": status_code}) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "Could not reach ElevenLabs model discovery",
            {"provider_error_code": _safe_http_error_details(exc).get("provider_error_code")},
        ) from exc
    except ProviderResponseTooLargeError as exc:
        raise AppError(
            502,
            "stt_inspection_failed",
            "ElevenLabs model discovery response exceeded the permitted size",
            {"provider_error_code": "response_too_large"},
        ) from exc
    except ValueError as exc:
        raise AppError(502, "stt_inspection_failed", "ElevenLabs model discovery returned invalid JSON") from exc

    raw_models = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return []

    discovered: list[str] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("model_id")
        if not isinstance(model_id, str):
            continue
        normalized = model_id.strip()
        if normalized in ELEVENLABS_SYNC_STT_MODEL_IDS:
            discovered.append(normalized)
    return sorted(set(discovered), key=_elevenlabs_model_sort_key)


def _elevenlabs_model_sort_key(model_id: str) -> tuple[int, str]:
    try:
        return (ELEVENLABS_PREFERRED_STT_MODELS.index(model_id), model_id)
    except ValueError:
        return (999, model_id)


def _preferred_elevenlabs_model(models: list[str]) -> str | None:
    for model in ELEVENLABS_PREFERRED_STT_MODELS:
        if model in models:
            return model
    return models[0] if models else None


def _validated_elevenlabs_model(model_name: str | None, *, field: str = "model_name") -> str:
    normalized = normalize_optional_stt_text(model_name) or ELEVENLABS_PREFERRED_STT_MODELS[0]
    if normalized not in ELEVENLABS_SYNC_STT_MODEL_IDS:
        raise AppError(
            422,
            "business_rule_violation",
            "Selected ElevenLabs STT model is not supported",
            {"field": field, "allowed_models": list(ELEVENLABS_PREFERRED_STT_MODELS)},
        )
    return normalized


def _preferred_deepgram_model(models: list[str]) -> str | None:
    for model in ("nova-3", "nova-2"):
        if model in models:
            return model
    return models[0] if models else None


def _fallback_openai_transcription_models() -> list[str]:
    return list(SUPPORTED_OPENAI_TRANSCRIPTION_MODELS)


def _openai_model_options(models: list[str], *, source: str) -> list[SttModelOption]:
    return [SttModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


def _extract_response_text(payload: dict[str, Any], path: str) -> str:
    try:
        current = extract_json_path(payload, path)
    except AppError as exc:
        raise AppError(502, "stt_response_invalid", "STT provider response did not contain transcript text") from exc
    if current is None:
        raise AppError(502, "stt_response_invalid", "STT provider response did not contain transcript text")
    text = str(current).strip()
    if not text:
        raise AppError(502, "stt_response_invalid", "STT provider response did not contain transcript text")
    return text


def paragraphize_timestamped_segments(
    segments: list[dict[str, Any]],
    *,
    max_chars: int = 180,
    pause_threshold_seconds: float = 1.2,
) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush_current() -> None:
        nonlocal current
        if current is None:
            return
        current["text"] = " ".join(str(current["text"]).split()).strip()
        if current["text"]:
            paragraphs.append(current)
        current = None

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        speaker = str(segment.get("speaker") or "UNKNOWN")
        if current is None:
            current = {
                "start": start,
                "end": end,
                "speaker": speaker,
                "text": text,
                "segment_count": 1,
            }
            continue

        pause = max(0.0, start - float(current["end"]))
        current_text = str(current["text"])
        next_text = f"{current_text} {text}".strip()
        should_break = (
            speaker != current["speaker"]
            or pause >= pause_threshold_seconds
            or len(next_text) > max_chars
            or current_text.endswith((".", "?", "!", ":")) and pause >= 0.45
        )
        if should_break:
            flush_current()
            current = {
                "start": start,
                "end": end,
                "speaker": speaker,
                "text": text,
                "segment_count": 1,
            }
            continue

        current["end"] = end
        current["text"] = next_text
        current["segment_count"] = int(current["segment_count"]) + 1

    flush_current()
    return paragraphs


def _format_timestamped_transcript_payload(payload: dict[str, Any], *, response_text_path: str) -> str:
    return _format_timestamped_transcript_payload_with_segments(
        payload,
        response_text_path=response_text_path,
        segments_path="segments",
        segment_text_field="text",
        segment_start_field="start",
        segment_end_field="end",
        segment_speaker_field="speaker",
    )


def _format_timestamped_transcript_payload_with_segments(
    payload: dict[str, Any],
    *,
    response_text_path: str,
    segments_path: str | None,
    segment_text_field: str | None,
    segment_start_field: str | None,
    segment_end_field: str | None,
    segment_speaker_field: str | None,
) -> str:
    segments = None
    if segments_path:
        try:
            segments = extract_json_path(payload, segments_path)
        except AppError:
            segments = None
    if isinstance(segments, list):
        normalized_segments: list[dict[str, Any]] = []
        text_field = segment_text_field or "text"
        start_field = segment_start_field or "start"
        end_field = segment_end_field or "end"
        speaker_field = segment_speaker_field or "speaker"
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            normalized_segments.append(
                {
                    "text": segment.get(text_field),
                    "start": segment.get(start_field),
                    "end": segment.get(end_field),
                    "speaker": segment.get(speaker_field),
                }
            )
        paragraphs = paragraphize_timestamped_segments(normalized_segments)
        if paragraphs:
            return "\n\n".join(paragraph["text"] for paragraph in paragraphs if str(paragraph.get("text") or "").strip())
    return _extract_response_text(payload, response_text_path)


def _sanitize_logged_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    parts = urlsplit(raw_url)
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _sanitized_endpoint_url(base_url: str, path: str) -> str | None:
    safe_base = _sanitize_logged_url(base_url)
    if safe_base is None:
        return None
    safe_path = urlsplit(path).path
    return _sanitize_logged_url(f"{safe_base.rstrip('/')}/{safe_path.lstrip('/')}")


def ensure_stt_service_healthy(
    *,
    adapter_kind: SttAdapterKind,
    base_url: str,
    bearer_token: str | None = None,
    healthcheck_url: str | None = None,
) -> None:
    if adapter_kind is SttAdapterKind.openai_cloud or not healthcheck_url:
        return
    require_safe_provider_url(healthcheck_url)
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    try:
        response = httpx.get(healthcheck_url, headers=headers, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.warning(
            "stt_healthcheck_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "healthcheck": True,
                }
            },
        )
        raise AppError(
            502,
            "stt_healthcheck_failed",
            "STT provider health check failed",
            {"status_code": status_code},
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "stt_healthcheck_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "healthcheck": True,
                }
            },
        )
        raise AppError(
            502,
            "stt_healthcheck_failed",
            "Could not reach the STT provider health endpoint",
            {"provider_error_code": _safe_http_error_details(exc).get("provider_error_code")},
        ) from exc


_STT_HEALTH_CACHE_TTL_SECONDS = 60
_stt_health_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def clear_stt_health_cache() -> None:
    _stt_health_cache.clear()


def _stt_health_detail_allowed(actor: User) -> bool:
    return bool(actor.is_system_admin or actor.team_role is TeamRole.leader)


def _stt_health_response(
    *,
    status: str,
    message: str,
    checked: bool,
    checked_at: float | None = None,
    details: dict[str, Any] | None = None,
    include_details: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "message": message,
        "checked": checked,
        "checked_at": checked_at,
    }
    if include_details and details:
        payload["details"] = details
    return payload


def _filter_stt_health_payload(payload: dict[str, Any], *, include_details: bool) -> dict[str, Any]:
    filtered = dict(payload)
    if not include_details:
        filtered.pop("details", None)
    return filtered


def _stt_health_skip_reason(config: TeamSttConfig) -> str | None:
    if config.adapter_kind is SttAdapterKind.openai_cloud:
        return "openai_cloud_skipped"
    if config.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        return "elevenlabs_skipped"
    if config.provider_preset in {SttProviderPreset.deepgram.value, SttProviderPreset.elevenlabs.value}:
        return "provider_specific_health_skipped"
    if config.adapter_kind not in {SttAdapterKind.generic_rest, SttAdapterKind.openai_compatible_rest}:
        return "unsupported_adapter_skipped"
    return None


def check_selected_stt_health(
    db: Session,
    actor: User,
    *,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
    bypass_cache: bool = False,
    cache_only: bool = False,
) -> dict[str, Any]:
    include_details = _stt_health_detail_allowed(actor)
    if actor.team_id is None:
        return _stt_health_response(status="unavailable", message="Speech service is not configured.", checked=False, include_details=include_details)
    try:
        selection = active_team_stt_selection(db, team_id=actor.team_id, purpose=purpose)
    except AppError:
        return _stt_health_response(status="unavailable", message="Speech service is not configured.", checked=False, include_details=include_details)
    config = selection.config
    cache_key = (str(actor.team_id), str(config.id), purpose.value)
    now = time()
    if not bypass_cache:
        cached = _stt_health_cache.get(cache_key)
        if cached is not None and now - cached[0] < _STT_HEALTH_CACHE_TTL_SECONDS:
            return _filter_stt_health_payload(cached[1], include_details=include_details)
    if cache_only:
        return _stt_health_response(
            status="unknown",
            message="Speech service health has not been checked yet.",
            checked=False,
            include_details=include_details,
        )

    checked_at = now
    skip_reason = _stt_health_skip_reason(config)
    if skip_reason:
        payload = _stt_health_response(
            status="unknown",
            message="Speech service health is not reported.",
            checked=False,
            checked_at=checked_at,
            details={"reason": skip_reason},
            include_details=True,
        )
        _stt_health_cache[cache_key] = (now, payload)
        return _filter_stt_health_payload(payload, include_details=include_details)

    health_url = f"{config.base_url.rstrip('/')}/health"
    require_safe_provider_url(health_url)
    try:
        bearer_token = _read_saved_stt_bearer_token(team_id=actor.team_id, config=config)
    except AppError:
        payload = _stt_health_response(
            status="unavailable",
            message="Speech service needs attention from your team lead.",
            checked=False,
            checked_at=checked_at,
            details={"reason": "credential_unavailable", "health_url": _sanitize_logged_url(health_url)},
            include_details=True,
        )
        _stt_health_cache[cache_key] = (now, payload)
        return _filter_stt_health_payload(payload, include_details=include_details)
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    started_at = monotonic()
    details: dict[str, Any] = {"health_url": _sanitize_logged_url(health_url)}
    try:
        response = httpx.get(health_url, headers=headers, timeout=5.0)
        duration_ms = int((monotonic() - started_at) * 1000)
        details.update({"status_code": response.status_code, "duration_ms": duration_ms})
        if 200 <= response.status_code < 300:
            payload = _stt_health_response(
                status="healthy",
                message="Speech service reachable.",
                checked=True,
                checked_at=checked_at,
                details=details,
                include_details=True,
            )
        elif response.status_code == 404:
            payload = _stt_health_response(
                status="unknown",
                message="Speech service health is not reported.",
                checked=True,
                checked_at=checked_at,
                details=details,
                include_details=True,
            )
        elif response.status_code in {401, 403}:
            payload = _stt_health_response(
                status="warning",
                message="Speech service needs attention from your team lead.",
                checked=True,
                checked_at=checked_at,
                details={**details, "provider_error_code": _provider_error_code_from_response(response) or "http_status_error"},
                include_details=True,
            )
        else:
            payload = _stt_health_response(
                status="warning",
                message="Speech service may be unavailable; transcription may fail.",
                checked=True,
                checked_at=checked_at,
                details={**details, "provider_error_code": _provider_error_code_from_response(response) or "http_status_error"},
                include_details=True,
            )
    except httpx.HTTPError as exc:
        error_details = _safe_http_error_details(exc)
        payload = _stt_health_response(
            status="warning",
            message="Speech service may be unavailable; transcription may fail.",
            checked=True,
            checked_at=checked_at,
            details={**details, **error_details, "duration_ms": int((monotonic() - started_at) * 1000)},
            include_details=True,
        )
    _stt_health_cache[cache_key] = (now, payload)
    return _filter_stt_health_payload(payload, include_details=include_details)


def _translate_http_stt_error(exc: httpx.HTTPError) -> AppError:
    if isinstance(exc, httpx.TimeoutException):
        return AppError(
            504,
            "stt_timeout",
            "STT provider timed out",
            {"provider_error_code": "timeout"},
        )
    if isinstance(exc, httpx.ConnectError):
        return AppError(
            502,
            "stt_unavailable",
            "Could not reach the STT provider",
            {"provider_error_code": "connection_error"},
        )
    if isinstance(exc, httpx.HTTPStatusError):
        details = _safe_http_error_details(exc)
        status_code = details.get("status_code")
        return AppError(
            502,
            "stt_request_failed",
            "STT provider request failed",
            {
                "status_code": status_code,
                "provider_status_code": status_code,
                "provider_error_code": details.get("provider_error_code"),
            },
        )
    return AppError(502, "stt_unavailable", "STT provider is unavailable")


def _provider_error_code_from_response(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    try:
        payload = json.loads(read_limited_httpx_response(response, max_bytes=STT_PROVIDER_ERROR_RESPONSE_MAX_BYTES))
    except ProviderResponseTooLargeError:
        return "response_too_large"
    except (ValueError, httpx.StreamError):
        return None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        status_value = detail.get("status")
        if isinstance(status_value, str) and status_value:
            return safe_provider_error_code(status_value, status_code=response.status_code)
        code_value = detail.get("code")
        if isinstance(code_value, str) and code_value:
            return safe_provider_error_code(code_value, status_code=response.status_code)
    error = payload.get("error")
    if isinstance(error, dict):
        code_value = error.get("code")
        if isinstance(code_value, str) and code_value:
            return safe_provider_error_code(code_value, status_code=response.status_code)
    code_value = payload.get("code")
    if isinstance(code_value, str) and code_value:
        return safe_provider_error_code(code_value, status_code=response.status_code)
    return None


def _raise_for_stt_stream_status(response: httpx.Response) -> None:
    """Raise HTTP status failures after safely reading their bounded body."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # The stream closes before the surrounding error handler runs. Capture
        # only a safe error code now, while its body remains readable.
        setattr(exc, _STT_PROVIDER_ERROR_CODE_ATTRIBUTE, _provider_error_code_from_response(response))
        raise


def _read_stt_transcription_response(
    response: httpx.Response,
    *,
    invalid_json_message: str,
) -> Any:
    try:
        response_body = read_limited_httpx_response(response, max_bytes=STT_TRANSCRIPTION_RESPONSE_MAX_BYTES)
    except ProviderResponseTooLargeError as exc:
        raise AppError(
            502,
            "stt_response_invalid",
            "STT provider response exceeded the permitted size",
            {"provider_error_code": "response_too_large"},
        ) from exc
    try:
        return json.loads(response_body)
    except ValueError as exc:
        raise AppError(502, "stt_response_invalid", invalid_json_message) from exc


def _safe_http_error_details(exc: httpx.HTTPError) -> dict[str, Any]:
    request = getattr(exc, "request", None)
    response = getattr(exc, "response", None)
    details: dict[str, Any] = {
        "error_type": exc.__class__.__name__,
        "method": getattr(request, "method", None),
        "url": _sanitize_logged_url(str(request.url)) if getattr(request, "url", None) else None,
    }
    if response is not None:
        details["status_code"] = response.status_code
    if isinstance(exc, httpx.TimeoutException):
        details["provider_error_code"] = "timeout"
    elif isinstance(exc, httpx.ConnectError):
        details["provider_error_code"] = "connection_error"
    elif isinstance(exc, httpx.HTTPStatusError):
        provider_error_code = getattr(exc, _STT_PROVIDER_ERROR_CODE_ATTRIBUTE, _STT_PROVIDER_ERROR_CODE_UNREAD)
        if provider_error_code is _STT_PROVIDER_ERROR_CODE_UNREAD:
            provider_error_code = _provider_error_code_from_response(response)
        details["provider_error_code"] = provider_error_code or "http_status_error"
    else:
        details["provider_error_code"] = "http_error"
    return details


def _transcribe_via_http(
    *,
    base_url: str,
    transcribe_path: str,
    file_field_name: str,
    response_text_path: str,
    extra_form_fields_json: dict[str, str] | None,
    bearer_token: str | None,
    model_name: str | None,
    model_field_name: str | None,
    language: str | None,
    language_field_name: str | None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    provider_preset: str | None = None,
    adapter_kind: SttAdapterKind | None = None,
    segments_path: str | None = None,
    segment_text_field: str | None = None,
    segment_start_field: str | None = None,
    segment_end_field: str | None = None,
    segment_speaker_field: str | None = None,
) -> str:
    require_safe_provider_url(base_url)
    url = f"{base_url.rstrip('/')}{transcribe_path}"
    resolved_provider_preset = resolve_stt_provider_preset(provider_preset, adapter_kind or SttAdapterKind.generic_rest, base_url)
    if adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        return _transcribe_via_elevenlabs_speech_to_text(
            base_url=base_url,
            api_key=bearer_token,
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            model_name=model_name,
            language=language,
        )
    if resolved_provider_preset == SttProviderPreset.deepgram.value:
        return _transcribe_via_deepgram(
            url=url,
            bearer_token=bearer_token,
            audio_bytes=audio_bytes,
            content_type=content_type,
            model_name=model_name,
            language=language,
            extra_query_params=_normalize_deepgram_extra_query_params(
                extra_form_fields_json,
                provider_preset=resolved_provider_preset,
                adapter_kind=adapter_kind or SttAdapterKind.generic_rest,
                base_url=base_url,
                reject_explicit_non_true=False,
            ),
            response_text_path=response_text_path,
            segments_path=segments_path,
            segment_text_field=segment_text_field,
            segment_start_field=segment_start_field,
            segment_end_field=segment_end_field,
            segment_speaker_field=segment_speaker_field,
        )
    if provider_preset == SttProviderPreset.elevenlabs.value:
        return _transcribe_via_elevenlabs(
            url=url,
            api_key=bearer_token,
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            model_name=model_name,
            language=language,
            response_text_path=response_text_path,
            segments_path=segments_path,
            segment_text_field=segment_text_field,
            segment_start_field=segment_start_field,
            segment_end_field=segment_end_field,
            segment_speaker_field=segment_speaker_field,
        )
    form_fields = dict(extra_form_fields_json or {})
    language = normalize_stt_language(language)
    if model_name and model_field_name:
        form_fields[model_field_name] = model_name
    if language and language_field_name:
        form_fields[language_field_name] = language
    try:
        headers: dict[str, str] = {}
        if bearer_token:
            preset = get_stt_provider_preset(resolved_provider_preset) if resolved_provider_preset else None
            if preset and preset.auth_header_style == "token":
                headers["Authorization"] = f"Token {bearer_token}"
            elif preset and preset.auth_header_style == "xi-api-key":
                headers["xi-api-key"] = bearer_token
            elif not preset or preset.auth_header_style == "bearer":
                headers["Authorization"] = f"Bearer {bearer_token}"
        with httpx.stream(
            "POST",
            url,
            headers=headers,
            data=form_fields,
            files={file_field_name: (filename, audio_bytes, content_type)},
            timeout=STT_TRANSCRIPTION_TIMEOUT_SECONDS,
        ) as response:
            _raise_for_stt_stream_status(response)
            try:
                payload = _read_stt_transcription_response(
                    response,
                    invalid_json_message="STT provider response was not valid JSON",
                )
            except AppError as exc:
                if exc.code == "stt_response_invalid":
                    logger.warning(
                        "stt_http_response_invalid_json",
                        extra={
                            "stt_transport": {
                                "method": "POST",
                                "url": _sanitize_logged_url(url),
                                "status_code": response.status_code,
                                "response_text_path": response_text_path,
                            }
                        },
                    )
                raise
    except httpx.HTTPError as exc:
        logger.warning(
            "stt_http_request_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "file_field_name": file_field_name,
                    "response_text_path": response_text_path,
                    "audio_byte_count": len(audio_bytes),
                    "form_field_keys": sorted(form_fields.keys()),
                    "model_field_name": model_field_name,
                    "language_field_name": language_field_name,
                }
            },
        )
        raise _translate_http_stt_error(exc) from exc
    return _format_timestamped_transcript_payload_with_segments(
        payload,
        response_text_path=response_text_path,
        segments_path=segments_path or "segments",
        segment_text_field=segment_text_field or "text",
        segment_start_field=segment_start_field or "start",
        segment_end_field=segment_end_field or "end",
        segment_speaker_field=segment_speaker_field or "speaker",
    )


def _transcribe_via_elevenlabs_speech_to_text(
    *,
    base_url: str,
    api_key: str | None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    model_name: str | None,
    language: str | None,
) -> str:
    if not api_key:
        raise AppError(409, "stt_config_secret_missing", "ElevenLabs STT requires a saved API key.")

    url = f"{base_url.rstrip('/')}/v1/speech-to-text"
    data: dict[str, str] = {"model_id": _validated_elevenlabs_model(model_name)}
    language = normalize_stt_language(language)
    if language:
        data["language_code"] = language

    try:
        with httpx.stream(
            "POST",
            url,
            headers={"xi-api-key": api_key},
            data=data,
            files={"file": (filename, audio_bytes, content_type or "application/octet-stream")},
            timeout=STT_TRANSCRIPTION_TIMEOUT_SECONDS,
        ) as response:
            _raise_for_stt_stream_status(response)
            payload = _read_stt_transcription_response(
                response,
                invalid_json_message="ElevenLabs response was not valid JSON",
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "stt_elevenlabs_request_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "provider_preset": SttProviderPreset.elevenlabs.value,
                    "form_field_keys": sorted(data.keys()),
                    "audio_byte_count": len(audio_bytes),
                    "content_type": content_type,
                }
            },
        )
        raise _translate_http_stt_error(exc) from exc

    return _format_timestamped_transcript_payload_with_segments(
        payload,
        response_text_path="text",
        segments_path="words",
        segment_text_field="text",
        segment_start_field="start",
        segment_end_field="end",
        segment_speaker_field="speaker_id",
    )


def _transcribe_via_elevenlabs(**kwargs: Any) -> str:
    if "url" in kwargs:
        url = str(kwargs.pop("url"))
        suffix = "/v1/speech-to-text"
        kwargs["base_url"] = url[: -len(suffix)] if url.endswith(suffix) else url.rstrip("/")
    kwargs.pop("response_text_path", None)
    kwargs.pop("segments_path", None)
    kwargs.pop("segment_text_field", None)
    kwargs.pop("segment_start_field", None)
    kwargs.pop("segment_end_field", None)
    kwargs.pop("segment_speaker_field", None)
    return _transcribe_via_elevenlabs_speech_to_text(**kwargs)


def _transcribe_via_deepgram(
    *,
    url: str,
    bearer_token: str | None,
    audio_bytes: bytes,
    content_type: str,
    model_name: str | None,
    language: str | None,
    extra_query_params: dict[str, str],
    response_text_path: str,
    segments_path: str | None = None,
    segment_text_field: str | None = None,
    segment_start_field: str | None = None,
    segment_end_field: str | None = None,
    segment_speaker_field: str | None = None,
) -> str:
    if not bearer_token:
        raise AppError(409, "stt_config_secret_missing", "Deepgram STT requires a saved API key.")

    # For Deepgram, stored extra_form_fields_json represents query parameters for /v1/listen, not multipart form fields.
    params = dict(extra_query_params or {})
    language = normalize_stt_language(language)
    if model_name:
        params["model"] = model_name
    if language:
        params["language"] = language

    try:
        with httpx.stream(
            "POST",
            url,
            headers={
                "Authorization": f"Token {bearer_token}",
                "Content-Type": content_type or "application/octet-stream",
            },
            params=params,
            content=audio_bytes,
            timeout=STT_TRANSCRIPTION_TIMEOUT_SECONDS,
        ) as response:
            _raise_for_stt_stream_status(response)
            payload = _read_stt_transcription_response(
                response,
                invalid_json_message="Deepgram response was not valid JSON",
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "stt_deepgram_request_failed",
            extra={
                "stt_transport": {
                    **_safe_http_error_details(exc),
                    "provider_preset": SttProviderPreset.deepgram.value,
                    "query_keys": sorted(params.keys()),
                    "audio_byte_count": len(audio_bytes),
                    "content_type": content_type,
                }
            },
        )
        raise _translate_http_stt_error(exc) from exc

    return _format_timestamped_transcript_payload_with_segments(
        payload,
        response_text_path=response_text_path,
        segments_path=segments_path,
        segment_text_field=segment_text_field,
        segment_start_field=segment_start_field,
        segment_end_field=segment_end_field,
        segment_speaker_field=segment_speaker_field,
    )


def _transcribe_via_openai_cloud(
    *,
    base_url: str,
    extra_form_fields_json: dict[str, str] | None,
    bearer_token: str,
    model_name: str | None,
    language: str | None,
    audio_bytes: bytes,
    filename: str,
) -> str:
    require_safe_provider_url(base_url)
    # Provider-attempt accounting is one row per outbound request. Disable SDK
    # retries so an implicit retry cannot consume provider capacity unrecorded.
    client = OpenAI(api_key=bearer_token, base_url=base_url, timeout=STT_TRANSCRIPTION_TIMEOUT_SECONDS, max_retries=0)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    kwargs: dict[str, Any] = {
        "file": audio_file,
        "model": model_name or "whisper-1",
    }
    if language:
        language = normalize_stt_language(language)
    if language:
        kwargs["language"] = language
    if response_format := (extra_form_fields_json or {}).get("response_format"):
        kwargs["response_format"] = response_format
    try:
        response = client.audio.transcriptions.create(**kwargs)
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "stt_request_failed", "STT provider request failed") from exc

    text = getattr(response, "text", None)
    if text:
        stripped = str(text).strip()
        if stripped:
            return stripped
    if hasattr(response, "model_dump"):
        return _extract_response_text(response.model_dump(), "text")
    raise AppError(502, "stt_response_invalid", "STT provider response did not contain transcript text")


def _transcribe_with_resolved_team_stt(
    *,
    team_id: UUID,
    config: TeamSttConfig,
    resolved_model_name: str | None,
    resolved_language: str | None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    bearer_token: str | None,
) -> str:
    provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)
    if config.adapter_kind is SttAdapterKind.openai_cloud and provider_preset != SttProviderPreset.deepgram.value:
        if not bearer_token:
            raise _missing_stt_credential_error(team_id=team_id, config_id=config.id)
        return _transcribe_via_openai_cloud(
            base_url=config.base_url,
            extra_form_fields_json=config.extra_form_fields_json,
            bearer_token=bearer_token,
            model_name=resolved_model_name,
            language=resolved_language,
            audio_bytes=audio_bytes,
            filename=filename,
        )
    return _transcribe_via_http(
        base_url=config.base_url,
        transcribe_path=config.transcribe_path,
        file_field_name=config.file_field_name,
        response_text_path=config.response_text_path,
        extra_form_fields_json=config.extra_form_fields_json,
        bearer_token=bearer_token,
        provider_preset=provider_preset,
        adapter_kind=config.adapter_kind,
        model_name=resolved_model_name,
        model_field_name=config.model_field_name or "model",
        language=resolved_language,
        language_field_name=config.language_field_name or "language",
        segments_path=config.segments_path,
        segment_text_field=config.segment_text_field,
        segment_start_field=config.segment_start_field,
        segment_end_field=config.segment_end_field,
        segment_speaker_field=config.segment_speaker_field,
        audio_bytes=audio_bytes,
        filename=filename,
        content_type=content_type,
    )


def _transcribe_metered_audio(
    db: Session,
    *,
    team_id: UUID,
    owner_user_id: UUID | None,
    transcript_id: UUID | None,
    attempt_kind: AttemptKind,
    measured_duration_seconds: float,
    provider_adapter: str,
    provider_model: str | None,
    provider_call: Any,
) -> str:
    """Reserve, durably submit, then settle one synchronous STT request.

    Accounting uses isolated sessions so its required pre-network commit never
    commits an enclosing provider-configuration or content transaction.
    """
    now = utcnow()
    with Session(bind=db.get_bind(), future=True) as accounting_db:
        attempt = reserve_provider_attempt(
            accounting_db,
            team_id=team_id,
            owner_user_id=owner_user_id,
            resource=QuotaResource.audio_seconds,
            attempt_kind=attempt_kind,
            correlation_id=uuid4(),
            attempt_number=1,
            reserved_units=ceil(measured_duration_seconds),
            reservation_valid_until=now + timedelta(seconds=STT_TRANSCRIPTION_TIMEOUT_SECONDS + SYNC_STT_RESERVATION_GRACE_SECONDS),
            authorized_at=now,
            transcript_id=transcript_id,
            provider_adapter=provider_adapter,
            provider_model=provider_model,
            measured_audio_seconds=measured_duration_seconds,
        )
        mark_provider_attempt_submitted(
            accounting_db,
            attempt_id=attempt.id,
            deadline_at=now + timedelta(seconds=STT_TRANSCRIPTION_TIMEOUT_SECONDS + SYNC_STT_RESERVATION_GRACE_SECONDS),
        )
        # Network boundary: attempt state must survive process loss now.
        accounting_db.commit()
        attempt_id = attempt.id
    try:
        result = provider_call()
    except AppError:
        with Session(bind=db.get_bind(), future=True) as accounting_db:
            settle_provider_attempt_audio(
                accounting_db, attempt_id=attempt_id,
                measured_audio_seconds=measured_duration_seconds,
                outcome=AttemptOutcome.failed,
            )
            accounting_db.commit()
        raise
    except Exception as exc:
        with Session(bind=db.get_bind(), future=True) as accounting_db:
            settle_provider_attempt_audio(
                accounting_db, attempt_id=attempt_id,
                measured_audio_seconds=measured_duration_seconds,
                outcome=AttemptOutcome.unknown,
            )
            accounting_db.commit()
        raise AppError(502, "stt_request_failed", "STT provider request failed") from exc
    with Session(bind=db.get_bind(), future=True) as accounting_db:
        settle_provider_attempt_audio(
            accounting_db, attempt_id=attempt_id,
            measured_audio_seconds=measured_duration_seconds,
        )
        accounting_db.commit()
    return result


def transcribe_metered_team_stt(
    db: Session,
    *,
    team_id: UUID,
    owner_user_id: UUID,
    transcript_id: UUID,
    attempt_kind: AttemptKind,
    measured_duration_seconds: float,
    purpose: SttSelectionPurpose,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
) -> str:
    _, config, resolved_model_name, resolved_language = resolve_selected_team_stt(db, team_id=team_id, purpose=purpose)
    # Resolve credentials before reservation: missing credentials never dispatch.
    bearer_token = _read_saved_stt_bearer_token(team_id=team_id, config=config)
    return _transcribe_metered_audio(
        db,
        team_id=team_id,
        owner_user_id=owner_user_id,
        transcript_id=transcript_id,
        attempt_kind=attempt_kind,
        measured_duration_seconds=measured_duration_seconds,
        provider_adapter=config.adapter_kind.value,
        provider_model=resolved_model_name,
        provider_call=lambda: _transcribe_with_resolved_team_stt(
            team_id=team_id, config=config, resolved_model_name=resolved_model_name,
            resolved_language=resolved_language, audio_bytes=audio_bytes, filename=filename, content_type=content_type,
            bearer_token=bearer_token,
        ),
    )


def transcribe_with_team_stt(
    db: Session,
    *,
    team_id: UUID,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    _resolved_bearer_token: Any = _STT_CREDENTIAL_UNRESOLVED,
) -> str:
    _, config, resolved_model_name, resolved_language = resolve_selected_team_stt(db, team_id=team_id, purpose=purpose)
    bearer_token = (
        _read_saved_stt_bearer_token(team_id=team_id, config=config)
        if _resolved_bearer_token is _STT_CREDENTIAL_UNRESOLVED
        else _resolved_bearer_token
    )
    return _transcribe_with_resolved_team_stt(
        team_id=team_id, config=config, resolved_model_name=resolved_model_name,
        resolved_language=resolved_language, audio_bytes=audio_bytes, filename=filename, content_type=content_type,
        bearer_token=bearer_token,
    )


def resolve_stt_snapshot_bearer_token(
    db: Session,
    *,
    team_id: UUID,
    stt_config_id: UUID | None,
    adapter_kind: str | None,
    base_url: str | None,
    transcribe_path: str | None,
    file_field_name: str | None,
    response_text_path: str | None,
    provider_preset: str | None = None,
) -> str | None:
    """Validate queued snapshot and resolve its credential before dispatch claim."""
    if not stt_config_id or not adapter_kind or not base_url:
        _, config, _, _ = resolve_selected_team_stt(db, team_id=team_id, purpose=SttSelectionPurpose.conversation)
        return _read_saved_stt_bearer_token(team_id=team_id, config=config)

    resolved_adapter = SttAdapterKind(adapter_kind)
    replay_provider_preset = resolve_stt_provider_preset(provider_preset, resolved_adapter, base_url)
    if (
        not (resolved_adapter is SttAdapterKind.openai_cloud and replay_provider_preset != SttProviderPreset.deepgram.value)
        and (not transcribe_path or not file_field_name or not response_text_path)
    ):
        raise AppError(422, "business_rule_violation", "Queued STT snapshot is incomplete")

    bearer_token = _read_stt_snapshot_bearer_token(
        db,
        team_id=team_id,
        stt_config_id=stt_config_id,
        adapter_kind=resolved_adapter,
    )
    if resolved_adapter is SttAdapterKind.openai_cloud and replay_provider_preset != SttProviderPreset.deepgram.value:
        if not bearer_token:
            raise _missing_stt_credential_error(team_id=team_id, config_id=stt_config_id)
    return bearer_token


def transcribe_with_stt_snapshot(
    db: Session,
    *,
    team_id: UUID,
    stt_config_id: UUID | None,
    adapter_kind: str | None,
    base_url: str | None,
    transcribe_path: str | None,
    file_field_name: str | None,
    response_text_path: str | None,
    extra_form_fields_json: dict[str, str] | None,
    model_name: str | None,
    language: str | None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    provider_preset: str | None = None,
    model_field_name: str | None = None,
    language_field_name: str | None = None,
    segments_path: str | None = None,
    segment_text_field: str | None = None,
    segment_start_field: str | None = None,
    segment_end_field: str | None = None,
    segment_speaker_field: str | None = None,
    _resolved_bearer_token: Any = _STT_CREDENTIAL_UNRESOLVED,
) -> str:
    if not stt_config_id or not adapter_kind or not base_url:
        return transcribe_with_team_stt(
            db,
            team_id=team_id,
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            _resolved_bearer_token=_resolved_bearer_token,
        )
    resolved_adapter = SttAdapterKind(adapter_kind)
    replay_provider_preset = resolve_stt_provider_preset(provider_preset, resolved_adapter, base_url)
    bearer_token = (
        resolve_stt_snapshot_bearer_token(
            db,
            team_id=team_id,
            stt_config_id=stt_config_id,
            adapter_kind=adapter_kind,
            base_url=base_url,
            transcribe_path=transcribe_path,
            file_field_name=file_field_name,
            response_text_path=response_text_path,
            provider_preset=provider_preset,
        )
        if _resolved_bearer_token is _STT_CREDENTIAL_UNRESOLVED
        else _resolved_bearer_token
    )
    if resolved_adapter is SttAdapterKind.openai_cloud and replay_provider_preset != SttProviderPreset.deepgram.value:
        if not bearer_token:
            raise _missing_stt_credential_error(team_id=team_id, config_id=stt_config_id)
        return _transcribe_via_openai_cloud(
            base_url=base_url,
            extra_form_fields_json=extra_form_fields_json,
            bearer_token=bearer_token,
            model_name=model_name,
            language=language,
            audio_bytes=audio_bytes,
            filename=filename,
        )
    if not transcribe_path or not file_field_name or not response_text_path:
        raise AppError(422, "business_rule_violation", "Queued STT snapshot is incomplete")
    return _transcribe_via_http(
        base_url=base_url,
        transcribe_path=transcribe_path,
        file_field_name=file_field_name,
        response_text_path=response_text_path,
        extra_form_fields_json=extra_form_fields_json,
        bearer_token=bearer_token,
        provider_preset=replay_provider_preset,
        adapter_kind=resolved_adapter,
        model_name=model_name,
        model_field_name=model_field_name or "model",
        language=language,
        language_field_name=language_field_name or "language",
        segments_path=segments_path,
        segment_text_field=segment_text_field,
        segment_start_field=segment_start_field,
        segment_end_field=segment_end_field,
        segment_speaker_field=segment_speaker_field,
        audio_bytes=audio_bytes,
        filename=filename,
        content_type=content_type,
    )


def run_saved_stt_config_test(
    db: Session,
    actor: User,
    *,
    config_id: UUID,
    team_id: UUID | None = None,
    sample_path: Path = DEFAULT_STT_SAMPLE_PATH,
) -> dict[str, Any]:
    config = get_stt_config(db, actor, config_id=config_id, team_id=team_id)
    try:
        audio_bytes = sample_path.read_bytes()
    except OSError as exc:  # pragma: no cover
        raise AppError(500, "stt_test_sample_unavailable", "Bundled STT test audio is unavailable") from exc

    measured_duration_seconds = normalized_wav_duration_seconds(audio_bytes=audio_bytes)
    started_at = monotonic()
    health_status = "skipped"
    health_url = None
    try:
        bearer_token = _read_saved_stt_bearer_token(team_id=config.team_id, config=config)
        provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)

        def provider_call() -> str:
            return (
                _transcribe_via_openai_cloud(
                    base_url=config.base_url,
                    extra_form_fields_json=config.extra_form_fields_json,
                    bearer_token=bearer_token,
                    model_name=config.model_name,
                    language=config.language,
                    audio_bytes=audio_bytes,
                    filename=sample_path.name,
                )
                if config.adapter_kind is SttAdapterKind.openai_cloud and provider_preset != SttProviderPreset.deepgram.value
                else _transcribe_via_http(
                    base_url=config.base_url,
                    transcribe_path=config.transcribe_path,
                    file_field_name=config.file_field_name,
                    response_text_path=config.response_text_path,
                    extra_form_fields_json=config.extra_form_fields_json,
                    bearer_token=bearer_token,
                    provider_preset=provider_preset,
                    adapter_kind=config.adapter_kind,
                    model_name=config.model_name,
                    model_field_name=config.model_field_name or "model",
                    language=config.language,
                    language_field_name=config.language_field_name or "language",
                    segments_path=config.segments_path,
                    segment_text_field=config.segment_text_field,
                    segment_start_field=config.segment_start_field,
                    segment_end_field=config.segment_end_field,
                    segment_speaker_field=config.segment_speaker_field,
                    audio_bytes=audio_bytes,
                    filename=sample_path.name,
                    content_type="audio/wav",
                )
            )
        transcript_text = _transcribe_metered_audio(
            db,
            team_id=config.team_id,
            owner_user_id=None,
            transcript_id=None,
            attempt_kind=AttemptKind.stt_provider_test,
            measured_duration_seconds=measured_duration_seconds,
            provider_adapter=config.adapter_kind.value,
            provider_model=config.model_name,
            provider_call=provider_call,
        )
        result = {
            "success": True,
            "health_status": health_status,
            "sample_filename": sample_path.name,
            "sample_size_bytes": len(audio_bytes),
            "health_url": health_url,
            "transcribe_url": _sanitized_endpoint_url(config.base_url, config.transcribe_path),
            "model_name": config.model_name,
            "language": config.language,
            "duration_ms": int((monotonic() - started_at) * 1000),
            "transcript_text": transcript_text,
            "error_code": None,
            "error_message": None,
            "provider_status_code": None,
            "provider_error_code": None,
        }
        _record_stt_audit(
            db,
            action="stt_config_tested",
            actor=actor,
            team_id=config.team_id,
            config_id=config.id,
            outcome="success",
            duration_ms=result["duration_ms"],
            sample_size_bytes=result["sample_size_bytes"],
            provider_status_code=None,
        )
        return result
    except AppError as exc:
        details = exc.details or {}
        result = {
            "success": False,
            "health_status": health_status,
            "sample_filename": sample_path.name,
            "sample_size_bytes": len(audio_bytes),
            "health_url": health_url,
            "transcribe_url": _sanitized_endpoint_url(config.base_url, config.transcribe_path),
            "model_name": config.model_name,
            "language": config.language,
            "duration_ms": int((monotonic() - started_at) * 1000),
            "transcript_text": None,
            "error_code": exc.code,
            "error_message": exc.message,
            "provider_status_code": details.get("provider_status_code") or details.get("status_code"),
            "provider_error_code": details.get("provider_error_code"),
        }
        _record_stt_audit(
            db,
            action="stt_config_tested",
            actor=actor,
            team_id=config.team_id,
            config_id=config.id,
            outcome="failure",
            duration_ms=result["duration_ms"],
            sample_size_bytes=result["sample_size_bytes"],
            reason_code=exc.code,
            provider_status_code=result["provider_status_code"],
            provider_error_code=result["provider_error_code"],
        )
        return result


def _verify_generic_stt_config_with_sample(
    db: Session,
    config: TeamSttConfig,
    *,
    bearer_token: str | None,
    sample_path: Path = DEFAULT_STT_SAMPLE_PATH,
) -> None:
    try:
        audio_bytes = sample_path.read_bytes()
    except OSError as exc:  # pragma: no cover
        raise AppError(500, "stt_test_sample_unavailable", "Bundled STT test audio is unavailable") from exc

    measured_duration_seconds = normalized_wav_duration_seconds(audio_bytes=audio_bytes)
    _transcribe_metered_audio(
        db,
        team_id=config.team_id,
        owner_user_id=None,
        transcript_id=None,
        attempt_kind=AttemptKind.stt_provider_test,
        measured_duration_seconds=measured_duration_seconds,
        provider_adapter=config.adapter_kind.value,
        provider_model=config.model_name,
        provider_call=lambda: _transcribe_via_http(
        base_url=config.base_url,
        transcribe_path=config.transcribe_path,
        file_field_name=config.file_field_name,
        response_text_path=config.response_text_path,
        extra_form_fields_json=config.extra_form_fields_json,
        bearer_token=bearer_token,
        provider_preset=config.provider_preset,
        adapter_kind=config.adapter_kind,
        model_name=config.model_name,
        model_field_name=config.model_field_name or "model",
        language=config.language,
        language_field_name=config.language_field_name or "language",
        segments_path=config.segments_path,
        segment_text_field=config.segment_text_field,
        segment_start_field=config.segment_start_field,
        segment_end_field=config.segment_end_field,
        segment_speaker_field=config.segment_speaker_field,
        audio_bytes=audio_bytes,
        filename=sample_path.name,
        content_type="audio/wav",
        ),
    )


def create_stt_config_draft(db: Session, actor: User, payload: SttConfigDraftCreate) -> tuple[TeamSttConfig, SttInspectResult]:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    target = None
    if payload.revision_of_config_id is not None:
        target = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == payload.revision_of_config_id, TeamSttConfig.team_id == team.id, TeamSttConfig.revision_of_config_id.is_(None), TeamSttConfig.setup_status == SttConfigSetupStatus.ready))
        if target is None:
            raise AppError(404, "not_found", "Ready STT config not found", {"resource": "stt_config", "config_id": str(payload.revision_of_config_id)})
    provider_preset, adapter_kind, base_url, preset = apply_stt_provider_defaults(
        provider_preset=payload.provider_preset,
        base_url=payload.base_url,
    )
    provider_preset = _resolve_stt_provider_preset_for_admin_write(provider_preset, adapter_kind, base_url)
    preset = get_stt_provider_preset(provider_preset)
    bearer_token = payload.bearer_token
    inherits_bearer_token = not bearer_token and preset.requires_api_key and target is not None and bool(target.vault_secret_ref)
    if inherits_bearer_token:
        bearer_token = read_team_stt_bearer_token(
            team_id=team.id,
            config_id=target.id,
            secret_ref=target.vault_secret_ref,
        )
    if preset.requires_api_key and not bearer_token:
        raise AppError(422, "business_rule_violation", "This STT provider requires an API key", {"field": "bearer_token"})
    inspection = inspect_stt_contract(
        db,
        actor,
        SttInspectRequest(
            team_id=team.id,
            provider_preset=provider_preset,
            adapter_kind=adapter_kind,
            base_url=base_url,
            openapi_path=payload.openapi_path,
            bearer_token=bearer_token,
        ),
    )
    label = (payload.label or "").strip() or (target.label if target is not None else default_stt_config_label(provider_display_name=preset.display_name, team_name=team.name))
    if target is None:
        _ensure_unique_stt_config_label(db, team_id=team.id, label=label)
    fingerprint = _credential_fingerprint(bearer_token)
    duplicate = _duplicate_stt_config(db, team_id=team.id, adapter_kind=inspection.adapter_kind, base_url=inspection.base_url, fingerprint=fingerprint)
    if duplicate is not None and target is not None and duplicate.id == target.id:
        duplicate = None
    if duplicate is not None:
        raise AppError(
            409,
            "provider_credential_duplicate_warning",
            "A saved STT provider for this team, adapter, endpoint, and credential already exists.",
            {"duplicate_config_id": str(duplicate.id), "team_id": str(team.id), "provider_type": "stt"},
        )
    status = _inspection_status(inspection, had_secret=bool(bearer_token)) if bearer_token else ProviderCredentialStatus.unknown
    config = TeamSttConfig(
        id=uuid4(),
        team_id=team.id,
        revision_of_config_id=target.id if target is not None else None,
        label=label,
        provider_preset=provider_preset,
        adapter_kind=inspection.adapter_kind,
        base_url=inspection.base_url,
        transcribe_path=inspection.transcribe_path,
        auth_mode=SttAuthMode.bearer if preset.requires_api_key or payload.bearer_token else SttAuthMode.none,
        model_name=None,
        model_field_name=inspection.model_field_name,
        available_models_json=list(inspection.available_models),
        file_field_name=inspection.file_field_name,
        language=None,
        language_field_name=inspection.language_field_name,
        response_text_path=inspection.response_text_path,
        segments_path=inspection.segments_path,
        segment_text_field=inspection.segment_text_field,
        segment_start_field=inspection.segment_start_field,
        segment_end_field=inspection.segment_end_field,
        segment_speaker_field=inspection.segment_speaker_field,
        extra_form_fields_json=inspection.extra_form_fields_json,
        vault_secret_ref="pending" if bearer_token else "",
        credential_status=status,
        credential_fingerprint=fingerprint,
        inspection_metadata_json=_status_metadata_from_preset_inspection(inspection, provider_preset=provider_preset, provider_display_name=preset.display_name, status=status),
        setup_status=SttConfigSetupStatus.pending_model_selection,
        is_active=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(config)
    written_secret_ref = ""
    try:
        db.flush()
        if bearer_token:
            if inherits_bearer_token:
                written_secret_ref = write_team_stt_bearer_token(
                    team_id=team.id,
                    config_id=config.id,
                    bearer_token=bearer_token,
                    secret_id=uuid4(),
                )
            else:
                written_secret_ref = write_team_stt_bearer_token(
                    team_id=team.id,
                    config_id=config.id,
                    bearer_token=bearer_token,
                )
            config.vault_secret_ref = written_secret_ref
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if written_secret_ref:
            queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=written_secret_ref)
        _raise_stt_label_conflict_if_needed(exc)
        raise
    except Exception:
        db.rollback()
        if written_secret_ref:
            queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=written_secret_ref)
        raise
    db.refresh(config)
    _record_stt_audit(db, action="stt_config_draft_created", actor=actor, team_id=team.id, config_id=config.id, credential_present=bool(config.vault_secret_ref))
    return config, inspection


def finalize_stt_config_draft(db: Session, actor: User, payload: SttConfigFinalize) -> TeamSttConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == payload.config_id, TeamSttConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "STT config not found", {"resource": "stt_config", "config_id": str(payload.config_id)})
    target = None
    if config.revision_of_config_id is not None:
        target = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == config.revision_of_config_id, TeamSttConfig.team_id == team.id, TeamSttConfig.revision_of_config_id.is_(None)).with_for_update())
        if target is None:
            raise AppError(409, "conflict", "STT revision target is unavailable")
    job_config_id = target.id if target is not None else config.id
    if _stt_config_has_in_flight_jobs(db, config_id=job_config_id):
        raise AppError(409, "conflict", "Cannot edit this STT config while transcription jobs are queued or processing", {"config_id": str(config.id)})
    label = payload.label.strip()
    _ensure_unique_stt_config_label(db, team_id=team.id, label=label, current_config_id=target.id if target is not None else config.id)
    model_name = payload.model_name.strip() if payload.model_name else None
    if config.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        model_name = _validated_elevenlabs_model(model_name)
    provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)
    config.provider_preset = provider_preset
    config.extra_form_fields_json = _normalize_deepgram_extra_query_params(
        config.extra_form_fields_json,
        provider_preset=provider_preset,
        adapter_kind=config.adapter_kind,
        base_url=config.base_url,
        reject_explicit_non_true=True,
    )
    available = list(config.available_models_json or [])
    if available and model_name and model_name not in available:
        raise AppError(422, "business_rule_violation", "Selected model is not available for this provider", {"field": "model_name"})
    if model_name and not available:
        available = [model_name]
    config.label = label
    config.model_name = model_name
    config.available_models_json = available
    config.language = normalize_stt_language(payload.language)
    config.setup_status = SttConfigSetupStatus.ready
    config.is_active = payload.is_active
    config.updated_by_user_id = actor.id
    result = config
    old_secret_ref = ""
    revision_secret_ref = ""
    rebound_secret_ref = ""
    if target is not None:
        old_secret_ref = target.vault_secret_ref
        if config.vault_secret_ref and config.vault_secret_ref != old_secret_ref:
            revision_secret_ref = config.vault_secret_ref
            try:
                bearer_token = read_team_stt_bearer_token(
                    team_id=team.id,
                    config_id=config.id,
                    secret_ref=revision_secret_ref,
                )
                rebound_secret_ref = write_team_stt_bearer_token(
                    team_id=team.id,
                    config_id=target.id,
                    bearer_token=bearer_token,
                    secret_id=uuid4(),
                )
            except Exception:
                db.rollback()
                raise
    try:
        if rebound_secret_ref:
            config.vault_secret_ref = rebound_secret_ref
        if target is not None:
            editable = ("label", "provider_preset", "adapter_kind", "base_url", "transcribe_path", "auth_mode", "model_name", "model_field_name", "available_models_json", "file_field_name", "language", "language_field_name", "response_text_path", "segments_path", "segment_text_field", "segment_start_field", "segment_end_field", "segment_speaker_field", "extra_form_fields_json", "vault_secret_ref", "credential_status", "credential_fingerprint", "inspection_metadata_json", "setup_status", "is_active")
            for field in editable:
                setattr(target, field, getattr(config, field))
            target.updated_by_user_id = actor.id
            db.delete(config)
            result = target
        db.add(result)
        if target is not None:
            queue_provider_secret_cleanup(
                db,
                kind=ProviderSecretCleanupKind.stt,
                secret_refs=[
                    secret_ref
                    for secret_ref in (old_secret_ref, revision_secret_ref)
                    if secret_ref and secret_ref != result.vault_secret_ref
                ],
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if rebound_secret_ref:
            queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=rebound_secret_ref)
        _raise_stt_label_conflict_if_needed(exc)
        raise
    except Exception:
        db.rollback()
        if rebound_secret_ref:
            queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=rebound_secret_ref)
        raise
    db.refresh(result)
    _record_stt_audit(db, action="stt_config_finalized", actor=actor, team_id=team.id, config_id=result.id, setup_status=_enum_value(result.setup_status), active=result.is_active)
    return result


def replace_stt_config_draft_credential(db: Session, actor: User, payload: SttConfigDraftReplaceCredential) -> tuple[TeamSttConfig, SttInspectResult]:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == payload.config_id, TeamSttConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "STT config not found", {"resource": "stt_config", "config_id": str(payload.config_id)})
    if _stt_config_has_in_flight_jobs(db, config_id=config.id):
        raise AppError(409, "conflict", "Cannot edit this STT config while transcription jobs are queued or processing", {"config_id": str(config.id)})
    provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)
    preset = get_stt_provider_preset(provider_preset)
    inspection = inspect_stt_contract(
        db,
        actor,
        SttInspectRequest(
            team_id=team.id,
            provider_preset=provider_preset,
            adapter_kind=config.adapter_kind,
            base_url=config.base_url,
            bearer_token=payload.bearer_token,
        ),
    )
    old_secret_ref = config.vault_secret_ref
    new_secret_ref = write_team_stt_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token, secret_id=uuid4())
    try:
        config.vault_secret_ref = new_secret_ref
        config.credential_fingerprint = _credential_fingerprint(payload.bearer_token)
        config.credential_status = _inspection_status(inspection, had_secret=True)
        config.available_models_json = list(inspection.available_models)
        config.provider_preset = provider_preset
        config.extra_form_fields_json = _normalize_deepgram_extra_query_params(
            config.extra_form_fields_json,
            provider_preset=provider_preset,
            adapter_kind=config.adapter_kind,
            base_url=config.base_url,
            reject_explicit_non_true=True,
        )
        if config.model_name and inspection.available_models and config.model_name not in inspection.available_models:
            config.model_name = None
        config.inspection_metadata_json = _status_metadata_from_preset_inspection(inspection, provider_preset=provider_preset, provider_display_name=preset.display_name, status=config.credential_status)
        config.setup_status = SttConfigSetupStatus.pending_model_selection
        config.is_active = False
        config.updated_by_user_id = actor.id
        db.add(config)
        if old_secret_ref and old_secret_ref != new_secret_ref:
            queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.stt, secret_refs=[old_secret_ref])
        db.commit()
    except Exception:
        db.rollback()
        queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=new_secret_ref)
        raise
    db.refresh(config)
    _record_stt_audit(db, action="stt_config_credential_replaced", actor=actor, team_id=team.id, config_id=config.id, credential_status=_enum_value(config.credential_status))
    return config, inspection


def upsert_stt_config(db: Session, actor: User, payload: SttConfigUpsert) -> TeamSttConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    provider_preset = _resolve_stt_provider_preset_for_admin_write(payload.provider_preset, payload.adapter_kind, payload.base_url)
    _validate_stt_auth_mode_for_adapter(adapter_kind=payload.adapter_kind, auth_mode=payload.auth_mode)
    if provider_preset == SttProviderPreset.deepgram.value and payload.adapter_kind is not SttAdapterKind.generic_rest:
        raise AppError(422, "business_rule_violation", "Deepgram STT must use the Deepgram generic REST contract", {"field": "adapter_kind"})
    extra_form_fields_json = _normalize_deepgram_extra_query_params(
        payload.extra_form_fields_json,
        provider_preset=provider_preset,
        adapter_kind=payload.adapter_kind,
        base_url=payload.base_url,
        reject_explicit_non_true=True,
    )
    _ensure_unique_stt_config_label(db, team_id=team.id, label=payload.label, current_config_id=payload.config_id)
    config = None
    if payload.config_id is not None:
        config = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == payload.config_id, TeamSttConfig.team_id == team.id))
        if config is None:
            raise AppError(404, "not_found", "STT config not found", {"resource": "stt_config", "config_id": str(payload.config_id)})
        if _stt_config_has_in_flight_jobs(db, config_id=config.id):
            raise AppError(
                409,
                "conflict",
                "Cannot edit this STT config while transcription jobs are queued or processing",
                {"config_id": str(config.id)},
            )
    creating = config is None
    old_secret_ref = config.vault_secret_ref if config is not None else ""
    pending_secret_ref = ""
    replacing_secret = payload.credential_action == "replace" or bool(payload.bearer_token)
    removing_secret = payload.credential_action == "remove"
    if removing_secret and payload.bearer_token:
        raise AppError(422, "business_rule_violation", "Bearer token cannot be supplied when credential_action is remove", {"field": "credential_action"})
    if replacing_secret and not payload.bearer_token:
        raise AppError(422, "business_rule_violation", "Bearer token is required when credential_action is replace", {"field": "bearer_token"})

    fingerprint = _credential_fingerprint(payload.bearer_token if replacing_secret else None)
    duplicate = _duplicate_stt_config(
        db,
        team_id=team.id,
        adapter_kind=payload.adapter_kind,
        base_url=payload.base_url,
        fingerprint=fingerprint,
        exclude_config_id=payload.config_id,
    )
    if duplicate is not None and not payload.confirm_duplicate:
        raise AppError(
            409,
            "provider_credential_duplicate_warning",
            "A saved STT provider for this team, adapter, endpoint, and credential already exists. Confirm duplicate to save anyway.",
            {"duplicate_config_id": str(duplicate.id), "team_id": str(team.id), "provider_type": "stt"},
        )
    if payload.adapter_kind is SttAdapterKind.openai_cloud and config is None and not replacing_secret:
        raise AppError(422, "business_rule_violation", "Bearer token is required for OpenAI Cloud STT configs", {"field": "bearer_token"})
    if payload.adapter_kind is SttAdapterKind.openai_cloud and removing_secret:
        raise AppError(422, "business_rule_violation", "OpenAI Cloud STT configs require a saved bearer token", {"field": "credential_action"})
    if payload.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text and config is None and not replacing_secret:
        raise AppError(422, "business_rule_violation", "API key is required for ElevenLabs STT configs", {"field": "bearer_token"})
    if payload.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text and removing_secret:
        raise AppError(422, "business_rule_violation", "ElevenLabs STT configs require a saved API key", {"field": "credential_action"})

    available_models_json: list[str] = []
    if payload.adapter_kind is SttAdapterKind.openai_cloud:
        if replacing_secret and payload.bearer_token:
            try:
                available_models_json = _list_openai_transcription_models(api_key=payload.bearer_token, base_url=payload.base_url)
            except AppError:
                available_models_json = _fallback_openai_transcription_models()
        elif config is not None:
            available_models_json = list(config.available_models_json or [])
    elif payload.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        _validated_elevenlabs_model(payload.model_name)
        available_models_json = list(ELEVENLABS_PREFERRED_STT_MODELS)

    if config is None:
        config = TeamSttConfig(
            id=uuid4(),
            team_id=team.id,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
            label=payload.label.strip(),
            provider_preset=provider_preset,
            adapter_kind=payload.adapter_kind,
            base_url=payload.base_url,
            transcribe_path=payload.transcribe_path,
            auth_mode=payload.auth_mode,
            model_name=payload.model_name.strip() if payload.model_name else None,
            model_field_name=payload.model_field_name or ("model" if payload.model_name else None),
            available_models_json=available_models_json,
            file_field_name=payload.file_field_name.strip(),
            language=normalize_stt_language(payload.language),
            language_field_name=payload.language_field_name or ("language" if payload.language else None),
            response_text_path=payload.response_text_path.strip(),
            segments_path=payload.segments_path,
            segment_text_field=payload.segment_text_field,
            segment_start_field=payload.segment_start_field,
            segment_end_field=payload.segment_end_field,
            segment_speaker_field=payload.segment_speaker_field,
            extra_form_fields_json=extra_form_fields_json,
            vault_secret_ref="pending" if replacing_secret or payload.adapter_kind is SttAdapterKind.openai_cloud else "",
            credential_status=ProviderCredentialStatus.pending_inspection if replacing_secret else ProviderCredentialStatus.unknown,
            credential_fingerprint=fingerprint,
            inspection_metadata_json={},
            setup_status=SttConfigSetupStatus.ready,
            is_active=payload.is_active,
        )
        db.add(config)
        db.flush()
    else:
        config.label = payload.label.strip()
        config.provider_preset = provider_preset
        config.adapter_kind = payload.adapter_kind
        config.base_url = payload.base_url
        config.transcribe_path = payload.transcribe_path
        config.auth_mode = payload.auth_mode
        config.model_name = payload.model_name.strip() if payload.model_name else None
        config.model_field_name = payload.model_field_name or ("model" if payload.model_name else None)
        config.available_models_json = available_models_json or list(config.available_models_json or [])
        config.file_field_name = payload.file_field_name.strip()
        config.language = normalize_stt_language(payload.language)
        config.language_field_name = payload.language_field_name or ("language" if payload.language else None)
        config.response_text_path = payload.response_text_path.strip()
        config.segments_path = payload.segments_path
        config.segment_text_field = payload.segment_text_field
        config.segment_start_field = payload.segment_start_field
        config.segment_end_field = payload.segment_end_field
        config.segment_speaker_field = payload.segment_speaker_field
        config.extra_form_fields_json = extra_form_fields_json
        config.is_active = payload.is_active
        config.setup_status = SttConfigSetupStatus.ready
        config.updated_by_user_id = actor.id
        if fingerprint:
            config.credential_fingerprint = fingerprint
        db.add(config)

    delete_after_commit = False
    delete_secret_ref = ""
    if replacing_secret and payload.bearer_token:
        config.credential_status = ProviderCredentialStatus.pending_inspection
    elif removing_secret:
        if config.vault_secret_ref:
            delete_after_commit = True
            delete_secret_ref = config.vault_secret_ref
        config.vault_secret_ref = ""
        config.credential_fingerprint = None
        config.credential_status = ProviderCredentialStatus.unknown
        config.inspection_metadata_json = {
            "status": ProviderCredentialStatus.unknown.value,
            "reason": "credential_removed",
        }
    elif payload.adapter_kind in {SttAdapterKind.generic_rest, SttAdapterKind.openai_compatible_rest} and creating:
        config.vault_secret_ref = ""
        config.credential_fingerprint = None
        config.credential_status = ProviderCredentialStatus.unknown
    elif payload.adapter_kind is SttAdapterKind.openai_cloud and not config.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "Bearer token is required for OpenAI Cloud STT configs", {"field": "bearer_token"})
    elif payload.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text and not config.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "API key is required for ElevenLabs STT configs", {"field": "bearer_token"})

    if replacing_secret and payload.bearer_token:
        try:
            if payload.adapter_kind in {SttAdapterKind.generic_rest, SttAdapterKind.openai_compatible_rest}:
                _verify_generic_stt_config_with_sample(db, config, bearer_token=payload.bearer_token)
                inspection = None
            elif payload.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
                inspection = inspect_stt_contract(
                    db,
                    actor,
                    SttInspectRequest(
                        team_id=team.id,
                        provider_preset=SttProviderPreset.elevenlabs,
                        adapter_kind=payload.adapter_kind,
                        base_url=payload.base_url,
                        bearer_token=payload.bearer_token,
                    ),
                )
            else:
                inspection = inspect_stt_contract(
                    db,
                    actor,
                    SttInspectRequest(team_id=team.id, adapter_kind=payload.adapter_kind, base_url=payload.base_url, bearer_token=payload.bearer_token),
                )
        except AppError as exc:
            if _is_credential_rejection(exc):
                failed_config_id = config.id
                db.rollback()
                if creating:
                    try:
                        delete_team_stt_bearer_token(team_id=team.id, config_id=failed_config_id)
                    except AppError as cleanup_exc:
                        logger.warning("stt_config_secret_cleanup_failed", extra={"config_id": str(failed_config_id), "team_id": str(team.id), "error_code": cleanup_exc.code})
                raise AppError(422, "provider_credential_invalid", "STT provider rejected the supplied credential", {"provider_type": "stt"}) from exc
            config.credential_status = ProviderCredentialStatus.partial
            config.inspection_metadata_json = {"status": "partial", "warning": exc.message, "error_code": exc.code}
        else:
            if inspection is None:
                config.credential_status = ProviderCredentialStatus.verified
                config.inspection_metadata_json = {
                    "status": ProviderCredentialStatus.verified.value,
                    "adapter_kind": config.adapter_kind.value,
                    "candidate_paths": [config.transcribe_path],
                    "sample_test": "passed",
                }
            elif inspection.available_models:
                config.available_models_json = list(inspection.available_models)
                config.credential_status = _inspection_status(inspection, had_secret=True)
                config.inspection_metadata_json = _status_metadata_from_inspection(inspection, status=config.credential_status)
            else:
                config.credential_status = _inspection_status(inspection, had_secret=True)
                config.inspection_metadata_json = _status_metadata_from_inspection(inspection, status=config.credential_status)
    try:
        if replacing_secret and payload.bearer_token:
            pending_secret_ref = write_team_stt_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token, secret_id=None if creating else uuid4())
            config.vault_secret_ref = pending_secret_ref
            config.credential_fingerprint = fingerprint
        if delete_after_commit:
            queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.stt, secret_refs=[delete_secret_ref])
        elif pending_secret_ref and old_secret_ref and old_secret_ref != pending_secret_ref:
            queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.stt, secret_refs=[old_secret_ref])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if pending_secret_ref:
            queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=pending_secret_ref)
        _raise_stt_label_conflict_if_needed(exc)
        raise
    except Exception:
        db.rollback()
        if pending_secret_ref:
            queue_orphan_provider_secret_after_rollback(db, kind=ProviderSecretCleanupKind.stt, secret_ref=pending_secret_ref)
        raise
    db.refresh(config)
    _record_stt_audit(
        db,
        action="stt_config_created" if creating else "stt_config_updated",
        actor=actor,
        team_id=team.id,
        config_id=config.id,
        credential_action=payload.credential_action,
        credential_status=_enum_value(config.credential_status),
        setup_status=_enum_value(config.setup_status),
        active=config.is_active,
    )
    return config


def reinspect_stt_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> TeamSttConfig:
    config = get_stt_config(db, actor, config_id=config_id, team_id=team_id)
    bearer_token = _read_saved_stt_bearer_token(team_id=config.team_id, config=config)
    provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)
    try:
        inspection = inspect_stt_contract(
            db,
            actor,
            SttInspectRequest(
                team_id=config.team_id,
                provider_preset=provider_preset,
                adapter_kind=config.adapter_kind,
                base_url=config.base_url,
                bearer_token=bearer_token,
            ),
        )
    except AppError as exc:
        if _is_credential_rejection(exc):
            config.credential_status = ProviderCredentialStatus.invalid
            config.inspection_metadata_json = {"status": "invalid", "error_code": exc.code, "warning": exc.message}
            _clear_stt_selections_for_config(db, config_id=config.id)
        else:
            config.credential_status = ProviderCredentialStatus.degraded
            config.inspection_metadata_json = {"status": "degraded", "error_code": exc.code, "warning": exc.message}
        config.updated_by_user_id = actor.id
        db.add(config)
        db.commit()
        db.refresh(config)
        return config

    _apply_stt_inspection_to_config(config, inspection)
    config.credential_status = _inspection_status(inspection, had_secret=bool(bearer_token))
    config.inspection_metadata_json = _status_metadata_from_inspection(inspection, status=config.credential_status)
    config.updated_by_user_id = actor.id
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def _resolve_openapi_pointer(document: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise AppError(422, "business_rule_violation", "Only local OpenAPI references are supported")
    current: Any = document
    for part in ref[2:].split("/"):
        if not isinstance(current, dict) or part not in current:
            raise AppError(422, "business_rule_violation", "OpenAPI document contains an invalid local reference")
        current = current[part]
    if not isinstance(current, dict):
        raise AppError(422, "business_rule_violation", "OpenAPI reference did not resolve to an object")
    return current


def _dereference(document: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            return _dereference(document, _resolve_openapi_pointer(document, value["$ref"]))
        return {key: _dereference(document, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dereference(document, item) for item in value]
    return value


def _select_operation(document: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        operation = path_item.get("post")
        if not isinstance(operation, dict):
            continue
        resolved_operation = _dereference(document, operation)
        request_body = _dereference(document, resolved_operation.get("requestBody") or {})
        content = request_body.get("content") or {}
        media_types = [media_type.lower() for media_type in content.keys()]
        if not media_types:
            continue
        score = 0
        lowered_path = path.lower()
        for keyword in ("transcribe", "transcription", "audio", "asr", "stt", "speech", "whisper"):
            if keyword in lowered_path:
                score += 3
        if "multipart/form-data" in media_types:
            score += 6
        if any(media.startswith("audio/") for media in media_types):
            score += 2
        if "application/json" in media_types:
            score += 1
        if "summary" in resolved_operation:
            for keyword in ("transcribe", "transcription", "speech", "audio"):
                if keyword in str(resolved_operation["summary"]).lower():
                    score += 1
        candidates.append((score, path, resolved_operation))
    if not candidates:
        raise AppError(422, "business_rule_violation", "No candidate STT POST operation was found in the OpenAPI document")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected_score, selected_path, selected_operation = candidates[0]
    candidate_paths = [path for _, path, _ in candidates[:5]]
    if selected_score <= 0:
        raise AppError(422, "business_rule_violation", "OpenAPI document did not contain a recognizable STT endpoint")
    return selected_path, selected_operation, candidate_paths


def _request_schema_for_operation(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    request_body = _dereference(document, operation.get("requestBody") or {})
    content = request_body.get("content") or {}
    schema = None
    if "multipart/form-data" in content:
        schema = content["multipart/form-data"].get("schema")
    elif "application/json" in content:
        schema = content["application/json"].get("schema")
    if not schema:
        raise AppError(422, "business_rule_violation", "The candidate STT endpoint did not expose a supported request schema")
    resolved = _dereference(document, schema)
    if not isinstance(resolved, dict):
        raise AppError(422, "business_rule_violation", "The candidate STT request schema was invalid")
    return resolved


def _pick_property_value(properties: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        for candidate_key in ("default", "example"):
            value = prop.get(candidate_key)
            if value is not None:
                return str(value)
        enum_values = prop.get("enum")
        if isinstance(enum_values, list) and enum_values:
            return str(enum_values[0])
    return None


def _pick_property_name(properties: dict[str, Any], preferred_names: tuple[str, ...]) -> str | None:
    lowered = {key.lower(): key for key in properties}
    for preferred in preferred_names:
        if preferred.lower() in lowered:
            return lowered[preferred.lower()]
    return None


def _property_description(properties: dict[str, Any], key: str) -> str | None:
    prop = properties.get(key)
    if not isinstance(prop, dict):
        return None
    description = prop.get("description")
    return str(description) if description else None


def _infer_file_field_name(properties: dict[str, Any]) -> str:
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        if prop.get("type") == "string" and prop.get("format") == "binary":
            return name
    for preferred in ("file", "audio", "audio_file", "upload"):
        if preferred in properties:
            return preferred
    return "file"


def _response_schema_for_operation(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    responses = _dereference(document, operation.get("responses") or {})
    success = None
    for key in ("200", "201", "202", "default"):
        candidate = responses.get(key)
        if isinstance(candidate, dict):
            success = candidate
            break
    if success is None:
        return None
    content = success.get("content") or {}
    for media_type in ("application/json", "application/*+json"):
        if media_type in content and isinstance(content[media_type], dict):
            schema = content[media_type].get("schema")
            return _dereference(document, schema) if schema else None
    for media in content.values():
        if isinstance(media, dict) and media.get("schema"):
            return _dereference(document, media["schema"])
    return None


def _flatten_response_paths(schema: dict[str, Any], prefix: str = "") -> list[str]:
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        paths: list[str] = []
        for key, child in (schema.get("properties") or {}).items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            paths.extend(_flatten_response_paths(child, child_prefix))
        return paths or ([prefix] if prefix else [])
    if schema_type == "array":
        item_schema = schema.get("items") or {}
        item_prefix = f"{prefix}[]" if prefix else "[]"
        return _flatten_response_paths(item_schema, item_prefix)
    return [prefix] if prefix else []


def _infer_response_text_path(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "text"
    candidate_paths = _flatten_response_paths(schema)
    for preferred in ("text", "transcript", "result.text", "results.transcript", "data.text"):
        if preferred in candidate_paths:
            return preferred
    for candidate in candidate_paths:
        if candidate.endswith(".text") or candidate.endswith(".transcript"):
            return candidate
    return "text"


def _infer_segments_contract(schema: dict[str, Any] | None) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if not schema:
        return None, None, None, None, None
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return None, None, None, None, None
    segments_path = _pick_property_name(properties, ("segments", "results", "words", "utterances"))
    if not segments_path:
        return None, None, None, None, None
    segment_schema = properties.get(segments_path) or {}
    if isinstance(segment_schema, dict) and segment_schema.get("type") == "array":
        segment_schema = segment_schema.get("items") or {}
    segment_props = segment_schema.get("properties") or {} if isinstance(segment_schema, dict) else {}
    if not isinstance(segment_props, dict):
        segment_props = {}
    return (
        segments_path,
        _pick_property_name(segment_props, ("text", "transcript", "word")),
        _pick_property_name(segment_props, ("start", "start_time", "begin")),
        _pick_property_name(segment_props, ("end", "end_time", "stop")),
        _pick_property_name(segment_props, ("speaker", "speaker_id", "channel")),
    )


def inspect_stt_contract(db: Session, actor: User, payload: SttInspectRequest) -> SttInspectResult:
    _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    provider_preset = _resolve_stt_provider_preset_for_admin_write(payload.provider_preset, payload.adapter_kind, payload.base_url)
    if payload.adapter_kind is SttAdapterKind.elevenlabs_speech_to_text:
        provider_preset = SttProviderPreset.elevenlabs.value
    if provider_preset == SttProviderPreset.deepgram.value:
        preset = get_stt_provider_preset(provider_preset)
        if not payload.bearer_token:
            raise AppError(422, "business_rule_violation", "Deepgram requires an API key", {"field": "bearer_token"})
        notes = ["Discovered Deepgram STT models from /v1/models."]
        try:
            models = _list_deepgram_stt_models(api_key=payload.bearer_token, base_url=payload.base_url)
        except AppError as exc:
            if _is_credential_rejection(exc):
                raise
            models = []
            notes = [f"Deepgram model discovery failed: {exc.message}. Enter a model manually if needed."]
        default_model = _preferred_deepgram_model(models)
        return SttInspectResult(
            base_url=payload.base_url,
            openapi_path=None,
            adapter_kind=preset.adapter_kind,
            transcribe_path=preset.transcribe_path,
            model_name=default_model,
            model_field_name=preset.default_model_field_name if models else None,
            file_field_name=preset.default_file_field_name,
            language=None,
            language_field_name=preset.default_language_field_name,
            response_text_path=preset.default_response_text_path,
            segments_path=None,
            segment_text_field=None,
            segment_start_field=None,
            segment_end_field=None,
            segment_speaker_field=None,
            extra_form_fields_json=dict(preset.default_extra_form_fields or {}),
            candidate_paths=[preset.transcribe_path],
            operation_summary="Known Deepgram prerecorded transcription contract",
            available_models=models,
            available_model_options=_stt_model_options(models, source="provider"),
            field_tips=[
                SttInspectFieldTip(name=preset.default_file_field_name, role="file", default_value=None, description="Raw audio request body.", required=True),
            ],
            notes=notes,
        )
    if provider_preset == SttProviderPreset.elevenlabs.value:
        preset = get_stt_provider_preset(provider_preset)
        if not payload.bearer_token:
            raise AppError(422, "business_rule_violation", "ElevenLabs requires an API key", {"field": "bearer_token"})
        notes = ["Validated ElevenLabs API key via /v1/models."]
        try:
            catalog_models = _list_elevenlabs_stt_models(api_key=payload.bearer_token, base_url=payload.base_url)
        except AppError as exc:
            if _is_credential_rejection(exc):
                raise
            catalog_models = []
            notes = [f"ElevenLabs credential probe degraded: {exc.message}. Using built-in synchronous STT model list."]
        models = list(ELEVENLABS_PREFERRED_STT_MODELS)
        if catalog_models:
            notes.append("ElevenLabs catalog probe succeeded; selectable STT models remain the built-in synchronous list.")
        return SttInspectResult(
            base_url=payload.base_url,
            openapi_path=None,
            adapter_kind=preset.adapter_kind,
            transcribe_path=preset.transcribe_path,
            model_name=ELEVENLABS_PREFERRED_STT_MODELS[0],
            model_field_name=preset.default_model_field_name,
            file_field_name=preset.default_file_field_name,
            language=None,
            language_field_name=preset.default_language_field_name,
            response_text_path=preset.default_response_text_path,
            segments_path="words",
            segment_text_field="text",
            segment_start_field="start",
            segment_end_field="end",
            segment_speaker_field="speaker_id",
            extra_form_fields_json=dict(preset.default_extra_form_fields or {}),
            candidate_paths=[preset.transcribe_path, "/v1/models"],
            operation_summary="ElevenLabs Speech to Text",
            available_models=models,
            available_model_options=_stt_model_options(models, source="provider"),
            field_tips=[
                SttInspectFieldTip(name=preset.default_file_field_name, role="file", default_value=None, description="Audio file upload.", required=True),
            ],
            notes=[*notes, "This adapter uses OpenScribe's ElevenLabs preset; no OpenAPI fetch was required."],
        )
    if payload.adapter_kind in {SttAdapterKind.openai_cloud, SttAdapterKind.openai_compatible_rest}:
        transcribe_path, file_field_name, response_text_path = _normalized_known_adapter_fields(payload.adapter_kind)
        available_models: list[str] = []
        available_model_options: list[SttModelOption] = []
        default_model = "whisper-1"
        notes = ["This adapter uses a known fixed request contract; no OpenAPI fetch was required."]
        if payload.adapter_kind is SttAdapterKind.openai_cloud:
            if payload.bearer_token:
                try:
                    available_models = _list_openai_transcription_models(api_key=payload.bearer_token, base_url=payload.base_url)
                    available_model_options = _openai_model_options(available_models, source="fetched")
                except AppError as exc:
                    if _is_credential_rejection(exc):
                        raise
                    available_models = _fallback_openai_transcription_models()
                    available_model_options = _openai_model_options(available_models, source="default")
                    notes.append("OpenAI model discovery failed, so OpenScribe fell back to the built-in supported transcription model list.")
                except Exception:
                    available_models = _fallback_openai_transcription_models()
                    available_model_options = _openai_model_options(available_models, source="default")
                    notes.append("OpenAI model discovery failed, so OpenScribe fell back to the built-in supported transcription model list.")
            else:
                available_models = _fallback_openai_transcription_models()
                available_model_options = _openai_model_options(available_models, source="default")
                notes.append("No API key was provided; using built-in OpenAI transcription model defaults.")
            if available_models:
                default_model = available_models[0]
            notes.append("This adapter uses the official OpenAI transcription contract and loads available models through the OpenAI Python SDK.")
        else:
            available_models = _fallback_openai_transcription_models()
            available_model_options = _openai_model_options(available_models, source="default")
            default_model = available_models[0]
            notes.append("This adapter is intended for OpenAI-compatible REST transcription endpoints on custom hosts.")
        return SttInspectResult(
            base_url=payload.base_url,
            openapi_path=payload.openapi_path,
            adapter_kind=payload.adapter_kind,
            transcribe_path=transcribe_path,
            model_name=default_model,
            model_field_name="model",
            file_field_name=file_field_name,
            language=None,
            language_field_name="language",
            response_text_path=response_text_path,
            segments_path="segments",
            segment_text_field="text",
            segment_start_field="start",
            segment_end_field="end",
            segment_speaker_field="speaker",
            extra_form_fields_json={},
            candidate_paths=[transcribe_path],
            operation_summary="Known OpenAI transcription contract",
            available_models=available_models,
            available_model_options=available_model_options,
            field_tips=[
                SttInspectFieldTip(name="file", role="file", default_value=None, description="Audio file upload.", required=True),
                SttInspectFieldTip(name="model", role="model", default_value=default_model, description="Model to use.", required=True),
                SttInspectFieldTip(name="language", role="language", default_value=None, description="Optional language code.", required=False),
            ],
            notes=notes,
        )

    document, resolved_openapi_path = fetch_openapi_document(
        base_url=payload.base_url,
        candidate_paths=_candidate_stt_openapi_paths(payload.openapi_path),
        bearer_token=payload.bearer_token,
    )
    document = dereference_openapi_document(document)

    transcribe_path, operation, candidate_paths = _select_operation(document)
    request_schema = _request_schema_for_operation(document, operation)
    properties = request_schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    required_fields = {str(item) for item in (request_schema.get("required") or []) if isinstance(item, str)}

    file_field_name = _infer_file_field_name(properties)
    model_field_name = _pick_property_name(properties, ("model", "model_id", "model_name", "engine", "deployment", "deployment_id"))
    language_field_name = _pick_property_name(properties, ("language", "lang", "locale", "language_code", "languageCode"))
    model_name = _pick_property_value(properties, model_field_name) if model_field_name else None
    language = _pick_property_value(properties, language_field_name) if language_field_name else None
    extra_fields: dict[str, str] = {}
    for key, prop in properties.items():
        if key in {file_field_name, model_field_name, language_field_name} or not isinstance(prop, dict):
            continue
        value = _pick_property_value({key: prop}, key)
        if value is not None:
            extra_fields[key] = value

    response_schema = _response_schema_for_operation(document, operation)
    response_text_path = _infer_response_text_path(response_schema)
    segments_path, segment_text_field, segment_start_field, segment_end_field, segment_speaker_field = _infer_segments_contract(response_schema)
    adapter_kind = SttAdapterKind.generic_rest
    if transcribe_path == "/v1/audio/transcriptions" and file_field_name == "file" and ("model" in properties or "model_name" in properties):
        adapter_kind = SttAdapterKind.openai_compatible_rest
    field_tips: list[SttInspectFieldTip] = [
        SttInspectFieldTip(
            name=file_field_name,
            role="file",
            default_value=_pick_property_value(properties, file_field_name),
            description=_property_description(properties, file_field_name),
            required=file_field_name in required_fields,
        )
    ]
    if model_field_name:
        field_tips.append(
            SttInspectFieldTip(
                name=model_field_name,
                role="model",
                default_value=model_name,
                description=_property_description(properties, model_field_name),
                required=model_field_name in required_fields,
            )
        )
    if language_field_name:
        field_tips.append(
            SttInspectFieldTip(
                name=language_field_name,
                role="language",
                default_value=language,
                description=_property_description(properties, language_field_name),
                required=language_field_name in required_fields,
            )
        )
    for key in sorted(extra_fields):
        field_tips.append(
            SttInspectFieldTip(
                name=key,
                role="extra",
                default_value=extra_fields[key],
                description=_property_description(properties, key),
                required=key in required_fields,
            )
        )
    notes: list[str] = []
    if len(candidate_paths) > 1:
        notes.append("Multiple candidate POST endpoints were found; the highest-ranked one was selected.")
    if not model_name:
        notes.append("No model field default was found; set it manually if your provider requires one.")
    if file_field_name == "file" and "file" not in properties:
        notes.append("The file field name could not be inferred confidently; review it before saving.")
    if adapter_kind is SttAdapterKind.openai_compatible_rest:
        notes.append("This endpoint matches the OpenAI-compatible REST transcription adapter family.")

    return SttInspectResult(
        base_url=payload.base_url,
        openapi_path=resolved_openapi_path,
        adapter_kind=adapter_kind,
        transcribe_path=transcribe_path,
        model_name=model_name,
        model_field_name=model_field_name,
        file_field_name=file_field_name,
        language=language,
        language_field_name=language_field_name,
        response_text_path=response_text_path,
        segments_path=segments_path,
        segment_text_field=segment_text_field,
        segment_start_field=segment_start_field,
        segment_end_field=segment_end_field,
        segment_speaker_field=segment_speaker_field,
        extra_form_fields_json=extra_fields,
        candidate_paths=candidate_paths,
        operation_summary=operation.get("summary"),
        available_models=[],
        available_model_options=[],
        field_tips=field_tips,
        notes=notes,
    )
