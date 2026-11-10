from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import AttemptKind, GenerationTraceEvent, QuotaResource, Transcript, TranscriptStatus, utcnow
from app.services import generation_trace as trace
from app.services.content_crypto import encrypt_text_for_owner
from app.services.quotas import reserve_provider_attempt, mark_provider_attempt_submitted
from app.services.transcripts import delete_expired_transcripts


@pytest.fixture
def trace_scope(db_session, make_user):
    owner = make_user()
    owner.generation_trace_enabled = True
    now = utcnow()
    root = Transcript(owner_user_id=owner.id, team_id=owner.team_id, status=TranscriptStatus.ready,
                      retention_expires_at=now + timedelta(days=2), retention_days_applied=2)
    db_session.add(root)
    db_session.flush()
    encrypt_text_for_owner(db_session, owner_user_id=owner.id, table="transcripts", field="synthetic", record_id=root.id, plaintext="test")
    attempt = reserve_provider_attempt(db_session, team_id=owner.team_id, owner_user_id=owner.id,
        resource=QuotaResource.tokens, attempt_kind=AttemptKind.llm_generation, correlation_id=uuid4(),
        attempt_number=1, reserved_units=3, authorized_at=now, reservation_valid_until=now + timedelta(minutes=5),
        transcript_id=root.id, provider_model="model", provider_adapter="openai_chat")
    mark_provider_attempt_submitted(db_session, attempt_id=attempt.id, now=now, deadline_at=now + timedelta(seconds=150))
    db_session.commit()
    return owner, root, attempt


def test_capture_roundtrip_owner_authorization_and_clear(db_session, make_user, trace_scope):
    owner, root, attempt = trace_scope
    other = make_user(email="other@example.com", team=owner.team)
    admin = make_user(email="admin@example.com", is_system_admin=True)
    request = {"messages": [{"role": "user", "content": "synthetic [PERSON_1]"}]}
    raw = ' \n{"note": "  a\\nb  "}\n '
    with trace.attempt_context(db_session, attempt.id), trace.request_context(request, "synthetic-api-key"):
        event_id = trace.start_transport(1)
        assert event_id is not None
        trace.raw_model_text(raw)
        trace.end_transport(event_id, outcome="success")
    db_session.expire_all()
    row = db_session.get(GenerationTraceEvent, event_id)
    assert "synthetic [PERSON_1]" not in row.request_encrypted
    assert raw not in row.response_encrypted
    detail = trace.get_trace(db_session, owner, root.id, event_id)
    assert detail["response"] == raw
    assert detail["parsed_json_available"] is True
    assert detail["parsed_json_preview"] == {"note": "  a\nb  "}
    assert detail["state"] == "incomplete"
    trace.record_outcome(db_session, attempt_id=attempt.id, validation="accepted", decision="terminal")
    db_session.expire_all()
    assert trace.get_trace(db_session, owner, root.id, event_id)["state"] == "complete"
    for actor in (other, admin):
        with pytest.raises(AppError):
            trace.get_trace(db_session, actor, root.id, event_id)
    trace.clear_traces(db_session, owner, root.id)
    assert trace.list_traces(db_session, owner, root.id)["events"] == []
    with trace.attempt_context(db_session, attempt.id), trace.request_context(request, None):
        trace.end_transport(event_id, outcome="success")
    assert db_session.get(GenerationTraceEvent, event_id) is None


def test_disabled_and_expiry_sweep(db_session, trace_scope):
    owner, root, attempt = trace_scope
    owner.generation_trace_enabled = False
    db_session.commit()
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        assert trace.start_transport(1) is None
    owner.generation_trace_enabled = True
    db_session.commit()
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        event_id = trace.start_transport(1)
    event = db_session.get(GenerationTraceEvent, event_id)
    event.captured_at = utcnow() - timedelta(hours=25)
    event.expires_at = event.captured_at + timedelta(hours=23)
    db_session.commit()
    assert trace.list_traces(db_session, owner, root.id)["events"] == []
    assert delete_expired_transcripts(db_session) == 0
    db_session.expire_all()
    assert db_session.get(GenerationTraceEvent, event_id) is None


def test_missing_key_never_contacts_vault_and_reports_omission(db_session, trace_scope, monkeypatch):
    owner, root, attempt = trace_scope
    db_session.info.clear()
    monkeypatch.setattr("app.services.content_crypto.unwrap_user_content_data_key", lambda **_: pytest.fail("Vault called"))
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": ["synthetic"]}, None):
        event_id = trace.start_transport(1)
    db_session.expire_all()
    event = db_session.get(GenerationTraceEvent, event_id)
    assert (event.state, event.reason, event.request_encrypted) == ("incomplete", "key_unavailable", None)


def test_expired_root_and_trace_never_decrypt(db_session, trace_scope, monkeypatch):
    owner, root, attempt = trace_scope
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        event_id = trace.start_transport(1)
    monkeypatch.setattr(trace, "decrypt_text_for_owner", lambda *a, **k: pytest.fail("decrypt called"))
    event = db_session.get(GenerationTraceEvent, event_id)
    event.captured_at = utcnow() - timedelta(hours=25)
    event.expires_at = event.captured_at + timedelta(hours=23)
    db_session.commit()
    with pytest.raises(AppError):
        trace.get_trace(db_session, owner, root.id, event_id)
    root.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(AppError):
        trace.get_trace(db_session, owner, root.id, event_id)


def test_bounds_do_not_capture_partial_content(db_session, trace_scope, monkeypatch):
    owner, root, attempt = trace_scope
    monkeypatch.setattr(trace, "MAX_EVENT_BYTES", 20)
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": ["x" * 30]}, None):
        event_id = trace.start_transport(1)
    db_session.expire_all()
    event = db_session.get(GenerationTraceEvent, event_id)
    assert (event.state, event.reason, event.request_encrypted) == ("incomplete", "size_limit", None)


def test_event_cap_leaves_an_explicit_marker(db_session, trace_scope, monkeypatch):
    owner, root, attempt = trace_scope
    monkeypatch.setattr(trace, "MAX_ROOT_EVENTS", 2)
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        first = trace.start_transport(1)
        trace.end_transport(first, outcome="provider_error")
        marker = trace.start_transport(2)
        assert trace.start_transport(3) is None
    db_session.expire_all()
    assert db_session.get(GenerationTraceEvent, marker).reason == "event_limit"


def test_capture_failure_does_not_interrupt_provider(db_session, trace_scope, monkeypatch):
    from app.services.llm_execution import bounded_request
    owner, root, attempt = trace_scope
    monkeypatch.setattr(trace, "encrypt_text_for_existing_owner", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    calls = []
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        result = bounded_request(lambda _budget: calls.append("called") or "ok")
    assert result == "ok"
    assert calls == ["called"]
    assert db_session.scalar(select(Transcript.id).where(Transcript.id == root.id)) == root.id


def test_owner_api_authorization_no_store_and_csrf(raw_client, db_session, make_user, trace_scope):
    from app.models import SessionAuthLevel, TeamRole
    from app.services.auth import SESSION_COOKIE_NAME, create_session
    owner, root, attempt = trace_scope
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": ["synthetic"]}, None):
        event_id = trace.start_transport(1)
        trace.raw_model_text("synthetic output")
        trace.end_transport(event_id, outcome="success")
    other = make_user(email="other-api@example.com", team=owner.team)
    leader = make_user(email="leader-api@example.com", team=owner.team, team_role=TeamRole.leader)
    admin = make_user(email="admin-api@example.com", is_system_admin=True)
    path = f"/api/v1/transcripts/{root.id}/generation-traces"
    for actor, expected in ((owner, 200), (other, 403), (leader, 403), (admin, 403)):
        raw_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, actor, auth_level=SessionAuthLevel.full))
        response = raw_client.get(f"{path}/{event_id}")
        assert response.status_code == expected
        assert response.headers["cache-control"] == "no-store"
    raw_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, owner, auth_level=SessionAuthLevel.full))
    assert raw_client.delete(path, headers={"Origin": "http://testserver"}).status_code == 403
    assert raw_client.get(path).json()["events"]


@pytest.mark.real_db_connections
def test_locked_root_does_not_block_provider_or_poison_caller(db_session, trace_scope):
    from app.services.llm_execution import bounded_request
    from time import monotonic
    owner, root, attempt = trace_scope
    db_session.scalar(select(Transcript).where(Transcript.id == root.id).with_for_update())
    calls = []
    started = monotonic()
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": ["synthetic"]}, None):
        assert bounded_request(lambda _: calls.append(1) or "ok") == "ok"
    assert monotonic() - started < 3
    assert calls == [1]
    assert db_session.scalar(select(Transcript.id).where(Transcript.id == root.id)) == root.id
    db_session.rollback()
    assert db_session.scalar(select(GenerationTraceEvent.id).where(GenerationTraceEvent.transcript_id == root.id)) is None


def test_ciphertext_is_bound_to_owner_field_and_row(db_session, trace_scope):
    from app.services.content_crypto import decrypt_text_for_owner
    owner, root, attempt = trace_scope
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": ["synthetic"]}, None):
        event_id = trace.start_transport(1)
    db_session.expire_all()
    event = db_session.get(GenerationTraceEvent, event_id)
    with pytest.raises(AppError):
        decrypt_text_for_owner(db_session, owner_user_id=owner.id, table="generation_trace_events",
            field="response", record_id=event_id, stored_value=event.request_encrypted)
    with pytest.raises(AppError):
        decrypt_text_for_owner(db_session, owner_user_id=owner.id, table="generation_trace_events",
            field="request", record_id=uuid4(), stored_value=event.request_encrypted)


def test_transcript_cascade_removes_encrypted_trace(db_session, trace_scope):
    from sqlalchemy import delete
    owner, root, attempt = trace_scope
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        event_id = trace.start_transport(1)
    db_session.execute(delete(Transcript).where(Transcript.id == root.id))
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(GenerationTraceEvent, event_id) is None


def test_root_size_cap_counts_ciphertext_envelope(db_session, trace_scope, monkeypatch):
    owner, root, attempt = trace_scope
    monkeypatch.setattr(trace, "MAX_ROOT_BYTES", 60)
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        event_id = trace.start_transport(1)
    db_session.expire_all()
    event = db_session.get(GenerationTraceEvent, event_id)
    assert event.state == "incomplete"
    assert event.reason == "size_limit"
    assert event.request_encrypted is None


def test_missing_capture_reports_content_free_transport_metadata(db_session, trace_scope):
    owner, root, attempt = trace_scope
    attempt.transport_events_json = [{"occurred_at": utcnow().isoformat(), "request_ordinal": 1,
        "duration_ms": 12, "code": "rate_limited", "http_status": 429, "retry_wait_ms": 123}]
    db_session.commit()
    result = trace.list_traces(db_session, owner, root.id)
    assert result["events"] == []
    assert result["missing_attempts"][0]["attempt_id"] == str(attempt.id)
    assert result["missing_attempts"][0]["http_status"] == 429
    assert "request" not in result["missing_attempts"][0]


def test_trace_session_restores_existing_timeouts_on_shared_connection(db_session, trace_scope):
    from sqlalchemy import text
    owner, root, attempt = trace_scope
    db_session.execute(text("SET LOCAL lock_timeout = '3s'"))
    db_session.execute(text("SET LOCAL statement_timeout = '4s'"))
    with trace.attempt_context(db_session, attempt.id), trace.request_context({"messages": []}, None):
        event_id = trace.start_transport(1)
        trace.raw_model_text("synthetic")
        trace.end_transport(event_id, outcome="success")
    trace.record_outcome(db_session, attempt_id=attempt.id, validation="accepted")
    values = db_session.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()
    assert values == ("3s", "4s")


def test_preflight_decision_at_event_cap_marks_incomplete(db_session, trace_scope, monkeypatch):
    owner, root, attempt = trace_scope
    monkeypatch.setattr(trace, "MAX_ROOT_EVENTS", 1)
    trace.record_outcome(db_session, attempt_id=attempt.id, decision="terminal", not_dispatched=True)
    db_session.expire_all()
    event = db_session.scalar(select(GenerationTraceEvent).where(GenerationTraceEvent.transcript_id == root.id))
    assert (event.state, event.reason, event.outcome) == ("incomplete", "event_limit", "not_dispatched")
