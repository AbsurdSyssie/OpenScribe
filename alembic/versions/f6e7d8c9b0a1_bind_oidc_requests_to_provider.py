"""bind OIDC authorization requests to a provider

Revision ID: f6e7d8c9b0a1
Revises: d1e2f3a4b5c6
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "f6e7d8c9b0a1"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These rows are short-lived login transactions. They predate provider
    # binding and cannot be assigned safely, so invalidate them during deploy.
    op.execute(sa.text("DELETE FROM oidc_authorization_requests"))
    op.add_column(
        "oidc_authorization_requests",
        sa.Column("provider_key", sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM oidc_authorization_requests"))
    op.drop_column("oidc_authorization_requests", "provider_key")
