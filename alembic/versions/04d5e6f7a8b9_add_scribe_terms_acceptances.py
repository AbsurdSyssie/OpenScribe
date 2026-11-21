"""Persist Scribe Terms session pins and acceptance evidence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "04d5e6f7a8b9"
down_revision = "02b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_sessions", sa.Column("scribe_terms_version_no", sa.Integer(), nullable=True))
    op.create_table(
        "scribe_terms_acceptances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no > 0", name="ck_scribe_terms_acceptances_version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "version_no", name="uq_scribe_terms_acceptances_user_version"),
    )


def downgrade() -> None:
    op.drop_table("scribe_terms_acceptances")
    op.drop_column("user_sessions", "scribe_terms_version_no")
