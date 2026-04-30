"""add personal smart phrases

Revision ID: 20260430_001
Revises: c0d1e2f3a4b6
Create Date: 2026-04-30
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260430_001"
down_revision = "c0d1e2f3a4b6"
branch_labels = None
depends_on = None


DEFAULT_TRIGGER = "CESRF"
DEFAULT_EXPANSION = (
    "Discussed cauda equina red flags with the patient, including new saddle numbness, "
    "new bladder or bowel dysfunction, new leg weakness, or worsening bilateral sciatica. "
    "Advised them to seek urgent medical attention if these symptoms develop."
)
DEFAULT_DESCRIPTION = "Cauda equina red flags safety-netting"


def upgrade() -> None:
    op.create_table(
        "smart_phrases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(length=64), nullable=False),
        sa.Column("expansion_text", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("trigger ~ '^[A-Z0-9_]{1,64}$'", name="ck_smart_phrases_trigger_format"),
        sa.CheckConstraint("char_length(expansion_text) BETWEEN 1 AND 2000", name="ck_smart_phrases_expansion_length"),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 255", name="ck_smart_phrases_description_length"),
    )
    op.create_index(
        "uq_smart_phrases_owner_trigger_lower",
        "smart_phrases",
        ["owner_user_id", sa.text("lower(trigger)")],
        unique=True,
    )

    connection = op.get_bind()
    user_rows = connection.execute(
        sa.text(
            """
            SELECT id
            FROM users
            WHERE is_system_admin IS FALSE
              AND team_id IS NOT NULL
            """
        )
    ).mappings()
    for row in user_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO smart_phrases (
                    id,
                    owner_user_id,
                    trigger,
                    expansion_text,
                    description,
                    times_used,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :owner_user_id,
                    :trigger,
                    :expansion,
                    :description,
                    0,
                    now(),
                    now()
                )
                """
            ),
            {
                "id": str(uuid4()),
                "owner_user_id": str(row["id"]),
                "trigger": DEFAULT_TRIGGER,
                "expansion": DEFAULT_EXPANSION,
                "description": DEFAULT_DESCRIPTION,
            },
        )


def downgrade() -> None:
    op.drop_index("uq_smart_phrases_owner_trigger_lower", table_name="smart_phrases")
    op.drop_table("smart_phrases")
