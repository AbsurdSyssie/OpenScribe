"""Add durable lineage for one backup JSON repair attempt."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "fb1c2d3e4f50"
down_revision = "eafc081108b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("provider_attempts", sa.Column("repair_of_attempt_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key("fk_provider_attempts_repair_of", "provider_attempts", "provider_attempts", ["repair_of_attempt_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_provider_attempts_repair_of", "provider_attempts", ["repair_of_attempt_id"])
    op.create_check_constraint("ck_provider_attempts_repair_identity", "provider_attempts", "repair_of_attempt_id IS NULL OR (backup_of_attempt_id IS NULL AND resource = 'tokens' AND attempt_kind = 'llm_generation' AND attempt_number = 1 AND correlation_id = repair_of_attempt_id AND id <> repair_of_attempt_id AND consultation_split_execution_id IS NULL AND transcript_ingestion_job_id IS NULL AND transcript_stream_session_id IS NULL)")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM provider_attempts WHERE repair_of_attempt_id IS NOT NULL)")):
        raise RuntimeError("Cannot downgrade while durable repair provider attempts exist")
    op.drop_constraint("ck_provider_attempts_repair_identity", "provider_attempts", type_="check")
    op.drop_constraint("uq_provider_attempts_repair_of", "provider_attempts", type_="unique")
    op.drop_constraint("fk_provider_attempts_repair_of", "provider_attempts", type_="foreignkey")
    op.drop_column("provider_attempts", "repair_of_attempt_id")
