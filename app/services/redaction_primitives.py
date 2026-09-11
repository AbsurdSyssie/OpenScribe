"""Cycle-safe redaction helpers with caller-supplied service dependencies."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import TranscriptManualPiiEntity


PhiIndex: TypeAlias = dict[str, Any]
RedactText: TypeAlias = Callable[[Session, str], Mapping[str, object]]
ManualPiiValueReader: TypeAlias = Callable[[Session, TranscriptManualPiiEntity], str]


@dataclass(frozen=True, slots=True)
class ManualPiiProtection:
    entity_type: str
    value: str


def redact_dynamic_prompt_text(
    db: Session,
    text: str | None,
    *,
    team_id: UUID,
    start_index: int,
    redact_text: Callable[..., Mapping[str, object]],
) -> tuple[str | None, list[PhiIndex]]:
    """Redact non-persisted prompt text, preserving blank input exactly."""
    if text is None:
        return None, []
    if not text.strip():
        return text, []
    result = redact_text(db, text, team_id=team_id, start_index=start_index)
    if not isinstance(result, Mapping):
        raise AppError(500, "redaction_failed", "Dynamic prompt redaction returned an invalid result")
    redacted_text = result.get("redacted_text")
    phi_index = result.get("phi_index")
    if not isinstance(redacted_text, str) or not isinstance(phi_index, list):
        raise AppError(500, "redaction_failed", "Dynamic prompt redaction returned an invalid result")
    return redacted_text, _validated_phi_index(phi_index, start_index=start_index)


def redact_dynamic_prompt_value(
    db: Session,
    value: object,
    *,
    team_id: UUID,
    start_index: int,
    redact_text: Callable[..., Mapping[str, object]],
) -> tuple[object, list[PhiIndex]]:
    """Redact string leaves in lists/dicts in insertion order without mutation."""
    if isinstance(value, str):
        return redact_dynamic_prompt_text(
            db,
            value,
            team_id=team_id,
            start_index=start_index,
            redact_text=redact_text,
        )
    if isinstance(value, list):
        redacted_items: list[object] = []
        phi_index: list[PhiIndex] = []
        next_index = start_index
        for item in value:
            redacted_item, item_phi_index = redact_dynamic_prompt_value(
                db,
                item,
                team_id=team_id,
                start_index=next_index,
                redact_text=redact_text,
            )
            redacted_items.append(redacted_item)
            phi_index.extend(item_phi_index)
            next_index += len(item_phi_index)
        return redacted_items, phi_index
    if isinstance(value, dict):
        redacted_dict: dict[object, object] = {}
        phi_index: list[PhiIndex] = []
        next_index = start_index
        for key, item in value.items():
            redacted_item, item_phi_index = redact_dynamic_prompt_value(
                db,
                item,
                team_id=team_id,
                start_index=next_index,
                redact_text=redact_text,
            )
            redacted_dict[key] = redacted_item
            phi_index.extend(item_phi_index)
            next_index += len(item_phi_index)
        return redacted_dict, phi_index
    return value, []


def manual_pii_entities_for_transcript(
    db: Session,
    *,
    transcript_id: UUID,
    owner_user_id: UUID,
) -> list[TranscriptManualPiiEntity]:
    return list(
        db.scalars(
            select(TranscriptManualPiiEntity)
            .where(
                TranscriptManualPiiEntity.transcript_id == transcript_id,
                TranscriptManualPiiEntity.owner_user_id == owner_user_id,
            )
            .order_by(TranscriptManualPiiEntity.created_at.asc(), TranscriptManualPiiEntity.id.asc())
        )
    )


def manual_pii_protections_from_entities(
    db: Session,
    *,
    entities: Iterable[TranscriptManualPiiEntity],
    value_reader: Callable[..., str],
) -> list[ManualPiiProtection]:
    """Decrypt and normalize protections, preserving first entity/type per value."""
    protections: list[ManualPiiProtection] = []
    seen_values: set[str] = set()
    for entity in entities:
        raw_value = value_reader(db, entity=entity)
        value = _normalize_value(raw_value)
        normalized_key = value.lower()
        if not value or normalized_key in seen_values:
            continue
        seen_values.add(normalized_key)
        protections.append(ManualPiiProtection(entity_type=entity.entity_type, value=value))
    return protections


def apply_manual_pii_redaction(
    *,
    transcript_text: str,
    dictation_text: str,
    start_index: int,
    protections: Iterable[ManualPiiProtection],
) -> tuple[str, str, list[dict[str, str | int]]]:
    """Apply each distinct manual protection to both source streams."""
    redacted_transcript_text = transcript_text
    redacted_dictation_text = dictation_text
    phi_index: list[dict[str, str | int]] = []
    next_index = start_index
    normalized_protections = _deduplicated_protections(protections)
    for protection in sorted(normalized_protections, key=lambda item: len(item.value), reverse=True):
        pattern = manual_pii_value_pattern(protection.value)
        if pattern.search(redacted_transcript_text) is None and pattern.search(redacted_dictation_text) is None:
            continue
        placeholder = f"[PHI-{next_index}]"
        redacted_transcript_text = pattern.sub(placeholder, redacted_transcript_text)
        redacted_dictation_text = pattern.sub(placeholder, redacted_dictation_text)
        phi_index.append(
            {
                "index": next_index,
                "type": protection.entity_type,
                "value": protection.value,
                "placeholder": placeholder,
            }
        )
        next_index += 1
    return redacted_transcript_text, redacted_dictation_text, phi_index


def apply_manual_pii_redaction_to_values(
    values: Mapping[str, object],
    *,
    start_index: int,
    protections: Iterable[ManualPiiProtection],
) -> tuple[dict[str, object], list[dict[str, str | int]]]:
    """Apply one manual-PII placeholder namespace across nested source values.

    Values are copied recursively in mapping insertion order.  Each protected
    value receives one placeholder everywhere it occurs, including separate
    source streams and structured Working-note leaves.
    """
    redacted: dict[str, object] = dict(values)
    phi_index: list[dict[str, str | int]] = []
    next_index = start_index
    for protection in sorted(_deduplicated_protections(protections), key=lambda item: len(item.value), reverse=True):
        pattern = manual_pii_value_pattern(protection.value)
        if not _value_contains_pattern(redacted, pattern):
            continue
        placeholder = f"[PHI-{next_index}]"
        redacted = {key: _replace_value_pattern(value, pattern, placeholder) for key, value in redacted.items()}
        phi_index.append(
            {
                "index": next_index,
                "type": protection.entity_type,
                "value": protection.value,
                "placeholder": placeholder,
            }
        )
        next_index += 1
    return redacted, phi_index


def merge_manual_pii_protections(
    confirmed: Iterable[ManualPiiProtection],
    additions: Iterable[ManualPiiProtection],
) -> list[ManualPiiProtection]:
    """Return the monotonic first-wins union for a future immutable snapshot."""
    return _deduplicated_protections((*confirmed, *additions))


def manual_pii_value_pattern(value: str) -> re.Pattern[str]:
    tokens = [token for token in re.split(r"\s+", value.strip()) if token]
    return re.compile(r"\s+".join(re.escape(token) for token in tokens), re.IGNORECASE)


def _deduplicated_protections(protections: Iterable[ManualPiiProtection]) -> list[ManualPiiProtection]:
    normalized: list[ManualPiiProtection] = []
    seen_values: set[str] = set()
    for protection in protections:
        if not isinstance(protection, ManualPiiProtection):
            continue
        value = _normalize_value(protection.value)
        normalized_key = value.lower()
        if not value or normalized_key in seen_values:
            continue
        seen_values.add(normalized_key)
        normalized.append(ManualPiiProtection(entity_type=protection.entity_type, value=value))
    return normalized


def _normalize_value(value: object) -> str:
    return " ".join(value.strip().split()) if isinstance(value, str) else ""


def _value_contains_pattern(value: object, pattern: re.Pattern[str]) -> bool:
    if isinstance(value, str):
        return pattern.search(value) is not None
    if isinstance(value, list):
        return any(_value_contains_pattern(item, pattern) for item in value)
    if isinstance(value, dict):
        return any(_value_contains_pattern(item, pattern) for item in value.values())
    return False


def _replace_value_pattern(value: object, pattern: re.Pattern[str], placeholder: str) -> object:
    if isinstance(value, str):
        return pattern.sub(placeholder, value)
    if isinstance(value, list):
        return [_replace_value_pattern(item, pattern, placeholder) for item in value]
    if isinstance(value, dict):
        return {key: _replace_value_pattern(item, pattern, placeholder) for key, item in value.items()}
    return value


def _validated_phi_index(raw_phi_index: list[object], *, start_index: int) -> list[PhiIndex]:
    """Validate the mapping shape consumed by redaction re-identification."""
    validated: list[PhiIndex] = []
    seen_indices: set[int] = set()
    required_fields = {"index", "type", "value", "placeholder"}
    for offset, item in enumerate(raw_phi_index):
        if not isinstance(item, Mapping) or not required_fields.issubset(item):
            raise AppError(500, "redaction_failed", "Dynamic prompt redaction returned an invalid result")
        index = item.get("index")
        entity_type = item.get("type")
        value = item.get("value")
        placeholder = item.get("placeholder")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index != start_index + offset
            or index < 1
            or index in seen_indices
            or not isinstance(entity_type, str)
            or not entity_type.strip()
            or not isinstance(value, str)
            or not value
            or not isinstance(placeholder, str)
            or placeholder != f"[PHI-{index}]"
        ):
            raise AppError(500, "redaction_failed", "Dynamic prompt redaction returned an invalid result")
        seen_indices.add(index)
        validated.append(dict(item))
    return validated
