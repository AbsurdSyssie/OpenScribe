"""add redaction runs and entities

Revision ID: r8f9a0b1c2d3
Revises: q7e8f9a0b1c2
Create Date: 2026-03-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "r8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "q7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


redactionrunstatus = postgresql.ENUM("succeeded", "failed", name="redactionrunstatus")


def upgrade() -> None:
    bind = op.get_bind()
    redactionrunstatus.create(bind, checkfirst=True)

    op.create_table(
        "redaction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", postgresql.ENUM("succeeded", "failed", name="redactionrunstatus", create_type=False), nullable=False),
        sa.Column("redacted_text_encrypted", sa.Text(), nullable=True),
        sa.Column("mapping_hash", sa.String(length=64), nullable=True),
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("api_provider", sa.String(length=64), nullable=False),
        sa.Column("api_model_or_version", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.ForeignKeyConstraint(["transcript_version_id"], ["transcript_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "redaction_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("redaction_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_order", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=255), nullable=False),
        sa.Column("placeholder", sa.String(length=64), nullable=False),
        sa.Column("original_value_encrypted", sa.Text(), nullable=False),
        sa.Column("normalized_value_hash", sa.String(length=64), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["redaction_run_id"], ["redaction_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("redaction_run_id", "entity_order", name="uq_redaction_entity_order"),
        sa.UniqueConstraint("redaction_run_id", "placeholder", name="uq_redaction_entity_placeholder"),
    )
    op.add_column("generated_documents", sa.Column("redaction_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_generated_documents_redaction_run_id_redaction_runs",
        "generated_documents",
        "redaction_runs",
        ["redaction_run_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_generated_documents_redaction_run_id_redaction_runs", "generated_documents", type_="foreignkey")
    op.drop_column("generated_documents", "redaction_run_id")
    op.drop_table("redaction_entities")
    op.drop_table("redaction_runs")
    bind = op.get_bind()
    redactionrunstatus.drop(bind, checkfirst=True)
