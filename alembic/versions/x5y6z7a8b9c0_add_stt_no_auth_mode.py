"""add explicit STT no-auth mode

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
"""

from alembic import op
import sqlalchemy as sa


revision = "x5y6z7a8b9c0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sttauthmode ADD VALUE IF NOT EXISTS 'none'")


def downgrade() -> None:
    no_auth_config = op.get_bind().execute(
        sa.text("SELECT 1 FROM team_stt_configs WHERE auth_mode = 'none' LIMIT 1")
    ).first()
    if no_auth_config is not None:
        raise RuntimeError(
            "Cannot downgrade STT no-auth support while configs use auth_mode='none'; "
            "convert or remove those configs first."
        )
    # PostgreSQL enum values cannot be removed transactionally. Leaving an
    # unused label is safe for the previous application version.
