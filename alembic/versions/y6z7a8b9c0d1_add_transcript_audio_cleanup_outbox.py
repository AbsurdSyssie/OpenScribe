"""add transcript audio cleanup outbox

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
"""

from alembic import op
import sqlalchemy as sa


revision = "y6z7a8b9c0d1"
down_revision = "x5y6z7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transcript_audio_cleanup_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("secret_ref", sa.String(length=512), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=255), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_transcript_audio_cleanup_attempt_count_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_ref", name="uq_transcript_audio_cleanup_job_secret_ref"),
    )
    op.create_index(
        "ix_transcript_audio_cleanup_jobs_next_attempt_at",
        "transcript_audio_cleanup_jobs",
        ["next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transcript_audio_cleanup_jobs_next_attempt_at", table_name="transcript_audio_cleanup_jobs")
    op.drop_table("transcript_audio_cleanup_jobs")
