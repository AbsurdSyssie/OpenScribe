"""add durable template suggestion jobs

Revision ID: g7h8i9j0k1l2
Revises: f6e7d8c9b0a1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "g7h8i9j0k1l2"
down_revision = "f6e7d8c9b0a1"
branch_labels = None
depends_on = None


suggestion_status = postgresql.ENUM(
    "queued", "processing", "completed", "failed", name="templatesuggestionstatus"
)
suggestion_status_existing = postgresql.ENUM(name="templatesuggestionstatus", create_type=False)


def upgrade() -> None:
    suggestion_status.create(op.get_bind(), checkfirst=False)
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE attemptkind ADD VALUE IF NOT EXISTS 'llm_template_suggestion'")
        op.execute("ALTER TYPE taskdispatchkind ADD VALUE IF NOT EXISTS 'template_suggestion'")
        op.execute("ALTER TYPE taskdispatchsourcekind ADD VALUE IF NOT EXISTS 'template_suggestion_job'")
    op.create_table(
        "template_suggestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("status", suggestion_status_existing, nullable=False),
        sa.Column("excerpt_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("candidates_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("suggestion_result_encrypted", sa.Text(), nullable=True),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_llm_configs.id"), nullable=True),
        sa.Column("model_used", sa.String(255), nullable=True),
        sa.Column("llm_adapter_kind", sa.String(64), nullable=True),
        sa.Column("llm_base_url", sa.String(2048), nullable=True),
        sa.Column("llm_provider_config_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("worker_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("transcript_id", name="uq_template_suggestion_jobs_transcript"),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'processing' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('completed', 'failed') AND completed_at IS NOT NULL)",
            name="ck_template_suggestion_jobs_state_timestamps",
        ),
        sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 128", name="ck_template_suggestion_jobs_error_code_length"),
    )


def downgrade() -> None:
    op.drop_table("template_suggestion_jobs")
    suggestion_status_existing.drop(op.get_bind(), checkfirst=False)
    # PostgreSQL enum values are intentionally retained because removing them
    # is unsafe when durable accounting/outbox history may refer to them.
