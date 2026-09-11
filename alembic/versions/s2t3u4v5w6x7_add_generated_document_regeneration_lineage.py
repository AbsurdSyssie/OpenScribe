"""add immutable generated-document regeneration lineage

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "s2t3u4v5w6x7"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Leave historical rows untouched. They acquire a lineage only if a
    # clinician explicitly regenerates them, which avoids a broad rewrite of
    # encrypted transcript-derived content.
    op.drop_constraint(
        "uq_generated_documents_consultation_split_batch_topic",
        "generated_documents",
        type_="unique",
    )
    op.create_index(
        "ix_generated_documents_consultation_split_batch_topic",
        "generated_documents",
        ["consultation_split_batch_topic_id"],
    )
    op.add_column(
        "generated_documents",
        sa.Column("parent_generated_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_documents",
        sa.Column("regeneration_lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_documents",
        sa.Column(
            "regeneration_revision_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "generated_documents",
        sa.Column("regeneration_source_output_encrypted", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_generated_documents_regeneration_parent",
        "generated_documents",
        "generated_documents",
        ["parent_generated_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_generated_documents_regeneration_revision_positive",
        "generated_documents",
        "regeneration_revision_no >= 1",
    )
    op.create_unique_constraint(
        "uq_generated_documents_regeneration_lineage_revision",
        "generated_documents",
        ["regeneration_lineage_id", "regeneration_revision_no"],
    )
    op.create_index(
        "ix_generated_documents_regeneration_parent",
        "generated_documents",
        ["parent_generated_document_id"],
    )
    op.create_index(
        "ix_generated_documents_regeneration_lineage",
        "generated_documents",
        ["regeneration_lineage_id"],
    )
    op.create_index(
        "uq_generated_documents_active_regeneration_lineage",
        "generated_documents",
        ["regeneration_lineage_id"],
        unique=True,
        postgresql_where=sa.text(
            "regeneration_lineage_id IS NOT NULL "
            "AND status IN ('queued', 'processing')"
        ),
    )
    op.alter_column("generated_documents", "regeneration_revision_no", server_default=None)


def downgrade() -> None:
    existing_revisions = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM generated_documents
            WHERE parent_generated_document_id IS NOT NULL
               OR regeneration_lineage_id IS NOT NULL
               OR regeneration_source_output_encrypted IS NOT NULL
               OR regeneration_revision_no <> 1
            LIMIT 1
            """
        )
    ).first()
    if existing_revisions is not None:
        raise RuntimeError(
            "Cannot downgrade generated-document regeneration lineage while revisions exist"
        )
    op.drop_index("uq_generated_documents_active_regeneration_lineage", table_name="generated_documents")
    op.drop_index("ix_generated_documents_regeneration_lineage", table_name="generated_documents")
    op.drop_index("ix_generated_documents_regeneration_parent", table_name="generated_documents")
    op.drop_constraint(
        "uq_generated_documents_regeneration_lineage_revision",
        "generated_documents",
        type_="unique",
    )
    op.drop_constraint(
        "ck_generated_documents_regeneration_revision_positive",
        "generated_documents",
        type_="check",
    )
    op.drop_constraint(
        "fk_generated_documents_regeneration_parent",
        "generated_documents",
        type_="foreignkey",
    )
    op.drop_column("generated_documents", "regeneration_source_output_encrypted")
    op.drop_column("generated_documents", "regeneration_revision_no")
    op.drop_column("generated_documents", "regeneration_lineage_id")
    op.drop_column("generated_documents", "parent_generated_document_id")
    op.drop_index(
        "ix_generated_documents_consultation_split_batch_topic",
        table_name="generated_documents",
    )
    op.create_unique_constraint(
        "uq_generated_documents_consultation_split_batch_topic",
        "generated_documents",
        ["consultation_split_batch_topic_id"],
    )
