"""add staged provider config revisions

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
"""

from alembic import op
import sqlalchemy as sa


revision = "w4x5y6z7a8b9"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("team_stt_configs", "team_llm_configs"):
        op.add_column(table, sa.Column("revision_of_config_id", sa.UUID(), nullable=True))
        op.create_foreign_key(f"fk_{table}_revision_of_config_id", table, table, ["revision_of_config_id"], ["id"], ondelete="CASCADE")
        op.drop_index(f"uq_{table}_team_label_lower", table_name=table)
        op.create_index(f"uq_{table}_team_label_lower", table, ["team_id", sa.text("lower(btrim(label))")], unique=True, postgresql_where=sa.text("revision_of_config_id IS NULL"))
        op.create_index(f"uq_{table}_pending_revision", table, ["revision_of_config_id"], unique=True, postgresql_where=sa.text("revision_of_config_id IS NOT NULL"))


def downgrade() -> None:
    for table in ("team_llm_configs", "team_stt_configs"):
        # Revision rows may intentionally reuse their root config's label. They
        # cannot be represented by the pre-revision schema, so discard them
        # before restoring unconditional normalized-label uniqueness.
        revision_table = sa.table(table, sa.column("revision_of_config_id", sa.UUID()))
        op.execute(revision_table.delete().where(revision_table.c.revision_of_config_id.is_not(None)))
        op.drop_index(f"uq_{table}_pending_revision", table_name=table)
        op.drop_index(f"uq_{table}_team_label_lower", table_name=table)
        op.create_index(f"uq_{table}_team_label_lower", table, ["team_id", sa.text("lower(btrim(label))")], unique=True)
        op.drop_constraint(f"fk_{table}_revision_of_config_id", table, type_="foreignkey")
        op.drop_column(table, "revision_of_config_id")
