"""Owner-only, short-lived encrypted generation observations.

Trace writes use their own transaction and never affect provider execution.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, defer

from app.errors import AppError
from app.models import GenerationTraceEvent, ProviderAttempt, QuotaResource, Transcript, User, utcnow
from app.services.content_crypto import (DEK_CACHE_SESSION_KEY, decrypt_text_for_owner, encrypt_text_for_existing_owner, owner_dek_is_cached)
from app.services.security_audit import add_security_event
from app.services.transcripts import get_active_owner_transcript

logger = logging.getLogger("openscribe.generation_trace")
MAX_EVENT_BYTES = 1_000_000
MAX_ROOT_EVENTS = 100
MAX_ROOT_BYTES = 10_000_000
SAFE_ERROR_CODES = frozenset({"llm_generation_invalid_json", "llm_generation_truncated", "llm_generation_failed",
    "llm_provider_bad_response", "llm_provider_timeout", "llm_provider_unreachable", "llm_hallucination_check_failed",
    "llm_invalid_credential", "llm_generation_preparation_failed", "consultation_split_analysis_invalid",
    "consultation_split_generation_invalid", "consultation_split_verification_invalid", "template_suggestion_invalid_response"})
_context: ContextVar[object | None] = ContextVar("generation_trace_context", default=None)
_request: ContextVar[dict | None] = ContextVar("generation_trace_request", default=None)



def _trace_timeouts(db: Session) -> tuple[str, str] | None:
    if db.get_bind().dialect.name != "postgresql":
        return None
    previous = db.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()
    db.execute(text("SET LOCAL lock_timeout = '250ms'"))
    db.execute(text("SET LOCAL statement_timeout = '500ms'"))
    return previous[0], previous[1]


def _restore_timeouts(db: Session, previous: tuple[str, str] | None) -> None:
    db.flush()
    if previous is not None:
        db.execute(text("SELECT set_config('lock_timeout', :lock, true), set_config('statement_timeout', :statement, true)"),
                   {"lock": previous[0], "statement": previous[1]})

def set_user_generation_trace_enabled(db: Session, *, actor: User, target_user_id: UUID, enabled: bool) -> None:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System administrator access required")
    target = db.get(User, target_user_id)
    if target is None or target.is_system_admin:
        raise AppError(404, "not_found", "User not found")
    target.generation_trace_enabled = bool(enabled)
    add_security_event(db, action="generation_trace_enabled" if enabled else "generation_trace_disabled", actor=actor, target=target)
    db.commit()


def _metadata(event: GenerationTraceEvent, attempt: ProviderAttempt | None = None) -> dict:
    return {"id": str(event.id), "captured_at": event.captured_at.isoformat(), "expires_at": event.expires_at.isoformat(),
            "workflow": event.workflow, "attempt_id": str(event.attempt_id) if event.attempt_id else None,
            "generated_document_id": str(event.generated_document_id) if event.generated_document_id else None,
            "state": event.state, "reason": event.reason, "request_ordinal": event.request_ordinal,
            "outcome": event.outcome, "validation": event.validation, "error_code": event.trace_error_code, "decision": event.decision, "http_status": event.http_status, "retry_wait_ms": event.retry_wait_ms,
            "provider_adapter": event.provider_adapter, "provider_model": event.provider_model,
            "correlation_id": str(attempt.correlation_id) if attempt else None,
            "attempt_number": attempt.attempt_number if attempt else None,
            "backup_of_attempt_id": str(attempt.backup_of_attempt_id) if attempt and attempt.backup_of_attempt_id else None,
            "repair_of_attempt_id": str(attempt.repair_of_attempt_id) if attempt and attempt.repair_of_attempt_id else None,
            "split_execution_id": str(attempt.consultation_split_execution_id) if attempt and attempt.consultation_split_execution_id else None}


def list_traces(db: Session, actor: User, transcript_id: UUID, *, generated_document_id: UUID | None = None) -> dict:
    if actor.is_system_admin:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    root = get_active_owner_transcript(db, actor, transcript_id=transcript_id)
    query = select(GenerationTraceEvent).options(defer(GenerationTraceEvent.request_encrypted), defer(GenerationTraceEvent.response_encrypted)).where(GenerationTraceEvent.transcript_id == root.id,
        GenerationTraceEvent.owner_user_id == actor.id, GenerationTraceEvent.expires_at > utcnow())
    if generated_document_id is not None:
        query = query.where(GenerationTraceEvent.generated_document_id == generated_document_id)
    events = db.scalars(query.order_by(GenerationTraceEvent.captured_at, GenerationTraceEvent.id)).all()
    attempt_ids = {event.attempt_id for event in events if event.attempt_id is not None}
    attempts = {attempt.id: attempt for attempt in db.scalars(select(ProviderAttempt).where(
        ProviderAttempt.id.in_(attempt_ids), ProviderAttempt.owner_user_id == actor.id,
        ProviderAttempt.transcript_id == root.id))} if attempt_ids else {}
    # Content-free transport observations can reveal missing captures, but cannot
    # distinguish disabled capture, expiry, or a failed write.
    candidates = list(db.scalars(select(ProviderAttempt).where(
        ProviderAttempt.transcript_id == root.id, ProviderAttempt.owner_user_id == actor.id,
        ProviderAttempt.created_at > utcnow() - timedelta(hours=24),
        ProviderAttempt.resource == QuotaResource.tokens
    ).order_by(ProviderAttempt.created_at.desc(), ProviderAttempt.id.desc()).limit(101)))
    missing = []
    present = {(event.attempt_id, event.request_ordinal) for event in events}
    for attempt in candidates[:100]:
        if generated_document_id is not None and attempt.generated_document_id != generated_document_id:
            continue
        for observation in attempt.transport_events_json or []:
            ordinal = observation.get("request_ordinal") if isinstance(observation, dict) else None
            if type(ordinal) is not int or (attempt.id, ordinal) in present:
                continue
            missing.append({"attempt_id": str(attempt.id), "request_ordinal": ordinal,
                            "occurred_at": observation.get("occurred_at"), "outcome": observation.get("code"),
                            "http_status": observation.get("http_status"), "retry_wait_ms": observation.get("retry_wait_ms")})
    return {"enabled": actor.generation_trace_enabled,
            "events": [_metadata(event, attempts.get(event.attempt_id)) for event in events],
            "missing_attempts": missing, "missing_attempts_bounded": len(candidates) > 100}


def get_trace(db: Session, actor: User, transcript_id: UUID, event_id: UUID) -> dict:
    if actor.is_system_admin:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    root = get_active_owner_transcript(db, actor, transcript_id=transcript_id)
    event = db.scalar(select(GenerationTraceEvent).where(GenerationTraceEvent.id == event_id,
        GenerationTraceEvent.transcript_id == root.id, GenerationTraceEvent.owner_user_id == actor.id,
        GenerationTraceEvent.expires_at > utcnow()))
    if event is None:
        raise AppError(404, "not_found", "Generation trace not found")
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.id == event.attempt_id,
        ProviderAttempt.owner_user_id == actor.id, ProviderAttempt.transcript_id == root.id)) if event.attempt_id else None
    result = _metadata(event, attempt)
    for field in ("request", "response"):
        result[field] = decrypt_text_for_owner(db, owner_user_id=actor.id, table="generation_trace_events",
            field=field, record_id=event.id, stored_value=getattr(event, field + "_encrypted"))
    # This is a syntactic preview only; validation is reported separately.
    from app.services.json_validation import reject_duplicate_json_keys
    result["parsed_json_preview"] = None
    result["parsed_json_available"] = False
    if result["response"] is not None:
        try:
            result["parsed_json_preview"] = json.loads(result["response"], object_pairs_hook=reject_duplicate_json_keys,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid constant")))
            result["parsed_json_available"] = True
        except (ValueError, RecursionError):
            pass
    return result


def clear_traces(db: Session, actor: User, transcript_id: UUID) -> None:
    if actor.is_system_admin:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    root = get_active_owner_transcript(db, actor, transcript_id=transcript_id)
    db.execute(delete(GenerationTraceEvent).where(GenerationTraceEvent.transcript_id == root.id,
                                                   GenerationTraceEvent.owner_user_id == actor.id))
    db.commit()


@contextmanager
def attempt_context(db: Session, attempt_id: UUID):
    enabled = False
    try:
        with Session(bind=db.get_bind(), join_transaction_mode="create_savepoint") as lookup:
            _trace_timeouts(lookup)
            attempt = lookup.get(ProviderAttempt, attempt_id)
            owner = lookup.get(User, attempt.owner_user_id) if attempt and attempt.owner_user_id else None
            enabled = owner is not None and not owner.is_system_admin and owner.generation_trace_enabled
    except Exception:
        logger.warning("generation_trace_lookup_failed")
    token = _context.set((db.get_bind(), attempt_id, dict(db.info.get(DEK_CACHE_SESSION_KEY, {}))) if enabled else None)
    try:
        yield
    finally:
        _context.reset(token)


def begin_request(request: dict, credential: object | None, ordinal: int) -> UUID | None:
    context = _context.get()
    if context is None:
        return None
    try:
        bind, attempt_id, cache = context
        serialized = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        with Session(bind=bind, join_transaction_mode="create_savepoint") as db:
            db.info[DEK_CACHE_SESSION_KEY] = cache.copy()
            previous_timeouts = _trace_timeouts(db)
            attempt = db.get(ProviderAttempt, attempt_id)
            if not attempt or not attempt.transcript_id or not attempt.owner_user_id:
                return None
            root = db.scalar(select(Transcript).where(Transcript.id == attempt.transcript_id).with_for_update())
            now = utcnow()
            if root is None or root.owner_user_id != attempt.owner_user_id or root.retention_expires_at <= now:
                return None
            owner = db.get(User, root.owner_user_id)
            if owner is None or owner.is_system_admin or not owner.generation_trace_enabled:
                return None
            count, size = db.execute(select(func.count(GenerationTraceEvent.id),
                func.coalesce(func.sum(func.coalesce(func.length(GenerationTraceEvent.request_encrypted), 0) +
                                       func.coalesce(func.length(GenerationTraceEvent.response_encrypted), 0)), 0))
                .where(GenerationTraceEvent.transcript_id == root.id)).one()
            if count >= MAX_ROOT_EVENTS:
                return None
            event = GenerationTraceEvent(id=uuid4(), transcript_id=root.id, owner_user_id=owner.id,
                attempt_id=attempt.id, generated_document_id=attempt.generated_document_id,
                workflow=attempt.attempt_kind.value, request_ordinal=ordinal, provider_adapter=attempt.provider_adapter,
                provider_model=attempt.provider_model, captured_at=now,
                expires_at=min(now + timedelta(hours=24), root.retention_expires_at), state="pending")
            secret = credential if isinstance(credential, str) and credential else None
            if count == MAX_ROOT_EVENTS - 1:
                event.state, event.reason = "incomplete", "event_limit"
            elif secret and secret in serialized:
                event.state, event.reason = "incomplete", "credential_safety"
            elif not owner_dek_is_cached(db, owner_user_id=owner.id):
                event.state, event.reason = "incomplete", "key_unavailable"
            elif len(serialized.encode()) > MAX_EVENT_BYTES:
                event.state, event.reason = "incomplete", "size_limit"
            else:
                ciphertext = encrypt_text_for_existing_owner(db, owner_user_id=owner.id,
                    table="generation_trace_events", field="request", record_id=event.id, plaintext=serialized)
                if size + len(ciphertext.encode()) > MAX_ROOT_BYTES:
                    event.state, event.reason = "incomplete", "size_limit"
                else:
                    event.request_encrypted = ciphertext
            db.add(event)
            _restore_timeouts(db, previous_timeouts)
            db.commit()
            return event.id
    except Exception:
        logger.warning("generation_trace_write_failed")
        return None


def finish_request(event_id: UUID | None, response: str | None, *, reason: str | None = None, outcome: str | None = None, http_status: int | None = None, retry_wait_ms: int = 0, error_code: str | None = None) -> None:
    context = _context.get()
    if context is None or event_id is None:
        return
    try:
        bind, _, cache = context
        with Session(bind=bind, join_transaction_mode="create_savepoint") as db:
            db.info[DEK_CACHE_SESSION_KEY] = cache.copy()
            previous_timeouts = _trace_timeouts(db)
            event = db.get(GenerationTraceEvent, event_id)
            if event is None:
                return
            root = db.scalar(select(Transcript).where(Transcript.id == event.transcript_id).with_for_update())
            now = utcnow()
            if root is None or root.retention_expires_at <= now or event.expires_at <= now:
                return
            event = db.get(GenerationTraceEvent, event_id, populate_existing=True)
            if event is None or event.state != "pending":
                return
            event.outcome, event.http_status, event.retry_wait_ms = outcome, http_status, retry_wait_ms
            if error_code in SAFE_ERROR_CODES:
                event.trace_error_code = error_code
            if response is None:
                event.state, event.reason = ("incomplete", reason) if reason else ("complete", None)
            elif not owner_dek_is_cached(db, owner_user_id=event.owner_user_id):
                event.state, event.reason = "incomplete", "key_unavailable"
            elif len(response.encode()) > MAX_EVENT_BYTES:
                event.state, event.reason = "incomplete", "size_limit"
            else:
                ciphertext = encrypt_text_for_existing_owner(db, owner_user_id=event.owner_user_id,
                    table="generation_trace_events", field="response", record_id=event.id, plaintext=response)
                size = db.scalar(select(func.coalesce(func.sum(
                    func.coalesce(func.length(GenerationTraceEvent.request_encrypted), 0)
                    + func.coalesce(func.length(GenerationTraceEvent.response_encrypted), 0)
                ), 0)).where(GenerationTraceEvent.transcript_id == root.id)) or 0
                if size + len(ciphertext.encode()) > MAX_ROOT_BYTES:
                    event.state, event.reason = "incomplete", "size_limit"
                else:
                    event.response_encrypted = ciphertext
                    event.state, event.reason = "incomplete", "validation_not_captured" if outcome == "success" else "partial_response"
            _restore_timeouts(db, previous_timeouts)
            db.commit()
    except Exception:
        logger.warning("generation_trace_write_failed")


@contextmanager
def request_context(request: dict, credential: object | None):
    token = _request.set({"request": request, "credential": credential, "raw": None})
    try:
        yield
    finally:
        _request.reset(token)


def start_transport(ordinal):
    state = _request.get()
    if state is None:
        return None
    state["raw"] = None
    return begin_request(state["request"], state["credential"], ordinal)


def capture_active() -> bool:
    return _context.get() is not None and _request.get() is not None


def raw_model_text(text: str) -> None:
    state = _request.get()
    if state is not None:
        state["raw"] = text


def end_transport(event_id, *, outcome=None, http_status=None, retry_wait_ms=0, error_code=None):
    state = _request.get()
    if state is None:
        return
    raw = state["raw"]
    credential = state["credential"]
    if isinstance(raw, str) and isinstance(credential, str) and credential and credential in raw:
        finish_request(event_id, None, reason="credential_safety", outcome=outcome, http_status=http_status, retry_wait_ms=retry_wait_ms, error_code=error_code)
    else:
        finish_request(event_id, raw, outcome=outcome, http_status=http_status, retry_wait_ms=retry_wait_ms, error_code=error_code)


def record_outcome(db: Session, *, attempt_id: UUID, validation: str | None = None, decision: str | None = None, not_dispatched: bool = False, error_code: str | None = None) -> None:
    """Record controlled workflow decisions independently of business work."""
    if validation not in {None, "accepted", "invalid"} or decision not in {None, "retry", "backup", "repair", "terminal"}:
        logger.warning("generation_trace_invalid_outcome")
        return
    try:
        with Session(bind=db.get_bind(), join_transaction_mode="create_savepoint") as trace_db:
            previous_timeouts = _trace_timeouts(trace_db)
            attempt = trace_db.get(ProviderAttempt, attempt_id)
            if attempt is None or attempt.transcript_id is None:
                return
            root = trace_db.get(Transcript, attempt.transcript_id)
            now = utcnow()
            if root is None or root.retention_expires_at <= now:
                return
            event = trace_db.scalar(select(GenerationTraceEvent).where(GenerationTraceEvent.attempt_id == attempt_id,
                GenerationTraceEvent.transcript_id == root.id, GenerationTraceEvent.expires_at > now)
                .order_by(GenerationTraceEvent.captured_at.desc(), GenerationTraceEvent.request_ordinal.desc(), GenerationTraceEvent.id.desc()).limit(1))
            if event is None and not_dispatched:
                root = trace_db.scalar(select(Transcript).where(Transcript.id == attempt.transcript_id).with_for_update())
                if root is None or root.retention_expires_at <= now or root.owner_user_id != attempt.owner_user_id:
                    return
                owner = trace_db.get(User, root.owner_user_id)
                count = trace_db.scalar(select(func.count(GenerationTraceEvent.id)).where(GenerationTraceEvent.transcript_id == root.id))
                if owner is None or owner.is_system_admin or not owner.generation_trace_enabled or count >= MAX_ROOT_EVENTS:
                    return
                event = GenerationTraceEvent(id=uuid4(), transcript_id=root.id, owner_user_id=owner.id,
                    attempt_id=attempt.id, generated_document_id=attempt.generated_document_id,
                    workflow=attempt.attempt_kind.value, request_ordinal=1, provider_adapter=attempt.provider_adapter,
                    provider_model=attempt.provider_model, captured_at=now,
                    expires_at=min(now + timedelta(hours=24), root.retention_expires_at),
                    state="incomplete" if count == MAX_ROOT_EVENTS - 1 else "complete",
                    reason="event_limit" if count == MAX_ROOT_EVENTS - 1 else None, outcome="not_dispatched")
                trace_db.add(event)
            if event is None:
                return
            if validation is not None:
                event.validation = validation
                if event.reason == "validation_not_captured":
                    event.state, event.reason = "complete", None
            if decision is not None:
                event.decision = decision
            if error_code in SAFE_ERROR_CODES:
                event.trace_error_code = error_code
            _restore_timeouts(trace_db, previous_timeouts)
            trace_db.commit()
    except Exception:
        logger.warning("generation_trace_write_failed")
