"""split stt selection and add ingestion jobs

Revision ID: g7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-17 23:45:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "g7a8b9c0d1e2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


transcript_ingestion_job_kind = postgresql.ENUM(
    "live_chunk",
    "audio_file",
    name="transcriptingestionjobkind",
)
transcript_ingestion_job_kind_existing = postgresql.ENUM(
    "live_chunk",
    "audio_file",
    name="transcriptingestionjobkind",
    create_type=False,
)

transcript_ingestion_job_status = postgresql.ENUM(
    "queued",
    "processing",
    "completed",
    "applied",
    "failed",
    name="transcriptingestionjobstatus",
)
transcript_ingestion_job_status_existing = postgresql.ENUM(
    "queued",
    "processing",
    "completed",
    "applied",
    "failed",
    name="transcriptingestionjobstatus",
    create_type=False,
)


def upgrade() -> None:
    op.drop_constraint("uq_team_stt_configs_team_id", "team_stt_configs", type_="unique")
    op.add_column(
        "team_stt_configs",
        sa.Column("available_models_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("team_stt_configs", "available_models_json", server_default=None)

    op.create_table(
        "team_stt_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stt_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_name_override", sa.String(length=255), nullable=True),
        sa.Column("language_override", sa.String(length=32), nullable=True),
        sa.Column("selected_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["selected_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["stt_config_id"], ["team_stt_configs.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", name="uq_team_stt_selections_team_id"),
    )

    op.add_column(
        "transcripts",
        sa.Column(
            "next_live_chunk_sequence_no_applied",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.alter_column("transcripts", "next_live_chunk_sequence_no_applied", server_default=None)

    transcript_ingestion_job_kind.create(op.get_bind(), checkfirst=False)
    transcript_ingestion_job_status.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "transcript_ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_kind", transcript_ingestion_job_kind_existing, nullable=False),
        sa.Column("chunk_sequence_no", sa.Integer(), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("status", transcript_ingestion_job_status_existing, nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("result_text_encrypted", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transcript_id", "chunk_sequence_no", name="uq_transcript_ingestion_job_chunk_sequence"),
    )


def downgrade() -> None:
    op.drop_table("transcript_ingestion_jobs")
    transcript_ingestion_job_status_existing.drop(op.get_bind(), checkfirst=False)
    transcript_ingestion_job_kind_existing.drop(op.get_bind(), checkfirst=False)
    op.drop_column("transcripts", "next_live_chunk_sequence_no_applied")
    op.drop_table("team_stt_selections")
    op.drop_column("team_stt_configs", "available_models_json")
    op.create_unique_constraint("uq_team_stt_configs_team_id", "team_stt_configs", ["team_id"])
