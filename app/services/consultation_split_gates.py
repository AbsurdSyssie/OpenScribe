"""Narrow, server-derived consultation-split eligibility decisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConsultationSplitAnalysis, ConsultationSplitBatch, ConsultationSplitIntent, ConsultationSplitIntentStatus, Transcript, User
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled


def consultation_splitting_available(actor: User | None) -> bool:
    """Whether this deployment permits an eligible owner to request a split."""
    return bool(
        consultation_splitting_feature_enabled()
        and actor is not None
        and not actor.is_system_admin
        and actor.team_id is not None
        and actor.team_role is not None
        and actor.team_role.value in {"user", "leader"}
    )


def transcript_split_enabled(db: Session, actor: User, transcript: Transcript) -> bool:
    """Effective new-work gate: automatic preference or this owner's mark."""
    return (
        transcript.owner_user_id == actor.id
        and transcript.team_id == actor.team_id
        and consultation_splitting_available(actor)
        and (
            consultation_splitting_enabled(db, actor) or transcript.multiple_problems
        )
    )


def analysis_split_enabled(db: Session, actor: User, *, analysis_id) -> bool:
    """Permit durable accepted manual work after a later transcript unmark.

    The intent flag is immutable and server-derived while the transcript root is
    locked during creation.  It never bypasses the deployment or owner gate.
    """
    if not consultation_splitting_available(actor):
        return False
    if consultation_splitting_enabled(db, actor):
        return True
    return db.scalar(
        select(ConsultationSplitIntent.id).join(
            ConsultationSplitAnalysis,
            ConsultationSplitAnalysis.id == ConsultationSplitIntent.analysis_id,
        ).where(
            ConsultationSplitIntent.analysis_id == analysis_id,
            ConsultationSplitIntent.owner_user_id == actor.id,
            ConsultationSplitIntent.team_id == actor.team_id,
            ConsultationSplitIntent.manual_review_requested.is_(True),
            ConsultationSplitIntent.status.in_([ConsultationSplitIntentStatus.analysis_pending, ConsultationSplitIntentStatus.confirmed]),
            ConsultationSplitAnalysis.owner_user_id == actor.id,
            ConsultationSplitAnalysis.team_id == actor.team_id,
            ConsultationSplitAnalysis.transcript_id == ConsultationSplitIntent.transcript_id,
        ).limit(1)
    ) is not None


def intent_split_enabled(db: Session, actor: User, *, intent: ConsultationSplitIntent) -> bool:
    """Authorize this exact durable intent, never another intent's exception."""
    if not consultation_splitting_available(actor):
        return False
    if intent.owner_user_id != actor.id or intent.team_id != actor.team_id:
        return False
    if consultation_splitting_enabled(db, actor):
        return True
    return (
        intent.manual_review_requested
        and intent.status in {ConsultationSplitIntentStatus.analysis_pending, ConsultationSplitIntentStatus.confirmed}
    )


def batch_split_enabled(db: Session, actor: User, *, batch: ConsultationSplitBatch) -> bool:
    """Apply the accepted-intent exception only to its exact immutable batch."""
    if not consultation_splitting_available(actor):
        return False
    if consultation_splitting_enabled(db, actor):
        return True
    return db.scalar(
        select(ConsultationSplitIntent.id).where(
            ConsultationSplitIntent.id == batch.intent_id,
            ConsultationSplitIntent.owner_user_id == actor.id,
            ConsultationSplitIntent.team_id == actor.team_id,
            ConsultationSplitIntent.transcript_id == batch.transcript_id,
            ConsultationSplitIntent.analysis_id == batch.analysis_id,
            ConsultationSplitIntent.manual_review_requested.is_(True),
            ConsultationSplitIntent.status == ConsultationSplitIntentStatus.confirmed,
        )
    ) is not None
