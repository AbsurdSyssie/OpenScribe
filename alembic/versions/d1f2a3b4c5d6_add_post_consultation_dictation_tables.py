"""add post consultation dictation tables

Revision ID: d1f2a3b4c5d6
Revises: c9e0f1a2b3c4
Create Date: 2026-04-13 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d1f2a3b4c5d6"
down_revision = "c9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_consultation_dictations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combined_edited_text_encrypted", sa.Text(), nullable=True),
        sa.Column("is_combined_text_user_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latest_appended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transcript_id", name="uq_post_consultation_dictations_transcript"),
    )
    op.create_table(
        "post_consultation_dictation_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_consultation_dictation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("asr_text_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(
            ["post_consultation_dictation_id"],
            ["post_consultation_dictations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "post_consultation_dictation_id",
            "sequence_no",
            name="uq_post_consultation_dictation_segments_sequence",
        ),
    )


def downgrade() -> None:
    op.drop_table("post_consultation_dictation_segments")
    op.drop_table("post_consultation_dictations")
