"""add llm config setup status

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f6
Create Date: 2026-05-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_llm_configs",
        sa.Column("setup_status", sa.String(length=64), nullable=False, server_default="ready"),
    )
    op.execute(
        """
        UPDATE team_llm_configs
        SET setup_status = CASE
            WHEN model_name IS NULL THEN 'pending_model_selection'
            ELSE 'ready'
        END
        """
    )
    op.create_index("ix_team_llm_configs_setup_status", "team_llm_configs", ["setup_status"])
    op.alter_column("team_llm_configs", "setup_status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_team_llm_configs_setup_status", table_name="team_llm_configs")
    op.drop_column("team_llm_configs", "setup_status")
