"""add llm provider presets

Revision ID: a0b1c2d3e4f6
Revises: z6b7c8d9e0f1, 20260509_003
Create Date: 2026-05-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a0b1c2d3e4f6"
down_revision = ("z6b7c8d9e0f1", "20260509_003")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team_llm_configs", sa.Column("provider_preset", sa.String(length=64), nullable=True))
    op.add_column(
        "team_llm_configs",
        sa.Column("inspection_metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.execute(
        """
        UPDATE team_llm_configs
        SET provider_preset = CASE
            WHEN adapter_kind::text = 'ollama_chat' THEN 'ollama'
            WHEN adapter_kind::text = 'bedrock_chat' THEN 'bedrock_http_gateway'
            WHEN adapter_kind::text = 'openai_chat' AND lower(coalesce(base_url, '')) LIKE '%://api.openai.com/%' THEN 'openai'
            WHEN adapter_kind::text = 'openai_chat' AND lower(coalesce(base_url, '')) LIKE '%openrouter.ai/%' THEN 'openrouter'
            WHEN adapter_kind::text = 'openai_chat' AND lower(coalesce(base_url, '')) LIKE '%://api.x.ai/%' THEN 'xai'
            WHEN adapter_kind::text = 'openai_chat' AND lower(coalesce(base_url, '')) LIKE '%://api.groq.com/%' THEN 'groq'
            WHEN adapter_kind::text = 'openai_chat' AND lower(coalesce(base_url, '')) LIKE '%://api.deepseek.com%' THEN 'deepseek'
            WHEN adapter_kind::text = 'openai_chat' AND lower(coalesce(base_url, '')) LIKE '%://api.mistral.ai/%' THEN 'mistral'
            WHEN adapter_kind::text = 'openai_chat' AND (
                lower(coalesce(base_url, '')) LIKE '%://api.together.xyz/%'
                OR lower(coalesce(base_url, '')) LIKE '%://api.together.ai/%'
            ) THEN 'together'
            ELSE 'custom_openai_compatible'
        END
        WHERE provider_preset IS NULL
        """
    )
    op.alter_column("team_llm_configs", "provider_preset", nullable=False)
    op.create_index("ix_team_llm_configs_provider_preset", "team_llm_configs", ["provider_preset"])
    op.alter_column("team_llm_configs", "inspection_metadata_json", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_team_llm_configs_provider_preset", table_name="team_llm_configs")
    op.drop_column("team_llm_configs", "inspection_metadata_json")
    op.drop_column("team_llm_configs", "provider_preset")
