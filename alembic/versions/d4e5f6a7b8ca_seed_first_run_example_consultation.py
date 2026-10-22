"""seed first-run example consultations

Revision ID: d4e5f6a7b8ca
Revises: e5f6a7b8c9da
Create Date: 2026-10-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8ca"
down_revision = "e5f6a7b8c9da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("example_consultation_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Existing accounts are eligible only when the database has durable evidence
    # they have never signed in and no root or deletion record exists.
    op.execute(
        sa.text(
            "UPDATE users SET example_consultation_pending = TRUE "
            "WHERE is_system_admin IS FALSE AND team_id IS NOT NULL AND last_login_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM transcripts WHERE transcripts.owner_user_id = users.id) "
            "AND NOT EXISTS (SELECT 1 FROM security_audit_events "
            "WHERE security_audit_events.actor_user_id = users.id "
            "AND security_audit_events.action = 'transcript_root_deleted')"
        )
    )
    op.alter_column("users", "example_consultation_pending", server_default=None)
    op.add_column("transcripts", sa.Column("is_example", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("transcripts", "is_example", server_default=None)
    op.create_index(
        "uq_transcripts_owner_example",
        "transcripts",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("is_example IS TRUE"),
        sqlite_where=sa.text("is_example IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_transcripts_owner_example", table_name="transcripts")
    op.drop_column("transcripts", "is_example")
    op.drop_column("users", "example_consultation_pending")
