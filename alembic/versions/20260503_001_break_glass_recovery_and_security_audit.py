"""break glass recovery and security audit

Revision ID: 20260503_001
Revises: 20260430_001
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260503_001"
down_revision = "20260430_001"
branch_labels = None
depends_on = None


user_recovery_mode = postgresql.ENUM(
    "manager_password_reset",
    "manager_account_recovery",
    "break_glass_password_reset",
    "break_glass_account_recovery",
    name="userrecoverymode",
)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        user_recovery_mode.create(bind, checkfirst=True)
        recovery_mode_type = user_recovery_mode
        uuid_type = postgresql.UUID(as_uuid=True)
        details_default = sa.text("'{}'::json")
    else:
        recovery_mode_type = sa.Enum(
            "manager_password_reset",
            "manager_account_recovery",
            "break_glass_password_reset",
            "break_glass_account_recovery",
            name="userrecoverymode",
        )
        uuid_type = sa.String(length=32)
        details_default = sa.text("'{}'")

    op.add_column("users", sa.Column("temporary_password_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recovery_mode", recovery_mode_type, nullable=True))
    op.add_column("users", sa.Column("recovery_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recovery_started_by_user_id", uuid_type, nullable=True))
    op.create_foreign_key(
        "fk_users_recovery_started_by_user_id_users",
        "users",
        "users",
        ["recovery_started_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "security_audit_events",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=True),
        sa.Column("target_user_id", uuid_type, nullable=True),
        sa.Column("team_id", uuid_type, nullable=True),
        sa.Column("request_ip", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.String(length=1024), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=details_default),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_security_audit_events_actor_created", "security_audit_events", ["actor_user_id", "created_at"])
    op.create_index("ix_security_audit_events_target_created", "security_audit_events", ["target_user_id", "created_at"])
    op.create_index("ix_security_audit_events_action_created", "security_audit_events", ["action", "created_at"])


def downgrade():
    op.drop_index("ix_security_audit_events_action_created", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_target_created", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_actor_created", table_name="security_audit_events")
    op.drop_table("security_audit_events")

    op.drop_constraint("fk_users_recovery_started_by_user_id_users", "users", type_="foreignkey")
    op.drop_column("users", "recovery_started_by_user_id")
    op.drop_column("users", "recovery_started_at")
    op.drop_column("users", "recovery_mode")
    op.drop_column("users", "temporary_password_expires_at")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        user_recovery_mode.drop(bind, checkfirst=True)
