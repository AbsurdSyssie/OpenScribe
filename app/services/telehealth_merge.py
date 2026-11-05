"""Pure, source-attributed telehealth transcript ordering.

This module has no database, HTTP or provider side effects. Callers must derive
channel assignments from server-owned capture-session metadata, never from
client-supplied clinical identity claims. Timings are consultation-relative,
validated upstream against the original pre-VAD capture timeline.

Untimed provider results are deliberately NOT interleaved by guessed order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


Channel = Literal["clinician", "patient"]
MAX_TURNS = 100_000
MAX_TEXT_CHARS = 8_000_000
MAX_TURN_CHARS = 200_000
CHANNEL_LABELS = {"clinician": "Clinician", "patient": "Patient"}
MAX_WORD_GAP_MS = 1_500


@dataclass(frozen=True, slots=True)
class TelehealthSourceTurn:
    """One normalized provider segment, linked to a durable input part."""

    part_id: UUID
    channel: Channel
    source_sequence: int
    segment_index: int
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TelehealthDisplayTurn:
    """A role-labelled display turn; scope/IDs are assigned by the DB service."""

    speaker_id: Channel
    speaker_label: str
    text: str
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True, slots=True)
class TelehealthMergeResult:
    mode: Literal["timed", "source_blocks"]
    turns: tuple[TelehealthDisplayTurn, ...]
    # Source text only. Persist through OpenScribe's owner-encrypted draft path.
    canonical_text: str
    # Presentation-only role projection; generation must run established redaction
    # against canonical source before adding these labels, never send this raw.
    labelled_text: str


def _valid_timestamp(value: int | None) -> bool:
    return value is None or (type(value) is int and 0 <= value <= 2**53 - 1)


def merge_telehealth_source_turns(
    source_turns: list[TelehealthSourceTurn] | tuple[TelehealthSourceTurn, ...],
) -> TelehealthMergeResult:
    """Order timed turns; otherwise preserve two labelled, source-only blocks.

    The exact (part_id, channel, segment_index) identity is deduplicated for
    durable retry replay. A conflicting reuse is rejected, never overwritten.
    This helper does not resolve interrupted capture or permit note generation;
    those decisions belong to the owner's ingestion/session service.
    """
    if len(source_turns) > MAX_TURNS:
        raise ValueError("Telehealth transcript exceeds supported turn count.")
    unique: dict[tuple[UUID, Channel, int], TelehealthSourceTurn] = {}
    part_channel: dict[UUID, Channel] = {}
    source_sequence_parts: dict[tuple[Channel, int], UUID] = {}
    total = 0
    for turn in source_turns:
        if (
            not isinstance(turn, TelehealthSourceTurn)
            or not isinstance(turn.part_id, UUID)
            or turn.channel not in CHANNEL_LABELS
            or type(turn.source_sequence) is not int
            or turn.source_sequence < 0
            or type(turn.segment_index) is not int
            or turn.segment_index < 0
            or type(turn.text) is not str
            or not turn.text.strip()
            or len(turn.text) > MAX_TURN_CHARS
            or not _valid_timestamp(turn.start_ms)
            or not _valid_timestamp(turn.end_ms)
            or (turn.start_ms is None) != (turn.end_ms is None)
            or (
                turn.start_ms is not None
                and turn.end_ms is not None
                and turn.end_ms < turn.start_ms
            )
        ):
            raise ValueError("Invalid telehealth source turn.")
        prior_channel = part_channel.setdefault(turn.part_id, turn.channel)
        if prior_channel != turn.channel:
            raise ValueError("A telehealth source part cannot change channel.")
        sequence_key = (turn.channel, turn.source_sequence)
        prior_part = source_sequence_parts.setdefault(sequence_key, turn.part_id)
        if prior_part != turn.part_id:
            raise ValueError("Conflicting telehealth source-part ordering.")
        key = (turn.part_id, turn.channel, turn.segment_index)
        old = unique.get(key)
        if old is not None:
            if old != turn:
                raise ValueError("Conflicting telehealth source turn replay.")
            continue
        unique[key] = turn
        total += len(turn.text)
        if total > MAX_TEXT_CHARS:
            raise ValueError("Telehealth transcript exceeds text limit.")

    items = tuple(unique.values())
    # A source can produce only flat text; ordering it amongst precisely timed
    # turns would invent chronology. Fall back for the entire combined draft.
    timed = all(turn.start_ms is not None for turn in items)
    if timed:
        sorted_items = sorted(
            items,
            key=lambda turn: (
                turn.start_ms,
                0 if turn.channel == "clinician" else 1,
                turn.source_sequence,
                turn.segment_index,
                str(turn.part_id),
            ),
        )
        grouped: list[TelehealthDisplayTurn] = []
        for turn in sorted_items:
            previous = grouped[-1] if grouped else None
            if (
                previous is not None
                and previous.speaker_id == turn.channel
                and previous.end_ms is not None
                and turn.start_ms is not None
                and turn.start_ms >= previous.end_ms
                and turn.start_ms - previous.end_ms <= MAX_WORD_GAP_MS
                and len(previous.text) + 1 + len(turn.text.strip()) <= MAX_TURN_CHARS
            ):
                grouped[-1] = TelehealthDisplayTurn(
                    speaker_id=turn.channel,
                    speaker_label=CHANNEL_LABELS[turn.channel],
                    text=f"{previous.text} {turn.text.strip()}",
                    start_ms=previous.start_ms,
                    end_ms=max(previous.end_ms, turn.end_ms),
                )
            else:
                grouped.append(TelehealthDisplayTurn(
                    speaker_id=turn.channel,
                    speaker_label=CHANNEL_LABELS[turn.channel],
                    text=turn.text.strip(),
                    start_ms=turn.start_ms,
                    end_ms=turn.end_ms,
                ))
        turns = tuple(grouped)
        return TelehealthMergeResult(
            mode="timed",
            turns=turns,
            canonical_text="\n".join(turn.text for turn in turns),
            labelled_text="\n".join(
                f"{turn.speaker_label}: {turn.text}" for turn in turns
            ),
        )

    # Flat text has no evidenced cross-channel chronology. Sort by the durable
    # server-owned part sequence rather than provider job completion order.
    blocks: list[TelehealthDisplayTurn] = []
    for channel in ("clinician", "patient"):
        ordered = sorted(
            (turn for turn in items if turn.channel == channel),
            key=lambda turn: (turn.source_sequence, turn.segment_index),
        )
        chunk: list[str] = []
        chunk_length = 0

        def append_block() -> None:
            nonlocal chunk_length
            if chunk:
                blocks.append(
                    TelehealthDisplayTurn(
                        speaker_id=channel,
                        speaker_label=CHANNEL_LABELS[channel],
                        text="\n".join(chunk),
                        start_ms=None,
                        end_ms=None,
                    )
                )
                chunk.clear()
                chunk_length = 0

        by_sequence: dict[int, list[TelehealthSourceTurn]] = {}
        for turn in ordered:
            by_sequence.setdefault(turn.source_sequence, []).append(turn)
        for sequence in sorted(by_sequence):
            part_words = [turn.text.strip() for turn in sorted(
                by_sequence[sequence], key=lambda item: item.segment_index,
            )]
            part_chunks: list[str] = []
            part_words_chunk: list[str] = []
            part_chunk_length = 0
            for word in part_words:
                extra = len(word) + (1 if part_chunk_length else 0)
                if part_words_chunk and part_chunk_length + extra > MAX_TURN_CHARS:
                    part_chunks.append(" ".join(part_words_chunk))
                    part_words_chunk = []
                    part_chunk_length = 0
                    extra = len(word)
                part_words_chunk.append(word)
                part_chunk_length += extra
            if part_words_chunk:
                part_chunks.append(" ".join(part_words_chunk))
            for value in part_chunks:
                if chunk and chunk_length + len(value) + 1 > MAX_TURN_CHARS:
                    append_block()
                chunk.append(value)
                chunk_length += len(value) + (1 if chunk_length else 0)
        append_block()

    result_turns = tuple(blocks)
    return TelehealthMergeResult(
        mode="source_blocks",
        turns=result_turns,
        canonical_text="\n".join(turn.text for turn in result_turns),
        labelled_text="\n\n".join(
            f"{turn.speaker_label}:\n{turn.text}" for turn in result_turns
        ),
    )
