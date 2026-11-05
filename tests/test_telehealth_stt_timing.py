"""Telehealth accepts provider timing without accepting provider speaker identities."""
from uuid import UUID

import pytest

from app.services.stt import _source_timed_turns_from_segments, _turns_match_transcript
from app.services.telehealth_audio import (
    TelehealthAudioPart,
    TelehealthSourceSpan,
    map_provider_turn_to_consultation,
)


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
