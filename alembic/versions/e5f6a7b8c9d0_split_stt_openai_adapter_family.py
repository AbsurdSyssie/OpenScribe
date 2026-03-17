"""split stt openai adapter family

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-17 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


old_stt_adapter_kind = postgresql.ENUM(
    "generic_rest",
    "openai_transcription",
    name="sttadapterkind",
    create_type=False,
)
new_stt_adapter_kind = postgresql.ENUM(
    "generic_rest",
    "openai_cloud",
    "openai_compatible_rest",
    name="sttadapterkind_new",
)
new_stt_adapter_kind_existing = postgresql.ENUM(
    "generic_rest",
    "openai_cloud",
    "openai_compatible_rest",
    name="sttadapterkind_new",
    create_type=False,
)


def upgrade() -> None:
    new_stt_adapter_kind.create(op.get_bind(), checkfirst=False)
    op.execute(
        """
        ALTER TABLE team_stt_configs
        ALTER COLUMN adapter_kind
        TYPE sttadapterkind_new
        USING (
            CASE
                WHEN adapter_kind::text = 'openai_transcription' THEN 'openai_compatible_rest'
                ELSE adapter_kind::text
            END
        )::sttadapterkind_new
        """
    )
    old_stt_adapter_kind.drop(op.get_bind(), checkfirst=False)
    op.execute("ALTER TYPE sttadapterkind_new RENAME TO sttadapterkind")


def downgrade() -> None:
    old_stt_adapter_kind.create(op.get_bind(), checkfirst=False)
    op.execute(
        """
        ALTER TABLE team_stt_configs
        ALTER COLUMN adapter_kind
        TYPE sttadapterkind
        USING (
            CASE
                WHEN adapter_kind::text IN ('openai_cloud', 'openai_compatible_rest') THEN 'openai_transcription'
                ELSE adapter_kind::text
            END
        )::sttadapterkind
        """
    )
    new_stt_adapter_kind_existing.drop(op.get_bind(), checkfirst=False)
