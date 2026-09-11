"""add safe consultation split bundled verification state

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u
"""

from alembic import op
import sqlalchemy as sa


revision = "q6r7s8t9u0v1"
down_revision = "p5q6r7s8t9u"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE TYPE commits independently of Alembic's revision transaction.
    # If a later step in this migration fails, a retry starts at p5 with this
    # type already present. Reuse only the precise type this migration owns;
    # a same-named type with different labels is not compatible state.
    with op.get_context().autocommit_block():
        op.execute("""
            DO $$
            DECLARE
                type_exists boolean;
                existing_labels text[];
                expected_labels constant text[] := ARRAY['pending', 'verifying', 'verified', 'unchecked'];
            BEGIN
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_type AS type_row
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = type_row.typnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND type_row.typname = 'consultationsplitverificationstatus'
                ) INTO type_exists;

                IF NOT type_exists THEN
                    CREATE TYPE consultationsplitverificationstatus AS ENUM (
                        'pending', 'verifying', 'verified', 'unchecked'
                    );
                    RETURN;
                END IF;

                SELECT array_agg(enum_row.enumlabel ORDER BY enum_row.enumsortorder)
                INTO existing_labels
                FROM pg_type AS type_row
                JOIN pg_namespace AS namespace_row ON namespace_row.oid = type_row.typnamespace
                JOIN pg_enum AS enum_row ON enum_row.enumtypid = type_row.oid
                WHERE namespace_row.nspname = current_schema()
                  AND type_row.typname = 'consultationsplitverificationstatus';

                IF existing_labels IS DISTINCT FROM expected_labels THEN
                    RAISE EXCEPTION
                        'consultationsplitverificationstatus has incompatible labels: expected %, found %',
                        expected_labels, existing_labels;
                END IF;
            END $$;
        """)
    op.add_column("consultation_split_batches", sa.Column("verification_status", sa.Enum(name="consultationsplitverificationstatus"), nullable=False, server_default="pending"))
    op.add_column("consultation_split_batches", sa.Column("verification_reason", sa.String(length=128), nullable=True))
    op.add_column("consultation_split_batches", sa.Column("verification_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("consultation_split_batches", sa.Column("verification_correction_count", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_consultation_split_batches_verification_reason_length", "consultation_split_batches", "verification_reason IS NULL OR char_length(verification_reason) <= 128")
    op.create_check_constraint("ck_consultation_split_batches_verification_correction_count", "consultation_split_batches", "verification_correction_count IS NULL OR verification_correction_count >= 0")
    op.add_column("consultation_split_topic_outcomes", sa.Column("verified_output_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("consultation_split_topic_outcomes", "verified_output_encrypted")
    op.drop_constraint("ck_consultation_split_batches_verification_correction_count", "consultation_split_batches", type_="check")
    op.drop_constraint("ck_consultation_split_batches_verification_reason_length", "consultation_split_batches", type_="check")
    op.drop_column("consultation_split_batches", "verification_correction_count")
    op.drop_column("consultation_split_batches", "verification_completed_at")
    op.drop_column("consultation_split_batches", "verification_reason")
    op.drop_column("consultation_split_batches", "verification_status")
    op.execute("DROP TYPE consultationsplitverificationstatus")
