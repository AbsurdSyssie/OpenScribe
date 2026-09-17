"""keep transcript versions from becoming split-content deletion roots

Revision ID: t3u4v5w6x7y8
Revises: s2t3u4v5w6x7
"""

from alembic import op


revision = "t3u4v5w6x7y8"
down_revision = "s2t3u4v5w6x7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_split_batches_materialization_version",
        "consultation_split_batches",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_split_batches_materialization_version",
        "consultation_split_batches",
        "transcript_versions",
        ["materialization_transcript_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_split_batches_materialization_version",
        "consultation_split_batches",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_split_batches_materialization_version",
        "consultation_split_batches",
        "transcript_versions",
        ["materialization_transcript_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
