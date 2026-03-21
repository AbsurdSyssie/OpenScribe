"""add provider usage events and generation metrics

Revision ID: n4b5c6d7e8f9
Revises: m3a4b5c6d7e8
Create Date: 2026-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "n4b5c6d7e8f9"
down_revision = "m3a4b5c6d7e8"
branch_labels = None
depends_on = None


provider_feature_type = postgresql.ENUM(
    "llm_generation",
    name="providerfeaturetype",
    create_type=False,
)

provider_usage_event_type = postgresql.ENUM(
    "queued",
    "started",
    "completed",
    "failed",
    "enqueue_failed",
    name="providerusageeventtype",
    create_type=False,
)


def upgrade() -> None:
    provider_feature_type.create(op.get_bind(), checkfirst=True)
    provider_usage_event_type.create(op.get_bind(), checkfirst=True)

    op.add_column("generated_documents", sa.Column("input_token_count", sa.Integer(), nullable=True))
    op.add_column("generated_documents", sa.Column("output_token_count", sa.Integer(), nullable=True))
    op.add_column("generated_documents", sa.Column("total_token_count", sa.Integer(), nullable=True))
    op.add_column("generated_documents", sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True))
    op.add_column("generated_documents", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("generated_documents", sa.Column("provider_duration_ms", sa.Integer(), nullable=True))
    op.add_column("generated_documents", sa.Column("provider_error_code", sa.String(length=255), nullable=True))
    op.add_column("generated_documents", sa.Column("provider_http_status", sa.Integer(), nullable=True))

    op.create_table(
        "provider_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feature_type", provider_feature_type, nullable=False),
        sa.Column("event_type", provider_usage_event_type, nullable=False),
        sa.Column("provider_adapter", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("provider_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("provider_error_code", sa.String(length=255), nullable=True),
        sa.Column("provider_http_status", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["generated_document_id"], ["generated_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_usage_events_team_id", "provider_usage_events", ["team_id"])
    op.create_index("ix_provider_usage_events_owner_user_id", "provider_usage_events", ["owner_user_id"])
    op.create_index("ix_provider_usage_events_created_at", "provider_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_usage_events_created_at", table_name="provider_usage_events")
    op.drop_index("ix_provider_usage_events_owner_user_id", table_name="provider_usage_events")
    op.drop_index("ix_provider_usage_events_team_id", table_name="provider_usage_events")
    op.drop_table("provider_usage_events")

    op.drop_column("generated_documents", "provider_http_status")
    op.drop_column("generated_documents", "provider_error_code")
    op.drop_column("generated_documents", "provider_duration_ms")
    op.drop_column("generated_documents", "duration_ms")
    op.drop_column("generated_documents", "estimated_cost_usd")
    op.drop_column("generated_documents", "total_token_count")
    op.drop_column("generated_documents", "output_token_count")
    op.drop_column("generated_documents", "input_token_count")

    provider_usage_event_type.drop(op.get_bind(), checkfirst=True)
    provider_feature_type.drop(op.get_bind(), checkfirst=True)
