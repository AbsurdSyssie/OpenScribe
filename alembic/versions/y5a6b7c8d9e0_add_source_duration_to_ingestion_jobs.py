"""add source duration to transcript ingestion jobs

Revision ID: y5a6b7c8d9e0
Revises: x4f5a6b7c8d9
Create Date: 2026-04-05 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "y5a6b7c8d9e0"
down_revision = "x4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("source_audio_duration_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "source_audio_duration_seconds")
