"""add bedrock llm adapter

Revision ID: w3e4f5a6b7c8
Revises: v2d3e4f5a6b7
Create Date: 2026-04-03 00:00:00.000000
"""

from alembic import op


revision = "w3e4f5a6b7c8"
down_revision = "v2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE llmadapterkind ADD VALUE IF NOT EXISTS 'bedrock_chat'")


def downgrade() -> None:
    pass
