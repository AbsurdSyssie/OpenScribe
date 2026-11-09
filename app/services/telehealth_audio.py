"""Strict metadata validation for captured telehealth audio parts.

No audio content or provider output is persisted by this module. Its validated
metadata is intended for a *server-owned*, owner-scoped telehealth capture
session. The caller MUST independently:
- authorize the transcript/session and enforce the chosen STT snapshot;
- inspect actual uploaded audio duration/bytes server-side;
- persist source bytes only through the established Vault+cleanup lifecycle;
- enforce monotonically advancing per-source sequence under database locks;
- count actual submitted/provider-metered seconds for quota and billing.

Both channels share original, pre-VAD consultation frames. The source file can
contain VAD-trimmed speech from disjoint intervals; its sample-to-time map
must be explicit and may never silently compress consultation-time silence.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Literal
from uuid import UUID


Channel = Literal["clinician", "patient"]
CHANNELS = frozenset(("clinician", "patient"))

MAX_SOURCE_PART_SECONDS = 30
MAX_PROVIDER_SEGMENT_END_OVERRUN_MS = 2_000
MAX_SPANS_PER_PART = 128
MAX_SESSION_SECONDS = 8 * 60 * 60
MAX_SOURCE_SEQUENCE = 100_000
MAX_SAMPLE_RATE = 192_000
MAX_AUDIO_TIMING_DIFFERENCE_MS = 25


@dataclass(frozen=True, slots=True)
class TelehealthSourceSpan:
    """One contiguous source interval within a submitted/trimmed audio part."""

    file_start_sample: int
    file_end_sample: int
    timeline_start_frame: int
    timeline_end_frame: int


@dataclass(frozen=True, slots=True)
class TelehealthAudioPart:
    """Validated metadata, never a source of authority about billed duration."""

    session_id: UUID
    channel: Channel
    sequence_no: int
    idempotency_key: UUID
    timeline_sample_rate: int
    file_sample_rate: int
    spans: tuple[TelehealthSourceSpan, ...]
    timeline_start_frame: int
    timeline_end_frame: int
    file_samples: int

    @property
    def submitted_audio_seconds(self) -> float:
        return self.file_samples / self.file_sample_rate

    @property
    def first_original_time_ms(self) -> int:
        return round(self.timeline_start_frame * 1000 / self.timeline_sample_rate)

    @property
    def last_original_time_ms(self) -> int:
        return round(self.timeline_end_frame * 1000 / self.timeline_sample_rate)


def _safe_nonnegative_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**53 - 1


def _positive_sample_rate(value: object) -> bool:
    return _safe_nonnegative_int(value) and 8_000 <= value <= MAX_SAMPLE_RATE


def validate_telehealth_audio_part(
    *,
    session_id: UUID,
    channel: str,
    sequence_no: int,
    idempotency_key: UUID,
    timeline_sample_rate: int,
    file_sample_rate: int,
    file_samples: int,
    spans: tuple[TelehealthSourceSpan, ...] | list[TelehealthSourceSpan],
    measured_audio_seconds: float,
) -> TelehealthAudioPart:
    """Validate sample-to-original-time map and server-measured upload duration.

    file_samples MUST be the frame count from a server-inspected decoded or
    normalized media file, not a trusted client declaration. The caller checks
    that the parsed media format and sample rate match file_sample_rate and
    enforces size/duration limits before storing bytes or reserving provider
    quota.

    All file spans must be strictly ordered, non-overlapping, and cover every
    submitted sample with no holes. Original consultation time may have gaps
    between spans (removed silence), but its frame clock cannot reverse.
    """
    if type(session_id) is not UUID or type(idempotency_key) is not UUID:
        raise ValueError("Telehealth capture and request identifiers must be UUIDs.")
    if channel not in CHANNELS:
        raise ValueError("Unknown telehealth capture channel.")
    if not _safe_nonnegative_int(sequence_no) or sequence_no > MAX_SOURCE_SEQUENCE:
        raise ValueError("Invalid channel sequence number.")
    if not _positive_sample_rate(timeline_sample_rate) or not _positive_sample_rate(file_sample_rate):
        raise ValueError("Invalid telehealth audio sample rate.")
    if (
        not _safe_nonnegative_int(file_samples)
        or file_samples == 0
        or file_samples > MAX_SOURCE_PART_SECONDS * file_sample_rate
        or type(measured_audio_seconds) not in (int, float)
        or not isfinite(measured_audio_seconds)
        or measured_audio_seconds <= 0
    ):
        raise ValueError("Invalid measured telehealth audio duration.")
    duration_ms = file_samples * 1000 / file_sample_rate
    if abs(duration_ms - measured_audio_seconds * 1000) > MAX_AUDIO_TIMING_DIFFERENCE_MS:
        raise ValueError("Declared audio duration differs from inspected media.")
    if not isinstance(spans, (tuple, list)) or not 1 <= len(spans) <= MAX_SPANS_PER_PART:
        raise ValueError("Telehealth audio requires bounded source timing spans.")

    previous_file_end = 0
    previous_timeline_end = 0
    timeline_start = None
    for index, span in enumerate(spans):
        if not isinstance(span, TelehealthSourceSpan):
            raise ValueError("Invalid telehealth source timing span.")
        start, end = span.file_start_sample, span.file_end_sample
        t_start, t_end = span.timeline_start_frame, span.timeline_end_frame
        if (
            not all(_safe_nonnegative_int(value) for value in (start, end, t_start, t_end))
            or start != previous_file_end
            or end <= start
            or end > file_samples
            or t_end <= t_start
            or (index > 0 and t_start < previous_timeline_end)
        ):
            raise ValueError("Source samples must map monotonically to the common clock.")
        file_ms = (end - start) * 1000 / file_sample_rate
        source_ms = (t_end - t_start) * 1000 / timeline_sample_rate
        if abs(file_ms - source_ms) > MAX_AUDIO_TIMING_DIFFERENCE_MS:
            raise ValueError("Source audio timing span does not match its PCM duration.")
        if timeline_start is None:
            timeline_start = t_start
        previous_file_end = end
        previous_timeline_end = t_end

    if previous_file_end != file_samples:
        raise ValueError("Source timing map leaves audio samples unattributed.")
    if previous_timeline_end > MAX_SESSION_SECONDS * timeline_sample_rate:
        raise ValueError("Telehealth consultation time exceeds the allowed bound.")

    return TelehealthAudioPart(
        session_id=session_id,
        channel=channel,
        sequence_no=sequence_no,
        idempotency_key=idempotency_key,
        timeline_sample_rate=timeline_sample_rate,
        file_sample_rate=file_sample_rate,
        file_samples=file_samples,
        spans=tuple(spans),
        timeline_start_frame=timeline_start,  # validated non-None
        timeline_end_frame=previous_timeline_end,
    )


def validate_next_telehealth_part(
    previous: TelehealthAudioPart | None,
    incoming: TelehealthAudioPart,
    *,
    expected_sequence_no: int,
) -> bool:
    """Fail closed on replay/conflict/rewind; return False for exact replay.

    The DB caller must lock the capture session and look up the existing
    source sequence/idempotency key to implement reliable retries. A stale
    part cannot be appended under a new idempotency key.
    """
    if not _safe_nonnegative_int(expected_sequence_no):
        raise ValueError("Invalid next expected telehealth part number.")
    if previous is not None:
        if previous.session_id != incoming.session_id or previous.channel != incoming.channel:
            raise ValueError("Telehealth source cannot cross a session or channel.")
        if incoming.sequence_no == previous.sequence_no:
            if incoming == previous:
                return False  # idempotent retry, don't increment quota or store again
            raise ValueError("Conflicting telehealth audio-part replay.")
        if incoming.timeline_start_frame < previous.timeline_end_frame:
            raise ValueError("Telehealth source clock cannot rewind.")
        if incoming.timeline_sample_rate != previous.timeline_sample_rate:
            raise ValueError("Telehealth source sample rate changed during capture.")
    if incoming.sequence_no != expected_sequence_no:
        raise ValueError("Telehealth source part was submitted out of order.")
    return True



def map_provider_turn_to_consultation(
    part: TelehealthAudioPart,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[int, int] | None:
    """Map ASR time relative to submitted audio back to ORIGINAL clock time.

    Starts must lie inside the inspected file. An end up to two seconds past
    its actual duration is clipped to EOF for minor provider timing overrun.
    If a segment straddles disjoint VAD spans, it cannot be located precisely;
    return None. Never invent elapsed silence.
    """
    if (
        type(start_ms) is not int
        or type(end_ms) is not int
        or start_ms < 0
        or end_ms < start_ms
    ):
        return None
    # Provider timestamps can run slightly beyond a short final audio part.
    # Bound this against the inspected file sample count (not the 30s maximum)
    # and clip only its end. Compare in integer sample-rate units so rounding
    # cannot admit a segment more than the inclusive 2s allowance.
    file_duration_ms_numerator = part.file_samples * 1000
    start_ms_numerator = start_ms * part.file_sample_rate
    end_ms_numerator = end_ms * part.file_sample_rate
    overrun_ms_numerator = (
        end_ms_numerator - file_duration_ms_numerator
    )
    if (
        start_ms_numerator >= file_duration_ms_numerator
        or overrun_ms_numerator > MAX_PROVIDER_SEGMENT_END_OVERRUN_MS * part.file_sample_rate
    ):
        return None
    file_start = round(start_ms * part.file_sample_rate / 1000)
    file_end = min(
        part.file_samples,
        round(end_ms * part.file_sample_rate / 1000),
    )
    for span in part.spans:
        if (
            span.file_start_sample <= file_start <= file_end <= span.file_end_sample
        ):
            frame_per_file_sample = (
                (span.timeline_end_frame - span.timeline_start_frame)
                / (span.file_end_sample - span.file_start_sample)
            )
            original_start = (
                span.timeline_start_frame
                + (file_start - span.file_start_sample) * frame_per_file_sample
            )
            original_end = (
                span.timeline_start_frame
                + (file_end - span.file_start_sample) * frame_per_file_sample
            )
            return (
                round(original_start * 1000 / part.timeline_sample_rate),
                round(original_end * 1000 / part.timeline_sample_rate),
            )
    return None
