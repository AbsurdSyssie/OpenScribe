"""add stt segment snapshots to ingestion jobs

Revision ID: 20260509_003
Revises: 20260509_002
Create Date: 2026-05-09 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_003"
down_revision: Union[str, None] = "20260509_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_segments_path", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_segment_text_field", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_segment_start_field", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_segment_end_field", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_segment_speaker_field", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "stt_segment_speaker_field")
    op.drop_column("transcript_ingestion_jobs", "stt_segment_end_field")
    op.drop_column("transcript_ingestion_jobs", "stt_segment_start_field")
    op.drop_column("transcript_ingestion_jobs", "stt_segment_text_field")
    op.drop_column("transcript_ingestion_jobs", "stt_segments_path")
