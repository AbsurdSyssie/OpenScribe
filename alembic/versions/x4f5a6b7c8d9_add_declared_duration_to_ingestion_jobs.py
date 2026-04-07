"""add declared duration to transcript ingestion jobs

Revision ID: x4f5a6b7c8d9
Revises: w3e4f5a6b7c8
Create Date: 2026-04-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "x4f5a6b7c8d9"
down_revision = "w3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("declared_duration_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "declared_duration_seconds")
