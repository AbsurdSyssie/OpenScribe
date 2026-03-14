"""add team name key and email normalization

Revision ID: d9e8e4d5e6f7
Revises: 2789ef68805c
Create Date: 2026-03-12 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9e8e4d5e6f7"
down_revision: Union[str, None] = "2789ef68805c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("name_key", sa.String(length=255), nullable=True))

    op.execute(
        """
        UPDATE teams
        SET name_key = lower(regexp_replace(btrim(name), '\s+', ' ', 'g'))
        """
    )
    op.alter_column("teams", "name_key", nullable=False)
    op.create_unique_constraint("uq_teams_name_key", "teams", ["name_key"])

    op.execute(
        """
        UPDATE users
        SET email = lower(btrim(email))
        """
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_constraint("uq_teams_name_key", "teams", type_="unique")
    op.drop_column("teams", "name_key")
