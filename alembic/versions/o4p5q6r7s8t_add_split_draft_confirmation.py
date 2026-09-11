"""add consultation split draft confirmation linkage

Revision ID: o4p5q6r7s8t
Revises: n3o4p5q6r7s
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "o4p5q6r7s8t"
down_revision = "n3o4p5q6r7s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum labels are append-only and must commit before rows can
    # use them. Existing intents remain in their current terminal/pending state.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE consultationsplitintentstatus ADD VALUE IF NOT EXISTS 'confirmed'")

    op.add_column(
        "consultation_split_batches",
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_consultation_split_batches_intent",
        "consultation_split_batches",
        "consultation_split_intents",
        ["intent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_consultation_split_batches_intent",
        "consultation_split_batches",
        ["intent_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_consultation_split_batches_intent", "consultation_split_batches", type_="unique")
    op.drop_constraint("fk_consultation_split_batches_intent", "consultation_split_batches", type_="foreignkey")
    op.drop_column("consultation_split_batches", "intent_id")
    # PostgreSQL enum labels are append-only and intentionally remain.
