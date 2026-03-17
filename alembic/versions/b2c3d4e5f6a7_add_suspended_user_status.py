"""add suspended user status

Revision ID: b2c3d4e5f6a7
Revises: a1c7d9e3f4b2
Create Date: 2026-03-14 16:10:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1c7d9e3f4b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'suspended'")


def downgrade() -> None:
    op.execute("ALTER TYPE userstatus RENAME TO userstatus_old")
    op.execute("CREATE TYPE userstatus AS ENUM ('active', 'locked', 'disabled')")
    op.execute(
        """
        ALTER TABLE users
        ALTER COLUMN status TYPE userstatus
        USING (
            CASE
                WHEN status::text = 'suspended' THEN 'disabled'
                ELSE status::text
            END
        )::userstatus
        """
    )
    op.execute("DROP TYPE userstatus_old")
