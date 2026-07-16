"""add quota accounting foundation

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a5
Create Date: 2026-07-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c1d2e3f4a5b6"
down_revision = "b9c0d1e2f3a5"
branch_labels = None
depends_on = None


quota_resource = postgresql.ENUM("tokens", "audio_seconds", name="quotaresource", create_type=False)
quota_period = postgresql.ENUM("daily", "monthly", name="quotaperiod", create_type=False)
user_quota_policy_event_type = postgresql.ENUM("grant", "reset", "limit_change", name="userquotapolicyeventtype", create_type=False)
user_quota_reason_code = postgresql.ENUM(
    "policy_change",
    "temporary_allowance",
    "failed_job_correction",
    "administrative_correction",
    "other",
    name="userquotareasoncode",
    create_type=False,
)
attempt_kind = postgresql.ENUM(
    "llm_generation",
    "llm_hallucination_check",
    "stt_conversation",
    "stt_post_consultation_dictation",
    "stt_prompt_context",
    "stt_provider_test",
    name="attemptkind",
    create_type=False,
)
attempt_status = postgresql.ENUM("reserved", "submitted", "settled", "cancelled", name="attemptstatus", create_type=False)
attempt_outcome = postgresql.ENUM("succeeded", "failed", "unknown", "cancelled", name="attemptoutcome", create_type=False)
provider_settlement_basis = postgresql.ENUM(
    "reported", "measured", "conservative_unknown", name="providersettlementbasis", create_type=False
)
task_dispatch_kind = postgresql.ENUM("generation", "ingestion", name="taskdispatchkind", create_type=False)
task_dispatch_state = postgresql.ENUM("pending", "published", "cancelled", "failed", name="taskdispatchstate", create_type=False)
task_dispatch_source_kind = postgresql.ENUM("generated_document", "transcript_ingestion_job", name="taskdispatchsourcekind", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TYPE hallucinationcheckstatus ADD VALUE IF NOT EXISTS 'skipped_quota'")
    for enum_type in (
        quota_resource, quota_period, user_quota_policy_event_type, user_quota_reason_code,
        attempt_kind, attempt_status, attempt_outcome, provider_settlement_basis,
        task_dispatch_kind, task_dispatch_state, task_dispatch_source_kind,
    ):
        enum_type.create(bind, checkfirst=True)

    for column_name in (
        "daily_token_limit", "monthly_token_limit", "daily_audio_seconds_limit", "monthly_audio_seconds_limit"
    ):
        op.add_column("users", sa.Column(column_name, sa.Integer(), nullable=True))
        op.create_check_constraint(
            f"ck_users_{column_name}_nonnegative", "users", f"{column_name} IS NULL OR {column_name} >= 0"
        )

    op.create_table(
        "user_quota_policy_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id_snapshot", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revoker_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoker_user_id_snapshot", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revocation_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", user_quota_policy_event_type, nullable=False),
        sa.Column("resource", quota_resource, nullable=False),
        sa.Column("period", quota_period, nullable=False),
        sa.Column("reason_code", user_quota_reason_code, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("previous_limit", sa.Integer(), nullable=True),
        sa.Column("new_limit", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason_code", user_quota_reason_code, nullable=True),
        sa.Column("revocation_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("char_length(btrim(reason)) BETWEEN 1 AND 500", name="ck_user_quota_policy_events_reason_length"),
        sa.CheckConstraint("expires_at IS NULL OR expires_at > effective_at", name="ck_user_quota_policy_events_expiry_after_effective"),
        sa.CheckConstraint(
            "((event_type = 'grant' AND amount > 0 AND previous_limit IS NULL AND new_limit IS NULL) OR "
            "(event_type = 'reset' AND amount IS NULL AND previous_limit IS NULL AND new_limit IS NULL AND expires_at IS NULL) OR "
            "(event_type = 'limit_change' AND amount IS NULL AND expires_at IS NULL "
            "AND (previous_limit IS NULL OR previous_limit >= 0) AND (new_limit IS NULL OR new_limit >= 0) "
            "AND previous_limit IS DISTINCT FROM new_limit)) IS TRUE",
            name="ck_user_quota_policy_events_event_shape",
        ),
        sa.CheckConstraint(
            "((event_type = 'grant' AND revoked_at IS NOT NULL AND revoker_user_id_snapshot IS NOT NULL "
            "AND revocation_operation_id IS NOT NULL AND revocation_reason_code IS NOT NULL "
            "AND char_length(btrim(revocation_reason)) BETWEEN 1 AND 500) OR "
            "(revoker_user_id IS NULL AND revoker_user_id_snapshot IS NULL AND revocation_operation_id IS NULL "
            "AND revoked_at IS NULL AND revocation_reason_code IS NULL AND revocation_reason IS NULL)) IS TRUE",
            name="ck_user_quota_policy_events_revocation_shape",
        ),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoker_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revocation_operation_id", name="uq_user_quota_policy_events_revocation_operation"),
        sa.UniqueConstraint("operation_id", "target_user_id", "resource", "period", "event_type", name="uq_uqpe_operation_target_resource_period_type"),
    )
    op.create_index("ix_user_quota_policy_events_target_created", "user_quota_policy_events", ["target_user_id", "created_at"])
    op.create_index("ix_user_quota_policy_events_lookup", "user_quota_policy_events", ["target_user_id", "resource", "period", "effective_at"])
    op.create_index(
        "ix_user_quota_policy_events_active_grant", "user_quota_policy_events",
        ["target_user_id", "resource", "period", "effective_at", "expires_at"],
        postgresql_where=sa.text("event_type = 'grant' AND revoked_at IS NULL"),
    )

    op.create_table(
        "provider_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transcript_ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempt_kind", attempt_kind, nullable=False),
        sa.Column("resource", quota_resource, nullable=False),
        sa.Column("status", attempt_status, nullable=False),
        sa.Column("outcome", attempt_outcome, nullable=True),
        sa.Column("settlement_basis", provider_settlement_basis, nullable=True),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
        sa.Column("settled_units", sa.Integer(), nullable=True),
        sa.Column("reported_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reported_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reported_total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("measured_audio_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("provider_adapter", sa.String(length=64), nullable=True),
        sa.Column("provider_model", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("provider_error_code", sa.String(length=128), nullable=True),
        sa.Column("provider_http_status", sa.Integer(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reservation_valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_number >= 1", name="ck_provider_attempts_attempt_number_positive"),
        sa.CheckConstraint("reserved_units > 0", name="ck_provider_attempts_reserved_units_positive"),
        sa.CheckConstraint("settled_units IS NULL OR settled_units >= 0", name="ck_provider_attempts_settled_units_nonnegative"),
        sa.CheckConstraint("reported_input_tokens IS NULL OR reported_input_tokens >= 0", name="ck_provider_attempts_reported_input_tokens_nonnegative"),
        sa.CheckConstraint("reported_output_tokens IS NULL OR reported_output_tokens >= 0", name="ck_provider_attempts_reported_output_tokens_nonnegative"),
        sa.CheckConstraint("reported_total_tokens IS NULL OR reported_total_tokens >= 0", name="ck_provider_attempts_reported_total_tokens_nonnegative"),
        sa.CheckConstraint("measured_audio_seconds IS NULL OR measured_audio_seconds > 0", name="ck_provider_attempts_measured_audio_seconds_positive"),
        sa.CheckConstraint("provider_http_status IS NULL OR provider_http_status BETWEEN 100 AND 599", name="ck_provider_attempts_provider_http_status_range"),
        sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 128", name="ck_provider_attempts_error_code_length"),
        sa.CheckConstraint("provider_error_code IS NULL OR char_length(provider_error_code) <= 128", name="ck_provider_attempts_provider_error_code_length"),
        sa.CheckConstraint(
            "((status = 'reserved' AND submitted_at IS NULL AND deadline_at IS NULL AND settled_at IS NULL AND cancelled_at IS NULL "
            "AND outcome IS NULL AND settlement_basis IS NULL AND settled_units IS NULL) OR "
            "(status = 'submitted' AND submitted_at IS NOT NULL AND deadline_at IS NOT NULL AND settled_at IS NULL AND cancelled_at IS NULL "
            "AND outcome IS NULL AND settlement_basis IS NULL AND settled_units IS NULL) OR "
            "(status = 'settled' AND submitted_at IS NOT NULL AND deadline_at IS NOT NULL AND settled_at IS NOT NULL AND cancelled_at IS NULL "
            "AND outcome IN ('succeeded', 'failed', 'unknown') AND settled_units IS NOT NULL AND settlement_basis IS NOT NULL) OR "
            "(status = 'cancelled' AND submitted_at IS NULL AND deadline_at IS NULL AND settled_at IS NULL AND cancelled_at IS NOT NULL "
            "AND outcome = 'cancelled' AND settlement_basis IS NULL AND settled_units IS NULL)) IS TRUE",
            name="ck_provider_attempts_state_shape",
        ),
        sa.CheckConstraint("reservation_valid_until > authorized_at", name="ck_provider_attempts_reservation_valid_after_authorized"),
        sa.CheckConstraint(
            "((settlement_basis = 'reported' AND resource = 'tokens' AND reported_total_tokens = settled_units) OR "
            "(settlement_basis = 'measured' AND resource = 'audio_seconds' AND measured_audio_seconds > 0) OR "
            "(settlement_basis = 'conservative_unknown' AND resource = 'tokens' AND reported_total_tokens IS NULL "
            "AND settled_units = reserved_units AND outcome = 'unknown') OR settlement_basis IS NULL) IS TRUE",
            name="ck_provider_attempts_settlement_basis_shape",
        ),
        sa.CheckConstraint(
            "((resource = 'tokens' AND measured_audio_seconds IS NULL) OR "
            "(resource = 'audio_seconds' AND measured_audio_seconds > 0 AND reported_input_tokens IS NULL "
            "AND reported_output_tokens IS NULL AND reported_total_tokens IS NULL)) IS TRUE",
            name="ck_provider_attempts_resource_payload_shape",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transcript_ingestion_job_id"], ["transcript_ingestion_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["generated_document_id"], ["generated_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id", "attempt_number", name="uq_provider_attempts_correlation_attempt"),
    )
    op.create_index("ix_provider_attempts_owner_resource_authorized", "provider_attempts", ["owner_user_id", "resource", "authorized_at"])
    op.create_index("ix_provider_attempts_active_reservations", "provider_attempts", ["owner_user_id", "resource", "reservation_valid_until"], postgresql_where=sa.text("status = 'reserved'"))
    op.create_index("ix_provider_attempts_submitted_deadline", "provider_attempts", ["deadline_at"], postgresql_where=sa.text("status = 'submitted'"))
    op.create_index("ix_provider_attempts_team_resource_settled", "provider_attempts", ["team_id", "resource", "settled_at"], postgresql_where=sa.text("status = 'settled'"))

    op.create_table(
        "task_dispatch_outbox",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dispatch_kind", task_dispatch_kind, nullable=False),
        sa.Column("state", task_dispatch_state, nullable=False),
        sa.Column("source_kind", task_dispatch_source_kind, nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_task_dispatch_outbox_attempt_count_nonnegative"),
        sa.CheckConstraint("last_error_code IS NULL OR char_length(last_error_code) <= 128", name="ck_task_dispatch_outbox_last_error_code_length"),
        sa.CheckConstraint(
            "(state = 'pending' AND published_at IS NULL AND cancelled_at IS NULL AND failed_at IS NULL) OR "
            "(state = 'published' AND published_at IS NOT NULL AND cancelled_at IS NULL AND failed_at IS NULL) OR "
            "(state = 'cancelled' AND published_at IS NULL AND cancelled_at IS NOT NULL AND failed_at IS NULL) OR "
            "(state = 'failed' AND published_at IS NULL AND cancelled_at IS NULL AND failed_at IS NOT NULL)",
            name="ck_task_dispatch_outbox_state_timestamps",
        ),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint("dispatch_kind", "source_kind", "source_id", name="uq_task_dispatch_outbox_dispatch_source"),
    )
    op.create_index("ix_task_dispatch_outbox_pending_retry", "task_dispatch_outbox", ["state", "next_attempt_at"])
    op.create_index("ix_task_dispatch_outbox_source", "task_dispatch_outbox", ["source_kind", "source_id"])


def downgrade() -> None:
    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("daily_token_limit", sa.Integer()),
        sa.column("monthly_token_limit", sa.Integer()),
        sa.column("daily_audio_seconds_limit", sa.Integer()),
        sa.column("monthly_audio_seconds_limit", sa.Integer()),
    )
    if bind.execute(sa.select(users.c.daily_token_limit).where(sa.or_(
        users.c.daily_token_limit.is_not(None), users.c.monthly_token_limit.is_not(None),
        users.c.daily_audio_seconds_limit.is_not(None), users.c.monthly_audio_seconds_limit.is_not(None),
    )).limit(1)).first() is not None:
        raise RuntimeError("Cannot downgrade quota accounting foundation while user quota limits are populated.")
    for table_name, key_column in (("user_quota_policy_events", "id"), ("provider_attempts", "id"), ("task_dispatch_outbox", "task_id")):
        table = sa.table(table_name, sa.column(key_column, postgresql.UUID(as_uuid=True)))
        if bind.execute(sa.select(table.c[key_column]).limit(1)).first() is not None:
            raise RuntimeError(f"Cannot downgrade quota accounting foundation while {table_name} contains rows.")

    op.drop_index("ix_task_dispatch_outbox_source", table_name="task_dispatch_outbox")
    op.drop_index("ix_task_dispatch_outbox_pending_retry", table_name="task_dispatch_outbox")
    op.drop_table("task_dispatch_outbox")
    for index_name in (
        "ix_provider_attempts_team_resource_settled", "ix_provider_attempts_submitted_deadline",
        "ix_provider_attempts_active_reservations", "ix_provider_attempts_owner_resource_authorized",
    ):
        op.drop_index(index_name, table_name="provider_attempts")
    op.drop_table("provider_attempts")
    for index_name in (
        "ix_user_quota_policy_events_active_grant", "ix_user_quota_policy_events_lookup",
        "ix_user_quota_policy_events_target_created",
    ):
        op.drop_index(index_name, table_name="user_quota_policy_events")
    op.drop_table("user_quota_policy_events")
    for column_name in (
        "monthly_audio_seconds_limit", "daily_audio_seconds_limit", "monthly_token_limit", "daily_token_limit"
    ):
        op.drop_constraint(f"ck_users_{column_name}_nonnegative", "users", type_="check")
        op.drop_column("users", column_name)
    for enum_type in (
        task_dispatch_source_kind, task_dispatch_state, task_dispatch_kind, provider_settlement_basis,
        attempt_outcome, attempt_status, attempt_kind, user_quota_reason_code,
        user_quota_policy_event_type, quota_period, quota_resource,
    ):
        enum_type.drop(bind, checkfirst=True)
    # PostgreSQL enum values cannot be removed transactionally. Leaving the
    # unused hallucinationcheckstatus label is safe for the previous version.
