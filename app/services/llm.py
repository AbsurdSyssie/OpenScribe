import json
import logging
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import ConsultationSplitAnalysis, ConsultationSplitAnalysisStatus, ConsultationSplitBatch, ConsultationSplitBatchStatus, ConsultationSplitExecution, ConsultationSplitExecutionStatus, GeneratedDocument, GeneratedDocumentStatus, LlmAdapterKind, LlmAuthMode, LlmConfigSetupStatus, LlmProviderPreset, ProviderSecretCleanupKind, Team, TeamHallucinationCheckSelection, TeamLlmBackupSelection, TeamLlmConfig, TeamLlmProviderModelPolicy, TeamLlmSelection, TeamRole, TemplateSuggestionJob, TemplateSuggestionStatus, User, UserLlmPreference, utcnow
from app.schemas import HallucinationCheckSelectionUpsert, LlmBackupSelectionUpsert, LlmConfigDraftCreate, LlmConfigDraftReplaceCredential, LlmConfigFinalize, LlmConfigInspectResult, LlmConfigUpsert, LlmInspectRequest, LlmModelOption, LlmProviderModelPolicyUpsert, LlmSelectionUpsert, UserLlmPreferenceUpsert
from app.services.llm_presets import (
    apply_provider_defaults,
    default_llm_config_label,
    filter_discovered_models,
    get_llm_provider_preset,
    infer_llm_provider_preset,
    reclassify_preset_for_base_url,
)
from app.services.security_audit import record_security_event
from app.services.vault import (
    delete_team_llm_bearer_token,
    read_team_llm_bearer_token,
    read_team_llm_secret,
    write_team_llm_bearer_token,
    write_team_llm_secret,
)
from app.services.llm_adapters.gemini_enterprise import (
    discover_gemini_models,
    service_account_credentials_from_info,
    validate_gemini_model,
)
from app.services.llm_credentials import google_service_account_secret, resolve_llm_runtime_credential
from app.services.llm_adapters.runtime import build_provider_snapshot, validate_provider_snapshot_shape
from app.services.content_crypto import decrypt_json_for_owner, is_encrypted_envelope
from app.services.llm_backup import retire_backup_snapshots_for_config
from app.services.llm_checker_selection import retire_checker_snapshots_for_config
from app.services.llm_model_policy import (
    ensure_no_legacy_checker_work_for_policy_change,
    get_provider_model_policy,
    model_allowed_for_provider,
    provider_model_policy_state,
)
from app.services.provider_credential_persistence import provider_credential_write_transaction
from app.services.provider_secret_cleanup import queue_orphan_provider_secret_after_rollback, queue_provider_secret_cleanup
from app.services.provider_inspection import ProviderResponseTooLargeError, read_limited_httpx_response
from app.services.provider_errors import safe_provider_error_code, safe_provider_error_diagnostic
from app.provider_url_security import require_safe_provider_url


logger = logging.getLogger("openscribe.llm")
MODEL_DISCOVERY_MAX_RESPONSE_BYTES = 1024 * 1024
_INSPECTION_DIAGNOSTIC_CODES = frozenset({"invalid_json", "invalid_response", "response_too_large", "unsupported_content_encoding"})


def _provider_diagnostic_details(details: dict[str, object] | None) -> dict[str, object]:
    source = details or {}
    status = source.get("provider_status") or source.get("provider_status_code") or source.get("provider_http_status")
    result: dict[str, object] = {}
    if type(status) is int and 400 <= status <= 599:
        result["provider_status"] = status
    raw_code = source.get("provider_error_code")
    if isinstance(raw_code, str) and raw_code.strip().lower() in _INSPECTION_DIAGNOSTIC_CODES:
        result["provider_error_code"] = raw_code.strip().lower()
    elif isinstance(raw_code, str) or result.get("provider_status") is not None:
        result["provider_error_code"] = safe_provider_error_code(
            raw_code,
            status_code=result.get("provider_status") if isinstance(result.get("provider_status"), int) else None,
        )
    return result


def _provider_discovery_note(error: AppError) -> str | None:
    details = _provider_diagnostic_details(error.details)
    return safe_provider_error_diagnostic(
        status_code=details.get("provider_status"),
        provider_error_code=details.get("provider_error_code"),
    )


def _record_llm_audit(db: Session, *, action: str, actor: User, team_id: UUID, config_id: UUID | None = None, outcome: str = "success", **details) -> None:
    payload = {"category": "provider", "outcome": outcome, "provider_type": "llm"}
    if config_id is not None:
        payload.update({"object_type": "team_llm_config", "object_id": str(config_id)})
    payload.update(details)
    record_security_event(db, action=action, actor=actor, team_id=team_id, details=payload)


def _enum_value(value):
    return getattr(value, "value", value)


def _gemini_provider_config(payload) -> dict[str, object]:
    config = dict(getattr(payload, "provider_config_json", None) or {})
    return {
        "project_id": str(config.get("project_id") or payload.google_project_id),
        "location": str(config.get("location") or payload.google_location),
        "api_version": "v1",
        "capacity_mode": str(config.get("capacity_mode") or payload.capacity_mode or "auto"),
    }


def _gemini_submission_credential(payload):
    if payload.google_auth_method == "application_default":
        return None
    credential_json = payload.google_service_account_json
    if not isinstance(credential_json, dict):
        raise AppError(422, "business_rule_violation", "Service-account authentication requires credential JSON")
    return service_account_credentials_from_info(credential_json)


def _gemini_auth_mode(payload) -> LlmAuthMode:
    return (
        LlmAuthMode.google_adc
        if payload.google_auth_method == "application_default"
        else LlmAuthMode.google_service_account
    )


def _fetch_bounded_model_discovery_json(
    *,
    api_key: str,
    base_url: str,
    failure_message: str,
) -> object:
    require_safe_provider_url(base_url)
    try:
        with httpx.stream(
            "GET",
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        ) as response:
            response.raise_for_status()
            payload = json.loads(read_limited_httpx_response(response, max_bytes=MODEL_DISCOVERY_MAX_RESPONSE_BYTES))
    except httpx.HTTPStatusError as exc:  # pragma: no cover
        provider_status = exc.response.status_code
        provider_error_code = safe_provider_error_code(None, status_code=provider_status)
        if exc.response.status_code in {401, 403}:
            raise AppError(
                401,
                "llm_invalid_credential",
                "The API key was rejected by the provider.",
                {"provider_status": provider_status, "provider_error_code": provider_error_code},
            ) from exc
        raise AppError(
            502,
            "llm_inspection_failed",
            failure_message,
            {"provider_status": provider_status, "provider_error_code": provider_error_code},
        ) from exc
    except ProviderResponseTooLargeError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", failure_message, {"provider_error_code": "response_too_large"}) from exc
    except httpx.TimeoutException as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", failure_message, {"provider_error_code": "timeout"}) from exc
    except httpx.ConnectError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", failure_message, {"provider_error_code": "connection_error"}) from exc
    except ValueError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", failure_message, {"provider_error_code": "invalid_json"}) from exc
    except httpx.DecodingError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", failure_message, {"provider_error_code": "invalid_response"}) from exc
    except httpx.HTTPError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", failure_message, {"provider_error_code": "http_error"}) from exc
    return payload


def _list_openai_compatible_models(*, api_key: str, base_url: str) -> list[str]:
    payload = _fetch_bounded_model_discovery_json(
        api_key=api_key,
        base_url=base_url,
        failure_message="Could not load available models from the provider.",
    )

    records = payload.get("data") if isinstance(payload, dict) else []
    models: set[str] = set()
    for model in records:
        model_id = model.get("id") if isinstance(model, dict) else None
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
        raise AppError(502, "llm_inspection_failed", "Could not load available OpenAI chat models", _provider_diagnostic_details(exc.details)) from exc

    return filter_discovered_models(LlmProviderPreset.openai.value, models)


def _list_mistral_chat_models(*, api_key: str, base_url: str) -> list[str]:
    payload = _fetch_bounded_model_discovery_json(
        api_key=api_key,
        base_url=base_url,
        failure_message="Could not load available Mistral chat models",
    )

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
    payload = _fetch_bounded_model_discovery_json(
        api_key=api_key,
        base_url=base_url,
        failure_message="Could not load available Together AI chat models",
    )

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
        raise AppError(502, "llm_inspection_failed", "Could not load available OpenAI-compatible chat models", _provider_diagnostic_details(exc.details)) from exc
    return filter_discovered_models(provider_preset, models)


def _list_bedrock_chat_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        return _list_openai_compatible_models(api_key=api_key, base_url=base_url)
    except AppError as exc:  # pragma: no cover
        if exc.code == "llm_invalid_credential":
            raise
        raise AppError(502, "llm_inspection_failed", "Could not load available Amazon Bedrock chat models", _provider_diagnostic_details(exc.details)) from exc


def _ensure_unique_llm_config_label(db: Session, *, team_id: UUID, label: str, current_config_id: UUID | None = None) -> None:
    normalized = label.strip().lower()
    stmt = select(TeamLlmConfig.id).where(
        TeamLlmConfig.team_id == team_id,
        TeamLlmConfig.revision_of_config_id.is_(None),
        func.lower(func.btrim(TeamLlmConfig.label)) == normalized,
    )
    if current_config_id is not None:
        stmt = stmt.where(TeamLlmConfig.id != current_config_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise AppError(409, "conflict", "An LLM provider with this name already exists for this team.", {"field": "label"})


def _raise_llm_label_conflict_if_needed(exc: IntegrityError) -> None:
    if "uq_team_llm_configs_team_label_lower" in str(exc.orig):
        raise AppError(409, "conflict", "An LLM provider with this name already exists for this team.", {"field": "label"}) from exc


def _pending_llm_revision(db: Session, *, team_id: UUID, target_id: UUID) -> TeamLlmConfig | None:
    return db.scalar(select(TeamLlmConfig).where(
        TeamLlmConfig.team_id == team_id,
        TeamLlmConfig.revision_of_config_id == target_id,
    ).limit(1))


def _raise_llm_pending_revision_conflict(target_id: UUID, revision: TeamLlmConfig | None) -> None:
    details: dict[str, str] = {
        "resource": "llm_config_revision",
        "revision_of_config_id": str(target_id),
    }
    message = "Another LLM connection edit changed while this request was being saved. Reload the provider and retry."
    if revision is not None:
        details["pending_revision_id"] = str(revision.id)
        message = "An unfinished LLM connection edit already exists. Resume or discard it before starting another edit."
    raise AppError(409, "conflict", message, {
        **details,
    })


def _llm_model_options(models: list[str], *, source: str) -> list[LlmModelOption]:
    return [LlmModelOption(id=model, source=source, label=f"{model} ({source})") for model in models]


def _inspection_metadata(inspection: LlmConfigInspectResult) -> dict[str, object]:
    metadata: dict[str, object] = {
        "provider_preset": inspection.provider_preset,
        "provider_display_name": inspection.provider_display_name,
        "discovery_status": inspection.discovery_status,
        "default_model_source": inspection.default_model_source,
        "warnings": list(inspection.warnings),
        "notes": list(inspection.notes),
        "inspected_at": utcnow().isoformat(),
    }
    if inspection.model_catalog_check_status == "checked":
        metadata["connection_check"] = _connection_check_record(passed=True)
    elif inspection.model_catalog_check_status == "failed":
        metadata["connection_check"] = _connection_check_record(
            passed=False,
            error_code=inspection.model_catalog_check_error_code or "provider_error",
            http_status=inspection.model_catalog_check_http_status,
        )
    return metadata


def _connection_check_record(*, passed: bool, error_code: str | None = None, http_status: int | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "status": "passed" if passed else "failed",
        "checked_at": utcnow().isoformat(),
    }
    if not passed:
        if error_code:
            record["error_code"] = error_code
        if type(http_status) is int and 400 <= http_status <= 599:
            record["http_status"] = http_status
    return record


def _ensure_empty_provider_model_policy(db: Session, *, config: TeamLlmConfig, actor_id: UUID) -> TeamLlmProviderModelPolicy:
    policy = get_provider_model_policy(db, config=config)
    if policy is None:
        policy = TeamLlmProviderModelPolicy(
            llm_config_id=config.id,
            team_id=config.team_id,
            enabled_models_json=[],
            default_model_name=None,
            updated_by_user_id=actor_id,
        )
        db.add(policy)
        db.flush()
    return policy


def _discovery_metadata(
    *,
    provider_preset: str,
    discovery_status: str,
    default_model_source: str,
    warnings: list[str],
    notes: list[str],
    manual_model_name: str | None = None,
    allow_disabled_provider: bool = False,
) -> dict[str, object]:
    preset = get_llm_provider_preset(provider_preset, allow_disabled_provider=allow_disabled_provider)
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


def _successful_discovery_metadata(
    *,
    provider_preset: str,
    models: list[str],
    empty_warning: str,
    fetched_note: str | None = None,
    allow_disabled_provider: bool = False,
) -> dict[str, object]:
    if models:
        notes = [fetched_note] if fetched_note else []
        return _discovery_metadata(
            provider_preset=provider_preset,
            discovery_status="fetched",
            default_model_source="provider",
            warnings=[],
            notes=notes,
            allow_disabled_provider=allow_disabled_provider,
        )
    return _discovery_metadata(
        provider_preset=provider_preset,
        discovery_status="manual_required",
        default_model_source="manual",
        warnings=[empty_warning],
        notes=[],
        allow_disabled_provider=allow_disabled_provider,
    )


def _list_ollama_chat_models(*, base_url: str, bearer_token: str | None) -> list[str]:
    require_safe_provider_url(base_url)
    try:
        with httpx.stream(
            "GET",
            _ollama_api_url(base_url, "/api/tags"),
            headers=_ollama_headers(bearer_token=bearer_token),
            timeout=10.0,
        ) as response:
            response.raise_for_status()
            payload = json.loads(read_limited_httpx_response(response, max_bytes=MODEL_DISCOVERY_MAX_RESPONSE_BYTES))
    except httpx.HTTPStatusError as exc:  # pragma: no cover
        provider_status = exc.response.status_code
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_status": provider_status, "provider_error_code": safe_provider_error_code(None, status_code=provider_status)}) from exc
    except ProviderResponseTooLargeError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_error_code": "response_too_large"}) from exc
    except httpx.TimeoutException as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_error_code": "timeout"}) from exc
    except httpx.ConnectError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_error_code": "connection_error"}) from exc
    except ValueError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_error_code": "invalid_json"}) from exc
    except httpx.DecodingError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_error_code": "invalid_response"}) from exc
    except httpx.HTTPError as exc:  # pragma: no cover
        raise AppError(502, "llm_inspection_failed", "Could not load available Ollama chat models", {"provider_error_code": "http_error"}) from exc

    models: list[str] = []
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        model_name = item.get("name") or item.get("model")
        if isinstance(model_name, str) and model_name.strip():
            models.append(model_name.strip())
    return sorted(set(models))


def inspect_llm_contract(
    db: Session,
    actor: User,
    payload,
    *,
    allow_disabled_provider: bool = False,
) -> LlmConfigInspectResult:
    _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    preset_key, adapter_kind, base_url, _region = apply_provider_defaults(
        provider_preset=getattr(payload, "provider_preset", None),
        base_url=payload.base_url,
        bedrock_region=getattr(payload, "bedrock_region", None),
        allow_disabled_provider=allow_disabled_provider,
    )
    preset_key = reclassify_preset_for_base_url(
        preset_key,
        base_url,
        allow_disabled_provider=allow_disabled_provider,
    )
    preset = get_llm_provider_preset(preset_key, allow_disabled_provider=allow_disabled_provider)
    adapter_kind = preset.adapter_kind
    model_catalog_checked = False
    model_catalog_check_status = "not_attempted"
    model_catalog_check_error_code = None
    model_catalog_check_http_status = None
    if adapter_kind is LlmAdapterKind.openai_chat:
        if payload.bearer_token:
            try:
                models = _list_openai_compatible_chat_models(provider_preset=preset_key, api_key=payload.bearer_token, base_url=base_url)
                model_catalog_checked = True
                model_catalog_check_status = "checked"
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
                model_catalog_check_status = "failed"
                diagnostic_details = _provider_diagnostic_details(exc.details)
                model_catalog_check_error_code = diagnostic_details.get("provider_error_code") or safe_provider_error_code(exc.code, status_code=exc.status_code)
                model_catalog_check_http_status = diagnostic_details.get("provider_status") if isinstance(diagnostic_details.get("provider_status"), int) else None
                source = "manual"
                discovery_status = "manual_required"
                default_model_source = "manual"
                diagnostic = _provider_discovery_note(exc)
                warnings = ["Live model discovery failed. Verify the API key and endpoint, or enter a model name manually."]
                notes = [diagnostic] if diagnostic else []
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
                model_catalog_checked = True
                model_catalog_check_status = "checked"
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
                model_catalog_check_status = "failed"
                diagnostic_details = _provider_diagnostic_details(exc.details)
                model_catalog_check_error_code = diagnostic_details.get("provider_error_code") or safe_provider_error_code(exc.code, status_code=exc.status_code)
                model_catalog_check_http_status = diagnostic_details.get("provider_status") if isinstance(diagnostic_details.get("provider_status"), int) else None
                source = "manual"
                discovery_status = "manual_required"
                default_model_source = "manual"
                diagnostic = _provider_discovery_note(exc)
                recovery = "Live Amazon Bedrock model discovery failed. Verify the Bedrock Mantle base URL, region, and API key, or enter a model name manually."
                warnings = ["Could not load region-specific Bedrock models. Enter a model ID manually or inspect again with credentials."]
                notes = [recovery, diagnostic] if diagnostic else [recovery]
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
            model_catalog_checked = True
            model_catalog_check_status = "checked"
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
        except AppError as exc:
            model_catalog_check_status = "failed"
            diagnostic_details = _provider_diagnostic_details(exc.details)
            model_catalog_check_error_code = diagnostic_details.get("provider_error_code") or safe_provider_error_code(exc.code, status_code=exc.status_code)
            model_catalog_check_http_status = diagnostic_details.get("provider_status") if isinstance(diagnostic_details.get("provider_status"), int) else None
            models = []
            source = "manual"
            discovery_status = "manual_required"
            default_model_source = "manual"
            diagnostic = _provider_discovery_note(exc)
            recovery = "Live Ollama model discovery failed. Verify the base URL and local Ollama host, or enter a model name manually."
            warnings = ["Could not reach Ollama /api/tags. Verify the base URL and network access."]
            notes = [recovery, diagnostic] if diagnostic else [recovery]
        model_options = _llm_model_options(models, source=source)
    elif adapter_kind is LlmAdapterKind.gemini_enterprise:
        provider_config = _gemini_provider_config(payload)
        credentials = _gemini_submission_credential(payload)
        try:
            models = discover_gemini_models(
                project_id=str(provider_config["project_id"]),
                location=str(provider_config["location"]),
                credentials=credentials,
                capacity_mode=str(provider_config["capacity_mode"]),
            )
            model_catalog_checked = True
            model_catalog_check_status = "checked"
            source = "fetched" if models else "manual"
            metadata = _successful_discovery_metadata(
                provider_preset=preset_key,
                models=models,
                empty_warning="No compatible Gemini models were returned. Enter a model ID manually.",
                allow_disabled_provider=allow_disabled_provider,
            )
            discovery_status = str(metadata["discovery_status"])
            default_model_source = str(metadata["default_model_source"])
            warnings = list(metadata["warnings"])
            notes = list(metadata["notes"])
        except AppError as exc:
            if exc.code in {
                "llm_invalid_credential",
                "llm_permission_denied",
                "llm_provider_api_disabled",
                "llm_location_unavailable",
            }:
                raise
            model_catalog_check_status = "failed"
            diagnostic_details = _provider_diagnostic_details(exc.details)
            model_catalog_check_error_code = diagnostic_details.get("provider_error_code") or safe_provider_error_code(exc.code, status_code=exc.status_code)
            model_catalog_check_http_status = diagnostic_details.get("provider_status") if isinstance(diagnostic_details.get("provider_status"), int) else None
            models = []
            source = "manual"
            discovery_status = "manual_required"
            default_model_source = "manual"
            diagnostic = _provider_discovery_note(exc)
            recovery = "Gemini model discovery is temporarily unavailable. Enter a model ID manually."
            warnings = [recovery]
            notes = [diagnostic] if diagnostic else []
        model_options = _llm_model_options(models, source=source)
    else:  # pragma: no cover
        models = []
        model_catalog_checked = False
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
        model_catalog_checked=model_catalog_checked,
        model_catalog_check_status=model_catalog_check_status,
        model_catalog_check_error_code=model_catalog_check_error_code,
        model_catalog_check_http_status=model_catalog_check_http_status,
        default_model_source=default_model_source,
        requires_bearer_token=preset.requires_bearer_token,
        supports_model_discovery=preset.supports_model_discovery,
        warnings=warnings,
        notes=notes,
        google_project_id=getattr(payload, "google_project_id", None) if adapter_kind is LlmAdapterKind.gemini_enterprise else None,
        google_location=getattr(payload, "google_location", None) if adapter_kind is LlmAdapterKind.gemini_enterprise else None,
        google_auth_method=getattr(payload, "google_auth_method", None) if adapter_kind is LlmAdapterKind.gemini_enterprise else None,
        capacity_mode=getattr(payload, "capacity_mode", None) if adapter_kind is LlmAdapterKind.gemini_enterprise else None,
    )


def inspect_saved_llm_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> LlmConfigInspectResult:
    config = get_llm_config(db, actor, config_id=config_id, team_id=team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config.id).execution_options(populate_existing=True).with_for_update())
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(config_id)})
    try:
        bearer_token = (
            read_team_llm_bearer_token(team_id=config.team_id, config_id=config.id, secret_ref=config.vault_secret_ref)
            if config.auth_mode is LlmAuthMode.bearer and config.vault_secret_ref
            else None
        )
        google_credential_json = None
        if config.auth_mode is LlmAuthMode.google_service_account:
            secret = read_team_llm_secret(team_id=config.team_id, config_id=config.id, secret_ref=config.vault_secret_ref)
            candidate = secret.get("credential_json")
            if not isinstance(candidate, dict):
                raise AppError(502, "vault_read_failed", "Vault secret read failed")
            google_credential_json = candidate
        inspection = inspect_llm_contract(
            db,
            actor,
            LlmInspectRequest.from_persisted_config(
                config,
                bearer_token=bearer_token,
                google_service_account_json=google_credential_json,
            ),
            allow_disabled_provider=True,
        )
    except AppError as exc:
        details = _provider_diagnostic_details(exc.details)
        db.rollback()
        config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config_id).execution_options(populate_existing=True).with_for_update())
        if config is None:
            raise
        metadata = dict(config.inspection_metadata_json or {})
        metadata["connection_check"] = _connection_check_record(
            passed=False,
            error_code=str(details.get("provider_error_code") or safe_provider_error_code(exc.code, status_code=details.get("provider_status") or exc.status_code)),
            http_status=details.get("provider_status") if isinstance(details.get("provider_status"), int) else None,
        )
        config.inspection_metadata_json = metadata
        config.updated_by_user_id = actor.id
        db.add(config)
        db.commit()
        raise
    metadata = _inspection_metadata(inspection)
    if inspection.model_catalog_check_status == "checked":
        metadata["connection_check"] = _connection_check_record(passed=True)
    elif inspection.model_catalog_check_status == "failed":
        metadata["connection_check"] = _connection_check_record(
            passed=False,
            error_code=inspection.model_catalog_check_error_code or "provider_error",
            http_status=inspection.model_catalog_check_http_status,
        )
    else:
        old_check = (config.inspection_metadata_json or {}).get("connection_check")
        if isinstance(old_check, dict):
            metadata["connection_check"] = old_check
    refreshed_catalog = list(inspection.available_models) if inspection.model_catalog_checked else None
    if refreshed_catalog is not None and refreshed_catalog != list(config.available_models_json or []):
        ensure_no_legacy_checker_work_for_policy_change(db, team_id=config.team_id, config_id=config.id)
    config.inspection_metadata_json = metadata
    if refreshed_catalog is not None:
        config.available_models_json = refreshed_catalog
        if config.model_name not in refreshed_catalog:
            config.model_name = inspection.model_name if inspection.model_name in refreshed_catalog else None
    config.updated_by_user_id = actor.id
    db.add(config)
    db.commit()
    db.refresh(config)
    return inspection


def list_llm_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamLlmConfig]:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    stmt = select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id, TeamLlmConfig.revision_of_config_id.is_(None)).order_by(TeamLlmConfig.created_at.desc(), TeamLlmConfig.id.desc())
    return list(db.scalars(stmt))


def get_llm_provider_model_policy(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> dict:
    config = get_llm_config(db, actor, config_id=config_id, team_id=team_id)
    if config.revision_of_config_id is not None:
        raise AppError(422, "business_rule_violation", "Model policy belongs to the saved provider, not a connection draft")
    return provider_model_policy_state(db, config=config)


def set_llm_provider_model_policy(
    db: Session,
    actor: User,
    *,
    config_id: UUID,
    payload: LlmProviderModelPolicyUpsert,
) -> dict:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    # Preserve the selection/freeze lock order: provider config, checker
    # assignment, then policy. It prevents accepted-work snapshots from
    # crossing a policy update boundary.
    config = db.scalar(select(TeamLlmConfig).where(
        TeamLlmConfig.id == config_id,
        TeamLlmConfig.team_id == team.id,
        TeamLlmConfig.revision_of_config_id.is_(None),
    ).execution_options(populate_existing=True).with_for_update())
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(config_id)})
    checker = db.scalar(select(TeamHallucinationCheckSelection).where(
        TeamHallucinationCheckSelection.team_id == team.id,
    ).execution_options(populate_existing=True).with_for_update())
    ensure_no_legacy_checker_work_for_policy_change(db, team_id=team.id, config_id=config.id)
    policy = get_provider_model_policy(db, config=config, lock=True)
    if policy is None:
        policy = TeamLlmProviderModelPolicy(
            llm_config_id=config.id,
            team_id=team.id,
            enabled_models_json=[],
            default_model_name=None,
            updated_by_user_id=actor.id,
        )
    catalog = list(dict.fromkeys(
        model.strip() for model in (config.available_models_json or [])
        if isinstance(model, str) and model.strip()
    ))
    invalid = [model for model in payload.enabled_models_json if model not in catalog]
    if invalid:
        raise AppError(422, "business_rule_violation", "Selected models are not currently available for this provider", {"field": "enabled_models_json"})
    policy.enabled_models_json = list(payload.enabled_models_json)
    policy.default_model_name = payload.default_model_name
    policy.updated_by_user_id = actor.id
    policy.updated_at = utcnow()
    db.add(policy)
    # Keep legacy columns as a read-only compatibility projection for older
    # consumers; they never define policy and leaders cannot mutate them.
    selection = db.scalar(select(TeamLlmSelection).where(
        TeamLlmSelection.team_id == team.id,
    ).execution_options(populate_existing=True).with_for_update())
    if selection is not None and selection.llm_config_id == config.id:
        selection.allowed_models_json = list(payload.enabled_models_json)
        selection.model_name_override = payload.default_model_name
        db.add(selection)
    db.commit()
    db.refresh(config)
    _record_llm_audit(db, action="llm_provider_model_policy_set", actor=actor, team_id=team.id, config_id=config.id, enabled_model_count=len(payload.enabled_models_json))
    return provider_model_policy_state(db, config=config)


def get_llm_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> TeamLlmConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config_id, TeamLlmConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(config_id)})
    return config


def update_llm_config_details(
    db: Session,
    actor: User,
    *,
    config_id: UUID,
    team_id: UUID,
    label: str,
    is_active: bool,
) -> TeamLlmConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    config = db.scalar(
        select(TeamLlmConfig).where(
            TeamLlmConfig.id == config_id,
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.revision_of_config_id.is_(None),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
        ).execution_options(populate_existing=True).with_for_update()
    )
    if config is None:
        raise AppError(404, "not_found", "Ready LLM config not found", {"resource": "llm_config", "config_id": str(config_id)})
    if config.is_active != is_active:
        ensure_no_legacy_checker_work_for_policy_change(
            db,
            team_id=config.team_id,
            config_id=config.id,
        )
    normalized_label = label.strip()
    if not normalized_label:
        raise AppError(422, "business_rule_violation", "LLM provider name is required", {"field": "label"})
    _ensure_unique_llm_config_label(db, team_id=team.id, label=normalized_label, current_config_id=config.id)
    config.label = normalized_label
    config.is_active = is_active
    config.updated_by_user_id = actor.id
    db.add(config)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_llm_label_conflict_if_needed(exc)
        raise
    db.refresh(config)
    _record_llm_audit(db, action="llm_config_details_updated", actor=actor, team_id=team.id, config_id=config.id, active=config.is_active)
    return config


def _llm_config_has_in_flight_jobs(db: Session, *, config_id: UUID) -> bool:
    generated_document_id = db.scalar(
        select(GeneratedDocument.id).where(
            or_(GeneratedDocument.llm_config_id == config_id, GeneratedDocument.backup_llm_config_id == config_id, GeneratedDocument.hallucination_check_selection_config_id == config_id),
            GeneratedDocument.status.in_([GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing]),
        ).limit(1)
    )
    if generated_document_id is not None:
        return True
    suggestion_job_id = db.scalar(
        select(TemplateSuggestionJob.id).where(
            or_(TemplateSuggestionJob.llm_config_id == config_id, TemplateSuggestionJob.backup_llm_config_id == config_id),
            TemplateSuggestionJob.status.in_([TemplateSuggestionStatus.queued, TemplateSuggestionStatus.processing]),
        ).limit(1)
    )
    if suggestion_job_id is not None:
        return True
    split_execution_id = db.scalar(
        select(ConsultationSplitExecution.id).where(
            or_(ConsultationSplitExecution.llm_config_id == config_id, ConsultationSplitExecution.backup_llm_config_id == config_id),
            ConsultationSplitExecution.status.in_(
                [ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing]
            ),
        ).limit(1)
    )
    if split_execution_id is not None:
        return True
    split_analysis_id = db.scalar(
        select(ConsultationSplitAnalysis.id).where(
            ConsultationSplitAnalysis.backup_llm_config_id == config_id,
            ConsultationSplitAnalysis.status.in_([ConsultationSplitAnalysisStatus.queued, ConsultationSplitAnalysisStatus.processing]),
        ).limit(1)
    )
    if split_analysis_id is not None:
        return True
    return db.scalar(
        select(ConsultationSplitBatch.id).where(
            or_(ConsultationSplitBatch.backup_llm_config_id == config_id, ConsultationSplitBatch.hallucination_check_selection_config_id == config_id),
            ConsultationSplitBatch.status.in_([
                ConsultationSplitBatchStatus.generation_queued,
                ConsultationSplitBatchStatus.generating,
                ConsultationSplitBatchStatus.verifying,
                ConsultationSplitBatchStatus.partially_ready,
            ]),
        ).limit(1)
    ) is not None


def _validate_in_flight_backup_models(
    db: Session, *, config: TeamLlmConfig, available_models: list[str],
) -> None:
    """Protect exact queued backup models without reading clinical content.

    The config lock must already be held, so queue-time freezing cannot add a
    reference between this check and credential replacement. Only the dedicated
    provider-metadata ciphertext is selected/decrypted; no source payloads are
    loaded on this administrator path. Corrupt metadata fails closed.
    """
    sources = (
        (GeneratedDocument, [GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing]),
        (TemplateSuggestionJob, [TemplateSuggestionStatus.queued, TemplateSuggestionStatus.processing]),
        (ConsultationSplitAnalysis, [ConsultationSplitAnalysisStatus.queued, ConsultationSplitAnalysisStatus.processing]),
        (ConsultationSplitBatch, [ConsultationSplitBatchStatus.generation_queued, ConsultationSplitBatchStatus.generating, ConsultationSplitBatchStatus.verifying]),
        (ConsultationSplitExecution, [ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing]),
    )
    for model, statuses in sources:
        rows = db.execute(select(
            model.id, model.owner_user_id, model.team_id,
            model.backup_provider_snapshot_encrypted,
        ).where(model.backup_llm_config_id == config.id, model.status.in_(statuses)))
        for row in rows:
            if row.team_id != config.team_id or not is_encrypted_envelope(row.backup_provider_snapshot_encrypted):
                raise AppError(409, "conflict", "Queued backup provider metadata is unavailable")
            snapshot = validate_provider_snapshot_shape(decrypt_json_for_owner(
                db, owner_user_id=row.owner_user_id, table=model.__tablename__,
                field="backup_provider_snapshot_encrypted", record_id=row.id,
                stored_value=row.backup_provider_snapshot_encrypted,
            ))
            if snapshot["llm_config_id"] != str(config.id):
                raise AppError(409, "conflict", "Queued backup provider metadata is unavailable")
            if snapshot["model"] not in available_models:
                raise AppError(409, "conflict", "Replacement credential does not expose a model used by queued or processing backup generation", {"field": "bearer_token", "config_id": str(config.id)})


def create_llm_config_draft(db: Session, actor: User, payload: LlmConfigDraftCreate) -> tuple[TeamLlmConfig, LlmConfigInspectResult]:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    team_id = team.id
    target = None
    target_id = None
    if payload.revision_of_config_id is not None:
        target = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.revision_of_config_id, TeamLlmConfig.team_id == team.id, TeamLlmConfig.revision_of_config_id.is_(None), TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready))
        if target is None:
            raise AppError(404, "not_found", "Ready LLM config not found", {"resource": "llm_config", "config_id": str(payload.revision_of_config_id)})
        target_id = target.id
        pending_revision = _pending_llm_revision(db, team_id=team_id, target_id=target_id)
        if pending_revision is not None:
            _raise_llm_pending_revision_conflict(target.id, pending_revision)
    provider_preset, adapter_kind, base_url, region = apply_provider_defaults(
        provider_preset=payload.provider_preset,
        base_url=payload.base_url,
        bedrock_region=payload.bedrock_region,
    )
    provider_preset = reclassify_preset_for_base_url(provider_preset, base_url)
    preset = get_llm_provider_preset(provider_preset)
    adapter_kind = preset.adapter_kind
    is_gemini = adapter_kind is LlmAdapterKind.gemini_enterprise
    bearer_token = payload.bearer_token
    inherits_bearer_token = not bearer_token and preset.requires_bearer_token and target is not None and bool(target.vault_secret_ref)
    if inherits_bearer_token:
        bearer_token = read_team_llm_bearer_token(
            team_id=team.id,
            config_id=target.id,
            secret_ref=target.vault_secret_ref,
        )
    if preset.requires_bearer_token and not bearer_token:
        raise AppError(422, "business_rule_violation", "This LLM provider requires an API key", {"field": "bearer_token"})
    google_credential_json = payload.google_service_account_json
    inherits_google_credential = (
        adapter_kind is LlmAdapterKind.gemini_enterprise
        and payload.google_auth_method == "service_account_json"
        and google_credential_json is None
        and target is not None
        and target.auth_mode is LlmAuthMode.google_service_account
        and bool(target.vault_secret_ref)
    )
    if inherits_google_credential:
        inherited_secret = read_team_llm_secret(
            team_id=team.id,
            config_id=target.id,
            secret_ref=target.vault_secret_ref,
        )
        candidate = inherited_secret.get("credential_json")
        if not isinstance(candidate, dict):
            raise AppError(502, "vault_read_failed", "Vault secret read failed")
        google_credential_json = candidate
    raw_label = (payload.label or "").strip()
    inspection = inspect_llm_contract(
        db,
        actor,
        LlmInspectRequest(
            team_id=team.id,
            provider_preset=provider_preset,
            adapter_kind=adapter_kind,
            base_url=base_url,
            bearer_token=bearer_token,
            bedrock_region=region,
            google_project_id=payload.google_project_id,
            google_location=payload.google_location,
            google_auth_method=payload.google_auth_method,
            google_service_account_json=google_credential_json,
            capacity_mode=payload.capacity_mode,
        ),
    )
    label = raw_label or (target.label if target is not None else default_llm_config_label(provider_display_name=inspection.provider_display_name, team_name=team.name))
    if target is None:
        _ensure_unique_llm_config_label(db, team_id=team.id, label=label)
    config = TeamLlmConfig(
        id=uuid4(),
        team_id=team.id,
        revision_of_config_id=target.id if target is not None else None,
        label=label,
        provider_preset=inspection.provider_preset,
        adapter_kind=inspection.adapter_kind,
        base_url=inspection.base_url,
        auth_mode=(
            _gemini_auth_mode(payload)
            if adapter_kind is LlmAdapterKind.gemini_enterprise
            else LlmAuthMode.bearer if preset.requires_bearer_token or payload.bearer_token else LlmAuthMode.none
        ),
        model_name=None,
        available_models_json=list(inspection.available_models),
        inspection_metadata_json=_inspection_metadata(inspection),
        provider_config_json=(dict(payload.provider_config_json) if adapter_kind is LlmAdapterKind.gemini_enterprise else {}),
        setup_status=LlmConfigSetupStatus.pending_model_selection,
        vault_secret_ref="pending" if bearer_token or google_credential_json else "",
        is_active=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(config)
    try:
        with provider_credential_write_transaction(db, kind=ProviderSecretCleanupKind.llm) as register_written_secret:
            db.flush()
            if bearer_token:
                if inherits_bearer_token:
                    written_secret_ref = write_team_llm_bearer_token(
                        team_id=team.id,
                        config_id=config.id,
                        bearer_token=bearer_token,
                        secret_id=uuid4(),
                    )
                else:
                    written_secret_ref = write_team_llm_bearer_token(
                        team_id=team.id,
                        config_id=config.id,
                        bearer_token=bearer_token,
                    )
                register_written_secret(written_secret_ref)
                config.vault_secret_ref = written_secret_ref
            elif google_credential_json:
                written_secret_ref = write_team_llm_secret(
                    team_id=team.id,
                    config_id=config.id,
                    secret_payload=google_service_account_secret(google_credential_json),
                    secret_id=uuid4() if inherits_google_credential else None,
                )
                register_written_secret(written_secret_ref)
                config.vault_secret_ref = written_secret_ref
    except IntegrityError as exc:
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint_name == "uq_team_llm_configs_pending_revision" and target_id is not None:
            pending_revision = _pending_llm_revision(db, team_id=team_id, target_id=target_id)
            _raise_llm_pending_revision_conflict(target_id, pending_revision)
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
    target = None
    if config.revision_of_config_id is not None:
        target = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == config.revision_of_config_id, TeamLlmConfig.team_id == team.id, TeamLlmConfig.revision_of_config_id.is_(None)).execution_options(populate_existing=True).with_for_update())
        if target is None:
            raise AppError(409, "conflict", "LLM revision target is unavailable")
    config = db.scalar(select(TeamLlmConfig).where(
        TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id,
    ).execution_options(populate_existing=True).with_for_update())
    if config is None or (target is not None and config.revision_of_config_id != target.id):
        raise AppError(409, "conflict", "LLM revision is unavailable")
    has_in_flight_jobs = _llm_config_has_in_flight_jobs(db, config_id=target.id if target is not None else config.id)
    if target is not None:
        ensure_no_legacy_checker_work_for_policy_change(db, team_id=team.id, config_id=target.id)
    if has_in_flight_jobs:
        raise AppError(409, "conflict", "Cannot edit this LLM config while generated documents are queued or processing", {"config_id": str(config.id)})
    model_name = payload.model_name.strip()
    available = list(config.available_models_json or [])
    if available and model_name not in available:
        raise AppError(422, "business_rule_violation", "Selected model is not available for this provider", {"field": "model_name"})
    if config.adapter_kind is LlmAdapterKind.gemini_enterprise and not available:
        provider_config = dict(config.provider_config_json or {})
        validate_gemini_model(
            project_id=str(provider_config.get("project_id") or ""),
            location=str(provider_config.get("location") or ""),
            credentials=resolve_llm_runtime_credential(config),
            capacity_mode=str(provider_config.get("capacity_mode") or "auto"),
            model=model_name,
        )
    label = payload.label.strip()
    _ensure_unique_llm_config_label(db, team_id=team.id, label=label, current_config_id=target.id if target is not None else config.id)
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
    result = config
    old_secret_ref = ""
    revision_secret_ref = ""
    rebound_secret_ref = ""
    try:
        with provider_credential_write_transaction(db, kind=ProviderSecretCleanupKind.llm) as register_written_secret:
            if target is not None:
                old_secret_ref = target.vault_secret_ref
                if config.vault_secret_ref and config.vault_secret_ref != old_secret_ref:
                    revision_secret_ref = config.vault_secret_ref
                    secret_payload = read_team_llm_secret(
                        team_id=team.id,
                        config_id=config.id,
                        secret_ref=revision_secret_ref,
                    )
                    rebound_secret_ref = write_team_llm_secret(
                        team_id=team.id,
                        config_id=target.id,
                        secret_payload=secret_payload,
                        secret_id=uuid4(),
                    )
                    register_written_secret(rebound_secret_ref)
            if rebound_secret_ref:
                config.vault_secret_ref = rebound_secret_ref
            if target is not None:
                for field in ("label", "provider_preset", "adapter_kind", "base_url", "auth_mode", "model_name", "available_models_json", "inspection_metadata_json", "provider_config_json", "setup_status", "vault_secret_ref", "is_active"):
                    setattr(target, field, getattr(config, field))
                target.updated_by_user_id = actor.id
                db.delete(config)
                result = target
            db.add(result)
            if target is None:
                _ensure_empty_provider_model_policy(db, config=result, actor_id=actor.id)
            if target is not None:
                queue_provider_secret_cleanup(
                    db,
                    kind=ProviderSecretCleanupKind.llm,
                    secret_refs=[
                        secret_ref
                        for secret_ref in (old_secret_ref, revision_secret_ref)
                        if secret_ref and secret_ref != result.vault_secret_ref
                    ],
                )
            db.commit()
    except IntegrityError as exc:
        _raise_llm_label_conflict_if_needed(exc)
        raise
    db.refresh(result)
    _record_llm_audit(db, action="llm_config_finalized", actor=actor, team_id=team.id, config_id=result.id, setup_status=_enum_value(result.setup_status), active=result.is_active)
    return result


def replace_llm_config_draft_credential(db: Session, actor: User, payload: LlmConfigDraftReplaceCredential) -> tuple[TeamLlmConfig, LlmConfigInspectResult]:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id).execution_options(populate_existing=True).with_for_update())
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(payload.config_id)})
    is_gemini = config.adapter_kind is LlmAdapterKind.gemini_enterprise
    if is_gemini and payload.bearer_token:
        raise AppError(422, "business_rule_violation", "Gemini Enterprise does not accept bearer tokens")
    if not is_gemini and not payload.bearer_token:
        raise AppError(422, "business_rule_violation", "This LLM provider requires a bearer token")
    has_in_flight_jobs = _llm_config_has_in_flight_jobs(db, config_id=config.id)
    replacement_auth_mode = (
        LlmAuthMode.google_adc if payload.google_auth_method == "application_default"
        else LlmAuthMode.google_service_account if is_gemini else LlmAuthMode.bearer
    )
    if has_in_flight_jobs and replacement_auth_mode is not config.auth_mode:
        raise AppError(409, "conflict", "Cannot change authentication type while dependent work is queued or processing")
    was_ready = config.setup_status == LlmConfigSetupStatus.ready
    was_active = config.is_active
    existing_model_name = config.model_name
    inspection = inspect_llm_contract(
        db,
        actor,
        LlmInspectRequest.from_persisted_config(
            config,
            bearer_token=payload.bearer_token,
            google_service_account_json=payload.google_service_account_json,
            google_auth_method=payload.google_auth_method if is_gemini else None,
        ),
        allow_disabled_provider=is_gemini,
    )
    if has_in_flight_jobs and was_ready and existing_model_name and existing_model_name not in inspection.available_models:
        raise AppError(
            409,
            "conflict",
            "Replacement credential does not expose the model used by queued or processing generated documents",
            {"field": "bearer_token", "model_name": existing_model_name, "config_id": str(config.id)},
        )
    if has_in_flight_jobs:
        _validate_in_flight_backup_models(db, config=config, available_models=list(inspection.available_models))
    old_secret_ref = config.vault_secret_ref
    try:
        with provider_credential_write_transaction(db, kind=ProviderSecretCleanupKind.llm) as register_written_secret:
            new_secret_ref = ""
            if payload.bearer_token:
                new_secret_ref = write_team_llm_bearer_token(
                    team_id=team.id,
                    config_id=config.id,
                    bearer_token=payload.bearer_token,
                    secret_id=uuid4(),
                )
                register_written_secret(new_secret_ref)
            elif payload.google_auth_method == "service_account_json":
                new_secret_ref = write_team_llm_secret(
                    team_id=team.id,
                    config_id=config.id,
                    secret_payload=google_service_account_secret(payload.google_service_account_json or {}),
                    secret_id=uuid4(),
                )
                register_written_secret(new_secret_ref)
            config.vault_secret_ref = new_secret_ref
            if is_gemini:
                config.auth_mode = (
                    LlmAuthMode.google_adc
                    if payload.google_auth_method == "application_default"
                    else LlmAuthMode.google_service_account
                )
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
            if old_secret_ref and old_secret_ref != new_secret_ref:
                queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.llm, secret_refs=[old_secret_ref])
            db.commit()
    except Exception:
        raise
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
    is_gemini = adapter_kind is LlmAdapterKind.gemini_enterprise
    config = None
    if payload.config_id is not None:
        config = db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == payload.config_id, TeamLlmConfig.team_id == team.id).execution_options(populate_existing=True).with_for_update())
        if config is None:
            raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(payload.config_id)})
    creating = config is None
    replacing_secret = payload.credential_action == "replace" or bool(payload.bearer_token) or bool(payload.google_service_account_json)
    removing_secret = payload.credential_action == "remove" or (is_gemini and payload.google_auth_method == "application_default")
    if removing_secret and payload.bearer_token:
        raise AppError(422, "business_rule_violation", "Bearer token cannot be supplied when credential_action is remove", {"field": "credential_action"})
    if replacing_secret and not payload.bearer_token and not payload.google_service_account_json:
        raise AppError(422, "business_rule_violation", "Bearer token is required when credential_action is replace", {"field": "bearer_token"})
    if removing_secret and preset.requires_bearer_token:
        raise AppError(422, "business_rule_violation", "This LLM provider requires a saved bearer token", {"field": "credential_action"})
    requires_saved_secret = preset.requires_bearer_token or (is_gemini and payload.google_auth_method == "service_account_json")
    has_existing_secret = config is not None and bool(config.vault_secret_ref)
    if has_existing_secret and not replacing_secret and not removing_secret:
        retained_secret_auth_mode = (
            LlmAuthMode.google_service_account
            if is_gemini and payload.google_auth_method == "service_account_json"
            else LlmAuthMode.bearer
        )
        if config.auth_mode is not retained_secret_auth_mode:
            raise AppError(
                422,
                "business_rule_violation",
                "Replace the saved credential when changing authentication type",
                {"field": "credential_action"},
            )
    if requires_saved_secret and not replacing_secret and not has_existing_secret:
        raise AppError(422, "business_rule_violation", "This LLM provider requires a saved bearer token", {"field": "bearer_token"})

    provider_endpoint_changed = (
        config is not None
        and (
            (config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url)) != provider_preset
            or config.adapter_kind != adapter_kind
            or config.base_url.rstrip("/") != base_url.rstrip("/")
            or dict(config.provider_config_json or {}) != (dict(payload.provider_config_json) if is_gemini else {})
        )
    )
    if config is not None and (
        provider_endpoint_changed or replacing_secret or removing_secret
        or (payload.model_name or "").strip() != (config.model_name or "")
    ):
        ensure_no_legacy_checker_work_for_policy_change(db, team_id=team.id, config_id=config.id)
    has_in_flight_jobs = config is not None and _llm_config_has_in_flight_jobs(db, config_id=config.id)
    incoming_auth_mode = (
        _gemini_auth_mode(payload) if is_gemini
        else LlmAuthMode.bearer if adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}
        or replacing_secret or (has_existing_secret and not removing_secret)
        else LlmAuthMode.none
    )
    if has_in_flight_jobs and incoming_auth_mode is not config.auth_mode:
        raise AppError(409, "conflict", "Cannot change authentication type while dependent work is queued or processing")
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
    if provider_endpoint_changed and not replacing_secret and has_existing_secret and not is_gemini:
        existing_token_for_discovery = read_team_llm_bearer_token(team_id=team.id, config_id=config.id, secret_ref=config.vault_secret_ref)

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
    elif adapter_kind is LlmAdapterKind.gemini_enterprise:
        if payload.google_service_account_json:
            gemini_credential = service_account_credentials_from_info(payload.google_service_account_json)
        elif config is not None and config.auth_mode is LlmAuthMode.google_service_account and config.vault_secret_ref:
            gemini_credential = resolve_llm_runtime_credential(config)
        else:
            gemini_credential = None
        provider_config = dict(payload.provider_config_json)
        try:
            available_models_json = discover_gemini_models(
                project_id=str(provider_config.get("project_id") or ""),
                location=str(provider_config.get("location") or ""),
                credentials=gemini_credential,
                capacity_mode=str(provider_config.get("capacity_mode") or "auto"),
            )
            discovery_metadata = _successful_discovery_metadata(
                provider_preset=provider_preset,
                models=available_models_json,
                empty_warning="No compatible Gemini models were returned. Enter a model ID manually.",
            )
        except AppError as exc:
            if exc.code in {"llm_invalid_credential", "llm_permission_denied", "llm_provider_api_disabled", "llm_location_unavailable"}:
                raise
            available_models_json = []
            discovery_metadata = _discovery_metadata(
                provider_preset=provider_preset,
                discovery_status="manual_required",
                default_model_source="manual",
                warnings=["Gemini model discovery is temporarily unavailable. Enter a model ID manually."],
                notes=[],
            )
    elif config is not None and not provider_endpoint_changed:
        available_models_json = list(config.available_models_json or [])
    else:
        available_models_json = []

    if credential_correction_during_in_flight:
        _validate_in_flight_backup_models(db, config=config, available_models=available_models_json)

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

    if adapter_kind is LlmAdapterKind.gemini_enterprise:
        resolved_auth_mode = _gemini_auth_mode(payload)
    elif adapter_kind in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
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
            provider_config_json=dict(payload.provider_config_json) if is_gemini else {},
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
        _ensure_empty_provider_model_policy(db, config=config, actor_id=actor.id)
    else:
        provider_config = dict(payload.provider_config_json) if is_gemini else {}
        checker_relevant_change = (
            config.model_name != model_name
            or list(config.available_models_json or []) != list(available_models_json)
            or provider_endpoint_changed
            or config.provider_preset != provider_preset
            or config.adapter_kind != adapter_kind
            or config.auth_mode != resolved_auth_mode
            or dict(config.provider_config_json or {}) != provider_config
            or config.is_active != payload.is_active
            or replacing_secret
            or removing_secret
        )
        if checker_relevant_change:
            ensure_no_legacy_checker_work_for_policy_change(
                db,
                team_id=config.team_id,
                config_id=config.id,
            )
        config.label = label
        config.provider_preset = provider_preset
        config.adapter_kind = adapter_kind
        config.base_url = base_url
        config.auth_mode = resolved_auth_mode
        config.model_name = model_name
        config.available_models_json = available_models_json
        config.inspection_metadata_json = discovery_metadata or dict(config.inspection_metadata_json or {})
        config.provider_config_json = provider_config
        config.setup_status = LlmConfigSetupStatus.ready
        config.is_active = config.is_active if credential_correction_during_in_flight else payload.is_active
        config.updated_by_user_id = actor.id
        db.add(config)

    deleted_secret_ref = ""
    pending_secret_ref = ""
    old_secret_ref = config.vault_secret_ref if not creating else ""
    try:
        with provider_credential_write_transaction(db, kind=ProviderSecretCleanupKind.llm) as register_written_secret:
            if replacing_secret and payload.bearer_token:
                pending_secret_ref = write_team_llm_bearer_token(
                    team_id=team.id,
                    config_id=config.id,
                    bearer_token=payload.bearer_token,
                    secret_id=uuid4() if not creating else None,
                )
                register_written_secret(pending_secret_ref)
                config.vault_secret_ref = pending_secret_ref
            elif replacing_secret and payload.google_service_account_json:
                pending_secret_ref = write_team_llm_secret(
                    team_id=team.id,
                    config_id=config.id,
                    secret_payload=google_service_account_secret(payload.google_service_account_json),
                    secret_id=uuid4() if not creating else None,
                )
                register_written_secret(pending_secret_ref)
                config.vault_secret_ref = pending_secret_ref
            elif removing_secret:
                if config.vault_secret_ref:
                    deleted_secret_ref = config.vault_secret_ref
                config.vault_secret_ref = ""
            elif adapter_kind in {LlmAdapterKind.ollama_chat, LlmAdapterKind.gemini_enterprise} and creating:
                config.vault_secret_ref = ""
            elif creating:
                raise AppError(422, "business_rule_violation", "Bearer token is required when creating the LLM config", {"field": "bearer_token"})

            if deleted_secret_ref:
                queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.llm, secret_refs=[deleted_secret_ref])
            elif old_secret_ref and old_secret_ref != pending_secret_ref:
                queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.llm, secret_refs=[old_secret_ref])
    except IntegrityError as exc:
        _raise_llm_label_conflict_if_needed(exc)
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
    # Queueing takes this same lock before it inserts its execution.  That
    # prevents a guard-then-SET-NULL race from detaching newly queued work.
    config = db.scalar(
        select(TeamLlmConfig).where(TeamLlmConfig.id == config.id).execution_options(populate_existing=True).with_for_update()
    )
    if config is None:
        raise AppError(404, "not_found", "LLM config not found", {"resource": "llm_config", "config_id": str(config_id)})
    if _llm_config_has_in_flight_jobs(db, config_id=config.id):
        raise AppError(
            409,
            "conflict",
            "Cannot delete this LLM config while dependent work is queued or processing",
            {"config_id": str(config.id)},
        )
    ensure_no_legacy_checker_work_for_policy_change(db, team_id=config.team_id, config_id=config.id)
    # The FK uses ON DELETE SET NULL, but its encrypted snapshot is paired
    # metadata. Retire both on terminal sources so a later retry can proceed
    # with its frozen primary provider instead of treating the detached
    # snapshot as corruption.
    retire_backup_snapshots_for_config(db, config_id=config.id)
    retire_checker_snapshots_for_config(db, config_id=config.id)
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.llm_config_id == config.id))
    if selection is not None:
        db.delete(selection)
        db.flush()
    backup_selection = db.scalar(select(TeamLlmBackupSelection).where(TeamLlmBackupSelection.llm_config_id == config.id))
    if backup_selection is not None:
        db.delete(backup_selection)
        db.flush()
    revisions = list(db.scalars(select(TeamLlmConfig).where(TeamLlmConfig.revision_of_config_id == config.id))) if config.revision_of_config_id is None else []
    revision_secrets = [item.vault_secret_ref for item in revisions if item.vault_secret_ref]
    deleted_secret_ref = config.vault_secret_ref
    delete_after_commit = bool(deleted_secret_ref)
    team_id = config.team_id
    deleted_config_id = config.id
    queue_provider_secret_cleanup(
        db,
        kind=ProviderSecretCleanupKind.llm,
        secret_refs=[*revision_secrets, deleted_secret_ref],
    )
    db.delete(config)
    db.commit()
    _record_llm_audit(db, action="llm_config_deleted", actor=actor, team_id=team_id, config_id=deleted_config_id)


def cancel_llm_config_draft(db: Session, actor: User, *, config_id: UUID, team_id: UUID) -> None:
    config = get_llm_config(db, actor, config_id=config_id, team_id=team_id)
    if config.setup_status != LlmConfigSetupStatus.pending_model_selection:
        raise AppError(409, "conflict", "Only a pending LLM setup draft can be cancelled")
    delete_llm_config(db, actor, config_id=config_id, team_id=team_id)


def list_selectable_llm_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamLlmConfig]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    stmt = (
        select(TeamLlmConfig)
        .where(
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.revision_of_config_id.is_(None),
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
        )
        .order_by(TeamLlmConfig.created_at.desc(), TeamLlmConfig.id.desc())
    )
    return [config for config in db.scalars(stmt) if provider_model_policy_state(db, config=config)["default_model_valid"]]


def get_team_llm_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> TeamLlmSelection | None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    return db.scalar(select(TeamLlmSelection).options(joinedload(TeamLlmSelection.config)).where(TeamLlmSelection.team_id == team.id))


def set_team_llm_selection(db: Session, actor: User, payload: LlmSelectionUpsert) -> TeamLlmSelection:
    team = _resolve_selection_scoped_team(db, actor, team_id=payload.team_id)
    config = db.scalar(
        select(TeamLlmConfig).where(
            TeamLlmConfig.id == payload.llm_config_id,
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.revision_of_config_id.is_(None),
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
        ).execution_options(populate_existing=True).with_for_update()
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable LLM config not found", {"resource": "llm_config", "config_id": str(payload.llm_config_id)})
    policy = get_provider_model_policy(db, config=config)
    if policy is None or not model_allowed_for_provider(db, config=config, model_name=policy.default_model_name):
        raise AppError(422, "business_rule_violation", "This provider has no valid enabled default model", {"resource": "llm_provider_model_policy"})
    allowed_models = list(policy.enabled_models_json or [])
    override = policy.default_model_name
    # Deprecated request fields may only echo the saved policy. They cannot
    # change global provider allowances through the team-selection endpoint.
    if payload.allowed_models_json is not None and list(dict.fromkeys(payload.allowed_models_json)) != allowed_models:
        raise AppError(409, "conflict", "Model allowances are managed in provider settings", {"resource": "llm_provider_model_policy"})
    if payload.model_name_override is not None and payload.model_name_override.strip() != override:
        raise AppError(409, "conflict", "The writing default is managed in provider settings", {"resource": "llm_provider_model_policy"})
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id).execution_options(populate_existing=True).with_for_update())
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
    selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id).execution_options(populate_existing=True).with_for_update())
    if selection is None:
        raise AppError(404, "not_found", "LLM selection not found", {"resource": "llm_selection", "team_id": str(team.id)})
    db.delete(selection)
    db.commit()
    _record_llm_audit(db, action="llm_selection_cleared", actor=actor, team_id=team.id)


def get_team_llm_backup_selection(db: Session, actor: User, *, team_id: UUID) -> TeamLlmBackupSelection | None:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    return db.scalar(
        select(TeamLlmBackupSelection)
        .options(joinedload(TeamLlmBackupSelection.config))
        .where(TeamLlmBackupSelection.team_id == team.id)
    )


def set_team_llm_backup_selection(
    db: Session, actor: User, payload: LlmBackupSelectionUpsert
) -> TeamLlmBackupSelection | None:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    if payload.llm_config_id is None:
        current = db.scalar(select(TeamLlmBackupSelection).where(
            TeamLlmBackupSelection.team_id == team.id,
        ).execution_options(populate_existing=True).with_for_update())
        if current is None:
            return None
        deleted_config_id = current.llm_config_id
        db.delete(current)
        db.commit()
        _record_llm_audit(db, action="llm_backup_selection_cleared", actor=actor, team_id=team.id, config_id=deleted_config_id)
        return None

    config = db.scalar(
        select(TeamLlmConfig).where(
            TeamLlmConfig.id == payload.llm_config_id,
            TeamLlmConfig.team_id == team.id,
            TeamLlmConfig.revision_of_config_id.is_(None),
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
        ).execution_options(populate_existing=True).with_for_update()
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable backup LLM config not found", {"resource": "llm_config", "config_id": str(payload.llm_config_id)})
    current = db.scalar(select(TeamLlmBackupSelection).where(
        TeamLlmBackupSelection.team_id == team.id,
    ).execution_options(populate_existing=True).with_for_update())
    get_provider_model_policy(db, config=config, lock=True)
    model_name = (payload.model_name or "").strip()
    if not model_allowed_for_provider(db, config=config, model_name=model_name):
        raise AppError(422, "business_rule_violation", "Selected backup model is not enabled for this provider", {"field": "model_name"})
    if current is None:
        current = TeamLlmBackupSelection(
            team_id=team.id,
            llm_config_id=config.id,
            model_name=model_name,
        )
    else:
        current.llm_config_id = config.id
        current.model_name = model_name
    db.add(current)
    db.commit()
    db.refresh(current)
    _record_llm_audit(db, action="llm_backup_selection_set", actor=actor, team_id=team.id, config_id=config.id)
    return db.scalar(
        select(TeamLlmBackupSelection)
        .options(joinedload(TeamLlmBackupSelection.config))
        .where(TeamLlmBackupSelection.team_id == team.id)
    ) or current


def snapshot_team_llm_backup(db: Session, *, team_id: UUID) -> dict[str, object] | None:
    """Return an eligible team's backup as the standard non-secret provider snapshot."""
    selection = db.scalar(
        select(TeamLlmBackupSelection)
        .options(joinedload(TeamLlmBackupSelection.config))
        .where(TeamLlmBackupSelection.team_id == team_id)
    )
    if selection is None:
        return None
    config = selection.config
    if config is None or selection.llm_config_id is None:
        return None
    if (
        config.team_id != team_id
        or config.revision_of_config_id is not None
        or not config.is_active
        or config.setup_status != LlmConfigSetupStatus.ready
        or not model_allowed_for_provider(db, config=config, model_name=selection.model_name)
    ):
        return None
    return build_provider_snapshot(config=config, model=selection.model_name).to_dict()


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
            TeamLlmConfig.revision_of_config_id.is_(None),
            TeamLlmConfig.is_active.is_(True),
            TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
        ).execution_options(populate_existing=True).with_for_update()
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable hallucination checker LLM config not found", {"resource": "llm_config", "config_id": str(payload.llm_config_id)})
    selection = db.scalar(select(TeamHallucinationCheckSelection).where(
        TeamHallucinationCheckSelection.team_id == team.id,
    ).execution_options(populate_existing=True).with_for_update())
    ensure_no_legacy_checker_work_for_policy_change(db, team_id=team.id)
    get_provider_model_policy(db, config=config, lock=True)
    override = payload.model_name_override.strip() if payload.model_name_override else None
    if override is None:
        policy = get_provider_model_policy(db, config=config)
        override = policy.default_model_name if policy else None
    if not model_allowed_for_provider(db, config=config, model_name=override):
        raise AppError(422, "business_rule_violation", "Choose an enabled model for the hallucination checker", {"field": "model_name_override"})
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
    current_config_id = db.scalar(select(TeamHallucinationCheckSelection.llm_config_id).where(TeamHallucinationCheckSelection.team_id == team.id))
    if current_config_id is not None:
        db.scalar(select(TeamLlmConfig.id).where(TeamLlmConfig.id == current_config_id).execution_options(populate_existing=True).with_for_update())
    selection = db.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id).execution_options(populate_existing=True).with_for_update())
    if selection is None:
        raise AppError(404, "not_found", "Hallucination checker selection not found", {"resource": "hallucination_check_selection", "team_id": str(team.id)})
    ensure_no_legacy_checker_work_for_policy_change(db, team_id=team.id)
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
    ):
        raise AppError(422, "business_rule_violation", "No active LLM selection for team", {"team_id": str(team_id)})
    return selection


def resolve_team_llm(db: Session, *, team_id: UUID) -> tuple[TeamLlmSelection, TeamLlmConfig, str | None]:
    selection = active_team_llm_selection(db, team_id=team_id)
    config = selection.config
    policy = get_provider_model_policy(db, config=config)
    resolved_model_name = policy.default_model_name if policy and model_allowed_for_provider(db, config=config, model_name=policy.default_model_name) else None
    return selection, config, resolved_model_name


def resolve_effective_user_llm_for_new_work(
    db: Session, actor: User
) -> tuple[TeamLlmSelection | None, TeamLlmConfig | None, str | None, UserLlmPreference | None, bool]:
    """Resolve the accepted writing choice with personal preference and backup.

    Personal preferences apply only to the active writing provider. An
    unusable primary never carries that preference to the backup provider.
    The boolean marks backup resolution; callers still freeze snapshots and
    reserve quotas at their established work-acceptance boundary.
    """
    _resolve_user_preference_scope(actor)
    if actor.team_id is None:
        raise AppError(403, "forbidden", "Current user does not belong to a team")
    selection = db.scalar(select(TeamLlmSelection).options(
        joinedload(TeamLlmSelection.config),
    ).where(TeamLlmSelection.team_id == actor.team_id))
    preference = db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))
    config = selection.config if selection is not None else None
    if (
        config is not None
        and config.team_id == actor.team_id
        and config.revision_of_config_id is None
        and config.is_active
        and config.setup_status == LlmConfigSetupStatus.ready
    ):
        preferred = preference.preferred_model_name if preference is not None else None
        if preferred and model_allowed_for_provider(db, config=config, model_name=preferred):
            return selection, config, preferred, preference, False
        policy = get_provider_model_policy(db, config=config)
        default = policy.default_model_name if policy is not None else None
        if model_allowed_for_provider(db, config=config, model_name=default):
            return selection, config, default, preference, False

    backup = db.scalar(select(TeamLlmBackupSelection).options(
        joinedload(TeamLlmBackupSelection.config),
    ).where(TeamLlmBackupSelection.team_id == actor.team_id))
    backup_config = backup.config if backup is not None else None
    if (
        backup is not None
        and backup_config is not None
        and backup_config.team_id == actor.team_id
        and backup_config.revision_of_config_id is None
        and backup_config.is_active
        and backup_config.setup_status == LlmConfigSetupStatus.ready
        and model_allowed_for_provider(db, config=backup_config, model_name=backup.model_name)
    ):
        return selection, backup_config, backup.model_name, preference, True
    return selection, config, None, preference, False


def get_user_llm_preference(db: Session, actor: User) -> UserLlmPreference | None:
    _resolve_user_preference_scope(actor)
    return db.scalar(select(UserLlmPreference).where(UserLlmPreference.user_id == actor.id))


def set_user_llm_preference(db: Session, actor: User, payload: UserLlmPreferenceUpsert) -> UserLlmPreference:
    _resolve_user_preference_scope(actor)
    if actor.team_id is None:
        raise AppError(403, "forbidden", "Current user does not belong to a team")
    selection = active_team_llm_selection(db, team_id=actor.team_id)
    preferred_model_name = payload.preferred_model_name.strip() if payload.preferred_model_name else None
    if preferred_model_name:
        if not model_allowed_for_provider(db, config=selection.config, model_name=preferred_model_name):
            raise AppError(
                422,
                "business_rule_violation",
                "The active team LLM provider does not currently expose selectable models",
                {"field": "preferred_model_name"},
            )
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
    if preferred_model and not model_allowed_for_provider(db, config=config, model_name=preferred_model):
        preferred_model = None
    return selection, config, preferred_model or team_model, preference


def read_active_team_llm_bearer_token(db: Session, *, team_id: UUID) -> str:
    _, config, _ = resolve_team_llm(db, team_id=team_id)
    if config.auth_mode is not LlmAuthMode.bearer or not config.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "No stored API key is configured for this LLM provider", {"team_id": str(team_id)})
    return read_team_llm_bearer_token(team_id=team_id, config_id=config.id, secret_ref=config.vault_secret_ref)
