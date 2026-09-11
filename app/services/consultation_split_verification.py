"""Pure contract for the optional bundled split-note checker.

Only a complete set of structured survivors can use the existing exact
substring patch protocol.  Freeform and mixed batches deliberately bypass the
checker: there is no freeform patch protocol.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.errors import AppError
from app.services.consultation_split_generation import (
    SplitGenerationTopic,
    parse_split_generation,
)
from app.services.templates import (
    _apply_hallucination_check_edits,
    _hallucination_check_response_json_schema,
    _parse_hallucination_check_response,
)

MAX_BUNDLE_VERIFICATION_PROMPT_CHARS = 220_000
MAX_BUNDLE_VERIFICATION_RESPONSE_CHARS = 262_144


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSplitVerification:
    request_body: dict[str, object]
    response_json_schema: dict[str, object]
    output_token_cap: int
    reservation_units: int


def _invalid() -> AppError:
    return AppError(502, "consultation_split_verification_invalid_output", "Bundled verification output was invalid")


def _topics(plan: Mapping[str, Any], allowed: set[UUID] | None = None) -> list[SplitGenerationTopic]:
    raw_topics = plan.get("topics")
    if not isinstance(raw_topics, list):
        raise _invalid()
    result: list[SplitGenerationTopic] = []
    for item in raw_topics:
        if not isinstance(item, dict) or item.get("disposition") != "separate_note":
            continue
        try:
            item_uuid = UUID(str(item["topic_uuid"]))
        except (KeyError, TypeError, ValueError):
            raise _invalid() from None
        if allowed is not None and item_uuid not in allowed:
            continue
        template = item.get("template")
        sections = template.get("structured_sections", {}).get("sections") if isinstance(template, dict) else None
        if not isinstance(sections, list):
            raise _invalid()
        try:
            topic_uuid = item_uuid
            keys = tuple(str(section["section_key"]) for section in sections)
        except (KeyError, TypeError, ValueError):
            raise _invalid() from None
        result.append(SplitGenerationTopic(topic_uuid=topic_uuid, mode="structured", section_keys=keys))
    if not result:
        raise _invalid()
    return result


def prepare_split_verification_request(*, source_snapshot: Mapping[str, Any], clinical_snapshot: Mapping[str, Any],
                                       confirmed_plan: Mapping[str, Any], survivor_outputs: Mapping[UUID, Mapping[str, Any]]) -> PreparedSplitVerification:
    """Create a deterministic request from immutable snapshots and survivors.

    The caller proves all requested survivors are structured.  This function
    intentionally does not accept live templates, generated documents, edits,
    or titles.
    """
    topics = _topics(confirmed_plan, allowed=set(survivor_outputs))
    if set(survivor_outputs) != {topic.topic_uuid for topic in topics}:
        raise _invalid()
    notes: list[dict[str, object]] = []
    for topic in topics:
        output = survivor_outputs[topic.topic_uuid]
        if output.get("mode") != "structured" or not isinstance(output.get("content"), dict):
            raise _invalid()
        notes.append({"topic_uuid": str(topic.topic_uuid), "content": output["content"]})
    payload = {"source": source_snapshot, "clinical": clinical_snapshot, "plan": confirmed_plan, "notes": notes}
    try:
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, RecursionError):
        raise _invalid() from None
    if len(encoded) > MAX_BUNDLE_VERIFICATION_PROMPT_CHARS:
        raise _invalid()
    system = (
        "Check every bundled structured clinical-note survivor against only the supplied redacted source and clinical hints. "
        "Return one JSON object keyed by exact topic UUID. Each value must use the exact-substring correction protocol: "
        "{\"status\":\"unchanged\"} or {\"status\":\"corrected\",\"edits\":[{\"section_key\":\"key\",\"original\":\"exact substring\",\"replacement\":\"replacement\"}]}. "
        "Do not add topics or sections. Do not return commentary, reasoning, or markdown."
    )
    schema = {
        "type": "object", "additionalProperties": False,
        "required": [str(topic.topic_uuid) for topic in topics],
        "properties": {str(topic.topic_uuid): _hallucination_check_response_json_schema() for topic in topics},
    }
    # A bounded, conservative estimate is enough for the independent checker
    # reservation; no generated-document checker settings are inherited.
    output_cap = max(512, min(8192, len(topics) * 1024))
    return PreparedSplitVerification(
        request_body={"messages": [{"role": "system", "content": system}, {"role": "user", "content": encoded}]},
        response_json_schema=schema, output_token_cap=output_cap,
        reservation_units=max(1, (len(system) + len(encoded)) // 4 + output_cap),
    )


def apply_split_verification_response(*, payload_text: str, confirmed_plan: Mapping[str, Any],
                                      survivor_outputs: Mapping[UUID, Mapping[str, Any]], edit_cap: int = 32) -> tuple[dict[UUID, dict[str, Any]], int]:
    """Validate the whole response, apply patches in memory, then revalidate.

    A single malformed topic invalidates the entire verifier response.  The
    returned values are candidates for ``verified_output_encrypted`` only.
    """
    if not isinstance(payload_text, str) or len(payload_text) > MAX_BUNDLE_VERIFICATION_RESPONSE_CHARS:
        raise _invalid()
    topics = _topics(confirmed_plan, allowed=set(survivor_outputs))
    expected = {topic.topic_uuid for topic in topics}
    if set(survivor_outputs) != expected:
        raise _invalid()
    try:
        raw = json.loads(payload_text)
    except (TypeError, ValueError):
        raise _invalid() from None
    if not isinstance(raw, dict) or set(raw) != {str(item) for item in expected}:
        raise _invalid()
    corrected: dict[UUID, dict[str, Any]] = {}
    count = 0
    for topic in topics:
        current = survivor_outputs[topic.topic_uuid]
        content = current.get("content")
        if current.get("mode") != "structured" or not isinstance(content, dict):
            raise _invalid()
        try:
            # The ordinary checker parser owns the exact patch contract and
            # accepts its JSON envelope as text.  Keep the bundled outer
            # envelope structured, then pass each value through unchanged as
            # canonical JSON rather than creating another parser.
            parsed = _parse_hallucination_check_response(
                json.dumps(raw[str(topic.topic_uuid)], separators=(",", ":"), sort_keys=True),
                edit_cap=edit_cap,
            )
            if any(edit["section_key"] == "__title__" for edit in parsed["edits"]):
                # Split outcomes have no provider title field.  Silently
                # accepting a title patch would create a second protocol.
                raise _invalid()
            sections = [{"section_key": key, "text": value} for key, value in content.items()]
            _title, sections, applied = _apply_hallucination_check_edits(title="Split note", sections=sections, edits=parsed["edits"])
            next_content = {str(item["section_key"]): str(item["text"]) for item in sections}
            # The split contract requires all captured keys and non-empty text;
            # parse it again rather than trusting the patch operation.
            validated = parse_split_generation({"notes": [{"topic_uuid": str(topic.topic_uuid), "mode": "structured", "content": next_content}]}, topics=[topic])[0]
        except (AppError, KeyError, TypeError, ValueError):
            raise _invalid() from None
        corrected[topic.topic_uuid] = {"mode": "structured", "content": validated.content}
        count += applied
    return corrected, count
