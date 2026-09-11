"""bind confirmed split batches to their materialization transcript version

Revision ID: r1s2t3u4v5w6
Revises: q6r7s8t9u0v1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "r1s2t3u4v5w6"
down_revision = "q6r7s8t9u0v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing confirmed rows predate this boundary and remain nullable. New
    # confirmation writes the immutable version before generation is queued.
    op.add_column(
        "consultation_split_batches",
        sa.Column("materialization_transcript_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_split_batches_materialization_version",
        "consultation_split_batches",
        "transcript_versions",
        ["materialization_transcript_version_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_split_batches_materialization_version",
        "consultation_split_batches",
        type_="foreignkey",
    )
    op.drop_column("consultation_split_batches", "materialization_transcript_version_id")
