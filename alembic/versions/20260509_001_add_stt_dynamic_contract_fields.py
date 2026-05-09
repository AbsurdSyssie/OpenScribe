"""add stt dynamic contract fields

Revision ID: 20260509_001
Revises: 20260503_001
Create Date: 2026-05-09 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_001"
down_revision: Union[str, None] = "20260503_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("team_stt_configs", sa.Column("model_field_name", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("language_field_name", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("segments_path", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("segment_text_field", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("segment_start_field", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("segment_end_field", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("segment_speaker_field", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_model_field_name", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_language_field_name", sa.String(length=255), nullable=True))

    op.execute("UPDATE team_stt_configs SET model_field_name = 'model' WHERE model_name IS NOT NULL AND model_field_name IS NULL")
    op.execute("UPDATE team_stt_configs SET language_field_name = 'language' WHERE language IS NOT NULL AND language_field_name IS NULL")
    op.execute("UPDATE transcript_ingestion_jobs SET stt_model_field_name = 'model' WHERE stt_model_name IS NOT NULL AND stt_model_field_name IS NULL")
    op.execute("UPDATE transcript_ingestion_jobs SET stt_language_field_name = 'language' WHERE stt_language IS NOT NULL AND stt_language_field_name IS NULL")


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "stt_language_field_name")
    op.drop_column("transcript_ingestion_jobs", "stt_model_field_name")
    op.drop_column("team_stt_configs", "segment_speaker_field")
    op.drop_column("team_stt_configs", "segment_end_field")
    op.drop_column("team_stt_configs", "segment_start_field")
    op.drop_column("team_stt_configs", "segment_text_field")
    op.drop_column("team_stt_configs", "segments_path")
    op.drop_column("team_stt_configs", "language_field_name")
    op.drop_column("team_stt_configs", "model_field_name")
