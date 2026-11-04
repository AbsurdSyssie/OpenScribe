"""Redact owner-entered consultation problem titles for provider routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import User
from app.services.redaction import (
    redact_transient_text,
    validate_redacted_output_placeholders,
)
from app.services.redaction_primitives import (
    apply_manual_pii_redaction_to_values,
    manual_pii_entities_for_transcript,
    manual_pii_protections_from_entities,
    redact_dynamic_prompt_text,
)
from app.services.transcripts import manual_pii_entity_value


def redact_problem_plan_titles(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    source_snapshot: Mapping[str, Any],
    problems: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Return redacted titles keyed by problem UUID and the merged PHI map.

    The caller must invoke this after source preparation has released its
    canonical transcript locks, then reacquire those locks and re-prove the
    source and plan revisions before committing a batch. Raw titles and
    mapping values remain local to this boundary and are never logged.
    """
    try:
        if owner.team_id is None or not isinstance(source_snapshot, Mapping):
            raise _redaction_failed()
        source_state = source_snapshot.get("source_state")
        if not isinstance(source_state, Mapping):
            raise _redaction_failed()
        if (
            source_state.get("owner_user_id") != str(owner.id)
            or source_state.get("team_id") != str(owner.team_id)
            or source_state.get("transcript_id") != str(transcript_id)
        ):
            raise _redaction_failed()
        base_index = source_snapshot.get("phi_index")
        if not isinstance(base_index, list):
            raise _redaction_failed()
        maximum_index = 0
        seen_indices: set[int] = set()
        for entry in base_index:
            if not isinstance(entry, Mapping):
                raise _redaction_failed()
            index = entry.get("index")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 1
                or index in seen_indices
                or entry.get("placeholder") != f"[PHI-{index}]"
                or not isinstance(entry.get("type"), str)
                or not entry.get("type", "").strip()
                or not isinstance(entry.get("value"), str)
                or not entry.get("value")
            ):
                raise _redaction_failed()
            seen_indices.add(index)
            maximum_index = max(maximum_index, index)

        title_values: list[tuple[str, str]] = []
        seen_problem_ids: set[str] = set()
        if len(problems) > 6:
            raise _redaction_failed()
        if not problems:
            return {}, deepcopy(base_index)
        for problem in problems:
            if not isinstance(problem, Mapping):
                raise _redaction_failed()
            problem_id = str(UUID(str(problem.get("uuid"))))
            title = problem.get("title")
            if problem_id in seen_problem_ids or not isinstance(title, str) or not title.strip() or len(title) > 255:
                raise _redaction_failed()
            seen_problem_ids.add(problem_id)
            title_values.append((problem_id, title))

        # Dynamic de-identification gets one monotonically increasing
        # placeholder namespace across active and ignored problem titles.
        dynamic_titles: dict[str, str] = {}
        dynamic_phi_index: list[dict[str, Any]] = []
        next_index = maximum_index + 1
        for problem_id, title in title_values:
            redacted, additions = redact_dynamic_prompt_text(
                db,
                title,
                team_id=owner.team_id,
                start_index=next_index,
                redact_text=redact_transient_text,
            )
            if not isinstance(redacted, str):
                raise _redaction_failed()
            dynamic_titles[problem_id] = redacted
            dynamic_phi_index.extend(additions)
            next_index += len(additions)

        protections = manual_pii_protections_from_entities(
            db,
            entities=manual_pii_entities_for_transcript(
                db,
                transcript_id=transcript_id,
                owner_user_id=owner.id,
            ),
            value_reader=manual_pii_entity_value,
        )
        manually_redacted, manual_phi_index = apply_manual_pii_redaction_to_values(
            dynamic_titles,
            start_index=next_index,
            protections=protections,
        )
        if not isinstance(manually_redacted, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in manually_redacted.items()
        ):
            raise _redaction_failed()
        merged_phi_index = [
            *deepcopy(base_index),
            *dynamic_phi_index,
            *manual_phi_index,
        ]
        for redacted_title in manually_redacted.values():
            validate_redacted_output_placeholders(redacted_title, phi_index=merged_phi_index)
        return manually_redacted, merged_phi_index
    except AppError:
        raise _redaction_failed() from None
    except Exception:
        raise _redaction_failed() from None


def _redaction_failed() -> AppError:
    return AppError(500, "redaction_failed", "Problem titles could not be safely redacted")
