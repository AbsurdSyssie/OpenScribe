"""repair security audit event set-null foreign keys

Revision ID: u2v3w4x5y6z7
Revises: t9u0v1w2x3y4
Create Date: 2026-06-24
"""

from alembic import op


revision = "u2v3w4x5y6z7"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE security_audit_events
        DROP CONSTRAINT IF EXISTS security_audit_events_actor_user_id_fkey,
        DROP CONSTRAINT IF EXISTS security_audit_events_target_user_id_fkey,
        DROP CONSTRAINT IF EXISTS security_audit_events_team_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE security_audit_events
        ADD CONSTRAINT security_audit_events_actor_user_id_fkey
            FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
        ADD CONSTRAINT security_audit_events_target_user_id_fkey
            FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL,
        ADD CONSTRAINT security_audit_events_team_id_fkey
            FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE security_audit_events
        DROP CONSTRAINT IF EXISTS security_audit_events_actor_user_id_fkey,
        DROP CONSTRAINT IF EXISTS security_audit_events_target_user_id_fkey,
        DROP CONSTRAINT IF EXISTS security_audit_events_team_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE security_audit_events
        ADD CONSTRAINT security_audit_events_actor_user_id_fkey
            FOREIGN KEY (actor_user_id) REFERENCES users(id),
        ADD CONSTRAINT security_audit_events_target_user_id_fkey
            FOREIGN KEY (target_user_id) REFERENCES users(id),
        ADD CONSTRAINT security_audit_events_team_id_fkey
            FOREIGN KEY (team_id) REFERENCES teams(id)
        """
    )
