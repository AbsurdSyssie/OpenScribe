"""Regression tests for bounded SSE workspace refresh costs."""

import asyncio
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
