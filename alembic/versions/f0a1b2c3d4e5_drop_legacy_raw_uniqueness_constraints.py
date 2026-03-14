"""drop legacy raw uniqueness constraints

Revision ID: f0a1b2c3d4e5
Revises: d9e8e4d5e6f7
Create Date: 2026-03-12 22:18:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "d9e8e4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("teams_name_key", "teams", type_="unique")
    op.drop_constraint("users_email_key", "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("teams_name_key", "teams", ["name"])
    op.create_unique_constraint("users_email_key", "users", ["email"])
