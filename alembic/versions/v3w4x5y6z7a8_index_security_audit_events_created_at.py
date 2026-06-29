"""index security audit events by creation time

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-06-29 00:00:00.000000
"""

from alembic import op


revision = "v3w4x5y6z7a8"
down_revision = "u2v3w4x5y6z7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_security_audit_events_created_at",
            "security_audit_events",
            ["created_at"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_security_audit_events_created_at",
            table_name="security_audit_events",
            postgresql_concurrently=True,
        )
