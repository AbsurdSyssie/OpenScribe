"""Regression tests for bounded SSE workspace refresh costs."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from app.models import TranscriptIngestionMode, TranscriptStatus
from app.web import transcribe_workspace as workspace


def test_workspace_stream_uses_fast_refresh_during_active_work():
    assert workspace.workspace_stream_poll_interval_seconds({
        "active_transcript": {"status": "recording"},
    }) == workspace.WORKSPACE_ACTIVE_POLL_SECONDS
    assert workspace.workspace_stream_poll_interval_seconds({
        "active_transcript": {"status": "ready", "latest_ingestion_job_status": "processing"},
    }) == workspace.WORKSPACE_ACTIVE_POLL_SECONDS
    assert workspace.workspace_stream_poll_interval_seconds({
        "active_transcript": {"status": "ready"},
        "generated_documents": [{"status": "queued"}],
    }) == workspace.WORKSPACE_ACTIVE_POLL_SECONDS
    assert workspace.workspace_stream_poll_interval_seconds({
        "consultation_split_analysis": {"status": "processing"},
    }) == workspace.WORKSPACE_ACTIVE_POLL_SECONDS
    assert workspace.workspace_stream_poll_interval_seconds({
        "active_transcript_stream_gap": {"window_no": 1},
    }) == workspace.WORKSPACE_ACTIVE_POLL_SECONDS


def test_workspace_stream_reduces_idle_refreshes():
    assert workspace.workspace_stream_poll_interval_seconds({
        "active_transcript": {"status": "ready"},
        "generated_documents": [{"status": "ready"}],
    }) == workspace.WORKSPACE_IDLE_POLL_SECONDS
    assert workspace.workspace_stream_poll_interval_seconds({
        "active_transcript": None,
    }) == workspace.WORKSPACE_IDLE_POLL_SECONDS


def test_workspace_sse_serializes_only_changed_payloads(monkeypatch):
    payloads = iter([
        {"active_transcript": {"status": "ready"}, "revision": 1},
        {"active_transcript": {"status": "ready"}, "revision": 1},
        {"active_transcript": {"status": "ready"}, "revision": 2},
    ])
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", lambda **_kwargs: next(payloads))
    serializer = Mock(wraps=workspace.serialize_sse_event)
    monkeypatch.setattr(workspace, "serialize_sse_event", serializer)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 2

    async def collect():
        result = []
        async for event in workspace.stream_transcribe_workspace_events(
            request=Request(), raw_session_token="synthetic", transcript_id=None,
            queued_transcript_id=None, once=False,
        ):
            result.append(event)
        return result

    events = asyncio.run(collect())
    assert len(events) == 2
    assert serializer.call_count == 2
    assert '"revision":1' in events[0]
    assert '"revision":2' in events[1]
    assert sleeps == [workspace.WORKSPACE_IDLE_POLL_SECONDS] * 3


def test_workspace_detail_can_reuse_already_reconciled_transcript(monkeypatch):
    transcript = SimpleNamespace(
        id="synthetic", multiple_problems=False,
        status=TranscriptStatus.ready, ingestion_mode=TranscriptIngestionMode.whole_file,
    )
    reconcile = Mock(return_value=transcript)
    monkeypatch.setattr(workspace, "reconcile_transcript_status_service", reconcile)
    monkeypatch.setattr(workspace, "latest_ingestion_job_for_transcript_service", lambda *_a, **_k: None)
    monkeypatch.setattr(workspace, "_earliest_retryable_ingestion_job", lambda *_a, **_k: None)
    monkeypatch.setattr(
        workspace, "transcript_list_item_response",
        lambda *_a, **_k: SimpleNamespace(model_dump=lambda: {"id": "synthetic"}),
    )
    monkeypatch.setattr(workspace, "transcript_draft_text_service", lambda *_a, **_k: "")
    monkeypatch.setattr(workspace, "current_turns_for_response", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(workspace, "transcript_structured_context_service", lambda *_a, **_k: None)
    monkeypatch.setattr(
        workspace, "TranscriptDetail",
        SimpleNamespace(model_validate=lambda payload: payload),
    )

    workspace.transcript_detail_response(object(), transcript, already_reconciled=True)
    reconcile.assert_not_called()

    workspace.transcript_detail_response(object(), transcript)
    reconcile.assert_called_once()


def test_revision_gate_skips_idle_full_hydration_and_resumes_on_commit(monkeypatch):
    revisions = iter([(0, 0), (0, 0), (1, 0), (1, 0)])
    scope = Mock(return_value=("owner-one", "team-one"))
    fetch = Mock(return_value={"active_transcript": {"status": "ready"}})
    monkeypatch.setattr(workspace, "revisions_enabled", lambda: True)
    monkeypatch.setattr(workspace, "realtime_workspace_revision_scope", scope)
    monkeypatch.setattr(workspace, "read_workspace_revisions", lambda *_: next(revisions))
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", fetch)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 3

    async def collect():
        return [
            event async for event in workspace.stream_transcribe_workspace_events(
                request=Request(), raw_session_token="synthetic",
                transcript_id=None, queued_transcript_id=None, once=False,
            )
        ]

    events = asyncio.run(collect())
    assert len(events) == 1  # same content after revision increment
    assert fetch.call_count == 2  # initial plus changed revision
    scope.assert_called_once()  # separate auth interval
    assert sleeps == [workspace.WORKSPACE_REVISION_POLL_SECONDS] * 4


def test_revision_gate_closes_when_session_is_revoked(monkeypatch):
    scopes = iter([("owner-one", "team-one"), None])
    fetch = Mock(return_value={"active_transcript": {"status": "ready"}})
    monkeypatch.setattr(workspace, "revisions_enabled", lambda: True)
    monkeypatch.setattr(workspace, "realtime_workspace_revision_scope", lambda **_: next(scopes))
    monkeypatch.setattr(workspace, "read_workspace_revisions", lambda *_: (0, 0, 0))
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", fetch)
    monkeypatch.setattr(workspace, "WORKSPACE_ACCESS_RECHECK_SECONDS", 0.0)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        async def is_disconnected(self):
            return False

    async def collect():
        return [
            event async for event in workspace.stream_transcribe_workspace_events(
                request=Request(), raw_session_token="synthetic",
                transcript_id=None, queued_transcript_id=None, once=False,
            )
        ]

    assert len(asyncio.run(collect())) == 1
    fetch.assert_called_once()


def test_revision_gate_forces_bounded_full_reconcile(monkeypatch):
    payload = {"active_transcript": {"status": "ready"}}
    fetch = Mock(return_value=payload)
    monkeypatch.setattr(workspace, "revisions_enabled", lambda: True)
    monkeypatch.setattr(workspace, "realtime_workspace_revision_scope", lambda **_: ("owner-one", None))
    monkeypatch.setattr(workspace, "read_workspace_revisions", lambda *_: (1, 0))
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", fetch)
    # An expired age gate must override an unchanged revision.
    monkeypatch.setattr(workspace, "WORKSPACE_MAX_IDLE_RECONCILE_SECONDS", 0.0)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 2

    async def collect():
        return [
            event async for event in workspace.stream_transcribe_workspace_events(
                request=Request(), raw_session_token="synthetic",
                transcript_id=None, queued_transcript_id=None, once=False,
            )
        ]

    assert len(asyncio.run(collect())) == 1
    assert fetch.call_count == 3


def test_revision_gate_skips_active_full_hydration_until_lifecycle_revision(monkeypatch):
    revisions = iter([(0, 0), (0, 0), (0, 0), (1, 0), (1, 0)])
    fetch = Mock(side_effect=[
        {"active_transcript": {"status": "recording"}},
        {"active_transcript": {"status": "transcribing"}},
    ])
    monkeypatch.setattr(workspace, "revisions_enabled", lambda: True)
    monkeypatch.setattr(workspace, "realtime_workspace_revision_scope", lambda **_: ("owner-one", None))
    monkeypatch.setattr(workspace, "read_workspace_revisions", lambda *_: next(revisions))
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", fetch)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 4

    async def collect():
        return [
            event async for event in workspace.stream_transcribe_workspace_events(
                request=Request(), raw_session_token="synthetic", transcript_id=None,
                queued_transcript_id=None, once=False,
            )
        ]

    events = asyncio.run(collect())
    assert len(events) == 2
    assert '"status":"recording"' in events[0]
    assert '"status":"transcribing"' in events[1]
    assert fetch.call_count == 2  # not one full workspace hydration per second
    assert sleeps == [workspace.WORKSPACE_REVISION_POLL_SECONDS] * 5


def test_active_revision_gate_preserves_bounded_full_reconcile(monkeypatch):
    fetch = Mock(return_value={"active_transcript": {"status": "recording"}})
    monkeypatch.setattr(workspace, "revisions_enabled", lambda: True)
    monkeypatch.setattr(workspace, "realtime_workspace_revision_scope", lambda **_: ("owner-one", None))
    monkeypatch.setattr(workspace, "read_workspace_revisions", lambda *_: (1, 0))
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", fetch)
    monkeypatch.setattr(workspace, "WORKSPACE_MAX_ACTIVE_RECONCILE_SECONDS", 0.0)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 2

    async def collect():
        return [
            event async for event in workspace.stream_transcribe_workspace_events(
                request=Request(), raw_session_token="synthetic",
                transcript_id=None, queued_transcript_id=None, once=False,
            )
        ]

    assert len(asyncio.run(collect())) == 1
    assert fetch.call_count == 3


def test_redis_outage_preserves_active_one_second_fallback(monkeypatch):
    fetch = Mock(return_value={"active_transcript": {"status": "recording"}})
    monkeypatch.setattr(workspace, "revisions_enabled", lambda: True)
    monkeypatch.setattr(workspace, "realtime_workspace_revision_scope", lambda **_: ("owner-one", None))
    monkeypatch.setattr(workspace, "read_workspace_revisions", lambda *_: None)
    monkeypatch.setattr(workspace, "resolve_realtime_workspace_payload", fetch)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(workspace.asyncio, "sleep", fake_sleep)

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 2

    async def collect():
        return [
            event async for event in workspace.stream_transcribe_workspace_events(
                request=Request(), raw_session_token="synthetic",
                transcript_id=None, queued_transcript_id=None, once=False,
            )
        ]

    assert len(asyncio.run(collect())) == 1
    assert fetch.call_count == 3
    assert sleeps == [workspace.WORKSPACE_ACTIVE_POLL_SECONDS] * 3


def test_recovery_expiry_schedules_reconcile_before_normal_active_deadline():
    from app.models import utcnow

    expiry = (utcnow() + timedelta(seconds=3)).isoformat()
    payload = {
        "active_transcript": {"status": "failed"},
        "active_transcript_stream_gap": {
            "recovery_available": True, "recovery_expires_at": expiry,
        },
    }
    interval = workspace.workspace_stream_reconcile_interval_seconds(payload)
    assert 1 <= interval <= 3.1
    assert interval < workspace.WORKSPACE_MAX_ACTIVE_RECONCILE_SECONDS
    retention_deadline = (utcnow() + timedelta(seconds=4)).isoformat()
    retention_interval = workspace.workspace_stream_reconcile_interval_seconds({
        "active_transcript": {
            "status": "ready", "retention_expires_at": retention_deadline,
        },
    })
    assert 1 <= retention_interval <= 4.1
    assert retention_interval < workspace.WORKSPACE_MAX_IDLE_RECONCILE_SECONDS
    assert workspace.workspace_stream_reconcile_interval_seconds({
        "active_transcript": {"status": "recording"},
    }) == workspace.WORKSPACE_MAX_ACTIVE_RECONCILE_SECONDS
    assert workspace.workspace_stream_reconcile_interval_seconds({
        "active_transcript_stream_gap": {
            "recovery_available": True, "recovery_expires_at": "invalid",
        },
    }) == workspace.WORKSPACE_MAX_ACTIVE_RECONCILE_SECONDS
