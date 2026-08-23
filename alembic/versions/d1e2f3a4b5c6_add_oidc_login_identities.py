"""add linked OIDC login identities

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_oidc_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("issuer", sa.String(length=2048), nullable=False),
        sa.Column("issuer_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_hash", sa.String(length=96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issuer_hash",
            "subject_hash",
            name="uq_user_oidc_identities_issuer_subject_hash",
        ),
        sa.UniqueConstraint(
            "user_id",
            "provider_key",
            name="uq_user_oidc_identities_user_provider",
        ),
    )
    op.create_table(
        "oidc_authorization_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(purpose = 'login' AND user_id IS NULL AND user_session_id IS NULL) OR "
            "(purpose = 'link' AND user_id IS NOT NULL AND user_session_id IS NOT NULL)",
            name="ck_oidc_authorization_requests_binding",
        ),
        sa.CheckConstraint(
            "purpose IN ('login', 'link')",
            name="ck_oidc_authorization_requests_purpose",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_session_id"],
            ["user_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "state_hash",
            name="uq_oidc_authorization_requests_state_hash",
        ),
    )
    op.create_index(
        "ix_oidc_authorization_requests_expires_at",
        "oidc_authorization_requests",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    linked_identity = op.get_bind().execute(
        sa.text("SELECT 1 FROM user_oidc_identities LIMIT 1")
    ).first()
    if linked_identity is not None:
        raise RuntimeError(
            "Cannot downgrade OIDC identity storage while linked login identities exist"
        )
    op.drop_index(
        "ix_oidc_authorization_requests_expires_at",
        table_name="oidc_authorization_requests",
    )
    op.drop_table("oidc_authorization_requests")
    op.drop_table("user_oidc_identities")
