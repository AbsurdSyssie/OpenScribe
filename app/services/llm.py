import logging
from uuid import UUID, uuid4

import httpx
from openai import APIStatusError, AuthenticationError, OpenAI, PermissionDeniedError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import GeneratedDocument, GeneratedDocumentStatus, LlmAdapterKind, LlmAuthMode, LlmConfigSetupStatus, LlmProviderPreset, Team, TeamHallucinationCheckSelection, TeamLlmConfig, TeamLlmSelection, TeamRole, User, UserLlmPreference, utcnow
from app.schemas import HallucinationCheckSelectionUpsert, LlmConfigDraftCreate, LlmConfigDraftReplaceCredential, LlmConfigFinalize, LlmConfigInspectResult, LlmConfigUpsert, LlmInspectRequest, LlmModelOption, LlmSelectionUpsert, UserLlmPreferenceUpsert
from app.services.llm_presets import (
    apply_provider_defaults,
    default_llm_config_label,
    filter_discovered_models,
    get_llm_provider_preset,
    infer_llm_provider_preset,
    reclassify_preset_for_base_url,
)
from app.services.security_audit import record_security_event
from app.services.vault import delete_team_llm_bearer_token, read_team_llm_bearer_token, write_team_llm_bearer_token


logger = logging.getLogger("openscribe.llm")


def _record_llm_audit(db: Session, *, action: str, actor: User, team_id: UUID, config_id: UUID | None = None, outcome: str = "success", **details) -> None:
    payload = {"category": "provider", "outcome": outcome, "provider_type": "llm"}
    if config_id is not None:
        payload.update({"object_type": "team_llm_config", "object_id": str(config_id)})
    payload.update(details)
    record_security_event(db, action=action, actor=actor, team_id=team_id, details=payload)


def _enum_value(value):
    return getattr(value, "value", value)


def _list_openai_compatible_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models_page = client.models.list()
    except (AuthenticationError, PermissionDeniedError) as exc:  # pragma: no cover
        raise AppError(
            401,
            "llm_invalid_credential",
            "The API key was rejected by the provider.",
            {"provider_status": getattr(exc, "status_code", None)},
        ) from exc
    except APIStatusError as exc:  # pragma: no cover
        if exc.status_code in {401, 403}:
            raise AppError(
                401,
                "llm_invalid_credential",
                "The API key was rejected by the provider.",
                {"provider_status": exc.status_code},
            ) from exc
        raise AppError(
            502,
            "llm_inspection_failed",
            "Could not load available models from the provider.",
            {"provider_status": exc.status_code},
        ) from exc
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available models from the provider.") from exc

    models: set[str] = set()
    for model in getattr(models_page, "data", []):
        model_id = getattr(model, "id", None)
        if isinstance(model_id, str) and model_id.strip():
            models.add(model_id.strip())
    return sorted(models)


def _resolve_team(db: Session, *, team_id: UUID) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(team_id)})
    return team


def _resolve_admin_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin LLM provisioning access required")
    if team_id is None:
        raise AppError(422, "business_rule_violation", "Team is required for system-admin LLM management", {"field": "team_id"})
    return _resolve_team(db, team_id=team_id)


def _resolve_selection_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if actor.is_system_admin:
        if team_id is None:
            raise AppError(422, "business_rule_violation", "Team is required for LLM selection management", {"field": "team_id"})
        return _resolve_team(db, team_id=team_id)
    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "LLM selection access required")
    if team_id is not None and team_id != actor.team_id:
        raise AppError(403, "forbidden", "Leaders may only manage LLM selection for their own team")
    return _resolve_team(db, team_id=actor.team_id)


def _resolve_user_preference_scope(actor: User) -> None:
    if actor.is_system_admin or actor.team_id is None:
        raise AppError(403, "forbidden", "User LLM preference access is restricted to normal team users")


def _ollama_headers(*, bearer_token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}


def _ollama_api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _list_openai_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        models = _list_openai_compatible_models(api_key=api_key, base_url=base_url)
    except AppError as exc:  # pragma: no cover
        if exc.code == "llm_invalid_credential":
            raise
        raise AppError(502, "llm_inspection_failed", "Could not load available OpenAI chat models") from exc

    return filter_discovered_models(LlmProviderPreset.openai.value, models)


def _list_mistral_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:  # pragma: no cover
        if exc.response.status_code in {401, 403}:
            raise AppError(
                401,
                "llm_invalid_credential",
                "The API key was rejected by the provider.",
                {"provider_status": exc.response.status_code},
            ) from exc
        raise AppError(502, "llm_inspection_failed", "Could not load available Mistral chat models", {"provider_status": exc.response.status_code}) from exc
    except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Mistral chat models") from exc

    records = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        records = []
    models: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        capabilities = item.get("capabilities") or {}
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        if item.get("archived") is True:
            continue
        if isinstance(capabilities, dict) and capabilities.get("completion_chat") is True:
            models.add(model_id.strip())
    return sorted(models)


def _list_together_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:  # pragma: no cover
        if exc.response.status_code in {401, 403}:
            raise AppError(
                401,
                "llm_invalid_credential",
                "The API key was rejected by the provider.",
                {"provider_status": exc.response.status_code},
            ) from exc
        raise AppError(502, "llm_inspection_failed", "Could not load available Together AI chat models", {"provider_status": exc.response.status_code}) from exc
    except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Together AI chat models") from exc

    records = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        records = []

    models: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        model_type = item.get("type")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        if model_type in {"chat", "language", "code"}:
            models.add(model_id.strip())
    return sorted(models)


def _list_openai_compatible_chat_models(*, provider_preset: str, api_key: str, base_url: str) -> list[str]:
    if provider_preset == LlmProviderPreset.openai.value:
        return _list_openai_chat_models(api_key=api_key, base_url=base_url)
    if provider_preset == LlmProviderPreset.mistral.value:
        return _list_mistral_chat_models(api_key=api_key, base_url=base_url)
    if provider_preset == LlmProviderPreset.together.value:
        return _list_together_chat_models(api_key=api_key, base_url=base_url)
    try:
        models = _list_openai_compatible_models(api_key=api_key, base_url=base_url)
    except AppError as exc:  # pragma: no cover
        if exc.code == "llm_invalid_credential":
            raise
        raise AppError(502, "llm_inspection_failed", "Could not load available OpenAI-compatible chat models") from exc
    return filter_discovered_models(provider_preset, models)


def _list_bedrock_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        return _list_openai_compatible_models(api_key=api_key, base_url=base_url)
    except AppError as exc:  # pragma: no cover
        if exc.code == "llm_invalid_credential":
            raise
        raise AppError(502, "llm_inspection_failed", "Could not load available Amazon Bedrock chat models") from exc


def _ensure_unique_llm_config_label(db: Session, *, team_id: UUID, label: str, current_config_id: UUID | None = None) -> None:
    normalized = label.strip().lower()
    stmt = select(TeamLlmConfig.id).where(
        TeamLlmConfig.team_id == team_id,
        func.lower(func.btrim(TeamLlmConfig.label)) == normalized,
    )
    if current_config_id is not None:
        stmt = stmt.where(TeamLlmConfig.id != current_config_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise AppError(409, "conflict", "An LLM provider with this name already exists for this team.", {"field": "label"})


def _raise_llm_label_conflict_if_needed(exc: IntegrityError) -> None:
    if "uq_team_llm_configs_team_label_lower" in str(exc.orig):
        raise AppError(409, "conflict", "An LLM provider with this name already exists for this team.", {"field": "label"}) from exc


def _llm_model_options(models: list[str], *, source: str) -> list[LlmModelOption]:
    return [LlmModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


def _inspection_metadata(inspection: LlmConfigInspectResult) -> dict[str, object]:
    return {
        "provider_preset": inspection.provider_preset,
        "provider_display_name": inspection.provider_display_name,
        "discovery_status": inspection.discovery_status,
        "default_model_source": inspection.default_model_source,
        "warnings": list(inspection.warnings),
        "notes": list(inspection.notes),
        "inspected_at": utcnow().isoformat(),
    }


def _discovery_metadata(
    *,
    provider_preset: str,
    discovery_status: str,
    default_model_source: str,
    warnings: list[str],
    notes: list[str],
    manual_model_name: str | None = None,
) -> dict[str, object]:
    preset = get_llm_provider_preset(provider_preset)
    metadata: dict[str, object] = {
        "provider_preset": preset.key,
        "provider_display_name": preset.display_name,
        "discovery_status": discovery_status,
        "default_model_source": default_model_source,
        "warnings": list(warnings),
        "notes": list(notes),
        "inspected_at": utcnow().isoformat(),
    }
    if manual_model_name:
        metadata["manual_model_name"] = manual_model_name
    return metadata


def _successful_discovery_metadata(*, provider_preset: str, models: list[str], empty_warning: str, fetched_note: str | None = None) -> dict[str, object]:
    if models:
        notes = [fetched_note] if fetched_note else []
        return _discovery_metadata(provider_preset=provider_preset, discovery_status="fetched", default_model_source="provider", warnings=[], notes=notes)
    return _discovery_metadata(
        provider_preset=provider_preset,
        discovery_status="manual_required",
        default_model_source="manual",
        warnings=[empty_warning],
        notes=[],
    )


def _list_ollama_chat_models(*, base_url: str, bearer_token: str | None) -> list[str]:
    try:
        response = httpx.get(
            _ollama_api_url(base_url, "/api/tags"),
            headers=_ollama_headers(bearer_token=bearer_token),
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models") from exc

    models: list[str] = []
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        model_name = item.get("name") or item.get("model")
        if isinstance(model_name, str) and model_name.strip():
            models.append(model_name.strip())
    return sorted(set(models))


def inspect_llm_contract(db: Session, actor: User, payload) -> LlmConfigInspectResult:
    _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    preset_key, adapter_kind, base_url, _region = apply_provider_defaults(
        provider_preset=getattr(payload, "provider_preset", None),
        base_url=payload.base_url,
        bedrock_region=getattr(payload, "bedrock_region", None),
    )
    preset_key = reclassify_preset_for_base_url(preset_key, base_url)
    preset = get_llm_provider_preset(preset_key)
    adapter_kind = preset.adapter_kind
    if adapter_kind is LlmAdapterKind.openai_chat:
        if payload.bearer_token:
            try:
                models = _list_openai_compatible_chat_models(provider_preset=preset_key, api_key=payload.bearer_token, base_url=base_url)
                source = "fetched" if models else "manual"
                metadata = _successful_discovery_metadata(
                    provider_preset=preset_key,
                    models=models,
                    empty_warning="No compatible chat models were returned. Enter a model name manually.",
                )
                discovery_status = str(metadata["discovery_status"])
                default_model_source = str(metadata["default_model_source"])
                warnings = list(metadata["warnings"])
                notes: list[str] = []
            except AppError as exc:
                if exc.code == "llm_invalid_credential":
                    raise
                models = []
                source = "manual"
                discovery_status = "manual_required"
                default_model_source = "manual"
                warnings = ["Live model discovery failed. Verify the API key and endpoint, or enter a model name manually."]
                notes = []
        else:
            models = []
            source = "manual"
            discovery_status = "manual_required"
            default_model_source = "manual"
            warnings = ["Live model discovery requires an API key. Enter a model name manually or inspect again with a key."]
            notes = []
        model_options = _llm_model_options(models, source=source)
    elif adapter_kind is LlmAdapterKind.bedrock_chat:
        if payload.bearer_token:
            try:
                models = _list_bedrock_chat_models(api_key=payload.bearer_token, base_url=base_url)
                fetched_note = "OpenScribe loaded the Amazon Bedrock model list through the OpenAI-compatible Models API for the supplied Bedrock Mantle endpoint."
                source = "fetched" if models else "manual"
                metadata = _successful_discovery_metadata(
                    provider_preset=preset_key,
                    models=models,
                    empty_warning="No compatible chat models were returned. Enter a model name manually.",
                    fetched_note=fetched_note,
                )
                discovery_status = str(metadata["discovery_status"])
                default_model_source = str(metadata["default_model_source"])
                warnings = list(metadata["warnings"])
                notes = list(metadata["notes"])
            except AppError as exc:
                if exc.code == "llm_invalid_credential":
                    raise
                models = []
                source = "manual"
                discovery_status = "manual_required"
                default_model_source = "manual"
                warnings = ["Could not load region-specific Bedrock models. Enter a model ID manually or inspect again with credentials."]
                notes = [
                    "Live Amazon Bedrock model discovery failed. Verify the Bedrock Mantle base URL, region, and API key, or enter a model name manually.",
                ]
        else:
            models = []
            source = "manual"
            discovery_status = "manual_required"
            default_model_source = "manual"
            warnings = ["Could not load region-specific Bedrock models. Enter a model ID manually or inspect again with credentials."]
            notes = [
                "No Bedrock API key provided for inspection, so OpenScribe could not load the region-specific model list. Enter a model name manually or inspect again with a key.",
            ]
        model_options = _llm_model_options(models, source=source)
    elif adapter_kind is LlmAdapterKind.ollama_chat:
        try:
            models = _list_ollama_chat_models(base_url=base_url, bearer_token=payload.bearer_token)
            source = "fetched" if models else "manual"
            metadata = _successful_discovery_metadata(
                provider_preset=preset_key,
                models=models,
                empty_warning="No compatible chat models were returned. Enter a model name manually.",
            )
            discovery_status = str(metadata["discovery_status"])
            default_model_source = str(metadata["default_model_source"])
            warnings = list(metadata["warnings"])
            notes = list(metadata["notes"])
        except AppError:
            models = []
            source = "manual"
            discovery_status = "manual_required"
            default_model_source = "manual"
            warnings = ["Could not reach Ollama /api/tags. Verify the base URL and network access."]
            notes = ["Live Ollama model discovery failed. Verify the base URL and local Ollama host, or enter a model name manually."]
        model_options = _llm_model_options(models, source=source)
    else:  # pragma: no cover
        models = []
        source = "default"
        discovery_status = "failed"
        default_model_source = "none"
        requires_bearer_token = False
        supports_model_discovery = False
        warnings = ["Unsupported LLM adapter kind."]
        notes = []
        model_options = []
    model_name = models[0] if models else None
    return LlmConfigInspectResult(
        provider_preset=preset.key,
        provider_display_name=preset.display_name,
        base_url=base_url,
        adapter_kind=adapter_kind,
        model_name=model_name,
        available_models=models,
        available_model_options=model_options,
        discovery_status=discovery_status,
        default_model_source=default_model_source,
        requires_bearer_token=preset.requires_bearer_token,
        supports_model_discovery=preset.supports_model_discovery,
        warnings=warnings,
        notes=notes,
    )


def inspect_saved_llm_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> LlmConfigInspectResult:
    config = get_llm_config(db, actor, config_id=config_id, team_id=team_id)
    bearer_token = read_team_llm_bearer_token(team_id=config.team_id, config_id=config.id) if config.vault_secret_ref else None
    inspection = inspect_llm_contract(
        db,
        actor,
        LlmInspectRequest(
            team_id=config.team_id,
            provider_preset=config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url),
            adapter_kind=config.adapter_kind,
            base_url=config.base_url,
            bearer_token=bearer_token,
        ),
    )
    config.inspection_metadata_json = _inspection_metadata(inspection)
    if inspection.available_models:
        config.available_models_json = list(inspection.available_models)
        if config.model_name not in inspection.available_models:
            config.model_name = inspection.model_name
    config.updated_by_user_id = actor.id
    db.add(config)
    db.commit()
    db.refresh(config)
    return inspection


def list_llm_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamLlmConfig]:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    stmt = select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id).order_by(TeamLlmConfig.created_at.desc(), TeamLlmConfig.id.desc())
    return list(db.scalars(stmt))


def get_llm_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> TeamLlmConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config_id, TeamLlmConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(config_id)})
    return config


def _llm_config_has_in_flight_jobs(db: Session, *, config_id: UUID) -> bool:
    return db.scalar(
        select(GeneratedDocument.id).where(
            GeneratedDocument.llm_config_id == config_id,
            GeneratedDocument.status.in_([GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing]),
        ).limit(1)
    ) is not None


def create_llm_config_draft(db: Session, actor: User, payload: LlmConfigDraftCreate) -> tuple[TeamLlmConfig, LlmConfigInspectResult]:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    provider_preset, adapter_kind, base_url, region = apply_provider_defaults(
        provider_preset=payload.provider_preset,
        base_url=payload.base_url,
        bedrock_region=payload.bedrock_region,
    )
    provider_preset = reclassify_preset_for_base_url(provider_preset, base_url)
    preset = get_llm_provider_preset(provider_preset)
    adapter_kind = preset.adapter_kind
    if preset.requires_bearer_token and not payload.bearer_token:
        raise AppError(422, "business_rule_violation", "This LLM provider requires an API key", {"field": "bearer_token"})
    raw_label = (payload.label or "").strip()
    inspection = inspect_llm_contract(
        db,
        actor,
        LlmInspectRequest(
            team_id=team.id,
            provider_preset=provider_preset,
            adapter_kind=adapter_kind,
            base_url=base_url,
            bearer_token=payload.bearer_token,
            bedrock_region=region,
        ),
    )
    label = raw_label or default_llm_config_label(provider_display_name=inspection.provider_display_name, team_name=team.name)
    _ensure_unique_llm_config_label(db, team_id=team.id, label=label)
    config = TeamLlmConfig(
        id=uuid4(),
        team_id=team.id,
        label=label,
        provider_preset=inspection.provider_preset,
        adapter_kind=inspection.adapter_kind,
        base_url=inspection.base_url,
        auth_mode=LlmAuthMode.bearer if preset.requires_bearer_token or payload.bearer_token else LlmAuthMode.none,
        model_name=None,
        available_models_json=list(inspection.available_models),
        inspection_metadata_json=_inspection_metadata(inspection),
        setup_status=LlmConfigSetupStatus.pending_model_selection,
        vault_secret_ref="pending" if payload.bearer_token else "",
        is_active=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(config)
    try:
        db.flush()
        if payload.bearer_token:
            config.vault_secret_ref = write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_llm_label_conflict_if_needed(exc)
        raise
    db.refresh(config)
    _record_llm_audit(db, action="llm_config_draft_created", actor=actor, team_id=team.id, config_id=config.id, credential_present=bool(config.vault_secret_ref))
    return config, inspection


def finalize_llm_config_draft(db: Session, actor: User, payload: LlmConfigFinalize) -> TeamLlmConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(payload.config_id)})
    has_in_flight_jobs = _llm_config_has_in_flight_jobs(db, config_id=config.id)
    if has_in_flight_jobs and config.setup_status != LlmConfigSetupStatus.pending_model_selection:
        raise AppError(409, "conflict", "Cannot edit this LLM config while generated documents are queued or processing", {"config_id": str(config.id)})
    model_name = payload.model_name.strip()
    available = list(config.available_models_json or [])
    if available and model_name not in available:
        raise AppError(422, "business_rule_violation", "Selected model is not available for this provider", {"field": "model_name"})
    label = payload.label.strip()
    _ensure_unique_llm_config_label(db, team_id=team.id, label=label, current_config_id=config.id)
    metadata = dict(config.inspection_metadata_json or {})
    if not available:
        available = [model_name]
        metadata["manual_model_name"] = model_name
        metadata["discovery_status"] = "manual_required"
        metadata["default_model_source"] = "manual"
    config.label = label
    config.model_name = model_name
    config.available_models_json = available
    config.inspection_metadata_json = metadata
    config.setup_status = LlmConfigSetupStatus.ready
    config.is_active = payload.is_active
    config.updated_by_user_id = actor.id
    db.add(config)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_llm_label_conflict_if_needed(exc)
        raise
    db.refresh(config)
    _record_llm_audit(db, action="llm_config_finalized", actor=actor, team_id=team.id, config_id=config.id, setup_status=_enum_value(config.setup_status), active=config.is_active)
    return config


def replace_llm_config_draft_credential(db: Session, actor: User, payload: LlmConfigDraftReplaceCredential) -> tuple[TeamLlmConfig, LlmConfigInspectResult]:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(payload.config_id)})
    has_in_flight_jobs = _llm_config_has_in_flight_jobs(db, config_id=config.id)
    was_ready = config.setup_status == LlmConfigSetupStatus.ready
    was_active = config.is_active
    existing_model_name = config.model_name
    inspection = inspect_llm_contract(
        db,
        actor,
        LlmInspectRequest(
            team_id=team.id,
            provider_preset=config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url),
            adapter_kind=config.adapter_kind,
            base_url=config.base_url,
            bearer_token=payload.bearer_token,
        ),
    )
    if has_in_flight_jobs and was_ready and existing_model_name and existing_model_name not in inspection.available_models:
        raise AppError(
            409,
            "conflict",
            "Replacement credential does not expose the model used by queued or processing generated documents",
            {"field": "bearer_token", "model_name": existing_model_name, "config_id": str(config.id)},
        )
    config.vault_secret_ref = write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
    config.available_models_json = list(inspection.available_models)
    if config.model_name and inspection.available_models and config.model_name not in inspection.available_models:
        config.model_name = None
    config.inspection_metadata_json = _inspection_metadata(inspection)
    if has_in_flight_jobs and was_ready and existing_model_name and (not inspection.available_models or existing_model_name in inspection.available_models):
        config.model_name = existing_model_name
        config.setup_status = LlmConfigSetupStatus.ready
        config.is_active = was_active
    else:
        config.setup_status = LlmConfigSetupStatus.pending_model_selection
        config.is_active = False
    config.updated_by_user_id = actor.id
    db.add(config)
    db.commit()
    db.refresh(config)
    _record_llm_audit(db, action="llm_config_credential_replaced", actor=actor, team_id=team.id, config_id=config.id, setup_status=_enum_value(config.setup_status), active=config.is_active)
    return config, inspection


def upsert_llm_config(db: Session, actor: User, payload: LlmConfigUpsert) -> TeamLlmConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    label = payload.label.strip()
    provider_preset, adapter_kind, base_url, _region = apply_provider_defaults(
        provider_preset=payload.provider_preset,
        base_url=payload.base_url,
        bedrock_region=payload.bedrock_region,
    )
    provider_preset = reclassify_preset_for_base_url(provider_preset, base_url)
    preset = get_llm_provider_preset(provider_preset)
    adapter_kind = preset.adapter_kind
    config = None
    if payload.config_id is not None:
        config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id))
        if config is None:
            raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(payload.config_id)})
    creating = config is None
    replacing_secret = payload.credential_action == "replace" or bool(payload.bearer_token)
    removing_secret = payload.credential_action == "remove"
    if removing_secret and payload.bearer_token:
        raise AppError(422, "business_rule_violation", "Bearer token cannot be supplied when credential_action is remove", {"field": "credential_action"})
    if replacing_secret and not payload.bearer_token:
        raise AppError(422, "business_rule_violation", "Bearer token is required when credential_action is replace", {"field": "bearer_token"})
    if removing_secret and preset.requires_bearer_token:
        raise AppError(422, "business_rule_violation", "This LLM provider requires a saved bearer token", {"field": "credential_action"})
    requires_saved_secret = preset.requires_bearer_token
    has_existing_secret = config is not None and bool(config.vault_secret_ref)
    if requires_saved_secret and not replacing_secret and not has_existing_secret:
        raise AppError(422, "business_rule_violation", "This LLM provider requires a saved bearer token", {"field": "bearer_token"})

    provider_endpoint_changed = (
        config is not None
        and (
            (config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url)) != provider_preset
            or config.adapter_kind != adapter_kind
            or config.base_url.rstrip("/") != base_url.rstrip("/")
        )
    )
    has_in_flight_jobs = config is not None and _llm_config_has_in_flight_jobs(db, config_id=config.id)
    submitted_model_name = (payload.model_name or "").strip()
    credential_correction_during_in_flight = (
        has_in_flight_jobs
        and config is not None
        and replacing_secret
        and not removing_secret
        and not provider_endpoint_changed
    )
    availability_only_update_during_in_flight = (
        has_in_flight_jobs
        and config is not None
        and not replacing_secret
        and not removing_secret
        and not provider_endpoint_changed
        and label == config.label
        and submitted_model_name == (config.model_name or "")
        and payload.is_active != config.is_active
    )
    if has_in_flight_jobs and not (credential_correction_during_in_flight or availability_only_update_during_in_flight):
        raise AppError(
            409,
            "conflict",
            "Cannot edit this LLM config while generated documents are queued or processing",
            {"config_id": str(config.id)},
        )
    if credential_correction_during_in_flight:
        label = config.label
    _ensure_unique_llm_config_label(db, team_id=team.id, label=label, current_config_id=config.id if config is not None else None)

    existing_token_for_discovery: str | None = None
    if provider_endpoint_changed and not replacing_secret and has_existing_secret:
        existing_token_for_discovery = read_team_llm_bearer_token(team_id=team.id, config_id=config.id)

    available_models_json: list[str]
    discovery_metadata: dict[str, object] = {}
    if adapter_kind is LlmAdapterKind.openai_chat:
        token_for_discovery = payload.bearer_token if replacing_secret and payload.bearer_token else existing_token_for_discovery
        if token_for_discovery:
            try:
                available_models_json = _list_openai_compatible_chat_models(provider_preset=provider_preset, api_key=token_for_discovery, base_url=base_url)
                discovery_metadata = _successful_discovery_metadata(
                    provider_preset=provider_preset,
                    models=available_models_json,
                    empty_warning="No compatible chat models were returned. Enter a model name manually.",
                )
            except AppError as exc:
                if exc.code == "llm_invalid_credential":
                    raise
                available_models_json = []
                warning = "Live model discovery failed. Verify the API key and endpoint, or enter a model name manually."
                discovery_metadata = _discovery_metadata(provider_preset=provider_preset, discovery_status="manual_required", default_model_source="manual", warnings=[warning], notes=[])
        elif config is not None and not provider_endpoint_changed:
            available_models_json = list(config.available_models_json or [])
        else:
            available_models_json = []
            if provider_endpoint_changed:
                discovery_metadata = _discovery_metadata(provider_preset=provider_preset, discovery_status="manual_required", default_model_source="manual", warnings=["Provider endpoint changed without a usable credential for live model discovery. Enter a model name manually or replace the credential."], notes=[])
    elif adapter_kind is LlmAdapterKind.bedrock_chat:
        token_for_discovery = payload.bearer_token if replacing_secret and payload.bearer_token else existing_token_for_discovery
        if token_for_discovery:
            try:
                available_models_json = _list_bedrock_chat_models(api_key=token_for_discovery, base_url=base_url)
                discovery_metadata = _successful_discovery_metadata(
                    provider_preset=provider_preset,
                    models=available_models_json,
                    empty_warning="No compatible chat models were returned. Enter a model name manually.",
                )
            except AppError as exc:
                if exc.code == "llm_invalid_credential":
                    raise
                available_models_json = []
                warning = "Live model discovery failed. Verify the API key and endpoint, or enter a model name manually."
                discovery_metadata = _discovery_metadata(provider_preset=provider_preset, discovery_status="manual_required", default_model_source="manual", warnings=[warning], notes=[])
        elif config is not None and not provider_endpoint_changed:
            available_models_json = list(config.available_models_json or [])
        else:
            available_models_json = []
            if provider_endpoint_changed:
                discovery_metadata = _discovery_metadata(provider_preset=provider_preset, discovery_status="manual_required", default_model_source="manual", warnings=["Provider endpoint changed without a usable credential for live model discovery. Enter a model name manually or replace the credential."], notes=[])
    elif adapter_kind is LlmAdapterKind.ollama_chat:
        try:
            token_for_lookup = payload.bearer_token if replacing_secret and payload.bearer_token else existing_token_for_discovery
            available_models_json = _list_ollama_chat_models(base_url=base_url, bearer_token=token_for_lookup)
            discovery_metadata = _successful_discovery_metadata(
                provider_preset=provider_preset,
                models=available_models_json,
                empty_warning="No compatible chat models were returned. Enter a model name manually.",
            )
        except AppError:
            available_models_json = []
            discovery_metadata = _discovery_metadata(provider_preset=provider_preset, discovery_status="manual_required", default_model_source="manual", warnings=["Live model discovery failed. Verify the endpoint, or enter a model name manually."], notes=[])
    elif config is not None and not provider_endpoint_changed:
        available_models_json = list(config.available_models_json or [])
    else:
        available_models_json = []

    if credential_correction_during_in_flight and config is not None:
        model_name = config.model_name
    else:
        model_name = submitted_model_name if submitted_model_name else (available_models_json[0] if available_models_json else None)
    if not model_name:
        raise AppError(422, "business_rule_violation", "Model name is required. Inspect models successfully or enter a model name manually.", {"field": "model_name"})
    manual_required_without_models = discovery_metadata.get("discovery_status") == "manual_required" and len(available_models_json) == 0
    if available_models_json and not manual_required_without_models and model_name not in available_models_json:
        raise AppError(422, "business_rule_violation", "Selected model is not available for this provider", {"field": "model_name"})
    if not available_models_json and model_name:
        available_models_json = [model_name]
        discovery_metadata = discovery_metadata or _discovery_metadata(provider_preset=provider_preset, discovery_status="manual_required", default_model_source="manual", warnings=[], notes=[])
        discovery_metadata["manual_model_name"] = model_name

    if adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
        resolved_auth_mode = LlmAuthMode.bearer
    else:
        resolved_auth_mode = LlmAuthMode.bearer if replacing_secret or (config is not None and config.vault_secret_ref and not removing_secret) else LlmAuthMode.none

    if config is None:
        config = TeamLlmConfig(
            id=uuid4(),
            team_id=team.id,
            label=label,
            provider_preset=provider_preset,
            adapter_kind=adapter_kind,
            base_url=base_url,
            auth_mode=resolved_auth_mode,
            model_name=model_name,
            available_models_json=available_models_json,
            inspection_metadata_json=discovery_metadata,
            setup_status=LlmConfigSetupStatus.ready,
            vault_secret_ref="pending" if replacing_secret or adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat} else "",
            is_active=payload.is_active,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(config)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            _raise_llm_label_conflict_if_needed(exc)
            raise
    else:
        config.label = label
        config.provider_preset = provider_preset
        config.adapter_kind = adapter_kind
        config.base_url = base_url
        config.auth_mode = resolved_auth_mode
        config.model_name = model_name
        config.available_models_json = available_models_json
        config.inspection_metadata_json = discovery_metadata or dict(config.inspection_metadata_json or {})
        config.setup_status = LlmConfigSetupStatus.ready
        config.is_active = config.is_active if credential_correction_during_in_flight else payload.is_active
        config.updated_by_user_id = actor.id
        db.add(config)

    deleted_secret_before_commit = False
    bearer_token_for_restore: str | None = None
    if replacing_secret and payload.bearer_token:
        config.vault_secret_ref = write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
    elif removing_secret:
        if config.vault_secret_ref:
            try:
                bearer_token_for_restore = read_team_llm_bearer_token(team_id=team.id, config_id=config.id)
            except AppError as exc:
                if exc.code != "vault_read_failed":
                    raise
                logger.warning("llm_config_secret_restore_snapshot_missing", extra={"config_id": str(config.id), "team_id": str(team.id), "error_code": exc.code})
            delete_team_llm_bearer_token(team_id=team.id, config_id=config.id)
            deleted_secret_before_commit = True
        config.vault_secret_ref = ""
    elif adapter_kind is LlmAdapterKind.ollama_chat and creating:
        config.vault_secret_ref = ""
    elif creating:
        raise AppError(422, "business_rule_violation", "Bearer token is required when creating the LLM config", {"field": "bearer_token"})

    try:
        db.commit()
    except IntegrityError as exc:
        if deleted_secret_before_commit and bearer_token_for_restore:
            try:
                write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=bearer_token_for_restore)
            except AppError as restore_exc:
                logger.warning("llm_config_secret_restore_failed", extra={"config_id": str(config.id), "team_id": str(team.id), "error_code": restore_exc.code})
        db.rollback()
        _raise_llm_label_conflict_if_needed(exc)
        raise
    except Exception:
        if deleted_secret_before_commit and bearer_token_for_restore:
            try:
                write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=bearer_token_for_restore)
            except AppError as exc:
                logger.warning("llm_config_secret_restore_failed", extra={"config_id": str(config.id), "team_id": str(team.id), "error_code": exc.code})
        raise
    db.refresh(config)
    _record_llm_audit(
        db,
        action="llm_config_created" if creating else "llm_config_updated",
        actor=actor,
        team_id=team.id,
        config_id=config.id,
        credential_action=payload.credential_action,
        setup_status=_enum_value(config.setup_status),
        active=config.is_active,
    )
    return config


def delete_llm_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> None:
    config = get_llm_config(db, actor, config_id=config_id, team_id=team_id)
    if _llm_config_has_in_flight_jobs(db, config_id=config.id):
        raise AppError(
            409,
            "conflict",
            "Cannot delete this LLM config while generated documents are queued or processing",
            {"config_id": str(config.id)},
        )
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.llm_config_id == config.id))
    if selection is not None:
        db.delete(selection)
        db.flush()
    delete_after_commit = bool(config.vault_secret_ref)
    team_id = config.team_id
    deleted_config_id = config.id
    db.delete(config)
    db.commit()
    if delete_after_commit:
        try:
            delete_team_llm_bearer_token(team_id=team_id, config_id=deleted_config_id)
        except AppError as exc:
            logger.warning("llm_config_secret_cleanup_failed", extra={"config_id": str(deleted_config_id), "team_id": str(team_id), "error_code": exc.code})
    _record_llm_audit(db, action="llm_config_deleted", actor=actor, team_id=team_id, config_id=deleted_config_id)


def list_selectable_llm_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamLlmConfig]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    stmt = (
        select(TeamLlmConfig)
        .where(
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
            TeamLlmConfig.model_name.is_not(None),
        )
        .order_by(TeamLlmConfig.created_at.desc(), TeamLlmConfig.id.desc())
    )
    return list(db.scalars(stmt))


def get_team_llm_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> TeamLlmSelection | None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    return db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.team_id == team.id))


def set_team_llm_selection(db: Session, actor: User, payload: LlmSelectionUpsert) -> TeamLlmSelection:
    team = _resolve_selection_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(
        select(TeamLlmConfig).where(
            TeamLlmConfig.id == payload.llm_config_id,
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
            TeamLlmConfig.model_name.is_not(None),
        )
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable LLM config not found", {"resource": "llm_config", "config_id": str(payload.llm_config_id)})
    provider_models = list(config.available_models_json or [])
    allowed_models = [model.strip() for model in payload.allowed_models_json if model and model.strip()]
    if allowed_models:
        if not provider_models:
            raise AppError(
                422,
                "business_rule_violation",
                "Selected LLM provider does not currently expose selectable models",
                {"field": "allowed_models_json"},
            )
        invalid = [model for model in allowed_models if model not in provider_models]
        if invalid:
            raise AppError(422, "business_rule_violation", "Selected allowed models are not available for this LLM provider", {"field": "allowed_models_json"})
    elif provider_models:
        allowed_models = list(provider_models)
    allowed_models = list(dict.fromkeys(allowed_models))
    override = payload.model_name_override.strip() if payload.model_name_override else None
    if override:
        if not allowed_models:
            raise AppError(
                422,
                "business_rule_violation",
                "Selected LLM provider does not currently expose selectable models",
                {"field": "model_name_override"},
            )
        if override not in allowed_models:
            raise AppError(422, "business_rule_violation", "Selected model is not available for this LLM provider", {"field": "model_name_override"})
    if not override and allowed_models:
        if config.model_name and config.model_name in allowed_models:
            override = config.model_name
        else:
            override = allowed_models[0]
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id))
    if selection is None:
        selection = TeamLlmSelection(
            id=uuid4(),
            team_id=team.id,
            llm_config_id=config.id,
            allowed_models_json=allowed_models,
            model_name_override=override,
            selected_by_user_id=actor.id,
        )
        db.add(selection)
    else:
        selection.llm_config_id = config.id
        selection.allowed_models_json = allowed_models
        selection.model_name_override = override
        selection.selected_by_user_id = actor.id
        db.add(selection)
    db.commit()
    db.refresh(selection)
    _record_llm_audit(db, action="llm_selection_set", actor=actor, team_id=team.id, config_id=config.id, allowed_model_count=len(allowed_models))
    return db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.id == selection.id)) or selection


def clear_team_llm_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id))
    if selection is None:
        raise AppError(404, "not_found", "LLM selection not found", {"resource": "llm_selection", "team_id": str(team.id)})
    db.delete(selection)
    db.commit()
    _record_llm_audit(db, action="llm_selection_cleared", actor=actor, team_id=team.id)


def _resolve_checker_scoped_team(db: Session, actor: User, *, team_id: UUID | None) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin hallucination checker access required")
    if team_id is None:
        raise AppError(422, "business_rule_violation", "Team is required for hallucination checker management", {"field": "team_id"})
    return _resolve_team(db, team_id=team_id)


def get_team_hallucination_check_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> TeamHallucinationCheckSelection | None:
    team = _resolve_checker_scoped_team(db, actor, team_id=team_id)
    return db.scalar(
        select(TeamHallucinationCheckSelection)
        .options(joinedload(TeamHallucinationCheckSelection.config))
        .where(TeamHallucinationCheckSelection.team_id == team.id)
    )


def set_team_hallucination_check_selection(db: Session, actor: User, payload: HallucinationCheckSelectionUpsert) -> TeamHallucinationCheckSelection:
    team = _resolve_checker_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(
        select(TeamLlmConfig).where(
            TeamLlmConfig.id == payload.llm_config_id,
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
            TeamLlmConfig.model_name.is_not(None),
        )
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable hallucination checker LLM config not found", {"resource": "llm_config", "config_id": str(payload.llm_config_id)})
    override = payload.model_name_override.strip() if payload.model_name_override else None
    provider_models = list(config.available_models_json or [])
    if override and provider_models and override not in provider_models:
        raise AppError(422, "business_rule_violation", "Selected checker model is not available for this LLM provider", {"field": "model_name_override"})
    selection = db.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id))
    if selection is None:
        selection = TeamHallucinationCheckSelection(
            id=uuid4(),
            team_id=team.id,
            llm_config_id=config.id,
            model_name_override=override,
            selected_by_user_id=actor.id,
        )
    else:
        selection.llm_config_id = config.id
        selection.model_name_override = override
        selection.selected_by_user_id = actor.id
    db.add(selection)
    db.commit()
    db.refresh(selection)
    _record_llm_audit(db, action="hallucination_check_selection_set", actor=actor, team_id=team.id, config_id=config.id)
    return db.scalar(
        select(TeamHallucinationCheckSelection)
        .options(joinedload(TeamHallucinationCheckSelection.config))
        .where(TeamHallucinationCheckSelection.id == selection.id)
    ) or selection


def clear_team_hallucination_check_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> None:
    team = _resolve_checker_scoped_team(db, actor, team_id=team_id)
    selection = db.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id))
    if selection is None:
        raise AppError(404, "not_found", "Hallucination checker selection not found", {"resource": "hallucination_check_selection", "team_id": str(team.id)})
    db.delete(selection)
    db.commit()
    _record_llm_audit(db, action="hallucination_check_selection_cleared", actor=actor, team_id=team.id)


def active_team_hallucination_check_selection(db: Session, *, team_id: UUID) -> TeamHallucinationCheckSelection | None:
    selection = db.scalar(
        select(TeamHallucinationCheckSelection)
        .options(joinedload(TeamHallucinationCheckSelection.config))
        .where(TeamHallucinationCheckSelection.team_id == team_id)
    )
    if (
        selection is None
        or selection.config is None
        or not selection.config.is_active
        or selection.config.setup_status != LlmConfigSetupStatus.ready
        or not selection.config.model_name
    ):
        return None
    return selection


def active_team_llm_selection(db: Session, *, team_id: UUID) -> TeamLlmSelection:
    selection = db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.team_id == team_id))
    if (
        selection is None
        or selection.config is None
        or not selection.config.is_active
        or selection.config.setup_status != LlmConfigSetupStatus.ready
        or not selection.config.model_name
    ):
        raise AppError(422, "business_rule_violation", "No active LLM selection for team", {"team_id": str(team_id)})
    return selection


def resolve_team_llm(db: Session, *, team_id: UUID) -> tuple[TeamLlmSelection, TeamLlmConfig, str | None]:
    selection = active_team_llm_selection(db, team_id=team_id)
    config = selection.config
    resolved_model_name = selection.model_name_override or config.model_name
    if resolved_model_name and selection.allowed_models_json and resolved_model_name not in selection.allowed_models_json:
        resolved_model_name = selection.allowed_models_json[0] if selection.allowed_models_json else None
    return selection, config, resolved_model_name


def get_user_llm_preference(db: Session, actor: User) -> UserLlmPreference | None:
    _resolve_user_preference_scope(actor)
    return db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))


def set_user_llm_preference(db: Session, actor: User, payload: UserLlmPreferenceUpsert) -> UserLlmPreference:
    _resolve_user_preference_scope(actor)
    if actor.team_id is None:
        raise AppError(403, "forbidden", "Current user does not belong to a team")
    selection = active_team_llm_selection(db, team_id=actor.team_id)
    preferred_model_name = payload.preferred_model_name.strip() if payload.preferred_model_name else None
    allowed_models = list(selection.allowed_models_json or selection.config.available_models_json or [])
    if preferred_model_name:
        if not allowed_models:
            raise AppError(
                422,
                "business_rule_violation",
                "The active team LLM provider does not currently expose selectable models",
                {"field": "preferred_model_name"},
            )
        if preferred_model_name not in allowed_models:
            raise AppError(422, "business_rule_violation", "Preferred model is not available for the active team LLM provider", {"field": "preferred_model_name"})
    preference = db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))
    if preference is None:
        preference = UserLlmPreference(id=uuid4(), user_id=actor.id, preferred_model_name=preferred_model_name)
        db.add(preference)
    else:
        preference.preferred_model_name = preferred_model_name
        db.add(preference)
    db.commit()
    db.refresh(preference)
    record_security_event(db, action="user_llm_preference_set", actor=actor, target=actor, team_id=actor.team_id, details={"category": "provider", "outcome": "success", "provider_type": "llm", "object_type": "user_llm_preference", "object_id": str(preference.id), "preferred_model_set": bool(preferred_model_name)})
    return preference


def clear_user_llm_preference(db: Session, actor: User) -> None:
    _resolve_user_preference_scope(actor)
    preference = db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))
    if preference is None:
        raise AppError(404, "not_found", "User LLM preference not found", {"resource": "user_llm_preference", "user_id": str(actor.id)})
    db.delete(preference)
    db.commit()
    record_security_event(db, action="user_llm_preference_cleared", actor=actor, target=actor, team_id=actor.team_id, details={"category": "provider", "outcome": "success", "provider_type": "llm", "object_type": "user_llm_preference", "object_id": str(preference.id)})


def resolve_user_llm(db: Session, actor: User) -> tuple[TeamLlmSelection, TeamLlmConfig, str | None, UserLlmPreference | None]:
    _resolve_user_preference_scope(actor)
    if actor.team_id is None:
        raise AppError(403, "forbidden", "Current user does not belong to a team")
    selection, config, team_model = resolve_team_llm(db, team_id=actor.team_id)
    preference = db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))
    preferred_model = preference.preferred_model_name if preference and preference.preferred_model_name else None
    allowed_models = list(selection.allowed_models_json or config.available_models_json or [])
    if preferred_model and allowed_models and preferred_model not in allowed_models:
        preferred_model = None
    return selection, config, preferred_model or team_model, preference


def read_active_team_llm_bearer_token(db: Session, *, team_id: UUID) -> str:
    _, config, _ = resolve_team_llm(db, team_id=team_id)
    if not config.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "No stored API key is configured for this LLM provider", {"team_id": str(team_id)})
    return read_team_llm_bearer_token(team_id=team_id, config_id=config.id)
