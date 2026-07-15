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
        revision_table = sa.table(table, sa.column("revision_of_config_id", sa.UUID()))
        pending_revision = op.get_bind().execute(
            sa.select(revision_table.c.revision_of_config_id)
            .where(revision_table.c.revision_of_config_id.is_not(None))
            .limit(1)
        ).first()
        if pending_revision is not None:
            raise RuntimeError(
                f"Cannot downgrade provider revisions while {table} contains pending revisions; "
                "cancel or finalize them first so Vault references are preserved."
            )
        op.drop_index(f"uq_{table}_pending_revision", table_name=table)
        op.drop_index(f"uq_{table}_team_label_lower", table_name=table)
        op.create_index(f"uq_{table}_team_label_lower", table, ["team_id", sa.text("lower(btrim(label))")], unique=True)
        op.drop_constraint(f"fk_{table}_revision_of_config_id", table, type_="foreignkey")
        op.drop_column(table, "revision_of_config_id")
