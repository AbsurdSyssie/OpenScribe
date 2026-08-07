"""add operator legal content

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e3
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e3"
branch_labels = None
depends_on = None


legal_document_kind = postgresql.ENUM(
    "privacy", "cookie_storage", "terms", name="legaldocumentkind", create_type=False
)
legal_document_version_state = postgresql.ENUM(
    "draft", "published", "superseded", name="legaldocumentversionstate", create_type=False
)


def upgrade() -> None:
    legal_document_kind.create(op.get_bind(), checkfirst=True)
    legal_document_version_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "operator_legal_profiles",
        sa.Column("singleton_key", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("company_number", sa.String(length=64), nullable=True),
        sa.Column("public_url", sa.String(length=2048), nullable=True),
        sa.Column("privacy_email", sa.String(length=254), nullable=True),
        sa.Column("complaints_email", sa.String(length=254), nullable=True),
        sa.Column("security_contact", sa.String(length=254), nullable=True),
        sa.Column("postal_address", sa.String(length=1000), nullable=True),
        sa.Column("cookie_banner_summary", sa.String(length=1000), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton_key IS TRUE", name="ck_operator_legal_profiles_singleton"),
        sa.CheckConstraint("revision > 0", name="ck_operator_legal_profiles_revision_positive"),
        sa.PrimaryKeyConstraint("singleton_key"),
    )

    op.create_table(
        "legal_document_roots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", legal_document_kind, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", name="uq_legal_document_roots_kind"),
    )

    op.create_table(
        "legal_document_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_root_id", sa.UUID(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("state", legal_document_version_state, nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("blocks_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=True),
        sa.Column("published_by_user_id", sa.UUID(), nullable=True),
        sa.Column("superseded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version_no > 0", name="ck_legal_document_versions_version_positive"),
        sa.CheckConstraint("revision > 0", name="ck_legal_document_versions_revision_positive"),
        sa.CheckConstraint(
            "(state = 'draft' AND published_at IS NULL AND superseded_at IS NULL) OR "
            "(state = 'published' AND published_at IS NOT NULL AND superseded_at IS NULL) OR "
            "(state = 'superseded' AND published_at IS NOT NULL AND superseded_at IS NOT NULL)",
            name="ck_legal_document_versions_state_timestamps",
        ),
        sa.ForeignKeyConstraint(["document_root_id"], ["legal_document_roots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_root_id", "version_no", name="uq_legal_document_versions_root_version"),
    )
    op.create_index(
        "uq_legal_document_versions_one_published",
        "legal_document_versions",
        ["document_root_id"],
        unique=True,
        postgresql_where=sa.text("state = 'published'"),
    )
    op.create_index(
        "ix_legal_document_versions_superseded_retention",
        "legal_document_versions",
        ["superseded_at"],
        postgresql_where=sa.text("state = 'superseded'"),
    )
    op.create_index(
        "ix_legal_document_versions_draft_retention",
        "legal_document_versions",
        ["updated_at"],
        postgresql_where=sa.text("state = 'draft'"),
    )

    op.create_table(
        "legal_document_version_holds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("legal_document_version_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_by_user_id", sa.UUID(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 500",
            name="ck_legal_document_version_holds_reason_length",
        ),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= created_at",
            name="ck_legal_document_version_holds_release_order",
        ),
        sa.ForeignKeyConstraint(
            ["legal_document_version_id"], ["legal_document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_legal_document_version_holds_active",
        "legal_document_version_holds",
        ["legal_document_version_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in (
        "legal_document_version_holds",
        "legal_document_versions",
        "legal_document_roots",
        "operator_legal_profiles",
    ):
        if connection.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first() is not None:
            raise RuntimeError(
                "Cannot downgrade operator legal content while retained legal records exist; "
                "roll back application code without deleting the additive legal-content tables"
            )

    op.drop_index("uq_legal_document_version_holds_active", table_name="legal_document_version_holds")
    op.drop_table("legal_document_version_holds")
    op.drop_index("ix_legal_document_versions_draft_retention", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_superseded_retention", table_name="legal_document_versions")
    op.drop_index("uq_legal_document_versions_one_published", table_name="legal_document_versions")
    op.drop_table("legal_document_versions")
    op.drop_table("legal_document_roots")
    op.drop_table("operator_legal_profiles")
    legal_document_version_state.drop(op.get_bind(), checkfirst=True)
    legal_document_kind.drop(op.get_bind(), checkfirst=True)
