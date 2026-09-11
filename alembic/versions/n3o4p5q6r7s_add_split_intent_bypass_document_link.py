"""add consultation split intent bypass document link

Revision ID: n3o4p5q6r7s
Revises: m2n3o4p5q6r7
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "n3o4p5q6r7s"
down_revision = "m2n3o4p5q6r7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum labels are append-only and must commit before use.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE consultationsplitintentstatus ADD VALUE IF NOT EXISTS 'bypassed'")

    # Existing intents predate one-note consumption and deliberately remain
    # unbound. Do not infer a document from encrypted request snapshots.
    op.add_column(
        "consultation_split_intents",
        sa.Column("generated_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_consultation_split_intents_generated_document",
        "consultation_split_intents",
        "generated_documents",
        ["generated_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_consultation_split_intents_generated_document",
        "consultation_split_intents",
        ["generated_document_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_consultation_split_intents_generated_document",
        "consultation_split_intents",
        type_="unique",
    )
    op.drop_constraint(
        "fk_consultation_split_intents_generated_document",
        "consultation_split_intents",
        type_="foreignkey",
    )
    op.drop_column("consultation_split_intents", "generated_document_id")

    # PostgreSQL enum labels are append-only and intentionally remain after a
    # downgrade, consistent with the project's enum convention.
