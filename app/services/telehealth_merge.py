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


@dataclass(frozen=True, slots=True)
class TelehealthSourceTurn:
    """One normalized provider segment, linked to a durable input part."""

    part_id: UUID
    channel: Channel
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
    total = 0
    for turn in source_turns:
        if (
            not isinstance(turn, TelehealthSourceTurn)
            or not isinstance(turn.part_id, UUID)
            or turn.channel not in CHANNEL_LABELS
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
                turn.end_ms,
                0 if turn.channel == "clinician" else 1,
                str(turn.part_id),
                turn.segment_index,
            ),
        )
        turns = tuple(
            TelehealthDisplayTurn(
                speaker_id=turn.channel,
                speaker_label=CHANNEL_LABELS[turn.channel],
                text=turn.text.strip(),
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
            )
            for turn in sorted_items
        )
        return TelehealthMergeResult(
            mode="timed",
            turns=turns,
            canonical_text="\n".join(turn.text for turn in turns),
        )

    # Server-owned per-part ordering is needed for this block presentation.
    # The caller passes original source turns in that order, not ASR finish order.
    blocks: list[TelehealthDisplayTurn] = []
    for channel in ("clinician", "patient"):
        text = "\n".join(
            turn.text.strip() for turn in items if turn.channel == channel
        )
        if text:
            blocks.append(
                TelehealthDisplayTurn(
                    speaker_id=channel,
                    speaker_label=CHANNEL_LABELS[channel],
                    text=text,
                    start_ms=None,
                    end_ms=None,
                )
            )
    return TelehealthMergeResult(
        mode="source_blocks",
        turns=tuple(blocks),
        canonical_text="\n\n".join(
            f"{turn.speaker_label}:\n{turn.text}" for turn in blocks
        ),
    )
