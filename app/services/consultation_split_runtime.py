"""One-shot provider runtime for consultation-split *analysis* executions.

The queue owns creation of encrypted source/request snapshots, the quota
reservation, and durable dispatch intent.  ``prepare_queued_split_analysis``
then resolves credentials and proves the queued snapshot under locks.  This
module owns the remaining small state machine:

``queued/reserved -> processing/submitted -> ready|not_required|failed``.

There is deliberately no provider retry.  A submitted attempt without a
durable response is never invoked again: the provider call cannot be atomic
with PostgreSQL, so at-most-once submission wins over an uncertain retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    AttemptOutcome,
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ProviderAttempt,
    ProviderFeatureType,
    ProviderUsageEvent,
    ProviderUsageEventType,
    TaskDispatchKind,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    Transcript,
    User,
    utcnow,
)
from app.services.consultation_split_analysis import (
    CONSULTATION_SPLIT_MAX_RESPONSE_CHARS,
    CONSULTATION_SPLIT_MAX_CANDIDATES,
    parse_split_analysis_output,
)
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_pre_submit import (
    PrepareSplitAnalysisSubmissionResult,
    PreparedSplitAnalysisSubmission,
    prepare_queued_split_analysis_for_submission,
)
from app.services.consultation_split_sources import current_consultation_split_analysis_source_matches
from app.services.consultation_splits import read_split_analysis_json, read_split_execution_json
from app.services.content_crypto import encrypt_json_for_existing_owner
from app.services.llm_adapters import runtime as llm_runtime
from app.services.preferences import consultation_splitting_enabled
from app.services.provider_errors import safe_provider_error_code
from app.services.quotas import (
    cancel_provider_attempt,
    mark_provider_attempt_submitted,
    settle_provider_attempt_tokens,
    settle_provider_attempt_unknown_tokens,
)
from app.services.task_outbox import cancel_pending_task_dispatch
from app.services.transcripts import transcript_is_expired


# This is intentionally shorter than the reservation, but long enough for the
# existing provider transport timeouts.  A timeout closes the submitted quota
# attempt conservatively through the regular quota lifecycle worker.
SPLIT_ANALYSIS_PROVIDER_DEADLINE_SECONDS = 600
RECOVERABLE_RESPONSE_MAX_CHARS = CONSULTATION_SPLIT_MAX_RESPONSE_CHARS * 8
MAX_RECORDED_TOKENS = 10_000_000
MAX_RECORDED_DURATION_MS = 86_400_000

SplitAnalysisRuntimeOutcome = Literal[
    "ready",
    "not_required",
    "failed",
    "stale",
    "in_flight",
    "noop",
]


@dataclass(frozen=True, slots=True)
class SplitAnalysisRuntimeResult:
    """A metadata-only worker result; it never carries source or response text."""

    outcome: SplitAnalysisRuntimeOutcome
    execution_id: UUID
    error_code: str | None = None


@dataclass(slots=True)
class _LockedProcessingWork:
    owner: User
    transcript: Transcript
    execution: ConsultationSplitExecution
    analysis: ConsultationSplitAnalysis
    attempt: ProviderAttempt
    dispatch: TaskDispatchOutbox


def _safe_int(value: object, *, upper: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > upper:
        return None
    return value


def _normalized_usage(value: object) -> dict[str, int | None]:
    """Keep only small, numeric provider metadata in durable response state."""
    raw = value if isinstance(value, dict) else {}
    input_tokens = _safe_int(raw.get("input_tokens"), upper=MAX_RECORDED_TOKENS)
    output_tokens = _safe_int(raw.get("output_tokens"), upper=MAX_RECORDED_TOKENS)
    total_tokens = _safe_int(raw.get("total_tokens"), upper=MAX_RECORDED_TOKENS)
    duration_ms = _safe_int(raw.get("duration_ms"), upper=MAX_RECORDED_DURATION_MS)
    provider_duration_ms = _safe_int(raw.get("provider_duration_ms"), upper=MAX_RECORDED_DURATION_MS)
    # A total lower than a component cannot be trustworthy.  Do not invent a
    # replacement: absent total settles conservatively.
    if total_tokens is not None:
        if input_tokens is not None and output_tokens is not None:
            if total_tokens < input_tokens + output_tokens:
                total_tokens = None
        elif input_tokens is not None and total_tokens < input_tokens:
            total_tokens = None
        elif output_tokens is not None and total_tokens < output_tokens:
            total_tokens = None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "provider_duration_ms": provider_duration_ms,
    }


def _safe_http_status(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else None


def _safe_provider_error(exc: AppError) -> tuple[str | None, int | None]:
    details = exc.details if isinstance(exc.details, dict) else {}
    http_status = _safe_http_status(details.get("provider_http_status"))
    raw_code = details.get("provider_error_code")
    # AppError codes are application codes, not provider-owned metadata.  They
    # are deliberately not copied into provider_error_code.
    return safe_provider_error_code(raw_code, status_code=http_status) if raw_code is not None or http_status is not None else None, http_status


def _result_for_terminal(execution: ConsultationSplitExecution, analysis: ConsultationSplitAnalysis | None) -> SplitAnalysisRuntimeResult:
    if analysis is not None:
        if analysis.status is ConsultationSplitAnalysisStatus.ready:
            return SplitAnalysisRuntimeResult("ready", execution.id)
        if analysis.status is ConsultationSplitAnalysisStatus.not_required:
            return SplitAnalysisRuntimeResult("not_required", execution.id)
        if analysis.status is ConsultationSplitAnalysisStatus.stale:
            return SplitAnalysisRuntimeResult("stale", execution.id, analysis.error_code)
        if analysis.status is ConsultationSplitAnalysisStatus.failed:
            return SplitAnalysisRuntimeResult("failed", execution.id, analysis.error_code)
    return SplitAnalysisRuntimeResult("failed", execution.id, execution.error_code)


def _read_execution_state(db: Session, *, execution_id: UUID) -> tuple[ConsultationSplitExecutionStatus, ConsultationSplitAnalysisStatus | None, bool] | None:
    """Inspect state in an isolated read session so pre-submit sees a clean one."""
    with Session(bind=db.get_bind(), future=True) as read_db:
        execution = read_db.get(ConsultationSplitExecution, execution_id)
        if execution is None or execution.kind is not ConsultationSplitExecutionKind.analysis:
            return None
        analysis = read_db.get(ConsultationSplitAnalysis, execution.analysis_id) if execution.analysis_id else None
        return execution.status, analysis.status if analysis is not None else None, bool(execution.recoverable_response_encrypted)


def _is_durably_submitted_for_provider_call(db: Session, *, execution_id: UUID) -> bool:
    """Prove the full delivered/processing/submitted state in a clean session."""
    with Session(bind=db.get_bind(), future=True) as read_db:
        execution = read_db.get(ConsultationSplitExecution, execution_id)
        if (
            execution is None
            or execution.kind is not ConsultationSplitExecutionKind.analysis
            or execution.status is not ConsultationSplitExecutionStatus.processing
            or execution.analysis_id is None
            or execution.recoverable_response_encrypted is not None
        ):
            return False
        analysis = read_db.get(ConsultationSplitAnalysis, execution.analysis_id)
        attempt = read_db.scalar(
            select(ProviderAttempt).where(ProviderAttempt.consultation_split_execution_id == execution.id)
        )
        dispatch = read_db.scalar(
            select(TaskDispatchOutbox).where(
                TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
                TaskDispatchOutbox.source_id == execution.id,
            )
        )
        return bool(
            analysis is not None
            and analysis.status is ConsultationSplitAnalysisStatus.processing
            and attempt is not None
            and attempt.status is AttemptStatus.submitted
            and dispatch is not None
            and dispatch.dispatch_kind is TaskDispatchKind.consultation_split_analysis
            # Celery publish necessarily precedes the publisher's durable
            # state update. A fast worker may therefore see ``pending``. The
            # received task itself proves delivery; only failed/cancelled
            # outbox rows are invalid here.
            and dispatch.state in {TaskDispatchState.pending, TaskDispatchState.published}
        )


def _lock_processing_work(db: Session, *, execution_id: UUID, expected_status: ConsultationSplitExecutionStatus) -> _LockedProcessingWork | None:
    """Lock the exact owner-to-dispatch lineage for a single analysis execution."""
    identity = db.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.id == execution_id))
    if identity is None:
        return None
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=identity.owner_user_id,
        transcript_id=identity.transcript_id,
    )
    if scope is None:
        return None
    owner = scope.owner
    transcript = scope.transcript
    execution = db.scalar(
        select(ConsultationSplitExecution)
        .where(ConsultationSplitExecution.id == execution_id)
        .with_for_update()
    )
    if (
        execution is None
        or execution.kind is not ConsultationSplitExecutionKind.analysis
        or execution.status is not expected_status
        or execution.analysis_id is None
        or execution.batch_id is not None
    ):
        return None
    analysis = db.scalar(
        select(ConsultationSplitAnalysis)
        .where(ConsultationSplitAnalysis.id == execution.analysis_id)
        .with_for_update()
    )
    attempt = db.scalar(
        select(ProviderAttempt)
        .where(ProviderAttempt.consultation_split_execution_id == execution.id)
        .with_for_update()
    )
    dispatch = db.scalar(
        select(TaskDispatchOutbox)
        .where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
            TaskDispatchOutbox.source_id == execution.id,
        )
        .with_for_update()
    )
    if analysis is None or attempt is None or dispatch is None:
        return None
    if (
        owner.is_system_admin
        or owner.team_id is None
        or transcript.owner_user_id != owner.id
        or transcript.team_id != owner.team_id
        or execution.owner_user_id != owner.id
        or execution.team_id != owner.team_id
        or execution.transcript_id != transcript.id
        or analysis.owner_user_id != owner.id
        or analysis.team_id != owner.team_id
        or analysis.transcript_id != transcript.id
        or attempt.owner_user_id != owner.id
        or attempt.team_id != transcript.team_id
        or attempt.transcript_id != transcript.id
        or attempt.correlation_id != execution.id
        or attempt.attempt_number != 1
        or attempt.attempt_kind.value != "consultation_split_analysis"
        or dispatch.dispatch_kind is not TaskDispatchKind.consultation_split_analysis
        or dispatch.state not in {TaskDispatchState.pending, TaskDispatchState.published}
    ):
        return None
    return _LockedProcessingWork(owner, transcript, execution, analysis, attempt, dispatch)


def _retention_binding_matches(work: _LockedProcessingWork) -> bool:
    return (
        work.execution.retention_expires_at == work.transcript.retention_expires_at
        and work.analysis.retention_expires_at == work.transcript.retention_expires_at
    )


def _cancel_queued_locked(
    db: Session,
    *,
    work: _LockedProcessingWork,
    code: str,
    stale: bool = False,
) -> SplitAnalysisRuntimeResult:
    """Terminalize a definite pre-submit failure without recording provider use."""
    now = utcnow()
    if work.attempt.status is not AttemptStatus.reserved:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", work.execution.id)
    cancel_provider_attempt(db, attempt_id=work.attempt.id, now=now)
    if work.dispatch.state is TaskDispatchState.pending:
        cancel_pending_task_dispatch(db, task_id=work.dispatch.task_id)
    work.execution.status = ConsultationSplitExecutionStatus.cancelled if stale or code.endswith("expired") else ConsultationSplitExecutionStatus.failed
    work.execution.error_code = code
    work.execution.completed_at = now
    work.analysis.status = ConsultationSplitAnalysisStatus.stale if stale else ConsultationSplitAnalysisStatus.failed
    work.analysis.error_code = code
    work.analysis.completed_at = now
    db.commit()
    return SplitAnalysisRuntimeResult("stale" if stale else "failed", work.execution.id, code)


def _cancel_queued_when_preference_disabled(
    db: Session,
    *,
    execution_id: UUID,
) -> SplitAnalysisRuntimeResult | None:
    """Cancel queued work before credential/source work if its gate is off."""
    work = _lock_processing_work(
        db,
        execution_id=execution_id,
        expected_status=ConsultationSplitExecutionStatus.queued,
    )
    if work is None:
        db.rollback()
        return None
    if consultation_splitting_enabled(db, work.owner):
        db.rollback()
        return None
    return _cancel_queued_locked(
        db,
        work=work,
        code="consultation_split_preference_disabled",
    )


def _terminalize_processing_when_preference_disabled(
    db: Session,
    *,
    execution_id: UUID,
) -> SplitAnalysisRuntimeResult | None:
    """Suppress a submitted result when the clinician revokes the feature."""
    work = _lock_processing_work(
        db,
        execution_id=execution_id,
        expected_status=ConsultationSplitExecutionStatus.processing,
    )
    if work is None:
        db.rollback()
        return None
    if consultation_splitting_enabled(db, work.owner):
        db.rollback()
        return None
    if work.analysis.status is not ConsultationSplitAnalysisStatus.processing or work.attempt.status is not AttemptStatus.submitted:
        db.rollback()
        return None
    usage: dict[str, int | None] | None = None
    # A previous delivery may have committed the response but crashed before
    # finalization.  Preserve known quota metadata even while suppressing the
    # proposal; malformed encrypted recovery data still settles conservatively.
    if work.execution.recoverable_response_encrypted is not None:
        try:
            response = read_split_execution_json(
                db,
                work.owner,
                execution=work.execution,
                field="recoverable_response_encrypted",
            )
            if isinstance(response, dict):
                usage = _normalized_usage(response.get("usage"))
        except (AppError, UnicodeDecodeError):
            usage = None
    db.rollback()
    return _terminalize_submitted_failure(
        db,
        execution_id=execution_id,
        code="consultation_split_preference_disabled",
        usage=usage,
        suppress_proposal=True,
    )


def _submit_prepared(
    db: Session,
    prepared: PreparedSplitAnalysisSubmission,
) -> tuple[SplitAnalysisRuntimeResult, bool]:
    """Repeat every mutable gate immediately before durable submission."""
    work = _lock_processing_work(
        db,
        execution_id=prepared.execution_id,
        expected_status=ConsultationSplitExecutionStatus.queued,
    )
    if work is None:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", prepared.execution_id), False
    now = utcnow()
    if work.analysis.status is not ConsultationSplitAnalysisStatus.queued or work.attempt.id != prepared.attempt_id:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", prepared.execution_id), False
    # A prepared credential/request is not authority to submit after the
    # clinician turns splitting off in another tab.  This runs under the final
    # owner/transcript/execution locks, immediately before submission.
    if not consultation_splitting_enabled(db, work.owner):
        return _cancel_queued_locked(
            db,
            work=work,
            code="consultation_split_preference_disabled",
        ), False
    if not _retention_binding_matches(work):
        return _cancel_queued_locked(db, work=work, code="consultation_split_retention_binding_invalid"), False
    if transcript_is_expired(work.transcript, now=now):
        return _cancel_queued_locked(db, work=work, code="consultation_split_source_expired"), False
    if work.attempt.reservation_valid_until <= now:
        return _cancel_queued_locked(db, work=work, code="consultation_split_reservation_expired"), False
    if not current_consultation_split_analysis_source_matches(
        db, work.owner, transcript=work.transcript, analysis=work.analysis
    ):
        return _cancel_queued_locked(db, work=work, code="consultation_split_source_stale", stale=True), False
    # The prepared values came from the same locked transaction.  This check
    # prevents a future caller from passing content or a credential for another
    # execution into this private transition.
    try:
        request = read_split_execution_json(
            db, work.owner, execution=work.execution, field="request_payload_encrypted"
        )
    except AppError:
        return _cancel_queued_locked(db, work=work, code="consultation_split_provider_binding_invalid"), False
    if request != prepared.request_body:
        return _cancel_queued_locked(db, work=work, code="consultation_split_provider_binding_invalid"), False
    final_now = utcnow()
    if transcript_is_expired(work.transcript, now=final_now):
        return _cancel_queued_locked(db, work=work, code="consultation_split_source_expired"), False
    if work.attempt.reservation_valid_until <= final_now:
        return _cancel_queued_locked(db, work=work, code="consultation_split_reservation_expired"), False
    if not current_consultation_split_analysis_source_matches(
        db, work.owner, transcript=work.transcript, analysis=work.analysis
    ):
        return _cancel_queued_locked(db, work=work, code="consultation_split_source_stale", stale=True), False
    try:
        mark_provider_attempt_submitted(
            db,
            attempt_id=work.attempt.id,
            now=final_now,
            deadline_at=final_now + timedelta(seconds=SPLIT_ANALYSIS_PROVIDER_DEADLINE_SECONDS),
        )
        work.execution.status = ConsultationSplitExecutionStatus.processing
        work.execution.started_at = work.execution.started_at or final_now
        work.execution.error_code = None
        work.execution.provider_error_code = None
        work.execution.provider_http_status = None
        work.analysis.status = ConsultationSplitAnalysisStatus.processing
        work.analysis.completed_at = None
        work.analysis.error_code = None
        # Provider invocation is legal only after this durable commit.
        db.commit()
    except Exception:
        # Commit outcome may be unknown.  Never risk a second invocation; a
        # later delivery observes submitted/processing or a still-queued row.
        db.rollback()
        return SplitAnalysisRuntimeResult("in_flight", prepared.execution_id), False
    return SplitAnalysisRuntimeResult("in_flight", prepared.execution_id), True


def _usage_event(
    *,
    work: _LockedProcessingWork,
    event_type: ProviderUsageEventType,
    status: str,
    usage: dict[str, int | None] | None,
    error_code: str | None = None,
    provider_error_code: str | None = None,
    provider_http_status: int | None = None,
) -> ProviderUsageEvent:
    return ProviderUsageEvent(
        team_id=work.transcript.team_id,
        owner_user_id=work.owner.id,
        consultation_split_execution_id=work.execution.id,
        transcript_id=work.transcript.id,
        llm_config_id=work.execution.llm_config_id,
        feature_type=ProviderFeatureType.consultation_split_analysis,
        event_type=event_type,
        provider_adapter=work.execution.provider_adapter,
        model_name=work.execution.provider_model,
        status=status,
        prompt_tokens=usage["input_tokens"] if usage is not None else None,
        completion_tokens=usage["output_tokens"] if usage is not None else None,
        total_tokens=usage["total_tokens"] if usage is not None else None,
        duration_ms=usage["duration_ms"] if usage is not None else None,
        provider_duration_ms=usage["provider_duration_ms"] if usage is not None else None,
        error_code=error_code,
        provider_error_code=provider_error_code,
        provider_http_status=provider_http_status,
    )


def _settle_processing_attempt(
    db: Session,
    *,
    work: _LockedProcessingWork,
    usage: dict[str, int | None] | None,
    outcome: AttemptOutcome,
    error_code: str | None = None,
    provider_error_code: str | None = None,
    provider_http_status: int | None = None,
) -> ProviderAttempt:
    total = usage["total_tokens"] if usage is not None else None
    if total is not None:
        settled = settle_provider_attempt_tokens(
            db,
            attempt_id=work.attempt.id,
            reported_total_tokens=total,
            reported_input_tokens=usage["input_tokens"],
            reported_output_tokens=usage["output_tokens"],
            outcome=outcome,
        )
        if settled.settled_units > settled.reserved_units:
            error_code = "quota_estimate_underflow"
    else:
        settled = settle_provider_attempt_unknown_tokens(db, attempt_id=work.attempt.id)
    settled.error_code = error_code
    settled.provider_error_code = provider_error_code
    settled.provider_http_status = provider_http_status
    return settled


def _terminalize_submitted_failure(
    db: Session,
    *,
    execution_id: UUID,
    code: str,
    usage: dict[str, int | None] | None = None,
    provider_error_code: str | None = None,
    provider_http_status: int | None = None,
    stale: bool = False,
    suppress_proposal: bool = False,
) -> SplitAnalysisRuntimeResult:
    work = _lock_processing_work(
        db,
        execution_id=execution_id,
        expected_status=ConsultationSplitExecutionStatus.processing,
    )
    if work is None:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", execution_id)
    if work.analysis.status is not ConsultationSplitAnalysisStatus.processing or work.attempt.status is not AttemptStatus.submitted:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", execution_id)
    existing_events = db.scalars(
        select(ProviderUsageEvent)
        .where(ProviderUsageEvent.consultation_split_execution_id == execution_id)
        .with_for_update()
    ).all()
    if existing_events:
        # A terminal execution must be the only writer of a usage event.  This
        # state is inconsistent, so leave it to the conservative timeout path
        # rather than manufacture duplicate accounting.
        db.rollback()
        return SplitAnalysisRuntimeResult("in_flight", execution_id)
    _settle_processing_attempt(
        db,
        work=work,
        usage=usage,
        outcome=AttemptOutcome.failed,
        error_code=code,
        provider_error_code=provider_error_code,
        provider_http_status=provider_http_status,
    )
    now = utcnow()
    work.execution.status = ConsultationSplitExecutionStatus.failed
    work.execution.error_code = code
    work.execution.provider_error_code = provider_error_code
    work.execution.provider_http_status = provider_http_status
    if usage is not None:
        work.execution.input_token_count = usage["input_tokens"]
        work.execution.output_token_count = usage["output_tokens"]
        work.execution.total_token_count = usage["total_tokens"]
    work.execution.completed_at = now
    work.analysis.status = ConsultationSplitAnalysisStatus.stale if stale else ConsultationSplitAnalysisStatus.failed
    work.analysis.error_code = code
    work.analysis.completed_at = now
    if suppress_proposal:
        # A late preference transition must never leave an owner-visible,
        # unconfirmed proposal behind.  The raw provider response remains
        # encrypted only when it was already durably stored for recovery.
        work.analysis.proposal_encrypted = None
    db.add(
        _usage_event(
            work=work,
            event_type=ProviderUsageEventType.failed,
            status="stale" if stale else "failed",
            usage=usage,
            error_code=code,
            provider_error_code=provider_error_code,
            provider_http_status=provider_http_status,
        )
    )
    db.commit()
    return SplitAnalysisRuntimeResult("stale" if stale else "failed", execution_id, code)


def _persist_recoverable_response(
    db: Session,
    *,
    execution_id: UUID,
    text: str,
    usage: dict[str, int | None],
) -> tuple[bool, SplitAnalysisRuntimeResult | None]:
    """Commit provider success before parsing, with no plaintext response row."""
    work = _lock_processing_work(
        db,
        execution_id=execution_id,
        expected_status=ConsultationSplitExecutionStatus.processing,
    )
    if work is None or work.analysis.status is not ConsultationSplitAnalysisStatus.processing or work.attempt.status is not AttemptStatus.submitted:
        db.rollback()
        return False, None
    # The provider has already been invoked.  If the clinician disables this
    # feature before its response reaches PostgreSQL, settle the known call but
    # do not create a reviewable proposal.
    if not consultation_splitting_enabled(db, work.owner):
        db.rollback()
        return False, _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_preference_disabled",
            usage=usage,
            suppress_proposal=True,
        )
    if transcript_is_expired(work.transcript):
        db.rollback()
        return False, _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_source_expired",
            usage=usage,
        )
    if not _retention_binding_matches(work):
        db.rollback()
        return False, _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_retention_binding_invalid",
            usage=usage,
        )
    if not current_consultation_split_analysis_source_matches(
        db, work.owner, transcript=work.transcript, analysis=work.analysis
    ):
        db.rollback()
        return False, _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_source_stale",
            usage=usage,
            stale=True,
        )
    if work.execution.recoverable_response_encrypted is not None:
        db.rollback()
        return True, None
    work.execution.recoverable_response_encrypted = encrypt_json_for_existing_owner(
        db,
        owner_user_id=work.owner.id,
        table="consultation_split_executions",
        field="recoverable_response_encrypted",
        record_id=work.execution.id,
        plaintext={"text": text, "usage": usage},
    )
    db.commit()
    return True, None


def _candidate_ids(snapshot: object) -> list[str]:
    if not isinstance(snapshot, dict) or set(snapshot) != {"templates"}:
        raise AppError(422, "consultation_split_analysis_candidates_invalid", "Candidate snapshot is invalid")
    templates = snapshot.get("templates")
    if not isinstance(templates, list) or len(templates) > CONSULTATION_SPLIT_MAX_CANDIDATES:
        raise AppError(422, "consultation_split_analysis_candidates_invalid", "Candidate snapshot is invalid")
    ids: list[str] = []
    for template in templates:
        if not isinstance(template, dict) or set(template) != {"id", "name", "description", "mode"}:
            raise AppError(422, "consultation_split_analysis_candidates_invalid", "Candidate snapshot is invalid")
        candidate_id = template.get("id")
        if not isinstance(candidate_id, str):
            raise AppError(422, "consultation_split_analysis_candidates_invalid", "Candidate snapshot is invalid")
        ids.append(candidate_id)
    return ids


def _proposal_json(parsed: object) -> dict[str, object]:
    topics = getattr(parsed, "topics", None)
    if not isinstance(topics, list):
        raise AppError(502, "consultation_split_analysis_invalid", "Split analysis output was invalid")
    return {
        "topics": [
            {
                "topic_uuid": str(topic.topic_uuid),
                "title": topic.title,
                "is_primary": topic.is_primary,
                "disposition": topic.disposition,
                "template_id": str(topic.template_id) if topic.template_id is not None else None,
            }
            for topic in topics
        ]
    }


def _finalize_recoverable_response(db: Session, *, execution_id: UUID) -> SplitAnalysisRuntimeResult:
    work = _lock_processing_work(
        db,
        execution_id=execution_id,
        expected_status=ConsultationSplitExecutionStatus.processing,
    )
    if work is None:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", execution_id)
    if work.analysis.status is not ConsultationSplitAnalysisStatus.processing or work.attempt.status is not AttemptStatus.submitted:
        db.rollback()
        return SplitAnalysisRuntimeResult("noop", execution_id)
    try:
        response = read_split_execution_json(
            db, work.owner, execution=work.execution, field="recoverable_response_encrypted"
        )
        if not isinstance(response, dict) or set(response) != {"text", "usage"}:
            raise AppError(502, "consultation_split_analysis_invalid", "Split analysis output was invalid")
        text = response.get("text")
        if not isinstance(text, str) or not text or len(text) > RECOVERABLE_RESPONSE_MAX_CHARS:
            raise AppError(502, "consultation_split_analysis_invalid", "Split analysis output was invalid")
        usage = _normalized_usage(response.get("usage"))
    except (AppError, UnicodeDecodeError):
        db.rollback()
        return _terminalize_submitted_failure(
            db, execution_id=execution_id, code="consultation_split_analysis_invalid_output"
        )
    if not consultation_splitting_enabled(db, work.owner):
        db.rollback()
        return _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_preference_disabled",
            usage=usage,
            suppress_proposal=True,
        )
    if transcript_is_expired(work.transcript):
        db.rollback()
        return _terminalize_submitted_failure(
            db, execution_id=execution_id, code="consultation_split_source_expired", usage=usage
        )
    if not _retention_binding_matches(work):
        db.rollback()
        return _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_retention_binding_invalid",
            usage=usage,
        )
    if not current_consultation_split_analysis_source_matches(
        db, work.owner, transcript=work.transcript, analysis=work.analysis
    ):
        db.rollback()
        return _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_source_stale",
            usage=usage,
            stale=True,
        )
    try:
        candidates = read_split_analysis_json(
            db, work.owner, analysis=work.analysis, field="candidate_template_snapshot_encrypted"
        )
        parsed = parse_split_analysis_output(text, candidate_ids=_candidate_ids(candidates))
        proposal = _proposal_json(parsed)
    except (AppError, UnicodeDecodeError):
        db.rollback()
        return _terminalize_submitted_failure(
            db,
            execution_id=execution_id,
            code="consultation_split_analysis_invalid_output",
            usage=usage,
        )
    existing_events = db.scalars(
        select(ProviderUsageEvent)
        .where(ProviderUsageEvent.consultation_split_execution_id == execution_id)
        .with_for_update()
    ).all()
    if existing_events:
        db.rollback()
        return SplitAnalysisRuntimeResult("in_flight", execution_id)
    try:
        # Recheck just before terminal persistence.  Parsing is bounded but can
        # still take time relative to a short retention deadline.
        if not consultation_splitting_enabled(db, work.owner):
            db.rollback()
            return _terminalize_submitted_failure(
                db,
                execution_id=execution_id,
                code="consultation_split_preference_disabled",
                usage=usage,
                suppress_proposal=True,
            )
        if transcript_is_expired(work.transcript):
            db.rollback()
            return _terminalize_submitted_failure(
                db,
                execution_id=execution_id,
                code="consultation_split_source_expired",
                usage=usage,
            )
        if not _retention_binding_matches(work):
            db.rollback()
            return _terminalize_submitted_failure(
                db,
                execution_id=execution_id,
                code="consultation_split_retention_binding_invalid",
                usage=usage,
            )
        if not current_consultation_split_analysis_source_matches(
            db, work.owner, transcript=work.transcript, analysis=work.analysis
        ):
            db.rollback()
            return _terminalize_submitted_failure(
                db,
                execution_id=execution_id,
                code="consultation_split_source_stale",
                usage=usage,
                stale=True,
            )
        work.analysis.proposal_encrypted = encrypt_json_for_existing_owner(
            db,
            owner_user_id=work.owner.id,
            table="consultation_split_analyses",
            field="proposal_encrypted",
            record_id=work.analysis.id,
            plaintext=proposal,
        )
        _settle_processing_attempt(
            db, work=work, usage=usage, outcome=AttemptOutcome.succeeded
        )
        now = utcnow()
        # A split review needs at least two meaningful topics. A valid
        # single-topic proposal follows the ordinary one-note path just like
        # an empty proposal; retaining it as ``ready`` would incorrectly open
        # the split workflow.
        is_not_required = len(proposal["topics"]) < 2
        work.analysis.status = (
            ConsultationSplitAnalysisStatus.not_required
            if is_not_required
            else ConsultationSplitAnalysisStatus.ready
        )
        work.analysis.error_code = None
        work.analysis.completed_at = now
        work.execution.status = ConsultationSplitExecutionStatus.completed
        work.execution.error_code = None
        work.execution.provider_error_code = None
        work.execution.provider_http_status = None
        work.execution.input_token_count = usage["input_tokens"]
        work.execution.output_token_count = usage["output_tokens"]
        work.execution.total_token_count = usage["total_tokens"]
        work.execution.completed_at = now
        # A committed proposal is the durable recovery point.  Failed output
        # stays encrypted for diagnosis/recovery; successful output does not.
        work.execution.recoverable_response_encrypted = None
        db.add(
            _usage_event(
                work=work,
                event_type=ProviderUsageEventType.completed,
                status="not_required" if is_not_required else "ready",
                usage=usage,
            )
        )
        db.commit()
    except Exception:
        # The durable response remains after rollback, so a duplicate task can
        # finalize it without invoking the provider again.
        db.rollback()
        return SplitAnalysisRuntimeResult("in_flight", execution_id)
    return SplitAnalysisRuntimeResult("not_required" if is_not_required else "ready", execution_id)


def _run_prepared_provider_call(db: Session, *, prepared: PreparedSplitAnalysisSubmission) -> SplitAnalysisRuntimeResult:
    submitted, submit_committed = _submit_prepared(db, prepared)
    if submitted.outcome != "in_flight" or not submit_committed:
        return submitted
    # Prove the durable *attempt* submission, not merely an execution state,
    # in a clean read session before an irreversible provider call.
    if not _is_durably_submitted_for_provider_call(db, execution_id=prepared.execution_id):
        return SplitAnalysisRuntimeResult("noop", prepared.execution_id)
    try:
        response_text, raw_usage = llm_runtime.invoke_llm(
            snapshot=prepared.provider_snapshot,
            credential=prepared.credential,
            request_body=prepared.request_body,
        )
    except AppError as exc:
        provider_error_code, provider_http_status = _safe_provider_error(exc)
        return _terminalize_submitted_failure(
            db,
            execution_id=prepared.execution_id,
            code="consultation_split_provider_failed",
            provider_error_code=provider_error_code,
            provider_http_status=provider_http_status,
        )
    except Exception:
        return _terminalize_submitted_failure(
            db, execution_id=prepared.execution_id, code="consultation_split_provider_failed"
        )
    if not isinstance(response_text, str) or not response_text or len(response_text) > RECOVERABLE_RESPONSE_MAX_CHARS:
        return _terminalize_submitted_failure(
            db,
            execution_id=prepared.execution_id,
            code="consultation_split_analysis_invalid_output",
            usage=_normalized_usage(raw_usage),
        )
    usage = _normalized_usage(raw_usage)
    try:
        persisted, terminal = _persist_recoverable_response(
            db, execution_id=prepared.execution_id, text=response_text.strip(), usage=usage
        )
    except Exception:
        db.rollback()
        # If the response commit actually succeeded, a future delivery sees it
        # and finalizes; if not, submitted/no-response remains in-flight.
        return SplitAnalysisRuntimeResult("in_flight", prepared.execution_id)
    if terminal is not None:
        return terminal
    if not persisted:
        return SplitAnalysisRuntimeResult("in_flight", prepared.execution_id)
    return _finalize_recoverable_response(db, execution_id=prepared.execution_id)


def process_consultation_split_analysis_execution(
    db: Session,
    *,
    execution_id: UUID,
) -> SplitAnalysisRuntimeResult:
    """Run/recover one initial analysis execution without automatic retries."""
    if not isinstance(execution_id, UUID):
        raise ValueError("execution_id must be a UUID")
    if db.in_transaction():
        raise AppError(500, "consultation_split_runtime_transaction_active", "Split runtime requires a clean session")
    state = _read_execution_state(db, execution_id=execution_id)
    if state is None:
        return SplitAnalysisRuntimeResult("noop", execution_id)
    execution_status, analysis_status, has_response = state
    if execution_status in {
        ConsultationSplitExecutionStatus.completed,
        ConsultationSplitExecutionStatus.failed,
        ConsultationSplitExecutionStatus.cancelled,
    } or analysis_status in {
        ConsultationSplitAnalysisStatus.ready,
        ConsultationSplitAnalysisStatus.not_required,
        ConsultationSplitAnalysisStatus.failed,
        ConsultationSplitAnalysisStatus.stale,
    }:
        with Session(bind=db.get_bind(), future=True) as terminal_db:
            execution = terminal_db.get(ConsultationSplitExecution, execution_id)
            analysis = terminal_db.get(ConsultationSplitAnalysis, execution.analysis_id) if execution and execution.analysis_id else None
            return _result_for_terminal(execution, analysis) if execution is not None else SplitAnalysisRuntimeResult("noop", execution_id)
    if execution_status is ConsultationSplitExecutionStatus.processing:
        disabled = _terminalize_processing_when_preference_disabled(db, execution_id=execution_id)
        if disabled is not None:
            return disabled
        return _finalize_recoverable_response(db, execution_id=execution_id) if has_response else SplitAnalysisRuntimeResult("in_flight", execution_id)
    if execution_status is not ConsultationSplitExecutionStatus.queued or analysis_status is not ConsultationSplitAnalysisStatus.queued:
        return SplitAnalysisRuntimeResult("noop", execution_id)
    disabled = _cancel_queued_when_preference_disabled(db, execution_id=execution_id)
    if disabled is not None:
        return disabled
    prepared_result: PrepareSplitAnalysisSubmissionResult = prepare_queued_split_analysis_for_submission(
        db, execution_id=execution_id
    )
    if prepared_result.outcome != "prepared" or prepared_result.prepared is None:
        return SplitAnalysisRuntimeResult(
            "stale" if prepared_result.outcome == "stale" else "failed" if prepared_result.outcome == "failed" else "noop",
            execution_id,
            prepared_result.error_code,
        )
    return _run_prepared_provider_call(db, prepared=prepared_result.prepared)
