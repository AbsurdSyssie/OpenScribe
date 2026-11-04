"""Add owner-scoped dual-source telehealth capture sessions and parts.

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telehealth_capture_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stt_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("timeline_sample_rate", sa.Integer(), nullable=False),
        sa.Column("failure_channel", sa.String(length=16), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("incomplete_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recording_stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_telehealth_capture_sessions"),
        sa.ForeignKeyConstraint(
            ["transcript_id"], ["transcripts.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_sessions_transcript",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_sessions_owner_user",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_sessions_team",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'stopping', 'interrupted', 'ready', 'processing', 'completed', 'failed')",
            name="ck_telehealth_capture_sessions_status",
        ),
        sa.CheckConstraint(
            "timeline_sample_rate >= 8000 AND timeline_sample_rate <= 192000",
            name="ck_telehealth_capture_sessions_sample_rate",
        ),
        sa.CheckConstraint(
            "failure_channel IS NULL OR failure_channel IN ('clinician', 'patient')",
            name="ck_telehealth_capture_sessions_failure_channel",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR char_length(failure_code) <= 128",
            name="ck_telehealth_capture_sessions_failure_code_length",
        ),
    )
    op.create_index(
        "uq_telehealth_capture_sessions_open_transcript",
        "telehealth_capture_sessions",
        ["transcript_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'stopping')"),
    )
    op.create_index(
        "ix_telehealth_capture_sessions_owner_state",
        "telehealth_capture_sessions", ["owner_user_id", "status", "updated_at"],
    )

    op.create_table(
        "telehealth_capture_parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("timeline_start_frame", sa.BigInteger(), nullable=False),
        sa.Column("timeline_end_frame", sa.BigInteger(), nullable=False),
        sa.Column("timeline_sample_rate", sa.Integer(), nullable=False),
        sa.Column("file_sample_rate", sa.Integer(), nullable=False),
        sa.Column("file_samples", sa.Integer(), nullable=False),
        sa.Column("timing_spans_json", sa.JSON(), nullable=False),
        sa.Column("source_audio_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_audio_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_audio_duration_seconds", sa.Float(), nullable=False),
        sa.Column("source_audio_vault_ref", sa.String(length=512), nullable=True),
        sa.Column("source_audio_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_audio_expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_telehealth_capture_parts"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["telehealth_capture_sessions.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_parts_session",
        ),
        sa.ForeignKeyConstraint(
            ["transcript_id"], ["transcripts.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_parts_transcript",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_parts_owner_user",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE",
            name="fk_telehealth_capture_parts_team",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id"], ["transcript_ingestion_jobs.id"], ondelete="SET NULL",
            name="fk_telehealth_capture_parts_ingestion_job",
        ),
        sa.UniqueConstraint(
            "session_id", "channel", "sequence_no",
            name="uq_telehealth_capture_parts_channel_sequence",
        ),
        sa.UniqueConstraint(
            "session_id", "idempotency_key",
            name="uq_telehealth_capture_parts_idempotency",
        ),
        sa.UniqueConstraint(
            "ingestion_job_id",
            name="uq_telehealth_capture_parts_ingestion_job",
        ),
        sa.CheckConstraint(
            "channel IN ('clinician', 'patient')",
            name="ck_telehealth_capture_parts_channel",
        ),
        sa.CheckConstraint(
            "status IN ('staged', 'processing', 'transcribed', 'failed', 'expired')",
            name="ck_telehealth_capture_parts_status",
        ),
        sa.CheckConstraint(
            "sequence_no >= 0 AND sequence_no <= 100000",
            name="ck_telehealth_capture_parts_sequence",
        ),
        sa.CheckConstraint(
            "timeline_start_frame >= 0 AND timeline_end_frame > timeline_start_frame",
            name="ck_telehealth_capture_parts_timeline_range",
        ),
        sa.CheckConstraint(
            "file_samples > 0 AND source_audio_size_bytes > 0",
            name="ck_telehealth_capture_parts_size_positive",
        ),
        sa.CheckConstraint(
            "source_audio_duration_seconds > 0",
            name="ck_telehealth_capture_parts_audio_duration_positive",
        ),
        sa.CheckConstraint(
            "source_audio_vault_ref IS NULL OR source_audio_expires_at IS NOT NULL",
            name="ck_telehealth_capture_parts_source_expiry",
        ),
    )
    op.create_index(
        "ix_telehealth_capture_parts_transcript_owner",
        "telehealth_capture_parts", ["transcript_id", "owner_user_id"],
    )
    op.create_index(
        "ix_telehealth_capture_parts_source_expiry",
        "telehealth_capture_parts", ["source_audio_expires_at"],
        postgresql_where=sa.text("source_audio_vault_ref IS NOT NULL"),
    )


def downgrade() -> None:
    # Cannot silently discard owner capture/part metadata or leave Vault
    # source references behind on an operator downgrade.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM telehealth_capture_sessions) THEN
                RAISE EXCEPTION 'Cannot downgrade with telehealth capture sessions present';
            END IF;
        END $$;
    """))
    op.drop_index("ix_telehealth_capture_parts_source_expiry", table_name="telehealth_capture_parts")
    op.drop_index("ix_telehealth_capture_parts_transcript_owner", table_name="telehealth_capture_parts")
    op.drop_table("telehealth_capture_parts")
    op.drop_index("ix_telehealth_capture_sessions_owner_state", table_name="telehealth_capture_sessions")
    op.drop_index("uq_telehealth_capture_sessions_open_transcript", table_name="telehealth_capture_sessions")
    op.drop_table("telehealth_capture_sessions")
