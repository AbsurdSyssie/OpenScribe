"""add stt provider preset snapshots

Revision ID: g6b7c9d1e2f3
Revises: f5a6b7c9d1e2
Create Date: 2026-05-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "g6b7c9d1e2f3"
down_revision = "f5a6b7c9d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_provider_preset", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "stt_provider_preset")
