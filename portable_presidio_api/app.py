#!/usr/bin/env python3
"""Portable Presidio-only PHI redaction and re-identification API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider

from presidio_policy import filter_analyzer_results, normalize_span_bounds

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "presidio_config.yaml"
PHI_TOKEN_PATTERN = re.compile(r"\[PHI-(\d+)\]")


def _load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config at {config_path} must be a mapping")
    return data


def _build_analyzer(config: dict[str, Any]) -> AnalyzerEngine:
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
    return analyzer


CONFIG = _load_config(DEFAULT_CONFIG_PATH)
ANALYZER = _build_analyzer(CONFIG)


@dataclass
class Span:
    start: int
    end: int
    entity_type: str
    score: float

    @property
    def length(self) -> int:
        return self.end - self.start


class RedactRequest(BaseModel):
    text: str = Field(..., description="Input text to redact.")
    language: str = Field(default="en", description="Presidio analyzer language.")
    score_threshold: float = Field(default=0.35, description="Minimum analyzer score.")
    entities: list[str] | None = Field(
        default=None,
        description="Optional list of entities to detect. Omit to use Presidio defaults plus custom recognizers.",
    )


class PhiItem(BaseModel):
    index: int = Field(..., description="Sequential placeholder index used in the redacted text.")
    type: str = Field(..., description="Detected Presidio entity type.")
    value: str = Field(..., description="Original detected PHI value.")


class RedactResponse(BaseModel):
    redacted_text: str = Field(..., description="Text with PHI placeholders like [PHI-1].")
    phi_mapping: dict[str, dict[str, str]] = Field(
        ...,
        description="Mapping keyed by phi-N for downstream systems that prefer object lookup.",
    )
    phi_index: list[PhiItem] = Field(
        ...,
        description="Ordered PHI index used for deterministic re-identification.",
    )
    phi_count: int = Field(..., description="Total number of PHI items replaced.")


class UnredactRequest(BaseModel):
    redacted_text: str = Field(..., description="Text containing placeholders like [PHI-1].")
    phi_index: list[PhiItem] = Field(..., description="Original PHI values keyed by placeholder index.")


class UnredactResponse(BaseModel):
    unredacted_text: str = Field(..., description="Restored text after replacing placeholders.")


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
        spans.append(
            Span(
                start=start,
                end=end,
                entity_type=str(result.entity_type),
                score=float(result.score),
            )
        )
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


def redact_text_with_mapping(
    text: str,
    *,
    language: str = "en",
    score_threshold: float = 0.35,
    entities: list[str] | None = None,
) -> dict[str, Any]:
    raw_results = ANALYZER.analyze(
        text=text,
        language=language,
        entities=entities,
        score_threshold=score_threshold,
    )
    spans = _resolve_overlaps(_filter_results(text, raw_results))

    parts: list[str] = []
    phi_index: list[dict[str, Any]] = []
    cursor = 0
    next_index = 1

    for span in spans:
        parts.append(text[cursor:span.start])
        parts.append(f"[PHI-{next_index}]")
        value = text[span.start:span.end]
        phi_index.append({"index": next_index, "type": span.entity_type, "value": value})
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
    }


def unredact_text(redacted_text: str, phi_index: list[dict[str, Any]]) -> str:
    index_map = {int(item["index"]): str(item["value"]) for item in phi_index}

    def replace(match: re.Match[str]) -> str:
        token_index = int(match.group(1))
        return index_map.get(token_index, match.group(0))

    return PHI_TOKEN_PATTERN.sub(replace, redacted_text)


app = FastAPI(
    title="Portable Presidio PHI API",
    description=(
        "Standalone Presidio API for PHI redaction, indexing, and re-identification. "
        "Custom recognizers are loaded from presidio_config.yaml."
    ),
    version="1.0.0",
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/config")
async def get_config() -> dict[str, Any]:
    return {
        "model_name": CONFIG.get("nlp", {}).get("model_name", "en_core_web_sm"),
        "custom_entities": [
            recognizer["entity_name"] for recognizer in CONFIG.get("custom_recognizers", [])
        ],
    }


@app.post("/redact", response_model=RedactResponse)
async def redact(request: RedactRequest) -> dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        return redact_text_with_mapping(
            request.text,
            language=request.language,
            score_threshold=request.score_threshold,
            entities=request.entities,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Redaction failed: {exc}") from exc


@app.post("/unredact", response_model=UnredactResponse)
async def unredact(request: UnredactRequest) -> dict[str, str]:
    try:
        return {
            "unredacted_text": unredact_text(
                redacted_text=request.redacted_text,
                phi_index=[item.model_dump() for item in request.phi_index],
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unredaction failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010)
