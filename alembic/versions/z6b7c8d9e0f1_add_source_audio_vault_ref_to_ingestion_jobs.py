"""add source audio vault ref to ingestion jobs

Revision ID: z6b7c8d9e0f1
Revises: y5a6b7c8d9e0
Create Date: 2026-04-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "z6b7c8d9e0f1"
down_revision = "y5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("source_audio_vault_ref", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("transcript_ingestion_jobs", "source_audio_vault_ref")
