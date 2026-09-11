from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.errors import AppError
from app.models import Transcript, TranscriptIngestionMode, TranscriptManualPiiEntity, TranscriptStatus, utcnow
from app.services.redaction_primitives import (
    ManualPiiProtection,
    apply_manual_pii_redaction,
    manual_pii_entities_for_transcript,
    manual_pii_protections_from_entities,
    merge_manual_pii_protections,
    redact_dynamic_prompt_text,
    redact_dynamic_prompt_value,
)


def test_dynamic_text_preserves_none_and_blank_without_callback():
    calls = []

    def redact(*args, **kwargs):
        calls.append((args, kwargs))
        return {"redacted_text": "unused", "phi_index": []}

    assert redact_dynamic_prompt_text(None, None, team_id=uuid4(), start_index=1, redact_text=redact) == (None, [])
    assert redact_dynamic_prompt_text(None, "  \n", team_id=uuid4(), start_index=1, redact_text=redact) == ("  \n", [])
    assert calls == []


def test_dynamic_value_recurses_in_order_advances_by_emitted_mappings_and_does_not_mutate():
    team_id = uuid4()
    value = ["one", {"first": "two", "second": ["three", 7]}, "four"]
    calls = []

    def redact(_db, text, *, team_id, start_index):
        calls.append((text, team_id, start_index))
        return {
            "redacted_text": f"redacted:{text}",
            "phi_index": [
                {
                    "index": index,
                    "type": "PERSON",
                    "value": f"secret-{index}",
                    "placeholder": f"[PHI-{index}]",
                }
                for index in range(start_index, start_index + (2 if text == "two" else 1))
            ],
        }

    redacted, mappings = redact_dynamic_prompt_value(None, value, team_id=team_id, start_index=4, redact_text=redact)

    assert value == ["one", {"first": "two", "second": ["three", 7]}, "four"]
    assert redacted == ["redacted:one", {"first": "redacted:two", "second": ["redacted:three", 7]}, "redacted:four"]
    assert calls == [("one", team_id, 4), ("two", team_id, 5), ("three", team_id, 7), ("four", team_id, 8)]
    assert [mapping["index"] for mapping in mappings] == [4, 5, 6, 7, 8]


def test_dynamic_text_returns_provider_result_shape():
    result = {
        "redacted_text": "[PHI-8]",
        "phi_index": [{"index": 8, "type": "PERSON", "value": "private", "placeholder": "[PHI-8]"}],
    }

    def redact(*_args, **_kwargs):
        return result

    assert redact_dynamic_prompt_text(None, "private", team_id=uuid4(), start_index=8, redact_text=redact) == (
        "[PHI-8]",
        result["phi_index"],
    )


@pytest.mark.parametrize(
    "result",
    [
        {"redacted_text": None, "phi_index": []},
        {"redacted_text": "[PHI-1]", "phi_index": "not-a-list"},
        {"redacted_text": "[PHI-1]", "phi_index": ["not-a-mapping"]},
        {"redacted_text": "[PHI-1]", "phi_index": [{"index": 1, "type": "PERSON", "placeholder": "[PHI-1]"}]},
        {"redacted_text": "[PHI-1]", "phi_index": [{"index": "1", "type": "PERSON", "value": "Alice", "placeholder": "[PHI-1]"}]},
        {"redacted_text": "[PHI-1]", "phi_index": [{"index": 1, "type": "PERSON", "value": "Alice", "placeholder": "[PHI-2]"}]},
        {"redacted_text": "[PHI-1]", "phi_index": [{"index": 2, "type": "PERSON", "value": "Alice", "placeholder": "[PHI-2]"}]},
    ],
)
def test_dynamic_text_fails_closed_for_malformed_provider_result(result):
    with pytest.raises(AppError) as exc_info:
        redact_dynamic_prompt_text(
            None,
            "Patient Alice Secret",
            team_id=uuid4(),
            start_index=1,
            redact_text=lambda *_args, **_kwargs: result,
        )

    assert exc_info.value.code == "redaction_failed"


def test_apply_manual_pii_redaction_handles_overlaps_regex_whitespace_case_and_both_streams():
    protections = [
        ManualPiiProtection("SHORT", "Ann"),
        ManualPiiProtection("ADDRESS", "Ann (Road)"),
        ManualPiiProtection("REGEX", "A+B"),
    ]

    transcript, dictation, mappings = apply_manual_pii_redaction(
        transcript_text="ANN (Road) saw A+B. Ann remains.",
        dictation_text="ann\n(Road) and a+b.",
        start_index=3,
        protections=protections,
    )

    assert transcript == "[PHI-3] saw [PHI-5]. [PHI-4] remains."
    assert dictation == "[PHI-3] and [PHI-5]."
    assert mappings == [
        {"index": 3, "type": "ADDRESS", "value": "Ann (Road)", "placeholder": "[PHI-3]"},
        {"index": 4, "type": "SHORT", "value": "Ann", "placeholder": "[PHI-4]"},
        {"index": 5, "type": "REGEX", "value": "A+B", "placeholder": "[PHI-5]"},
    ]


def test_apply_manual_pii_redaction_ignores_blank_duplicate_and_unmatched_protections():
    protections = [
        ManualPiiProtection("FIRST", "  Alice   Smith "),
        ManualPiiProtection("SECOND", "alice smith"),
        ManualPiiProtection("BLANK", "  "),
        ManualPiiProtection("UNMATCHED", "Nobody"),
    ]

    transcript, dictation, mappings = apply_manual_pii_redaction(
        transcript_text="ALICE\n Smith", dictation_text="", start_index=1, protections=protections
    )

    assert transcript == "[PHI-1]"
    assert dictation == ""
    assert mappings == [{"index": 1, "type": "FIRST", "value": "Alice Smith", "placeholder": "[PHI-1]"}]


def test_manual_protections_decrypts_once_and_keeps_first_normalized_value():
    first = type("Entity", (), {"entity_type": "FIRST", "id": uuid4()})()
    duplicate = type("Entity", (), {"entity_type": "SECOND", "id": uuid4()})()
    blank = type("Entity", (), {"entity_type": "BLANK", "id": uuid4()})()
    values = {first.id: " Alice\n Smith ", duplicate.id: "alice smith", blank.id: "  "}
    calls = []

    def reader(_db, *, entity):
        calls.append(entity.id)
        return values[entity.id]

    protections = manual_pii_protections_from_entities(None, entities=[first, duplicate, blank], value_reader=reader)

    assert protections == [ManualPiiProtection("FIRST", "Alice Smith")]
    assert calls == [first.id, duplicate.id, blank.id]


def test_merge_manual_pii_protections_is_monotonic_first_wins():
    confirmed = [ManualPiiProtection("CONFIRMED", " Alice\tSmith "), ManualPiiProtection("BLANK", " ")]
    additions = [ManualPiiProtection("NEW_TYPE", "alice smith"), ManualPiiProtection("NEW", "Bob  Jones")]

    assert merge_manual_pii_protections(confirmed, additions) == [
        ManualPiiProtection("CONFIRMED", "Alice Smith"),
        ManualPiiProtection("NEW", "Bob Jones"),
    ]


def test_manual_entity_query_scopes_owner_and_transcript_and_orders_rows(db_session, make_team, make_user):
    team = make_team(name="Manual PII primitive ordering")
    owner = make_user(email="manual-pii-primitives-owner@example.com", team=team)
    other = make_user(email="manual-pii-primitives-other@example.com", team=team)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Primitive source",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    other_transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Other source",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add_all([transcript, other_transcript])
    db_session.flush()
    created_at = utcnow()
    first = TranscriptManualPiiEntity(
        id=uuid4(), transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        entity_type="FIRST", original_value_encrypted="first", normalized_value_hash="first", created_at=created_at,
    )
    second = TranscriptManualPiiEntity(
        id=uuid4(), transcript_id=transcript.id, owner_user_id=owner.id, team_id=team.id,
        entity_type="SECOND", original_value_encrypted="second", normalized_value_hash="second", created_at=created_at,
    )
    excluded_owner = TranscriptManualPiiEntity(
        id=uuid4(), transcript_id=transcript.id, owner_user_id=other.id, team_id=team.id,
        entity_type="OTHER_OWNER", original_value_encrypted="other", normalized_value_hash="other", created_at=created_at,
    )
    excluded_transcript = TranscriptManualPiiEntity(
        id=uuid4(), transcript_id=other_transcript.id, owner_user_id=owner.id, team_id=team.id,
        entity_type="OTHER_TRANSCRIPT", original_value_encrypted="other", normalized_value_hash="other-transcript", created_at=created_at,
    )
    db_session.add_all([second, excluded_owner, first, excluded_transcript])
    db_session.commit()

    rows = manual_pii_entities_for_transcript(db_session, transcript_id=transcript.id, owner_user_id=owner.id)

    assert [row.id for row in rows] == sorted([first.id, second.id])
