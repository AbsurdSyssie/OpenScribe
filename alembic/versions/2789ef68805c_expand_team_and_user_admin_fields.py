"""expand team and user admin fields

Revision ID: 2789ef68805c
Revises: 19f69510e6b1
Create Date: 2026-03-12 21:41:51.584160

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2789ef68805c'
down_revision: Union[str, None] = '19f69510e6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    team_status = sa.Enum("active", "suspended", name="teamstatus")
    team_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "teams",
        sa.Column(
            "status",
            team_status,
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.add_column(
        "teams",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("teams", "status", server_default=None)
    op.alter_column("teams", "updated_at", server_default=None)
    op.alter_column("users", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "updated_at")
    op.drop_column("teams", "updated_at")
    op.drop_column("teams", "status")
    sa.Enum(name="teamstatus").drop(op.get_bind(), checkfirst=True)
