"""add worker_received_at timing columns

Revision ID: e4f5a6b7c8d9
Revises: d2e3f4a5b6c7
Create Date: 2026-07-20 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f5a6b7c8d9"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_documents",
        sa.Column("worker_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transcript_ingestion_jobs",
        sa.Column("worker_received_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "worker_received_at")
    op.drop_column("generated_documents", "worker_received_at")
