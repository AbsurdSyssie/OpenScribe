"""add consultation multiple-problems split metadata

Revision ID: z7c8d9e0f1a2
Revises: a7c8d9e0f1a2
Create Date: 2026-10-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "z7c8d9e0f1a2"
down_revision = "a7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("multiple_problems", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("consultation_split_intents", sa.Column("manual_review_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("transcripts", "multiple_problems", server_default=None)
    op.alter_column("consultation_split_intents", "manual_review_requested", server_default=None)


def downgrade() -> None:
    op.drop_column("consultation_split_intents", "manual_review_requested")
    op.drop_column("transcripts", "multiple_problems")
