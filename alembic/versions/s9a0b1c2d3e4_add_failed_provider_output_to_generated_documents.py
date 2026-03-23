"""add failed provider output to generated documents

Revision ID: s9a0b1c2d3e4
Revises: r8f9a0b1c2d3
Create Date: 2026-03-23 10:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "s9a0b1c2d3e4"
down_revision = "r8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_documents",
        sa.Column("failed_provider_output_redacted_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_documents", "failed_provider_output_redacted_encrypted")
