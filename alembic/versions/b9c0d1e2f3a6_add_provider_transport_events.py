"""Add bounded, content-safe per-request provider transport observations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

TRANSPORT_EVENTS_CONSTRAINT = """jsonb_typeof(transport_events_json) = 'array'
AND jsonb_array_length(transport_events_json) <= 4
AND NOT jsonb_path_exists(transport_events_json, '$[*] ? (
 @.type() != "object" ||
 !exists(@.occurred_at) || !exists(@.request_ordinal) || !exists(@.duration_ms) ||
 !exists(@.code) || !exists(@.http_status) || !exists(@.retry_wait_ms) ||
 @.occurred_at.type() != "string" || !(@.occurred_at like_regex "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+[+]00:00$") ||
 @.request_ordinal.type() != "number" || @.request_ordinal < 1 || @.request_ordinal > 4 || @.request_ordinal != @.request_ordinal.floor() ||
 @.duration_ms.type() != "number" || @.duration_ms < 0 || @.duration_ms > 150000 || @.duration_ms != @.duration_ms.floor() ||
 @.retry_wait_ms.type() != "number" || @.retry_wait_ms < 0 || @.retry_wait_ms > 30000 || @.retry_wait_ms != @.retry_wait_ms.floor() ||
 (@.http_status.type() != "null" && (@.http_status.type() != "number" || @.http_status < 100 || @.http_status > 599 || @.http_status != @.http_status.floor())) ||
 @.code.type() != "string" ||
 (@.code != "success" && @.code != "rate_limited" && @.code != "timeout" && @.code != "connection_error" && @.code != "provider_error")
)')
AND NOT jsonb_path_exists(transport_events_json, '$[*].keyvalue() ? (
 @.key != "occurred_at" && @.key != "request_ordinal" && @.key != "duration_ms" &&
 @.key != "code" && @.key != "http_status" && @.key != "retry_wait_ms"
)')"""

revision = "b9c0d1e2f3a6"
down_revision = "a8b9c0d1e2f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generated_documents", sa.Column("validated_draft_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("provider_attempts", sa.Column("transport_events_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_check_constraint("ck_provider_attempts_transport_events", "provider_attempts", TRANSPORT_EVENTS_CONSTRAINT)


def downgrade():
    op.drop_column("generated_documents", "validated_draft_at")
    op.drop_constraint("ck_provider_attempts_transport_events", "provider_attempts", type_="check")
    op.drop_column("provider_attempts", "transport_events_json")
