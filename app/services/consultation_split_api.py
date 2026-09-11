"""Owner-only, content-safe consultation split API projections.

This module is deliberately read-only apart from the existing queue service.
The workspace projection never prepares redaction, resolves credentials, calls a
provider, retries, or changes persisted lifecycle state.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (ConsultationSplitAnalysis, ConsultationSplitAnalysisStatus, ConsultationSplitBatch,
    ConsultationSplitBatchStatus, ConsultationSplitBatchTopic, ConsultationSplitExecution,
    ConsultationSplitExecutionKind, ConsultationSplitExecutionStatus, ConsultationSplitTopicDisposition,
    ConsultationSplitTopicOutcome, ConsultationSplitTopicOutcomeStatus, GeneratedDocument, Transcript, User)
from app.schemas.consultation_split import ConsultationSplitAnalysisDetail, ConsultationSplitBatchDetail, ConsultationSplitTopicDetail
from app.schemas.consultation_split import ConsultationSplitIntentStartResponse
from app.services.consultation_split_analysis import ValidatedSplitAnalysis
from app.services.consultation_split_intents import CreateOrReplayConsultationSplitIntentResult
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_queue import queue_or_reuse_split_analysis
from app.services.consultation_split_sources import current_consultation_split_analysis_source_matches
from app.services.consultation_splits import read_split_analysis_json
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled
from app.services.transcripts import get_active_owner_transcript, transcript_is_expired


_SAFE_ERROR_CODES = {
    "consultation_split_analysis_candidates_invalid",
    "consultation_split_analysis_input_invalid",
    "consultation_split_analysis_input_too_large",
    "consultation_split_analysis_invalid",
    "consultation_split_analysis_invalid_output",
    "consultation_split_analysis_provider_invalid",
    "consultation_split_analysis_request_invalid",
    "consultation_split_analysis_unavailable",
    "consultation_split_credential_unavailable",
    "consultation_split_llm_model_unavailable",
    "consultation_split_pre_submit_transaction_active",
    "consultation_split_preference_disabled",
    "consultation_split_provider_binding_invalid",
    "consultation_split_provider_config_invalid",
    "consultation_split_provider_failed",
    "consultation_split_provider_snapshot_metadata_mismatch",
    "consultation_split_provider_snapshot_mismatch",
    "consultation_split_proposal_unavailable",
    "consultation_split_request_payload_invalid",
    "consultation_split_reservation_expired",
    "consultation_split_retention_binding_invalid",
    "consultation_split_runtime_transaction_active",
    "consultation_split_source_empty",
    "consultation_split_source_expired",
    "consultation_split_source_stale",
}


def _safe_error_code(value: object, *, fallback: str | None = None) -> str | None:
    if isinstance(value, str) and value in _SAFE_ERROR_CODES:
        return value
    return fallback


def _disabled() -> AppError:
    return AppError(403, "consultation_split_disabled", "Consultation splitting is not enabled")


def _projection(
    db: Session,
    actor: User,
    analysis: ConsultationSplitAnalysis,
    *,
    forced_status: str | None = None,
    forced_error_code: str | None = None,
) -> ConsultationSplitAnalysisDetail:
    """Project one already owner-scoped row without exposing encrypted metadata."""
    status = forced_status or analysis.status.value
    error_code = _safe_error_code(analysis.error_code)
    if forced_error_code is not None:
        error_code = _safe_error_code(forced_error_code, fallback=forced_error_code)
    topics: list[ConsultationSplitTopicDetail] = []

    if status in {"ready", "not_required"}:
        try:
            proposal = read_split_analysis_json(db, actor, analysis=analysis, field="proposal_encrypted")
            validated = ValidatedSplitAnalysis.model_validate(proposal)
            topics = [
                ConsultationSplitTopicDetail.model_validate(topic.model_dump(mode="python"))
                for topic in validated.topics
            ]
        except (AppError, ValidationError, TypeError, ValueError, UnicodeDecodeError):
            # A damaged owner-encrypted proposal must not become a response
            # error or leak recoverable provider output.  No state is changed.
            status = "incomplete"
            error_code = "consultation_split_proposal_unavailable"

    return ConsultationSplitAnalysisDetail(
        analysis_id=analysis.id,
        status=status,  # type: ignore[arg-type]
        error_code=error_code,
        updated_at=analysis.updated_at,
        completed_at=analysis.completed_at,
        topics=topics,
    )


def queue_split_analysis_api(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
) -> ConsultationSplitAnalysisDetail:
    """Queue or reuse one analysis after both deployment and owner gates pass."""
    if not consultation_splitting_feature_enabled():
        raise _disabled()
    if not consultation_splitting_enabled(db, actor):
        raise _disabled()
    result = queue_or_reuse_split_analysis(db, actor, transcript_id=transcript_id)
    if result.outcome == "disabled":
        raise _disabled()
    if result.outcome == "incomplete":
        if result.analysis is None:
            return ConsultationSplitAnalysisDetail(
                status="incomplete",
                error_code="consultation_split_analysis_unavailable",
            )
        return _projection(
            db,
            actor,
            result.analysis,
            forced_status="incomplete",
            forced_error_code="consultation_split_analysis_unavailable",
        )
    if result.analysis is None:
        status = "stale" if result.outcome == "stale_conflict" else "incomplete"
        return ConsultationSplitAnalysisDetail(
            status=status,  # type: ignore[arg-type]
            error_code=(
                "consultation_split_source_stale"
                if result.outcome == "stale_conflict"
                else "consultation_split_analysis_unavailable"
            ),
        )
    return _projection(db, actor, result.analysis)


def project_consultation_split_intent_start(
    db: Session,
    actor: User,
    *,
    result: CreateOrReplayConsultationSplitIntentResult,
) -> ConsultationSplitIntentStartResponse:
    """Return only the intent identity and existing safe analysis projection.

    The atomic intent service owns gate, replay, source, encryption, quota, and
    outbox rules.  This route-facing adapter deliberately only maps its result;
    in particular it must not repeat the gate before a same-key replay.
    """
    if result.analysis is None:
        analysis = ConsultationSplitAnalysisDetail(
            status=("stale" if result.analysis_outcome == "stale_conflict" else "incomplete"),
            error_code=(
                "consultation_split_source_stale"
                if result.analysis_outcome == "stale_conflict"
                else "consultation_split_analysis_unavailable"
            ),
        )
    elif result.analysis_outcome == "incomplete":
        analysis = _projection(
            db,
            actor,
            result.analysis,
            forced_status="incomplete",
            forced_error_code="consultation_split_analysis_unavailable",
        )
    elif result.analysis_outcome == "stale_conflict":
        analysis = _projection(
            db,
            actor,
            result.analysis,
            forced_status="stale",
            forced_error_code="consultation_split_source_stale",
        )
    else:
        analysis = _projection(db, actor, result.analysis)

    return ConsultationSplitIntentStartResponse(
        intent_id=result.intent.id if result.intent is not None else None,
        idempotency_replayed=result.intent is not None and not result.created_new_intent,
        analysis=analysis,
    )


def _candidate_rows(
    db: Session,
    *,
    actor: User,
    transcript: Transcript,
) -> list[ConsultationSplitAnalysis]:
    return db.scalars(
        select(ConsultationSplitAnalysis)
        .where(
            ConsultationSplitAnalysis.owner_user_id == actor.id,
            ConsultationSplitAnalysis.team_id == actor.team_id,
            ConsultationSplitAnalysis.transcript_id == transcript.id,
        )
        .order_by(ConsultationSplitAnalysis.updated_at.desc(), ConsultationSplitAnalysis.id.desc())
    ).all()


def read_workspace_split_analysis(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
) -> ConsultationSplitAnalysisDetail | None:
    """Return current owner state without redaction, provider, retry, or writes."""
    try:
        if not consultation_splitting_enabled(db, actor):
            return None
        # Match the runtime's canonical owner -> transcript -> dictation lock
        # chain before recomputing the read-only current-source fingerprint.
        get_active_owner_transcript(db, actor, transcript_id=transcript_id)
        scope = lock_consultation_split_source_scope(
            db,
            owner_user_id=actor.id,
            transcript_id=transcript_id,
        )
        if scope is None:
            return None
        actor = scope.owner
        transcript = scope.transcript
        if transcript_is_expired(transcript):
            return None
        rows = _candidate_rows(db, actor=actor, transcript=transcript)
    except (AppError, UnicodeDecodeError):
        return None
    if not rows:
        return None

    fallback: ConsultationSplitAnalysis | None = None
    for analysis in rows:
        if analysis.status is ConsultationSplitAnalysisStatus.stale:
            fallback = fallback or analysis
            continue
        try:
            matches = current_consultation_split_analysis_source_matches(
                db, actor, transcript=transcript, analysis=analysis
            )
        except Exception:
            matches = False
        if matches:
            return _projection(db, actor, analysis)
        fallback = fallback or analysis

    if fallback is None:
        return None
    return _projection(
        db,
        actor,
        fallback,
        forced_status="stale",
        forced_error_code="consultation_split_source_stale",
    )


def read_workspace_split_batch(db: Session, actor: User, *, transcript_id: UUID) -> ConsultationSplitBatchDetail | None:
    """Return actions derived solely from locked durable batch state."""
    batch = db.scalar(select(ConsultationSplitBatch).where(
        ConsultationSplitBatch.owner_user_id == actor.id,
        ConsultationSplitBatch.team_id == actor.team_id,
        ConsultationSplitBatch.transcript_id == transcript_id,
    ).order_by(ConsultationSplitBatch.updated_at.desc()).limit(1))
    if batch is None:
        return None
    topics = db.scalars(select(ConsultationSplitBatchTopic).where(ConsultationSplitBatchTopic.batch_id == batch.id)).all()
    outcomes = {row.batch_topic_id: row for row in db.scalars(select(ConsultationSplitTopicOutcome).where(
        ConsultationSplitTopicOutcome.batch_topic_id.in_([topic.id for topic in topics])
    )).all()}
    failed = [topic for topic in topics if outcomes.get(topic.id) and outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.failed]
    validated = [topic for topic in topics if outcomes.get(topic.id) and outcomes[topic.id].status is ConsultationSplitTopicOutcomeStatus.validated]
    active = db.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == batch.id,
        ConsultationSplitExecution.kind.in_([ConsultationSplitExecutionKind.generation, ConsultationSplitExecutionKind.verification]),
        ConsultationSplitExecution.status.in_([ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing]),
    ).order_by(ConsultationSplitExecution.attempt_no.desc()).limit(1))
    partial = batch.status is ConsultationSplitBatchStatus.partially_ready
    documents_by_topic = {
        document.consultation_split_batch_topic_id: document.id
        for document in db.scalars(select(GeneratedDocument).where(
            GeneratedDocument.consultation_split_batch_topic_id.in_([topic.id for topic in topics])
        )).all()
    }
    primary = next((topic for topic in topics if topic.is_primary), None)
    preferred_document_id = documents_by_topic.get(primary.id) if primary is not None else None
    if preferred_document_id is None:
        preferred_document_id = next(
            (documents_by_topic.get(topic.id) for topic in topics if documents_by_topic.get(topic.id) is not None),
            None,
        )
    return ConsultationSplitBatchDetail(
        batch_id=batch.id, status=batch.status.value, failed_topic_count=len(failed),
        validated_topic_count=len(validated), primary_failed=any(topic.is_primary for topic in failed),
        can_retry_missing=partial and bool(failed) and active is None,
        can_keep_available=partial and bool(validated) and active is None,
        active_execution_id=active.id if active else None,
        preferred_document_id=preferred_document_id,
        verification_status=batch.verification_status.value,
        verification_correction_count=batch.verification_correction_count,
    )
