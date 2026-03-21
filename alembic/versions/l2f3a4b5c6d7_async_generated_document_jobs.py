"""make generated document generation async-ready

Revision ID: l2f3a4b5c6d7
Revises: k1e2f3a4b5c6
Create Date: 2026-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "l2f3a4b5c6d7"
down_revision = "k1e2f3a4b5c6"
branch_labels = None
depends_on = None


generated_document_status_existing = postgresql.ENUM(
    "queued",
    "processing",
    "ready",
    "failed",
    name="generateddocumentstatus",
    create_type=False,
)


def upgrade() -> None:
    op.execute("ALTER TYPE generateddocumentstatus ADD VALUE IF NOT EXISTS 'queued'")
    op.execute("ALTER TYPE generateddocumentstatus ADD VALUE IF NOT EXISTS 'processing'")
    op.add_column("generated_documents", sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("generated_documents", sa.Column("celery_task_id", sa.String(length=255), nullable=True))
    op.add_column("generated_documents", sa.Column("error_code", sa.String(length=255), nullable=True))
    op.add_column("generated_documents", sa.Column("error_message", sa.String(length=255), nullable=True))
    op.add_column("generated_documents", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generated_documents", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "completed_at")
    op.drop_column("generated_documents", "started_at")
    op.drop_column("generated_documents", "error_message")
    op.drop_column("generated_documents", "error_code")
    op.drop_column("generated_documents", "celery_task_id")
    op.drop_column("generated_documents", "llm_config_id")
