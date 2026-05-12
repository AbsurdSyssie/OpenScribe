"""add llm request payload to generated documents

Revision ID: f5a6b7c9d1e2
Revises: e4f5a6b7c9d1
Create Date: 2026-05-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f5a6b7c9d1e2"
down_revision = "e4f5a6b7c9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_documents", sa.Column("llm_request_payload_json_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "llm_request_payload_json_encrypted")
