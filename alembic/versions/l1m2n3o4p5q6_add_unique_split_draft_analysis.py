"""enforce one consultation split draft per analysis

Revision ID: l1m2n3o4p5q6
Revises: k0l1m2n3o4p5
"""

from alembic import op


revision = "l1m2n3o4p5q6"
down_revision = "k0l1m2n3o4p5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Consultation-split drafts have not been creatable before this revision.
    # Do not merge or delete unexpected rows: fail closed if an environment
    # contains duplicates that require an explicit remediation decision.
    op.create_unique_constraint(
        "uq_consultation_split_drafts_analysis",
        "consultation_split_drafts",
        ["analysis_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_consultation_split_drafts_analysis",
        "consultation_split_drafts",
        type_="unique",
    )
