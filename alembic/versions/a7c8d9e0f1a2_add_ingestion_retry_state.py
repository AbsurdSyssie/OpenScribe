"""add durable ingestion retry state

Revision ID: a7c8d9e0f1a2
Revises: f7e8d9c0a1b2
Create Date: 2026-10-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c8d9e0f1a2"
down_revision = "f7e8d9c0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_ingestion_jobs", sa.Column("request_idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("active_attempt_number", sa.Integer(), server_default="1", nullable=False))
    op.add_column("transcript_ingestion_jobs", sa.Column("automatic_retry_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("transcript_ingestion_jobs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_transcript_ingestion_jobs_request_idempotency", "transcript_ingestion_jobs", ["transcript_id", "request_idempotency_key"])
    op.create_check_constraint("ck_transcript_ingestion_jobs_active_attempt_positive", "transcript_ingestion_jobs", "active_attempt_number >= 1")
    op.create_check_constraint("ck_transcript_ingestion_jobs_automatic_retry_nonnegative", "transcript_ingestion_jobs", "automatic_retry_count >= 0")
    op.add_column("task_dispatch_outbox", sa.Column("dispatch_sequence", sa.Integer(), server_default="1", nullable=False))
    op.drop_constraint("uq_task_dispatch_outbox_dispatch_source", "task_dispatch_outbox", type_="unique")
    op.create_unique_constraint("uq_task_dispatch_outbox_dispatch_source", "task_dispatch_outbox", ["dispatch_kind", "source_kind", "source_id", "dispatch_sequence"])


def downgrade() -> None:
    duplicate = op.get_bind().execute(sa.text("SELECT 1 FROM task_dispatch_outbox GROUP BY dispatch_kind, source_kind, source_id HAVING count(*) > 1 LIMIT 1")).first()
    if duplicate is not None:
        raise RuntimeError("Cannot downgrade ingestion retry dispatches while more than one dispatch exists for a source")
    op.drop_constraint("uq_task_dispatch_outbox_dispatch_source", "task_dispatch_outbox", type_="unique")
    op.create_unique_constraint("uq_task_dispatch_outbox_dispatch_source", "task_dispatch_outbox", ["dispatch_kind", "source_kind", "source_id"])
    op.drop_column("task_dispatch_outbox", "dispatch_sequence")
    op.drop_constraint("ck_transcript_ingestion_jobs_automatic_retry_nonnegative", "transcript_ingestion_jobs", type_="check")
    op.drop_constraint("ck_transcript_ingestion_jobs_active_attempt_positive", "transcript_ingestion_jobs", type_="check")
    op.drop_constraint("uq_transcript_ingestion_jobs_request_idempotency", "transcript_ingestion_jobs", type_="unique")
    op.drop_column("transcript_ingestion_jobs", "next_retry_at")
    op.drop_column("transcript_ingestion_jobs", "automatic_retry_count")
    op.drop_column("transcript_ingestion_jobs", "active_attempt_number")
    op.drop_column("transcript_ingestion_jobs", "request_idempotency_key")
