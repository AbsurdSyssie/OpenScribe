"""Add source-independent owner problem plans and plan batch lineage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "m7n8o9p0q1r2"
down_revision = "k3l4m5n6o7p8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultation_problem_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("manual_override", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("plan_encrypted", sa.Text(), nullable=False),
        sa.Column("last_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_operation_base_revision", sa.Integer(), nullable=True),
        sa.Column("last_operation_receipt_encrypted", sa.Text(), nullable=True),
        sa.Column("suggestion_intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("suggestion_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("suggestion_status", sa.String(length=32), nullable=True),
        sa.Column("suggestion_error_code", sa.String(length=128), nullable=True),
        sa.Column("suggestion_base_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_consultation_problem_plans_revision_nonnegative"),
        sa.CheckConstraint("last_operation_base_revision IS NULL OR last_operation_base_revision >= 0", name="ck_consultation_problem_plans_operation_base_revision"),
        sa.CheckConstraint("suggestion_status IS NULL OR suggestion_status IN ('queued', 'processing', 'ready', 'failed')", name="ck_consultation_problem_plans_suggestion_status"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_consultation_problem_plans_owner_user"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_consultation_problem_plans_team"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE", name="fk_consultation_problem_plans_transcript"),
        sa.ForeignKeyConstraint(["suggestion_intent_id"], ["consultation_split_intents.id"], ondelete="SET NULL", name="fk_consultation_problem_plans_suggestion_intent"),
        sa.ForeignKeyConstraint(["suggestion_analysis_id"], ["consultation_split_analyses.id"], ondelete="SET NULL", name="fk_consultation_problem_plans_suggestion_analysis"),
        sa.PrimaryKeyConstraint("id", name="pk_consultation_problem_plans"),
        sa.UniqueConstraint("transcript_id", name="uq_consultation_problem_plans_transcript"),
    )
    op.create_index("ix_consultation_problem_plans_retention", "consultation_problem_plans", ["retention_expires_at"])

    op.add_column("consultation_split_intents", sa.Column("problem_plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("consultation_split_intents", sa.Column("problem_plan_revision", sa.Integer(), nullable=True))
    op.add_column("consultation_split_intents", sa.Column("intent_kind", sa.String(length=32), server_default="legacy_generate", nullable=False))
    op.create_foreign_key(
        "fk_consultation_split_intents_problem_plan", "consultation_split_intents",
        "consultation_problem_plans", ["problem_plan_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_consultation_split_intents_plan_revision",
        "consultation_split_intents",
        "problem_plan_id IS NULL OR (intent_kind = 'problem_suggestion' AND problem_plan_revision >= 0) OR (intent_kind = 'problem_plan_create' AND problem_plan_revision >= 1)",
    )
    op.create_check_constraint(
        "ck_consultation_split_intents_kind",
        "consultation_split_intents",
        "intent_kind IN ('legacy_generate', 'problem_suggestion', 'problem_plan_create')",
    )
    op.alter_column("consultation_split_intents", "intent_kind", server_default=None)

    op.drop_constraint("consultation_split_batches_analysis_id_fkey", "consultation_split_batches", type_="foreignkey")
    op.alter_column("consultation_split_batches", "analysis_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_foreign_key(
        "fk_consultation_split_batches_analysis", "consultation_split_batches",
        "consultation_split_analyses", ["analysis_id"], ["id"], ondelete="RESTRICT",
    )
    op.add_column("consultation_split_batches", sa.Column("problem_plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("consultation_split_batches", sa.Column("problem_plan_revision", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_consultation_split_batches_problem_plan", "consultation_split_batches",
        "consultation_problem_plans", ["problem_plan_id"], ["id"], ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_consultation_split_batches_one_lineage",
        "consultation_split_batches",
        "(analysis_id IS NOT NULL AND problem_plan_id IS NULL) OR (analysis_id IS NULL AND problem_plan_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_consultation_split_batches_plan_revision",
        "consultation_split_batches",
        "(problem_plan_id IS NULL AND problem_plan_revision IS NULL) OR (problem_plan_id IS NOT NULL AND problem_plan_revision >= 1)",
    )


def downgrade() -> None:
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM consultation_problem_plans) OR
               EXISTS (SELECT 1 FROM consultation_split_batches WHERE problem_plan_id IS NOT NULL) OR
               EXISTS (
                   SELECT 1 FROM consultation_split_intents
                   WHERE intent_kind <> 'legacy_generate'
                      OR problem_plan_id IS NOT NULL
                      OR problem_plan_revision IS NOT NULL
               ) THEN
                RAISE EXCEPTION 'cannot downgrade while living plans, plan-backed batches, or plan intents exist';
            END IF;
        END;
        $$
    """))
    op.drop_constraint("ck_consultation_split_intents_kind", "consultation_split_intents", type_="check")
    op.drop_constraint("ck_consultation_split_intents_plan_revision", "consultation_split_intents", type_="check")
    op.drop_constraint("fk_consultation_split_intents_problem_plan", "consultation_split_intents", type_="foreignkey")
    op.drop_column("consultation_split_intents", "intent_kind")
    op.drop_column("consultation_split_intents", "problem_plan_revision")
    op.drop_column("consultation_split_intents", "problem_plan_id")
    op.drop_constraint("ck_consultation_split_batches_one_lineage", "consultation_split_batches", type_="check")
    op.drop_constraint("ck_consultation_split_batches_plan_revision", "consultation_split_batches", type_="check")
    op.drop_constraint("fk_consultation_split_batches_problem_plan", "consultation_split_batches", type_="foreignkey")
    op.drop_column("consultation_split_batches", "problem_plan_revision")
    op.drop_column("consultation_split_batches", "problem_plan_id")
    op.drop_constraint("fk_consultation_split_batches_analysis", "consultation_split_batches", type_="foreignkey")
    op.alter_column("consultation_split_batches", "analysis_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.create_foreign_key(
        "consultation_split_batches_analysis_id_fkey", "consultation_split_batches",
        "consultation_split_analyses", ["analysis_id"], ["id"], ondelete="RESTRICT",
    )
    op.drop_index("ix_consultation_problem_plans_retention", table_name="consultation_problem_plans")
    op.drop_table("consultation_problem_plans")
