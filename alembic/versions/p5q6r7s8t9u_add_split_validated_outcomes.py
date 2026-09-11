"""add accepted split execution for partial note persistence

Revision ID: p5q6r7s8t9u
Revises: o4p5q6r7s8t
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "p5q6r7s8t9u"
down_revision = "o4p5q6r7s8t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum labels must be committed before application rows may use
    # them.  Keep the historical ``ready`` label for existing deployments.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE consultationsplittopicoutcomestatus ADD VALUE IF NOT EXISTS 'validated'")
    op.add_column(
        "consultation_split_topic_outcomes",
        sa.Column("accepted_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_consultation_split_topic_outcomes_accepted_execution",
        "consultation_split_topic_outcomes",
        "consultation_split_executions",
        ["accepted_execution_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_consultation_split_topic_outcomes_accepted_execution",
        "consultation_split_topic_outcomes", ["accepted_execution_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_consultation_split_topic_outcomes_accepted_execution", table_name="consultation_split_topic_outcomes")
    op.drop_constraint("fk_consultation_split_topic_outcomes_accepted_execution", "consultation_split_topic_outcomes", type_="foreignkey")
    op.drop_column("consultation_split_topic_outcomes", "accepted_execution_id")
    # PostgreSQL enum labels are append-only and intentionally remain.
