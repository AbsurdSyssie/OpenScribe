"""One-time nullable presence-metadata backfill using existing owner content keys.

Run after migrating in the normal key-enabled application environment.
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Transcript, TranscriptVersion
from app.services.transcripts import transcript_draft_text, transcript_version_text


def backfill_transcript_content_presence(*, batch_size: int = 100) -> tuple[int, int]:
    counts = [0, 0]
    with SessionLocal() as db:
        for index, (model, field, read_text) in enumerate((
            (Transcript, Transcript.current_draft_has_content, lambda row: transcript_draft_text(db, transcript=row) or ""),
            (TranscriptVersion, TranscriptVersion.has_content, lambda row: transcript_version_text(db, transcript_version=row)),
        )):
            while True:
                rows = list(db.scalars(
                    select(model).where(field.is_(None)).order_by(model.id)
                    .limit(batch_size).with_for_update()
                ))
                if not rows:
                    break
                for row in rows:
                    setattr(row, field.key, bool(read_text(row).strip()))
                db.commit()
                counts[index] += len(rows)
    return counts[0], counts[1]


if __name__ == "__main__":
    drafts, versions = backfill_transcript_content_presence()
    print(f"Backfilled {drafts} drafts and {versions} transcript versions")
