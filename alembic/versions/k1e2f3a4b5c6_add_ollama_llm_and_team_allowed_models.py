"""add ollama llm adapter and team allowed models

Revision ID: k1e2f3a4b5c6
Revises: j0d1e2f3a4b5
Create Date: 2026-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "k1e2f3a4b5c6"
down_revision = "j0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE llmadapterkind ADD VALUE IF NOT EXISTS 'ollama_chat'")
    op.execute("ALTER TYPE llmauthmode ADD VALUE IF NOT EXISTS 'none'")
    op.add_column(
        "team_llm_selections",
        sa.Column("allowed_models_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.execute(
        """
        UPDATE team_llm_selections AS selection
        SET allowed_models_json = COALESCE(config.available_models_json, '[]'::json)
        FROM team_llm_configs AS config
        WHERE config.id = selection.llm_config_id
        """
    )
    op.alter_column("team_llm_selections", "allowed_models_json", server_default=None)


def downgrade() -> None:
    op.drop_column("team_llm_selections", "allowed_models_json")
