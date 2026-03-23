from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    RedactionEntity,
    RedactionRun,
    RedactionRunStatus,
    TranscriptVersion,
    utcnow,
)

from .redaction_policy import filter_analyzer_results, normalize_span_bounds


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "redaction" / "presidio_config.yaml"
PHI_TOKEN_PATTERN = re.compile(r"\[PHI-(\d+)\]")
POTENTIAL_PHI_TOKEN_PATTERN = re.compile(r"\[PHI-[^\]]*(?:\]|$)")


@dataclass
class Span:
    start: int
    end: int
    entity_type: str
    score: float

    @property
    def length(self) -> int:
        return self.end - self.start


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
        spans.append(Span(start=start, end=end, entity_type=str(result.entity_type), score=float(result.score)))
    return spans


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
    text: str,
    *,
    language: str = "en",
    score_threshold: float = 0.35,
    entities: list[str] | None = None,
    start_index: int = 1,
) -> dict[str, Any]:
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
    spans = _resolve_overlaps(_filter_results(text, raw_results))

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
        "api_provider": "native_presidio",
        "api_model_or_version": config.get("nlp", {}).get("model_name", "en_core_web_sm"),
    }


def redact_transient_text(text: str, *, start_index: int) -> dict[str, Any]:
    clean = text.strip()
    if not clean:
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "native_presidio",
            "api_model_or_version": _load_yaml(DEFAULT_CONFIG_PATH).get("nlp", {}).get("model_name", "en_core_web_sm"),
        }
    return redact_text_with_mapping(clean, start_index=start_index)


def _mapping_hash(phi_index: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        [{"index": item["index"], "type": item["type"], "value": item["value"]} for item in phi_index],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _redaction_run_to_phi_index(run: RedactionRun) -> list[dict[str, Any]]:
    ordered = sorted(run.entities, key=lambda entity: entity.entity_order)
    return [
        {
            "index": entity.entity_order,
            "type": entity.entity_type,
            "value": entity.original_value_encrypted,
            "placeholder": entity.placeholder,
        }
        for entity in ordered
    ]


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
        return existing

    run = RedactionRun(
        transcript_id=transcript_version.transcript_id,
        transcript_version_id=transcript_version.id,
        owner_user_id=transcript_version.transcript.owner_user_id,
        team_id=transcript_version.transcript.team_id,
        status=RedactionRunStatus.succeeded,
        api_provider="native_presidio",
    )
    db.add(run)
    db.flush()
    try:
        result = redact_text_with_mapping(transcript_version.text_encrypted)
        run.redacted_text_encrypted = result["redacted_text"]
        run.mapping_hash = _mapping_hash(result["phi_index"])
        run.entity_count = int(result["phi_count"])
        run.api_provider = result["api_provider"]
        run.api_model_or_version = result["api_model_or_version"]
        run.error_code = None
        run.failed_at = None
        for item in result["phi_index"]:
            db.add(
                RedactionEntity(
                    redaction_run_id=run.id,
                    entity_order=int(item["index"]),
                    entity_type=str(item["type"]),
                    placeholder=str(item["placeholder"]),
                    original_value_encrypted=str(item["value"]),
                    normalized_value_hash=_normalized_value_hash(str(item["value"])),
                    occurrence_count=1,
                )
            )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
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


def next_placeholder_index(run: RedactionRun) -> int:
    return max((entity.entity_order for entity in run.entities), default=0) + 1


def combined_phi_index(
    run: RedactionRun,
    *,
    extra_phi_index: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    index = _redaction_run_to_phi_index(run)
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
