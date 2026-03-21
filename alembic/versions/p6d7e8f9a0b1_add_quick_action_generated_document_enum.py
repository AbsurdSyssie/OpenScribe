"""add quick action generated document enum value

Revision ID: p6d7e8f9a0b1
Revises: o5c6d7e8f9a0
Create Date: 2026-03-20 16:40:00.000000
"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "p6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = "o5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE generateddocumentgeneratortype ADD VALUE IF NOT EXISTS 'quick_action'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally not automated here.
    pass
