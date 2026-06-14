"""add generation snapshot to generated documents

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-06-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "t9u0v1w2x3y4"
down_revision = "s8t9u0v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_documents", sa.Column("generation_snapshot_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "generation_snapshot_json")
