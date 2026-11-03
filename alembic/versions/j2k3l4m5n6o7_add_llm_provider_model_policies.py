"""Persist model allowances and defaults per team LLM provider."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "j2k3l4m5n6o7"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def _clean_models(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def upgrade() -> None:
    op.create_table(
        "team_llm_provider_model_policies",
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_llm_configs.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enabled_models_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("default_model_name", sa.String(length=255), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("team_id", "llm_config_id", name="uq_team_llm_provider_model_policies_team_config"),
        sa.CheckConstraint("default_model_name IS NULL OR length(btrim(default_model_name)) > 0", name="ck_team_llm_provider_model_policies_default_nonempty"),
    )
    op.create_index("ix_team_llm_provider_model_policies_team_id", "team_llm_provider_model_policies", ["team_id"])
    op.alter_column("team_llm_provider_model_policies", "enabled_models_json", server_default=None)

    bind = op.get_bind()
    roots = bind.execute(sa.text("""
        SELECT id, team_id, model_name, available_models_json, updated_by_user_id
        FROM team_llm_configs WHERE revision_of_config_id IS NULL
    """)).mappings().all()
    active_by_team = {
        row["team_id"]: row for row in bind.execute(sa.text("""
            SELECT team_id, llm_config_id, allowed_models_json, model_name_override, selected_by_user_id
            FROM team_llm_selections
        """)).mappings().all()
    }
    backup_models = {
        (row["team_id"], row["llm_config_id"]): row["model_name"]
        for row in bind.execute(sa.text("""
            SELECT team_id, llm_config_id, model_name FROM team_llm_backup_selections
            WHERE llm_config_id IS NOT NULL AND model_name IS NOT NULL
        """)).mappings().all()
    }
    checker_models = {}
    checker_rows = bind.execute(sa.text("""
        SELECT selection.team_id, selection.llm_config_id,
               coalesce(selection.model_name_override, config.model_name) AS model_name
        FROM team_hallucination_check_selections AS selection
        JOIN team_llm_configs AS config ON config.id = selection.llm_config_id
    """)).mappings().all()
    for row in checker_rows:
        if row["model_name"]:
            checker_models[(row["team_id"], row["llm_config_id"])] = row["model_name"]

    insert_policy = sa.text("""
        INSERT INTO team_llm_provider_model_policies
            (llm_config_id, team_id, enabled_models_json, default_model_name,
             updated_by_user_id, created_at, updated_at)
        VALUES (:llm_config_id, :team_id, CAST(:enabled_models_json AS json),
                :default_model_name, :updated_by_user_id, NOW(), NOW())
    """)
    for config in roots:
        team_id = config["team_id"]
        config_id = config["id"]
        catalog = _clean_models(config["available_models_json"])
        # Legacy/manual providers could be ready with no discovered catalog;
        # their explicit configured model was the only known selectable ID.
        if not catalog and isinstance(config["model_name"], str) and config["model_name"].strip():
            catalog = [config["model_name"].strip()]
            bind.execute(sa.text(
                "UPDATE team_llm_configs SET available_models_json = CAST(:models AS json) WHERE id = :id"
            ), {"models": __import__("json").dumps(catalog), "id": config_id})
        active = active_by_team.get(team_id)
        if active is not None and active["llm_config_id"] == config_id:
            # Legacy empty allowed_models_json meant the complete current catalog.
            saved = _clean_models(active["allowed_models_json"])
            enabled = saved if saved else list(catalog)
            default = active["model_name_override"] or config["model_name"]
            if saved and default not in saved:
                default = saved[0] if saved else None
            changed_by = active["selected_by_user_id"]
        else:
            enabled = list(catalog)
            default = config["model_name"]
            changed_by = config["updated_by_user_id"]

        # Existing role assignments become part of the shared provider allowance.
        for role_model in (
            backup_models.get((team_id, config_id)),
            checker_models.get((team_id, config_id)),
        ):
            if isinstance(role_model, str) and role_model.strip():
                enabled.append(role_model.strip())
        writing_default = default.strip() if isinstance(default, str) and default.strip() else None
        if writing_default:
            # Preserve the historical effective default even if the catalog
            # no longer advertises it; runtime validity will flag the choice.
            enabled.append(writing_default)
        default = writing_default
        enabled = list(dict.fromkeys(enabled))
        bind.execute(insert_policy, {
            "llm_config_id": config_id,
            "team_id": team_id,
            "enabled_models_json": __import__("json").dumps(enabled),
            "default_model_name": default,
            "updated_by_user_id": changed_by,
        })

    # Freeze each existing checker's formerly dynamic effective model.
    op.execute(sa.text("""
        UPDATE team_hallucination_check_selections AS selection
        SET model_name_override = config.model_name
        FROM team_llm_configs AS config
        WHERE selection.llm_config_id = config.id
          AND selection.model_name_override IS NULL
          AND config.model_name IS NOT NULL
    """))

    op.add_column("generated_documents", sa.Column("hallucination_check_selection_snapshot_encrypted", sa.Text(), nullable=True))
    op.add_column("generated_documents", sa.Column(
        "hallucination_check_selection_config_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("team_llm_configs.id", ondelete="RESTRICT"),
        nullable=True,
    ))
    op.create_index("ix_generated_documents_checker_selection_config", "generated_documents", ["hallucination_check_selection_config_id"])
    op.add_column("consultation_split_batches", sa.Column("hallucination_check_selection_snapshot_encrypted", sa.Text(), nullable=True))
    op.add_column("consultation_split_batches", sa.Column(
        "hallucination_check_selection_config_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("team_llm_configs.id", ondelete="RESTRICT"),
        nullable=True,
    ))
    op.create_index("ix_split_batches_checker_selection_config", "consultation_split_batches", ["hallucination_check_selection_config_id"])


def downgrade() -> None:
    bind = op.get_bind()
    frozen_document = bind.execute(sa.text("""
        SELECT id FROM generated_documents
        WHERE status IN ('queued', 'processing')
          AND hallucination_check_selection_snapshot_encrypted IS NOT NULL
        LIMIT 1
    """)).first()
    frozen_split = bind.execute(sa.text("""
        SELECT id FROM consultation_split_batches
        WHERE status IN ('generation_queued', 'generating', 'verifying', 'partially_ready')
          AND verification_status IN ('pending', 'verifying')
          AND hallucination_check_selection_snapshot_encrypted IS NOT NULL
        LIMIT 1
    """)).first()
    if frozen_document or frozen_split:
        raise RuntimeError("Cannot downgrade provider model policies while accepted checker work is pending")
    op.drop_index("ix_split_batches_checker_selection_config", table_name="consultation_split_batches")
    op.drop_column("consultation_split_batches", "hallucination_check_selection_config_id")
    op.drop_column("consultation_split_batches", "hallucination_check_selection_snapshot_encrypted")
    op.drop_index("ix_generated_documents_checker_selection_config", table_name="generated_documents")
    op.drop_column("generated_documents", "hallucination_check_selection_config_id")
    op.drop_column("generated_documents", "hallucination_check_selection_snapshot_encrypted")
    op.drop_index("ix_team_llm_provider_model_policies_team_id", table_name="team_llm_provider_model_policies")
    op.drop_table("team_llm_provider_model_policies")
