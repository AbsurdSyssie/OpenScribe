"""add team llm provider tables

Revision ID: i9c0d1e2f3a4
Revises: h8b9c0d1e2f3
Create Date: 2026-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "i9c0d1e2f3a4"
down_revision = "h8b9c0d1e2f3"
branch_labels = None
depends_on = None


llmauthmode = postgresql.ENUM("bearer", name="llmauthmode")
llmauthmode_existing = postgresql.ENUM("bearer", name="llmauthmode", create_type=False)
llmadapterkind = postgresql.ENUM("openai_chat", name="llmadapterkind")
llmadapterkind_existing = postgresql.ENUM("openai_chat", name="llmadapterkind", create_type=False)


def upgrade() -> None:
    llmauthmode.create(op.get_bind(), checkfirst=True)
    llmadapterkind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "team_llm_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("adapter_kind", llmadapterkind_existing, nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("auth_mode", llmauthmode_existing, nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("available_models_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("vault_secret_ref", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.alter_column("team_llm_configs", "available_models_json", server_default=None)
    op.alter_column("team_llm_configs", "is_active", server_default=None)

    op.create_table(
        "team_llm_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_llm_configs.id"), nullable=False),
        sa.Column("model_name_override", sa.String(length=255), nullable=True),
        sa.Column("selected_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("team_id", name="uq_team_llm_selections_team_id"),
    )

    op.create_table(
        "user_llm_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("preferred_model_name", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", name="uq_user_llm_preferences_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_llm_preferences")
    op.drop_table("team_llm_selections")
    op.drop_table("team_llm_configs")
    llmadapterkind.drop(op.get_bind(), checkfirst=True)
    llmauthmode.drop(op.get_bind(), checkfirst=True)
