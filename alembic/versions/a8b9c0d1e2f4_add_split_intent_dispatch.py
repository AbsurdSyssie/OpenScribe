"""add durable consultation split intent dispatch

Revision ID: a8b9c0d1e2f4
Revises: z7c8d9e0f1a2
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa


revision = "a8b9c0d1e2f4"
down_revision = "z7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE taskdispatchkind ADD VALUE IF NOT EXISTS 'consultation_split_intent'")
        op.execute("ALTER TYPE taskdispatchsourcekind ADD VALUE IF NOT EXISTS 'consultation_split_intent'")
        op.execute("ALTER TYPE consultationsplitintentstatus ADD VALUE IF NOT EXISTS 'failed'")
    op.add_column("consultation_split_intents", sa.Column("error_code", sa.String(length=128), nullable=True))
    op.add_column("consultation_split_intents", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_consultation_split_intents_error_code_length",
        "consultation_split_intents",
        "error_code IS NULL OR char_length(error_code) <= 128",
    )


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum labels while the outbox may retain
    # rows that use them. Keep the append-only labels on downgrade, matching
    # the existing task-dispatch enum migration policy.
    op.drop_constraint(
        "ck_consultation_split_intents_error_code_length",
        "consultation_split_intents",
        type_="check",
    )
    op.drop_column("consultation_split_intents", "completed_at")
    op.drop_column("consultation_split_intents", "error_code")
