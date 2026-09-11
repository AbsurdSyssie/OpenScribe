"""retain LLM config identity for consultation split executions

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "k0l1m2n3o4p5"
down_revision = "j9k0l1m2n3o4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable is deliberate.  Existing passive scaffold rows have never been
    # submitted to a provider and cannot be truthfully backfilled with a
    # config.  The production queue boundary requires a same-team config.
    op.add_column(
        "consultation_split_executions",
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_consultation_split_executions_llm_config",
        "consultation_split_executions",
        "team_llm_configs",
        ["llm_config_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_consultation_split_executions_llm_config_status",
        "consultation_split_executions",
        ["llm_config_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consultation_split_executions_llm_config_status",
        table_name="consultation_split_executions",
    )
    op.drop_constraint(
        "fk_consultation_split_executions_llm_config",
        "consultation_split_executions",
        type_="foreignkey",
    )
    op.drop_column("consultation_split_executions", "llm_config_id")
