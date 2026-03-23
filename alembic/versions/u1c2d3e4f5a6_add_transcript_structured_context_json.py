"""add transcript structured context json

Revision ID: u1c2d3e4f5a6
Revises: t0b1c2d3e4f5
Create Date: 2026-03-23 11:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "u1c2d3e4f5a6"
down_revision = "t0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("structured_context_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcripts", "structured_context_json")
