"""add provider snapshots for jobs and documents

Revision ID: q7e8f9a0b1c2
Revises: p6d7e8f9a0b1
Create Date: 2026-03-20 17:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "q7e8f9a0b1c2"
down_revision: Union[str, None] = "p6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_config_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_adapter_kind", sa.String(length=64), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_base_url", sa.String(length=2048), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_transcribe_path", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_model_name", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_language", sa.String(length=32), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_file_field_name", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_response_text_path", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_extra_form_fields_json", sa.JSON(), nullable=True))

    op.add_column("generated_documents", sa.Column("prompt_snapshot_text", sa.Text(), nullable=True))
    op.add_column("generated_documents", sa.Column("llm_adapter_kind", sa.String(length=64), nullable=True))
    op.add_column("generated_documents", sa.Column("llm_base_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "llm_base_url")
    op.drop_column("generated_documents", "llm_adapter_kind")
    op.drop_column("generated_documents", "prompt_snapshot_text")

    op.drop_column("transcript_ingestion_jobs", "stt_extra_form_fields_json")
    op.drop_column("transcript_ingestion_jobs", "stt_response_text_path")
    op.drop_column("transcript_ingestion_jobs", "stt_file_field_name")
    op.drop_column("transcript_ingestion_jobs", "stt_language")
    op.drop_column("transcript_ingestion_jobs", "stt_model_name")
    op.drop_column("transcript_ingestion_jobs", "stt_transcribe_path")
    op.drop_column("transcript_ingestion_jobs", "stt_base_url")
    op.drop_column("transcript_ingestion_jobs", "stt_adapter_kind")
    op.drop_column("transcript_ingestion_jobs", "stt_config_id")
