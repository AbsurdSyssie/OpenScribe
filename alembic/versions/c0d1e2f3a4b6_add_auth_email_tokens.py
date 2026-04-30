"""add auth email tokens

Revision ID: c0d1e2f3a4b6
Revises: b8d9e0f1a2c3
Create Date: 2026-04-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c0d1e2f3a4b6"
down_revision: Union[str, None] = "b8d9e0f1a2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


auth_email_token_purpose = postgresql.ENUM(
    "account_activation",
    "password_reset",
    "manager_password_reset",
    "manager_account_recovery",
    name="authemailtokenpurpose",
    create_type=False,
)


def upgrade() -> None:
    auth_email_token_purpose.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "auth_email_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", auth_email_token_purpose, nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_email_tokens_token_hash"),
    )
    op.create_index("ix_auth_email_tokens_user_purpose", "auth_email_tokens", ["user_id", "purpose"])


def downgrade() -> None:
    op.drop_index("ix_auth_email_tokens_user_purpose", table_name="auth_email_tokens")
    op.drop_table("auth_email_tokens")
    auth_email_token_purpose.drop(op.get_bind(), checkfirst=True)
