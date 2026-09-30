"""add owner detected PII dismissal decisions

Revision ID: f7e8d9c0a1b2
Revises: t3u4v5w6x7y8
Create Date: 2026-09-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f7e8d9c0a1b2"
down_revision = "t3u4v5w6x7y8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detected_pii_dismissals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("transcript_id", sa.UUID(), nullable=False),
        sa.Column("transcript_version_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("source_start_index", sa.Integer(), nullable=False),
        sa.Column("source_end_index", sa.Integer(), nullable=False),
        sa.Column("source_text_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_end_index > source_start_index", name="ck_detected_pii_dismissal_span_positive"),
        sa.CheckConstraint("source_start_index >= 0", name="ck_detected_pii_dismissal_start_nonnegative"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transcript_version_id"], ["transcript_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_version_id",
            "source_start_index",
            "source_end_index",
            name="uq_detected_pii_dismissal_source_span",
        ),
    )
    op.create_index(
        "ix_detected_pii_dismissals_owner_version",
        "detected_pii_dismissals",
        ["owner_user_id", "transcript_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_detected_pii_dismissals_owner_version", table_name="detected_pii_dismissals")
    op.drop_table("detected_pii_dismissals")
