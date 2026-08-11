"""add DSPT retention controls

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


security_audit_hold_reason = postgresql.ENUM(
    "incident",
    "contractual_investigation",
    "legal_hold",
    "legal_duty",
    "dispute",
    name="securityauditholdreason",
    create_type=False,
)


def upgrade() -> None:
    op.add_column(
        "transcript_ingestion_jobs",
        sa.Column("source_audio_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transcript_ingestion_jobs",
        sa.Column("source_audio_expired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE transcript_ingestion_jobs
        SET source_audio_expires_at = created_at + INTERVAL '24 hours'
        WHERE source_audio_vault_ref IS NOT NULL OR source_audio_blob IS NOT NULL
        """
    )
    op.create_check_constraint(
        "ck_transcript_ingestion_jobs_source_expiry",
        "transcript_ingestion_jobs",
        "(source_audio_blob IS NULL AND source_audio_vault_ref IS NULL) OR source_audio_expires_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_transcript_ingestion_jobs_expired_has_deadline",
        "transcript_ingestion_jobs",
        "source_audio_expired_at IS NULL OR source_audio_expires_at IS NOT NULL",
    )
    op.create_index(
        "ix_transcript_ingestion_jobs_source_audio_expiry",
        "transcript_ingestion_jobs",
        ["source_audio_expires_at"],
        postgresql_where=sa.text("source_audio_vault_ref IS NOT NULL OR source_audio_blob IS NOT NULL"),
    )

    security_audit_hold_reason.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "security_audit_event_holds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("security_audit_event_id", sa.UUID(), nullable=False),
        sa.Column("reason", security_audit_hold_reason, nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewal_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("released_by_user_id", sa.UUID(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("approved_at >= created_at", name="ck_security_audit_event_holds_approval_order"),
        sa.CheckConstraint("review_at > approved_at", name="ck_security_audit_event_holds_review_order"),
        sa.CheckConstraint("expires_at > approved_at", name="ck_security_audit_event_holds_expiry_order"),
        sa.CheckConstraint(
            "expires_at <= approved_at + INTERVAL '90 days'",
            name="ck_security_audit_event_holds_max_duration",
        ),
        sa.CheckConstraint("review_at <= expires_at", name="ck_security_audit_event_holds_review_before_expiry"),
        sa.CheckConstraint("renewal_count >= 0", name="ck_security_audit_event_holds_renewal_nonnegative"),
        sa.CheckConstraint("released_at IS NULL OR released_at >= created_at", name="ck_security_audit_event_holds_release_order"),
        sa.CheckConstraint(
            "released_at IS NOT NULL OR owner_user_id IS NOT NULL",
            name="ck_security_audit_event_holds_active_owner",
        ),
        sa.ForeignKeyConstraint(
            ["security_audit_event_id"], ["security_audit_events.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_security_audit_event_holds_unreleased",
        "security_audit_event_holds",
        ["security_audit_event_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ix_security_audit_event_holds_expiry",
        "security_audit_event_holds",
        ["expires_at"],
    )


def downgrade() -> None:
    hold = op.get_bind().execute(
        sa.text("SELECT 1 FROM security_audit_event_holds LIMIT 1")
    ).first()
    if hold is not None:
        raise RuntimeError(
            "Cannot downgrade DSPT retention controls while security-audit holds exist; release and retain evidence first"
        )
    source_retention = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM transcript_ingestion_jobs
            WHERE source_audio_expires_at IS NOT NULL OR source_audio_expired_at IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if source_retention is not None:
        raise RuntimeError(
            "Cannot downgrade DSPT retention controls while source-audio retention records exist; expire and clean source audio first"
        )
    op.drop_index("ix_security_audit_event_holds_expiry", table_name="security_audit_event_holds")
    op.drop_index("uq_security_audit_event_holds_unreleased", table_name="security_audit_event_holds")
    op.drop_table("security_audit_event_holds")
    security_audit_hold_reason.drop(op.get_bind(), checkfirst=True)

    # Keep downgrade transactional so a later fail-closed downgrade does not
    # leave this migration partly removed while the Alembic revision stays put.
    op.drop_index(
        "ix_transcript_ingestion_jobs_source_audio_expiry",
        table_name="transcript_ingestion_jobs",
    )
    op.drop_constraint("ck_transcript_ingestion_jobs_expired_has_deadline", "transcript_ingestion_jobs", type_="check")
    op.drop_constraint("ck_transcript_ingestion_jobs_source_expiry", "transcript_ingestion_jobs", type_="check")
    op.drop_column("transcript_ingestion_jobs", "source_audio_expired_at")
    op.drop_column("transcript_ingestion_jobs", "source_audio_expires_at")
