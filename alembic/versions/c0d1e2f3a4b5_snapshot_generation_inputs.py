"""snapshot generated-document dictation and steering inputs

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_documents",
        sa.Column("dictation_snapshot_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "generated_documents",
        sa.Column("generation_steering_text_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    stored_snapshot = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM generated_documents
            WHERE dictation_snapshot_encrypted IS NOT NULL
               OR generation_steering_text_encrypted IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if stored_snapshot is not None:
        raise RuntimeError(
            "Cannot downgrade generated-document snapshots while encrypted generation inputs exist"
        )
    op.drop_column("generated_documents", "generation_steering_text_encrypted")
    op.drop_column("generated_documents", "dictation_snapshot_encrypted")
