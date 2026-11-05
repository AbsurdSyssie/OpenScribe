"""Apply a completed dual-source capture exactly once to the owner transcript.

The existing transcript ingestion worker persists individual encrypted STT
results and audio accounting. This service only assembles them when *all*
session parts have successful, decryptable results. It preserves OpenScribe's
structured-turn projection/version invariants, and then uses the existing
redaction and generation wake-up boundaries.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    TranscriptStatus,
    User,
    utcnow,
)
from app.services.content_crypto import decrypt_json_for_owner
from app.services.telehealth_audio import (
    TelehealthAudioPart, TelehealthSourceSpan, map_provider_turn_to_consultation,
)
from app.services.telehealth_merge import (
    TelehealthSourceTurn, merge_telehealth_source_turns,
)


def _mapped_turns(
    db: Session,
    *,
    transcript: Transcript,
    part: TelehealthCapturePart,
    job: TranscriptIngestionJob,
) -> tuple[TelehealthSourceTurn, ...]:
    """Keep timed segments only when all content is recoverable and aligned."""
    from app.services.transcripts import job_result_text

    text = job_result_text(db, transcript=transcript, job=job)
    if text is None:
        raise AppError(409, "conflict", "Transcription result is not yet available.")
    if not text.strip():
        return ()  # A connected source can legitimately contain only silence.

    meta = TelehealthAudioPart(
        session_id=part.session_id,
        channel=part.channel,
        sequence_no=part.sequence_no,
        idempotency_key=part.idempotency_key,
        timeline_sample_rate=part.timeline_sample_rate,
        file_sample_rate=part.file_sample_rate,
        file_samples=part.file_samples,
        spans=tuple(TelehealthSourceSpan(**span) for span in part.timing_spans_json),
        timeline_start_frame=part.timeline_start_frame,
        timeline_end_frame=part.timeline_end_frame,
    )
    stored = decrypt_json_for_owner(
        db,
        owner_user_id=transcript.owner_user_id,
        table="transcript_ingestion_jobs",
        field="result_turns_encrypted",
        record_id=job.id,
        stored_value=job.result_turns_encrypted,
    ) if job.result_turns_encrypted else None
    candidate = stored.get("turns") if isinstance(stored, dict) and stored.get("version") == 1 else None
    if isinstance(candidate, list) and candidate:
        segments = []
        has_unmappable_timing = False
        previous_start_ms = -1
        for index, turn in enumerate(candidate):
            if not isinstance(turn, dict) or not isinstance(turn.get("text"), str):
                segments = []
                break
            start, end = turn.get("start_ms"), turn.get("end_ms")
            position = (
                map_provider_turn_to_consultation(meta, start_ms=start, end_ms=end)
                if start is not None and end is not None
                else None
            )
            if position is None:
                has_unmappable_timing = True
            elif position[0] < previous_start_ms:
                # Never sort provider words into a new text order. Preserve
                # the canonical flat transcript when source time rewinds.
                has_unmappable_timing = True
            else:
                previous_start_ms = position[0]
            segments.append(TelehealthSourceTurn(
                part_id=part.id,
                channel=part.channel,
                source_sequence=part.sequence_no,
                segment_index=index,
                text=turn["text"].strip(),
                start_ms=position[0] if position else None,
                end_ms=position[1] if position else None,
            ))
        if segments and not has_unmappable_timing and all(item.text for item in segments):
            # Source text must match its segmentation, not just be similar:
            # otherwise we would omit potentially clinically important words.
            compact = lambda value: "".join(ch for ch in value if not ch.isspace())
            if compact(" ".join(turn.text for turn in segments)) == compact(text):
                return tuple(segments)

    # Provider supplied no segments, segments crossed VAD gaps, or content
    # differed from flat transcription: retain all content with source role.
    return (TelehealthSourceTurn(
        part_id=part.id,
        channel=part.channel,
        source_sequence=part.sequence_no,
        segment_index=0,
        text=text.strip(),
    ),)


def apply_completed_telehealth_capture(
    db: Session,
    *,
    capture_session_id: UUID,
) -> bool:
    """Return False while pending; commit all role-labelled results once ready.

    Call after individual jobs have durably committed their encrypted results.
    Safe for replay by multiple workers: locks owner -> transcript -> session.
    """
    identity = db.get(TelehealthCaptureSession, capture_session_id)
    if identity is None:
        return False
    owner_id, transcript_id = identity.owner_user_id, identity.transcript_id

    # Establish lock hierarchy used by normal source text writers. Never
    # trust the session's cached owner id as a user-facing authorization token.
    owner = db.scalar(select(User).where(User.id == owner_id).with_for_update())
    if owner is None:
        return False
    from app.services.telehealth_capture import _owner_capture_session_locked
    transcript, session = _owner_capture_session_locked(
        db, owner, transcript_id=transcript_id, capture_session_id=capture_session_id,
    )
    if session.status == "completed":
        return True
    if session.status != "processing":
        return False
    parts = list(db.scalars(
        select(TelehealthCapturePart).where(
            TelehealthCapturePart.session_id == session.id,
            TelehealthCapturePart.transcript_id == transcript.id,
            TelehealthCapturePart.owner_user_id == owner.id,
        ).order_by(TelehealthCapturePart.channel, TelehealthCapturePart.sequence_no).with_for_update()
    ))
    incomplete = bool(session.failure_code and session.incomplete_acknowledged_at)
    if not parts or (
        not incomplete and {p.channel for p in parts} != {"clinician", "patient"}
    ):
        return False
    if any(p.status != "transcribed" or p.ingestion_job_id is None for p in parts):
        return False
    jobs = {
        job.id: job for job in db.scalars(
            select(TranscriptIngestionJob).where(
                TranscriptIngestionJob.id.in_([p.ingestion_job_id for p in parts]),
                TranscriptIngestionJob.transcript_id == transcript.id,
                TranscriptIngestionJob.owner_user_id == owner.id,
            ).with_for_update()
        )
    }
    if len(jobs) != len(parts) or any(
        jobs[part.ingestion_job_id].status is not TranscriptIngestionJobStatus.completed
        or jobs[part.ingestion_job_id].result_text_encrypted is None
        for part in parts
    ):
        return False

    text_turns: list[TelehealthSourceTurn] = []
    for part in parts:
        text_turns.extend(_mapped_turns(
            db, transcript=transcript, part=part, job=jobs[part.ingestion_job_id],
        ))
    merged = merge_telehealth_source_turns(text_turns)
    if not merged.turns:
        # A fully connected, genuinely silent consultation cannot yield notes.
        session.status = "failed"
        session.failure_code = "no_transcribed_speech"
        transcript.status = TranscriptStatus.failed
        session.updated_at = utcnow()
        db.add(session)
        db.add(transcript)
        db.commit()
        return False

    from app.services.structured_transcripts import (
        append_transcript_turns,
        canonical_transcript_projection,
        store_turns_on_version,
    )
    from app.services.transcripts import (
        _attempt_preview_redaction,
        _create_transcript_version_from_text,
        _trigger_waiting_generation_dispatches,
    )
    # Labels are included in *source text*, not injected after redaction.
    # This preserves speaker provenance for the LLM through the existing
    # owner-encrypted transcript -> redaction -> generation pipeline.
    formatted = [
        {
            "speaker_id": turn.speaker_id,
            "start_ms": turn.start_ms,
            "end_ms": turn.end_ms,
            "text": f"{turn.speaker_label}: {turn.text}",
        }
        for turn in merged.turns
    ]
    if incomplete:
        # Put the warning *inside the source text before redaction*, never
        # solely in a front-end badge that downstream note generation misses.
        # Every generation still requires the client's explicit visible notice.
        formatted.insert(0, {
            "speaker_id": None, "start_ms": None, "end_ms": None,
            "text": (
                "INCOMPLETE TELEHEALTH RECORDING: audio may be missing. "
                "The clinician must verify this transcript before relying "
                "on its contents for a clinical note."
            ),
        })
    combined = append_transcript_turns(
        db,
        transcript=transcript,
        scope_id=session.id,
        turns=formatted,
        mark_ready=False,
    )
    draft_text = canonical_transcript_projection(combined)
    version = _create_transcript_version_from_text(
        db, transcript=transcript,
        owner_user_id=transcript.owner_user_id,
        plaintext=draft_text,
    )
    store_turns_on_version(
        db, version=version, owner_user_id=transcript.owner_user_id,
        turns=combined,
    )
    now = utcnow()
    for job in jobs.values():
        job.status = TranscriptIngestionJobStatus.applied
        job.applied_at = now
        db.add(job)
    session.status = "completed"
    session.updated_at = now
    transcript.status = TranscriptStatus.ready
    db.add(session)
    db.add(transcript)
    db.commit()
    _trigger_waiting_generation_dispatches(db, transcript_id=transcript.id)
    _attempt_preview_redaction(db, transcript_version=version)
    return True



def reconcile_processing_telehealth_captures(
    db: Session,
    *,
    batch_size: int = 50,
) -> int:
    """Recover a capture after the last STT worker committed but crashed.

    A second worker may see the same completed result. The capture session row
    lock and durable completed status make applying the clinical draft safe
    under duplicate scheduling. No provider call is ever repeated by this task.
    """
    if not 1 <= batch_size <= 100:
        raise ValueError("Invalid telehealth reconciliation batch size.")
    ids = list(db.scalars(select(TelehealthCaptureSession.id).where(
        TelehealthCaptureSession.status == "processing",
    ).order_by(TelehealthCaptureSession.updated_at.asc()).limit(batch_size)))
    db.rollback()
    applied = 0
    for capture_id in ids:
        try:
            if apply_completed_telehealth_capture(db, capture_session_id=capture_id):
                applied += 1
        except Exception:
            db.rollback()
            # Do not log clinical provider text, raw audio, or stack traces.
            import logging
            logging.getLogger("openscribe.telehealth").warning(
                "telehealth_capture_reconciliation_deferred",
                extra={"capture_session_id": str(capture_id)},
            )
        else:
            if db.in_transaction():
                db.rollback()
    return applied
