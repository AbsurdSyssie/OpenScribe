"""Record the first durable content timestamp on new consultations."""

from alembic import op
import sqlalchemy as sa


revision = "03c4d5e6f7a8"
down_revision = "02b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("first_content_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "transcripts",
        sa.Column("track_first_content_at", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("transcripts", "track_first_content_at", server_default=sa.true())
    op.create_check_constraint(
        "ck_transcripts_first_content_tracking", "transcripts", "track_first_content_at OR first_content_at IS NULL"
    )
    op.create_index(
        "ix_transcripts_owner_sidebar_timestamp",
        "transcripts",
        ["owner_user_id", sa.text("coalesce(first_content_at, created_at) DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_transcripts_owner_sidebar_timestamp", table_name="transcripts")
    op.drop_constraint("ck_transcripts_first_content_tracking", "transcripts", type_="check")
    op.drop_column("transcripts", "track_first_content_at")
    op.drop_column("transcripts", "first_content_at")
