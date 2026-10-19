"""add accessibility legal document kind

Revision ID: c0d1e2f3a6b7
Revises: b9c0d1e2f3a6
Create Date: 2026-10-15
"""

from alembic import op


revision = "c0d1e2f3a6b7"
down_revision = "b9c0d1e2f3a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE legaldocumentkind ADD VALUE IF NOT EXISTS 'accessibility'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value without rebuilding the type. Keep
    # the additive value, matching the repository's enum-extension migrations.
    pass
