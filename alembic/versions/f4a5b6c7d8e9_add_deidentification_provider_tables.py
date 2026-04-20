"""add deidentification provider tables

Revision ID: f4a5b6c7d8e9
Revises: e2f3a4b5c6d7
Create Date: 2026-04-14 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f4a5b6c7d8e9"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


deidentificationauthmode = postgresql.ENUM("none", "bearer", name="deidentificationauthmode")
deidentificationauthmode_existing = postgresql.ENUM("none", "bearer", name="deidentificationauthmode", create_type=False)
deidentificationadapterkind = postgresql.ENUM("native_presidio", "generic_rest", name="deidentificationadapterkind")
deidentificationadapterkind_existing = postgresql.ENUM(
    "native_presidio",
    "generic_rest",
    name="deidentificationadapterkind",
    create_type=False,
)


BUILTIN_PROVIDER_ID = "00000000-0000-0000-0000-00000000d1d1"


def upgrade() -> None:
    deidentificationauthmode.create(op.get_bind(), checkfirst=True)
    deidentificationadapterkind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "deidentification_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("adapter_kind", deidentificationadapterkind_existing, nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("detect_path", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("auth_mode", deidentificationauthmode_existing, nullable=False, server_default="none"),
        sa.Column("request_text_field", sa.String(length=255), nullable=False, server_default="text"),
        sa.Column("request_language_field", sa.String(length=255), nullable=True),
        sa.Column("extra_headers_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("extra_body_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("response_entities_path", sa.String(length=255), nullable=False, server_default="entities"),
        sa.Column("response_start_field", sa.String(length=255), nullable=False, server_default="start"),
        sa.Column("response_end_field", sa.String(length=255), nullable=False, server_default="end"),
        sa.Column("response_type_field", sa.String(length=255), nullable=False, server_default="entity_type"),
        sa.Column("response_score_field", sa.String(length=255), nullable=True),
        sa.Column("response_model_version_path", sa.String(length=255), nullable=True),
        sa.Column("entity_type_map_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("vault_secret_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.alter_column("deidentification_providers", "base_url", server_default=None)
    op.alter_column("deidentification_providers", "detect_path", server_default=None)
    op.alter_column("deidentification_providers", "auth_mode", server_default=None)
    op.alter_column("deidentification_providers", "request_text_field", server_default=None)
    op.alter_column("deidentification_providers", "extra_headers_json", server_default=None)
    op.alter_column("deidentification_providers", "extra_body_json", server_default=None)
    op.alter_column("deidentification_providers", "response_entities_path", server_default=None)
    op.alter_column("deidentification_providers", "response_start_field", server_default=None)
    op.alter_column("deidentification_providers", "response_end_field", server_default=None)
    op.alter_column("deidentification_providers", "response_type_field", server_default=None)
    op.alter_column("deidentification_providers", "entity_type_map_json", server_default=None)
    op.alter_column("deidentification_providers", "vault_secret_ref", server_default=None)
    op.alter_column("deidentification_providers", "is_active", server_default=None)
    op.alter_column("deidentification_providers", "is_builtin", server_default=None)

    op.create_table(
        "team_deidentification_provider_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deidentification_providers.id"), nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("team_id", "provider_id", name="uq_team_deidentification_provider_assignment"),
    )

    op.create_table(
        "team_deidentification_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deidentification_providers.id"), nullable=False),
        sa.Column("selected_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("team_id", name="uq_team_deidentification_selections_team_id"),
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO deidentification_providers (
                id,
                label,
                adapter_kind,
                base_url,
                detect_path,
                auth_mode,
                request_text_field,
                extra_headers_json,
                extra_body_json,
                response_entities_path,
                response_start_field,
                response_end_field,
                response_type_field,
                entity_type_map_json,
                vault_secret_ref,
                is_active,
                is_builtin,
                created_at,
                updated_at
            ) VALUES (
                '{BUILTIN_PROVIDER_ID}'::uuid,
                'Built-in Native Presidio',
                'native_presidio',
                '',
                '',
                'none',
                'text',
                '{{}}'::json,
                '{{}}'::json,
                'entities',
                'start',
                'end',
                'entity_type',
                '{{}}'::json,
                '',
                TRUE,
                TRUE,
                NOW(),
                NOW()
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("team_deidentification_selections")
    op.drop_table("team_deidentification_provider_assignments")
    op.drop_table("deidentification_providers")
    deidentificationadapterkind.drop(op.get_bind(), checkfirst=True)
    deidentificationauthmode.drop(op.get_bind(), checkfirst=True)
