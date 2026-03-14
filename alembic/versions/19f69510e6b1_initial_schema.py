"""initial schema

Revision ID: 19f69510e6b1
Revises: 
Create Date: 2026-03-12 17:29:46.427648

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '19f69510e6b1'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    team_role = sa.Enum("leader", "user", name="teamrole")
    user_status = sa.Enum("active", "locked", "disabled", name="userstatus")
    transcript_status = sa.Enum("recording", "transcribing", "ready", "failed", name="transcriptstatus")

    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("default_retention_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("team_role", team_role, nullable=True),
        sa.Column("is_system_admin", sa.Boolean(), nullable=False),
        sa.Column("status", user_status, nullable=False),
        sa.Column("mfa_required", sa.Boolean(), nullable=False),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("current_draft_text_encrypted", sa.Text(), nullable=True),
        sa.Column("status", transcript_status, nullable=False),
        sa.Column("retention_days_applied", sa.Integer(), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transcript_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("text_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transcript_id", "version_no", name="uq_transcript_version_number"),
    )


def downgrade() -> None:
    op.drop_table("transcript_versions")
    op.drop_table("transcripts")
    op.drop_table("users")
    op.drop_table("teams")
    sa.Enum(name="transcriptstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="userstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="teamrole").drop(op.get_bind(), checkfirst=True)
