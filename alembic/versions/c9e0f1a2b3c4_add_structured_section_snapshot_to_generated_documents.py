"""add structured section snapshot to generated documents

Revision ID: c9e0f1a2b3c4
Revises: bb8d9e0f1a2b
Create Date: 2026-04-12 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c9e0f1a2b3c4"
down_revision = "bb8d9e0f1a2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_documents",
        sa.Column("structured_section_definitions_json", sa.JSON(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE generated_documents AS document
            SET structured_section_definitions_json = jsonb_build_object(
                'profile', 'emis',
                'sections', section_snapshot.sections_json
            )
            FROM (
                SELECT
                    version.id AS template_version_id,
                    COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'section_key', section.value ->> 'section_key',
                                'section_label', COALESCE(NULLIF(section.value ->> 'section_label', ''), section.value ->> 'section_key'),
                                'section_order', COALESCE((section.value ->> 'section_order')::integer, section.ordinality - 1)
                            )
                            ORDER BY COALESCE((section.value ->> 'section_order')::integer, section.ordinality - 1)
                        ) FILTER (WHERE section.value ? 'section_key'),
                        '[]'::jsonb
                    ) AS sections_json
                FROM template_versions AS version
                LEFT JOIN LATERAL jsonb_array_elements(COALESCE(version.config_json::jsonb -> 'sections', '[]'::jsonb)) WITH ORDINALITY AS section(value, ordinality) ON TRUE
                GROUP BY version.id
            ) AS section_snapshot
            WHERE document.template_version_id = section_snapshot.template_version_id
              AND section_snapshot.sections_json <> '[]'::jsonb
              AND document.structured_section_definitions_json IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE generated_documents AS document
            SET structured_section_definitions_json = jsonb_build_object(
                'profile', 'emis',
                'sections', section_snapshot.sections_json
            )
            FROM (
                SELECT
                    generated_document_id,
                    jsonb_agg(
                        jsonb_build_object(
                            'section_key', section_key,
                            'section_label', section_label,
                            'section_order', section_order
                        )
                        ORDER BY section_order
                    ) AS sections_json
                FROM generated_document_sections
                GROUP BY generated_document_id
            ) AS section_snapshot
            WHERE document.id = section_snapshot.generated_document_id
              AND document.structured_section_definitions_json IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("generated_documents", "structured_section_definitions_json")
