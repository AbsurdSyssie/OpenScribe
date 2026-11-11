"""Synthetic, database-free telehealth source-mapping contract tests."""
from uuid import UUID

import pytest

from app.services.telehealth_audio import (
    TelehealthSourceSpan,
    validate_next_telehealth_part,
    validate_telehealth_audio_part,
)


SESSION = UUID("11111111-1111-4111-8111-111111111111")
REQUEST = UUID("22222222-2222-4222-8222-222222222222")


def make_part(*, spans=None, **kwargs):
    inputs = {
        "session_id": SESSION,
        "channel": "clinician",
        "sequence_no": 0,
        "idempotency_key": REQUEST,
        "timeline_sample_rate": 48000,
        "file_sample_rate": 16000,
        "file_samples": 16000,
        "spans": (
            TelehealthSourceSpan(0, 8000, 48000, 72000),
            TelehealthSourceSpan(8000, 16000, 480000, 504000),
        ),
        "measured_audio_seconds": 1.0,
    }
    if spans is not None:
        inputs["spans"] = spans
    inputs.update(kwargs)
    return validate_telehealth_audio_part(**inputs)


def test_vad_omits_silence_but_keeps_original_consultation_clock():
    part = make_part()
    assert part.submitted_audio_seconds == 1.0
    assert part.first_original_time_ms == 1000
    assert part.last_original_time_ms == 10500
    assert part.timeline_start_frame == 48000
    assert part.timeline_end_frame == 504000
    assert part.spans[0].file_end_sample == part.spans[1].file_start_sample
    assert part.spans[0].timeline_end_frame < part.spans[1].timeline_start_frame


def test_original_clock_can_be_shared_by_both_separately_validated_sources():
    clinician = make_part()
    patient = make_part(
        channel="patient",
        spans=(TelehealthSourceSpan(0, 16000, 240000, 288000),),
    )
    assert clinician.session_id == patient.session_id
    assert clinician.file_samples == patient.file_samples
    assert clinician.timeline_start_frame != patient.timeline_start_frame


@pytest.mark.parametrize(
    "spans",
    [
        (),  # VAD must not upload zero-speech source as an unexplained part.
        (TelehealthSourceSpan(0, 4000, 0, 12000),),  # Missing file tail.
        (TelehealthSourceSpan(10, 16000, 0, 47970),),  # Missing file head.
        (
            TelehealthSourceSpan(0, 8000, 0, 24000),
            TelehealthSourceSpan(7999, 16000, 48000, 72003),
        ),  # Overlap / wrong source duration.
        (
            TelehealthSourceSpan(0, 8000, 48000, 72000),
            TelehealthSourceSpan(8000, 16000, 71999, 95999),
        ),  # Original timeline rewinds.
        (TelehealthSourceSpan(0, 16000, 0, 96000),),  # Time-scale mismatch.
        (TelehealthSourceSpan(0, 16000, -1, 47999),),  # Invalid frame index.
    ],
)
def test_rejects_bad_or_ambiguous_original_audio_timing_maps(spans):
    with pytest.raises(ValueError):
        make_part(spans=spans)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"channel": "remote"},
        {"sequence_no": -1},
        {"sequence_no": True},
        {"timeline_sample_rate": 0},
        {"timeline_sample_rate": 192001},
        {"file_sample_rate": 7999},
        {"file_samples": 16001, "measured_audio_seconds": 0.5},
        {"measured_audio_seconds": float("nan")},
        {"measured_audio_seconds": float("inf")},
        {"measured_audio_seconds": 0},
        {"session_id": "not-a-UUID"},
        {"idempotency_key": "not-a-UUID"},
        {"file_samples": 16000 * 31},
    ],
)
def test_rejects_invalid_channel_duration_rate_and_untrusted_client_metadata(kwargs):
    with pytest.raises(ValueError):
        make_part(**kwargs)


def test_exact_replay_is_deduplicated_and_conflicts_are_rejected():
    part = make_part()
    assert validate_next_telehealth_part(None, part, expected_sequence_no=0)
    assert not validate_next_telehealth_part(part, part, expected_sequence_no=1)
    with pytest.raises(ValueError, match="Conflicting"):
        validate_next_telehealth_part(
            part,
            make_part(
                spans=(TelehealthSourceSpan(0, 16000, 96000, 144000),),
            ),
            expected_sequence_no=1,
        )


def test_next_part_must_keep_sequence_and_original_clock():
    first = make_part()
    following = make_part(
        sequence_no=1,
        idempotency_key=UUID("33333333-3333-4333-8333-333333333333"),
        spans=(TelehealthSourceSpan(0, 16000, 600000, 648000),),
    )
    assert validate_next_telehealth_part(first, following, expected_sequence_no=1)
    with pytest.raises(ValueError, match="out of order"):
        validate_next_telehealth_part(first, following, expected_sequence_no=2)
    rewind = make_part(
        sequence_no=1,
        idempotency_key=following.idempotency_key,
        spans=(TelehealthSourceSpan(0, 16000, 480000, 528000),),
    )
    # Original first ends at 504000: starting at 480000 is a rewind.
    with pytest.raises(ValueError, match="rewind"):
        validate_next_telehealth_part(first, rewind, expected_sequence_no=1)
