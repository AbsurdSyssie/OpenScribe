"""Require revisions on plan-backed split lineage."""

from alembic import op


revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


_INTENT_CHECK = (
    "problem_plan_id IS NULL OR "
    "(intent_kind = 'problem_suggestion' AND problem_plan_revision IS NOT NULL AND problem_plan_revision >= 0) OR "
    "(intent_kind = 'problem_plan_create' AND problem_plan_revision IS NOT NULL AND problem_plan_revision >= 1)"
)
_BATCH_CHECK = (
    "(problem_plan_id IS NULL AND problem_plan_revision IS NULL) OR "
    "(problem_plan_id IS NOT NULL AND problem_plan_revision IS NOT NULL AND problem_plan_revision >= 1)"
)


def upgrade() -> None:
    # Adding the stricter checks validates existing rows. If a row has an
    # unknown revision, fail closed instead of inventing lineage information.
    op.drop_constraint(
        "ck_consultation_split_intents_plan_revision",
        "consultation_split_intents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_consultation_split_intents_plan_revision",
        "consultation_split_intents",
        _INTENT_CHECK,
    )
    op.drop_constraint(
        "ck_consultation_split_batches_plan_revision",
        "consultation_split_batches",
        type_="check",
    )
    op.create_check_constraint(
        "ck_consultation_split_batches_plan_revision",
        "consultation_split_batches",
        _BATCH_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_consultation_split_batches_plan_revision",
        "consultation_split_batches",
        type_="check",
    )
    op.create_check_constraint(
        "ck_consultation_split_batches_plan_revision",
        "consultation_split_batches",
        "(problem_plan_id IS NULL AND problem_plan_revision IS NULL) OR "
        "(problem_plan_id IS NOT NULL AND problem_plan_revision >= 1)",
    )
    op.drop_constraint(
        "ck_consultation_split_intents_plan_revision",
        "consultation_split_intents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_consultation_split_intents_plan_revision",
        "consultation_split_intents",
        "problem_plan_id IS NULL OR "
        "(intent_kind = 'problem_suggestion' AND problem_plan_revision >= 0) OR "
        "(intent_kind = 'problem_plan_create' AND problem_plan_revision >= 1)",
    )
