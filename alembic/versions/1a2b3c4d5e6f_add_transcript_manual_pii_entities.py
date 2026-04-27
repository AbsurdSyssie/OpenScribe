"""add transcript manual pii entities

Revision ID: 1a2b3c4d5e6f
Revises: 0a1b2c3d4e6f
Create Date: 2026-04-23 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transcript_manual_pii_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=255), nullable=False),
        sa.Column("original_value_encrypted", sa.Text(), nullable=False),
        sa.Column("normalized_value_hash", sa.String(length=64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("occurrence_count > 0", name="ck_transcript_manual_pii_occurrence_count_positive"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transcript_id", "entity_type", "normalized_value_hash", name="uq_transcript_manual_pii_entity_value"),
    )
    op.create_index(
        "ix_transcript_manual_pii_entities_owner_transcript",
        "transcript_manual_pii_entities",
        ["owner_user_id", "transcript_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transcript_manual_pii_entities_owner_transcript", table_name="transcript_manual_pii_entities")
    op.drop_table("transcript_manual_pii_entities")
