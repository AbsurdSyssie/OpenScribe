from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    DeidentificationAdapterKind,
    DeidentificationProvider,
    DetectedPiiDismissal,
    RedactionEntity,
    RedactionRun,
    RedactionRunStatus,
    Transcript,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner
from app.services.deidentification import active_team_deidentification_provider, read_deidentification_provider_bearer_token
from app.services.provider_inspection import ProviderResponseTooLargeError, read_limited_httpx_response

from .redaction_policy import filter_analyzer_results, is_labelled_acknowledgement_person, normalize_span_bounds


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "redaction" / "presidio_config.yaml"
PHI_TOKEN_PATTERN = re.compile(r"\[PHI-(\d+)\]")
POTENTIAL_PHI_TOKEN_PATTERN = re.compile(r"\[PHI-[^\]]*(?:\]|$)")
CLINICAL_ENTITY_TYPES = {"DISEASE", "DIAGNOSIS", "CONDITION", "PROBLEM", "SYMPTOM", "SIGN"}
REDACTION_PROVIDER_RESPONSE_MAX_BYTES = 8 * 1024 * 1024


@dataclass
class Span:
    start: int
    end: int
    entity_type: str
    score: float

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class DeidentificationDetectionResult:
    spans: list[Span]
    api_provider: str
    api_model_or_version: str | None = None


@dataclass(frozen=True)
class RedactionSourceSpan:
    entity: RedactionEntity
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class EffectiveRedaction:
    redacted_text: str
    phi_index: list[dict[str, Any]]
    dismissed_entity_ids: frozenset[uuid.UUID]
    requires_review_dismissal_ids: frozenset[uuid.UUID]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise AppError(
            500,
            "redaction_unavailable",
            "Native PHI redaction dependencies are not installed",
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise AppError(500, "redaction_invalid_config", "Presidio configuration must be a mapping")
    return data


@lru_cache(maxsize=1)
def _redaction_runtime() -> tuple[Any, dict[str, Any]]:
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError as exc:  # pragma: no cover
        raise AppError(
            500,
            "redaction_unavailable",
            "Native PHI redaction dependencies are not installed",
        ) from exc

    config = _load_yaml(DEFAULT_CONFIG_PATH)
    nlp_config = config.get("nlp", {})
    model_name = nlp_config.get("model_name", "en_core_web_sm")
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_name}],
        }
    )
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine())
    for recognizer_config in config.get("custom_recognizers", []):
        entity_name = recognizer_config["entity_name"]
        regex = recognizer_config["regex"]
        score = float(recognizer_config["score"])
        recognizer = PatternRecognizer(
            supported_entity=entity_name,
            patterns=[Pattern(name=entity_name, regex=regex, score=score)],
        )
        analyzer.registry.add_recognizer(recognizer)
    return analyzer, config


def _filter_results(text: str, raw_results: list[Any]) -> list[Span]:
    spans: list[Span] = []
    for result in filter_analyzer_results(text, raw_results):
        normalized_bounds = normalize_span_bounds(
            text,
            result.start,
            result.end,
            str(result.entity_type),
        )
        if normalized_bounds is None:
            continue
        start, end = normalized_bounds
        if is_labelled_acknowledgement_person(text, start, end, str(result.entity_type)):
            continue
        spans.append(Span(start=start, end=end, entity_type=str(result.entity_type), score=float(result.score)))
    return spans


def _filter_provider_spans(
    text: str,
    spans: list[Span],
    *,
    score_threshold: float,
    entities: list[str] | None,
    excluded_entities: set[str] | None = None,
) -> list[Span]:
    allowed_entities = {str(entity) for entity in entities} if entities is not None else None
    excluded_normalized = {entity.strip().upper() for entity in excluded_entities or set()}
    normalized_spans: list[Span] = []
    for span in spans:
        if span.start < 0 or span.end > len(text) or span.start >= span.end:
            raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")
        if span.score < score_threshold:
            continue
        if allowed_entities is not None and span.entity_type not in allowed_entities:
            continue
        if span.entity_type.strip().upper() in excluded_normalized:
            continue
        normalized_bounds = normalize_span_bounds(text, span.start, span.end, span.entity_type)
        if normalized_bounds is None:
            continue
        start, end = normalized_bounds
        normalized_spans.append(Span(start=start, end=end, entity_type=span.entity_type, score=span.score))
    return list(filter_analyzer_results(text, normalized_spans))


def _extract_path(payload: Any, path: str | None) -> Any:
    if path is None or not path.strip():
        return payload
    current = payload
    for part in path.split("."):
        key = part.strip()
        if not key:
            continue
        if not isinstance(current, dict) or key not in current:
            raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")
        current = current[key]
    return current


def _coerce_span_field(item: dict[str, Any], field_name: str, *, kind: type[int] | type[str]) -> int | str:
    if field_name not in item:
        raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")
    value = item[field_name]
    try:
        return kind(value)
    except (TypeError, ValueError) as exc:
        raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response") from exc


def _normalize_entity_type(raw_type: str, *, entity_type_map: dict[str, str]) -> str:
    trimmed = raw_type.strip()
    if not trimmed:
        raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")
    return entity_type_map.get(trimmed, trimmed)


ENTITY_VALUE_FIELD_CANDIDATES = (
    "value",
    "text",
    "entity_text",
    "entity",
    "word",
    "match",
    "matched_text",
    "source_text",
    "original",
    "pii",
    "span",
)


def _provider_span_from_item(
    text: str,
    raw_item: dict[str, Any],
    *,
    provider: DeidentificationProvider,
    search_start: int,
) -> tuple[Span, int]:
    score = 1.0
    if provider.response_score_field:
        raw_score = raw_item.get(provider.response_score_field)
        if raw_score is not None:
            try:
                score = float(raw_score)
            except (TypeError, ValueError) as exc:
                raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response") from exc
    entity_type = _normalize_entity_type(
        _coerce_span_field(raw_item, provider.response_type_field, kind=str),
        entity_type_map={str(key): str(value) for key, value in (provider.entity_type_map_json or {}).items()},
    )
    if provider.response_start_field in raw_item and provider.response_end_field in raw_item:
        start = _coerce_span_field(raw_item, provider.response_start_field, kind=int)
        end = _coerce_span_field(raw_item, provider.response_end_field, kind=int)
        return Span(start=start, end=end, entity_type=entity_type, score=score), int(end)
    for value_field in ENTITY_VALUE_FIELD_CANDIDATES:
        raw_value = raw_item.get(value_field)
        if raw_value is None:
            continue
        value = str(raw_value)
        if not value:
            continue
        start = text.find(value, search_start)
        if start < 0:
            start = text.find(value)
        if start < 0:
            lowered_text = text.lower()
            lowered_value = value.lower()
            start = lowered_text.find(lowered_value, search_start)
            if start < 0:
                start = lowered_text.find(lowered_value)
        if start >= 0:
            end = start + len(value)
            return Span(start=start, end=end, entity_type=entity_type, score=score), end
    raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")


def _provider_spans_from_payload(
    text: str,
    payload: Any,
    *,
    provider: DeidentificationProvider,
    score_threshold: float,
    entities: list[str] | None,
    excluded_entities: set[str] | None = None,
) -> list[Span]:
    entities_payload = _extract_path(payload, provider.response_entities_path)
    if not isinstance(entities_payload, list):
        raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")
    spans: list[Span] = []
    search_start = 0
    for raw_item in entities_payload:
        if not isinstance(raw_item, dict):
            raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response")
        span, search_start = _provider_span_from_item(
            text,
            raw_item,
            provider=provider,
            search_start=search_start,
        )
        spans.append(span)
    return _resolve_overlaps(_filter_provider_spans(text, spans, score_threshold=score_threshold, entities=entities, excluded_entities=excluded_entities))


def _detect_with_presidio(text: str, *, language: str, score_threshold: float, entities: list[str] | None) -> DeidentificationDetectionResult:
    analyzer, config = _redaction_runtime()
    try:
        raw_results = analyzer.analyze(
            text=text,
            language=language,
            entities=entities,
            score_threshold=score_threshold,
        )
    except Exception as exc:  # pragma: no cover
        raise AppError(502, "redaction_failed", "PHI redaction failed") from exc
    return DeidentificationDetectionResult(
        spans=_resolve_overlaps(_filter_results(text, raw_results)),
        api_provider="native_presidio",
        api_model_or_version=config.get("nlp", {}).get("model_name", "en_core_web_sm"),
    )


def _detect_with_generic_rest(
    db: Session,
    *,
    provider: DeidentificationProvider,
    text: str,
    language: str,
    score_threshold: float,
    entities: list[str] | None,
    bearer_token_override: str | None = None,
    excluded_entities: set[str] | None = None,
    extra_body_overrides: dict[str, Any] | None = None,
    failure_code: str = "redaction_failed",
    failure_message: str = "PHI redaction failed",
) -> DeidentificationDetectionResult:
    from app.provider_url_security import require_safe_provider_url

    require_safe_provider_url(provider.base_url)
    body: dict[str, Any] = dict(provider.extra_body_json or {})
    if extra_body_overrides:
        for key, value in extra_body_overrides.items():
            body.setdefault(key, value)
    body[provider.request_text_field] = text
    if provider.request_language_field:
        body[provider.request_language_field] = language
    headers = {key: str(value) for key, value in (provider.extra_headers_json or {}).items()}
    if provider.auth_mode.value == "bearer":
        token = bearer_token_override or read_deidentification_provider_bearer_token(db, provider_id=provider.id)
        headers["Authorization"] = f"Bearer {token}"
    url = f"{provider.base_url.rstrip('/')}{provider.detect_path}"
    try:
        with httpx.stream("POST", url, json=body, headers=headers, timeout=20.0) as response:
            if response.status_code >= 400:
                raise AppError(502, failure_code, failure_message)
            response_body = read_limited_httpx_response(
                response,
                max_bytes=REDACTION_PROVIDER_RESPONSE_MAX_BYTES,
            )
    except httpx.HTTPError as exc:  # pragma: no cover
        raise AppError(502, failure_code, failure_message) from exc
    except ProviderResponseTooLargeError as exc:
        raise AppError(
            502,
            "redaction_provider_invalid_response",
            "PHI redaction provider response exceeded the permitted size",
            {"provider_error_code": "response_too_large"},
        ) from exc
    try:
        payload = json.loads(response_body)
    except ValueError as exc:
        raise AppError(502, "redaction_provider_invalid_response", "PHI redaction provider returned an invalid response") from exc
    spans = _provider_spans_from_payload(text, payload, provider=provider, score_threshold=score_threshold, entities=entities, excluded_entities=excluded_entities)
    model_or_version = None
    if provider.response_model_version_path:
        resolved_value = _extract_path(payload, provider.response_model_version_path)
        model_or_version = str(resolved_value) if resolved_value is not None else None
    return DeidentificationDetectionResult(
        spans=spans,
        api_provider=provider.label,
        api_model_or_version=model_or_version,
    )


def _detect_phi(
    db: Session,
    *,
    provider: DeidentificationProvider,
    text: str,
    language: str,
    score_threshold: float,
    entities: list[str] | None,
    bearer_token_override: str | None = None,
) -> DeidentificationDetectionResult:
    if provider.adapter_kind is DeidentificationAdapterKind.native_presidio:
        return _detect_with_presidio(text, language=language, score_threshold=score_threshold, entities=entities)
    if provider.adapter_kind is DeidentificationAdapterKind.generic_rest:
        return _detect_with_generic_rest(
            db,
            provider=provider,
            text=text,
            language=language,
            score_threshold=score_threshold,
            entities=entities,
            bearer_token_override=bearer_token_override,
            excluded_entities=CLINICAL_ENTITY_TYPES if provider.clinical_detection_enabled else None,
        )
    raise AppError(422, "business_rule_violation", "Unsupported de-identification adapter")


def _resolve_overlaps(spans: list[Span]) -> list[Span]:
    if not spans:
        return []
    ranked = sorted(spans, key=lambda span: (span.start, -span.length, -span.score))
    kept: list[Span] = []
    for span in ranked:
        if not kept or span.start >= kept[-1].end:
            kept.append(span)
    return kept


def _normalized_value_hash(value: str) -> str:
    normalized = " ".join(value.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def redact_text_with_mapping(
    db: Session,
    text: str,
    *,
    provider: DeidentificationProvider,
    language: str = "en",
    score_threshold: float = 0.35,
    entities: list[str] | None = None,
    start_index: int = 1,
    bearer_token_override: str | None = None,
) -> dict[str, Any]:
    detect_kwargs: dict[str, Any] = {
        "provider": provider,
        "text": text,
        "language": language,
        "score_threshold": score_threshold,
        "entities": entities,
    }
    if bearer_token_override is not None:
        detect_kwargs["bearer_token_override"] = bearer_token_override
    detection = _detect_phi(db, **detect_kwargs)
    spans = detection.spans

    parts: list[str] = []
    phi_index: list[dict[str, Any]] = []
    cursor = 0
    next_index = start_index

    for span in spans:
        parts.append(text[cursor:span.start])
        placeholder = f"[PHI-{next_index}]"
        parts.append(placeholder)
        value = text[span.start:span.end]
        phi_index.append({"index": next_index, "type": span.entity_type, "value": value, "placeholder": placeholder})
        cursor = span.end
        next_index += 1

    parts.append(text[cursor:])
    redacted_text = "".join(parts)
    phi_mapping = {
        f"phi-{row['index']}": {"type": row["type"], "value": row["value"]}
        for row in phi_index
    }
    return {
        "redacted_text": redacted_text,
        "phi_mapping": phi_mapping,
        "phi_index": phi_index,
        "phi_count": len(phi_index),
        "api_provider": detection.api_provider,
        "api_model_or_version": detection.api_model_or_version,
    }


def redact_transient_text(db: Session, text: str, *, team_id: uuid.UUID, start_index: int) -> dict[str, Any]:
    provider = active_team_deidentification_provider(db, team_id=team_id)
    clean = text.strip()
    if not clean:
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": provider.label,
            "api_model_or_version": None,
        }
    return redact_text_with_mapping(db, clean, provider=provider, start_index=start_index)


def _mapping_hash(phi_index: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        [{"index": item["index"], "type": item["type"], "value": item["value"]} for item in phi_index],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def redaction_run_text(db: Session, *, run: RedactionRun) -> str | None:
    return decrypt_text_for_owner(
        db,
        owner_user_id=run.owner_user_id,
        table="redaction_runs",
        field="redacted_text_encrypted",
        record_id=run.id,
        stored_value=run.redacted_text_encrypted,
    )


def redaction_entity_original_value(db: Session, *, entity: RedactionEntity) -> str:
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=entity.redaction_run.owner_user_id,
            table="redaction_entities",
            field="original_value_encrypted",
            record_id=entity.id,
            stored_value=entity.original_value_encrypted,
        )
        or ""
    )


def _redaction_run_to_phi_index(db: Session, run: RedactionRun) -> list[dict[str, Any]]:
    ordered = sorted(run.entities, key=lambda entity: entity.entity_order)
    return [
        {
            "index": entity.entity_order,
            "type": entity.entity_type,
            "value": redaction_entity_original_value(db, entity=entity),
            "placeholder": entity.placeholder,
        }
        for entity in ordered
    ]


def _redaction_source_spans(db: Session, *, run: RedactionRun) -> dict[uuid.UUID, RedactionSourceSpan]:
    """Recover the immutable source coordinates represented by a redaction run.

    A run stores a lossless sequence of literal text and numbered placeholders.
    Replaying that sequence against its encrypted transcript version is the only
    safe way to associate a detected entity with one source occurrence without
    adding mutable coordinates to the baseline entity record.
    """
    redacted_text = redaction_run_text(db, run=run)
    transcript_text = decrypt_text_for_owner(
        db,
        owner_user_id=run.owner_user_id,
        table="transcript_versions",
        field="text_encrypted",
        record_id=run.transcript_version_id,
        stored_value=run.transcript_version.text_encrypted,
    )
    if redacted_text is None or transcript_text is None:
        raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")

    entity_by_placeholder = {entity.placeholder: entity for entity in run.entities}
    source_cursor = 0
    redacted_cursor = 0
    spans: dict[uuid.UUID, RedactionSourceSpan] = {}
    for token in PHI_TOKEN_PATTERN.finditer(redacted_text):
        literal = redacted_text[redacted_cursor:token.start()]
        if not transcript_text.startswith(literal, source_cursor):
            raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
        source_cursor += len(literal)
        entity = entity_by_placeholder.get(token.group(0))
        if entity is None:
            raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
        if entity.id in spans:
            # Entity placeholders are one occurrence each.  A malformed or
            # ambiguous run must never turn a single owner decision into a
            # broader unredaction.
            raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
        value = redaction_entity_original_value(db, entity=entity)
        source_end = source_cursor + len(value)
        if not value or transcript_text[source_cursor:source_end] != value:
            raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
        spans[entity.id] = RedactionSourceSpan(entity=entity, start=source_cursor, end=source_end, text=value)
        source_cursor = source_end
        redacted_cursor = token.end()

    if transcript_text[source_cursor:] != redacted_text[redacted_cursor:]:
        raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
    return spans


def redaction_source_offsets(db: Session, *, run: RedactionRun) -> dict[uuid.UUID, tuple[int, int]]:
    """Return exact detected-occurrence offsets, or none when the run is uncertain.

    Offsets are derived only from the current encrypted source/run pair and are
    intended for the owner workspace.  They are not persisted or logged.
    """
    try:
        spans = _redaction_source_spans(db, run=run)
    except AppError:
        return {}
    return {entity_id: (span.start, span.end) for entity_id, span in spans.items()}


def _active_dismissals_for_run(db: Session, *, run: RedactionRun) -> list[DetectedPiiDismissal]:
    return list(
        db.scalars(
            select(DetectedPiiDismissal)
            .where(
                DetectedPiiDismissal.transcript_id == run.transcript_id,
                DetectedPiiDismissal.transcript_version_id == run.transcript_version_id,
                DetectedPiiDismissal.owner_user_id == run.owner_user_id,
            )
            .order_by(DetectedPiiDismissal.created_at.asc(), DetectedPiiDismissal.id.asc())
        )
    )


def _dismissals_matching_run(
    db: Session,
    *,
    run: RedactionRun,
) -> tuple[dict[uuid.UUID, DetectedPiiDismissal], set[uuid.UUID]]:
    dismissals = _active_dismissals_for_run(db, run=run)
    # Existing successful redaction output is authoritative when there are no
    # override decisions.  Do not introduce a new reconstruction requirement
    # into ordinary copy/generation/workspace reads.
    if not dismissals:
        return {}, set()
    try:
        spans = _redaction_source_spans(db, run=run)
    except AppError as exc:
        if exc.code == "redaction_override_requires_review":
            return {}, {dismissal.id for dismissal in dismissals}
        raise
    spans_by_identity = {(span.start, span.end, span.text): entity_id for entity_id, span in spans.items()}
    matched: dict[uuid.UUID, DetectedPiiDismissal] = {}
    requires_review: set[uuid.UUID] = set()
    for dismissal in dismissals:
        source_text = decrypt_text_for_owner(
            db,
            owner_user_id=dismissal.owner_user_id,
            table="detected_pii_dismissals",
            field="source_text_encrypted",
            record_id=dismissal.id,
            stored_value=dismissal.source_text_encrypted,
        )
        if not source_text:
            requires_review.add(dismissal.id)
            continue
        entity_id = spans_by_identity.get((dismissal.source_start_index, dismissal.source_end_index, source_text))
        if entity_id is not None:
            matched[entity_id] = dismissal
            continue
        # A newer run may no longer detect this span.  It remains redacted if
        # it detects a non-identical/overlapping occurrence, pending review.
        overlaps_detected = any(
            dismissal.source_start_index < span.end and dismissal.source_end_index > span.start
            for span in spans.values()
        )
        if overlaps_detected:
            requires_review.add(dismissal.id)
    return matched, requires_review


def detected_pii_dismissed_entity_ids(db: Session, *, run: RedactionRun) -> frozenset[uuid.UUID]:
    """Return current-run entity IDs whose owner dismissal still matches exactly."""
    matched, _ = _dismissals_matching_run(db, run=run)
    return frozenset(matched)


def effective_redaction_text_and_phi_index(db: Session, *, run: RedactionRun) -> EffectiveRedaction:
    """Apply exact, owner-scoped dismissals without changing baseline records.

    Manual PII is intentionally not applied here.  Callers apply the existing
    manual-protection pass after this result, so a manual protection always
    redacts even a dismissed detector occurrence.
    """
    matched, requires_review = _dismissals_matching_run(db, run=run)
    baseline_text = redaction_run_text(db, run=run)
    if baseline_text is None:
        raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
    entity_by_placeholder = {entity.placeholder: entity for entity in run.entities}

    def replace(match: re.Match[str]) -> str:
        entity = entity_by_placeholder.get(match.group(0))
        if entity is None or entity.id not in matched:
            return match.group(0)
        span_text = redaction_entity_original_value(db, entity=entity)
        return span_text

    dismissed_ids = frozenset(matched)
    return EffectiveRedaction(
        redacted_text=PHI_TOKEN_PATTERN.sub(replace, baseline_text),
        phi_index=[
            item
            for item in _redaction_run_to_phi_index(db, run)
            if entity_by_placeholder.get(str(item["placeholder"])) is None
            or entity_by_placeholder[str(item["placeholder"])].id not in dismissed_ids
        ],
        dismissed_entity_ids=dismissed_ids,
        requires_review_dismissal_ids=frozenset(requires_review),
    )


def _owner_detected_pii_entity_for_dismissal(
    db: Session,
    owner_user_id: uuid.UUID,
    *,
    transcript_id: uuid.UUID,
    entity_id: uuid.UUID,
) -> tuple[Transcript, RedactionRun, RedactionEntity]:
    transcript = db.scalar(
        select(Transcript)
        .where(Transcript.id == transcript_id, Transcript.owner_user_id == owner_user_id, Transcript.retention_expires_at > utcnow())
        .with_for_update()
    )
    if transcript is None:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    row = db.execute(
        select(RedactionRun, RedactionEntity)
        .join(RedactionEntity, RedactionEntity.redaction_run_id == RedactionRun.id)
        .where(
            RedactionEntity.id == entity_id,
            RedactionRun.transcript_id == transcript.id,
            RedactionRun.owner_user_id == owner_user_id,
            RedactionRun.status == RedactionRunStatus.succeeded,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise AppError(404, "not_found", "Detected PII entity not found", {"resource": "detected_pii_entity", "entity_id": str(entity_id)})
    run, entity = row
    current_text = (
        decrypt_text_for_owner(
            db,
            owner_user_id=owner_user_id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            stored_value=transcript.current_draft_text_encrypted,
        )
        or ""
    ).strip()
    version_text = (
        decrypt_text_for_owner(
            db,
            owner_user_id=owner_user_id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=run.transcript_version_id,
            stored_value=run.transcript_version.text_encrypted,
        )
        or ""
    ).strip()
    current_version_id = None
    for version in db.scalars(
        select(TranscriptVersion)
        .where(TranscriptVersion.transcript_id == transcript.id)
        .order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.created_at.desc(), TranscriptVersion.id.desc())
    ):
        candidate_text = (
            decrypt_text_for_owner(
                db,
                owner_user_id=owner_user_id,
                table="transcript_versions",
                field="text_encrypted",
                record_id=version.id,
                stored_value=version.text_encrypted,
            )
            or ""
        ).strip()
        if candidate_text == current_text:
            current_version_id = version.id
            break
    latest_run_id = db.scalar(
        select(RedactionRun.id)
        .where(
            RedactionRun.transcript_version_id == run.transcript_version_id,
            RedactionRun.owner_user_id == owner_user_id,
            RedactionRun.status == RedactionRunStatus.succeeded,
        )
        .order_by(RedactionRun.created_at.desc(), RedactionRun.id.desc())
        .limit(1)
    )
    if (
        transcript.status is not TranscriptStatus.ready
        or not current_text
        or current_text != version_text
        or current_version_id != run.transcript_version_id
        or latest_run_id != run.id
    ):
        raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
    return transcript, run, entity


def dismiss_detected_pii_entity_service(
    db: Session,
    owner: Any,
    *,
    transcript_id: uuid.UUID,
    entity_id: uuid.UUID,
) -> bool:
    transcript, run, entity = _owner_detected_pii_entity_for_dismissal(
        db, owner.id, transcript_id=transcript_id, entity_id=entity_id
    )
    span = _redaction_source_spans(db, run=run).get(entity.id)
    if span is None:
        raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
    existing = db.scalar(
        select(DetectedPiiDismissal)
        .where(
            DetectedPiiDismissal.transcript_version_id == run.transcript_version_id,
            DetectedPiiDismissal.source_start_index == span.start,
            DetectedPiiDismissal.source_end_index == span.end,
        )
        .with_for_update()
    )
    if existing is not None:
        return False
    dismissal_id = uuid.uuid4()
    db.add(
        DetectedPiiDismissal(
            id=dismissal_id,
            transcript_id=transcript.id,
            transcript_version_id=run.transcript_version_id,
            owner_user_id=owner.id,
            team_id=transcript.team_id,
            source_start_index=span.start,
            source_end_index=span.end,
            source_text_encrypted=encrypt_text_for_owner(
                db,
                owner_user_id=owner.id,
                table="detected_pii_dismissals",
                field="source_text_encrypted",
                record_id=dismissal_id,
                plaintext=span.text,
            ),
        )
    )
    db.commit()
    return True


def restore_detected_pii_entity_service(
    db: Session,
    owner: Any,
    *,
    transcript_id: uuid.UUID,
    entity_id: uuid.UUID,
) -> bool:
    _, run, entity = _owner_detected_pii_entity_for_dismissal(db, owner.id, transcript_id=transcript_id, entity_id=entity_id)
    span = _redaction_source_spans(db, run=run).get(entity.id)
    if span is None:
        raise AppError(409, "redaction_override_requires_review", "The detected PII needs to be reviewed again")
    dismissal = db.scalar(
        select(DetectedPiiDismissal)
        .where(
            DetectedPiiDismissal.transcript_version_id == run.transcript_version_id,
            DetectedPiiDismissal.owner_user_id == owner.id,
            DetectedPiiDismissal.source_start_index == span.start,
            DetectedPiiDismissal.source_end_index == span.end,
        )
        .with_for_update()
    )
    if dismissal is None:
        return False
    db.delete(dismissal)
    db.commit()
    return True


def ensure_redaction_run_for_transcript_version(db: Session, *, transcript_version: TranscriptVersion) -> RedactionRun:
    existing = db.scalar(
        select(RedactionRun)
        .where(
            RedactionRun.transcript_version_id == transcript_version.id,
            RedactionRun.status == RedactionRunStatus.succeeded,
        )
        .order_by(RedactionRun.created_at.desc(), RedactionRun.id.desc())
        .limit(1)
    )
    if existing is not None:
        from app.services.clinical_nlp import ensure_optional_clinical_entity_run_for_transcript_version

        clinical_run = ensure_optional_clinical_entity_run_for_transcript_version(
            db,
            transcript_version=transcript_version,
            redaction_run=existing,
        )
        if clinical_run is not None:
            db.refresh(existing)
        return existing

    provider = active_team_deidentification_provider(db, team_id=transcript_version.transcript.team_id)
    run = RedactionRun(
        transcript_id=transcript_version.transcript_id,
        transcript_version_id=transcript_version.id,
        owner_user_id=transcript_version.transcript.owner_user_id,
        team_id=transcript_version.transcript.team_id,
        status=RedactionRunStatus.succeeded,
        api_provider=provider.label,
    )
    db.add(run)
    db.flush()
    try:
        transcript_text = (
            decrypt_text_for_owner(
                db,
                owner_user_id=transcript_version.transcript.owner_user_id,
                table="transcript_versions",
                field="text_encrypted",
                record_id=transcript_version.id,
                stored_value=transcript_version.text_encrypted,
            )
            or ""
        )
        if not transcript_text.strip():
            run.redacted_text_encrypted = encrypt_text_for_owner(
                db,
                owner_user_id=transcript_version.transcript.owner_user_id,
                table="redaction_runs",
                field="redacted_text_encrypted",
                record_id=run.id,
                plaintext="",
            )
            run.mapping_hash = _mapping_hash([])
            run.entity_count = 0
            run.api_model_or_version = None
            run.error_code = None
            run.failed_at = None
            db.add(run)
            db.commit()
            db.refresh(run)
            return run
        result = redact_text_with_mapping(db, transcript_text, provider=provider)
        run.redacted_text_encrypted = encrypt_text_for_owner(
            db,
            owner_user_id=transcript_version.transcript.owner_user_id,
            table="redaction_runs",
            field="redacted_text_encrypted",
            record_id=run.id,
            plaintext=result["redacted_text"],
        )
        run.mapping_hash = _mapping_hash(result["phi_index"])
        run.entity_count = int(result["phi_count"])
        run.api_provider = result["api_provider"]
        run.api_model_or_version = result["api_model_or_version"]
        run.error_code = None
        run.failed_at = None
        for item in result["phi_index"]:
            entity_id = uuid.uuid4()
            db.add(
                RedactionEntity(
                    id=entity_id,
                    redaction_run_id=run.id,
                    entity_order=int(item["index"]),
                    entity_type=str(item["type"]),
                    placeholder=str(item["placeholder"]),
                    original_value_encrypted=encrypt_text_for_owner(
                        db,
                        owner_user_id=transcript_version.transcript.owner_user_id,
                        table="redaction_entities",
                        field="original_value_encrypted",
                        record_id=entity_id,
                        plaintext=str(item["value"]),
                    ),
                    normalized_value_hash=_normalized_value_hash(str(item["value"])),
                    occurrence_count=1,
                )
            )
        db.add(run)
        db.commit()
        db.refresh(run)
    except AppError as exc:
        run.status = RedactionRunStatus.failed
        run.error_code = exc.code
        run.failed_at = utcnow()
        db.add(run)
        db.commit()
        raise
    except Exception as exc:  # pragma: no cover
        run.status = RedactionRunStatus.failed
        run.error_code = "redaction_failed"
        run.failed_at = utcnow()
        db.add(run)
        db.commit()
        raise AppError(502, "redaction_failed", "PHI redaction failed") from exc

    from app.services.clinical_nlp import ensure_optional_clinical_entity_run_for_transcript_version

    # Required redaction is durable before optional enrichment begins.
    ensure_optional_clinical_entity_run_for_transcript_version(
        db,
        transcript_version=transcript_version,
        redaction_run=run,
    )
    db.refresh(run)
    return run


def next_placeholder_index(run: RedactionRun) -> int:
    return max((entity.entity_order for entity in run.entities), default=0) + 1


def combined_phi_index(
    db: Session,
    run: RedactionRun,
    *,
    extra_phi_index: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    index = _redaction_run_to_phi_index(db, run)
    if extra_phi_index:
        index.extend(extra_phi_index)
    return index


def validate_redacted_output_placeholders(redacted_text: str, *, phi_index: list[dict[str, Any]]) -> None:
    known = {int(item["index"]) for item in phi_index}
    for match in POTENTIAL_PHI_TOKEN_PATTERN.finditer(redacted_text):
        token = match.group(0)
        exact = PHI_TOKEN_PATTERN.fullmatch(token)
        if exact is None:
            raise AppError(
                422,
                "redaction_placeholder_invalid",
                "Generated output contained a malformed PHI placeholder",
            )
        if int(exact.group(1)) not in known:
            raise AppError(
                422,
                "redaction_placeholder_invalid",
                "Generated output contained an unknown PHI placeholder",
            )
    if "[PHI-" in redacted_text:
        valid_tokens = {match.group(0) for match in PHI_TOKEN_PATTERN.finditer(redacted_text)}
        fragments = {match.group(0) for match in POTENTIAL_PHI_TOKEN_PATTERN.finditer(redacted_text)}
        invalid_fragments = fragments - valid_tokens
        if invalid_fragments:
            raise AppError(
                422,
                "redaction_placeholder_invalid",
                "Generated output contained a malformed PHI placeholder",
            )


def reidentify_text(redacted_text: str, *, phi_index: list[dict[str, Any]]) -> str:
    validate_redacted_output_placeholders(redacted_text, phi_index=phi_index)
    index_map = {int(item["index"]): str(item["value"]) for item in phi_index}

    def replace(match: re.Match[str]) -> str:
        token_index = int(match.group(1))
        return index_map[token_index]

    return PHI_TOKEN_PATTERN.sub(replace, redacted_text)
