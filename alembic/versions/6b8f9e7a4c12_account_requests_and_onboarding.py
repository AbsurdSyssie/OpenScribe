"""account requests and onboarding

Revision ID: 6b8f9e7a4c12
Revises: f0a1b2c3d4e5
Create Date: 2026-03-14 14:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "6b8f9e7a4c12"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


user_onboarding_state = postgresql.ENUM(
    "pending_password_change",
    "pending_totp_enrollment",
    "pending_recovery_codes",
    "complete",
    name="useronboardingstate",
    create_type=False,
)
account_request_status = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    "reviewed",
    "withdrawn",
    name="accountrequeststatus",
    create_type=False,
)
session_auth_level = postgresql.ENUM("onboarding", "full", name="sessionauthlevel", create_type=False)
session_status = postgresql.ENUM("active", "revoked", "expired", name="sessionstatus", create_type=False)
mfa_method_type = postgresql.ENUM("totp", name="mfamethodtype", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    user_onboarding_state.create(bind, checkfirst=True)
    account_request_status.create(bind, checkfirst=True)
    session_auth_level.create(bind, checkfirst=True)
    session_status.create(bind, checkfirst=True)
    mfa_method_type.create(bind, checkfirst=True)

    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column(
        "users",
        sa.Column(
            "onboarding_state",
            user_onboarding_state,
            server_default="complete",
            nullable=False,
        ),
    )

    op.create_table(
        "account_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_name", sa.String(length=255), nullable=False),
        sa.Column("requested_email", sa.String(length=320), nullable=False),
        sa.Column("requested_team_name", sa.String(length=255), nullable=False),
        sa.Column("requested_team_name_key", sa.String(length=255), nullable=False),
        sa.Column("request_details", sa.Text(), nullable=True),
        sa.Column("status", account_request_status, nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("linked_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["linked_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_token_hash", sa.String(length=128), nullable=False),
        sa.Column("auth_level", session_auth_level, nullable=False),
        sa.Column("status", session_status, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_token_hash"),
    )

    op.create_table(
        "user_mfa_methods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method_type", mfa_method_type, nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.alter_column("users", "must_change_password", server_default=None)
    op.alter_column("users", "onboarding_state", server_default=None)


def downgrade() -> None:
    op.drop_table("user_recovery_codes")
    op.drop_table("user_mfa_methods")
    op.drop_table("user_sessions")
    op.drop_table("account_requests")
    op.drop_column("users", "onboarding_state")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "full_name")

    bind = op.get_bind()
    mfa_method_type.drop(bind, checkfirst=True)
    session_status.drop(bind, checkfirst=True)
    session_auth_level.drop(bind, checkfirst=True)
    account_request_status.drop(bind, checkfirst=True)
    user_onboarding_state.drop(bind, checkfirst=True)
