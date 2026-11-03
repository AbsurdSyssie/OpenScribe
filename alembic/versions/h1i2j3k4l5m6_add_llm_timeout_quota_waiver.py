"""Waive token accounting for identified LLM timeouts."""
from alembic import op
import sqlalchemy as sa

revision = "h1i2j3k4l5m6"
down_revision = "g0b1c2d3e4f5"
branch_labels = None
depends_on = None

LLM_KINDS = (
    "'llm_generation', 'llm_hallucination_check', 'llm_template_suggestion', "
    "'consultation_split_analysis', 'consultation_split_generation', 'consultation_split_verification'"
)


def _basis_constraint(include_timeout: bool) -> str:
    timeout_clause = (
        f"(settlement_basis = 'timeout_waived' AND resource = 'tokens' AND attempt_kind IN ({LLM_KINDS}) "
        "AND settled_units = 0 AND outcome = 'unknown') OR "
        if include_timeout else ""
    )
    return (
        "((settlement_basis = 'reported' AND resource = 'tokens' AND reported_total_tokens = settled_units) OR "
        "(settlement_basis = 'measured' AND resource = 'audio_seconds' AND measured_audio_seconds > 0) OR "
        "(settlement_basis = 'conservative_unknown' AND resource = 'tokens' AND reported_total_tokens IS NULL "
        "AND settled_units = reserved_units AND outcome = 'unknown') OR "
        f"{timeout_clause}"
        "(settlement_basis = 'stream_authorized' AND resource = 'audio_seconds' "
        "AND attempt_kind = 'stt_deepgram_live_stream' AND measured_audio_seconds IS NULL "
        "AND settled_units = reserved_units AND outcome = 'unknown') OR "
        "(settlement_basis = 'stream_measured' AND resource = 'audio_seconds' "
        "AND attempt_kind = 'stt_deepgram_live_stream' AND measured_audio_seconds IS NULL "
        "AND stream_authorized_samples IS NOT NULL AND settled_units = ((stream_authorized_samples + 15999) / 16000) "
        "AND outcome = 'succeeded') OR settlement_basis IS NULL) IS TRUE"
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE providersettlementbasis ADD VALUE IF NOT EXISTS 'timeout_waived' BEFORE 'stream_authorized'")

    op.drop_constraint("ck_provider_attempts_settlement_basis_shape", "provider_attempts", type_="check")
    op.create_check_constraint(
        "ck_provider_attempts_settlement_basis_shape", "provider_attempts", _basis_constraint(True)
    )
    # Only settled conservative-unknown LLM attempts with reliable terminal timeout
    # evidence are backfilled. Other unknown outcomes remain unchanged.
    op.execute(sa.text("""
        UPDATE provider_attempts
        SET settlement_basis = 'timeout_waived', settled_units = 0,
            error_code = coalesce(error_code, 'llm_provider_timeout')
        WHERE status = 'settled'
          AND resource = 'tokens'
          AND attempt_kind IN (
              'llm_generation', 'llm_hallucination_check', 'llm_template_suggestion',
              'consultation_split_analysis', 'consultation_split_generation', 'consultation_split_verification'
          )
          AND settlement_basis = 'conservative_unknown'
          AND (
              error_code = 'llm_provider_timeout'
              OR (jsonb_typeof(transport_events_json) = 'array'
                  AND jsonb_array_length(transport_events_json) > 0
                  AND transport_events_json -> -1 ->> 'code' = 'timeout')
          )
    """))
    # Usage-event aggregation must exclude known timed-out attempts while retaining
    # the failed event and its bounded error metadata.
    op.execute(sa.text("""
        UPDATE provider_usage_events
        SET prompt_tokens = NULL, completion_tokens = NULL, total_tokens = NULL,
            estimated_cost_usd = NULL
        WHERE event_type = 'failed'
          AND (error_code = 'llm_provider_timeout' OR provider_error_code = 'timeout')
    """))


def downgrade() -> None:
    # Map waived attempts back to the prior conservative policy before removing
    # the enum value. Timed-out event token/cost values remain cleared.
    op.execute(sa.text("""
        UPDATE provider_attempts
        SET settlement_basis = 'conservative_unknown', settled_units = reserved_units,
            reported_input_tokens = NULL, reported_output_tokens = NULL, reported_total_tokens = NULL
        WHERE settlement_basis = 'timeout_waived'
    """))
    op.drop_constraint("ck_provider_attempts_settlement_basis_shape", "provider_attempts", type_="check")
    op.create_check_constraint(
        "ck_provider_attempts_settlement_basis_shape", "provider_attempts", _basis_constraint(False)
    )
    # PostgreSQL enum labels are append-only; downgrade intentionally leaves the
    # unused label in place, matching the repository's enum migration convention.
