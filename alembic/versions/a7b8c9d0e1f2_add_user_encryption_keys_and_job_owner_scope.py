"""add user encryption keys and job owner scope

Revision ID: a7b8c9d0e1f2
Revises: z6b7c8d9e0f1
Create Date: 2026-04-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7b8c9d0e1f2"
down_revision = "z6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_encryption_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("wrapped_dek", sa.Text(), nullable=False),
        sa.Column("kek_mount", sa.String(length=64), nullable=False),
        sa.Column("kek_key_name", sa.String(length=255), nullable=False),
        sa.Column("kek_key_version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_encryption_keys_user"),
    )

    op.add_column("transcript_ingestion_jobs", sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("transcript_ingestion_jobs", sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_transcript_ingestion_jobs_owner_user_id_users",
        "transcript_ingestion_jobs",
        "users",
        ["owner_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_transcript_ingestion_jobs_team_id_teams",
        "transcript_ingestion_jobs",
        "teams",
        ["team_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE transcript_ingestion_jobs AS jobs
        SET owner_user_id = transcripts.owner_user_id,
            team_id = transcripts.team_id
        FROM transcripts
        WHERE transcripts.id = jobs.transcript_id
        """
    )
    op.alter_column("transcript_ingestion_jobs", "owner_user_id", nullable=False)
    op.alter_column("transcript_ingestion_jobs", "team_id", nullable=False)


def downgrade() -> None:
    op.drop_constraint("fk_transcript_ingestion_jobs_team_id_teams", "transcript_ingestion_jobs", type_="foreignkey")
    op.drop_constraint("fk_transcript_ingestion_jobs_owner_user_id_users", "transcript_ingestion_jobs", type_="foreignkey")
    op.drop_column("transcript_ingestion_jobs", "team_id")
    op.drop_column("transcript_ingestion_jobs", "owner_user_id")
    op.drop_table("user_encryption_keys")
