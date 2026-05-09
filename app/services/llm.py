from uuid import UUID, uuid4

import httpx
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import GeneratedDocument, GeneratedDocumentStatus, LlmAdapterKind, LlmAuthMode, Team, TeamLlmConfig, TeamLlmSelection, TeamRole, User, UserLlmPreference
from app.schemas import LlmConfigInspectResult, LlmConfigUpsert, LlmInspectRequest, LlmModelOption, LlmSelectionUpsert, UserLlmPreferenceUpsert
from app.services.vault import delete_team_llm_bearer_token, read_team_llm_bearer_token, write_team_llm_bearer_token


OPENAI_CHAT_MODEL_FALLBACKS = (
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
)


def _list_openai_compatible_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models_page = client.models.list()
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available models") from exc

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
        raise AppError(502, "llm_inspection_failed", "Could not load available OpenAI chat models") from exc

    allowed: set[str] = set()
    for normalized in models:
        lower = normalized.lower()
        if any(token in lower for token in ("embedding", "transcribe", "whisper", "tts", "moderation", "image", "search")):
            continue
        if lower.startswith(("gpt-", "o1", "o3", "o4")) or lower.startswith("chatgpt-"):
            allowed.add(normalized)
    return sorted(allowed)


def _list_bedrock_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        return _list_openai_compatible_models(api_key=api_key, base_url=base_url)
    except AppError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Amazon Bedrock chat models") from exc


def _fallback_openai_chat_models() -> list[str]:
    return list(OPENAI_CHAT_MODEL_FALLBACKS)


def _openai_model_options(models: list[str], *, source: str) -> list[LlmModelOption]:
    return [LlmModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


def _bedrock_model_options(models: list[str], *, source: str) -> list[LlmModelOption]:
    return [LlmModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


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


def _ollama_model_options(models: list[str], *, source: str) -> list[LlmModelOption]:
    return [LlmModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


def inspect_llm_contract(db: Session, actor: User, payload) -> LlmConfigInspectResult:
    _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    if payload.adapter_kind is LlmAdapterKind.openai_chat:
        requires_bearer_token = True
        supports_model_discovery = True
        if payload.bearer_token:
            try:
                models = _list_openai_chat_models(api_key=payload.bearer_token, base_url=payload.base_url)
                source = "fetched"
                discovery_status = "fetched"
                default_model_source = "provider"
                warnings: list[str] = []
                notes: list[str] = []
            except AppError:
                models = _fallback_openai_chat_models()
                source = "default"
                discovery_status = "fallback"
                default_model_source = "builtin"
                warnings = ["Live OpenAI model discovery failed; verify API key/base URL."]
                notes = ["Live OpenAI model discovery failed, so OpenScribe used the built-in default chat model list."]
        else:
            models = _fallback_openai_chat_models()
            source = "default"
            discovery_status = "fallback"
            default_model_source = "builtin"
            warnings = ["No API key was provided; using built-in OpenAI chat model defaults."]
            notes = ["No API key provided for inspection, so OpenScribe used the built-in default chat model list."]
        model_options = _openai_model_options(models, source=source)
    elif payload.adapter_kind is LlmAdapterKind.bedrock_chat:
        requires_bearer_token = True
        supports_model_discovery = True
        if payload.bearer_token:
            try:
                models = _list_bedrock_chat_models(api_key=payload.bearer_token, base_url=payload.base_url)
                source = "fetched"
                discovery_status = "fetched"
                default_model_source = "provider"
                warnings = []
                notes = [
                    "OpenScribe loaded the Amazon Bedrock model list through the OpenAI-compatible Models API for the supplied Bedrock Mantle endpoint.",
                ]
            except AppError:
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
        model_options = _bedrock_model_options(models, source=source)
    elif payload.adapter_kind is LlmAdapterKind.ollama_chat:
        requires_bearer_token = False
        supports_model_discovery = True
        try:
            models = _list_ollama_chat_models(base_url=payload.base_url, bearer_token=payload.bearer_token)
            source = "fetched"
            discovery_status = "fetched"
            default_model_source = "provider"
            warnings = []
            notes = []
        except AppError:
            models = []
            source = "manual"
            discovery_status = "manual_required"
            default_model_source = "manual"
            warnings = ["Could not reach Ollama /api/tags. Verify the base URL and network access."]
            notes = ["Live Ollama model discovery failed. Verify the base URL and local Ollama host, or enter a model name manually."]
        model_options = _ollama_model_options(models, source=source)
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
        base_url=payload.base_url,
        adapter_kind=payload.adapter_kind,
        model_name=model_name,
        available_models=models,
        available_model_options=model_options,
        discovery_status=discovery_status,
        default_model_source=default_model_source,
        requires_bearer_token=requires_bearer_token,
        supports_model_discovery=supports_model_discovery,
        warnings=warnings,
        notes=notes,
    )


def inspect_saved_llm_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> LlmConfigInspectResult:
    config = get_llm_config(db, actor, config_id=config_id, team_id=team_id)
    bearer_token = read_team_llm_bearer_token(team_id=config.team_id, config_id=config.id) if config.vault_secret_ref else None
    inspection = inspect_llm_contract(
        db,
        actor,
        LlmInspectRequest(team_id=config.team_id, adapter_kind=config.adapter_kind, base_url=config.base_url, bearer_token=bearer_token),
    )
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


def upsert_llm_config(db: Session, actor: User, payload: LlmConfigUpsert) -> TeamLlmConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = None
    if payload.config_id is not None:
        config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id))
        if config is None:
            raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(payload.config_id)})
        if _llm_config_has_in_flight_jobs(db, config_id=config.id):
            raise AppError(
                409,
                "conflict",
                "Cannot edit this LLM config while generated documents are queued or processing",
                {"config_id": str(config.id)},
            )
    creating = config is None

    available_models_json: list[str]
    if payload.adapter_kind is LlmAdapterKind.openai_chat:
        if payload.bearer_token:
            try:
                available_models_json = _list_openai_chat_models(api_key=payload.bearer_token, base_url=payload.base_url)
            except AppError:
                available_models_json = _fallback_openai_chat_models()
        elif config is not None:
            available_models_json = list(config.available_models_json or [])
        else:
            available_models_json = _fallback_openai_chat_models()
    elif payload.adapter_kind is LlmAdapterKind.bedrock_chat:
        if payload.bearer_token:
            try:
                available_models_json = _list_bedrock_chat_models(api_key=payload.bearer_token, base_url=payload.base_url)
            except AppError:
                available_models_json = list(config.available_models_json or []) if config is not None else []
        elif config is not None:
            available_models_json = list(config.available_models_json or [])
        else:
            available_models_json = []
    elif payload.adapter_kind is LlmAdapterKind.ollama_chat:
        try:
            token_for_lookup = payload.bearer_token if payload.bearer_token else None
            available_models_json = _list_ollama_chat_models(base_url=payload.base_url, bearer_token=token_for_lookup)
        except AppError:
            available_models_json = list(config.available_models_json or []) if config is not None else []
    elif config is not None:
        available_models_json = list(config.available_models_json or [])
    else:
        available_models_json = []

    if payload.adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
        resolved_auth_mode = LlmAuthMode.bearer
    else:
        resolved_auth_mode = LlmAuthMode.bearer if payload.bearer_token or (config is not None and config.vault_secret_ref) else LlmAuthMode.none

    if config is None:
        config = TeamLlmConfig(
            id=uuid4(),
            team_id=team.id,
            label=payload.label.strip(),
            adapter_kind=payload.adapter_kind,
            base_url=payload.base_url,
            auth_mode=resolved_auth_mode,
            model_name=payload.model_name.strip() if payload.model_name else None,
            available_models_json=available_models_json,
            vault_secret_ref="pending" if payload.bearer_token or payload.adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat} else "",
            is_active=payload.is_active,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(config)
        db.flush()
    else:
        config.label = payload.label.strip()
        config.adapter_kind = payload.adapter_kind
        config.base_url = payload.base_url
        config.auth_mode = resolved_auth_mode
        config.model_name = payload.model_name.strip() if payload.model_name else None
        config.available_models_json = available_models_json
        config.is_active = payload.is_active
        config.updated_by_user_id = actor.id
        db.add(config)

    if payload.bearer_token:
        config.vault_secret_ref = write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
    elif payload.adapter_kind is LlmAdapterKind.ollama_chat:
        if config.vault_secret_ref:
            delete_team_llm_bearer_token(team_id=team.id, config_id=config.id)
        config.vault_secret_ref = ""
    elif creating:
        raise AppError(422, "business_rule_violation", "Bearer token is required when creating the LLM config", {"field": "bearer_token"})

    db.commit()
    db.refresh(config)
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
    if config.vault_secret_ref:
        delete_team_llm_bearer_token(team_id=config.team_id, config_id=config.id)
    db.delete(config)
    db.commit()


def list_selectable_llm_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamLlmConfig]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    stmt = select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id, TeamLlmConfig.is_active.is_(True)).order_by(TeamLlmConfig.created_at.desc(), TeamLlmConfig.id.desc())
    return list(db.scalars(stmt))


def get_team_llm_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> TeamLlmSelection | None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    return db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.team_id == team.id))


def set_team_llm_selection(db: Session, actor: User, payload: LlmSelectionUpsert) -> TeamLlmSelection:
    team = _resolve_selection_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.llm_config_id, TeamLlmConfig.team_id == team.id, TeamLlmConfig.is_active.is_(True)))
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
    return db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.id == selection.id)) or selection


def clear_team_llm_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id))
    if selection is None:
        raise AppError(404, "not_found", "LLM selection not found", {"resource": "llm_selection", "team_id": str(team.id)})
    db.delete(selection)
    db.commit()


def active_team_llm_selection(db: Session, *, team_id: UUID) -> TeamLlmSelection:
    selection = db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.team_id == team_id))
    if selection is None or selection.config is None or not selection.config.is_active:
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
    return preference


def clear_user_llm_preference(db: Session, actor: User) -> None:
    _resolve_user_preference_scope(actor)
    preference = db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))
    if preference is None:
        raise AppError(404, "not_found", "User LLM preference not found", {"resource": "user_llm_preference", "user_id": str(actor.id)})
    db.delete(preference)
    db.commit()


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
