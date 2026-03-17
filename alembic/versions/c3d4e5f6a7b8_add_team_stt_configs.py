"""add team stt configs

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-14 18:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


sttauthmode = postgresql.ENUM("bearer", name="sttauthmode")
sttauthmode_existing = postgresql.ENUM("bearer", name="sttauthmode", create_type=False)


def upgrade() -> None:
    sttauthmode.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "team_stt_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("transcribe_path", sa.String(length=255), nullable=False),
        sa.Column("auth_mode", sttauthmode_existing, nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("file_field_name", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("response_text_path", sa.String(length=255), nullable=False),
        sa.Column("extra_form_fields_json", sa.JSON(), nullable=False),
        sa.Column("vault_secret_ref", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", name="uq_team_stt_configs_team_id"),
    )


def downgrade() -> None:
    op.drop_table("team_stt_configs")
    sttauthmode.drop(op.get_bind(), checkfirst=True)
