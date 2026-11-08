"""Add durable partial telehealth capture status.

Revision ID: a1b2c3d4e5f6
Revises: p0q1r2s3t4u5
"""

from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("telehealth_capture_sessions") as batch:
        batch.drop_constraint("ck_telehealth_capture_sessions_status", type_="check")
        batch.create_check_constraint(
            "ck_telehealth_capture_sessions_status",
            "status IN ('active', 'stopping', 'interrupted', 'ready', 'processing', 'partial', 'completed', 'failed', 'discarded')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    partial = bind.exec_driver_sql(
        "SELECT id FROM telehealth_capture_sessions WHERE status = 'partial' LIMIT 1"
    ).first()
    if partial is not None:
        raise RuntimeError(
            "Cannot downgrade telehealth partial status while accepted partial captures exist."
        )
    with op.batch_alter_table("telehealth_capture_sessions") as batch:
        batch.drop_constraint("ck_telehealth_capture_sessions_status", type_="check")
        batch.create_check_constraint(
            "ck_telehealth_capture_sessions_status",
            "status IN ('active', 'stopping', 'interrupted', 'ready', 'processing', 'completed', 'failed', 'discarded')",
        )
