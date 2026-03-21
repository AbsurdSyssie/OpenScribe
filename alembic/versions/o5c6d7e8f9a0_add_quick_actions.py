"""add quick actions

Revision ID: o5c6d7e8f9a0
Revises: n4b5c6d7e8f9
Create Date: 2026-03-20 16:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "o5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "n4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    generateddocumentgeneratortype = postgresql.ENUM(
        "template",
        "followup",
        "quick_action",
        name="generateddocumentgeneratortype",
        create_type=False,
    )
    generateddocumentgeneratortype.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "quick_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", postgresql.ENUM("team", "user", name="templatescope", create_type=False), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND team_id IS NULL) "
            "OR (scope = 'team' AND team_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_quick_actions_scope_owner_team",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "quick_action_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quick_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("mode", postgresql.ENUM("freeform", name="templatemode", create_type=False), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["quick_action_id"], ["quick_actions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quick_action_id", "version_no", name="uq_quick_action_version_number"),
    )
    op.add_column("generated_documents", sa.Column("quick_action_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("generated_documents", sa.Column("source_quick_action_name", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_generated_documents_quick_action_version_id",
        "generated_documents",
        "quick_action_versions",
        ["quick_action_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_generated_documents_quick_action_version_id", "generated_documents", type_="foreignkey")
    op.drop_column("generated_documents", "source_quick_action_name")
    op.drop_column("generated_documents", "quick_action_version_id")
    op.drop_table("quick_action_versions")
    op.drop_table("quick_actions")
