"""Telehealth accepts provider timing without accepting provider speaker identities."""
from uuid import UUID

import pytest

from app.services.stt import _source_timed_turns_from_segments, _turns_match_transcript
from app.services.telehealth_audio import (
    TelehealthAudioPart,
    TelehealthSourceSpan,
    map_provider_turn_to_consultation,
)
from app.services.telehealth_merge import TelehealthSourceTurn, merge_telehealth_source_turns


def test_source_timing_extracted_without_provider_diarisation():
    provider = {
        "results": {"channels": [{
            "alternatives": [{
                "transcript": "Hello, how are you?",
                "words": [
                    {"punctuated_word": "Hello,", "start": 0.5, "end": 0.8, "speaker": 999},
                    {"punctuated_word": "how", "start": 0.9, "end": 1.0, "speaker": 1000},
                    {"punctuated_word": "are", "start": 1.1, "end": 1.2},
                    {"punctuated_word": "you?", "start": 1.3, "end": 1.5},
                ]
            }]
        }]}
    }
    turns = _source_timed_turns_from_segments(
        provider,
        segments_path="results.channels.0.alternatives.0.words",
        segment_text_field="punctuated_word",
        segment_start_field="start",
        segment_end_field="end",
    )
    assert turns is not None
    assert [turn.text for turn in turns] == ["Hello,", "how", "are", "you?"]
    assert all(turn.speaker_id is None for turn in turns)
    assert turns[0].start_ms == 500 and turns[-1].end_ms == 1500
    assert _turns_match_transcript(turns, "Hello, how are you?")


@pytest.mark.parametrize("segments", [
    [{"text": "synthetic", "start": 4, "end": 3}],
    [{"text": "synthetic", "start": True, "end": 5}],
    [{"text": "synthetic", "start": None, "end": 5}],
    [{"text": "", "start": 0, "end": 1}],
    [{}],
])
def test_unsafe_provider_segments_fall_back_to_complete_source_text(segments):
    assert _source_timed_turns_from_segments(
        {"segments": segments},
        segments_path="segments", segment_text_field="text",
        segment_start_field="start", segment_end_field="end",
    ) is None


def test_provider_relative_offset_maps_through_separate_vad_gaps():
    part = TelehealthAudioPart(
        session_id=UUID("11111111-1111-4111-8111-111111111111"),
        channel="patient", sequence_no=0,
        idempotency_key=UUID("22222222-2222-4222-8222-222222222222"),
        timeline_sample_rate=48000, file_sample_rate=16000,
        file_samples=32000,
        spans=(
            TelehealthSourceSpan(0, 16000, 96000, 144000),
            TelehealthSourceSpan(16000, 32000, 480000, 528000),
        ),
        timeline_start_frame=96000, timeline_end_frame=528000,
    )
    assert map_provider_turn_to_consultation(part, start_ms=250, end_ms=750) == (2250, 2750)
    assert map_provider_turn_to_consultation(part, start_ms=1200, end_ms=1400) == (10200, 10400)
    # A single word/turn crossing a VAD removed interval is not precisely
    # placeable; merge falls back to two source-labelled content blocks.
    assert map_provider_turn_to_consultation(part, start_ms=900, end_ms=1100) is None
    assert map_provider_turn_to_consultation(part, start_ms=3000, end_ms=3050) is None


def _synthetic_deepgram_word_payload(words, transcript):
    return {"results": {"channels": [{"alternatives": [{
        "transcript": transcript,
        "words": [
            {"punctuated_word": word, "start": start, "end": end}
            for word, start, end in words
        ],
    }]}]}}


def _synthetic_part(*, file_samples=1600):
    return TelehealthAudioPart(
        session_id=UUID("11111111-1111-4111-8111-111111111111"),
        channel="clinician", sequence_no=0,
        idempotency_key=UUID("22222222-2222-4222-8222-222222222222"),
        timeline_sample_rate=16000, file_sample_rate=16000,
        file_samples=file_samples,
        spans=(TelehealthSourceSpan(0, file_samples, 0, file_samples),),
        timeline_start_frame=0, timeline_end_frame=file_samples,
    )


def _mapped_synthetic_deepgram_words(words, transcript, *, part):
    provider = _synthetic_deepgram_word_payload(words, transcript)
    extracted = _source_timed_turns_from_segments(
        provider,
        segments_path="results.channels.0.alternatives.0.words",
        segment_text_field="punctuated_word",
        segment_start_field="start",
        segment_end_field="end",
    )
    assert _turns_match_transcript(extracted, transcript)
    source_turns = []
    for index, word in enumerate(extracted or ()):
        position = map_provider_turn_to_consultation(
            part, start_ms=word.start_ms, end_ms=word.end_ms,
        )
        source_turns.append(TelehealthSourceTurn(
            part_id=part.idempotency_key,
            channel=part.channel,
            source_sequence=part.sequence_no,
            segment_index=index,
            text=word.text,
            start_ms=position[0] if position else None,
            end_ms=position[1] if position else None,
        ))
    return merge_telehealth_source_turns(source_turns)


def test_unmappable_word_falls_back_without_newline_per_word():
    # One word ending just outside the uploaded source boundary forces the
    # conservative source-block mode while preserving the complete utterance.
    result = _mapped_synthetic_deepgram_words(
        [("Hello,", 0.00, 0.02), ("how", 0.02, 0.04),
         ("are", 0.04, 0.07), ("you?", 0.09, 0.11)],
        "Hello, how are you?", part=_synthetic_part(),
    )
    assert result.mode == "source_blocks"
    assert len(result.turns) == 1
    assert result.turns[0].text == "Hello, how are you?"
    assert result.canonical_text == "Hello, how are you?"


def test_valid_timed_deepgram_words_group_into_one_source_turn():
    result = _mapped_synthetic_deepgram_words(
        [("Hello,", 0.00, 0.02), ("how", 0.02, 0.04),
         ("are", 0.04, 0.07), ("you?", 0.07, 0.10)],
        "Hello, how are you?", part=_synthetic_part(),
    )
    assert result.mode == "timed"
    assert len(result.turns) == 1
    assert (result.turns[0].speaker_id, result.turns[0].text) == (
        "clinician", "Hello, how are you?",
    )
    assert (result.turns[0].start_ms, result.turns[0].end_ms) == (0, 100)


def test_reversed_deepgram_word_timings_are_discarded_not_reordered():
    provider = _synthetic_deepgram_word_payload(
        [("first", 1.0, 1.2), ("second", 0.5, 0.7)],
        "first second",
    )
    extracted = _source_timed_turns_from_segments(
        provider,
        segments_path="results.channels.0.alternatives.0.words",
        segment_text_field="punctuated_word",
        segment_start_field="start",
        segment_end_field="end",
    )
    assert extracted is None
