"""add templates and generated documents

Revision ID: j0d1e2f3a4b5
Revises: i9c0d1e2f3a4
Create Date: 2026-03-19 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "j0d1e2f3a4b5"
down_revision: str | None = "i9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


template_scope = postgresql.ENUM("team", "user", name="templatescope", create_type=False)
template_mode = postgresql.ENUM("freeform", name="templatemode", create_type=False)
generated_document_generator_type = postgresql.ENUM("template", name="generateddocumentgeneratortype", create_type=False)
generated_document_status = postgresql.ENUM("ready", "failed", name="generateddocumentstatus", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    template_scope.create(bind, checkfirst=True)
    template_mode.create(bind, checkfirst=True)
    generated_document_generator_type.create(bind, checkfirst=True)
    generated_document_status.create(bind, checkfirst=True)

    op.create_table(
        "templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", template_scope, nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND team_id IS NULL) "
            "OR (scope = 'team' AND team_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_templates_scope_owner_team",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "template_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("mode", template_mode, nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version_no", name="uq_template_version_number"),
    )

    op.create_table(
        "generated_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generator_type", generated_document_generator_type, nullable=False),
        sa.Column("template_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_template_name", sa.String(length=255), nullable=False),
        sa.Column("status", generated_document_status, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("document_mode", template_mode, nullable=False),
        sa.Column("original_output_text_encrypted", sa.Text(), nullable=False),
        sa.Column("edited_output_text_encrypted", sa.Text(), nullable=False),
        sa.Column("is_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_used", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["template_version_id"], ["template_versions.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.ForeignKeyConstraint(["transcript_version_id"], ["transcript_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("generated_documents")
    op.drop_table("template_versions")
    op.drop_table("templates")

    bind = op.get_bind()
    generated_document_status.drop(bind, checkfirst=True)
    generated_document_generator_type.drop(bind, checkfirst=True)
    template_mode.drop(bind, checkfirst=True)
    template_scope.drop(bind, checkfirst=True)
