"""add consultation split dispatch and quota foundation

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "j9k0l1m2n3o4"
down_revision = "i8j9k0l1m2n3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum labels are intentionally append-only.  They must commit
    # before columns/defaults can use them, so keep this separate from the DDL
    # transaction below.
    with op.get_context().autocommit_block():
        for enum_name, labels in (
            (
                "taskdispatchkind",
                (
                    "consultation_split_analysis",
                    "consultation_split_generation",
                    "consultation_split_verification",
                ),
            ),
            (
                "attemptkind",
                (
                    "consultation_split_analysis",
                    "consultation_split_generation",
                    "consultation_split_verification",
                ),
            ),
            (
                "providerfeaturetype",
                (
                    "consultation_split_analysis",
                    "consultation_split_generation",
                    "consultation_split_verification",
                ),
            ),
            ("taskdispatchsourcekind", ("consultation_split_execution",)),
        ):
            for label in labels:
                op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{label}'")

    op.add_column(
        "provider_attempts",
        sa.Column("consultation_split_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_provider_attempts_consultation_split_execution",
        "provider_attempts",
        "consultation_split_executions",
        ["consultation_split_execution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_provider_attempts_split_execution",
        "provider_attempts",
        ["consultation_split_execution_id"],
    )
    op.create_check_constraint(
        "ck_provider_attempts_split_execution_source_shape",
        "provider_attempts",
        "consultation_split_execution_id IS NULL OR "
        "(generated_document_id IS NULL AND transcript_ingestion_job_id IS NULL)",
    )
    op.create_index(
        "ix_provider_attempts_split_execution_status",
        "provider_attempts",
        ["consultation_split_execution_id", "status"],
    )

    op.add_column(
        "provider_usage_events",
        sa.Column("consultation_split_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_provider_usage_events_consultation_split_execution",
        "provider_usage_events",
        "consultation_split_executions",
        ["consultation_split_execution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_provider_usage_events_split_execution",
        "provider_usage_events",
        ["consultation_split_execution_id"],
    )
    op.create_index(
        "uq_provider_usage_events_split_execution_completed",
        "provider_usage_events",
        ["consultation_split_execution_id"],
        unique=True,
        postgresql_where=sa.text(
            "consultation_split_execution_id IS NOT NULL AND event_type = 'completed'"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_provider_usage_events_split_execution_completed", table_name="provider_usage_events")
    op.drop_index("ix_provider_usage_events_split_execution", table_name="provider_usage_events")
    op.drop_constraint(
        "fk_provider_usage_events_consultation_split_execution",
        "provider_usage_events",
        type_="foreignkey",
    )
    op.drop_column("provider_usage_events", "consultation_split_execution_id")

    op.drop_index("ix_provider_attempts_split_execution_status", table_name="provider_attempts")
    op.drop_constraint("ck_provider_attempts_split_execution_source_shape", "provider_attempts", type_="check")
    op.drop_constraint("uq_provider_attempts_split_execution", "provider_attempts", type_="unique")
    op.drop_constraint(
        "fk_provider_attempts_consultation_split_execution",
        "provider_attempts",
        type_="foreignkey",
    )
    op.drop_column("provider_attempts", "consultation_split_execution_id")

    # PostgreSQL enum labels are append-only and intentionally remain after a
    # downgrade, consistent with the project's established enum convention.
