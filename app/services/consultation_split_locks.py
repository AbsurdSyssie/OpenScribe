"""Canonical locking for consultation-split source state.

The provider runtime proves mutable sources while holding this exact order:
owner ``User`` -> transcript root -> extant post-consultation dictations in
stable id order.  Source writers use the same order, including writers that
create the first dictation row.  The transcript root is the serialization
point for the latter case, because there is no child row to lock yet.

This module deliberately does not lock templates or clinical-provider policy.
Candidate templates are frozen in each analysis request and re-fingerprinted
at the final source proof; they are reusable configuration rather than
transcript-owned source content.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AttemptKind,
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    PostConsultationDictation,
    ProviderAttempt,
    TaskDispatchKind,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    Transcript,
    User,
)


@dataclass(frozen=True, slots=True)
class LockedConsultationSplitSourceScope:
    """The durable owner/root/child lock chain for one transcript source."""

    owner: User
    transcript: Transcript
    dictations: tuple[PostConsultationDictation, ...]


@dataclass(frozen=True, slots=True)
class AnalysisExecutionLockRequirements:
    """Explicit state requirements for the analysis-only execution lock chain."""

    execution_status: ConsultationSplitExecutionStatus
    analysis_status: ConsultationSplitAnalysisStatus | None
    attempt_status: AttemptStatus | None
    dispatch_kind: TaskDispatchKind = TaskDispatchKind.consultation_split_analysis


@dataclass(frozen=True, slots=True)
class LockedAnalysisExecutionWork:
    owner: User
    transcript: Transcript
    execution: ConsultationSplitExecution
    analysis: ConsultationSplitAnalysis
    attempt: ProviderAttempt
    dispatch: TaskDispatchOutbox


def lock_consultation_split_source_scope(
    db: Session,
    *,
    owner_user_id: UUID,
    transcript_id: UUID,
) -> LockedConsultationSplitSourceScope | None:
    """Lock a transcript source in canonical order.

    Callers retain their existing authorization and expiry checks.  ``None``
    means a concurrent deletion or ownership change removed the expected
    scope before it could be locked; it intentionally reveals no content.
    """
    owner = db.scalar(
        select(User)
        .where(User.id == owner_user_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if owner is None:
        return None
    transcript = db.scalar(
        select(Transcript)
        .where(
            Transcript.id == transcript_id,
            Transcript.owner_user_id == owner.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if transcript is None:
        return None
    dictations = tuple(
        db.scalars(
            select(PostConsultationDictation)
            .where(PostConsultationDictation.transcript_id == transcript.id)
            .order_by(PostConsultationDictation.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
    )
    return LockedConsultationSplitSourceScope(
        owner=owner,
        transcript=transcript,
        dictations=dictations,
    )


def lock_analysis_execution_work(
    db: Session,
    *,
    execution_id: UUID,
    requirements: AnalysisExecutionLockRequirements,
) -> LockedAnalysisExecutionWork | None:
    """Lock one analysis execution without imposing its caller's terminal contract.

    The lock order is deliberately owner/root/dictations, execution, analysis,
    attempt, then dispatch.  Analysis pre-submit requires queued/reserved rows;
    runtime callers may require queued or processing and apply their own
    attempt/analysis transition checks afterwards.
    """
    identity = db.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.id == execution_id,
    ))
    if identity is None:
        return None
    scope = lock_consultation_split_source_scope(
        db, owner_user_id=identity.owner_user_id, transcript_id=identity.transcript_id,
    )
    if scope is None:
        return None
    owner, transcript = scope.owner, scope.transcript
    execution = db.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.id == execution_id,
    ).with_for_update())
    if (
        execution is None
        or execution.kind is not ConsultationSplitExecutionKind.analysis
        or execution.status is not requirements.execution_status
        or execution.analysis_id is None
        or execution.batch_id is not None
    ):
        return None
    analysis = db.scalar(select(ConsultationSplitAnalysis).where(
        ConsultationSplitAnalysis.id == execution.analysis_id,
    ).with_for_update())
    attempt = db.scalar(select(ProviderAttempt).where(
        ProviderAttempt.consultation_split_execution_id == execution.id,
    ).with_for_update())
    dispatch = db.scalar(select(TaskDispatchOutbox).where(
        TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
        TaskDispatchOutbox.source_id == execution.id,
    ).with_for_update())
    if (
        analysis is None
        or attempt is None
        or dispatch is None
        or (requirements.analysis_status is not None and analysis.status is not requirements.analysis_status)
        or (requirements.attempt_status is not None and attempt.status is not requirements.attempt_status)
        or attempt.attempt_kind is not AttemptKind.consultation_split_analysis
        or attempt.owner_user_id != owner.id
        or attempt.team_id != transcript.team_id
        or attempt.transcript_id != transcript.id
        or attempt.correlation_id != execution.id
        or attempt.attempt_number != 1
        or dispatch.dispatch_kind is not requirements.dispatch_kind
        or dispatch.state not in {TaskDispatchState.pending, TaskDispatchState.published}
        or owner.team_id is None
        or owner.is_system_admin
        or transcript.owner_user_id != owner.id
        or transcript.team_id != owner.team_id
        or execution.owner_user_id != owner.id
        or execution.team_id != owner.team_id
        or execution.transcript_id != transcript.id
        or analysis.owner_user_id != owner.id
        or analysis.team_id != owner.team_id
        or analysis.transcript_id != transcript.id
    ):
        return None
    return LockedAnalysisExecutionWork(owner, transcript, execution, analysis, attempt, dispatch)
