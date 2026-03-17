"""add stt adapter kind

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-17 20:05:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


sttadapterkind = postgresql.ENUM("generic_rest", "openai_transcription", name="sttadapterkind")
sttadapterkind_existing = postgresql.ENUM("generic_rest", "openai_transcription", name="sttadapterkind", create_type=False)


def upgrade() -> None:
    sttadapterkind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "team_stt_configs",
        sa.Column("adapter_kind", sttadapterkind_existing, nullable=False, server_default="generic_rest"),
    )
    op.alter_column("team_stt_configs", "adapter_kind", server_default=None)


def downgrade() -> None:
    op.drop_column("team_stt_configs", "adapter_kind")
    sttadapterkind.drop(op.get_bind(), checkfirst=True)
