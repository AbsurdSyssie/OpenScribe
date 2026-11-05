"""Permit explicit discard of an unsubmitted telehealth capture.

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa

revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None

_OLD = "status IN ('active', 'stopping', 'interrupted', 'ready', 'processing', 'completed', 'failed')"
_NEW = "status IN ('active', 'stopping', 'interrupted', 'ready', 'processing', 'completed', 'failed', 'discarded')"
_CONSTRAINT = "ck_telehealth_capture_sessions_status"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "telehealth_capture_sessions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "telehealth_capture_sessions", _NEW)


def downgrade() -> None:
    # Never turn discarded clinical-audio metadata into an invalid state or
    # silently erase its associated forensic/audit provenance on downgrade.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM telehealth_capture_sessions WHERE status = 'discarded'
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade with discarded telehealth capture sessions';
            END IF;
        END $$;
    """))
    op.drop_constraint(_CONSTRAINT, "telehealth_capture_sessions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "telehealth_capture_sessions", _OLD)
