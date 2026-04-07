"""add source audio blob to transcript ingestion jobs

Revision ID: v2d3e4f5a6b7
Revises: u1c2d3e4f5a6
Create Date: 2026-04-01 11:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "v2d3e4f5a6b7"
down_revision = "u1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("source_audio_blob", sa.LargeBinary(), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("source_audio_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "source_audio_size_bytes")
    op.drop_column("transcript_ingestion_jobs", "source_audio_blob")
