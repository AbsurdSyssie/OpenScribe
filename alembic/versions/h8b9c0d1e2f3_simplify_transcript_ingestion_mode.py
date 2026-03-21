"""simplify transcript ingestion mode

Revision ID: h8b9c0d1e2f3
Revises: g7a8b9c0d1e2
Create Date: 2026-03-19 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "h8b9c0d1e2f3"
down_revision: str | None = "g7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE transcriptingestionmode RENAME TO transcriptingestionmode_old")
    op.execute("CREATE TYPE transcriptingestionmode AS ENUM ('whole_file', 'live_chunked')")
    op.execute(
        """
        ALTER TABLE transcripts
        ALTER COLUMN ingestion_mode
        TYPE transcriptingestionmode
        USING (
            CASE
                WHEN ingestion_mode::text IN ('file_upload', 'microphone_batch') THEN 'whole_file'
                ELSE ingestion_mode::text
            END
        )::transcriptingestionmode
        """
    )
    op.execute("DROP TYPE transcriptingestionmode_old")


def downgrade() -> None:
    op.execute("ALTER TYPE transcriptingestionmode RENAME TO transcriptingestionmode_new")
    op.execute("CREATE TYPE transcriptingestionmode AS ENUM ('file_upload', 'microphone_batch', 'live_chunked')")
    op.execute(
        """
        ALTER TABLE transcripts
        ALTER COLUMN ingestion_mode
        TYPE transcriptingestionmode
        USING (
            CASE
                WHEN ingestion_mode::text = 'whole_file' THEN 'file_upload'
                ELSE ingestion_mode::text
            END
        )::transcriptingestionmode
        """
    )
    op.execute("DROP TYPE transcriptingestionmode_new")
