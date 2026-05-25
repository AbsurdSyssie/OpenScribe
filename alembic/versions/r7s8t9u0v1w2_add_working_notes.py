"""add working notes

Revision ID: r7s8t9u0v1w2
Revises: g6b7c9d1e2f3
Create Date: 2026-05-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "r7s8t9u0v1w2"
down_revision = "g6b7c9d1e2f3"
branch_labels = None
depends_on = None


working_note_mode = sa.Enum("freeform", "structured", name="transcriptworkingnotemode")


def upgrade() -> None:
    working_note_mode.create(op.get_bind(), checkfirst=True)
    op.add_column("transcripts", sa.Column("working_note_mode", working_note_mode, nullable=True))
    op.add_column("transcripts", sa.Column("freeform_working_note_encrypted", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("working_note_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generated_documents", sa.Column("working_note_mode_snapshot", working_note_mode, nullable=True))
    op.add_column("generated_documents", sa.Column("freeform_working_note_snapshot_encrypted", sa.Text(), nullable=True))
    op.add_column("generated_documents", sa.Column("structured_working_note_snapshot_json", sa.JSON(), nullable=True))
    op.execute(
        """
        UPDATE transcripts
        SET working_note_mode = 'structured', working_note_updated_at = created_at
        WHERE structured_context_json IS NOT NULL
          AND (
            (
              jsonb_typeof(structured_context_json::jsonb) = 'object'
              AND jsonb_typeof((structured_context_json::jsonb)->'sections') = 'object'
              AND EXISTS (
                SELECT 1
                FROM jsonb_each((structured_context_json::jsonb)->'sections') AS section(key, value)
                WHERE key IN ('problem', 'history', 'family_history', 'social_history', 'examination', 'comment', 'tasks', 'investigations')
                  AND (
                    (jsonb_typeof(value) = 'string' AND btrim(value #>> '{}') <> '')
                    OR (
                      jsonb_typeof(value) = 'array'
                      AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(value) AS item(text)
                        WHERE btrim(item.text) <> ''
                      )
                    )
                  )
              )
            )
            OR (
              jsonb_typeof(structured_context_json::jsonb) = 'string'
              AND (structured_context_json::jsonb #>> '{}') LIKE '{"alg":"AES-256-GCM",%'
              AND (structured_context_json::jsonb #>> '{}') LIKE '%"ct":%'
              AND (structured_context_json::jsonb #>> '{}') LIKE '%"n":%'
              AND (structured_context_json::jsonb #>> '{}') LIKE '%"v"%'
            )
          )
        """
    )


def downgrade() -> None:
    op.drop_column("generated_documents", "structured_working_note_snapshot_json")
    op.drop_column("generated_documents", "freeform_working_note_snapshot_encrypted")
    op.drop_column("generated_documents", "working_note_mode_snapshot")
    op.drop_column("transcripts", "working_note_updated_at")
    op.drop_column("transcripts", "freeform_working_note_encrypted")
    op.drop_column("transcripts", "working_note_mode")
    working_note_mode.drop(op.get_bind(), checkfirst=True)
