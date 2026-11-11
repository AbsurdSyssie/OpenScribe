"""Apply a completed dual-source capture exactly once to the owner transcript.

The existing transcript ingestion worker persists individual encrypted STT
results and audio accounting. This service only assembles them when *all*
session parts have successful, decryptable results. It preserves OpenScribe's
structured-turn projection/version invariants, and then uses the existing
redaction and generation wake-up boundaries.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    TranscriptStreamSession,
    TranscriptStatus,
    utcnow,
    User,
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
    """Apply newly completed results once, including accepted partial retries."""
    identity = db.get(TelehealthCaptureSession, capture_session_id)
    if identity is None:
        return False
    owner_id, transcript_id = identity.owner_user_id, identity.transcript_id
    from app.services.consultation_split_locks import lock_consultation_split_source_scope
    scope = lock_consultation_split_source_scope(
        db, owner_user_id=owner_id, transcript_id=transcript_id,
    )
    if scope is None:
        return False
    owner, transcript = scope.owner, scope.transcript
    if owner.is_system_admin or owner.team_id is None or transcript.team_id != owner.team_id:
        return False
    from app.services.transcripts import transcript_is_expired
    if transcript_is_expired(transcript):
        return False
    session = db.scalar(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.id == capture_session_id,
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
        TelehealthCaptureSession.team_id == transcript.team_id,
    ).with_for_update())
    if session is None:
        return False
    if session.status == "completed":
        return True
    partial = session.status == "partial"
    if session.status != "processing" and not partial:
        return False
    parts = list(db.scalars(
        select(TelehealthCapturePart).where(
            TelehealthCapturePart.session_id == session.id,
            TelehealthCapturePart.transcript_id == transcript.id,
            TelehealthCapturePart.owner_user_id == owner.id,
        ).order_by(TelehealthCapturePart.channel, TelehealthCapturePart.sequence_no).with_for_update()
    ))
    incomplete = bool(session.failure_code and session.incomplete_acknowledged_at)
    if not parts or (not partial and not incomplete and {p.channel for p in parts} != {"clinician", "patient"}):
        return False
    if not partial and any(p.status != "transcribed" or p.ingestion_job_id is None for p in parts):
        return False
    eligible_parts = [p for p in parts if p.status == "transcribed" and p.ingestion_job_id is not None]
    if not eligible_parts:
        return False
    jobs = {
        job.id: job for job in db.scalars(
            select(TranscriptIngestionJob).where(
                TranscriptIngestionJob.id.in_([p.ingestion_job_id for p in eligible_parts]),
                TranscriptIngestionJob.transcript_id == transcript.id,
                TranscriptIngestionJob.owner_user_id == owner.id,
            ).with_for_update()
        )
    }
    if len(jobs) != len(eligible_parts):
        return False
    new_parts = [part for part in eligible_parts if (
        jobs[part.ingestion_job_id].status is TranscriptIngestionJobStatus.completed
        and jobs[part.ingestion_job_id].result_text_encrypted is not None
    )]
    if partial:
        new_parts = [part for part in new_parts if jobs[part.ingestion_job_id].applied_at is None]
    elif len(new_parts) != len(parts):
        return False
    if not new_parts:
        return False

    # Source insertion shares the canonical writer boundary with edits and
    # generation. Keep completed encrypted results for durable reconciliation
    # while another unresolved source owns the writer boundary.
    capture_barrier = db.scalar(select(TelehealthCaptureSession.id).where(
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.id != session.id,
        TelehealthCaptureSession.status.in_(
            ("active", "stopping", "ready", "interrupted", "processing", "failed")
        ),
    ).limit(1))
    accepted_partial_job = exists(select(TelehealthCapturePart.id).join(
        TelehealthCaptureSession,
        TelehealthCaptureSession.id == TelehealthCapturePart.session_id,
    ).where(
        TelehealthCapturePart.ingestion_job_id == TranscriptIngestionJob.id,
        TelehealthCaptureSession.status == "partial",
    ))
    current_capture_job = exists(select(TelehealthCapturePart.id).where(
        TelehealthCapturePart.ingestion_job_id == TranscriptIngestionJob.id,
        TelehealthCapturePart.session_id == session.id,
    ))
    pending_barrier = db.scalar(select(TranscriptIngestionJob.id).where(
        TranscriptIngestionJob.transcript_id == transcript.id,
        TranscriptIngestionJob.status.in_(
            (TranscriptIngestionJobStatus.queued, TranscriptIngestionJobStatus.processing,
             TranscriptIngestionJobStatus.completed)
        ),
        not_(accepted_partial_job),
        not_(current_capture_job),
    ).limit(1))
    active_stream = db.scalar(select(TranscriptStreamSession.id).where(
        TranscriptStreamSession.transcript_id == transcript.id,
        TranscriptStreamSession.purpose == "conversation",
        TranscriptStreamSession.status.in_(
            ("active", "finalizing", "recovering", "failed")
        ),
    ).limit(1))
    if capture_barrier is not None or pending_barrier is not None or active_stream is not None:
        return False

    text_turns: list[TelehealthSourceTurn] = []
    for part in new_parts:
        text_turns.extend(_mapped_turns(
            db, transcript=transcript, part=part, job=jobs[part.ingestion_job_id],
        ))
    merged = merge_telehealth_source_turns(text_turns)

    from app.services.structured_transcripts import (
        merge_transcript_turns_preserving_order,
        canonical_transcript_projection,
        load_current_turns,
        _normalize_turn,
        _refresh_partial_source_warnings,
        store_turns_on_version,
    )
    from app.services.transcripts import (
        _attempt_preview_redaction,
        _create_transcript_version_from_text,
        _trigger_waiting_generation_dispatches,
    )
    # Role provenance comes from the server-owned capture channel and is stored
    # in the existing owner-encrypted structured turn snapshot. Spoken source
    # text remains unchanged; the renderer adds the role label after redaction.
    formatted = [
        {
            "speaker_id": turn.speaker_id,
            "speaker_role": turn.speaker_id,
            "start_ms": turn.start_ms,
            "end_ms": turn.end_ms,
            "text": turn.text,
        }
        for turn in merged.turns
    ]
    current = load_current_turns(db, transcript=transcript)
    if current is None:
        from app.services.transcripts import transcript_draft_text
        prior_text = transcript_draft_text(db, transcript=transcript) or ""
        current = ([_normalize_turn({
            "scope_id": uuid4(), "speaker_id": None, "text": prior_text,
        })] if prior_text.strip() else [])
    has_prior_clinical_text = bool(current and any(
        turn.get("text", "").strip() and not turn.get("source_warning") for turn in current
    ))
    if not formatted and not partial:
        # Preserve the explicit owner-resolution path for truly silent source.
        # Keep completed encrypted jobs available for /use-available.
        session.status = "failed"
        # A silent STT result must not erase an earlier recorder interruption:
        # that provenance describes audio which was never submitted and remains
        # clinically relevant after the available results are accepted.
        if session.failure_code is None:
            session.failure_code = "no_transcribed_speech"
        session.updated_at = utcnow()
        transcript.status = TranscriptStatus.failed
        db.add(session)
        db.add(transcript)
        db.commit()
        return False
    now = utcnow()
    for part in new_parts:
        job = jobs[part.ingestion_job_id]
        job.status = TranscriptIngestionJobStatus.applied
        job.applied_at = now
        db.add(job)
    all_parts_applied = all(
        part.ingestion_job_id is not None
        and (part.ingestion_job_id in jobs and jobs[part.ingestion_job_id].status is TranscriptIngestionJobStatus.applied
             or db.scalar(select(TranscriptIngestionJob.status).where(
                 TranscriptIngestionJob.id == part.ingestion_job_id,
             )) is TranscriptIngestionJobStatus.applied)
        for part in parts
    )
    if all_parts_applied and session.failure_code in (None, "transcription_failed", "no_transcribed_speech"):
        session.status = "completed"
        if session.failure_code in {"transcription_failed", "no_transcribed_speech"}:
            session.failure_code = None
            session.failure_channel = None
    elif partial:
        session.status = "partial"
    else:
        session.status = "completed"
    session.updated_at = now
    # Readiness and warning projections below query SQL directly; flush before
    # they inspect the durable job/session transitions (sessions disable autoflush).
    db.flush()
    has_new_clinical_text = any(turn["text"].strip() for turn in formatted)
    if has_new_clinical_text:
        previously_applied_same_capture = db.scalar(select(TranscriptIngestionJob.id).join(
            TelehealthCapturePart,
            TelehealthCapturePart.ingestion_job_id == TranscriptIngestionJob.id,
        ).where(
            TelehealthCapturePart.session_id == session.id,
            TranscriptIngestionJob.status == TranscriptIngestionJobStatus.applied,
            TranscriptIngestionJob.id.not_in([part.ingestion_job_id for part in new_parts]),
        ).limit(1)) is not None
        has_same_capture_timed_anchor = bool(current and any(
            turn.get("scope_id") == str(session.id) and turn.get("start_ms") is not None
            for turn in current
        ))
        if partial and previously_applied_same_capture and not has_same_capture_timed_anchor:
            for turn in formatted:
                turn["text"] = "Recovered audio (timing cannot be aligned to saved capture turns): " + turn["text"]
        elif partial and previously_applied_same_capture:
            for turn in formatted:
                if turn["start_ms"] is None:
                    turn["text"] = "Recovered audio (timing unavailable): " + turn["text"]
    if has_new_clinical_text:
        combined = merge_transcript_turns_preserving_order(
            db, transcript=transcript, turns=formatted, scope_id=session.id,
        )
    else:
        combined = current or []
        if not combined and transcript.current_draft_turns_encrypted is None:
            # Persist an encrypted structured empty source so a later clinician
            # commit can acquire the same protected-warning invariant.
            from app.services.structured_transcripts import _encrypt_current_turns
            _encrypt_current_turns(db, transcript=transcript, turns=[])
    combined = _refresh_partial_source_warnings(db, transcript=transcript, turns=combined)
    from app.services.structured_transcripts import _encrypt_current_turns
    _encrypt_current_turns(db, transcript=transcript, turns=combined)
    draft_text = canonical_transcript_projection(combined)
    version = None
    if has_new_clinical_text or has_prior_clinical_text:
        version = _create_transcript_version_from_text(
            db, transcript=transcript,
            owner_user_id=transcript.owner_user_id,
            plaintext=draft_text,
        )
        store_turns_on_version(
            db, version=version, owner_user_id=transcript.owner_user_id,
            turns=combined,
        )
    from app.services.transcripts import _resolved_transcript_status
    transcript.status = _resolved_transcript_status(db, transcript=transcript)
    db.add(session)
    db.add(transcript)
    db.commit()
    if version is not None:
        _trigger_waiting_generation_dispatches(db, transcript_id=transcript.id)
        _attempt_preview_redaction(db, transcript_version=version)
    return True


def use_available_telehealth_capture(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    capture_session_id: UUID,
) -> TelehealthCaptureSession:
    """Owner-authorized acceptance of a submitted failed capture's available source."""
    from app.services.consultation_split_locks import lock_consultation_split_source_scope
    from app.services.structured_transcripts import (
        _encrypt_current_turns,
        _refresh_partial_source_warnings,
        _normalize_turn,
        canonical_transcript_projection,
        load_current_turns,
        store_turns_on_version,
    )
    from app.services.transcripts import (
        _attempt_preview_redaction,
        _create_transcript_version_from_text,
        _resolved_transcript_status,
        transcript_draft_text,
        transcript_is_expired,
    )

    scope = lock_consultation_split_source_scope(
        db, owner_user_id=owner.id, transcript_id=transcript_id,
    )
    if scope is None or scope.owner.id != owner.id or transcript_is_expired(scope.transcript):
        raise AppError(404, "not_found", "Telehealth capture was not found.")
    transcript = scope.transcript
    if owner.is_system_admin or transcript.team_id != owner.team_id:
        raise AppError(404, "not_found", "Telehealth capture was not found.")
    session = db.scalar(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.id == capture_session_id,
        TelehealthCaptureSession.transcript_id == transcript.id,
        TelehealthCaptureSession.owner_user_id == owner.id,
        TelehealthCaptureSession.team_id == transcript.team_id,
    ).with_for_update())
    if session is None:
        raise AppError(404, "not_found", "Telehealth capture was not found.")
    if session.status in ("partial", "completed"):
        return session
    if session.status != "failed":
        raise AppError(409, "conflict", "Only a failed submitted Telehealth capture can use available results.")
    parts = list(db.scalars(select(TelehealthCapturePart).where(
        TelehealthCapturePart.session_id == session.id,
        TelehealthCapturePart.transcript_id == transcript.id,
        TelehealthCapturePart.owner_user_id == owner.id,
    ).order_by(TelehealthCapturePart.id).with_for_update()))
    if not parts or not any(part.ingestion_job_id is not None for part in parts):
        raise AppError(409, "conflict", "This capture has no submitted results to use.")
    db.add(session)
    session.status = "partial"
    session.incomplete_acknowledged_at = session.incomplete_acknowledged_at or utcnow()
    session.updated_at = utcnow()
    db.flush()
    transcript.status = _resolved_transcript_status(db, transcript=transcript)
    db.add(transcript)
    # The nested apply reacquires the owner/root writer scope with
    # populate_existing=True. Persist the resolved state first so that a
    # no-ready-result fallback cannot reload the old failed root status.
    db.flush()
    # Keep the canonical source-writer lock until all already-completed parts
    # are either applied atomically or the durable partial acceptance commits.
    if apply_completed_telehealth_capture(db, capture_session_id=session.id):
        db.refresh(session)
        return session

    current = load_current_turns(db, transcript=transcript)
    if current is None:
        prior_text = transcript_draft_text(db, transcript=transcript) or ""
        current = ([_normalize_turn({
            "scope_id": uuid4(), "speaker_id": None, "text": prior_text,
        })] if prior_text.strip() else [])
    has_clinical_text = any(
        turn.get("text", "").strip() and not turn.get("source_warning") for turn in current
    )
    if has_clinical_text:
        current = _refresh_partial_source_warnings(db, transcript=transcript, turns=current)
        _encrypt_current_turns(db, transcript=transcript, turns=current)
        projection = canonical_transcript_projection(current)
        version = _create_transcript_version_from_text(
            db, transcript=transcript, owner_user_id=owner.id, plaintext=projection,
        )
        store_turns_on_version(db, version=version, owner_user_id=owner.id, turns=current)
    else:
        # The empty structured source prevents a later plain-text commit from
        # bypassing the same accepted-partial warning invariant.
        _encrypt_current_turns(db, transcript=transcript, turns=[])
        version = None
    db.add(session)
    db.add(transcript)
    db.commit()
    if version is not None:
        _attempt_preview_redaction(db, transcript_version=version)
    db.refresh(session)
    return session



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
    session = TelehealthCaptureSession
    part = TelehealthCapturePart
    job = TranscriptIngestionJob
    ready_parts = exists(select(part.id).where(
        part.session_id == session.id,
        part.status == "transcribed",
        part.ingestion_job_id.is_not(None),
    ))
    bad_part = exists(select(part.id).outerjoin(
        job, job.id == part.ingestion_job_id,
    ).where(
        part.session_id == session.id,
        or_(
            part.status != "transcribed",
            part.ingestion_job_id.is_(None),
            job.id.is_(None),
            job.transcript_id != part.transcript_id,
            job.owner_user_id != part.owner_user_id,
            job.status != TranscriptIngestionJobStatus.completed,
            job.result_text_encrypted.is_(None),
        ),
    ))
    clinician_part = exists(select(part.id).where(
        part.session_id == session.id, part.channel == "clinician",
    ))
    patient_part = exists(select(part.id).where(
        part.session_id == session.id, part.channel == "patient",
    ))
    unapplied_ready_part = exists(select(part.id).join(
        job, job.id == part.ingestion_job_id,
    ).where(
        part.session_id == session.id,
        part.status == "transcribed",
        job.status == TranscriptIngestionJobStatus.completed,
        job.result_text_encrypted.is_not(None),
        job.applied_at.is_(None),
    ))
    now = utcnow()
    ids = list(db.scalars(
        select(session.id)
        .join(Transcript, and_(
            Transcript.id == session.transcript_id,
            Transcript.owner_user_id == session.owner_user_id,
            Transcript.team_id == session.team_id,
            Transcript.retention_expires_at > now,
        ))
        .join(User, and_(
            User.id == session.owner_user_id,
            User.team_id == session.team_id,
            User.is_system_admin.is_(False),
        ))
        .where(
            or_(
                and_(
                    session.status == "processing",
                    ready_parts,
                    not_(bad_part),
                    or_(and_(clinician_part, patient_part), and_(session.failure_code.is_not(None), session.incomplete_acknowledged_at.is_not(None))),
                ),
                and_(session.status == "partial", unapplied_ready_part),
            ),
        )
        .order_by(session.updated_at.asc(), session.id.asc())
        .limit(batch_size)
    ))
    db.rollback()
    applied = 0
    for capture_id in ids:
        try:
            if apply_completed_telehealth_capture(db, capture_session_id=capture_id):
                applied += 1
            else:
                db.rollback()
                _best_effort_rotate_reconciliation_candidate(db, capture_id)
        except Exception:
            db.rollback()
            _best_effort_rotate_reconciliation_candidate(db, capture_id)
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


def _best_effort_rotate_reconciliation_candidate(db: Session, capture_session_id: UUID) -> None:
    try:
        _rotate_reconciliation_candidate(db, capture_session_id)
    except Exception:
        db.rollback()


def _rotate_reconciliation_candidate(db: Session, capture_session_id: UUID) -> None:
    """Move a failed ready candidate behind peers using the established lock order."""
    identity = db.get(TelehealthCaptureSession, capture_session_id)
    if identity is None:
        return
    owner = db.scalar(select(User).where(User.id == identity.owner_user_id).with_for_update())
    if owner is None:
        db.rollback()
        return
    try:
        from app.services.telehealth_capture import _owner_capture_session_locked
        _, locked_session = _owner_capture_session_locked(
            db, owner, transcript_id=identity.transcript_id,
            capture_session_id=capture_session_id,
        )
    except AppError:
        db.rollback()
        return
    if locked_session.status in ("processing", "partial"):
        locked_session.updated_at = utcnow()
        db.add(locked_session)
        db.commit()
    else:
        db.rollback()
