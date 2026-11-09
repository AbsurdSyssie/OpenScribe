"""Record the requested Telehealth browser audio scope.

Revision ID: eafc081108b1
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "eafc081108b1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("telehealth_capture_sessions") as batch:
        batch.add_column(sa.Column(
            "requested_audio_scope", sa.String(length=16),
            nullable=False, server_default="unknown",
        ))
        batch.create_check_constraint(
            "ck_telehealth_capture_sessions_audio_scope",
            "requested_audio_scope IN ('unknown', 'call', 'system')",
        )


def downgrade() -> None:
    with op.batch_alter_table("telehealth_capture_sessions") as batch:
        batch.drop_constraint("ck_telehealth_capture_sessions_audio_scope", type_="check")
        batch.drop_column("requested_audio_scope")
