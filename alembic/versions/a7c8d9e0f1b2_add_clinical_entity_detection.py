"""add clinical entity detection

Revision ID: a7c8d9e0f1b2
Revises: 1a2b3c4d5e6f
Create Date: 2026-04-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a7c8d9e0f1b2"
down_revision: Union[str, None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    redaction_run_status = postgresql.ENUM("succeeded", "failed", name="redactionrunstatus", create_type=False)
    op.add_column("deidentification_providers", sa.Column("clinical_detection_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("deidentification_providers", sa.Column("clinical_detection_allow_unredacted", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "clinical_entity_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("redaction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", redaction_run_status, nullable=False),
        sa.Column("source_text_redacted", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("api_provider", sa.String(length=255), nullable=True),
        sa.Column("api_model_or_version", sa.String(length=255), nullable=True),
        sa.Column("entity_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["deidentification_providers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["redaction_run_id"], ["redaction_runs.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.ForeignKeyConstraint(["transcript_version_id"], ["transcript_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clinical_entity_runs_transcript_owner", "clinical_entity_runs", ["transcript_id", "owner_user_id"])
    op.create_table(
        "clinical_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clinical_entity_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_order", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=255), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("normalized_value_hash", sa.String(length=128), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["clinical_entity_run_id"], ["clinical_entity_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clinical_entities_run_order", "clinical_entities", ["clinical_entity_run_id", "entity_order"])


def downgrade() -> None:
    op.drop_index("ix_clinical_entities_run_order", table_name="clinical_entities")
    op.drop_table("clinical_entities")
    op.drop_index("ix_clinical_entity_runs_transcript_owner", table_name="clinical_entity_runs")
    op.drop_table("clinical_entity_runs")
    op.drop_column("deidentification_providers", "clinical_detection_allow_unredacted")
    op.drop_column("deidentification_providers", "clinical_detection_enabled")
