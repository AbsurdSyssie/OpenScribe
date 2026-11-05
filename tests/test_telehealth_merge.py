"""Pure tests for source-role attribution and conservative ASR merging."""
from uuid import UUID

import pytest

from app.services.telehealth_merge import (
    TelehealthSourceTurn,
    merge_telehealth_source_turns,
)


P1 = UUID("11111111-1111-4111-8111-111111111111")
P2 = UUID("22222222-2222-4222-8222-222222222222")
P3 = UUID("33333333-3333-4333-8333-333333333333")


def test_timed_merge_interleaves_in_consultation_order_not_worker_order():
    # The patient's provider job completed first, even though the clinician
    # had spoken earlier. Overlap between sources is not removed.
    turns = [
        TelehealthSourceTurn(P2, "patient", 1, 0, "Synthetic patient reply", 3200, 4200),
        TelehealthSourceTurn(P1, "clinician", 1, 0, "Synthetic question", 1000, 2400),
        TelehealthSourceTurn(P3, "clinician", 2, 0, "Synthetic clarification", 3900, 4500),
    ]
    result = merge_telehealth_source_turns(turns)
    assert result.mode == "timed"
    assert [(turn.speaker_label, turn.start_ms, turn.end_ms) for turn in result.turns] == [
        ("Clinician", 1000, 2400),
        ("Patient", 3200, 4200),
        ("Clinician", 3900, 4500),
    ]
    assert result.canonical_text == "\n".join(turn.text for turn in result.turns)
    assert result.labelled_text.startswith("Clinician: Synthetic question")
    assert "Patient: Synthetic patient reply" in result.labelled_text


def test_untimed_provider_fallback_keeps_two_labelled_source_blocks():
    turns = [
        TelehealthSourceTurn(P2, "patient", 2, 0, "Later patient content"),
        TelehealthSourceTurn(P1, "patient", 1, 0, "Earlier patient content"),
        TelehealthSourceTurn(P3, "clinician", 1, 0, "Clinician content"),
    ]
    result = merge_telehealth_source_turns(turns)
    assert result.mode == "source_blocks"
    assert len(result.turns) == 2
    assert [turn.speaker_label for turn in result.turns] == ["Clinician", "Patient"]
    assert result.turns[1].text == "Earlier patient content\nLater patient content"
    assert all(turn.start_ms is None and turn.end_ms is None for turn in result.turns)
    assert result.canonical_text == "\n".join(turn.text for turn in result.turns)
    assert result.labelled_text == (
        "Clinician:\nClinician content\n\n"
        "Patient:\nEarlier patient content\nLater patient content"
    )


def test_a_single_untimed_channel_demotes_whole_consultation_to_source_blocks():
    turns = [
        TelehealthSourceTurn(P1, "clinician", 1, 0, "Timed clinician", 2000, 2500),
        TelehealthSourceTurn(P2, "patient", 1, 0, "Flat patient text"),
    ]
    result = merge_telehealth_source_turns(turns)
    assert result.mode == "source_blocks"
    assert all(turn.start_ms is None for turn in result.turns)


def test_source_blocks_join_word_segments_but_keep_source_part_boundaries():
    result = merge_telehealth_source_turns([
        TelehealthSourceTurn(P1, "patient", 0, 0, "Hello,"),
        TelehealthSourceTurn(P1, "patient", 0, 1, "how are you?"),
        TelehealthSourceTurn(P2, "patient", 1, 0, "I feel well."),
    ])
    assert result.mode == "source_blocks"
    assert len(result.turns) == 1
    assert result.turns[0].text == "Hello, how are you?\nI feel well."


def test_timed_merge_groups_only_adjacent_source_words_and_respects_limits():
    turns = [
        TelehealthSourceTurn(P1, "clinician", 0, 0, "First", 0, 100),
        TelehealthSourceTurn(P2, "patient", 0, 0, "Reply", 50, 80),
        TelehealthSourceTurn(P1, "clinician", 0, 1, "later", 100, 200),
        TelehealthSourceTurn(P3, "clinician", 1, 0, "after pause", 2_000, 2_500),
    ]
    result = merge_telehealth_source_turns(turns)
    assert [(turn.speaker_id, turn.text) for turn in result.turns] == [
        ("clinician", "First"), ("patient", "Reply"),
        ("clinician", "later"), ("clinician", "after pause"),
    ]

    size_limited = merge_telehealth_source_turns([
        TelehealthSourceTurn(P1, "clinician", 0, 0, "a" * 150_000, 0, 100),
        TelehealthSourceTurn(P1, "clinician", 0, 1, "b" * 150_000, 100, 200),
    ])
    assert [len(turn.text) for turn in size_limited.turns] == [150_000, 150_000]


def test_equal_starts_keep_source_word_order_and_overlapping_roles_separate():
    result = merge_telehealth_source_turns([
        TelehealthSourceTurn(P1, "clinician", 0, 1, "second", 100, 400),
        TelehealthSourceTurn(P1, "clinician", 0, 0, "first", 100, 300),
        TelehealthSourceTurn(P2, "patient", 0, 0, "overlap", 100, 200),
    ])
    assert [(turn.speaker_id, turn.text) for turn in result.turns] == [
        ("clinician", "first"), ("clinician", "second"), ("patient", "overlap"),
    ]
    assert (result.turns[0].start_ms, result.turns[1].end_ms) == (100, 400)


def test_identical_replayed_turn_deduplicates_but_conflict_is_rejected():
    turn = TelehealthSourceTurn(P1, "clinician", 1, 0, "Synthetic speech", 1000, 1200)
    result = merge_telehealth_source_turns([turn, turn])
    assert len(result.turns) == 1
    with pytest.raises(ValueError, match="Conflicting"):
        merge_telehealth_source_turns([turn, TelehealthSourceTurn(
            P1, "clinician", 1, 0, "Changed speech", 1000, 1200,
        )])
    with pytest.raises(ValueError, match="cannot change channel"):
        merge_telehealth_source_turns([turn, TelehealthSourceTurn(
            P1, "patient", 1, 0, "Wrong role", 1000, 1200,
        )])
    with pytest.raises(ValueError, match="source-part ordering"):
        merge_telehealth_source_turns([turn, TelehealthSourceTurn(
            P2, "clinician", 1, 0, "Another part", 2000, 2100,
        )])


@pytest.mark.parametrize("bad", [
    TelehealthSourceTurn(P1, "unknown", 0, 0, "Synthetic"),
    TelehealthSourceTurn(P1, "patient", -1, 0, "Synthetic"),
    TelehealthSourceTurn(P1, "patient", 0, 0, ""),
    TelehealthSourceTurn(P1, "patient", 0, 0, "Synthetic", -1, 200),
    TelehealthSourceTurn(P1, "patient", 0, 0, "Synthetic", 100, None),
    TelehealthSourceTurn(P1, "patient", 0, 0, "Synthetic", 500, 400),
])
def test_malformed_turns_fail_closed(bad):
    with pytest.raises(ValueError, match="Invalid"):
        merge_telehealth_source_turns([bad])


def test_empty_input_is_valid_and_unattributed_audio_is_not_invented():
    result = merge_telehealth_source_turns([])
    assert result.mode == "timed"
    assert result.turns == ()
    assert result.canonical_text == result.labelled_text == ""
