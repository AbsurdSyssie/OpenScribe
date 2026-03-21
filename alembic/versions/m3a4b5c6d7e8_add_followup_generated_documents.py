"""add follow-up generated document support

Revision ID: m3a4b5c6d7e8
Revises: l2f3a4b5c6d7
Create Date: 2026-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "m3a4b5c6d7e8"
down_revision = "l2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE generateddocumentgeneratortype ADD VALUE IF NOT EXISTS 'followup'")
    op.add_column("generated_documents", sa.Column("follow_up_prompt_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "follow_up_prompt_text")
