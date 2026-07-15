from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models import (
    ClinicalEntityRun,
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    DeidentificationProvider,
    ProviderSecretCleanupKind,
    Team,
    TeamClinicalNlpSelection,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    TeamRole,
    User,
)
from app.schemas import (
    DeidentificationInspectEntity,
    DeidentificationInspectFieldTip,
    DeidentificationInspectResult,
    ClinicalNlpSelectionUpsert,
    DeidentificationProviderAssignmentUpsert,
    DeidentificationProviderInspectRequest,
    DeidentificationProviderUpsert,
    DeidentificationSelectionUpsert,
)
from app.services.vault import (
    delete_deidentification_bearer_token,
    read_deidentification_bearer_token,
    write_deidentification_bearer_token,
)
from app.services.security_audit import record_security_event
from app.services.provider_secret_cleanup import queue_orphan_provider_secret_after_rollback, queue_provider_secret_cleanup


BUILTIN_DEIDENTIFICATION_PROVIDER_ID = UUID("00000000-0000-0000-0000-00000000d1d1")


def _record_deid_audit(db: Session, *, action: str, actor: User, provider_id: UUID | None = None, team_id: UUID | None = None, outcome: str = "success", **details: Any) -> None:
    payload: dict[str, Any] = {"category": "provider", "outcome": outcome, "provider_type": "deidentification"}
    if provider_id is not None:
        payload.update({"object_type": "deidentification_provider", "object_id": str(provider_id)})
    payload.update(details)
    record_security_event(db, action=action, actor=actor, team_id=team_id, details=payload)


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
            return _dereference(document, _resolve_openapi_pointer(document, str(value["$ref"])))
        return {key: _dereference(document, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dereference(document, item) for item in value]
    return value


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


def _candidate_openapi_paths(detect_path: str) -> list[str]:
    normalized = detect_path.strip() or "/openapi.json"
    if normalized.endswith("/docs"):
        prefix = normalized[: -len("/docs")]
        return [f"{prefix or ''}/openapi.json"]
    if normalized.endswith("/redoc"):
        prefix = normalized[: -len("/redoc")]
        return [f"{prefix or ''}/openapi.json"]
    if normalized.endswith("/openapi.json") or normalized.endswith(".json"):
        return [normalized]
    return [normalized, "/openapi.json"]


def _openapi_lookup_path(payload: DeidentificationProviderInspectRequest) -> str:
    return (payload.openapi_path or payload.detect_path or "/openapi.json").strip()


def _looks_like_openapi_path(path: str) -> bool:
    normalized = path.strip().lower()
    return normalized.endswith("/docs") or normalized.endswith("/redoc") or normalized.endswith("/openapi.json") or normalized.endswith(".json")


def _fetch_openapi_document(payload: DeidentificationProviderInspectRequest) -> tuple[dict[str, Any], str]:
    headers = {}
    if payload.bearer_token:
        headers["Authorization"] = f"Bearer {payload.bearer_token}"
    last_status = None
    for path in _candidate_openapi_paths(_openapi_lookup_path(payload)):
        url = f"{payload.base_url.rstrip('/')}{path}"
        try:
            response = httpx.get(url, headers=headers, timeout=10.0)
        except httpx.HTTPError:
            continue
        last_status = response.status_code
        if response.status_code in {401, 403}:
            raise AppError(401, "unauthorized", "De-identification OpenAPI document rejected the provided credentials")
        if response.status_code >= 400:
            continue
        try:
            document = response.json()
        except ValueError:
            continue
        if isinstance(document, dict) and "paths" in document:
            return document, path
    details = {"status_code": last_status} if last_status is not None else None
    raise AppError(422, "business_rule_violation", "Could not load a valid OpenAPI JSON document from the provided de-identification docs path", details)


def _select_deidentification_operation(document: dict[str, Any], *, selected_path: str | None = None) -> tuple[str, dict[str, Any], list[str]]:
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
        if "application/json" not in media_types and not any(media.endswith("+json") for media in media_types):
            continue
        score = 1
        haystack = " ".join(
            str(value).lower()
            for value in (path, resolved_operation.get("summary"), resolved_operation.get("description"), resolved_operation.get("operationId"))
            if value
        )
        for keyword in ("deid", "de-ident", "identify", "anonym", "pseudonym", "pii", "phi", "entity", "entities", "detect", "analyze", "redact"):
            if keyword in haystack:
                score += 3
        candidates.append((score, str(path), resolved_operation))
    if not candidates:
        raise AppError(422, "business_rule_violation", "No candidate de-identification POST operation was found in the OpenAPI document")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    if selected_path and not _looks_like_openapi_path(selected_path):
        for _, path, operation in candidates:
            if path == selected_path:
                return path, operation, [candidate_path for _, candidate_path, _ in candidates[:8]]
        raise AppError(422, "business_rule_violation", "Selected de-identification endpoint was not found in the OpenAPI document", {"detect_path": selected_path})
    selected_score, selected_path, selected_operation = candidates[0]
    if selected_score <= 1:
        raise AppError(422, "business_rule_violation", "OpenAPI document did not contain a recognizable de-identification endpoint")
    return selected_path, selected_operation, [path for _, path, _ in candidates[:5]]


def _request_schema_for_operation(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    request_body = _dereference(document, operation.get("requestBody") or {})
    content = request_body.get("content") or {}
    schema = None
    if "application/json" in content:
        schema = content["application/json"].get("schema")
    else:
        for media_type, media in content.items():
            if str(media_type).endswith("+json") and isinstance(media, dict):
                schema = media.get("schema")
                break
    resolved = _dereference(document, schema) if schema else None
    if not isinstance(resolved, dict):
        raise AppError(422, "business_rule_violation", "The candidate de-identification request schema was invalid")
    return resolved


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
    for media_type, media in content.items():
        if (media_type == "application/json" or str(media_type).endswith("+json")) and isinstance(media, dict) and media.get("schema"):
            return _dereference(document, media["schema"])
    return None


def _property_description(properties: dict[str, Any], key: str) -> str | None:
    prop = properties.get(key)
    if not isinstance(prop, dict):
        return None
    description = prop.get("description")
    return str(description) if description else None


def _pick_property_value(properties: dict[str, Any], key: str) -> Any | None:
    prop = properties.get(key)
    if not isinstance(prop, dict):
        return None
    for candidate_key in ("default", "example"):
        value = prop.get(candidate_key)
        if value is not None:
            return value
    enum_values = prop.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]
    return None


def _infer_request_text_field(properties: dict[str, Any]) -> str:
    for preferred in ("text", "input", "content", "document", "value", "query"):
        if preferred in properties:
            return preferred
    for key, prop in properties.items():
        if isinstance(prop, dict) and prop.get("type") == "string":
            return key
    return "text"


def _infer_request_language_field(properties: dict[str, Any]) -> str | None:
    for preferred in ("language", "lang", "language_code", "locale"):
        if preferred in properties:
            return preferred
    return None


def _field_from_properties(properties: dict[str, Any], preferred: tuple[str, ...], fallback: str) -> str:
    for key in preferred:
        if key in properties:
            return key
    return fallback


def _infer_response_contract(schema: dict[str, Any] | None) -> tuple[str, str, str, str, str | None, str | None]:
    if not isinstance(schema, dict):
        return "entities", "start", "end", "entity_type", "score", None
    resolved_schema = schema
    entities_path = "entities"
    item_schema: dict[str, Any] = {}
    if resolved_schema.get("type") == "array":
        entities_path = ""
        item_schema = resolved_schema.get("items") if isinstance(resolved_schema.get("items"), dict) else {}
    else:
        properties = resolved_schema.get("properties") if isinstance(resolved_schema.get("properties"), dict) else {}
        for preferred in ("entities", "items", "results", "spans", "detections"):
            prop = properties.get(preferred)
            if isinstance(prop, dict) and prop.get("type") == "array":
                entities_path = preferred
                item_schema = prop.get("items") if isinstance(prop.get("items"), dict) else {}
                break
        else:
            for key, prop in properties.items():
                if isinstance(prop, dict) and prop.get("type") == "array":
                    entities_path = key
                    item_schema = prop.get("items") if isinstance(prop.get("items"), dict) else {}
                    break
    item_properties = item_schema.get("properties") if isinstance(item_schema.get("properties"), dict) else {}
    start_field = _field_from_properties(item_properties, ("start", "begin", "start_offset", "offset"), "start")
    end_field = _field_from_properties(item_properties, ("end", "stop", "end_offset"), "end")
    type_field = _field_from_properties(item_properties, ("entity_type", "type", "label", "category", "entity"), "entity_type")
    score_field = _field_from_properties(item_properties, ("score", "confidence", "probability"), "score") if item_properties else "score"
    model_path = None
    if isinstance(schema.get("properties"), dict):
        for preferred in ("model", "model_version", "version", "meta", "metadata"):
            if preferred in schema["properties"]:
                model_path = preferred
                break
    return entities_path, start_field, end_field, type_field, score_field, model_path


def _display_default(value: Any | None) -> str | None:
    return None if value is None else str(value)


def _looks_like_language_value(value: str | None) -> bool:
    return (value or "").strip().lower() in {"en", "eng", "english", "cy", "welsh"}


def _extra_forbidden_body_fields(raw_response: Any) -> set[str]:
    if not isinstance(raw_response, dict):
        return set()
    error = raw_response.get("error")
    details = error.get("details") if isinstance(error, dict) else raw_response.get("detail")
    if not isinstance(details, list):
        return set()
    fields: set[str] = set()
    for item in details:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"extra_forbidden", "value_error.extra"}:
            continue
        field = item.get("field") or item.get("loc")
        if isinstance(field, str) and field.startswith("body."):
            fields.add(field.removeprefix("body."))
        elif isinstance(field, list) and len(field) >= 2 and field[0] == "body":
            fields.add(str(field[1]))
    return fields


def _entity_items_from_raw_response(raw_response: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(raw_response, list):
        return [item for item in raw_response if isinstance(item, dict)], ""
    if not isinstance(raw_response, dict):
        return [], None
    for key in ("entities", "items", "results", "spans", "detections"):
        value = raw_response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)], key
    for key, value in raw_response.items():
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)], str(key)
    return [], None


def _infer_response_fields_from_raw_response(raw_response: Any) -> dict[str, str | None]:
    items, entities_path = _entity_items_from_raw_response(raw_response)
    first = items[0] if items else {}
    if not first:
        return {}
    inferred: dict[str, str | None] = {}
    for key in ("start", "begin", "start_offset", "offset"):
        if key in first:
            inferred["response_start_field"] = key
            break
    for key in ("end", "stop", "end_offset"):
        if key in first:
            inferred["response_end_field"] = key
            break
    for key in ("entity_type", "type", "label", "category", "entity"):
        if key in first:
            inferred["response_type_field"] = key
            break
    for key in ("score", "confidence", "probability"):
        if key in first:
            inferred["response_score_field"] = key
            break
    if entities_path is not None:
        inferred["response_entities_path"] = entities_path
    return inferred


def _apply_inferred_response_fields(provider: DeidentificationProvider, inferred: dict[str, str | None]) -> list[str]:
    changed: list[str] = []
    field_map = {
        "response_entities_path": "response_entities_path",
        "response_start_field": "response_start_field",
        "response_end_field": "response_end_field",
        "response_type_field": "response_type_field",
        "response_score_field": "response_score_field",
    }
    for payload_key, attr in field_map.items():
        value = inferred.get(payload_key)
        if value is None or value == getattr(provider, attr):
            continue
        setattr(provider, attr, value)
        changed.append(f"{attr}={value or '<top-level array>'}")
    return changed


def _inspect_provider_ping(
    db: Session,
    *,
    provider: DeidentificationProvider,
    sample_text: str,
    bearer_token: str | None,
) -> tuple[list[DeidentificationInspectEntity], str | None, Any | None, list[str]]:
    from app.services.redaction import _extract_path, _provider_spans_from_payload

    body: dict[str, Any] = dict(provider.extra_body_json or {})
    body[provider.request_text_field] = sample_text
    notes: list[str] = []
    if _looks_like_language_value(provider.request_language_field):
        notes.append("Request language field looked like a language value, so it was omitted. Use a field name such as lang or language, not en.")
        provider.request_language_field = None
    if provider.request_language_field:
        body[provider.request_language_field] = body.get(provider.request_language_field) or "en"
    headers = {key: str(value) for key, value in (provider.extra_headers_json or {}).items()}
    if provider.auth_mode is DeidentificationAuthMode.bearer:
        token = bearer_token or read_deidentification_provider_bearer_token(db, provider_id=provider.id)
        headers["Authorization"] = f"Bearer {token}"
    def post_body(request_body: dict[str, Any]):
        try:
            return httpx.post(f"{provider.base_url.rstrip('/')}{provider.detect_path}", json=request_body, headers=headers, timeout=20.0)
        except httpx.HTTPError as exc:
            raise AppError(502, "redaction_failed", "PHI redaction provider ping failed") from exc

    response = post_body(body)
    try:
        raw_response = response.json()
    except ValueError as exc:
        raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider ping did not return JSON") from exc
    forbidden_fields = _extra_forbidden_body_fields(raw_response) if response.status_code == 422 else set()
    retry_body = dict(body)
    pruned_fields = sorted(field for field in forbidden_fields if field in retry_body and field != provider.request_text_field)
    if pruned_fields:
        for field in pruned_fields:
            retry_body.pop(field, None)
        provider.extra_body_json = {key: value for key, value in (provider.extra_body_json or {}).items() if key not in pruned_fields}
        if provider.request_language_field in pruned_fields:
            provider.request_language_field = None
        notes.append(f"Provider rejected extra body fields, so synthetic ping retried without: {', '.join(pruned_fields)}.")
        response = post_body(retry_body)
        try:
            raw_response = response.json()
        except ValueError as exc:
            raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider ping did not return JSON") from exc
    if response.status_code >= 400:
        return [], None, raw_response, notes + [f"Provider ping returned HTTP {response.status_code}; inspect raw response to adjust request fields."]
    spans = []
    try:
        spans = _provider_spans_from_payload(sample_text, raw_response, provider=provider, score_threshold=0.0, entities=None)
    except (AppError, KeyError, TypeError, ValueError) as exc:
        inferred = _infer_response_fields_from_raw_response(raw_response)
        changed = _apply_inferred_response_fields(provider, inferred)
        if changed:
            try:
                spans = _provider_spans_from_payload(sample_text, raw_response, provider=provider, score_threshold=0.0, entities=None)
                notes.append(f"Provider response fields were adjusted from the ping response: {', '.join(changed)}.")
            except (AppError, KeyError, TypeError, ValueError):
                notes.append("Provider responded, but response could not be parsed with the current entity field settings.")
                spans = []
        else:
            notes.append("Provider responded, but response could not be parsed with the current entity field settings.")
            if isinstance(raw_response, dict):
                notes.append(f"Top-level response keys: {', '.join(sorted(str(key) for key in raw_response.keys())) or 'none'}.")
            spans = []
    model_or_version = None
    if provider.response_model_version_path:
        try:
            resolved_value = _extract_path(raw_response, provider.response_model_version_path)
            model_or_version = str(resolved_value) if resolved_value is not None else None
        except AppError:
            pass
    entities = [
        DeidentificationInspectEntity(
            start=span.start,
            end=span.end,
            entity_type=span.entity_type,
            score=span.score,
            value=sample_text[span.start:span.end],
        )
        for span in spans
    ]
    return entities, model_or_version, raw_response, notes


def _inspect_deidentification_openapi(db: Session, actor: User, payload: DeidentificationProviderInspectRequest) -> DeidentificationInspectResult:
    _ = (db, actor)
    document, openapi_path = _fetch_openapi_document(payload)
    selected_path = payload.detect_path if payload.openapi_path else None
    detect_path, operation, candidate_paths = _select_deidentification_operation(document, selected_path=selected_path)
    request_schema = _request_schema_for_operation(document, operation)
    properties = request_schema.get("properties") if isinstance(request_schema.get("properties"), dict) else {}
    required_fields = {str(item) for item in (request_schema.get("required") or []) if isinstance(item, str)}
    request_text_field = _infer_request_text_field(properties)
    request_language_field = _infer_request_language_field(properties)
    extra_body = {}
    for key, prop in properties.items():
        if key in {request_text_field, request_language_field} or not isinstance(prop, dict):
            continue
        value = _pick_property_value(properties, key)
        if value is not None:
            extra_body[key] = value
    entities_path, start_field, end_field, type_field, score_field, model_path = _infer_response_contract(_response_schema_for_operation(document, operation))
    field_tips = [
        DeidentificationInspectFieldTip(
            name=request_text_field,
            role="text",
            default_value=_display_default(_pick_property_value(properties, request_text_field)),
            description=_property_description(properties, request_text_field),
            required=request_text_field in required_fields,
        )
    ]
    if request_language_field:
        field_tips.append(
            DeidentificationInspectFieldTip(
                name=request_language_field,
                role="language",
                default_value=_display_default(_pick_property_value(properties, request_language_field)),
                description=_property_description(properties, request_language_field),
                required=request_language_field in required_fields,
            )
        )
    for key in sorted(extra_body):
        field_tips.append(
            DeidentificationInspectFieldTip(
                name=key,
                role="extra",
                default_value=_display_default(extra_body[key]),
                description=_property_description(properties, key),
                required=key in required_fields,
            )
        )
    provider = DeidentificationProvider(
        id=uuid4(),
        label=payload.label.strip(),
        adapter_kind=payload.adapter_kind,
        base_url=payload.base_url,
        detect_path=detect_path,
        auth_mode=payload.auth_mode,
        request_text_field=request_text_field,
        request_language_field=request_language_field,
        extra_headers_json=payload.extra_headers_json,
        extra_body_json=extra_body,
        response_entities_path=entities_path,
        response_start_field=start_field,
        response_end_field=end_field,
        response_type_field=type_field,
        response_score_field=score_field,
        response_model_version_path=model_path,
        entity_type_map_json=payload.entity_type_map_json,
        clinical_detection_enabled=payload.clinical_detection_enabled,
        clinical_detection_allow_unredacted=payload.clinical_detection_allow_unredacted,
        vault_secret_ref="",
        is_active=payload.is_active,
        is_builtin=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    ping_entities: list[DeidentificationInspectEntity] = []
    ping_model_or_version = None
    raw_response = None
    ping_notes: list[str] = []
    if payload.auth_mode is not DeidentificationAuthMode.bearer or payload.bearer_token:
        ping_entities, ping_model_or_version, raw_response, ping_notes = _inspect_provider_ping(
            db,
            provider=provider,
            sample_text=payload.sample_text,
            bearer_token=payload.bearer_token,
        )
    else:
        ping_notes.append("Inferred contract was not pinged because bearer auth requires a bearer token.")
    return DeidentificationInspectResult(
        provider_label=payload.label.strip(),
        adapter_kind=payload.adapter_kind,
        openapi_path=openapi_path,
        detect_path=detect_path,
        request_text_field=request_text_field,
        request_language_field=provider.request_language_field,
        extra_body_json=provider.extra_body_json or {},
        response_entities_path=provider.response_entities_path,
        response_start_field=provider.response_start_field,
        response_end_field=provider.response_end_field,
        response_type_field=provider.response_type_field,
        response_score_field=provider.response_score_field,
        response_model_version_path=provider.response_model_version_path,
        api_provider=payload.label.strip(),
        api_model_or_version=ping_model_or_version,
        sample_text=payload.sample_text,
        entities=ping_entities,
        candidate_paths=candidate_paths,
        operation_summary=str(operation.get("summary") or operation.get("operationId") or "OpenAPI de-identification operation"),
        field_tips=field_tips,
        raw_response_json=raw_response,
        notes=[
            f"Loaded OpenAPI JSON from {openapi_path}.",
            "Inspection inferred fields and pinged the inferred detect path with synthetic sample text.",
            "No transcript or note content was sent.",
        ] + ping_notes,
    )


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
        clinical_detection_enabled=False,
        clinical_detection_allow_unredacted=False,
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


def inspect_deidentification_provider(
    db: Session,
    actor: User,
    payload: DeidentificationProviderInspectRequest,
) -> DeidentificationInspectResult:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin de-identification provisioning access required")
    ensure_builtin_deidentification_provider(db)
    existing_provider = db.get(DeidentificationProvider, payload.provider_id) if payload.provider_id is not None else None
    if payload.provider_id is not None and existing_provider is None:
        raise AppError(404, "not_found", "De-identification provider not found", {"resource": "deidentification_provider", "provider_id": str(payload.provider_id)})
    if payload.adapter_kind is DeidentificationAdapterKind.native_presidio:
        raise AppError(409, "conflict", "Built-in de-identification provider does not need remote inspection")
    if payload.openapi_path or _looks_like_openapi_path(payload.detect_path):
        return _inspect_deidentification_openapi(db, actor, payload)
    if payload.auth_mode is DeidentificationAuthMode.bearer and not payload.bearer_token and not (existing_provider and existing_provider.vault_secret_ref):
        raise AppError(
            422,
            "business_rule_violation",
            "Bearer token is required to inspect bearer-auth de-identification provider",
            {"field": "bearer_token"},
        )
    provider = DeidentificationProvider(
        id=existing_provider.id if existing_provider is not None else uuid4(),
        label=payload.label.strip(),
        adapter_kind=payload.adapter_kind,
        base_url=payload.base_url,
        detect_path=payload.detect_path,
        auth_mode=payload.auth_mode,
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
        clinical_detection_enabled=payload.clinical_detection_enabled,
        clinical_detection_allow_unredacted=payload.clinical_detection_allow_unredacted,
        vault_secret_ref=existing_provider.vault_secret_ref if existing_provider is not None else "",
        is_active=payload.is_active,
        is_builtin=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    entities, model_or_version, raw_response, ping_notes = _inspect_provider_ping(
        db,
        provider=provider,
        sample_text=payload.sample_text,
        bearer_token=payload.bearer_token,
    )
    notes = ["Ping used synthetic sample text only; no transcript or note content was sent."]
    if not entities:
        notes.append("Provider responded successfully but returned no entities for sample text.")
    return DeidentificationInspectResult(
        provider_label=provider.label,
        adapter_kind=provider.adapter_kind,
        openapi_path=payload.openapi_path,
        detect_path=provider.detect_path,
        request_text_field=provider.request_text_field,
        request_language_field=provider.request_language_field,
        extra_body_json=provider.extra_body_json or {},
        response_entities_path=provider.response_entities_path,
        response_start_field=provider.response_start_field,
        response_end_field=provider.response_end_field,
        response_type_field=provider.response_type_field,
        response_score_field=provider.response_score_field,
        response_model_version_path=provider.response_model_version_path,
        api_provider=provider.label,
        api_model_or_version=model_or_version,
        sample_text=payload.sample_text,
        entities=entities,
        raw_response_json=raw_response,
        notes=notes + ping_notes,
    )


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

    creating = provider is None
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
            clinical_detection_enabled=payload.clinical_detection_enabled,
            clinical_detection_allow_unredacted=payload.clinical_detection_allow_unredacted,
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
        provider.clinical_detection_enabled = payload.clinical_detection_enabled
        provider.clinical_detection_allow_unredacted = payload.clinical_detection_allow_unredacted
        provider.is_active = payload.is_active
        provider.updated_by_user_id = actor.id
        db.add(provider)

    try:
        if provider.auth_mode is DeidentificationAuthMode.bearer and payload.bearer_token:
            pending_secret_ref = write_deidentification_bearer_token(
                provider_id=provider.id,
                bearer_token=payload.bearer_token,
                secret_id=uuid4(),
            )
            provider.vault_secret_ref = pending_secret_ref
        elif provider.auth_mode is DeidentificationAuthMode.none:
            provider.vault_secret_ref = ""

        if old_secret_ref and old_secret_ref != provider.vault_secret_ref:
            queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.deidentification, secret_refs=[old_secret_ref])
        db.commit()
    except Exception:
        db.rollback()
        if pending_secret_ref:
            queue_orphan_provider_secret_after_rollback(
                db,
                kind=ProviderSecretCleanupKind.deidentification,
                secret_ref=pending_secret_ref,
            )
        raise
    db.refresh(provider)
    _record_deid_audit(
        db,
        action="deidentification_provider_created" if creating else "deidentification_provider_updated",
        actor=actor,
        provider_id=provider.id,
        auth_mode=provider.auth_mode.value,
        active=provider.is_active,
        clinical_detection_enabled=provider.clinical_detection_enabled,
        credential_present=bool(provider.vault_secret_ref),
    )
    return provider


def delete_deidentification_provider(db: Session, actor: User, *, provider_id: UUID) -> None:
    provider = get_deidentification_provider(db, actor, provider_id=provider_id)
    if provider.is_builtin:
        raise AppError(409, "conflict", "Built-in de-identification provider cannot be deleted")
    old_secret_ref = provider.vault_secret_ref
    selections = list(db.scalars(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.provider_id == provider.id)))
    for selection in selections:
        db.delete(selection)
    clinical_nlp_selections = list(db.scalars(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.provider_id == provider.id)))
    for selection in clinical_nlp_selections:
        db.delete(selection)
    assignments = list(db.scalars(select(TeamDeidentificationProviderAssignment).where(TeamDeidentificationProviderAssignment.provider_id == provider.id)))
    for assignment in assignments:
        db.delete(assignment)
    clinical_entity_runs = list(db.scalars(select(ClinicalEntityRun).where(ClinicalEntityRun.provider_id == provider.id)))
    for run in clinical_entity_runs:
        run.provider_id = None
        db.add(run)
    if selections or clinical_nlp_selections or assignments or clinical_entity_runs:
        db.flush()
    queue_provider_secret_cleanup(
        db,
        kind=ProviderSecretCleanupKind.deidentification,
        secret_refs=[old_secret_ref],
    )
    db.delete(provider)
    db.commit()
    _record_deid_audit(db, action="deidentification_provider_deleted", actor=actor, provider_id=provider_id)


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
    _record_deid_audit(db, action="deidentification_provider_assigned", actor=actor, provider_id=provider.id, team_id=team.id)
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
    clinical_selection = db.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team_id, TeamClinicalNlpSelection.provider_id == provider_id))
    if clinical_selection is not None:
        db.delete(clinical_selection)
        db.flush()
    db.delete(assignment)
    db.commit()
    _record_deid_audit(db, action="deidentification_provider_assignment_removed", actor=actor, provider_id=provider_id, team_id=team_id)


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
    _record_deid_audit(db, action="deidentification_selection_set", actor=actor, provider_id=payload.provider_id, team_id=team.id)
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
    _record_deid_audit(db, action="deidentification_selection_cleared", actor=actor, team_id=team.id)


def list_selectable_clinical_nlp_providers(db: Session, actor: User, *, team_id: UUID | None = None) -> list[DeidentificationProvider]:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    stmt = (
        select(DeidentificationProvider)
        .join(TeamDeidentificationProviderAssignment, TeamDeidentificationProviderAssignment.provider_id == DeidentificationProvider.id)
        .where(
            TeamDeidentificationProviderAssignment.team_id == team.id,
            DeidentificationProvider.is_active.is_(True),
            DeidentificationProvider.is_builtin.is_(False),
            DeidentificationProvider.clinical_detection_enabled.is_(True),
        )
        .order_by(DeidentificationProvider.created_at.desc(), DeidentificationProvider.id.desc())
    )
    return list(db.scalars(stmt))


def get_team_clinical_nlp_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> TeamClinicalNlpSelection | None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    return db.scalar(
        select(TeamClinicalNlpSelection)
        .options(joinedload(TeamClinicalNlpSelection.provider))
        .where(TeamClinicalNlpSelection.team_id == team.id)
    )


def set_team_clinical_nlp_selection(db: Session, actor: User, payload: ClinicalNlpSelectionUpsert) -> TeamClinicalNlpSelection:
    team = _resolve_selection_scoped_team(db, actor, team_id=payload.team_id)
    selectable_ids = {provider.id for provider in list_selectable_clinical_nlp_providers(db, actor, team_id=team.id)}
    if payload.provider_id not in selectable_ids:
        raise AppError(404, "not_found", "Selectable clinical NLP endpoint not found", {"resource": "clinical_nlp_provider", "provider_id": str(payload.provider_id)})
    selection = db.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id))
    if selection is None:
        selection = TeamClinicalNlpSelection(
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
    _record_deid_audit(db, action="clinical_nlp_selection_set", actor=actor, provider_id=payload.provider_id, team_id=team.id, provider_type="clinical_nlp")
    return db.scalar(
        select(TeamClinicalNlpSelection)
        .options(joinedload(TeamClinicalNlpSelection.provider))
        .where(TeamClinicalNlpSelection.id == selection.id)
    ) or selection


def clear_team_clinical_nlp_selection(db: Session, actor: User, *, team_id: UUID | None = None) -> None:
    team = _resolve_selection_scoped_team(db, actor, team_id=team_id)
    selection = db.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id))
    if selection is None:
        raise AppError(404, "not_found", "Clinical NLP selection not found", {"resource": "clinical_nlp_selection", "team_id": str(team.id)})
    db.delete(selection)
    db.commit()
    _record_deid_audit(db, action="clinical_nlp_selection_cleared", actor=actor, team_id=team.id, provider_type="clinical_nlp")


def active_team_clinical_nlp_provider(db: Session, *, team_id: UUID) -> DeidentificationProvider | None:
    selection = db.scalar(
        select(TeamClinicalNlpSelection)
        .options(joinedload(TeamClinicalNlpSelection.provider))
        .where(TeamClinicalNlpSelection.team_id == team_id)
    )
    if selection is None or selection.provider is None or not selection.provider.is_active or not selection.provider.clinical_detection_enabled:
        return None
    assignment = db.scalar(
        select(TeamDeidentificationProviderAssignment.id).where(
            TeamDeidentificationProviderAssignment.team_id == team_id,
            TeamDeidentificationProviderAssignment.provider_id == selection.provider.id,
        )
    )
    if assignment is None:
        return None
    return selection.provider


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
