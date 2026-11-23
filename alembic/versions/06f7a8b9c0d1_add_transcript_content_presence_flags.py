"""Persist nonblank transcript draft/version metadata for history lookups."""

from alembic import op
import sqlalchemy as sa

revision = "06f7a8b9c0d1"
down_revision = "05e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL means legacy/unknown, not false: existing ciphertext must not be guessed.
    op.add_column("transcripts", sa.Column("current_draft_has_content", sa.Boolean(), nullable=True))
    op.add_column("transcript_versions", sa.Column("has_content", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_versions", "has_content")
    op.drop_column("transcripts", "current_draft_has_content")
