"""Safe bounded provider transport metadata schema."""
EVENT_CODES = frozenset({"success", "rate_limited", "timeout", "connection_error", "provider_error"})
EVENT_KEYS = frozenset({"occurred_at", "request_ordinal", "duration_ms", "code", "http_status", "retry_wait_ms"})
# JSONPath excludes missing keys, extra keys, nested values and nonintegral
# numbers. Keep the same expression in migration and canonical metadata.
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
