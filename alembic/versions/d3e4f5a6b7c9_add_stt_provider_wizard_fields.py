"""add stt provider wizard fields

Revision ID: d3e4f5a6b7c9
Revises: c2d3e4f5a6b8
Create Date: 2026-05-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e4f5a6b7c9"
down_revision = "c2d3e4f5a6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team_stt_configs", sa.Column("provider_preset", sa.String(length=64), nullable=True))
    op.add_column("team_stt_configs", sa.Column("setup_status", sa.String(length=64), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE team_stt_configs
            SET provider_preset = CASE
                WHEN adapter_kind = 'openai_cloud' THEN 'openai'
                WHEN adapter_kind = 'openai_compatible_rest' THEN 'custom_openai_compatible'
                ELSE 'custom_rest_openapi'
            END,
            setup_status = CASE
                WHEN model_name IS NOT NULL OR (adapter_kind = 'generic_rest' AND response_text_path IS NOT NULL AND response_text_path <> '') THEN 'ready'
                ELSE 'pending_model_selection'
            END
            """
        )
    )
    op.alter_column("team_stt_configs", "provider_preset", nullable=False)
    op.alter_column("team_stt_configs", "setup_status", nullable=False)
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
                    SELECT ranked.id, ranked.team_id, ranked.duplicate_rank, left(ranked.trimmed_label, 240) AS base_label
                    FROM (
                        SELECT id, team_id,
                            row_number() OVER (PARTITION BY team_id, lower(btrim(label)) ORDER BY created_at ASC, id ASC) AS duplicate_rank,
                            btrim(label) AS trimmed_label
                        FROM team_stt_configs
                    ) AS ranked
                    WHERE ranked.duplicate_rank > 1
                    ORDER BY ranked.duplicate_rank ASC, ranked.id ASC
                LOOP
                    base_label := NULLIF(record.base_label, '');
                    IF base_label IS NULL THEN
                        base_label := 'STT provider';
                    END IF;
                    suffix_rank := record.duplicate_rank;
                    LOOP
                        candidate_label := left(base_label, 240) || ' copy ' || suffix_rank::text;
                        SELECT EXISTS (
                            SELECT 1 FROM team_stt_configs
                            WHERE team_id = record.team_id
                            AND lower(btrim(label)) = lower(btrim(candidate_label))
                            AND id <> record.id
                        ) INTO candidate_exists;
                        EXIT WHEN NOT candidate_exists;
                        suffix_rank := suffix_rank + 1;
                    END LOOP;
                    UPDATE team_stt_configs SET label = candidate_label WHERE id = record.id;
                END LOOP;
            END $$;
            """
        )
    )
    op.create_index(
        "uq_team_stt_configs_team_label_lower",
        "team_stt_configs",
        ["team_id", sa.text("lower(btrim(label))")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_team_stt_configs_team_label_lower", table_name="team_stt_configs")
    op.drop_column("team_stt_configs", "setup_status")
    op.drop_column("team_stt_configs", "provider_preset")
