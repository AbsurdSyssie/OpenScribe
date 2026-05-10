"""add unique llm config labels

Revision ID: c2d3e4f5a6b8
Revises: b1c2d3e4f5a6
Create Date: 2026-05-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c2d3e4f5a6b8"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                record RECORD;
                base_label TEXT;
                candidate_label TEXT;
                candidate_exists BOOLEAN;
                suffix_rank INTEGER;
            BEGIN
                FOR record IN
                    SELECT
                        ranked.id,
                        ranked.team_id,
                        ranked.duplicate_rank,
                        left(ranked.trimmed_label, 240) AS base_label
                    FROM (
                        SELECT
                            id,
                            team_id,
                            row_number() OVER (
                                PARTITION BY team_id, lower(btrim(label))
                                ORDER BY created_at ASC, id ASC
                            ) AS duplicate_rank,
                            btrim(label) AS trimmed_label
                        FROM team_llm_configs
                    ) AS ranked
                    WHERE ranked.duplicate_rank > 1
                    ORDER BY ranked.duplicate_rank ASC, ranked.id ASC
                LOOP
                    base_label := NULLIF(record.base_label, '');
                    IF base_label IS NULL THEN
                        base_label := 'LLM provider';
                    END IF;
                    suffix_rank := record.duplicate_rank;
                    LOOP
                        candidate_label := left(base_label, 240) || ' copy ' || suffix_rank::text;
                        SELECT EXISTS (
                            SELECT 1
                            FROM team_llm_configs
                            WHERE team_id = record.team_id
                            AND lower(btrim(label)) = lower(btrim(candidate_label))
                            AND id <> record.id
                        ) INTO candidate_exists;
                        EXIT WHEN NOT candidate_exists;
                        suffix_rank := suffix_rank + 1;
                    END LOOP;
                    UPDATE team_llm_configs SET label = candidate_label WHERE id = record.id;
                END LOOP;
            END $$;
            """
        )
    )
    op.create_index(
        "uq_team_llm_configs_team_label_lower",
        "team_llm_configs",
        ["team_id", sa.text("lower(btrim(label))")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_team_llm_configs_team_label_lower", table_name="team_llm_configs")
