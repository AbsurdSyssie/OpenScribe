"""Redis revision hints are advisory, tenant-scoped and fail open to DB reads."""

from types import SimpleNamespace
from unittest.mock import Mock

from app.services import workspace_revision_signals as revisions


def test_owner_and_team_changes_publish_after_commit_only(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)

    class Transcript:
        owner_user_id = "owner-one"
        team_id = "team-one"
        current_draft_text_encrypted = "must-not-be-sent-to-redis"

    class QuickAction:
        owner_user_id = None
        team_id = "team-one"

    class TranscriptStreamDelivery:
        owner_user_id = "owner-one"

    pipeline = Mock()
    fake_client = Mock()
    fake_client.pipeline.return_value = pipeline
    monkeypatch.setattr(revisions, "_redis_client", lambda: fake_client)

    session = SimpleNamespace(
        new={Transcript(), QuickAction(), TranscriptStreamDelivery()},
        dirty=set(), deleted=set(), info={},
    )
    revisions._capture_workspace_changes(session)
    assert pipeline.execute.call_count == 0
    revisions._publish_workspace_changes(session)
    owner_key = revisions._key("owner", "owner-one")
    team_key = revisions._key("team", "team-one")
    pipeline.incr.assert_any_call(owner_key)
    pipeline.incr.assert_any_call(team_key)
    assert pipeline.incr.call_count == 2  # high-frequency delivery is ignored
    assert "must-not-be-sent-to-redis" not in repr(pipeline.mock_calls)

    revisions._publish_workspace_changes(session)
    assert pipeline.execute.call_count == 1  # no duplicate publication


def test_rollback_discards_pending_changes(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)

    class UserAppPreference:
        user_id = "owner-one"

    session = SimpleNamespace(new={UserAppPreference()}, dirty=set(), deleted=set(), info={})
    revisions._capture_workspace_changes(session)
    assert session.info
    revisions._discard_workspace_changes(session)
    assert session.info == {}


def test_revision_read_is_scoped_and_redis_outage_uses_full_refresh(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    client = Mock()
    client.mget.return_value = [b"12", None, b"4"]
    monkeypatch.setattr(revisions, "_redis_client", lambda: client)
    assert revisions.read_workspace_revisions("owner-one", "team-one") == (12, 0, 4)
    client.mget.assert_called_once_with([
        revisions._key("owner", "owner-one"), revisions._key("global"),
        revisions._key("team", "team-one"),
    ])

    from redis import RedisError
    client.mget.side_effect = RedisError("synthetic outage")
    assert revisions.read_workspace_revisions("owner-one", "team-one") is None
