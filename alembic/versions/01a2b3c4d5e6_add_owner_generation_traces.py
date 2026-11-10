"""Add opt-in owner generation traces.

Revision ID: 01a2b3c4d5e6
Revises: fb1c2d3e4f50
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "01a2b3c4d5e6"
down_revision = "fb1c2d3e4f50"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("generation_trace_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("generation_trace_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("transcript_id", UUID(as_uuid=True), sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", UUID(as_uuid=True), sa.ForeignKey("provider_attempts.id", ondelete="SET NULL")),
        sa.Column("generated_document_id", UUID(as_uuid=True), sa.ForeignKey("generated_documents.id", ondelete="SET NULL")),
        sa.Column("workflow", sa.String(64), nullable=False),
        sa.Column("request_ordinal", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32)),
        sa.Column("validation", sa.String(16)),
        sa.Column("trace_error_code", sa.String(64)),
        sa.Column("decision", sa.String(16)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("retry_wait_ms", sa.Integer()),
        sa.Column("provider_adapter", sa.String(64)),
        sa.Column("provider_model", sa.String(255)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column("request_encrypted", sa.Text()),
        sa.Column("response_encrypted", sa.Text()),
        sa.CheckConstraint("state IN ('pending', 'complete', 'incomplete')", name="ck_generation_trace_state"),
        sa.CheckConstraint("expires_at > captured_at AND expires_at <= captured_at + interval '24 hours'", name="ck_generation_trace_lifetime"),
        sa.CheckConstraint("request_ordinal BETWEEN 1 AND 4", name="ck_generation_trace_ordinal"),
        sa.CheckConstraint("workflow IN ('llm_generation', 'llm_hallucination_check', 'llm_template_suggestion', 'consultation_split_analysis', 'consultation_split_generation', 'consultation_split_verification')", name='ck_generation_trace_workflow'),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('success', 'rate_limited', 'timeout', 'connection_error', 'provider_error', 'not_dispatched')", name='ck_generation_trace_outcome'),
        sa.CheckConstraint("validation IS NULL OR validation IN ('accepted', 'invalid')", name='ck_generation_trace_validation'),
        sa.CheckConstraint("decision IS NULL OR decision IN ('retry', 'backup', 'repair', 'terminal')", name='ck_generation_trace_decision'),
        sa.CheckConstraint("reason IS NULL OR reason IN ('event_limit', 'size_limit', 'key_unavailable', 'credential_safety', 'validation_not_captured', 'partial_response')", name='ck_generation_trace_reason'),
        sa.CheckConstraint('(http_status IS NULL OR http_status BETWEEN 100 AND 599) AND (retry_wait_ms IS NULL OR retry_wait_ms BETWEEN 0 AND 30000)', name='ck_generation_trace_transport'),
    )
    op.create_index("ix_generation_trace_events_expiry", "generation_trace_events", ["expires_at"])
    op.create_index("ix_generation_trace_events_root_time", "generation_trace_events", ["transcript_id", "captured_at"])


def downgrade():
    op.drop_table("generation_trace_events")
    op.drop_column("users", "generation_trace_enabled")
