"""Persist owner-keyed transcript version digests for bounded PII freshness checks."""

import sqlalchemy as sa
from alembic import op


revision = "7ca9e231d4b0"
down_revision = "06f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy encrypted snapshots cannot be safely backfilled in a schema-only
    # migration. They keep NULL digests until a normal transcript writer reuses
    # or recreates their snapshots.
    op.add_column("transcript_versions", sa.Column("content_digest", sa.String(64), nullable=True))
    op.create_index(
        "ix_transcript_versions_transcript_content_digest",
        "transcript_versions",
        ["transcript_id", "content_digest", "version_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_transcript_versions_transcript_content_digest", table_name="transcript_versions")
    op.drop_column("transcript_versions", "content_digest")
