"""Owner-encrypted structured transcript turns and safe source projection."""
from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    TelehealthCapturePart, TelehealthCaptureSession, Transcript,
    TranscriptIngestionJob, TranscriptIngestionJobStatus, TranscriptStatus,
    TranscriptVersion, User,
)
from app.schemas.transcripts import MAX_TRANSCRIPT_TURN_CHARS
from app.services.content_crypto import (
    decrypt_json_for_owner,
    decrypt_text_for_owner,
    encrypt_json_for_owner,
    encrypt_text_for_owner,
)
from app.services.consultation_split_locks import lock_consultation_split_source_scope

STRUCTURED_TURNS_SCHEMA_VERSION = 1


def canonical_transcript_projection(turns: list[dict[str, Any]]) -> str:
    """Join turn text with a single LF; labels and editor metadata never enter it."""
    return "\n".join(str(turn["text"]) for turn in turns)


def _normalize_turn(turn: dict[str, Any], *, scope_id: UUID | None = None) -> dict[str, Any]:
    text = turn.get("text")
    if not isinstance(text, str):
        raise AppError(422, "validation_error", "Transcript turn text must be text")
    if len(text) > MAX_TRANSCRIPT_TURN_CHARS:
        raise AppError(422, "validation_error", "Transcript turn is too long")
    actual_scope = turn.get("scope_id", scope_id)
    try:
        actual_scope = UUID(str(actual_scope))
    except (TypeError, ValueError, AttributeError):
        raise AppError(422, "validation_error", "Transcript turn scope is invalid")
    speaker_id = turn.get("speaker_id")
    if speaker_id is not None:
        speaker_id = str(speaker_id)
        if len(speaker_id) > 64:
            raise AppError(422, "validation_error", "Transcript speaker identifier is invalid")
    speaker_role = turn.get("speaker_role")
    if speaker_role is not None and (not isinstance(speaker_role, str) or speaker_role not in {"clinician", "patient"}):
        raise AppError(422, "validation_error", "Transcript speaker role is invalid")
    if speaker_role is not None and speaker_id != speaker_role:
        raise AppError(422, "validation_error", "Transcript speaker role is inconsistent")
    speaker_text_prefix = turn.get("speaker_text_prefix", "")
    if not isinstance(speaker_text_prefix, str) or speaker_text_prefix not in {"", "Clinician: ", "Patient: "}:
        raise AppError(422, "validation_error", "Transcript speaker text prefix is invalid")
    if speaker_text_prefix and (
        speaker_role is None or speaker_text_prefix != f"{speaker_role.title()}: "
    ):
        raise AppError(422, "validation_error", "Transcript speaker text prefix is inconsistent")
    speaker_number = turn.get("speaker_number")
    if speaker_number is not None and (type(speaker_number) is not int or speaker_number < 1):
        raise AppError(422, "validation_error", "Transcript speaker number is invalid")
    source_warning = turn.get("source_warning", False)
    if type(source_warning) is not bool:
        raise AppError(422, "validation_error", "Transcript source warning metadata is invalid")
    start_ms, end_ms = turn.get("start_ms"), turn.get("end_ms")
    if start_ms is not None and (type(start_ms) is not int or start_ms < 0):
        raise AppError(422, "validation_error", "Transcript turn start is invalid")
    if end_ms is not None and (type(end_ms) is not int or end_ms < 0):
        raise AppError(422, "validation_error", "Transcript turn end is invalid")
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise AppError(422, "validation_error", "Transcript turn interval is invalid")
    try:
        turn_id = UUID(str(turn.get("id") or uuid4()))
    except (TypeError, ValueError, AttributeError):
        raise AppError(422, "validation_error", "Transcript turn identifier is invalid")
    return {
        "id": str(turn_id),
        "scope_id": str(actual_scope),
        "speaker_id": speaker_id,
        "speaker_role": speaker_role,
        "speaker_text_prefix": speaker_text_prefix,
        "speaker_number": speaker_number if speaker_id is not None else None,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": text,
        "edited": bool(turn.get("edited", False)),
        "source_warning": source_warning,
    }



def _assign_speaker_numbers(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain encrypted presentation numbers when earlier turns are deleted."""
    labels: dict[tuple[str, str], int] = {}
    maxima: dict[str, int] = {}
    for turn in turns:
        speaker, number = turn.get("speaker_id"), turn.get("speaker_number")
        if speaker is not None and number is not None:
            labels.setdefault((turn["scope_id"], speaker), number)
            maxima[turn["scope_id"]] = max(maxima.get(turn["scope_id"], 0), number)
    for turn in turns:
        speaker, scope = turn.get("speaker_id"), turn["scope_id"]
        if speaker is None:
            turn["speaker_number"] = None
            continue
        key = (scope, speaker)
        if key not in labels:
            maxima[scope] = maxima.get(scope, 0) + 1
            labels[key] = maxima[scope]
        turn["speaker_number"] = labels[key]
    return turns


def _restore_legacy_telehealth_roles(
    db: Session, *, transcript: Transcript, turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive presentation metadata for old telehealth turns without rewriting source."""
    scope_ids = {
        turn["scope_id"] for turn in turns
        if turn.get("speaker_role") is None and turn.get("speaker_id") in {"clinician", "patient"}
    }
    if not scope_ids:
        return turns
    sessions = db.scalars(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.id.in_([UUID(scope_id) for scope_id in scope_ids]),
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == transcript.owner_user_id,
    )).all()
    valid_channels: dict[str, set[str]] = {str(session.id): set() for session in sessions}
    if sessions:
        parts = db.scalars(select(TelehealthCapturePart).where(
            TelehealthCapturePart.session_id.in_([session.id for session in sessions]),
            TelehealthCapturePart.transcript_id == transcript.id,
            TelehealthCapturePart.owner_user_id == transcript.owner_user_id,
        )).all()
        for part in parts:
            if part.channel in {"clinician", "patient"}:
                valid_channels[str(part.session_id)].add(part.channel)
    for turn in turns:
        if turn.get("speaker_role") is not None:
            continue
        role = turn.get("speaker_id")
        if role not in valid_channels.get(turn["scope_id"], set()):
            continue
        label = "Clinician" if role == "clinician" else "Patient"
        turn["speaker_role"] = role
        prefix = f"{label}: "
        if turn["text"].startswith(prefix):
            turn["speaker_text_prefix"] = prefix
    return turns


def load_current_turns(db: Session, *, transcript: Transcript) -> list[dict[str, Any]] | None:
    value = decrypt_json_for_owner(
        db, owner_user_id=transcript.owner_user_id, table="transcripts",
        field="current_draft_turns_encrypted", record_id=transcript.id,
        stored_value=transcript.current_draft_turns_encrypted,
    )
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("version") != STRUCTURED_TURNS_SCHEMA_VERSION or not isinstance(value.get("turns"), list):
        raise AppError(500, "transcript_turns_unavailable", "Structured transcript data is unavailable")
    try:
        turns = [_normalize_turn(turn) for turn in value["turns"]]
        if len({turn["id"] for turn in turns}) != len(turns):
            raise ValueError("duplicate turn IDs")
        return _restore_legacy_telehealth_roles(
            db, transcript=transcript, turns=_assign_speaker_numbers(turns),
        )
    except (TypeError, KeyError, ValueError):
        raise AppError(500, "transcript_turns_unavailable", "Structured transcript data is unavailable")


def turns_for_version(db: Session, *, version: TranscriptVersion) -> list[dict[str, Any]] | None:
    if not version.structured_turns_encrypted:
        return None
    value = decrypt_json_for_owner(
        db, owner_user_id=version.transcript.owner_user_id,
        table="transcript_versions", field="structured_turns_encrypted",
        record_id=version.id, stored_value=version.structured_turns_encrypted,
    )
    if not isinstance(value, dict) or value.get("version") != version.structured_turns_schema_version or not isinstance(value.get("turns"), list):
        raise AppError(500, "transcript_turns_unavailable", "Structured transcript data is unavailable")
    try:
        turns = [_normalize_turn(turn) for turn in value["turns"]]
        if len({turn["id"] for turn in turns}) != len(turns):
            raise ValueError("duplicate turn IDs")
        return _restore_legacy_telehealth_roles(
            db, transcript=version.transcript, turns=_assign_speaker_numbers(turns),
        )
    except (TypeError, KeyError, ValueError):
        raise AppError(500, "transcript_turns_unavailable", "Structured transcript data is unavailable")


def _encrypt_current_turns(db: Session, *, transcript: Transcript, turns: list[dict[str, Any]]) -> None:
    transcript.current_draft_turns_encrypted = encrypt_json_for_owner(
        db, owner_user_id=transcript.owner_user_id, table="transcripts",
        field="current_draft_turns_encrypted", record_id=transcript.id,
        plaintext={"version": STRUCTURED_TURNS_SCHEMA_VERSION, "turns": turns},
    )
    projection = canonical_transcript_projection(turns)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db, owner_user_id=transcript.owner_user_id, table="transcripts",
        field="current_draft_text_encrypted", record_id=transcript.id,
        plaintext=projection,
    )


def append_transcript_turns(
    db: Session,
    *,
    transcript: Transcript,
    turns: Iterable[dict[str, Any]],
    scope_id: UUID,
    fallback_text: str = "",
    mark_ready: bool = False,
) -> list[dict[str, Any]]:
    """Append normalized provider turns, preserving older flat text as unattributed."""
    previous = load_current_turns(db, transcript=transcript)
    if previous is None:
        old_text = decrypt_text_for_owner(
            db, owner_user_id=transcript.owner_user_id, table="transcripts",
            field="current_draft_text_encrypted", record_id=transcript.id,
            stored_value=transcript.current_draft_text_encrypted,
        ) or ""
        previous = ([ _normalize_turn({"scope_id": uuid4(), "speaker_id": None, "text": old_text}) ] if old_text else [])
    incoming = [_normalize_turn({**turn, "speaker_number": None}, scope_id=scope_id) for turn in turns]
    if (not incoming or not any(turn["text"] for turn in incoming)) and fallback_text:
        incoming = [_normalize_turn({"scope_id": scope_id, "speaker_id": None, "text": fallback_text})]
    combined = _refresh_partial_source_warnings(
        db, transcript=transcript, turns=[*previous, *incoming],
    )
    combined = _assign_speaker_numbers(combined)
    if len(combined) > 100000 or sum(len(turn["text"]) for turn in combined) > 8_000_000:
        raise AppError(422, "validation_error", "Structured transcript exceeds supported size")
    if len({turn["id"] for turn in combined}) != len(combined):
        raise AppError(422, "validation_error", "Transcript contains duplicate turn identifiers")
    _encrypt_current_turns(db, transcript=transcript, turns=combined)
    if mark_ready:
        transcript.status = TranscriptStatus.ready
    db.add(transcript)
    return combined


def merge_transcript_turns_preserving_order(
    db: Session,
    *,
    transcript: Transcript,
    turns: Iterable[dict[str, Any]],
    scope_id: UUID,
    fallback_text: str = "",
) -> list[dict[str, Any]]:
    """Insert newly recovered timed turns without reordering existing turns."""
    previous = load_current_turns(db, transcript=transcript)
    if previous is None:
        old_text = decrypt_text_for_owner(
            db, owner_user_id=transcript.owner_user_id, table="transcripts",
            field="current_draft_text_encrypted", record_id=transcript.id,
            stored_value=transcript.current_draft_text_encrypted,
        ) or ""
        previous = [_normalize_turn({"scope_id": uuid4(), "speaker_id": None, "text": old_text})] if old_text else []
    incoming = [_normalize_turn({**turn, "speaker_number": None}, scope_id=scope_id) for turn in turns]
    if not incoming and fallback_text:
        incoming = [_normalize_turn({"scope_id": scope_id, "speaker_id": None, "text": fallback_text})]
    combined = list(previous)
    timed = sorted(
        (turn for turn in incoming if turn.get("start_ms") is not None),
        key=lambda turn: (turn["start_ms"], turn.get("end_ms") or turn["start_ms"]),
    )
    untimed = [turn for turn in incoming if turn.get("start_ms") is None]
    for new_turn in timed:
        same_scope_indexes = [index for index, old_turn in enumerate(combined)
                              if old_turn.get("scope_id") == str(scope_id)
                              and old_turn.get("start_ms") is not None]
        position = next((index for index in same_scope_indexes
                         if combined[index]["start_ms"] > new_turn["start_ms"]),
                        same_scope_indexes[-1] + 1 if same_scope_indexes else len(combined))
        combined.insert(position, new_turn)
    # Missing chronology is explicit and stays as a distinct appended source block.
    combined.extend(untimed)
    combined = _assign_speaker_numbers(combined)
    if len(combined) > 100000 or sum(len(turn["text"]) for turn in combined) > 8_000_000:
        raise AppError(422, "validation_error", "Structured transcript exceeds supported size")
    if len({turn["id"] for turn in combined}) != len(combined):
        raise AppError(422, "validation_error", "Transcript contains duplicate turn identifiers")
    _encrypt_current_turns(db, transcript=transcript, turns=combined)
    db.add(transcript)
    return combined


def store_turns_on_version(
    db: Session,
    *,
    version: TranscriptVersion,
    owner_user_id: UUID,
    turns: list[dict[str, Any]],
) -> None:
    normalized = _assign_speaker_numbers([_normalize_turn(turn) for turn in turns])
    if len(normalized) > 100000 or sum(len(turn["text"]) for turn in normalized) > 8_000_000:
        raise AppError(422, "validation_error", "Structured transcript exceeds supported size")
    if len({turn["id"] for turn in normalized}) != len(normalized):
        raise AppError(422, "validation_error", "Transcript contains duplicate turn identifiers")
    projection = canonical_transcript_projection(normalized)
    source_text = decrypt_text_for_owner(
        db, owner_user_id=owner_user_id, table="transcript_versions",
        field="text_encrypted", record_id=version.id, stored_value=version.text_encrypted,
    )
    if source_text != projection:
        raise AppError(409, "transcript_version_mismatch", "Structured transcript does not match its text snapshot")
    version.structured_turns_schema_version = STRUCTURED_TURNS_SCHEMA_VERSION
    version.structured_turns_encrypted = encrypt_json_for_owner(
        db, owner_user_id=owner_user_id, table="transcript_versions",
        field="structured_turns_encrypted", record_id=version.id,
        plaintext={"version": STRUCTURED_TURNS_SCHEMA_VERSION, "turns": normalized},
    )
    db.add(version)


def _version_for_transcript(db: Session, transcript: Transcript) -> TranscriptVersion | None:
    return db.scalar(select(TranscriptVersion).where(
        TranscriptVersion.transcript_id == transcript.id,
    ).order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.created_at.desc(), TranscriptVersion.id.desc()))


def _turn_label(turn: dict[str, Any]) -> str:
    role = turn.get("speaker_role")
    if role == "clinician":
        return "Clinician"
    if role == "patient":
        return "Patient"
    return f"Speaker {turn['speaker_number']}" if turn["speaker_id"] is not None else "?"


def current_turns_for_response(db: Session, *, transcript: Transcript) -> tuple[UUID | None, list[dict[str, Any]] | None]:
    version = _version_for_transcript(db, transcript)
    turns = load_current_turns(db, transcript=transcript)
    if turns is not None:
        revision_id = version.id if version is not None else None
        ranges: list[dict[str, Any]] = []
        cursor = 0
        for turn in turns:
            text = turn["text"]
            label = _turn_label(turn)
            ranges.append({**turn, "speaker_label": label, "start_index": cursor, "end_index": cursor + len(text)})
            cursor += len(text) + 1
        return revision_id, ranges
    # A flat legacy draft is returned without fabricated structured metadata.
    return (version.id if version else None), None


def _lock_editable_transcript(db: Session, owner: User, transcript_id: UUID, expected_version_id: UUID) -> tuple[Transcript, TranscriptVersion, list[dict[str, Any]]]:
    from app.services.transcripts import (
        _lock_split_source_writer_transcript,
        _resolved_transcript_status,
        assert_no_unresolved_stream_session,
        transcript_is_expired,
    )
    transcript = _lock_split_source_writer_transcript(db, owner, transcript_id=transcript_id)
    if transcript_is_expired(transcript):
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    assert_no_unresolved_stream_session(db, transcript_id=transcript.id)
    if transcript.status is not TranscriptStatus.ready or _resolved_transcript_status(db, transcript=transcript) is not TranscriptStatus.ready:
        raise AppError(409, "business_rule_violation", "Transcript editing is available after transcription finishes", {"code": "transcript_not_ready"})
    version = _version_for_transcript(db, transcript)
    if version is None or version.id != expected_version_id:
        raise AppError(409, "transcript_version_conflict", "Transcript changed; reload before editing", {"current_version_id": str(version.id) if version else None})
    turns = load_current_turns(db, transcript=transcript)
    if turns is None:
        raise AppError(409, "business_rule_violation", "This transcript has no structured speaker turns")
    return transcript, version, turns


def _commit_turn_mutation(db: Session, *, transcript: Transcript, owner: User, turns: list[dict[str, Any]]) -> TranscriptVersion:
    from app.services.transcripts import _create_transcript_version_from_text
    turns = _refresh_partial_source_warnings(db, transcript=transcript, turns=turns)
    normalized = _assign_speaker_numbers([_normalize_turn(turn) for turn in turns])
    projection = canonical_transcript_projection(normalized)
    version = _create_transcript_version_from_text(db, transcript=transcript, owner_user_id=owner.id, plaintext=projection)
    store_turns_on_version(db, version=version, owner_user_id=owner.id, turns=normalized)
    _encrypt_current_turns(db, transcript=transcript, turns=normalized)
    db.add(transcript)
    db.flush()
    return version


def _refresh_partial_source_warnings(
    db: Session,
    *,
    transcript: Transcript,
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep current gaps in the saved clinical source without allowing notice-only content."""
    clinical_turns = [turn for turn in turns if not turn.get("source_warning") and turn.get("text", "").strip()]
    existing = {
        turn["id"]: turn for turn in turns
        if turn.get("source_warning")
    }
    if not clinical_turns:
        return [turn for turn in turns if not turn.get("source_warning")]

    sessions = list(db.scalars(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == transcript.owner_user_id,
        or_(
            TelehealthCaptureSession.status == "partial",
            TelehealthCaptureSession.incomplete_acknowledged_at.is_not(None),
        ),
    )))
    managed_ids = {str(uuid5(session.id, "telehealth-partial-source-warning-v1")) for session in sessions}
    notices: list[dict[str, Any]] = [
        turn for turn in turns
        if turn.get("source_warning") and turn["id"] not in managed_ids
    ]
    for session in sessions:
        # Retain discarded session IDs in managed_ids so a prior warning is
        # removed on refresh, but do not warn about audio the owner discarded.
        if session.status == "discarded":
            continue
        needs_warning = session.status == "partial" or session.failure_code in {
            "source_ended", "permission_revoked", "audio_clock_interrupted",
            "audio_backpressure", "audio_processor_failed", "upload_failed",
            "browser_capture_failed",
        }
        if not needs_warning:
            continue
        parts = list(db.scalars(select(TelehealthCapturePart).where(
            TelehealthCapturePart.session_id == session.id,
            TelehealthCapturePart.transcript_id == transcript.id,
            TelehealthCapturePart.owner_user_id == transcript.owner_user_id,
        ).order_by(TelehealthCapturePart.channel, TelehealthCapturePart.sequence_no)))
        jobs = {
            job.id: job for job in db.scalars(select(TranscriptIngestionJob).where(
                TranscriptIngestionJob.id.in_([part.ingestion_job_id for part in parts if part.ingestion_job_id]),
                TranscriptIngestionJob.transcript_id == transcript.id,
                TranscriptIngestionJob.owner_user_id == transcript.owner_user_id,
            ))
        }
        gaps = []
        for part in parts:
            job = jobs.get(part.ingestion_job_id)
            if job is None or job.status is not TranscriptIngestionJobStatus.applied:
                start_ms = part.timeline_start_frame * 1000 // max(1, part.timeline_sample_rate)
                end_ms = (part.timeline_end_frame * 1000 + max(1, part.timeline_sample_rate) - 1) // max(1, part.timeline_sample_rate)
                gaps.append(f"{part.channel} {start_ms}-{end_ms}ms")
        shown_gaps = gaps[:20]
        detail = ", ".join(shown_gaps) if shown_gaps else "the recording may have an uncaptured interval"
        if len(gaps) > len(shown_gaps):
            detail += f", plus {len(gaps) - len(shown_gaps)} additional intervals"
        text = (
            "INCOMPLETE TELEHEALTH RECORDING: Current missing audio: " + detail + ". "
            "Verify this transcript before relying on it for a clinical note."
        )
        notice_id = str(uuid5(session.id, "telehealth-partial-source-warning-v1"))
        notice = existing.get(notice_id, {
            "id": notice_id,
            "scope_id": str(session.id),
            "speaker_id": None,
            "speaker_role": None,
            "speaker_text_prefix": "",
            "speaker_number": None,
            "start_ms": None,
            "end_ms": None,
            "edited": False,
            "source_warning": True,
        })
        notice["text"] = text
        notice["source_warning"] = True
        notices.append(notice)
    preserved = [turn for turn in turns if not turn.get("source_warning")]
    return [*notices, *preserved]


def edit_transcript_turn(db: Session, owner: User, *, transcript_id: UUID, turn_id: UUID, expected_version_id: UUID, text: str) -> Transcript:
    transcript, _, turns = _lock_editable_transcript(db, owner, transcript_id, expected_version_id)
    match = next((turn for turn in turns if turn["id"] == str(turn_id)), None)
    if match is None:
        raise AppError(404, "not_found", "Transcript turn not found", {"resource": "transcript_turn", "turn_id": str(turn_id)})
    if match.get("source_warning"):
        raise AppError(409, "business_rule_violation", "Telehealth source warnings cannot be edited.")
    match["text"] = text
    match["edited"] = True
    version = _commit_turn_mutation(db, transcript=transcript, owner=owner, turns=turns)
    db.commit()
    from app.services.transcripts import _attempt_preview_redaction
    _attempt_preview_redaction(db, transcript_version=version)
    db.refresh(transcript)
    return transcript


def add_transcript_turn(db: Session, owner: User, *, transcript_id: UUID, expected_version_id: UUID, text: str, after_turn_id: UUID | None) -> Transcript:
    transcript, _, turns = _lock_editable_transcript(db, owner, transcript_id, expected_version_id)
    position = len(turns)
    scope_id = UUID(turns[-1]["scope_id"]) if turns else uuid4()
    if after_turn_id is not None:
        for index, turn in enumerate(turns):
            if turn["id"] == str(after_turn_id):
                position = index + 1
                scope_id = UUID(turn["scope_id"])
                break
        else:
            raise AppError(404, "not_found", "Transcript turn not found", {"resource": "transcript_turn", "turn_id": str(after_turn_id)})
    turns.insert(position, _normalize_turn({"scope_id": scope_id, "speaker_id": None, "text": text}))
    version = _commit_turn_mutation(db, transcript=transcript, owner=owner, turns=turns)
    db.commit()
    from app.services.transcripts import _attempt_preview_redaction
    _attempt_preview_redaction(db, transcript_version=version)
    db.refresh(transcript)
    return transcript


def delete_transcript_turn(db: Session, owner: User, *, transcript_id: UUID, turn_id: UUID, expected_version_id: UUID) -> Transcript:
    transcript, _, turns = _lock_editable_transcript(db, owner, transcript_id, expected_version_id)
    match = next((turn for turn in turns if turn["id"] == str(turn_id)), None)
    if match is not None and match.get("source_warning"):
        raise AppError(409, "business_rule_violation", "Telehealth source warnings cannot be deleted.")
    remaining = [turn for turn in turns if turn["id"] != str(turn_id)]
    if len(remaining) == len(turns):
        raise AppError(404, "not_found", "Transcript turn not found", {"resource": "transcript_turn", "turn_id": str(turn_id)})
    version = _commit_turn_mutation(db, transcript=transcript, owner=owner, turns=remaining)
    db.commit()
    from app.services.transcripts import _attempt_preview_redaction
    _attempt_preview_redaction(db, transcript_version=version)
    db.refresh(transcript)
    return transcript


def render_redacted_diarised_transcript(
    db: Session,
    *,
    version: TranscriptVersion,
    redacted_text: str,
    phi_index: list[dict[str, Any]],
) -> str | None:
    """Render labels only after exact offset mapping through every replacement.

    Returns None when any replacement cannot be mapped to a single turn, so callers
    can fail closed before sending source content to a generation provider.
    """
    source = decrypt_text_for_owner(
        db, owner_user_id=version.transcript.owner_user_id, table="transcript_versions",
        field="text_encrypted", record_id=version.id, stored_value=version.text_encrypted,
    )
    turns = turns_for_version(db, version=version)
    if turns is None:
        return None
    if source is None or canonical_transcript_projection(turns) != source:
        raise AppError(409, "transcript_version_mismatch", "Structured transcript does not match its saved text")

    return render_redacted_turn_snapshot(
        source_text=source, turns=turns, redacted_text=redacted_text, phi_index=phi_index,
    )


def render_redacted_turn_snapshot(
    *,
    source_text: str,
    turns: list[dict[str, Any]],
    redacted_text: str,
    phi_index: list[dict[str, Any]],
) -> str | None:
    """Pure renderer for an already authorized, immutable turn snapshot."""
    import re

    if canonical_transcript_projection(turns) != source_text:
        return None
    source = source_text

    by_placeholder: dict[str, str] = {}
    for item in phi_index:
        placeholder, value = item.get("placeholder"), item.get("value")
        if not isinstance(placeholder, str) or not isinstance(value, str) or not value:
            raise AppError(409, "diarised_redaction_mapping_failed", "Speaker labels could not be safely mapped through redaction")
        if placeholder in by_placeholder:
            raise AppError(409, "diarised_redaction_mapping_failed", "Speaker labels could not be safely mapped through redaction")
        by_placeholder[placeholder] = value

    token_pattern = re.compile(r"\[PHI-[1-9][0-9]*\]")
    tokens = list(token_pattern.finditer(redacted_text))
    spans: list[tuple[int, int, int, int]] = []
    source_cursor = out_cursor = 0
    used: set[str] = set()
    for token in tokens:
        placeholder = token.group(0)
        value = by_placeholder.get(placeholder)
        if value is None:
            # A literal PHI-shaped string in source is not a redaction token.
            continue
        # Resolve in one pass. Searching every later occurrence for every PHI
        # value can become quadratic for repeated short values in long notes.
        prefix = redacted_text[out_cursor:token.start()]
        source_start = source_cursor + len(prefix)
        if source[source_cursor:source_start] != prefix or source[source_start:source_start + len(value)] != value:
            # An untracked literal token may precede the actual generated token.
            continue
        source_end = source_start + len(value)
        spans.append((source_start, source_end, token.start(), token.end()))
        source_cursor = source_end
        out_cursor = token.end()
        used.add(placeholder)
    if used != set(by_placeholder):
        return None
    if source[source_cursor:] != redacted_text[out_cursor:]:
        return None

    # Convert source boundaries only when they land outside a redacted span.
    boundaries: set[int] = {0, len(source)}
    turn_ranges: list[tuple[int, int]] = []
    cursor = 0
    for turn in turns:
        start, end = cursor, cursor + len(turn["text"])
        turn_ranges.append((start, end))
        boundaries.update((start, end))
        cursor = end + 1
    mapped: dict[int, int] = {}
    src_cursor = out_cursor = 0
    for source_start, source_end, red_start, red_end in spans:
        literal_length = source_start - src_cursor
        for boundary in boundaries:
            if src_cursor <= boundary <= source_start:
                mapped[boundary] = out_cursor + boundary - src_cursor
        if any(source_start < boundary < source_end for boundary in boundaries):
            # A PII span crossing a turn edge would make attribution ambiguous.
            if any(source_start < edge < source_end for edge in boundaries):
                return None
        mapped[source_start] = red_start
        mapped[source_end] = red_end
        src_cursor, out_cursor = source_end, red_end
    for boundary in boundaries:
        if src_cursor <= boundary <= len(source):
            mapped[boundary] = out_cursor + boundary - src_cursor
    if any(boundary not in mapped for boundary in boundaries):
        return None

    turns = _assign_speaker_numbers([dict(turn) for turn in turns])
    scope_order: dict[str, int] = {}
    output_lines: list[str] = []
    for turn, (start, end) in zip(turns, turn_ranges):
        scope = turn["scope_id"]
        if scope not in scope_order:
            scope_order[scope] = len(scope_order) + 1
            number = scope_order[scope]
            suffix = "" if number == 1 else " (speaker labels are independent of earlier recordings)"
            output_lines.append(f"Recording {number}{suffix}")
        label = _turn_label(turn)
        output_start, output_end = mapped[start], mapped[end]
        if output_start > output_end or output_end > len(redacted_text):
            return None
        body = redacted_text[output_start:output_end]
        legacy_prefix = turn.get("speaker_text_prefix", "")
        expected_prefix = f"{turn.get('speaker_role', '').title()}: " if turn.get("speaker_role") else ""
        if legacy_prefix and legacy_prefix == expected_prefix and body.startswith(legacy_prefix):
            body = body[len(legacy_prefix):]
        elif (
            legacy_prefix and legacy_prefix == expected_prefix and body == turn["text"]
            and len(body) <= len(legacy_prefix) and legacy_prefix.startswith(body)
        ):
            # Frozen excerpts can end inside an old literal prefix. Strip only
            # when the entire mapped turn is that exact prefix fragment.
            body = ""
        output_lines.append(f"{label}: {body}")
    return "\n".join(output_lines)
