"""Add finite team quota defaults.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


TEAM_QUOTA_DEFAULTS = (
    ("default_daily_token_limit", 100_000, 10_000_000),
    ("default_monthly_token_limit", 1_000_000, 100_000_000),
    ("default_daily_audio_seconds_limit", 28_800, 24 * 3600),
    ("default_monthly_audio_seconds_limit", 259_200, 1_000 * 3600),
)


def upgrade() -> None:
    for column_name, default_value, maximum in TEAM_QUOTA_DEFAULTS:
        op.add_column(
            "teams",
            sa.Column(column_name, sa.Integer(), server_default=str(default_value), nullable=False),
        )
        op.create_check_constraint(
            f"ck_teams_{column_name}_range",
            "teams",
            f"{column_name} >= 0 AND {column_name} <= {maximum}",
        )


def downgrade() -> None:
    for column_name, _, _ in reversed(TEAM_QUOTA_DEFAULTS):
        op.drop_constraint(f"ck_teams_{column_name}_range", "teams", type_="check")
        op.drop_column("teams", column_name)
