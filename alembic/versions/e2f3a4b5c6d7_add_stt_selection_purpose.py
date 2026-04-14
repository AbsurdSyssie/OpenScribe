"""add stt selection purpose

Revision ID: e2f3a4b5c6d7
Revises: d1f2a3b4c5d6
Create Date: 2026-04-13 13:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1f2a3b4c5d6"
branch_labels = None
depends_on = None


sttselectionpurpose = sa.Enum(
    "conversation",
    "post_consultation_dictation",
    name="sttselectionpurpose",
)


def upgrade() -> None:
    sttselectionpurpose.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "team_stt_selections",
        sa.Column(
            "purpose",
            sttselectionpurpose,
            nullable=False,
            server_default="conversation",
        ),
    )
    op.drop_constraint("uq_team_stt_selections_team_id", "team_stt_selections", type_="unique")
    op.create_unique_constraint(
        "uq_team_stt_selections_team_purpose",
        "team_stt_selections",
        ["team_id", "purpose"],
    )
    op.alter_column("team_stt_selections", "purpose", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_team_stt_selections_team_purpose", "team_stt_selections", type_="unique")
    op.create_unique_constraint("uq_team_stt_selections_team_id", "team_stt_selections", ["team_id"])
    op.drop_column("team_stt_selections", "purpose")
    sttselectionpurpose.drop(op.get_bind(), checkfirst=True)
