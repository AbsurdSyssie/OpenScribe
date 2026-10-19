"""add durable Deepgram stream session and delivery accounting

Revision ID: d3e4f5a6b7c8
Revises: c0d1e2f3a6b7
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d3e4f5a6b7c8"
down_revision = "c0d1e2f3a6b7"
branch_labels = None
depends_on = None


stream_status = postgresql.ENUM(
    "active", "finalizing", "completed", "failed", "recovering", "discarded",
    name="transcriptstreamstatus", create_type=False,
)
window_status = postgresql.ENUM(
    "active", "committed", "failed", "recovering", "discarded",
    name="transcriptstreamwindowstatus", create_type=False,
)
delivery_status = postgresql.ENUM(
    "authorized", "sent", "ambiguous", name="transcriptstreamdeliverystatus", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE attemptkind ADD VALUE IF NOT EXISTS 'stt_deepgram_live_stream'")
        op.execute("ALTER TYPE providersettlementbasis ADD VALUE IF NOT EXISTS 'stream_authorized'")
        op.execute("ALTER TYPE providersettlementbasis ADD VALUE IF NOT EXISTS 'stream_measured'")
    for enum_type in (stream_status, window_status, delivery_status):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "transcript_stream_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", stream_status, nullable=False),
        sa.Column("connection_fence", sa.Integer(), nullable=False),
        sa.Column("fence_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_source_sample_offset", sa.BigInteger(), nullable=False),
        sa.Column("received_sample_count", sa.BigInteger(), nullable=False),
        sa.Column("authorized_sample_count", sa.BigInteger(), nullable=False),
        sa.Column("window_no", sa.Integer(), nullable=False),
        sa.Column("window_forwarded_sample_count", sa.Integer(), nullable=False),
        sa.Column("active_provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("failure_code IS NULL OR char_length(failure_code) <= 128", name="ck_transcript_stream_sessions_failure_code_length"),
        sa.CheckConstraint("connection_fence > 0", name="ck_transcript_stream_sessions_connection_fence_positive"),
        sa.CheckConstraint("next_source_sample_offset >= 0", name="ck_transcript_stream_sessions_next_offset_nonnegative"),
        sa.CheckConstraint("received_sample_count >= 0", name="ck_transcript_stream_sessions_received_samples_nonnegative"),
        sa.CheckConstraint("authorized_sample_count >= 0", name="ck_transcript_stream_sessions_authorized_samples_nonnegative"),
        sa.CheckConstraint("window_forwarded_sample_count >= 0 AND window_forwarded_sample_count <= 480000", name="ck_transcript_stream_sessions_window_samples_range"),
        sa.CheckConstraint("window_no >= 0", name="ck_transcript_stream_sessions_window_no_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(selection_snapshot_json) = 'object'", name="ck_transcript_stream_sessions_snapshot_object"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_transcript_stream_sessions_open_transcript", "transcript_stream_sessions", ["transcript_id"],
        unique=True, postgresql_where=sa.text("status IN ('active', 'finalizing', 'failed', 'recovering')"),
    )
    op.create_index("ix_transcript_stream_sessions_owner_state", "transcript_stream_sessions", ["owner_user_id", "status", "updated_at"])

    op.create_table(
        "transcript_stream_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_no", sa.Integer(), nullable=False),
        sa.Column("source_start_sample_offset", sa.BigInteger(), nullable=False),
        sa.Column("source_end_sample_offset", sa.BigInteger(), nullable=False),
        sa.Column("forwarded_sample_count", sa.BigInteger(), nullable=False),
        sa.Column("status", window_status, nullable=False),
        sa.Column("final_segment_count", sa.Integer(), nullable=False),
        sa.Column("text_digest", sa.String(length=64), nullable=True),
        sa.Column("recovery_audio_vault_ref", sa.Text(), nullable=True),
        sa.Column("recovery_audio_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("recovery_audio_stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_audio_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("window_no >= 0", name="ck_transcript_stream_windows_window_no_nonnegative"),
        sa.CheckConstraint("source_start_sample_offset >= 0", name="ck_transcript_stream_windows_start_nonnegative"),
        sa.CheckConstraint("source_end_sample_offset >= source_start_sample_offset", name="ck_transcript_stream_windows_offsets_ordered"),
        sa.CheckConstraint("forwarded_sample_count >= 0 AND forwarded_sample_count <= 480000", name="ck_transcript_stream_windows_forwarded_samples_range"),
        sa.CheckConstraint("final_segment_count >= 0", name="ck_transcript_stream_windows_final_segment_count_nonnegative"),
        sa.CheckConstraint("text_digest IS NULL OR text_digest ~ '^[0-9a-f]{64}$'", name="ck_transcript_stream_windows_digest_canonical"),
        sa.CheckConstraint("recovery_audio_expires_at IS NULL OR recovery_audio_stored_at IS NOT NULL", name="ck_transcript_stream_windows_recovery_expiry_shape"),
        sa.ForeignKeyConstraint(["session_id"], ["transcript_stream_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "window_no", name="uq_transcript_stream_windows_session_window"),
    )

    op.create_table(
        "transcript_stream_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_no", sa.Integer(), nullable=False),
        sa.Column("source_sample_offset", sa.BigInteger(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("pcm_digest", sa.String(length=64), nullable=False),
        sa.Column("provider_sample_start", sa.BigInteger(), nullable=False),
        sa.Column("provider_sample_end", sa.BigInteger(), nullable=False),
        sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", delivery_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_sample_offset >= 0", name="ck_transcript_stream_deliveries_offset_nonnegative"),
        sa.CheckConstraint("sample_count BETWEEN 1 AND 16000", name="ck_transcript_stream_deliveries_sample_count_range"),
        sa.CheckConstraint("window_no >= 0", name="ck_transcript_stream_deliveries_window_nonnegative"),
        sa.CheckConstraint("provider_sample_start >= 0 AND provider_sample_end > provider_sample_start", name="ck_transcript_stream_deliveries_provider_offsets_ordered"),
        sa.CheckConstraint("pcm_digest ~ '^[0-9a-f]{64}$'", name="ck_transcript_stream_deliveries_pcm_digest_canonical"),
        sa.ForeignKeyConstraint(["session_id"], ["transcript_stream_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_attempt_id"], ["provider_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "source_sample_offset", name="uq_transcript_stream_deliveries_session_source_offset"),
    )
    op.create_index("ix_transcript_stream_deliveries_session_window", "transcript_stream_deliveries", ["session_id", "window_no", "source_sample_offset"])

    op.add_column("transcript_ingestion_jobs", sa.Column("transcript_stream_window_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_transcript_ingestion_jobs_stream_window", "transcript_ingestion_jobs", "transcript_stream_windows",
        ["transcript_stream_window_id"], ["id"], ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_transcript_ingestion_jobs_stream_window", "transcript_ingestion_jobs", ["transcript_stream_window_id"])

    op.add_column("provider_attempts", sa.Column("stream_received_samples", sa.BigInteger(), nullable=True))
    op.add_column("provider_attempts", sa.Column("stream_authorized_samples", sa.BigInteger(), nullable=True))
    op.add_column("provider_attempts", sa.Column("transcript_stream_session_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_provider_attempts_stream_session", "provider_attempts", "transcript_stream_sessions",
        ["transcript_stream_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_provider_attempts_stream_session_status", "provider_attempts", ["transcript_stream_session_id", "status"])

    for name in ("ck_provider_attempts_settlement_basis_shape", "ck_provider_attempts_resource_payload_shape"):
        op.drop_constraint(name, "provider_attempts", type_="check")
    op.create_check_constraint(
        "ck_provider_attempts_stream_received_samples_nonnegative", "provider_attempts",
        "stream_received_samples IS NULL OR stream_received_samples >= 0",
    )
    op.create_check_constraint(
        "ck_provider_attempts_stream_authorized_samples_positive", "provider_attempts",
        "stream_authorized_samples IS NULL OR stream_authorized_samples > 0",
    )
    op.create_check_constraint(
        "ck_provider_attempts_settlement_basis_shape", "provider_attempts",
        "((settlement_basis = 'reported' AND resource = 'tokens' AND reported_total_tokens = settled_units) OR "
        "(settlement_basis = 'measured' AND resource = 'audio_seconds' AND measured_audio_seconds > 0) OR "
        "(settlement_basis = 'conservative_unknown' AND resource = 'tokens' AND reported_total_tokens IS NULL "
        "AND settled_units = reserved_units AND outcome = 'unknown') OR "
        "(settlement_basis = 'stream_authorized' AND resource = 'audio_seconds' AND attempt_kind = 'stt_deepgram_live_stream' "
        "AND measured_audio_seconds IS NULL AND settled_units = reserved_units AND outcome = 'unknown') OR "
        "(settlement_basis = 'stream_measured' AND resource = 'audio_seconds' AND attempt_kind = 'stt_deepgram_live_stream' "
        "AND measured_audio_seconds IS NULL AND stream_authorized_samples IS NOT NULL "
        "AND settled_units = ((stream_authorized_samples + 15999) / 16000) AND outcome = 'succeeded') OR settlement_basis IS NULL) IS TRUE",
    )
    op.create_check_constraint(
        "ck_provider_attempts_resource_payload_shape", "provider_attempts",
        "((resource = 'tokens' AND measured_audio_seconds IS NULL) OR "
        "(resource = 'audio_seconds' AND reported_input_tokens IS NULL AND reported_output_tokens IS NULL "
        "AND reported_total_tokens IS NULL AND ((attempt_kind = 'stt_deepgram_live_stream' AND measured_audio_seconds IS NULL "
        "AND stream_received_samples IS NOT NULL AND stream_authorized_samples IS NOT NULL "
        "AND reserved_units = ((stream_authorized_samples + 15999) / 16000)) OR "
        "(attempt_kind <> 'stt_deepgram_live_stream' AND measured_audio_seconds > 0 "
        "AND stream_received_samples IS NULL AND stream_authorized_samples IS NULL)))) IS TRUE",
    )
    op.create_check_constraint(
        "ck_provider_attempts_stream_source_shape", "provider_attempts",
        "((attempt_kind = 'stt_deepgram_live_stream' AND resource = 'audio_seconds' "
        "AND transcript_ingestion_job_id IS NULL "
        "AND generated_document_id IS NULL AND consultation_split_execution_id IS NULL) OR "
        "(attempt_kind <> 'stt_deepgram_live_stream' AND transcript_stream_session_id IS NULL "
        "AND stream_received_samples IS NULL AND stream_authorized_samples IS NULL)) IS TRUE",
    )


def downgrade() -> None:
    op.drop_constraint("ck_provider_attempts_stream_source_shape", "provider_attempts", type_="check")
    for name in ("ck_provider_attempts_stream_received_samples_nonnegative", "ck_provider_attempts_stream_authorized_samples_positive"):
        op.drop_constraint(name, "provider_attempts", type_="check")
    op.drop_constraint("ck_provider_attempts_resource_payload_shape", "provider_attempts", type_="check")
    op.drop_constraint("ck_provider_attempts_settlement_basis_shape", "provider_attempts", type_="check")
    op.create_check_constraint(
        "ck_provider_attempts_settlement_basis_shape", "provider_attempts",
        "((settlement_basis = 'reported' AND resource = 'tokens' AND reported_total_tokens = settled_units) OR "
        "(settlement_basis = 'measured' AND resource = 'audio_seconds' AND measured_audio_seconds > 0) OR "
        "(settlement_basis = 'conservative_unknown' AND resource = 'tokens' AND reported_total_tokens IS NULL "
        "AND settled_units = reserved_units AND outcome = 'unknown') OR settlement_basis IS NULL) IS TRUE",
    )
    op.create_check_constraint(
        "ck_provider_attempts_resource_payload_shape", "provider_attempts",
        "((resource = 'tokens' AND measured_audio_seconds IS NULL) OR "
        "(resource = 'audio_seconds' AND measured_audio_seconds > 0 AND reported_input_tokens IS NULL "
        "AND reported_output_tokens IS NULL AND reported_total_tokens IS NULL)) IS TRUE",
    )
    op.drop_index("ix_provider_attempts_stream_session_status", table_name="provider_attempts")
    op.drop_constraint("fk_provider_attempts_stream_session", "provider_attempts", type_="foreignkey")
    for name in ("transcript_stream_session_id", "stream_authorized_samples", "stream_received_samples"):
        op.drop_column("provider_attempts", name)
    op.drop_constraint("uq_transcript_ingestion_jobs_stream_window", "transcript_ingestion_jobs", type_="unique")
    op.drop_constraint("fk_transcript_ingestion_jobs_stream_window", "transcript_ingestion_jobs", type_="foreignkey")
    op.drop_column("transcript_ingestion_jobs", "transcript_stream_window_id")
    op.drop_index("ix_transcript_stream_deliveries_session_window", table_name="transcript_stream_deliveries")
    op.drop_table("transcript_stream_deliveries")
    op.drop_table("transcript_stream_windows")
    op.drop_index("ix_transcript_stream_sessions_owner_state", table_name="transcript_stream_sessions")
    op.drop_index("uq_transcript_stream_sessions_open_transcript", table_name="transcript_stream_sessions")
    op.drop_table("transcript_stream_sessions")
    for enum_type in (delivery_status, window_status, stream_status):
        enum_type.drop(op.get_bind(), checkfirst=True)
    # PostgreSQL enum values are append-only; the new quota enum values remain unused after downgrade.
