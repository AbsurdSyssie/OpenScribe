"""add explicit STT no-auth mode

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
"""

from alembic import op


revision = "x5y6z7a8b9c0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sttauthmode ADD VALUE IF NOT EXISTS 'none'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while rows may use them.
    pass
