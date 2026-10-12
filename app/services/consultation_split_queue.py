"""Atomic initial queue/cache boundary for consultation split analysis.

This service does not resolve credentials, invoke an LLM, claim a task, retry
work, parse a result, or create drafts.  It commits only a new analysis,
reserved provider attempt, and durable outbox intent before asking the
best-effort dispatcher to publish it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    AttemptStatus,
    ProviderAttempt,
    User,
    utcnow,
)
from app.services.consultation_split_analysis import prepare_split_analysis_request
from app.services.consultation_split_sources import (
    PreparedConsultationSplitAnalysis,
    find_cached_prepared_consultation_split_analysis,
    prepare_source_bound_consultation_split_analysis,
)
from app.services.consultation_splits import (
    create_split_analysis,
    queue_split_execution,
    read_split_analysis_json,
    read_split_execution_json,
    _validate_split_execution_for_reuse,
)
from app.services.llm import resolve_user_llm
from app.services.llm_adapters.runtime import build_provider_snapshot
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.preferences import consultation_splitting_enabled
from app.services.task_outbox import try_publish_task_dispatch_safely


SPLIT_ANALYSIS_RESERVATION_SECONDS = 1_500
QueueSplitAnalysisOutcome = Literal[
    "disabled",
    "ready",
    "not_required",
    "queued",
    "processing",
    "failed",
    "incomplete",
    "stale_conflict",
]


@dataclass(frozen=True, slots=True)
class QueueOrReuseSplitAnalysisResult:
    """Content-safe queue decision; persisted content stays encrypted."""

    outcome: QueueSplitAnalysisOutcome
    analysis: ConsultationSplitAnalysis | None = None
    execution: ConsultationSplitExecution | None = None
    created_new_work: bool = False

    @property
    def queued_new_work(self) -> bool:
        return self.created_new_work


@dataclass(frozen=True, slots=True)
class _PreparedSplitAnalysisQueueDecision:
    """Uncommitted queue result for a source that was prepared already.

    The task identifier is deliberately private to this module's callers.  A
    caller may publish it only after committing the analysis, execution,
    reservation, and durable outbox row together.
    """

    result: QueueOrReuseSplitAnalysisResult
    dispatch_task_id: UUID | None = None


def _locked_analysis_executions(
    db: Session,
    *,
    analysis_id: UUID,
) -> tuple[ConsultationSplitAnalysis | None, list[ConsultationSplitExecution]]:
    """Lock execution rows before their analysis parent.

    Worker pre-submit takes the same order after the owner/transcript source
    scope.  Cache/replay callers must already hold that source scope; keeping
    execution before analysis avoids a deadlock with a worker that has claimed
    an execution and is about to validate its parent.
    """
    executions = db.scalars(
        select(ConsultationSplitExecution)
        .where(
            ConsultationSplitExecution.analysis_id == analysis_id,
            ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.analysis,
        )
        .order_by(ConsultationSplitExecution.attempt_no, ConsultationSplitExecution.id)
        .with_for_update()
    ).all()
    analysis = db.scalar(
        select(ConsultationSplitAnalysis)
        .where(ConsultationSplitAnalysis.id == analysis_id)
        .with_for_update()
    )
    return analysis, executions


def _existing_execution_is_complete_work(
    db: Session,
    *,
    actor: User,
    analysis: ConsultationSplitAnalysis,
    executions: list[ConsultationSplitExecution],
) -> ConsultationSplitExecution | None:
    """Return the queue-safe latest execution, never repairing passive rows.

    Passive incomplete rows are deliberately fail-closed.  Attaching work to
    one could pair a reservation with unknown historical encrypted metadata.
    A later explicit repair/migration can make that decision with evidence.
    """
    if not executions:
        return None
    execution = executions[-1]
    if any(
        earlier.status not in {
            ConsultationSplitExecutionStatus.completed,
            ConsultationSplitExecutionStatus.failed,
            ConsultationSplitExecutionStatus.cancelled,
        }
        for earlier in executions[:-1]
    ):
        return None
    if analysis.status is ConsultationSplitAnalysisStatus.queued:
        expected_execution_status = ConsultationSplitExecutionStatus.queued
        expected_attempt_status = AttemptStatus.reserved
    elif analysis.status is ConsultationSplitAnalysisStatus.processing:
        expected_execution_status = ConsultationSplitExecutionStatus.processing
        expected_attempt_status = AttemptStatus.submitted
    else:
        return None
    if not _validate_split_execution_for_reuse(
        db,
        execution_id=execution.id,
        expected_execution_status=expected_execution_status,
        expected_attempt_status=expected_attempt_status,
    ):
        return None
    return execution


def _retry_failed_prepared_split_analysis(
    db: Session,
    actor: User,
    *,
    analysis: ConsultationSplitAnalysis,
) -> _PreparedSplitAnalysisQueueDecision:
    """Queue a clinician-requested next attempt using the exact frozen input."""
    locked_analysis, executions = _locked_analysis_executions(db, analysis_id=analysis.id)
    if locked_analysis is None or locked_analysis.status is not ConsultationSplitAnalysisStatus.failed:
        return _PreparedSplitAnalysisQueueDecision(_reuse_result(db, actor=actor, analysis=analysis))
    if not executions or any(
        execution.status not in {
            ConsultationSplitExecutionStatus.completed,
            ConsultationSplitExecutionStatus.failed,
            ConsultationSplitExecutionStatus.cancelled,
        }
        for execution in executions
    ):
        return _PreparedSplitAnalysisQueueDecision(QueueOrReuseSplitAnalysisResult("incomplete", analysis=locked_analysis))
    for prior in executions:
        attempt = db.scalar(select(ProviderAttempt).where(
            ProviderAttempt.consultation_split_execution_id == prior.id,
        ).with_for_update())
        if attempt is None or attempt.status not in {AttemptStatus.cancelled, AttemptStatus.settled}:
            return _PreparedSplitAnalysisQueueDecision(QueueOrReuseSplitAnalysisResult("incomplete", analysis=locked_analysis))
    latest = executions[-1]
    if latest.status is ConsultationSplitExecutionStatus.completed or latest.llm_config_id is None:
        return _PreparedSplitAnalysisQueueDecision(QueueOrReuseSplitAnalysisResult("incomplete", analysis=locked_analysis))
    source = read_split_analysis_json(db, actor, analysis=locked_analysis, field="source_snapshot_encrypted")
    candidates = read_split_analysis_json(db, actor, analysis=locked_analysis, field="candidate_template_snapshot_encrypted")
    provider = read_split_analysis_json(db, actor, analysis=locked_analysis, field="provider_snapshot_encrypted")
    if not isinstance(source, dict) or not isinstance(candidates, dict) or not isinstance(provider, dict):
        return _PreparedSplitAnalysisQueueDecision(QueueOrReuseSplitAnalysisResult("incomplete", analysis=locked_analysis))
    try:
        frozen_provider = LlmProviderSnapshot(**provider)
        frozen_config_id = UUID(frozen_provider.llm_config_id)
        request = prepare_split_analysis_request(
            owner_user_id=actor.id,
            provider_snapshot=frozen_provider,
            source_snapshot=source,
            candidate_template_snapshot=candidates,
        )
        latest_provider = read_split_execution_json(
            db, actor, execution=latest, field="provider_snapshot_encrypted"
        )
    except (AppError, TypeError, ValueError):
        return _PreparedSplitAnalysisQueueDecision(QueueOrReuseSplitAnalysisResult("incomplete", analysis=locked_analysis))
    if (
        latest.llm_config_id != frozen_config_id
        or latest.provider_adapter != frozen_provider.adapter_kind
        or latest.provider_base_url != frozen_provider.base_url
        or latest.provider_model != frozen_provider.model
        or latest_provider != provider
    ):
        return _PreparedSplitAnalysisQueueDecision(QueueOrReuseSplitAnalysisResult("incomplete", analysis=locked_analysis))
    execution, _attempt, dispatch = queue_split_execution(
        db,
        actor,
        kind=ConsultationSplitExecutionKind.analysis,
        reserved_units=request.reservation_units,
        reservation_valid_until=utcnow() + timedelta(seconds=SPLIT_ANALYSIS_RESERVATION_SECONDS),
        analysis=locked_analysis,
        llm_config_id=latest.llm_config_id,
        provider_snapshot=provider,
        request_payload=request.request_body,
        expected_model=frozen_provider.model,
    )
    # A prior Create intent may still be waiting for its progress task. It
    # belongs to the failed attempt and must never consume this new result.
    prior_intents = db.scalars(select(ConsultationSplitIntent).where(
        ConsultationSplitIntent.analysis_id == locked_analysis.id,
        ConsultationSplitIntent.status == ConsultationSplitIntentStatus.analysis_pending,
    ).order_by(ConsultationSplitIntent.id).with_for_update()).all()
    for prior_intent in prior_intents:
        prior_intent.status = ConsultationSplitIntentStatus.failed
        prior_intent.error_code = "consultation_split_analysis_unavailable"
        prior_intent.completed_at = utcnow()
    locked_analysis.status = ConsultationSplitAnalysisStatus.queued
    locked_analysis.error_code = None
    locked_analysis.completed_at = None
    return _PreparedSplitAnalysisQueueDecision(
        QueueOrReuseSplitAnalysisResult("queued", analysis=locked_analysis, execution=execution, created_new_work=True),
        dispatch_task_id=dispatch.task_id,
    )


def _reuse_result(
    db: Session,
    *,
    actor: User,
    analysis: ConsultationSplitAnalysis,
) -> QueueOrReuseSplitAnalysisResult:
    """Map only exact current, non-stale cache rows to safe public outcomes."""
    locked_analysis, executions = _locked_analysis_executions(db, analysis_id=analysis.id)
    if locked_analysis is None:
        return QueueOrReuseSplitAnalysisResult("stale_conflict")
    analysis = locked_analysis
    if analysis.status is ConsultationSplitAnalysisStatus.ready:
        if not analysis.proposal_encrypted:
            return QueueOrReuseSplitAnalysisResult("incomplete", analysis=analysis)
        return QueueOrReuseSplitAnalysisResult("ready", analysis=analysis)
    if analysis.status is ConsultationSplitAnalysisStatus.not_required:
        if not analysis.proposal_encrypted:
            return QueueOrReuseSplitAnalysisResult("incomplete", analysis=analysis)
        return QueueOrReuseSplitAnalysisResult("not_required", analysis=analysis)
    if analysis.status is ConsultationSplitAnalysisStatus.failed:
        return QueueOrReuseSplitAnalysisResult("failed", analysis=analysis)
    if analysis.status in {
        ConsultationSplitAnalysisStatus.queued,
        ConsultationSplitAnalysisStatus.processing,
    }:
        execution = _existing_execution_is_complete_work(
            db,
            actor=actor,
            analysis=analysis,
            executions=executions,
        )
        if execution is None:
            return QueueOrReuseSplitAnalysisResult("incomplete", analysis=analysis)
        return QueueOrReuseSplitAnalysisResult(analysis.status.value, analysis=analysis, execution=execution)
    # Cache lookup excludes stale rows. Keep this defensive response for an
    # in-memory status transition during a future implementation change.
    return QueueOrReuseSplitAnalysisResult("stale_conflict", analysis=analysis)


def _duplicate_result(
    db: Session,
    *,
    actor: User,
    prepared: PreparedConsultationSplitAnalysis,
) -> QueueOrReuseSplitAnalysisResult:
    """Read the canonical row after an insert race without reserving again."""
    canonical = find_cached_prepared_consultation_split_analysis(db, actor, prepared=prepared)
    if canonical is None:
        # A historical stale row can own the same fingerprint after sources
        # revert. This slice never revives it or creates a second reservation.
        return QueueOrReuseSplitAnalysisResult("stale_conflict")
    return _reuse_result(db, actor=actor, analysis=canonical)


def _queue_or_reuse_prepared_split_analysis(
    db: Session,
    actor: User,
    *,
    prepared: PreparedConsultationSplitAnalysis,
    retry_failed_analysis: bool = False,
) -> _PreparedSplitAnalysisQueueDecision:
    """Return an uncommitted cache/queue decision for a prepared source.

    Source preparation is intentionally outside this transaction-owned core:
    it can run the required redaction boundary and commit its own durable
    redaction state.  Every newly-created analysis row, execution, quota
    reservation, and outbox row below remains uncommitted for the caller to
    combine with another root record, such as an idempotent Generate intent.
    """
    cached = find_cached_prepared_consultation_split_analysis(db, actor, prepared=prepared)
    if cached is not None:
        if retry_failed_analysis and cached.status is ConsultationSplitAnalysisStatus.failed:
            return _retry_failed_prepared_split_analysis(db, actor, analysis=cached)
        return _PreparedSplitAnalysisQueueDecision(_reuse_result(db, actor=actor, analysis=cached))

    _selection, config, resolved_model, _preference = resolve_user_llm(db, actor)
    if not isinstance(resolved_model, str) or not resolved_model.strip():
        raise AppError(422, "consultation_split_llm_model_unavailable", "No LLM model is available for consultation splitting")
    resolved_model = resolved_model.strip()
    provider_snapshot = build_provider_snapshot(config=config, model=resolved_model).to_dict()
    request = prepare_split_analysis_request(
        owner_user_id=actor.id,
        provider_snapshot=build_provider_snapshot(config=config, model=resolved_model),
        source_snapshot=prepared.source_snapshot,
        candidate_template_snapshot=prepared.candidate_template_snapshot,
    )
    dispatch_task_id = None
    try:
        with db.begin_nested():
            state = prepared.source_state
            analysis = create_split_analysis(
                db,
                actor,
                transcript_id=state.transcript_id,
                source_fingerprint=state.source_fingerprint,
                transcript_version_id=state.transcript_version_id,
                redaction_run_id=state.redaction_run_id,
                source_snapshot=prepared.source_snapshot,
                candidate_template_snapshot=prepared.candidate_template_snapshot,
                provider_snapshot=provider_snapshot,
            )
            execution, _attempt, dispatch = queue_split_execution(
                db,
                actor,
                kind=ConsultationSplitExecutionKind.analysis,
                reserved_units=request.reservation_units,
                reservation_valid_until=utcnow() + timedelta(seconds=SPLIT_ANALYSIS_RESERVATION_SECONDS),
                analysis=analysis,
                llm_config_id=config.id,
                provider_snapshot=provider_snapshot,
                request_payload=request.request_body,
                expected_model=resolved_model,
            )
            dispatch_task_id = dispatch.task_id
    except IntegrityError as exc:
        diagnostic = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if diagnostic != "uq_consultation_split_analyses_owner_source":
            raise
        # Only the inner savepoint rolls back. The canonical row remains
        # available for a locked reread and no second attempt/outbox is made.
        result = _duplicate_result(db, actor=actor, prepared=prepared)
        return _PreparedSplitAnalysisQueueDecision(result)

    assert dispatch_task_id is not None
    return _PreparedSplitAnalysisQueueDecision(
        QueueOrReuseSplitAnalysisResult("queued", analysis=analysis, execution=execution, created_new_work=True),
        dispatch_task_id=dispatch_task_id,
    )


def queue_or_reuse_split_analysis(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
) -> QueueOrReuseSplitAnalysisResult:
    """Queue one initial split analysis or safely reuse its exact source row.

    The opt-in check intentionally runs before transcript lookup, source
    preparation, decryption, redaction, provider selection, or any write.
    Source preparation releases locks for provider-bound redaction, then
    re-locks and proves the complete source state before a reservation can be
    created.  A later worker still rechecks its source before submit because
    writes can occur after this initial reservation.
    """
    if not consultation_splitting_enabled(db, actor):
        return QueueOrReuseSplitAnalysisResult("disabled")

    prepared = prepare_source_bound_consultation_split_analysis(
        db,
        actor,
        transcript_id=transcript_id,
    )
    decision = _queue_or_reuse_prepared_split_analysis(db, actor, prepared=prepared)

    # Preserve the established public boundary: the cache decision is made in
    # one transaction, then best-effort publish happens only after its commit.
    db.commit()
    if decision.dispatch_task_id is not None:
        try_publish_task_dispatch_safely(decision.dispatch_task_id)
    return decision.result
