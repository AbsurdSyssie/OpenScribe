"""Add monotonic revisions and bounded replay identity to Working notes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "k3l4m5n6o7p8"
down_revision = "j2k3l4m5n6o7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("working_note_revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "transcripts",
        sa.Column("working_note_last_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(sa.text(
        "UPDATE transcripts SET working_note_revision = 1 WHERE working_note_updated_at IS NOT NULL"
    ))
    op.create_check_constraint(
        "ck_transcripts_working_note_revision_nonnegative",
        "transcripts",
        "working_note_revision >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_transcripts_working_note_revision_nonnegative", "transcripts", type_="check")
    op.drop_column("transcripts", "working_note_last_operation_id")
    op.drop_column("transcripts", "working_note_revision")
