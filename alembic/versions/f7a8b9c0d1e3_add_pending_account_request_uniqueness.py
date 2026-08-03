"""Add pending account-request uniqueness.

Revision ID: f7a8b9c0d1e3
Revises: e4f5a6b7c8d9
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e3"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_account_requests_pending_email_team"


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            """
            SELECT requested_email, requested_team_name_key
            FROM account_requests
            WHERE status = 'pending'
            GROUP BY requested_email, requested_team_name_key
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot add pending account-request uniqueness while duplicate pending requests exist; review and resolve them first"
        )
    op.create_index(
        INDEX_NAME,
        "account_requests",
        ["requested_email", "requested_team_name_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="account_requests")
