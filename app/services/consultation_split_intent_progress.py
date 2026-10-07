"""Server-owned continuation for accepted consultation split Create intents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    Transcript,
    TeamRole,
    User,
    utcnow,
)
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_drafts import initialize_or_reuse_split_draft_for_intent
from app.services.consultation_split_intents import continue_consultation_split_intent_as_one_note
from app.services.transcripts import transcript_is_expired


ConsultationSplitIntentProgressOutcome = Literal[
    "waiting",
    "continued_as_one_note",
    "review_ready",
    "terminal",
    "failed",
]


@dataclass(frozen=True, slots=True)
class ConsultationSplitIntentProgressResult:
    outcome: ConsultationSplitIntentProgressOutcome
    intent_id: UUID
    error_code: str | None = None


_SAFE_PROGRESS_ERROR_CODES = {
    "consultation_split_analysis_unavailable",
    "consultation_split_proposal_unavailable",
    "consultation_split_scope_invalid",
    "consultation_split_source_stale",
    "consultation_split_template_unavailable",
    "consultation_split_generation_unavailable",
}


def safe_split_intent_error_code(code: object) -> str | None:
    if code is None:
        return None
    return code if isinstance(code, str) and code in _SAFE_PROGRESS_ERROR_CODES else "consultation_split_generation_unavailable"


def _fail_intent(
    db: Session,
    *,
    intent: ConsultationSplitIntent,
    error_code: str | None,
) -> ConsultationSplitIntentProgressResult:
    safe_code = safe_split_intent_error_code(error_code) or "consultation_split_generation_unavailable"
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=intent.owner_user_id,
        transcript_id=intent.transcript_id,
    )
    if scope is None or transcript_is_expired(scope.transcript):
        db.rollback()
        return ConsultationSplitIntentProgressResult("terminal", intent.id)
    locked = db.scalar(
        select(ConsultationSplitIntent)
        .where(ConsultationSplitIntent.id == intent.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.status is not ConsultationSplitIntentStatus.analysis_pending:
        db.rollback()
        return ConsultationSplitIntentProgressResult("terminal", intent.id)
    locked.status = ConsultationSplitIntentStatus.failed
    locked.error_code = safe_code
    locked.completed_at = utcnow()
    db.commit()
    return ConsultationSplitIntentProgressResult("failed", intent.id, safe_code)


def progress_consultation_split_intent(
    db: Session,
    *,
    intent_id: UUID,
) -> ConsultationSplitIntentProgressResult:
    """Advance one accepted intent without relying on a browser continuation.

    Pending analysis is the only retryable outcome. Terminal, stale, corrupt,
    or unavailable state never starts ordinary generation and never asks the
    worker to retry permanently.
    """
    intent = db.get(ConsultationSplitIntent, intent_id)
    if intent is None:
        db.rollback()
        return ConsultationSplitIntentProgressResult("terminal", intent_id)
    if intent.status is not ConsultationSplitIntentStatus.analysis_pending:
        db.rollback()
        return ConsultationSplitIntentProgressResult("terminal", intent_id)
    if intent.analysis_id is None:
        return _fail_intent(db, intent=intent, error_code="consultation_split_analysis_unavailable")
    actor = db.scalar(
        select(User).where(
            User.id == intent.owner_user_id,
            User.team_id == intent.team_id,
        )
    )
    transcript = db.get(Transcript, intent.transcript_id)
    analysis = db.get(ConsultationSplitAnalysis, intent.analysis_id)
    if actor is None or actor.is_system_admin or actor.team_role not in {TeamRole.user, TeamRole.leader} or transcript is None or transcript_is_expired(transcript) or analysis is None or (
        analysis.owner_user_id != intent.owner_user_id
        or analysis.team_id != intent.team_id
        or analysis.transcript_id != intent.transcript_id
        or analysis.retention_expires_at != intent.retention_expires_at
        or transcript.owner_user_id != intent.owner_user_id
        or transcript.team_id != intent.team_id
        or transcript.retention_expires_at != intent.retention_expires_at
    ):
        return _fail_intent(db, intent=intent, error_code="consultation_split_scope_invalid")
    if analysis.status in {
        ConsultationSplitAnalysisStatus.queued,
        ConsultationSplitAnalysisStatus.processing,
    }:
        db.rollback()
        return ConsultationSplitIntentProgressResult("waiting", intent_id)
    if analysis.status in {
        ConsultationSplitAnalysisStatus.failed,
        ConsultationSplitAnalysisStatus.stale,
    }:
        return _fail_intent(
            db,
            intent=intent,
            error_code=analysis.error_code or "consultation_split_analysis_unavailable",
        )

    try:
        if (
            analysis.status is ConsultationSplitAnalysisStatus.not_required
            and not intent.manual_review_requested
        ):
            continue_consultation_split_intent_as_one_note(
                db,
                actor,
                transcript_id=intent.transcript_id,
                intent_id=intent.id,
            )
            return ConsultationSplitIntentProgressResult("continued_as_one_note", intent_id)
        initialize_or_reuse_split_draft_for_intent(db, actor, intent_id=intent.id)
        return ConsultationSplitIntentProgressResult("review_ready", intent_id)
    except AppError as exc:
        db.rollback()
        refreshed = db.get(ConsultationSplitIntent, intent_id)
        if refreshed is None:
            return ConsultationSplitIntentProgressResult("terminal", intent_id)
        return _fail_intent(db, intent=refreshed, error_code=exc.code)
