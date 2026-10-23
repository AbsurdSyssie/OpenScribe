"""add encrypted structured transcript turns and frozen diarisation settings

Revision ID: e8f9a0b1c2d3
Revises: d4e5f6a7b8ca
Create Date: 2026-10-22 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "e8f9a0b1c2d3"
down_revision = "d4e5f6a7b8ca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_suggestion_jobs", sa.Column("excerpt_turns_snapshot_encrypted", sa.Text(), nullable=True))
    op.create_table(
        "transcript_capture_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transcript_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("stt_config_id", sa.Uuid(), nullable=False),
        sa.Column("stt_contract_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("stt_diarisation_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("stt_diarization_batch_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("stt_diarization_field_name", sa.String(length=255), nullable=True),
        sa.Column("stt_diarization_on_value", sa.String(length=64), server_default="true", nullable=False),
        sa.Column("stt_diarization_off_value", sa.String(length=64), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transcript_capture_settings_transcript_created",
        "transcript_capture_settings", ["transcript_id", "created_at"], unique=False,
    )
    op.add_column(
        "transcript_ingestion_jobs",
        sa.Column("capture_settings_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_transcript_ingestion_jobs_capture_settings_id",
        "transcript_ingestion_jobs", "transcript_capture_settings",
        ["capture_settings_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("transcripts", sa.Column("current_draft_turns_encrypted", sa.Text(), nullable=True))
    op.add_column("transcript_stream_windows", sa.Column("turns_digest", sa.String(length=64), nullable=True))
    op.add_column("transcript_versions", sa.Column("structured_turns_encrypted", sa.Text(), nullable=True))
    op.add_column("transcript_versions", sa.Column("structured_turns_schema_version", sa.Integer(), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("result_turns_encrypted", sa.Text(), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_diarisation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_diarization_batch_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_diarization_field_name", sa.String(length=255), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_diarization_on_value", sa.String(length=64), nullable=False, server_default="true"))
    op.add_column("transcript_ingestion_jobs", sa.Column("stt_diarization_off_value", sa.String(length=64), nullable=False, server_default="false"))
    op.add_column("transcript_stream_sessions", sa.Column("stt_diarisation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("team_stt_configs", sa.Column("diarization_live_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("team_stt_configs", sa.Column("diarization_batch_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("team_stt_configs", sa.Column("diarization_field_name", sa.String(length=255), nullable=True))
    op.add_column("team_stt_configs", sa.Column("diarization_on_value", sa.String(length=64), nullable=False, server_default="true"))
    op.add_column("team_stt_configs", sa.Column("diarization_off_value", sa.String(length=64), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_constraint("fk_transcript_ingestion_jobs_capture_settings_id", "transcript_ingestion_jobs", type_="foreignkey")
    op.drop_column("transcript_ingestion_jobs", "capture_settings_id")
    op.drop_index("ix_transcript_capture_settings_transcript_created", table_name="transcript_capture_settings")
    op.drop_table("transcript_capture_settings")
    op.drop_column("template_suggestion_jobs", "excerpt_turns_snapshot_encrypted")
    op.drop_column("team_stt_configs", "diarization_off_value")
    op.drop_column("team_stt_configs", "diarization_on_value")
    op.drop_column("team_stt_configs", "diarization_field_name")
    op.drop_column("team_stt_configs", "diarization_batch_enabled")
    op.drop_column("team_stt_configs", "diarization_live_enabled")
    op.drop_column("transcript_stream_sessions", "stt_diarisation_enabled")
    op.drop_column("transcript_stream_windows", "turns_digest")
    op.drop_column("transcript_ingestion_jobs", "stt_diarization_off_value")
    op.drop_column("transcript_ingestion_jobs", "stt_diarization_batch_enabled")
    op.drop_column("transcript_ingestion_jobs", "stt_diarization_on_value")
    op.drop_column("transcript_ingestion_jobs", "stt_diarization_field_name")
    op.drop_column("transcript_ingestion_jobs", "stt_diarisation_enabled")
    op.drop_column("transcript_ingestion_jobs", "result_turns_encrypted")
    op.drop_column("transcript_versions", "structured_turns_schema_version")
    op.drop_column("transcript_versions", "structured_turns_encrypted")
    op.drop_column("transcripts", "current_draft_turns_encrypted")
