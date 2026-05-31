"""add hallucination check

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-05-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "s8t9u0v1w2x3"
down_revision: Union[str, None] = "r7s8t9u0v1w2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


hallucination_check_status = sa.Enum(
    "not_applicable",
    "skipped_not_configured",
    "skipped_config_invalid",
    "failed_provider",
    "failed_invalid_response",
    "checked_unchanged",
    "checked_corrected",
    name="hallucinationcheckstatus",
)


def upgrade() -> None:
    hallucination_check_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "team_hallucination_check_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_name_override", sa.String(length=255), nullable=True),
        sa.Column("selected_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["llm_config_id"], ["team_llm_configs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id"),
    )
    op.add_column("generated_documents", sa.Column("hallucination_check_debug_json_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "generated_documents",
        sa.Column(
            "hallucination_check_status",
            hallucination_check_status,
            server_default="not_applicable",
            nullable=False,
        ),
    )
    op.add_column("generated_documents", sa.Column("hallucination_check_llm_config_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("generated_documents", sa.Column("hallucination_check_model_name", sa.String(length=255), nullable=True))
    op.add_column("generated_documents", sa.Column("hallucination_check_provider_snapshot_json", sa.JSON(), nullable=True))
    op.add_column("generated_documents", sa.Column("hallucination_check_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generated_documents", sa.Column("hallucination_check_applied_edit_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "hallucination_check_applied_edit_count")
    op.drop_column("generated_documents", "hallucination_check_completed_at")
    op.drop_column("generated_documents", "hallucination_check_provider_snapshot_json")
    op.drop_column("generated_documents", "hallucination_check_model_name")
    op.drop_column("generated_documents", "hallucination_check_llm_config_id")
    op.drop_column("generated_documents", "hallucination_check_status")
    op.drop_column("generated_documents", "hallucination_check_debug_json_encrypted")
    op.drop_table("team_hallucination_check_selections")
    hallucination_check_status.drop(op.get_bind(), checkfirst=True)
