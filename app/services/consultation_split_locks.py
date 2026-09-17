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

from app.models import PostConsultationDictation, Transcript, User


@dataclass(frozen=True, slots=True)
class LockedConsultationSplitSourceScope:
    """The durable owner/root/child lock chain for one transcript source."""

    owner: User
    transcript: Transcript
    dictations: tuple[PostConsultationDictation, ...]


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
