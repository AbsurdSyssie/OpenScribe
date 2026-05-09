import io
import logging
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import SttAdapterKind, SttSelectionPurpose, Team, TeamRole, TeamSttConfig, TeamSttSelection, TranscriptIngestionJob, TranscriptIngestionJobStatus, User
from app.schemas import (
    SttConfigUpsert,
    SttInspectFieldTip,
    SttInspectRequest,
    SttInspectResult,
    SttModelOption,
    SttSelectionUpsert,
)
from app.services.vault import delete_team_stt_bearer_token, read_team_stt_bearer_token, write_team_stt_bearer_token
from app.services.provider_inspection import (
    dereference_openapi_document,
    display_default_from_schema_property,
    extract_json_path,
    fetch_openapi_document,
    operation_request_schema,
    operation_response_schema,
)


SUPPORTED_OPENAI_TRANSCRIPTION_MODELS = (
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "gpt-4o-transcribe-diarize",
    "whisper-1",
)


logger = logging.getLogger("openscribe.stt")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STT_SAMPLE_PATH = REPO_ROOT / "tests" / "MoreOrLess.wav"


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
    stmt = select(TeamSttConfig).where(TeamSttConfig.team_id == team.id).order_by(TeamSttConfig.created_at.desc(), TeamSttConfig.id.desc())
    return list(db.scalars(stmt))


def get_stt_config(db: Session, actor: User, *, config_id: UUID, team_id: UUID | None = None) -> TeamSttConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=team_id)
    config = db.scalar(select(TeamSttConfig).where(TeamSttConfig.id == config_id, TeamSttConfig.team_id == team.id))
    if config is None:
        raise AppError(404, "not_found", "STT config not found", {"resource": "stt_config", "config_id": str(config_id)})
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
    selections = list(db.scalars(select(TeamSttSelection).where(TeamSttSelection.stt_config_id == config.id)))
    for selection in selections:
        db.delete(selection)
    if selections:
        db.flush()
    delete_team_stt_bearer_token(team_id=config.team_id, config_id=config.id)
    db.delete(config)
    db.commit()


def list_selectable_stt_configs(db: Session, actor: User, *, team_id: UUID | None = None) -> list[TeamSttConfig]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    stmt = select(TeamSttConfig).where(TeamSttConfig.team_id == team.id, TeamSttConfig.is_active.is_(True)).order_by(TeamSttConfig.created_at.desc(), TeamSttConfig.id.desc())
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
            TeamSttConfig.is_active.is_(True),
        )
    )
    if config is None:
        raise AppError(404, "not_found", "Selectable STT config not found", {"resource": "stt_config", "config_id": str(payload.stt_config_id)})
    ensure_stt_config_credential_ready(team_id=team.id, config=config)

    provider_models = list(config.available_models_json or [])
    override = payload.model_name_override.strip() if payload.model_name_override else None
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
            language_override=payload.language_override.strip() if payload.language_override else None,
            selected_by_user_id=actor.id,
        )
        db.add(selection)
    else:
        selection.purpose = payload.purpose
        selection.stt_config_id = config.id
        selection.model_name_override = override
        selection.language_override = payload.language_override.strip() if payload.language_override else None
        selection.selected_by_user_id = actor.id
        db.add(selection)

    db.commit()
    db.refresh(selection)
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
    if selection is None or selection.config is None or not selection.config.is_active:
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
    resolved_model_name = selection.model_name_override or config.model_name
    if provider_models:
        if resolved_model_name not in provider_models:
            resolved_model_name = provider_models[0]
    resolved_language = selection.language_override or config.language
    return selection, config, resolved_model_name, resolved_language


def _missing_stt_credential_error(*, team_id: UUID, config_id: UUID) -> AppError:
    return AppError(
        409,
        "stt_config_secret_missing",
        "The selected STT configuration is missing its saved credential. Ask a system admin to re-save the STT endpoint, or save it without a credential if the endpoint does not require auth.",
        {"team_id": str(team_id), "config_id": str(config_id)},
    )


def _read_saved_stt_bearer_token(*, team_id: UUID, config: TeamSttConfig) -> str | None:
    if not config.vault_secret_ref:
        if config.adapter_kind is SttAdapterKind.openai_cloud:
            raise _missing_stt_credential_error(team_id=team_id, config_id=config.id)
        return None
    try:
        return read_team_stt_bearer_token(team_id=team_id, config_id=config.id)
    except AppError as exc:
        if exc.code == "vault_read_failed":
            raise _missing_stt_credential_error(team_id=team_id, config_id=config.id) from exc
        raise


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
    if adapter_kind is SttAdapterKind.openai_cloud:
        raise _missing_stt_credential_error(team_id=team_id, config_id=stt_config_id)
    return None


def ensure_stt_config_credential_ready(*, team_id: UUID, config: TeamSttConfig) -> None:
    _read_saved_stt_bearer_token(team_id=team_id, config=config)


def _normalized_known_adapter_fields(adapter_kind: SttAdapterKind) -> tuple[str, str, str]:
    if adapter_kind in {SttAdapterKind.openai_cloud, SttAdapterKind.openai_compatible_rest}:
        return "/v1/audio/transcriptions", "file", "text"
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
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models_page = client.models.list()
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "stt_inspection_failed", "Could not load available OpenAI transcription models") from exc

    return sorted(
        {
            model.id
            for model in getattr(models_page, "data", [])
            if getattr(model, "id", None) in SUPPORTED_OPENAI_TRANSCRIPTION_MODELS
        }
    )


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
    segments = payload.get("segments")
    if isinstance(segments, list):
        paragraphs = paragraphize_timestamped_segments(segments)
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


def ensure_stt_service_healthy(
    *,
    adapter_kind: SttAdapterKind,
    base_url: str,
    bearer_token: str | None = None,
    healthcheck_url: str | None = None,
) -> None:
    if adapter_kind is SttAdapterKind.openai_cloud or not healthcheck_url:
        return
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
        status_code = exc.response.status_code if exc.response is not None else None
        return AppError(
            502,
            "stt_request_failed",
            "STT provider request failed",
            {"status_code": status_code},
        )
    return AppError(502, "stt_unavailable", "STT provider is unavailable")


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
        details["provider_error_code"] = "http_status_error"
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
) -> str:
    url = f"{base_url.rstrip('/')}{transcribe_path}"
    form_fields = dict(extra_form_fields_json or {})
    if model_name and model_field_name:
        form_fields[model_field_name] = model_name
    if language and language_field_name:
        form_fields[language_field_name] = language
    try:
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        response = httpx.post(
            url,
            headers=headers,
            data=form_fields,
            files={file_field_name: (filename, audio_bytes, content_type)},
            timeout=60.0,
        )
        response.raise_for_status()
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
    try:
        payload = response.json()
    except ValueError as exc:
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
        raise AppError(502, "stt_response_invalid", "STT provider response was not valid JSON") from exc
    return _format_timestamped_transcript_payload(payload, response_text_path=response_text_path)


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
    client = OpenAI(api_key=bearer_token, base_url=base_url)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    kwargs: dict[str, Any] = {
        "file": audio_file,
        "model": model_name or "whisper-1",
    }
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


def transcribe_with_team_stt(
    db: Session,
    *,
    team_id: UUID,
    purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
) -> str:
    _, config, resolved_model_name, resolved_language = resolve_selected_team_stt(db, team_id=team_id, purpose=purpose)
    bearer_token = _read_saved_stt_bearer_token(team_id=team_id, config=config)
    if config.adapter_kind is SttAdapterKind.openai_cloud:
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
        model_name=resolved_model_name,
        model_field_name=config.model_field_name or "model",
        language=resolved_language,
        language_field_name=config.language_field_name or "language",
        audio_bytes=audio_bytes,
        filename=filename,
        content_type=content_type,
    )


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
    model_field_name: str | None = None,
    language_field_name: str | None = None,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
) -> str:
    if not stt_config_id or not adapter_kind or not base_url:
        return transcribe_with_team_stt(
            db,
            team_id=team_id,
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
        )
    resolved_adapter = SttAdapterKind(adapter_kind)
    bearer_token = _read_stt_snapshot_bearer_token(
        db,
        team_id=team_id,
        stt_config_id=stt_config_id,
        adapter_kind=resolved_adapter,
    )
    if resolved_adapter is SttAdapterKind.openai_cloud:
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
        model_name=model_name,
        model_field_name=model_field_name or "model",
        language=language,
        language_field_name=language_field_name or "language",
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

    started_at = monotonic()
    health_status = "skipped"
    health_url = None
    try:
        bearer_token = _read_saved_stt_bearer_token(team_id=config.team_id, config=config)
        transcript_text = (
            _transcribe_via_openai_cloud(
                base_url=config.base_url,
                extra_form_fields_json=config.extra_form_fields_json,
                bearer_token=bearer_token,
                model_name=config.model_name,
                model_field_name=config.model_field_name or "model",
                language=config.language,
                language_field_name=config.language_field_name or "language",
                audio_bytes=audio_bytes,
                filename=sample_path.name,
            )
            if config.adapter_kind is SttAdapterKind.openai_cloud
            else _transcribe_via_http(
                base_url=config.base_url,
                transcribe_path=config.transcribe_path,
                file_field_name=config.file_field_name,
                response_text_path=config.response_text_path,
                extra_form_fields_json=config.extra_form_fields_json,
                bearer_token=bearer_token,
                model_name=config.model_name,
                language=config.language,
                audio_bytes=audio_bytes,
                filename=sample_path.name,
                content_type="audio/wav",
            )
        )
        return {
            "success": True,
            "health_status": health_status,
            "sample_filename": sample_path.name,
            "sample_size_bytes": len(audio_bytes),
            "health_url": health_url,
            "transcribe_url": f"{config.base_url.rstrip('/')}{config.transcribe_path}",
            "model_name": config.model_name,
            "language": config.language,
            "duration_ms": int((monotonic() - started_at) * 1000),
            "transcript_text": transcript_text,
            "error_code": None,
            "error_message": None,
        }
    except AppError as exc:
        return {
            "success": False,
            "health_status": health_status,
            "sample_filename": sample_path.name,
            "sample_size_bytes": len(audio_bytes),
            "health_url": health_url,
            "transcribe_url": f"{config.base_url.rstrip('/')}{config.transcribe_path}",
            "model_name": config.model_name,
            "language": config.language,
            "duration_ms": int((monotonic() - started_at) * 1000),
            "transcript_text": None,
            "error_code": exc.code,
            "error_message": exc.message,
        }


def upsert_stt_config(db: Session, actor: User, payload: SttConfigUpsert) -> TeamSttConfig:
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
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

    available_models_json: list[str] = []
    if payload.adapter_kind is SttAdapterKind.openai_cloud:
        if payload.bearer_token:
            try:
                available_models_json = _list_openai_transcription_models(api_key=payload.bearer_token, base_url=payload.base_url)
            except AppError:
                available_models_json = _fallback_openai_transcription_models()
        elif config is not None:
            available_models_json = list(config.available_models_json or [])

    if config is None:
        config = TeamSttConfig(
            id=uuid4(),
            team_id=team.id,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
            label=payload.label.strip(),
            adapter_kind=payload.adapter_kind,
            base_url=payload.base_url,
            transcribe_path=payload.transcribe_path,
            auth_mode=payload.auth_mode,
            model_name=payload.model_name.strip() if payload.model_name else None,
            model_field_name=payload.model_field_name or ("model" if payload.model_name else None),
            available_models_json=available_models_json,
            file_field_name=payload.file_field_name.strip(),
            language=payload.language.strip() if payload.language else None,
            language_field_name=payload.language_field_name or ("language" if payload.language else None),
            response_text_path=payload.response_text_path.strip(),
            segments_path=payload.segments_path,
            segment_text_field=payload.segment_text_field,
            segment_start_field=payload.segment_start_field,
            segment_end_field=payload.segment_end_field,
            segment_speaker_field=payload.segment_speaker_field,
            extra_form_fields_json=payload.extra_form_fields_json,
            vault_secret_ref="pending" if payload.bearer_token or payload.adapter_kind is SttAdapterKind.openai_cloud else "",
            is_active=payload.is_active,
        )
        db.add(config)
        db.flush()
    else:
        config.label = payload.label.strip()
        config.adapter_kind = payload.adapter_kind
        config.base_url = payload.base_url
        config.transcribe_path = payload.transcribe_path
        config.auth_mode = payload.auth_mode
        config.model_name = payload.model_name.strip() if payload.model_name else None
        config.model_field_name = payload.model_field_name or ("model" if payload.model_name else None)
        config.available_models_json = available_models_json or list(config.available_models_json or [])
        config.file_field_name = payload.file_field_name.strip()
        config.language = payload.language.strip() if payload.language else None
        config.language_field_name = payload.language_field_name or ("language" if payload.language else None)
        config.response_text_path = payload.response_text_path.strip()
        config.segments_path = payload.segments_path
        config.segment_text_field = payload.segment_text_field
        config.segment_start_field = payload.segment_start_field
        config.segment_end_field = payload.segment_end_field
        config.segment_speaker_field = payload.segment_speaker_field
        config.extra_form_fields_json = payload.extra_form_fields_json
        config.is_active = payload.is_active
        config.updated_by_user_id = actor.id
        db.add(config)

    if payload.bearer_token:
        config.vault_secret_ref = write_team_stt_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
    elif payload.adapter_kind in {SttAdapterKind.generic_rest, SttAdapterKind.openai_compatible_rest}:
        if config.vault_secret_ref:
            delete_team_stt_bearer_token(team_id=team.id, config_id=config.id)
        config.vault_secret_ref = ""
    elif payload.adapter_kind is SttAdapterKind.openai_cloud and not config.vault_secret_ref:
        raise AppError(422, "business_rule_violation", "Bearer token is required for OpenAI Cloud STT configs", {"field": "bearer_token"})

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
