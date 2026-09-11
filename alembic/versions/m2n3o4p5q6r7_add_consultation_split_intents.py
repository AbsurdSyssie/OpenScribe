"""add durable consultation split intents

Revision ID: m2n3o4p5q6r7
Revises: l1m2n3o4p5q6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "m2n3o4p5q6r7"
down_revision = "l1m2n3o4p5q6"
branch_labels = None
depends_on = None


intent_status = postgresql.ENUM(
    "analysis_pending",
    name="consultationsplitintentstatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    intent_status.create(bind, checkfirst=True)

    op.create_table(
        "consultation_split_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("status", intent_status, nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_consultation_split_intents_owner_user",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_consultation_split_intents_team",
        ),
        sa.ForeignKeyConstraint(
            ["transcript_id"],
            ["transcripts.id"],
            name="fk_consultation_split_intents_transcript",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["consultation_split_analyses.id"],
            name="fk_consultation_split_intents_analysis",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consultation_split_intents"),
        sa.UniqueConstraint(
            "owner_user_id",
            "client_idempotency_key",
            name="uq_consultation_split_intents_owner_idempotency_key",
        ),
    )
    op.create_index(
        "ix_consultation_split_intents_transcript_status",
        "consultation_split_intents",
        ["transcript_id", "status"],
    )
    op.create_index(
        "ix_consultation_split_intents_retention",
        "consultation_split_intents",
        ["retention_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_consultation_split_intents_retention", table_name="consultation_split_intents")
    op.drop_index("ix_consultation_split_intents_transcript_status", table_name="consultation_split_intents")
    op.drop_table("consultation_split_intents")

    bind = op.get_bind()
    intent_status.drop(bind, checkfirst=True)
