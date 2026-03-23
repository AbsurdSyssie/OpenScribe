"""add structured emis templates and sections

Revision ID: t0b1c2d3e4f5
Revises: s9a0b1c2d3e4
Create Date: 2026-03-23 11:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "t0b1c2d3e4f5"
down_revision = "s9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE templatemode ADD VALUE IF NOT EXISTS 'structured'")
    op.add_column("template_versions", sa.Column("config_json", sa.JSON(), nullable=True))
    op.add_column("generated_documents", sa.Column("structured_context_json", sa.JSON(), nullable=True))
    op.create_table(
        "generated_document_sections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("generated_document_id", sa.UUID(), nullable=False),
        sa.Column("section_key", sa.String(length=64), nullable=False),
        sa.Column("section_label", sa.String(length=255), nullable=False),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("original_text_encrypted", sa.Text(), nullable=False),
        sa.Column("edited_text_encrypted", sa.Text(), nullable=False),
        sa.Column("is_edited", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["generated_document_id"], ["generated_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generated_document_id", "section_key", name="uq_generated_document_sections_key"),
    )


def downgrade() -> None:
    op.drop_table("generated_document_sections")
    op.drop_column("generated_documents", "structured_context_json")
    op.drop_column("template_versions", "config_json")
