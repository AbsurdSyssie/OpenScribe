"""snapshot selected template for template suggestions

Revision ID: h7i8j9k0l1m2
Revises: g7h8i9j0k1l2
"""

from alembic import op
import sqlalchemy as sa


revision = "h7i8j9k0l1m2"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "template_suggestion_jobs",
        sa.Column("selected_template_snapshot_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("template_suggestion_jobs", "selected_template_snapshot_json")
