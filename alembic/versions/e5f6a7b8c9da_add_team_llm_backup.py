"""Add admin team LLM backup selection and durable provider failover lineage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e5f6a7b8c9da"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None

_SOURCES = ("generated_documents", "template_suggestion_jobs", "consultation_split_analyses", "consultation_split_batches", "consultation_split_executions")


def upgrade() -> None:
    op.create_table("team_llm_backup_selections",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_llm_configs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(llm_config_id IS NULL AND model_name IS NULL) OR (llm_config_id IS NOT NULL AND model_name IS NOT NULL AND length(btrim(model_name)) > 0)", name="ck_team_llm_backup_selections_shape"),
    )
    for table in _SOURCES:
        op.add_column(table, sa.Column("backup_llm_config_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_{table}_backup_llm_config", table, "team_llm_configs", ["backup_llm_config_id"], ["id"], ondelete="SET NULL")
        op.add_column(table, sa.Column("backup_provider_snapshot_encrypted", sa.Text(), nullable=True))
    op.add_column("template_suggestion_jobs", sa.Column("llm_request_payload_json_encrypted", sa.Text(), nullable=True))
    op.add_column("provider_attempts", sa.Column("backup_of_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_provider_attempts_backup_of", "provider_attempts", "provider_attempts", ["backup_of_attempt_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_provider_attempts_backup_of", "provider_attempts", ["backup_of_attempt_id"])
    op.create_check_constraint("ck_provider_attempts_backup_identity", "provider_attempts", "backup_of_attempt_id IS NULL OR (resource = 'tokens' AND attempt_number = 1 AND correlation_id = backup_of_attempt_id AND id <> backup_of_attempt_id)")
    op.drop_constraint("uq_provider_attempts_split_execution", "provider_attempts", type_="unique")
    op.create_index("uq_provider_attempts_split_execution", "provider_attempts", ["consultation_split_execution_id"], unique=True, postgresql_where=sa.text("backup_of_attempt_id IS NULL"))


def downgrade() -> None:
    # Fail explicitly instead of silently deleting accounted backup dispatches.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM provider_attempts WHERE backup_of_attempt_id IS NOT NULL)")):
        raise RuntimeError("Cannot downgrade while durable backup provider attempts exist")
    op.drop_index("uq_provider_attempts_split_execution", table_name="provider_attempts")
    op.create_unique_constraint("uq_provider_attempts_split_execution", "provider_attempts", ["consultation_split_execution_id"])
    op.drop_constraint("ck_provider_attempts_backup_identity", "provider_attempts", type_="check")
    op.drop_constraint("uq_provider_attempts_backup_of", "provider_attempts", type_="unique")
    op.drop_constraint("fk_provider_attempts_backup_of", "provider_attempts", type_="foreignkey")
    op.drop_column("provider_attempts", "backup_of_attempt_id")
    op.drop_column("template_suggestion_jobs", "llm_request_payload_json_encrypted")
    for table in reversed(_SOURCES):
        op.drop_column(table, "backup_provider_snapshot_encrypted")
        op.drop_constraint(f"fk_{table}_backup_llm_config", table, type_="foreignkey")
        op.drop_column(table, "backup_llm_config_id")
    op.drop_table("team_llm_backup_selections")
