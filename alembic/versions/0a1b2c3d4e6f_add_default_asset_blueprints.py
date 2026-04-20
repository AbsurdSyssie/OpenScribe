"""add default asset blueprints

Revision ID: 0a1b2c3d4e6f
Revises: f4a5b6c7d8e9
Create Date: 2026-04-15 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0a1b2c3d4e6f"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


templatemode_existing = postgresql.ENUM("freeform", "structured", name="templatemode", create_type=False)


def upgrade() -> None:
    op.create_table(
        "default_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("uq_default_templates_name_lower", "default_templates", [sa.text("lower(btrim(name))")], unique=True)
    op.alter_column("default_templates", "is_active", server_default=None)

    op.create_table(
        "default_template_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("default_template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("default_templates.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("mode", templatemode_existing, nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("default_template_id", "version_no", name="uq_default_template_version_number"),
    )

    op.create_table(
        "default_quick_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("uq_default_quick_actions_name_lower", "default_quick_actions", [sa.text("lower(btrim(name))")], unique=True)
    op.alter_column("default_quick_actions", "is_active", server_default=None)

    op.create_table(
        "default_quick_action_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("default_quick_action_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("default_quick_actions.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("mode", templatemode_existing, nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("default_quick_action_id", "version_no", name="uq_default_quick_action_version_number"),
    )


def downgrade() -> None:
    op.drop_table("default_quick_action_versions")
    op.drop_index("uq_default_quick_actions_name_lower", table_name="default_quick_actions")
    op.drop_table("default_quick_actions")
    op.drop_table("default_template_versions")
    op.drop_index("uq_default_templates_name_lower", table_name="default_templates")
    op.drop_table("default_templates")
