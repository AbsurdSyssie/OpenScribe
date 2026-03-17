"""add transcript ingestion mode

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-17 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


transcript_ingestion_mode = postgresql.ENUM(
    "file_upload",
    "microphone_batch",
    "live_chunked",
    name="transcriptingestionmode",
)
transcript_ingestion_mode_existing = postgresql.ENUM(
    "file_upload",
    "microphone_batch",
    "live_chunked",
    name="transcriptingestionmode",
    create_type=False,
)


def upgrade() -> None:
    transcript_ingestion_mode.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "transcripts",
        sa.Column(
            "ingestion_mode",
            transcript_ingestion_mode_existing,
            nullable=False,
            server_default="live_chunked",
        ),
    )
    op.alter_column("transcripts", "ingestion_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("transcripts", "ingestion_mode")
    transcript_ingestion_mode_existing.drop(op.get_bind(), checkfirst=False)
