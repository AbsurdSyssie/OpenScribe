"""Telehealth accepts provider timing without accepting provider speaker identities."""
from uuid import UUID
import json

import pytest

from app.services.stt import (
    SttTranscriptResult, _source_timed_turns_from_segments,
    _transcribe_via_http, _turns_match_transcript,
)
from app.models import SttAdapterKind
from app.errors import AppError
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


def _single_span_part(*, channel="patient", duration_seconds=10, timeline_start_ms=0):
    rate = 16_000
    samples = duration_seconds * rate
    start_frame = timeline_start_ms * 48
    end_frame = start_frame + duration_seconds * 48_000
    return TelehealthAudioPart(
        session_id=UUID("11111111-1111-4111-8111-111111111111"),
        channel=channel, sequence_no=0,
        idempotency_key=UUID("22222222-2222-4222-8222-222222222222"),
        timeline_sample_rate=48_000, file_sample_rate=rate,
        file_samples=samples,
        spans=(TelehealthSourceSpan(0, samples, start_frame, end_frame),),
        timeline_start_frame=start_frame, timeline_end_frame=end_frame,
    )


@pytest.mark.parametrize("overrun_ms", [1_600, 2_000])
def test_small_provider_end_overrun_clips_to_actual_file_end(overrun_ms):
    part = _single_span_part()
    assert map_provider_turn_to_consultation(
        part, start_ms=9_500, end_ms=10_000 + overrun_ms,
    ) == (9_500, 10_000)


@pytest.mark.parametrize("start_ms,end_ms", [
    (9_500, 12_001),       # More than 2s beyond this 10s file.
    (10_000, 10_000),      # Starts exactly at EOF: no audio evidence.
    (10_001, 10_100),      # Entire segment is outside the file.
])
def test_provider_segments_outside_bounded_file_tail_remain_unmappable(start_ms, end_ms):
    assert map_provider_turn_to_consultation(
        _single_span_part(), start_ms=start_ms, end_ms=end_ms,
    ) is None


@pytest.mark.parametrize("start_ms,end_ms", [
    (-1, 0), (0, -1), (10, 9), (True, 1), (0, 1.0),
])
def test_provider_segment_timing_must_be_nonnegative_integer_and_forward(start_ms, end_ms):
    assert map_provider_turn_to_consultation(
        _single_span_part(), start_ms=start_ms, end_ms=end_ms,
    ) is None


def test_two_second_tolerance_is_relative_to_short_final_part_and_30s_maximum():
    short_part = _single_span_part(duration_seconds=3)
    assert map_provider_turn_to_consultation(
        short_part, start_ms=2_500, end_ms=5_000,
    ) == (2_500, 3_000)
    assert map_provider_turn_to_consultation(
        short_part, start_ms=2_500, end_ms=5_001,
    ) is None

    max_part = _single_span_part(duration_seconds=30)
    assert map_provider_turn_to_consultation(
        max_part, start_ms=29_500, end_ms=32_000,
    ) == (29_500, 30_000)


def test_tail_tolerance_does_not_bridge_internal_vad_gap_or_remove_role_overlap():
    gapped = TelehealthAudioPart(
        session_id=UUID("11111111-1111-4111-8111-111111111111"),
        channel="patient", sequence_no=0,
        idempotency_key=UUID("22222222-2222-4222-8222-222222222222"),
        timeline_sample_rate=48_000, file_sample_rate=16_000,
        file_samples=32_000,
        spans=(
            TelehealthSourceSpan(0, 16_000, 0, 48_000),
            TelehealthSourceSpan(16_000, 32_000, 480_000, 528_000),
        ),
        timeline_start_frame=0, timeline_end_frame=528_000,
    )
    assert map_provider_turn_to_consultation(gapped, start_ms=900, end_ms=1_100) is None

    clinician = _single_span_part(channel="clinician", duration_seconds=10)
    patient_position = map_provider_turn_to_consultation(
        _single_span_part(channel="patient", duration_seconds=10),
        start_ms=9_500, end_ms=11_600,
    )
    clinician_position = map_provider_turn_to_consultation(
        clinician, start_ms=9_800, end_ms=10_000,
    )
    result = merge_telehealth_source_turns([
        TelehealthSourceTurn(clinician.idempotency_key, "clinician", 0, 0,
                             "Synthetic clinician word", *clinician_position),
        TelehealthSourceTurn(UUID("33333333-3333-4333-8333-333333333333"),
                             "patient", 0, 0, "Synthetic patient word", *patient_position),
    ])
    assert result.mode == "timed"
    assert [turn.speaker_id for turn in result.turns] == ["patient", "clinician"]
    assert result.turns[0].end_ms == 10_000


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
    # More than two seconds beyond the uploaded source boundary forces the
    # conservative source-block mode while preserving the complete utterance.
    result = _mapped_synthetic_deepgram_words(
        [("Hello,", 0.00, 0.02), ("how", 0.02, 0.04),
         ("are", 0.04, 0.07), ("you?", 0.09, 2.11)],
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


@pytest.mark.parametrize("provider", ["generic", "deepgram", "elevenlabs"])
def test_telehealth_keeps_canonical_text_when_timing_segments_drop_negation(monkeypatch, provider):
    canonical = "Patient denies chest pain"
    if provider == "deepgram":
        payload = _synthetic_deepgram_word_payload(
            [("Patient", 0.0, 0.2), ("chest", 0.3, 0.5), ("pain", 0.6, 0.8)],
            canonical,
        )
    elif provider == "elevenlabs":
        payload = {
            "text": canonical,
            "words": [
                {"text": "Patient", "start": 0.0, "end": 0.2},
                {"text": "chest", "start": 0.3, "end": 0.5},
                {"text": "pain", "start": 0.6, "end": 0.8},
            ],
        }
    else:
        payload = {
            "text": canonical,
            "segments": [
                {"text": "Patient", "start": 0.0, "end": 0.2},
                {"text": "chest", "start": 0.3, "end": 0.5},
                {"text": "pain", "start": 0.6, "end": 0.8},
            ],
        }

    class SyntheticResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def json(self):
            return payload

        def iter_bytes(self):
            return iter([json.dumps(payload).encode()])

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.services.stt.httpx.stream", lambda *_a, **_kw: SyntheticResponse())
    kwargs = dict(
        base_url="https://stt.example.test", transcribe_path="/transcribe",
        file_field_name="file", response_text_path="text",
        extra_form_fields_json={}, bearer_token="synthetic-token",
        model_name=None, model_field_name="model", language=None,
        language_field_name="language", audio_bytes=b"synthetic audio",
        filename="synthetic.wav", content_type="audio/wav",
        telehealth_timing=True,
    )
    if provider == "deepgram":
        kwargs.update(
            response_text_path="results.channels.0.alternatives.0.transcript",
            provider_preset="deepgram", adapter_kind=SttAdapterKind.generic_rest,
            segments_path="segments", segment_text_field="text",
            segment_start_field="start", segment_end_field="end",
        )
    elif provider == "elevenlabs":
        kwargs.update(adapter_kind=SttAdapterKind.elevenlabs_speech_to_text)
    else:
        kwargs.update(
            segments_path="segments", segment_text_field="text",
            segment_start_field="start", segment_end_field="end",
        )
    result = _transcribe_via_http(**kwargs)
    assert isinstance(result, SttTranscriptResult)
    assert result.text == canonical
    assert result.turns is None


@pytest.mark.parametrize("payload", [{"text": ""}, {"segments": [{"text": "partial"}]}, {"text": 7}])
def test_generic_telehealth_rejects_missing_or_invalid_canonical_text(monkeypatch, payload):
    class SyntheticResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def json(self):
            return payload

        def iter_bytes(self):
            return iter([json.dumps(payload).encode()])

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.services.stt.httpx.stream", lambda *_a, **_kw: SyntheticResponse())
    with pytest.raises(AppError) as exc_info:
        _transcribe_via_http(
            base_url="https://stt.example.test", transcribe_path="/transcribe",
            file_field_name="file", response_text_path="text",
            extra_form_fields_json={}, bearer_token="synthetic-token",
            model_name=None, model_field_name="model", language=None,
            language_field_name="language", audio_bytes=b"synthetic audio",
            filename="synthetic.wav", content_type="audio/wav", telehealth_timing=True,
        )
    assert getattr(exc_info.value, "code", None) == "stt_response_invalid"
