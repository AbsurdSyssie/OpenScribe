"""add Gemini Enterprise provider foundation

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE llmadapterkind ADD VALUE IF NOT EXISTS 'gemini_enterprise'")
    op.execute("ALTER TYPE llmauthmode ADD VALUE IF NOT EXISTS 'google_adc'")
    op.execute("ALTER TYPE llmauthmode ADD VALUE IF NOT EXISTS 'google_service_account'")

    op.add_column(
        "team_llm_configs",
        sa.Column(
            "provider_config_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.alter_column("team_llm_configs", "provider_config_json", server_default=None)
    op.add_column(
        "generated_documents",
        sa.Column("llm_provider_config_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_documents", "llm_provider_config_json")
    op.drop_column("team_llm_configs", "provider_config_json")
    # PostgreSQL enum values cannot be removed safely. Previous application
    # versions ignore these unused labels.
