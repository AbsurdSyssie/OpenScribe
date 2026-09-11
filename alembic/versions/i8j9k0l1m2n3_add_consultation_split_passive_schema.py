"""add passive consultation split persistence schema

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "i8j9k0l1m2n3"
down_revision = "h7i8j9k0l1m2"
branch_labels = None
depends_on = None


analysis_status = postgresql.ENUM("queued", "processing", "ready", "not_required", "failed", "stale", name="consultationsplitanalysisstatus", create_type=False)
draft_status = postgresql.ENUM("active", "stale", "confirmed", "bypassed", name="consultationsplitdraftstatus", create_type=False)
topic_disposition = postgresql.ENUM("separate_note", "include_in_primary", "exclude_from_notes", name="consultationsplittopicdisposition", create_type=False)
batch_status = postgresql.ENUM(
    "generation_queued",
    "generating",
    "verifying",
    "ready",
    "partially_ready",
    "completed_partial",
    "failed",
    name="consultationsplitbatchstatus",
    create_type=False,
)
topic_outcome_status = postgresql.ENUM("pending", "ready", "failed", name="consultationsplittopicoutcomestatus", create_type=False)
execution_kind = postgresql.ENUM("analysis", "generation", "verification", name="consultationsplitexecutionkind", create_type=False)
execution_status = postgresql.ENUM("queued", "processing", "completed", "failed", "cancelled", name="consultationsplitexecutionstatus", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        analysis_status,
        draft_status,
        topic_disposition,
        batch_status,
        topic_outcome_status,
        execution_kind,
        execution_status,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "consultation_split_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("redaction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_encrypted", sa.Text(), nullable=True),
        sa.Column("candidate_template_snapshot_encrypted", sa.Text(), nullable=True),
        sa.Column("provider_snapshot_encrypted", sa.Text(), nullable=True),
        sa.Column("proposal_encrypted", sa.Text(), nullable=True),
        sa.Column("status", analysis_status, nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_consultation_split_analyses_source_fingerprint_canonical"),
        sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 128", name="ck_consultation_split_analyses_error_code_length"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transcript_version_id"], ["transcript_versions.id"]),
        sa.ForeignKeyConstraint(["redaction_run_id"], ["redaction_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "transcript_id", "source_fingerprint", name="uq_consultation_split_analyses_owner_source"),
    )
    op.create_index("ix_consultation_split_analyses_transcript_status", "consultation_split_analyses", ["transcript_id", "status"])
    op.create_index("ix_consultation_split_analyses_retention", "consultation_split_analyses", ["retention_expires_at"])

    op.create_table(
        "consultation_split_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", draft_status, nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_consultation_split_drafts_source_fingerprint_canonical"),
        sa.ForeignKeyConstraint(["analysis_id"], ["consultation_split_analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consultation_split_drafts_transcript_status", "consultation_split_drafts", ["transcript_id", "status"])
    op.create_index("ix_consultation_split_drafts_retention", "consultation_split_drafts", ["retention_expires_at"])

    op.create_table(
        "consultation_split_draft_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_encrypted", sa.Text(), nullable=False),
        sa.Column("topic_order", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("disposition", topic_disposition, nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("template_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("topic_order BETWEEN 0 AND 5", name="ck_consultation_split_draft_topics_order_range"),
        sa.CheckConstraint("is_primary IS FALSE OR disposition = 'separate_note'", name="ck_consultation_split_draft_topics_primary_disposition"),
        sa.ForeignKeyConstraint(["draft_id"], ["consultation_split_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_version_id"], ["template_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "topic_uuid", name="uq_consultation_split_draft_topics_topic"),
        sa.UniqueConstraint("draft_id", "topic_order", name="uq_consultation_split_draft_topics_order"),
    )
    op.create_index("uq_consultation_split_draft_topics_one_primary", "consultation_split_draft_topics", ["draft_id"], unique=True, postgresql_where=sa.text("is_primary IS TRUE"))
    op.create_index("ix_consultation_split_draft_topics_transcript", "consultation_split_draft_topics", ["transcript_id"])

    op.create_table(
        "consultation_split_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confirmed_plan_encrypted", sa.Text(), nullable=False),
        sa.Column("clinical_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("source_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("template_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("pii_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("provider_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("note_options_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("status", batch_status, nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_consultation_split_batches_source_fingerprint_canonical"),
        sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 128", name="ck_consultation_split_batches_error_code_length"),
        sa.ForeignKeyConstraint(["analysis_id"], ["consultation_split_analyses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consultation_split_batches_transcript_status", "consultation_split_batches", ["transcript_id", "status"])
    op.create_index("ix_consultation_split_batches_retention", "consultation_split_batches", ["retention_expires_at"])

    op.create_table(
        "consultation_split_batch_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_encrypted", sa.Text(), nullable=False),
        sa.Column("topic_order", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("disposition", topic_disposition, nullable=False),
        sa.Column("template_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("topic_order BETWEEN 0 AND 5", name="ck_consultation_split_batch_topics_order_range"),
        sa.CheckConstraint("is_primary IS FALSE OR disposition = 'separate_note'", name="ck_consultation_split_batch_topics_primary_disposition"),
        sa.ForeignKeyConstraint(["batch_id"], ["consultation_split_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "topic_uuid", name="uq_consultation_split_batch_topics_topic"),
        sa.UniqueConstraint("batch_id", "topic_order", name="uq_consultation_split_batch_topics_order"),
        sa.UniqueConstraint("id", "topic_uuid", name="uq_consultation_split_batch_topics_membership"),
    )
    op.create_index("uq_consultation_split_batch_topics_one_primary", "consultation_split_batch_topics", ["batch_id"], unique=True, postgresql_where=sa.text("is_primary IS TRUE"))
    op.create_index("ix_consultation_split_batch_topics_transcript", "consultation_split_batch_topics", ["transcript_id"])

    op.create_table(
        "consultation_split_topic_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", topic_outcome_status, nullable=False),
        sa.Column("output_encrypted", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 128", name="ck_consultation_split_topic_outcomes_error_code_length"),
        sa.ForeignKeyConstraint(["batch_topic_id"], ["consultation_split_batch_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_topic_id", name="uq_consultation_split_topic_outcomes_batch_topic"),
    )
    op.create_index("ix_consultation_split_topic_outcomes_transcript_status", "consultation_split_topic_outcomes", ["transcript_id", "status"])
    op.create_index("ix_consultation_split_topic_outcomes_retention", "consultation_split_topic_outcomes", ["retention_expires_at"])

    op.create_table(
        "consultation_split_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", execution_kind, nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", execution_status, nullable=False),
        sa.Column("provider_adapter", sa.String(length=64), nullable=True),
        sa.Column("provider_base_url", sa.String(length=2048), nullable=True),
        sa.Column("provider_model", sa.String(length=255), nullable=True),
        sa.Column("provider_snapshot_encrypted", sa.Text(), nullable=True),
        sa.Column("request_payload_encrypted", sa.Text(), nullable=True),
        sa.Column("recoverable_response_encrypted", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("provider_error_code", sa.String(length=128), nullable=True),
        sa.Column("provider_http_status", sa.Integer(), nullable=True),
        sa.Column("input_token_count", sa.BigInteger(), nullable=True),
        sa.Column("output_token_count", sa.BigInteger(), nullable=True),
        sa.Column("total_token_count", sa.BigInteger(), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("(kind = 'analysis' AND analysis_id IS NOT NULL AND batch_id IS NULL) OR (kind IN ('generation', 'verification') AND analysis_id IS NULL AND batch_id IS NOT NULL)", name="ck_consultation_split_executions_kind_parent"),
        sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 128", name="ck_consultation_split_executions_error_code_length"),
        sa.CheckConstraint("provider_error_code IS NULL OR char_length(provider_error_code) <= 128", name="ck_consultation_split_executions_provider_error_code_length"),
        sa.CheckConstraint("provider_http_status IS NULL OR provider_http_status BETWEEN 100 AND 599", name="ck_consultation_split_executions_provider_http_status_range"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_consultation_split_executions_attempt_no_positive"),
        sa.CheckConstraint("input_token_count IS NULL OR input_token_count >= 0", name="ck_consultation_split_executions_input_token_count_nonnegative"),
        sa.CheckConstraint("output_token_count IS NULL OR output_token_count >= 0", name="ck_consultation_split_executions_output_token_count_nonnegative"),
        sa.CheckConstraint("total_token_count IS NULL OR total_token_count >= 0", name="ck_consultation_split_executions_total_token_count_nonnegative"),
        sa.ForeignKeyConstraint(["analysis_id"], ["consultation_split_analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["consultation_split_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consultation_split_executions_transcript_status", "consultation_split_executions", ["transcript_id", "status"])
    op.create_index("ix_consultation_split_executions_retention", "consultation_split_executions", ["retention_expires_at"])
    op.create_index(
        "uq_consultation_split_executions_analysis_kind_attempt",
        "consultation_split_executions",
        ["analysis_id", "kind", "attempt_no"],
        unique=True,
        postgresql_where=sa.text("analysis_id IS NOT NULL"),
    )
    op.create_index(
        "uq_consultation_split_executions_batch_kind_attempt",
        "consultation_split_executions",
        ["batch_id", "kind", "attempt_no"],
        unique=True,
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )

    op.add_column("generated_documents", sa.Column("consultation_split_batch_topic_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("generated_documents", sa.Column("consultation_split_topic_uuid", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_generated_documents_consultation_split_batch_topic",
        "generated_documents",
        "consultation_split_batch_topics",
        ["consultation_split_batch_topic_id", "consultation_split_topic_uuid"],
        ["id", "topic_uuid"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_generated_documents_consultation_split_batch_topic",
        "generated_documents",
        ["consultation_split_batch_topic_id"],
    )
    op.create_check_constraint(
        "ck_generated_documents_consultation_split_membership",
        "generated_documents",
        "(consultation_split_batch_topic_id IS NULL) = (consultation_split_topic_uuid IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_generated_documents_consultation_split_membership", "generated_documents", type_="check")
    op.drop_constraint("uq_generated_documents_consultation_split_batch_topic", "generated_documents", type_="unique")
    op.drop_constraint("fk_generated_documents_consultation_split_batch_topic", "generated_documents", type_="foreignkey")
    op.drop_column("generated_documents", "consultation_split_topic_uuid")
    op.drop_column("generated_documents", "consultation_split_batch_topic_id")

    for table_name in (
        "consultation_split_executions",
        "consultation_split_topic_outcomes",
        "consultation_split_batch_topics",
        "consultation_split_batches",
        "consultation_split_draft_topics",
        "consultation_split_drafts",
        "consultation_split_analyses",
    ):
        op.drop_table(table_name)

    bind = op.get_bind()
    for enum_type in (
        execution_status,
        execution_kind,
        topic_outcome_status,
        batch_status,
        topic_disposition,
        draft_status,
        analysis_status,
    ):
        enum_type.drop(bind, checkfirst=True)
