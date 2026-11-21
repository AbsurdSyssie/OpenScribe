from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Transcript, utcnow


def mark_first_content_saved(db: Session, *, transcript_id: UUID, owner_user_id: UUID) -> None:
    """Stamp a new consultation once, within the transaction saving its content."""
    db.execute(
        update(Transcript)
        .where(
            Transcript.id == transcript_id,
            Transcript.owner_user_id == owner_user_id,
            Transcript.track_first_content_at.is_(True),
            Transcript.first_content_at.is_(None),
        )
        .values(first_content_at=utcnow())
        .execution_options(synchronize_session="fetch")
    )
