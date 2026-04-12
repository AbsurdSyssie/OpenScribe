"""add unique asset name indexes

Revision ID: bb8d9e0f1a2b
Revises: aa7c8d9e0f1a
Create Date: 2026-04-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "bb8d9e0f1a2b"
down_revision = "aa7c8d9e0f1a"
branch_labels = None
depends_on = None


def _dedupe_names_for_scope(table_name: str, scope_column: str, scope_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
                record RECORD;
                base_name TEXT;
                candidate_name TEXT;
                candidate_exists BOOLEAN;
                suffix_rank INTEGER;
            BEGIN
                FOR record IN
                    SELECT
                        ranked.id,
                        ranked.scope_value,
                        ranked.duplicate_rank,
                        left(ranked.trimmed_name, 240) AS base_name
                    FROM (
                        SELECT
                            id,
                            {scope_column} AS scope_value,
                            row_number() OVER (
                                PARTITION BY {scope_column}, lower(btrim(name))
                                ORDER BY created_at ASC, id ASC
                            ) AS duplicate_rank,
                            btrim(name) AS trimmed_name
                        FROM {table_name}
                        WHERE scope = {scope_name!r}
                    ) AS ranked
                    WHERE ranked.duplicate_rank > 1
                    ORDER BY ranked.duplicate_rank ASC, ranked.id ASC
                LOOP
                    base_name := NULLIF(record.base_name, '');
                    IF base_name IS NULL THEN
                        base_name := 'copy';
                    END IF;
                    suffix_rank := record.duplicate_rank;
                    LOOP
                        candidate_name := left(base_name, 240) || ' copy ' || suffix_rank::text;
                        EXECUTE format(
                            'SELECT EXISTS (SELECT 1 FROM %I WHERE scope = %L AND %I IS NOT DISTINCT FROM $1 AND lower(btrim(name)) = lower(btrim($2)) AND id <> $3)',
                            {table_name!r},
                            {scope_name!r},
                            {scope_column!r}
                        )
                        INTO candidate_exists
                        USING record.scope_value, candidate_name, record.id;
                        EXIT WHEN NOT candidate_exists;
                        suffix_rank := suffix_rank + 1;
                    END LOOP;
                    EXECUTE format('UPDATE %I SET name = $1 WHERE id = $2', {table_name!r})
                    USING candidate_name, record.id;
                END LOOP;
            END $$;
            """
        )
    )


def _dedupe_scope_names(table_name: str, scope_column: str) -> None:
    _dedupe_names_for_scope(table_name, scope_column, "user")
    _dedupe_names_for_scope(table_name, "team_id", "team")


def upgrade() -> None:
    _dedupe_scope_names("templates", "owner_user_id")
    _dedupe_scope_names("quick_actions", "owner_user_id")
    op.create_index(
        "uq_templates_team_name_lower",
        "templates",
        ["team_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("scope = 'team'"),
    )
    op.create_index(
        "uq_templates_owner_name_lower",
        "templates",
        ["owner_user_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("scope = 'user'"),
    )
    op.create_index(
        "uq_quick_actions_team_name_lower",
        "quick_actions",
        ["team_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("scope = 'team'"),
    )
    op.create_index(
        "uq_quick_actions_owner_name_lower",
        "quick_actions",
        ["owner_user_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("scope = 'user'"),
    )


def downgrade() -> None:
    op.drop_index("uq_quick_actions_owner_name_lower", table_name="quick_actions")
    op.drop_index("uq_quick_actions_team_name_lower", table_name="quick_actions")
    op.drop_index("uq_templates_owner_name_lower", table_name="templates")
    op.drop_index("uq_templates_team_name_lower", table_name="templates")
